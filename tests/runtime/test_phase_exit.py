"""Phase-exit write-path behavior and blocking conditions."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.runtime.write_path_helpers import (
    authored_clean_reviews,
)
from tests.runtime.write_path_helpers import (
    copy_host as _copy_host,
)
from tests.runtime.write_path_helpers import (
    read_yaml as _read_yaml,
)
from tests.runtime.write_path_helpers import (
    write_yaml as _write_yaml,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EDGE = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "edge"
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"
COMPLETED_BOUNDARY_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "workspaces" / "completed-boundary"
)


def _copy_edge(tmp_path, source, name="host"):
    host = _copy_host(tmp_path, source, name)
    authored_clean_reviews(host / "plans/gate-stage-converged/state.yaml")
    # Keep the failing first suite current so the next action remains Codex.
    import json

    from tests.readiness_helpers import authored_current_event

    path = host / "plans/gate-stage-latest-fail/state.yaml"
    state = _read_yaml(path)
    run = state["gates"][0]["runs"][0]
    payload = json.loads((path.parent / run["artifact"]).read_text())["content"]
    state["gates"][0]["runs"][0] = authored_current_event(
        path,
        "spec-review",
        "feature",
        payload=payload,
        run_id=run["run_id"],
        at=run["at"],
    )
    _write_yaml(path, state)
    return host


def _e2_implement_boundary_host(tmp_path: Path, flow: str) -> Path:
    """A hitl/auto implement boundary with NO derived grant blockers: milestone
    work genuinely in progress, so the only refusal left for a real exit is
    _implement_exit_block (kickoff-guidance-integration E2 live shape — there
    the derived blocking set was exactly [awaiting-human-authorization])."""
    host = _copy_edge(tmp_path, EDGE, f"e2-{flow}")
    # Ratify principles so the auto leg reaches _implement_exit_block instead
    # of the earlier principles-not-ratified refusal (no-op for hitl).
    principles = host / "docs" / "workflow" / "engineering-principles.md"
    principles.parent.mkdir(parents=True, exist_ok=True)
    principles.write_text(
        "---\nstatus: ratified\n---\n\n# Engineering Principles\n",
        encoding="utf-8",
    )
    state_path = host / "plans" / "blocked-task-incomplete" / "state.yaml"
    state = _read_yaml(state_path)
    state["flow"] = flow
    # Cure the fixture's deliberate AC-8(d) done-invariant violation so no
    # kernel blocking condition fires; reader stays current with an open task.
    state["milestones"][0]["tasks"][1]["status"] = "done"
    _write_yaml(state_path, state)
    return host


def test_phase_exit_blocks_exit_from_implement_with_incomplete_milestones(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Checkpoint 2 lifecycle boundary / REQ-10 (review): exiting implement requires
    # every
    # milestone done. reader's blocking_conditions returns () for nl-screening
    # (core done, reader current with open tasks), so a bare phase-exit would have
    # silently advanced implement -> peer-review. _implement_exit_block now
    # closes that hole. Two sub-cases exercise both branches of the gate.
    golden = _copy_host(tmp_path, GOLDEN, "incomplete")
    monkeypatch.chdir(golden)
    state_path = golden / "plans" / "nl-screening" / "state.yaml"

    # Branch 1: a current milestone with open tasks -> advance_milestone raises,
    # surfaced as task-incomplete.
    before = state_path.read_bytes()
    code, out, _err = run_cli(["phase-exit", "--feature", "nl-screening", "--json"])
    envelope = envelope_tools.parse(out)
    envelope_tools.assert_shape(envelope)
    assert code == 3 and envelope["error"]["code"] == "task-incomplete", (
        "FAIL REQ-10: phase-exit from implement with an unfinished milestone "
        f"must block, got exit {code} / {envelope!r}"
    )
    assert envelope["error"]["hint"], (
        "FAIL lifecycle boundary: the block must carry an actionable hint toward "
        "completion"
    )
    assert state_path.read_bytes() == before, (
        "FAIL lifecycle boundary/INV-3: blocked phase-exit must leave state.yaml "
        "unchanged"
    )

    # Branch 2: a promotable todo with no current milestone -> advance_milestone
    # would succeed, so the gate itself reports milestone-out-of-sequence and
    # steers the operator to `heddle milestone advance` first.
    promotable = _read_yaml(state_path)
    for milestone in promotable["milestones"]:
        if milestone["id"] == "m2":
            milestone["status"] = "todo"
    _write_yaml(state_path, promotable)
    before = state_path.read_bytes()
    code, out, _err = run_cli(["phase-exit", "--feature", "nl-screening", "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "milestone-out-of-sequence", (
        "FAIL REQ-10: an unadvanced promotable milestone must block exit, "
        f"got exit {code} / {envelope!r}"
    )
    assert any(
        action["command"] == "heddle milestone advance --feature nl-screening"
        for action in envelope["next_actions"]
    ), "FAIL lifecycle boundary: the gate must steer toward `heddle milestone advance`"
    assert state_path.read_bytes() == before, (
        "FAIL lifecycle boundary/INV-3: blocked phase-exit must leave state.yaml "
        "unchanged"
    )


# --------------------------------------------------------------------------- #
# completion (kickoff-guidance-integration) — phase-exit no longer owns the close
# (reader)
# --------------------------------------------------------------------------- #
# Red discriminators for AC-9/AC-10 (entry half) + REQ-14: entering `complete`
# only advances/authorizes — it requires no completion artifact, runs no close
# suite, and stamps no lifecycle. Both fail today: the current handler runs
# auto_close_block on the auto transition and stamps the spec frontmatter on
# every robustness -> complete advance (the AC-12 tests above pin exactly the
# superseded contract; milestone reader inverts them in the same green unit).
# Behavior contract: kickoff-guidance-integration


def _completion_spec_lifecycle(host: Path) -> str:
    spec = host / "docs" / "features" / "example" / "complete-boundary.md"
    for line in spec.read_text(encoding="utf-8").splitlines():
        if line.startswith("lifecycle:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("FIXTURE ROT: complete-boundary spec lost lifecycle")
