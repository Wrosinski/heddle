"""Confirmed review choices and qualified closure inputs, without effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from heddle.contracts.gates import GATE_CATALOG

POLICY_SCHEMA = "heddle.feature-policy/v1"
INTAKE_INPUT_SCHEMA_ID = "heddle.intake-input/v1"
INTAKE_ROUTES = ("direct", "heddle")
INTAKE_INPUT_FIELDS = ("schema", "route", "route_reason", "research", "axes")
INTAKE_RESEARCH_FIELDS = ("reference", "summary")
TRIGGER_FIELDS = ("gate", "gap", "references")
ROLES = tuple(GATE_CATALOG)
DOCUMENT_ROLES = frozenset({"spec-review", "plan-review"})
ITERATIVE_DOCUMENT_ROLES = DOCUMENT_ROLES | {"review-test-scaffolding"}
SCOPES = frozenset({"small", "medium", "large"})
COMPLEXITIES = frozenset({"low", "high"})
TESTABILITIES = frozenset({"full", "partial", "none"})
MODES = frozenset({"off", "upper-limit", "convergence"})
type OriginalRef = tuple[str, str]


@dataclass(frozen=True)
class FeatureAxes:
    scope: str
    complexity: str
    testability: str
    scope_rationale: str
    complexity_rationale: str
    testability_rationale: str


@dataclass(frozen=True)
class Reviewer:
    cli: str
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class GatePolicy:
    role: str
    scope: str
    mode: str
    limit: int | None
    minimum_rounds: int
    primary: Reviewer
    secondary: Reviewer | None = None
    trigger: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ConfirmedPolicy:
    revision: int
    axes: FeatureAxes
    approval: str
    entries: tuple[GatePolicy, ...]


def _model_fields(model: type[Any]) -> tuple[str, ...]:
    return tuple(field.name for field in fields(model))


FEATURE_AXES_FIELDS = _model_fields(FeatureAxes)
REVIEWER_FIELDS = _model_fields(Reviewer)
GATE_POLICY_FIELDS = _model_fields(GatePolicy)
GATE_POLICY_OPTIONAL_FIELDS = ("secondary", "trigger")
CONFIRMED_POLICY_FIELDS = _model_fields(ConfirmedPolicy)
POLICY_INPUT_FIELDS = ("schema", *CONFIRMED_POLICY_FIELDS)


@dataclass(frozen=True)
class Recommendation:
    base: str
    entries: tuple[GatePolicy, ...]


@dataclass(frozen=True)
class CallBudget:
    feature_minimum: int
    per_milestone_minimum: int
    feature_maximum: int | None
    per_milestone_maximum: int | None

    @staticmethod
    def _validate_milestones(milestones: int) -> None:
        if type(milestones) is not int or milestones < 0:
            raise ValueError("policy milestone count must be an integer >= 0")

    def minimum_for(self, milestones: int) -> int:
        self._validate_milestones(milestones)
        return self.feature_minimum + milestones * self.per_milestone_minimum

    def maximum_for(self, milestones: int) -> int | None:
        self._validate_milestones(milestones)
        if self.feature_maximum is None or (
            milestones and self.per_milestone_maximum is None
        ):
            return None
        return self.feature_maximum + milestones * (self.per_milestone_maximum or 0)


@dataclass(frozen=True)
class EffectivePolicy:
    revision: int
    axes: FeatureAxes
    approval: str
    base: str
    entries: tuple[GatePolicy, ...]
    budget: CallBudget
    final_verification_stage: str


@dataclass(frozen=True)
class ReviewRound:
    number: int
    purpose: str
    scope_identity: str
    required_slots: tuple[str, ...]
    accepted_slots: tuple[str, ...]
    new_important: int
    before_open: tuple[OriginalRef, ...]
    material_progress: tuple[OriginalRef, ...]


@dataclass(frozen=True)
class ReviewObligation:
    run_id: str
    finding_id: str
    severity: str
    classification: str
    reviewer: str
    requires_inspection: bool = False
    decision_id: str | None = None


@dataclass(frozen=True)
class ReviewResolution:
    run_id: str
    finding_id: str
    status: str
    references: tuple[tuple[str, str], ...]
    evidence_basis: str | None
    reason: str
    reviewer: str | None = None
    decision_id: str | None = None


@dataclass(frozen=True)
class ReviewDecision:
    id: str
    run_id: str
    finding_id: str
    status: str


@dataclass(frozen=True)
class ReviewClosureFacts:
    policy: GatePolicy
    rounds: tuple[ReviewRound, ...]
    obligations: tuple[ReviewObligation, ...]
    resolutions: tuple[ReviewResolution, ...]
    decisions: tuple[ReviewDecision, ...]
    current_basis: str
    historical_complete: bool = False
    continuation_authorized: bool = False


@dataclass(frozen=True)
class ReviewClosure:
    closed: bool
    open_refs: tuple[OriginalRef, ...]
    rounds_used: int
    calls_completed: int
    rounds_remaining: int | None
    missing_slots: tuple[str, ...]
    next_step: str
    stop_reason: str | None
