"""Host reporting and Claude transport mechanics."""

from __future__ import annotations

import importlib.util
import inspect
import json
from datetime import date
from pathlib import Path

from tests.tiering_review_helpers import V7_FEATURE, current_host


def test_host_and_reporting_smoke() -> None:
    """Survivor: managed markers retain their shared exact judgment."""
    from heddle.kernel.managed_regions import (
        begin_marker,
        end_marker,
        marker_fault_kind,
    )
    from heddle.runtime.sync import SESSION_ENTRY_ID, render_session_entry

    body = render_session_entry()
    rendered = (
        f"{begin_marker(SESSION_ENTRY_ID)}\n{body}{end_marker(SESSION_ENTRY_ID)}\n"
    )
    assert "Run `heddle orient`" in body
    assert marker_fault_kind(rendered, SESSION_ENTRY_ID) is None
    assert marker_fault_kind("host prose\n", SESSION_ENTRY_ID) == "absent"


def test_ac4_current_trajectory_omits_unavailable_historical_cap_fields(
    tmp_path: Path, monkeypatch
) -> None:
    """Red: current events do not publish counters they cannot establish."""
    from heddle.runtime.trajectory import archive_trajectory

    host, _state = current_host(tmp_path, monkeypatch)
    events = host / "plans" / V7_FEATURE / "events.jsonl"
    events.write_text("")
    report = archive_trajectory(host, V7_FEATURE, now=date(2026, 9, 13))
    assert report is not None
    budget = json.loads(report.read_text())["budget"]
    assert budget["rounds_count"] == 0 and budget["calls_count"] == 0
    assert "near_cap" not in budget
    assert "near_cap_runs" not in budget
    assert "Unrecorded attempts are unavailable" in budget["note"]


def test_ac5_claude_terminal_event_selection_has_one_transport_owner(
    tmp_path: Path,
) -> None:
    """Red: lead and reviewer transports share terminal-event selection only."""
    spec = importlib.util.find_spec("heddle.io.claude_events")
    assert spec is not None, "FAIL AC-5: shared Claude event reader is missing"
    from heddle.driver import sessions
    from heddle.gate.runners import claude
    from heddle.io import claude_events

    events = [
        {"type": "assistant", "message": "work"},
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    assert claude_events.last_result_event(events) == events[-1]
    stream = tmp_path / "events.jsonl"
    stream.write_text("noise\n" + "\n".join(json.dumps(row) for row in events) + "\n")
    assert claude_events.last_result_event_from_path(stream) == events[-1]
    assert (
        claude.classify_claude(
            type(
                "Monitor",
                (),
                {"termination": claude.Termination.COMPLETED, "exit_code": 0},
            )(),
            stream,
            events,
        )
        is None
    )
    assert "def _last_result_event" not in inspect.getsource(sessions)
    assert "def _last_result_event" not in inspect.getsource(claude)
    assert "last_result_event_from_path" in inspect.getsource(sessions)
    assert "last_result_event" in inspect.getsource(claude)
