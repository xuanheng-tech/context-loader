"""Read the bounded local Git state needed by the context loader."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

GIT_EXECUTABLE = "/usr/bin/git"
GIT_TIMEOUT_SECONDS = 30
MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_RECENT_COMMITS = 8

_OID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_GIT_PREFIX = (
    "--no-pager",
    "--no-optional-locks",
    "--literal-pathspecs",
    "-c",
    "color.ui=false",
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "diff.external=",
    "-c",
    "diff.trustExitCode=false",
    "-c",
    "i18n.logOutputEncoding=UTF-8",
    "-c",
    "log.showSignature=false",
)
_GIT_ENVIRONMENT = MappingProxyType(
    {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PAGER": "cat",
        "TERM": "dumb",
        "XDG_CONFIG_HOME": "/nonexistent",
    }
)


class ContextLoaderError(Exception):
    """A safe, user-facing context-loader failure."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class GitResult:
    stdout: bytes
    returncode: int


@dataclass(frozen=True, slots=True)
class WorkingTreeChange:
    status: str
    path: str
    sort_key: bytes


@dataclass(frozen=True, slots=True)
class RecentCommit:
    object_id: str
    author_date: str
    subject: str


@dataclass(frozen=True, slots=True)
class RepositoryState:
    repository: Path
    branch: str
    head: str
    upstream: str
    ahead_behind: str
    changes: tuple[WorkingTreeChange, ...]
    commits: tuple[RecentCommit, ...]

    @property
    def worktree(self) -> str:
        return "dirty" if self.changes else "clean"


def _run_git(repo: Path, arguments: tuple[str, ...], *, check: bool = True) -> GitResult:
    argv = [GIT_EXECUTABLE, *_GIT_PREFIX, "-C", os.fspath(repo), *arguments]
    try:
        completed = subprocess.run(  # noqa: S603 - executable and every command are fixed here.
            argv,
            cwd="/",
            env=dict(_GIT_ENVIRONMENT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContextLoaderError("unable to read repository Git state") from exc
    if len(completed.stdout) > MAX_GIT_OUTPUT_BYTES:
        raise ContextLoaderError("repository Git output exceeded the safety limit")
    if check and completed.returncode != 0:
        raise ContextLoaderError("unable to read repository Git state")
    return GitResult(completed.stdout, completed.returncode)


def _without_one_line_ending(raw: bytes) -> bytes:
    if raw.endswith(b"\n"):
        return raw[:-1]
    return raw


def _decode_path(raw: bytes) -> str:
    return os.fsdecode(raw)


def _decode_text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="backslashreplace")


def validate_repository(raw_path: str) -> Path:
    """Return the canonical root of an explicit non-bare Git worktree."""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise ContextLoaderError("--repo must be an absolute path", exit_code=2)
    try:
        canonical = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ContextLoaderError("--repo must name an existing directory", exit_code=2) from None
    if not canonical.is_dir():
        raise ContextLoaderError("--repo must name an existing directory", exit_code=2)

    bare = _run_git(canonical, ("rev-parse", "--is-bare-repository"), check=False)
    root = _run_git(
        canonical,
        ("rev-parse", "--path-format=absolute", "--show-toplevel"),
        check=False,
    )
    if bare.returncode != 0 or root.returncode != 0:
        raise ContextLoaderError(
            "--repo must be the canonical root of a non-bare Git worktree", exit_code=2
        )
    if _without_one_line_ending(bare.stdout) != b"false":
        raise ContextLoaderError(
            "--repo must be the canonical root of a non-bare Git worktree", exit_code=2
        )
    try:
        discovered = Path(_decode_path(_without_one_line_ending(root.stdout))).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ContextLoaderError(
            "--repo must be the canonical root of a non-bare Git worktree", exit_code=2
        ) from None
    if discovered != canonical:
        raise ContextLoaderError(
            "--repo must be the canonical root of a non-bare Git worktree", exit_code=2
        )
    return canonical


def _head_and_branch(repo: Path) -> tuple[str, str, str | None]:
    symbolic = _run_git(repo, ("symbolic-ref", "--quiet", "--short", "HEAD"), check=False)
    branch_name = (
        _decode_path(_without_one_line_ending(symbolic.stdout))
        if symbolic.returncode == 0
        else None
    )
    resolved_head = _run_git(repo, ("rev-parse", "--verify", "HEAD^{commit}"), check=False)
    if resolved_head.returncode != 0:
        if branch_name is None:
            raise ContextLoaderError("unable to read repository Git state")
        return branch_name, "unborn", branch_name

    head = _decode_text(_without_one_line_ending(resolved_head.stdout))
    if _OID_PATTERN.fullmatch(head) is None:
        raise ContextLoaderError("unable to read repository Git state")
    return branch_name or "detached", head, branch_name


def _config_value(repo: Path, key: str) -> str | None:
    result = _run_git(repo, ("config", "--get", key), check=False)
    if result.returncode != 0:
        return None
    value = _decode_text(_without_one_line_ending(result.stdout))
    return value or None


def _fallback_upstream(repo: Path, branch_name: str) -> str | None:
    remote = _config_value(repo, f"branch.{branch_name}.remote")
    merge = _config_value(repo, f"branch.{branch_name}.merge")
    if remote is None or merge is None:
        return None
    merge_name = merge.removeprefix("refs/heads/")
    if remote == ".":
        return merge_name
    return f"{remote}/{merge_name}"


def _upstream_state(repo: Path, branch_name: str | None) -> tuple[str, str]:
    if branch_name is None:
        return "not configured", "not available"

    resolved = _run_git(
        repo,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
        check=False,
    )
    if resolved.returncode != 0:
        configured = _fallback_upstream(repo, branch_name)
        return configured or "not configured", "not available"

    upstream = _decode_text(_without_one_line_ending(resolved.stdout))
    if not upstream:
        raise ContextLoaderError("unable to read repository Git state")
    divergence = _run_git(
        repo,
        ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"),
        check=False,
    )
    if divergence.returncode != 0:
        return upstream, "not available"
    counts = _without_one_line_ending(divergence.stdout).split()
    if len(counts) != 2 or any(not count.isdigit() for count in counts):
        return upstream, "not available"
    return upstream, f"{counts[0].decode('ascii')} / {counts[1].decode('ascii')}"


def _working_tree_changes(repo: Path) -> tuple[WorkingTreeChange, ...]:
    result = _run_git(
        repo,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--ignore-submodules=none",
        ),
    )
    if not result.stdout:
        return ()
    if not result.stdout.endswith(b"\0"):
        raise ContextLoaderError("unable to parse repository Git state")

    changes: list[WorkingTreeChange] = []
    for record in result.stdout[:-1].split(b"\0"):
        if len(record) < 4 or record[2:3] != b" ":
            raise ContextLoaderError("unable to parse repository Git state")
        try:
            status = record[:2].decode("ascii")
        except UnicodeDecodeError:
            raise ContextLoaderError("unable to parse repository Git state") from None
        path = record[3:]
        if not path:
            raise ContextLoaderError("unable to parse repository Git state")
        changes.append(WorkingTreeChange(status, _decode_path(path), path))
    return tuple(sorted(changes, key=lambda change: change.sort_key))


def _recent_commits(repo: Path, head: str) -> tuple[RecentCommit, ...]:
    if head == "unborn":
        return ()
    result = _run_git(
        repo,
        (
            "log",
            f"--max-count={MAX_RECENT_COMMITS}",
            "--no-decorate",
            "--format=%H%x09%aI%x09%s",
            "HEAD",
            "--",
        ),
    )
    commits: list[RecentCommit] = []
    for line in result.stdout.splitlines():
        fields = line.split(b"\t", 2)
        if len(fields) != 3:
            raise ContextLoaderError("unable to parse repository Git state")
        object_id = _decode_text(fields[0])
        if _OID_PATTERN.fullmatch(object_id) is None:
            raise ContextLoaderError("unable to parse repository Git state")
        commits.append(
            RecentCommit(
                object_id=object_id,
                author_date=_decode_text(fields[1]),
                subject=_decode_text(fields[2]),
            )
        )
    return tuple(commits)


def collect_repository_state(raw_path: str) -> RepositoryState:
    """Validate one repository and collect only the Phase 01 Git fields."""
    repository = validate_repository(raw_path)
    branch, head, branch_name = _head_and_branch(repository)
    upstream, ahead_behind = _upstream_state(repository, branch_name)
    changes = _working_tree_changes(repository)
    commits = _recent_commits(repository, head)
    return RepositoryState(
        repository=repository,
        branch=branch,
        head=head,
        upstream=upstream,
        ahead_behind=ahead_behind,
        changes=changes,
        commits=commits,
    )
