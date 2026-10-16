"""Current boundary observations composed once for reads, routing and writers."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
)
from heddle.gate import entry
from heddle.gate.registry import GATES
from heddle.kernel.model import (
    BLOCKING_TRIGGER_ROWS,
    FeatureSnapshot,
    derive_next_actions,
    is_terminal,
    with_assessed_review_closure,
)
from heddle.kernel.project_config import KernelError, ProjectConfig
from heddle.kernel.readiness import (
    Boundary,
    BoundaryAssessment,
    EvidenceState,
    ReviewAssessment,
    assess_boundary,
    implementation_pending,
)
from heddle.kernel.reviews import (
    run_owner,
)
from heddle.kernel.source_manifest import ObservedPath
from heddle.kernel.verification import VerificationFreshness
from heddle.kernel.witness_waiver import (
    acceptable_prior_witness,
    verification_refresh_reason,
    waivable_witness_rerun,
    witness_offer,
)
from heddle.runtime.flow_changes import phase_exit_grant_blockers
from heddle.runtime.review_launch import LaunchMember, LaunchSet, launch_set
from heddle.runtime.verification import (
    assess_current_verifications,
    required_verification_scopes,
    verify_named_source_evidence,
)


@dataclass(frozen=True)
class ComposedReadiness:
    assessment: BoundaryAssessment
    assignment_projection: dict[str, Any] | None
    blocking_conditions: tuple[str, ...]
    # The snapshot whose kernel facts carry the runtime's review closure.
    snapshot: FeatureSnapshot


def composed_readiness(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    *,
    purpose: str,
    assess: Callable[..., BoundaryAssessment] | None = None,
) -> ComposedReadiness:
    """Compose one read frame's readiness for every surface that renders it.

    Status, orient and the plan-status projection share it, so the plan's next
    command and conditions are orient's first action and blocking conditions.
    ``assess`` defaults to ``assess_current_readiness``.
    """
    captures: dict[str, ObservedPath] = {}
    assignment_projection = None
    if snapshot.state.feature_policy is not None:
        from heddle.runtime.review_assignments import projection

        verify_named_source_evidence(config.root, snapshot.state)
        assignment_projection = projection(
            config, snapshot, source_observations=captures
        )
        snapshot = with_assessed_review_closure(
            snapshot, assessed_closed_assignments(assignment_projection)
        )
    assessment = (assess or assess_current_readiness)(
        config,
        snapshot,
        source_observations=captures,
        assignment_projection=assignment_projection,
        purpose=purpose,
    )
    combined = set(snapshot.blocking_conditions) | set(assessment.blockers)
    ordered = [code for code, _action in BLOCKING_TRIGGER_ROWS]
    conditions = [code for code in ordered if code in combined]
    conditions.extend(sorted(combined - set(ordered)))
    return ComposedReadiness(
        assessment, assignment_projection, tuple(conditions), snapshot
    )


def assessed_closed_assignments(observed: dict[str, Any]) -> frozenset[str]:
    """Assignment ids the runtime projection assessed closed against current source."""
    return frozenset(
        str(row["assignment_id"])
        for row in observed.get("review_closure", {}).get("assignments", ())
        if row["closed"] and row["evidence_basis"] is not None
    )


def assessed_review_snapshot(
    config: ProjectConfig, snapshot: FeatureSnapshot
) -> FeatureSnapshot:
    """The snapshot whose derived actions carry the runtime's review closure.

    The kernel then never derives a gate run for an assignment closed only by
    review equivalence, which its recorded facts alone cannot see.
    """
    if snapshot.state.feature_policy is None or is_terminal(snapshot.state):
        return snapshot
    from heddle.runtime.review_assignments import projection

    return with_assessed_review_closure(
        snapshot, assessed_closed_assignments(projection(config, snapshot))
    )


def assess_current_readiness(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    *,
    boundary: Boundary | None = None,
    source_observations: dict[str, ObservedPath] | None = None,
    assignment_projection: dict[str, Any] | None = None,
    purpose: str = "boundary",
    project_launch: bool = True,
    project_stale: bool = True,
) -> BoundaryAssessment:
    """Observe canonical authority and current source in an invocation-local frame.

    ``project_launch=False`` keeps each review slot's own ``RunGate`` action
    instead of the ``RunGates`` action that joins a launch set, and
    ``project_stale=False`` keeps each stale scope's own ``Verify`` action
    instead of the ``verify --stale`` action that covers them.
    """
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
        project_launch=project_launch,
        project_stale=project_stale,
    )


def _assignment_readiness(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    boundary: Boundary,
    captures: dict[str, ObservedPath],
    observed: dict[str, Any] | None,
    *,
    purpose: str,
    project_launch: bool,
    project_stale: bool,
) -> BoundaryAssessment:
    from heddle.contracts.review_assignments import ROLE_STAGES
    from heddle.contracts.schemas import STAGES
    from heddle.runtime.review_assignments import next_actions, projection

    if observed is None:
        observed = projection(config, snapshot, source_observations=captures)
    snapshot = with_assessed_review_closure(
        snapshot, assessed_closed_assignments(observed)
    )
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
    implementation_work_pending = (
        implementation_pending(snapshot.state, boundary.stage) is not None
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
    unavailable = unavailable_baseline(config, snapshot)
    proof_actions = len(actions)
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
            if unavailable is None:
                routed = next_actions(snapshot, row)
                # Every assignment routed to reaffirm carries the same
                # boundary-level work, which is offered once.
                if row["next_step"] != "reaffirm" or all(
                    item.action != routed[0].action for item in actions
                ):
                    actions.extend(routed)
    obligations = tuple(
        (str(reference[0]), str(reference[1]))
        for reference in observed["review_closure"]["open_refs"]
    )
    identity = hashlib.sha256(
        f"{snapshot.feature}:{snapshot.state.revision}:{snapshot.stage}:{purpose}".encode()
    ).hexdigest()
    if unavailable is not None:
        # No review or gate can resolve its subject until the baseline is
        # restored or re-pointed, so the re-point is the only next action.
        return BoundaryAssessment(
            boundary,
            tuple(rows),
            verifications,
            (entry.BASELINE_UNAVAILABLE, *blockers),
            (_baseline_repoint_action(snapshot, unavailable),),
            purpose,
            obligations,
            identity,
        )
    if len(actions) > proof_actions:
        # Open review work here may change owned content again, so a default
        # post-review witness rerun waits behind it; the lane still blocks exit.
        reruns = [
            row.action
            for row in actionable_verifications
            if waivable_witness_rerun(row)
        ]
        actions = [
            *(item for item in actions if item.action not in reruns),
            *(item for item in actions[:proof_actions] if item.action in reruns),
        ]
    if project_launch:
        actions = project_launch_actions(
            snapshot.feature,
            actions,
            _launch_members(config, snapshot, observed, actions, captures).members,
        )
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
    routed = tuple(actions) or derived
    if project_stale:
        # Projected over the final order, so the batch covers exactly the
        # leading scopes `verify --stale` selects from the unprojected actions.
        routed = tuple(project_stale_verify_actions(routed, verifications))
    return BoundaryAssessment(
        boundary,
        tuple(rows),
        verifications,
        tuple(blockers),
        routed,
        purpose,
        obligations,
        identity,
    )


def actionable_stale(row: VerificationFreshness) -> bool:
    """Whether ``verify --stale`` reruns this scope.

    Content- and source-set-stale scopes with a routed rerun qualify; a waived
    or accepted scope routes none, and the live lane reruns only on its own.
    """
    return (
        row.status in {"content-stale", "source-set-stale"}
        and row.action is not None
        and row.scope != "live"
    )


def leading_verify_scopes(actions: Iterable[NextAction]) -> tuple[str, ...]:
    """Scopes of the single-scope verify actions readiness routes first."""
    scopes: list[str] = []
    for item in actions:
        action = item.action
        if not (
            isinstance(action, ops.CommandAction)
            and isinstance(action.operation, ops.Verify)
            and not action.operation.stale
            and action.operation.scope is not None
        ):
            break
        scopes.append(action.operation.scope)
    return tuple(scopes)


def stale_batch_scopes(
    actions: Iterable[NextAction], verifications: Iterable[VerificationFreshness]
) -> tuple[str, ...]:
    """The leading verify scopes a ``verify --stale`` batch reruns, in order."""
    rows = {row.scope: row for row in verifications}
    return tuple(
        scope
        for scope in leading_verify_scopes(actions)
        if scope in rows and actionable_stale(rows[scope])
    )


def project_stale_verify_actions(
    actions: Sequence[NextAction],
    verifications: Iterable[VerificationFreshness],
) -> list[NextAction]:
    """Route one ``verify --stale`` in place of two or more stale reruns it covers."""
    rows = {row.scope: row for row in verifications}
    covered = stale_batch_scopes(actions, rows.values())
    if len(covered) < 2:
        return list(actions)
    items = [
        (index, item)
        for index, item in enumerate(actions)
        if isinstance(item.action, ops.CommandAction)
        and isinstance(item.action.operation, ops.Verify)
        and item.action.operation.scope in covered
    ]
    first, lead = items[0]
    assert isinstance(lead.action, ops.CommandAction)
    operation = lead.action.operation
    assert isinstance(operation, ops.Verify)
    named = [f"{scope} ({rows[scope].status})" for scope in covered]
    reason = (
        "refresh "
        + (", ".join(named[:-1]) + " and " + named[-1])
        + " verification in one batch"
        + "".join(
            witness_offer(rows[scope], lane=f"the {scope} lane") for scope in covered
        )
    )
    batch = NextAction(
        action=ops.CommandAction(replace(operation, scope=None, stale=True)),
        reason=reason,
    )
    skipped = {index for index, _item in items}
    return [
        *actions[:first],
        batch,
        *(
            item
            for index, item in enumerate(actions)
            if index > first and index not in skipped
        ),
    ]


def unavailable_baseline(
    config: ProjectConfig, snapshot: FeatureSnapshot
) -> entry.UnavailableBaseline | None:
    """Why the feature's recorded baseline no longer resolves, or None."""
    baseline = snapshot.state.source_baseline
    if baseline is None:
        return None
    return entry.unavailable_feature_baseline(config.root, baseline)


def _baseline_repoint_action(
    snapshot: FeatureSnapshot, unavailable: entry.UnavailableBaseline
) -> NextAction:
    return NextAction(
        ops.ManualAction(
            f"{entry.REPOINT_REMEDY} --feature {snapshot.feature} "
            f"--expect-revision {snapshot.state.revision}"
        ),
        f"{unavailable.explanation}; restore it, or record a reachable ancestor "
        "of HEAD as the feature baseline",
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


def _launch_members(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    observed: dict[str, Any],
    actions: list[NextAction] | tuple[NextAction, ...],
    captures: dict[str, ObservedPath],
) -> LaunchSet:
    """Resolve the launch set, selecting the milestone a milestone gate reviews."""
    milestone_gate = next(
        (
            action.action.operation.gate
            for action in actions
            if isinstance(action.action, ops.CommandAction)
            and isinstance(action.action.operation, ops.RunGate)
            and (gate_type := GATES.get(action.action.operation.gate)) is not None
            and gate_type.requires_milestone
        ),
        None,
    )
    milestone_scope = (
        select_review_milestone(
            config, snapshot, milestone_gate, source_observations=captures
        )
        if milestone_gate is not None
        else None
    )
    return launch_set(
        config, snapshot, observed, actions, milestone_scope=milestone_scope
    )


def project_launch_actions(
    feature: str,
    actions: list[NextAction] | tuple[NextAction, ...],
    members: tuple[LaunchMember, ...],
) -> list[NextAction]:
    """Replace a launch set's own actions with one batch action.

    Only routed run-gates that select a member slot, by run-gate's own CLI
    rule, are replaced. The batch takes the first replaced position, or
    follows the last routed run-gate of a member gate when none is replaced.
    A run-gate whose slot is not a member, such as one the host refuses,
    keeps its own position so its refusal stays visible.
    """
    if len(members) < 2:
        return list(actions)
    gates = {member.role for member in members}
    routed = {
        index: action.action.operation
        for index, action in enumerate(actions)
        if isinstance(action.action, ops.CommandAction)
        and isinstance(action.action.operation, ops.RunGate)
        and action.action.operation.gate in gates
    }
    replaced = {
        index
        for index, operation in routed.items()
        if any(_runs_member_slot(operation, member) for member in members)
    }
    projected = [
        action for index, action in enumerate(actions) if index not in replaced
    ]
    projected.insert(
        min(replaced) if replaced else max(routed) + 1,
        NextAction(
            ops.CommandAction(ops.RunGates(feature=feature)),
            "run "
            + ", ".join(member.label for member in members)
            + " together; no member reads another's findings",
        ),
    )
    return projected


def _runs_member_slot(operation: ops.RunGate, member: LaunchMember) -> bool:
    """True when this run-gate selects the member's slot, as run-gate selects it."""
    return operation.gate == member.role and (
        operation.cli == member.reviewer.cli
        if operation.cli is not None
        else member.slot == "primary"
    )


def current_launch_set(
    config: ProjectConfig, snapshot: FeatureSnapshot
) -> tuple[LaunchSet, tuple[NextAction, ...]]:
    """Return the launch set run-gates may execute and readiness's current actions."""
    from heddle.runtime.review_assignments import projection

    captures: dict[str, ObservedPath] = {}
    observed = projection(config, snapshot, source_observations=captures)
    actions = assess_current_readiness(
        config,
        snapshot,
        source_observations=captures,
        assignment_projection=observed,
        project_launch=False,
    ).next_actions
    launch = _launch_members(config, snapshot, observed, actions, captures)
    return launch, tuple(
        project_launch_actions(snapshot.feature, actions, launch.members)
    )


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
    from heddle.runtime.completion import absent_optional_evidence
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
            absent_optional_evidence(root, root / snapshot.workspace, snapshot.state),
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
    stale = tuple(status for status in statuses if not status.satisfies_boundary)
    if not stale:
        return None
    first = stale[0]
    assert first.action is not None
    hint = "rerun every listed verification action and retry the transition"
    if any(waivable_witness_rerun(status) for status in stale):
        hint += "; the owner may instead waive a witness lane's post-review rerun"
    elif any(acceptable_prior_witness(status) for status in stale):
        hint += (
            "; only formatting changed since a witness lane passed, so the owner "
            "may instead accept that earlier witness"
        )
    return HeddleResult.failure(
        HeddleError(
            code="verification-missing",
            message=(
                f"verification scope {first.scope!r} is not fresh: {first.status}"
            ),
            hint=hint,
        ),
        exit_code=ExitCode.FATAL,
        diagnostics=diagnostics,
        next_actions=tuple(
            project_stale_verify_actions(
                [
                    NextAction(
                        action=status.action,
                        reason=verification_refresh_reason(status),
                    )
                    for status in stale
                    if status.action is not None
                ],
                stale,
            )
        ),
    )
