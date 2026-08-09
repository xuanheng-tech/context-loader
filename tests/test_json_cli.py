from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from context_loader.application import source_scope_for_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "codex-project-context"
GIT = "/usr/bin/git"
CONTROLLED_ENV = {
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
    return subprocess.run(
        [GIT, *arguments],
        cwd="/",
        env=CONTROLLED_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _git(repo: Path, *arguments: str) -> bytes:
    result = _git_process("-C", os.fspath(repo), *arguments)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--initial-branch=main")
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


def _run(
    repo: str | Path,
    *,
    output_format: str | None = None,
    extra_env: dict[str, str] | None = None,
    focus: str | None = None,
    path: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    argv = [os.fspath(CLI), "--repo", os.fspath(repo)]
    if output_format is not None:
        argv.extend(("--format", output_format))
    if focus is not None:
        argv.extend(("--focus", focus))
    if path is not None:
        argv.extend(("--path", path))
    environment = dict(CONTROLLED_ENV)
    if extra_env is not None:
        environment.update(extra_env)
    return subprocess.run(
        argv,
        cwd="/",
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _working_tree_hashes(repo: Path) -> tuple[tuple[str, str], ...]:
    hashes: list[tuple[str, str]] = []
    for path in sorted(repo.rglob("*")):
        relative = path.relative_to(repo)
        if ".git" in relative.parts or path.is_symlink() or not path.is_file():
            continue
        hashes.append((relative.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(hashes)


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


def test_json_contract_sources_and_hashes_are_stable(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_text("AGENT SOURCE UNIQUE\n", encoding="utf-8")
    (repo / "README.md").write_text("README SOURCE UNIQUE\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "json-demo-unique"\n', encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    assert result.stderr == b""
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    assert document["tool"] == {"name": "context-loader", "version": "0.1.3"}
    assert document["repository"] == {
        "requested_path": os.fspath(repo.resolve()),
        "canonical_root": os.fspath(repo.resolve()),
    }
    assert document["warnings"] == []
    assert [source["ordinal"] for source in document["sources"]] == [0, 1, 2]
    assert [source["kind"] for source in document["sources"]] == [
        "agents",
        "readme",
        "entry_file",
    ]
    assert [Path(source["path"]).name for source in document["sources"]] == [
        "AGENTS.md",
        "README.md",
        "pyproject.toml",
    ]
    assert {source["scope"] for source in document["sources"]} == {"repository"}
    selection = document["sources"][0]["selection"]
    assert selection == {
        "source": "AGENTS.md",
        "selected_sections": [
            {"heading": "Document head", "heading_level": 0, "reasons": ["head"]}
        ],
        "indexed_only_sections": [],
        "chars_selected": len("AGENT SOURCE UNIQUE\n"),
        "chars_omitted": 0,
        "truncated": False,
        "parse_fallback": False,
        "source_scan_truncated": False,
        "index_truncated": False,
    }
    assert all("selection" not in source for source in document["sources"][1:])

    context = document["context"]
    search_offset = 0
    for source in document["sources"]:
        assert (
            source["content_sha256"]
            == hashlib.sha256(source["content"].encode("utf-8")).hexdigest()
        )
        position = context.find(source["content"], search_offset)
        assert position >= search_offset
        search_offset = position + len(source["content"])
    assert document["context_sha256"] == hashlib.sha256(context.encode("utf-8")).hexdigest()
    canonical_json = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert result.stdout == canonical_json + b"\n"


def test_json_selection_audit_does_not_persist_focus_or_target_path(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_text("# Demo\n\n## Core\ncore\n", encoding="utf-8")
    focus = "unused-sensitive-shaped-focus-phrase"
    path = "private-shaped/component.py"

    result = _run(repo, output_format="json", focus=focus, path=path)

    assert result.returncode == 0
    assert focus.encode() not in result.stdout
    assert path.encode() not in result.stdout
    selection = json.loads(result.stdout)["sources"][0]["selection"]
    assert set(selection) == {
        "source",
        "selected_sections",
        "indexed_only_sections",
        "chars_selected",
        "chars_omitted",
        "truncated",
        "parse_fallback",
        "source_scan_truncated",
        "index_truncated",
    }


def test_markdown_default_and_explicit_format_are_byte_identical(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "README.md").write_text("# Compatible Markdown\n", encoding="utf-8")

    default = _run(repo)
    explicit = _run(repo, output_format="markdown")
    machine = _run(repo, output_format="json")

    assert default.returncode == explicit.returncode == machine.returncode == 0
    assert default.stderr == explicit.stderr == machine.stderr == b""
    assert default.stdout == explicit.stdout
    assert default.stdout.startswith(b"# Codex Project Context\n")
    assert json.loads(machine.stdout)["context"].encode() == default.stdout


def test_json_accepts_subdirectory_and_normalizes_symlink_request(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    alias = tmp_path / "repo-alias"
    alias.symlink_to(nested, target_is_directory=True)

    default = _run(nested)
    explicit_markdown = _run(nested, output_format="markdown")
    nested_json = _run(nested, output_format="json")
    alias_json = _run(alias, output_format="json")

    assert default.returncode == explicit_markdown.returncode == 2
    assert default.stdout == explicit_markdown.stdout == b""
    assert nested_json.returncode == alias_json.returncode == 0
    assert nested_json.stderr == alias_json.stderr == b""
    assert nested_json.stdout == alias_json.stdout
    repository = json.loads(nested_json.stdout)["repository"]
    assert repository == {
        "requested_path": os.fspath(nested.resolve()),
        "canonical_root": os.fspath(repo.resolve()),
    }


@pytest.mark.parametrize("kind", ["missing", "file", "non_git"])
def test_json_invalid_path_has_empty_stdout_and_short_stderr(tmp_path: Path, kind: str) -> None:
    if kind == "missing":
        requested = tmp_path / "missing"
    elif kind == "file":
        requested = tmp_path / "regular-file"
        requested.write_text("not a directory\n", encoding="utf-8")
    else:
        requested = tmp_path / "non-git"
        requested.mkdir()

    result = _run(requested, output_format="json")

    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr.startswith(b"error: ")
    assert len(result.stderr) < 160
    assert b"Traceback" not in result.stderr
    assert b"\x1b" not in result.stderr


def test_json_skips_symlinked_source_outside_repository(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE SECRET MUST NOT APPEAR\n", encoding="utf-8")
    (repo / "AGENTS.md").symlink_to(outside)
    (repo / "README.md").write_text("safe overview\n", encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    assert result.stderr == b""
    assert b"OUTSIDE SECRET MUST NOT APPEAR" not in result.stdout
    document = json.loads(result.stdout)
    assert "Skipped: symlink." in document["context"]
    assert [Path(source["path"]).name for source in document["sources"]] == ["README.md"]
    assert all(
        Path(source["path"]).is_relative_to(repo.resolve()) for source in document["sources"]
    )


def test_json_is_deterministic_read_only_and_does_not_emit_environment(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_text("\x1b[31mrepository rule\x1b[0m\n", encoding="utf-8")
    (repo / "README.md").write_text("# Read-only JSON\n", encoding="utf-8")
    # Keep the synthetic token split so repository scans do not treat test data as a secret.
    fake_secret = "".join(
        [
            "sk",
            "-test-context-loader-",
            "not-a-real-credential",
        ]
    )
    before_status = _git(repo, "status", "--porcelain=v1", "-z")
    before_hashes = _working_tree_hashes(repo)
    before_metadata = _git_file_metadata(repo)

    first = _run(
        repo,
        output_format="json",
        extra_env={"CONTEXT_LOADER_FAKE_SECRET": fake_secret},
    )
    second = _run(
        repo,
        output_format="json",
        extra_env={"CONTEXT_LOADER_FAKE_SECRET": fake_secret},
    )

    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == b""
    assert first.stdout == second.stdout
    assert first.stdout.endswith(b"\n") and not first.stdout.endswith(b"\n\n")
    assert b"\x1b" not in first.stdout
    assert b"CONTEXT_LOADER_FAKE_SECRET" not in first.stdout
    assert fake_secret.encode() not in first.stdout
    assert _git(repo, "status", "--porcelain=v1", "-z") == before_status
    assert _working_tree_hashes(repo) == before_hashes
    assert _git_file_metadata(repo) == before_metadata


def test_source_scope_distinguishes_repository_and_global_paths(tmp_path: Path) -> None:
    repo = (tmp_path / "repo").resolve()
    global_source = (tmp_path / "global" / "AGENTS.md").resolve()

    assert source_scope_for_path(repo / "README.md", repo) == "repository"
    assert source_scope_for_path(global_source, repo) == "global"
