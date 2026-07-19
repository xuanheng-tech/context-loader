"""Collect bounded root-file and directory context without executing repository code."""

from __future__ import annotations

import codecs
import errno
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

AGENTS_LIMIT_BYTES = 16 * 1024
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


@dataclass(frozen=True, slots=True)
class CollectedFile:
    name: str
    language: str
    status: str | None
    content: str = ""
    truncated: bool = False

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


def collect_project_context(repository: Path) -> ProjectContext:
    """Collect only fixed root candidates and a bounded two-level directory tree."""
    instructions = _collect_root_file(repository, "AGENTS.md", "markdown", AGENTS_LIMIT_BYTES)
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
