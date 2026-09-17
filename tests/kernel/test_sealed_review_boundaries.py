"""Sealed review-boundary structural contracts (Feature AC-3/5/8)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from heddle.kernel.project_config import KernelError
from heddle.kernel.state import parse_state_document
from tests.operational_model_helpers import document


def test_ac8_new_state_is_v9_v3_and_old_operational_state_is_refused() -> None:
    current = document()
    assert current["schema"] == "heddle.state/v9"
    assert current["review_assignments"]["schema"] == "heddle.review-assignments/v3"
    assert current["review_assignments"]["acceptances"] == []
    parse_state_document(current, source=Path("state.yaml"))

    old = deepcopy(current)
    old["schema"] = "heddle.state/v8"
    old["review_assignments"] = {
        key: value
        for key, value in old["review_assignments"].items()
        if key != "acceptances"
    }
    old["review_assignments"]["schema"] = "heddle.review-assignments/v2"
    with pytest.raises(KernelError, match="v8.*unsupported") as refused:
        parse_state_document(old, source=Path("state.yaml"))
    assert refused.value.reason == "incompatible-state-schema"


def test_ac5_receipt_record_digest_is_canonical_and_content_sensitive() -> None:
    from heddle.kernel.review_assignments import acceptance_record_digest

    row = {"run_id": "r1", "finding_id": "F-1", "status": "addressed"}
    reordered = {"status": "addressed", "finding_id": "F-1", "run_id": "r1"}
    changed = {**row, "status": "settled"}

    assert acceptance_record_digest(row) == acceptance_record_digest(reordered)
    assert acceptance_record_digest(row) != acceptance_record_digest(changed)


def test_ac3_completed_duty_boundary_without_receipt_is_invalid() -> None:
    from heddle.contracts.review_assignments import ReviewAssignment
    from heddle.kernel.review_assignments import validate_boundary_acceptances

    current = parse_state_document(document(), source=Path("state.yaml"))
    assignment = ReviewAssignment(
        "operational-example:review-test-scaffolding:feature:p1",
        "review-test-scaffolding",
        "feature",
        1,
        (),
    )
    forged = replace(
        current,
        stage="implement",
        review_assignments=replace(
            current.review_assignments,
            assignments=(assignment,),
        ),
    )
    with pytest.raises(KernelError, match="acceptance receipt"):
        validate_boundary_acceptances(forged)
