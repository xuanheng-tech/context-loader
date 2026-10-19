from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import benchmark

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("output_format", ["markdown", "json", "json-compact"])
def test_measure_keeps_real_repository_and_parent_read_only(
    tmp_path: Path, output_format: str
) -> None:
    foreign = tmp_path / "foreign"
    repo = foreign / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("# Read-only benchmark fixture\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    repo.chmod(0o555)
    foreign.chmod(0o555)
    try:
        result = benchmark.measure(
            ROOT / "project-context",
            repo,
            2,
            metrics=scratch / "time.txt",
            output_format=output_format,
            warmup=1,
        )
        assert len(result["samples_seconds"]) == len(result["samples_rss_kib"]) == 2
        assert result["output_bytes"] > 0
        assert len(result["output_sha256"]) == 64
        assert sorted(p.name for p in foreign.iterdir()) == ["repo"]
        assert sorted(p.name for p in repo.iterdir()) == [".git", "README.md"]
        assert (scratch / "time.txt").is_file()
    finally:
        foreign.chmod(0o755)
        repo.chmod(0o755)


def test_real_repository_cannot_contain_scratch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
    scratch = repo / "scratch"
    scratch.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/benchmark.py"),
            "--cli",
            str(ROOT / "project-context"),
            "--scratch",
            str(scratch),
            "--repo",
            str(repo),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--scratch must be outside --repo" in result.stderr
    assert list(scratch.iterdir()) == []
