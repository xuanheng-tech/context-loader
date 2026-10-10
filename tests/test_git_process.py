"""Verify deadlines using real, bounded child processes rather than mocked waits."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from context_loader import git
from context_loader.cli import main


@pytest.mark.parametrize("mode", ["silent", "streaming", "closed-stdout", "expired"])
def test_git_output_deadline_kills_and_reaps_the_process(mode: str) -> None:
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
                git._read_bounded_process_stdout(process, 1024, 0.0 if mode == "expired" else 0.15)
            assert process.returncode == -signal.SIGKILL
            assert process.poll() == -signal.SIGKILL
            with pytest.raises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert process.stdout.closed


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
