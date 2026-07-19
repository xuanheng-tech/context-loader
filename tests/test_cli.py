from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "codex-project-context"
GIT = "/usr/bin/git"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
}


def _git(repo: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        [GIT, "-C", os.fspath(repo), *arguments],
        cwd="/",
        env=GIT_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def _repository(tmp_path: Path, *, commit: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    if commit:
        (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(
            repo,
            "-c",
            "user.name=Codex Test",
            "-c",
            "user.email=codex-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        )
    return repo


def _run(repo: str | Path, *, cwd: Path | str = "/") -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [os.fspath(CLI), "--repo", os.fspath(repo)],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_clean_repository_and_missing_upstream(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()

    result = _run(repo)

    assert result.returncode == 0
    assert result.stderr == b""
    output = result.stdout.decode("utf-8")
    assert output.startswith("# Codex Project Context\n\n")
    assert "- Schema: `context-loader/v0.1`\n" in output
    assert f"- Repository: `{repo.resolve()}`\n" in output
    assert "- Branch: `main`\n" in output
    assert f"- HEAD: `{head}`\n" in output
    assert "- Upstream: `not configured`\n" in output
    assert "- Ahead / behind: `not available`\n" in output
    assert "- Worktree: `clean`\n" in output
    assert "### Working Tree Changes\n\nNo changes.\n" in output
    assert f"- `{head[:12]}` · " in output
    assert output.endswith(" · baseline\n")


def test_dirty_paths_are_sorted_and_include_tracked_and_untracked(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
    (repo / "another.txt").write_text("untracked\n", encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    assert "- Worktree: `dirty`\n" in output
    assert "- `?? another.txt`\n- ` M tracked.txt`\n" in output
    assert "diff --git" not in output
    assert "modified" not in output


def test_repeated_output_is_byte_identical(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    first = _run(repo)
    second = _run(repo)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr == b""


@pytest.mark.parametrize("kind", ["relative", "subdirectory", "non_git", "bare"])
def test_invalid_repository_is_rejected_with_empty_stdout(tmp_path: Path, kind: str) -> None:
    repo = _repository(tmp_path)
    cwd: Path | str = "/"
    if kind == "relative":
        requested: str | Path = "repo"
        cwd = tmp_path
    elif kind == "subdirectory":
        requested = repo / "nested"
        requested.mkdir()
    elif kind == "non_git":
        requested = tmp_path / "non-git"
        requested.mkdir()
    else:
        requested = tmp_path / "bare.git"
        requested.mkdir()
        _git(requested, "init", "--quiet", "--bare")

    result = _run(requested, cwd=cwd)

    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr.startswith(b"error: ")
    assert b"Traceback" not in result.stderr


def test_local_upstream_counts_do_not_fetch(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    _git(repo, "config", "branch.main.remote", "origin")
    _git(repo, "config", "branch.main.merge", "refs/heads/main")
    _git(repo, "config", "remote.origin.url", "ssh://invalid.example.invalid/repo.git")
    _git(
        repo,
        "config",
        "remote.origin.fetch",
        "+refs/heads/*:refs/remotes/origin/*",
    )

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    assert "- Upstream: `origin/main`\n" in output
    assert "- Ahead / behind: `0 / 0`\n" in output


def test_configured_but_missing_upstream_ref_is_not_available(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _git(repo, "config", "branch.main.remote", "origin")
    _git(repo, "config", "branch.main.merge", "refs/heads/main")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    assert "- Upstream: `origin/main`\n" in output
    assert "- Ahead / behind: `not available`\n" in output


def test_detached_and_unborn_head_labels(tmp_path: Path) -> None:
    detached = _repository(tmp_path / "detached")
    _git(detached, "checkout", "--quiet", "--detach")
    detached_result = _run(detached)
    assert detached_result.returncode == 0
    assert b"- Branch: `detached`\n" in detached_result.stdout

    unborn_root = tmp_path / "unborn"
    unborn_root.mkdir()
    unborn = _repository(unborn_root, commit=False)
    unborn_result = _run(unborn)
    assert unborn_result.returncode == 0
    assert b"- Branch: `unborn`\n" in unborn_result.stdout
    assert b"- HEAD: `unborn`\n" in unborn_result.stdout
    assert unborn_result.stdout.endswith(b"## Recent Commits\n\nNo commits.\n")


def test_change_list_is_limited_to_one_hundred_entries(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    for index in range(105):
        (repo / f"untracked-{index:03}.txt").write_text("x\n", encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    change_section = output.split("### Working Tree Changes\n\n", 1)[1].split(
        "\n\n## Recent Commits", 1
    )[0]
    assert change_section.count("\n- `?? ") == 99
    assert change_section.startswith("- `?? ")
    assert "Additional changes omitted" in change_section
    assert "untracked-099.txt" in change_section
    assert "untracked-100.txt" not in change_section


def test_change_list_is_limited_to_four_kibibytes(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    for index in range(30):
        filename = f"long-{index:03}-{'x' * 180}.txt"
        (repo / filename).write_text("x\n", encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    change_section = output.split("### Working Tree Changes\n\n", 1)[1].split(
        "\n\n## Recent Commits", 1
    )[0]
    path_lines = [line for line in change_section.splitlines() if line.startswith("- `?? ")]
    assert len(path_lines) < 30
    assert sum(len(f"{line}\n".encode()) for line in path_lines) <= 4 * 1024
    assert "Additional changes omitted" in change_section


def test_recent_commits_are_limited_to_eight(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    for index in range(8):
        (repo / "tracked.txt").write_text(f"update {index}\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(
            repo,
            "-c",
            "user.name=Codex Test",
            "-c",
            "user.email=codex-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            f"update-{index}",
        )

    result = _run(repo)

    assert result.returncode == 0
    recent_section = result.stdout.decode("utf-8").split("## Recent Commits\n\n", 1)[1]
    assert len(recent_section.splitlines()) == 8
    assert " · baseline\n" not in recent_section
    assert " · update-7\n" in recent_section
    assert " · update-0\n" in recent_section


def test_configured_fsmonitor_is_not_executed(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    monitor = tmp_path / "fsmonitor"
    monitor.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    monitor.chmod(monitor.stat().st_mode | stat.S_IXUSR)
    _git(repo, "config", "core.fsmonitor", os.fspath(monitor))

    result = _run(repo)

    assert result.returncode == 0
    assert not marker.exists()
