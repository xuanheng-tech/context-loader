import re
import tomllib
from pathlib import Path

import pytest

from scripts import changelog

ROOT = Path(__file__).resolve().parents[1]
VALID_CHANGELOG = """# Changelog

## Unreleased

## 1.2.3 - 2026-08-01

- Changed: target notes

## 1.2.2

- Fixed: adjacent notes
"""


def test_current_source_version_has_one_nonempty_changelog_section() -> None:
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    package_match = re.search(
        r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
        (ROOT / "context_loader/__init__.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert package_match is not None
    assert package_match.group(1) == project_version

    body = changelog.validate_version(
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), project_version
    )
    assert body


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            VALID_CHANGELOG + "\n## 1.2.3\n\n- Fixed: duplicate\n",
            "duplicate version section",
        ),
        (
            VALID_CHANGELOG + "\n## Unreleased\n",
            "exactly one Unreleased section",
        ),
        (
            VALID_CHANGELOG.replace("## 1.2.3 - 2026-08-01", "## 01.2.3"),
            "invalid level-two heading",
        ),
    ],
)
def test_invalid_or_duplicate_headings_are_rejected(text: str, message: str) -> None:
    with pytest.raises(changelog.ChangelogError, match=message):
        changelog.parse_changelog(text)


def test_missing_empty_and_mismatched_sections_are_rejected() -> None:
    with pytest.raises(changelog.ChangelogError, match="missing version section: 1.2.4"):
        changelog.validate_version(VALID_CHANGELOG, "1.2.4")

    empty = VALID_CHANGELOG.replace("- Changed: target notes", "")
    with pytest.raises(changelog.ChangelogError, match="version section is empty: 1.2.3"):
        changelog.validate_version(empty, "1.2.3")

    with pytest.raises(changelog.ChangelogError, match="missing version section: 1.2.4"):
        changelog.extract_tag(VALID_CHANGELOG, "v1.2.4")


def test_release_tag_extraction_does_not_include_adjacent_versions() -> None:
    assert changelog.extract_tag(VALID_CHANGELOG, "v1.2.3") == "- Changed: target notes"


def test_release_workflow_cli_extracts_notes_and_fails_on_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(VALID_CHANGELOG, encoding="utf-8")

    assert changelog.main(["extract", "v1.2.3", "--changelog", str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "- Changed: target notes\n"
    assert captured.err == ""

    assert changelog.main(["extract", "v1.2.4", "--changelog", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing version section: 1.2.4" in captured.err
