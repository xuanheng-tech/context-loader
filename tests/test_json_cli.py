from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from context_loader.application import source_scope_for_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "project-context"
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
        "user.name=Context Test",
        "-c",
        "user.email=context-test@example.invalid",
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
    assert document["schema_version"] == 3
    assert document["tool"] == {"name": "context-loader", "version": "1.3.1"}
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
    assert default.stdout.startswith(b"# Project Context\n")
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


def test_json_statuses_expose_skipped_and_absent_sources(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    outside = tmp_path / "outside-agents.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    (repo / "AGENTS.md").symlink_to(outside)
    (repo / "README.md").write_text("# Overview\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["schema_version"] == 3
    assert document["warnings"] == []
    assert [Path(source["path"]).name for source in document["sources"]] == [
        "README.md",
        "pyproject.toml",
    ]
    statuses = document["statuses"]
    assert {
        "code": "skipped_symlink",
        "subject": "AGENTS.md",
        "subject_kind": "source",
    } in statuses
    assert any(
        status["code"] == "not_present" and status["subject_kind"] == "source"
        for status in statuses
    )
    assert "Skipped: symlink." in document["context"]
    # Existing fields remain present and usable without parsing Markdown.
    assert (
        document["context_sha256"]
        == hashlib.sha256(document["context"].encode("utf-8")).hexdigest()
    )


def test_json_statuses_expose_unreadable_tree_and_preserve_markdown(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "README.md").write_text("# Overview\n", encoding="utf-8")
    locked = repo / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("nope\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        result = _run(repo, output_format="json")
    finally:
        locked.chmod(0o700)

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert {
        "code": "unreadable",
        "subject": "locked",
        "subject_kind": "tree_entry",
    } in document["statuses"]
    assert "locked/ [Skipped: unreadable.]" in document["context"]


def test_json_statuses_compatible_with_existing_source_contract(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_text("# Rules\nKeep going.\n", encoding="utf-8")
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert set(document) >= {
        "schema_version",
        "tool",
        "repository",
        "sources",
        "statuses",
        "context",
        "context_sha256",
        "warnings",
    }
    assert isinstance(document["statuses"], list)
    for status in document["statuses"]:
        assert set(status) == {"code", "subject", "subject_kind"}
    agents = document["sources"][0]
    assert agents["kind"] == "agents"
    assert "selection" in agents
    assert agents["content"] in document["context"]


def test_json_statuses_expose_truncated_source(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    # Exceed the README body budget so the collected overview is truncated.
    (repo / "README.md").write_text("# Overview\n\n" + ("word\n" * 20_000), encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["warnings"] == []
    assert {
        "code": "truncated",
        "subject": "README.md",
        "subject_kind": "source",
    } in document["statuses"]
    assert (
        document["statuses"].count(
            {
                "code": "truncated",
                "subject": "README.md",
                "subject_kind": "source",
            }
        )
        == 1
    )
    assert "… truncated by context-loader …" in document["context"]


def test_json_statuses_expose_globally_omitted_sections(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "README.md").write_text("# Overview\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("large subject\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    huge_subject = "s" * 110_000
    _git(
        repo,
        "-c",
        "user.name=Context Test",
        "-c",
        "user.email=context-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        huge_subject,
    )

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["warnings"] == []
    omitted = [
        status
        for status in document["statuses"]
        if status["code"] == "section_omitted" and status["subject_kind"] == "section"
    ]
    subjects = [status["subject"] for status in omitted]
    assert "Recent Commits" in subjects
    assert "Directory Tree" in subjects
    assert subjects == sorted(subjects, key=subjects.index)  # stable encounter order
    assert len(subjects) == len(set(subjects))
    assert "## Recent Commits\n\nOmitted: global output limit reached." in document["context"]


def test_json_compact_projects_json_document_without_source_bodies(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_text("# Rules\n\n## Deployment\npush with care\n", encoding="utf-8")
    (repo / "README.md").write_bytes(b"# Overview\r\nread me\r\n")
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")

    full = _run(repo, output_format="json")
    compact = _run(repo, output_format="json-compact")
    repeat = _run(repo, output_format="json-compact")

    assert full.returncode == compact.returncode == repeat.returncode == 0
    assert full.stderr == compact.stderr == b""
    assert repeat.stdout == compact.stdout
    full_document = json.loads(full.stdout)
    document = json.loads(compact.stdout)
    assert full_document["schema_version"] == 3
    assert document["schema_version"] == 4
    assert (
        set(document)
        == set(full_document)
        == {
            "schema_version",
            "tool",
            "repository",
            "sources",
            "statuses",
            "nested_context",
            "context",
            "context_sha256",
            "warnings",
        }
    )
    for key in (
        "tool",
        "repository",
        "statuses",
        "nested_context",
        "context",
        "context_sha256",
        "warnings",
    ):
        assert document[key] == full_document[key]
    assert len(document["sources"]) == len(full_document["sources"])
    assert document["sources"] != []
    for full_source, compact_source in zip(
        full_document["sources"], document["sources"], strict=True
    ):
        assert "content" not in compact_source
        assert {k: v for k, v in full_source.items() if k != "content"} == compact_source
        assert full_source["content"] in document["context"]
        assert (
            full_source["content_sha256"]
            == hashlib.sha256(full_source["content"].encode("utf-8")).hexdigest()
        )
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert compact.stdout == canonical + b"\n"
    assert compact.stdout.endswith(b"\n") and not compact.stdout.endswith(b"\n\n")
    readme = next(
        source for source in full_document["sources"] if source["path"].endswith("README.md")
    )
    assert "\r" not in readme["content"]
    assert hashlib.sha256((repo / "README.md").read_bytes()).hexdigest() != readme["content_sha256"]


def test_json_compact_preserves_statuses_from_rendered_entry_bodies(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    # Bodies stay under the per-file capture limit but exceed the 24-KiB aggregate
    # budget, so only the rendered go.mod body exposes the cut via its marker.
    filler = "word\n" * 1_600  # 8,000 bytes each
    (repo / "package.json").write_text(filler, encoding="utf-8")
    (repo / "Makefile").write_text(filler, encoding="utf-8")
    (repo / "Cargo.toml").write_text(filler, encoding="utf-8")
    (repo / "go.mod").write_text(filler, encoding="utf-8")

    full = _run(repo, output_format="json")
    compact = _run(repo, output_format="json-compact")

    assert full.returncode == compact.returncode == 0
    full_document = json.loads(full.stdout)
    document = json.loads(compact.stdout)
    assert {
        "code": "truncated",
        "subject": "go.mod",
        "subject_kind": "source",
    } in full_document["statuses"]
    assert document["statuses"] == full_document["statuses"]
    assert "… truncated by context-loader …" in document["context"]
    go_mod = [
        source for source in full_document["sources"] if Path(source["path"]).name == "go.mod"
    ]
    assert len(go_mod) == 1
    assert go_mod[0]["content"].endswith("… truncated by context-loader …")
    # The capture kept the whole file, so the truncated status is marker-derived:
    # only the rendered body is cut, by the aggregate entry-file budget.
    go_mod_bytes = (repo / "go.mod").read_bytes()
    assert len(go_mod_bytes) <= 8_192
    assert len(go_mod[0]["content"].encode()) < len(go_mod_bytes)


def test_json_compact_accepts_subdirectory_and_keeps_context_equal(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "README.md").write_text("# Overview\n", encoding="utf-8")
    nested = repo / "nested"
    nested.mkdir()

    from_root = _run(repo, output_format="json-compact")
    from_nested = _run(nested, output_format="json-compact")
    nested_json = _run(nested, output_format="json")

    assert from_root.returncode == from_nested.returncode == nested_json.returncode == 0
    root_document = json.loads(from_root.stdout)
    nested_document = json.loads(from_nested.stdout)
    assert nested_document["repository"] == {
        "requested_path": os.fspath(nested.resolve()),
        "canonical_root": os.fspath(repo.resolve()),
    }
    assert root_document["context"] == nested_document["context"]
    assert nested_document["context"] == json.loads(nested_json.stdout)["context"]


def test_json_compact_focus_selection_flows_into_both_documents(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_text(
        "# Demo\n\n## Core\ncore\n\n"
        "## Unrelated Commands\n"
        + ("build lint command\n" * 500)
        + "\n## JoinQuant Provider Runtime\nLATE_RUNTIME_GATE\n",
        encoding="utf-8",
    )
    focus = "JoinQuant provider runtime"
    target_path = "runtime/provider_probe.py"

    unfocused_json = _run(repo, output_format="json")
    unfocused = _run(repo, output_format="json-compact")
    focused = _run(repo, output_format="json-compact", focus=focus, path=target_path)
    focused_full = _run(repo, output_format="json", focus=focus, path=target_path)

    assert all(
        result.returncode == 0 for result in (unfocused_json, unfocused, focused, focused_full)
    )
    unfocused_document = json.loads(unfocused.stdout)
    focused_document = json.loads(focused.stdout)
    assert "LATE_RUNTIME_GATE" not in unfocused_document["context"]
    assert "LATE_RUNTIME_GATE" in focused_document["context"]
    assert focused_document["context"] == json.loads(focused_full.stdout)["context"]
    assert focused_document["statuses"] == json.loads(focused_full.stdout)["statuses"]
    agents = focused_document["sources"][0]
    full_agents = json.loads(focused_full.stdout)["sources"][0]
    assert agents["selection"] == full_agents["selection"]
    assert agents["content_sha256"] == full_agents["content_sha256"]
    assert "content" not in agents
    assert full_agents["content"] in focused_document["context"]
    assert focus.encode() not in focused.stdout
    assert target_path.encode() not in focused.stdout
    assert unfocused_document["context"] == json.loads(unfocused_json.stdout)["context"]


def test_json_nested_context_is_existence_only(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    docs = repo / "docs"
    docs.mkdir()
    (docs / "AGENTS.md").write_text("NESTED RULE TEXT MUST NOT TRAVEL\n", encoding="utf-8")
    deep = repo / "pkg" / "service"
    deep.mkdir(parents=True)
    (deep / "AGENTS.md").write_text("second\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Root rules\n", encoding="utf-8")
    ghost = repo / "ghost"
    ghost.mkdir()
    outside = tmp_path / "outside-agents.md"
    outside.write_text("OUTSIDE AGENTS MUST NOT TRAVEL\n", encoding="utf-8")
    (ghost / "AGENTS.md").symlink_to(outside)
    dangling = repo / "dang"
    dangling.mkdir()
    (dangling / "AGENTS.md").symlink_to(repo / "never-created.md")
    loop = repo / "loop"
    loop.symlink_to(repo, target_is_directory=True)
    for vendored_name in ("node_modules", ".venv", "venv", "site-packages"):
        vendored = repo / vendored_name / "pkg"
        vendored.mkdir(parents=True)
        (vendored / "AGENTS.md").write_text("vendored\n", encoding="utf-8")

    first = _run(repo, output_format="json")
    second = _run(repo, output_format="json")
    compact = _run(repo, output_format="json-compact")

    assert first.returncode == second.returncode == compact.returncode == 0
    assert first.stdout == second.stdout
    document = json.loads(first.stdout)
    nested = document["nested_context"]
    # Symlinked and dangling AGENTS.md entries are listed by existence alone:
    # the scan never resolves targets and never carries their bytes.
    assert nested == {
        "files": [
            "dang/AGENTS.md",
            "docs/AGENTS.md",
            "ghost/AGENTS.md",
            "pkg/service/AGENTS.md",
        ],
        "list_truncated": False,
        "scan_truncated": False,
    }
    assert b"NESTED RULE TEXT MUST NOT TRAVEL" not in first.stdout
    assert b"OUTSIDE AGENTS MUST NOT TRAVEL" not in first.stdout
    assert b"vendored" not in first.stdout
    assert json.loads(compact.stdout)["nested_context"] == nested
    statuses = document["statuses"]
    assert not any(status["subject_kind"] == "nested_context" for status in statuses)


def test_json_nested_context_reports_list_truncation(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    # Created newest-first so surviving selection must be name-ordered, not time-ordered.
    for index in range(34, -1, -1):
        directory = repo / f"component{index:02d}"
        directory.mkdir()
        (directory / "AGENTS.md").write_text("rule\n", encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    nested = document["nested_context"]
    assert nested["files"] == [f"component{index:02d}/AGENTS.md" for index in range(32)]
    assert nested["list_truncated"] is True
    assert nested["scan_truncated"] is False
    assert {
        "code": "nested_agents_list_truncated",
        "subject": "AGENTS.md",
        "subject_kind": "nested_context",
    } in document["statuses"]


def test_json_nested_context_reports_scan_depth_truncation(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    beyond = repo / "a" / "b" / "c" / "d" / "e"
    beyond.mkdir(parents=True)
    (beyond / "AGENTS.md").write_text("too deep\n", encoding="utf-8")
    edge = repo / "a" / "b" / "c" / "d"
    (edge / "AGENTS.md").write_text("at depth four\n", encoding="utf-8")
    visible = repo / "src" / "core"
    visible.mkdir(parents=True)
    (visible / "AGENTS.md").write_text("in budget\n", encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    nested = document["nested_context"]
    assert nested["files"] == ["a/b/c/d/AGENTS.md", "src/core/AGENTS.md"]
    assert nested["list_truncated"] is False
    assert nested["scan_truncated"] is True
    assert {
        "code": "nested_agents_scan_truncated",
        "subject": "AGENTS.md",
        "subject_kind": "nested_context",
    } in document["statuses"]


def test_json_nested_context_marks_unreadable_directory(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    visible = repo / "open"
    visible.mkdir()
    (visible / "AGENTS.md").write_text("seen\n", encoding="utf-8")
    locked = repo / "locked"
    locked.mkdir()
    (locked / "AGENTS.md").write_text("hidden\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        result = _run(repo, output_format="json")
    finally:
        locked.chmod(0o700)

    assert result.returncode == 0
    document = json.loads(result.stdout)
    nested = document["nested_context"]
    assert nested["files"] == ["open/AGENTS.md"]
    assert nested["scan_truncated"] is True
    assert b"hidden" not in result.stdout


def test_json_nested_context_sanitizes_undecodable_names(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    weird = os.fsdecode(b"weird_\xff\xfe-dir")
    directory = repo / weird
    directory.mkdir()
    (directory / "AGENTS.md").write_text("x\n", encoding="utf-8")

    full = _run(repo, output_format="json")
    compact = _run(repo, output_format="json-compact")

    assert full.returncode == compact.returncode == 0
    document = json.loads(full.stdout)
    assert document["nested_context"]["files"] == ["weird_\\xff\\xfe-dir/AGENTS.md"]
    assert document["nested_context"]["scan_truncated"] is False
    assert json.loads(compact.stdout)["nested_context"] == document["nested_context"]


def test_undecodable_repository_root_is_markdown_only(tmp_path: Path) -> None:
    """Pins the documented Limits carve-out: root paths are emitted verbatim, not escaped."""
    root = tmp_path / os.fsdecode(b"xff-\xff-repo")
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch=main")
    (root / "tracked.txt").write_text("baseline\n", encoding="utf-8")

    markdown = _run(root)
    json_document = _run(root, output_format="json")
    compact = _run(root, output_format="json-compact")

    assert markdown.returncode == 0
    assert markdown.stdout.startswith(b"# Project Context\n")
    assert json_document.returncode == compact.returncode == 1
    assert json_document.stdout == b"" and compact.stdout == b""
    assert json_document.stderr == b"error: context collection failed\n"


def test_json_unreadable_directory_with_undecodable_name_still_renders(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    locked = repo / os.fsdecode(b"locked-\xff-dir")
    locked.mkdir()
    (locked / "inner.txt").write_text("x\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        result = _run(repo, output_format="json")
    finally:
        locked.chmod(0o700)

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert any(
        status["code"] == "unreadable" and "\\xff" in status["subject"]
        for status in document["statuses"]
    )


def _emitted_nested_list_bytes(document: bytes) -> int:
    """Measure the nested files array from the bytes the document actually emitted."""
    files = json.loads(document)["nested_context"]["files"]
    array = json.dumps(files, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert b'"nested_context":{"files":' + array + b',"list_truncated"' in document
    return len(array)


def test_json_nested_context_bytes_report_cap(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    for index in range(20):
        directory = repo / ("z" * 241 + str(index).zfill(2))
        directory.mkdir()
        (directory / "AGENTS.md").write_text("rule\n", encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    nested = json.loads(result.stdout)["nested_context"]
    # Each 253-byte path serializes to 255, so 15 fit (257 + 14 x 256 = 3841 <= 4096) and a 16th
    # would cost 4097. Metering raw bytes, or omitting the array's brackets, both admit 16 entries
    # and emit a 4097-byte array over the declared cap.
    assert len(nested["files"]) == 15
    assert nested["list_truncated"] is True
    assert nested["scan_truncated"] is False
    assert _emitted_nested_list_bytes(result.stdout) == 3841


def test_json_nested_context_budget_counts_escaped_bytes(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    for index in range(20):
        directory = repo / (f"{'`' * 200}{index:02d}")
        directory.mkdir()
        (directory / "AGENTS.md").write_text("rule\n", encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    nested = json.loads(result.stdout)["nested_context"]
    # Escaping turns each backtick into \x60 and JSON then doubles each backslash, so a
    # 212-byte path serializes to 1014 bytes. The budget admits 4
    # (1016 + 3 x 1015 <= 4096 < 1016 + 4 x 1015) where pre-escape metering claimed 19.
    assert len(nested["files"]) == 4
    assert nested["list_truncated"] is True
    assert nested["scan_truncated"] is False
    assert all("\\x60" in path for path in nested["files"])
    assert _emitted_nested_list_bytes(result.stdout) <= 4 * 1024


def test_json_document_respects_its_bounds_on_a_stress_repository(tmp_path: Path) -> None:
    from context_loader import application, collect
    from context_loader.render import GLOBAL_OUTPUT_LIMIT_BYTES

    repo = _repository(tmp_path)
    (repo / "AGENTS.md").write_text("# Instructions\n" + ("x" * 16 * 1024), encoding="utf-8")
    (repo / "README.md").write_text("readme " * 3000, encoding="utf-8")
    (repo / "package.json").write_text('{"name": "x"}\n', encoding="utf-8")
    for index in range(40):
        directory = repo / os.fsdecode(b"\xff" * 40) / f"component{index:03d}"
        directory.mkdir(parents=True)
        (directory / "AGENTS.md").write_text("rule\n", encoding="utf-8")
        (directory / "notes.txt").write_text("noise\n", encoding="utf-8")

    markdown = _run(repo)
    assert markdown.returncode == 0
    assert len(markdown.stdout) <= GLOBAL_OUTPUT_LIMIT_BYTES

    for output_format in ("json", "json-compact"):
        result = _run(repo, output_format=output_format)
        assert result.returncode == 0
        assert len(result.stdout) <= application.JSON_OUTPUT_LIMIT_BYTES
        nested = json.loads(result.stdout)["nested_context"]
        assert _emitted_nested_list_bytes(result.stdout) <= collect.NESTED_AGENTS_MAX_LIST_BYTES
        assert nested["list_truncated"] is True


def test_json_nested_context_budget_holds_on_mixed_escape_density(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    shapes = (
        "plain",
        "`" * 60,
        os.fsdecode(b"\xff" * 60),
        "\x01" * 40,
        'quote"back\\slash',
    )
    for index, shape in enumerate(shapes * 12):
        directory = repo / f"{shape}{index:02d}"
        directory.mkdir()
        (directory / "AGENTS.md").write_text("rule\n", encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    nested = json.loads(result.stdout)["nested_context"]
    assert nested["list_truncated"] is True
    assert _emitted_nested_list_bytes(result.stdout) <= 4 * 1024


def test_json_document_bound_is_enforced_on_the_exact_emitted_length(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from context_loader import application
    from context_loader.git import ContextLoaderError

    result = application.load_project_context(os.fspath(_repository(tmp_path)))
    reference = application.render_json(result)

    monkeypatch.setattr(application, "JSON_OUTPUT_LIMIT_BYTES", len(reference))
    assert application.render_json(result) == reference

    monkeypatch.setattr(application, "JSON_OUTPUT_LIMIT_BYTES", len(reference) - 1)
    with pytest.raises(ContextLoaderError, match="JSON output exceeded the"):
        application.render_json(result)
    with pytest.raises(ContextLoaderError, match="JSON output exceeded the"):
        application.render_json(result, compact=True)


def test_json_nested_context_budget_uses_emitted_bytes_for_undecodable_names(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    for index in range(30):
        directory = repo / os.fsdecode(b"\xff" * 200 + f"{index:02d}".encode())
        directory.mkdir()
        (directory / "AGENTS.md").write_text("rule\n", encoding="utf-8")

    result = _run(repo, output_format="json")

    assert result.returncode == 0
    nested = json.loads(result.stdout)["nested_context"]
    # 200 undecodable bytes escape to 800 characters and JSON doubles each escape backslash,
    # so this 210-byte path serializes to 1012 and only 4 fit the budget
    # (1014 + 3 x 1013 <= 4096) where pre-escape metering admitted 19.
    assert len(nested["files"]) == 4
    assert nested["list_truncated"] is True
    assert _emitted_nested_list_bytes(result.stdout) <= 4 * 1024


def test_readme_declares_the_enforced_output_bounds() -> None:
    from context_loader import application
    from context_loader.render import GLOBAL_OUTPUT_LIMIT_BYTES

    readme = " ".join((PROJECT_ROOT / "README.md").read_text(encoding="utf-8").split())
    # Each format's final bound is stated on its own terms; no single figure claims both.
    assert f"Markdown stdout: {GLOBAL_OUTPUT_LIMIT_BYTES:,} bytes" in readme
    assert application.JSON_OUTPUT_LIMIT_BYTES == 8 * 1024 * 1024
    assert f"{application.JSON_OUTPUT_LIMIT_BYTES:,} bytes (8 MiB)" in readme
    assert "JSON output exceeded the 8388608 byte document limit" in readme
    assert "a 4 KiB budget metered on the emitted array" in readme
    assert "- Final stdout:" not in readme


def test_readme_example_and_help_match_emitted_schema_versions() -> None:
    import re

    from context_loader.application import COMPACT_JSON_SCHEMA_VERSION, JSON_SCHEMA_VERSION
    from context_loader.cli import _parser

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r'"schema_version": (\d+),\n  "tool"', readme)
    assert match is not None
    assert int(match.group(1)) == JSON_SCHEMA_VERSION
    assert f"`schema_version` {COMPACT_JSON_SCHEMA_VERSION} document" in readme
    format_help = next(action.help for action in _parser()._actions if action.dest == "format")
    assert "schema version" not in format_help
    assert "schema_version" not in format_help


def test_nested_presence_documented_bounds_match_code() -> None:
    from context_loader import collect

    assert collect.NESTED_AGENTS_MAX_DEPTH == 4
    assert collect.NESTED_AGENTS_MAX_DIRECTORIES == 2_000
    assert collect.NESTED_AGENTS_MAX_FILES == 32
    assert collect.NESTED_AGENTS_MAX_LIST_BYTES == 4 * 1024
    excluded = {".git", ".venv", "venv", "node_modules", "site-packages"}
    assert frozenset(excluded) == collect.NESTED_AGENTS_EXCLUDED_DIRECTORIES


def test_json_document_schema_versions_name_exact_shapes() -> None:
    from context_loader import application

    # 1 (json) and 2 (compact) are the shapes published in release 1.2.0 and must never be
    # re-emitted: adding the nested_context key changed the default document's key set, so the
    # default moved to 3 and the compact projection to 4. Each number names exactly one key set.
    assert application.JSON_SCHEMA_VERSION == 3
    assert application.COMPACT_JSON_SCHEMA_VERSION == 4
    assert {
        application.JSON_SCHEMA_VERSION,
        application.COMPACT_JSON_SCHEMA_VERSION,
    }.isdisjoint({1, 2})
