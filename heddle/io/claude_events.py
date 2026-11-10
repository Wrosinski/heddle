"""Shared selection of terminal events from Claude JSONL output."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DISABLE_REFUSAL_FALLBACK_ENV = "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"
"""Claude Code answers a refused turn with another model unless this is ``1``.

Heddle records the model it asked for, so every Claude launch turns the switch
off and a refusal fails the call; Heddle's own fallback then decides the rerun.
"""

_SUBSTITUTION_SUBTYPES = frozenset({"model_refusal_fallback", "model_fallback"})


def model_substitution(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The first event reporting that a model other than the requested one answered."""
    for event in events:
        if (
            event.get("type") == "system"
            and event.get("subtype") in _SUBSTITUTION_SUBTYPES
        ):
            return event
    return None


def model_refusal(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The event reporting a main-thread refusal that no retry answered.

    The ``model_refusal_no_fallback`` notice, which names the refusal
    category, is preferred over the refused assistant turn.
    """
    events = list(events)
    for event in events:
        if (
            event.get("type") == "system"
            and event.get("subtype") == "model_refusal_no_fallback"
        ):
            return event
    for event in events:
        message = event.get("message")
        if (
            event.get("type") == "assistant"
            and not event.get("parent_tool_use_id")
            and isinstance(message, dict)
            and message.get("stop_reason") == "refusal"
        ):
            return event
    return None


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


def model_failure_from_path(path: Path) -> str | None:
    """``model-substituted`` or ``model-refusal`` when the stream reports one."""
    if not path.exists():
        return None
    events = []
    with path.open(encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") in {
                "system",
                "assistant",
            }:
                events.append(parsed)
    if model_substitution(events) is not None:
        return "model-substituted"
    if model_refusal(events) is not None:
        return "model-refusal"
    return None
