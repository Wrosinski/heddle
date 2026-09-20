"""Current assignment decoding and projections into the shared closure resolver."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, fields, is_dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from heddle.contracts.decisions import AcceptDegradedSmoke, ContinueReview
from heddle.contracts.feature_policy import (
    ROLES,
    GatePolicy,
    ReviewClosureFacts,
    ReviewDecision,
    Reviewer,
    ReviewObligation,
    ReviewResolution,
    ReviewRound,
)
from heddle.contracts.review_assignments import (
    DISPOSITION_STATUSES,
    EVIDENCE_KINDS,
    SCHEMA,
    AcceptanceRecord,
    AcceptanceRecordFamily,
    AcceptedAssignment,
    ArtifactRef,
    AssignmentDisposition,
    AssignmentRound,
    AssignmentSource,
    BoundaryAcceptance,
    CanonicalReview,
    EngineFailure,
    FindingFact,
    RetainedReview,
    ReviewAssignment,
    ReviewAssignments,
    ReviewAttempt,
    ReviewerSlot,
    ReviewInterpretation,
    ReviewScopeChange,
    UsableReviewCapture,
    parse_review_attempt,
    review_attempt_document,
)
from heddle.kernel.project_config import KernelError
from heddle.kernel.review_closure import assess_review_closure
from heddle.kernel.source_manifest import normalize_paths

if TYPE_CHECKING:
    from heddle.kernel.state import GateRun, StateFile


def invalid(message: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"review assignment: {message}",
        hint="inspect original canonical reviews and native dispositions; "
        "use feature policy for explicit schedule amendments, never reset history",
    )


def _object(value: Any, cls: type, *, defaults: dict | None = None) -> dict[str, Any]:
    expected = {field.name for field in fields(cls)}
    supplied = {**(defaults or {}), **value} if isinstance(value, dict) else None
    if supplied is None or set(supplied) != expected:
        raise invalid(f"{cls.__name__} requires exactly fields {sorted(expected)}")
    return supplied


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid("expected nonempty text")
    return value


def _nullable(value: Any) -> str | None:
    return None if value is None else _text(value)


def _number(value: Any, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise invalid(f"expected integer >= {minimum}")
    return value


def _choice(value: Any, choices: Any) -> str:
    if not isinstance(value, str) or value not in choices:
        raise invalid(f"expected one of {tuple(choices)}")
    return value


def _rows(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise invalid("expected list")
    return value


def _sha(value: Any) -> str:
    if re.fullmatch(r"[a-f0-9]{64}", _text(value)) is None:
        raise invalid("expected SHA-256 identity")
    return cast(str, value)


def _pairs(value: Any, *, hashes: bool = False) -> tuple[tuple[str, str], ...]:
    pairs = []
    for row in _rows(value):
        if not isinstance(row, list) or len(row) != 2:
            raise invalid("expected two-element reference pair")
        path = _text(row[0])
        if hashes:
            normalize_paths((path,))
        pairs.append((path, _sha(row[1]) if hashes else _text(row[1])))
    if len(set(pairs)) != len(pairs):
        raise invalid("duplicate reference")
    return tuple(pairs)


def _round(value: Any) -> AssignmentRound:
    if isinstance(value, dict) and (
        "source_synthesis_run_id" in value or "source_run_ids" in value
    ):
        if value.get("source_synthesis_run_id") is not None or value.get(
            "source_run_ids"
        ) not in (None, []):
            raise invalid("historical producer provenance is unsupported")
        value = {
            key: item
            for key, item in value.items()
            if key not in {"source_synthesis_run_id", "source_run_ids"}
        }
    row = _object(value, AssignmentRound)
    change = None
    if row["scope_change"] is not None:
        raw_change = _object(row["scope_change"], ReviewScopeChange)
        change = ReviewScopeChange(
            _text(raw_change["reason"]), _pairs(raw_change["references"], hashes=True)
        )
        if not change.references:
            raise invalid("scope change requires captured contract references")
    slots = []
    for item in _rows(row["slots"]):
        slot = _object(item, ReviewerSlot)
        reviewer = _object(slot["reviewer"], Reviewer)
        slots.append(
            ReviewerSlot(
                _choice(slot["name"], ("primary", "secondary")),
                Reviewer(
                    _choice(reviewer["cli"], ("claude", "codex")),
                    _text(reviewer["model"]),
                    _choice(
                        reviewer["reasoning_effort"], ("low", "medium", "high", "xhigh")
                    ),
                ),
            )
        )
    if tuple(slot.name for slot in slots) not in {
        ("primary",),
        ("primary", "secondary"),
    }:
        raise invalid("round requires primary and optionally secondary slots")
    return AssignmentRound(
        _number(row["number"]),
        _number(row["policy_revision"]),
        _choice(row["purpose"], ("discovery", "verification", "independent-pass")),
        _text(row["reason"]),
        _text(row["scope_identity"]),
        _pairs(row["before_open"]),
        tuple(slots),
        change,
    )


def _acceptance_record(value: Any) -> AcceptanceRecord:
    row = _object(value, AcceptanceRecord)
    return AcceptanceRecord(
        cast(
            AcceptanceRecordFamily,
            _choice(row["family"], ("decisions", "dispositions", "verifications")),
        ),
        _number(row["index"], 0),
        _sha(row["sha256"]),
    )


def _accepted_assignment(value: Any) -> AcceptedAssignment:
    row = _object(value, AcceptedAssignment)
    round_numbers = tuple(_number(item) for item in _rows(row["round_numbers"]))
    attempt_ids = tuple(_text(item) for item in _rows(row["attempt_ids"]))
    records = tuple(_acceptance_record(item) for item in _rows(row["records"]))
    if not round_numbers or tuple(sorted(set(round_numbers))) != round_numbers:
        raise invalid("accepted assignment rounds must be unique and increasing")
    if not attempt_ids or len(set(attempt_ids)) != len(attempt_ids):
        raise invalid("accepted assignment attempts must be nonempty and unique")
    keys = tuple((item.family, item.index) for item in records)
    if len(set(keys)) != len(keys) or keys != tuple(sorted(keys)):
        raise invalid("accepted assignment records must be unique and ordered")
    return AcceptedAssignment(
        _text(row["assignment_id"]),
        _number(row["policy_revision"]),
        _sha(row["source_identity"]),
        round_numbers,
        attempt_ids,
        records,
        _sha(row["digest"]),
    )


def _acceptance(value: Any) -> BoundaryAcceptance:
    row = _object(value, BoundaryAcceptance)
    scope = _nullable(row["scope"])
    assignments = tuple(
        _accepted_assignment(item) for item in _rows(row["assignments"])
    )
    if not assignments:
        raise invalid("boundary acceptance requires an accepted assignment")
    assignment_ids = tuple(item.assignment_id for item in assignments)
    if len(set(assignment_ids)) != len(assignment_ids) or assignment_ids != tuple(
        sorted(assignment_ids)
    ):
        raise invalid("accepted assignments must be unique and ordered")
    return BoundaryAcceptance(
        _text(row["feature"]),
        _choice(
            row["boundary"],
            ("spec-review", "plan-review", "scaffold", "milestone"),
        ),
        scope,
        _text(row["stage_before"]),
        _text(row["stage_after"]),
        _text(row["accepted_at"]),
        _number(row["revision_before"], 0),
        _number(row["revision_after"]),
        assignments,
    )


def parse_assignments(value: Any) -> ReviewAssignments:
    if value is None:
        return ReviewAssignments()
    if isinstance(value, dict) and "decision_links" in value:
        if value["decision_links"] not in (None, []):
            raise invalid("historical synthesis decision links are unsupported")
        value = {key: item for key, item in value.items() if key != "decision_links"}
    raw = _object(value, ReviewAssignments)
    _choice(raw["schema"], (SCHEMA,))
    assignments = []
    for item in _rows(raw["assignments"]):
        row = _object(item, ReviewAssignment)
        rounds = tuple(_round(value) for value in _rows(row["rounds"]))
        if tuple(r.number for r in rounds) != tuple(range(1, len(rounds) + 1)):
            raise invalid("rounds must be contiguous from one")
        assignments.append(
            ReviewAssignment(
                _text(row["id"]),
                _choice(row["role"], ROLES),
                _text(row["scope"]),
                _number(row["policy_revision"]),
                rounds,
                _nullable(row["stop_decision_id"]),
            )
        )
    dispositions = []
    for item in _rows(raw["dispositions"]):
        row = _object(
            item, AssignmentDisposition, defaults={"source_disposition": None}
        )
        historical = row["source_disposition"]
        if historical is not None:
            if not isinstance(historical, list) or len(historical) != 2:
                raise invalid("source disposition needs its original owner and ordinal")
            historical = (_text(historical[0]), _number(historical[1], 0))
        if type(row["requires_inspection"]) is not bool:
            raise invalid("requires_inspection must be boolean")
        dispositions.append(
            AssignmentDisposition(
                _text(row["run_id"]),
                _text(row["finding_id"]),
                _choice(
                    row["status"],
                    DISPOSITION_STATUSES,
                ),
                _choice(
                    row["evidence_kind"],
                    EVIDENCE_KINDS,
                ),
                _pairs(row["references"], hashes=True),
                None if row["evidence_basis"] is None else _sha(row["evidence_basis"]),
                _text(row["reason"]),
                row["requires_inspection"],
                _nullable(row["decision_id"]),
                _nullable(row["review_run_id"]),
                _nullable(row["verification_scope"]),
                _text(row["at"]),
                historical,
            )
        )
    attempts = tuple(parse_review_attempt(item) for item in _rows(raw["attempts"]))
    acceptances = tuple(_acceptance(item) for item in _rows(raw["acceptances"]))
    return ReviewAssignments(
        SCHEMA,
        tuple(assignments),
        attempts,
        tuple(dispositions),
        acceptances,
    )


def acceptance_record_digest(value: Any) -> str:
    """Return the canonical content identity used by receipt row references."""
    payload = asdict(cast(Any, value)) if is_dataclass(value) else value
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def accepted_assignment_digest(value: AcceptedAssignment) -> str:
    payload = asdict(value)
    payload.pop("digest")
    return acceptance_record_digest(payload)


BOUNDARY_ROLES = {
    "spec-review": "spec-review",
    "plan-review": "plan-review",
    "scaffold": "review-test-scaffolding",
    "milestone": "milestone-review",
}
PHASE_BOUNDARIES = {
    "spec-review": ("spec-review", "plan-review"),
    "plan-review": ("plan-review", "scaffold"),
    "scaffold": ("scaffold", "implement"),
}


def _record_row(state: StateFile, record: AcceptanceRecord) -> Any:
    rows: tuple[Any, ...] = {
        "decisions": state.decisions,
        "dispositions": state.review_assignments.dispositions,
        "verifications": state.verifications,
    }[record.family]
    if record.index >= len(rows):
        raise invalid(
            f"acceptance record {record.family}[{record.index}] does not exist"
        )
    row = rows[record.index]
    if acceptance_record_digest(row) != record.sha256:
        raise invalid(
            f"acceptance record {record.family}[{record.index}] digest differs"
        )
    return row


def policy_at_revision(state: StateFile, role: str, revision: int) -> GatePolicy:
    """Return the role policy confirmed at an assignment's recorded revision."""
    confirmed = next(
        (row for row in state.policy_history if row.revision == revision), None
    )
    if confirmed is None:
        raise invalid(f"policy revision {revision} has no confirmed history")
    return next(row for row in confirmed.entries if row.role == role)


def _validate_acceptance_entry(
    state: StateFile,
    receipt: BoundaryAcceptance,
    entry: AcceptedAssignment,
    assignments: dict[str, ReviewAssignment],
) -> None:
    assignment = assignments.get(entry.assignment_id)
    if assignment is None:
        raise invalid("acceptance names an unknown assignment")
    if (
        assignment.role != BOUNDARY_ROLES[receipt.boundary]
        or assignment.scope != (receipt.scope or "feature")
        or assignment.policy_revision != entry.policy_revision
    ):
        raise invalid("acceptance assignment identity differs from its boundary")
    if accepted_assignment_digest(entry) != entry.digest:
        raise invalid("accepted assignment digest differs")

    sources = tuple(
        source
        for source in authoritative_sources(state)
        if source.assignment_id == assignment.id
    )
    source_attempt_ids = tuple(sorted(source.run_id for source in sources))
    source_rounds = tuple(sorted({source.round_number for source in sources}))
    if entry.attempt_ids != source_attempt_ids or entry.round_numbers != source_rounds:
        raise invalid("acceptance assignment attempts or rounds are incomplete")

    attempts = {
        attempt.attempt_id: attempt for attempt in state.review_assignments.attempts
    }
    if any(
        attempt_id not in attempts
        or attempts[attempt_id].assignment_id != assignment.id
        for attempt_id in entry.attempt_ids
    ):
        raise invalid("acceptance attempt belongs to another assignment")

    resolved_records = tuple(
        (record, _record_row(state, record)) for record in entry.records
    )
    referenced_dispositions = [
        cast(AssignmentDisposition, row)
        for record, row in resolved_records
        if record.family == "dispositions"
    ]
    referenced_decisions = [
        cast(Any, row)
        for record, row in resolved_records
        if record.family == "decisions"
    ]
    referenced_verifications = [
        cast(Any, row)
        for record, row in resolved_records
        if record.family == "verifications"
    ]
    source_ids = set(entry.attempt_ids)
    if any(row.run_id not in source_ids for row in referenced_dispositions):
        raise invalid("acceptance disposition belongs to another assignment")
    allowed_scopes = {
        disposition.verification_scope
        for disposition in referenced_dispositions
        if disposition.verification_scope is not None
    }
    if receipt.boundary == "milestone" and receipt.scope is not None:
        allowed_scopes.add(receipt.scope)
    if any(row.scope not in allowed_scopes for row in referenced_verifications):
        raise invalid("acceptance verification does not support its boundary")

    smoke_decision_ids = {
        decision.id
        for decision in referenced_decisions
        if decision.status == "resolved"
        and decision.resolution_source == "user"
        and isinstance(decision.resolution, AcceptDegradedSmoke)
    }
    if any(
        decision.origin_run_id not in source_ids
        and decision.id not in smoke_decision_ids
        for decision in referenced_decisions
    ):
        raise invalid("acceptance decision belongs to another assignment")
    decision_ids = {decision.id for decision in referenced_decisions}
    required_decisions = {
        disposition.decision_id
        for disposition in referenced_dispositions
        if disposition.decision_id is not None
    }
    failed_smoke = any(
        verification.scope == "smoke" and verification.exit_code != 0
        for verification in referenced_verifications
    )
    if failed_smoke:
        if not smoke_decision_ids:
            raise invalid("failed smoke acceptance requires its user decision")
        required_decisions.update(smoke_decision_ids)
    if decision_ids != required_decisions:
        raise invalid("acceptance decision references are incomplete or unrelated")
    verification_scopes = {
        verification.scope for verification in referenced_verifications
    }
    required_verifications = {
        disposition.verification_scope
        for disposition in referenced_dispositions
        if disposition.verification_scope is not None
    }
    if receipt.boundary == "milestone" and receipt.scope is not None:
        required_verifications.add(receipt.scope)
    if verification_scopes != required_verifications:
        raise invalid("acceptance verification references are incomplete or unrelated")
    if any(
        verification.exit_code != 0 and verification.scope != "smoke"
        for verification in referenced_verifications
    ):
        raise invalid("acceptance verification must record a passing execution")

    try:
        closure = assess_review_closure(
            closure_facts(
                state,
                assignment,
                current_basis=entry.source_identity,
                valid_dispositions=tuple(referenced_dispositions),
                policy=policy_at_revision(
                    state, assignment.role, assignment.policy_revision
                ),
                historical_complete=True,
            )
        )
    except ValueError as error:
        raise invalid(f"acceptance closure is invalid: {error}") from error
    if not closure.closed:
        raise invalid("acceptance assignment is not closed by its referenced evidence")


def _completed_boundary_assignments(
    state: StateFile,
) -> dict[tuple[str, str | None], str]:
    from heddle.contracts.schemas import STAGES

    materialized = {
        (row.role, row.scope): row.id for row in state.review_assignments.assignments
    }
    required: dict[tuple[str, str | None], str] = {}
    assert state.feature_policy is not None
    for boundary, role in BOUNDARY_ROLES.items():
        if boundary == "milestone":
            for milestone in state.milestones:
                if milestone.status != "done":
                    continue
                assignment_id = materialized.get((role, milestone.id))
                if assignment_id is not None:
                    required[(boundary, milestone.id)] = assignment_id
            continue
        _before, after = PHASE_BOUNDARIES[boundary]
        if STAGES.index(state.stage) < STAGES.index(after):
            continue
        assignment_id = materialized.get((role, "feature"))
        if assignment_id is not None:
            required[(boundary, None)] = assignment_id
    return required


def validate_boundary_acceptances(
    state: StateFile,
) -> dict[str, BoundaryAcceptance]:
    """Validate sealed boundary receipts and return their assignment lookup."""
    assignments = {row.id: row for row in state.review_assignments.assignments}
    receipts_by_boundary: dict[tuple[str, str | None], BoundaryAcceptance] = {}
    accepted: dict[str, BoundaryAcceptance] = {}
    prior_revision = -1
    for receipt in state.review_assignments.acceptances:
        key = (receipt.boundary, receipt.scope)
        if key in receipts_by_boundary:
            raise invalid("boundary acceptance must be unique")
        receipts_by_boundary[key] = receipt
        if receipt.feature != state.feature:
            raise invalid("boundary acceptance belongs to another feature")
        if receipt.revision_after != receipt.revision_before + 1 or (
            receipt.revision_after > state.revision
        ):
            raise invalid("boundary acceptance revision is inconsistent")
        if receipt.revision_after <= prior_revision:
            raise invalid("boundary acceptances must follow commit order")
        prior_revision = receipt.revision_after
        expected_stages = PHASE_BOUNDARIES.get(receipt.boundary)
        valid_milestone_stages = (
            receipt.boundary == "milestone"
            and receipt.stage_before == receipt.stage_after
            and receipt.stage_before in {"implement", "complete"}
        )
        if not valid_milestone_stages and (
            expected_stages is None
            or (receipt.stage_before, receipt.stage_after) != expected_stages
        ):
            raise invalid("boundary acceptance transition is inconsistent")
        if (receipt.boundary == "milestone") != (receipt.scope is not None):
            raise invalid("only milestone acceptance carries a scope")
        for entry in receipt.assignments:
            if entry.assignment_id in accepted:
                raise invalid("assignment has more than one acceptance receipt")
            _validate_acceptance_entry(state, receipt, entry, assignments)
            accepted[entry.assignment_id] = receipt

    required = _completed_boundary_assignments(state)
    for key, assignment_id in required.items():
        candidate = receipts_by_boundary.get(key)
        if candidate is None or assignment_id not in {
            entry.assignment_id for entry in candidate.assignments
        }:
            label = key[0] if key[1] is None else f"{key[0]} {key[1]}"
            raise invalid(f"completed {label} boundary requires an acceptance receipt")
    for key, receipt in receipts_by_boundary.items():
        expected_assignment = required.get(key)
        if expected_assignment is None or tuple(
            entry.assignment_id for entry in receipt.assignments
        ) != (expected_assignment,):
            raise invalid("acceptance receipt does not match a completed duty boundary")
    return accepted


def pending_retained(
    state: StateFile, assignment_id: str | None = None
) -> tuple[RetainedReview, ...]:
    interpreted = {row.run_id for row in review_interpretations(state)}
    return tuple(
        row
        for row in retained_responses(state)
        if row.run_id not in interpreted
        and (assignment_id is None or row.assignment_id == assignment_id)
    )


def retained_responses(state: StateFile) -> tuple[RetainedReview, ...]:
    if state.review_assignments.attempts:
        return tuple(
            RetainedReview(
                row.attempt_id,
                row.assignment_id,
                row.round_number,
                row.reviewer_slot,
                row.outcome.capture.path,
                row.outcome.capture.sha256,
                row.invocation.input_hash,
                row.invocation.review_basis_hash,
                row.created_at,
                row.outcome.warnings,
            )
            for row in state.review_assignments.attempts
            if isinstance(row.outcome, UsableReviewCapture)
        )
    return state.review_assignments.retained


def review_interpretations(state: StateFile) -> tuple[ReviewInterpretation, ...]:
    if state.review_assignments.attempts:
        return tuple(
            ReviewInterpretation(
                row.attempt_id,
                row.outcome.capture.sha256,
                row.interpretation.result.sha256,
                row.interpretation.author,
                row.interpretation.reason,
                row.interpretation.at or row.created_at,
            )
            for row in state.review_assignments.attempts
            if isinstance(row.outcome, UsableReviewCapture)
            and row.interpretation is not None
        )
    return state.review_assignments.interpretations


def attempts_for_assignment(
    attempts: tuple[ReviewAttempt, ...], assignment_id: str
) -> tuple[ReviewAttempt, ...]:
    """Select original work only from the assignment that commissioned it."""
    return tuple(row for row in attempts if row.assignment_id == assignment_id)


def authoritative_sources(state: StateFile) -> tuple[AssignmentSource, ...]:
    """Project canonical source rows from accepted attempt membership."""
    ledger = state.review_assignments
    if not ledger.attempts:
        return ledger.sources
    sources = {row.run_id: row for row in ledger.sources}
    accepted = tuple(
        attempt
        for attempt in ledger.attempts
        if isinstance(attempt.outcome, CanonicalReview)
        or attempt.interpretation is not None
    )
    try:
        return tuple(sources[attempt.attempt_id] for attempt in accepted)
    except KeyError as error:
        raise invalid(
            f"accepted attempt has no derived source view: {error.args[0]}"
        ) from error


def attempt_artifacts(attempts: tuple[ReviewAttempt, ...]) -> tuple[ArtifactRef, ...]:
    """Return immutable artifacts plus unsuperseded legacy rolling aliases."""
    artifacts: list[ArtifactRef] = []
    by_path: dict[str, ArtifactRef] = {}
    positions: dict[str, int] = {}
    rolling_aliases: set[str] = set()
    superseded_aliases = _superseded_rolling_aliases(attempts)
    for attempt in attempts:
        candidates: list[ArtifactRef] = list(attempt.artifacts)
        if isinstance(attempt.outcome, CanonicalReview):
            candidates.append(attempt.outcome.result)
        elif isinstance(attempt.outcome, UsableReviewCapture):
            candidates.append(attempt.outcome.capture)
        if attempt.interpretation is not None:
            candidates.append(attempt.interpretation.result)
        for reference in candidates:
            rolling = _is_rolling_attempt_alias(attempt, reference)
            if rolling and reference.path in superseded_aliases:
                continue
            previous = by_path.get(reference.path)
            if previous is not None and previous != reference:
                if reference.path not in rolling_aliases or not rolling:
                    raise ValueError(f"artifact identity conflict: {reference.path}")
                by_path[reference.path] = reference
                artifacts[positions[reference.path]] = reference
            if previous is None:
                by_path[reference.path] = reference
                positions[reference.path] = len(artifacts)
                artifacts.append(reference)
                if rolling:
                    rolling_aliases.add(reference.path)
    return tuple(artifacts)


def _superseded_rolling_aliases(
    attempts: tuple[ReviewAttempt, ...],
) -> frozenset[str]:
    """Drop legacy aliases once their gate records immutable replacements."""
    superseded: set[str] = set()
    for attempt in attempts:
        aliases = _rolling_alias_paths(attempt)
        if aliases is None:
            continue
        log_alias, summary_alias = aliases
        log_stem = re.escape(log_alias.removesuffix(".log"))
        summary_stem = re.escape(summary_alias.removesuffix(".gate-summary.json"))
        for reference in attempt.artifacts:
            if reference.role == "log" and re.fullmatch(
                rf"{log_stem}\.[0-9a-f]{{64}}\.log", reference.path
            ):
                superseded.add(log_alias)
            if reference.role == "derived" and re.fullmatch(
                rf"{summary_stem}\.[0-9a-f]{{64}}\.review\.gate-summary\.json",
                reference.path,
            ):
                superseded.add(summary_alias)
    return frozenset(superseded)


def _is_rolling_attempt_alias(attempt: ReviewAttempt, reference: ArtifactRef) -> bool:
    """Recognize only aliases emitted by the current native gate path scheme."""
    aliases = _rolling_alias_paths(attempt)
    if aliases is None:
        return False
    log_alias, summary_alias = aliases
    return (reference.role == "log" and reference.path == log_alias) or (
        reference.role == "derived" and reference.path == summary_alias
    )


def _rolling_alias_paths(attempt: ReviewAttempt) -> tuple[str, str] | None:
    try:
        _feature, role, scope, _revision = attempt.assignment_id.rsplit(":", 3)
    except ValueError:
        return None
    gate = "impl-review" if role == "peer-review-sequential" else role
    if role == "milestone-review":
        if re.fullmatch(r"m[1-9][0-9]*", scope) is None:
            return None
        gate = f"{gate}-{scope}"
    if attempt.invocation.cli != "claude":
        gate = f"{gate}.{attempt.invocation.cli}"
    return (
        f"reviews/{gate}.log",
        f"reviews/{gate}.gate-summary.json",
    )


def validate_retained_transition(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Protect accepted history while allowing unrelated append-only growth."""
    original_ledger = before.get("review_assignments") or {}
    current_ledger = after.get("review_assignments") or {}
    original_attempts = original_ledger.get("attempts", [])
    current_attempts = current_ledger.get("attempts", [])
    if not isinstance(original_attempts, list) or not isinstance(
        current_attempts, list
    ):
        raise invalid("attempt history requires list representation")
    if len(current_attempts) < len(original_attempts):
        raise invalid("attempt history is append-only")
    for original, current in zip(original_attempts, current_attempts, strict=False):
        try:
            old = parse_review_attempt(original)
            new = parse_review_attempt(current)
        except ValueError as error:
            raise invalid(f"attempt history is append-only: {error}") from error
        if old == new:
            continue
        if (
            old.interpretation is not None
            or new.interpretation is None
            or replace(new, interpretation=None, source=old.source) != old
            or old.source is not None
            and new.source != old.source
        ):
            raise invalid(
                "attempt history permits only one additive lead interpretation"
            )

    original_acceptances = original_ledger.get("acceptances", [])
    current_acceptances = current_ledger.get("acceptances", [])
    if not isinstance(original_acceptances, list) or not isinstance(
        current_acceptances, list
    ):
        raise invalid("acceptance history requires list representation")
    if current_acceptances[: len(original_acceptances)] != original_acceptances:
        raise invalid("sealed acceptance history is append-only and immutable")

    sealed_assignments: set[str] = set()
    sealed_attempts: set[str] = set()
    for receipt in original_acceptances:
        if not isinstance(receipt, dict) or not isinstance(
            receipt.get("assignments"), list
        ):
            raise invalid("sealed acceptance history is malformed")
        for entry in receipt["assignments"]:
            if not isinstance(entry, dict):
                raise invalid("sealed acceptance assignment is malformed")
            assignment_id = entry.get("assignment_id")
            attempt_ids = entry.get("attempt_ids")
            records = entry.get("records")
            if (
                not isinstance(assignment_id, str)
                or not isinstance(attempt_ids, list)
                or not isinstance(records, list)
            ):
                raise invalid("sealed acceptance assignment is malformed")
            sealed_assignments.add(assignment_id)
            sealed_attempts.update(
                attempt_id for attempt_id in attempt_ids if isinstance(attempt_id, str)
            )
            for record in records:
                if not isinstance(record, dict):
                    raise invalid("sealed acceptance record is malformed")
                family, index = record.get("family"), record.get("index")
                old_rows = (
                    before.get(family)
                    if family in {"decisions", "verifications"}
                    else original_ledger.get(family)
                    if family == "dispositions"
                    else None
                )
                new_rows = (
                    after.get(family)
                    if family in {"decisions", "verifications"}
                    else current_ledger.get(family)
                    if family == "dispositions"
                    else None
                )
                if (
                    type(index) is not int
                    or not isinstance(old_rows, list)
                    or not isinstance(new_rows, list)
                    or index < 0
                    or index >= len(old_rows)
                    or index >= len(new_rows)
                    or new_rows[index] != old_rows[index]
                ):
                    raise invalid("sealed acceptance record was removed or changed")

    def indexed(rows: Any, key: str) -> dict[str, Any]:
        if not isinstance(rows, list):
            raise invalid("sealed acceptance dependency requires list representation")
        return {
            row[key]: row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get(key), str)
        }

    old_assignments = indexed(original_ledger.get("assignments", []), "id")
    new_assignments = indexed(current_ledger.get("assignments", []), "id")
    old_attempts = indexed(original_attempts, "attempt_id")
    new_attempts = indexed(current_attempts, "attempt_id")
    if any(
        new_assignments.get(identifier) != old_assignments.get(identifier)
        for identifier in sealed_assignments
    ) or any(
        new_attempts.get(identifier) != old_attempts.get(identifier)
        for identifier in sealed_attempts
    ):
        raise invalid("sealed acceptance assignment or attempt was removed or changed")

    appended_attempts = current_attempts[len(original_attempts) :]
    if any(
        isinstance(row, dict) and row.get("assignment_id") in sealed_assignments
        for row in appended_attempts
    ):
        raise invalid("sealed acceptance assignment cannot gain review attempts")
    old_dispositions = original_ledger.get("dispositions", [])
    new_dispositions = current_ledger.get("dispositions", [])
    if not isinstance(old_dispositions, list) or not isinstance(new_dispositions, list):
        raise invalid("disposition history requires list representation")
    if any(
        isinstance(row, dict) and row.get("run_id") in sealed_attempts
        for row in new_dispositions[len(old_dispositions) :]
    ):
        raise invalid("sealed acceptance assignment cannot gain dispositions")
    old_decisions = before.get("decisions", [])
    new_decisions = after.get("decisions", [])
    if not isinstance(old_decisions, list) or not isinstance(new_decisions, list):
        raise invalid("decision history requires list representation")
    if any(
        isinstance(row, dict) and row.get("origin_run_id") in sealed_attempts
        for row in new_decisions[len(old_decisions) :]
    ):
        raise invalid("sealed acceptance assignment cannot gain decisions")


def selected_policy(state: StateFile, role: str) -> GatePolicy:
    if state.feature_policy is None:
        raise invalid("current confirmed policy is required")
    return next(row for row in state.feature_policy.entries if row.role == role)


def assignment_for(state: StateFile, role: str, scope: str) -> ReviewAssignment:
    for assignment in state.review_assignments.assignments:
        if (assignment.role, assignment.scope) == (role, scope):
            return assignment
    policy = selected_policy(state, role)
    if (policy.scope == "feature") != (scope == "feature") or (
        scope != "feature" and scope not in {m.id for m in state.milestones}
    ):
        raise invalid(f"invalid assignment scope {scope} for {role}")
    assert state.feature_policy is not None
    revision = state.feature_policy.revision
    return ReviewAssignment(
        f"{state.feature}:{role}:{scope}:p{revision}", role, scope, revision, ()
    )


def _check_round_allowance(policy: GatePolicy, number: int) -> None:
    if policy.limit is not None and number > policy.limit:
        raise invalid(
            f"round {number} exceeds the confirmed round limit {policy.limit}; "
            "explicitly amend policy before requesting more reviews"
        )


def new_round(
    state: StateFile,
    assignment: ReviewAssignment,
    *,
    purpose: str = "discovery",
    reason: str = "Initial confirmed review",
    before_open: tuple = (),
    scope_change: ReviewScopeChange | None = None,
) -> AssignmentRound:
    policy = selected_policy(state, assignment.role)
    number = len(assignment.rounds) + 1
    _check_round_allowance(policy, number)
    slots = [ReviewerSlot("primary", policy.primary)]
    if number == 1 and policy.secondary is not None:
        slots.append(ReviewerSlot("secondary", policy.secondary))
    assert state.feature_policy is not None
    return AssignmentRound(
        number,
        state.feature_policy.revision,
        purpose,
        reason,
        scope_identity(assignment, scope_change),
        before_open,
        tuple(slots),
        scope_change,
    )


def scope_identity(
    assignment: ReviewAssignment, change: ReviewScopeChange | None
) -> str:
    previous = (
        assignment.rounds[-1].scope_identity
        if assignment.rounds
        else f"{assignment.role}:{assignment.scope}"
    )
    if change is None:
        return previous
    if not assignment.rounds:
        raise invalid("initial assignment already defines its reviewed scope")
    if any(
        r.scope_change is not None and r.scope_change.references == change.references
        for r in assignment.rounds
    ):
        raise invalid("scope change repeats already captured contract evidence")
    return hashlib.sha256(
        json.dumps((previous, change.references), sort_keys=True).encode()
    ).hexdigest()


def launch_round(state: StateFile, assignment: ReviewAssignment) -> AssignmentRound:
    return assignment.rounds[-1] if assignment.rounds else new_round(state, assignment)


def assignment_sealed(state: StateFile, assignment: ReviewAssignment) -> bool:
    return any(
        entry.assignment_id == assignment.id
        for receipt in state.review_assignments.acceptances
        for entry in receipt.assignments
    )


def verification_targets(
    state: StateFile, assignment: ReviewAssignment, row: AssignmentRound
) -> tuple[tuple[str, str], ...] | None:
    """Derive targets without changing the open-concern progress operand."""
    from heddle.kernel.review_closure import required_obligation

    if row.purpose != "verification" or row.number < 2:
        return None
    previous = {
        source.run_id
        for source in authoritative_sources(state)
        if source.assignment_id == assignment.id
        and source.round_number == row.number - 1
    }
    return tuple(
        sorted(
            set(row.before_open)
            | {
                (item.run_id, item.finding_id)
                for item in closure_facts(
                    state, assignment, current_basis=""
                ).obligations
                if item.run_id in previous and required_obligation(item)
            }
        )
    )


def source_run(state: StateFile, run_id: str) -> GateRun:
    for fact in state.gates:
        for run in fact.runs:
            if run.run_id == run_id:
                return run
    raise invalid(f"unknown source run {run_id}")


def reviewer_identity(run: GateRun) -> str:
    execution = run.execution_config or {}
    return json.dumps(
        {key: execution.get(key) for key in ("cli", "model", "reasoning_effort")},
        sort_keys=True,
    )


def inspection_identity(
    state: StateFile, run_id: str, *, origin: str | None = None
) -> str:
    """Bind review evidence to the actual current reviewer identity."""
    del origin
    return reviewer_identity(source_run(state, run_id))


def continuation_authorized(state: StateFile, assignment: ReviewAssignment) -> bool:
    """A user ruling buys the next round once; history consumes it by advancing."""
    return any(
        d.id == assignment.stop_decision_id
        and d.status == "resolved"
        and d.resolution_source == "user"
        and isinstance(d.resolution, ContinueReview)
        and d.resolution.assignment_id == assignment.id
        and d.resolution.stopped_round == len(assignment.rounds)
        for d in state.decisions
    )


def closure_facts(
    state: StateFile,
    assignment: ReviewAssignment,
    *,
    current_basis: str,
    valid_dispositions: tuple[AssignmentDisposition, ...] | None = None,
    policy: GatePolicy | None = None,
    historical_complete: bool = False,
) -> ReviewClosureFacts:
    ledger = state.review_assignments
    sources = tuple(
        source
        for source in authoritative_sources(state)
        if source.assignment_id == assignment.id
    )
    source_ids = {s.run_id for s in sources}
    dispositions = tuple(d for d in ledger.dispositions if d.run_id in source_ids)
    latest_dispositions = {
        (disposition.run_id, disposition.finding_id): disposition
        for disposition in dispositions
    }
    inspections = {
        reference
        for reference, disposition in latest_dispositions.items()
        if disposition.requires_inspection
    }
    obligations = []
    for source in sources:
        for finding in (
            *source.findings,
            *(
                (FindingFact("@coverage", "important", "implement"),)
                if source.coverage_required
                else ()
            ),
        ):
            owner = next(
                (
                    d.id
                    for d in state.decisions
                    if (d.origin_run_id, d.origin_finding_id)
                    == (source.run_id, finding.finding_id)
                ),
                None,
            )
            obligations.append(
                ReviewObligation(
                    source.run_id,
                    finding.finding_id,
                    finding.severity,
                    finding.classification,
                    inspection_identity(state, source.run_id),
                    (source.run_id, finding.finding_id) in inspections,
                    owner,
                )
            )
    qualified = dispositions if valid_dispositions is None else valid_dispositions
    resolutions = tuple(
        ReviewResolution(
            d.run_id,
            d.finding_id,
            d.status,
            d.references,
            d.evidence_basis,
            d.reason,
            inspection_identity(state, d.review_run_id, origin=d.run_id)
            if d.review_run_id
            else None,
            d.decision_id,
        )
        for d in qualified
        if d.run_id in source_ids
    )
    resolved = {
        (d.run_id, d.finding_id)
        for d in qualified
        if (d.status in {"addressed", "settled"} and d.evidence_basis == current_basis)
    }
    rounds = []
    for row in assignment.rounds:
        accepted = tuple(s for s in sources if s.round_number == row.number)
        rounds.append(
            ReviewRound(
                row.number,
                row.purpose,
                row.scope_identity,
                tuple(slot.name for slot in row.slots),
                tuple(s.reviewer_slot for s in accepted),
                sum(s.new_important for s in accepted),
                row.before_open,
                tuple(ref for ref in row.before_open if ref in resolved),
            )
        )
    return ReviewClosureFacts(
        policy or selected_policy(state, assignment.role),
        tuple(rounds),
        tuple(obligations),
        resolutions,
        tuple(
            ReviewDecision(d.id, d.origin_run_id, d.origin_finding_id, d.status)
            for d in state.decisions
            if d.origin_run_id and d.origin_finding_id
        ),
        current_basis,
        historical_complete,
        continuation_authorized(state, assignment),
    )


def validate_assignments(state: StateFile) -> None:
    ledger = state.review_assignments
    attempts = {row.attempt_id: row for row in ledger.attempts}
    attempt_ids = set(attempts)
    if len(attempt_ids) != len(ledger.attempts):
        raise invalid("attempt identity must be unique")
    if any(
        review_attempt_document(row) != review_attempt_document(observed)
        for row, observed in zip(
            ledger.attempts,
            (
                parse_review_attempt(review_attempt_document(row))
                for row in ledger.attempts
            ),
            strict=True,
        )
    ):
        raise invalid("attempt representation is not canonical")
    assignments = {a.id: a for a in ledger.assignments}
    if len(assignments) != len(ledger.assignments) or len(
        {(a.role, a.scope) for a in ledger.assignments}
    ) != len(assignments):
        raise invalid("duplicate assignment identity or role/scope")
    for decision in state.decisions:
        resolution = decision.resolution
        if isinstance(resolution, ContinueReview):
            owner = assignments.get(resolution.assignment_id)
            if (
                owner is None
                or not 1 <= resolution.stopped_round <= len(owner.rounds)
                or decision.source != "session"
                or decision.escalation_class != 2
                or decision.resolution_source != "user"
            ):
                raise invalid("review continuation must bind an existing stopped round")
    sources = {s.run_id: s for s in ledger.sources}
    if len(sources) != len(ledger.sources):
        raise invalid("source run must belong to exactly one assignment")
    captures = {row.run_id: row for row in ledger.retained}
    interpretations = {row.run_id: row for row in ledger.interpretations}
    if len(captures) != len(ledger.retained) or len(interpretations) != len(
        ledger.interpretations
    ):
        raise invalid("duplicate retained review or interpretation identity")
    if attempts:
        for source in ledger.sources:
            attempt = attempts.get(source.run_id)
            if attempt is None or (
                not isinstance(attempt.outcome, CanonicalReview)
                and attempt.interpretation is None
            ):
                raise invalid("accepted source is not derived from its review attempt")
            if (
                source.assignment_id,
                source.round_number,
                source.reviewer_slot,
            ) != (
                attempt.assignment_id,
                attempt.round_number,
                attempt.reviewer_slot,
            ):
                raise invalid("accepted source differs from its review attempt")
        for capture in ledger.retained:
            attempt = attempts.get(capture.run_id)
            if attempt is None or not isinstance(attempt.outcome, UsableReviewCapture):
                raise invalid(
                    "retained response is not derived from its review attempt"
                )
            if (
                capture.assignment_id,
                capture.round_number,
                capture.reviewer_slot,
                capture.artifact,
                capture.artifact_sha256,
            ) != (
                attempt.assignment_id,
                attempt.round_number,
                attempt.reviewer_slot,
                attempt.outcome.capture.path,
                attempt.outcome.capture.sha256,
            ):
                raise invalid("retained response differs from its review attempt")
        for interpretation in ledger.interpretations:
            attempt = attempts.get(interpretation.run_id)
            if attempt is None or attempt.interpretation is None:
                raise invalid("interpretation is not derived from its review attempt")
            capture_outcome = cast(UsableReviewCapture, attempt.outcome)
            if (
                interpretation.capture_sha256,
                interpretation.result_sha256,
                interpretation.author,
                interpretation.reason,
                interpretation.at,
            ) != (
                capture_outcome.capture.sha256,
                attempt.interpretation.result.sha256,
                attempt.interpretation.author,
                attempt.interpretation.reason,
                attempt.interpretation.at,
            ):
                raise invalid("interpretation differs from its review attempt")
    slots = set()
    for capture in ledger.retained:
        owner = assignments.get(capture.assignment_id)
        slot_key = (capture.assignment_id, capture.round_number, capture.reviewer_slot)
        if (
            owner is None
            or capture.round_number > len(owner.rounds)
            or slot_key in slots
        ):
            raise invalid("retained response needs one unique current assignment slot")
        slots.add(slot_key)
        row = owner.rounds[capture.round_number - 1]
        if row.policy_revision is None or capture.reviewer_slot not in {
            s.name for s in row.slots
        }:
            raise invalid("retained response differs from the frozen reviewer slot")
        matches = [
            source
            for source in ledger.sources
            if (source.assignment_id, source.round_number, source.reviewer_slot)
            == slot_key
        ]
        if matches and (
            len(matches) != 1
            or matches[0].run_id != capture.run_id
            or capture.run_id not in interpretations
        ):
            raise invalid(
                "captured slot can only be accepted by its own interpretation"
            )
        if (
            any(
                run.run_id == capture.run_id
                for fact in state.gates
                for run in fact.runs
            )
            and not matches
        ):
            raise invalid("retained identity cannot alias another gate event")
    for interpretation in ledger.interpretations:
        original_capture = captures.get(interpretation.run_id)
        source_row = sources.get(interpretation.run_id)
        if original_capture is None or source_row is None:
            raise invalid(
                "interpretation requires its original capture and accepted source"
            )
        run = source_run(state, interpretation.run_id)
        if (
            interpretation.capture_sha256 != original_capture.artifact_sha256
            or interpretation.result_sha256 != run.artifact_sha256
            or run.input_hash != original_capture.input_hash
            or run.review_basis_hash != original_capture.review_basis_hash
            or run.at != original_capture.at
            or (run.assignment_id, run.round_number, run.reviewer_slot)
            != (
                original_capture.assignment_id,
                original_capture.round_number,
                original_capture.reviewer_slot,
            )
        ):
            raise invalid("interpretation must preserve original response identity")
    revisions = {p.revision: p for p in state.policy_history}
    for assignment in ledger.assignments:
        if assignment.policy_revision not in revisions:
            raise invalid("assignment policy revision has no confirmed history")
        if assignment.scope != "feature" and assignment.scope not in {
            m.id for m in state.milestones
        }:
            raise invalid("assignment names an unknown milestone")
        policy = next(
            p
            for p in revisions[assignment.policy_revision].entries
            if p.role == assignment.role
        )
        if (policy.scope == "feature") != (assignment.scope == "feature"):
            raise invalid("assignment scope differs from its confirmed role")
        if not assignment.rounds or (
            assignment.rounds[0].policy_revision != assignment.policy_revision
        ):
            raise invalid("assignment must begin under its originating policy revision")
        previous_revision = assignment.policy_revision
        for row in assignment.rounds:
            if row.policy_revision not in revisions:
                raise invalid("round policy revision has no confirmed history")
            if row.policy_revision < previous_revision:
                raise invalid("round policy revisions cannot move backwards")
            previous_revision = row.policy_revision
            authorized = next(
                p
                for p in revisions[row.policy_revision].entries
                if p.role == assignment.role
            )
            _check_round_allowance(authorized, row.number)
            expected = [ReviewerSlot("primary", authorized.primary)]
            if row.number == 1 and authorized.secondary is not None:
                expected.append(ReviewerSlot("secondary", authorized.secondary))
            if authorized.mode == "off" or row.slots != tuple(expected):
                raise invalid("frozen reviewer slots differ from confirmed policy")
            prior = ReviewAssignment(
                assignment.id,
                assignment.role,
                assignment.scope,
                assignment.policy_revision,
                assignment.rounds[: row.number - 1],
            )
            if row.scope_identity != scope_identity(prior, row.scope_change):
                raise invalid(
                    "scope identity differs from the recorded contract change"
                )
        if assignment.stop_decision_id is not None and not any(
            d.id == assignment.stop_decision_id and d.kind == "question"
            for d in state.decisions
        ):
            raise invalid("stop decision does not exist")
    for source in ledger.sources:
        if not source.coverage_required:
            raise invalid(
                "current accepted sources require explicit coverage disposition"
            )
        if source.new_important > sum(
            f.severity == "important" and f.classification == "implement"
            for f in source.findings
        ):
            raise invalid("new Important count exceeds the canonical findings")
        if source.assignment_id not in assignments:
            raise invalid("source names an unknown assignment")
        assignment = assignments[source.assignment_id]
        run = source_run(state, source.run_id)
        if run.verdict.get("status") == "error":
            raise invalid("provider errors cannot be accepted sources")
        if source.round_number > len(assignment.rounds):
            raise invalid("source names an unknown round")
        if source.round_number == 0:
            raise invalid("current accepted sources require a positive round")
        if (run.assignment_id, run.round_number, run.reviewer_slot) != (
            source.assignment_id,
            source.round_number,
            source.reviewer_slot,
        ):
            raise invalid("source differs from the accepted invocation binding")
    for fact in state.gates:
        for run in fact.runs:
            if attempts:
                attempt = attempts.get(run.run_id)
                if attempt is None:
                    raise invalid("gate event is not derived from a review attempt")
                expected_artifact = (
                    attempt.outcome.result
                    if isinstance(attempt.outcome, CanonicalReview)
                    else attempt.interpretation.result
                    if attempt.interpretation is not None
                    else attempt.artifacts[0]
                    if attempt.artifacts
                    else None
                )
                if (
                    (
                        run.assignment_id,
                        run.round_number,
                        run.reviewer_slot,
                        run.input_hash,
                        run.review_basis_hash,
                    )
                    != (
                        attempt.assignment_id,
                        attempt.round_number,
                        attempt.reviewer_slot,
                        attempt.invocation.input_hash,
                        attempt.invocation.review_basis_hash,
                    )
                    or expected_artifact is not None
                    and (
                        run.artifact,
                        run.artifact_sha256,
                    )
                    != (expected_artifact.path, expected_artifact.sha256)
                ):
                    raise invalid("gate event differs from its review attempt")
                if (run.verdict.get("status") == "error") != isinstance(
                    attempt.outcome, EngineFailure
                ):
                    raise invalid("gate event outcome differs from its review attempt")
            run_assignment = assignments.get(run.assignment_id or "")
            if run_assignment is None or (
                run_assignment.role,
                run_assignment.scope,
            ) != (
                fact.gate,
                fact.scope,
            ):
                raise invalid("gate event has no matching assignment")
            if run.round_number is None or run.round_number > len(
                run_assignment.rounds
            ):
                raise invalid("gate event has no matching round")
            row = run_assignment.rounds[run.round_number - 1]
            slot = next((s for s in row.slots if s.name == run.reviewer_slot), None)
            execution = run.execution_config or {}
            if slot is None or any(
                execution.get(key) != getattr(slot.reviewer, key)
                for key in ("cli", "model", "reasoning_effort")
            ):
                raise invalid("gate execution differs from frozen reviewer slot")
            if run.verdict.get("status") != "error" and run.run_id not in sources:
                raise invalid(
                    "accepted gate event is missing its canonical source projection"
                )
    origins = {(s.run_id, f.finding_id) for s in ledger.sources for f in s.findings}
    origins.update((s.run_id, "@coverage") for s in ledger.sources)
    for disposition in ledger.dispositions:
        if (
            disposition.source_disposition is not None
            or disposition.evidence_basis is None
        ):
            raise invalid("dispositions require current evidence and no legacy source")
        if (disposition.run_id, disposition.finding_id) not in origins:
            raise invalid("disposition names an unknown original finding")
        if disposition.decision_id is not None and not any(
            d.id == disposition.decision_id
            and (d.origin_run_id, d.origin_finding_id)
            == (disposition.run_id, disposition.finding_id)
            for d in state.decisions
        ):
            raise invalid(
                "disposition decision must name the original concern "
                f"{disposition.run_id}#{disposition.finding_id}"
            )
        if disposition.review_run_id is not None:
            proof = sources.get(disposition.review_run_id)
            original = sources[disposition.run_id]
            if (
                proof is None
                or proof.assignment_id != original.assignment_id
                or proof.round_number <= original.round_number
            ):
                raise invalid("review evidence must come from a later assignment round")
        required = {
            "decision": disposition.decision_id,
            "review": disposition.review_run_id,
            "verification": disposition.verification_scope,
        }
        if (
            disposition.evidence_kind in required
            and not required[disposition.evidence_kind]
        ):
            raise invalid(
                f"{disposition.evidence_kind} evidence needs its native reference"
            )
    for assignment in ledger.assignments:
        try:
            assess_review_closure(closure_facts(state, assignment, current_basis=""))
        except ValueError as error:
            raise invalid(str(error)) from error
