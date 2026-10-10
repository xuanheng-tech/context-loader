"""Measure the installed CLI on disposable synthetic repositories (Linux/GNU time)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import statistics
import subprocess
import time
from contextlib import suppress
from pathlib import Path

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


def command(arguments: list[str | Path], cwd: Path) -> bytes:
    # A timeout ends the task-owned group, including GNU time's measured child.
    with subprocess.Popen(
        [os.fspath(argument) for argument in arguments],
        cwd=cwd,
        env=ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[:1000])
        return stdout


def git(repo: Path, *arguments: str) -> None:
    command(
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
        repo.parent,
    )


def fixture(scratch: Path, name: str) -> Path:
    repo = scratch / name
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "README.md").write_text("# Synthetic benchmark\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(
        repo,
        "-c",
        "user.name=Benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    if name == "long-agents":
        (repo / "AGENTS.md").write_text(
            "# Instructions\nPreserve user data.\n"
            + "".join(f"## Topic {i:04d}\nSynthetic instruction {i}.\n" for i in range(1000))
            + "## Security target\nSECURITY_TARGET_BENCHMARK\n",
            encoding="utf-8",
        )
    else:
        directory = repo / "entries" if name == "large-directory" else repo
        directory.mkdir(exist_ok=True)
        for i in range(5000 if name == "large-directory" else 1000):
            (directory / f"item{i:05d}").touch()
    return repo


def measure(cli: Path, repo: Path, runs: int) -> dict[str, object]:
    elapsed = []
    rss = []
    expected = None
    metrics = repo.parent / "time.txt"
    for _ in range(runs):
        start = time.perf_counter()
        output = command(
            [
                "/usr/bin/time",
                "--format=%M",
                "--output",
                metrics,
                cli,
                "--repo",
                repo,
                "--format",
                "json-compact",
                "--focus",
                "security target",
            ],
            repo.parent,
        )
        elapsed.append(time.perf_counter() - start)
        rss.append(int(metrics.read_text(encoding="utf-8").strip()))
        document = json.loads(output)
        if document["context_sha256"] != hashlib.sha256(document["context"].encode()).hexdigest():
            raise RuntimeError("benchmark context digest does not match its content")
        if expected is not None and output != expected:
            raise RuntimeError("benchmark output changed between identical invocations")
        expected = output
    assert expected is not None
    return {
        "median_seconds": statistics.median(elapsed),
        "min_seconds": min(elapsed),
        "max_seconds": max(elapsed),
        "peak_rss_kib": max(rss),
        "output_bytes": len(expected),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument(
        "--scratch", type=Path, required=True, help="existing empty absolute directory"
    )
    parser.add_argument("--runs", type=int, choices=range(1, 21), default=5)
    arguments = parser.parse_args()
    if not arguments.cli.is_absolute() or not arguments.cli.is_file():
        parser.error("--cli must name an absolute executable file")
    if not os.access(arguments.cli, os.X_OK):
        parser.error("--cli must be executable")
    scratch = arguments.scratch
    if not scratch.is_absolute() or not scratch.is_dir():
        parser.error("--scratch must name an existing absolute directory")
    if scratch.stat().st_uid != os.getuid() or any(scratch.iterdir()):
        parser.error("--scratch must be user-owned and empty")
    if not os.access("/usr/bin/time", os.X_OK):
        parser.error("benchmarking requires GNU /usr/bin/time")
    version = command([arguments.cli, "--version"], scratch).decode("utf-8").strip()
    results = {
        name: measure(arguments.cli, fixture(scratch, name), arguments.runs)
        for name in ("long-agents", "large-directory", "large-status")
    }
    print(
        json.dumps(
            {"cli_version": version, "runs": arguments.runs, "results": results}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
