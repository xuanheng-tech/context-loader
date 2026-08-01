"""Validate the repository changelog and extract notes for one release tag."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

_VERSION_PATTERN = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
_VERSION_RE = re.compile(rf"^{_VERSION_PATTERN}$")
_TAG_RE = re.compile(rf"^v(?P<version>{_VERSION_PATTERN})$")
_VERSION_HEADING_RE = re.compile(
    rf"^## (?P<version>{_VERSION_PATTERN})(?: - (?P<date>\d{{4}}-\d{{2}}-\d{{2}}))?$"
)


class ChangelogError(ValueError):
    """Raised when CHANGELOG.md does not satisfy the repository contract."""


def parse_changelog(text: str) -> dict[str, str]:
    """Return version bodies after validating all level-two changelog headings."""
    lines = text.splitlines()
    if not lines or lines[0] != "# Changelog":
        raise ChangelogError("CHANGELOG.md must start with '# Changelog'")

    markers: list[tuple[str | None, int]] = []
    version_lines: dict[str, int] = {}
    unreleased_count = 0

    for index, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        if line == "## Unreleased":
            unreleased_count += 1
            markers.append((None, index))
            continue

        match = _VERSION_HEADING_RE.fullmatch(line)
        if match is None:
            raise ChangelogError(f"invalid level-two heading on line {index + 1}: {line}")
        version = match.group("version")
        if version in version_lines:
            raise ChangelogError(f"duplicate version section: {version}")
        release_date = match.group("date")
        if release_date is not None:
            try:
                date.fromisoformat(release_date)
            except ValueError as exc:
                raise ChangelogError(f"invalid date for version {version}: {release_date}") from exc
        version_lines[version] = index
        markers.append((version, index))

    if unreleased_count != 1:
        raise ChangelogError(f"expected exactly one Unreleased section, found {unreleased_count}")

    sections: dict[str, str] = {}
    for marker_index, (version, start) in enumerate(markers):
        end = markers[marker_index + 1][1] if marker_index + 1 < len(markers) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if version is None:
            continue
        if not body:
            raise ChangelogError(f"version section is empty: {version}")
        sections[version] = body
    return sections


def validate_version(text: str, version: str) -> str:
    """Validate the document and return the unique non-empty body for version."""
    if _VERSION_RE.fullmatch(version) is None:
        raise ChangelogError(f"invalid version: {version}")
    sections = parse_changelog(text)
    if version not in sections:
        raise ChangelogError(f"missing version section: {version}")
    return sections[version]


def extract_tag(text: str, tag: str) -> str:
    """Return release notes for one strict vX.Y.Z tag."""
    match = _TAG_RE.fullmatch(tag)
    if match is None:
        raise ChangelogError(f"invalid release tag: {tag}")
    return validate_version(text, match.group("version"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "extract"))
    parser.add_argument("value", help="X.Y.Z for validate; vX.Y.Z for extract")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    args = parser.parse_args(argv)

    try:
        text = args.changelog.read_text(encoding="utf-8")
        if args.command == "validate":
            validate_version(text, args.value)
            print(f"validated_changelog_version={args.value}")
        else:
            sys.stdout.write(extract_tag(text, args.value) + "\n")
    except (ChangelogError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
