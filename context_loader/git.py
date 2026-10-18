"""Read the bounded local Git state needed by the context loader."""

from __future__ import annotations

import os
import re
import selectors
import subprocess
import time
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .model import MAX_CHANGE_ENTRIES

GIT_EXECUTABLE = "/usr/bin/git"
GIT_TIMEOUT_SECONDS = 30
MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_RECENT_COMMITS = 8
MAX_STATUS_REPOSITORIES = 128

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
    "-c",
    "protocol.allow=never",
)
_GIT_ENVIRONMENT = MappingProxyType(
    {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_ALLOW_PROTOCOL": "",
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
    changes_truncated: bool = False

    @property
    def worktree(self) -> str:
        return "dirty" if self.changes or self.changes_truncated else "clean"


@dataclass(frozen=True, slots=True)
class RepositoryLocation:
    requested_path: Path
    canonical_root: Path


def _read_bounded_process_stdout(
    proc: subprocess.Popen[bytes],
    max_bytes: int,
    timeout_seconds: float,
    *,
    deadline: float | None = None,
    consume: Callable[[bytes], None] | None = None,
) -> bytes:
    if deadline is None:
        deadline = time.monotonic() + timeout_seconds
    buffer = bytearray()
    total_bytes = 0
    assert proc.stdout is not None
    os.set_blocking(proc.stdout.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(proc.args, timeout_seconds)
            events = selector.select(timeout=max(0.0, remaining))
            if not events:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(proc.args, timeout_seconds)
            try:
                chunk = os.read(proc.stdout.fileno(), 64 * 1024)
            except BlockingIOError:
                continue
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                proc.kill()
                proc.wait()
                raise ContextLoaderError("repository Git output exceeded the safety limit")
            if consume is None:
                buffer.extend(chunk)
            else:
                consume(chunk)
    except Exception:
        try:
            proc.kill()
            proc.wait()
        except OSError:
            pass
        raise
    finally:
        selector.close()

    remaining = max(0.0, deadline - time.monotonic())
    try:
        proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    return bytes(buffer)


def _check_git_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ContextLoaderError("unable to read repository Git state")


def _run_git(
    repo: Path,
    arguments: tuple[str, ...],
    *,
    check: bool = True,
    deadline: float | None = None,
    consume: Callable[[bytes], None] | None = None,
) -> GitResult:
    command_deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    deadline = command_deadline if deadline is None else min(deadline, command_deadline)
    argv = [GIT_EXECUTABLE, *_GIT_PREFIX, "-C", os.fspath(repo), *arguments]
    try:
        _check_git_deadline(deadline)
        with subprocess.Popen(  # noqa: S603 - executable and every command are fixed here.
            argv,
            cwd="/",
            env=dict(_GIT_ENVIRONMENT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as proc:
            stdout = _read_bounded_process_stdout(
                proc,
                MAX_GIT_OUTPUT_BYTES,
                GIT_TIMEOUT_SECONDS,
                deadline=deadline,
                consume=consume,
            )
            returncode = proc.returncode
        _check_git_deadline(deadline)
    except ContextLoaderError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContextLoaderError("unable to read repository Git state") from exc

    if check and returncode != 0:
        raise ContextLoaderError("unable to read repository Git state")
    return GitResult(stdout, returncode)


def _without_one_line_ending(raw: bytes) -> bytes:
    if raw.endswith(b"\n"):
        return raw[:-1]
    return raw


def _decode_path(raw: bytes) -> str:
    return os.fsdecode(raw)


def _decode_text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="backslashreplace")


def discover_repository(
    raw_path: str | os.PathLike[str], *, require_canonical_root: bool = False
) -> RepositoryLocation:
    """Resolve an absolute directory and discover its non-bare Git worktree root."""
    return _discover_repository(
        raw_path,
        require_canonical_root=require_canonical_root,
        deadline=time.monotonic() + GIT_TIMEOUT_SECONDS,
    )


def _discover_repository(
    raw_path: str | os.PathLike[str], *, require_canonical_root: bool, deadline: float
) -> RepositoryLocation:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise ContextLoaderError("--repo must be an absolute path", exit_code=2)
    try:
        canonical = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ContextLoaderError("--repo must name an existing directory", exit_code=2) from None
    if not canonical.is_dir():
        raise ContextLoaderError("--repo must name an existing directory", exit_code=2)

    bare = _run_git(
        canonical, ("rev-parse", "--is-bare-repository"), check=False, deadline=deadline
    )
    root = _run_git(
        canonical,
        ("rev-parse", "--path-format=absolute", "--show-toplevel"),
        check=False,
        deadline=deadline,
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
    if require_canonical_root and discovered != canonical:
        raise ContextLoaderError(
            "--repo must be the canonical root of a non-bare Git worktree", exit_code=2
        )
    _check_git_deadline(deadline)
    return RepositoryLocation(requested_path=canonical, canonical_root=discovered)


def validate_repository(raw_path: str | os.PathLike[str]) -> Path:
    """Return the canonical root of an explicitly supplied Git worktree root."""
    return discover_repository(raw_path, require_canonical_root=True).canonical_root


def _head_and_branch(repo: Path, deadline: float) -> tuple[str, str, str | None]:
    symbolic = _run_git(
        repo, ("symbolic-ref", "--quiet", "--short", "HEAD"), check=False, deadline=deadline
    )
    branch_name = (
        _decode_path(_without_one_line_ending(symbolic.stdout))
        if symbolic.returncode == 0
        else None
    )
    resolved_head = _run_git(
        repo, ("rev-parse", "--verify", "HEAD^{commit}"), check=False, deadline=deadline
    )
    if resolved_head.returncode != 0:
        if branch_name is None:
            raise ContextLoaderError("unable to read repository Git state")
        return branch_name, "unborn", branch_name

    head = _decode_text(_without_one_line_ending(resolved_head.stdout))
    if _OID_PATTERN.fullmatch(head) is None:
        raise ContextLoaderError("unable to read repository Git state")
    return branch_name or "detached", head, branch_name


def _config_value(repo: Path, key: str, deadline: float) -> str | None:
    result = _run_git(repo, ("config", "--get", key), check=False, deadline=deadline)
    if result.returncode != 0:
        return None
    value = _decode_text(_without_one_line_ending(result.stdout))
    return value or None


def _fallback_upstream(repo: Path, branch_name: str, deadline: float) -> str | None:
    remote = _config_value(repo, f"branch.{branch_name}.remote", deadline)
    merge = _config_value(repo, f"branch.{branch_name}.merge", deadline)
    if remote is None or merge is None:
        return None
    merge_name = merge.removeprefix("refs/heads/")
    if remote == ".":
        return merge_name
    return f"{remote}/{merge_name}"


def _upstream_state(repo: Path, branch_name: str | None, deadline: float) -> tuple[str, str]:
    if branch_name is None:
        return "not configured", "not available"

    resolved = _run_git(
        repo,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
        check=False,
        deadline=deadline,
    )
    if resolved.returncode != 0:
        configured = _fallback_upstream(repo, branch_name, deadline)
        return configured or "not configured", "not available"

    upstream = _decode_text(_without_one_line_ending(resolved.stdout))
    if not upstream:
        raise ContextLoaderError("unable to read repository Git state")
    divergence = _run_git(
        repo,
        ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"),
        check=False,
        deadline=deadline,
    )
    if divergence.returncode != 0:
        return upstream, "not available"
    counts = _without_one_line_ending(divergence.stdout).split()
    if len(counts) != 2 or any(not count.isdigit() for count in counts):
        return upstream, "not available"
    return upstream, f"{counts[0].decode('ascii')} / {counts[1].decode('ascii')}"


def _ensure_safe_status(repo: Path, deadline: float) -> None:
    """Reject executable conversions before status can refresh any index entry.

    Attributes can select clean/process drivers even during a read-only status.
    Ignoring those drivers would change Git's cleanliness semantics, so fail closed
    instead. Status also inspects initialized submodules; check their own config
    without invoking `submodule foreach` or any repository-supplied command.
    """
    pending = [repo]
    seen: set[Path] = set()
    while pending:
        current = pending.pop()
        identity = current.resolve()
        if identity in seen:
            raise ContextLoaderError("unable to safely inspect cyclic submodule state")
        seen.add(identity)
        if len(seen) > MAX_STATUS_REPOSITORIES:
            raise ContextLoaderError("repository submodule inspection exceeded the safety limit")
        filters = _run_git(
            current,
            ("config", "--null", "--get-regexp", r"^filter\..*\.(clean|process)$"),
            check=False,
            deadline=deadline,
        )
        if filters.returncode not in (0, 1):
            raise ContextLoaderError("unable to safely inspect repository Git filters")
        filter_commands: dict[bytes, bytes] = {}
        for record in filters.stdout.split(b"\0"):
            if not record:
                continue
            key, separator, value = record.partition(b"\n")
            if not key or not separator:
                raise ContextLoaderError("unable to safely inspect repository Git filters")
            filter_commands[key] = value
        if any(filter_commands.values()):
            raise ContextLoaderError(
                "repository or submodule config enables Git clean/process filters; "
                "unable to safely inspect working-tree state"
            )
        index = _run_git(current, ("ls-files", "--stage", "-z"), deadline=deadline)
        children: set[Path] = set()
        for record in index.stdout.split(b"\0"):
            metadata, separator, raw_path = record.partition(b"\t")
            if metadata.split(b" ", 1)[0] != b"160000":
                continue
            if not separator or not raw_path:
                raise ContextLoaderError("unable to parse repository submodule state")
            path = Path(_decode_path(raw_path))
            if path.is_absolute() or ".." in path.parts:
                raise ContextLoaderError("unable to safely inspect repository submodule state")
            child = current / path
            if (child / ".git").exists():
                children.add(child)
        pending.extend(sorted(children))
        if len(seen) + len(pending) > MAX_STATUS_REPOSITORIES:
            raise ContextLoaderError("repository submodule inspection exceeded the safety limit")


def _working_tree_changes(
    repo: Path, deadline: float
) -> tuple[tuple[WorkingTreeChange, ...], bool]:
    """Consume all bounded status evidence, retaining only its sorted display prefix."""
    _ensure_safe_status(repo, deadline)
    changes: list[WorkingTreeChange] = []
    sort_keys: list[bytes] = []
    pending = bytearray()
    count = 0

    def consume(chunk: bytes) -> None:
        nonlocal count
        pending.extend(chunk)
        start = 0
        while (end := pending.find(b"\0", start)) >= 0:
            record = bytes(pending[start:end])
            start = end + 1
            if len(record) < 4 or record[2:3] != b" ":
                raise ContextLoaderError("unable to parse repository Git state")
            try:
                status = record[:2].decode("ascii")
            except UnicodeDecodeError:
                raise ContextLoaderError("unable to parse repository Git state") from None
            path = record[3:]
            if not path:
                raise ContextLoaderError("unable to parse repository Git state")
            count += 1
            position = bisect_right(sort_keys, path)
            if position < MAX_CHANGE_ENTRIES:
                sort_keys.insert(position, path)
                changes.insert(position, WorkingTreeChange(status, _decode_path(path), path))
                if len(changes) > MAX_CHANGE_ENTRIES:
                    sort_keys.pop()
                    changes.pop()
        del pending[:start]

    _run_git(
        repo,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--ignore-submodules=none",
        ),
        deadline=deadline,
        consume=consume,
    )
    if pending:
        raise ContextLoaderError("unable to parse repository Git state")
    return tuple(changes), count > len(changes)


def _recent_commits(repo: Path, head: str, deadline: float) -> tuple[RecentCommit, ...]:
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
        deadline=deadline,
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


def collect_repository(
    raw_path: str | os.PathLike[str], *, require_canonical_root: bool = True
) -> tuple[RepositoryLocation, RepositoryState]:
    """Discover and collect local Git state within one shared elapsed-time budget."""
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    location = _discover_repository(
        raw_path, require_canonical_root=require_canonical_root, deadline=deadline
    )
    repository = location.canonical_root
    branch, head, branch_name = _head_and_branch(repository, deadline)
    upstream, ahead_behind = _upstream_state(repository, branch_name, deadline)
    changes, changes_truncated = _working_tree_changes(repository, deadline)
    commits = _recent_commits(repository, head, deadline)
    state = RepositoryState(
        repository=repository,
        branch=branch,
        head=head,
        upstream=upstream,
        ahead_behind=ahead_behind,
        changes=changes,
        commits=commits,
        changes_truncated=changes_truncated,
    )
    _check_git_deadline(deadline)
    return location, state


def collect_repository_state(raw_path: str | os.PathLike[str]) -> RepositoryState:
    """Validate one repository root and collect only the Phase 01 Git fields."""
    _location, state = collect_repository(raw_path)
    return state
