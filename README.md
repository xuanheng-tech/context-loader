# Context Loader

Context Loader renders deterministic, bounded context for one local Git working tree.
The default CLI output remains Markdown; a stable JSON interface is also available for machine
callers. The tool reads repository state and a fixed set of root files without fetching, executing
repository code, network access, or writes to the target repository. Runtime code uses only the
Python standard library.

## Open-source quick start

Context Loader is a local CLI that exports repository context deterministically for coding-agent workflows,
rendering stable Markdown or JSON output.

```bash
uv tool install codex-project-context-loader
```

```bash
pip install codex-project-context-loader
```

```bash
codex-project-context --repo /path/to/repo
```

```bash
codex-project-context --repo /path/to/repo --format json
```

## Install

Public stable release on PyPI: `0.1.7`.

Install via `uv`:

```bash
uv tool install codex-project-context-loader
```

Install via `pip`:

```bash
pip install codex-project-context-loader
```

For development or source-based installs tracking current repository (`0.1.7`):

```bash
uv tool install git+https://github.com/xuanheng-tech/context-loader.git
```

The repository also retains `./codex-project-context` as a direct development entry point.

The wheel installs the runtime package only. The source distribution additionally carries
`tool_cli_contract.json`, so a package-only consumer can pin the declared public CLI contract
without cloning. The test suite, `justfile` and lockfile stay in the Git repository and are not
part of either distribution; run them from a checkout of the matching tag.

## Platform and Runtime Requirements

- **Python**: Python 3.12 (`>=3.12,<3.13`). Runtime code uses only the Python standard library with zero runtime dependencies.
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
codex-project-context --version
codex-project-context --repo /home/user/projects/example
codex-project-context --repo /home/user/projects/example --format markdown
codex-project-context --repo /home/user/projects/example --format json
codex-project-context --repo /home/user/projects/example \
  --focus "Authentication and session management" \
  --path auth/session.py
```

`--format` defaults to `markdown`. In Markdown mode, `--repo` retains the 0.1.1 contract: it must be
the absolute, canonical root of a non-bare Git working tree. In JSON mode, an absolute existing
directory inside the working tree is accepted; symlinks are normalized and the discovered root is
reported as `canonical_root`. Relative paths, non-Git directories, regular files, and bare
repositories are rejected in both modes.

`--focus` and `--path` are optional, bounded selection signals for the root `AGENTS.md`. `--path`
must be repository-relative. The collector does not retain either input in output or audit data.
Calls that omit both options remain valid and use the conservative fallback described below.

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
    "version": "0.1.7"
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
      "content": "actual selected source text",
      "selection": {
        "source": "AGENTS.md",
        "selected_sections": [],
        "indexed_only_sections": [],
        "chars_selected": 0,
        "chars_omitted": 0,
        "truncated": false,
        "parse_fallback": false,
        "source_scan_truncated": false,
        "index_truncated": false
      }
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
`repository` from `global`; version 0.1.7's fixed root-file selection currently emits only
`repository` sources and does not add any global-file discovery.

The optional `selection` object is present only on a rendered `AGENTS.md` source. Its section entries
contain heading, heading level, and fixed selection reasons; it never contains the original focus or
target path. Existing source fields and schema version 1 remain unchanged.

The JSON schema version and package version are independent: `schema_version` is currently the
integer `1`, while `tool.version` is `0.1.7`. Callers must depend only on fields declared above.
The document contains no generated time or random identifier, so unchanged input produces identical
JSON bytes. On failure, stdout remains empty and stderr contains only a short diagnostic.

## Authority Boundary

Context Loader is a bounded transport for repository-root context, not the authority for a
repository's instruction hierarchy. It reads the fixed root candidates listed below, renders them
under explicit limits, and stops there.

Resolving an instruction hierarchy stays with the calling agent harness. That includes any
shared or user-level instruction file outside the repository, nested or scoped `AGENTS.md` files
under subdirectories, and any include or import directive written inside an instruction file:
such a directive is transported as literal text and is never followed. `--path` selects sections
of the repository-root `AGENTS.md` only; it never changes which files are read.

A successful run therefore proves that the bounded root context was collected and rendered. It
does not prove that every instruction applicable to a task has been loaded.

## Supported Root Files

Only these exact files directly under the Git root are eligible:

- Development instructions: `AGENTS.md`
- Project overview: `README.md`
- Entry files, in fixed order: `pyproject.toml`, `justfile` or `Justfile`, `package.json`,
  `Makefile`, `Cargo.toml`, and `go.mod`

Lowercase `justfile` takes precedence over `Justfile`. Nested files, lockfiles, CI configuration,
`.env`, and glob-discovered files are not read.

A leading UTF-8 byte order mark is removed from the root `AGENTS.md` before parsing, so a
heading on the first line is still recognized as a heading.

The root `AGENTS.md` is split at Markdown headings outside fenced code blocks. The output always
starts with complete early sections fitting a 4-KiB head, then adds complete relevant sections in
source order using exact normalized focus/path tokens and their parent context. Remaining headings
appear in an explicit index whose body text is not loaded. With no selection signals, only the small
head and index are emitted. Unsafe heading parsing falls back to a bounded head and an explicit
manual-recovery notice. A head whose own headings would not fit the budget falls back to the same
bounded head without a section index; a single instruction file never fails the whole collection.

`Omitted source characters` is measured against the whole normalized source, so a bounded source
scan that ends before EOF still reports the characters it could not select.

## Limits

- Final stdout: 98,304 bytes
- `AGENTS.md`: 256-KiB bounded source scan; selected source plus selection audit remains at most
  16 KiB, including a 4-KiB maximum small head
- `README.md`: 16 KiB
- Each entry file: 8 KiB
- All entry-file bodies: 24 KiB
- Declared commands: 8 KiB
- Directory tree: 12 KiB, 300 entries, and depth 2
- Working-tree changes: 100 paths and 4 KiB
- Recent commits: 8

Truncation occurs only at complete UTF-8 and line boundaries and is marked explicitly.
These are output/capture limits. Eligible regular files are still streamed to EOF to validate UTF-8
and reject NUL bytes, including beyond the captured prefix; a large file can therefore take longer
to read. Symlinked candidate files are skipped, and directory symlinks are not traversed.

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

Version 0.1.7 does not provide AI summaries, project-type detection, nested `AGENTS.md` handling,
Memory retrieval, semantic ranking, ignore-rule parsing, plugins, profiles, caches, databases,
network services, MCP, daemons, GUIs, CI/CD, telemetry, or automatic updates.

## Development

Run the complete local check:

```bash
just check
```

## Version maintenance

The versions in `pyproject.toml` and `context_loader/__init__.py`, the matching `CHANGELOG.md`
section, and required tests must change in the same release-preparation batch. `CHANGELOG.md` is the
authoritative version-change record. Merging to `master` is not a release; formal publication
still requires a separately created and pushed tag.

## Release closure and retries

GitHub `ci` and Gitea `quality` both use `just check`. GitHub `publish-pypi.yml` is the only
package build/upload authority, using the `pypi` environment and OIDC Trusted Publishing.
GitHub `release-record` closes the GitHub Release after package publication. Gitea `release`
only verifies the public release, mirrors its exact formal tag object if missing, and closes
the Gitea Release record. It builds no package and has no PyPI publishing credentials.
No private endpoint or credential is needed on GitHub.

Release checks use Python 3.12, Git, and the runner's existing OpenSSL CLI. They compare downloaded
PyPI file bytes, metadata and SHA-256, plus the publisher/tag/commit claims in PyPI's HTTPS-served
provenance. This is an identity check, not an independent cryptographic Sigstore verifier.

- Tag/version or expected-commit mismatch and `just check` failure stop before build/upload.
- The build refuses to run unless `dist/` is empty, and refuses any produced file set other than
  the current version's wheel and sdist. A leftover artifact can carry a release filename while
  holding different bytes, so it is never treated as the current release.
- A complete matching PyPI version skips both build and upload. Missing/conflicting provenance,
  unexpected files or differing hashes stop; existing files are never overwritten.
- Release closure runs right after publication, so `record`, `verify` and the Gitea sync poll the
  PyPI index and integrity endpoints for a bounded period before treating a version as missing.
  Build and upload selection never poll: there, an absent version still means "not yet published".
- A refused release API call reports the bounded server message alongside its status code. A
  GitHub Release is created against the release commit, so that commit must already be reachable
  from the public branch; pushing the release commit to the public branch precedes record closure.
- If an upload stopped after one file, rerun the **original failed publish job** while its original
  `dist` artifact is available. It compares the original bytes and selects only missing files.
  A full rebuild is refused for an incomplete PyPI file set. If the original artifact is gone,
  stop for recovery; do not substitute a fresh build.
- If PyPI succeeded but a Release failed, rerun `release-record` on GitHub (manual input: tag)
  and `release` on Gitea. Existing matching records pass; a missing record is created once;
  a draft, differing recorded identity or tag conflict stops without overwriting it.
- Gitea also retries the current project version on `master` pushes, only after that version has
  a public tag, verified PyPI files and GitHub Release. An unpublished version reports `SKIP`.
  Use the **built-in Actions job token** for exact missing-tag synchronization: Gitea suppresses
  recursive workflows for that actor, including old tag workflows. Never use a PAT for this step.
- Keep historical/private tags and archive refs private. Never mirror all refs or push all tags.

Release verification reads the public GitHub API with `PUBLIC_GITHUB_TOKEN` when it is set, as CI
does. A local caller without that variable falls back to the credential an authenticated
[GitHub CLI](https://cli.github.com/) already holds, keeping verification authenticated and clear
of the unauthenticated rate limit without this repository storing a token. With neither available
the calls stay unauthenticated and may be rate limited; the reported API message names that cause.

For each formal version, verify both platforms separately (the Gitea API URL and `RELEASE_TOKEN`
come from the caller's private environment):

```bash
python scripts/release.py verify vX.Y.Z
python scripts/release.py verify vX.Y.Z --platform gitea \
  --api-url "$GITEA_API_URL" --repository "$GITEA_REPOSITORY"
```

Each check reports tag commit, PyPI file identities and the selected Release ID. Git Finalizer's
`remote_verified` describes branch publication only; report package/release verification separately.
