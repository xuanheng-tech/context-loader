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
PACKAGE = "codex-project-context-loader"
ARCHIVE = PACKAGE.replace("-", "_")
GITHUB_API = "https://api.github.com"
TAG_RE = re.compile(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
MARKER = "<!-- context-loader-release "


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
    if project["name"] != PACKAGE or project["version"] != version:
        raise ReleaseError("tag/project version mismatch")
    if re.search(rf'^__version__ = "{re.escape(version)}"$', package, re.MULTILINE) is None:
        raise ReleaseError("package version declarations disagree")
    notes = extract_tag(command("git", "show", f"{commit}:CHANGELOG.md"), tag)
    return {"tag": tag, "version": version, "commit": commit, "notes": notes}


def request(url: str, *, token: str = "", method: str = "GET", data: dict | None = None):
    headers = {"Accept": "application/json", "User-Agent": "context-loader-release"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if data is None else json.dumps(data).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    operation = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(operation, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == "GET":
            return None
        raise ReleaseError(f"release HTTP {method} failed: {exc.code}") from None
    except urllib.error.URLError:
        raise ReleaseError("release HTTP request unavailable") from None
    if len(raw) > 8 * 1024 * 1024:
        raise ReleaseError("release response exceeds 8 MiB")
    return raw


def api(url: str, **kwargs):
    raw = request(url, **kwargs)
    return None if raw is None else json.loads(raw)


def github_tag(tag: str) -> str | None:
    token = os.environ.get("PUBLIC_GITHUB_TOKEN", "")
    ref = api(f"{GITHUB_API}/repos/{PUBLIC_REPOSITORY}/git/ref/tags/{tag}", token=token)
    if ref is None:
        return None
    obj = ref["object"]
    if obj["type"] == "tag":
        obj = api(f"{GITHUB_API}/repos/{PUBLIC_REPOSITORY}/git/tags/{obj['sha']}", token=token)[
            "object"
        ]
    if obj["type"] != "commit":
        raise ReleaseError("public tag does not resolve to a commit")
    return obj["sha"]


def filenames(version: str) -> set[str]:
    return {f"{ARCHIVE}-{version}-py3-none-any.whl", f"{ARCHIVE}-{version}.tar.gz"}


def check_artifact(name: str, raw: bytes, version: str) -> None:
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
    if parsed["Name"] != PACKAGE or parsed["Version"] != version:
        raise ReleaseError("package artifact identity mismatch")


def check_provenance(item: dict, release: dict) -> None:
    name = item["filename"]
    provenance = api(f"https://pypi.org/integrity/{PACKAGE}/{release['version']}/{name}/provenance")
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


def pypi_files(release: dict, *, complete: bool = True) -> dict[str, str] | None:
    doc = api(f"https://pypi.org/pypi/{PACKAGE}/{release['version']}/json")
    if doc is None:
        return None
    if doc["info"]["name"] != PACKAGE or doc["info"]["version"] != release["version"]:
        raise ReleaseError("PyPI project/version conflict")
    items = doc["urls"]
    names = {item["filename"] for item in items}
    expected = filenames(release["version"])
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
        check_artifact(item["filename"], raw, release["version"])
        check_provenance(item, release)
        hashes[item["filename"]] = digest
    return hashes


def dist_artifacts(dist: Path) -> list[str]:
    if not dist.is_dir():
        return []
    return sorted(entry.name for entry in dist.iterdir() if entry.name != ".gitignore")


def build(release: dict, dist: Path) -> bool:
    identity(release["tag"], release["commit"], checkout=True)
    command("just", "check")
    if pypi_files(release) is not None:
        return False
    # An artifact left in dist can carry a release filename while holding different
    # bytes, so only an empty dist may be built into and only the expected pair may
    # come out of the build.
    if dist_artifacts(dist):
        raise ReleaseError("dist already holds artifacts; build only into an empty dist")
    command("uv", "build")
    produced = dist_artifacts(dist)
    if set(produced) != filenames(release["version"]):
        raise ReleaseError("build produced an unexpected artifact set")
    return True


def pending_dist(release: dict, source: Path, output: Path) -> list[str]:
    expected = filenames(release["version"])
    paths = {p.name: p for p in source.iterdir() if p.name != ".gitignore"}
    if set(paths) != expected or any(not p.is_file() or p.is_symlink() for p in paths.values()):
        raise ReleaseError("expected original wheel and sdist only")
    existing = pypi_files(release, complete=False) or {}
    pending = []
    for name, path in sorted(paths.items()):
        raw = path.read_bytes()
        check_artifact(name, raw, release["version"])
        digest = hashlib.sha256(raw).hexdigest()
        if name in existing:
            if existing[name] != digest:
                raise ReleaseError(
                    "existing PyPI file differs from original build; refusing upload"
                )
        else:
            pending.append(name)
    output.mkdir()  # A fresh job-owned directory; never clean or overwrite caller data.
    for name in pending:
        shutil.copyfile(paths[name], output / name)
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
        target = github_tag(release["tag"])
    else:
        tag = api(f"{root}/tags/{release['tag']}", token=token)
        target = None if tag is None else tag["commit"]["sha"]
    if target != release["commit"]:
        raise ReleaseError(f"{platform} tag missing or conflicting")
    expected = {"tag": release["tag"], "commit": release["commit"], "files": hashes}
    endpoint = f"{root}/releases/tags/{release['tag']}"
    record = api(endpoint, token=token)
    created = False
    if record is None and apply:
        if not token:
            raise ReleaseError("RELEASE_TOKEN is missing")
        body = (
            release["notes"]
            + f"\n\nPackage: https://pypi.org/project/{PACKAGE}/{release['version']}/"
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
    public_commit = github_tag(tag)
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
    hashes = pypi_files(release)
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
            result["pending"] = pending_dist(release, args.dist, args.output)
            outputs["pending"] = str(bool(result["pending"])).lower()
        elif args.command != "gate":
            if github_tag(args.tag) != release["commit"]:
                raise ReleaseError("GitHub tag identity conflict")
            hashes = pypi_files(release)
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
