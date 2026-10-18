from __future__ import annotations

from pathlib import Path

import pytest

from context_loader import agents
from context_loader.collect import (
    AGENTS_HEAD_LIMIT_BYTES,
    AGENTS_LIMIT_BYTES,
    AGENTS_SCAN_LIMIT_BYTES,
    _parse_markdown_sections,
    collect_project_context,
    render_agents_selection_audit,
)
from context_loader.model import Availability


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


def _heading_dense_agents(headings: int) -> str:
    parts = ["# Root\n\nIntro about authentication.\n\n"]
    for index in range(1, headings + 1):
        parts.append(
            f"## Section {index} authentication policy\n\n"
            f"Rules about authentication and session handling for module {index}.\n"
            + ("filler text line about authentication\n" * 3)
            + "\n"
        )
    return "".join(parts)


def _aggregate(source) -> int:
    assert source.selection is not None
    return len(source.content.encode("utf-8")) + len(
        render_agents_selection_audit(source.selection).encode("utf-8")
    )


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


@pytest.mark.parametrize(
    ("focus", "heading"),
    [
        ("安全", "安全规则"),
        ("部署", "部署"),
        ("测试", "测试流程"),
        ("ci", "ＣＩ Checks"),
        *[
            (word, f"{word.upper()} Rules")
            for word in ("cd", "ui", "ux", "io", "db", "qa", "ai", "ml", "pr", "os", "js", "ts")
        ],
    ],
)
def test_short_focus_selects_late_chinese_and_technical_headings(
    tmp_path: Path, focus: str, heading: str
) -> None:
    content = (
        "# Demo\n\n## Core\nKeep the invariant.\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + f"\n## {heading}\nSHORT_FOCUS_TARGET\n"
    )
    assert len(content[: content.index(f"## {heading}")].encode("utf-8")) > AGENTS_LIMIT_BYTES

    source = _collect(tmp_path, content, focus=focus)
    repeated = collect_project_context(tmp_path / "repo", focus=focus).instructions

    assert "SHORT_FOCUS_TARGET" in source.content
    assert "focus_match" in _entry(source, heading).reasons
    assert source == repeated
    assert _aggregate(source) <= AGENTS_LIMIT_BYTES


@pytest.mark.parametrize(
    ("path", "heading"), [("src/安全/handler.py", "安全规则"), ("src/ci/handler.py", "CI Checks")]
)
def test_short_path_component_selects_a_late_heading(
    tmp_path: Path, path: str, heading: str
) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + f"\n## {heading}\nSHORT_PATH_TARGET\n"
    )

    source = _collect(tmp_path, content, path=path)

    assert path not in content
    assert "SHORT_PATH_TARGET" in source.content
    assert "path_match" in _entry(source, heading).reasons


def test_short_focus_also_matches_section_bodies(tmp_path: Path) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + "\n## Operational Rules\n安全策略、部署流程、测试流程和 CI checks.\nBODY_TARGET\n"
    )

    source = _collect(tmp_path, content, focus="安全 部署 测试 CI")

    assert "BODY_TARGET" in source.content
    assert "focus_match" in _entry(source, "Operational Rules").reasons


@pytest.mark.parametrize(
    ("focus", "body"),
    [
        ("future leakage", "Prevent future leakage before accepting a result."),
        ("cache invalidation drift", "Check cache invalidation and drift."),
        ("安全 部署", "遵循安全策略和部署流程。"),
        ("CI QA", "Require CI checks and QA approval."),
    ],
)
def test_distinct_body_focus_terms_select_a_late_section(
    tmp_path: Path, focus: str, body: str
) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + f"\n## Operational Rules\n{body}\nBODY_FOCUS_TARGET\n"
    )

    source = _collect(tmp_path, content, focus=focus)

    assert "BODY_FOCUS_TARGET" in source.content
    assert "focus_match" in _entry(source, "Operational Rules").reasons
    assert _aggregate(source) <= AGENTS_LIMIT_BYTES


@pytest.mark.parametrize("focus", ["future", "future FUTURE future"])
def test_one_distinct_body_focus_term_does_not_select_a_late_section(
    tmp_path: Path, focus: str
) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + "\n## Operational Rules\n"
        + "future leakage can affect a result.\n" * 10
        + "INCIDENTAL_BODY_NOT_LOADED\n"
    )

    source = _collect(tmp_path, content, focus=focus)

    assert "INCIDENTAL_BODY_NOT_LOADED" not in source.content


def test_body_focus_terms_must_match_the_same_section(tmp_path: Path) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + "\n## First Gate\nfuture result\nFIRST_BODY_NOT_LOADED\n"
        "\n## Second Gate\nleakage check\nSECOND_BODY_NOT_LOADED\n"
    )

    source = _collect(tmp_path, content, focus="future leakage")

    assert "FIRST_BODY_NOT_LOADED" not in source.content
    assert "SECOND_BODY_NOT_LOADED" not in source.content


def test_heading_focus_still_out_ranks_a_body_only_match(tmp_path: Path) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + "\n## Operational Rules\nfuture leakage alignment\nBODY_RULE_NOT_LOADED\n"
        + "filler line\n" * 800
        + "\n## Future Analysis\nHEADING_RULE_REQUIRED\n"
        + "filler line\n" * 800
    )

    source = _collect(tmp_path, content, focus="future leakage alignment")

    assert "HEADING_RULE_REQUIRED" in source.content
    assert "BODY_RULE_NOT_LOADED" not in source.content
    assert source.selection is not None
    assert next(
        entry
        for entry in source.selection.indexed_only_sections
        if entry.heading == "Operational Rules"
    ).reasons == ("budget_fallback",)
    assert _aggregate(source) <= AGENTS_LIMIT_BYTES


def test_body_focus_match_over_budget_remains_indexed(tmp_path: Path) -> None:
    content = "# Demo\n\n## Operational Rules\n" + "future leakage payload\n" * 1000

    source = _collect(tmp_path, content, focus="future leakage")

    assert "future leakage payload" not in source.content
    assert source.selection is not None
    assert next(
        entry
        for entry in source.selection.indexed_only_sections
        if entry.heading == "Operational Rules"
    ).reasons == ("budget_fallback",)
    assert _aggregate(source) <= AGENTS_LIMIT_BYTES


def test_common_research_path_term_does_not_select_unrelated_sections(tmp_path: Path) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + "\n## Research Notebook Imports\nUNRELATED_NOTEBOOK_BODY\n"
        "\n## Risk Validation\nRISK_RULE_REQUIRED\n"
    )

    source = _collect(tmp_path, content, path="packages/research/risk/validator.py")

    assert "RISK_RULE_REQUIRED" in source.content
    assert "path_match" in _entry(source, "Risk Validation").reasons
    assert "UNRELATED_NOTEBOOK_BODY" not in source.content


def test_explicit_research_focus_remains_eligible(tmp_path: Path) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + "\n## Research Notebook Imports\nRESEARCH_RULE_REQUIRED\n"
    )

    source = _collect(tmp_path, content, focus="research")

    assert "RESEARCH_RULE_REQUIRED" in source.content
    assert "focus_match" in _entry(source, "Research Notebook Imports").reasons


@pytest.mark.parametrize("focus", ["安", "I", "an", "to", "42", "x2", "and"])
def test_generic_short_words_and_stopwords_do_not_select_late_sections(
    tmp_path: Path, focus: str
) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + f"\n## {focus}\nUNRELATED_SHORT_BODY\n"
    )

    source = _collect(tmp_path, content, focus=focus)

    assert "UNRELATED_SHORT_BODY" not in source.content
    assert source.selection is not None
    assert focus in {entry.heading for entry in source.selection.indexed_only_sections}


def test_latin_abbreviations_do_not_match_inside_longer_words(tmp_path: Path) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + "\n## Special Circuit Collision\nFALSE_POSITIVE_BODY\n"
        "\n## CI Checks\nCI_TARGET\n"
    )

    source = _collect(tmp_path, content, focus="CI")

    assert "CI_TARGET" in source.content
    assert "FALSE_POSITIVE_BODY" not in source.content


@pytest.mark.parametrize("focus", ["安全", "CI"])
def test_short_focus_keeps_oversized_sections_in_the_budget_fallback_index(
    tmp_path: Path, focus: str
) -> None:
    content = f"# Demo\n\n## {focus} Rules\n" + "OVERSIZED_SHORT_RULE\n" * 1000

    source = _collect(tmp_path, content, focus=focus)

    assert "OVERSIZED_SHORT_RULE" not in source.content
    assert source.selection is not None
    assert next(
        entry
        for entry in source.selection.indexed_only_sections
        if entry.heading == f"{focus} Rules"
    ).reasons == ("budget_fallback",)
    assert _aggregate(source) <= AGENTS_LIMIT_BYTES


def test_repeated_han_term_does_not_out_rank_an_earlier_equally_relevant_section(
    tmp_path: Path,
) -> None:
    content = (
        "# Demo\n\n## Large Unrelated\n"
        + "unrelated filler line\n" * 1000
        + "\n## 安全规范\nFIRST_RULE\n"
        + "filler line\n" * 800
        + "\n## 安全规则\n安全策略\nSECOND_RULE\n"
        + "filler line\n" * 800
    )

    source = _collect(tmp_path, content, focus="安全")

    assert "FIRST_RULE" in source.content
    assert "SECOND_RULE" not in source.content
    assert source.selection is not None
    assert next(
        entry for entry in source.selection.indexed_only_sections if entry.heading == "安全规则"
    ).reasons == ("budget_fallback",)
    assert _aggregate(source) <= AGENTS_LIMIT_BYTES


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


def test_selection_does_not_rebuild_the_entire_index_for_each_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    headings = 800
    content = _heading_dense_agents(headings)
    assert len(content.encode("utf-8")) <= AGENTS_SCAN_LIMIT_BYTES
    renders = 0
    entries_processed = 0
    original = agents.render_agents_selection_audit

    def counting(audit):
        nonlocal renders, entries_processed
        renders += 1
        entries_processed += len(audit.selected_sections) + len(audit.indexed_only_sections)
        return original(audit)

    monkeypatch.setattr(agents, "render_agents_selection_audit", counting)

    source = _collect(tmp_path, content, focus="authentication session policy module")

    # Count actual index work as well as calls: a bounded call count per candidate
    # still allowed quadratic work when each call walked all headings.
    assert renders <= 7
    assert entries_processed <= 2 * (headings + 1)
    assert source.selection is not None
    assert source.selection.index_truncated is True
    assert _aggregate(source) <= AGENTS_LIMIT_BYTES


def test_heading_dense_head_degrades_instead_of_failing_collection(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# A\n" * 1_024, encoding="utf-8")
    (repo / "README.md").write_text("# Overview\n", encoding="utf-8")

    project = collect_project_context(repo)

    source = project.instructions
    assert source.reason is Availability.PRESENT
    assert source.selection is not None
    assert source.selection.parse_fallback is False
    assert source.selection.indexed_only_sections == ()
    assert source.content.startswith("# A\n")
    assert len(source.content.encode("utf-8")) <= AGENTS_HEAD_LIMIT_BYTES
    assert _aggregate(source) <= AGENTS_LIMIT_BYTES
    assert project.overview.content == "# Overview\n"


def test_leading_byte_order_mark_does_not_hide_the_first_heading(tmp_path: Path) -> None:
    content = "\ufeff# Project Identity\n\nintro\n\n## Core Rules\nKeep the invariant.\n"

    source = _collect(tmp_path, content)

    assert source.content == content[1:]
    assert source.selection is not None
    assert [entry.heading for entry in source.selection.selected_sections] == [
        "Project Identity",
        "Core Rules",
    ]
    assert source.selection.chars_omitted == 0


def test_truncated_source_scan_reports_the_whole_omitted_source(tmp_path: Path) -> None:
    content = "X" * (AGENTS_SCAN_LIMIT_BYTES + 4_096)

    source = _collect(tmp_path, content)

    assert source.selection is not None
    assert source.selection.source_scan_truncated is True
    assert source.selection.truncated is True
    assert source.selection.chars_omitted == len(content) - source.selection.chars_selected
    assert source.selection.chars_omitted > 0
