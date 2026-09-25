"""Every unusable root candidate must reach consumers as its own typed reason.

The collector used to store a human sentence and let the application look the machine
code back up in a table, so any rewording silently reported ``skipped_unreadable`` for an
unrelated condition. These tests pin each observed condition to exactly one code and one
displayed sentence.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from context_loader import application
from context_loader.collect import collect_project_context
from context_loader.model import Availability, CollectedFile
from context_loader.render import _unavailable_sentence

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

# One row per skip condition: the observed filesystem shape, the machine code it must
# produce, and the sentence Markdown must show. The codes are published output, so they
# are pinned by literal here rather than derived from the enum.
SKIP_CONDITIONS = (
    pytest.param("absent", Availability.NOT_PRESENT, "not_present", "Not present.", id="absent"),
    pytest.param(
        "symlink", Availability.SYMLINK, "skipped_symlink", "Skipped: symlink.", id="symlink"
    ),
    pytest.param(
        "directory",
        Availability.NOT_REGULAR,
        "skipped_not_regular",
        "Skipped: not a regular file.",
        id="not-regular",
    ),
    pytest.param(
        "nul-bytes",
        Availability.ENCODING,
        "skipped_encoding",
        "Skipped: unsupported text encoding.",
        id="encoding",
    ),
    pytest.param(
        "unreadable",
        Availability.UNREADABLE,
        "skipped_unreadable",
        "Skipped: unreadable.",
        id="unreadable",
    ),
)

ALL_CODES = (
    "not_present",
    "skipped_symlink",
    "skipped_not_regular",
    "skipped_encoding",
    "skipped_unreadable",
)


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


def _shape_source(repo: Path, shape: str, tmp_path: Path) -> None:
    if shape == "absent":
        return
    if shape == "symlink":
        outside = tmp_path / "outside.txt"
        outside.write_text("OUTSIDE SECRET MUST NOT APPEAR\n", encoding="utf-8")
        (repo / "README.md").symlink_to(outside)
        return
    if shape == "directory":
        (repo / "README.md").mkdir()
        return
    if shape == "nul-bytes":
        (repo / "README.md").write_bytes(b"# Overview\n\0hidden\n")
        return
    assert shape == "unreadable"
    path = repo / "README.md"
    path.write_text("# Overview\n")
    path.chmod(0o000)


def _document(repo: Path) -> dict[str, object]:
    return json.loads(application.render_json(application.load_project_context(os.fspath(repo))))


@pytest.mark.parametrize(("shape", "reason", "code", "sentence"), SKIP_CONDITIONS)
def test_each_skip_condition_reports_its_own_code_and_sentence(
    tmp_path: Path,
    shape: str,
    reason: Availability,
    code: str,
    sentence: str,
) -> None:
    repo = _repository(tmp_path)
    _shape_source(repo, shape, tmp_path)

    collected = collect_project_context(repo).overview
    document = _document(repo)

    assert collected.reason is reason
    assert collected.is_text is (reason is Availability.PRESENT)
    statuses = [
        status
        for status in document["statuses"]
        if status["subject_kind"] == "source" and status["subject"] == "README.md"
    ]
    assert [status["code"] for status in statuses] == [code]
    # The silent-fallback bug reported one condition as another; no other skip code may
    # appear for this source.
    assert not ({status["code"] for status in statuses} & (set(ALL_CODES) - {code}))
    overview = (
        document["context"]
        .split("## Project Overview\n\n", 1)[1]
        .split("\n\n## Declared Commands", 1)[0]
    )
    assert overview == f"Source: `README.md`\n\n{sentence}"


def test_every_unusable_reason_has_one_code_and_one_sentence() -> None:
    unusable = tuple(reason for reason in Availability if reason is not Availability.PRESENT)
    assert [reason.value for reason in unusable] == [
        "not_present",
        "skipped_symlink",
        "skipped_not_regular",
        "skipped_encoding",
        "skipped_unreadable",
    ]
    assert len({_unavailable_sentence(reason) for reason in unusable}) == len(unusable)
    assert _unavailable_sentence(Availability.NOT_PRESENT) == "Not present."
    assert _unavailable_sentence(Availability.SYMLINK) == "Skipped: symlink."
    assert _unavailable_sentence(Availability.NOT_REGULAR) == "Skipped: not a regular file."
    assert _unavailable_sentence(Availability.ENCODING) == "Skipped: unsupported text encoding."
    assert _unavailable_sentence(Availability.UNREADABLE) == "Skipped: unreadable."


def test_machine_code_is_taken_from_the_reason_and_not_from_any_message() -> None:
    # A reason is the only input: an unusable reason cannot be coerced into a
    # plausible-looking code, and a present reason emits no source status at all.
    for reason in Availability:
        source = CollectedFile("README.md", "markdown", reason)
        statuses = application._statuses_for_source(source)
        if reason is Availability.PRESENT:
            assert statuses == []
            continue
        assert [status.code for status in statuses] == [reason.value]


def test_a_present_reason_is_never_rendered_as_a_sentence() -> None:
    with pytest.raises(AssertionError, match="no display sentence"):
        _unavailable_sentence(Availability.PRESENT)


def test_unusable_source_is_omitted_from_rendered_bodies(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _shape_source(repo, "symlink", tmp_path)
    (repo / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")

    result = application.load_project_context(os.fspath(repo))
    document = json.loads(application.render_json(result))

    assert [Path(source["path"]).name for source in document["sources"]] == ["AGENTS.md"]
    assert {
        "code": "skipped_symlink",
        "subject": "README.md",
        "subject_kind": "source",
    } in document["statuses"]
    assert "Skipped: symlink." in document["context"]
