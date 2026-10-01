"""Keep local Git fixtures independent of the invoking shell's repository/config."""

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    for name, value in {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }.items():
        monkeypatch.setenv(name, value)
