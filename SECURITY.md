# Security reporting

Context Loader trusts the current user and OS and treats repository contents as untrusted data.
It reads a fixed set of root files and local Git state without network access or execution of
repository code, but its output is **not redacted**: it can contain instructions, paths, branch
names and commit subjects from the repository. See [README.md](README.md) for the read boundary,
size limits and Git configuration checks. Review output before sharing it.

For a suspected vulnerability, use **Report a vulnerability** in the repository's GitHub Security
tab if private reporting is enabled. If that option is unavailable, open an issue containing only
a request for a private reporting channel; do not post credentials, private paths or a sensitive
exploit there. There is no promised response deadline or support policy for older releases.

Provide the package and Git versions, platform, exact command, expected boundary and a minimal
reproduction using a disposable repository with synthetic data. Never include real secrets.
Report the observed impact separately from assumptions.
