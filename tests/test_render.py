from pathlib import Path

from context_loader.collect import (
    CollectedFile,
    DeclaredCommand,
    DirectoryTree,
    ProjectContext,
    TreeEntry,
)
from context_loader.git import RecentCommit, RepositoryState
from context_loader.render import render_markdown


def test_markdown_renderer_matches_0_1_1_golden() -> None:
    state = RepositoryState(
        repository=Path("/workspace/demo"),
        branch="main",
        head="a" * 40,
        upstream="not configured",
        ahead_behind="not available",
        changes=(),
        commits=(
            RecentCommit(
                object_id="a" * 40,
                author_date="2026-01-02T03:04:05+00:00",
                subject="baseline",
            ),
        ),
    )
    project = ProjectContext(
        instructions=CollectedFile("AGENTS.md", "markdown", None, "Use care.\n"),
        overview=CollectedFile("README.md", "markdown", None, "Demo overview.\n"),
        entry_files=(CollectedFile("pyproject.toml", "toml", None, '[project]\nname = "demo"\n'),),
        commands=(DeclaredCommand("pyproject.toml", "demo", "pkg:main"),),
        directory_tree=DirectoryTree(
            (TreeEntry(".git", "directory"), TreeEntry("README.md", "file"))
        ),
    )
    fixture = Path(__file__).with_name("fixtures") / "markdown_0_1_1.md"

    assert render_markdown(state, project) == fixture.read_bytes()
