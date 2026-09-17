"""Current boundary observations composed once for reads, routing and writers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

from heddle.contracts import operations as ops
from heddle.contracts.gates import INDEPENDENT_GATE_GROUPS
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
)
from heddle.gate import entry
from heddle.kernel.model import (
    FeatureSnapshot,
    derive_next_actions,
    is_terminal,
)
from heddle.kernel.project_config import KernelError, ProjectConfig
from heddle.kernel.readiness import (
    Boundary,
    BoundaryAssessment,
    EvidenceState,
    ReviewAssessment,
    assess_boundary,
)
from heddle.kernel.reviews import (
    run_owner,
)
from heddle.kernel.source_manifest import ObservedPath
from heddle.kernel.verification import VerificationFreshness
from heddle.runtime.flow_changes import phase_exit_grant_blockers
from heddle.runtime.verification import (
    assess_current_verifications,
    required_verification_scopes,
)


def assess_current_readiness(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    *,
    boundary: Boundary | None = None,
    source_observations: dict[str, ObservedPath] | None = None,
    assignment_projection: dict[str, Any] | None = None,
    purpose: str = "boundary",
) -> BoundaryAssessment:
    """Observe canonical authority and current source in an invocation-local frame."""
    boundary = boundary or Boundary(
        snapshot.stage,
        snapshot.current_milestone.id
        if snapshot.stage == "implement" and snapshot.current_milestone is not None
        else None,
    )
    if is_terminal(snapshot.state):
        return BoundaryAssessment(boundary, (), (), (), (), purpose=purpose)
    captures = source_observations if source_observations is not None else {}
    return _assignment_readiness(
        config,
        snapshot,
        boundary,
        captures,
        assignment_projection,
        purpose=purpose,
    )


def _assignment_readiness(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    boundary: Boundary,
    captures: dict[str, ObservedPath],
    observed: dict[str, Any] | None,
    *,
    purpose: str,
) -> BoundaryAssessment:
    from heddle.contracts.review_assignments import ROLE_STAGES
    from heddle.contracts.schemas import STAGES
    from heddle.runtime.review_assignments import next_action, projection

    if observed is None:
        observed = projection(config, snapshot, source_observations=captures)
    actionable_scopes = (
        (boundary.milestone_id,)
        if boundary.milestone_id
        else required_verification_scopes(snapshot.state, stage=boundary.stage)
    )
    accepted_milestone_scopes = tuple(
        receipt.scope
        for receipt in snapshot.state.review_assignments.acceptances
        if receipt.boundary == "milestone" and receipt.scope is not None
    )
    scopes = tuple(dict.fromkeys((*accepted_milestone_scopes, *actionable_scopes)))
    verifications = assess_current_verifications(
        config.root, snapshot.state, scopes, source_observations=captures
    )
    current = snapshot.current_milestone
    implementation_work_pending = boundary.stage == "implement" and (
        (current is None and any(m.status == "todo" for m in snapshot.state.milestones))
        or (
            current is not None
            and (
                not current.tasks
                or any(task.status != "done" for task in current.tasks)
            )
        )
    )
    # Verification and milestone review are exit evidence. While ordinary
    # implementation work remains, keep their status observable without
    # allowing their remedies to suppress task expansion or the current task.
    actionable_verifications = (
        ()
        if implementation_work_pending
        else tuple(row for row in verifications if row.scope in actionable_scopes)
    )
    basic = assess_boundary(snapshot.state, boundary, (), (), actionable_verifications)
    rows, actions, blockers = [], list(basic.next_actions), list(basic.blockers)
    completed_scopes = {m.id for m in snapshot.state.milestones if m.status == "done"}
    for row in observed["review_closure"]["assignments"]:
        role_stage = ROLE_STAGES[row["role"]]
        earlier = STAGES.index(role_stage) < STAGES.index(boundary.stage)
        previous_milestone = (
            role_stage == boundary.stage == "implement"
            and row["scope"] in completed_scopes
        )
        retained = (earlier or previous_milestone) and (
            row["evidence_state"] == "accepted"
            or row["open_refs"]
            or row["missing_slots"]
        )
        final = boundary.stage == "complete" and role_stage in {
            "peer-review",
            "robustness",
        }
        if role_stage != boundary.stage and not retained and not final:
            continue
        # A fresh implement boundary has no current milestone. Future milestone
        # assignments are not actionable until milestone advance promotes one;
        # otherwise their gate actions suppress derive_next_actions' promotion.
        if (
            role_stage == boundary.stage == "implement"
            and row["scope"] != boundary.milestone_id
            and not retained
        ):
            continue
        assignment = next(
            (
                a
                for a in snapshot.state.review_assignments.assignments
                if a.id == row["assignment_id"]
            ),
            None,
        )
        sources = [
            s.run_id
            for s in snapshot.state.review_assignments.sources
            if assignment is not None and s.assignment_id == assignment.id
        ]
        rows.append(
            ReviewAssessment(
                row["role"],
                row["scope"],
                sources[-1] if sources else None,
                "retained-uninterpreted"
                if row.get("retained_responses")
                else "completed"
                if row["calls_completed"]
                else "missing",
                "settled" if row["closed"] else "open",
                row["evidence_state"]
                if row["evidence_state"] == "accepted"
                else "current"
                if row["closed"]
                else "unavailable",
                evidence_state=EvidenceState(row["evidence_state"]),
                evidence_basis=row["evidence_basis"],
            )
        )
        if not row["closed"] and (retained or not implementation_work_pending):
            if "gate-not-converged" not in blockers:
                blockers.append("gate-not-converged")
            actions.append(next_action(snapshot, row))
    actions = _group_independent_gate_actions(snapshot, observed, actions)
    derived = derive_next_actions(snapshot)
    off_robustness_exit = _qualified_fresh_off_robustness_exit(
        snapshot,
        boundary,
        observed,
        blockers=blockers,
        actions=actions,
    )
    has_original_work = any(
        row["open_refs"] or row["retained_responses"] or row["calls_completed"]
        for row in observed["review_closure"]["assignments"]
        if ROLE_STAGES[row["role"]] == boundary.stage
    )
    if off_robustness_exit is not None:
        actions = [off_robustness_exit]
    elif (
        snapshot.entry == "fresh"
        and not has_original_work
        and not _current_robustness_policy_is_off(snapshot, observed)
        and derived
        and isinstance(derived[0].action, ops.SessionAction)
    ):
        actions = [derived[0], *(item for item in actions if item != derived[0])]
    obligations = tuple(
        (str(reference[0]), str(reference[1]))
        for reference in observed["review_closure"]["open_refs"]
    )
    identity = hashlib.sha256(
        f"{snapshot.feature}:{snapshot.state.revision}:{snapshot.stage}:{purpose}".encode()
    ).hexdigest()
    return BoundaryAssessment(
        boundary,
        tuple(rows),
        verifications,
        tuple(blockers),
        tuple(actions) or derived,
        purpose,
        obligations,
        identity,
    )


def _qualified_fresh_off_robustness_exit(
    snapshot: FeatureSnapshot,
    boundary: Boundary,
    observed: dict[str, Any],
    *,
    blockers: list[str],
    actions: list[NextAction],
) -> NextAction | None:
    """Recognize the one fresh stage whose explicit policy makes work inapplicable."""
    if (
        snapshot.entry != "fresh"
        or boundary.stage != snapshot.stage
        or snapshot.stage != "robustness"
        or boundary.milestone_id is not None
        or blockers
        or actions
        or phase_exit_grant_blockers(snapshot)
    ):
        return None

    effective = observed.get("effective_policy")
    closure = observed.get("review_closure")
    if not isinstance(effective, dict) or not isinstance(closure, dict):
        return None
    revision = effective.get("revision")
    entries = effective.get("entries")
    assignment_rows = closure.get("assignments")
    if (
        type(revision) is not int
        or not isinstance(entries, list)
        or not isinstance(assignment_rows, list)
    ):
        return None

    current_policy = _current_robustness_policy_entries(observed)
    robustness_rows = [
        row
        for row in assignment_rows
        if isinstance(row, dict)
        and row.get("role") == "robustness-analysis"
        and row.get("scope") == "feature"
    ]
    current_rows = [
        row for row in robustness_rows if row.get("policy_revision") == revision
    ]
    if (
        len(current_policy) != 1
        or current_policy[0].get("mode") != "off"
        or len(current_rows) != 1
        or not current_rows[0].get("closed")
        or current_rows[0].get("evidence_state") != "not-applicable"
    ):
        return None

    # Off controls only the current assignment. Any authored or retained work
    # from an earlier policy revision remains an original duty.
    if any(
        row.get("open_refs")
        or row.get("retained_responses")
        or row.get("calls_completed")
        or row.get("missing_slots")
        for row in robustness_rows
    ):
        return None
    return NextAction(
        ops.CommandAction(
            ops.PhaseExit(
                feature=snapshot.feature,
                expect_revision=snapshot.state.revision,
            )
        ),
        (
            "robustness work is explicitly Off; human authorization is required "
            "to advance"
            if "awaiting-human-authorization" in snapshot.blocking_conditions
            else "robustness work is explicitly Off and all exit requirements are met"
        ),
    )


def _current_robustness_policy_entries(
    observed: dict[str, Any],
) -> list[dict[str, Any]]:
    effective = observed.get("effective_policy")
    if not isinstance(effective, dict) or not isinstance(
        effective.get("entries"), list
    ):
        return []
    return [
        row
        for row in effective["entries"]
        if isinstance(row, dict)
        and row.get("role") == "robustness-analysis"
        and row.get("scope") == "feature"
    ]


def _current_robustness_policy_is_off(
    snapshot: FeatureSnapshot, observed: dict[str, Any]
) -> bool:
    if snapshot.stage != "robustness":
        return False
    entries = _current_robustness_policy_entries(observed)
    return len(entries) == 1 and entries[0].get("mode") == "off"


def _group_independent_gate_actions(
    snapshot: FeatureSnapshot,
    observed: dict[str, Any],
    actions: list[NextAction],
) -> list[NextAction]:
    """Replace one fully eligible declared group with its typed batch action."""
    if snapshot.stage != "peer-review":
        return actions
    rows = {row["role"]: row for row in observed["review_closure"]["assignments"]}
    for group in INDEPENDENT_GATE_GROUPS:
        if not all(
            (row := rows.get(gate)) is not None
            and row["scope"] == "feature"
            and not row["closed"]
            and row["next_step"] == "run"
            and row["rounds_used"] == 0
            and row["calls_completed"] == 0
            and not row["open_refs"]
            and not row["retained_responses"]
            for gate in group
        ):
            continue
        member_indexes = [
            index
            for index, action in enumerate(actions)
            if isinstance(action.action, ops.CommandAction)
            and isinstance(action.action.operation, ops.RunGate)
            and action.action.operation.gate in group
        ]
        if len(member_indexes) != len(group):
            continue
        insertion = min(member_indexes)
        remaining = [
            action
            for index, action in enumerate(actions)
            if index not in member_indexes
        ]
        remaining.insert(
            insertion,
            NextAction(
                ops.CommandAction(ops.RunGates(feature=snapshot.feature)),
                "run the independent initial review group",
            ),
        )
        return remaining
    return actions


def select_review_milestone(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    gate: str,
    *,
    source_observations: dict[str, ObservedPath] | None = None,
) -> str | None:
    """Resolve the actual all-done repair target in milestone order."""
    from heddle.runtime.review_assignments import projection

    observed = projection(config, snapshot, source_observations=source_observations)
    eligible = {
        m.id for m in snapshot.state.milestones if m.status in {"current", "done"}
    }
    selected = next(
        (
            row["scope"]
            for row in observed["review_closure"]["assignments"]
            if row["role"] == gate and row["scope"] in eligible and not row["closed"]
        ),
        None,
    )
    return cast(str | None, selected) or (
        snapshot.current_milestone.id if snapshot.current_milestone else None
    )


def _canonical_result(
    root: Path,
    snapshot: FeatureSnapshot,
    run_id: str,
    cache: dict[str, entry.ReviewResult],
) -> entry.ReviewResult:
    if run_id not in cache:
        owner = run_owner(snapshot.state, run_id)
        if owner is None or owner[1].verdict.get("status") == "error":
            raise _integrity_error(
                f"canonical review references an unavailable completed run: {run_id}"
            )
        fact, run = owner
        source, text = entry.read_contained_utf8_artifact(
            root / snapshot.workspace, relative_path=run.artifact
        )
        if entry.artifact_identity(text) != run.artifact_sha256:
            raise _integrity_error(
                f"canonical review bytes differ from their accepted event: {source}"
            )
        cache[run_id] = entry.retained_review_result(
            text, run, feature=snapshot.feature, gate=fact.gate, scope=fact.scope
        )
    return cache[run_id]


def validate_retained_reviews(
    root: Path, snapshot: FeatureSnapshot
) -> dict[str, entry.ReviewResult]:
    """Return validated original results without recomputing today's freshness."""
    from heddle.runtime.review_assignments import (
        ReviewEvidenceAccess,
        validate_sources,
    )

    return validate_sources(
        ReviewEvidenceAccess(
            root,
            snapshot.workspace,
            snapshot.feature,
            snapshot.state,
        )
    )


def _integrity_error(message: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=message,
        hint=(
            "restore the recorded canonical JSON and exact bindings; "
            "generated views cannot repair authority"
        ),
    )


def readiness_failure(
    error: KernelError, diagnostics: tuple[Diagnostic, ...] = ()
) -> HeddleResult:
    """Keep an integrity or source refusal actionable at every readiness boundary."""
    return HeddleResult.failure(
        HeddleError(
            code=error.code,
            message=error.message,
            hint=error.hint,
            details=error.details,
        ),
        exit_code=ExitCode.FATAL,
        diagnostics=diagnostics,
        next_actions=(
            NextAction(
                ops.ManualAction(error.hint or error.message),
                "repair current boundary evidence",
            ),
        ),
    )


def readiness_status_block(
    assessment: BoundaryAssessment, diagnostics: tuple[Diagnostic, ...] = ()
) -> HeddleResult | None:
    if not assessment.blockers:
        return None
    if assessment.blockers[0] == "verification-missing":
        return verification_status_block(assessment.verifications, diagnostics)
    return HeddleResult.failure(
        HeddleError(
            code=assessment.blockers[0],
            message=(
                f"{assessment.boundary.stage} boundary review evidence is not ready: "
                + ", ".join(assessment.blockers)
            ),
            hint="complete the listed evidence remedies and retry the transition",
        ),
        exit_code=ExitCode.FATAL,
        diagnostics=diagnostics,
        next_actions=assessment.next_actions,
    )


def verification_status_block(
    statuses: tuple[VerificationFreshness, ...],
    diagnostics: tuple[Diagnostic, ...] = (),
) -> HeddleResult | None:
    stale = tuple(status for status in statuses if not status.authorizes)
    if not stale:
        return None
    first = stale[0]
    assert first.action is not None
    return HeddleResult.failure(
        HeddleError(
            code="verification-missing",
            message=(
                f"verification scope {first.scope!r} is not fresh: {first.status}"
            ),
            hint="rerun every listed verification action and retry the transition",
        ),
        exit_code=ExitCode.FATAL,
        diagnostics=diagnostics,
        next_actions=tuple(
            NextAction(
                action=status.action,
                reason=f"refresh {status.scope} verification ({status.status})",
            )
            for status in stale
            if status.action is not None
        ),
    )
