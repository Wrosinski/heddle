"""
`milestones_landed_since` — the deferred-batch staleness derivation.

Post-completion friction retrospective (review): a code-quality-core recommendation
was obsolete by resolution because gate/write had landed under the decision deferral
deferral.
The derivation names milestones done after a pending decision was raised,
read-time only (workflow-model design principle: derived, never stored),
using each done milestone's EARLIEST passing milestone-scoped verification
fact as the landing proxy (the `milestone advance` precondition).
"""

from __future__ import annotations

import pytest

from heddle.kernel.model import milestones_landed_since
from heddle.kernel.source_manifest import EvidenceReference
from heddle.kernel.state import (
    DecisionFact,
    Milestone,
    StateFile,
    VerificationEvidence,
    VerificationFact,
)


def _milestone(mid: str, status: str = "done") -> Milestone:
    return Milestone(
        id=mid,
        title=f"Milestone {mid}",
        satisfies=("AC-1",),
        depends_on=(),
        owns=(f"src/{mid}",),
        complexity="low",
        estimated_hours=(1, 2),
        verification={"command": "pytest -q", "expected": "all tests pass"},
        status=status,
        tasks=(),
    )


def _verification(scope: str, at: str, exit_code: int = 0) -> VerificationFact:
    reference = EvidenceReference(
        schema="heddle.source-evidence-ref/v1",
        artifact="verification/source-evidence." + "0" * 64 + ".yaml",
        artifact_sha256="0" * 64,
        source_sha256="0" * 64,
    )
    return VerificationFact(
        scope=scope,
        command="pytest -q",
        exit_code=exit_code,
        at=at,
        log=f"verification/{scope}.log",
        evidence=VerificationEvidence(
            schema="heddle.verification-evidence/v1",
            before=reference,
            after=reference,
        ),
    )


def _decision(
    created_at: str = "2026-07-14T10:00Z", status: str = "pending"
) -> DecisionFact:
    return DecisionFact(
        id="code-quality#1",
        kind="question",
        rationale=None,
        source="code-quality#1",
        title="Extract the seam-probe helper",
        status=status,
        resolution=None,
        routes_to=(),
        created_at=created_at,
        resolved_at=None,
    )


def _state(
    milestones: tuple[Milestone, ...],
    verifications: tuple[VerificationFact, ...],
) -> StateFile:
    return StateFile(
        schema="heddle.state/v9",
        completion=None,
        feature="staleness-fixture",
        spec="docs/features/driver/staleness-fixture.md",
        revision=1,
        created="2026-07-14T00:00Z",
        updated="2026-07-14T00:00Z",
        stage="implement",
        authorized_through="implement",
        commands={},
        milestones=milestones,
        gates=(),
        verifications=verifications,
        decisions=(),
        sessions=(),
    )


def test_mixed_precision_cross_minute_counts() -> None:
    state = _state(
        (_milestone("m1"),),
        (_verification("m1", "2026-07-14T10:01:30Z"),),
    )
    assert milestones_landed_since(state, _decision()) == ("m1",)


def test_same_minute_tie_never_counts() -> None:
    # The lexicographic trap: "10:00Z" > "10:00:59Z" as strings ('Z' > '5').
    # Minute-truncated comparison makes the tie unordered, so it never counts.
    state = _state(
        (_milestone("m1"),),
        (_verification("m1", "2026-07-14T10:00:59Z"),),
    )
    assert milestones_landed_since(state, _decision()) == ()


def test_minute_precision_verification_fact_counts() -> None:
    # The golden fixture records verification `at` at minute precision —
    # both sides must tolerate both recorded precisions.
    state = _state(
        (_milestone("m1"),),
        (_verification("m1", "2026-07-14T10:30Z"),),
    )
    assert milestones_landed_since(state, _decision()) == ("m1",)


def test_earliest_passing_rule_ignores_post_done_reverification() -> None:
    # core landed before the decision; a later re-verify must not resurrect it.
    state = _state(
        (_milestone("m1"),),
        (
            _verification("m1", "2026-07-14T09:00:00Z"),
            _verification("m1", "2026-07-14T11:00:00Z"),
        ),
    )
    assert milestones_landed_since(state, _decision()) == ()


def test_failing_verifications_never_count() -> None:
    state = _state(
        (_milestone("m1"),),
        (_verification("m1", "2026-07-14T11:00:00Z", exit_code=1),),
    )
    assert milestones_landed_since(state, _decision()) == ()
    # A pass before + a fail after: the earliest PASSING fact decides.
    state = _state(
        (_milestone("m1"),),
        (
            _verification("m1", "2026-07-14T09:00:00Z"),
            _verification("m1", "2026-07-14T11:00:00Z", exit_code=1),
        ),
    )
    assert milestones_landed_since(state, _decision()) == ()


def test_non_milestone_scopes_never_count() -> None:
    # Feature/smoke-scoped passes are not milestone landings; a done
    # milestone with no scoped fact (hand-edited state) is not counted.
    state = _state(
        (_milestone("m1"),),
        (
            _verification("feature", "2026-07-14T11:00:00Z"),
            _verification("smoke", "2026-07-14T11:00:00Z"),
        ),
    )
    assert milestones_landed_since(state, _decision()) == ()


def test_no_milestones_tier1_shape() -> None:
    state = _state((), (_verification("feature", "2026-07-14T11:00:00Z"),))
    assert milestones_landed_since(state, _decision()) == ()


def test_non_done_milestones_excluded() -> None:
    state = _state(
        (_milestone("m1", status="current"), _milestone("m2", status="todo")),
        (
            _verification("m1", "2026-07-14T11:00:00Z"),
            _verification("m2", "2026-07-14T11:00:00Z"),
        ),
    )
    assert milestones_landed_since(state, _decision()) == ()


def test_resolved_decision_derives_empty() -> None:
    state = _state(
        (_milestone("m1"),),
        (_verification("m1", "2026-07-14T11:00:00Z"),),
    )
    assert milestones_landed_since(state, _decision(status="resolved")) == ()


def test_decision_created_after_every_landing() -> None:
    state = _state(
        (_milestone("m1"),),
        (_verification("m1", "2026-07-14T09:00:00Z"),),
    )
    late = _decision(created_at="2026-07-14T12:00Z")
    assert milestones_landed_since(state, late) == ()


@pytest.mark.parametrize("created_at", ["not-a-time", "2026-07-14T10:00", ""])
def test_malformed_or_naive_created_at_derives_empty(created_at: str) -> None:
    # fromisoformat parses Z-less strings as NAIVE datetimes — they must be
    # rejected, not compared (aware/naive comparison raises TypeError).
    state = _state(
        (_milestone("m1"),),
        (_verification("m1", "2026-07-14T11:00:00Z"),),
    )
    assert milestones_landed_since(state, _decision(created_at=created_at)) == ()


def test_one_malformed_fact_excludes_the_milestone() -> None:
    # Fail-closed: skipping garbage could promote a later fact into a false
    # earliest, so any unparseable passing fact excludes the milestone.
    state = _state(
        (_milestone("m1"), _milestone("m2")),
        (
            _verification("m1", "garbage"),
            _verification("m1", "2026-07-14T11:00:00Z"),
            _verification("m2", "2026-07-14T11:00:00Z"),
        ),
    )
    assert milestones_landed_since(state, _decision()) == ("m2",)


def test_multiple_landings_render_in_plan_order() -> None:
    state = _state(
        (
            _milestone("m1"),
            _milestone("m2"),
            _milestone("m3"),
            _milestone("m4"),
        ),
        (
            _verification("m1", "2026-07-14T09:00:00Z"),
            # Deliberately out-of-order landings: plan order must win.
            _verification("m4", "2026-07-14T13:00:00Z"),
            _verification("m2", "2026-07-14T11:00:00Z"),
            _verification("m3", "2026-07-14T12:00:00Z"),
        ),
    )
    assert milestones_landed_since(state, _decision()) == ("m2", "m3", "m4")
