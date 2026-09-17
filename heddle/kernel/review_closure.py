"""Derive closure from qualified originals and facts; never from a last verdict."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from heddle.contracts.feature_policy import (
    ITERATIVE_DOCUMENT_ROLES,
    ReviewClosure,
    ReviewClosureFacts,
    ReviewObligation,
    ReviewResolution,
)
from heddle.contracts.review_assignments import ReviewAttempt
from heddle.kernel.feature_policy import validate_gate_policy


@dataclass(frozen=True)
class AttemptObligation:
    attempt_id: str
    finding_id: str
    severity: str
    classification: str
    requires_inspection: bool


def derive_assignment_obligations(
    attempts: tuple[ReviewAttempt, ...],
    *,
    assignment_id: str,
    canonical_reviews: dict[str, dict[str, Any]],
    dispositions: tuple[Any, ...],
    role_enabled: bool,
) -> tuple[AttemptObligation, ...]:
    """Keep original findings and coverage duties local to their attempt owner."""
    del dispositions, role_enabled
    obligations: list[AttemptObligation] = []
    for attempt in attempts:
        if attempt.assignment_id != assignment_id:
            continue
        review = canonical_reviews.get(attempt.attempt_id)
        if review is None:
            continue
        findings = review.get("findings", [])
        if not isinstance(findings, list):
            raise ValueError("canonical review findings must be a list")
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("canonical review finding must be an object")
            obligations.append(
                AttemptObligation(
                    attempt.attempt_id,
                    str(finding["id"]),
                    str(finding["severity"]),
                    str(finding["classification"]),
                    bool(finding.get("requires_inspection", False)),
                )
            )
        if review.get("coverage_required") is True:
            obligations.append(
                AttemptObligation(
                    attempt.attempt_id,
                    "@coverage",
                    "important",
                    "implement",
                    False,
                )
            )
    return tuple(obligations)


def _ref(value: ReviewObligation | ReviewResolution) -> tuple[str, str]:
    return value.run_id, value.finding_id


def _validate(facts: ReviewClosureFacts) -> None:
    validate_gate_policy(facts.policy)
    if type(facts.historical_complete) is not bool:
        raise ValueError("historical completion must be a qualified boolean fact")
    if type(facts.continuation_authorized) is not bool:
        raise ValueError("review continuation must be a qualified boolean fact")
    origins = {_ref(row) for row in facts.obligations}
    if len(origins) != len(facts.obligations) or any(not all(ref) for ref in origins):
        raise ValueError("review obligation origins must be distinct and nonempty")
    for obligation in facts.obligations:
        if obligation.severity not in {"critical", "important", "minor"} or (
            obligation.classification
            not in {"implement", "report", "ignore", "unknown"}
        ):
            raise ValueError("review origin has unknown severity or classification")
    for disposition in facts.resolutions:
        if _ref(disposition) not in origins:
            raise ValueError("review resolution references an unknown origin")
        if disposition.status not in {
            "retained",
            "addressed",
            "awaiting-decision",
            "settled",
        }:
            raise ValueError("review resolution has an unknown disposition")
    for number, row in enumerate(facts.rounds, 1):
        if type(row.number) is not int or row.number != number:
            raise ValueError("review round numbers must be contiguous from one")
        required, accepted = set(row.required_slots), set(row.accepted_slots)
        if (
            not required
            or "primary" not in required
            or not required <= {"primary", "secondary"}
            or len(required) != len(row.required_slots)
            or len(accepted) != len(row.accepted_slots)
            or not accepted <= required
        ):
            raise ValueError("review round slots must be distinct declared slots")
        if number < len(facts.rounds) and required != accepted:
            raise ValueError("a missing slot blocks opening a later review round")
        if row.purpose not in {"discovery", "verification", "independent-pass"}:
            raise ValueError("review round has an unknown purpose")
        if (
            not row.scope_identity
            or type(row.new_important) is not int
            or row.new_important < 0
        ):
            raise ValueError("review round needs scope identity and nonnegative count")
        if not set(row.material_progress) <= set(row.before_open):
            raise ValueError(
                "material progress must reference an original open concern"
            )


def _required(row: ReviewObligation) -> bool:
    return (
        row.finding_id == "@coverage"
        or row.severity == "critical"
        or (row.severity == "important" and row.classification != "ignore")
    )


def _resolved(
    source: ReviewObligation,
    resolution: ReviewResolution | None,
    facts: ReviewClosureFacts,
) -> bool:
    if (
        resolution is None
        or resolution.status not in {"addressed", "settled"}
        or not resolution.references
        or resolution.evidence_basis != facts.current_basis
        or not resolution.reason.strip()
        or source.classification == "unknown"
    ):
        return False
    if source.requires_inspection and resolution.reviewer != source.reviewer:
        return False
    if source.classification == "report":
        return (
            resolution.status == "settled"
            and source.decision_id is not None
            and any(
                decision.id == source.decision_id == resolution.decision_id
                and (decision.run_id, decision.finding_id) == _ref(source)
                and decision.status == "resolved"
                for decision in facts.decisions
            )
        )
    return True


def assess_review_closure(facts: ReviewClosureFacts) -> ReviewClosure:
    _validate(facts)
    latest = {_ref(row): row for row in facts.resolutions}
    open_refs = tuple(
        _ref(row)
        for row in facts.obligations
        if _required(row) and not _resolved(row, latest.get(_ref(row)), facts)
    )
    used = len(facts.rounds)
    calls = sum(len(row.accepted_slots) for row in facts.rounds)
    policy = facts.policy
    remaining = (
        0
        if policy.mode == "off"
        else None
        if policy.limit is None
        else max(0, policy.limit - used)
    )
    missing = (
        tuple(
            slot
            for slot in facts.rounds[-1].required_slots
            if slot not in facts.rounds[-1].accepted_slots
        )
        if facts.rounds
        else ()
    )
    minimum_met = (
        facts.historical_complete
        or policy.mode == "off"
        or used >= policy.minimum_rounds
    )
    closed = not open_refs and not missing and minimum_met
    stop = None
    if closed:
        next_step = "closed"
    elif policy.mode == "off":
        next_step, stop = "disposition", "gate-off"
    elif missing:
        next_step = "run"
    else:
        if policy.limit is not None and used >= policy.limit:
            stop = "round-limit"
        elif len(facts.rounds) >= 2 and not facts.continuation_authorized:
            previous, current = facts.rounds[-2:]
            same_scope = previous.scope_identity == current.scope_identity
            if (
                current.purpose == "verification"
                and same_scope
                and current.before_open
                and set(current.before_open) == set(open_refs)
                and not current.material_progress
            ):
                stop = "no-progress"
            elif (
                policy.role in ITERATIVE_DOCUMENT_ROLES
                and same_scope
                and previous.purpose == current.purpose
                and previous.new_important > 0
                and current.new_important >= previous.new_important
            ):
                stop = "no-decrease"
        next_step = (
            "decision"
            if stop
            else "run"
            if facts.continuation_authorized
            else "disposition"
            if open_refs
            else "run"
        )
    return ReviewClosure(
        closed, open_refs, used, calls, remaining, missing, next_step, stop
    )
