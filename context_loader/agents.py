"""Pure, bounded AGENTS section selection and its shared audit representation."""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from .model import (
    AGENTS_FOCUS_LIMIT_BYTES,
    AGENTS_HEAD_LIMIT_BYTES,
    AGENTS_LIMIT_BYTES,
    AGENTS_PATH_LIMIT_BYTES,
    AgentsSectionAuditEntry,
    AgentsSelectionAudit,
    AgentsSelectionInputError,
    Availability,
    CollectedFile,
    MarkdownSection,
    MarkdownSectionParseError,
)

_ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SHORT_TECHNICAL_TOKENS = frozenset(
    {"ai", "cd", "ci", "db", "io", "js", "ml", "os", "pr", "qa", "ts", "ui", "ux"}
)
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
    "research",
    "scripts",
    "src",
}
_MIN_SECTION_RELEVANCE = 4
_SELECTION_REASON_ORDER = (
    "head",
    "focus_match",
    "path_match",
    "parent_context",
    "budget_fallback",
)


@dataclass(frozen=True, slots=True)
class _SelectionSignals:
    focus_tokens: frozenset[str]
    path_tokens: frozenset[str]
    normalized_path: str | None


def _normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _is_han_token(value: str) -> bool:
    return bool(value) and all(
        unicodedata.name(character, "").startswith("CJK UNIFIED IDEOGRAPH-") for character in value
    )


def _matching_tokens(value: str, stopwords: frozenset[str]) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN_RE.findall(_normalized_text(value))
        if token not in stopwords
        and (
            len(token) >= 3
            or token in _SHORT_TECHNICAL_TOKENS
            or (len(token) == 2 and _is_han_token(token))
        )
    )


def _matched_terms(terms: frozenset[str], tokens: frozenset[str]) -> frozenset[str]:
    matches = terms & tokens
    han_terms = tuple(term for term in terms - matches if _is_han_token(term))
    if not han_terms:
        return matches
    # Han text need not delimit words with spaces. Count each query term once,
    # without introducing substring matching for Latin words or abbreviations.
    han_tokens = tuple(token for token in tokens if _is_han_token(token))
    return matches | frozenset(
        term for term in han_terms if any(term in token for token in han_tokens)
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


_AUDIT_EMPTY_LIST_LINE = "- None."
_AUDIT_NO_INDEX_LINE = "- Omitted content has no available heading index; read the source path."


def _audit_heading(heading: str) -> str:
    safe = heading.replace("`", r"\x60").strip()
    if len(safe) <= 160:
        return safe
    return f"{safe[:159]}…"


def _audit_index_line(entry: AgentsSectionAuditEntry) -> str:
    return f"- H{entry.heading_level} {_audit_heading(entry.heading)}"


def _audit_selected_line(entry: AgentsSectionAuditEntry) -> str:
    label = "Document head" if entry.heading_level == 0 else _audit_heading(entry.heading)
    return f"- {label} — `{', '.join(entry.reasons)}`"


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
            lines.append(_audit_selected_line(entry))
    else:
        lines.append(_AUDIT_EMPTY_LIST_LINE)
    lines.extend(
        (
            "",
            "Additional AGENTS sections not loaded (index only; rule text not loaded):",
        )
    )
    if audit.indexed_only_sections:
        for entry in audit.indexed_only_sections:
            lines.append(_audit_index_line(entry))
    else:
        lines.append(_AUDIT_EMPTY_LIST_LINE)
        if audit.truncated and not audit.index_truncated:
            lines.append(_AUDIT_NO_INDEX_LINE)
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
    heading_tokens = _matching_tokens(section.normalized_heading, _MATCH_TOKEN_STOPWORDS)
    section_tokens = _matching_tokens(section.text, _MATCH_TOKEN_STOPWORDS)
    body_tokens = section_tokens - heading_tokens
    score = 0
    reasons: set[str] = set()

    focus_heading = _matched_terms(signals.focus_tokens, heading_tokens)
    focus_body = _matched_terms(signals.focus_tokens, body_tokens) - focus_heading
    if focus_heading or focus_body:
        reasons.add("focus_match")
        focus_score = 6 * len(focus_heading) + len(focus_body)
        # Two distinct terms in one body suffice without outranking a heading match.
        if len(focus_body) >= 2:
            focus_score = max(_MIN_SECTION_RELEVANCE, focus_score)
        score += focus_score

    path_heading = _matched_terms(signals.path_tokens, heading_tokens)
    path_body = _matched_terms(signals.path_tokens, body_tokens) - path_heading
    exact_path = bool(
        signals.normalized_path
        and _normalized_text(signals.normalized_path) in _normalized_text(section.text)
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


def _interval_is_covered(
    start: int,
    end: int,
    intervals: tuple[tuple[int, int], ...],
    starts: tuple[int, ...],
) -> bool:
    """Whether one merged interval covers [start, end); intervals are disjoint and sorted."""
    position = bisect_right(starts, start) - 1
    return position >= 0 and end <= intervals[position][1]


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
    source_characters: int,
) -> tuple[str, AgentsSelectionAudit]:
    intervals = [(0, head_end)] if head_end else []
    intervals.extend((sections[index].start, sections[index].end) for index in selected_indices)
    merged = _merge_intervals(intervals)
    merged_starts = tuple(start for start, _end in merged)
    selected_content = "".join(content[start:end] for start, end in merged)

    selected_entries: list[AgentsSectionAuditEntry] = []
    document_head_end = sections[0].start if sections else len(content)
    if document_head_end and _interval_is_covered(
        0, min(document_head_end, head_end), merged, merged_starts
    ):
        selected_entries.append(AgentsSectionAuditEntry("Document head", 0, ("head",)))
    indexed_entries: list[AgentsSectionAuditEntry] = []
    for index, section in enumerate(sections):
        if _interval_is_covered(section.start, section.end, merged, merged_starts):
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
    # The scanned prefix can be shorter than the file, so omission is measured
    # against the whole normalized source rather than the captured prefix.
    chars_omitted = max(0, source_characters - chars_selected)
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
    """Keep the longest leading index prefix that fits the AGENTS budget.

    A truncated index renders one line per retained entry in place of the single
    empty-list placeholder, so every candidate size follows from the placeholder
    render plus a prefix sum of entry line lengths. Sizes grow strictly with the
    retained count, which selects the same prefix the earlier per-step re-render
    chose while rendering the audit a fixed number of times.
    """
    if _aggregate_agents_bytes(content, audit) <= AGENTS_LIMIT_BYTES:
        return audit
    entries = audit.indexed_only_sections
    without_index = replace(audit, indexed_only_sections=(), index_truncated=True)
    without_index_bytes = _aggregate_agents_bytes(content, without_index)
    fixed_bytes = without_index_bytes - len(_AUDIT_EMPTY_LIST_LINE.encode("utf-8")) - 1
    retained = 0
    entry_bytes = 0
    for position, entry in enumerate(entries[:-1]):
        entry_bytes += len(_audit_index_line(entry).encode("utf-8")) + 1
        if fixed_bytes + entry_bytes > AGENTS_LIMIT_BYTES:
            break
        retained = position + 1
    if retained:
        return replace(audit, indexed_only_sections=entries[:retained], index_truncated=True)
    if without_index_bytes > AGENTS_LIMIT_BYTES:
        raise RuntimeError("AGENTS head and selection audit exceed the AGENTS budget")
    return without_index


def _without_byte_order_mark(source: CollectedFile) -> CollectedFile:
    """Drop one leading UTF-8 BOM so a first-line heading still parses as a heading."""
    if not source.content.startswith("\ufeff"):
        return source
    return replace(
        source,
        content=source.content[1:],
        source_characters=max(0, source.source_characters - 1),
    )


def _bounded_head_fallback(source: CollectedFile, *, parse_fallback: bool) -> CollectedFile:
    """Return a byte-bounded head with no section index.

    The head never exceeds AGENTS_HEAD_LIMIT_BYTES and the audit carries no index
    entries, so the result always fits the larger AGENTS budget.
    """
    head_end = _line_safe_prefix_end(source.content, AGENTS_HEAD_LIMIT_BYTES)
    content, audit = _selection_state(
        source.content,
        (),
        head_end,
        frozenset(),
        {},
        frozenset(),
        parse_fallback=parse_fallback,
        source_scan_truncated=source.truncated,
        source_characters=source.source_characters,
    )
    audit = _fit_agents_index(content, audit)
    return CollectedFile(
        source.name,
        source.language,
        Availability.PRESENT,
        content,
        audit.truncated,
        audit,
    )


def _select_agents_content(source: CollectedFile, signals: _SelectionSignals) -> CollectedFile:
    if not source.is_text:
        return source
    source = _without_byte_order_mark(source)
    try:
        sections = _parse_markdown_sections(source.content)
    except MarkdownSectionParseError:
        return _bounded_head_fallback(source, parse_fallback=True)

    head_end = _small_head_end(source.content, sections)
    selected_indices = {index for index, section in enumerate(sections) if section.end <= head_end}
    reasons: dict[int, set[str]] = {index: {"head"} for index in selected_indices}
    ranked: list[tuple[int, int, frozenset[str]]] = []
    for index, section in enumerate(sections):
        if signals.focus_tokens or signals.path_tokens or signals.normalized_path:
            score, section_reasons = _section_relevance(section, signals)
            if score >= _MIN_SECTION_RELEVANCE:
                ranked.append((score, index, section_reasons))
    ranked.sort(key=lambda item: (-item[0], sections[item[1]].start))

    # Sections are disjoint. Candidate admission therefore needs only the bytes,
    # characters and audit lines changed by that section and its (at most six)
    # parents, rather than rebuilding every section/index for every candidate.
    extra_bytes = [
        len(source.content[max(head_end, section.start) : section.end].encode("utf-8"))
        if section.end > head_end
        else 0
        for section in sections
    ]
    index_sizes = [
        len(
            _audit_index_line(AgentsSectionAuditEntry(s.heading, s.heading_level, ())).encode(
                "utf-8"
            )
        )
        + 1
        for s in sections
    ]
    selected_sizes = {
        index: len(
            _audit_selected_line(
                AgentsSectionAuditEntry(
                    sections[index].heading, sections[index].heading_level, ("head",)
                )
            ).encode("utf-8")
        )
        + 1
        for index in selected_indices
    }
    selected_lines = sum(selected_sizes.values())
    document_head_end = sections[0].start if sections else len(source.content)
    if document_head_end and head_end:
        selected_lines += (
            len(
                _audit_selected_line(AgentsSectionAuditEntry("Document head", 0, ("head",))).encode(
                    "utf-8"
                )
            )
            + 1
        )
    content_bytes = len(source.content[:head_end].encode("utf-8"))
    chars_selected = head_end
    index_count = len(sections) - len(selected_indices)
    index_lines = sum(
        size for index, size in enumerate(index_sizes) if index not in selected_indices
    )
    first_index = len(selected_indices)
    placeholder_bytes = len(_AUDIT_EMPTY_LIST_LINE.encode("utf-8")) + 1
    no_index_bytes = len(_AUDIT_NO_INDEX_LINE.encode("utf-8")) + 1
    empty_sizes = {
        (truncated, index_truncated): len(
            render_agents_selection_audit(
                AgentsSelectionAudit(
                    source="AGENTS.md",
                    selected_sections=(),
                    indexed_only_sections=(),
                    chars_selected=0,
                    chars_omitted=0,
                    truncated=truncated,
                    source_scan_truncated=source.truncated,
                    index_truncated=index_truncated,
                )
            ).encode("utf-8")
        )
        for truncated in (False, True)
        for index_truncated in (False, True)
    }

    def minimum_cost(
        body_bytes: int,
        chars: int,
        selected_line_bytes: int,
        remaining_count: int,
        remaining_lines: int,
        first_remaining: int,
    ) -> int:
        omitted = max(0, source.source_characters - chars)
        truncated = omitted > 0 or source.truncated
        common = body_bytes + len(str(chars)) + len(str(omitted)) - 2
        if selected_line_bytes:
            common += selected_line_bytes - placeholder_bytes
        full = common + empty_sizes[truncated, False]
        if remaining_count:
            full += remaining_lines - placeholder_bytes
            if truncated:
                full -= no_index_bytes
        fitted = common + empty_sizes[truncated, True]
        if remaining_count > 1:
            # A very short first index line can cost less than the empty placeholder.
            fitted += min(0, index_sizes[first_remaining] - placeholder_bytes)
        return min(full, fitted)

    budget_fallback: set[int] = set()
    for _score, index, section_reasons in ranked:
        trial_reasons = {index: reasons.get(index, set()) | set(section_reasons)}
        parent = sections[index].parent_index
        while parent is not None:
            trial_reasons[parent] = reasons.get(parent, set()) | {"parent_context"}
            parent = sections[parent].parent_index
        new_indices = trial_reasons.keys() - selected_indices
        trial_bytes = content_bytes + sum(extra_bytes[item] for item in new_indices)
        trial_chars = chars_selected + sum(
            sections[item].end - max(head_end, sections[item].start) for item in new_indices
        )
        trial_sizes = {
            item: len(
                _audit_selected_line(
                    AgentsSectionAuditEntry(
                        sections[item].heading,
                        sections[item].heading_level,
                        _ordered_reasons(item_reasons),
                    )
                ).encode("utf-8")
            )
            + 1
            for item, item_reasons in trial_reasons.items()
        }
        trial_selected_lines = selected_lines + sum(
            size - selected_sizes.get(item, 0) for item, size in trial_sizes.items()
        )
        trial_index_count = index_count - len(new_indices)
        trial_index_lines = index_lines - sum(index_sizes[item] for item in new_indices)
        trial_first = first_index
        while trial_first < len(sections) and (
            trial_first in selected_indices or trial_first in new_indices
        ):
            trial_first += 1
        if (
            minimum_cost(
                trial_bytes,
                trial_chars,
                trial_selected_lines,
                trial_index_count,
                trial_index_lines,
                trial_first,
            )
            > AGENTS_LIMIT_BYTES
        ):
            budget_fallback.add(index)
        else:
            selected_indices.update(new_indices)
            reasons.update(trial_reasons)
            selected_sizes.update(trial_sizes)
            content_bytes, chars_selected = trial_bytes, trial_chars
            selected_lines = trial_selected_lines
            index_count, index_lines, first_index = (
                trial_index_count,
                trial_index_lines,
                trial_first,
            )

    content, audit = _selection_state(
        source.content,
        sections,
        head_end,
        frozenset(selected_indices),
        reasons,
        frozenset(budget_fallback),
        parse_fallback=False,
        source_scan_truncated=source.truncated,
        source_characters=source.source_characters,
    )
    try:
        audit = _fit_agents_index(content, audit)
    except RuntimeError:
        # A heading-dense head can outgrow the budget on its own; degrade this one
        # source instead of failing the whole context collection.
        return _bounded_head_fallback(source, parse_fallback=False)
    return CollectedFile(
        source.name,
        source.language,
        Availability.PRESENT,
        content,
        audit.truncated,
        audit,
    )
