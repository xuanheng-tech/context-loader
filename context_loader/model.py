"""Shared bounded limits and frozen value types for project-context collection.

This module is the vocabulary of the package: it depends on nothing but the
standard library and never on collection, rendering, Git or CLI code, so every
layer can speak the same types without importing each other.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

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
# Deliberately above the item budget: which of a directory's own entries are shown must
# be decided by the item budget and its root-file guarantee, not by enumeration order.
# It is still a hard cap, so one huge directory can never be materialised in memory.
DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY = 512
# A capped directory is reported once as a number plus a bounded set of examples, so the
# completeness claim cannot itself inflate the section it describes.
DIRECTORY_TREE_INCOMPLETE_NOTE_EXAMPLES = 3
DIRECTORY_TREE_INCOMPLETE_STATUS_LIMIT = 8
NESTED_AGENTS_MAX_DEPTH = 4
NESTED_AGENTS_MAX_DIRECTORIES = 2_000
NESTED_AGENTS_MAX_FILES = 32
NESTED_AGENTS_MAX_LIST_BYTES = 4 * 1024
# The presence scan inspects names only and lists no directory entries at all, so it can
# examine a wider prefix per directory than the tree does; exceeding it is scan truncation.
NESTED_AGENTS_MAX_ENTRIES_PER_DIRECTORY = 1_024
NESTED_AGENTS_EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".venv", "node_modules", "site-packages", "venv"}
)
FILE_SCAN_LIMIT_BYTES = 16 * 1024 * 1024
MAX_FILE_SCAN_BYTES = FILE_SCAN_LIMIT_BYTES
TRUNCATION_MARKER = "… truncated by context-loader …"

ENTRY_FILE_SPECS = (
    ("pyproject.toml", "toml"),
    ("package.json", "json"),
    ("Makefile", "make"),
    ("Cargo.toml", "toml"),
    ("go.mod", "text"),
)


class Availability(enum.Enum):
    """One machine-readable observation of why a root candidate is usable or not.

    Each member's ``value`` is exactly the status code emitted for that condition,
    and the display sentence is derived from the member at render time, so a wording
    change can never move a source onto the wrong code.
    """

    PRESENT = "present"
    NOT_PRESENT = "not_present"
    SYMLINK = "skipped_symlink"
    NOT_REGULAR = "skipped_not_regular"
    ENCODING = "skipped_encoding"
    UNREADABLE = "skipped_unreadable"


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
    """One root candidate: the typed reason it is usable or unusable, and its captured text.

    ``reason`` is set once, at the moment the collector observes the condition, and carries no
    display wording; ``content`` is only meaningful while the file is text.
    """

    name: str
    language: str
    reason: Availability = Availability.PRESENT
    content: str = ""
    truncated: bool = False
    selection: AgentsSelectionAudit | None = None
    source_characters: int = 0

    @property
    def is_text(self) -> bool:
        return self.reason is Availability.PRESENT


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
    """A bounded directory listing that never overstates what it examined.

    ``truncated`` claims that entries exist beyond this listing. A directory holding more
    entries than one enumeration examines is counted in ``incomplete_count`` against
    ``enumeration_limit``, and ``incomplete_directories`` retains only the first
    ``DIRECTORY_TREE_INCOMPLETE_STATUS_LIMIT`` of them, so a partial listing of a directory is
    never presented as a complete one and the claim stays bounded however many hit the cap.

    Root-level files are listed ahead of descent only while the root's own enumeration stays
    below ``enumeration_limit``. Once a directory is capped, the retained names are simply the
    alphabetically first ones and this listing makes no claim about which files survived.
    """

    entries: tuple[TreeEntry, ...]
    truncated: bool = False
    incomplete_directories: tuple[str, ...] = ()
    incomplete_count: int = 0
    enumeration_limit: int = 0


@dataclass(frozen=True, slots=True)
class NestedContextPresence:
    """Existence-only index of nested AGENTS.md files; their contents are never read."""

    files: tuple[str, ...]
    list_truncated: bool = False
    scan_truncated: bool = False


@dataclass(frozen=True, slots=True)
class ProjectContext:
    instructions: CollectedFile
    overview: CollectedFile
    entry_files: tuple[CollectedFile, ...]
    commands: tuple[DeclaredCommand, ...]
    directory_tree: DirectoryTree
    nested_context: NestedContextPresence
