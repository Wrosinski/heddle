"""AC-3 pure core: one window rule decides the reviewer slots of every round."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from tests.tiering_helpers import FABLE, api, entry


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
