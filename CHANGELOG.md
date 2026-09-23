# Changelog

This file is the authoritative record of version-relevant behavior in this repository. Entries are
limited to facts verified from source and Git history. Dates on tagged versions are annotated tag
dates.

## Unreleased

- Added: JSON documents (`--format json` and `--format json-compact`) include an always-present
  existence-only `nested_context` object listing nested `AGENTS.md` relative paths found by a
  contents-blind bounded scan (depth 4 and 2,000-directory scan bounds, 32-path report cap;
  `.git`, `.venv`, `venv`, `node_modules` and `site-packages` trees skipped), with
  `list_truncated`/`scan_truncated` flags mirrored as `nested_agents_list_truncated` and
  `nested_agents_scan_truncated` statuses. Root-file selection, Authority Boundary semantics,
  Markdown bytes and all existing JSON field rules are otherwise unchanged; presence is not
  instruction and nested contents are never read or transported.
- Docs: the `json-compact` section states that `sources` is provenance/index metadata whose bodies
  already occur verbatim in `context`, so consumers must not reopen source files to recover a body
  the document already carried.

## 1.2.0

- Added: `--format json-compact` emits a `schema_version` 2 document that is exactly the version-1
  JSON document with the duplicated `sources[*].content` bodies omitted. Those bodies already occur
  verbatim inside `context`, so the projection removes the second copy a model consumer would
  otherwise pay for; every other field (`context`, `context_sha256`, `statuses`, `warnings`, `tool`,
  `repository`, and each source's `ordinal`, `kind`, `scope`, `path`, `content_sha256` and optional
  `selection`) is identical to `--format json` for the same arguments.
- Preserve: Default Markdown output, `--format json` bytes, exit codes, security bounds and
  determinism are unchanged, and `--focus` remains AGENTS section selection only.
- Changed: `tool_cli_contract.json` declares the widened CLI surface: `flags.format` gains
  `json-compact`, the `json_compact_accepts_path_inside_worktree` precondition is added, and
  `contract_version` moves to 3 for `tool_version` 1.2.0. The published 1.1.0 sdist keeps
  `contract_version` 2 immutably.

## 1.1.0

- Added: JSON results include a top-level `statuses` array that exposes skipped, absent, truncated, unreadable, and globally omitted context conditions without parsing Markdown. Existing `sources`, `context`, and `schema_version` fields are unchanged.

## 1.0.0

- Changed: Rename the sole CLI and development entrypoint to `project-context`, and use the
  provider-neutral `Project Context` Markdown heading (public CLI contract 2).
- Changed: Publish only `context-loader`; retire the compatibility distribution from new
  builds while retaining verification of factual historical package identities.
- Fixed: Git subprocess output is bounded while reading (16-MiB safety limit) to prevent
  unbounded memory buffering, terminating the subprocess immediately if exceeded.
- Fixed: Candidate repository files enforce an explicit 16-MiB hard input/read bound to prevent
  unbounded scanning of pathological files, failing closed as unreadable (`Skipped: unreadable.`).
- Fixed: Release verification selects the distribution model of the release being verified,
  supporting historical single-distribution, 0.1.8 dual-distribution, and 1.0.0+ neutral models.
- Fixed: Public tag identity is resolved from Git rather than the GitHub REST API, avoiding
  unauthenticated rate-limiting failures on shared CI runner addresses.
- Fixed: Rate-limited release API reads are waited out within a bounded budget instead of failing
  closure immediately.
- Changed: Documentation and README examples updated to provider-neutral `project-context` 1.0.0.
- Preserve: The existing read-only Git, environment isolation, JSON schema version 1 and release
  provenance checks remain intact.

## 0.1.8

- Fixed: release closure polls the PyPI index and integrity endpoints for a bounded period, so
  `record` and `verify` no longer fail the propagation race immediately after publication.
- Changed: a refused release API call reports the bounded server message, so a Release-record
  refusal names its own cause instead of only its status code.
- Added: the source distribution ships `tool_cli_contract.json`, so a package-only consumer can
  pin the declared public CLI contract; the wheel stays runtime-only.
- Changed: release verification resolves the public GitHub API token from `PUBLIC_GITHUB_TOKEN`,
  falling back to an authenticated GitHub CLI, so the canonical local check is not rate limited.
- Changed: every third-party Action is now pinned by commit SHA in the CI and release-record
  workflows as well, matching the publish workflow and the Gitea workflows.
- Removed: an unused parameter of the internal directory-tree classifier; rendered output,
  the CLI surface and the JSON contract are unchanged.
- Changed: the canonical PyPI distribution is now `context-loader`. It carries the same
  `context_loader` runtime and the unchanged `codex-project-context` console script, and the
  public CLI surface and JSON schema version 1 are unchanged.
- Added: `codex-project-context-loader` continues as a compatibility distribution that only
  depends on `context-loader==0.1.8`. It ships no runtime and no console script, so installing
  either distribution provides exactly one implementation. Releases 0.1.5-0.1.7 stay untouched.
- Changed: the release workflow builds, publishes and verifies both distributions, each through
  its own Trusted Publishing exchange, and release records cover the files of both.
- Changed: Version bump to `0.1.8`.

## 0.1.7

- Fixed: AGENTS index fitting no longer re-renders the whole selection audit once per dropped
  index entry. Fitting now measures each index line once and keeps the same longest fitting
  prefix, removing the cubic growth in heading count while leaving rendered output unchanged.
- Fixed: an AGENTS head whose own headings exceed the AGENTS budget now falls back to the bounded
  head without a section index instead of raising and failing the whole context collection.
- Fixed: a leading UTF-8 byte order mark is removed from the root `AGENTS.md` before heading
  parsing, so a heading on the first line is recognized instead of being folded into the head.
- Fixed: `Omitted source characters` is measured against the whole normalized source, so a bounded
  source scan that ends before EOF no longer reports `0` omitted characters alongside a truncated
  selection.
- Added: the release build refuses a non-empty `dist/` and refuses any produced file set other
  than the current version's wheel and sdist, so a leftover artifact cannot be treated as the
  current release.
- Added: release identity checks and safe retries for existing packages, partial uploads from the
  original build, and missing Release records; conflicting identities stop publication.
- Changed: GitHub is the sole package build and PyPI Trusted Publishing authority; Gitea only
  synchronizes verified public tags and Release records.
- Changed: every third-party Action in the PyPI release workflow is pinned by commit SHA with a
  version comment.
- Changed: integrate the pending Python tooling update and use Node 24 Actions for CI and release
  records.
- Changed: document that Git is used from the fixed absolute path `/usr/bin/git` rather than
  through `PATH`, and document the authority boundary between transported repository-root context
  and agent-harness instruction-hierarchy resolution.
- Changed: clarify tested platforms, determinism, sensitive output and large-file read limits.
- Changed: Version bump to `0.1.7`.

## 0.1.6

- Fixed: Correctly render linked worktree `.git` file as `.git` rather than `.git/` in the Directory Tree section.
- Added: PyPI release workflow quality gates (`just check` prerequisite and tag/version consistency validation).
- Changed: Explicitly document Python 3.12, Git, and POSIX/Linux platform boundaries in README.
- Changed: Version bump to `0.1.6`.

## 0.1.5 - 2026-08-18

- Added: PyPI installation-first guidance and pinned `0.1.5` release docs in README.
- Added: project metadata URLs (Repository/Issues) for the GitHub source repository.
- Added: first-party Trusted Publishing workflow for PyPI (`.github/workflows/publish-pypi.yml`) that builds
  wheels/sdists, uploads artifacts, and publishes via OIDC from `pypa/gh-action-pypi-publish`.
- Changed: Version bump to `0.1.5`; this release prepares the first PyPI distribution path.

## 0.1.4 - 2026-08-18

- Added: Apache-2.0 license declaration and repository `LICENSE` file for OSS release readiness.
- Added: Public GitHub Open-Source installation section in README.
- Added: GitHub Actions CI workflow at `.github/workflows/ci.yml` running the existing `just check` quality gate for public forks.
- Release status: Prepared as the first open-source release candidate on `master`.

## 0.1.3 - 2026-08-09

- Added: Optional bounded `focus` and repository-relative `path` signals plus a deterministic
  per-source AGENTS selection audit in Markdown and JSON output.
- Changed: Root `AGENTS.md` loading now combines a 4-KiB head, relevant complete Markdown sections,
  parent context, and an indexed-only heading fallback without increasing the 16-KiB output budget.
- Added: A tag-only Gitea Actions workflow with exact checkout, version and package verification,
  wheel and sdist builds, SHA-256 checksums, and refusal to overwrite an existing Release.
- Changed: Changelog validation now requires one non-empty section for the source version, and
  Gitea Release notes are extracted from that exact section.
- Release status: Released from annotated tag `v0.1.3`; its tag-triggered workflow creates the
  Gitea Release and verified package artifacts.

## 0.1.2 - 2026-08-01

- Added: The stable JSON output contract with source provenance and SHA-256 values while preserving
  the existing Markdown output.
- Changed: JSON mode accepts an absolute path inside a worktree and reports the canonical Git root.
- Release status: Tagged as `v0.1.2`.

## 0.1.1 - 2026-07-20

- Fixed: Report a configured upstream even when its commit is not yet locally resolvable, as in a
  clone of an empty remote.
- Release status: Tagged as `v0.1.1`.

## 0.1.0 - 2026-07-19

- Added: Deterministic, bounded local Git context collection and the `codex-project-context` CLI.
- Release status: Tagged as `v0.1.0`.
