"""Local E2E coverage for real write-path commands on synthetic hosts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.runtime.write_path_helpers import (
    copy_host as _copy_host,
)
from tests.runtime.write_path_helpers import (
    read_yaml as _read_yaml,
)
from tests.runtime.write_path_helpers import (
    write_yaml as _write_yaml,
)

pytestmark = [
    pytest.mark.e2e,
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"
EDGE = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "edge"
COMPLETED_BOUNDARY_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "workspaces" / "completed-boundary"
)


def _run_heddle(host: Path, *argv: str) -> tuple[int, dict[str, Any], str]:
    proc = subprocess.run(
        [sys.executable, "-m", "heddle", *argv, "--json"],
        cwd=host,
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        envelope = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "FAIL live: Heddle did not emit a clean JSON envelope on stdout; "
            f"exit={proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}"
        ) from exc
    return proc.returncode, envelope, proc.stderr


def _make_tiny_done_eligible(state_path: Path) -> None:
    state = _read_yaml(state_path)
    state["commands"]["test_command"] = f"{sys.executable} -c \"print('ok')\""
    for milestone in state["milestones"]:
        if milestone["id"] == "m1":
            milestone["verification"]["command"] = (
                f"{sys.executable} -c \"print('m1 ok')\""
            )
        for task in milestone.get("tasks", []):
            task["status"] = "done"
    _write_yaml(state_path, state)


def test_local_write_path_no_mocks(tmp_path: Path) -> None:
    """
    AC-1..AC-18 live-local smoke over real CLI process boundaries.

        Uses no monkeypatching. The current implementation is expected to fail red
        until write write commands are live.

    """

    tiny = _copy_host(tmp_path, TINY, "tiny")
    tiny_state = tiny / "plans" / "sample-feature" / "state.yaml"
    _make_tiny_done_eligible(tiny_state)
    before_revision = _read_yaml(tiny_state)["revision"]

    code, envelope, _stderr = _run_heddle(tiny, "orient", "--feature", "sample-feature")
    assert code == 0 and envelope["data"]["feature"] == "sample-feature", (
        f"FAIL live AC-15: orient failed: exit={code}, envelope={envelope!r}"
    )

    code, envelope, _stderr = _run_heddle(
        tiny, "kickoff", "--feature", "sample-feature"
    )
    assert code == 0 and envelope["data"]["stage"] == "implement", (
        f"FAIL live AC-15: kickoff failed: exit={code}, envelope={envelope!r}"
    )

    code, envelope, _stderr = _run_heddle(
        tiny, "task", "add", "live-local task", "--feature", "sample-feature"
    )
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live AC-6: task add failed: exit={code}, envelope={envelope!r}"
    )
    after_add = _read_yaml(tiny_state)
    assert after_add["revision"] == before_revision + 1, (
        "FAIL live AC-1: task add must bump revision once"
    )

    task_id = after_add["milestones"][0]["tasks"][-1]["id"]
    code, envelope, _stderr = _run_heddle(
        tiny, "task", "done", task_id, "--feature", "sample-feature"
    )
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live AC-6: task done failed: exit={code}, envelope={envelope!r}"
    )
    revision_after_done = _read_yaml(tiny_state)["revision"]
    code, envelope, _stderr = _run_heddle(
        tiny, "task", "done", task_id, "--feature", "sample-feature"
    )
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live AC-4: repeated task done failed: exit={code}, envelope={envelope!r}"
    )
    assert _read_yaml(tiny_state)["revision"] == revision_after_done, (
        "FAIL live AC-4: no-op task done must not bump revision"
    )

    code, envelope, _stderr = _run_heddle(
        tiny, "verify", "--scope", "m1", "--feature", "sample-feature"
    )
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live AC-7: verify failed: exit={code}, envelope={envelope!r}"
    )
    state = _read_yaml(tiny_state)
    facts = [fact for fact in state["verifications"] if fact["scope"] == "m1"]
    assert facts and facts[-1]["exit_code"] == 0, (
        "FAIL live AC-7: verify must append a passing m1 fact"
    )
    assert (tiny / "plans" / "sample-feature" / facts[-1]["log"]).is_file(), (
        "FAIL live AC-7: verify log must exist"
    )

    session_payload = tmp_path / "session.yaml"
    session_payload.write_text(
        "\n".join(
            [
                "started_at: 2026-06-15T14:00Z",
                "ended_at: 2026-06-15T14:10Z",
                "completed: [live]",
                "started: []",
                "key_context: live local",
                "next_steps: live next",
                "blockers: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    code, envelope, _stderr = _run_heddle(
        tiny,
        "session",
        "log",
        "--from-file",
        str(session_payload),
        "--feature",
        "sample-feature",
    )
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live AC-8: session log failed: exit={code}, envelope={envelope!r}"
    )

    code, envelope, _stderr = _run_heddle(
        tiny, "milestone", "advance", "--feature", "sample-feature"
    )
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live AC-5: milestone advance failed: exit={code}, envelope={envelope!r}"
    )

    edge = _copy_host(tmp_path, EDGE, "edge")
    phase_state = edge / "plans" / "gate-all-converged" / "state.yaml"
    before_phase = phase_state.read_bytes()
    code, envelope, _stderr = _run_heddle(
        edge, "phase-exit", "--feature", "gate-all-converged", "--dry-run"
    )
    assert code == 0 and envelope["data"]["dry_run"] is True, (
        f"FAIL live AC-3: phase-exit dry-run failed: exit={code}, envelope={envelope!r}"
    )
    assert phase_state.read_bytes() == before_phase, (
        "FAIL live AC-3: dry-run changed state.yaml"
    )

    blocked_state = edge / "plans" / "blocked-task-incomplete" / "state.yaml"
    before_blocked = blocked_state.read_bytes()
    code, envelope, _stderr = _run_heddle(
        edge, "phase-exit", "--feature", "blocked-task-incomplete", "--dry-run"
    )
    assert code == 3 and envelope["error"]["code"] == "task-incomplete", (
        f"FAIL live AC-18: blocked dry-run reported wrong result: exit={code}, "
        f"envelope={envelope!r}"
    )
    assert blocked_state.read_bytes() == before_blocked, (
        "FAIL live AC-18: blocked dry-run changed state.yaml"
    )

    code, envelope, _stderr = _run_heddle(
        edge, "phase-exit", "--feature", "gate-all-converged"
    )
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live AC-10/AC-11: phase-exit failed: exit={code}, envelope={envelope!r}"
    )
    state = _read_yaml(phase_state)
    assert state["stage"] == "peer-review", (
        f"FAIL live AC-11: implement should advance to peer-review, got "
        f"{state['stage']!r}"
    )
    assert "phase_exit_summary" in envelope["data"], (
        "FAIL live AC-13: phase-exit must emit a summary skeleton"
    )

    complete = _copy_host(tmp_path, COMPLETED_BOUNDARY_FIXTURE, "complete")
    spec_path = complete / "docs" / "features" / "example" / "complete-boundary.md"
    body_before = spec_path.read_text(encoding="utf-8").split("---\n", 2)[2]
    code, envelope, _stderr = _run_heddle(
        complete,
        "phase-exit",
        "--feature",
        "complete-boundary",
        "--through",
        "complete",
    )
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live REQ-14: complete entry failed: exit={code}, envelope={envelope!r}"
    )
    spec_text = spec_path.read_text(encoding="utf-8")
    assert yaml.safe_load(spec_text.split("---\n", 2)[1])["lifecycle"] == "active", (
        "FAIL live REQ-14: entering complete must not stamp the lifecycle — "
        "only qualifying completion stage session acceptance stamps (INV-8)"
    )

    (complete / "plans" / "complete-boundary.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n## Tooling fights\n\n## Prompt gaps\n\n"
        "## Validator false-positives\n\n## Escalations that should have been "
        "policy-resolvable\n",
        encoding="utf-8",
    )
    (complete / "plans" / "complete-boundary.decision-journal.md").write_text(
        "# Decision Journal\n", encoding="utf-8"
    )
    code, envelope, _stderr = _run_heddle(
        complete,
        "feature",
        "complete",
        "--feature",
        "complete-boundary",
    )
    assert code == 4 and envelope["data"]["accepted"] is True, (
        f"FAIL live AC-9/AC-10: qualifying final acceptance failed: exit={code}, "
        f"envelope={envelope!r}"
    )
    spec_text = spec_path.read_text(encoding="utf-8")
    assert yaml.safe_load(spec_text.split("---\n", 2)[1])["lifecycle"] == "complete"
    assert spec_text.split("---\n", 2)[2] == body_before, (
        "FAIL live AC-12: lifecycle stamp changed Markdown body"
    )

    code, envelope, _stderr = _run_heddle(
        complete,
        "feature",
        "complete",
        "--feature",
        "complete-boundary",
    )
    assert code == 4 and envelope["data"]["wrote"] is False, (
        f"FAIL live AC-8: the qualifying retry must be a wrote: false no-op, "
        f"exit={code}, envelope={envelope!r}"
    )
