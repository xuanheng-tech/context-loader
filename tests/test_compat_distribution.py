from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_has_one_distribution_and_one_neutral_entrypoint() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        root = tomllib.load(stream)
    assert root["project"]["name"] == "context-loader"
    assert root["project"]["version"] == "1.3.0"
    assert root["project"]["scripts"] == {"project-context": "context_loader.cli:main"}
    assert list((ROOT / "compat").glob("*/pyproject.toml")) == []
