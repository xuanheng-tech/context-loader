# Changelog

This file is the authoritative record of version-relevant behavior in this repository. Entries are
limited to facts verified from source and Git history. Dates on tagged versions are annotated tag
dates.

## Unreleased

- Docs: tighten the release-notes claims that a post-release audit could not reproduce from the
  published artifacts. The 1.3.1 "Preserve" note is exact only for Markdown: `--format json` and
  `--format json-compact` embed `tool.version`, so their bytes differ from 1.3.0 by that one string,
  while every remaining field — including each real repository's `nested_context` path list —
  matched 1.3.0 byte for byte (measured on seven repositories). Two README sentences that described
  the document key set and the version renumbering relative to 1.2.0 are restated version-neutrally,
  since a reader of 1.3.1 cannot resolve "now" or "below" from the shipped text.
- Fixed: a skipped or absent root source reports the machine code of the condition the collector
  observed, taken from that condition itself, instead of a code looked up from the sentence shown in
  `context`. The lookup matched only the exact wording in use, so any reworded or newly added
  sentence fell through to `skipped_unreadable` and `--format json` / `--format json-compact` could
  report an unrelated condition as an unreadable file with nothing anywhere indicating the mistake.
  The five codes (`not_present`, `skipped_symlink`, `skipped_not_regular`, `skipped_encoding`,
  `skipped_unreadable`) and the Markdown sentences are unchanged for every repository that collects
  today.
- Fixed: a `truncated` source status is no longer derived by searching the rendered body for the
  truncation marker. A source whose own text quotes that marker was reported as truncated; only a
  budget that actually shortened the body now claims it.
- Changed: the directory tree lists each directory's own entries before descending into any of its
  subdirectories, so a repository's top-level files are no longer displaced by deep content. In any
  worktree whose listing includes a subdirectory entry and that subdirectory's own contents, the
  lines after that entry appear in a different order than in 1.3.1, which changes the
  `## Directory Tree` body inside `context` and therefore `context_sha256`. The tree's budgets
  (12 KiB of listing body, 300 entries, depth 2), the meaning of `truncated`, the entry kinds and
  every JSON key are unchanged.
- Added: directory enumeration is capped per directory — 512 entries for the tree, 1,024 for the
  nested `AGENTS.md` presence scan — and the retained entries are the alphabetically first names, so
  a single huge directory is no longer materialised whole and a capped listing does not depend on
  operating-system enumeration order. Each scan keeps its own budget and its own truncation signal.
  Capped tree directories are reported by exactly one `Listing incomplete:` line carrying the total
  count, the limit that was applied and up to three example names, and by at most eight
  `directory_listing_incomplete` `statuses` entries naming individual directories plus one aggregate
  entry giving the totals when more were capped than are named individually; a capped directory
  beyond the retained examples is counted, not silently dropped. Previously each capped directory
  produced its own unbounded note outside any budget, which reached 12,973 bytes of section on a
  120-capped-directory fixture where 1.3.1 emitted 4,848 for the same tree. The note is now charged
  against the 12 KiB listing budget before the body is cut, so the claim cannot inflate the section,
  and its cost is paid in listing bytes rather than capped at one line. The heading and fence lines
  remain unmetered, as they were in 1.3.1. Internal counting and deduplication use the raw directory
  path and the aggregate carries an explicit identity, because display escaping is not injective: a
  directory named `` ` `` and one named `\x60` render identically and must stay two facts. A
  directory above the nested-scan cap sets the existing `scan_truncated` flag and
  `nested_agents_scan_truncated` status. No JSON key set changes, so `schema_version` stays 3/4.
- Added: when a directory's own entries no longer fit the tree item budget, its subdirectories and
  its non-directories each keep a share of it, so alphabetically-earlier directories cannot crowd
  out a root `README.md` entirely. The precondition is now stated exactly, and it is three limits
  rather than two: the root must be enumerated without hitting the 512-entry cap, the 300-entry
  budget must still have a slot, and the 12 KiB listing budget must still have room to render the
  line. Any one of them can drop a name on its own, so the tool promises no specific file; where a
  limit bites the listing reports the count, the cap and its retained examples instead of
  implying completeness.
- Fixed: a directory tree shortened by its own 12 KiB render budget is now reported in the machine
  formats. The renderer kept that fact to itself, so `context` carried the truncation marker while
  `statuses` stayed silent: measured on a repository of 297 root files with 41-character names, 298
  entries were collected and passed the 300-entry item budget, the 12 KiB body budget published 292
  of them, and 1.3.1 emitted no tree status at all for that cut. `truncated/tree/Directory Tree` is
  now reported for a render-budget cut as well as for a collection cut. `context` and
  `context_sha256` are byte-identical to 1.3.1 on that fixture and no JSON key changed; the gap
  predates 1.3.1 and is on a path this change touches, so it is fixed here rather than left to
  documentation.
- Changed: the shared limits and frozen value types move to `context_loader/model.py` and the safe,
  bounded filesystem primitives to `context_loader/filesystem.py`; `collect.py` keeps collection
  policy, `render.py` derives display text from the typed reason, and no output changes from the move
  itself.

## 1.3.1

- Fixed: the nested `AGENTS.md` path report budget is metered on each path's final serializable
  representation — escaped with the same display escaping the document emits, then measured as the
  JSON string bytes actually written, including quoting, the array separator and, for the first
  entry, the array's own brackets — instead of on pre-escape path bytes. Escape-dense names
  (backticks, undecodable byte sequences) could previously make the emitted `nested_context.files`
  carry several times the documented 4 KiB; because quoting is now counted, an array whose total is
  already near 4 KiB can also admit one entry fewer even with ordinary names. Both are value changes
  in that one field and in the two signals derived from it — `list_truncated` can become true and the
  `nested_agents_list_truncated` status can appear where 1.3.0 reported neither — while no key set
  changes, so `schema_version` stays 3/4 and `list_truncated` keeps its meaning.
- Added: `--format json` and `--format json-compact` enforce a bound on the final serialized
  document (8,388,608 bytes including the trailing newline). The bound is fail-closed: an
  over-budget repository exits 1 with an empty stdout and a one-line diagnostic rather than emitting
  a document the declared contract cannot describe. It is a backstop, not an expected truncation
  point: the component budgets cap the rendered `context` at 98,304 Markdown bytes and the repeated
  source bodies at 56 KiB before escaping, the nested path list at 4 KiB after escaping, and
  `statuses` at the 300-entry directory tree and 100 working-tree changes — the worst audited
  escape-dense repository measured 248 KiB of escaped subjects and 263 KiB for the whole document,
  so no run that succeeds today starts failing from this bound.
- Docs: "Limits" now states a per-format final bound (Markdown 98,304 bytes; JSON and
  JSON-compact 8 MiB serialized document) instead of one figure that only described Markdown, since
  the machine formats legitimately carry the rendered context plus a second copy of every source
  body and cannot share the Markdown cap. The same section now states the remaining asymmetry this
  audit exposed and a new test pins it: `nested_context` paths and `statuses` subjects are escaped,
  while `repository` and `sources[*].path` are emitted verbatim, so a repository root containing
  bytes the filesystem could not decode renders as Markdown but makes `--format json` and
  `--format json-compact` exit 1. Escaping those two fields is a separate published-value change
  with its own consumer impact, so it is deliberately not folded into this budget correction.
- Preserve: Markdown bytes, the default format, every flag and exit code other than the new
  over-budget JSON case, `schema_version` 3 and 4, `contract_version` 3, the collection scope and the
  Authority Boundary are unchanged; on ordinary repositories all three formats produce the same
  bytes as 1.3.0.

## 1.3.0

- Changed: because the new top-level `nested_context` key changes the exact document shape, the JSON
  document schema version advances — `--format json` now emits `schema_version` `3` (was `1`) and
  `--format json-compact` emits `4` (was `2`). Versions `1` and `2` stay bound to the shapes already
  published in release 1.2.0 and are never reused, so each `schema_version` names exactly
  one key set; the compact document remains the full document minus `sources[*].content`.
- Added: JSON documents (`--format json` and `--format json-compact`) include an always-present
  existence-only `nested_context` object listing non-directory nested `AGENTS.md` relative paths
  found by a contents-blind bounded scan (depth 4 and 2,000-directory scan bounds, 32-path and
  4-KiB report caps; symlinked entries listed without ever resolving or reading their targets;
  `.git`, `.venv`, `venv`, `node_modules` and `site-packages` trees skipped), with
  `list_truncated`/`scan_truncated` flags mirrored as `nested_agents_list_truncated` and
  `nested_agents_scan_truncated` statuses. Listed paths are sanitized with the same escaping the
  Markdown uses for repository-derived text, which also fixes a pre-existing crash class: an
  unreadable non-UTF-8 directory name no longer breaks `--format json` through a raw status
  subject. Root-file selection, Authority Boundary semantics and
  Markdown bytes are otherwise unchanged; presence is not instruction and nested contents are
  never read or transported.
- Docs: the `json-compact` section states that `sources` is provenance/index metadata whose bodies
  already occur verbatim in `context`, so consumers must not reopen source files to recover a body
  the document already carried.
- Changed: `tool_cli_contract.json` advances `tool_version` to 1.3.0 while `contract_version` stays
  3: this release changes the JSON document shape — governed by the output `schema_version` values 3
  and 4 — and no command, flag, format choice or precondition, mirroring the 1.1.0 precedent where a
  document-shape addition left the public CLI contract version alone.

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
