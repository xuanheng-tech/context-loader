"""Verify deadlines using real, bounded child processes rather than mocked waits."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_loader import git
from context_loader.cli import main


def _assert_reaped(process: subprocess.Popen[bytes]) -> None:
    assert process.poll() is not None
    assert process.stdout is not None and process.stdout.closed
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)


@pytest.mark.parametrize("mode", ["silent", "streaming", "closed-stdout", "expired"])
@pytest.mark.parametrize("streamed", [False, True])
def test_git_output_deadline_kills_and_reaps_the_process(mode: str, streamed: bool) -> None:
    code = "import os, time; os.write(1, b'ready\\n'); "
    if mode == "streaming":
        code += "\nfor _ in range(500):\n os.write(1, b'.'); time.sleep(0.01)"
    elif mode == "closed-stdout":
        code += "os.close(1); time.sleep(5)"
    else:
        code += "time.sleep(5)"
    with subprocess.Popen(
        [sys.executable, "-I", "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        assert process.stdout is not None
        assert process.stdout.readline() == b"ready\n"
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                git._read_bounded_process_stdout(
                    process,
                    1024,
                    0.0 if mode == "expired" else 0.15,
                    consume=(lambda chunk: None) if streamed else None,
                )
            assert process.returncode == -signal.SIGKILL
            assert process.poll() == -signal.SIGKILL
            with pytest.raises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert process.stdout.closed


@pytest.mark.parametrize("failure", [None, "output_limit", "consumer"])
def test_streamed_output_keeps_bounds_and_reaps_on_consumer_failure(failure: str | None) -> None:
    received = 0

    def consume(chunk: bytes) -> None:
        nonlocal received
        received += len(chunk)
        if failure == "consumer":
            raise git.ContextLoaderError("fixture consumer failure")

    code = "import os, time; os.write(1, b'x' * 131072); "
    code += "time.sleep(5)" if failure else ""
    with subprocess.Popen(
        [sys.executable, "-I", "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        if failure:
            with pytest.raises(git.ContextLoaderError):
                git._read_bounded_process_stdout(
                    process, 1024 if failure == "output_limit" else 262144, 2, consume=consume
                )
            assert process.returncode == -signal.SIGKILL
        else:
            assert git._read_bounded_process_stdout(process, 262144, 2, consume=consume) == b""
            assert received == 131072
            assert process.returncode == 0
    _assert_reaped(process)


@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
def test_status_stream_keeps_only_the_sorted_prefix_across_record_boundaries(
    monkeypatch: pytest.MonkeyPatch, chunk_size: int
) -> None:
    from context_loader.model import MAX_CHANGE_ENTRIES

    paths = [f"item-{index:05}".encode() for index in range(205, -1, -1)]
    paths.extend((b"undecodable-\xff", b"line\nname", b"`", b"\\x60"))
    payload = b"".join(b"?? " + path + b"\0" for path in paths)

    def run_git(repo, arguments, *, deadline, consume):
        for start in range(0, len(payload), chunk_size):
            consume(payload[start : start + chunk_size])
        return git.GitResult(b"", 0)

    monkeypatch.setattr(git, "_ensure_safe_status", lambda *args: None)
    monkeypatch.setattr(git, "_run_git", run_git)
    changes, truncated = git._working_tree_changes(Path("/fixture"), float("inf"))

    assert [change.sort_key for change in changes] == sorted(paths)[:MAX_CHANGE_ENTRIES]
    assert all(change.status == "??" for change in changes)
    assert truncated is True


@pytest.mark.parametrize("tail", [b"missing terminator", b"bad\0", b"\xff? invalid-status\0"])
def test_status_stream_validates_records_beyond_its_retained_prefix(
    monkeypatch: pytest.MonkeyPatch, tail: bytes
) -> None:
    def run_git(repo, arguments, *, deadline, consume):
        consume(b"".join(f"?? item-{index:04}\0".encode() for index in range(200)))
        consume(tail)
        return git.GitResult(b"", 0)

    monkeypatch.setattr(git, "_ensure_safe_status", lambda *args: None)
    monkeypatch.setattr(git, "_run_git", run_git)
    with pytest.raises(git.ContextLoaderError, match="unable to parse repository Git state"):
        git._working_tree_changes(Path("/fixture"), float("inf"))


def test_git_timeout_leaves_cli_stdout_empty_and_reaps_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    processes = []
    popen = subprocess.Popen

    def record_process(*arguments, **kwargs):
        process = popen(*arguments, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(git, "GIT_EXECUTABLE", sys.executable)
    monkeypatch.setattr(
        git,
        "_GIT_PREFIX",
        ("-I", "-c", "import os, time; os.write(1, b'fixture output'); time.sleep(5)"),
    )
    monkeypatch.setattr(git, "GIT_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(git.subprocess, "Popen", record_process)

    assert main(["--repo", os.fspath(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: unable to read repository Git state\n"
    assert len(processes) == 1
    process = processes[0]
    assert process.returncode == -signal.SIGKILL
    assert process.stdout is not None and process.stdout.closed
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)


@pytest.mark.parametrize("output_format", ["markdown", "json", "json-compact"])
def test_cli_stops_when_successful_git_commands_exhaust_the_shared_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    output_format: str,
) -> None:
    subprocess.run(["/usr/bin/git", "init", "--quiet", os.fspath(tmp_path)], check=True, timeout=5)
    now = 0.0
    processes = []
    popen = subprocess.Popen
    read_stdout = git._read_bounded_process_stdout

    def record_process(*arguments, **kwargs):
        process = popen(*arguments, **kwargs)
        processes.append(process)
        return process

    def read_with_elapsed_cost(*arguments, **kwargs):
        nonlocal now
        output = read_stdout(*arguments, **kwargs)
        # Each successful command costs less than one second; together they exceed it.
        now += 0.6
        return output

    monkeypatch.setattr(git, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(git, "GIT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(git.subprocess, "Popen", record_process)
    monkeypatch.setattr(git, "_read_bounded_process_stdout", read_with_elapsed_cost)

    assert main(["--repo", os.fspath(tmp_path), "--format", output_format]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: unable to read repository Git state\n"
    assert len(processes) == 2  # No branch/status command starts after discovery exhausts it.
    for process in processes:
        assert process.returncode == 0
        _assert_reaped(process)


def test_expired_git_budget_does_not_start_a_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_process(*arguments, **kwargs):
        pytest.fail("an exhausted budget must not start a Git process")

    monkeypatch.setattr(git.subprocess, "Popen", refuse_process)
    with pytest.raises(git.ContextLoaderError, match="unable to read repository Git state"):
        git._run_git(tmp_path, ("status",), deadline=git.time.monotonic() - 1)


def test_git_shared_deadline_counts_process_creation_and_reaps_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 0.0
    processes = []
    popen = subprocess.Popen

    def start_after_budget(*arguments, **kwargs):
        nonlocal now
        process = popen(*arguments, **kwargs)
        processes.append(process)
        assert process.stdout is not None
        assert process.stdout.readline() == b"ready\n"
        now = 2.0
        return process

    monkeypatch.setattr(git, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(git, "GIT_EXECUTABLE", sys.executable)
    monkeypatch.setattr(
        git,
        "_GIT_PREFIX",
        ("-I", "-c", "import os, time; os.write(1, b'ready\\n'); time.sleep(5)"),
    )
    monkeypatch.setattr(git.subprocess, "Popen", start_after_budget)

    with pytest.raises(git.ContextLoaderError, match="unable to read repository Git state"):
        git._run_git(tmp_path, ("status",), deadline=1.0)
    assert len(processes) == 1
    assert processes[0].returncode == -signal.SIGKILL
    _assert_reaped(processes[0])
