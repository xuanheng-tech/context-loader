# Agent notes

Worktree governance uses `.agents/worktree-policy.toml` and the installed Worktree
Controller. The canonical checkout is reserved for repository control and
verification; ordinary implementation uses a Controller-managed feature worktree
with its writer lease and write guard. New clones require explicit Controller
adoption before starting a governed writer session.

Release version consistency (package metadata, README current-stable declaration, and install pins) is enforced by `scripts/release.py` during release preparation. See README “Version maintenance”.
