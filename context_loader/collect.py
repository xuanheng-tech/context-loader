"""Collect bounded root-file and directory context without executing repository code."""

from __future__ import annotations

import codecs
import errno
import json
import os
import re
import stat
import tomllib
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

AGENTS_LIMIT_BYTES = 16 * 1024
AGENTS_HEAD_LIMIT_BYTES = 4 * 1024
AGENTS_SCAN_LIMIT_BYTES = 256 * 1024
AGENTS_FOCUS_LIMIT_BYTES = 1024
AGENTS_PATH_LIMIT_BYTES = 1024
README_LIMIT_BYTES = 16 * 1024
ENTRY_FILE_LIMIT_BYTES = 8 * 1024
ENTRY_FILES_TOTAL_LIMIT_BYTES = 24 * 1024
DECLARED_COMMANDS_LIMIT_BYTES = 8 * 1024
DIRECTORY_TREE_LIMIT_BYTES = 12 * 1024
DIRECTORY_TREE_MAX_ITEMS = 300
DIRECTORY_TREE_MAX_DEPTH = 2
TRUNCATION_MARKER = "… truncated by context-loader …"

NOT_PRESENT = "Not present."
SKIPPED_SYMLINK = "Skipped: symlink."
SKIPPED_NOT_REGULAR = "Skipped: not a regular file."
SKIPPED_ENCODING = "Skipped: unsupported text encoding."
SKIPPED_UNREADABLE = "Skipped: unreadable."

ENTRY_FILE_SPECS = (
    ("pyproject.toml", "toml"),
    ("package.json", "json"),
    ("Makefile", "make"),
    ("Cargo.toml", "toml"),
    ("go.mod", "text"),
)

_JUST_RECIPE_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_-]*)"
    r"(?:[ \t]+(?:[+*]?[A-Za-z_][A-Za-z0-9_-]*"
    r"(?:=(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^ \t:#]+))?))*"
    r"[ \t]*:(?!=)"
)
_JUST_ATTRIBUTE_RE = re.compile(r"^\[([^\]]+)\][ \t]*$")
_MAKE_TARGET_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.-]*"
    r"(?:[ \t]+[A-Za-z0-9][A-Za-z0-9_.-]*)*)[ \t]*:(?![:=])"
)
_ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_MATCH_TOKEN_STOPWORDS = frozenset(
    {
        "and",
        "code",
        "docs",
        "file",
        "files",
        "for",
        "from",
        "into",
        "must",
        "path",
        "project",
        "section",
        "should",
        "source",
        "task",
        "test",
        "tests",
        "that",
        "the",
        "this",
        "when",
        "where",
        "with",
        "work",
    }
)
_PATH_TOKEN_STOPWORDS = _MATCH_TOKEN_STOPWORDS | {
    "app",
    "apps",
    "application",
    "backend",
    "bin",
    "data",
    "doc",
    "frontend",
    "ingestion",
    "lib",
    "package",
    "packages",
    "platform",
    "provider",
    "providers",
    "py",
    "python",
    "quant",
    "scripts",
    "src",
}
_SELECTION_REASON_ORDER = (
    "head",
    "focus_match",
    "path_match",
    "parent_context",
    "budget_fallback",
)


class AgentsSelectionInputError(ValueError):
    """Raised when optional AGENTS selection inputs exceed the bounded contract."""


class MarkdownSectionParseError(ValueError):
    """Raised when Markdown headings cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    heading: str
    heading_level: int
    start: int
    end: int
    text: str
    normalized_heading: str
    parent_index: int | None


@dataclass(frozen=True, slots=True)
class AgentsSectionAuditEntry:
    heading: str
    heading_level: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentsSelectionAudit:
    source: str
    selected_sections: tuple[AgentsSectionAuditEntry, ...]
    indexed_only_sections: tuple[AgentsSectionAuditEntry, ...]
    chars_selected: int
    chars_omitted: int
    truncated: bool
    parse_fallback: bool = False
    source_scan_truncated: bool = False
    index_truncated: bool = False


@dataclass(frozen=True, slots=True)
class CollectedFile:
    name: str
    language: str
    status: str | None
    content: str = ""
    truncated: bool = False
    selection: AgentsSelectionAudit | None = None

    @property
    def is_text(self) -> bool:
        return self.status is None


@dataclass(frozen=True, slots=True)
class DeclaredCommand:
    source: str
    invocation: str | None = None
    target: str | None = None
    parse_error: bool = False


@dataclass(frozen=True, slots=True)
class TreeEntry:
    path: str
    kind: str


@dataclass(frozen=True, slots=True)
class DirectoryTree:
    entries: tuple[TreeEntry, ...]
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ProjectContext:
    instructions: CollectedFile
    overview: CollectedFile
    entry_files: tuple[CollectedFile, ...]
    commands: tuple[DeclaredCommand, ...]
    directory_tree: DirectoryTree


@dataclass(frozen=True, slots=True)
class _SelectionSignals:
    focus_tokens: frozenset[str]
    path_tokens: frozenset[str]
    normalized_path: str | None


def _normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _matching_tokens(value: str, stopwords: frozenset[str]) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN_RE.findall(_normalized_text(value))
        if len(token) >= 3 and token not in stopwords
    )


def _bounded_optional_input(name: str, value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AgentsSelectionInputError(f"{name} must be a string")
    bounded = value.strip()
    if not bounded:
        return None
    if len(bounded.encode("utf-8")) > limit:
        raise AgentsSelectionInputError(f"{name} exceeds {limit} bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in bounded):
        raise AgentsSelectionInputError(f"{name} contains control characters")
    return bounded


def _selection_signals(focus: str | None, path: str | None) -> _SelectionSignals:
    bounded_focus = _bounded_optional_input("focus", focus, AGENTS_FOCUS_LIMIT_BYTES)
    bounded_path = _bounded_optional_input("path", path, AGENTS_PATH_LIMIT_BYTES)
    normalized_path: str | None = None
    path_tokens: frozenset[str] = frozenset()
    if bounded_path is not None:
        candidate = PurePosixPath(bounded_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise AgentsSelectionInputError("path must be repository-relative without '..'")
        normalized_path = candidate.as_posix()
        if normalized_path == ".":
            normalized_path = None
        path_tokens = _matching_tokens(normalized_path or "", _PATH_TOKEN_STOPWORDS)
    return _SelectionSignals(
        focus_tokens=_matching_tokens(bounded_focus or "", _MATCH_TOKEN_STOPWORDS),
        path_tokens=path_tokens,
        normalized_path=normalized_path,
    )


def _ordered_reasons(reasons: set[str] | frozenset[str]) -> tuple[str, ...]:
    return tuple(reason for reason in _SELECTION_REASON_ORDER if reason in reasons)


def _audit_heading(heading: str) -> str:
    safe = heading.replace("`", r"\x60").strip()
    if len(safe) <= 160:
        return safe
    return f"{safe[:159]}…"


def render_agents_selection_audit(audit: AgentsSelectionAudit) -> str:
    """Render the bounded, prompt-free AGENTS selection audit and fail-safe index."""
    lines = [
        "### AGENTS Selection Audit",
        "",
        f"- Source: `{audit.source}`",
        f"- Selected source characters: `{audit.chars_selected}`",
        f"- Omitted source characters: `{audit.chars_omitted}`",
        f"- Selection truncated: `{'true' if audit.truncated else 'false'}`",
        "",
        "Selected AGENTS sections (rule text loaded):",
    ]
    if audit.selected_sections:
        for entry in audit.selected_sections:
            label = "Document head" if entry.heading_level == 0 else _audit_heading(entry.heading)
            reasons = ", ".join(entry.reasons)
            lines.append(f"- {label} — `{reasons}`")
    else:
        lines.append("- None.")
    lines.extend(
        (
            "",
            "Additional AGENTS sections not loaded (index only; rule text not loaded):",
        )
    )
    if audit.indexed_only_sections:
        for entry in audit.indexed_only_sections:
            lines.append(f"- H{entry.heading_level} {_audit_heading(entry.heading)}")
    else:
        lines.append("- None.")
        if audit.truncated and not audit.index_truncated:
            lines.append("- Omitted content has no available heading index; read the source path.")
    if audit.index_truncated:
        lines.append("- Additional headings omitted from this index by the AGENTS budget.")
    if audit.parse_fallback:
        lines.append("- Heading parsing was unsafe; use the source path for manual recovery.")
    if audit.source_scan_truncated:
        lines.append("- The bounded source scan ended before EOF; later headings may be absent.")
    return "\n".join(lines)


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or not stripped or stripped[0] not in {"`", "~"}:
        return None
    character = stripped[0]
    length = len(stripped) - len(stripped.lstrip(character))
    if length < 3:
        return None
    return character, length, stripped[length:]


def _parse_markdown_sections(content: str) -> tuple[MarkdownSection, ...]:
    headings: list[tuple[str, int, int]] = []
    offset = 0
    open_fence: tuple[str, int] | None = None
    for line in content.splitlines(keepends=True):
        visible = line.removesuffix("\n")
        marker = _fence_marker(visible)
        if open_fence is not None:
            if marker is not None:
                character, length, remainder = marker
                if character == open_fence[0] and length >= open_fence[1] and not remainder.strip():
                    open_fence = None
            offset += len(line)
            continue
        if marker is not None:
            character, length, _remainder = marker
            open_fence = (character, length)
            offset += len(line)
            continue
        match = _ATX_HEADING_RE.fullmatch(visible)
        if match is not None:
            heading = (match.group(2) or "").rstrip()
            heading = re.sub(r"[ \t]+#+[ \t]*$", "", heading).strip()
            headings.append((heading, len(match.group(1)), offset))
        offset += len(line)
    if open_fence is not None:
        raise MarkdownSectionParseError("unclosed fenced code block")

    sections: list[MarkdownSection] = []
    parents: list[int] = []
    for index, (heading, level, start) in enumerate(headings):
        while parents and sections[parents[-1]].heading_level >= level:
            parents.pop()
        parent_index = parents[-1] if parents else None
        end = headings[index + 1][2] if index + 1 < len(headings) else len(content)
        sections.append(
            MarkdownSection(
                heading=heading,
                heading_level=level,
                start=start,
                end=end,
                text=content[start:end],
                normalized_heading=" ".join(_TOKEN_RE.findall(_normalized_text(heading))),
                parent_index=parent_index,
            )
        )
        parents.append(index)
    return tuple(sections)


def _line_safe_prefix_end(content: str, limit: int) -> int:
    encoded = content.encode("utf-8")
    if len(encoded) <= limit:
        return len(content)
    boundary = encoded.rfind(b"\n", 0, limit + 1)
    if boundary < 0:
        return 0
    return len(encoded[: boundary + 1].decode("utf-8"))


def _small_head_end(content: str, sections: tuple[MarkdownSection, ...]) -> int:
    if len(content.encode("utf-8")) <= AGENTS_HEAD_LIMIT_BYTES:
        return len(content)
    document_head_end = sections[0].start if sections else len(content)
    if len(content[:document_head_end].encode("utf-8")) > AGENTS_HEAD_LIMIT_BYTES:
        return _line_safe_prefix_end(content[:document_head_end], AGENTS_HEAD_LIMIT_BYTES)
    boundary = document_head_end
    for section in sections:
        if len(content[: section.end].encode("utf-8")) > AGENTS_HEAD_LIMIT_BYTES:
            break
        boundary = section.end
    if boundary == 0:
        return _line_safe_prefix_end(content, AGENTS_HEAD_LIMIT_BYTES)
    return boundary


def _section_relevance(
    section: MarkdownSection, signals: _SelectionSignals
) -> tuple[int, frozenset[str]]:
    heading_tokens = _matching_tokens(section.heading, _MATCH_TOKEN_STOPWORDS)
    section_tokens = _matching_tokens(section.text, _MATCH_TOKEN_STOPWORDS)
    body_tokens = section_tokens - heading_tokens
    score = 0
    reasons: set[str] = set()

    focus_heading = signals.focus_tokens & heading_tokens
    focus_body = signals.focus_tokens & body_tokens
    if focus_heading or focus_body:
        reasons.add("focus_match")
        score += 6 * len(focus_heading) + len(focus_body)

    path_heading = signals.path_tokens & heading_tokens
    path_body = signals.path_tokens & body_tokens
    normalized_section = _normalized_text(section.text)
    exact_path = bool(
        signals.normalized_path and _normalized_text(signals.normalized_path) in normalized_section
    )
    if exact_path or path_heading or path_body:
        reasons.add("path_match")
        score += (10 if exact_path else 0) + 5 * len(path_heading) + 2 * len(path_body)
    return score, frozenset(reasons)


def _merge_intervals(intervals: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _interval_is_covered(start: int, end: int, intervals: tuple[tuple[int, int], ...]) -> bool:
    return any(
        selected_start <= start and end <= selected_end
        for selected_start, selected_end in intervals
    )


def _selection_state(
    content: str,
    sections: tuple[MarkdownSection, ...],
    head_end: int,
    selected_indices: frozenset[int],
    reasons: dict[int, set[str]],
    budget_fallback: frozenset[int],
    *,
    parse_fallback: bool,
    source_scan_truncated: bool,
) -> tuple[str, AgentsSelectionAudit]:
    intervals = [(0, head_end)] if head_end else []
    intervals.extend((sections[index].start, sections[index].end) for index in selected_indices)
    merged = _merge_intervals(intervals)
    selected_content = "".join(content[start:end] for start, end in merged)

    selected_entries: list[AgentsSectionAuditEntry] = []
    document_head_end = sections[0].start if sections else len(content)
    if document_head_end and _interval_is_covered(0, min(document_head_end, head_end), merged):
        selected_entries.append(AgentsSectionAuditEntry("Document head", 0, ("head",)))
    indexed_entries: list[AgentsSectionAuditEntry] = []
    for index, section in enumerate(sections):
        if _interval_is_covered(section.start, section.end, merged):
            entry_reasons = set(reasons.get(index, set()))
            if section.end <= head_end:
                entry_reasons.add("head")
            selected_entries.append(
                AgentsSectionAuditEntry(
                    section.heading,
                    section.heading_level,
                    _ordered_reasons(entry_reasons),
                )
            )
        else:
            indexed_entries.append(
                AgentsSectionAuditEntry(
                    section.heading,
                    section.heading_level,
                    ("budget_fallback",) if index in budget_fallback else (),
                )
            )

    chars_selected = sum(end - start for start, end in merged)
    chars_omitted = max(0, len(content) - chars_selected)
    audit = AgentsSelectionAudit(
        source="AGENTS.md",
        selected_sections=tuple(selected_entries),
        indexed_only_sections=tuple(indexed_entries),
        chars_selected=chars_selected,
        chars_omitted=chars_omitted,
        truncated=chars_omitted > 0 or source_scan_truncated,
        parse_fallback=parse_fallback,
        source_scan_truncated=source_scan_truncated,
    )
    return selected_content, audit


def _aggregate_agents_bytes(content: str, audit: AgentsSelectionAudit) -> int:
    return len(content.encode("utf-8")) + len(render_agents_selection_audit(audit).encode("utf-8"))


def _fit_agents_index(content: str, audit: AgentsSelectionAudit) -> AgentsSelectionAudit:
    if _aggregate_agents_bytes(content, audit) <= AGENTS_LIMIT_BYTES:
        return audit
    indexed = list(audit.indexed_only_sections)
    while indexed:
        indexed.pop()
        candidate = replace(audit, indexed_only_sections=tuple(indexed), index_truncated=True)
        if _aggregate_agents_bytes(content, candidate) <= AGENTS_LIMIT_BYTES:
            return candidate
    candidate = replace(audit, indexed_only_sections=(), index_truncated=True)
    if _aggregate_agents_bytes(content, candidate) > AGENTS_LIMIT_BYTES:
        raise RuntimeError("AGENTS head and selection audit exceed the AGENTS budget")
    return candidate


def _select_agents_content(source: CollectedFile, signals: _SelectionSignals) -> CollectedFile:
    if not source.is_text:
        return source
    try:
        sections = _parse_markdown_sections(source.content)
    except MarkdownSectionParseError:
        head_end = _line_safe_prefix_end(source.content, AGENTS_HEAD_LIMIT_BYTES)
        content, audit = _selection_state(
            source.content,
            (),
            head_end,
            frozenset(),
            {},
            frozenset(),
            parse_fallback=True,
            source_scan_truncated=source.truncated,
        )
        audit = _fit_agents_index(content, audit)
        return CollectedFile(
            source.name,
            source.language,
            None,
            content,
            audit.truncated,
            audit,
        )

    head_end = _small_head_end(source.content, sections)
    selected_indices: frozenset[int] = frozenset()
    reasons: dict[int, set[str]] = {}
    ranked: list[tuple[int, int, frozenset[str]]] = []
    for index, section in enumerate(sections):
        score, section_reasons = _section_relevance(section, signals)
        if score >= 4:
            ranked.append((score, index, section_reasons))
    ranked.sort(key=lambda item: (-item[0], sections[item[1]].start))

    budget_fallback: set[int] = set()
    for _score, index, section_reasons in ranked:
        trial_indices = set(selected_indices)
        trial_reasons = {key: set(value) for key, value in reasons.items()}
        trial_indices.add(index)
        trial_reasons.setdefault(index, set()).update(section_reasons)
        parent = sections[index].parent_index
        while parent is not None:
            trial_indices.add(parent)
            trial_reasons.setdefault(parent, set()).add("parent_context")
            parent = sections[parent].parent_index
        trial_content, trial_audit = _selection_state(
            source.content,
            sections,
            head_end,
            frozenset(trial_indices),
            trial_reasons,
            frozenset(budget_fallback),
            parse_fallback=False,
            source_scan_truncated=source.truncated,
        )
        try:
            _fit_agents_index(trial_content, trial_audit)
        except RuntimeError:
            budget_fallback.add(index)
        else:
            selected_indices = frozenset(trial_indices)
            reasons = trial_reasons

    content, audit = _selection_state(
        source.content,
        sections,
        head_end,
        selected_indices,
        reasons,
        frozenset(budget_fallback),
        parse_fallback=False,
        source_scan_truncated=source.truncated,
    )
    audit = _fit_agents_index(content, audit)
    return CollectedFile(
        source.name,
        source.language,
        None,
        content,
        audit.truncated,
        audit,
    )


def _append_normalized_character(
    capture: bytearray,
    character: str,
    limit: int,
) -> bool:
    encoded = character.encode()
    if len(capture) + len(encoded) > limit:
        return False
    capture.extend(encoded)
    return True


def _read_validated_text(file_descriptor: int, limit: int) -> tuple[str, bool, str | None]:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    capture = bytearray()
    last_line_boundary = 0
    overflow = False
    pending_carriage_return = False

    def consume(decoded: str) -> None:
        nonlocal last_line_boundary, overflow, pending_carriage_return
        if overflow:
            return
        for character in decoded:
            if pending_carriage_return:
                pending_carriage_return = False
                if not _append_normalized_character(capture, "\n", limit):
                    overflow = True
                    return
                last_line_boundary = len(capture)
                if character == "\n":
                    continue
            if character == "\r":
                pending_carriage_return = True
                continue
            if not _append_normalized_character(capture, character, limit):
                overflow = True
                return
            if character == "\n":
                last_line_boundary = len(capture)

    try:
        while True:
            raw = os.read(file_descriptor, 64 * 1024)
            if not raw:
                break
            if b"\0" in raw:
                return "", False, SKIPPED_ENCODING
            consume(decoder.decode(raw, final=False))
        consume(decoder.decode(b"", final=True))
        if not overflow and pending_carriage_return:
            if _append_normalized_character(capture, "\n", limit):
                last_line_boundary = len(capture)
            else:
                overflow = True
    except UnicodeDecodeError:
        return "", False, SKIPPED_ENCODING
    except OSError:
        return "", False, SKIPPED_UNREADABLE

    if overflow:
        del capture[last_line_boundary:]
    return capture.decode("utf-8"), overflow, None


def _collect_root_file(root: Path, name: str, language: str, limit: int) -> CollectedFile:
    if len(Path(name).parts) != 1 or Path(name).name != name:
        return CollectedFile(name, language, SKIPPED_UNREADABLE)
    path = root / name
    if path.parent != root:
        return CollectedFile(name, language, SKIPPED_UNREADABLE)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return CollectedFile(name, language, NOT_PRESENT)
    except OSError:
        return CollectedFile(name, language, SKIPPED_UNREADABLE)
    if stat.S_ISLNK(metadata.st_mode):
        return CollectedFile(name, language, SKIPPED_SYMLINK)
    if not stat.S_ISREG(metadata.st_mode):
        return CollectedFile(name, language, SKIPPED_NOT_REGULAR)

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return CollectedFile(name, language, SKIPPED_SYMLINK)
        return CollectedFile(name, language, SKIPPED_UNREADABLE)
    try:
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            return CollectedFile(name, language, SKIPPED_NOT_REGULAR)
        content, truncated, error_status = _read_validated_text(file_descriptor, limit)
    except OSError:
        return CollectedFile(name, language, SKIPPED_UNREADABLE)
    finally:
        os.close(file_descriptor)
    if error_status is not None:
        return CollectedFile(name, language, error_status)
    return CollectedFile(name, language, None, content, truncated)


def _root_name_exists(root: Path, name: str) -> bool:
    try:
        (root / name).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _selected_entry_specs(root: Path) -> tuple[tuple[str, str], ...]:
    just_spec = (
        ("justfile", "make") if _root_name_exists(root, "justfile") else ("Justfile", "make")
    )
    return (ENTRY_FILE_SPECS[0], just_spec, *ENTRY_FILE_SPECS[1:])


def _pyproject_commands(source: CollectedFile) -> tuple[DeclaredCommand, ...]:
    try:
        document = tomllib.loads(source.content)
    except (tomllib.TOMLDecodeError, ValueError):
        return (DeclaredCommand(source.name, parse_error=True),)
    project = document.get("project")
    if not isinstance(project, dict):
        return ()
    declarations: list[tuple[str, int, str]] = []
    for table_order, table_name in enumerate(("scripts", "gui-scripts")):
        table = project.get(table_name)
        if not isinstance(table, dict):
            continue
        declarations.extend(
            (name, table_order, value)
            for name, value in table.items()
            if isinstance(name, str) and isinstance(value, str)
        )
    return tuple(
        DeclaredCommand(source.name, name, target)
        for name, _table_order, target in sorted(declarations, key=lambda item: (item[0], item[1]))
    )


def _package_commands(source: CollectedFile) -> tuple[DeclaredCommand, ...]:
    try:
        document = json.loads(source.content)
    except (json.JSONDecodeError, ValueError):
        return (DeclaredCommand(source.name, parse_error=True),)
    if not isinstance(document, dict) or not isinstance(document.get("scripts"), dict):
        return ()
    scripts = document["scripts"]
    return tuple(
        DeclaredCommand(source.name, f"npm run {name}", value)
        for name, value in sorted(scripts.items())
        if isinstance(name, str) and isinstance(value, str)
    )


def _just_commands(source: CollectedFile) -> tuple[DeclaredCommand, ...]:
    recipes: set[str] = set()
    pending_private = False
    for line in source.content.splitlines():
        if not line or line.startswith((" ", "\t", "#")):
            continue
        attribute = _JUST_ATTRIBUTE_RE.fullmatch(line)
        if attribute is not None:
            names = {item.strip() for item in attribute.group(1).split(",")}
            pending_private = "private" in names
            continue
        if re.match(r"^(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_-]*[ \t]*(?::=|\?=|\+=|=)", line):
            pending_private = False
            continue
        match = _JUST_RECIPE_RE.match(line)
        if match is not None and not pending_private:
            recipes.add(match.group(1))
        pending_private = False
    return tuple(DeclaredCommand(source.name, f"just {name}") for name in sorted(recipes))


def _make_commands(source: CollectedFile) -> tuple[DeclaredCommand, ...]:
    targets: set[str] = set()
    for line in source.content.splitlines():
        if not line or line.startswith((" ", "\t", "#", ".")):
            continue
        if re.match(
            r"^(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_-]*[ \t]*(?::=|::=|\?=|\+=|!=|=)", line
        ):
            continue
        match = _MAKE_TARGET_RE.match(line)
        if match is None or "%" in line or "$" in line or "=" in line[match.end() :]:
            continue
        targets.update(match.group(1).split())
    return tuple(DeclaredCommand(source.name, f"make {name}") for name in sorted(targets))


def _collect_commands(entry_files: tuple[CollectedFile, ...]) -> tuple[DeclaredCommand, ...]:
    commands: list[DeclaredCommand] = []
    for source in entry_files:
        if not source.is_text:
            continue
        if source.name == "pyproject.toml":
            commands.extend(_pyproject_commands(source))
        elif source.name == "package.json":
            commands.extend(_package_commands(source))
        elif source.name in {"justfile", "Justfile"}:
            commands.extend(_just_commands(source))
        elif source.name == "Makefile":
            commands.extend(_make_commands(source))
    return tuple(commands)


class _TreeLimitReached(Exception):
    pass


def _classify_entries(
    file_descriptor: int, *, root_level: bool
) -> tuple[list[os.DirEntry[str]], list[os.DirEntry[str]]]:
    with os.scandir(file_descriptor) as iterator:
        scanned = list(iterator)
    directories: list[os.DirEntry[str]] = []
    others: list[os.DirEntry[str]] = []
    for entry in scanned:
        if root_level and entry.name == ".git":
            directories.append(entry)
            continue
        try:
            is_directory = entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
        except OSError:
            is_directory = False
        (directories if is_directory else others).append(entry)
    directories.sort(key=lambda entry: entry.name)
    others.sort(key=lambda entry: entry.name)
    return directories, others


def _collect_directory_tree(root: Path) -> DirectoryTree:
    collected: list[TreeEntry] = []
    truncated = False

    def add(entry: TreeEntry) -> None:
        nonlocal truncated
        if len(collected) >= DIRECTORY_TREE_MAX_ITEMS:
            truncated = True
            raise _TreeLimitReached
        collected.append(entry)

    def relative(prefix: str, name: str) -> str:
        return f"{prefix}/{name}" if prefix else name

    def walk(file_descriptor: int, prefix: str, depth: int) -> None:
        directories, others = _classify_entries(file_descriptor, root_level=depth == 0)
        for entry in directories:
            entry_path = relative(prefix, entry.name)
            if depth == 0 and entry.name == ".git":
                add(TreeEntry(entry_path, "directory"))
                continue
            if depth + 1 >= DIRECTORY_TREE_MAX_DEPTH:
                add(TreeEntry(entry_path, "directory"))
                continue
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            try:
                child_descriptor = os.open(entry.name, flags, dir_fd=file_descriptor)
            except OSError:
                add(TreeEntry(entry_path, "unreadable_directory"))
                continue
            try:
                directory_index = len(collected)
                add(TreeEntry(entry_path, "directory"))
                try:
                    walk(child_descriptor, entry_path, depth + 1)
                except OSError:
                    collected[directory_index] = TreeEntry(entry_path, "unreadable_directory")
            finally:
                os.close(child_descriptor)
        for entry in others:
            entry_path = relative(prefix, entry.name)
            try:
                kind = "symlink" if entry.is_symlink() else "file"
            except OSError:
                kind = "file"
            add(TreeEntry(entry_path, kind))

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_descriptor = os.open(root, flags)
    except OSError:
        return DirectoryTree((TreeEntry("", "unreadable_directory"),))
    try:
        try:
            walk(root_descriptor, "", 0)
        except _TreeLimitReached:
            pass
        except OSError:
            if not collected:
                collected.append(TreeEntry("", "unreadable_directory"))
    finally:
        os.close(root_descriptor)
    return DirectoryTree(tuple(collected), truncated)


def collect_project_context(
    repository: Path,
    *,
    focus: str | None = None,
    path: str | None = None,
) -> ProjectContext:
    """Collect fixed root candidates, selecting AGENTS sections from bounded signals."""
    signals = _selection_signals(focus, path)
    raw_instructions = _collect_root_file(
        repository,
        "AGENTS.md",
        "markdown",
        AGENTS_SCAN_LIMIT_BYTES,
    )
    instructions = _select_agents_content(raw_instructions, signals)
    overview = _collect_root_file(repository, "README.md", "markdown", README_LIMIT_BYTES)
    entry_files = tuple(
        _collect_root_file(repository, name, language, ENTRY_FILE_LIMIT_BYTES)
        for name, language in _selected_entry_specs(repository)
    )
    return ProjectContext(
        instructions=instructions,
        overview=overview,
        entry_files=entry_files,
        commands=_collect_commands(entry_files),
        directory_tree=_collect_directory_tree(repository),
    )
