"""Safe, bounded filesystem primitives shared by every context collection scan.

Everything here is descriptor-relative, never follows a symlink across a boundary it
does not report, and caps its own work: this module reads directory entries and file
bytes, and it reports what it observed as typed availability reasons rather than as
prose, so callers cannot lose the difference between absence and a bounded claim.
"""

from __future__ import annotations

import codecs
import errno
import json
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from heapq import nsmallest
from pathlib import Path

from .model import FILE_SCAN_LIMIT_BYTES, Availability

DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
REGULAR_FILE_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True, slots=True)
class DirectoryEntryFacts:
    """What one retained directory entry revealed without opening or resolving it."""

    name: str
    is_directory: bool
    is_symlink: bool
    unclassified: bool


@dataclass(frozen=True, slots=True)
class DirectoryListing:
    """A sorted, capped enumeration of one directory.

    ``capped`` is the honest claim that the directory holds more entries than were
    examined, so a caller can never present this listing as complete.
    """

    entries: tuple[DirectoryEntryFacts, ...]
    capped: bool


def display_text(value: str) -> str:
    r"""Render one filesystem or Git string as printable, encodable ASCII-safe text.

    Backticks, lone surrogates produced by ``surrogateescape`` decoding, and every
    other non-printable codepoint become ``\xNN``/``\uNNNN``/``\UNNNNNNNN`` escapes so
    the character can cross the JSON and Markdown serialization boundaries intact.
    """
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


def serialized_display_length(value: str) -> int:
    """Return the UTF-8 byte size of ``value`` once escaped and JSON-serialized.

    This is the exact bytes one ``nested_context`` entry costs, quotes included, so the
    report budget is metered on emitted output rather than on raw path bytes.
    """
    return len(json.dumps(display_text(value), ensure_ascii=False).encode("utf-8"))


def open_directory(path: str | os.PathLike[str]) -> int:
    """Open one directory for descriptor-relative enumeration without following links."""
    return os.open(path, DIRECTORY_OPEN_FLAGS)


def open_child_directory(parent_descriptor: int, name: str) -> int:
    """Open one child directory of ``parent_descriptor`` without following a symlink."""
    return os.open(name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)


def _counted(entries: Iterator[os.DirEntry[str]], counter: list[int]) -> Iterator[os.DirEntry[str]]:
    for entry in entries:
        counter[0] += 1
        yield entry


def _entry_facts(entry: os.DirEntry[str]) -> DirectoryEntryFacts:
    unclassified = False
    try:
        is_directory = entry.is_dir(follow_symlinks=False)
    except OSError:
        is_directory = False
        unclassified = True
    try:
        is_symlink = entry.is_symlink()
    except OSError:
        is_symlink = False
    return DirectoryEntryFacts(entry.name, is_directory, is_symlink, unclassified)


def enumerate_directory(directory_descriptor: int, limit: int) -> DirectoryListing:
    """Enumerate one open directory: descriptor-relative, no-follow, sorted and capped.

    At most ``limit`` entries are retained, and they are the alphabetically first names
    rather than whatever the operating system happened to yield first, so a capped
    listing is reproducible. Memory stays bounded by ``limit`` because entries are
    selected while the scan iterator is consumed, and only the retained entries are
    classified. ``OSError`` propagates when the directory itself cannot be read, so
    each caller keeps its own meaning for an unreadable directory.
    """
    if limit < 1:
        # Failing closed beats returning an empty listing that could be read as complete.
        msg = "directory enumeration limit must be at least one entry"
        raise ValueError(msg)
    counter = [0]
    with os.scandir(directory_descriptor) as iterator:
        retained: list[os.DirEntry[str]] = list(
            nsmallest(limit, _counted(iterator, counter), key=lambda entry: entry.name)
        )
    return DirectoryListing(tuple(_entry_facts(entry) for entry in retained), counter[0] > limit)


def open_bounded_regular_file(path: Path) -> tuple[int, Availability]:
    """Open one regular file for bounded reading without following a symlink.

    Returns ``(file_descriptor, Availability.PRESENT)`` when the caller may read and
    must close the descriptor, or ``(-1, reason)`` naming the observed condition.
    """
    try:
        file_descriptor = os.open(path, REGULAR_FILE_OPEN_FLAGS)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return -1, Availability.SYMLINK
        return -1, Availability.UNREADABLE
    reason = Availability.PRESENT
    try:
        stat_result = os.fstat(file_descriptor)
        if not stat.S_ISREG(stat_result.st_mode):
            reason = Availability.NOT_REGULAR
        elif stat_result.st_size > FILE_SCAN_LIMIT_BYTES:
            reason = Availability.UNREADABLE
    except OSError:
        reason = Availability.UNREADABLE
    if reason is Availability.PRESENT:
        return file_descriptor, reason
    os.close(file_descriptor)
    return -1, reason


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


def read_validated_text(file_descriptor: int, limit: int) -> tuple[str, bool, Availability, int]:
    """Capture a bounded normalized prefix and count the whole normalized source.

    Returns the captured text, whether the capture overflowed ``limit``, the availability
    reason (``Availability.PRESENT`` when the stream yielded text, otherwise the observed
    encoding or read failure) and the character count of the whole normalized source.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    capture = bytearray()
    last_line_boundary = 0
    overflow = False
    pending_carriage_return = False
    source_characters = 0

    def append(character: str) -> None:
        nonlocal last_line_boundary, overflow
        if overflow:
            return
        if not _append_normalized_character(capture, character, limit):
            overflow = True
            return
        if character == "\n":
            last_line_boundary = len(capture)

    def consume(decoded: str) -> None:
        nonlocal pending_carriage_return, source_characters
        for character in decoded:
            if pending_carriage_return:
                pending_carriage_return = False
                source_characters += 1
                append("\n")
                if character == "\n":
                    continue
            if character == "\r":
                pending_carriage_return = True
                continue
            source_characters += 1
            append(character)

    total_bytes_read = 0
    try:
        while True:
            raw = os.read(file_descriptor, 64 * 1024)
            if not raw:
                break
            total_bytes_read += len(raw)
            if total_bytes_read > FILE_SCAN_LIMIT_BYTES:
                return "", False, Availability.UNREADABLE, 0
            if b"\0" in raw:
                return "", False, Availability.ENCODING, 0
            consume(decoder.decode(raw, final=False))
        consume(decoder.decode(b"", final=True))
        if pending_carriage_return:
            source_characters += 1
            append("\n")
    except UnicodeDecodeError:
        return "", False, Availability.ENCODING, 0
    except OSError:
        return "", False, Availability.UNREADABLE, 0

    if overflow:
        del capture[last_line_boundary:]
    return capture.decode("utf-8"), overflow, Availability.PRESENT, source_characters
