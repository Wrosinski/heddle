"""
Tests for Codex stream diagnostics.

Covers: AC-4 (JSONL quality), AC-5 (event timeline), AC-6 (message reconciliation),
AC-7 (diagnosis categories).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


class TestJsonlParsing:
    """AC-4: JSONL parsing produces quality metrics."""

    @REQUIRES_IMPL
    def test_clean_stream_no_errors(
        self, tmp_path: Path, clean_codex_jsonl: str
    ) -> None:
        from heddle.gate.runners.codex_diagnostics import parse_jsonl

        raw = tmp_path / "raw.out"
        raw.write_text(clean_codex_jsonl)
        result = parse_jsonl(raw)

        assert result.parse_errors == 0
        assert result.non_json_lines == 0
        assert result.json_lines_parsed == len(clean_codex_jsonl.strip().split("\n"))
        assert len(result.events) == result.json_lines_parsed

    @REQUIRES_IMPL
    def test_corrupted_stream_tracks_errors(
        self, tmp_path: Path, corrupted_codex_jsonl: str
    ) -> None:
        from heddle.gate.runners.codex_diagnostics import parse_jsonl

        raw = tmp_path / "raw.out"
        raw.write_text(corrupted_codex_jsonl)
        result = parse_jsonl(raw)

        assert result.parse_errors == 1  # truncated JSON line
        assert result.non_json_lines == 2  # "Reconnecting..." and "Transport error..."
        assert len(result.diagnostic_lines) >= 2
        assert any("Reconnecting" in d for d in result.diagnostic_lines)
        assert any("Transport error" in d for d in result.diagnostic_lines)

    @REQUIRES_IMPL
    def test_empty_stream(self, tmp_path: Path, empty_codex_jsonl: str) -> None:
        from heddle.gate.runners.codex_diagnostics import parse_jsonl

        raw = tmp_path / "raw.out"
        raw.write_text(empty_codex_jsonl)
        result = parse_jsonl(raw)

        assert result.total_lines == 0
        assert result.json_lines_parsed == 0
        assert len(result.events) == 0

    @REQUIRES_IMPL
    def test_error_samples_truncated(self, tmp_path: Path) -> None:
        from heddle.gate.runners.codex_diagnostics import parse_jsonl

        long_bad_line = '{"broken": "' + "x" * 500
        raw = tmp_path / "raw.out"
        raw.write_text(long_bad_line + "\n")
        result = parse_jsonl(raw)

        assert result.parse_errors == 1
        assert len(result.error_samples) == 1
        assert len(result.error_samples[0]) <= 200


class TestEventTimeline:
    """AC-5: Event timeline maps to monitor checkpoints."""

    @REQUIRES_IMPL
    def test_timeline_from_checkpoints(
        self, tmp_path: Path, clean_codex_jsonl: str
    ) -> None:
        from heddle.gate.runners.codex_diagnostics import build_timeline, parse_jsonl
        from heddle.io.process import MonitorCheckpoint

        raw = tmp_path / "raw.out"
        raw.write_text(clean_codex_jsonl)
        parse = parse_jsonl(raw)

        checkpoints = [
            MonitorCheckpoint(offset_bytes=offset, monotonic_s=10.0 + index)
            for index, offset in enumerate(parse.event_offsets, start=0)
        ]
        launch_time = 5.0
        termination_time = 25.0

        timeline = build_timeline(parse, checkpoints, launch_time, termination_time)

        assert timeline.first_event_offset_s == 5.0
        assert timeline.last_event_offset_s == 10.0
        assert timeline.last_tool_event_offset_s == 8.0
        assert timeline.last_message_event_offset_s == 9.0
        assert timeline.turn_completed_offset_s == 10.0
        assert timeline.gap_before_termination_s == 10.0

    @REQUIRES_IMPL
    def test_empty_stream_timeline(self, tmp_path: Path) -> None:
        from heddle.gate.runners.codex_diagnostics import build_timeline, parse_jsonl

        raw = tmp_path / "raw.out"
        raw.write_text("")
        parse = parse_jsonl(raw)

        timeline = build_timeline(
            parse, checkpoints=[], launch_time=0, termination_time=10
        )
        assert timeline.first_event_offset_s is None
        assert timeline.last_event_offset_s is None


class TestMessageReconciliation:
    """AC-6: Message source reconciliation detects discrepancies."""

    @REQUIRES_IMPL
    def test_sources_agree(self, tmp_path: Path, clean_codex_jsonl: str) -> None:
        from heddle.gate.runners.codex_diagnostics import (
            parse_jsonl,
            reconcile_messages,
        )

        raw = tmp_path / "raw.out"
        raw.write_text(clean_codex_jsonl)
        parse = parse_jsonl(raw)

        # Write -o file with same content as last JSONL message
        last_msg = [
            e
            for e in parse.events
            if e.get("type") == "item.completed"
            and e.get("item", {}).get("type") in ("agent_message", "assistant_message")
        ][-1]["item"]["text"]
        o_file = tmp_path / "msg.txt"
        o_file.write_text(last_msg)

        extraction = reconcile_messages(o_file, parse)
        assert extraction.sources_agree is True
        assert extraction.source_used == "output-file"
        assert extraction.warnings == []

    @REQUIRES_IMPL
    def test_sources_disagree_empty_o_file(
        self, tmp_path: Path, clean_codex_jsonl: str
    ) -> None:
        from heddle.gate.runners.codex_diagnostics import (
            parse_jsonl,
            reconcile_messages,
        )

        raw = tmp_path / "raw.out"
        raw.write_text(clean_codex_jsonl)
        parse = parse_jsonl(raw)

        o_file = tmp_path / "msg.txt"
        o_file.write_text("")  # empty

        extraction = reconcile_messages(o_file, parse)
        assert extraction.sources_agree is False
        assert extraction.output_file_bytes == 0
        assert extraction.jsonl_message_count >= 1
        assert len(extraction.warnings) > 0

    @REQUIRES_IMPL
    def test_sources_disagree_non_empty_mismatch(
        self, tmp_path: Path, clean_codex_jsonl: str
    ) -> None:
        from heddle.gate.runners.codex_diagnostics import (
            parse_jsonl,
            reconcile_messages,
        )

        raw = tmp_path / "raw.out"
        raw.write_text(clean_codex_jsonl)
        parse = parse_jsonl(raw)

        o_file = tmp_path / "msg.txt"
        o_file.write_text("different final message")

        extraction = reconcile_messages(o_file, parse)
        assert extraction.sources_agree is False
        assert extraction.source_used == "output-file"
        assert "output file and JSONL last message differ" in extraction.warnings

    def test_missing_output_diagnostics_do_not_fabricate_a_result(
        self, tmp_path: Path, clean_codex_jsonl: str
    ) -> None:
        from heddle.gate.runners.codex_diagnostics import (
            parse_jsonl,
            reconcile_messages,
        )

        raw = tmp_path / "raw.out"
        raw.write_text(clean_codex_jsonl)
        output = tmp_path / "msg.txt"
        output.write_text("")
        diagnostic = reconcile_messages(output, parse_jsonl(raw))
        assert diagnostic.output_file_bytes == 0
        assert diagnostic.jsonl_message_count > 0
        assert diagnostic.warnings == [
            "output file empty while JSONL message events exist"
        ]
        assert output.read_bytes() == b""
        assert not output.with_suffix(".jsonl-extracted.md").exists()


class TestCodexDiagnosis:
    """AC-7: CodexDiagnosis categorizes all failure scenarios."""

    @REQUIRES_IMPL
    def test_timeout_tool_loop(self) -> None:
        from heddle.gate.runners.codex_diagnostics import diagnose

        d = diagnose(
            inactive=True,
            tool_events=50,
            message_events=0,
            raw_bytes=100000,
            parse_errors=0,
            transport_errors=0,
            sources_agree=True,
            gap_s=600,
        )
        assert d.category == "timeout-tool-loop"
        assert "tool activity" in d.description
        assert d.inspect_artifacts == ["raw_out", "events_jsonl"]
        assert d.recommended_action

    @REQUIRES_IMPL
    def test_timeout_no_stream(self) -> None:
        from heddle.gate.runners.codex_diagnostics import diagnose

        d = diagnose(
            inactive=True,
            tool_events=0,
            message_events=0,
            raw_bytes=0,
            parse_errors=0,
            transport_errors=0,
            sources_agree=True,
            gap_s=900,
        )
        assert d.category == "timeout-no-stream"
        assert "never established" in d.description
        assert d.inspect_artifacts == ["log", "raw_out"]
        assert d.recommended_action

    @REQUIRES_IMPL
    def test_timeout_stall(self) -> None:
        from heddle.gate.runners.codex_diagnostics import diagnose

        d = diagnose(
            inactive=True,
            tool_events=10,
            message_events=1,
            raw_bytes=50000,
            parse_errors=0,
            transport_errors=0,
            sources_agree=True,
            gap_s=900,
        )
        assert d.category == "timeout-stall"
        assert "stalled" in d.description
        assert d.inspect_artifacts == ["raw_out", "events_jsonl"]
        assert d.recommended_action

    @REQUIRES_IMPL
    def test_stream_quality(self) -> None:
        from heddle.gate.runners.codex_diagnostics import diagnose

        d = diagnose(
            inactive=False,
            tool_events=10,
            message_events=1,
            raw_bytes=50000,
            parse_errors=12,
            transport_errors=0,
            sources_agree=True,
            gap_s=0,
        )
        assert d.category == "stream-quality"
        assert "parse errors" in d.description
        assert d.inspect_artifacts == ["raw_out"]
        assert d.recommended_action

    @REQUIRES_IMPL
    def test_completion_path(self) -> None:
        from heddle.gate.runners.codex_diagnostics import diagnose

        d = diagnose(
            inactive=False,
            tool_events=10,
            message_events=1,
            raw_bytes=50000,
            parse_errors=0,
            transport_errors=0,
            sources_agree=False,
            gap_s=0,
        )
        assert d.category == "completion-path"
        assert "disagree" in d.description
        assert d.inspect_artifacts == ["last_message", "json_message", "events_jsonl"]
        assert d.recommended_action

    @REQUIRES_IMPL
    def test_transport(self) -> None:
        from heddle.gate.runners.codex_diagnostics import diagnose

        d = diagnose(
            inactive=False,
            tool_events=10,
            message_events=0,
            raw_bytes=50000,
            parse_errors=0,
            transport_errors=3,
            sources_agree=True,
            gap_s=0,
            failed=True,
        )
        assert d.category == "transport"
        assert "Transport errors" in d.description
        assert d.inspect_artifacts == ["raw_out", "events_jsonl"]
        assert d.recommended_action

    @REQUIRES_IMPL
    def test_clean(self) -> None:
        from heddle.gate.runners.codex_diagnostics import diagnose

        d = diagnose(
            inactive=False,
            tool_events=10,
            message_events=1,
            raw_bytes=50000,
            parse_errors=0,
            transport_errors=0,
            sources_agree=True,
            gap_s=0,
        )
        assert d.category == "clean"
        assert d.description == "No issues detected."
        assert d.inspect_artifacts == []
        assert d.recommended_action == ""

    @REQUIRES_IMPL
    def test_build_bundle_classifies_message_then_stall_as_timeout_stall(
        self, tmp_path: Path, clean_codex_jsonl: str
    ) -> None:
        from heddle.gate.runners.codex_diagnostics import build_bundle, parse_jsonl
        from heddle.io.process import MonitorCheckpoint

        raw = tmp_path / "raw.out"
        raw.write_text(clean_codex_jsonl)
        parse = parse_jsonl(raw)
        filtered = tmp_path / "events.jsonl"
        msg_out = tmp_path / "last-message.txt"
        msg_out.write_text("")
        checkpoints = [
            MonitorCheckpoint(offset_bytes=offset, monotonic_s=10.0 + index)
            for index, offset in enumerate(parse.event_offsets, start=0)
        ]

        bundle = build_bundle(
            raw_out=raw,
            msg_out=msg_out,
            filtered_jsonl=filtered,
            checkpoints=checkpoints,
            launch_time=5.0,
            termination_time=30.0,
            failed=True,
        )

        assert bundle.diagnosis.category == "timeout-stall"
        assert bundle.diagnosis.recommended_action
