# Changelog

This file records only behavior verified from this repository's Git history. Dates on tagged
versions are the annotated tag dates.

## Unreleased

- Add a tag-only Gitea Actions release workflow with version, source checkout, package metadata,
  and SHA-256 verification.
- Keep post-0.1.2 dependency and CI maintenance separate from the 0.1.2 release.

## 0.1.2 - 2026-08-01

- Add the stable JSON output contract with source provenance and SHA-256 values while preserving
  the existing Markdown output.
- Accept an absolute path inside a worktree in JSON mode and report the canonical Git root.

## 0.1.1 - 2026-07-20

- Report a configured upstream even when its commit is not yet locally resolvable, as in a clone
  of an empty remote.

## 0.1.0 - 2026-07-19

- Introduce deterministic, bounded local Git context collection and the
  `codex-project-context` CLI.
