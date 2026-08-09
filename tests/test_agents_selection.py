from __future__ import annotations

from pathlib import Path

from context_loader.collect import (
    AGENTS_HEAD_LIMIT_BYTES,
    AGENTS_LIMIT_BYTES,
    _parse_markdown_sections,
    collect_project_context,
    render_agents_selection_audit,
)


def _collect(
    tmp_path: Path,
    content: str,
    *,
    focus: str | None = None,
    path: str | None = None,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(content, encoding="utf-8")
    return collect_project_context(repo, focus=focus, path=path).instructions


def _long_joinquant_agents() -> str:
    parts = [
        "# Synthetic Project\n\n",
        "## Core Rules\nCore rule must remain visible.\n\n",
    ]
    for index in range(1, 10):
        parts.append(
            f"## Command Block {index}\n"
            f"COMMAND_BODY_{index}_MUST_NOT_LOAD\n"
            + ("build lint package release command only\n" * 140)
            + "\n"
        )
    parts.append(
        "## Section 20 JoinQuant Research Notebook Imports\n"
        "Before implementing a provider extractor, probe the actual runtime and record the callable.\n"
    )
    return "".join(parts)


def _entry(source, heading: str):
    assert source.selection is not None
    return next(entry for entry in source.selection.selected_sections if entry.heading == heading)


def test_markdown_sections_preserve_source_order_boundaries_and_normalized_headings() -> None:
    content = (
        "document head\n"
        "# Project Identity\nintro\n"
        "## Provider_Runtime\nrule\n"
        "```markdown\n# not a heading\n```\n"
        "### Child Rule\nchild\n"
    )

    sections = _parse_markdown_sections(content)

    assert [section.heading for section in sections] == [
        "Project Identity",
        "Provider_Runtime",
        "Child Rule",
    ]
    assert [section.heading_level for section in sections] == [1, 2, 3]
    assert [section.parent_index for section in sections] == [None, 0, 1]
    assert sections[1].normalized_heading == "provider runtime"
    assert sections[1].text == content[sections[1].start : sections[1].end]
    assert [section.start for section in sections] == sorted(section.start for section in sections)


def test_short_agents_is_loaded_completely_with_small_head_audit(tmp_path: Path) -> None:
    content = "# Demo\n\n## Core Rules\nKeep the invariant.\n"

    source = _collect(tmp_path, content)

    assert source.content == content
    assert source.selection is not None
    assert source.selection.chars_selected == len(content)
    assert source.selection.chars_omitted == 0
    assert source.selection.truncated is False
    assert all("head" in entry.reasons for entry in source.selection.selected_sections)
    assert source.selection.indexed_only_sections == ()


def test_late_joinquant_section_is_selected_beyond_old_prefix_without_command_blocks(
    tmp_path: Path,
) -> None:
    content = _long_joinquant_agents()
    section_start = content.index("## Section 20")
    assert len(content[:section_start].encode("utf-8")) > AGENTS_LIMIT_BYTES

    source = _collect(
        tmp_path,
        content,
        focus="JoinQuant provider notebook runtime",
        path="scripts/joinquant_cloud/p1_minute_provider_probe.py",
    )

    assert "Section 20 JoinQuant Research Notebook Imports" in source.content
    assert "probe the actual runtime" in source.content
    assert "COMMAND_BODY_1_MUST_NOT_LOAD" not in source.content
    assert source.selection is not None
    assert "focus_match" in _entry(source, "Section 20 JoinQuant Research Notebook Imports").reasons
    indexed = {entry.heading for entry in source.selection.indexed_only_sections}
    assert {f"Command Block {index}" for index in range(1, 10)} <= indexed
    assert len(source.content.encode("utf-8")) < AGENTS_LIMIT_BYTES


def test_fail_safe_index_contains_only_omitted_headings_not_section_bodies(
    tmp_path: Path,
) -> None:
    source = _collect(tmp_path, _long_joinquant_agents())
    assert source.selection is not None

    audit = render_agents_selection_audit(source.selection)

    assert "Additional AGENTS sections not loaded" in audit
    assert "H2 Section 20 JoinQuant Research Notebook Imports" in audit
    assert "probe the actual runtime" not in audit
    assert "COMMAND_BODY_2_MUST_NOT_LOAD" not in audit
    assert "Core rule must remain visible" in source.content
    assert len(source.content.encode("utf-8")) <= AGENTS_HEAD_LIMIT_BYTES


def test_multi_token_focus_and_path_match_select_different_late_sections(
    tmp_path: Path,
) -> None:
    content = (
        "# Demo\n\n## Core\ncore\n\n"
        "## Large Unrelated\n"
        + ("unrelated filler line\n" * 300)
        + "\n## Runtime Provider Contract\nFOCUS_TARGET\n"
        "\n## Billing Adapter\nScope: services/billing/adapter.py\nPATH_TARGET\n"
    )

    source = _collect(
        tmp_path,
        content,
        focus="provider runtime",
        path="services/billing/adapter.py",
    )

    assert "FOCUS_TARGET" in source.content
    assert "PATH_TARGET" in source.content
    assert "focus_match" in _entry(source, "Runtime Provider Contract").reasons
    assert "path_match" in _entry(source, "Billing Adapter").reasons


def test_nested_relevant_section_includes_parent_context_once(tmp_path: Path) -> None:
    content = (
        "# Demo\n\n## Core\ncore\n\n"
        "## Large Unrelated\n"
        + ("filler line\n" * 500)
        + "\n## Integrations\nPARENT_CONTEXT_SENTINEL\n"
        "### JoinQuant Notebook Runtime\nCHILD_SENTINEL\n"
    )

    source = _collect(tmp_path, content, focus="JoinQuant notebook runtime")

    assert source.content.count("PARENT_CONTEXT_SENTINEL") == 1
    assert source.content.count("CHILD_SENTINEL") == 1
    assert "parent_context" in _entry(source, "Integrations").reasons
    assert "focus_match" in _entry(source, "JoinQuant Notebook Runtime").reasons


def test_relevant_section_over_budget_is_indexed_and_marked_budget_fallback(
    tmp_path: Path,
) -> None:
    content = "# Demo\n\n## Core\ncore\n\n## JoinQuant Notebook Runtime\n" + (
        "provider runtime payload that cannot fit\n" * 900
    )

    source = _collect(tmp_path, content, focus="JoinQuant notebook runtime provider")

    assert source.selection is not None
    indexed = next(
        entry
        for entry in source.selection.indexed_only_sections
        if entry.heading == "JoinQuant Notebook Runtime"
    )
    assert indexed.reasons == ("budget_fallback",)
    assert "provider runtime payload" not in source.content
    aggregate = len(source.content.encode("utf-8")) + len(
        render_agents_selection_audit(source.selection).encode("utf-8")
    )
    assert aggregate <= AGENTS_LIMIT_BYTES


def test_unclosed_fence_uses_explicit_safe_fallback_and_is_deterministic(
    tmp_path: Path,
) -> None:
    content = "# Demo\n\n```markdown\n# not safely parseable\n" + ("payload\n" * 1_000)

    first = _collect(tmp_path, content, focus="not safely parseable")
    first_repo = tmp_path / "repo"
    second = collect_project_context(first_repo, focus="not safely parseable").instructions

    assert first == second
    assert first.selection is not None
    assert first.selection.parse_fallback is True
    assert first.selection.truncated is True
    assert len(first.content.encode("utf-8")) <= AGENTS_HEAD_LIMIT_BYTES
    assert "Heading parsing was unsafe" in render_agents_selection_audit(first.selection)


def test_agents_budget_constant_and_legacy_collection_call_remain_compatible(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Demo\nlegacy call\n", encoding="utf-8")

    project = collect_project_context(repo)

    assert AGENTS_LIMIT_BYTES == 16 * 1024
    assert project.instructions.content == "# Demo\nlegacy call\n"
