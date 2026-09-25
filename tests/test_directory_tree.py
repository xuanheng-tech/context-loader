"""Directory enumeration is bounded, attributed per directory, and root-first.

These tests pin the guarantees that replaced the old unbounded depth-first walk: one
directory is never materialised whole, a listing that stopped early says so in both
Markdown and the machine formats, and a directory's own files are never displaced by its
alphabetically-earlier subdirectories.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from context_loader import application
from context_loader.model import (
    DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY,
    DIRECTORY_TREE_MAX_ITEMS,
)

GIT = "/usr/bin/git"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
}
TREE_CAP = DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY
MARKER = "… truncated by context-loader …"


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        [GIT, "init", "--quiet", "--initial-branch=main"],
        cwd=repo,
        env=GIT_ENV,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(
        [GIT, "add", "tracked.txt"], cwd=repo, env=GIT_ENV, stdin=subprocess.DEVNULL, check=True
    )
    subprocess.run(
        [
            GIT,
            "-c",
            "user.name=Context Test",
            "-c",
            "user.email=context-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ],
        cwd=repo,
        env=GIT_ENV,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    return repo


def _load(repo: Path) -> tuple[application.ProjectContextResult, dict[str, object]]:
    result = application.load_project_context(os.fspath(repo))
    return result, json.loads(application.render_json(result))


def _tree(result: application.ProjectContextResult) -> tuple[list[str], str]:
    section = result.context.split("## Directory Tree\n\n", 1)[1]
    body = section.split("```text\n", 1)[1].rsplit("\n```", 1)[0]
    return body.splitlines(), section


def _incomplete_subjects(document: dict[str, object]) -> list[str]:
    return [
        status["subject"]
        for status in document["statuses"]
        if status["code"] == "directory_listing_incomplete"
    ]


def test_enumeration_cap_claims_an_incomplete_root_listing(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    for index in range(TREE_CAP + 5):
        (repo / f"wide-{index:03d}.txt").write_text("x\n", encoding="utf-8")

    result, document = _load(repo)
    lines, section = _tree(result)

    assert lines[-1] == MARKER
    assert len(lines) - 1 <= DIRECTORY_TREE_MAX_ITEMS
    assert (
        f"Listing incomplete: . holds more than {TREE_CAP} directory entries, so only the "
        f"alphabetically first {TREE_CAP} were examined." in section
    )
    assert _incomplete_subjects(document) == ["."]
    assert {
        "code": "truncated",
        "subject": "Directory Tree",
        "subject_kind": "tree",
    } in document["statuses"]


def test_enumeration_cap_is_attributed_to_the_directories_that_hit_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", 4)
    repo = _repository(tmp_path)
    deep = repo / "deep"
    wide = repo / "wide"
    deep.mkdir()
    wide.mkdir()
    for index in range(6):
        (deep / f"deep-{index}.txt").write_text("x\n", encoding="utf-8")
    for index in range(7):
        (wide / f"wide-{index}.txt").write_text("x\n", encoding="utf-8")

    result, document = _load(repo)
    _lines, section = _tree(result)

    notes = [line for line in section.splitlines() if line.startswith("Listing incomplete:")]
    assert _incomplete_subjects(document) == ["deep", "wide"]
    assert len(notes) == 2
    assert any(note.startswith("Listing incomplete: deep holds") for note in notes)
    assert any(note.startswith("Listing incomplete: wide holds") for note in notes)
    assert not any(note.startswith("Listing incomplete: . holds") for note in notes)
    # The nested presence scan keeps its own, much larger enumeration budget, so the
    # tree cap must not leak into it.
    assert document["nested_context"]["scan_truncated"] is False
    assert document["nested_context"]["list_truncated"] is False


def test_nested_scan_reports_its_own_enumeration_cap_without_tree_coupling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("context_loader.collect.NESTED_AGENTS_MAX_ENTRIES_PER_DIRECTORY", 4)
    repo = _repository(tmp_path)
    package = repo / "package"
    package.mkdir()
    for index in range(6):
        (package / f"module-{index}.py").write_text("x\n", encoding="utf-8")
    (package / "AGENTS.md").write_text("nested rule\n", encoding="utf-8")

    result, document = _load(repo)
    lines, _section = _tree(result)

    assert document["nested_context"]["scan_truncated"] is True
    assert {
        "code": "nested_agents_scan_truncated",
        "subject": "AGENTS.md",
        "subject_kind": "nested_context",
    } in document["statuses"]
    # The tree examined every entry of this directory, so it makes no incompleteness
    # claim of its own.
    assert _incomplete_subjects(document) == []
    assert "Listing incomplete" not in result.context
    assert "package/module-5.py" in lines


def test_root_files_stay_visible_when_subdirectories_exceed_the_item_budget(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    for index in range(DIRECTORY_TREE_MAX_ITEMS + 20):
        (repo / f"a-dir-{index:03d}").mkdir()
    (repo / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    (repo / "README.md").write_text("# Overview\n", encoding="utf-8")

    result, document = _load(repo)
    lines, section = _tree(result)

    assert "README.md" in lines
    assert "AGENTS.md" in lines
    assert lines[-1] == MARKER
    assert len(lines) - 1 <= DIRECTORY_TREE_MAX_ITEMS
    assert {
        "code": "truncated",
        "subject": "Directory Tree",
        "subject_kind": "tree",
    } in document["statuses"]
    # Enumeration itself stayed complete, so the only claim is the item budget's.
    assert _incomplete_subjects(document) == []
    assert "Listing incomplete" not in section


def test_root_level_files_are_listed_before_any_descendant(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    source = repo / "src"
    nested = source / "core"
    source.mkdir()
    nested.mkdir()
    (source / "main.py").write_text("x\n", encoding="utf-8")
    (nested / "kernel.py").write_text("x\n", encoding="utf-8")
    (repo / "zeta.txt").write_text("x\n", encoding="utf-8")

    result, document = _load(repo)
    lines, _section = _tree(result)

    assert lines == [
        ".git/",
        "src/",
        "tracked.txt",
        "zeta.txt",
        "src/core/",
        "src/main.py",
    ]
    assert not any(status["subject_kind"].startswith("tree") for status in document["statuses"])


def test_listing_is_independent_of_the_operating_systems_enumeration_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path)
    for index in range(TREE_CAP + 5):
        (repo / f"order-{index:03d}.txt").write_text("x\n", encoding="utf-8")
    (repo / "zulu-dir").mkdir()

    forward, forward_document = _load(repo)
    forward_lines, _forward_section = _tree(forward)

    original = os.scandir

    class _ReversedOrder:
        """Hand back the same entries name-last, without resolving any of them."""

        def __init__(self, iterator: os.ScandirIterator[str]) -> None:
            self._iterator = iterator

        def __enter__(self) -> _ReversedOrder:
            return self

        def __exit__(self, *_exception: object) -> None:
            self._iterator.close()

        def __iter__(self) -> Iterator[os.DirEntry[str]]:
            return iter(sorted(self._iterator, key=lambda entry: entry.name, reverse=True))

    def reversed_scandir(file_descriptor: int) -> _ReversedOrder:
        return _ReversedOrder(original(file_descriptor))

    monkeypatch.setattr("context_loader.filesystem.os.scandir", reversed_scandir)
    backward, backward_document = _load(repo)
    backward_lines, _backward_section = _tree(backward)

    assert forward_lines != []
    assert backward_lines == forward_lines
    assert forward.context == backward.context
    assert forward_document["statuses"] == backward_document["statuses"]
