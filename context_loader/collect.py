"""Collect bounded root-file and directory context without executing repository code."""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from pathlib import Path

from .agents import (
    _parse_markdown_sections as _parse_markdown_sections,
)
from .agents import (
    _select_agents_content,
    _selection_signals,
)
from .agents import (
    render_agents_selection_audit as render_agents_selection_audit,
)
from .filesystem import (
    DirectoryScanLimitError,
    enumerate_directory,
    open_bounded_regular_file,
    open_child_directory,
    open_directory,
    read_validated_text,
    serialized_display_length,
)
from .model import (
    AGENTS_HEAD_LIMIT_BYTES as AGENTS_HEAD_LIMIT_BYTES,
)
from .model import (
    AGENTS_LIMIT_BYTES as AGENTS_LIMIT_BYTES,
)
from .model import (
    AGENTS_SCAN_LIMIT_BYTES,
    DIRECTORY_TREE_INCOMPLETE_STATUS_LIMIT,
    DIRECTORY_TREE_MAX_DEPTH,
    DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY,
    DIRECTORY_TREE_MAX_ITEMS,
    ENTRY_FILE_LIMIT_BYTES,
    ENTRY_FILE_SPECS,
    FILE_SCAN_LIMIT_BYTES,
    NESTED_AGENTS_EXCLUDED_DIRECTORIES,
    NESTED_AGENTS_MAX_DEPTH,
    NESTED_AGENTS_MAX_DIRECTORIES,
    NESTED_AGENTS_MAX_ENTRIES_PER_DIRECTORY,
    NESTED_AGENTS_MAX_FILES,
    NESTED_AGENTS_MAX_LIST_BYTES,
    README_LIMIT_BYTES,
    Availability,
    CollectedFile,
    DeclaredCommand,
    DirectoryTree,
    NestedContextPresence,
    ProjectContext,
    TreeEntry,
)
from .model import (
    AgentsSelectionInputError as AgentsSelectionInputError,
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
    r"(?:[ \t]+[A-Za-z0-9][A-Za-z0-9_.-]*)*)[ \t]*(?:::|:)(?![:=])"
)


def _collect_root_file(root: Path, name: str, language: str, limit: int) -> CollectedFile:
    """Read one bounded root candidate and record why it is usable or unusable.

    Each availability reason is set at the moment its condition is observed, so the
    machine code and the displayed sentence can never drift apart.
    """
    if len(Path(name).parts) != 1 or Path(name).name != name:
        return CollectedFile(name, language, Availability.UNREADABLE)
    path = root / name
    if path.parent != root:
        return CollectedFile(name, language, Availability.UNREADABLE)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return CollectedFile(name, language, Availability.NOT_PRESENT)
    except OSError:
        return CollectedFile(name, language, Availability.UNREADABLE)
    if stat.S_ISLNK(metadata.st_mode):
        return CollectedFile(name, language, Availability.SYMLINK)
    if not stat.S_ISREG(metadata.st_mode):
        return CollectedFile(name, language, Availability.NOT_REGULAR)
    if metadata.st_size > FILE_SCAN_LIMIT_BYTES:
        return CollectedFile(name, language, Availability.UNREADABLE)

    file_descriptor, reason = open_bounded_regular_file(path)
    if reason is not Availability.PRESENT:
        return CollectedFile(name, language, reason)
    try:
        content, truncated, read_reason, source_characters = read_validated_text(
            file_descriptor, limit
        )
    finally:
        os.close(file_descriptor)
    if read_reason is not Availability.PRESENT:
        return CollectedFile(name, language, read_reason)
    return CollectedFile(
        name,
        language,
        Availability.PRESENT,
        content,
        truncated,
        source_characters=source_characters,
    )


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
            pending_private = pending_private or "private" in names
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
        if match is None:
            continue
        prerequisites = line[match.end() :].split(";", 1)[0]
        if "=" in prerequisites:
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


def _entry_slots(directories: int, others: int, remaining: int) -> tuple[int, int]:
    """Split one directory's remaining item budget between its own two entry groups.

    When both groups cannot fit, each may take up to half of what remains before the
    other claims the leftover, so a directory's own files are never crowded out entirely
    by its alphabetically-earlier subdirectories.
    """
    budget = max(0, remaining)
    if directories + others <= budget:
        return directories, others
    half = budget // 2
    directory_slots = min(directories, half)
    other_slots = min(others, budget - directory_slots)
    return min(budget - other_slots, directories), other_slots


def _collect_directory_tree(root: Path) -> DirectoryTree:
    """List one repository tree root-first, with a bounded and honest enumeration.

    Two limits act in sequence, and the guarantee depends on both:

    1. ``DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY`` decides which names a directory *offers* to
       the listing. A directory at or below the limit offers all of its names; above it, only the
       alphabetically first ``limit`` names are offered and the rest are never seen.
    2. ``DIRECTORY_TREE_MAX_ITEMS`` together with :func:`_entry_slots` decides which of the
       offered names are *kept*: within a directory, subdirectories and non-directories each get
       a share of the remaining budget, so one group cannot crowd the other out entirely.

    Descending happens after a directory's own names are kept, so deep content cannot displace
    top-level names. A root-level non-directory is listed only when those two limits and the
    section's own byte budget, applied in :mod:`context_loader.render`, all leave room for it: the
    enumeration must offer it, the item budget must still have a slot, and the byte budget must
    still have room to render the line. Being offered means surviving the alphabetical cut, not an
    uncapped directory -- a capped root still lists every offered name whose group keeps a slot,
    and never offers anything sorting after its retained prefix. Each limit bites on its own, so no
    single one of them is sufficient. Every case is reported as incomplete or truncated rather than
    presented as a full listing, and capped directories are counted in full while only a bounded set
    of examples is retained, so the claim cannot itself grow the listing.
    """
    collected: list[TreeEntry] = []
    incomplete: list[str] = []
    truncated = False

    def add(entry_path: str, kind: str) -> int | None:
        nonlocal truncated
        if len(collected) >= DIRECTORY_TREE_MAX_ITEMS:
            truncated = True
            return None
        collected.append(TreeEntry(entry_path, kind))
        return len(collected) - 1

    def relative(prefix: str, name: str) -> str:
        return f"{prefix}/{name}" if prefix else name

    def mark_unreadable(index: int, entry_path: str) -> None:
        collected[index] = TreeEntry(entry_path, "unreadable_directory")

    def walk(file_descriptor: int, prefix: str, depth: int) -> None:
        nonlocal truncated
        listing = enumerate_directory(file_descriptor, DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY)
        if listing.capped:
            # A capped enumeration always leaves entries unlisted, even when the item
            # budget itself was not the binding limit.
            incomplete.append(prefix)
            truncated = True
        directories = [
            entry for entry in listing.entries if entry.is_directory and not entry.unclassified
        ]
        others = [
            entry for entry in listing.entries if not entry.is_directory or entry.unclassified
        ]
        directory_slots, other_slots = _entry_slots(
            len(directories), len(others), DIRECTORY_TREE_MAX_ITEMS - len(collected)
        )
        if directory_slots < len(directories) or other_slots < len(others):
            truncated = True
        descent: list[tuple[str, str, int]] = []
        for entry in directories[:directory_slots]:
            entry_path = relative(prefix, entry.name)
            if depth == 0 and entry.name == ".git":
                add(entry_path, "directory")
                continue
            index = add(entry_path, "directory")
            if index is None or depth + 1 >= DIRECTORY_TREE_MAX_DEPTH:
                continue
            descent.append((entry.name, entry_path, index))
        for entry in others[:other_slots]:
            kind = (
                "unreadable_entry"
                if entry.unclassified
                else "symlink"
                if entry.is_symlink
                else "file"
            )
            add(relative(prefix, entry.name), kind)
        for name, entry_path, index in descent:
            try:
                child_descriptor = open_child_directory(file_descriptor, name)
            except OSError:
                mark_unreadable(index, entry_path)
                continue
            try:
                walk(child_descriptor, entry_path, depth + 1)
            except DirectoryScanLimitError:
                collected[index] = TreeEntry(entry_path, "scan_limit_directory")
                truncated = True
            except OSError:
                mark_unreadable(index, entry_path)
            finally:
                os.close(child_descriptor)

    try:
        root_descriptor = open_directory(root)
    except OSError:
        return DirectoryTree((TreeEntry("", "unreadable_directory"),))
    try:
        try:
            walk(root_descriptor, "", 0)
        except DirectoryScanLimitError:
            collected.append(TreeEntry("", "scan_limit_directory"))
            truncated = True
        except OSError:
            if not collected:
                collected.append(TreeEntry("", "unreadable_directory"))
    finally:
        os.close(root_descriptor)
    ordered = sorted(incomplete)
    return DirectoryTree(
        tuple(collected),
        truncated,
        tuple(ordered[:DIRECTORY_TREE_INCOMPLETE_STATUS_LIMIT]),
        len(ordered),
        DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY,
    )


def collect_nested_agents_presence(repository: Path) -> NestedContextPresence:
    """List nested AGENTS.md paths under a bounded, contents-blind directory scan.

    The scan reads directory entries only: it never opens a candidate file, never
    traverses a symlink, and reports truncation honestly so presence claims stay
    auditable. Any non-directory entry named AGENTS.md is listed, including
    symlinked ones, because existence comes from enumeration alone; entry types
    are never resolved beyond directory-vs-not, and contents remain the caller's
    responsibility to read.
    """
    files: list[str] = []
    reported_bytes = 0
    state = {"list_truncated": False, "scan_truncated": False, "directories": 0}

    def walk(directory_descriptor: int, prefix: str, depth: int) -> None:
        nonlocal reported_bytes
        try:
            listing = enumerate_directory(
                directory_descriptor, NESTED_AGENTS_MAX_ENTRIES_PER_DIRECTORY
            )
        except OSError:
            state["scan_truncated"] = True
            return
        if listing.capped:
            state["scan_truncated"] = True
        subdirectories: list[tuple[str, str]] = []
        for entry in listing.entries:
            if entry.unclassified:
                state["scan_truncated"] = True
                continue
            path = f"{prefix}/{entry.name}" if prefix else entry.name
            if entry.is_directory:
                if entry.name not in NESTED_AGENTS_EXCLUDED_DIRECTORIES:
                    subdirectories.append((entry.name, path))
                continue
            if entry.name != "AGENTS.md" or not prefix:
                continue
            if len(files) >= NESTED_AGENTS_MAX_FILES:
                state["list_truncated"] = True
                continue
            # Charge the array's two bracket bytes to the first entry, so the budget
            # covers everything this list makes the document emit.
            name_bytes = serialized_display_length(path) + (1 if files else 2)
            if reported_bytes + name_bytes > NESTED_AGENTS_MAX_LIST_BYTES:
                state["list_truncated"] = True
                continue
            reported_bytes += name_bytes
            files.append(path)
        for name, path in subdirectories:
            if (
                depth >= NESTED_AGENTS_MAX_DEPTH
                or state["directories"] >= NESTED_AGENTS_MAX_DIRECTORIES
            ):
                state["scan_truncated"] = True
                break
            state["directories"] += 1
            try:
                child_descriptor = open_child_directory(directory_descriptor, name)
            except OSError:
                state["scan_truncated"] = True
                continue
            try:
                walk(child_descriptor, path, depth + 1)
            finally:
                os.close(child_descriptor)

    if NESTED_AGENTS_MAX_DIRECTORIES < 1:
        return NestedContextPresence((), False, True)
    state["directories"] = 1
    try:
        root_descriptor = open_directory(repository)
    except OSError:
        return NestedContextPresence((), False, True)
    try:
        walk(root_descriptor, "", 0)
    finally:
        os.close(root_descriptor)
    return NestedContextPresence(tuple(files), state["list_truncated"], state["scan_truncated"])


def collect_project_context(
    repository: Path,
    *,
    focus: str | None = None,
    path: str | None = None,
    include_nested_context: bool = True,
) -> ProjectContext:
    """Collect fixed root candidates, selecting AGENTS sections from bounded signals.

    Markdown callers may skip the contents-blind nested scan. Its empty result then
    remains explicitly incomplete, rather than claiming no nested instructions exist.
    """
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
        nested_context=(
            collect_nested_agents_presence(repository)
            if include_nested_context
            else NestedContextPresence((), scan_truncated=True)
        ),
    )
