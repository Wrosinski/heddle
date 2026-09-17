"""Pure boundary evidence assessment over supplied current observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

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
from heddle.kernel.state import StateFile
from heddle.kernel.verification import VerificationFreshness


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
    changed_dependencies: tuple[str, ...]
    failed_reference: str | None
    remedy: ops.Action | None
    detail_limit: str | None


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
        if not verification.authorizes:
            block(
                "verification-missing",
                verification.action,
                f"refresh {verification.scope} verification ({verification.status})",
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
