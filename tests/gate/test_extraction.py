"""
Tests for markdown section extraction (replaces 6 awk scripts).

Covers extraction functions: extract_milestone_section,
extract_acceptance_criteria_blocks, extract_feature_spec_ac_ids,
extract_markdown_section.
"""

from __future__ import annotations

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


class TestMilestoneExtraction:
    @REQUIRES_IMPL
    def test_extract_milestone_section_by_number(
        self, sample_plan_content: str
    ) -> None:
        from heddle.gate.extraction import extract_milestone_section

        section = extract_milestone_section(sample_plan_content, "2")
        assert "### Milestone 2:" in section
        assert "Satisfies: AC-3, AC-4" in section
        assert "### Milestone 1:" not in section

    @REQUIRES_IMPL
    def test_extract_milestone_section_missing(self, sample_plan_content: str) -> None:
        from heddle.gate.extraction import extract_milestone_section

        section = extract_milestone_section(sample_plan_content, "99")
        assert section == ""

    @REQUIRES_IMPL
    def test_lookalike_heading_does_not_shadow_exact_milestone(self) -> None:
        from textwrap import dedent

        from heddle.gate.extraction import (
            extract_milestone_section,
        )

        content = dedent("""\
            ### Milestone m1 context

            Narrative context, not the milestone contract.

            ### Milestone m1: Exact contract

            Satisfies: AC-1

            ### Milestone m2: Next contract
            """)

        section = extract_milestone_section(content, "m1")
        assert section.startswith("### Milestone m1: Exact contract")
        assert "Narrative context" not in section


class TestSpecExtraction:
    @REQUIRES_IMPL
    def test_extract_feature_spec_ac_ids(self, sample_spec_content: str) -> None:
        from heddle.gate.extraction import extract_feature_spec_ac_ids

        ids = extract_feature_spec_ac_ids(sample_spec_content)
        assert ids == ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]

    @REQUIRES_IMPL
    def test_extract_acceptance_criteria_blocks(self, sample_spec_content: str) -> None:
        from heddle.gate.extraction import extract_acceptance_criteria_blocks

        blocks = extract_acceptance_criteria_blocks(
            sample_spec_content, ["AC-1", "AC-3"]
        )
        assert "### AC-1:" in blocks
        assert "### AC-3:" in blocks
        assert "### AC-2:" not in blocks


class TestMarkdownSection:
    @REQUIRES_IMPL
    def test_extract_h2_section(self, sample_spec_content: str) -> None:
        from heddle.gate.extraction import extract_markdown_section

        section = extract_markdown_section(sample_spec_content, "## Purpose")
        assert "sample feature for testing" in section.lower()
        assert "## Acceptance Criteria" not in section

    @REQUIRES_IMPL
    def test_extract_nested_section_respects_level(
        self, sample_plan_content: str
    ) -> None:
        from heddle.gate.extraction import extract_markdown_section

        section = extract_markdown_section(sample_plan_content, "## Milestones")
        assert "### Milestone 1:" in section
        assert "### Milestone 2:" in section

    def test_fenced_heading_is_content_not_boundary(self) -> None:
        # review-write finding 1: free-form section bodies (Decision Log
        # prose) legitimately carry fenced markdown samples; a fenced
        # `## ` line is content, not a section boundary.
        from heddle.gate.extraction import extract_markdown_section

        content = (
            "## Decision Log\n\n"
            "### D-1: Layout\n\n"
            "Rejected sample:\n\n"
            "```markdown\n"
            "## Fake Heading\n"
            "body\n"
            "```\n\n"
            "### D-2: Second decision\n\nKept.\n\n"
            "## Outcomes\n\nShipped.\n"
        )
        section = extract_markdown_section(content, "## Decision Log")
        assert "## Fake Heading" in section, "fenced heading truncated the slice"
        assert "### D-2: Second decision" in section
        assert section.count("```") == 2, "unbalanced fence in the slice"
        assert "## Outcomes" not in section

    def test_fenced_lookalike_heading_does_not_start_a_section(self) -> None:
        from heddle.gate.extraction import extract_markdown_section

        content = (
            "## Intro\n\n"
            "```\n"
            "## Decision Log\n"
            "fenced lookalike\n"
            "```\n\n"
            "## Decision Log\n\nReal body.\n\n"
            "## Tail\n\nx.\n"
        )
        section = extract_markdown_section(content, "## Decision Log")
        assert "Real body." in section
        assert "fenced lookalike" not in section


class TestExtractionDriftSensitivity:
    """Non-tautological format-drift guards for the parsers whose silent
    under-extraction would mislead preflight (the failure shape behind the
    finding-extractor telemetry bug). Each fixture spans a realistic format
    variation and asserts the exact set, so a regression that drops one entry
    fails the test."""

    @REQUIRES_IMPL
    def test_milestone_section_resolves_m_prefix_and_bounds(self) -> None:
        from textwrap import dedent

        from heddle.gate.extraction import extract_milestone_section

        content = dedent("""\
            ### M2: Integration

            Satisfies: AC-3

            ### M3: Polish

            Satisfies: AC-5
            """)
        section = extract_milestone_section(content, "2")
        assert "### M2: Integration" in section
        assert "AC-3" in section
        # Boundary: the next milestone's content must be excluded.
        assert "M3" not in section
        assert "AC-5" not in section
