"""The sole current ledger of assignments and origin-preserving lead facts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, ClassVar, Literal, cast
from uuid import UUID

from heddle.contracts.feature_policy import OriginalRef, Reviewer

SCHEMA = "heddle.review-assignments/v3"
DISPOSITION_STATUSES = (
    "retained",
    "addressed",
    "awaiting-decision",
    "settled",
)
EVIDENCE_KINDS = (
    "inspection",
    "contract",
    "verification",
    "review",
    "decision",
    "unavailable",
)
ROLE_STAGES = {
    "spec-review": "spec-review",
    "plan-review": "plan-review",
    "review-test-scaffolding": "scaffold",
    "milestone-review": "implement",
    "peer-review-sequential": "peer-review",
    "behavior-review": "peer-review",
    "complexity-review": "peer-review",
    "robustness-analysis": "robustness",
}


@dataclass(frozen=True)
class ReviewerSlot:
    name: str
    reviewer: Reviewer


@dataclass(frozen=True)
class ReviewScopeChange:
    reason: str
    references: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AssignmentRound:
    number: int
    policy_revision: int
    purpose: str
    reason: str
    scope_identity: str
    before_open: tuple[OriginalRef, ...]
    slots: tuple[ReviewerSlot, ...]
    scope_change: ReviewScopeChange | None = None


@dataclass(frozen=True)
class ReviewAssignment:
    id: str
    role: str
    scope: str
    policy_revision: int
    rounds: tuple[AssignmentRound, ...]
    stop_decision_id: str | None = None


@dataclass(frozen=True)
class FindingFact:
    finding_id: str
    severity: str
    classification: str


@dataclass(frozen=True)
class AssignmentSource:
    assignment_id: str
    run_id: str
    round_number: int
    reviewer_slot: str
    findings: tuple[FindingFact, ...]
    new_important: int
    coverage_required: bool = True


@dataclass(frozen=True)
class AssignmentDisposition:
    run_id: str
    finding_id: str
    status: str
    evidence_kind: str
    references: tuple[tuple[str, str], ...]
    evidence_basis: str | None
    reason: str
    requires_inspection: bool
    decision_id: str | None
    review_run_id: str | None
    verification_scope: str | None
    at: str
    # Original bootstrap ledger ordinal or canonical report/row ordinal.
    # Historical evidence has no newly observed source-basis claim.
    source_disposition: tuple[str, int] | None = None


@dataclass(frozen=True)
class RetainedReview:
    """A completed provider attempt, not yet necessarily an accepted review."""

    run_id: str
    assignment_id: str
    round_number: int
    reviewer_slot: str
    artifact: str
    artifact_sha256: str
    input_hash: str
    review_basis_hash: str
    at: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ReviewInterpretation:
    run_id: str
    capture_sha256: str
    result_sha256: str
    author: str
    reason: str
    at: str


type ArtifactRole = Literal[
    "canonical", "capture", "evidence", "derived", "temporary", "log"
]


@dataclass(frozen=True)
class ArtifactRef:
    """One emitted workspace artifact and its retention role."""

    path: str
    sha256: str
    role: ArtifactRole
    kind: Literal["file"] = "file"
    mode: int | None = None


@dataclass(frozen=True)
class AttemptInvocation:
    """Immutable reviewer selection and content identities for one attempt."""

    cli: str
    model: str
    reasoning_effort: str
    sandbox: str
    input_hash: str
    review_basis_hash: str
    prompt_version: str
    effective_prompt_sha256: str


@dataclass(frozen=True)
class EngineFailure:
    kind: ClassVar[str] = "engine-failure"
    reason: str
    detail: str


@dataclass(frozen=True)
class UsableReviewCapture:
    kind: ClassVar[str] = "usable-capture"
    capture: ArtifactRef
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalReview:
    kind: ClassVar[str] = "canonical-review"
    result: ArtifactRef


type ReviewOutcome = EngineFailure | UsableReviewCapture | CanonicalReview


@dataclass(frozen=True)
class LeadInterpretation:
    author: str
    reason: str
    result: ArtifactRef
    at: str | None = None


@dataclass(frozen=True)
class ReviewAttempt:
    attempt_id: str
    assignment_id: str
    round_number: int
    reviewer_slot: str
    invocation: AttemptInvocation
    outcome: ReviewOutcome
    created_at: str
    artifacts: tuple[ArtifactRef, ...] = ()
    interpretation: LeadInterpretation | None = None
    source: AssignmentSource | None = None


type AcceptanceRecordFamily = Literal["decisions", "dispositions", "verifications"]


@dataclass(frozen=True)
class AcceptanceRecord:
    family: AcceptanceRecordFamily
    index: int
    sha256: str


@dataclass(frozen=True)
class AcceptedAssignment:
    assignment_id: str
    policy_revision: int
    source_identity: str
    round_numbers: tuple[int, ...]
    attempt_ids: tuple[str, ...]
    records: tuple[AcceptanceRecord, ...]
    digest: str


@dataclass(frozen=True)
class BoundaryAcceptance:
    feature: str
    boundary: str
    scope: str | None
    stage_before: str
    stage_after: str
    accepted_at: str
    revision_before: int
    revision_after: int
    assignments: tuple[AcceptedAssignment, ...]


@dataclass(frozen=True)
class AttemptAccounting:
    """Validated projection derived from completed attempt facts."""

    provider_calls: int = 0
    completed_rounds: int = 0
    filled_slots: int = 0
    distinct_attempts: int = 0


def derive_attempt_accounting(
    attempts: tuple[ReviewAttempt, ...],
) -> AttemptAccounting:
    """Compute call, accepted-round and filled-slot counts from attempt facts."""
    accepted = tuple(
        row
        for row in attempts
        if isinstance(row.outcome, CanonicalReview) or row.interpretation is not None
    )
    return AttemptAccounting(
        provider_calls=len(attempts),
        completed_rounds=len(
            {(row.assignment_id, row.round_number) for row in accepted}
        ),
        filled_slots=len(
            {
                (row.assignment_id, row.round_number, row.reviewer_slot)
                for row in accepted
            }
        ),
        distinct_attempts=len({row.attempt_id for row in attempts}),
    )


_ARTIFACT_ROLES = {
    "canonical",
    "capture",
    "evidence",
    "derived",
    "temporary",
    "log",
}
RESERVED_AUTHORED_ARTIFACT_PATHS = frozenset({"state.yaml", "plan.md", "brief.md"})


def _document_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def _document_sha(value: Any, label: str) -> str:
    text = _document_text(value, label)
    if re.fullmatch(r"[a-f0-9]{64}", text) is None:
        raise ValueError(f"{label} must be a SHA-256 identity")
    return text


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} requires exactly fields {sorted(keys)}")
    return cast(dict[str, Any], value)


def _artifact_document(reference: ArtifactRef) -> dict[str, Any]:
    _validate_artifact(reference)
    return {
        "path": reference.path,
        "sha256": reference.sha256,
        "role": reference.role,
        "kind": reference.kind,
        "mode": reference.mode,
    }


def _parse_artifact(value: Any) -> ArtifactRef:
    row = _exact_object(value, {"path", "sha256", "role", "kind", "mode"}, "artifact")
    mode = row["mode"]
    if mode is not None and (type(mode) is not int or not 0 <= mode <= 0o7777):
        raise ValueError("artifact mode must be a filesystem permission integer")
    reference = ArtifactRef(
        path=_document_text(row["path"], "artifact path"),
        sha256=_document_sha(row["sha256"], "artifact identity"),
        role=cast(ArtifactRole, _document_text(row["role"], "artifact role")),
        kind=cast(Literal["file"], row["kind"]),
        mode=mode,
    )
    _validate_artifact(reference)
    return reference


def _validate_artifact(reference: ArtifactRef) -> None:
    path = PurePosixPath(_document_text(reference.path, "artifact path"))
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != reference.path
        or reference.path in {"", "."}
    ):
        raise ValueError("artifact path must be a contained normalized relative path")
    _document_sha(reference.sha256, "artifact identity")
    if reference.role not in _ARTIFACT_ROLES:
        raise ValueError(f"artifact role must be one of {sorted(_ARTIFACT_ROLES)}")
    if (
        reference.role in {"derived", "temporary"}
        and reference.path in RESERVED_AUTHORED_ARTIFACT_PATHS
    ):
        raise ValueError(
            "artifact role cannot make reserved authored path disposable: "
            f"{reference.path}"
        )
    if reference.kind != "file":
        raise ValueError("artifact kind must be file")
    if reference.mode is not None and (
        type(reference.mode) is not int or not 0 <= reference.mode <= 0o7777
    ):
        raise ValueError("artifact mode must be a filesystem permission integer")


def _invocation_document(invocation: AttemptInvocation) -> dict[str, str]:
    for label, value in (
        ("cli", invocation.cli),
        ("model", invocation.model),
        ("reasoning effort", invocation.reasoning_effort),
        ("sandbox", invocation.sandbox),
        ("prompt version", invocation.prompt_version),
    ):
        _document_text(value, label)
    for label, value in (
        ("input hash", invocation.input_hash),
        ("review basis hash", invocation.review_basis_hash),
        ("effective prompt hash", invocation.effective_prompt_sha256),
    ):
        _document_sha(value, label)
    return {
        "cli": invocation.cli,
        "model": invocation.model,
        "reasoning_effort": invocation.reasoning_effort,
        "sandbox": invocation.sandbox,
        "input_hash": invocation.input_hash,
        "review_basis_hash": invocation.review_basis_hash,
        "prompt_version": invocation.prompt_version,
        "effective_prompt_sha256": invocation.effective_prompt_sha256,
    }


def _parse_invocation(value: Any) -> AttemptInvocation:
    keys = {
        "cli",
        "model",
        "reasoning_effort",
        "sandbox",
        "input_hash",
        "review_basis_hash",
        "prompt_version",
        "effective_prompt_sha256",
    }
    row = _exact_object(value, keys, "attempt invocation")
    invocation = AttemptInvocation(
        **{key: _document_text(row[key], key) for key in keys}
    )
    _invocation_document(invocation)
    return invocation


def _source_document(source: AssignmentSource) -> dict[str, Any]:
    if type(source.round_number) is not int or source.round_number < 1:
        raise ValueError("attempt source round must be a positive integer")
    if source.reviewer_slot not in {"primary", "secondary"}:
        raise ValueError("attempt source reviewer slot is unsupported")
    if type(source.new_important) is not int or source.new_important < 0:
        raise ValueError("attempt source new-important count must be nonnegative")
    if type(source.coverage_required) is not bool:
        raise ValueError("attempt source coverage flag must be boolean")
    finding_ids: set[str] = set()
    findings = []
    for finding in source.findings:
        identifier = _document_text(finding.finding_id, "source finding id")
        if identifier == "@coverage" or identifier in finding_ids:
            raise ValueError("attempt source finding id is reserved or duplicated")
        finding_ids.add(identifier)
        if finding.severity not in {"critical", "important", "minor"}:
            raise ValueError("attempt source finding severity is unsupported")
        if finding.classification not in {"implement", "report", "ignore", "unknown"}:
            raise ValueError("attempt source finding classification is unsupported")
        findings.append(
            {
                "finding_id": identifier,
                "severity": finding.severity,
                "classification": finding.classification,
            }
        )
    if source.new_important > sum(
        row["severity"] == "important" and row["classification"] == "implement"
        for row in findings
    ):
        raise ValueError("attempt source new-important count exceeds its findings")
    return {
        "assignment_id": _document_text(source.assignment_id, "source assignment id"),
        "run_id": _document_text(source.run_id, "source run id"),
        "round_number": source.round_number,
        "reviewer_slot": source.reviewer_slot,
        "findings": findings,
        "new_important": source.new_important,
        "coverage_required": source.coverage_required,
    }


def _parse_source(value: Any) -> AssignmentSource:
    row = _exact_object(
        value,
        {
            "assignment_id",
            "run_id",
            "round_number",
            "reviewer_slot",
            "findings",
            "new_important",
            "coverage_required",
        },
        "attempt source",
    )
    if not isinstance(row["findings"], list):
        raise ValueError("attempt source findings must be a list")
    source = AssignmentSource(
        assignment_id=_document_text(row["assignment_id"], "source assignment id"),
        run_id=_document_text(row["run_id"], "source run id"),
        round_number=row["round_number"],
        reviewer_slot=_document_text(row["reviewer_slot"], "source reviewer slot"),
        findings=tuple(
            FindingFact(
                finding_id=_document_text(item.get("finding_id"), "source finding id"),
                severity=_document_text(
                    item.get("severity"), "source finding severity"
                ),
                classification=_document_text(
                    item.get("classification"), "source finding classification"
                ),
            )
            for item in row["findings"]
            if isinstance(item, dict)
            and set(item) == {"finding_id", "severity", "classification"}
        ),
        new_important=row["new_important"],
        coverage_required=row["coverage_required"],
    )
    if len(source.findings) != len(row["findings"]):
        raise ValueError("attempt source finding has an invalid shape")
    _source_document(source)
    return source


def _outcome_document(outcome: ReviewOutcome) -> dict[str, Any]:
    if isinstance(outcome, EngineFailure):
        return {
            "kind": outcome.kind,
            "reason": _document_text(outcome.reason, "failure reason"),
            "detail": _document_text(outcome.detail, "failure detail"),
        }
    if isinstance(outcome, UsableReviewCapture):
        if outcome.capture.role != "capture":
            raise ValueError("usable-capture outcome requires a capture artifact")
        return {
            "kind": outcome.kind,
            "capture": _artifact_document(outcome.capture),
            "warnings": [
                _document_text(warning, "capture warning")
                for warning in outcome.warnings
            ],
        }
    if isinstance(outcome, CanonicalReview):
        if outcome.result.role != "canonical":
            raise ValueError("canonical-review outcome requires a canonical artifact")
        return {"kind": outcome.kind, "result": _artifact_document(outcome.result)}
    raise ValueError("attempt outcome has an unsupported variant")


def _parse_outcome(value: Any) -> ReviewOutcome:
    if not isinstance(value, dict):
        raise ValueError("outcome must be an object")
    kind = value.get("kind")
    if kind == EngineFailure.kind:
        row = _exact_object(value, {"kind", "reason", "detail"}, "outcome")
        return EngineFailure(
            _document_text(row["reason"], "failure reason"),
            _document_text(row["detail"], "failure detail"),
        )
    if kind == UsableReviewCapture.kind:
        row = _exact_object(value, {"kind", "capture", "warnings"}, "outcome")
        if not isinstance(row["warnings"], list):
            raise ValueError("outcome warnings must be a list")
        return UsableReviewCapture(
            _parse_artifact(row["capture"]),
            tuple(_document_text(item, "capture warning") for item in row["warnings"]),
        )
    if kind == CanonicalReview.kind:
        row = _exact_object(value, {"kind", "result"}, "outcome")
        return CanonicalReview(_parse_artifact(row["result"]))
    raise ValueError("outcome has an unsupported kind")


def _interpretation_document(value: LeadInterpretation) -> dict[str, Any]:
    if value.result.role != "canonical":
        raise ValueError("interpretation requires a canonical result artifact")
    return {
        "author": _document_text(value.author, "interpretation author"),
        "reason": _document_text(value.reason, "interpretation reason"),
        "result": _artifact_document(value.result),
        "at": value.at,
    }


def _parse_interpretation(value: Any) -> LeadInterpretation:
    row = _exact_object(value, {"author", "reason", "result", "at"}, "interpretation")
    at = row["at"]
    if at is not None:
        at = _document_text(at, "interpretation time")
    return LeadInterpretation(
        _document_text(row["author"], "interpretation author"),
        _document_text(row["reason"], "interpretation reason"),
        _parse_artifact(row["result"]),
        at,
    )


def review_attempt_document(attempt: ReviewAttempt) -> dict[str, Any]:
    """Encode one exact attempt fact without optional outcome combinations."""
    try:
        UUID(attempt.attempt_id)
    except (ValueError, AttributeError) as error:
        raise ValueError("attempt id must be a UUID") from error
    if type(attempt.round_number) is not int or attempt.round_number < 1:
        raise ValueError("attempt round must be a positive integer")
    if attempt.reviewer_slot not in {"primary", "secondary"}:
        raise ValueError("attempt reviewer slot is unsupported")
    by_path: dict[str, ArtifactRef] = {}
    for reference in attempt.artifacts:
        _validate_artifact(reference)
        previous = by_path.get(reference.path)
        if previous is not None and previous != reference:
            raise ValueError(f"attempt artifact identity conflict: {reference.path}")
        by_path[reference.path] = reference
    return {
        "attempt_id": attempt.attempt_id,
        "assignment_id": _document_text(attempt.assignment_id, "assignment id"),
        "round_number": attempt.round_number,
        "reviewer_slot": attempt.reviewer_slot,
        "invocation": _invocation_document(attempt.invocation),
        "outcome": _outcome_document(attempt.outcome),
        "created_at": _document_text(attempt.created_at, "attempt time"),
        "artifacts": [_artifact_document(item) for item in attempt.artifacts],
        "interpretation": (
            _interpretation_document(attempt.interpretation)
            if attempt.interpretation is not None
            else None
        ),
        "source": (
            _source_document(attempt.source) if attempt.source is not None else None
        ),
    }


def parse_review_attempt(value: Any) -> ReviewAttempt:
    row = _exact_object(
        value,
        {
            "attempt_id",
            "assignment_id",
            "round_number",
            "reviewer_slot",
            "invocation",
            "outcome",
            "created_at",
            "artifacts",
            "interpretation",
            "source",
        },
        "review attempt",
    )
    attempt = ReviewAttempt(
        attempt_id=_document_text(row["attempt_id"], "attempt id"),
        assignment_id=_document_text(row["assignment_id"], "assignment id"),
        round_number=row["round_number"],
        reviewer_slot=_document_text(row["reviewer_slot"], "reviewer slot"),
        invocation=_parse_invocation(row["invocation"]),
        outcome=_parse_outcome(row["outcome"]),
        created_at=_document_text(row["created_at"], "attempt time"),
        artifacts=tuple(
            _parse_artifact(item) for item in _artifact_rows(row["artifacts"])
        ),
        interpretation=(
            _parse_interpretation(row["interpretation"])
            if row["interpretation"] is not None
            else None
        ),
        source=_parse_source(row["source"]) if row["source"] is not None else None,
    )
    review_attempt_document(attempt)
    if attempt.interpretation is not None and not isinstance(
        attempt.outcome, UsableReviewCapture
    ):
        raise ValueError("interpretation requires a usable-capture outcome")
    return attempt


def _artifact_rows(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("attempt artifacts must be a list")
    return value


@dataclass(frozen=True)
class ReviewAssignments:
    schema: str = SCHEMA
    assignments: tuple[ReviewAssignment, ...] = ()
    attempts: tuple[ReviewAttempt, ...] = ()
    dispositions: tuple[AssignmentDisposition, ...] = ()
    acceptances: tuple[BoundaryAcceptance, ...] = ()

    @property
    def sources(self) -> tuple[AssignmentSource, ...]:
        return tuple(
            attempt.source for attempt in self.attempts if attempt.source is not None
        )

    @property
    def retained(self) -> tuple[RetainedReview, ...]:
        return tuple(
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
            for attempt in self.attempts
            if isinstance(attempt.outcome, UsableReviewCapture)
        )

    @property
    def interpretations(self) -> tuple[ReviewInterpretation, ...]:
        return tuple(
            ReviewInterpretation(
                attempt.attempt_id,
                attempt.outcome.capture.sha256,
                attempt.interpretation.result.sha256,
                attempt.interpretation.author,
                attempt.interpretation.reason,
                attempt.interpretation.at or attempt.created_at,
            )
            for attempt in self.attempts
            if isinstance(attempt.outcome, UsableReviewCapture)
            and attempt.interpretation is not None
        )

    @property
    def accounting(self) -> AttemptAccounting:
        return derive_attempt_accounting(self.attempts)
