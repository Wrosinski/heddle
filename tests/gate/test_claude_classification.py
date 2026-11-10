"""
Tests for Claude error classification.

Covers classify_claude() — direct if/elif with 6 conditions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


class TestClaudeClassification:
    @REQUIRES_IMPL
    def test_success(self, tmp_path: Path) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.io.process import MonitorResult, Termination

        monitor = MonitorResult(
            exit_code=0,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )
        result_events = [{"type": "result", "subtype": "success", "result": "output"}]
        assert classify_claude(monitor, tmp_path / "raw.out", result_events) is None

    @REQUIRES_IMPL
    def test_inactivity_timeout(self, tmp_path: Path) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason
        from heddle.io.process import MonitorResult, Termination

        monitor = MonitorResult(
            exit_code=118,
            termination=Termination.INACTIVITY,
            saw_stream_activity=True,
            inactivity_seconds=900,
            inactivity_marker="1000:123456",
            checkpoints=[],
        )
        assert (
            classify_claude(monitor, tmp_path / "raw.out", [])
            is FailureReason.INACTIVITY_TIMEOUT
        )

    @REQUIRES_IMPL
    def test_hard_timeout(self, tmp_path: Path) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason
        from heddle.io.process import MonitorResult, Termination

        monitor = MonitorResult(
            exit_code=124,
            termination=Termination.TIMEOUT,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )
        assert (
            classify_claude(monitor, tmp_path / "raw.out", []) is FailureReason.TIMEOUT
        )

    @REQUIRES_IMPL
    def test_overloaded(self, tmp_path: Path) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason
        from heddle.io.process import MonitorResult, Termination

        raw = tmp_path / "raw.out"
        raw.write_text('{"error_status":529,"message":"Overloaded"}')
        monitor = MonitorResult(
            exit_code=1,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )
        assert classify_claude(monitor, raw, []) is FailureReason.OVERLOADED

    @REQUIRES_IMPL
    @pytest.mark.parametrize(
        "message",
        (
            "Error: unsupported model claude-typo",
            "Model claude-typo is not available for this account",
            "You don't have access to the model claude-typo",
        ),
    )
    def test_unsupported_model(self, message: str, tmp_path: Path) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason
        from heddle.io.process import MonitorResult, Termination

        raw = tmp_path / "raw.out"
        raw.write_text(message, encoding="utf-8")
        monitor = MonitorResult(
            exit_code=1,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )
        assert classify_claude(monitor, raw, []) is FailureReason.UNSUPPORTED_MODEL

    @REQUIRES_IMPL
    def test_model_adjacent_review_prose_does_not_masquerade_as_rejection(
        self, tmp_path: Path
    ) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason
        from heddle.io.process import MonitorResult, Termination

        raw = tmp_path / "raw.out"
        raw.write_text(
            "The reviewed model design is sound, but one fixture was not found.",
            encoding="utf-8",
        )
        monitor = MonitorResult(
            exit_code=1,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )

        assert classify_claude(monitor, raw, []) is FailureReason.NONZERO_EXIT

    @REQUIRES_IMPL
    def test_budget_exhausted_from_stream(self, tmp_path: Path) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason
        from heddle.io.process import MonitorResult, Termination

        monitor = MonitorResult(
            exit_code=0,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )
        events = [{"type": "result", "subtype": "error_max_budget"}]
        assert (
            classify_claude(monitor, tmp_path / "raw.out", events)
            is FailureReason.BUDGET_EXHAUSTED
        )

    @REQUIRES_IMPL
    def test_max_turns(self, tmp_path: Path) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason
        from heddle.io.process import MonitorResult, Termination

        monitor = MonitorResult(
            exit_code=0,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )
        events = [{"type": "result", "subtype": "error_max_turns"}]
        assert (
            classify_claude(monitor, tmp_path / "raw.out", events)
            is FailureReason.MAX_TURNS
        )


def _completed(exit_code: int = 0):
    from heddle.io.process import MonitorResult, Termination

    return MonitorResult(
        exit_code=exit_code,
        termination=Termination.COMPLETED,
        saw_stream_activity=True,
        inactivity_seconds=0,
        inactivity_marker="",
        checkpoints=[],
    )


SUCCESS = {"type": "result", "subtype": "success", "is_error": False}
REFUSED_TURN = {
    "type": "assistant",
    "parent_tool_use_id": None,
    "message": {"model": "claude-fable-5-2", "stop_reason": "refusal"},
}
NO_FALLBACK = {
    "type": "system",
    "subtype": "model_refusal_no_fallback",
    "original_model": "claude-fable-5-2",
}


class TestClaudeRefusal:
    @pytest.mark.parametrize(
        "events",
        (
            [REFUSED_TURN, SUCCESS],
            [NO_FALLBACK, SUCCESS],
            [REFUSED_TURN, NO_FALLBACK, {**SUCCESS, "is_error": True}],
        ),
    )
    @pytest.mark.parametrize("exit_code", (0, 1))
    def test_a_refusal_fails_the_call(
        self, tmp_path: Path, events: list[dict], exit_code: int
    ) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason

        assert (
            classify_claude(_completed(exit_code), tmp_path / "raw.out", events)
            is FailureReason.REFUSAL
        )

    @pytest.mark.parametrize("subtype", ("model_refusal_fallback", "model_fallback"))
    def test_another_model_answering_fails_even_a_successful_call(
        self, tmp_path: Path, subtype: str
    ) -> None:
        from heddle.gate.runners.claude import classify_claude
        from heddle.gate.types import FailureReason

        switch = {
            "type": "system",
            "subtype": subtype,
            "original_model": "claude-fable-5-2",
            "fallback_model": "claude-opus-5-5",
        }
        events = [switch, {**SUCCESS, "structured_output": {"role": "spec-review"}}]
        assert (
            classify_claude(_completed(), tmp_path / "raw.out", events)
            is FailureReason.MODEL_SUBSTITUTED
        )

    def test_refusal_words_in_review_text_or_a_subagent_do_not_fail_the_call(
        self, tmp_path: Path
    ) -> None:
        from heddle.gate.runners.claude import classify_claude

        raw = tmp_path / "raw.out"
        raw.write_text('"stop_reason": "refusal" model_refusal_no_fallback\n')
        events = [
            {
                "type": "assistant",
                "message": {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "a refusal path"}],
                },
            },
            {**REFUSED_TURN, "parent_tool_use_id": "toolu_1"},
            {**SUCCESS, "result": "the model_refusal_fallback event"},
        ]
        assert classify_claude(_completed(), raw, events) is None
