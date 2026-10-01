set positional-arguments := true

default:
    just --list

# Render deterministic context for one absolute Git worktree path
project-context *args:
    #!/usr/bin/env bash
    exec ./project-context "$@"

test:
    uv run --frozen pytest

# Root CI containers must drop privileges for permission-sensitive tests.
ci-check:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "$(id -u)" -ne 0 ]; then
        exec just check
    fi
    ci_user="${CONTEXT_LOADER_CI_USER:-ubuntu}"
    if ! id "$ci_user" >/dev/null 2>&1 || [ "$(id -u "$ci_user")" -eq 0 ]; then
        echo "Set CONTEXT_LOADER_CI_USER to an existing non-root CI account." >&2
        exit 1
    fi
    chown -R "$ci_user" .
    exec runuser -u "$ci_user" -- env "PATH=$PATH" just check

check:
    uv lock --check --no-config
    uv run --frozen ruff check context_loader scripts tests
    uv run --frozen ruff format --check context_loader scripts tests
    uv run --frozen pytest
