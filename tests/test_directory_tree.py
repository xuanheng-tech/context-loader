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
    notes = [
        line for line in document["context"].splitlines() if line.startswith("Listing incomplete:")
    ]
    assert notes == [
        f"Listing incomplete: 1 directory held more entries than the {TREE_CAP}-entry "
        f"per-directory enumeration limit, so only an alphabetically first prefix of each "
        f"was examined and the rest were neither examined nor listed; this section also "
        f"keeps only as many entries as its own entry and byte budgets allow. "
        f"Named here: `.`."
    ]
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
    assert len(notes) == 1
    assert "2 directories held more entries" in notes[0]
    assert "`deep`" in notes[0] and "`wide`" in notes[0]
    assert "` .`" not in notes[0]
    # The root itself was enumerated whole, so it is not among the named incomplete ones.
    assert "`.`" not in notes[0]
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


def _capped_directories(repo: Path, count: int, cap: int) -> list[str]:
    """Create ``count`` root directories that each hold more than ``cap`` entries.

    They must sit at the repository root: ``DIRECTORY_TREE_MAX_DEPTH`` stops descent before a
    directory two levels down is ever enumerated, so a nested fixture would report nothing.
    ``cap`` therefore has to be at least ``count + 1``, or the root itself would be capped by
    these very directories and the test would measure a different fact than it claims.
    """
    # Root entries are these directories plus .git and tracked.txt.
    assert cap >= count + 2, "the root must not be capped by the fixtures themselves"
    names = [f"cap{i:03d}" for i in range(count)]
    for name in names:
        directory = repo / name
        directory.mkdir()
        for index in range(cap + 1):
            (directory / f"e{index}.txt").write_text("x\n", encoding="utf-8")
    return names


def _aggregate_statuses(document: dict[str, object]) -> list[dict[str, str]]:
    return [
        status
        for status in document["statuses"]
        if status["code"] == "directory_listing_incomplete" and status["subject_kind"] == "tree"
    ]


def _per_directory_statuses(document: dict[str, object]) -> list[dict[str, str]]:
    return [
        status
        for status in document["statuses"]
        if status["code"] == "directory_listing_incomplete"
        and status["subject_kind"] == "tree_entry"
    ]


def test_note_stays_true_when_the_item_budget_hides_most_of_a_capped_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped prefix is not the same thing as a listed prefix.

    With the item budget lowered, only a few offered names ever reach the listing, so any
    wording claiming the limit's worth of names contributed to it would be false.
    """
    cap = 12
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ITEMS", 6)
    repo = _repository(tmp_path)
    directory = repo / "capped"
    directory.mkdir()
    for index in range(cap + 1):
        (directory / f"e{index}.txt").write_text("x\n", encoding="utf-8")

    result, _document = _load(repo)
    lines, _section = _tree(result)
    notes = [line for line in result.context.splitlines() if line.startswith("Listing incomplete:")]

    assert len(notes) == 1
    assert len(lines) <= 7, "the fixture must really be item-budget limited"
    assert "was examined and the rest were neither examined nor listed" in notes[0]
    for false_claim in (f"{cap} names to this listing", f"first {cap} of each were listed"):
        assert false_claim not in notes[0]


def test_note_grammar_matches_a_single_or_several_unnamed_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nine capped directories leave exactly one unnamed; the sentence must say so."""
    cap = 12
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    _capped_directories(repo, 9, cap)

    result, document = _load(repo)
    notes = [line for line in result.context.splitlines() if line.startswith("Listing incomplete:")]

    assert len(notes) == 1
    assert "(1 further directory left unnamed here)" in notes[0]
    assert "1 further directories" not in notes[0]
    aggregate = _aggregate_statuses(document)
    assert len(aggregate) == 1
    assert aggregate[0]["subject"].endswith("1 further directory not named individually")


def test_incomplete_total_covers_the_capped_directories_the_listing_reached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported count is what was observed, and the shortfall is still disclosed.

    400 capped directories exist, but the 300-entry budget never reaches them all: a directory
    is only examined once its parent offered it and kept it. Reporting 400 would claim knowledge
    the collector does not have, and reporting nothing about the rest would hide the shortfall,
    so the count must stay at the reached set while the entry budget reports the remainder.
    """
    cap = 3
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    total = 400
    for index in range(total):
        directory = repo / f"d{index:03d}"
        directory.mkdir()
        for inner in range(cap + 1):
            (directory / f"e{inner}.txt").write_text("x\n", encoding="utf-8")

    result, document = _load(repo)
    notes = [line for line in result.context.splitlines() if line.startswith("Listing incomplete:")]
    per_directory = _per_directory_statuses(document)
    aggregate = _aggregate_statuses(document)

    assert len(notes) == 1
    reported = int(notes[0].split("Listing incomplete: ")[1].split(" ")[0])
    assert reported < total, "the count must be what was reached, not what exists"
    # The count always reconciles with the machine channel: named entries, plus the aggregate's
    # own remainder when more were reached than are named individually.
    if aggregate:
        unnamed = int(aggregate[0]["subject"].split(";")[1].split(" ")[1])
        assert reported == len(per_directory) + unnamed
        assert aggregate[0]["subject"].startswith(f"{reported} directories exceeded")
    else:
        assert reported == len(per_directory) <= 8
    # Whatever was never reached is still disclosed, as the parent's own truncation.
    assert {
        "code": "truncated",
        "subject_kind": "tree",
        "subject": "Directory Tree",
    } in document["statuses"]
    assert result.context.count("Listing incomplete:") == 1


def test_capped_directories_beyond_the_example_limit_report_an_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = 14
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    _capped_directories(repo, 12, cap)

    _result, document = _load(repo)
    subjects = _incomplete_subjects(document)
    per_directory = _per_directory_statuses(document)
    aggregate = _aggregate_statuses(document)

    # Eight named individually, the remainder folded into exactly one bounded summary.
    assert len(per_directory) == 8
    assert [status["subject"] for status in per_directory] == [f"cap{i:03d}" for i in range(8)]
    assert len(aggregate) == 1
    assert aggregate[0]["code"] == "directory_listing_incomplete"
    assert aggregate[0]["subject"] == (
        f"12 directories exceeded the {cap}-entry per-directory enumeration limit; "
        "4 further directories not named individually"
    )
    assert len(subjects) == 9
    assert aggregate[0]["subject"] == subjects[-1]


def test_aggregate_says_directory_for_a_single_remainder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = 11
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    _capped_directories(repo, 9, cap)

    _result, document = _load(repo)
    aggregate = _aggregate_statuses(document)
    assert len(aggregate) == 1
    assert aggregate[0]["subject"].endswith("1 further directory not named individually")


def test_no_aggregate_when_every_capped_directory_is_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = 10
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    names = _capped_directories(repo, 8, cap)

    _result, document = _load(repo)
    assert _incomplete_subjects(document) == names
    assert _aggregate_statuses(document) == []


def test_display_escaping_cannot_merge_two_capped_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two names that escape to the same text are still two facts, and still counted.

    `display_text` is not injective: a directory named "`" and one named "\x60" both render
    as the four characters \x60. Deduplicating or counting on the displayed text would
    silently drop one capped directory and understate the aggregate.
    """
    cap = 12
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    colliding = ("`", "\\x60")
    for name in (*colliding, *(f"cap{index}" for index in range(7))):
        directory = repo / name
        directory.mkdir()
        for index in range(cap + 1):
            (directory / f"e{index}.txt").write_text("x\n", encoding="utf-8")

    result, document = _load(repo)
    per_directory = _per_directory_statuses(document)
    aggregate = _aggregate_statuses(document)

    # Nine capped directories, so eight are named individually and one is not. Had the
    # collision collapsed, only seven would be named and the arithmetic would still claim eight.
    assert len(per_directory) == 8, "two distinct directories must stay two statuses"
    assert sum(1 for status in per_directory if status["subject"] == r"\x60") == 2
    assert len(aggregate) == 1
    assert aggregate[0]["subject"] == (
        f"9 directories exceeded the {cap}-entry per-directory enumeration limit; "
        "1 further directory not named individually"
    )
    notes = [line for line in result.context.splitlines() if line.startswith("Listing incomplete:")]
    assert len(notes) == 1
    assert "9 directories held more entries" in notes[0]
    assert notes[0].count(r"`\x60`") == 2, "both colliding names appear in the note"


def test_enumeration_cap_reports_nothing_at_the_limit_and_everything_above_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = 4
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    exact = repo / "exactly"
    exact.mkdir()
    for index in range(cap):
        (exact / f"e{index}.txt").write_text("x\n", encoding="utf-8")

    result, document = _load(repo)
    assert _incomplete_subjects(document) == []
    assert "Listing incomplete" not in result.context
    assert result.context.count("```text") == 1

    over = repo / "over"
    over.mkdir()
    for index in range(cap + 1):
        (over / f"e{index}.txt").write_text("x\n", encoding="utf-8")

    result, document = _load(repo)
    assert _incomplete_subjects(document) == ["over"]
    assert "1 directory held more entries" in result.context


def test_item_budget_truncation_is_not_reported_as_an_enumeration_cap(tmp_path: Path) -> None:
    """The two limits are attributed separately: a short budget is not a capped directory."""
    repo = _repository(tmp_path)
    for index in range(250):
        directory = repo / f"d{index:03d}"
        directory.mkdir()
        (directory / "x.py").write_text("x\n", encoding="utf-8")
    for index in range(200):
        (repo / f"f{index:03d}.txt").write_text("y\n", encoding="utf-8")

    result, document = _load(repo)
    lines, _section = _tree(result)

    assert len(lines) - 1 == DIRECTORY_TREE_MAX_ITEMS
    assert lines[-1] == MARKER
    assert _incomplete_subjects(document) == []
    assert "Listing incomplete" not in result.context
    assert {
        "code": "truncated",
        "subject_kind": "tree",
        "subject": "Directory Tree",
    } in document["statuses"]
    # Both root groups kept a share of the budget: neither was crowded out entirely.
    assert any(line.endswith("/") for line in lines)
    assert any(line.endswith(".txt") for line in lines)


def test_note_is_charged_to_the_listing_budget_and_survives_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from context_loader.model import DIRECTORY_TREE_LIMIT_BYTES

    cap = 3
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    for index in range(40):
        directory = repo / f"cap{index:03d}"
        directory.mkdir()
        for name in range(cap + 1):
            (directory / f"e{name}.txt").write_text("x\n", encoding="utf-8")
    for index in range(400):
        (repo / f"very-long-root-entry-name-{index:04d}-padding.txt").write_text("z\n", "utf-8")

    result, _document = _load(repo)
    lines, section = _tree(result)
    note = [line for line in section.splitlines() if line.startswith("Listing incomplete:")]

    assert len(note) == 1, "the claim must survive a saturated budget"
    assert len("\n".join(lines).encode("utf-8")) + len(note[0].encode("utf-8")) + 2 <= (
        DIRECTORY_TREE_LIMIT_BYTES
    )


def test_incomplete_reporting_is_deterministic_across_repeated_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = 17
    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", cap)
    repo = _repository(tmp_path)
    _capped_directories(repo, 15, cap)

    first, first_document = _load(repo)
    second, second_document = _load(repo)
    assert first.context == second.context
    assert first_document["statuses"] == second_document["statuses"]
    assert first_document["context_sha256"] == second_document["context_sha256"]


def test_many_capped_directories_cannot_inflate_the_tree_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The published byte claims for this fixture are checked here.

    120 capped directories once produced 120 unbudgeted notes and pushed the section 5,710
    bytes past the 12 KiB it documents. The section must now stay bounded while the listing
    body keeps every entry it kept before, so bounding the claim cannot cost evidence.
    """
    from context_loader.model import DIRECTORY_TREE_LIMIT_BYTES

    monkeypatch.setattr("context_loader.collect.DIRECTORY_TREE_MAX_ENTRIES_PER_DIRECTORY", 3)
    repo = _repository(tmp_path)
    for outer in range(120):
        directory = repo / f"cap{outer:03d}"
        directory.mkdir()
        for inner in range(4):
            (directory / f"item{inner:03d}").write_text("", encoding="utf-8")

    result, document = _load(repo)
    _lines, section = _tree(result)
    body = section.split("```text\n", 1)[1].rsplit("\n```", 1)[0]
    notes = [line for line in section.splitlines() if line.startswith("Listing incomplete:")]
    incomplete = _incomplete_subjects(document)

    assert len(notes) == 1
    assert len(incomplete) <= 9, "named entries plus at most one aggregate"
    assert len(body.encode("utf-8")) <= DIRECTORY_TREE_LIMIT_BYTES
    assert len(section.encode("utf-8")) <= DIRECTORY_TREE_LIMIT_BYTES + 128, "bounded section"
    per_directory = _per_directory_statuses(document)
    aggregate = _aggregate_statuses(document)
    assert per_directory, "capped directories were reached"
    # Note, named entries and aggregate must reconcile, whether or not anything was elided.
    reported = int(notes[0].split("Listing incomplete: ")[1].split(" ")[0])
    assert reported == len(per_directory) + (
        int(aggregate[0]["subject"].split(";")[1].split(" ")[1]) if aggregate else 0
    )
    assert (reported > len(per_directory)) == bool(aggregate)
