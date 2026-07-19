# Codex Project Context Loader

Codex Project Context Loader renders deterministic, bounded Markdown context for one local Git
working tree. It reads repository state and a fixed set of root files without fetching, executing
repository code, or writing to the target repository. Runtime code uses only the Python standard
library.

## Install

Install a release wheel with `uv`:

```bash
uv tool install /path/to/codex_project_context_loader-0.1.0-py3-none-any.whl
```

The repository also retains `./codex-project-context` as a direct development entry point.

## Usage

```bash
codex-project-context --version
codex-project-context --repo /home/user/projects/example
```

`--repo` must be the absolute, canonical root of a non-bare Git working tree. Relative paths, Git
subdirectories, non-Git directories, and bare repositories are rejected.

## Output

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

## Supported Root Files

Only these exact files directly under the Git root are eligible:

- Development instructions: `AGENTS.md`
- Project overview: `README.md`
- Entry files, in fixed order: `pyproject.toml`, `justfile` or `Justfile`, `package.json`,
  `Makefile`, `Cargo.toml`, and `go.mod`

Lowercase `justfile` takes precedence over `Justfile`. Nested files, lockfiles, CI configuration,
`.env`, and glob-discovered files are not read.

## Limits

- Final stdout: 98,304 bytes
- `AGENTS.md`: 16 KiB
- `README.md`: 16 KiB
- Each entry file: 8 KiB
- All entry-file bodies: 24 KiB
- Declared commands: 8 KiB
- Directory tree: 12 KiB, 300 entries, and depth 2
- Working-tree changes: 100 paths and 4 KiB
- Recent commits: 8

Truncation occurs only at complete UTF-8 and line boundaries and is marked explicitly.

## Git State Semantics

Git state is derived only from the working tree and local refs. The command does not fetch or query
the remote.

- An unborn working tree reports its symbolic branch name, such as `Branch: main`, and
  `HEAD: unborn`.
- A detached HEAD reports `Branch: detached` and the resolved commit object ID.
- Without an upstream, `Upstream: not configured` and `Ahead / behind: not available` are used.
- Ahead/behind counts are computed only when the configured upstream ref is available locally.

## Exit Codes

- `0`: context or version output completed successfully
- `1`: required Git collection or an internal operation failed
- `2`: command arguments or the repository path do not satisfy the contract

On nonzero exit, stdout is empty and a bounded diagnostic is written to stderr without a traceback
or candidate-file content.

## Not Included

Version 0.1 does not provide AI summaries, project-type detection, nested `AGENTS.md` handling,
ignore-rule parsing, plugins, profiles, caches, databases, network services, MCP, APIs, daemons,
GUIs, CI/CD, telemetry, or automatic updates.

## Development

Run the complete local check:

```bash
just check
```
