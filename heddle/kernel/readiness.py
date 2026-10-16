"""Pure boundary evidence assessment over supplied current observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.result import NextAction
from heddle.kernel.reviews import (
    finding_authority,
    gate_converged,
    retained_unconverged_review,
    review_refresh_required,
    run_actionability_coherent,
    run_owner,
)
from heddle.kernel.source_manifest import SourceChange
from heddle.kernel.state import StateFile
from heddle.kernel.verification import VerificationFreshness
from heddle.kernel.witness_waiver import verification_refresh_reason


@dataclass(frozen=True)
class Boundary:
    stage: str
    milestone_id: str | None


@dataclass(frozen=True)
class ReviewRequirement:
    gate: str
    scope: str
    run_id: str | None
    cli: str | None = None


@dataclass(frozen=True)
class ReviewObservation:
    run_id: str
    execution: str
    applicability: str
    selected_inputs_current: bool = True


class EvidenceState(StrEnum):
    NOT_APPLICABLE = "not-applicable"
    SCOPE_NOT_DEFINED = "scope-not-defined"
    MISSING = "missing"
    CURRENT = "current"
    ACCEPTED = "accepted"
    STALE = "stale"
    INVALID = "invalid"


@dataclass(frozen=True)
class EvidenceExplanation:
    subject: str
    cause: str | None
    # The changed paths, kept beside the typed changes for existing readers.
    changed_dependencies: tuple[str, ...]
    failed_reference: str | None
    remedy: ops.Action | None
    detail_limit: str | None
    # Typed changes against a recorded manifest; empty when none records them.
    changes: tuple[SourceChange, ...] = ()


@dataclass(frozen=True)
class ReviewAssessment:
    gate: str
    scope: str
    run_id: str | None
    execution: str
    disposition: str
    applicability: str
    cli: str | None = None
    evidence_state: EvidenceState = EvidenceState.MISSING
    evidence_basis: str | None = None
    explanation: EvidenceExplanation | None = None


@dataclass(frozen=True)
class BoundaryAssessment:
    boundary: Boundary
    reviews: tuple[ReviewAssessment, ...]
    verifications: tuple[VerificationFreshness, ...]
    blockers: tuple[str, ...]
    next_actions: tuple[NextAction, ...]
    purpose: str = "boundary"
    original_obligations: tuple[tuple[str, str], ...] = ()
    observation_identity: str = ""


# Why ordinary implementation work remains at the implement stage.
PENDING_OPEN_TASKS = "open-tasks"
PENDING_NO_TASKS = "no-tasks"
PENDING_NO_CURRENT_MILESTONE = "no-current-milestone"


def implementation_pending(state: StateFile, stage: str) -> str | None:
    """Name the implementation work that remains at ``stage``, if any.

    Milestone review and verification are exit evidence, so they wait while
    the current milestone has no tasks or an unfinished one, or while a todo
    milestone has not been started.
    """
    if stage != "implement":
        return None
    current = next((m for m in state.milestones if m.status == "current"), None)
    if current is None:
        return (
            PENDING_NO_CURRENT_MILESTONE
            if any(m.status == "todo" for m in state.milestones)
            else None
        )
    if not current.tasks:
        return PENDING_NO_TASKS
    if any(task.status != "done" for task in current.tasks):
        return PENDING_OPEN_TASKS
    return None


def implementation_pending_details(state: StateFile, pending: str) -> dict[str, Any]:
    """The refusal details naming what implementation work remains."""
    current = next((m for m in state.milestones if m.status == "current"), None)
    return {
        "cause": "implementation-pending",
        "pending": pending,
        "milestone": current.id if current is not None else None,
        "open_tasks": [
            task.id
            for task in (current.tasks if current else ())
            if task.status != "done"
        ],
    }


def implementation_pending_text(details: dict[str, Any]) -> tuple[str, str]:
    """The refusal message and hint for ``implementation_pending_details``."""
    milestone, pending = details["milestone"], details["pending"]
    if pending == PENDING_OPEN_TASKS:
        return (
            f"milestone {milestone} has open tasks "
            f"{', '.join(details['open_tasks'])}; its review is exit evidence",
            "finish them with heddle task done, then run the review",
        )
    if pending == PENDING_NO_TASKS:
        return (
            f"milestone {milestone} has no tasks yet; its review is exit evidence",
            "add its tasks with heddle task add and finish them, then run the review",
        )
    return (
        "no milestone is current yet; milestone review is exit evidence",
        "start the next todo milestone as orient directs, then run the review",
    )


def assess_boundary(
    state: StateFile,
    boundary: Boundary,
    requirements: tuple[ReviewRequirement, ...],
    observations: tuple[ReviewObservation, ...],
    verifications: tuple[VerificationFreshness, ...],
    *,
    defer_gate_decisions: bool = False,
) -> BoundaryAssessment:
    """Keep execution, disposition, applicability and input selection independent."""
    observed = {item.run_id: item for item in observations}
    reviews = []
    blockers: list[str] = []
    actions: list[NextAction] = []

    def block(code: str, action: ops.Action | None, reason: str) -> None:
        if code not in blockers:
            blockers.append(code)
        if action is not None and not any(item.action == action for item in actions):
            actions.append(NextAction(action, reason))

    for verification in verifications:
        if not verification.satisfies_boundary:
            block(
                "verification-missing",
                verification.action,
                verification_refresh_reason(verification),
            )

    for requirement in requirements:
        owner = run_owner(state, requirement.run_id)
        observation = observed.get(requirement.run_id or "")
        execution = observation.execution if observation is not None else "missing"
        applicability = (
            observation.applicability if observation is not None else "unavailable"
        )
        disposition = "incoherent"
        if owner is not None and run_actionability_coherent(state, *owner):
            disposition = (
                "pending"
                if any(
                    decision.origin_run_id == requirement.run_id
                    and decision.status != "resolved"
                    for decision in state.decisions
                )
                else "settled"
            )
        row = ReviewAssessment(
            requirement.gate,
            requirement.scope,
            requirement.run_id,
            execution,
            disposition,
            applicability,
            requirement.cli,
        )
        reviews.append(row)
        reason = f"repair {row.gate}/{row.scope} review evidence"
        if execution == "missing":
            block("gate-not-converged", _review_action(state, requirement), reason)
            continue
        if execution != "completed":
            block(
                "workspace-invalid",
                ops.ManualAction(
                    "Restore the recorded canonical review and its exact finding "
                    "owners before retrying the boundary"
                ),
                reason,
            )
            continue
        if disposition == "incoherent":
            # Finding ownership retains the existing convergence repair route.
            block("gate-not-converged", None, reason)
            continue
        if disposition == "pending" and not defer_gate_decisions:
            block(
                "pending-decisions",
                ops.CommandAction(ops.DecisionsList(feature=state.feature)),
                f"resolve pending findings from {row.gate}/{row.scope}",
            )
        if review_refresh_required(
            state, row.gate, row.scope, row.run_id, applicability
        ) or (observation is not None and not observation.selected_inputs_current):
            block("gate-not-converged", _review_action(state, requirement), reason)
        elif finding_authority(state, row.gate) == row.gate and not gate_converged(
            state, row.gate, row.scope
        ):
            # Existing workflow routing chooses the required verdict pipeline round.
            block("gate-not-converged", None, reason)

    scopes = (
        (boundary.milestone_id,)
        if boundary.milestone_id is not None
        else tuple(milestone.id for milestone in state.milestones)
        if boundary.stage == "implement"
        else ("feature",)
    )
    retained = (
        retained_unconverged_review(state, scopes)
        if boundary.stage != "complete"
        else None
    )
    if retained is not None:
        block("gate-not-converged", None, "resolve the retained review obligation")

    return BoundaryAssessment(
        boundary,
        tuple(reviews),
        verifications,
        tuple(blockers),
        tuple(actions),
    )


def _review_action(state: StateFile, requirement: ReviewRequirement) -> ops.Action:
    return ops.CommandAction(
        ops.RunGate(requirement.gate, feature=state.feature, cli=requirement.cli)
    )
