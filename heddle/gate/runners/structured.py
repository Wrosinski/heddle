"""Bounded, strict event capture shared by the two native review adapters."""

from __future__ import annotations

from pathlib import Path

from heddle.gate.results import (
    MAX_EVENT_BYTES,
    MAX_STREAM_BYTES,
    read_bounded,
    strict_json,
)
from heddle.gate.types import JsonlParseResult


def read_native_stream(path: Path) -> JsonlParseResult:
    raw = read_bounded(path, MAX_STREAM_BYTES) if path.exists() else b""
    events = []
    offsets = []
    diagnostics = []
    lines = raw.splitlines(keepends=True)
    offset = 0
    non_json = 0
    for line in lines:
        offset += len(line)
        if len(line) > MAX_EVENT_BYTES:
            raise ValueError(
                f"provider event exceeds the {MAX_EVENT_BYTES}-byte size limit"
            )
        text = line.decode("utf-8").strip()
        if text.startswith("{"):
            events.append(strict_json(line, maximum=MAX_EVENT_BYTES))
            offsets.append(offset)
        elif text:
            # Providers merge diagnostic stderr into the captured stream.
            # These lines remain diagnostics; they cannot supply completion.
            diagnostics.append(text)
            non_json += 1
    return JsonlParseResult(
        events=events,
        total_lines=len(lines),
        json_lines_attempted=len(events),
        json_lines_parsed=len(events),
        parse_errors=0,
        non_json_lines=non_json,
        error_samples=[],
        diagnostic_lines=diagnostics,
        event_offsets=offsets,
    )
