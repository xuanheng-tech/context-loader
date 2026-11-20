"""Regression tests for context-loader 1.4.0 fixes and new layout option."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from context_loader import agents
from context_loader.cli import main
from context_loader.collect import collect_nested_agents_presence, collect_project_context


def _git(repo: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "-C", os.fspath(repo), *arguments],
        cwd="/",
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--quiet", "--initial-branch=main")
    (path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(path, "add", "tracked.txt")
    _git(
        path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "baseline",
    )
    return path


@pytest.mark.parametrize("output_format", ["json", "json-compact"])
def test_json_rejects_malicious_core_worktree_escape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], output_format: str
) -> None:
    victim = _init_repo(tmp_path / "victim")
    (victim / "AGENTS.md").write_text("# Victim Secret Rules\n", encoding="utf-8")

    malicious = _init_repo(tmp_path / "malicious")
    _git(malicious, "config", "core.worktree", os.fspath(victim))

    exit_code = main(["--repo", os.fspath(malicious), "--format", output_format])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "Victim Secret Rules" not in captured.err
    assert captured.err.startswith("error: ")


def test_oversized_first_h1_is_credited_as_head_selected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    big_first = "# Primary Architecture\n" + ("architecture rule line\n" * 300)
    (repo / "AGENTS.md").write_text(
        f"{big_first}\n## Secondary Rule\nSECONDARY\n", encoding="utf-8"
    )

    result = collect_project_context(repo)
    audit = result.instructions.selection
    assert audit is not None
    selected_headings = [entry.heading for entry in audit.selected_sections]
    indexed_headings = [entry.heading for entry in audit.indexed_only_sections]

    assert "Primary Architecture" in selected_headings
    assert "Primary Architecture" not in indexed_headings
    assert "Secondary Rule" in indexed_headings
    entry = next(e for e in audit.selected_sections if e.heading == "Primary Architecture")
    assert entry.reasons == ("head",)


def test_oversized_entry_files_bom_just_and_make_commands(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    scripts = {f"cmd{i:03}": f"echo {'x' * 50}" for i in range(180)}
    pkg = "\ufeff" + json.dumps({"name": "demo", "scripts": scripts}, indent=2) + "\n"
    assert len(pkg.encode("utf-8")) > 8 * 1024
    (repo / "package.json").write_text(pkg, encoding="utf-8")

    pyproject = (
        "\ufeff[project]\nname = 'demo'\n"
        + ("# padding\n" * 1000)
        + "\n[project.scripts]\nlate-cli = 'pkg:cli'\n"
    )
    assert len(pyproject.encode("utf-8")) > 8 * 1024
    (repo / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    (repo / "justfile").write_text(
        '@quiet:\n    echo ok\nrun $ENV_VAR="1":\n    echo run\nalias r := run\n',
        encoding="utf-8",
    )
    (repo / "Makefile").write_text(
        "build/app: src/main.c # FOO=1\n\techo build\n", encoding="utf-8"
    )

    project = collect_project_context(repo)
    invocations = {c.invocation for c in project.commands if not c.parse_error}
    errors = [c for c in project.commands if c.parse_error]

    assert errors == []
    assert "npm run cmd179" in invocations
    assert "late-cli" in invocations
    assert {"just quiet", "just run", "just r"} <= invocations
    assert "make build/app" in invocations


def test_commonmark_inline_backticks_and_truncated_eof_fence_do_not_fallback(
    tmp_path: Path,
) -> None:
    inline_doc = "# Intro\n\n```inline``` text after fence\n\n## After Inline\nKeep rule\n"
    sections = agents._parse_markdown_sections(inline_doc)
    assert [s.heading for s in sections] == ["Intro", "After Inline"]

    repo = _init_repo(tmp_path / "repo")
    head = "# Intro\nrule\n\n## Late Rule\nTARGET_SENTINEL\n\n## Unclosed Fence\n```text\n"
    filler = "line\n" * 70_000
    (repo / "AGENTS.md").write_text(head + filler + "```\n", encoding="utf-8")

    result = collect_project_context(repo)
    assert result.instructions.selection is not None
    assert result.instructions.selection.parse_fallback is False
    assert result.instructions.selection.source_scan_truncated is True
    assert "Late Rule" in [e.heading for e in result.instructions.selection.selected_sections]
    assert "Unclosed Fence" in [
        e.heading for e in result.instructions.selection.indexed_only_sections
    ]


def test_minimum_cost_empty_index_never_forges_index_truncated(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    # First section ~4KB + 1 section matching focus around 12KB leaving 0 indexed sections.
    # Pad near the boundary around AGENTS_LIMIT_BYTES (16384) to exercise remaining_count == 0.
    for pad in range(11940, 11995, 5):
        body = (
            "# Head\n"
            + ("h\n" * 100)
            + "## Target Focus Section\nfocus_keyword\n"
            + ("x" * pad)
            + "\n"
        )
        (repo / "AGENTS.md").write_text(body, encoding="utf-8")
        result = collect_project_context(repo, focus="focus_keyword")
        sel = result.instructions.selection
        assert sel is not None
        if not sel.indexed_only_sections:
            assert sel.index_truncated is False


def test_commit_subject_with_cr_and_oversized_subject_stay_safe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "part1\rpart2",
    )
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "s" * 110_000,
    )

    assert main(["--repo", os.fspath(repo)]) == 0
    out = capsys.readouterr().out
    assert "part1\\x0dpart2" in out
    assert "## Recent Commits\n\nOmitted:" not in out
    assert "## Directory Tree\n\n```text\n.git/" in out


def test_audit_heading_escapes_ansi_sequences(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    content = "# Head\nrule\n\n## Safe Spacer\n" + ("x\n" * 2000) + "\n## \x1b[31mEvil Title\nend\n"
    (repo / "AGENTS.md").write_text(content, encoding="utf-8")

    result = collect_project_context(repo)
    audit = agents.render_agents_selection_audit(result.instructions.selection)  # type: ignore[arg-type]
    assert "\x1b" not in audit
    assert "\\x1b[31mEvil Title" in audit


def test_directory_tree_excludes_caches_keeps_git_and_stops_when_full(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    for cache_dir in (".venv", "__pycache__", ".pytest_cache", "node_modules", "dist"):
        d = repo / cache_dir
        d.mkdir()
        (d / "noise.txt").write_text("n\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    result = collect_project_context(repo)
    entry_paths = {entry.path for entry in result.directory_tree.entries}
    assert ".git" in entry_paths
    assert "src" in entry_paths
    assert "src/app.py" in entry_paths
    assert ".venv" not in entry_paths
    assert "node_modules" not in entry_paths
    assert "__pycache__" not in entry_paths


def test_nested_agents_bfs_and_literal_rev_list_and_cache_friendly_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _init_repo(tmp_path / "repo")
    # Deep chain under a/ would starve shallow z_shallow/ under DFS when dir budget is tight.
    deep = repo / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "AGENTS.md").write_text("deep\n", encoding="utf-8")
    (repo / "z_shallow").mkdir()
    (repo / "z_shallow" / "AGENTS.md").write_text("z\n", encoding="utf-8")

    monkeypatch.setattr("context_loader.collect.NESTED_AGENTS_MAX_DIRECTORIES", 3)
    nested = collect_nested_agents_presence(repo)
    assert nested.files == ("z_shallow/AGENTS.md",)
    assert nested.scan_truncated is True

    # Literal file named HEAD...@{upstream} must not break rev-list
    upstream = tmp_path / "upstream.git"
    _git(repo, "init", "--quiet", "--bare", os.fspath(upstream))
    _git(repo, "remote", "add", "origin", os.fspath(upstream))
    _git(repo, "push", "-qu", "origin", "main")
    (repo / "HEAD...@{upstream}").write_text("tricky\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Instructions\nrule\n", encoding="utf-8")
    (repo / "README.md").write_text("# Overview\ninfo\n", encoding="utf-8")

    assert main(["--repo", os.fspath(repo), "--layout", "cache-friendly"]) == 0
    out = capsys.readouterr().out
    assert "- Upstream: `origin/main`" in out
    assert "- Ahead / behind: `0 / 0`" in out
    assert out.index("## Development Instructions") < out.index("## Directory Tree")
    assert out.index("## Directory Tree") < out.index("## Git State")
    assert out.index("## Git State") < out.index("## Recent Commits")


def test_release_git_isolates_locale_c(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[1] / "scripts"))
    import release

    seen_envs: list[dict[str, str] | None] = []

    def fake_run(args, *, capture_output, text, check, env=None, timeout=None):
        seen_envs.append(env)
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    assert release.command("git", "status") == "ok"
    assert seen_envs and seen_envs[0] is not None
    assert seen_envs[0]["LC_ALL"] == "C"
    assert seen_envs[0]["LANG"] == "C"
