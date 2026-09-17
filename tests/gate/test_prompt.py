"""
Tests for bracket-token prompt renderer.

Covers: AC-11 (prompt template substitution matches bash).
"""

from __future__ import annotations

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


class TestBracketTokenRenderer:
    """AC-11: Bracket-token renderer matches bash sed substitution."""

    @REQUIRES_IMPL
    def test_basic_substitution(self) -> None:
        from heddle.gate.prompt import render_template

        template = "Feature: [feature]\nSpec: [spec-path]\nPlan: [plan-path]"
        variables = {
            "feature": "my-feature",
            "spec-path": "docs/features/data/my-feature.md",
            "plan-path": "plans/my-feature.plan.md",
        }
        result = render_template(template, variables)
        assert (
            result == "Feature: my-feature\n"
            "Spec: docs/features/data/my-feature.md\n"
            "Plan: plans/my-feature.plan.md"
        )

    @REQUIRES_IMPL
    def test_all_known_tokens_substituted(self) -> None:
        from heddle.gate.prompt import render_template

        all_tokens = [
            "feature",
            "spec-path",
            "plan-path",
            "milestone",
            "tier",
            "cli-tool",
            "review-file-suffix",
            "review-correctness-path",
            "review-architecture-path",
            "review-robustness-path",
            "review-tests-path",
            "claude-spec-review-path",
            "codex-spec-review-path",
            "claude-plan-review-path",
            "codex-plan-review-path",
            "claude-test-scaffolding-review-path",
            "codex-test-scaffolding-review-path",
        ]
        template = " ".join(f"[{t}]" for t in all_tokens)
        variables = {t: f"value-{t}" for t in all_tokens}
        result = render_template(template, variables)
        assert "[" not in result  # all tokens replaced

    @REQUIRES_IMPL
    def test_validation_section_stripped(self) -> None:
        from heddle.gate.prompt import render_template

        template = (
            "# Review\n\nContent here.\n\n## Validation\n\n"
            "Maintainer-only content.\nMore."
        )
        result = render_template(template, {}, strip_validation=True)
        assert "## Validation" not in result
        assert "Maintainer-only" not in result
        assert "Content here." in result

    @REQUIRES_IMPL
    def test_unknown_tokens_left_intact(self) -> None:
        from heddle.gate.prompt import render_template

        template = "Hello [unknown-token] world"
        result = render_template(template, {})
        # Unknown tokens should NOT be silently dropped
        assert "[unknown-token]" in result
