"""Accepted gate-event allocation and atomic append through the state store."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from heddle.contracts import operations as ops
from heddle.contracts.review_assignments import (
    ArtifactRef,
    ArtifactRole,
    AssignmentSource,
    AttemptInvocation,
    CanonicalReview,
    EngineFailure,
    FindingFact,
    LeadInterpretation,
    ReviewAssignments,
    ReviewAttempt,
    UsableReviewCapture,
)
from heddle.kernel.project_config import KernelError
from heddle.kernel.recording import (
    record_attempt,
    record_attempt_interpretation,
    record_run,
    replay_run,
)
from heddle.runtime.state_store import append_state

if TYPE_CHECKING:
    from heddle.gate import entry


@dataclass(frozen=True)
class GateRecording:
    run_id: str
    decision_ids: tuple[str, ...]
    revision: int
    replay: bool


@dataclass(frozen=True)
class _AttemptPublication:
    revision: int
    wrote: bool
    replay: bool


def _publish_review_attempt(
    state_path: Path, *, attempt: ReviewAttempt
) -> _AttemptPublication:
    """Exercise the private attempt-ledger transaction used by focused tests."""
    replay = False

    def transform(document: dict[str, Any]) -> dict[str, Any]:
        nonlocal replay
        ledger = document.get("review_assignments")
        if ledger is None:
            ledger = ops.decoded_payload(ReviewAssignments())
        if not isinstance(ledger, dict):
            raise ValueError("review assignment ledger must be an object")
        updated_ledger, replay = record_attempt(ledger, attempt)
        if updated_ledger is ledger:
            return document
        updated = dict(document)
        updated["review_assignments"] = updated_ledger
        return updated

    committed = append_state(state_path, transform=transform)
    return _AttemptPublication(committed.revision, committed.wrote, replay)


def allocate_decision_ids(document: dict[str, Any], count: int) -> tuple[str, ...]:
    """Numeric suffixes are allocation only; callers hold the transaction lock."""
    highest = max(
        (
            int(match.group(1))
            for decision in document["decisions"]
            if (match := re.fullmatch(r"D([0-9]+)", decision["id"])) is not None
        ),
        default=0,
    )
    return tuple(f"D{number}" for number in range(highest + 1, highest + count + 1))


def record_gate_run(
    state_path: Path,
    *,
    gate: str,
    scope: str,
    run_fact: dict[str, Any],
    decisions: list[dict[str, Any]],
    expected_revision: int | None = None,
    interpretation: dict[str, Any] | None = None,
    artifacts: tuple[ArtifactRef, ...] = (),
) -> GateRecording:
    """Supply candidate IDs; the kernel retains the accepted identity on replay."""
    identity: tuple[str, tuple[str, ...], bool] | None = None

    def transform(document: dict[str, Any]) -> dict[str, Any]:
        nonlocal identity
        if expected_revision is not None and document["revision"] != expected_revision:
            raise KernelError(
                code="workspace-invalid",
                message="workflow state changed while the review was executing",
                hint=(
                    "prepare the review again against the current workflow state; "
                    "this result did not acquire authority"
                ),
            )
        from heddle.kernel.state import parse_state_document

        state = parse_state_document(document, source=state_path)
        existing = (
            replay_run(state, gate, scope, str(run_fact["input_hash"]))
            if interpretation is None and run_fact["verdict"].get("status") != "error"
            else None
        )
        run_id = (
            str(interpretation["run_id"])
            if interpretation is not None
            else existing.run_id
            if existing is not None
            else str(uuid4())
        )
        assignment_source = None
        if run_fact["verdict"].get("status") != "error":
            from heddle.gate import entry
            from heddle.kernel.state import parse_gate_fact
            from heddle.runtime.review_assignments import source_projection

            incoming = parse_gate_fact(
                state_path,
                {
                    "gate": gate,
                    "scope": scope,
                    "runs": [{**run_fact, "run_id": run_id}],
                },
            ).runs[0]
            _, content = entry.read_contained_utf8_artifact(
                state_path.parent, relative_path=incoming.artifact
            )
            if entry.artifact_identity(content) != incoming.artifact_sha256:
                raise KernelError(
                    code="workspace-invalid",
                    message="canonical artifact identity differs",
                    hint="restore the accepted canonical bytes before recording",
                )
            result = entry.retained_review_result(
                content, incoming, feature=document["feature"], gate=gate, scope=scope
            )
            if interpretation is not None:
                from heddle.kernel.review_assignments import retained_responses

                capture_row = next(
                    (r for r in retained_responses(state) if r.run_id == run_id),
                    None,
                )
                if capture_row is None:
                    raise KernelError(
                        code="workspace-invalid",
                        message="interpretation has no retained response",
                        hint="use the recorded capture identity",
                    )
                captured = entry.read_retained_capture(state_path.parent, capture_row)
                if result.invocation != captured.invocation:
                    raise KernelError(
                        code="workspace-invalid",
                        message="interpretation changed the original invocation",
                        hint="preserve the captured reviewer and inputs",
                    )
            assignment_source = source_projection(
                result,
                incoming.assignment_id,
                incoming.round_number,
                incoming.reviewer_slot,
            )
        identifiers = allocate_decision_ids(document, len(decisions))
        allocated = []
        for identifier, decision in zip(identifiers, decisions, strict=True):
            item = {**decision, "id": identifier}
            if item["kind"] == "finding":
                item["origin_run_id"] = run_id
            allocated.append(item)
        attempt_ledger = None
        if run_fact.get("assignment_id") is not None:
            invocation = _attempt_invocation(run_fact)
            ledger = document.get("review_assignments")
            if ledger is None:
                ledger = ops.decoded_payload(ReviewAssignments())
            if not isinstance(ledger, dict):
                raise ValueError("review assignment ledger must be an object")
            if interpretation is None:
                outcome = (
                    EngineFailure(
                        reason=str(
                            run_fact.get("failure_reason") or "execution-failure"
                        ),
                        detail=str(run_fact.get("failure_reason") or "provider failed"),
                    )
                    if run_fact["verdict"].get("status") == "error"
                    else CanonicalReview(
                        _primary_artifact(
                            artifacts,
                            path=str(run_fact["artifact"]),
                            sha256=str(run_fact["artifact_sha256"]),
                            role="canonical",
                        )
                    )
                )
                attempt = ReviewAttempt(
                    attempt_id=run_id,
                    assignment_id=str(run_fact["assignment_id"]),
                    round_number=int(run_fact["round_number"]),
                    reviewer_slot=str(run_fact["reviewer_slot"]),
                    invocation=invocation,
                    outcome=outcome,
                    created_at=str(run_fact["at"]),
                    artifacts=artifacts,
                    source=(
                        _attempt_source(run_id, assignment_source)
                        if assignment_source is not None
                        else None
                    ),
                )
                updated_ledger, _ = record_attempt(ledger, attempt)
            else:
                result_sha256 = interpretation["result_sha256"]
                if not isinstance(result_sha256, str):
                    raise ValueError(
                        "interpretation result requires a SHA-256 identity"
                    )
                lead = LeadInterpretation(
                    author=str(interpretation["author"]),
                    reason=str(interpretation["reason"]),
                    result=_primary_artifact(
                        artifacts,
                        path=str(run_fact["artifact"]),
                        sha256=result_sha256,
                        role="canonical",
                    ),
                    at=str(interpretation["at"]),
                )
                updated_ledger, _ = record_attempt_interpretation(
                    ledger,
                    attempt_id=run_id,
                    interpretation=lead,
                    source=(
                        _attempt_source(run_id, assignment_source)
                        if assignment_source is not None
                        else None
                    ),
                )
            if updated_ledger is not ledger:
                attempt_ledger = updated_ledger
        recorded = record_run(
            document,
            gate=gate,
            scope=scope,
            run_fact={**run_fact, "run_id": run_id},
            decisions=allocated,
            assignment_source=assignment_source,
            interpretation=interpretation,
            attempt_ledger=attempt_ledger,
        )
        identity = (recorded.run_id, recorded.decision_ids, recorded.replay)
        return recorded.document

    committed = append_state(state_path, transform=transform)
    assert identity is not None
    return GateRecording(*identity[:2], committed.revision, identity[2])


def record_retained_review(
    state_path: Path, *, outcome: entry.GateOutcome, expected_revision: int
) -> GateRecording:
    """Allocate and record a durable completed response without accepted findings."""
    from heddle.contracts.review_assignments import RetainedReview
    from heddle.gate import entry
    from heddle.runtime.clock import utc_now_minutes

    identity: tuple[str, tuple[str, ...], bool] | None = None

    def transform(document: dict[str, Any]) -> dict[str, Any]:
        nonlocal identity
        raw_ledger = document.get("review_assignments")
        if raw_ledger is None:
            raw_ledger = ops.decoded_payload(ReviewAssignments())
        if not isinstance(raw_ledger, dict):
            raise ValueError("review assignment ledger must be an object")
        for existing in raw_ledger.get("attempts", []):
            recorded_outcome = existing.get("outcome", {})
            capture = recorded_outcome.get("capture")
            if (
                isinstance(capture, dict)
                and capture.get("path") == outcome.artifact_relpath
            ):
                if capture.get("sha256") != outcome.artifact_sha256:
                    raise ValueError("retained response identity conflict")
                identity = (str(existing["attempt_id"]), (), True)
                return document
        if document["revision"] != expected_revision:
            raise KernelError(
                code="workspace-invalid",
                message="state changed while the reviewer ran",
                hint="preserve the unaccepted capture and inspect the changed state",
            )
        captured = cast("entry.CapturedReview", outcome.retained_review)
        invocation = captured.invocation
        row = RetainedReview(
            str(uuid4()),
            cast(str, invocation.assignment_id),
            cast(int, invocation.round_number),
            cast(str, invocation.reviewer_slot),
            outcome.artifact_relpath,
            cast(str, outcome.artifact_sha256),
            invocation.input_hash,
            invocation.review_basis_hash,
            utc_now_minutes(),
            captured.warnings,
        )
        observed = entry.read_retained_capture(state_path.parent, row)
        if (
            observed != outcome.retained_review
            or observed.invocation.feature != document["feature"]
        ):
            raise KernelError(
                code="workspace-invalid",
                message="retained capture readback differs",
                hint="preserve the original completed response",
            )
        attempt = ReviewAttempt(
            attempt_id=row.run_id,
            assignment_id=row.assignment_id,
            round_number=row.round_number,
            reviewer_slot=row.reviewer_slot,
            invocation=_attempt_invocation(
                {
                    "execution_config": {
                        "cli": invocation.execution.cli,
                        "model": invocation.execution.model,
                        "reasoning_effort": invocation.execution.reasoning_effort,
                        "sandbox": invocation.execution.sandbox,
                    },
                    "input_hash": invocation.input_hash,
                    "review_basis_hash": invocation.review_basis_hash,
                    "prompt_version": invocation.prompt_version,
                    "effective_prompt_sha256": invocation.effective_prompt_sha256,
                }
            ),
            outcome=UsableReviewCapture(
                _primary_artifact(
                    outcome.artifacts,
                    path=row.artifact,
                    sha256=row.artifact_sha256,
                    role="capture",
                ),
                row.warnings,
            ),
            created_at=row.at,
            artifacts=outcome.artifacts,
        )
        from heddle.kernel.review_assignments import assignment_for, launch_round
        from heddle.kernel.state import parse_state_document

        state = parse_state_document(document, source=state_path)
        assignment = assignment_for(state, invocation.gate, invocation.scope)
        current_round = launch_round(state, assignment)
        if (
            assignment.id != row.assignment_id
            or current_round.number != row.round_number
        ):
            raise KernelError(
                code="workspace-invalid",
                message="retained response no longer matches its assignment round",
                hint="preserve the capture and inspect current review ownership",
            )
        # Preserve the explicit union discriminators in the already validated
        # wire ledger. dataclasses.asdict() omits ClassVar ``kind`` tags from
        # existing attempt outcomes.
        prepared_ledger = deepcopy(raw_ledger)
        stored = next(
            (
                candidate
                for candidate in prepared_ledger["assignments"]
                if candidate["id"] == assignment.id
            ),
            None,
        )
        if stored is None:
            stored = ops.decoded_payload(assignment)
            prepared_ledger["assignments"].append(stored)
        if not any(
            existing["number"] == current_round.number for existing in stored["rounds"]
        ):
            stored["rounds"].append(ops.decoded_payload(current_round))
        ledger, replay = record_attempt(prepared_ledger, attempt)
        updated = dict(document)
        updated["review_assignments"] = ledger
        parse_state_document(updated, source=state_path)
        identity = (row.run_id, (), replay)
        return updated

    committed = append_state(state_path, transform=transform)
    assert identity is not None
    return GateRecording(*identity[:2], committed.revision, identity[2])


def _attempt_invocation(run_fact: dict[str, Any]) -> AttemptInvocation:
    execution = run_fact.get("execution_config")
    if not isinstance(execution, dict):
        raise ValueError("completed attempt requires execution configuration")
    return AttemptInvocation(
        cli=str(execution["cli"]),
        model=str(execution["model"]),
        reasoning_effort=str(execution["reasoning_effort"]),
        sandbox=str(execution["sandbox"]),
        input_hash=str(run_fact["input_hash"]),
        review_basis_hash=str(run_fact["review_basis_hash"]),
        prompt_version=str(run_fact["prompt_version"]),
        effective_prompt_sha256=str(run_fact["effective_prompt_sha256"]),
    )


def _attempt_source(run_id: str, value: dict[str, Any]) -> AssignmentSource:
    findings = value.get("findings")
    if not isinstance(findings, list):
        raise ValueError("canonical attempt source requires finding membership")
    return AssignmentSource(
        assignment_id=str(value["assignment_id"]),
        run_id=run_id,
        round_number=int(value["round_number"]),
        reviewer_slot=str(value["reviewer_slot"]),
        findings=tuple(
            FindingFact(
                finding_id=str(item["finding_id"]),
                severity=str(item["severity"]),
                classification=str(item["classification"]),
            )
            for item in findings
        ),
        new_important=int(value["new_important"]),
        coverage_required=bool(value["coverage_required"]),
    )


def _primary_artifact(
    artifacts: tuple[ArtifactRef, ...],
    *,
    path: str,
    sha256: str,
    role: ArtifactRole,
) -> ArtifactRef:
    matching = next((item for item in artifacts if item.path == path), None)
    if matching is not None:
        if matching.sha256 != sha256 or matching.role != role:
            raise ValueError("primary review artifact differs from its inventory")
        return matching
    return ArtifactRef(path=path, sha256=sha256, role=role)
