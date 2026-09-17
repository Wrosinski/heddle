from __future__ import annotations

import json
import re
from pathlib import Path

from heddle.gate.results import MAX_STREAM_BYTES, read_bounded
from heddle.gate.types import (
    CodexDiagnosis,
    CodexDiagnosticBundle,
    CodexEventInfo,
    CodexEventTimeline,
    JsonlParseResult,
    MessageExtraction,
)
from heddle.io.process import MonitorCheckpoint

TRANSPORT_PATTERN = re.compile(
    r"stream disconnected before completion|"
    r"Transport error: network error|Reconnecting\.\.\."
)


def parse_jsonl(raw_path: Path) -> JsonlParseResult:
    text = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
    lines = text.splitlines()
    events: list[dict[str, object]] = []
    error_samples: list[str] = []
    diagnostic_lines: list[str] = []
    event_offsets: list[int] = []
    json_lines_attempted = 0
    json_lines_parsed = 0
    parse_errors = 0
    non_json_lines = 0
    offset = 0

    for line in lines:
        encoded = (line + "\n").encode()
        if line.startswith("{"):
            json_lines_attempted += 1
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                if len(error_samples) < 5:
                    error_samples.append(line[:200])
            else:
                if isinstance(parsed, dict):
                    events.append(parsed)
                    event_offsets.append(offset + len(encoded))
                    json_lines_parsed += 1
        else:
            non_json_lines += 1
            if line:
                diagnostic_lines.append(line)
        offset += len(encoded)

    return JsonlParseResult(
        events=events,
        total_lines=len(lines),
        json_lines_attempted=json_lines_attempted,
        json_lines_parsed=json_lines_parsed,
        parse_errors=parse_errors,
        non_json_lines=non_json_lines,
        error_samples=error_samples,
        diagnostic_lines=diagnostic_lines,
        event_offsets=event_offsets,
    )


def build_timeline(
    parse: JsonlParseResult,
    checkpoints: list[MonitorCheckpoint],
    launch_time: float,
    termination_time: float,
) -> CodexEventTimeline:
    if not parse.events or not checkpoints:
        return CodexEventTimeline(
            first_event_offset_s=None,
            last_event_offset_s=None,
            last_tool_event_offset_s=None,
            last_message_event_offset_s=None,
            turn_completed_offset_s=None,
            gap_before_termination_s=None,
            last_event_type=None,
        )

    first_time = _event_time(parse.event_offsets[0], checkpoints)
    last_time = _event_time(parse.event_offsets[-1], checkpoints)
    last_tool_time = None
    last_message_time = None
    turn_completed_time = None
    for event, offset in zip(parse.events, parse.event_offsets, strict=False):
        event_time = _event_time(offset, checkpoints)
        event_type = str(event.get("type"))
        item = event.get("item", {})
        item_type = item.get("type") if isinstance(item, dict) else None
        if item_type in {"command_execution", "todo_list"}:
            last_tool_time = event_time
        if item_type in {"agent_message", "assistant_message"}:
            last_message_time = event_time
        if event_type == "turn.completed":
            turn_completed_time = event_time
    return CodexEventTimeline(
        first_event_offset_s=_relative(first_time, launch_time),
        last_event_offset_s=_relative(last_time, launch_time),
        last_tool_event_offset_s=_relative(last_tool_time, launch_time),
        last_message_event_offset_s=_relative(last_message_time, launch_time),
        turn_completed_offset_s=_relative(turn_completed_time, launch_time),
        gap_before_termination_s=(
            round(termination_time - last_time, 3) if last_time is not None else None
        ),
        last_event_type=str(parse.events[-1].get("type")) if parse.events else None,
    )


def reconcile_messages(
    msg_out_path: Path, parse: JsonlParseResult
) -> MessageExtraction:
    messages = _message_texts(parse)
    output_raw = read_bounded(msg_out_path) if msg_out_path.exists() else b""
    # Diagnostic view only: undecodable bytes still reach immutable capture.
    output_text = output_raw.decode("utf-8", errors="replace")
    output_bytes = len(output_raw)
    last_message = messages[-1] if messages else ""
    warnings: list[str] = []
    if output_text.encode("utf-8") != output_raw:
        warnings.append(
            "diagnostic view replaces non-UTF-8 bytes; original output is unchanged"
        )
    sources_agree = bool(output_text) and output_text == last_message
    source_used = "output-file" if output_text else "jsonl-last"

    if not output_text and messages:
        warnings.append("output file empty while JSONL message events exist")
    elif output_text and messages and output_text != last_message:
        warnings.append("output file and JSONL last message differ")

    return MessageExtraction(
        source_used=source_used,
        output_file_bytes=output_bytes,
        jsonl_message_count=len(messages),
        jsonl_last_message_bytes=len(last_message.encode()),
        jsonl_all_messages_bytes=sum(len(message.encode()) for message in messages),
        sources_agree=sources_agree,
        warnings=warnings,
    )


def diagnose(
    *,
    inactive: bool,
    tool_events: int,
    message_events: int,
    raw_bytes: int,
    parse_errors: int,
    transport_errors: int,
    sources_agree: bool,
    gap_s: float,
    failed: bool = False,
) -> CodexDiagnosis:
    if inactive and gap_s > 0 and tool_events > 0 and message_events == 0:
        return _diagnosis(
            "timeout-tool-loop",
            "Codex produced tool activity but no final review message before "
            "timing out.",
            ["raw_out", "events_jsonl"],
            "The model stayed in tool execution without reaching a final answer.",
            "Inspect the prompt and reduce tool churn before retrying.",
        )
    if (
        inactive
        and gap_s > 0
        and tool_events == 0
        and message_events == 0
        and raw_bytes == 0
    ):
        return _diagnosis(
            "timeout-no-stream",
            "Codex never established a usable event stream.",
            ["log", "raw_out"],
            "The CLI stalled before emitting any output.",
            "Check connectivity or authentication, then rerun.",
        )
    if inactive and gap_s > 0 and message_events > 0:
        return _diagnosis(
            "timeout-stall",
            "Codex emitted partial review output and then stalled.",
            ["raw_out", "events_jsonl"],
            "The model stopped making progress after producing some output.",
            "Inspect the last event gap and consider trimming prompt size.",
        )
    if parse_errors > 0:
        return _diagnosis(
            "stream-quality",
            "The Codex JSONL stream contained parse errors.",
            ["raw_out"],
            "Transport or CLI output corruption caused event loss.",
            "Inspect the raw stream before trusting the summary.",
        )
    if not sources_agree:
        return _diagnosis(
            "completion-path",
            "The final message file and JSONL message stream disagree.",
            ["last_message", "json_message", "events_jsonl"],
            "The completion path diverged between Codex output channels.",
            "Inspect both message sources before rerunning.",
        )
    if transport_errors > 0 and failed:
        return _diagnosis(
            "transport",
            "Transport errors interrupted the Codex stream.",
            ["raw_out", "events_jsonl"],
            "Network instability or stream reconnects interrupted completion.",
            "Retry after confirming Codex connectivity.",
        )
    return _diagnosis(
        "clean",
        "No issues detected.",
        [],
        "",
        "",
    )


def build_bundle(
    raw_out: Path,
    msg_out: Path,
    filtered_jsonl: Path,
    checkpoints: list[MonitorCheckpoint],
    launch_time: float | None,
    termination_time: float | None,
    failed: bool,
    parsed: JsonlParseResult | None = None,
) -> CodexDiagnosticBundle:
    parse = parsed if parsed is not None else parse_jsonl(raw_out)
    filtered_jsonl.write_text(
        "".join(json.dumps(event) + "\n" for event in parse.events),
        encoding="utf-8",
    )
    timeline = build_timeline(
        parse,
        checkpoints,
        launch_time or 0.0,
        termination_time or 0.0,
    )
    extraction = reconcile_messages(msg_out, parse)
    diagnosis = diagnose(
        inactive=bool(
            failed
            and timeline.gap_before_termination_s is not None
            and timeline.gap_before_termination_s > 0
        ),
        tool_events=count_tool_events(parse),
        message_events=count_message_events(parse),
        raw_bytes=raw_out.stat().st_size if raw_out.exists() else 0,
        parse_errors=parse.parse_errors,
        transport_errors=count_transport_errors(raw_out, parse),
        sources_agree=extraction.sources_agree,
        gap_s=timeline.gap_before_termination_s or 0.0,
        failed=failed,
    )
    event_info = CodexEventInfo(
        tool_event_count=count_tool_events(parse),
        message_event_count=count_message_events(parse),
        turn_completed_count=count_turn_completed(parse),
        transport_error_count=count_transport_errors(raw_out, parse),
        parse_errors=parse.parse_errors,
        non_json_lines=parse.non_json_lines,
        timeline=timeline,
        message_extraction=extraction,
    )
    return CodexDiagnosticBundle(
        parse_result=parse,
        timeline=timeline,
        extraction=extraction,
        diagnosis=diagnosis,
        event_info=event_info,
    )


def count_tool_events(parse: JsonlParseResult) -> int:
    return sum(
        1
        for event in parse.events
        if event.get("type") in {"item.started", "item.completed"}
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") in {"command_execution", "todo_list"}
    )


def count_message_events(parse: JsonlParseResult) -> int:
    return len(_message_texts(parse))


def count_turn_completed(parse: JsonlParseResult) -> int:
    return sum(1 for event in parse.events if event.get("type") == "turn.completed")


def count_transport_errors(raw_out: Path, parse: JsonlParseResult) -> int:
    text = (
        read_bounded(raw_out, MAX_STREAM_BYTES).decode("utf-8")
        if raw_out.exists()
        else ""
    )
    from_raw = len(TRANSPORT_PATTERN.findall(text))
    if from_raw:
        return from_raw
    return sum(1 for line in parse.diagnostic_lines if TRANSPORT_PATTERN.search(line))


def _message_texts(parse: JsonlParseResult) -> list[str]:
    messages: list[str] = []
    for event in parse.events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"agent_message", "assistant_message"}:
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            messages.append(text)
    return messages


def _event_time(offset: int, checkpoints: list[MonitorCheckpoint]) -> float:
    for checkpoint in checkpoints:
        if checkpoint.offset_bytes >= offset:
            return checkpoint.monotonic_s
    return checkpoints[-1].monotonic_s


def _relative(value: float | None, launch_time: float) -> float | None:
    if value is None:
        return None
    return round(value - launch_time, 3)


def _diagnosis(
    category: str,
    description: str,
    inspect_artifacts: list[str],
    likely_cause: str,
    recommended_action: str,
) -> CodexDiagnosis:
    return CodexDiagnosis(
        category=category,
        description=description,
        inspect_artifacts=inspect_artifacts,
        likely_cause=likely_cause,
        recommended_action=recommended_action,
    )
