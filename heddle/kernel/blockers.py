"""Distinct stage and session budgets derived from typed continuation grants."""

from __future__ import annotations

from heddle.contracts.decisions import ContinueSession, ContinueStage
from heddle.contracts.review_assignments import EngineFailure
from heddle.kernel.state import SessionFact, StateFile

# workflow-model §12.1: each counter uses its own maximum grant baseline.
STAGE_CAP_LIMIT = 5


def errored_gate_run_count(state: StateFile, required_gates: tuple[str, ...]) -> int:
    """Error attempts across required gates and all scopes, never quality runs."""
    if state.review_assignments.attempts:
        roles = {
            assignment.id: assignment.role
            for assignment in state.review_assignments.assignments
        }
        return sum(
            isinstance(attempt.outcome, EngineFailure)
            and roles.get(attempt.assignment_id) in required_gates
            for attempt in state.review_assignments.attempts
        )
    return sum(
        run.verdict.get("status") == "error"
        for fact in state.gates
        if fact.gate in required_gates
        for run in fact.runs
    )


def gate_cap_reached(state: StateFile, required_gates: tuple[str, ...]) -> bool:
    baseline = max(
        (
            decision.resolution.gate_run_count_before
            for decision in state.decisions
            if isinstance(decision.resolution, ContinueStage)
            and decision.target_stage == state.stage
        ),
        default=0,
    )
    return (
        max(0, errored_gate_run_count(state, required_gates) - baseline)
        >= STAGE_CAP_LIMIT
    )


def sessions_since_grant(
    state: StateFile, stage: str, blocker: str
) -> tuple[SessionFact, ...]:
    sessions = tuple(session for session in state.sessions if session.stage == stage)
    baseline = max(
        (
            decision.resolution.session_count_before
            for decision in state.decisions
            if isinstance(decision.resolution, ContinueSession)
            and decision.target_stage == stage
            and decision.target_blocker == blocker
        ),
        default=0,
    )
    if stage == "implement" and blocker == "attempt-cap":
        baseline = max(baseline, _milestone_baseline(state, sessions))
    return sessions[baseline:]


def _milestone_baseline(state: StateFile, sessions: tuple[SessionFact, ...]) -> int:
    """Index just past the last implement session that started before the latest
    milestone landed.

    The attempt cap budgets each milestone, so a feature that keeps advancing
    is not paused every five sessions. A milestone lands with a sealed
    acceptance receipt; without one (milestone review off) the stage-wide count
    stands. Sessions are appended in order and advance is boundary-owned, so no
    session spans a landing: an entry without revisions (a narrative session
    log) sits on the side of the landing its position shows.
    """
    landed = max(
        (
            receipt.revision_after
            for receipt in state.review_assignments.acceptances
            if receipt.boundary == "milestone"
        ),
        default=None,
    )
    if landed is None:
        return 0
    started_before = [
        index
        for index, session in enumerate(sessions)
        if session.revision_before is not None and session.revision_before < landed
    ]
    return started_before[-1] + 1 if started_before else 0


def stage_session_count(state: StateFile, stage: str, blocker: str) -> int:
    """Driver-run sessions in the window; a narrative session log is no attempt.

    The driver records each phase session's revisions, while the session's own
    `heddle session log` entry records none, so counting both would charge one
    attempt twice.
    """
    return sum(
        session.revision_before is not None
        for session in sessions_since_grant(state, stage, blocker)
    )


def no_progress_reached(state: StateFile) -> bool:
    sessions = sessions_since_grant(state, state.stage, "no-progress")
    return len(sessions) >= 2 and all(
        session.advanced is False for session in sessions[-2:]
    )


def attempt_cap_reached(state: StateFile) -> bool:
    return stage_session_count(state, state.stage, "attempt-cap") >= STAGE_CAP_LIMIT
