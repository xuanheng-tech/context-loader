"""Exercise the built wheel through its installed CLI, outside the source checkout."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
FORMATS = ("markdown", "json", "json-compact")
ENV = {
    "PATH": os.defpath,
    "HOME": "/nonexistent",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+0000",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+0000",
}


def _run(
    arguments: list[str | Path], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [os.fspath(argument) for argument in arguments],
        cwd=cwd,
        env=ENV if env is None else env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )


def _git(repo: Path, *arguments: str) -> bytes:
    result = _run(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-C",
            repo,
            *arguments,
        ],
        cwd=repo.parent,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


@pytest.fixture(scope="module")
def installed_cli(tmp_path_factory: pytest.TempPathFactory) -> Path:
    scratch = tmp_path_factory.mktemp("installed-package")
    uv = shutil.which("uv")
    assert uv is not None, "package acceptance requires the development uv CLI"
    # Use the backend cache populated by `uv run --frozen`, without caller index,
    # proxy, Python-routing or configuration overrides. No test downloads packages.
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    build_env = {
        **ENV,
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", os.fspath(cache_home / "uv")),
    }
    wheels = scratch / "dist"
    built = _run(
        [
            uv,
            "build",
            "--wheel",
            "--out-dir",
            wheels,
            "--python",
            sys.executable,
            "--offline",
            "--no-config",
            "--no-sources",
        ],
        cwd=ROOT,
        env=build_env,
    )
    assert built.returncode == 0, built.stderr.decode("utf-8", errors="replace")
    artifacts = tuple(wheels.glob("*.whl"))
    assert len(artifacts) == 1

    environment = scratch / "venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    installed = _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            environment / "bin/python",
            "--offline",
            "--no-config",
            "--no-index",
            "--no-deps",
            artifacts[0],
        ],
        cwd=scratch,
        env=build_env,
    )
    assert installed.returncode == 0, installed.stderr.decode("utf-8", errors="replace")
    probe = _run(
        [
            environment / "bin/python",
            "-I",
            "-c",
            "import context_loader, importlib.metadata, json; "
            "d = importlib.metadata.distribution('context-loader'); "
            "print(json.dumps({'path': context_loader.__file__, 'version': d.version, "
            "'entry_points': {e.name: e.value for e in d.entry_points}, "
            "'requires': d.requires or []}))",
        ],
        cwd=scratch,
    )
    assert probe.returncode == 0, probe.stderr.decode("utf-8", errors="replace")
    metadata = json.loads(probe.stdout)
    assert Path(metadata["path"]).resolve().is_relative_to(environment.resolve())
    assert metadata["version"] == VERSION
    assert metadata["entry_points"] == {"project-context": "context_loader.cli:main"}
    assert metadata["requires"] == []
    return environment / "bin/project-context"


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "README.md").write_text("# Installed package acceptance\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Instructions\nPreserve user data.\n", encoding="utf-8")
    _git(repo, "add", "README.md", "AGENTS.md")
    _git(
        repo,
        "-c",
        "user.name=Package Test",
        "-c",
        "user.email=package-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return repo


@pytest.mark.parametrize("linked", [False, True], ids=["root", "linked-worktree"])
@pytest.mark.parametrize("profile", ["short", "long"])
def test_installed_formats_are_deterministic_and_agree(
    installed_cli: Path, repository: Path, linked: bool, profile: str
) -> None:
    selection_arguments = []
    if profile == "long":
        instructions = (
            "# Synthetic monorepo\n\n## Core Rules\nCORE_RULE_REQUIRED\n\n"
            "## Large Unrelated\nUNRELATED_BODY_NOT_LOADED\n"
            + "unrelated filler line\n"
            * 1000
            + "\n## Runtime Budget\nFOCUS_RULE_REQUIRED\n"
            "\n## Billing Adapter\nScope: modules/billing/adapter.py\nPATH_RULE_REQUIRED\n"
            "\n## Runtime Payload Too Large\nOVERSIZED_RULE_NOT_LOADED\n"
            + "runtime budget payload that cannot fit\n"
            * 1000
        )
        (repository / "AGENTS.md").write_text(instructions, encoding="utf-8")
        module = repository / "modules/billing"
        module.mkdir(parents=True)
        (module / "adapter.py").touch()
        (module / "AGENTS.md").write_text("NESTED_RULE_MUST_NOT_LOAD\n", encoding="utf-8")
        _git(repository, "add", "AGENTS.md", "modules")
        _git(
            repository,
            "-c",
            "user.name=Package Test",
            "-c",
            "user.email=package-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "monorepo instructions",
        )
        selection_arguments = ["--focus", "runtime budget", "--path", "modules/billing/adapter.py"]
    repo = repository
    if linked:
        repo = repository.parent / "linked"
        _git(repository, "worktree", "add", "--quiet", "--detach", os.fspath(repo), "HEAD")
        assert (repo / ".git").is_file()
    index = Path(
        os.fsdecode(
            _git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index").strip()
        )
    )
    git_metadata = index.read_bytes(), index.stat().st_mtime_ns
    outputs = {}
    for output_format in FORMATS:
        arguments = [installed_cli, "--repo", repo, "--format", output_format, *selection_arguments]
        first = _run(arguments, cwd=repository.parent)
        second = _run(arguments, cwd=repository.parent)
        assert first.returncode == second.returncode == 0
        assert first.stderr == second.stderr == b""
        assert first.stdout == second.stdout
        outputs[output_format] = first.stdout

    full = json.loads(outputs["json"])
    compact = json.loads(outputs["json-compact"])
    assert full["schema_version"] == 3
    assert compact["schema_version"] == 4
    assert full["tool"] == {"name": "context-loader", "version": VERSION}
    assert full["repository"]["canonical_root"] == os.fspath(repo.resolve())
    assert full["context"].encode("utf-8") == outputs["markdown"]
    assert full["context_sha256"] == hashlib.sha256(outputs["markdown"]).hexdigest()
    for source in full["sources"]:
        assert source["content"] in full["context"]
        assert source["content_sha256"] == hashlib.sha256(source["content"].encode()).hexdigest()
    if profile == "long":
        for marker in ("CORE_RULE_REQUIRED", "FOCUS_RULE_REQUIRED", "PATH_RULE_REQUIRED"):
            assert marker in full["context"]
        for marker in (
            "UNRELATED_BODY_NOT_LOADED",
            "OVERSIZED_RULE_NOT_LOADED",
            "NESTED_RULE_MUST_NOT_LOAD",
        ):
            assert marker not in full["context"]
        source = next(source for source in full["sources"] if source["kind"] == "agents")
        selected = {
            item["heading"]: item["reasons"] for item in source["selection"]["selected_sections"]
        }
        indexed = {
            item["heading"]: item["reasons"]
            for item in source["selection"]["indexed_only_sections"]
        }
        assert "focus_match" in selected["Runtime Budget"]
        assert "path_match" in selected["Billing Adapter"]
        assert indexed["Runtime Payload Too Large"] == ["budget_fallback"]
        assert {"code": "truncated", "subject": "AGENTS.md", "subject_kind": "source"} in full[
            "statuses"
        ]
        assert full["nested_context"]["files"] == ["modules/billing/AGENTS.md"]
        assert not full["nested_context"]["list_truncated"]
        assert not full["nested_context"]["scan_truncated"]
    full["schema_version"] = 4
    for source in full["sources"]:
        del source["content"]
    assert compact == full
    assert git_metadata == (index.read_bytes(), index.stat().st_mtime_ns)
    version = _run([installed_cli, "--version"], cwd=repository.parent)
    assert version.returncode == 0
    assert version.stdout == f"project-context {VERSION}\n".encode()
    assert version.stderr == b""


@pytest.mark.parametrize("driver", ["clean", "process"])
def test_installed_cli_refuses_filters_without_executing_them(
    installed_cli: Path, repository: Path, driver: str
) -> None:
    marker = repository.parent / "filter-executed"
    _git(repository, "config", f"filter.acceptance.{driver}", f"touch {shlex.quote(str(marker))}")
    (repository / ".gitattributes").write_text("README.md filter=acceptance\n", encoding="utf-8")
    (repository / "README.md").write_text("changed\n", encoding="utf-8")
    for output_format in FORMATS:
        result = _run(
            [installed_cli, "--repo", repository, "--format", output_format],
            cwd=repository.parent,
        )
        assert result.returncode == 1
        assert result.stdout == b""
        assert b"Git clean/process filters" in result.stderr
        assert b"Traceback" not in result.stderr
        assert not marker.exists()


def test_installed_cli_reports_invalid_arguments_without_output(installed_cli: Path) -> None:
    for output_format in FORMATS:
        result = _run(
            [installed_cli, "--repo", "relative/path", "--format", output_format],
            cwd=installed_cli.parent,
        )
        assert result.returncode == 2
        assert result.stdout == b""
        assert result.stderr == b"error: --repo must be an absolute path\n"
