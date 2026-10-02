"""AC-3 pure core: one window rule decides the reviewer slots of every round."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from tests.tiering_helpers import FABLE, SOL, api, entry


def _round_slots():
    kernel = api("heddle.kernel.review_assignments")
    assert hasattr(kernel, "round_slots"), (
        "FAIL AC-3: the review-assignment kernel has no round_slots derivation"
    )
    return kernel.round_slots


def _windowed(window, *, secondary=FABLE, **kw):
    row = entry("spec-review", secondary=secondary, **kw)
    if window is None:
        return row
    assert "secondary_rounds" in {f.name for f in fields(row)}, (
        "FAIL AC-3: policy entries have no secondary_rounds window"
    )
    return replace(row, secondary_rounds=window)


BOTH = ("primary", "secondary")
PRIMARY = ("primary",)


@pytest.mark.parametrize(
    "window,secondary,kw,expected",
    [
        (None, FABLE, {"limit": 4}, (BOTH, PRIMARY, PRIMARY, PRIMARY)),
        (1, FABLE, {"limit": 4}, (BOTH, PRIMARY, PRIMARY, PRIMARY)),
        (2, FABLE, {"limit": 4}, (BOTH, BOTH, PRIMARY, PRIMARY)),
        ("all", FABLE, {"limit": 4}, (BOTH, BOTH, BOTH, BOTH)),
        (None, None, {"limit": 4}, (PRIMARY, PRIMARY, PRIMARY, PRIMARY)),
        (
            "all",
            FABLE,
            {"mode": "convergence", "limit": None},
            (BOTH, BOTH, BOTH, BOTH),
        ),
    ],
)
def test_ac3_round_slots_follow_the_window(window, secondary, kw, expected):
    round_slots = _round_slots()
    serves = api("heddle.kernel.feature_policy").secondary_serves
    row = _windowed(window, secondary=secondary, **kw)
    for number, names in enumerate(expected, start=1):
        slots = round_slots(row, number)
        assert tuple(slot.name for slot in slots) == names, (
            f"FAIL AC-3: round {number} slots under window {window!r}"
        )
        assert slots[0].reviewer == row.primary
        if names == BOTH:
            assert slots[1].reviewer == row.secondary
        # One owner: the slots and the resolver's predicate never disagree.
        assert serves(row, number) is (names == BOTH)


def test_ac3_all_keeps_serving_after_an_allowance_raises_the_limit():
    round_slots = _round_slots()
    row = _windowed("all", limit=2)
    raised = replace(row, limit=6)
    assert [len(round_slots(raised, number)) for number in range(1, 7)] == [2] * 6


def _milestone_review_state(stage, rounds_by_milestone):
    """A parsed state whose milestone reviews have opened the given rounds."""
    from pathlib import Path

    from heddle.contracts.review_assignments import (
        AssignmentRound,
        ReviewAssignment,
        ReviewAssignments,
        ReviewerSlot,
    )
    from heddle.kernel.state import parse_state_document
    from tests.operational_model_helpers import document

    state = parse_state_document(document(), source=Path("state.yaml"))
    reviewing = entry("milestone-review", primary=SOL)
    policy = replace(
        state.feature_policy,
        entries=tuple(
            reviewing if row.role == "milestone-review" else row
            for row in state.feature_policy.entries
        ),
    )
    primary = (ReviewerSlot("primary", reviewing.primary),)
    assignments = tuple(
        ReviewAssignment(
            f"milestone-review-{scope}",
            "milestone-review",
            scope,
            1,
            tuple(
                AssignmentRound(
                    number,
                    1,
                    "initial" if number == 1 else "verification",
                    "Review the milestone.",
                    scope,
                    (),
                    primary,
                )
                for number in range(1, count + 1)
            ),
        )
        for scope, count in rounds_by_milestone.items()
    )
    return replace(
        state,
        stage=stage,
        feature_policy=policy,
        policy_history=(policy,),
        review_assignments=ReviewAssignments(assignments=assignments),
    )


@pytest.mark.parametrize(
    "stage,rounds,window,refused",
    [
        # Past implement with every milestone review started, the next round decides.
        ("peer-review", {"m1": 1, "m2": 1}, 1, True),
        ("peer-review", {"m1": 1, "m2": 1}, 2, False),
        ("peer-review", {"m1": 1, "m2": 1}, "all", False),
        ("peer-review", {"m1": 2, "m2": 2}, "all", True),
        ("peer-review", {"m1": 2, "m2": 1}, 2, False),
        # A milestone whose review has not started opens round 1 with the secondary.
        ("peer-review", {"m1": 2}, 1, False),
        # At or before implement a milestone can still be added.
        ("implement", {"m1": 2, "m2": 2}, 1, False),
    ],
)
def test_ac6_milestone_review_amendment_follows_the_rounds_milestones_can_open(
    stage, rounds, window, refused
):
    kernel = api("heddle.kernel.review_assignments")
    assert hasattr(kernel, "secondary_window_refusal"), (
        "FAIL AC-6: the review-assignment kernel has no amendment window check"
    )
    state = _milestone_review_state(stage, rounds)
    added = replace(
        entry("milestone-review", primary=SOL, secondary=FABLE),
        secondary_rounds=window,
    )
    incoming = replace(
        state.feature_policy,
        revision=2,
        entries=tuple(
            added if row.role == "milestone-review" else row
            for row in state.feature_policy.entries
        ),
    )
    message = kernel.secondary_window_refusal(state, incoming)
    if not refused:
        assert message is None, f"FAIL AC-6: an openable round was refused: {message}"
        return
    assert message is not None, "FAIL AC-6: a secondary that can serve no round passed"
    for fact in (
        "milestone-review",
        f"secondary_rounds {window}",
        "m1 review at round",
    ):
        assert fact in message, f"FAIL AC-6: refusal omits {fact!r}: {message}"
