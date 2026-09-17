"""Shared selection of terminal events from Claude JSONL output."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def last_result_event(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    result = None
    for event in events:
        if event.get("type") == "result":
            result = event
    return result


def last_result_event_from_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    result = None
    with path.open(encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "result":
                result = parsed
    return result or {}
