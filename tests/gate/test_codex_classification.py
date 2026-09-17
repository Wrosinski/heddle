"""
Tests for Codex facts-based classification cascade.

Covers: AC-9 (partial — Codex classification ordering).
Tests the rule walker as a pure function over CodexFacts,
with no file I/O.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


def _make_facts(**overrides):
    """Create CodexFacts with sensible defaults, overriding specific fields."""
    from heddle.gate.runners.codex import CodexFacts

    base = CodexFacts(
        timed_out=False,
        inactive=False,
        cli_exit=0,
        has_tool_events=True,
        has_message_events=True,
        has_turn_completed=True,
        last_message_nonempty=True,
        json_message_nonempty=True,
        model_unsupported=False,
        transport_error=False,
    )
    return replace(base, **overrides)


class TestCodexClassification:
    """Tests that the ordered rule list produces correct FailureReason
    for each combination of CodexFacts."""

    @REQUIRES_IMPL
    def test_success(self) -> None:
        from heddle.gate.runners.codex import classify_codex

        assert classify_codex(_make_facts()) is None

    @REQUIRES_IMPL
    def test_inactivity_no_final_message(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(
            inactive=True, has_tool_events=True, has_message_events=False
        )
        assert classify_codex(facts) is FailureReason.INACTIVITY_NO_FINAL

    @REQUIRES_IMPL
    def test_inactivity_timeout(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(inactive=True, has_tool_events=False)
        assert classify_codex(facts) is FailureReason.INACTIVITY_TIMEOUT

    @REQUIRES_IMPL
    def test_timeout(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(timed_out=True)
        assert classify_codex(facts) is FailureReason.TIMEOUT

    @REQUIRES_IMPL
    def test_unsupported_model(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(cli_exit=1, model_unsupported=True)
        assert classify_codex(facts) is FailureReason.UNSUPPORTED_MODEL

    @REQUIRES_IMPL
    def test_transport_error(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(cli_exit=1, transport_error=True)
        assert classify_codex(facts) is FailureReason.TRANSPORT_ERROR

    @REQUIRES_IMPL
    def test_nonzero_exit(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(cli_exit=1)
        assert classify_codex(facts) is FailureReason.NONZERO_EXIT

    @REQUIRES_IMPL
    def test_output_last_message_empty(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(last_message_nonempty=False, json_message_nonempty=True)
        assert classify_codex(facts) is FailureReason.OUTPUT_LAST_MSG_EMPTY

    @REQUIRES_IMPL
    def test_missing_final_message(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(last_message_nonempty=False, json_message_nonempty=False)
        assert classify_codex(facts) is FailureReason.MISSING_FINAL_MESSAGE

    @REQUIRES_IMPL
    def test_missing_message_event(self) -> None:
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(has_message_events=False)
        assert classify_codex(facts) is FailureReason.MISSING_MESSAGE_EVENT

    @REQUIRES_IMPL
    def test_priority_inactivity_over_timeout(self) -> None:
        """Inactivity is checked before timeout in the rule order."""
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(inactive=True, timed_out=True)
        result = classify_codex(facts)
        assert result in (
            FailureReason.INACTIVITY_NO_FINAL,
            FailureReason.INACTIVITY_TIMEOUT,
        )

    @REQUIRES_IMPL
    def test_priority_model_unsupported_over_transport(self) -> None:
        """Unsupported model is checked before transport error."""
        from heddle.gate.runners.codex import classify_codex
        from heddle.gate.types import FailureReason

        facts = _make_facts(cli_exit=1, model_unsupported=True, transport_error=True)
        assert classify_codex(facts) is FailureReason.UNSUPPORTED_MODEL
