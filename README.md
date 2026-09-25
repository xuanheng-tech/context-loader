# Context Loader

Context Loader renders deterministic, bounded context for one local Git working tree.
The default CLI output remains Markdown; a stable JSON interface is also available for machine
callers. The tool reads repository state and a fixed set of root files without fetching, executing
repository code, network access, or writes to the target repository. Runtime code uses only the
Python standard library.

## Open-source quick start

Current stable release: **1.3.1**. Local CLI for coding-agent workflows; renders deterministic Markdown or JSON.

```bash
uv tool install 'context-loader==1.3.1'
```

```bash
pip install 'context-loader==1.3.1'
```

```bash
project-context --repo /path/to/repo
```

```bash
project-context --repo /path/to/repo --format json
```

## Install

Source version: `1.3.1`. Install its matching published release or an exact source commit.

Install via `uv`:

```bash
uv tool install 'context-loader==1.3.1'
```

Install via `pip`:

```bash
pip install 'context-loader==1.3.1'
```

For development or source-based installs tracking current repository (`1.3.1`):

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

`--format json` writes exactly one separator-compact UTF-8 JSON document plus one trailing newline
to stdout, within the JSON document bound under “Limits”. Keys are serialized in sorted order
with `ensure_ascii=False`. `--format json-compact` uses the identical serialization; only the field
set differs, as described under “Compact model consumption”. The declared contract for
`--format json` is:

```json
{
  "schema_version": 3,
  "tool": {
    "name": "context-loader",
    "version": "1.3.1"
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
  "statuses": [],
  "nested_context": {
    "files": ["docs/AGENTS.md"],
    "list_truncated": false,
    "scan_truncated": false
  },
  "warnings": []
}
```

`context_sha256` hashes the UTF-8 bytes of `context`; each `content_sha256` does the same for that
source's `content` body as rendered (after newline normalization, any `AGENTS.md` section selection
and any truncation marker) — never for the raw bytes of the file at `path`, which match only for
unselected, untruncated LF files. `sources` contains only file bodies that actually enter the final
context, in assembly order, after the existing newline normalization and truncation rules. `scope`
distinguishes `repository` from `global`; version 1.0.0's fixed root-file selection currently emits
only `repository` sources and does not add any global-file discovery.

`statuses` lists machine-readable collection and render conditions that previously appeared only inside `context` Markdown — plus the nested-context truncation flags, which exist only in machine form: skipped or absent sources, truncated sources, unreadable directory-tree entries, directory-tree entries whose directory holds more entries than one enumeration examines, sections omitted under the global output budget, truncated working-tree, declared-command or directory-tree listings, and truncated nested-`AGENTS.md` presence reports. A skipped or absent source reports the code of the condition the collector observed (`not_present`, `skipped_symlink`, `skipped_not_regular`, `skipped_encoding`, `skipped_unreadable`); the sentence in `context` is rendered from that same condition, so wording and code cannot drift apart. Each entry has stable `code`, `subject_kind`, and `subject` fields. The two `nested_agents_*` codes mirror the `nested_context` truncation booleans for consumers that branch only on `statuses`; the full `nested_context` object remains a first-class field, never folded into `statuses`. Callers can ignore `statuses` safely.

The optional `selection` object is present only on a rendered `AGENTS.md` source. Its section entries
contain heading, heading level, and fixed selection reasons; it never contains the original focus or
target path. Existing per-source fields remain unchanged; the document's exact key
set (which includes `nested_context`) is named by its `schema_version`, described below.

`nested_context` is always present and existence-only: `files` lists repository-relative paths of
non-directory `AGENTS.md` entries under subdirectories, found by a bounded scan that enumerates
directory entries, never opens or reads a candidate file and never traverses a symlink; it carries
no sizes, no mtimes and no contents. A symlinked (even dangling) `AGENTS.md` is listed, because its
existence comes from the directory entry itself; the scan never resolves what it points at. Any
`.git`, `.venv`, `venv`, `node_modules` or `site-packages` directory is skipped at every depth:
package-manager and interpreter-managed trees are not authored repository instructions.
`list_truncated` means more matching entries exist beyond the report caps: 32 paths, and a 4 KiB
budget metered on the emitted array itself — each path's escaped, JSON-serialized string plus its
separator, with the array's brackets charged to the first entry — so the emitted array never costs
more than the budget it declares. Because quoting is counted, an array already near the cap can
report one path fewer than an older release did, and then says so through `list_truncated`.
`scan_truncated` means the depth (4) or directory-count (2,000) budget was reached, a directory
held more entries than one enumeration examines (1,024), or a directory or entry could not be
read, so absence of a path is not proof of absence of the file. Nested paths
are sanitized with the same escaping the Markdown uses for repository-derived text, so a nested name
the filesystem could not decode never breaks the document;
see “Limits” for the root paths that are emitted verbatim. The corresponding
`nested_agents_list_truncated` and `nested_agents_scan_truncated` status entries mirror both flags.
Presence is not instruction: whether a nested file applies, and its text, remain the caller's
judgment; Markdown output is unchanged.

### Compact model consumption (`--format json-compact`)

`--format json-compact` emits a `schema_version` 4 document: exactly the version-3 document with
`sources[*].content` omitted. Every other field — `context`, `context_sha256`, `statuses`,
`nested_context`, `warnings`, `tool`, `repository`, and each source's `ordinal`, `kind`, `scope`,
`path`, `content_sha256` and optional `selection` — is identical to `--format json` for the same
arguments.
`sources` is provenance and index metadata, not the place a body must be fetched from: every omitted
body is already inside `context` verbatim, so consumers should read `context` for rule text and
reopen a source file only when the rendered body was truncated or selected away — never to recover a
body that the document already carried.
The projection exists because the selected source bodies already occur verbatim inside `context`,
so carrying them again in `sources` duplicates a large share of the document bytes (measured up to
~44% on real worktrees, scaling with how much of `context` those bodies occupy) for a model
consumer without adding information. Nothing is lost: each omitted body remains inside `context`,
`content_sha256` still fingerprints that rendered body, and `path` plus `canonical_root` locate the
underlying file for a bounded manual re-read (whose raw bytes may differ from the rendered body as
defined above). `schema_version` is an exact document selector, not an upgrade marker: version 4 is
a field-set projection of version 3, so consumers must branch on the value and must not apply
`>=`-superset reasoning. Introducing this compact format does
not otherwise change default Markdown bytes, exit codes or determinism.

One historical exception is acknowledged: releases 1.0.0 and 1.1.0 both emitted `schema_version` 1
although 1.1.0 added the top-level `statuses` key. From release 1.2.0 onward each emitted number
names exactly one key set, and a new number is issued whenever a key set would otherwise be reused.

The JSON schema version and package version are independent: this build emits `schema_version` `3`
for `--format json` and `4` for `--format json-compact`, while `tool.version` is `1.3.1`. Version 1
and 2 are the exact shapes already published in release 1.2.0 and are never reused: because
`nested_context` changes the default document's key set, version 3 names the current full-document
key set and version 4 names the compact projection.
Callers must depend only on fields declared above.
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
of the repository-root `AGENTS.md` only; it never changes which files are read. The JSON-only
`nested_context` field reports the existence of nested `AGENTS.md` paths without reading them;
deciding whether any of them applies stays with the calling agent harness exactly as before.

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

Final output bounds differ per format. The two stdout bounds below are enforced on the bytes written
to stdout and the nested report cap on the bytes the document emits for that array; every other
bullet is a per-component, read or capture bound, as each one states:

- Markdown stdout: 98,304 bytes. A section that cannot fit is replaced by an omission notice, and so
  is every later section, so the limit holds without cutting a section mid-body.
- JSON and JSON-compact stdout: 8,388,608 bytes (8 MiB) for the serialized document, including its
  trailing newline. This bound is fail-closed: if a repository's evidence cannot fit, the tool exits
  1, writes nothing to stdout and prints `error: JSON output exceeded the 8388608 byte document
  limit`. It never emits a truncated document. The component budgets below keep legitimate
  documents far inside it — the largest escape-dense repository audited for this release emitted
  263 KiB, of which 248 KiB was escaped `statuses` subjects — so it is a final backstop rather
  than an expected truncation point.
- Component budgets below are metered on the Markdown `context` content, and that same string is the
  JSON `context` value; because JSON escaping expands quotes, backslashes and control characters,
  the serialized bytes can be several times larger. The JSON document also repeats the source
  bodies, so no single component figure bounds JSON stdout.
- `AGENTS.md`: 256-KiB bounded source scan; selected source plus selection audit remains at most
  16 KiB, including a 4-KiB maximum small head
- `README.md`: 16 KiB
- Each entry file: 8 KiB
- All entry-file bodies: 24 KiB
- Declared commands: 8 KiB
- Directory tree: 12 KiB of listing body, 300 entries, and depth 2. Each directory is
  enumerated up to 512 entries, keeping the alphabetically first names so the listing never
  depends on operating system order. A directory that holds more is reported by a single
  `Listing incomplete:` line stating the total, the limit and up to three names, and by at most
  eight `directory_listing_incomplete` status entries naming individual directories plus one
  aggregate entry giving the totals when more directories were capped than are named: beyond the
  retained examples a capped directory is counted but not individually named, and no listing
  claims completeness it does not have.
  Three limits act in sequence and a name survives only if all three leave room. The 512-entry
  enumeration limit decides which names a directory offers at all; above it only an alphabetical
  prefix is ever seen. The 300-entry item budget, split between a directory's subdirectories and
  its non-directories so neither group can crowd the other out entirely, decides which offered
  names are kept. The 12 KiB listing budget then decides which kept names are rendered. Descent
  happens after a directory's own entries, so deep content cannot displace top-level names, but
  the tool promises no specific file: a root `README.md` is listed only when the root is not
  enumeration-capped, the entry budget still has a slot, and the byte budget still has room, and
  each of those three can fail on its own. Whenever one bites, the listing says so through the
  note, the marker, or a `truncated` status instead of presenting itself as complete.
  The `Listing incomplete:` line is charged to the 12 KiB listing budget before the body is cut,
  so the claim can never inflate the section; its cost is paid in listing bytes, so a long note
  removes that many bytes of entries, and the `truncated` status plus the marker inside the
  fence say so. The heading and the code-fence lines are not metered, so the whole Markdown
  section can exceed 12 KiB by that fixed few dozen bytes.
- Nested `AGENTS.md` presence scan: depth 4, 2,000 directories, at most 32 reported paths, a
  4 KiB budget metered on the emitted array, and at most 1,024 entries examined per directory;
  file contents are never read
- Working-tree changes: 100 paths and 4 KiB
- Recent commits: 8
- JSON-only listings have no byte budget of their own: `statuses` subjects are escaped names bounded
  by the entry counts above, while `repository` and `sources[*].path` are emitted verbatim, so a
  repository root the filesystem could not decode makes the machine formats exit 1 where Markdown
  still renders
- Git subprocess output: 16 MiB (bounded while reading)
- Candidate file validation: 16 MiB hard read bound

Truncation occurs only at complete UTF-8 and line boundaries and is marked explicitly.
These are output/capture limits. Eligible regular files are streamed up to a hard safety limit of
16 MiB to validate UTF-8 and reject NUL bytes, including beyond the captured prefix. A file exceeding
this limit or failing validation fails closed and is skipped (`Skipped: unreadable.` /
`Skipped: unsupported text encoding.`); pathological files cannot require unbounded scanning.
Symlinked candidate files are skipped, and directory symlinks are not traversed.

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

Version 1.3.1 does not provide AI summaries, project-type detection, loading or transport of
nested `AGENTS.md` contents (the JSON `nested_context` field reports bounded existence only),
Memory retrieval, semantic ranking, ignore-rule parsing, plugins, profiles, caches, databases,
network services, MCP, daemons, GUIs, CI/CD, telemetry, or automatic updates.

## Development

Run the complete local check:

```bash
just check
```

## Version maintenance

The versions in `pyproject.toml` and `context_loader/__init__.py`, the `tool_version` in
`tool_cli_contract.json`, the matching `CHANGELOG.md` section, required tests, and the README
current-stable declaration plus `context-loader==X.Y.Z`
install pins must change in the same release-preparation batch. `CHANGELOG.md` is the
authoritative version-change record. `scripts/release.py` rejects README stable/install
pins that disagree with the intended package version; historical changelog entries are
not part of that check. Merging to `master` is not a release; formal publication
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
