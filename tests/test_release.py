from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.error
import zipfile
from pathlib import Path

import pytest

from scripts import release as r

ROOT = Path(__file__).resolve().parents[1]
TAG = "v1.2.3"
SHA = "a" * 40
RELEASE = {"tag": TAG, "version": "1.2.3", "commit": SHA, "notes": "- Release notes"}


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
    (repo / "context_loader").mkdir()
    (repo / "pyproject.toml").write_text(f'[project]\nname = "{r.PACKAGE}"\nversion = "1.2.3"\n')
    (repo / "context_loader/__init__.py").write_text('__version__ = "1.2.3"\n')
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n## 1.2.3\n\n- Notes\n")
    compat = repo / r.COMPAT_ROOT
    compat.mkdir(parents=True)
    (compat / "pyproject.toml").write_text(
        f'[project]\nname = "{r.COMPAT_PACKAGE}"\nversion = "1.2.3"\n'
        f'dependencies = ["{r.PACKAGE}==1.2.3"]\n'
    )
    subprocess.run(
        ["git", "add", "pyproject.toml", "context_loader", "CHANGELOG.md", "compat"], check=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    subprocess.run(["git", "tag", TAG], check=True)
    return repo


@pytest.mark.parametrize("tag", ["v1.2.4", "v01.2.3", "archive-v1.2.3"])
def test_tag_version_mismatch_blocks_before_quality_or_build(repository: Path, tag: str) -> None:
    if tag == "v1.2.4":
        subprocess.run(["git", "tag", tag], check=True)
    with pytest.raises(r.ReleaseError):
        r.identity(tag)


def test_exact_commit_is_required(repository: Path) -> None:
    with pytest.raises(r.ReleaseError, match="tag/expected commit mismatch"):
        r.identity(TAG, SHA)


def test_real_quality_failure_stops_before_network_or_build(
    repository: Path, tmp_path: Path
) -> None:
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "just").write_text("#!/bin/sh\nexit 19\n")
    (tools / "just").chmod(0o755)
    (tools / "uv").write_text("#!/bin/sh\ntouch unexpected-build\nexit 0\n")
    (tools / "uv").chmod(0o755)
    env = {**os.environ, "PATH": f"{tools}:{os.defpath}"}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/release.py"), "build", TAG],
        cwd=repository,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "just failed (exit 19)" in result.stderr
    assert not (repository / "unexpected-build").exists()
    assert not (repository / "dist").exists()


def test_published_package_skips_build(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    identity = r.identity(TAG)
    original = r.command
    actions = []

    def command(*args):
        if args[0] == "git":
            return original(*args)
        actions.append(args)
        return ""

    monkeypatch.setattr(r, "command", command)
    monkeypatch.setattr(r, "all_pypi_files", lambda _: {"existing": "hash"})
    assert r.build(identity, repository / "dist") is False
    assert actions == [("just", "check")]


def test_existing_dist_artifact_stops_before_build(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = r.identity(TAG)
    original = r.command
    actions = []

    def command(*args):
        if args[0] == "git":
            return original(*args)
        actions.append(args)
        return ""

    monkeypatch.setattr(r, "command", command)
    monkeypatch.setattr(r, "all_pypi_files", lambda _: None)
    dist = repository / "dist"
    dist.mkdir()
    (dist / ".gitignore").write_text("*\n")
    (dist / f"{r.archive(r.PACKAGE)}-1.2.3-py3-none-any.whl").write_bytes(b"stale")

    with pytest.raises(r.ReleaseError, match="build only into an empty dist"):
        r.build(identity, dist)

    assert actions == [("just", "check")]


def test_unexpected_build_output_is_rejected(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = r.identity(TAG)
    original = r.command
    dist = repository / "dist"

    def command(*args):
        if args[0] == "git":
            return original(*args)
        if args[:2] == ("uv", "build"):
            dist.mkdir(exist_ok=True)
            (dist / f"{r.archive(r.PACKAGE)}-9.9.9-py3-none-any.whl").write_bytes(b"wrong version")
        return ""

    monkeypatch.setattr(r, "command", command)
    monkeypatch.setattr(r, "all_pypi_files", lambda _: None)

    with pytest.raises(r.ReleaseError, match="unexpected artifact set"):
        r.build(identity, dist)


def test_release_closure_waits_out_pypi_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(r.time, "sleep", slept.append)
    responses = [None, None, {"ready": True}]
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return responses[calls - 1]

    value = r.poll(load)

    assert value == {"ready": True}
    assert calls == 3
    assert slept == [r.PROPAGATION_DELAY_SECONDS, r.PROPAGATION_DELAY_SECONDS]


def test_release_closure_gives_up_after_bounded_propagation_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(r.time, "sleep", slept.append)
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return None

    assert r.poll(load) is None
    assert calls == r.PROPAGATION_ATTEMPTS
    assert len(slept) == r.PROPAGATION_ATTEMPTS - 1


def test_missing_package_only_waits_for_release_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[bool] = []
    monkeypatch.setattr(r, "identity", lambda *_, **__: RELEASE)
    monkeypatch.setattr(r, "public_tag_commit", lambda _: RELEASE["commit"])

    def all_pypi_files(release, *, complete=True, wait=False):
        waits.append(wait)
        return None

    monkeypatch.setattr(r, "all_pypi_files", all_pypi_files)

    assert r.main(["package-state", TAG]) == 0
    assert r.main(["record", TAG]) == 1
    assert waits == [False, True]


def test_api_refusal_reports_the_bounded_server_message() -> None:
    exc = urllib.error.HTTPError(
        "https://api.github.com/repos/owner/name/releases",
        403,
        "Forbidden",
        {},  # type: ignore[arg-type]
        io.BytesIO(json.dumps({"message": "Resource not accessible by integration"}).encode()),
    )

    assert r.detail(exc) == "Resource not accessible by integration"


def test_api_refusal_without_a_message_stays_bounded() -> None:
    exc = urllib.error.HTTPError(
        "https://api.github.com/repos/owner/name/releases",
        403,
        "Forbidden",
        {},
        io.BytesIO(b"<html>"),
    )  # type: ignore[arg-type]

    assert r.detail(exc) == "no detail"


def test_public_token_prefers_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_GITHUB_TOKEN", "environment-token")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the CLI fallback must not run when the environment supplies a token")

    monkeypatch.setattr(r.subprocess, "run", forbidden)

    assert r.github_token() == "environment-token"


def test_public_token_falls_back_to_the_authenticated_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_GITHUB_TOKEN", raising=False)
    seen: list[tuple[str, ...]] = []

    def run(arguments, **_kwargs):
        seen.append(tuple(arguments))
        return subprocess.CompletedProcess(arguments, 0, "cli-token\n", "")

    monkeypatch.setattr(r.subprocess, "run", run)

    assert r.github_token() == "cli-token"
    assert seen == [("gh", "auth", "token")]


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.CompletedProcess(("gh", "auth", "token"), 1, "", "not logged in"),
        FileNotFoundError("gh"),
    ],
)
def test_public_token_is_absent_without_an_authenticated_cli(
    monkeypatch: pytest.MonkeyPatch, outcome
) -> None:
    monkeypatch.delenv("PUBLIC_GITHUB_TOKEN", raising=False)

    def run(*_args, **_kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(r.subprocess, "run", run)

    assert r.github_token() == ""


HISTORICAL_TAG = "v0.1.5"


def _commit_fixture(repo: Path, tag: str) -> None:
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    subprocess.run(["git", "tag", tag], check=True)


@pytest.fixture
def historical_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tag from before 0.1.8: legacy distribution name, no compatibility project."""
    repo = tmp_path / "historical"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
    (repo / "context_loader").mkdir()
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "{r.COMPAT_PACKAGE}"\nversion = "0.1.5"\n'
    )
    (repo / "context_loader/__init__.py").write_text('__version__ = "0.1.5"\n')
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n## 0.1.5\n\n- Notes\n")
    _commit_fixture(repo, HISTORICAL_TAG)
    return repo


def test_distribution_model_is_selected_by_release_version() -> None:
    assert r.distributions("0.1.5") == (r.COMPAT_PACKAGE,)
    assert r.distributions("0.1.7") == (r.COMPAT_PACKAGE,)
    assert r.distributions("0.1.8") == r.DISTRIBUTIONS
    assert r.distributions("0.2.0") == r.DISTRIBUTIONS
    assert r.canonical_package("0.1.5") == r.COMPAT_PACKAGE
    assert r.canonical_package("0.1.8") == r.PACKAGE
    assert r.all_filenames("0.1.5") == r.filenames("0.1.5", r.COMPAT_PACKAGE)
    assert r.all_filenames("0.1.8") == r.filenames("0.1.8", r.PACKAGE) | r.filenames(
        "0.1.8", r.COMPAT_PACKAGE
    )


def test_historical_release_verifies_against_its_own_single_distribution(
    historical_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = r.identity(HISTORICAL_TAG)
    assert release["version"] == "0.1.5"

    requested: list[str] = []

    def pypi_files(rel, *, package=None, complete=True, wait=False):
        requested.append(package or r.canonical_package(rel["version"]))
        return {name: "0" * 64 for name in r.filenames("0.1.5", r.COMPAT_PACKAGE)}

    monkeypatch.setattr(r, "pypi_files", pypi_files)
    hashes = r.all_pypi_files(release)

    # The canonical project has no historical version and must never be requested.
    assert requested == [r.COMPAT_PACKAGE]
    assert set(hashes) == r.filenames("0.1.5", r.COMPAT_PACKAGE)


def test_historical_compat_artifact_is_not_required_to_pin_a_canonical_release() -> None:
    metadata = f"Name: {r.COMPAT_PACKAGE}\nVersion: 0.1.5\n".encode()
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr(f"{r.archive(r.COMPAT_PACKAGE)}-0.1.5.dist-info/METADATA", metadata)

    r.check_artifact(
        f"{r.archive(r.COMPAT_PACKAGE)}-0.1.5-py3-none-any.whl",
        raw.getvalue(),
        "0.1.5",
        r.COMPAT_PACKAGE,
    )


def _retag(repo: Path, version: str) -> None:
    """Move the whole fixture to a new dual-model version and tag it."""
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "{r.PACKAGE}"\nversion = "{version}"\n'
    )
    (repo / "context_loader/__init__.py").write_text(f'__version__ = "{version}"\n')
    (repo / "CHANGELOG.md").write_text(f"# Changelog\n\n## Unreleased\n\n## {version}\n\n- Notes\n")
    _commit_fixture(repo, f"v{version}")


def test_dual_release_requires_the_compatibility_project(repository: Path) -> None:
    assert r.identity(TAG)["version"] == "1.2.3"

    subprocess.run(["git", "rm", "-rq", r.COMPAT_ROOT], check=True)
    _retag(repository, "1.2.4")

    with pytest.raises(r.ReleaseError):
        r.identity("v1.2.4")


def test_dual_release_rejects_a_compatibility_project_pinning_another_version(
    repository: Path,
) -> None:
    (repository / r.COMPAT_ROOT / "pyproject.toml").write_text(
        f'[project]\nname = "{r.COMPAT_PACKAGE}"\nversion = "1.2.4"\n'
        f'dependencies = ["{r.PACKAGE}==1.2.3"]\n'
    )
    _retag(repository, "1.2.4")

    with pytest.raises(r.ReleaseError, match="does not pin this canonical release"):
        r.identity("v1.2.4")


def test_dual_compat_artifact_without_the_canonical_pin_fails_closed() -> None:
    metadata = f"Name: {r.COMPAT_PACKAGE}\nVersion: 1.2.3\n".encode()
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr(f"{r.archive(r.COMPAT_PACKAGE)}-1.2.3.dist-info/METADATA", metadata)

    with pytest.raises(r.ReleaseError, match="must depend only on the canonical pin"):
        r.check_artifact(
            f"{r.archive(r.COMPAT_PACKAGE)}-1.2.3-py3-none-any.whl",
            raw.getvalue(),
            "1.2.3",
            r.COMPAT_PACKAGE,
        )


def test_dual_release_missing_one_distribution_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "dist"
    hashes = artifacts(source)
    (source / f"{r.archive(r.PACKAGE)}-1.2.3.tar.gz").unlink()
    monkeypatch.setattr(r, "pypi_files", lambda *_, **__: {})

    with pytest.raises(r.ReleaseError, match="original wheel and sdist of both distributions"):
        r.pending_dist(RELEASE, source, tmp_path / "pending")

    assert not (tmp_path / "pending").exists()
    assert set(hashes) == r.all_filenames("1.2.3")


def test_public_tag_identity_uses_git_not_the_rate_limited_rest_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the Gitea backfill failure: a REST read that 403s without a credential."""
    commands: list[tuple[str, ...]] = []

    def command(*args):
        commands.append(args)
        return (
            "ac7296567b86d293dbf24c5fa2826cab2ed5c9e4\trefs/tags/v0.1.5\n"
            "4d80d2ba0eb43b31cd0431a689faf11898801c8f\trefs/tags/v0.1.5^{}\n"
        )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("tag identity must not reach the rate limited REST API")

    monkeypatch.setattr(r, "command", command)
    monkeypatch.setattr(r, "api", forbidden)

    # The annotated tag resolves to its peeled commit, never to the tag object.
    assert r.public_tag_commit("v0.1.5") == "4d80d2ba0eb43b31cd0431a689faf11898801c8f"
    assert commands == [
        (
            "git",
            "ls-remote",
            "--tags",
            f"https://github.com/{r.PUBLIC_REPOSITORY}.git",
            "refs/tags/v0.1.5*",
        )
    ]


def test_public_tag_identity_prefers_the_peeled_commit_and_tolerates_neighbours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = (
        f"{'1' * 40}\trefs/tags/v0.1.5\n"
        f"{'2' * 40}\trefs/tags/v0.1.5^{{}}\n"
        f"{'3' * 40}\trefs/tags/v0.1.50\n"
    )
    monkeypatch.setattr(r, "command", lambda *_: listing)

    assert r.public_tag_commit("v0.1.5") == "2" * 40


def test_public_tag_identity_reports_an_absent_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r, "command", lambda *_: "")

    assert r.public_tag_commit("v9.9.9") is None


def test_lightweight_public_tag_resolves_to_its_own_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(r, "command", lambda *_: f"{'4' * 40}\trefs/tags/v0.1.5\n")

    assert r.public_tag_commit("v0.1.5") == "4" * 40


def test_gitea_sync_skips_a_version_without_a_public_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("RELEASE_TOKEN", "gitea-fixture")
    monkeypatch.setattr(r, "public_tag_commit", lambda _: None)
    monkeypatch.setattr(r, "api", lambda *_, **__: pytest.fail("no REST call before the tag check"))

    assert r.sync_gitea("v9.9.9", "http://gitea.invalid/api/v1", "org/repo") == {
        "status": "SKIP",
        "reason": "version has no public tag",
    }


def _http_error(code: int, headers: dict[str, str], message: str = "boom"):
    return urllib.error.HTTPError(
        "https://api.github.com/repos/owner/name/releases/tags/v0.1.5",
        code,
        "Forbidden",
        headers,  # type: ignore[arg-type]
        io.BytesIO(json.dumps({"message": message}).encode()),
    )


def test_rate_limited_read_is_waited_out_within_a_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second Gitea backfill failure: an unauthenticated public read refused 403."""
    slept: list[float] = []
    monkeypatch.setattr(r.time, "sleep", slept.append)
    monkeypatch.setattr(r.time, "time", lambda: 1000.0)
    attempts = 0

    def urlopen(_request, timeout=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _http_error(
                403,
                {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1030"},
                "API rate limit exceeded for 203.0.113.7.",
            )
        return contextlib.nullcontext(io.BytesIO(b'{"ok": true}')).__enter__()

    monkeypatch.setattr(r.urllib.request, "urlopen", urlopen)

    assert json.loads(r.request("https://api.github.com/x")) == {"ok": True}
    assert attempts == 2
    assert slept == [31.0]  # waits out the reset window, plus one second


def test_rate_limit_wait_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r.time, "time", lambda: 0.0)
    exc = _http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "999999"})

    assert r.rate_limit_delay(exc) == r.RATE_LIMIT_MAX_DELAY_SECONDS


def test_permission_refusal_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(r.time, "sleep", slept.append)
    attempts = 0

    def urlopen(_request, timeout=None):
        nonlocal attempts
        attempts += 1
        raise _http_error(
            403, {"X-RateLimit-Remaining": "59"}, "Resource not accessible by integration"
        )

    monkeypatch.setattr(r.urllib.request, "urlopen", urlopen)

    with pytest.raises(r.ReleaseError, match="Resource not accessible by integration"):
        r.request("https://api.github.com/x", method="POST", data={})

    # A permission refusal must stop on the first response, not consume the budget.
    assert attempts == 1
    assert slept == []


def test_rate_limited_read_gives_up_within_the_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(r.time, "sleep", slept.append)
    monkeypatch.setattr(r.time, "time", lambda: 0.0)

    def urlopen(_request, timeout=None):
        raise _http_error(403, {"X-RateLimit-Remaining": "0"}, "API rate limit exceeded")

    monkeypatch.setattr(r.urllib.request, "urlopen", urlopen)

    with pytest.raises(r.ReleaseError, match="API rate limit exceeded"):
        r.request("https://api.github.com/x")

    assert len(slept) == r.RATE_LIMIT_ATTEMPTS - 1
    assert sum(slept) <= r.RATE_LIMIT_ATTEMPTS * r.RATE_LIMIT_MAX_DELAY_SECONDS


def artifacts(path: Path) -> dict[str, str]:
    path.mkdir()
    for package in r.DISTRIBUTIONS:
        stem = r.archive(package)
        fields = f"Name: {package}\nVersion: 1.2.3\n"
        if package == r.COMPAT_PACKAGE:
            fields += f"Requires-Dist: {r.PACKAGE}==1.2.3\n"
        metadata = fields.encode()
        with zipfile.ZipFile(path / f"{stem}-1.2.3-py3-none-any.whl", "w") as archive:
            archive.writestr(f"{stem}-1.2.3.dist-info/METADATA", metadata)
        with tarfile.open(path / f"{stem}-1.2.3.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo(f"{stem}-1.2.3/PKG-INFO")
            member.size = len(metadata)
            archive.addfile(member, io.BytesIO(metadata))
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}


def test_partial_upload_reuses_only_missing_original_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "dist"
    hashes = artifacts(source)
    canonical_wheel = f"{r.archive(r.PACKAGE)}-1.2.3-py3-none-any.whl"

    def published(release, *, package=r.PACKAGE, complete=True, wait=False):
        return {canonical_wheel: hashes[canonical_wheel]} if package == r.PACKAGE else {}

    monkeypatch.setattr(r, "pypi_files", published)
    output = tmp_path / "pending"
    pending = r.pending_dist(RELEASE, source, output)

    assert pending[r.PACKAGE] == [f"{r.archive(r.PACKAGE)}-1.2.3.tar.gz"]
    assert pending[r.COMPAT_PACKAGE] == sorted(r.filenames("1.2.3", r.COMPAT_PACKAGE))
    assert {p.name for p in output.iterdir()} == set(r.DISTRIBUTIONS)
    for package, names in pending.items():
        assert {p.name for p in (output / package).iterdir()} == set(names)
        for name in names:
            assert (output / package / name).read_bytes() == (source / name).read_bytes()

    monkeypatch.setattr(r, "pypi_files", lambda *_, **__: hashes)
    assert r.pending_dist(RELEASE, source, tmp_path / "retry") == {
        r.PACKAGE: [],
        r.COMPAT_PACKAGE: [],
    }


def test_existing_file_conflict_stops_before_upload_selection(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "dist"
    hashes = artifacts(source)
    monkeypatch.setattr(r, "pypi_files", lambda *_, **__: dict.fromkeys(hashes, "0" * 64))
    with pytest.raises(r.ReleaseError, match="differs from original build"):
        r.pending_dist(RELEASE, source, tmp_path / "pending")
    assert not (tmp_path / "pending").exists()


def test_record_resume_after_post_succeeded_but_readback_failed(monkeypatch) -> None:
    hashes = {"wheel": "hash"}
    record = None
    posts = 0
    fail_readback = True
    monkeypatch.setattr(r, "public_tag_commit", lambda _: SHA)

    def api(url, **kwargs):
        nonlocal record, posts, fail_readback
        if kwargs.get("method") == "POST":
            posts += 1
            record = {**kwargs["data"], "id": 17, "html_url": "https://example.invalid/release"}
            return record
        if record and fail_readback:
            fail_readback = False
            raise r.ReleaseError("readback unavailable")
        return record

    monkeypatch.setattr(r, "api", api)
    with pytest.raises(r.ReleaseError, match="readback unavailable"):
        r.release_record(
            RELEASE,
            hashes,
            "github",
            r.GITHUB_API,
            r.PUBLIC_REPOSITORY,
            apply=True,
            token="fixture",
        )
    for _ in range(2):
        result = r.release_record(
            RELEASE,
            hashes,
            "github",
            r.GITHUB_API,
            r.PUBLIC_REPOSITORY,
            apply=True,
            token="fixture",
        )
        assert result["status"] == "PASS" and not result["created"]
    assert posts == 1


@pytest.mark.parametrize("conflict", ["tag", "files", "draft", "commit", "target"])
def test_release_conflict_is_fail_closed_without_mutation(monkeypatch, conflict) -> None:
    hashes = {"wheel": "hash"}
    marker = {"tag": TAG, "commit": SHA, "files": hashes}
    record = {
        "id": 1,
        "tag_name": TAG,
        "draft": False,
        "prerelease": False,
        "html_url": "https://example.invalid/release",
        "sha1": SHA,
    }
    if conflict == "files":
        marker["files"] = {"wheel": "different"}
    if conflict == "draft":
        record["draft"] = True
    if conflict == "commit":
        record["sha1"] = "b" * 40
    if conflict == "target":
        record["target_commitish"] = "b" * 40
    record["body"] = r.MARKER + json.dumps(marker) + " -->"
    monkeypatch.setattr(r, "public_tag_commit", lambda _: "b" * 40 if conflict == "tag" else SHA)

    def api(url, **kwargs):
        assert kwargs.get("method", "GET") == "GET"
        return record

    monkeypatch.setattr(r, "api", api)
    with pytest.raises(r.ReleaseError, match="conflict"):
        r.release_record(RELEASE, hashes, "github", r.GITHUB_API, r.PUBLIC_REPOSITORY, apply=True)


def test_local_caller_cannot_backfill_tag_with_pat(monkeypatch) -> None:
    monkeypatch.delenv("GITEA_ACTIONS", raising=False)
    with pytest.raises(r.ReleaseError, match="Gitea Actions job token"):
        r.sync_gitea(TAG, "https://example.invalid/api/v1", "org/repo")


@pytest.mark.parametrize("conflicting_record", [False, True])
def test_gitea_backfill_preserves_tag_object_and_does_not_build(
    monkeypatch, conflicting_record
) -> None:
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("RELEASE_TOKEN", "private-fixture")
    monkeypatch.setattr(r, "public_tag_commit", lambda _: SHA)
    monkeypatch.setattr(r, "identity", lambda *_: RELEASE)
    monkeypatch.setattr(r, "pypi_files", lambda *_, **__: {"wheel": "hash"})

    def api(url, **kwargs):
        if url.endswith("/org/repo"):
            return {}
        if "/releases/tags/" in url and conflicting_record:
            return {"tag_name": TAG, "draft": True, "prerelease": False}
        return None

    monkeypatch.setattr(r, "api", api)
    actions = []

    def command(*args):
        actions.append(args)
        if args[1] == "rev-parse":
            return "c" * 40
        if args[1] == "ls-remote":
            return "c" * 40 + "\trefs/tags/" + TAG
        return ""

    def record(*args, **kwargs):
        if args[2] == "github":
            assert "token" not in kwargs
        else:
            assert kwargs["token"] == "private-fixture"
        return {"status": "PASS"}

    monkeypatch.setattr(r, "command", command)
    monkeypatch.setattr(r, "release_record", record)
    if conflicting_record:
        with pytest.raises(r.ReleaseError, match="Release identity conflict"):
            r.sync_gitea(TAG, "https://example.invalid/api/v1", "org/repo")
        assert not any(action[1] == "push" for action in actions)
        return
    result = r.sync_gitea(TAG, "https://example.invalid/api/v1", "org/repo")
    assert result["tag_object"] == "c" * 40
    assert all(action[0] == "git" for action in actions)
    assert [a for a in actions if a[1] == "push"] == [
        ("git", "push", "--no-follow-tags", "origin", f"refs/tags/{TAG}:refs/tags/{TAG}")
    ]
