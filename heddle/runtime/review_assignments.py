"""Qualify original review sources and apply lead facts at the state owner."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

from heddle.contracts import operations as ops
from heddle.contracts.decisions import Disposition
from heddle.contracts.feature_policy import ReviewClosure, ReviewResolution
from heddle.contracts.result import (
    Conflict,
    Diagnostic,
    ExitCode,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.contracts.review_assignments import (
    DISPOSITION_OPTIONAL_FIELDS,
    DISPOSITION_REQUIRED_FIELDS,
    DISPOSITION_STATUSES,
    EVIDENCE_KINDS,
    REBOUND_BY_EQUIVALENCE,
    REBOUND_BY_REAFFIRM,
    ROLE_STAGES,
    AcceptanceRecord,
    AcceptedAssignment,
    AssignmentDisposition,
    AssignmentRound,
    BoundaryAcceptance,
    CanonicalReview,
    EngineFailure,
    ManifestReference,
    RetainedReview,
    ReviewAssignment,
    ReviewScopeChange,
    UsableReviewCapture,
    derive_attempt_accounting,
)
from heddle.contracts.schemas import STAGES
from heddle.gate import entry
from heddle.gate.cli import resolve_gate_execution
from heddle.gate.preparation import input_contract_for, reviewed_test_subject
from heddle.gate.registry import GATES
from heddle.gate.types import (
    FailureReason,
    GateInvocationOverrides,
    GateType,
    ResolvedGateInvocation,
)
from heddle.io.source import (
    capture_source_path,
    observe_source,
    resolve_source_definition,
)
from heddle.kernel import review_assignments as core
from heddle.kernel.feature_policy import (
    secondary_serves,
    validate_host_review_selection,
)
from heddle.kernel.managed_regions import authored_plan_observation
from heddle.kernel.model import FeatureSnapshot
from heddle.kernel.project_config import KernelError, ProjectConfig, feature_state_path
from heddle.kernel.readiness import (
    EvidenceExplanation,
    implementation_pending,
    implementation_pending_details,
    implementation_pending_text,
)
from heddle.kernel.review_closure import (
    assess_review_closure,
    required_obligation,
    settles_decision_route,
)
from heddle.kernel.review_equivalence import equivalence_identity, rule_id_for
from heddle.kernel.source_manifest import (
    ObservedPath,
    SourceChange,
    SourceComparison,
    SourceEvidence,
    changed_paths,
    compare_source_evidence,
    evidence_reference,
    normalize_paths,
)
from heddle.kernel.state import StateFile, parse_state_document
from heddle.kernel.verification import VerificationFreshness
from heddle.runtime.clock import utc_now_minutes
from heddle.runtime.diagnostics import (
    conflict_failure,
    kernel_error_result,
    write_failure,
)
from heddle.runtime.feature_context import (
    ResolveFeatureFailure,
    resolve_snapshot_from_cwd,
)
from heddle.runtime.output import emit_envelope
from heddle.runtime.recording import allocate_decision_ids
from heddle.runtime.state_store import (
    check_expect_revision,
    commit_state,
    mutation_fields,
    read_state_document,
)
from heddle.runtime.write_args import parse_write_args, usage_failure


def _slot_refusal(
    state: StateFile, role: str, row: AssignmentRound, cli: str | None
) -> KernelError:
    """Name the round's slots, and the secondary's window when it is outside."""
    policy = core.policy_at_revision(state, role, row.policy_revision)
    secondary = policy.secondary
    if (
        secondary is not None
        and secondary.cli == cli
        and not secondary_serves(policy, row.number)
    ):
        primary = next(s for s in row.slots if s.name == "primary")
        window = policy.secondary_rounds
        span = "round 1 only" if window == 1 else f"rounds 1 to {window}"
        message = (
            f"{role} round {row.number} has only the primary slot "
            f"({primary.reviewer.cli}); the secondary reviewer ({cli}) serves "
            f"{span} (secondary_rounds: {window})"
        )
        hint = "omit --cli to run the primary slot of this round"
    else:
        slots = ", ".join(f"{s.name} {s.reviewer.cli}" for s in row.slots)
        message = (
            f"requested CLI {cli} is not a confirmed slot of {role} "
            f"round {row.number} (slots: {slots})"
        )
        hint = "pass --cli for a listed slot, or omit it for the primary slot"
    return KernelError(
        code="workspace-invalid",
        message=f"review assignment: {message}",
        hint=hint,
    )


def resolve_invocation(
    snapshot: FeatureSnapshot,
    config: ProjectConfig,
    gate_type: GateType,
    overrides: GateInvocationOverrides,
    *,
    admit: bool = False,
) -> ResolvedGateInvocation:
    """Use a frozen round slot; public overrides can select, never replace it."""
    state = snapshot.state
    from heddle.runtime.readiness import select_review_milestone

    scope = (
        select_review_milestone(config, snapshot, gate_type.name)
        if gate_type.requires_milestone
        else "feature"
    )
    if scope is None:
        raise core.invalid("no current or retained milestone duty needs this review")
    assignment = core.assignment_for(state, gate_type.name, scope)
    row = core.launch_round(state, assignment)
    slot = next(
        (
            s
            for s in row.slots
            if (
                overrides.cli is None
                and s.name == "primary"
                or overrides.cli == s.reviewer.cli
            )
        ),
        None,
    )
    if slot is None:
        raise _slot_refusal(state, gate_type.name, row, overrides.cli)
    for key in ("model", "reasoning_effort"):
        explicit = getattr(overrides, key)
        if explicit is not None and explicit != getattr(slot.reviewer, key):
            raise core.invalid(f"{key} differs from the confirmed reviewer tuple")
    if admit:
        policy = core.selected_policy(state, gate_type.name)
        if policy.mode == "off":
            raise core.invalid(f"{gate_type.name} is explicitly off")
        try:
            validate_host_review_selection(
                gate_type.name,
                (slot.reviewer,),
                gates_enabled=config.gates_enabled,
                agents=config.agents,
            )
        except ValueError as error:
            raise core.invalid(str(error)) from error
        stage = ROLE_STAGES[gate_type.name]
        if STAGES.index(state.stage) < STAGES.index(stage) or STAGES.index(
            state.authorized_through
        ) < STAGES.index(stage):
            raise core.invalid(
                f"{gate_type.name} requires authorized {stage} "
                "or a later repair boundary"
            )
        current = snapshot.current_milestone
        if gate_type.requires_milestone and current is not None and scope == current.id:
            # The rule readiness applies: the current milestone's review is
            # exit evidence, so it waits for that milestone's implementation.
            pending = implementation_pending(state, state.stage)
            if pending is not None:
                details = implementation_pending_details(state, pending)
                message, hint = implementation_pending_text(details)
                raise KernelError(
                    code="task-incomplete",
                    message=f"{gate_type.name} {scope}: {message}",
                    hint=hint,
                    details=details,
                )
    invocation = resolve_gate_execution(
        gate_type, GateInvocationOverrides(**asdict(slot.reviewer))
    )
    return invocation


def changed_source_attempt_is_pending(
    state: StateFile, assignment: ReviewAssignment
) -> bool:
    """True while completed output bound to changed source awaits repair.

    Only a current-round slot whose latest attempt is that rejection is
    pending; a later attempt for the slot or a new round ends it.
    """
    current = core.launch_round(state, assignment).number
    latest = {
        attempt.reviewer_slot: attempt
        for attempt in state.review_assignments.attempts
        if attempt.assignment_id == assignment.id and attempt.round_number == current
    }
    return any(
        isinstance(attempt.outcome, EngineFailure)
        and attempt.outcome.reason
        in {
            FailureReason.REVIEWED_SOURCE_CHANGED.value,
            FailureReason.REVIEWED_SOURCE_UNAVAILABLE.value,
        }
        for attempt in latest.values()
    )


def source_projection(
    result: entry.ReviewResult,
    assignment_id: str | None,
    round_number: int | None,
    reviewer_slot: str | None,
    *,
    coverage_required: bool = True,
) -> dict[str, Any]:
    retained = {
        d.output_finding_id
        for d in result.content.prior_dispositions
        if d.disposition == "retained"
    }
    return {
        "assignment_id": assignment_id,
        "round_number": round_number,
        "reviewer_slot": reviewer_slot,
        "coverage_required": coverage_required,
        "findings": [
            {
                "finding_id": f.id,
                "severity": f.severity,
                "classification": f.classification,
            }
            for f in result.content.findings
        ],
        "new_important": sum(
            f.severity == "important"
            and f.classification == "implement"
            and f.id not in retained
            for f in result.content.findings
        ),
    }


@dataclass(frozen=True)
class ReviewEvidenceAccess:
    """The complete contained input needed to validate recorded review evidence."""

    root: Path
    workspace: str
    feature: str
    state: StateFile
    absent_evidence: frozenset[str] = frozenset()


def validate_sources(access: ReviewEvidenceAccess) -> dict[str, entry.ReviewResult]:
    """Canonical JSON plus its event is authority, never a derived Markdown file."""
    state = access.state
    results: dict[str, entry.ReviewResult] = {}
    captures = validate_retained_access(access)
    owners = {run.run_id: fact for fact in state.gates for run in fact.runs}
    sources_by_id = {
        source.run_id: source for source in state.review_assignments.sources
    }
    authoritative = tuple(
        attempt
        for attempt in state.review_assignments.attempts
        if isinstance(attempt.outcome, CanonicalReview)
        or attempt.interpretation is not None
    )
    source_ids = (
        tuple(row.attempt_id for row in authoritative)
        if state.review_assignments.attempts
        else tuple(sources_by_id)
    )
    for run_id in source_ids:
        source = sources_by_id.get(run_id)
        if source is None:
            raise core.invalid(f"review attempt has no derived source view: {run_id}")
        run = core.source_run(state, run_id)
        _, text = entry.read_contained_utf8_artifact(
            access.root / access.workspace, relative_path=run.artifact
        )
        if hashlib.sha256(text.encode()).hexdigest() != run.artifact_sha256:
            raise core.invalid(f"canonical source bytes changed: {source.run_id}")
        result = entry.retained_review_result(
            text,
            run,
            feature=access.feature,
            gate=owners[source.run_id].gate,
            scope=owners[source.run_id].scope,
        )
        canonical = entry.machine_projection_from_result(result)
        canonical_reports = tuple(
            (item["id"], item["title"], item["severity"])
            for item in canonical["report_findings"]
        )
        if (
            dict(run.verdict) != canonical["verdict"]
            or dict(run.findings) != canonical["findings"]
            or tuple(
                (item.finding_id, item.title, item.severity)
                for item in run.report_findings
            )
            != canonical_reports
        ):
            raise core.invalid(
                f"derived gate view differs from canonical review: {source.run_id}"
            )
        projected = {
            "run_id": source.run_id,
            **source_projection(
                result,
                source.assignment_id,
                source.round_number,
                source.reviewer_slot,
                coverage_required=True,
            ),
        }
        if ops.decoded_payload(source) != projected:
            raise core.invalid(f"canonical source projection changed: {source.run_id}")
        results[source.run_id] = result
    if set(source_ids) != set(sources_by_id):
        raise core.invalid("derived source views differ from authoritative attempts")
    absent_runs = {
        row.run_id
        for row in _retained_rows(state)
        if row.artifact in access.absent_evidence
    }
    for interpretation in state.review_assignments.interpretations:
        if interpretation.run_id in absent_runs:
            continue
        if (
            results[interpretation.run_id].invocation
            != captures[interpretation.run_id].invocation
        ):
            raise core.invalid(
                "interpreted result differs from its original invocation"
            )
    return results


def validate_retained(
    config: ProjectConfig, snapshot: FeatureSnapshot
) -> dict[str, entry.CapturedReview]:
    return validate_retained_access(
        ReviewEvidenceAccess(
            config.root,
            snapshot.workspace,
            snapshot.feature,
            snapshot.state,
        )
    )


def validate_retained_access(
    access: ReviewEvidenceAccess,
) -> dict[str, entry.CapturedReview]:
    captures: dict[str, entry.CapturedReview] = {}
    state = access.state
    for row in _retained_rows(state):
        if row.artifact in access.absent_evidence:
            continue
        try:
            capture = entry.read_retained_capture(access.root / access.workspace, row)
            assignment = next(
                a
                for a in state.review_assignments.assignments
                if a.id == row.assignment_id
            )
            round_ = assignment.rounds[row.round_number - 1]
            slot = next(s for s in round_.slots if s.name == row.reviewer_slot)
            invocation = capture.invocation
            if (invocation.feature, invocation.gate, invocation.scope) != (
                access.feature,
                assignment.role,
                assignment.scope,
            ) or any(
                getattr(invocation.execution, key) != getattr(slot.reviewer, key)
                for key in ("cli", "model", "reasoning_effort")
            ):
                raise core.invalid(
                    "retained response differs from its original reviewer"
                )
            captures[row.run_id] = capture
        except (OSError, ValueError, StopIteration) as error:
            raise core.invalid(
                f"retained response cannot be validated: {error}"
            ) from error
    return captures


def _retained_rows(state: StateFile) -> tuple[RetainedReview, ...]:
    if not state.review_assignments.attempts:
        return state.review_assignments.retained
    rows = tuple(
        RetainedReview(
            attempt.attempt_id,
            attempt.assignment_id,
            attempt.round_number,
            attempt.reviewer_slot,
            attempt.outcome.capture.path,
            attempt.outcome.capture.sha256,
            attempt.invocation.input_hash,
            attempt.invocation.review_basis_hash,
            attempt.created_at,
            attempt.outcome.warnings,
        )
        for attempt in state.review_assignments.attempts
        if isinstance(attempt.outcome, UsableReviewCapture)
    )
    if tuple(ops.decoded_payload(row) for row in rows) != tuple(
        ops.decoded_payload(row) for row in state.review_assignments.retained
    ):
        raise core.invalid("retained response views differ from authoritative attempts")
    return rows


def retained_response_result(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    retained: RetainedReview,
    *,
    cached: bool,
) -> HeddleResult:
    capture = validate_retained(config, snapshot)[retained.run_id]
    invocation = capture.invocation
    return HeddleResult.success(
        {
            "feature": snapshot.feature,
            "gate": invocation.gate,
            "scope": invocation.scope,
            "status": "retained",
            "accepted": False,
            "cached": cached,
            "run_id": retained.run_id,
            "revision": snapshot.state.revision,
            "findings": None,
            "artifact": snapshot.workspace + retained.artifact,
            "capture_sha256": retained.artifact_sha256,
            "invocation": asdict(invocation),
            "attempt": retained.round_number,
            "rerun_allowed": False,
            "interpretation_required": True,
        },
        exit_code=ExitCode.ADVISORY,
        diagnostics=tuple(
            Diagnostic(
                severity=Severity.ADVISORY,
                code="gate-output-structure",
                message=f"Complete {invocation.gate} output retained: {warning}. "
                "Interpret the original response; do not rerun for formatting.",
            )
            for warning in retained.warnings
        ),
        next_actions=(interpretation_action(snapshot, retained),),
    )


def interpretation_action(
    snapshot: FeatureSnapshot, retained: RetainedReview
) -> NextAction:
    return NextAction(
        ops.AuthoringAction(
            snapshot.feature,
            "review-interpretation",
            (
                retained.run_id,
                snapshot.workspace + retained.artifact,
                retained.artifact_sha256,
            ),
            "heddle.review-interpretation-input/v1",
            snapshot.state.revision,
        ),
        f"Read the full retained response {snapshot.workspace}{retained.artifact}; "
        "submit review interpret --input-json with schema "
        "heddle.review-interpretation-input/v1, "
        f"run_id {retained.run_id}, capture_sha256 {retained.artifact_sha256}, "
        "author, reason and native content. Lead interpretation earns original "
        "reviewer credit; no formatting-only provider retry.",
    )


def captured_reference(
    root: Path,
    path: str,
    *,
    plan_path: str,
    source_observations: dict[str, ObservedPath] | None = None,
) -> tuple[str, str]:
    captured = _captured_citation(
        root, path, plan_path=plan_path, source_observations=source_observations
    )
    return captured.path, _citation_digest(captured.executable, captured.content)


def _captured_citation(
    root: Path,
    path: str,
    *,
    plan_path: str,
    source_observations: dict[str, ObservedPath] | None,
) -> ObservedPath:
    normalized = normalize_paths((path,))[0]
    captures = source_observations if source_observations is not None else {}
    captured = capture_source_path(root, normalized, captures)
    if captured.kind != "file":
        raise core.invalid(f"evidence is not an available regular file: {normalized}")
    if normalized == plan_path:
        captured = authored_plan_observation(captured)
    return captured


_COMMIT_REFERENCE = re.compile(r"commit [0-9a-fA-F]{7,64}")
_COMMIT_HINT = (
    "a commit is not a reference: cite the files it changed and name the commit "
    "in reason"
)


def _reference_input_failure(
    root: Path, reference: str, error: Exception
) -> tuple[str, str]:
    """Classify a newly supplied reference that could not be captured.

    Returns the predicate and remedy. A reference that is not a repository
    path is malformed, as is a missing path that names a pytest node of an
    existing file or a commit in place of a file. Anything that exists but is
    not a readable regular file, and any other missing path, is unavailable.
    Only new input is classified this way: a recorded reference was a regular
    file when it was accepted, so a missing one is unavailable.
    """
    try:
        path = normalize_paths((reference,))[0]
    except (KernelError, ValueError) as invalid:
        return (
            "malformed-field",
            f"cite a repository-relative regular file instead of {reference}: "
            f"{invalid}",
        )
    try:
        present = (root / path).exists() or (root / path).is_symlink()
    except OSError:
        present = False
    if present:
        return (
            "reference-unavailable",
            f"cite a readable regular file in place of {reference}: {error}",
        )
    if _COMMIT_REFERENCE.fullmatch(reference):
        return "malformed-field", _COMMIT_HINT
    if reference.startswith("commit "):
        return (
            "reference-unavailable",
            f"restore {reference} and retry: {error}; {_COMMIT_HINT}",
        )
    if "::" in reference:
        cited, node = reference.split("::", 1)
        try:
            exists = (root / normalize_paths((cited,))[0]).is_file()
        except (KernelError, ValueError, OSError):
            exists = False
        if exists:
            return (
                "malformed-field",
                f"a test node is not a reference: cite {cited} and name {node} "
                "in reason",
            )
        return (
            "reference-unavailable",
            f"restore {cited} and retry: {error}; a test node is not a reference, "
            "cite its file and name the node in reason",
        )
    return "reference-unavailable", f"restore {reference} and retry: {error}"


def _citation_digest(executable: bool, content: bytes) -> str:
    return hashlib.sha256(bytes([executable]) + content).hexdigest()


def _citation_equivalence(
    path: str, executable: bool, content: bytes
) -> tuple[str, str, str] | None:
    """The rule and digest a row records for a cited rule-bearing file.

    Like the raw citation it binds the executable bit, so only a
    formatting-only content change keeps it.
    """
    identity = equivalence_identity(path, content)
    if identity is None:
        return None
    rule, digest = identity
    return path, rule, _citation_digest(executable, bytes.fromhex(digest))


def _citation_change(
    disposition: AssignmentDisposition, captured: ObservedPath, recorded: str
) -> tuple[SourceChange, ...] | None:
    """How a cited file changed since the row; None when only by formatting.

    A row recorded before citation identities names no typed change, and nor
    does a rule-bearing citation the row recorded no identity for: its raw
    difference may be formatting only.
    """
    if disposition.basis_evidence is None:
        return ()
    path = captured.path
    bound = next(
        (row for row in disposition.reference_equivalence or () if row[0] == path),
        None,
    )
    if bound is not None and bound[1] == rule_id_for(path):
        current = _citation_equivalence(path, captured.executable, captured.content)
        if current == bound:
            return None
        mode_only = bound == _citation_equivalence(
            path, not captured.executable, captured.content
        )
    else:
        mode_only = recorded == _citation_digest(
            not captured.executable, captured.content
        )
        if not mode_only and bound is None and rule_id_for(path) is not None:
            return ()
    return (SourceChange("mode" if mode_only else "content", path),)


def current_basis(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    assignment: ReviewAssignment,
    *,
    source_observations: dict[str, ObservedPath] | None = None,
) -> str:
    return current_basis_evidence(
        config, snapshot, assignment, source_observations=source_observations
    ).source_sha256


def current_basis_evidence(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    assignment: ReviewAssignment,
    *,
    source_observations: dict[str, ObservedPath] | None = None,
) -> SourceEvidence:
    """Observe the assignment's current basis manifest, raw identity only."""
    # Disposition evidence survives a model amendment, not a source amendment.
    # Invocation-specific prompt/model identity remains on the canonical run.
    # Explicit references are captured and checked by the shared qualifier.
    # Document judgments do not acquire a dependency on later implementation work.
    descriptor = GATES.get(assignment.role)
    if descriptor is None:
        raise core.invalid(f"unknown review assignment role: {assignment.role}")
    contract = input_contract_for(descriptor)
    plan_path = None
    if contract.source_selector != "none":
        paths = list(snapshot.state.feature_inputs)
        for milestone in snapshot.state.milestones:
            if assignment.scope == "feature" or milestone.id == assignment.scope:
                paths.extend(milestone.owns)
    else:
        paths = []
        if contract.requires_spec:
            paths.append(snapshot.spec_path)
        if contract.requires_plan:
            plan_path = snapshot.plan_path
            paths.append(plan_path)
        if "test-scaffolding" in contract.context_builders:
            context = entry.build_gate_context(
                snapshot,
                config,
                descriptor,
                milestone_id=None,
                basis_only=True,
                source_observations=source_observations,
            )
            # A legacy assignment keeps the recipe its rows were recorded under.
            legacy = core.legacy_assignment(snapshot.state, assignment.id)
            context = replace(
                context,
                source_baseline=None if legacy else snapshot.state.source_baseline,
            )
            paths.extend(reviewed_test_subject(context, reviewer=False).paths)
    definition = resolve_source_definition(
        config.root, "feature-inputs", tuple(set(paths))
    )
    return observe_source(
        config.root,
        definition,
        observations=source_observations,
        plan_path=plan_path,
    )


@dataclass(frozen=True)
class _EvidenceQualification:
    predicate: str | None = None
    cause: str | None = None
    field: str | None = None
    reference: str | None = None
    verification_scope: str | None = None
    status: str | None = None
    remedy: str | None = None
    changed_dependencies: tuple[str, ...] = ()
    detail_limit: str | None = None
    changes: tuple[SourceChange, ...] = ()
    # A recorded dependency changed (stale), rather than the evidence being
    # unusable as recorded (invalid).
    stale: bool = False
    # Qualified only through review equivalence, so sealing re-binds the row.
    by_equivalence: bool = False

    @property
    def qualifies(self) -> bool:
        return self.predicate is None


# What closes an open reference at a kernel stop; see
# _Qualification.open_remedies.
OPEN_RE_RECORD = "re-record"
OPEN_COVERAGE = "coverage"
OPEN_ROUND_OR_OWNER = "round-or-owner"
# The stops the kernel derives; each records a pending owner decision.
_KERNEL_STOPS = frozenset({"round-limit", "no-progress", "no-decrease"})


_VERIFICATION_SCOPE_REMEDY = (
    "record the run with heddle verify and give its scope as verification_scope; "
    "for edits not yet run under heddle verify, use evidence_kind inspection"
)
_BASIS_DETAIL_UNAVAILABLE = (
    "change detail is unavailable: the row was recorded before basis manifests, "
    "so only the assignment-level source identity is retained"
)
_CITATION_DETAIL_UNAVAILABLE = (
    "change detail is unavailable: the row was recorded before basis manifests, "
    "so only the raw citation identity is retained"
)
_CITATION_IDENTITY_UNAVAILABLE = (
    "change detail is unavailable: the row recorded no equivalence identity for "
    "this citation, so a formatting-only change cannot be told from a content change"
)
_REVIEW_DETAIL_UNAVAILABLE = (
    "change detail is unavailable: the row or its review attempt was recorded "
    "before basis manifests, so only the raw review basis is retained"
)


def _rejected_evidence(
    predicate: str,
    cause: str,
    remedy: str,
    *,
    field: str | None = None,
    reference: str | None = None,
    verification_scope: str | None = None,
    status: str | None = None,
    changed_dependencies: tuple[str, ...] = (),
    detail_limit: str | None = None,
    changes: tuple[SourceChange, ...] = (),
    stale: bool = False,
) -> _EvidenceQualification:
    return _EvidenceQualification(
        predicate=predicate,
        cause=cause,
        field=field,
        reference=reference,
        verification_scope=verification_scope,
        status=status,
        remedy=remedy,
        changed_dependencies=changed_dependencies,
        detail_limit=detail_limit,
        changes=changes,
        stale=stale,
    )


def _qualify_evidence(
    frame: _Qualification,
    disposition: AssignmentDisposition,
    basis: str | None,
    *,
    assignment: ReviewAssignment | None = None,
) -> _EvidenceQualification:
    """Judge a row's evidence: raw-current, current by review equivalence, or not.

    Its basis, each citation and any review it names count as current when raw
    identity matches or, for a row that recorded identities, when the change
    is formatting-only; the latter marks the row for re-binding at sealing.
    """
    config, snapshot, results = frame.config, frame.snapshot, frame.results
    relied = False
    if disposition.source_disposition is not None:
        # A v2 result has no retained current-source manifest. Original native
        # dispositions are preserved as history, never stamped fresh at conversion.
        return _rejected_evidence(
            "historical-evidence-unavailable",
            "historical disposition has no current source identity",
            "record new evidence against the current assignment",
            field="source_disposition",
        )
    if disposition.evidence_basis != basis:
        comparison = frame.basis_comparison(
            assignment or frame.assignment_of(disposition), disposition
        )
        if comparison is None or not comparison.equivalent:
            changes = comparison.changes if comparison is not None else ()
            return _rejected_evidence(
                "assignment-basis-changed",
                "governing review source changed",
                "refresh evidence against the current assignment source",
                field="evidence_basis",
                changed_dependencies=changed_paths(changes),
                detail_limit=(
                    None if comparison is not None else _BASIS_DETAIL_UNAVAILABLE
                ),
                changes=changes,
                stale=True,
            )
        relied = True
    if not disposition.references or disposition.evidence_kind == "unavailable":
        return _rejected_evidence(
            "missing-evidence",
            "affirmative supporting evidence is unavailable",
            "supply at least one current affirmative evidence reference",
            field="references",
        )
    for reference in disposition.references:
        try:
            captured = _captured_citation(
                config.root,
                reference[0],
                plan_path=snapshot.plan_path,
                source_observations=frame.captures,
            )
        except (KernelError, OSError, ValueError) as error:
            return _rejected_evidence(
                "reference-unavailable",
                "referenced evidence is unavailable",
                f"restore {reference[0]} and retry: {error}",
                field="references",
                reference=reference[0],
            )
        digest = _citation_digest(captured.executable, captured.content)
        if (captured.path, digest) == reference:
            continue
        cited = _citation_change(disposition, captured, reference[1])
        if cited is None:
            relied = True
            continue
        return _rejected_evidence(
            "reference-changed",
            "referenced evidence changed",
            f"review the current {reference[0]} and resubmit its evidence",
            field="references",
            reference=reference[0],
            changed_dependencies=(reference[0],),
            detail_limit=(
                None
                if cited
                else _CITATION_DETAIL_UNAVAILABLE
                if disposition.basis_evidence is None
                else _CITATION_IDENTITY_UNAVAILABLE
            ),
            changes=cited,
            stale=True,
        )
    if disposition.evidence_kind == "verification":
        from heddle.runtime.verification import (
            STALE_FRESHNESS,
            assess_current_verifications,
        )

        scope = disposition.verification_scope
        if not isinstance(scope, str) or not scope:
            return _rejected_evidence(
                "verification-scope-missing",
                "verification evidence has no scope",
                _VERIFICATION_SCOPE_REMEDY,
                field="verification_scope",
            )
        try:
            if scope not in frame.verifications:
                frame.verifications[scope] = assess_current_verifications(
                    config.root,
                    snapshot.state,
                    (scope,),
                    source_observations=frame.captures,
                )
        except KernelError as error:
            return _rejected_evidence(
                "verification-not-qualifying",
                "verification evidence cannot be assessed",
                error.hint,
                field="verification_scope",
                reference=scope,
                verification_scope=scope,
            )
        checks = frame.verifications[scope]
        rejected = next((row for row in checks if not row.authorizes), None)
        if not checks or rejected is not None:
            status = rejected.status if rejected is not None else "missing"
            # The row follows its verification: stale when a later change
            # made it stale, invalid otherwise, with that change's detail.
            explained = rejected.explanation if rejected is not None else None
            changes = explained.changes if explained is not None else ()
            return _rejected_evidence(
                "verification-not-qualifying",
                "verification evidence does not authorize this disposition",
                f"repair or refresh verification for {scope} ({status})",
                field="verification_scope",
                reference=scope,
                verification_scope=scope,
                status=status,
                changed_dependencies=changed_paths(changes),
                changes=changes,
                stale=status in STALE_FRESHNESS,
            )
        return _EvidenceQualification(by_equivalence=relied)
    if disposition.evidence_kind == "decision":
        if any(
            d.id == disposition.decision_id
            and d.status == "resolved"
            and d.resolution_source == "user"
            and (d.origin_run_id, d.origin_finding_id)
            == (disposition.run_id, disposition.finding_id)
            for d in snapshot.state.decisions
        ):
            return _EvidenceQualification(by_equivalence=relied)
        return _rejected_evidence(
            "decision-not-qualifying",
            "decision evidence is not a resolved user decision",
            "supply the resolved user decision for this original concern",
            field="decision_id",
            reference=disposition.decision_id,
        )
    if disposition.evidence_kind == "review":
        proof = results.get(cast(str, disposition.review_run_id))
        if proof is None:
            return _rejected_evidence(
                "review-not-qualifying",
                "review evidence has no valid canonical result",
                "supply a current later-round review run",
                field="review_run_id",
                reference=disposition.review_run_id,
            )
        original = core.source_run(snapshot.state, disposition.run_id)
        reviewer = core.source_run(snapshot.state, cast(str, disposition.review_run_id))
        if core.inspection_identity(
            snapshot.state, original.run_id
        ) != core.inspection_identity(
            snapshot.state, reviewer.run_id, origin=original.run_id
        ):
            return _rejected_evidence(
                "review-not-qualifying",
                "review evidence does not inspect the original concern",
                "use a later assignment round that explicitly inspects this origin",
                field="review_run_id",
                reference=disposition.review_run_id,
            )
        descriptor = GATES.get(proof.invocation.gate)
        if descriptor is None:
            return _rejected_evidence(
                "review-not-qualifying",
                "review evidence uses a retired role",
                "run the current assigned review role",
                field="review_run_id",
                reference=disposition.review_run_id,
            )
        assert reviewer.execution_config is not None
        invocation = resolve_gate_execution(
            descriptor,
            GateInvocationOverrides(
                cli=cast(Any, reviewer.execution_config["cli"]),
                model=reviewer.execution_config["model"],
                reasoning_effort=cast(
                    Any, reviewer.execution_config["reasoning_effort"]
                ),
            ),
        )
        if reviewer.run_id not in frame.review_bases:
            context = entry.build_gate_context(
                snapshot,
                config,
                descriptor,
                cli=reviewer.cli,
                milestone_id=proof.invocation.scope
                if descriptor.requires_milestone
                else None,
                basis_only=True,
                source_observations=frame.captures,
            )
            frame.review_bases[reviewer.run_id] = entry.prepare_current_review_manifest(
                context, invocation
            )
        reviewed_basis, current_manifest = frame.review_bases[reviewer.run_id]
        if reviewer.review_basis_hash != reviewed_basis.review_basis_hash:
            # A row recorded before basis manifests compares raw, like its basis.
            comparison = (
                frame.review_comparison(reviewer.run_id, current_manifest)
                if disposition.basis_evidence is not None
                else None
            )
            if comparison is None or not comparison.equivalent:
                changes = comparison.changes if comparison is not None else ()
                return _rejected_evidence(
                    "review-not-qualifying",
                    "review evidence source changed",
                    "refresh the later-round review against current source",
                    field="review_run_id",
                    reference=disposition.review_run_id,
                    changed_dependencies=changed_paths(changes),
                    detail_limit=(
                        None if comparison is not None else _REVIEW_DETAIL_UNAVAILABLE
                    ),
                    changes=changes,
                    stale=True,
                )
            relied = True
        if any(
            (row.source.run_id, row.source.finding_id)
            == (disposition.run_id, disposition.finding_id)
            and row.disposition in {"addressed", "settled"}
            and row.evidence.kind not in {"absence", "unavailable", "speculation"}
            and bool(row.evidence.references)
            for row in proof.content.prior_dispositions
        ):
            return _EvidenceQualification(by_equivalence=relied)
        return _rejected_evidence(
            "review-not-qualifying",
            "review does not affirmatively disposition the original concern",
            "use a review result with affirmative evidence for this origin",
            field="review_run_id",
            reference=disposition.review_run_id,
        )
    return _EvidenceQualification(by_equivalence=relied)


def _qualify_resolution(
    frame: _Qualification,
    disposition: AssignmentDisposition,
    basis: str | None,
    *,
    assignment: ReviewAssignment | None = None,
) -> _EvidenceQualification:
    qualification = _qualify_evidence(frame, disposition, basis, assignment=assignment)
    if (
        qualification.qualifies
        and disposition.requires_inspection
        and disposition.evidence_kind != "review"
    ):
        return _rejected_evidence(
            "reviewer-inspection-not-qualifying",
            "required originating inspection is unavailable",
            "supply a qualifying later-round review for required inspection",
            field="evidence_kind",
            reference=disposition.review_run_id,
        )
    return qualification


def _decision_route(
    state: StateFile,
    reference: tuple[str, str],
    status: str,
    decision_id: str | None,
) -> _EvidenceQualification:
    """Refuse an affirmative row the closure rule never counts for a REPORT."""
    origin = next(
        (s for s in core.authoritative_sources(state) if s.run_id == reference[0]),
        None,
    )
    if status not in {"addressed", "settled"} or origin is None:
        return _EvidenceQualification()
    assignment = next(
        row
        for row in state.review_assignments.assignments
        if row.id == origin.assignment_id
    )
    facts = core.closure_facts(
        state, assignment, current_basis="", valid_dispositions=()
    )
    source = next(
        (row for row in facts.obligations if (row.run_id, row.finding_id) == reference),
        None,
    )
    if (
        source is None
        or source.classification != "report"
        or not required_obligation(source)
        or settles_decision_route(
            source,
            ReviewResolution(*reference, status, (), None, "", decision_id=decision_id),
            facts.decisions,
        )
    ):
        return _EvidenceQualification()
    return _rejected_evidence(
        "decision-routed",
        "a REPORT finding closes only through its resolved owner decision",
        f"once decision {source.decision_id} is resolved, record status settled "
        f"with evidence_kind decision and decision_id {source.decision_id}; "
        "until then record awaiting-decision",
        field="decision_id",
        reference=source.decision_id,
    )


def _closing_qualification(
    frame: _Qualification,
    disposition: AssignmentDisposition,
    basis: str,
    *,
    assignment: ReviewAssignment | None = None,
) -> _EvidenceQualification:
    """Judge a recorded row by the evidence and route checks applied at input."""
    result = _qualify_evidence(frame, disposition, basis, assignment=assignment)
    if not result.qualifies:
        return result
    return _decision_route(
        frame.snapshot.state,
        (disposition.run_id, disposition.finding_id),
        disposition.status,
        disposition.decision_id,
    )


def _evidence_explanation(
    frame: _Qualification,
    disposition: AssignmentDisposition,
    basis: str,
    remedy: ops.Action,
) -> EvidenceExplanation:
    """Project the shared qualification result into readiness explanation."""
    return _explanation(
        disposition, _closing_qualification(frame, disposition, basis), remedy
    )


def _explanation(
    disposition: AssignmentDisposition,
    result: _EvidenceQualification,
    remedy: ops.Action,
) -> EvidenceExplanation:
    return EvidenceExplanation(
        subject=f"{disposition.run_id}#{disposition.finding_id}",
        cause=result.cause or "recorded supporting evidence is no longer current",
        changed_dependencies=result.changed_dependencies,
        failed_reference=(
            result.reference if result.predicate == "reference-unavailable" else None
        ),
        remedy=remedy,
        detail_limit=result.detail_limit,
        changes=result.changes,
    )


def reviewer_basis_manifest(
    workspace: Path, state: StateFile, run_id: str
) -> SourceEvidence | None:
    """The reviewer-basis manifest a recorded attempt names, read intact.

    It is the attempt's one source-evidence ``evidence`` artifact with named
    components; None when there is not exactly one, as for an attempt recorded
    before them. A named manifest that is missing or altered is an integrity
    failure naming the attempt and the artifact.
    """
    from heddle.runtime.verification import read_named_source_evidence

    attempt = next(
        (row for row in state.review_assignments.attempts if row.attempt_id == run_id),
        None,
    )
    if attempt is None:
        return None
    manifests = []
    for artifact in core.reviewer_basis_artifacts(attempt):
        evidence = read_named_source_evidence(
            workspace,
            artifact.path,
            artifact.sha256,
            subject=f"review attempt {run_id}",
        )
        if evidence is not None and evidence.components:
            manifests.append(evidence)
    return manifests[0] if len(manifests) == 1 else None


class _Qualification:
    """One immutable snapshot observation, discarded before mutation or execution."""

    def __init__(
        self,
        config: ProjectConfig,
        snapshot: FeatureSnapshot,
        *,
        source_observations: dict[str, ObservedPath] | None = None,
    ) -> None:
        self.config, self.snapshot = config, snapshot
        self.captures = source_observations if source_observations is not None else {}
        self.results = validate_sources(
            ReviewEvidenceAccess(
                config.root,
                snapshot.workspace,
                snapshot.feature,
                snapshot.state,
            )
        )
        self.basis_manifests: dict[str, SourceEvidence] = {}
        self.stamped_bases: dict[str, SourceEvidence] = {}
        self.recorded_manifests: dict[str, SourceEvidence] = {}
        self.basis_comparisons: dict[tuple[str, str], SourceComparison] = {}
        self.assessments: dict[str, ReviewClosure] = {}
        self.qualified_dispositions: dict[str, tuple[AssignmentDisposition, ...]] = {}
        self.qualified_disposition_indexes: dict[str, tuple[int, ...]] = {}
        self.rebinding_indexes: dict[str, tuple[int, ...]] = {}
        self.verifications: dict[str, tuple[VerificationFreshness, ...]] = {}
        self.review_bases: dict[
            str, tuple[entry.ReviewBasis, SourceEvidence | None]
        ] = {}
        self.review_comparisons: dict[str, SourceComparison | None] = {}

    def basis(self, assignment: ReviewAssignment) -> str:
        return self.basis_evidence(assignment).source_sha256

    def basis_evidence(self, assignment: ReviewAssignment) -> SourceEvidence:
        """The assignment's one current basis observation, raw identity only."""
        if assignment.id not in self.basis_manifests:
            self.basis_manifests[assignment.id] = current_basis_evidence(
                self.config,
                self.snapshot,
                assignment,
                source_observations=self.captures,
            )
        return self.basis_manifests[assignment.id]

    def stamped_basis(self, assignment: ReviewAssignment) -> SourceEvidence:
        """The current basis manifest as a write records and publishes it."""
        if assignment.id not in self.stamped_bases:
            from heddle.runtime.verification import stamp_recorded_equivalence

            self.stamped_bases[assignment.id] = stamp_recorded_equivalence(
                self.config.root, self.basis_evidence(assignment)
            )
        return self.stamped_bases[assignment.id]

    def basis_reference(
        self, assignment: ReviewAssignment, *, stamped: bool = True
    ) -> ManifestReference:
        """The reference a written row names; a dry run computes it unpublished.

        Unstamped, it names the basis as this frame observed it, so a read
        that only judges a reaffirm candidate never stamps.
        """
        reference = evidence_reference(
            self.stamped_basis(assignment)
            if stamped
            else self.basis_evidence(assignment)
        )
        return ManifestReference(
            reference.schema,
            reference.artifact,
            reference.artifact_sha256,
            reference.source_sha256,
        )

    def basis_comparison(
        self, assignment: ReviewAssignment, disposition: AssignmentDisposition
    ) -> SourceComparison | None:
        """A row's basis manifest against the current basis; None when it names none."""
        reference = disposition.basis_evidence
        if reference is None:
            return None
        key = (assignment.id, reference.artifact)
        if key not in self.basis_comparisons:
            from heddle.runtime.verification import (
                read_named_source_evidence,
                stamp_changed_equivalence,
            )

            recorded = self.recorded_manifests.get(reference.artifact)
            if recorded is None:
                # A named manifest is required evidence: absence is an integrity
                # failure, never a fallback to the unavailable detail.
                recorded = cast(
                    SourceEvidence,
                    read_named_source_evidence(
                        self.config.root / self.snapshot.workspace,
                        reference.artifact,
                        reference.artifact_sha256,
                        subject=(
                            f"disposition {disposition.run_id}#{disposition.finding_id}"
                        ),
                        source_sha256=reference.source_sha256,
                    ),
                )
                self.recorded_manifests[reference.artifact] = recorded
            current = self.basis_evidence(assignment)
            self.basis_comparisons[key] = compare_source_evidence(
                recorded,
                stamp_changed_equivalence(self.config.root, current, recorded),
            )
        return self.basis_comparisons[key]

    def review_comparison(
        self, run_id: str, current: SourceEvidence | None
    ) -> SourceComparison | None:
        """A review attempt's recorded reviewer basis against the current one.

        None when the attempt names no single reviewer-basis manifest, as an
        attempt recorded before them does.
        """
        if run_id not in self.review_comparisons:
            from heddle.runtime.verification import stamp_changed_equivalence

            recorded = reviewer_basis_manifest(
                self.config.root / self.snapshot.workspace,
                self.snapshot.state,
                run_id,
            )
            self.review_comparisons[run_id] = (
                None
                if recorded is None or current is None
                else compare_source_evidence(
                    recorded,
                    stamp_changed_equivalence(self.config.root, current, recorded),
                )
            )
        return self.review_comparisons[run_id]

    def assignment_of(self, disposition: AssignmentDisposition) -> ReviewAssignment:
        state = self.snapshot.state
        assignment_id = next(
            (
                source.assignment_id
                for source in core.authoritative_sources(state)
                if source.run_id == disposition.run_id
            ),
            None,
        )
        for assignment in state.review_assignments.assignments:
            if assignment.id == assignment_id:
                return assignment
        raise core.invalid(
            f"disposition has no review assignment: {disposition.run_id}"
        )

    def effective_dispositions(
        self, assignment: ReviewAssignment
    ) -> tuple[tuple[int, AssignmentDisposition], ...]:
        state = self.snapshot.state
        source_ids = {
            s.run_id
            for s in core.authoritative_sources(state)
            if s.assignment_id == assignment.id
        }
        latest = {
            (d.run_id, d.finding_id): (index, d)
            for index, d in enumerate(state.review_assignments.dispositions)
            if d.run_id in source_ids
        }
        return tuple(latest.values())

    def assess(self, assignment: ReviewAssignment) -> ReviewClosure:
        if assignment.id not in self.assessments:
            basis = self.basis(assignment)
            state = self.snapshot.state
            judged = tuple(
                (index, d, _qualify_evidence(self, d, basis, assignment=assignment))
                for index, d in self.effective_dispositions(assignment)
            )
            qualified_rows = tuple(row for row in judged if row[2].qualifies)
            qualified = tuple(d for _index, d, _result in qualified_rows)
            self.qualified_dispositions[assignment.id] = qualified
            self.qualified_disposition_indexes[assignment.id] = tuple(
                index for index, _d, _result in qualified_rows
            )
            self.rebinding_indexes[assignment.id] = tuple(
                index for index, _d, result in qualified_rows if result.by_equivalence
            )
            # A qualified row away from the current basis passed only because
            # its manifest is review-equivalent to the current one.
            equivalent = frozenset(
                d.evidence_basis
                for d in qualified
                if d.evidence_basis is not None and d.evidence_basis != basis
            )
            facts = core.closure_facts(
                state,
                assignment,
                current_basis=basis,
                valid_dispositions=qualified,
                equivalent_bases=equivalent,
            )
            self.assessments[assignment.id] = assess_review_closure(facts)
        return self.assessments[assignment.id]

    def explain_open(
        self,
        assignment: ReviewAssignment,
        open_refs: tuple[tuple[str, str], ...],
        remedy: ops.Action,
    ) -> tuple[tuple[EvidenceExplanation, bool], ...]:
        """Explain reopened original work without inventing unavailable detail.

        Each explanation is paired with whether a recorded dependency changed.
        """
        latest = self._latest_dispositions(assignment)
        basis = self.basis(assignment)
        return tuple(
            (_explanation(disposition, result, remedy), result.stale)
            for reference in open_refs
            if (disposition := latest.get(reference)) is not None
            and not (
                result := _closing_qualification(
                    self, disposition, basis, assignment=assignment
                )
            ).qualifies
        )

    def open_remedies(
        self,
        assignment: ReviewAssignment,
        open_refs: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        """Classify each open reference by the work that can close it.

        ``re-record``: the lead re-records the row's evidence, after a rerun or
        a fresh citation where one changed; reaffirm writes a row that still
        qualifies. ``coverage``: the latest round's coverage, not yet disposed.
        ``round-or-owner``: anything else, which keeps a kernel stop.

        A row is judged at the current basis, as reaffirm judges it, and as
        re-cited where a citation changed, so neither change hides evidence
        that fails on its own.
        """
        state = self.snapshot.state
        latest_round = {
            source.run_id
            for source in core.authoritative_sources(state)
            if source.assignment_id == assignment.id
            and source.round_number == len(assignment.rounds)
        }
        latest = self._latest_dispositions(assignment)
        basis = self.basis(assignment)
        reference: ManifestReference | None = None
        remedies = []
        for run_id, finding_id in open_refs:
            disposition = latest.get((run_id, finding_id))
            if disposition is None:
                remedies.append(
                    OPEN_COVERAGE
                    if finding_id == "@coverage" and run_id in latest_round
                    else OPEN_ROUND_OR_OWNER
                )
                continue
            if disposition.status not in {"addressed", "settled"} or (
                _closing_qualification(
                    self, disposition, basis, assignment=assignment
                ).qualifies
            ):
                # Qualifying evidence the kernel still holds open owes a duty
                # re-recording cannot discharge.
                remedies.append(OPEN_ROUND_OR_OWNER)
                continue
            if reference is None:
                reference = self.basis_reference(assignment, stamped=False)
            remedies.append(
                self._resolution_remedy(assignment, disposition, basis, reference)
            )
        return tuple(remedies)

    def _resolution_remedy(
        self,
        assignment: ReviewAssignment,
        disposition: AssignmentDisposition,
        basis: str | None,
        reference: ManifestReference,
    ) -> str:
        """Whether re-recording closes one stale affirmative row."""
        route = _decision_route(
            self.snapshot.state,
            (disposition.run_id, disposition.finding_id),
            disposition.status,
            disposition.decision_id,
        )
        if not route.qualifies:
            return OPEN_ROUND_OR_OWNER
        candidate = _reaffirm_candidate(disposition, basis, reference)
        result = _qualify_resolution(self, candidate, basis, assignment=assignment)
        if result.qualifies:
            return OPEN_RE_RECORD
        # Refreshing review evidence or an inspection duty needs a new round.
        if (
            not result.stale
            or disposition.requires_inspection
            or disposition.evidence_kind == "review"
        ):
            return OPEN_ROUND_OR_OWNER
        if result.predicate != "reference-changed":
            return OPEN_RE_RECORD
        # Citations are judged first, so a changed one hides what the rest of
        # the evidence now shows; judge the row as the lead would re-cite it.
        recited = self._recited(candidate)
        if recited is None:
            return OPEN_ROUND_OR_OWNER
        result = _qualify_resolution(self, recited, basis, assignment=assignment)
        return (
            OPEN_RE_RECORD if result.qualifies or result.stale else OPEN_ROUND_OR_OWNER
        )

    def _recited(
        self, disposition: AssignmentDisposition
    ) -> AssignmentDisposition | None:
        """The row citing each of its paths as it reads now, or None if one can't be
        captured."""
        try:
            references = tuple(
                captured_reference(
                    self.config.root,
                    path,
                    plan_path=self.snapshot.plan_path,
                    source_observations=self.captures,
                )
                for path, _digest in disposition.references
            )
        except (KernelError, OSError, ValueError):
            return None
        return replace(disposition, references=references, reference_equivalence=None)

    def _latest_dispositions(
        self, assignment: ReviewAssignment
    ) -> dict[tuple[str, str], AssignmentDisposition]:
        source_ids = {
            source.run_id
            for source in core.authoritative_sources(self.snapshot.state)
            if source.assignment_id == assignment.id
        }
        return {
            (item.run_id, item.finding_id): item
            for item in self.snapshot.state.review_assignments.dispositions
            if item.run_id in source_ids
        }


def _snapshot_with_state(
    snapshot: FeatureSnapshot, state: StateFile
) -> FeatureSnapshot:
    current_milestone = next(
        (milestone for milestone in state.milestones if milestone.status == "current"),
        None,
    )
    current_task = (
        next(
            (task for task in current_milestone.tasks if task.status == "current"),
            None,
        )
        if current_milestone is not None
        else None
    )
    return replace(
        snapshot,
        feature=state.feature,
        spec_path=state.spec,
        stage=state.stage,
        authorized_through=state.authorized_through,
        authorizations=state.authorizations,
        current_milestone=current_milestone,
        current_task=current_task,
        owned_paths=current_milestone.owns if current_milestone is not None else (),
        verification_commands=state.commands,
        state=state,
    )


def _qualifying_verification(
    frame: _Qualification, scope: str
) -> VerificationFreshness:
    from heddle.runtime.verification import assess_current_verifications

    if scope not in frame.verifications:
        frame.verifications[scope] = assess_current_verifications(
            frame.config.root,
            frame.snapshot.state,
            (scope,),
            source_observations=frame.captures,
        )
    result = next(
        (row for row in frame.verifications[scope] if row.scope == scope),
        None,
    )
    if result is None or not result.authorizes or result.fact_index is None:
        status = result.status if result is not None else "missing"
        raise core.invalid(
            f"acceptance verification {scope} is not current and passing: {status}"
        )
    return result


def seal_boundary(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    document: dict[str, Any],
    sealed: dict[str, Any],
    *,
    boundary: str,
    scope: str | None,
    stage_after: str,
    accepted_at: str,
    publish: bool = True,
) -> list[dict[str, Any]]:
    """Seal a boundary into the transition ``sealed`` rendered from ``document``.

    Each effective row current only by review equivalence gains a re-binding
    row at the current basis, appended before the receipt that records it, so
    one write re-binds and seals. Returns those rows; without ``publish`` the
    basis manifest they name is computed but not published.
    """
    rows = _rebinding_rows(
        config,
        snapshot,
        document,
        boundary=boundary,
        scope=scope,
        accepted_at=accepted_at,
        publish=publish,
    )
    source = document
    if rows:
        source = deepcopy(document)
        source["review_assignments"]["dispositions"].extend(deepcopy(rows))
        dispositions = sealed["review_assignments"]["dispositions"]
        if dispositions != document["review_assignments"]["dispositions"]:
            raise core.invalid("a sealing transition must not rewrite dispositions")
        dispositions.extend(deepcopy(rows))
    receipt = build_boundary_acceptance(
        config,
        snapshot,
        source,
        boundary=boundary,
        scope=scope,
        stage_after=stage_after,
        accepted_at=accepted_at,
    )
    if receipt is not None:
        sealed["review_assignments"]["acceptances"].append(receipt)
    return rows


def _rebinding_rows(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    document: dict[str, Any],
    *,
    boundary: str,
    scope: str | None,
    accepted_at: str,
    publish: bool,
) -> list[dict[str, Any]]:
    state = parse_state_document(document, source=Path("state.yaml"))
    assignment = _boundary_assignment(state, boundary, scope)
    if assignment is None:
        return []
    frame = _Qualification(config, _snapshot_with_state(snapshot, state))
    if not frame.assess(assignment).closed:
        # The receipt build refuses an open assignment with its own reason.
        return []
    reference = frame.basis_reference(assignment)
    rows = [
        ops.decoded_payload(
            replace(
                state.review_assignments.dispositions[index],
                evidence_basis=frame.basis(assignment),
                basis_evidence=reference,
                rebound_by=REBOUND_BY_EQUIVALENCE,
                at=accepted_at,
            )
        )
        for index in frame.rebinding_indexes[assignment.id]
    ]
    if rows and publish:
        _publish_basis_manifests(frame, (assignment,))
    return rows


def _boundary_assignment(
    state: StateFile, boundary: str, scope: str | None
) -> ReviewAssignment | None:
    """The assignment a boundary seals; None when its review is off."""
    role = core.BOUNDARY_ROLES.get(boundary)
    if role is None:
        raise core.invalid(f"unknown acceptance boundary: {boundary}")
    assignment_scope = scope or "feature"
    assignment = next(
        (
            row
            for row in state.review_assignments.assignments
            if (row.role, row.scope) == (role, assignment_scope)
        ),
        None,
    )
    if assignment is None and core.selected_policy(state, role).mode != "off":
        raise core.invalid(
            f"completed {boundary} duty has no materialized review assignment"
        )
    return assignment


def build_boundary_acceptance(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    document: dict[str, Any],
    *,
    boundary: str,
    scope: str | None,
    stage_after: str,
    accepted_at: str,
) -> dict[str, Any] | None:
    """Build one receipt from the locked pre-transition document."""
    state = parse_state_document(document, source=Path("state.yaml"))
    exact_snapshot = _snapshot_with_state(snapshot, state)
    assignment = _boundary_assignment(state, boundary, scope)
    if assignment is None:
        return None

    frame = _Qualification(config, exact_snapshot)
    closure = frame.assess(assignment)
    if not closure.closed:
        raise core.invalid(
            f"review assignment {assignment.id} is not qualified for acceptance"
        )
    dispositions = frame.qualified_dispositions[assignment.id]
    records = [
        AcceptanceRecord(
            "dispositions",
            index,
            core.acceptance_record_digest(state.review_assignments.dispositions[index]),
        )
        for index in frame.qualified_disposition_indexes[assignment.id]
    ]

    decision_ids = {
        row.decision_id for row in dispositions if row.decision_id is not None
    }
    verification_scopes = {
        row.verification_scope
        for row in dispositions
        if row.verification_scope is not None
    }
    if boundary == "milestone" and scope is not None:
        verification_scopes.add(scope)
    verification_results = {
        verification_scope: _qualifying_verification(frame, verification_scope)
        for verification_scope in verification_scopes
    }
    decision_ids.update(
        result.accepted_decision
        for result in verification_results.values()
        if result.accepted_decision is not None
    )
    records.extend(
        AcceptanceRecord("decisions", index, core.acceptance_record_digest(row))
        for index, row in enumerate(state.decisions)
        if row.id in decision_ids
    )
    records.extend(
        AcceptanceRecord(
            "verifications",
            result.fact_index,
            core.acceptance_record_digest(state.verifications[result.fact_index]),
        )
        for result in verification_results.values()
        if result.fact_index is not None
    )
    records.sort(key=lambda row: (row.family, row.index))

    sources = tuple(
        source
        for source in core.authoritative_sources(state)
        if source.assignment_id == assignment.id
    )
    entry = AcceptedAssignment(
        assignment_id=assignment.id,
        policy_revision=assignment.policy_revision,
        source_identity=frame.basis(assignment),
        round_numbers=tuple(sorted({source.round_number for source in sources})),
        attempt_ids=tuple(sorted(source.run_id for source in sources)),
        records=tuple(records),
        digest="",
    )
    entry = replace(entry, digest=core.accepted_assignment_digest(entry))
    receipt = BoundaryAcceptance(
        feature=state.feature,
        boundary=boundary,
        scope=scope,
        stage_before=state.stage,
        stage_after=stage_after,
        accepted_at=accepted_at,
        revision_before=state.revision,
        revision_after=state.revision + 1,
        assignments=(entry,),
    )
    return ops.decoded_payload(receipt)


def _stop_needs_only_rerecording(
    frame: _Qualification, assignment: ReviewAssignment, closed: ReviewClosure
) -> bool:
    """A kernel stop whose open work closes by re-recording evidence alone.

    At a round limit the latest round's undisposed coverage counts too; every
    other open reference keeps the stop for its round or owner decision.
    """
    if closed.next_step != "decision" or not closed.open_refs:
        return False
    allowed = {OPEN_RE_RECORD}
    if closed.stop_reason == "round-limit":
        allowed.add(OPEN_COVERAGE)
    return set(frame.open_remedies(assignment, closed.open_refs)) <= allowed


def projection(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    *,
    source_observations: dict[str, ObservedPath] | None = None,
) -> dict[str, Any]:
    from heddle.contracts.review_assignments import ROLE_STAGES
    from heddle.contracts.schemas import STAGES
    from heddle.kernel.feature_policy import effective_policy

    policy = snapshot.state.feature_policy
    if policy is None:
        return {}
    frame = _Qualification(config, snapshot, source_observations=source_observations)
    reaffirm: _ReaffirmWork | None = None
    accepted = core.validate_boundary_acceptances(snapshot.state)
    assignments = []
    for selection in policy.entries:
        scopes = (
            ("feature",)
            if selection.scope == "feature"
            else tuple(m.id for m in snapshot.state.milestones)
        )
        for scope in scopes:
            assignment = core.assignment_for(snapshot.state, selection.role, scope)
            receipt = accepted.get(assignment.id)
            evidence_basis: str | None
            has_accepted_source = any(
                source.assignment_id == assignment.id
                for source in core.authoritative_sources(snapshot.state)
            )
            basis_applies = has_accepted_source or (
                selection.mode != "off"
                and STAGES.index(ROLE_STAGES[selection.role])
                <= STAGES.index(snapshot.state.stage)
            )
            if receipt is not None:
                accepted_entry = next(
                    entry
                    for entry in receipt.assignments
                    if entry.assignment_id == assignment.id
                )
                referenced_dispositions = tuple(
                    snapshot.state.review_assignments.dispositions[record.index]
                    for record in accepted_entry.records
                    if record.family == "dispositions"
                )
                closed = assess_review_closure(
                    core.closure_facts(
                        snapshot.state,
                        assignment,
                        current_basis=accepted_entry.source_identity,
                        valid_dispositions=referenced_dispositions,
                        historical_complete=True,
                    )
                )
                evidence_basis = accepted_entry.source_identity
                evidence_state = "accepted"
            elif basis_applies:
                try:
                    closed = frame.assess(assignment)
                    evidence_basis = frame.basis(assignment)
                    evidence_state = "current" if has_accepted_source else "missing"
                except KernelError as error:
                    if error.reason != entry.BASELINE_UNAVAILABLE:
                        raise
                    # The subject cannot be resolved against an unavailable
                    # baseline: the assignment reads unresolved, and readiness
                    # names the re-point.
                    basis_applies = False
                    closed = assess_review_closure(
                        core.closure_facts(
                            snapshot.state,
                            assignment,
                            current_basis="",
                            valid_dispositions=(),
                        )
                    )
                    evidence_basis = None
                    evidence_state = "missing"
            else:
                # Off and future assignments have no current implementation-source
                # obligation.  Assess their schedule/history without manufacturing
                # an empty source identity; once the assignment becomes active,
                # the ordinary qualified path still requires declared scope.
                closed = assess_review_closure(
                    core.closure_facts(
                        snapshot.state,
                        assignment,
                        current_basis="",
                        valid_dispositions=(),
                    )
                )
                evidence_basis = None
                evidence_state = (
                    "not-applicable"
                    if selection.mode == "off"
                    and STAGES.index(ROLE_STAGES[selection.role])
                    <= STAGES.index(snapshot.state.stage) + 1
                    else "scope-not-defined"
                )
            owners = [
                d.id
                for d in snapshot.state.decisions
                if d.status == "pending"
                and (d.origin_run_id, d.origin_finding_id) in closed.open_refs
            ]
            owner = owners[0] if owners else None
            stop_decision = next(
                (
                    decision
                    for decision in snapshot.state.decisions
                    if decision.id == assignment.stop_decision_id
                ),
                None,
            )
            if (
                owner is None
                and stop_decision is not None
                and stop_decision.status == "pending"
            ):
                owner = stop_decision.id
                owners = [owner]
            row = {
                "assignment_id": assignment.id,
                "role": assignment.role,
                "scope": assignment.scope,
                "policy_revision": assignment.policy_revision,
                **ops.decoded_payload(closed),
                "decision_id": owner,
                "decision_ids": owners,
                "evidence_state": evidence_state,
                "evidence_basis": evidence_basis,
            }
            if receipt is not None:
                row["acceptance"] = {
                    "boundary": receipt.boundary,
                    "scope": receipt.scope,
                    "source_identity": evidence_basis,
                    "revision": receipt.revision_after,
                    "accepted_at": receipt.accepted_at,
                }
            if (
                owner is not None
                and assignment.stop_decision_id == owner
                and not closed.closed
                and not closed.missing_slots
            ):
                decision = next(d for d in snapshot.state.decisions if d.id == owner)
                prefix = f"{assignment.role} stopped: "
                recorded_stop = (
                    decision.title.removeprefix(prefix)
                    if decision.title.startswith(prefix)
                    else None
                )
                if recorded_stop in _KERNEL_STOPS:
                    row["stop_reason"] = recorded_stop
            pending = core.pending_retained(snapshot.state, assignment.id)
            row["retained_responses"] = [ops.decoded_payload(r) for r in pending]
            row["calls_completed"] += len(pending)
            if pending:
                row["next_step"] = "interpret"
            elif owner and not closed.missing_slots:
                row["next_step"] = "decision"
            elif (
                stop_decision is not None
                and stop_decision.status == "resolved"
                and isinstance(stop_decision.resolution, Disposition)
                and not closed.closed
                and not closed.missing_slots
            ):
                row["next_step"] = "disposition"
            elif (
                receipt is None
                and basis_applies
                and _stop_needs_only_rerecording(frame, assignment, closed)
            ):
                row["next_step"] = "disposition"
            elif closed.stop_reason == "round-limit" and core.continuation_authorized(
                snapshot.state, assignment
            ):
                row["next_step"] = "policy"
            explained: tuple[tuple[EvidenceExplanation, bool], ...] = ()
            if receipt is None and basis_applies and closed.open_refs:
                remedy = next_action(snapshot, row).action
                if (
                    isinstance(remedy, ops.AuthoringAction)
                    and remedy.work == "review-disposition"
                ):
                    if reaffirm is None:
                        reaffirm = _reaffirm_work(frame)
                    if assignment.id in reaffirm.routed:
                        # A writable stale row routes the one boundary-level
                        # reaffirm work, which every such assignment shares.
                        row["next_step"] = "reaffirm"
                        row["reaffirm"] = reaffirm.work
                        if _unresolved_kernel_stop(snapshot, row):
                            reaffirm.work["stops"].append(
                                f"{row['role']} {row['scope']} ({row['stop_reason']})"
                            )
                        remedy = next_action(snapshot, row).action
                explained = frame.explain_open(assignment, closed.open_refs, remedy)
            row["evidence_explanations"] = []
            for explanation, _stale in explained:
                payload = ops.decoded_payload(explanation)
                assert explanation.remedy is not None
                payload["remedy"] = ops.action_payload(explanation.remedy)
                row["evidence_explanations"].append(payload)
            if explained:
                row["evidence_state"] = (
                    "stale"
                    if any(stale for _explanation, stale in explained)
                    else "invalid"
                )
            assignments.append(row)
    return {
        "effective_policy": ops.decoded_payload(effective_policy(policy)),
        "review_closure": {
            "assignments": assignments,
            "open_refs": [ref for row in assignments for ref in row["open_refs"]],
        },
    }


def allowance_impact(
    before: StateFile,
    after: StateFile,
    *,
    role: str,
) -> dict[str, Any]:
    """Project role-wide round and provider-call impact from typed facts."""
    before_policy = core.selected_policy(before, role)
    after_policy = core.selected_policy(after, role)
    assert before_policy.limit is not None and after_policy.limit is not None
    scopes = (
        ("feature",)
        if after_policy.scope == "feature"
        else tuple(milestone.id for milestone in after.milestones)
    )
    added = after_policy.limit - before_policy.limit
    materialized = {
        (assignment.role, assignment.scope)
        for assignment in after.review_assignments.assignments
    }
    assignments: list[dict[str, Any]] = []
    for scope in scopes:
        assignment = core.assignment_for(after, role, scope)
        accounting = derive_attempt_accounting(
            tuple(
                attempt
                for attempt in after.review_assignments.attempts
                if attempt.assignment_id == assignment.id
            )
        )
        used = len(assignment.rounds)
        assignments.append(
            {
                "assignment_id": assignment.id,
                "role": role,
                "scope": scope,
                "policy_revision": assignment.policy_revision,
                "materialized": (role, scope) in materialized,
                "quality_round_limit_before": before_policy.limit,
                "quality_round_limit_after": after_policy.limit,
                "quality_rounds_used": used,
                "quality_rounds_remaining_before": max(0, before_policy.limit - used),
                "quality_rounds_remaining_after": max(0, after_policy.limit - used),
                "newly_available_quality_rounds": added,
                "provider_calls_completed": accounting.provider_calls,
            }
        )
    return {
        "affected_assignments": assignments,
        "future_milestone_assignments": after_policy.scope == "milestone",
    }


def _allowance_command(feature: str, role: str, revision: int | str) -> str:
    return (
        "heddle review allowance "
        f"--role {role} "
        "--limit ABSOLUTE_TOTAL "
        "--approval OWNER_APPROVAL "
        f"--feature {feature} "
        f"--expect-revision {revision}"
    )


def _stop_actions(
    feature: str, role: str, owner: str, stop_reason: str, *, preview: bool
) -> tuple[NextAction, ...]:
    """Name every step a recorded stop needs, in order, before the first runs."""
    resolve = (
        f"a write would record stop decision {owner}; resolve it then"
        if preview
        else f"resolve stop decision {owner}"
    )
    actions = [
        NextAction(
            ops.CommandAction(ops.DecisionsList(feature)),
            f"{resolve}; no original work is waived",
        )
    ]
    if stop_reason == "round-limit":
        actions.append(
            NextAction(
                ops.ManualAction(_allowance_command(feature, role, "CURRENT_REVISION")),
                f"If the owner approves another round, the reached cap also needs "
                f"raising: after resolving {owner} with continue-review, run review "
                "allowance with the owner's absolute total and approval at the "
                "revision that resolution returns. Raising the cap neither "
                "resolves the stop nor invokes a provider.",
            )
        )
    return tuple(actions)


def _decision_action(
    snapshot: FeatureSnapshot, identifier: str, position: str = ""
) -> NextAction:
    decision = next(
        item
        for item in snapshot.state.decisions
        if item.id == identifier and item.status != "resolved"
    )
    return NextAction(
        ops.DecisionAction(
            feature=snapshot.feature,
            decision_id=decision.id,
            choices=decision.options,
            routes_to=decision.routes_to,
        ),
        f"resolve original review decision {identifier}{position}",
    )


def next_actions(
    snapshot: FeatureSnapshot, row: dict[str, Any]
) -> tuple[NextAction, ...]:
    """Every action an assignment row needs now: one per pending decision a
    round recorded, otherwise its single next step."""
    owners = row["decision_ids"]
    if row["next_step"] == "decision" and len(owners) > 1:
        return tuple(
            _decision_action(snapshot, owner, f" ({index} of {len(owners)})")
            for index, owner in enumerate(owners, start=1)
        )
    return (next_action(snapshot, row),)


def next_action(snapshot: FeatureSnapshot, row: dict[str, Any]) -> NextAction:
    if row["next_step"] == "policy":
        reason = (
            f"The continuation for {row['role']} {row['scope']} is authorized, "
            "but its confirmed quality-round cap is reached. The owner must supply "
            "an absolute total and approval; increasing review allowance does not "
            "resolve the stop or invoke a provider."
        )
        command = _allowance_command(
            snapshot.feature, row["role"], snapshot.state.revision
        )
        return NextAction(ops.ManualAction(command), reason)
    if row["next_step"] == "interpret":
        retained = core.pending_retained(snapshot.state, row["assignment_id"])[0]
        return interpretation_action(snapshot, retained)
    if row["next_step"] == "decision" and row["decision_id"] is not None:
        return _decision_action(snapshot, row["decision_id"])
    if _originating_inspection_round_requested(snapshot, row):
        reason = (
            f"Open the originating-reviewer verification pass for "
            f"{row['role']} {row['scope']}"
        )
        return NextAction(
            ops.CommandAction(
                ops.ReviewRoundOpen(
                    feature=snapshot.feature,
                    payload={
                        "schema": "heddle.review-round-input/v1",
                        "role": row["role"],
                        "scope": row["scope"],
                        "purpose": "verification",
                        "reason": reason,
                    },
                    expect_revision=snapshot.state.revision,
                )
            ),
            reason,
        )
    if row["next_step"] == "decision" or (
        row["next_step"] == "run" and row["rounds_used"] and not row["missing_slots"]
    ):
        stopped = row["next_step"] == "decision"
        reason = (
            f"Record the mandatory {row['stop_reason']} stop for "
            f"{row['role']} {row['scope']} without another provider call"
            if stopped
            else f"Open the next required confirmed pass for "
            f"{row['role']} {row['scope']}"
        )
        return NextAction(
            ops.CommandAction(
                ops.ReviewRoundOpen(
                    feature=snapshot.feature,
                    payload={
                        "schema": "heddle.review-round-input/v1",
                        "role": row["role"],
                        "scope": row["scope"],
                        "purpose": "verification" if stopped else "independent-pass",
                        "reason": reason,
                    },
                    expect_revision=snapshot.state.revision,
                )
            ),
            reason,
        )
    if row["next_step"] == "run":
        assignment = core.assignment_for(snapshot.state, row["role"], row["scope"])
        current = core.launch_round(snapshot.state, assignment)
        missing = row["missing_slots"] or ["primary"]
        slot = next(s for s in current.slots if s.name in missing)
        return NextAction(
            ops.CommandAction(
                ops.RunGate(
                    row["role"], feature=snapshot.feature, **asdict(slot.reviewer)
                )
            ),
            f"complete {row['role']} {row['scope']} slot {slot.name}",
        )
    if row["next_step"] == "reaffirm":
        work = row["reaffirm"]
        skipped = (
            f" It skips {', '.join(work['skipped'])} until their missing reviewer "
            "slots complete."
            if work["skipped"]
            else ""
        )
        return NextAction(
            ops.AuthoringAction(
                snapshot.feature,
                "review-reaffirm",
                tuple(work["references"]),
                "",
                snapshot.state.revision,
            ),
            "Inspect whether each listed resolution still applies, then run review "
            "reaffirm: across every unsealed assignment it re-records the rows "
            "that still qualify and lists those marked refused, which need new "
            "dispositions." + skipped + _reaffirm_stop_note(work["stops"]),
        )
    return NextAction(
        ops.AuthoringAction(
            snapshot.feature,
            "review-disposition",
            tuple(f"{run_id}#{finding_id}" for run_id, finding_id in row["open_refs"]),
            "heddle.review-disposition-input/v1",
            snapshot.state.revision,
        ),
        f"Record evidence-bound native dispositions for {row['role']} {row['scope']}. "
        f"Read the original reports and disposition these open references: "
        f"{json.dumps(row['open_refs'])}. Use review disposition --input-json "
        "with schema heddle.review-disposition-input/v1 and authored dispositions."
        + _stop_note(snapshot, row),
    )


def _unresolved_kernel_stop(snapshot: FeatureSnapshot, row: dict[str, Any]) -> bool:
    """Whether the row is at a kernel stop no owner decision has resolved."""
    if row["stop_reason"] not in _KERNEL_STOPS:
        return False
    assignment = core.assignment_for(snapshot.state, row["role"], row["scope"])
    # A continue-review ruling resolves only the stop it was given for; once
    # its round is spent, a stop reached again is unresolved until recorded.
    return not core.continuation_authorized(snapshot.state, assignment) and not any(
        decision.id == assignment.stop_decision_id
        and decision.status == "resolved"
        and isinstance(decision.resolution, Disposition)
        for decision in snapshot.state.decisions
    )


# Implement step 5: a material change after a milestone's latest round owes
# a verification round that re-recording does not discharge.
_OWED_ROUND = " A round that a change since the latest round owes is still owed."


def _stop_note(snapshot: FeatureSnapshot, row: dict[str, Any]) -> str:
    """Say why open work routes past a kernel stop the owner has not resolved."""
    if not _unresolved_kernel_stop(snapshot, row):
        return ""
    closes = (
        "changed evidence to re-record, or the latest round's coverage"
        if row["stop_reason"] == "round-limit"
        else "changed evidence to re-record"
    )
    return (
        f" The {row['stop_reason']} stop is reached, but every open row closes "
        f"with recorded evidence alone ({closes}), so closing these rows needs "
        "no review round or owner decision."
        + (_OWED_ROUND if row["role"] == "milestone-review" else "")
    )


def _reaffirm_stop_note(stops: list[str]) -> str:
    """Name the assignments past whose unresolved stop reaffirm is routed."""
    if not stops:
        return ""
    return (
        f" Stops are reached for {', '.join(stops)}, but their open rows close "
        "by re-recording evidence alone, so closing them needs no review round "
        "or owner decision."
        + (_OWED_ROUND if any(s.startswith("milestone-review ") for s in stops) else "")
    )


def _originating_inspection_round_requested(
    snapshot: FeatureSnapshot, row: dict[str, Any]
) -> bool:
    if (
        row["next_step"] != "disposition"
        or row["evidence_state"] != "current"
        or row["missing_slots"]
        or row["rounds_remaining"] == 0
    ):
        return False
    latest = {
        (item.run_id, item.finding_id): item
        for item in snapshot.state.review_assignments.dispositions
    }
    return any(
        (disposition := latest.get(tuple(reference))) is not None
        and disposition.status == "retained"
        and disposition.requires_inspection
        for reference in row["open_refs"]
    )


def _shape(
    value: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not set(required) <= set(value)
        or (set(value) - set(required) - set(optional))
    ):
        raise core.invalid(
            f"input requires {sorted(required)}; optional {sorted(optional)}"
        )
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise core.invalid(f"{label} needs nonempty text")
    return value


@dataclass(frozen=True)
class _DispositionInput:
    row_index: int
    run_id: str
    finding_id: str
    status: str
    evidence_kind: str
    references: tuple[str, ...]
    reason: str
    requires_inspection: bool | None
    decision_id: str | None
    review_run_id: str | None
    verification_scope: str | None


def _decoded_text(value: Any) -> str | None:
    return value if isinstance(value, str) and bool(value.strip()) else None


def _row_failure(
    row_index: int,
    raw: Any,
    predicate: str,
    remedy: str,
    *,
    field: str | None = None,
    reference: str | None = None,
    verification_scope: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    evidence_kind = _decoded_text(row.get("evidence_kind"))
    if evidence_kind not in EVIDENCE_KINDS:
        evidence_kind = None
    return {
        "row_index": row_index,
        "run_id": _decoded_text(row.get("run_id")),
        "finding_id": _decoded_text(row.get("finding_id")),
        "evidence_kind": evidence_kind,
        "predicate": predicate,
        "field": field,
        "reference": reference,
        "verification_scope": verification_scope,
        "status": status,
        "remedy": remedy,
    }


def _decode_disposition_input(
    row_index: int, raw: Any
) -> _DispositionInput | dict[str, Any]:
    if not isinstance(raw, dict):
        return _row_failure(
            row_index,
            raw,
            "malformed-row",
            "provide a JSON object for this disposition row",
        )
    allowed = {*DISPOSITION_REQUIRED_FIELDS, *DISPOSITION_OPTIONAL_FIELDS}
    for field in DISPOSITION_REQUIRED_FIELDS:
        if field not in raw:
            return _row_failure(
                row_index,
                raw,
                "malformed-field",
                f"supply required field {field}",
                field=field,
            )
    extra = sorted(set(raw) - allowed)
    if extra:
        return _row_failure(
            row_index,
            raw,
            "malformed-field",
            f"remove unsupported field {extra[0]}",
            field=extra[0],
        )
    for field in ("run_id", "finding_id", "reason"):
        if _decoded_text(raw[field]) is None:
            return _row_failure(
                row_index,
                raw,
                "malformed-field",
                f"supply nonempty text for {field}",
                field=field,
            )
    if raw["status"] not in DISPOSITION_STATUSES:
        return _row_failure(
            row_index,
            raw,
            "malformed-field",
            f"choose status from {', '.join(DISPOSITION_STATUSES)}",
            field="status",
        )
    if raw["evidence_kind"] not in EVIDENCE_KINDS:
        return _row_failure(
            row_index,
            raw,
            "malformed-field",
            f"choose evidence_kind from {', '.join(EVIDENCE_KINDS)}",
            field="evidence_kind",
        )
    references = raw["references"]
    if (
        not isinstance(references, list)
        or not references
        or not all(_decoded_text(item) is not None for item in references)
    ):
        return _row_failure(
            row_index,
            raw,
            "missing-evidence",
            "supply at least one nonempty evidence reference",
            field="references",
        )
    requires_inspection = raw.get("requires_inspection")
    if requires_inspection is not None and type(requires_inspection) is not bool:
        return _row_failure(
            row_index,
            raw,
            "malformed-field",
            "requires_inspection must be boolean",
            field="requires_inspection",
        )
    optional: dict[str, str | None] = {}
    for field in ("decision_id", "review_run_id", "verification_scope"):
        value = raw.get(field)
        if value is not None and _decoded_text(value) is None:
            return _row_failure(
                row_index,
                raw,
                "malformed-field",
                f"{field} must be null or nonempty text",
                field=field,
            )
        optional[field] = cast(str | None, value)
    return _DispositionInput(
        row_index=row_index,
        run_id=cast(str, raw["run_id"]),
        finding_id=cast(str, raw["finding_id"]),
        status=cast(str, raw["status"]),
        evidence_kind=cast(str, raw["evidence_kind"]),
        references=tuple(cast(list[str], references)),
        reason=cast(str, raw["reason"]),
        requires_inspection=requires_inspection,
        decision_id=optional["decision_id"],
        review_run_id=optional["review_run_id"],
        verification_scope=optional["verification_scope"],
    )


def _dispositions(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    payload: Any,
    now: str,
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    _shape(payload, ("schema", "dispositions"))
    if payload["schema"] != "heddle.review-disposition-input/v1":
        raise core.invalid("unsupported disposition input schema")
    rows = payload["dispositions"]
    if not isinstance(rows, list) or not rows:
        raise core.invalid("dispositions must be a nonempty list")
    state = snapshot.state
    frame = _Qualification(config, snapshot)
    decoded = [
        _decode_disposition_input(row_index, row) for row_index, row in enumerate(rows)
    ]
    failures: dict[int, dict[str, Any]] = {
        item["row_index"]: item for item in decoded if isinstance(item, dict)
    }
    positions: dict[tuple[str, str], list[int]] = {}
    for item in decoded:
        if isinstance(item, _DispositionInput):
            positions.setdefault((item.run_id, item.finding_id), []).append(
                item.row_index
            )
    for duplicate_positions in positions.values():
        if len(duplicate_positions) < 2:
            continue
        for row_index in duplicate_positions:
            others = [index for index in duplicate_positions if index != row_index]
            failures[row_index] = _row_failure(
                row_index,
                rows[row_index],
                "duplicate-origin",
                "keep one row for this origin; duplicates are at row indices "
                + ", ".join(str(index) for index in others),
                field="run_id",
                reference=",".join(str(index) for index in others),
            )

    additions: list[dict[str, Any]] = []
    manifests: dict[str, ReviewAssignment] = {}
    for item in decoded:
        if not isinstance(item, _DispositionInput) or item.row_index in failures:
            continue
        row = cast(dict[str, Any], rows[item.row_index])
        ref = (item.run_id, item.finding_id)
        source = next(
            (s for s in core.authoritative_sources(state) if s.run_id == ref[0]),
            None,
        )
        if (
            source is None
            or ref[1] != "@coverage"
            and ref[1] not in {f.finding_id for f in source.findings}
        ):
            failures[item.row_index] = _row_failure(
                item.row_index,
                row,
                "unknown-origin",
                "use an original run_id and finding_id from the native review source",
                field="finding_id" if source is not None else "run_id",
                reference="#".join(ref),
            )
            continue
        assignment = next(
            a
            for a in state.review_assignments.assignments
            if a.id == source.assignment_id
        )
        current = frame.assess(assignment)
        if current.missing_slots:
            failures[item.row_index] = _row_failure(
                item.row_index,
                row,
                "incomplete-round",
                "complete missing reviewer slots before disposition: "
                + ", ".join(current.missing_slots),
                reference=assignment.id,
            )
            continue
        route = _decision_route(state, ref, item.status, item.decision_id)
        if not route.qualifies:
            failures[item.row_index] = _row_failure(
                item.row_index,
                row,
                cast(str, route.predicate),
                cast(str, route.remedy),
                field=route.field,
                reference=route.reference,
            )
            continue
        previous = next(
            (
                d
                for d in reversed(state.review_assignments.dispositions)
                if (d.run_id, d.finding_id) == ref
            ),
            None,
        )
        captured = []
        identities = []
        state_reference = (
            feature_state_path(config, snapshot.feature)
            .relative_to(config.root)
            .as_posix()
        )
        for reference in item.references:
            try:
                normalized_reference = normalize_paths((reference,))[0]
                if normalized_reference == state_reference:
                    failures[item.row_index] = _row_failure(
                        item.row_index,
                        row,
                        "self-mutating-reference",
                        "cite a stable source or report instead of the state file "
                        "mutated by this disposition transaction",
                        field="references",
                        reference=normalized_reference,
                    )
                    break
                cited = _captured_citation(
                    config.root,
                    normalized_reference,
                    plan_path=snapshot.plan_path,
                    source_observations=frame.captures,
                )
                captured.append(
                    (cited.path, _citation_digest(cited.executable, cited.content))
                )
                identity = _citation_equivalence(
                    cited.path, cited.executable, cited.content
                )
                if identity is not None:
                    identities.append(identity)
            except (KernelError, OSError, ValueError) as error:
                predicate, remedy = _reference_input_failure(
                    config.root, reference, error
                )
                failures[item.row_index] = _row_failure(
                    item.row_index,
                    row,
                    predicate,
                    remedy,
                    field="references",
                    reference=reference,
                )
                break
        if item.row_index in failures:
            continue
        disposition = AssignmentDisposition(
            *ref,
            item.status,
            item.evidence_kind,
            tuple(captured),
            frame.basis(assignment),
            item.reason,
            (
                item.requires_inspection
                if item.requires_inspection is not None
                else previous.requires_inspection
                if previous
                else False
            ),
            item.decision_id,
            item.review_run_id,
            item.verification_scope,
            now,
            basis_evidence=frame.basis_reference(assignment),
            reference_equivalence=tuple(identities) or None,
        )
        if disposition.evidence_kind == "verification" and (
            not isinstance(disposition.verification_scope, str)
            or not disposition.verification_scope.strip()
        ):
            failures[item.row_index] = _row_failure(
                item.row_index,
                row,
                "verification-scope-missing",
                _VERIFICATION_SCOPE_REMEDY,
                field="verification_scope",
            )
            continue
        if disposition.status in {"addressed", "settled"}:
            qualification = _qualify_resolution(
                frame, disposition, disposition.evidence_basis, assignment=assignment
            )
            if not qualification.qualifies:
                failures[item.row_index] = _row_failure(
                    item.row_index,
                    row,
                    cast(str, qualification.predicate),
                    qualification.remedy or "supply current qualifying evidence",
                    field=qualification.field,
                    reference=qualification.reference,
                    verification_scope=qualification.verification_scope,
                    status=qualification.status,
                )
                continue
        if previous is not None and replace(previous, at=now) == disposition:
            continue
        additions.append(ops.decoded_payload(disposition))
        manifests[assignment.id] = assignment

    if failures:
        ordered = [failures[index] for index in sorted(failures)]
        raise KernelError(
            code="disposition-batch-invalid",
            message=(f"{len(ordered)} of {len(rows)} disposition rows were rejected"),
            hint="correct every reported row and retry the unchanged batch",
            details={
                "feature": snapshot.feature,
                "revision": snapshot.state.revision,
                "dry_run": dry_run,
                "row_count": len(rows),
                "rejected_count": len(ordered),
                "wrote": False,
                "rows": ordered,
            },
        )
    if not dry_run:
        _publish_basis_manifests(frame, manifests.values())
    return additions


def _publish_basis_manifests(
    frame: _Qualification, assignments: Iterable[ReviewAssignment]
) -> None:
    """Publish exactly the stamped manifest each written row names, before commit."""
    from heddle.runtime.verification import publish_stamped_evidence

    workspace = frame.config.root / frame.snapshot.workspace
    for assignment in assignments:
        publish_stamped_evidence(workspace, frame.stamped_basis(assignment))


@dataclass(frozen=True)
class _Reaffirmation:
    """One assignment's reaffirm: the stale rows it writes and the rows it refuses.

    Each entry keeps the ledger index of the effective row it judged. A row
    already current, raw or by review equivalence, needs no write.
    """

    assignment: ReviewAssignment
    written: tuple[tuple[int, AssignmentDisposition], ...]
    refused: tuple[tuple[int, AssignmentDisposition, _EvidenceQualification], ...]


def _reaffirm_candidate(
    row: AssignmentDisposition, basis: str | None, reference: ManifestReference
) -> AssignmentDisposition:
    """``row`` re-bound to the current basis, as reaffirm writes it."""
    return replace(
        row,
        evidence_basis=basis,
        basis_evidence=reference,
        rebound_by=REBOUND_BY_REAFFIRM,
    )


def _reaffirmation(
    frame: _Qualification, assignment: ReviewAssignment
) -> _Reaffirmation:
    """Requalify each effective resolution at the assignment's current basis.

    A candidate names the unstamped current manifest, because qualification
    reads only that one is named; the command stamps the rows it writes.
    """
    basis = frame.basis(assignment)
    frame.assess(assignment)
    current = set(frame.qualified_disposition_indexes[assignment.id])
    reference: ManifestReference | None = None
    written: list[tuple[int, AssignmentDisposition]] = []
    refused: list[tuple[int, AssignmentDisposition, _EvidenceQualification]] = []
    for index, old in frame.effective_dispositions(assignment):
        if old.status not in {"addressed", "settled"}:
            continue
        if reference is None:
            reference = frame.basis_reference(assignment, stamped=False)
        candidate = _reaffirm_candidate(old, basis, reference)
        qualification = _qualify_resolution(
            frame, candidate, basis, assignment=assignment
        )
        if not qualification.qualifies:
            refused.append((index, old, qualification))
        elif index not in current:
            written.append((index, candidate))
    return _Reaffirmation(assignment, tuple(written), tuple(refused))


def _stale_resolutions(
    frame: _Qualification, assignment: ReviewAssignment
) -> tuple[AssignmentDisposition, ...]:
    """Effective resolutions that no longer qualify as recorded."""
    frame.assess(assignment)
    current = set(frame.qualified_disposition_indexes[assignment.id])
    return tuple(
        old
        for index, old in frame.effective_dispositions(assignment)
        if old.status in {"addressed", "settled"} and index not in current
    )


@dataclass(frozen=True)
class _ReaffirmSelection:
    """One reaffirm across its assignments.

    Each skipped assignment is missing a reviewer slot; it keeps those slots
    and its stale rows, which remain.
    """

    plans: tuple[_Reaffirmation, ...]
    skipped: tuple[
        tuple[ReviewAssignment, tuple[str, ...], tuple[AssignmentDisposition, ...]],
        ...,
    ]


def _reaffirm_selection(
    frame: _Qualification, role: str | None, scope: str | None
) -> _ReaffirmSelection:
    """Plan reaffirm for the assignment role and scope name, or for every one.

    Without a role every unsealed assignment is selected and sealed ones are
    skipped; a named sealed assignment refuses. The command and readiness's
    review-reaffirm work share it, so the work lists exactly what the role-free
    command writes and refuses.
    """
    state = frame.snapshot.state
    if role is None:
        selected = tuple(
            a
            for a in state.review_assignments.assignments
            if not core.assignment_sealed(state, a)
        )
    else:
        assignment = next(
            (
                a
                for a in state.review_assignments.assignments
                if (a.role, a.scope) == (role, scope)
            ),
            None,
        )
        if assignment is None:
            raise core.invalid("reaffirm requires an existing review assignment")
        if core.assignment_sealed(state, assignment):
            raise core.invalid("sealed acceptance cannot gain reaffirmed dispositions")
        selected = (assignment,)
    plans: list[_Reaffirmation] = []
    skipped = []
    for assignment in selected:
        missing = tuple(frame.assess(assignment).missing_slots)
        if missing:
            skipped.append((assignment, missing, _stale_resolutions(frame, assignment)))
        else:
            plans.append(_reaffirmation(frame, assignment))
    return _ReaffirmSelection(tuple(plans), tuple(skipped))


@dataclass(frozen=True)
class _ReaffirmWork:
    """Readiness's one review-reaffirm work item and the assignments it routes."""

    routed: frozenset[str]
    work: dict[str, Any]


def _reaffirm_work(frame: _Qualification) -> _ReaffirmWork:
    """The role-free command's effect as one boundary-level work item.

    Its references are the rows the command writes, then those it refuses,
    each marked with why. It routes only the assignments with a writable stale
    row; the others keep their disposition work.
    """
    try:
        selection = _reaffirm_selection(frame, None, None)
    except KernelError as error:
        if error.reason != entry.BASELINE_UNAVAILABLE:
            raise
        # Until the baseline resolves, readiness offers only its re-point.
        return _ReaffirmWork(frozenset(), {})
    return _ReaffirmWork(
        frozenset(plan.assignment.id for plan in selection.plans if plan.written),
        {
            "references": [
                *(
                    f"{row.run_id}#{row.finding_id}"
                    for plan in selection.plans
                    for _index, row in plan.written
                ),
                *(
                    f"{old.run_id}#{old.finding_id} (refused: {_refusal(result)})"
                    for plan in selection.plans
                    for _index, old, result in plan.refused
                ),
            ],
            "skipped": [
                assignment.id for assignment, _slots, _rows in selection.skipped
            ],
            # Routed assignments at an unresolved kernel stop, added as the
            # projection routes them; the work names them once for all.
            "stops": [],
        },
    )


def _refusal(result: _EvidenceQualification) -> str:
    return ", ".join(
        f"{change.kind} {change.path}" if change.path is not None else change.kind
        for change in result.changes
    ) or cast(str, result.predicate)


def _refused_row(
    assignment: ReviewAssignment,
    index: int,
    old: AssignmentDisposition,
    result: _EvidenceQualification,
) -> dict[str, Any]:
    """A refused row as reaffirm lists it, with what changed since it was recorded."""
    return {
        **_row_failure(
            index,
            ops.decoded_payload(old),
            cast(str, result.predicate),
            result.remedy or "supply current qualifying evidence",
            field=result.field,
            reference=result.reference,
            verification_scope=result.verification_scope,
            status=result.status,
        ),
        "assignment_id": assignment.id,
        "cause": result.cause,
        "changed_dependencies": list(result.changed_dependencies),
        "changes": [asdict(change) for change in result.changes],
        "detail_limit": result.detail_limit,
    }


def _citation_identities(
    frame: _Qualification, disposition: AssignmentDisposition
) -> tuple[tuple[str, str, str], ...] | None:
    """A reaffirmed copy's citation identities, keeping those already recorded.

    The copy qualified, so a citation without a recorded identity matched raw;
    its identity comes from the current bytes, as a disposition write records it.
    """
    identities = list(disposition.reference_equivalence or ())
    recorded = {row[0] for row in identities}
    for path, _digest in disposition.references:
        if path in recorded:
            continue
        cited = _captured_citation(
            frame.config.root,
            path,
            plan_path=frame.snapshot.plan_path,
            source_observations=frame.captures,
        )
        identity = _citation_equivalence(cited.path, cited.executable, cited.content)
        if identity is not None:
            identities.append(identity)
    return tuple(identities) or None


_NOTHING_REAFFIRMED = "nothing-reaffirmed"


def _reaffirmations(
    config: ProjectConfig, snapshot: FeatureSnapshot, operation: ops.ReaffirmReview
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    """Write every stale row that still qualifies, listing the rest.

    An assignment missing a slot is listed with its reason, and its stale rows
    remain. Returns the rows to append, the listing, and whether rows remain.
    """
    frame = _Qualification(config, snapshot)
    selection = _reaffirm_selection(frame, operation.role, operation.scope)
    skipped = [
        {
            "assignment_id": assignment.id,
            "role": assignment.role,
            "scope": assignment.scope,
            "reason": "complete missing reviewer slots before reaffirming: "
            + ", ".join(slots),
            "missing_slots": list(slots),
            "rows": [f"{d.run_id}#{d.finding_id}" for d in rows],
        }
        for assignment, slots, rows in selection.skipped
    ]
    refused = [
        _refused_row(plan.assignment, index, old, result)
        for plan in selection.plans
        for index, old, result in plan.refused
    ]
    remaining = bool(refused) or any(rows for _a, _s, rows in selection.skipped)
    written = [plan for plan in selection.plans if plan.written]
    if remaining and not written:
        raise KernelError(
            code="disposition-batch-invalid",
            message="reaffirmation wrote nothing: no remaining row qualifies at "
            "the current basis",
            hint="inspect each listed row and record new evidence with review "
            "disposition; complete missing reviewer slots before reaffirming "
            "their assignments; no rows were written",
            reason=_NOTHING_REAFFIRMED,
            details={
                "feature": snapshot.feature,
                "revision": snapshot.state.revision,
                "dry_run": operation.dry_run,
                "wrote": False,
                "rows": refused,
                "skipped": skipped,
            },
        )
    if written and not operation.dry_run:
        _publish_basis_manifests(frame, (plan.assignment for plan in written))
    now = utc_now_minutes()
    additions: list[dict[str, Any]] = []
    for plan in written:
        stamped = frame.basis_reference(plan.assignment)
        additions.extend(
            ops.decoded_payload(
                replace(
                    row,
                    basis_evidence=stamped,
                    reference_equivalence=_citation_identities(frame, row),
                    at=now,
                )
            )
            for _index, row in plan.written
        )
    listing = {
        "reaffirmed": [
            {
                "assignment_id": plan.assignment.id,
                "run_id": row.run_id,
                "finding_id": row.finding_id,
            }
            for plan in written
            for _index, row in plan.written
        ],
        "refused": refused,
        "skipped": skipped,
    }
    return additions, listing, remaining


def _scope_change(
    config: ProjectConfig, value: Any, *, plan_path: str
) -> ReviewScopeChange | None:
    if value is None:
        return None
    raw = _shape(value, ("reason", "references"))
    if not isinstance(raw["references"], list) or not raw["references"]:
        raise core.invalid("scope change requires material contract references")
    references = []
    for path in raw["references"]:
        try:
            references.append(
                captured_reference(config.root, path, plan_path=plan_path)
            )
        except (KernelError, OSError, ValueError) as error:
            predicate, remedy = _reference_input_failure(config.root, str(path), error)
            raise KernelError(
                code="workspace-invalid",
                message=f"review assignment: scope change reference {path!r} is "
                f"{predicate}",
                hint=remedy,
                details={"reference": str(path), "predicate": predicate},
            ) from error
    return ReviewScopeChange(
        _nonempty(raw["reason"], "material contract change reason"),
        tuple(sorted(references)),
    )


def _apply_round_open(
    config: ProjectConfig,
    current: FeatureSnapshot,
    updated: dict[str, Any],
    ledger: dict[str, Any],
    payload: Any,
) -> tuple[dict[str, Any], bool]:
    stopped = False
    response: dict[str, Any]
    payload = _shape(
        payload,
        ("schema", "role", "scope", "purpose", "reason"),
        ("scope_change",),
    )
    if payload["schema"] != "heddle.review-round-input/v1":
        raise core.invalid("unsupported review-round input schema")
    if payload["role"] not in ROLE_STAGES:
        raise core.invalid("unknown or retired review role")
    if STAGES.index(current.stage) < STAGES.index(ROLE_STAGES[payload["role"]]):
        raise core.invalid(
            "open rounds only at the assignment stage or a later repair boundary"
        )
    assignment = core.assignment_for(current.state, payload["role"], payload["scope"])
    if core.assignment_sealed(current.state, assignment):
        raise core.invalid("sealed acceptance cannot gain another review round")
    if core.selected_policy(current.state, assignment.role).mode == "off":
        raise core.invalid("review role is explicitly off; amend feature policy first")
    closed = _Qualification(config, current).assess(assignment)
    if closed.closed and payload["purpose"] != "verification":
        raise core.invalid(
            "assignment is already closed; explicitly amend the confirmed "
            "minimum passes if another independent review is required"
        )
    if closed.missing_slots:
        raise core.invalid("finish missing slots before opening another round")
    owner = next(
        (d for d in current.state.decisions if d.id == assignment.stop_decision_id),
        None,
    )
    if owner is not None and owner.status == "pending":
        raise core.invalid(
            f"use decisions resolve for stop decision {owner.id} "
            "before opening another round"
        )
    if closed.stop_reason:
        if core.continuation_authorized(current.state, assignment):
            raise core.invalid(
                "continuation is authorized but the confirmed cap is reached; "
                "raise it with review allowance (the owner's absolute total and "
                "approval) before opening the next round"
            )
        stopped = True
        if owner is None or owner.status == "resolved":
            identifier = allocate_decision_ids(updated, 1)[0]
            updated["decisions"].append(
                {
                    "id": identifier,
                    "kind": "question",
                    "class": 2,
                    "source": "session",
                    "title": f"{assignment.role} stopped: {closed.stop_reason}",
                    "question": "How should the original open work be "
                    "handled within explicit policy?",
                    "options": [
                        "Resolve with evidence and native dispositions",
                        "Explicitly amend policy",
                        "Authorize one more round with decisions resolve "
                        "--kind continue-review"
                        + (
                            ", then raise the reached cap with review allowance"
                            if closed.stop_reason == "round-limit"
                            else ""
                        ),
                    ],
                    "recommendation": "Preserve all original obligations "
                    "and settle the remaining work",
                    "status": "pending",
                    "routes_to": [],
                    "created_at": utc_now_minutes(),
                    "resolution": None,
                    "resolution_source": None,
                    "rationale": None,
                    "resolved_at": None,
                }
            )
            stored = next(a for a in ledger["assignments"] if a["id"] == assignment.id)
            stored["stop_decision_id"] = identifier
        response = {
            "assignment_id": assignment.id,
            "stop_reason": closed.stop_reason,
            "decision_id": next(
                a["stop_decision_id"]
                for a in ledger["assignments"]
                if a["id"] == assignment.id
            ),
        }
    else:
        if core.selected_policy(current.state, assignment.role).mode == "off":
            raise core.invalid("review role is explicitly off")
        if not assignment.rounds:
            raise core.invalid(
                "run the initial confirmed round before opening a later round"
            )
        row = core.new_round(
            current.state,
            assignment,
            purpose=payload["purpose"],
            reason=_nonempty(payload["reason"], "round reason"),
            before_open=closed.open_refs,
            scope_change=_scope_change(
                config, payload.get("scope_change"), plan_path=current.plan_path
            ),
        )
        if closed.closed and not core.verification_targets(
            current.state, assignment, row
        ):
            raise core.invalid("verification requires available original targets")
        stored = next(a for a in ledger["assignments"] if a["id"] == assignment.id)
        stored["rounds"].append(ops.decoded_payload(row))
        response = {
            "assignment_id": assignment.id,
            "round_number": row.number,
        }
    return response, stopped


def _operate(
    operation: ops.ReviewRoundOpen | ops.RecordReviewDisposition | ops.ReaffirmReview,
    *,
    opening: bool,
) -> HeddleResult:
    resolved = resolve_snapshot_from_cwd(operation.feature, writable=True)
    if isinstance(resolved, ResolveFeatureFailure):
        return write_failure(resolved.error)
    snapshot, config = resolved.snapshot, resolved.config
    path = config.root / snapshot.workspace / "state.yaml"
    try:
        if (
            snapshot.state.feature_policy is None
            or snapshot.state.completion is not None
        ):
            raise core.invalid("an active current policy owner is required")
        if conflict := check_expect_revision(
            operation.expect_revision, snapshot.state.revision
        ):
            raise conflict
        response: dict[str, Any] = {}
        stopped = remaining = False

        def transform(document: dict[str, Any]) -> dict[str, Any]:
            nonlocal response, stopped, remaining
            current = replace(
                snapshot, state=parse_state_document(document, source=path)
            )
            updated = deepcopy(document)
            ledger = updated.setdefault(
                "review_assignments",
                ops.decoded_payload(current.state.review_assignments),
            )
            if isinstance(operation, ops.ReaffirmReview):
                additions, listing, remaining = _reaffirmations(
                    config, current, operation
                )
                ledger["dispositions"].extend(additions)
                response.update(listing)
            elif opening:
                response, stopped = _apply_round_open(
                    config, current, updated, ledger, operation.payload
                )
            else:
                ledger["dispositions"].extend(
                    _dispositions(
                        config,
                        current,
                        operation.payload,
                        utc_now_minutes(),
                        dry_run=operation.dry_run,
                    )
                )
            after = replace(snapshot, state=parse_state_document(updated, source=path))
            observed = projection(config, after)
            response.update(observed)
            if isinstance(operation, ops.ReaffirmReview):
                if operation.role is not None:
                    response["closure"] = next(
                        row
                        for row in observed["review_closure"]["assignments"]
                        if (row["role"], row["scope"])
                        == (operation.role, operation.scope)
                    )
            elif not opening:
                origins = {
                    row["run_id"]
                    for row in cast(dict[str, Any], operation.payload)["dispositions"]
                }
                ids = {
                    s.assignment_id
                    for s in core.authoritative_sources(after.state)
                    if s.run_id in origins
                }
                response["closure"] = next(
                    row
                    for row in observed["review_closure"]["assignments"]
                    if row["assignment_id"] in ids
                )
            return updated

        if operation.dry_run:
            before = read_state_document(path)
            if before["revision"] != snapshot.state.revision:
                raise Conflict("state revision changed during review preview")
            after = transform(before)
            wrote, revision = after != before, before["revision"]
        else:
            result = commit_state(
                path, expect_revision=snapshot.state.revision, transform=transform
            )
            wrote, revision = result.wrote, result.revision
        response.update(
            revision=revision,
            **mutation_fields(ops.operation_name(operation), wrote, operation.dry_run),
        )
        if stopped:
            # A recorded stop is the routed outcome, not a refusal: the round
            # is replaced by an owner decision, and the advisory exit says
            # that owner work remains before any further round.
            assert not isinstance(operation, ops.ReaffirmReview)
            return HeddleResult.success(
                {"feature": snapshot.feature, **response},
                exit_code=ExitCode.ADVISORY,
                next_actions=_stop_actions(
                    snapshot.feature,
                    cast(dict[str, Any], operation.payload)["role"],
                    response["decision_id"],
                    response["stop_reason"],
                    preview=operation.dry_run,
                ),
            )
        if remaining:
            # Rows that no longer qualify are listed beside those written; the
            # advisory exit says they still need new evidence.
            return HeddleResult.success(response, exit_code=ExitCode.ADVISORY)
        return HeddleResult.success(response)
    except Conflict as error:
        return conflict_failure(error)
    except KernelError as error:
        if error.reason == _NOTHING_REAFFIRMED:
            # Every remaining row needs new evidence: reaffirm's own exit 1,
            # distinct from a refused operation.
            return kernel_error_result(
                error, exit_codes={error.code: ExitCode.INTERNAL}
            )
        return write_failure(error)
    except (OSError, ValueError, TypeError) as error:
        return write_failure(core.invalid(str(error)))


def record_disposition(operation: ops.RecordReviewDisposition) -> HeddleResult:
    return _operate(operation, opening=False)


def reaffirm_review(operation: ops.ReaffirmReview) -> HeddleResult:
    if (operation.role is None) != (operation.scope is None):
        return usage_failure(_ROLE_AND_SCOPE, "use review reaffirm --help")
    return _operate(operation, opening=False)


def open_review_round(operation: ops.ReviewRoundOpen) -> HeddleResult:
    return _operate(operation, opening=True)


def interpret_review(operation: ops.InterpretReview) -> HeddleResult:
    """Accept explicit lead interpretation, retaining the original independent call."""
    from heddle.runtime.gate_run import record_interpreted_review

    resolved = resolve_snapshot_from_cwd(operation.feature, writable=True)
    if isinstance(resolved, ResolveFeatureFailure):
        return write_failure(resolved.error)
    snapshot, config = resolved.snapshot, resolved.config
    workspace = config.root / snapshot.workspace
    try:
        if (
            snapshot.state.feature_policy is None
            or snapshot.state.completion is not None
        ):
            raise core.invalid("an active current policy owner is required")
        if conflict := check_expect_revision(
            operation.expect_revision, snapshot.state.revision
        ):
            raise conflict
        payload = _shape(
            operation.payload,
            (
                "schema",
                "run_id",
                "capture_sha256",
                "author",
                "reason",
                "content",
            ),
        )
        if payload["schema"] != "heddle.review-interpretation-input/v1":
            raise core.invalid("unsupported lead interpretation input schema")
        retained = next(
            (
                r
                for r in core.retained_responses(snapshot.state)
                if r.run_id == payload["run_id"]
            ),
            None,
        )
        if retained is None or retained.artifact_sha256 != payload["capture_sha256"]:
            raise core.invalid("interpretation must name an exact retained response")
        author = _nonempty(payload["author"], "interpretation author")
        reason = _nonempty(payload["reason"], "interpretation reason")
        capture = validate_retained(config, snapshot)[retained.run_id]
        # Validate before any publication, including on replay and dry-run.
        outcome = entry.interpret_retained_response(
            workspace,
            retained,
            capture,
            payload["content"],
            publish=False,
        )
        previous = next(
            (
                r
                for r in core.review_interpretations(snapshot.state)
                if r.run_id == retained.run_id
            ),
            None,
        )
        if previous is not None:
            validate_sources(
                ReviewEvidenceAccess(
                    config.root,
                    snapshot.workspace,
                    snapshot.feature,
                    snapshot.state,
                )
            )
            if (previous.result_sha256, previous.author, previous.reason) != (
                outcome.artifact_sha256,
                author,
                reason,
            ):
                raise core.invalid(
                    "an accepted interpretation is immutable; use ordinary dispositions"
                )
            return HeddleResult.success(
                {
                    "run_id": retained.run_id,
                    "accepted": True,
                    "cached": True,
                    "wrote": False,
                    "revision": snapshot.state.revision,
                }
            )
        if operation.dry_run:
            return HeddleResult.success(
                {
                    "run_id": retained.run_id,
                    "accepted": False,
                    "dry_run": True,
                    "wrote": False,
                    "would_accept": True,
                    "revision": snapshot.state.revision,
                    "findings": dict(outcome.findings),
                }
            )
        outcome = entry.interpret_retained_response(
            workspace,
            retained,
            capture,
            payload["content"],
            publish=True,
        )
        recorded = record_interpreted_review(
            snapshot,
            workspace,
            retained,
            capture,
            outcome,
            author=author,
            reason=reason,
        )
        return HeddleResult.success(
            {
                "run_id": recorded.run_id,
                "accepted": True,
                "cached": recorded.replay,
                "wrote": not recorded.replay,
                "revision": recorded.revision,
                "artifact": snapshot.workspace + outcome.artifact_relpath,
                "original_capture": snapshot.workspace + retained.artifact,
                "interpretation_author": author,
                "findings": dict(outcome.findings),
                "decision_ids": list(recorded.decision_ids),
                "reviewer_credit": "original-independent-slot",
            },
            diagnostics=tuple(
                Diagnostic(
                    severity=Severity.ADVISORY,
                    code="gate-output-structure",
                    message=(
                        "Original response retained with lead interpretation: "
                        f"{warning}"
                    ),
                )
                for warning in outcome.structure_warnings
            ),
        )
    except Conflict as error:
        return conflict_failure(error)
    except KernelError as error:
        return write_failure(error)
    except (OSError, ValueError, TypeError) as error:
        return write_failure(core.invalid(str(error)))


def _run_input(
    args: list[str], json_mode: bool, *, opening: bool, interpreting: bool = False
) -> int:
    parsed, values, _, failure = parse_write_args(
        args,
        value_flags={
            "--input-json": ("--input-json requires a value", "use heddle help")
        },
        unknown_hint="use --input-json <path|-> and --feature <slug>",
        allow_positionals=False,
    )
    if failure is None:
        assert parsed is not None
        try:
            source = values["--input-json"]
            if source is None:
                raise ValueError("--input-json <path|-> is required")
            payload = json.loads(
                sys.stdin.read()
                if source == "-"
                else Path(source).read_text(encoding="utf-8")
            )
            if not isinstance(payload, dict):
                raise ValueError("input must be a JSON object")
            operation = (
                ops.InterpretReview
                if interpreting
                else ops.ReviewRoundOpen
                if opening
                else ops.RecordReviewDisposition
            )(
                feature=parsed.feature,
                payload=payload,
                dry_run=parsed.dry_run,
                expect_revision=parsed.expect_revision,
            )
            from heddle.runtime.application import execute

            result = execute(operation)
            return emit_envelope(result, json_mode, _render)
        except (OSError, ValueError) as error:
            failure = usage_failure(str(error), "use a versioned native review input")
    assert failure is not None
    return emit_envelope(failure, json_mode, _render)


def _render(result: HeddleResult) -> None:
    stream = sys.stdout if result.ok else sys.stderr
    if result.error is not None and result.error.code == "disposition-batch-invalid":
        print(
            f"heddle: error[{result.error.code}]: {result.error.message}",
            file=stream,
        )
        for row in result.error.details.get("rows", ()):
            if isinstance(row, dict):
                print(
                    f"  row {row.get('row_index', 0) + 1}: "
                    f"{row.get('predicate')}: {row.get('remedy')}",
                    file=stream,
                )
        for skipped in result.error.details.get("skipped", ()):
            if isinstance(skipped, dict):
                print(
                    f"  {skipped.get('assignment_id')}: {skipped.get('reason')}",
                    file=stream,
                )
        print(f"  hint: {result.error.hint}", file=stream)
        return
    print(json.dumps(result.to_envelope(), indent=2), file=stream)


def run_disposition(args: list[str], json_mode: bool) -> int:
    return _run_input(args, json_mode, opening=False)


def run_round_open(args: list[str], json_mode: bool) -> int:
    return _run_input(args, json_mode, opening=True)


def run_interpret(args: list[str], json_mode: bool) -> int:
    return _run_input(args, json_mode, opening=False, interpreting=True)


_ROLE_AND_SCOPE = (
    "--role and --scope name one assignment together; give both, or neither "
    "for every unsealed assignment"
)


def run_reaffirm(args: list[str], json_mode: bool) -> int:
    parsed, values, _, failure = parse_write_args(
        args,
        value_flags={
            flag: (f"{flag} requires a value", "use review reaffirm --help")
            for flag in ("--role", "--scope")
        },
        unknown_hint="use --role <role> --scope <feature|mN>, or neither, and "
        "--feature <slug>",
        allow_positionals=False,
    )
    if failure is None:
        assert parsed is not None
        role, scope = values["--role"], values["--scope"]
        if (role is None) != (scope is None) or "" in (role, scope):
            failure = usage_failure(_ROLE_AND_SCOPE, "use review reaffirm --help")
        else:
            from heddle.runtime.application import execute

            result = execute(
                ops.ReaffirmReview(
                    role,
                    scope,
                    feature=parsed.feature,
                    expect_revision=parsed.expect_revision,
                    dry_run=parsed.dry_run,
                )
            )
            return emit_envelope(result, json_mode, _render)
    assert failure is not None
    return emit_envelope(failure, json_mode, _render)
