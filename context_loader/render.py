"""Render collected Git and project context as bounded deterministic Markdown."""

from __future__ import annotations

import os
import re

from .collect import (
    AGENTS_LIMIT_BYTES,
    DECLARED_COMMANDS_LIMIT_BYTES,
    DIRECTORY_TREE_LIMIT_BYTES,
    ENTRY_FILE_LIMIT_BYTES,
    ENTRY_FILES_TOTAL_LIMIT_BYTES,
    README_LIMIT_BYTES,
    TRUNCATION_MARKER,
    CollectedFile,
    DeclaredCommand,
    DirectoryTree,
    ProjectContext,
    TreeEntry,
)
from .git import RecentCommit, RepositoryState

SCHEMA = "context-loader/v0.1"
GLOBAL_OUTPUT_LIMIT_BYTES = 98_304
MAX_CHANGE_ENTRIES = 100
MAX_CHANGE_MARKDOWN_BYTES = 4 * 1024
GLOBAL_OMISSION = "Omitted: global output limit reached."


def _display(value: str) -> str:
    rendered: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "`":
            rendered.append(r"\x60")
        elif 0xDC80 <= codepoint <= 0xDCFF:
            rendered.append(f"\\x{codepoint - 0xDC00:02x}")
        elif character.isprintable():
            rendered.append(character)
        elif codepoint <= 0xFF:
            rendered.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            rendered.append(f"\\u{codepoint:04x}")
        else:
            rendered.append(f"\\U{codepoint:08x}")
    return "".join(rendered)


def _display_limited(value: str, limit: int) -> str | None:
    rendered: list[str] = []
    used = 0
    for character in value:
        piece = _display(character)
        size = len(piece.encode())
        if used + size > limit:
            return None
        rendered.append(piece)
        used += size
    return "".join(rendered)


def _truncate_at_line_boundary(content: str, limit: int) -> tuple[str, bool]:
    encoded = content.encode()
    if len(encoded) <= limit:
        return content, False
    if limit <= 0:
        return "", True
    boundary = encoded.rfind(b"\n", 0, limit + 1)
    if boundary < 0:
        return "", True
    return encoded[: boundary + 1].decode(), True


def _content_with_marker(content: str, limit: int, truncated: bool) -> tuple[str, bool]:
    if not truncated and len(content.encode()) <= limit:
        return content, False
    marker_size = len(TRUNCATION_MARKER.encode())
    prefix, _was_cut = _truncate_at_line_boundary(content, max(0, limit - marker_size))
    return f"{prefix}{TRUNCATION_MARKER}", True


def _fenced(language: str, body: str) -> str:
    runs = (len(match.group(0)) for match in re.finditer(r"`+", body))
    fence = "`" * max(3, max(runs, default=0) + 1)
    separator = "" if not body or body.endswith("\n") else "\n"
    return f"{fence}{language}\n{body}{separator}{fence}"


def _file_payload(source: CollectedFile, limit: int) -> str:
    if source.status is not None:
        return source.status
    body, _truncated = _content_with_marker(source.content, limit, source.truncated)
    return _fenced(source.language, body)


def _change_lines(state: RepositoryState) -> tuple[list[str], bool]:
    lines: list[str] = []
    used_bytes = 0
    for change in state.changes:
        if len(lines) == MAX_CHANGE_ENTRIES:
            return lines, True
        line = f"- `{change.status} {_display(change.path)}`"
        line_bytes = len(f"{line}\n".encode())
        if used_bytes + line_bytes > MAX_CHANGE_MARKDOWN_BYTES:
            return lines, True
        lines.append(line)
        used_bytes += line_bytes
    return lines, False


def _render_git_state(state: RepositoryState) -> str:
    lines = [
        "## Git State",
        "",
        f"- Branch: `{_display(state.branch)}`",
        f"- HEAD: `{_display(state.head)}`",
        f"- Upstream: `{_display(state.upstream)}`",
        f"- Ahead / behind: `{_display(state.ahead_behind)}`",
        f"- Worktree: `{state.worktree}`",
        "",
        "### Working Tree Changes",
        "",
    ]
    changes, truncated = _change_lines(state)
    if changes:
        lines.extend(changes)
    elif not truncated:
        lines.append("No changes.")
    if truncated:
        lines.append("- Additional changes omitted by the 100-entry / 4-KiB limit.")
    return "\n".join(lines)


def _render_source_section(title: str, source: CollectedFile, limit: int) -> str:
    return "\n".join(
        (
            f"## {title}",
            "",
            f"Source: `{source.name}`",
            "",
            _file_payload(source, limit),
        )
    )


def _command_line(command: DeclaredCommand) -> str:
    if command.parse_error:
        return f"- `{command.source}`: unable to parse command declarations."
    assert command.invocation is not None
    invocation = _display(command.invocation)
    if command.target is None:
        return f"- `{invocation}`"
    return f"- `{invocation}` → `{_display(command.target)}`"


def _bounded_lines(lines: list[str], limit: int, *, already_truncated: bool = False) -> str:
    complete = "\n".join(lines)
    if not already_truncated and len(complete.encode()) <= limit:
        return complete
    marker_size = len(TRUNCATION_MARKER.encode())
    budget = max(0, limit - marker_size - 1)
    selected: list[str] = []
    used = 0
    for line in lines:
        size = len(line.encode()) + (1 if selected else 0)
        if used + size > budget:
            break
        selected.append(line)
        used += size
    selected.append(TRUNCATION_MARKER)
    return "\n".join(selected)


def _render_commands(commands: tuple[DeclaredCommand, ...]) -> str:
    lines = [_command_line(command) for command in commands]
    if not lines:
        lines.append("No supported command declarations found.")
    return "\n".join(
        (
            "## Declared Commands",
            "",
            _bounded_lines(lines, DECLARED_COMMANDS_LIMIT_BYTES),
        )
    )


def _entry_payload(source: CollectedFile, remaining: int) -> tuple[str, int]:
    if source.status is not None:
        return source.status, 0
    encoded_size = len(source.content.encode())
    aggregate_truncated = encoded_size > remaining
    needs_marker = source.truncated or aggregate_truncated
    marker_size = len(TRUNCATION_MARKER.encode()) if needs_marker else 0
    content_limit = min(remaining, ENTRY_FILE_LIMIT_BYTES - marker_size)
    visible, was_cut = _truncate_at_line_boundary(source.content, max(0, content_limit))
    needs_marker = needs_marker or was_cut
    body = f"{visible}{TRUNCATION_MARKER}" if needs_marker else visible
    return _fenced(source.language, body), len(visible.encode())


def _render_entry_files(entry_files: tuple[CollectedFile, ...]) -> str:
    parts = ["## Project Entry Files"]
    used_content = 0
    for source in entry_files:
        remaining = max(0, ENTRY_FILES_TOTAL_LIMIT_BYTES - used_content)
        payload, consumed = _entry_payload(source, remaining)
        used_content += consumed
        parts.extend(("", f"### `{source.name}`", "", payload))
    return "\n".join(parts)


def _render_recent_commits(commits: tuple[RecentCommit, ...]) -> str | None:
    lines = ["## Recent Commits", ""]
    if not commits:
        lines.append("No commits.")
        return "\n".join(lines)
    used = len(b"## Recent Commits\n\n")
    for commit in commits:
        prefix = f"- `{commit.object_id[:12]}` · "
        date = _display_limited(commit.author_date, GLOBAL_OUTPUT_LIMIT_BYTES)
        if date is None:
            return None
        prefix = f"{prefix}{date} · "
        remaining = GLOBAL_OUTPUT_LIMIT_BYTES - used - len(prefix.encode()) - 1
        subject = _display_limited(commit.subject, max(0, remaining))
        if subject is None:
            return None
        line = f"{prefix}{subject}"
        used += len(line.encode()) + 1
        if used > GLOBAL_OUTPUT_LIMIT_BYTES:
            return None
        lines.append(line)
    return "\n".join(lines)


def _tree_line(entry: TreeEntry) -> str:
    path = _display(entry.path)
    if entry.kind == "directory":
        return f"{path}/"
    if entry.kind == "symlink":
        return f"{path}@"
    if entry.kind == "unreadable_directory":
        return f"{path}/ [Skipped: unreadable.]" if path else "Skipped: unreadable directory."
    return path


def _render_directory_tree(tree: DirectoryTree) -> str:
    lines = [_tree_line(entry) for entry in tree.entries]
    if not lines:
        lines.append("No entries.")
    body = _bounded_lines(
        lines,
        DIRECTORY_TREE_LIMIT_BYTES,
        already_truncated=tree.truncated,
    )
    return "\n".join(("## Directory Tree", "", _fenced("text", body)))


def _omitted_section(title: str) -> str:
    return f"## {title}\n\n{GLOBAL_OMISSION}"


def render_markdown(state: RepositoryState, project: ProjectContext) -> bytes:
    """Render all sections in fixed order without exceeding the global byte limit."""
    header = "\n".join(
        (
            "# Codex Project Context",
            "",
            f"- Schema: `{SCHEMA}`",
            f"- Repository: `{_display(os.fspath(state.repository))}`",
        )
    )
    sections: list[tuple[str, str | None]] = [
        ("Git State", _render_git_state(state)),
        (
            "Development Instructions",
            _render_source_section(
                "Development Instructions",
                project.instructions,
                AGENTS_LIMIT_BYTES,
            ),
        ),
        (
            "Project Overview",
            _render_source_section("Project Overview", project.overview, README_LIMIT_BYTES),
        ),
        ("Declared Commands", _render_commands(project.commands)),
        ("Project Entry Files", _render_entry_files(project.entry_files)),
        ("Recent Commits", _render_recent_commits(state.commits)),
        ("Directory Tree", _render_directory_tree(project.directory_tree)),
    ]

    rendered_parts = [header.encode()]
    omission_started = False
    for index, (title, full_section) in enumerate(sections):
        omission = _omitted_section(title).encode()
        future_omissions = [
            _omitted_section(future_title).encode()
            for future_title, _future_section in sections[index + 1 :]
        ]
        if omission_started or full_section is None:
            selected = omission
            omission_started = True
        else:
            candidate = full_section.encode()
            tentative = b"\n\n".join((*rendered_parts, candidate, *future_omissions)) + b"\n"
            if len(tentative) <= GLOBAL_OUTPUT_LIMIT_BYTES:
                selected = candidate
            else:
                selected = omission
                omission_started = True
        rendered_parts.append(selected)

    output = b"\n\n".join(rendered_parts) + b"\n"
    if len(output) > GLOBAL_OUTPUT_LIMIT_BYTES:
        raise RuntimeError("global context output limit could not be satisfied")
    return output
