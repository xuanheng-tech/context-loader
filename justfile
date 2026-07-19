set positional-arguments := true

default:
    just --list

# Render deterministic context for one absolute Git worktree root
codex-project-context *args:
    #!/usr/bin/env bash
    exec ./codex-project-context "$@"

test:
    uv run --frozen pytest

check:
    uv run --frozen ruff check context_loader tests
    uv run --frozen ruff format --check context_loader tests
    uv run --frozen pytest
