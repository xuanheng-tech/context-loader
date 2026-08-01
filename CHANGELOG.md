# Changelog

This file is the authoritative record of version-relevant behavior in this repository. Entries are
limited to facts verified from source and Git history. Dates on tagged versions are annotated tag
dates.

## Unreleased

- Added: A tag-only Gitea Actions workflow with exact checkout, version and package verification,
  wheel and sdist builds, SHA-256 checksums, and refusal to overwrite an existing Release.
- Changed: Changelog validation now requires one non-empty section for the source version, and
  Gitea Release notes are extracted from that exact section.
- Release status: These changes were committed to `master` after `v0.1.2`; no later version tag
  exists.

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
