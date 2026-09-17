"""Decision inputs and closed resolution values; no workflow effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

DecisionKind = Literal[
    "finding",
    "question",
    "stage-cap",
    "session-cap",
    "smoke-disposition",
    "policy",
]
ResolutionKind = Literal[
    "disposition",
    "continue-review",
    "continue-stage",
    "continue-session",
    "accept-degraded-smoke",
    "policy",
]


@dataclass(frozen=True)
class Disposition:
    kind: Literal["disposition"] = field(default="disposition", init=False)


@dataclass(frozen=True)
class ContinueReview:
    assignment_id: str
    stopped_round: int
    kind: Literal["continue-review"] = field(default="continue-review", init=False)


@dataclass(frozen=True)
class ContinueStage:
    review_run_count_before: int
    gate_run_count_before: int
    kind: Literal["continue-stage"] = field(default="continue-stage", init=False)


@dataclass(frozen=True)
class ContinueSession:
    session_count_before: int
    kind: Literal["continue-session"] = field(default="continue-session", init=False)


@dataclass(frozen=True)
class AcceptDegradedSmoke:
    kind: Literal["accept-degraded-smoke"] = field(
        default="accept-degraded-smoke", init=False
    )


@dataclass(frozen=True)
class PolicyResolution:
    choice: str
    basis: str
    reversibility: str
    confidence: str
    review_flag: str
    scope: str
    kind: Literal["policy"] = field(default="policy", init=False)


type Resolution = (
    Disposition
    | ContinueReview
    | ContinueStage
    | ContinueSession
    | AcceptDegradedSmoke
    | PolicyResolution
)


@dataclass(frozen=True)
class PolicyResolutionInput:
    id: str
    title: str
    question: str
    options: tuple[str, ...]
    choice: str
    rationale: str
    basis: str
    reversibility: str
    confidence: str
    scope: str
    routes_to: tuple[str, ...]
    review_flag: str = ""


@dataclass(frozen=True)
class DecisionInput:
    kind: DecisionKind
    escalation_class: int
    source: str
    title: str
    question: str
    options: tuple[str, ...]
    routes_to: tuple[str, ...]
    recommendation: str | None = None
    target_stage: str | None = None
    target_blocker: str | None = None
    smoke_disposition: Mapping[str, object] | None = None


def resolution_payload(resolution: Resolution) -> dict[str, object]:
    """Return the canonical state payload, independent of authored rationale."""
    return asdict(resolution)
