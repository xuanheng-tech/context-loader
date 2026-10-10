set positional-arguments := true

default:
    just --list

# Render deterministic context for one absolute Git worktree path
project-context *args:
    #!/usr/bin/env bash
    exec ./project-context "$@"

test:
    uv run --frozen pytest

# Prepare CI-owned Python/cache before running permission-sensitive tests as non-root.
ci-check:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "$(id -u)" -eq 0 ]; then
        ci_user="${CONTEXT_LOADER_CI_USER:-ubuntu}"
        if ! id "$ci_user" >/dev/null 2>&1 || [ "$(id -u "$ci_user")" -eq 0 ]; then
            echo "Set CONTEXT_LOADER_CI_USER to an existing non-root CI account." >&2
            exit 1
        fi
    fi
    ci_runtime="${RUNNER_TEMP:-$PWD/.ci-runtime}/context-loader-ci"
    export UV_PYTHON_INSTALL_DIR="$ci_runtime/python"
    export UV_CACHE_DIR="$ci_runtime/cache"
    export UV_PYTHON=3.12.13
    uv python install --no-bin "$UV_PYTHON"
    if [ "$(id -u)" -ne 0 ]; then
        exec just check
    fi
    chown -R "$ci_user" . "$ci_runtime"
    exec runuser -u "$ci_user" -- env "PATH=$PATH" just check

check:
    uv lock --check --no-config
    uv run --frozen ruff check context_loader scripts tests
    uv run --frozen ruff format --check context_loader scripts tests
    uv run --frozen pytest
