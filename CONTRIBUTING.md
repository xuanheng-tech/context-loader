# Contributing

Use the Python, Git, `uv` and `just` versions described in [README.md](README.md).
Runtime code uses only the Python standard library; keep it that way.

```bash
uv sync --frozen --no-config
just check
uv build
```

`just check` validates the lockfile, runs Ruff lint/format checks and the complete test suite,
including a wheel built and installed outside the checkout. Permission-sensitive tests must run as
a non-root user; `just ci-check` prepares that environment on a root CI runner.

Keep changes focused and add a synthetic regression test for changed behavior. Tests use
disposable repositories only: no credentials, real services or the caller's Git configuration.
Preserve the read-only boundary: the loader must not fetch, execute repository code, follow
symlinks or write to the target repository. Output must stay deterministic for identical inputs.

Changes to the CLI, JSON key sets or Markdown headings are public contract changes. Update
`tool_cli_contract.json`, README and `CHANGELOG.md` (under `## Unreleased`) in the same change,
and issue a new JSON `schema_version` whenever a key set changes.

Commit `uv.lock` changes only when dependencies or index metadata intentionally change.
Regenerate with `uv lock --no-config --default-index https://pypi.org/simple`; local mirror
configuration is not part of the public project lockfile.

Contributions are provided under the project's [Apache-2.0 license](LICENSE).
Follow [SECURITY.md](SECURITY.md) for security reports and README for release controls.
