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
    return sessions[baseline:]


def stage_session_count(state: StateFile, stage: str, blocker: str) -> int:
    return len(sessions_since_grant(state, stage, blocker))


def no_progress_reached(state: StateFile) -> bool:
    sessions = sessions_since_grant(state, state.stage, "no-progress")
    return len(sessions) >= 2 and all(
        session.advanced is False for session in sessions[-2:]
    )


def attempt_cap_reached(state: StateFile) -> bool:
    return stage_session_count(state, state.stage, "attempt-cap") >= STAGE_CAP_LIMIT
