"""Render collected Git and project context as bounded deterministic Markdown."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .agents import render_agents_selection_audit
from .filesystem import display_text
from .git import RecentCommit, RepositoryState
from .model import (
    AGENTS_LIMIT_BYTES,
    DECLARED_COMMANDS_LIMIT_BYTES,
    DIRECTORY_TREE_INCOMPLETE_NOTE_EXAMPLES,
    DIRECTORY_TREE_LIMIT_BYTES,
    ENTRY_FILE_LIMIT_BYTES,
    ENTRY_FILES_TOTAL_LIMIT_BYTES,
    MAX_CHANGE_ENTRIES,
    README_LIMIT_BYTES,
    TRUNCATION_MARKER,
    Availability,
    CollectedFile,
    DeclaredCommand,
    DirectoryTree,
    ProjectContext,
    TreeEntry,
)

SCHEMA = "context-loader/v0.1"
GLOBAL_OUTPUT_LIMIT_BYTES = 98_304
MAX_CHANGE_MARKDOWN_BYTES = 4 * 1024
GLOBAL_OMISSION = "Omitted: global output limit reached."


@dataclass(frozen=True, slots=True)
class MarkdownRender:
    output: bytes
    included_sections: tuple[str, ...]
    omitted_sections: tuple[str, ...]
    changes_truncated: bool
    commands_truncated: bool
    tree_truncated: bool
    source_bodies: tuple[RenderedSourceBody, ...]


@dataclass(frozen=True, slots=True)
class RenderedSourceBody:
    """One collected source as it actually entered the rendered context.

    ``truncated`` is the renderer's own claim that this body was shortened by a render
    budget, so machine output never has to look for a marker inside rendered text.
    """

    source: CollectedFile
    body: str
    truncated: bool


def _unavailable_sentence(reason: Availability) -> str:
    """Derive the fixed display sentence for one unusable source.

    The reason is the fact and the sentence is its rendering: an unusable reason raises
    rather than being mapped to a plausible-looking sentence.
    """
    if reason is Availability.NOT_PRESENT:
        return "Not present."
    if reason is Availability.SYMLINK:
        return "Skipped: symlink."
    if reason is Availability.NOT_REGULAR:
        return "Skipped: not a regular file."
    if reason is Availability.ENCODING:
        return "Skipped: unsupported text encoding."
    if reason is Availability.UNREADABLE:
        return "Skipped: unreadable."
    raise AssertionError(f"no display sentence for source reason {reason!r}")


def _display_limited(value: str, limit: int) -> str | None:
    rendered: list[str] = []
    used = 0
    for character in value:
        piece = display_text(character)
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


def _file_body(source: CollectedFile, limit: int) -> tuple[str, bool] | None:
    """Rendered body plus whether a render budget cut it; ``None`` when unusable."""
    if not source.is_text:
        return None
    if source.selection is not None:
        return source.content, False
    body, truncated = _content_with_marker(source.content, limit, source.truncated)
    return body, truncated


def _file_payload(source: CollectedFile, body: RenderedSourceBody | None) -> str:
    if body is None:
        return _unavailable_sentence(source.reason)
    return _fenced(source.language, body.body)


def _change_lines(state: RepositoryState) -> tuple[list[str], bool]:
    lines: list[str] = []
    used_bytes = 0
    for change in state.changes:
        if len(lines) == MAX_CHANGE_ENTRIES:
            return lines, True
        line = f"- `{change.status} {display_text(change.path)}`"
        line_bytes = len(f"{line}\n".encode())
        if used_bytes + line_bytes > MAX_CHANGE_MARKDOWN_BYTES:
            return lines, True
        lines.append(line)
        used_bytes += line_bytes
    return lines, state.changes_truncated


def _render_git_state(state: RepositoryState) -> str:
    lines = [
        "## Git State",
        "",
        f"- Branch: `{display_text(state.branch)}`",
        f"- HEAD: `{display_text(state.head)}`",
        f"- Upstream: `{display_text(state.upstream)}`",
        f"- Ahead / behind: `{display_text(state.ahead_behind)}`",
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


def _render_source_section(
    title: str, source: CollectedFile, body: RenderedSourceBody | None
) -> str:
    parts = [
        f"## {title}",
        "",
        f"Source: `{source.name}`",
        "",
        _file_payload(source, body),
    ]
    if source.selection is not None:
        parts.extend(("", render_agents_selection_audit(source.selection)))
    return "\n".join(parts)


def _command_line(command: DeclaredCommand) -> str:
    if command.parse_error:
        return f"- `{command.source}`: unable to parse command declarations."
    assert command.invocation is not None
    invocation = display_text(command.invocation)
    if command.target is None:
        return f"- `{invocation}`"
    return f"- `{invocation}` → `{display_text(command.target)}`"


def _bounded_lines(
    lines: list[str], limit: int, *, already_truncated: bool = False
) -> tuple[str, bool]:
    complete = "\n".join(lines)
    if not already_truncated and len(complete.encode()) <= limit:
        return complete, False
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
    return "\n".join(selected), True


def _render_commands(commands: tuple[DeclaredCommand, ...]) -> tuple[str, bool]:
    lines = [_command_line(command) for command in commands]
    if not lines:
        lines.append("No supported command declarations found.")
    body, truncated = _bounded_lines(lines, DECLARED_COMMANDS_LIMIT_BYTES)
    return "\n".join(("## Declared Commands", "", body)), truncated


def _entry_body(source: CollectedFile, remaining: int) -> tuple[str | None, int, bool]:
    """Rendered body, consumed aggregate bytes, and whether a render budget cut it."""
    if not source.is_text:
        return None, 0, False
    encoded_size = len(source.content.encode())
    limit = max(0, min(remaining, ENTRY_FILE_LIMIT_BYTES))
    needs_marker = source.truncated or encoded_size > limit
    marker_size = len(TRUNCATION_MARKER.encode()) if needs_marker else 0
    content_limit = limit - marker_size
    visible, was_cut = _truncate_at_line_boundary(source.content, max(0, content_limit))
    needs_marker = needs_marker or was_cut
    marker = TRUNCATION_MARKER if needs_marker and limit >= marker_size else ""
    body = f"{visible}{marker}"
    return body, len(body.encode()), needs_marker


def _entry_bodies(entry_files: tuple[CollectedFile, ...]) -> tuple[RenderedSourceBody, ...]:
    """Meter actual bodies, reserving room for later sources' truncation markers."""
    marker_size = len(TRUNCATION_MARKER.encode())
    reserved = sum(
        marker_size
        for source in entry_files
        if source.is_text and (source.content or source.truncated)
    )
    used = 0
    rendered: list[RenderedSourceBody] = []
    for source in entry_files:
        if source.is_text and (source.content or source.truncated):
            reserved -= marker_size
        remaining = max(0, ENTRY_FILES_TOTAL_LIMIT_BYTES - used - reserved)
        body, consumed, truncated = _entry_body(source, remaining)
        used += consumed
        if body is not None:
            rendered.append(RenderedSourceBody(source, body, truncated))
    return tuple(rendered)


def _render_entry_files(
    entry_files: tuple[CollectedFile, ...], bodies: dict[str, RenderedSourceBody]
) -> str:
    parts = ["## Project Entry Files"]
    for source in entry_files:
        payload = _file_payload(source, bodies.get(source.name))
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
    path = display_text(entry.path)
    if entry.kind == "directory":
        return f"{path}/"
    if entry.kind == "symlink":
        return f"{path}@"
    if entry.kind == "unreadable_directory":
        return f"{path}/ [Skipped: unreadable.]" if path else "Skipped: unreadable directory."
    if entry.kind == "unreadable_entry":
        return f"{path} [Skipped: unable to classify.]"
    if entry.kind == "scan_limit_directory":
        note = "Skipped: directory name scan limit reached."
        return f"{path}/ [{note}]" if path else note
    return path


def _incomplete_directory_note(tree: DirectoryTree) -> str:
    """One bounded line whose every claim is something the collection actually observed.

    The listing is limited three times over: a capped directory offers only an alphabetical
    prefix, the item budget keeps only some offered entries, and the section budget cuts what
    is rendered. The wording must stay true under all three, so it never states how many names
    reached the listing.
    """
    shown = tree.incomplete_directories[:DIRECTORY_TREE_INCOMPLETE_NOTE_EXAMPLES]
    examples = ", ".join(f"`{display_text(path) if path else '.'}`" for path in shown)
    further = len(tree.incomplete_directories) - len(shown)
    if further > 0:
        examples += f" (+{further} more named in statuses)"
    unnamed = tree.incomplete_count - len(tree.incomplete_directories)
    if unnamed > 0:
        remainder = "directory" if unnamed == 1 else "directories"
        examples += f" ({unnamed} further {remainder} left unnamed here)"
    stem = "directory" if tree.incomplete_count == 1 else "directories"
    limit = tree.enumeration_limit
    return (
        f"Listing incomplete: {tree.incomplete_count} {stem} held more entries than the "
        f"{limit}-entry per-directory retention limit. All names were scanned to select an "
        f"alphabetically first prefix; only that prefix was retained and classified. This "
        f"section also keeps only as many entries as its own entry and byte budgets allow. "
        f"Named here: {examples}."
    )


def _render_directory_tree(tree: DirectoryTree) -> tuple[str, bool]:
    """Render the tree section and report whether this render cut the listing itself.

    The second value is the renderer's own claim: the collected listing can be complete and
    still not fit the section budget, and a reader of the machine formats must not have to
    look for the marker inside rendered text to learn that.
    """
    lines = [_tree_line(entry) for entry in tree.entries]
    if not lines:
        lines.append("No entries.")
    note = _incomplete_directory_note(tree) if tree.incomplete_count else ""
    # Charged to the listing's own byte budget before the listing is cut, so the section stays
    # bounded however many directories are capped. The cost is paid in listing lines: the note
    # reserves room for itself, so a longer note can displace several listing lines, and the
    # truncation marker plus `truncated` then say so.
    reserve = len(note.encode("utf-8")) + 2 if note else 0
    body, cut_by_budget = _bounded_lines(
        lines,
        max(0, DIRECTORY_TREE_LIMIT_BYTES - reserve),
        already_truncated=tree.truncated,
    )
    parts = ["## Directory Tree", "", _fenced("text", body)]
    if note:
        parts.extend(("", note))
    return "\n".join(parts), cut_by_budget


def _omitted_section(title: str) -> str:
    return f"## {title}\n\n{GLOBAL_OMISSION}"


def render_markdown_with_details(state: RepositoryState, project: ProjectContext) -> MarkdownRender:
    """Render all sections in fixed order without exceeding the global byte limit."""
    header = "\n".join(
        (
            "# Project Context",
            "",
            f"- Schema: `{SCHEMA}`",
            f"- Repository: `{display_text(os.fspath(state.repository))}`",
        )
    )
    commands_section, commands_truncated = _render_commands(project.commands)
    changes_truncated = _change_lines(state)[1]
    tree_section, tree_truncated = _render_directory_tree(project.directory_tree)
    all_bodies = _source_bodies(project)
    bodies = {body.source.name: body for body in all_bodies}
    sections: list[tuple[str, str | None]] = [
        ("Git State", _render_git_state(state)),
        (
            "Development Instructions",
            _render_source_section(
                "Development Instructions",
                project.instructions,
                bodies.get(project.instructions.name),
            ),
        ),
        (
            "Project Overview",
            _render_source_section(
                "Project Overview", project.overview, bodies.get(project.overview.name)
            ),
        ),
        ("Declared Commands", commands_section),
        ("Project Entry Files", _render_entry_files(project.entry_files, bodies)),
        ("Recent Commits", _render_recent_commits(state.commits)),
        ("Directory Tree", tree_section),
    ]

    rendered_parts = [header.encode()]
    included_sections: list[str] = []
    omitted_sections: list[str] = []
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
            omitted_sections.append(title)
        else:
            candidate = full_section.encode()
            tentative = b"\n\n".join((*rendered_parts, candidate, *future_omissions)) + b"\n"
            if len(tentative) <= GLOBAL_OUTPUT_LIMIT_BYTES:
                selected = candidate
                included_sections.append(title)
            else:
                selected = omission
                omission_started = True
                omitted_sections.append(title)
        rendered_parts.append(selected)

    output = b"\n\n".join(rendered_parts) + b"\n"
    if len(output) > GLOBAL_OUTPUT_LIMIT_BYTES:
        raise RuntimeError("global context output limit could not be satisfied")
    return MarkdownRender(
        output=output,
        included_sections=tuple(included_sections),
        omitted_sections=tuple(omitted_sections),
        changes_truncated=changes_truncated,
        tree_truncated=tree_truncated,
        commands_truncated=commands_truncated,
        source_bodies=_included_source_bodies(all_bodies, tuple(included_sections)),
    )


def _source_bodies(project: ProjectContext) -> tuple[RenderedSourceBody, ...]:
    rendered: list[RenderedSourceBody] = []
    for source, limit in (
        (project.instructions, AGENTS_LIMIT_BYTES),
        (project.overview, README_LIMIT_BYTES),
    ):
        body = _file_body(source, limit)
        if body is not None:
            rendered.append(RenderedSourceBody(source, body[0], body[1]))
    rendered.extend(_entry_bodies(project.entry_files))
    return tuple(rendered)


def _included_source_bodies(
    bodies: tuple[RenderedSourceBody, ...], included_sections: tuple[str, ...]
) -> tuple[RenderedSourceBody, ...]:
    included = frozenset(included_sections)
    return tuple(
        body
        for body in bodies
        if (
            "Development Instructions"
            if body.source.name == "AGENTS.md"
            else "Project Overview"
            if body.source.name == "README.md"
            else "Project Entry Files"
        )
        in included
    )


def rendered_source_contents(
    project: ProjectContext, included_sections: tuple[str, ...]
) -> tuple[RenderedSourceBody, ...]:
    """Return source-file bodies under the same budgets used by the Markdown renderer."""
    return _included_source_bodies(_source_bodies(project), included_sections)


def render_markdown(state: RepositoryState, project: ProjectContext) -> bytes:
    """Render all sections in fixed order without exceeding the global byte limit."""
    return render_markdown_with_details(state, project).output
