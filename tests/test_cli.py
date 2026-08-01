from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from importlib import import_module
from pathlib import Path

import pytest

from context_loader import __version__
from context_loader.cli import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "codex-project-context"
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


def _git_process(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        [GIT, *arguments],
        cwd="/",
        env=GIT_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result


def _git(repo: Path, *arguments: str) -> bytes:
    result = _git_process("-C", os.fspath(repo), *arguments)
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


def _section(output: str, title: str, next_title: str | None) -> str:
    content = output.split(f"## {title}\n\n", 1)[1]
    if next_title is not None:
        content = content.split(f"\n\n## {next_title}", 1)[0]
    return content


def _git_file_metadata(repo: Path) -> tuple[tuple[str, int, int, int], ...]:
    git_directory = repo / ".git"
    metadata: list[tuple[str, int, int, int]] = []
    for path in sorted(git_directory.rglob("*")):
        if not path.is_file():
            continue
        details = path.stat()
        metadata.append(
            (
                path.relative_to(git_directory).as_posix(),
                details.st_mode,
                details.st_size,
                details.st_mtime_ns,
            )
        )
    return tuple(metadata)


def test_version_output_and_packaging_metadata_are_consistent() -> None:
    result = subprocess.run(
        [os.fspath(CLI), "--version"],
        cwd="/",
        env=GIT_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    lock_package = next(
        package for package in lock["package"] if package["name"] == "codex-project-context-loader"
    )
    entry_point = project["project"]["scripts"]["codex-project-context"]
    module_name, attribute = entry_point.split(":", 1)

    assert result.returncode == 0
    assert result.stdout == b"codex-project-context 0.1.2\n"
    assert result.stderr == b""
    assert project["project"]["version"] == lock_package["version"] == __version__ == "0.1.2"
    assert entry_point == "context_loader.cli:main"
    assert getattr(import_module(module_name), attribute) is main
    assert (PROJECT_ROOT / "codex-project-context").read_text(encoding="utf-8") == (
        "#!/usr/bin/env python3\nfrom context_loader.cli import main\nraise SystemExit(main())\n"
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
    assert " · baseline\n\n## Directory Tree\n" in output


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


def test_empty_remote_clone_reports_configured_unresolved_upstream(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--quiet", "--bare", "--initial-branch=main")
    clone = tmp_path / "clone"

    clone_result = _git_process("clone", "--quiet", os.fspath(remote), os.fspath(clone))

    assert clone_result.returncode == 0, clone_result.stderr.decode("utf-8", errors="replace")
    assert _git(clone, "symbolic-ref", "--quiet", "--short", "HEAD") == b"main\n"
    assert _git(clone, "config", "--get", "branch.main.remote") == b"origin\n"
    assert _git(clone, "config", "--get", "branch.main.merge") == b"refs/heads/main\n"
    assert (
        _git_process("-C", os.fspath(clone), "rev-parse", "--verify", "HEAD^{commit}").returncode
        != 0
    )
    assert (
        _git_process(
            "-C", os.fspath(clone), "rev-parse", "--verify", "@{upstream}^{commit}"
        ).returncode
        != 0
    )
    before_status = _git(clone, "status", "--porcelain=v1", "-z")
    before_metadata = _git_file_metadata(clone)

    first = _run(clone)
    second = _run(clone)

    after_status = _git(clone, "status", "--porcelain=v1", "-z")
    after_metadata = _git_file_metadata(clone)
    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == b""
    assert first.stdout == second.stdout
    assert len(first.stdout) <= 98_304
    output = first.stdout.decode("utf-8")
    assert "- Branch: `main`\n" in output
    assert "- HEAD: `unborn`\n" in output
    assert "- Upstream: `origin/main`\n" in output
    assert "- Ahead / behind: `not available`\n" in output
    assert before_status == after_status == b""
    assert before_metadata == after_metadata


def test_init_created_unborn_with_remote_has_no_upstream(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--quiet", "--bare", "--initial-branch=main")
    repo = _repository(tmp_path, commit=False)
    _git(repo, "remote", "add", "origin", os.fspath(remote))

    assert _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD") == b"main\n"
    assert (
        _git_process("-C", os.fspath(repo), "config", "--get", "branch.main.remote").returncode != 0
    )
    assert (
        _git_process("-C", os.fspath(repo), "config", "--get", "branch.main.merge").returncode != 0
    )
    assert (
        _git_process(
            "-C", os.fspath(repo), "rev-parse", "--verify", "@{upstream}^{commit}"
        ).returncode
        != 0
    )

    result = _run(repo)

    assert result.returncode == 0
    assert result.stderr == b""
    output = result.stdout.decode("utf-8")
    assert "- Branch: `main`\n" in output
    assert "- HEAD: `unborn`\n" in output
    assert "- Upstream: `not configured`\n" in output
    assert "- Ahead / behind: `not available`\n" in output


def test_pushed_upstream_is_resolved_and_local_ahead_is_counted(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--quiet", "--bare", "--initial-branch=main")
    repo = _repository(tmp_path)
    _git(repo, "remote", "add", "origin", os.fspath(remote))
    _git(repo, "push", "--quiet", "--set-upstream", "origin", "main")
    head = _git(repo, "rev-parse", "HEAD").strip()

    assert _git(repo, "rev-parse", "--verify", "@{upstream}^{commit}").strip() == head
    resolved_result = _run(repo)

    assert resolved_result.returncode == 0
    assert resolved_result.stderr == b""
    resolved_output = resolved_result.stdout.decode("utf-8")
    assert "- Upstream: `origin/main`\n" in resolved_output
    assert "- Ahead / behind: `0 / 0`\n" in resolved_output

    (repo / "tracked.txt").write_text("local ahead\n", encoding="utf-8")
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
        "local ahead",
    )

    ahead_result = _run(repo)

    assert ahead_result.returncode == 0
    assert ahead_result.stderr == b""
    ahead_output = ahead_result.stdout.decode("utf-8")
    assert "- Upstream: `origin/main`\n" in ahead_output
    assert "- Ahead / behind: `1 / 0`\n" in ahead_output


def test_detached_head_label(tmp_path: Path) -> None:
    detached = _repository(tmp_path)
    head = _git(detached, "rev-parse", "HEAD").decode("ascii").strip()
    _git(detached, "checkout", "--quiet", "--detach")

    detached_result = _run(detached)

    assert detached_result.returncode == 0
    assert b"- Branch: `detached`\n" in detached_result.stdout
    assert f"- HEAD: `{head}`\n".encode() in detached_result.stdout


def test_unborn_empty_repository_reports_symbolic_branch(tmp_path: Path) -> None:
    unborn = _repository(tmp_path, commit=False)
    _git(unborn, "symbolic-ref", "HEAD", "refs/heads/release")

    unborn_result = _run(unborn)

    assert unborn_result.returncode == 0
    assert b"- Branch: `release`\n" in unborn_result.stdout
    assert b"- HEAD: `unborn`\n" in unborn_result.stdout
    assert b"- Upstream: `not configured`\n" in unborn_result.stdout
    assert b"- Ahead / behind: `not available`\n" in unborn_result.stdout
    assert b"## Recent Commits\n\nNo commits.\n\n## Directory Tree\n" in unborn_result.stdout


def test_unborn_repository_with_untracked_project_files(tmp_path: Path) -> None:
    unborn = _repository(tmp_path, commit=False)
    (unborn / "README.md").write_text("# Unborn project\n", encoding="utf-8")
    (unborn / "pyproject.toml").write_text(
        '[project.scripts]\ndemo = "example:main"\n', encoding="utf-8"
    )

    result = _run(unborn)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    assert "- Branch: `main`\n" in output
    assert "- HEAD: `unborn`\n" in output
    assert "- Worktree: `dirty`\n" in output
    assert "- `?? README.md`\n- `?? pyproject.toml`\n" in output
    assert "```markdown\n# Unborn project\n```" in output
    assert "- `demo` → `example:main`" in output
    assert "## Recent Commits\n\nNo commits.\n" in output


def test_change_list_is_limited_to_one_hundred_entries(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    for index in range(105):
        (repo / f"untracked-{index:03}.txt").write_text("x\n", encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    change_section = output.split("### Working Tree Changes\n\n", 1)[1].split(
        "\n\n## Development Instructions", 1
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
        "\n\n## Development Instructions", 1
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
    recent_section = _section(result.stdout.decode("utf-8"), "Recent Commits", "Directory Tree")
    assert len(recent_section.splitlines()) == 8
    assert " · baseline\n" not in recent_section
    assert " · update-7\n" in recent_section
    assert recent_section.endswith(" · update-0")


def test_all_supported_root_files_render_in_fixed_order_with_commands(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_text("# Rules\nUse care.\n", encoding="utf-8")
    (repo / "README.md").write_bytes(b"# Demo\r\nOverview.\r\n")
    (repo / "pyproject.toml").write_text(
        """[project]
name = "demo"

[project.scripts]
zeta = "pkg:zeta"
alpha = "pkg:alpha"

[project.gui-scripts]
gui = "pkg:gui"
""",
        encoding="utf-8",
    )
    (repo / "justfile").write_text(
        """set positional-arguments := true
default:
build mode="debug":
_internal:
[private]
secret:
codex-project-context *args:
    echo ignored
""",
        encoding="utf-8",
    )
    (repo / "Justfile").write_text("fallback:\n", encoding="utf-8")
    (repo / "package.json").write_text(
        '{"scripts":{"zeta":"node z.js","alpha":"node a.js"},"name":"demo"}\n',
        encoding="utf-8",
    )
    (repo / "Makefile").write_text(
        ".PHONY: all\nVAR := value\nall: build\nbuild test: dependency\npattern%:\n",
        encoding="utf-8",
    )
    (repo / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    (repo / "go.mod").write_text("module example.invalid/demo\n", encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    titles = [
        "Git State",
        "Development Instructions",
        "Project Overview",
        "Declared Commands",
        "Project Entry Files",
        "Recent Commits",
        "Directory Tree",
    ]
    assert [output.index(f"## {title}\n") for title in titles] == sorted(
        output.index(f"## {title}\n") for title in titles
    )
    assert "Source: `AGENTS.md`\n\n```markdown\n# Rules\nUse care.\n```" in output
    assert "Source: `README.md`\n\n```markdown\n# Demo\nOverview.\n```" in output
    commands = _section(output, "Declared Commands", "Project Entry Files")
    expected_commands = [
        "- `alpha` → `pkg:alpha`",
        "- `gui` → `pkg:gui`",
        "- `zeta` → `pkg:zeta`",
        "- `just build`",
        "- `just codex-project-context`",
        "- `just default`",
        "- `npm run alpha` → `node a.js`",
        "- `npm run zeta` → `node z.js`",
        "- `make all`",
        "- `make build`",
        "- `make test`",
    ]
    assert commands.splitlines() == expected_commands
    entries = _section(output, "Project Entry Files", "Recent Commits")
    assert "### `justfile`" in entries
    assert "### `Justfile`" not in entries
    assert "```toml\n[project]" in entries
    assert '```json\n{"scripts"' in entries
    assert "```make\nset positional-arguments := true" in entries
    assert "```text\nmodule example.invalid/demo" in entries
    assert "just fallback" not in commands


def test_missing_root_files_ignore_nested_candidates(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("NESTED INSTRUCTIONS MUST NOT BE READ\n", encoding="utf-8")
    (nested / "README.md").write_text("NESTED OVERVIEW MUST NOT BE READ\n", encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    assert _section(output, "Development Instructions", "Project Overview") == (
        "Source: `AGENTS.md`\n\nNot present."
    )
    assert _section(output, "Project Overview", "Declared Commands") == (
        "Source: `README.md`\n\nNot present."
    )
    entries = _section(output, "Project Entry Files", "Recent Commits")
    assert entries.count("Not present.") == 6
    assert "### `Justfile`" in entries
    assert "NESTED INSTRUCTIONS" not in output
    assert "NESTED OVERVIEW" not in output
    assert "No supported command declarations found." in output


def test_lowercase_justfile_takes_precedence_over_Justfile(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "justfile").write_text("lower:\n", encoding="utf-8")
    (repo / "Justfile").write_text("upper:\n", encoding="utf-8")

    output = _run(repo).stdout.decode("utf-8")

    entries = _section(output, "Project Entry Files", "Recent Commits")
    commands = _section(output, "Declared Commands", "Project Entry Files")
    assert "### `justfile`" in entries
    assert "### `Justfile`" not in entries
    assert commands == "- `just lower`"


def test_symlink_candidates_are_skipped_without_following_outside_target(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("OUTSIDE SECRET MUST NOT APPEAR\n", encoding="utf-8")
    (repo / "AGENTS.md").symlink_to(outside)
    (repo / "README.md").symlink_to(repo / "tracked.txt")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    assert "Source: `AGENTS.md`\n\nSkipped: symlink." in output
    assert "Source: `README.md`\n\nSkipped: symlink." in output
    assert "OUTSIDE SECRET MUST NOT APPEAR" not in output


def test_unsupported_text_and_non_regular_candidates_are_skipped(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_bytes(b"safe line\n" * 2_000 + b"\xff")
    (repo / "README.md").write_bytes(b"safe\0hidden")
    (repo / "pyproject.toml").mkdir()

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    assert "Source: `AGENTS.md`\n\nSkipped: unsupported text encoding." in output
    assert "Source: `README.md`\n\nSkipped: unsupported text encoding." in output
    entries = _section(output, "Project Entry Files", "Recent Commits")
    assert "### `pyproject.toml`\n\nSkipped: not a regular file." in entries
    assert "hidden" not in output


def test_single_file_truncation_is_utf8_and_line_safe(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    line = f"{'🙂' * 200}\n"
    (repo / "AGENTS.md").write_text(f"{line * 30}END MUST NOT APPEAR\n", encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    instructions = _section(output, "Development Instructions", "Project Overview")
    body = instructions.split("```markdown\n", 1)[1].rsplit("\n```", 1)[0]
    assert "END MUST NOT APPEAR" not in output
    assert body.endswith("… truncated by context-loader …")
    assert len(body.encode()) <= 16 * 1024


def test_entry_file_content_has_per_file_and_aggregate_limits(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    payload = "x\n" * 4_096
    for name in ("pyproject.toml", "justfile", "package.json", "Makefile", "Cargo.toml", "go.mod"):
        (repo / name).write_text(payload, encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    entries = _section(result.stdout.decode("utf-8"), "Project Entry Files", "Recent Commits")
    assert entries.count(payload) == 3
    for name in ("Makefile", "Cargo.toml", "go.mod"):
        subsection = entries.split(f"### `{name}`\n\n", 1)[1]
        assert subsection.startswith(
            "```" + ("toml" if name == "Cargo.toml" else "make" if name == "Makefile" else "text")
        )
        assert "… truncated by context-loader …" in subsection.split("### `", 1)[0]


def test_global_output_limit_omits_late_sections_without_invalid_utf8(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "tracked.txt").write_text("large subject\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    huge_subject = "s" * 110_000
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
        huge_subject,
    )

    result = _run(repo)

    assert result.returncode == 0
    assert len(result.stdout) <= 98_304
    output = result.stdout.decode("utf-8")
    assert "## Recent Commits\n\nOmitted: global output limit reached." in output
    assert "## Directory Tree\n\nOmitted: global output limit reached." in output


def test_file_fence_is_longer_than_backticks_in_source(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "README.md").write_text("before\n```\nafter\n", encoding="utf-8")

    output = _run(repo).stdout.decode("utf-8")

    overview = _section(output, "Project Overview", "Declared Commands")
    assert "````markdown\nbefore\n```\nafter\n````" in overview


def test_command_extractors_are_conservative_and_parse_failures_are_local(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "pyproject.toml").write_text("[project.scripts\nbroken = true\n", encoding="utf-8")
    (repo / "package.json").write_text("{broken json\n", encoding="utf-8")
    (repo / "justfile").write_text(
        """# comment
set value := "x"
default:
build target="release":
_private:
[private]
secret:
    indented:
+unsupported:
""",
        encoding="utf-8",
    )
    (repo / "Makefile").write_text(
        """# comment
.PHONY: all
VALUE := x
all: build
build test: dependency
pattern%:
$(GENERATED):
""",
        encoding="utf-8",
    )

    result = _run(repo)

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    commands = _section(output, "Declared Commands", "Project Entry Files")
    assert commands.splitlines() == [
        "- `pyproject.toml`: unable to parse command declarations.",
        "- `just build`",
        "- `just default`",
        "- `package.json`: unable to parse command declarations.",
        "- `make all`",
        "- `make build`",
        "- `make test`",
    ]
    entries = _section(output, "Project Entry Files", "Recent Commits")
    assert "[project.scripts" in entries
    assert "{broken json" in entries


def test_declared_commands_have_an_eight_kibibyte_limit(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    scripts = {f"cmd{index:03}": f"node {'x' * 20}" for index in range(180)}
    package = json.dumps({"scripts": scripts}, separators=(",", ":")) + "\n"
    assert len(package.encode()) < 8 * 1024
    (repo / "package.json").write_text(package, encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    commands = _section(result.stdout.decode("utf-8"), "Declared Commands", "Project Entry Files")
    assert len(commands.encode()) <= 8 * 1024
    assert commands.endswith("… truncated by context-loader …")


def test_directory_tree_depth_sorting_symlink_and_unreadable_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    repo = _repository(tmp_path)
    adir = repo / "adir"
    bdir = repo / "bdir"
    locked = repo / "locked"
    adir.mkdir()
    bdir.mkdir()
    locked.mkdir()
    subdir = adir / "subdir"
    subdir.mkdir()
    (subdir / "too-deep.txt").write_text("hidden from tree\n", encoding="utf-8")
    (adir / "z-child.txt").write_text("child\n", encoding="utf-8")
    (repo / "a-root.txt").write_text("root\n", encoding="utf-8")
    (repo / "z-root.txt").write_text("root\n", encoding="utf-8")
    (repo / "link").symlink_to(adir, target_is_directory=True)
    locked_metadata = locked.stat()
    locked_identity = (locked_metadata.st_dev, locked_metadata.st_ino)
    original_scandir = os.scandir

    def permission_denied_scandir(file_descriptor: int) -> os.ScandirIterator[str]:
        metadata = os.fstat(file_descriptor)
        if (metadata.st_dev, metadata.st_ino) == locked_identity:
            raise PermissionError("synthetic unreadable directory")
        return original_scandir(file_descriptor)

    monkeypatch.setattr("context_loader.collect.os.scandir", permission_denied_scandir)
    exit_code = main(["--repo", os.fspath(repo)])
    captured = capsysbinary.readouterr()

    assert exit_code == 0
    assert captured.err == b""
    tree = _section(captured.out.decode("utf-8"), "Directory Tree", None)
    body = tree.split("```text\n", 1)[1].rsplit("\n```", 1)[0]
    lines = body.splitlines()
    assert lines.index(".git/") < lines.index("adir/") < lines.index("bdir/")
    assert lines.index("adir/subdir/") < lines.index("adir/z-child.txt")
    assert lines.index("bdir/") < lines.index("a-root.txt") < lines.index("z-root.txt")
    assert "link@" in lines
    assert "too-deep.txt" not in body
    assert "locked/ [Skipped: unreadable.]" in lines
    assert ".git/HEAD" not in body


def test_directory_tree_item_limit_uses_truncation_marker(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    for index in range(305):
        (repo / f"tree-{index:03}.txt").write_text("x\n", encoding="utf-8")

    result = _run(repo)

    assert result.returncode == 0
    tree = _section(result.stdout.decode("utf-8"), "Directory Tree", None)
    body = tree.split("```text\n", 1)[1].rsplit("\n```", 1)[0]
    lines = body.splitlines()
    assert lines[-1] == "… truncated by context-loader …"
    assert len(lines[:-1]) <= 300
    assert len(body.encode()) <= 12 * 1024


def test_cli_does_not_change_porcelain_or_git_metadata(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "README.md").write_text("# Read only\n", encoding="utf-8")
    before_status = _git(repo, "status", "--porcelain=v1", "-z")
    before_metadata = _git_file_metadata(repo)

    result = _run(repo)

    after_status = _git(repo, "status", "--porcelain=v1", "-z")
    after_metadata = _git_file_metadata(repo)
    assert result.returncode == 0
    assert before_status == after_status
    assert before_metadata == after_metadata


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
