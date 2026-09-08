"""Close release records without rebuilding or republishing an existing package.

Git publication remains separate. This script consumes existing exact tags and the
existing changelog; only the GitHub workflow may build/upload packages.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path

try:
    from scripts.changelog import extract_tag
except ModuleNotFoundError:
    from changelog import extract_tag

PUBLIC_REPOSITORY = "xuanheng-tech/context-loader"
# 0.1.8 splits the published identity: the canonical distribution carries the runtime
# and the console script, and the legacy name stays as a shim that depends on it.
PACKAGE = "context-loader"
COMPAT_PACKAGE = "codex-project-context-loader"
DISTRIBUTIONS = (PACKAGE, COMPAT_PACKAGE)
COMPAT_ROOT = f"compat/{COMPAT_PACKAGE}"
# The split starts at 0.1.8. Every earlier tag shipped one distribution under the
# legacy name, so those releases are verified against the model that existed then
# and are never expected to have a canonical counterpart.
DUAL_DISTRIBUTION_VERSION = (0, 1, 8)


def archive(package: str) -> str:
    return package.replace("-", "_")


def is_dual(version: str) -> bool:
    return tuple(int(part) for part in version.split(".")) >= DUAL_DISTRIBUTION_VERSION


def canonical_package(version: str) -> str:
    """The distribution that carries the runtime for this release."""
    return PACKAGE if is_dual(version) else COMPAT_PACKAGE


def distributions(version: str) -> tuple[str, ...]:
    return DISTRIBUTIONS if is_dual(version) else (COMPAT_PACKAGE,)


GITHUB_API = "https://api.github.com"
TAG_RE = re.compile(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
MARKER = "<!-- context-loader-release "
# PyPI serves a freshly uploaded version from its index and integrity endpoints
# a little after upload, so release closure polls instead of failing that race.
PROPAGATION_ATTEMPTS = 12
PROPAGATION_DELAY_SECONDS = 15
# An unauthenticated public read is limited to 60 requests per hour per source
# address, and the Gitea sync carries no GitHub credential by design. The window is
# short relative to the job timeout, so a rate-limited refusal is waited out rather
# than failing the release closure. Only rate limiting is retried; every other
# refusal, including a permission refusal, still stops immediately.
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_DELAY_SECONDS = 60
RATE_LIMIT_MAX_DELAY_SECONDS = 180


class ReleaseError(ValueError):
    """Missing evidence or conflicting immutable publication identity."""


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ReleaseError(f"{args[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def identity(tag: str, expected_sha: str | None = None, *, checkout: bool = False) -> dict:
    if TAG_RE.fullmatch(tag) is None:
        raise ReleaseError("invalid formal release tag")
    commit = command("git", "rev-parse", f"refs/tags/{tag}^{{commit}}")
    if expected_sha is not None and commit != expected_sha:
        raise ReleaseError("tag/expected commit mismatch")
    if checkout and command("git", "rev-parse", "HEAD") != commit:
        raise ReleaseError("build checkout is not the exact release commit")
    project = tomllib.loads(command("git", "show", f"{commit}:pyproject.toml"))["project"]
    package = command("git", "show", f"{commit}:context_loader/__init__.py")
    version = tag[1:]
    if project["name"] != canonical_package(version) or project["version"] != version:
        raise ReleaseError("tag/project version mismatch")
    if re.search(rf'^__version__ = "{re.escape(version)}"$', package, re.MULTILINE) is None:
        raise ReleaseError("package version declarations disagree")
    if is_dual(version):
        compat = tomllib.loads(command("git", "show", f"{commit}:{COMPAT_ROOT}/pyproject.toml"))
        if (
            compat["project"]["name"] != COMPAT_PACKAGE
            or compat["project"]["version"] != version
            or compat["project"].get("dependencies") != [f"{PACKAGE}=={version}"]
            or "scripts" in compat["project"]
        ):
            raise ReleaseError("compatibility distribution does not pin this canonical release")
    notes = extract_tag(command("git", "show", f"{commit}:CHANGELOG.md"), tag)
    return {"tag": tag, "version": version, "commit": commit, "notes": notes}


def detail(exc: urllib.error.HTTPError) -> str:
    """Return the bounded API error message; responses carry no request credentials."""
    try:
        message = json.loads(exc.read(4096).decode("utf-8")).get("message")
    except (OSError, UnicodeError, ValueError, AttributeError):
        return "no detail"
    return message[:200] if isinstance(message, str) else "no detail"


def rate_limit_delay(exc: urllib.error.HTTPError) -> float | None:
    """Seconds to wait when a refusal is a rate limit, or None when it is not one."""
    if exc.code not in {403, 429}:
        return None
    if exc.headers.get("X-RateLimit-Remaining") != "0":
        return None
    try:
        reset = float(exc.headers.get("X-RateLimit-Reset", ""))
    except (TypeError, ValueError):
        return RATE_LIMIT_DELAY_SECONDS
    return min(max(reset - time.time(), 0.0) + 1.0, RATE_LIMIT_MAX_DELAY_SECONDS)


def poll(load):
    """Repeat one bounded PyPI read while the published version is still propagating."""
    for remaining in range(PROPAGATION_ATTEMPTS - 1, -1, -1):
        value = load()
        if value is not None or not remaining:
            return value
        time.sleep(PROPAGATION_DELAY_SECONDS)
    return None


def request(url: str, *, token: str = "", method: str = "GET", data: dict | None = None):
    headers = {"Accept": "application/json", "User-Agent": "context-loader-release"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if data is None else json.dumps(data).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    operation = urllib.request.Request(url, data=body, headers=headers, method=method)
    for remaining in range(RATE_LIMIT_ATTEMPTS - 1, -1, -1):
        try:
            with urllib.request.urlopen(operation, timeout=30) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            delay = rate_limit_delay(exc)
            if delay is None or not remaining:
                raise ReleaseError(
                    f"release HTTP {method} failed: {exc.code}: {detail(exc)}"
                ) from None
            time.sleep(delay)
        except urllib.error.URLError:
            raise ReleaseError("release HTTP request unavailable") from None
    if len(raw) > 8 * 1024 * 1024:
        raise ReleaseError("release response exceeds 8 MiB")
    return raw


def api(url: str, **kwargs):
    raw = request(url, **kwargs)
    return None if raw is None else json.loads(raw)


def github_token() -> str:
    """Return the public read token from the environment, else from an authenticated gh CLI.

    CI supplies PUBLIC_GITHUB_TOKEN. A local caller falls back to the credential the
    GitHub CLI already holds, so verification is authenticated without this repository
    storing, printing, or requiring a personal token of its own.
    """
    token = os.environ.get("PUBLIC_GITHUB_TOKEN", "")
    if token:
        return token
    try:
        result = subprocess.run(
            ("gh", "auth", "token"),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def public_tag_commit(tag: str) -> str | None:
    """Resolve the commit of the public tag from Git, or None when the tag is absent.

    Tag identity is a Git fact, and the public Git endpoint answers it without a
    credential. The REST API is rate limited per source address, and the Gitea sync
    deliberately carries no GitHub credential - its job token must never be sent to
    GitHub - so reading tag identity over REST made that job fail on a shared address.
    """
    listing = command(
        "git",
        "ls-remote",
        "--tags",
        f"https://github.com/{PUBLIC_REPOSITORY}.git",
        f"refs/tags/{tag}*",
    )
    refs: dict[str, str] = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) == 2:
            refs[fields[1]] = fields[0]
    # An annotated tag advertises its peeled commit; a lightweight tag points at one.
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if commit is None:
        return None
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ReleaseError("public tag does not resolve to a commit")
    return commit


def filenames(version: str, package: str = PACKAGE) -> set[str]:
    stem = archive(package)
    return {f"{stem}-{version}-py3-none-any.whl", f"{stem}-{version}.tar.gz"}


def all_filenames(version: str) -> set[str]:
    return set().union(*(filenames(version, package) for package in distributions(version)))


def check_artifact(name: str, raw: bytes, version: str, package: str = PACKAGE) -> None:
    if name.endswith(".whl"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            paths = [p for p in archive.namelist() if p.endswith(".dist-info/METADATA")]
            if len(paths) != 1:
                raise ReleaseError("ambiguous wheel metadata")
            metadata = archive.read(paths[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            paths = [p for p in archive.getmembers() if p.name.endswith("/PKG-INFO")]
            if len(paths) != 1 or not paths[0].isfile():
                raise ReleaseError("ambiguous source metadata")
            metadata = archive.extractfile(paths[0]).read()
    parsed = BytesParser().parsebytes(metadata)
    if parsed["Name"] != package or parsed["Version"] != version:
        raise ReleaseError("package artifact identity mismatch")
    if package == COMPAT_PACKAGE and is_dual(version):
        required = f"{PACKAGE}=={version}"
        if [value.strip() for value in parsed.get_all("Requires-Dist") or []] != [required]:
            raise ReleaseError("compatibility distribution must depend only on the canonical pin")


def check_provenance(
    item: dict, release: dict, *, package: str = PACKAGE, wait: bool = False
) -> None:
    name = item["filename"]
    url = f"https://pypi.org/integrity/{package}/{release['version']}/{name}/provenance"
    provenance = poll(lambda: api(url)) if wait else api(url)
    if provenance is None:
        raise ReleaseError("PyPI provenance missing; do not rebuild or upload")
    for bundle in provenance["attestation_bundles"]:
        publisher = bundle["publisher"]
        if publisher != {
            "kind": "GitHub",
            "repository": PUBLIC_REPOSITORY,
            "workflow": "publish-pypi.yml",
            "environment": "pypi",
        }:
            continue
        for attestation in bundle["attestations"]:
            statement = json.loads(base64.b64decode(attestation["envelope"]["statement"]))
            if statement["subject"] != [
                {"name": name, "digest": {"sha256": item["digests"]["sha256"]}}
            ]:
                continue
            certificate = base64.b64decode(attestation["verification_material"]["certificate"])
            result = subprocess.run(
                ["openssl", "x509", "-inform", "DER", "-noout", "-text"],
                input=certificate,
                capture_output=True,
                check=False,
            )
            text = result.stdout.decode("utf-8")
            # Compare PyPI's HTTPS-served provenance claims; this is not a new
            # cryptographic verifier or a substitute for Sigstore verification.
            sha = re.search(r"1\.3\.6\.1\.4\.1\.57264\.1\.3:\s*\n\s*([0-9a-f]{40})\s*\n", text)
            uri = f"URI:https://github.com/{PUBLIC_REPOSITORY}/.github/workflows/publish-pypi.yml@refs/tags/{release['tag']}"
            if (
                result.returncode == 0
                and sha
                and sha[1] == release["commit"]
                and uri + "\n" in text
            ):
                return
    raise ReleaseError("PyPI publisher/commit/file provenance conflict")


def pypi_files(
    release: dict, *, package: str | None = None, complete: bool = True, wait: bool = False
) -> dict[str, str] | None:
    package = package or canonical_package(release["version"])
    url = f"https://pypi.org/pypi/{package}/{release['version']}/json"
    doc = poll(lambda: api(url)) if wait else api(url)
    if doc is None:
        return None
    if doc["info"]["name"] != package or doc["info"]["version"] != release["version"]:
        raise ReleaseError("PyPI project/version conflict")
    items = doc["urls"]
    names = {item["filename"] for item in items}
    expected = filenames(release["version"], package)
    if len(names) != len(items) or not names <= expected or (complete and names != expected):
        raise ReleaseError("PyPI file set incomplete/conflicting; resume the original publish job")
    hashes = {}
    for item in items:
        url = urllib.parse.urlsplit(item["url"])
        if url.scheme != "https" or url.hostname != "files.pythonhosted.org" or item["yanked"]:
            raise ReleaseError("unexpected PyPI file origin or yanked file")
        raw = request(item["url"])
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item["digests"]["sha256"] or len(raw) != item["size"]:
            raise ReleaseError("PyPI downloaded file digest/size conflict")
        check_artifact(item["filename"], raw, release["version"], package)
        check_provenance(item, release, package=package, wait=wait)
        hashes[item["filename"]] = digest
    return hashes


def all_pypi_files(
    release: dict, *, complete: bool = True, wait: bool = False
) -> dict[str, str] | None:
    """Merge both distributions' verified files, or None while any is unpublished."""
    merged: dict[str, str] = {}
    for package in distributions(release["version"]):
        hashes = pypi_files(release, package=package, complete=complete, wait=wait)
        if hashes is None:
            return None
        merged.update(hashes)
    return merged


def dist_artifacts(dist: Path) -> list[str]:
    if not dist.is_dir():
        return []
    return sorted(entry.name for entry in dist.iterdir() if entry.name != ".gitignore")


def build(release: dict, dist: Path) -> bool:
    identity(release["tag"], release["commit"], checkout=True)
    command("just", "check")
    if all_pypi_files(release) is not None:
        return False
    # An artifact left in dist can carry a release filename while holding different
    # bytes, so only an empty dist may be built into and only the expected pair may
    # come out of the build.
    if dist_artifacts(dist):
        raise ReleaseError("dist already holds artifacts; build only into an empty dist")
    # Both distributions are built from this one release commit into one dist
    # directory; their archive names never collide.
    command("uv", "build", "--out-dir", os.fspath(dist))
    if is_dual(release["version"]):
        command("uv", "build", "--project", COMPAT_ROOT, "--out-dir", os.fspath(dist))
    produced = dist_artifacts(dist)
    if set(produced) != all_filenames(release["version"]):
        raise ReleaseError("build produced an unexpected artifact set")
    return True


def pending_dist(release: dict, source: Path, output: Path) -> dict[str, list[str]]:
    """Select only the missing original artifacts, grouped by target PyPI project."""
    paths = {p.name: p for p in source.iterdir() if p.name != ".gitignore"}
    if set(paths) != all_filenames(release["version"]) or any(
        not p.is_file() or p.is_symlink() for p in paths.values()
    ):
        raise ReleaseError("expected the original wheel and sdist of both distributions only")
    pending: dict[str, list[str]] = {}
    for package in distributions(release["version"]):
        existing = pypi_files(release, package=package, complete=False) or {}
        selected = []
        for name in sorted(filenames(release["version"], package)):
            raw = paths[name].read_bytes()
            check_artifact(name, raw, release["version"], package)
            digest = hashlib.sha256(raw).hexdigest()
            if name in existing:
                if existing[name] != digest:
                    raise ReleaseError(
                        "existing PyPI file differs from original build; refusing upload"
                    )
            else:
                selected.append(name)
        pending[package] = selected
    # Nothing is written until every distribution passed its identity comparison, so a
    # refusal leaves no partial upload directory behind.
    output.mkdir()  # A fresh job-owned directory; never clean or overwrite caller data.
    for package, names in pending.items():
        if not names:
            continue
        project_output = output / package
        project_output.mkdir()
        for name in names:
            shutil.copyfile(paths[name], project_output / name)
    return pending


def check_record(record: dict, release: dict, hashes: dict, platform: str) -> None:
    if record["tag_name"] != release["tag"] or record["draft"] or record["prerelease"]:
        raise ReleaseError(f"{platform} Release identity conflict")
    if record.get("sha1", release["commit"]) != release["commit"]:
        raise ReleaseError(f"{platform} Release commit conflict")
    target = record.get("target_commitish", "")
    if re.fullmatch(r"[0-9a-f]{40}", target) and target != release["commit"]:
        raise ReleaseError(f"{platform} Release target commit conflict")
    body = record.get("body") or ""
    if MARKER in body:
        marker = body.split(MARKER, 1)[1].split(" -->", 1)[0]
        expected = {"tag": release["tag"], "commit": release["commit"], "files": hashes}
        if json.loads(marker) != expected:
            raise ReleaseError(f"{platform} Release file identity conflict")


def release_record(
    release: dict,
    hashes: dict,
    platform: str,
    base: str,
    repository: str,
    *,
    apply: bool = False,
    token: str = "",
) -> dict:
    root = f"{base.rstrip('/')}/repos/{repository}"
    if platform == "github":
        if base != GITHUB_API or repository != PUBLIC_REPOSITORY:
            raise ReleaseError("unexpected GitHub release authority")
        target = public_tag_commit(release["tag"])
    else:
        tag = api(f"{root}/tags/{release['tag']}", token=token)
        target = None if tag is None else tag["commit"]["sha"]
    if target != release["commit"]:
        raise ReleaseError(f"{platform} tag missing or conflicting")
    expected = {"tag": release["tag"], "commit": release["commit"], "files": hashes}
    endpoint = f"{root}/releases/tags/{release['tag']}"
    # The Release record is GitHub-only metadata and stays a REST read; authenticate it
    # whenever a public read credential exists so it is not rate limited either.
    record = api(endpoint, token=token or (github_token() if platform == "github" else ""))
    created = False
    if record is None and apply:
        if not token:
            raise ReleaseError("RELEASE_TOKEN is missing")
        body = (
            release["notes"]
            + "\n\nPackage: "
            + f"https://pypi.org/project/{canonical_package(release['version'])}/"
            + f"{release['version']}/"
            + f"\n\nSource commit: `{release['commit']}`\n\n"
            + MARKER
            + json.dumps(expected, sort_keys=True)
            + " -->"
        )
        record = api(
            f"{root}/releases",
            token=token,
            method="POST",
            data={
                "tag_name": release["tag"],
                "target_commitish": release["commit"],
                "name": release["tag"],
                "body": body,
                "draft": False,
                "prerelease": False,
            },
        )
        created = True
        record = api(endpoint, token=token)
    if record is None:
        raise ReleaseError(f"{platform} Release missing")
    check_record(record, release, hashes, platform)
    # target_commitish can be a historical branch name. For an existing tag,
    # GitHub identifies the release through tag_name, not today's branch HEAD.
    return {"status": "PASS", "created": created, "id": record["id"], "url": record["html_url"]}


def sync_gitea(tag: str, base: str, repository: str) -> dict:
    # Only the built-in Actions actor suppresses recursive tag workflows on
    # Gitea. A PAT/local caller must never use this old-tag backfill route.
    if os.environ.get("GITEA_ACTIONS") != "true" or base == GITHUB_API:
        raise ReleaseError("tag synchronization requires the Gitea Actions job token")
    if TAG_RE.fullmatch(tag) is None:
        raise ReleaseError("invalid formal release tag")
    token = os.environ.get("RELEASE_TOKEN", "")
    if not token:
        raise ReleaseError("RELEASE_TOKEN is missing")
    public_commit = public_tag_commit(tag)
    if public_commit is None:
        return {"status": "SKIP", "reason": "version has no public tag"}
    command(
        "git",
        "fetch",
        "--no-tags",
        f"https://github.com/{PUBLIC_REPOSITORY}.git",
        f"refs/tags/{tag}:refs/tags/{tag}",
    )
    release = identity(tag, public_commit)
    hashes = all_pypi_files(release, wait=True)
    if hashes is None:
        return {
            "status": "SKIP",
            "reason": "package not yet published; rerun after GitHub publication",
        }
    public = release_record(release, hashes, "github", GITHUB_API, PUBLIC_REPOSITORY)
    root = f"{base.rstrip('/')}/repos/{repository}"
    if api(root, token=token) is None:
        raise ReleaseError("Gitea repository is unavailable to the job token")
    existing_record = api(f"{root}/releases/tags/{tag}", token=token)
    if existing_record is not None:
        check_record(existing_record, release, hashes, "gitea")
    endpoint = f"{root}/tags/{tag}"
    existing = api(endpoint, token=token)
    if existing is None:
        # Checkout installs a host-scoped ephemeral job credential. Fetching the
        # public tag above does not send that credential to GitHub.
        command("git", "push", "--no-follow-tags", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    elif existing["commit"]["sha"] != public_commit:
        raise ReleaseError("Gitea formal tag identity conflict")
    raw_tag = command("git", "rev-parse", f"refs/tags/{tag}")
    remote = command("git", "ls-remote", "--refs", "origin", f"refs/tags/{tag}")
    if remote.split() != [raw_tag, f"refs/tags/{tag}"]:
        raise ReleaseError("Gitea tag object identity conflict")
    private = release_record(release, hashes, "gitea", base, repository, apply=True, token=token)
    return {
        "tag": tag,
        "commit": public_commit,
        "tag_object": raw_tag,
        "package_verification": "PASS",
        "github_release": public,
        "gitea_release": private,
        "files": hashes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "gate",
            "build",
            "package-state",
            "pending-dist",
            "record",
            "verify",
            "sync-gitea",
        ),
    )
    parser.add_argument("tag")
    parser.add_argument("--expected-sha")
    parser.add_argument("--platform", choices=("github", "gitea"), default="github")
    parser.add_argument("--api-url", default=GITHUB_API)
    parser.add_argument("--repository", default=PUBLIC_REPOSITORY)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--output", type=Path, default=Path("pending-dist"))
    args = parser.parse_args(argv)
    try:
        if args.command == "sync-gitea":
            print(json.dumps(sync_gitea(args.tag, args.api_url, args.repository), sort_keys=True))
            return 0
        release = identity(args.tag, args.expected_sha)
        result = {"tag": args.tag, "commit": release["commit"]}
        outputs = {}
        if args.command == "build":
            outputs["built"] = str(build(release, args.dist)).lower()
        elif args.command == "pending-dist":
            selected = pending_dist(release, args.dist, args.output)
            result["pending"] = selected
            outputs["pending"] = str(any(selected.values())).lower()
            for package in distributions(release["version"]):
                outputs[f"pending_{archive(package)}"] = str(bool(selected[package])).lower()
        elif args.command != "gate":
            if public_tag_commit(args.tag) != release["commit"]:
                raise ReleaseError("GitHub tag identity conflict")
            hashes = all_pypi_files(release, wait=args.command in ("record", "verify"))
            result["package_verification"] = "MISSING" if hashes is None else "PASS"
            result["files"] = hashes
            if args.command in ("record", "verify"):
                if hashes is None:
                    raise ReleaseError(
                        "PyPI package missing; record closure cannot publish packages"
                    )
                result["release_verification"] = release_record(
                    release,
                    hashes,
                    args.platform,
                    args.api_url,
                    args.repository,
                    apply=args.command == "record",
                    token=os.environ.get("RELEASE_TOKEN", ""),
                )
        result.update(outputs)
        if outputs and os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                for key, value in outputs.items():
                    stream.write(f"{key}={value}\n")
        print(json.dumps(result, sort_keys=True))
    except (ReleaseError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"release_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
