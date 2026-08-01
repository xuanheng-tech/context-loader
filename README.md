# Codex Project Context Loader

Codex Project Context Loader renders deterministic, bounded context for one local Git working tree.
The default CLI output remains Markdown; a stable JSON interface is also available for machine
callers. The tool reads repository state and a fixed set of root files without fetching, executing
repository code, network access, or writes to the target repository. Runtime code uses only the
Python standard library.

## Install

Current release: `0.1.2`.

Install a release wheel with `uv`:

```bash
uv tool install /path/to/codex_project_context_loader-0.1.2-py3-none-any.whl
```

The repository also retains `./codex-project-context` as a direct development entry point.

## Usage

```bash
codex-project-context --version
codex-project-context --repo /home/user/projects/example
codex-project-context --repo /home/user/projects/example --format markdown
codex-project-context --repo /home/user/projects/example --format json
```

`--format` defaults to `markdown`. In Markdown mode, `--repo` retains the 0.1.1 contract: it must be
the absolute, canonical root of a non-bare Git working tree. In JSON mode, an absolute existing
directory inside the working tree is accepted; symlinks are normalized and the discovered root is
reported as `canonical_root`. Relative paths, non-Git directories, regular files, and bare
repositories are rejected in both modes.

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

## JSON Output

`--format json` writes exactly one compact UTF-8 JSON document plus one trailing newline to stdout.
Keys are serialized in sorted order with `ensure_ascii=False`. The declared contract is:

```json
{
  "schema_version": 1,
  "tool": {
    "name": "context-loader",
    "version": "0.1.2"
  },
  "repository": {
    "requested_path": "/canonical/requested/path",
    "canonical_root": "/canonical/worktree/root"
  },
  "sources": [
    {
      "ordinal": 0,
      "kind": "agents",
      "scope": "repository",
      "path": "/canonical/worktree/root/AGENTS.md",
      "content_sha256": "sha256-hex",
      "content": "actual rendered source text"
    }
  ],
  "context": "the same assembled Markdown context",
  "context_sha256": "sha256-hex",
  "warnings": []
}
```

`context_sha256` hashes the UTF-8 bytes of `context`; each `content_sha256` does the same for that
source's `content`. `sources` contains only file bodies that actually enter the final context, in
assembly order, after the existing newline normalization and truncation rules. `scope` distinguishes
`repository` from `global`; version 0.1.2's fixed root-file selection currently emits only
`repository` sources and does not add any global-file discovery.

The JSON schema version and package version are independent: `schema_version` is currently the
integer `1`, while `tool.version` is `0.1.2`. Callers must depend only on fields declared above. The
document contains no generated time or random identifier, so unchanged input produces identical
JSON bytes. On failure, stdout remains empty and stderr contains only a short diagnostic.

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
- `Upstream` is the current branch's configured tracking target. Without one,
  `Upstream: not configured` is used.
- A configured target such as `origin/main` may be shown before its commit is resolvable locally;
  this is normal after cloning an empty remote.
- `Ahead / behind: not available` means the commit relationship cannot currently be computed.
  Counts are reported only when the configured upstream commit is available locally.

## Exit Codes

- `0`: context or version output completed successfully
- `1`: required Git collection or an internal operation failed
- `2`: command arguments or the repository path do not satisfy the contract

On nonzero exit, stdout is empty and a bounded diagnostic is written to stderr without a traceback
or candidate-file content.

## Not Included

Version 0.1.2 does not provide AI summaries, project-type detection, nested `AGENTS.md` handling,
ignore-rule parsing, plugins, profiles, caches, databases, network services, MCP, daemons, GUIs,
CI/CD, telemetry, or automatic updates.

## Development

Run the complete local check:

```bash
just check
```

## Version maintenance

The versions in `pyproject.toml` and `context_loader/__init__.py`, the matching `CHANGELOG.md`
section, and required tests must change in the same release-preparation batch. `CHANGELOG.md` is the
authoritative version-change record, and Gitea Release notes are generated from the matching
section. Merging to `master` is not a release; formal publication still requires a separately
created and pushed tag.
