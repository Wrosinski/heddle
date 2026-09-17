from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        data = json.loads(args.summary.read_text())
    except FileNotFoundError as exc:
        print(
            f"Error: cannot read summary at {args.summary}: {exc}",
            file=sys.stderr,
        )
        return 1
    except json.JSONDecodeError as exc:
        print(
            f"Error: invalid JSON in summary {args.summary}: {exc}",
            file=sys.stderr,
        )
        return 1
    try:
        diagnosis = data.get("codex_diagnosis") or _diagnose_from_summary(data)
    except (KeyError, TypeError, ValueError) as exc:
        diagnosis = _malformed_summary_diagnosis(exc)

    if args.json:
        print(json.dumps(diagnosis, indent=2))
        return 0

    print("# Gate Diagnosis")
    print()
    print(f"- Feature: {data.get('feature', '')}")
    print(f"- Gate: {data.get('gate', '')}")
    print(f"- CLI: {data.get('cli', '')}")
    print(f"- Status: {data.get('status', '')}")
    print(f"- Reason: {data.get('reason', '')}")
    print()
    print("## Diagnosis")
    print(f"- Category: {diagnosis['category']}")
    print(f"- Description: {diagnosis['description']}")
    if diagnosis.get("recommended_action"):
        print(f"- Recommended action: {diagnosis['recommended_action']}")
    print()
    print("## Artifacts")
    artifacts = data.get("artifacts", {})
    for key in ("summary", "log", "prompt", "raw_out", "events_jsonl"):
        value = artifacts.get(key)
        if value:
            print(f"- {key}: {value}")
    return 0


def _diagnose_from_summary(data: dict) -> dict:
    preflight_diagnosis = _preflight_diagnosis(data)
    if preflight_diagnosis is not None:
        return preflight_diagnosis

    from heddle.gate.runners.codex_diagnostics import diagnose

    codex_events = data.get("codex_events", {})
    io = data.get("io", {})
    runtime = data.get("runtime", {})
    timeline = codex_events.get("timeline") or {}
    gap_s = float(
        timeline.get("gap_before_termination_s", 0)
        or runtime.get("inactivity_observed_seconds", 0)
        or 0
    )
    result = diagnose(
        inactive=data.get("reason") == "inactivity-timeout",
        tool_events=int(codex_events.get("tool_event_count", 0)),
        message_events=int(codex_events.get("message_event_count", 0)),
        raw_bytes=int(io.get("raw_bytes", 0)),
        parse_errors=int(codex_events.get("parse_errors", 0)),
        transport_errors=int(codex_events.get("transport_error_count", 0)),
        sources_agree=bool(
            (codex_events.get("message_extraction") or {}).get(
                "sources_agree",
                True,
            )
        ),
        gap_s=gap_s,
        failed=data.get("status") != "success",
    )
    return {
        "category": result.category,
        "description": result.description,
        "inspect_artifacts": result.inspect_artifacts,
        "likely_cause": result.likely_cause,
        "recommended_action": result.recommended_action,
    }


def _preflight_diagnosis(data: dict) -> dict[str, object] | None:
    reason = data.get("reason")
    if reason == "zero-diff-owned-path-overlap":
        return {
            "category": "preflight-overlap",
            "description": (
                "The gate stopped before model invocation because the diff does "
                "not overlap milestone-owned paths."
            ),
            "inspect_artifacts": ["summary", "log"],
            "likely_cause": (
                "The current git diff is unrelated to the active milestone scope."
            ),
            "recommended_action": (
                "Check whether the current git diff belongs to this milestone or "
                "update the milestone owned paths."
            ),
        }
    if reason == "empty-authoritative-diff":
        return {
            "category": "preflight-empty-diff",
            "description": (
                "The gate stopped before model invocation because the "
                "authoritative diff is empty."
            ),
            "inspect_artifacts": ["summary", "log"],
            "likely_cause": (
                "No implementation changes were present for a diff-required "
                "heddle.gate."
            ),
            "recommended_action": (
                "Provide a non-empty diff or run the gate after making milestone "
                "changes."
            ),
        }
    return None


def _malformed_summary_diagnosis(exc: Exception) -> dict[str, object]:
    return {
        "category": "malformed-summary",
        "description": (
            "The summary JSON is missing required fields or has incompatible "
            "field types."
        ),
        "inspect_artifacts": ["summary"],
        "likely_cause": str(exc),
        "recommended_action": (
            "Regenerate the gate summary or inspect the summary JSON manually."
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
