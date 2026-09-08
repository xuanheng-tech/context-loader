from __future__ import annotations

import json
import tomllib
from pathlib import Path

from context_loader.cli import _parser

ROOT = Path(__file__).resolve().parents[1]


def test_public_cli_contract_is_consistent() -> None:
    contract_path = ROOT / "tool_cli_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert contract["schema_version"] == 1
    assert contract["contract_version"] == 1
    assert contract["tool_name"] == "context-loader"
    assert contract["tool_version"] == project["version"]
    assert {command["name"] for command in contract["commands"]} == {"codex-project-context"}
    declared_flags = set(contract["commands"][0]["flags"])
    assert declared_flags == {
        "--repo",
        "--focus",
        "--path",
        "--format",
        "--version",
        "--help",
    }
    parser = _parser()
    parser_flags = {
        action_str
        for action in parser._actions
        for action_str in action.option_strings
        if action_str.startswith("--")
    }
    assert declared_flags == parser_flags
    assert {command["operation_class"] for command in contract["commands"]} == {"read_only"}
    assert "/home/" not in contract_path.read_text(encoding="utf-8")


def test_source_distribution_ships_the_public_cli_contract() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        build_backend = tomllib.load(stream)["tool"]["uv"]["build-backend"]

    # The contract belongs with the source a consumer can pin against; the wheel
    # stays runtime-only, so it is deliberately not added to the installed package.
    assert build_backend["source-include"] == ["tool_cli_contract.json"]
    assert "wheel-exclude" not in build_backend
