# Context Loader

Context Loader renders deterministic, bounded context for one local Git working tree.
The default CLI output remains Markdown; a stable JSON interface is also available for machine
callers. The tool reads repository state and a fixed set of root files without fetching, executing
repository code, network access, or writes to the target repository. Runtime code uses only the
Python standard library.

## Open-source quick start

Current stable release: **1.3.10**. Local CLI for coding-agent workflows; renders deterministic Markdown or JSON.

```bash
uv tool install 'context-loader==1.3.10'
```

```bash
pip install 'context-loader==1.3.10'
```

```bash
project-context --repo /path/to/repo
```

```bash
project-context --repo /path/to/repo --format json
```

## Install

Source version: `1.3.10`. Install its matching published release or an exact source commit.

Install via `uv`:

```bash
uv tool install 'context-loader==1.3.10'
```

Install via `pip`:

```bash
pip install 'context-loader==1.3.10'
```

For development or source-based installs tracking current repository (`1.3.10`):

```bash
uv tool install git+https://github.com/xuanheng-tech/context-loader.git
```

The repository also retains `./project-context` as a direct development entry point.

### Distributions

The sole distribution is **`context-loader`**, containing the `context_loader` runtime
and the `project-context` console script. Version `1.0.0` broke the CLI name and Markdown
heading contract while leaving the then-current JSON schema unchanged. Terminal and any executor call this same
entry point with explicit repository and focus arguments; no provider adapter or private
session state is involved.

Previously published packages and historical release records remain unchanged. The new
release publishes no compatibility distribution or legacy executable alias.

The wheel installs the runtime package only. The source distribution additionally carries
`tool_cli_contract.json`, so a package-only consumer can pin the declared public CLI contract
without cloning. The test suite, `justfile` and lockfile stay in the Git repository and are not
part of either distribution; run them from a checkout of the matching tag.

## Platform and Runtime Requirements

- **Python**: Python 3.12–3.14 (`>=3.12,<3.15`). Runtime code uses only the Python standard library with zero runtime dependencies.
- **Git**: Requires the standard `git` CLI at the fixed absolute path `/usr/bin/git`. The
  executable is never resolved through `PATH`, so an installation elsewhere is not used and
  every invocation fails with exit `1`. Git runs with a sanitized environment that ignores
  system, global and per-command configuration, attributes, and hooks.
- **Operating System**: supported and locally tested on Ubuntu 24.04 LTS; CI also tests the
  GitHub-hosted Ubuntu runner. Other Linux/POSIX systems are unverified, not a portability promise.
  Windows is not supported.

Deterministic means byte-identical output for the same tool version, CLI arguments, canonical
repository path, readable file contents, directory entries, Git state/local refs, and relevant Git
configuration, with no concurrent changes. Different checkout paths, permissions, Git versions or
working-tree contents can change the output. The tool does not freeze a repository snapshot.

Output can contain sensitive information from the repository itself: README/instructions, declared
commands, paths, branch names and commit subjects. Fixed file selection and size limits are **not
automatic redaction**. Inspect the output before sharing it with a person or external service.

## Usage

```bash
project-context --version
project-context --repo /home/user/projects/example
project-context --repo /home/user/projects/example --format markdown
project-context --repo /home/user/projects/example --format json
project-context --repo /home/user/projects/example --format json-compact
project-context --repo /home/user/projects/example \
  --focus "Authentication and session management" \
  --path auth/session.py
```

`--format` defaults to `markdown`. In Markdown mode, `--repo` retains the 0.1.1 contract: it must be
the absolute, canonical root of a non-bare Git working tree. In the `json` and `json-compact` modes,
an absolute existing directory inside the working tree is accepted; symlinks are normalized and the
discovered root is reported as `canonical_root`. Relative paths, non-Git directories, regular files,
and bare repositories are rejected in every mode.

`--focus` and `--path` are optional, bounded selection signals for the root `AGENTS.md`. `--path`
must be repository-relative. The collector does not retain either input in output or audit data.
Calls that omit both options remain valid and use the conservative fallback described in
[Supported Root Files](https://github.com/xuanheng-tech/context-loader/blob/HEAD/docs/reference.md#supported-root-files).
Each value option (`--repo`, `--focus`, `--path`, `--format`) may be supplied once;
duplicates are rejected before collection rather than silently overriding an earlier value.

## Markdown Output

Successful repository collection uses schema `context-loader/v0.1` and emits these sections in
order:

1. `Git State`, including bounded working-tree changes
2. `Development Instructions`
3. `Project Overview`
4. `Declared Commands`
5. `Project Entry Files`
6. `Recent Commits`
7. `Directory Tree`

The output has no generated timestamp, AI summary, architecture inference, or diff body.
The Markdown CLI skips the nested-instruction presence scan, which contributes only to
the machine formats; this does not change Markdown content or its byte budgets.

## JSON Output

`--format json` writes one sorted, separator-compact UTF-8 JSON document (`schema_version` 3);
`--format json-compact` writes the same document without `sources[*].content` (`schema_version`
4). Field definitions, compact-consumption guidance and schema-version rules are in
[docs/reference.md](https://github.com/xuanheng-tech/context-loader/blob/HEAD/docs/reference.md#json-output).

## Authority Boundary

Context Loader is a bounded transport for repository-root context, not the authority for a
repository's instruction hierarchy. It reads the fixed root candidates listed below, renders them
under explicit limits, and stops there.

Resolving an instruction hierarchy stays with the calling agent harness. That includes any
shared or user-level instruction file outside the repository, nested or scoped `AGENTS.md` files
under subdirectories, and any include or import directive written inside an instruction file:
such a directive is transported as literal text and is never followed. `--path` selects sections
of the repository-root `AGENTS.md` only; it never changes which files are read. The JSON-only
`nested_context` field reports the existence of nested `AGENTS.md` paths without reading them;
deciding whether any of them applies stays with the calling agent harness exactly as before.

A successful run therefore proves that the bounded root context was collected and rendered. It
does not prove that every instruction applicable to a task has been loaded.

## Inputs, limits and Git semantics

The exact root files read, every output and capture bound, and how Git state is derived are
documented in [docs/reference.md](https://github.com/xuanheng-tech/context-loader/blob/HEAD/docs/reference.md).

## Exit Codes

- `0`: context or version output completed successfully
- `1`: required Git collection or an internal operation failed
- `2`: command arguments or the repository path do not satisfy the contract

On nonzero exit, stdout is empty and a bounded diagnostic is written to stderr without a traceback
or candidate-file content.

## Not Included

Version 1.3.10 does not provide AI summaries, project-type detection, loading or transport of
nested `AGENTS.md` contents (the JSON `nested_context` field reports bounded existence only),
Memory retrieval, semantic ranking, ignore-rule parsing, plugins, profiles, caches, databases,
network services, MCP, daemons, GUIs, CI/CD, telemetry, or automatic updates.

## Development

Run the complete local check:

```bash
just check
```

The shared check also builds a wheel and installs it into a fresh virtual environment. Package
acceptance runs the installed CLI outside the source checkout, checking deterministic agreement
between all three formats, linked worktrees, long multi-module instructions with focus/path selection,
explicit truncation and nested-instruction presence, and safe failures. Build and installation run offline
using the backend cache populated by `uv run --frozen`; the tests use only disposable repositories.
Git deadline regressions verify that silent, streaming, and stdout-closed child processes are
terminated and reaped, that successful commands cannot reset the shared budget, and that process
creation consumes it. A timeout leaves CLI stdout empty in every output format.

The lockfile uses public PyPI artifact URLs; no organization mirror or local Renovate preset
is needed. Renovate keeps scheduled, rate-limited updates and manual merges using a public
recommended preset plus the repository's explicit settings. The GitHub quality gate runs on
Python 3.12.13, 3.13.13 and 3.14.5; the Gitea root-runner gate and release builds use 3.12.13.
Other interpreter/platform combinations require validation before widening the supported range.

Git test fixtures discard inherited `GIT_*` routing/configuration before creating repositories.
Permission-sensitive tests must run as a non-root user. Gitea uses `just ci-check`, which installs
Python 3.12.13 without user-level executable links and keeps its interpreter/cache under
`$RUNNER_TEMP/context-loader-ci` (or ignored `.ci-runtime/context-loader-ci` locally).
Non-root runners execute directly; root containers transfer ownership of that runtime and the
checkout, then use `runuser` with an existing account named by `CONTEXT_LOADER_CI_USER` (default
`ubuntu`). That account must be able to execute the installed `uv` and `just`; it does not need
access to `/root` or a root-owned Python/cache. No `sudo` dependency is required for this CI step.

Performance baselines, version maintenance and release closure/recovery procedures are in
[docs/reference.md](https://github.com/xuanheng-tech/context-loader/blob/HEAD/docs/reference.md).
