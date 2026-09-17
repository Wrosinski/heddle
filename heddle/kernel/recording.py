"""Pure accepted-event transforms over decoded current state documents."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.review_assignments import (
    AssignmentSource,
    LeadInterpretation,
    ReviewAssignments,
    ReviewAttempt,
    UsableReviewCapture,
    parse_review_attempt,
    review_attempt_document,
)
from heddle.kernel.project_config import KernelError
from heddle.kernel.review_assignments import assignment_for, launch_round
from heddle.kernel.reviews import run_actionability_coherent, run_owner
from heddle.kernel.state import (
    GateRun,
    StateFile,
    parse_gate_fact,
    parse_state_document,
)

_SOURCE = Path("state.yaml")


@dataclass(frozen=True)
class RecordedRun:
    document: dict[str, Any]
    run_id: str
    decision_ids: tuple[str, ...]
    replay: bool


def record_attempt(
    document: dict[str, Any], attempt: ReviewAttempt
) -> tuple[dict[str, Any], bool]:
    """Append one completed attempt or recognize its exact immutable replay."""
    encoded = review_attempt_document(attempt)
    rows = document.get("attempts", [])
    if not isinstance(rows, list):
        raise ValueError("attempt ledger requires an attempts list")
    for existing in rows:
        parsed = parse_review_attempt(existing)
        if parsed.attempt_id != attempt.attempt_id:
            continue
        if review_attempt_document(parsed) != encoded:
            raise ValueError("attempt identity conflict")
        return document, True
    updated = deepcopy(document)
    updated.setdefault("attempts", []).append(encoded)
    return updated, False


def interpret_attempt(
    attempt: ReviewAttempt,
    interpretation: LeadInterpretation,
    source: AssignmentSource | None = None,
) -> tuple[ReviewAttempt, bool]:
    """Add lead attribution without changing the original reviewer attempt."""
    if not isinstance(attempt.outcome, UsableReviewCapture):
        raise ValueError("interpretation requires a usable review capture")
    candidate = ReviewAttempt(
        attempt_id=attempt.attempt_id,
        assignment_id=attempt.assignment_id,
        round_number=attempt.round_number,
        reviewer_slot=attempt.reviewer_slot,
        invocation=attempt.invocation,
        outcome=attempt.outcome,
        created_at=attempt.created_at,
        artifacts=attempt.artifacts,
        interpretation=interpretation,
        source=source or attempt.source,
    )
    review_attempt_document(candidate)
    if attempt.interpretation is None:
        return candidate, False
    if attempt.interpretation != interpretation or attempt.source != candidate.source:
        raise ValueError("interpretation identity conflict")
    return attempt, True


def record_attempt_interpretation(
    document: dict[str, Any],
    *,
    attempt_id: str,
    interpretation: LeadInterpretation,
    source: AssignmentSource | None = None,
) -> tuple[dict[str, Any], bool]:
    """Append attribution to its exact attempt without rebinding original work."""
    rows = document.get("attempts")
    if not isinstance(rows, list):
        raise ValueError("attempt ledger requires an attempts list")
    updated = deepcopy(document)
    for index, raw in enumerate(rows):
        attempt = parse_review_attempt(raw)
        if attempt.attempt_id != attempt_id:
            continue
        interpreted, replay = interpret_attempt(attempt, interpretation, source)
        if replay:
            return document, True
        updated["attempts"][index] = review_attempt_document(interpreted)
        return updated, False
    raise ValueError("interpretation names an unknown attempt")


def replay_run(
    state: StateFile, gate: str, scope: str, input_hash: str
) -> GateRun | None:
    """Only the immediately recorded completed event can be an exact replay."""
    for fact in state.gates:
        if fact.gate == gate and fact.scope == scope and fact.runs:
            latest = fact.runs[-1]
            if (
                latest.verdict.get("status") != "error"
                and latest.input_hash == input_hash
            ):
                return latest
    return None


def record_run(
    document: dict[str, Any],
    *,
    gate: str,
    scope: str,
    run_fact: dict[str, Any],
    decisions: list[dict[str, Any]],
    assignment_source: dict[str, Any] | None = None,
    interpretation: dict[str, Any] | None = None,
    attempt_ledger: dict[str, Any] | None = None,
) -> RecordedRun:
    """Derive public output views from an authoritative attempt atomically.

    Runtime allocates IDs under the state lock. The transform performs no I/O,
    timestamp synthesis, ID inference, or serialization.
    """
    state = parse_state_document(document, source=_SOURCE)
    incoming = parse_gate_fact(
        _SOURCE,
        {"gate": gate, "scope": scope, "runs": [run_fact]},
    ).runs[0]
    if incoming.verdict.get("status") != "error":
        replay = replay_run(state, gate, scope, incoming.input_hash)
        if replay is not None:
            if interpretation is not None:
                previous = next(
                    (
                        row
                        for row in state.review_assignments.interpretations
                        if row.run_id == replay.run_id
                    ),
                    None,
                )
                if previous is None or any(
                    ops.decoded_payload(previous)[key] != value
                    for key, value in interpretation.items()
                    if key != "at"
                ):
                    raise _incomplete(gate, scope)
            if (
                incoming.assignment_id,
                incoming.round_number,
                incoming.reviewer_slot,
            ) != (replay.assignment_id, replay.round_number, replay.reviewer_slot):
                raise KernelError(
                    code="workspace-invalid",
                    message="replay assignment binding differs",
                    hint="preserve the canonical assignment, round and reviewer slot",
                )
            owner = run_owner(state, replay.run_id)
            assert owner is not None
            if not run_actionability_coherent(state, *owner):
                raise _incomplete(gate, scope)
            return RecordedRun(
                document,
                replay.run_id,
                tuple(
                    d.id for d in state.decisions if d.origin_run_id == replay.run_id
                ),
                True,
            )
    updated = deepcopy(document)
    if attempt_ledger is not None:
        attempts = tuple(
            parse_review_attempt(row) for row in attempt_ledger.get("attempts", [])
        )
        if incoming.assignment_id is not None and not any(
            attempt.attempt_id == incoming.run_id for attempt in attempts
        ):
            raise ValueError("derived gate view requires its review attempt")
        updated["review_assignments"] = deepcopy(attempt_ledger)
    elif incoming.assignment_id is not None:
        raise ValueError("review gate views cannot be written without their attempt")
    if state.feature_policy is not None:
        ledger = updated.setdefault(
            "review_assignments", ops.decoded_payload(ReviewAssignments())
        )
        assignment = assignment_for(state, gate, scope)
        current = launch_round(state, assignment)
        if (
            incoming.assignment_id != assignment.id
            or incoming.round_number != current.number
        ):
            raise KernelError(
                code="workspace-invalid",
                message="incoming assignment or round differs",
                hint="prepare against the current frozen assignment",
            )
        stored = next(
            (a for a in ledger["assignments"] if a["id"] == assignment.id), None
        )
        if stored is None:
            stored = ops.decoded_payload(assignment)
            ledger["assignments"].append(stored)
        if not stored["rounds"]:
            stored["rounds"].append(ops.decoded_payload(current))
        if assignment_source is not None:
            accepted_attempt = next(
                row
                for row in ledger["attempts"]
                if row["attempt_id"] == incoming.run_id
            )
            if accepted_attempt.get("source") != {
                **assignment_source,
                "run_id": incoming.run_id,
            }:
                raise ValueError("review attempt source differs from canonical content")
    gate_owner = next(
        (
            fact
            for fact in updated["gates"]
            if fact["gate"] == gate and fact["scope"] == scope
        ),
        None,
    )
    if gate_owner is None:
        gate_owner = {"gate": gate, "scope": scope, "runs": []}
        updated["gates"].append(gate_owner)
    gate_owner["runs"].append(deepcopy(run_fact))
    updated["decisions"].extend(deepcopy(decisions))
    accepted = parse_state_document(updated, source=_SOURCE)
    if incoming.verdict.get("status") != "error":
        recorded = run_owner(accepted, incoming.run_id)
        assert recorded is not None
        if not run_actionability_coherent(accepted, *recorded):
            raise _incomplete(gate, scope)
    elif decisions:
        raise _incomplete(gate, scope)
    return RecordedRun(
        updated, incoming.run_id, tuple(d["id"] for d in decisions), False
    )


def _incomplete(gate: str, scope: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"{gate} for {scope}: incomplete REPORT metadata or decision ownership",
        hint="record the complete validated event and its required finding "
        "owners together",
    )
