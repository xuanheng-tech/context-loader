from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPAT = ROOT / "compat" / "codex-project-context-loader"


def test_compat_distribution_is_a_pure_shim() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        root = tomllib.load(stream)
    with (COMPAT / "pyproject.toml").open("rb") as stream:
        shim = tomllib.load(stream)

    project = shim["project"]
    version = root["project"]["version"]

    assert shim["build-system"] == root["build-system"]
    assert project["name"] == "codex-project-context-loader"
    assert project["version"] == version
    assert project["requires-python"] == root["project"]["requires-python"]
    assert project["dependencies"] == [f"context-loader=={version}"]
    assert "scripts" not in project
    assert not any(path.is_dir() for path in COMPAT.rglob("context_loader"))
