"""Value locks for the current workflow vocabulary."""

from heddle.contracts.gates import VERDICT_PRODUCING_GATES
from heddle.contracts.review_assignments import ROLE_STAGES
from heddle.contracts.schemas import STAGES
from heddle.kernel.model import BLOCKING_TRIGGER_ROWS, PER_MILESTONE_GATE_STAGES


def test_current_role_stages_cover_the_exact_stage_vocabulary() -> None:
    assert tuple(dict.fromkeys(ROLE_STAGES.values())) == STAGES[1:-1]


def test_per_milestone_scope_marks_exactly_implement() -> None:
    assert PER_MILESTONE_GATE_STAGES == frozenset({"implement"})


def test_current_verdict_roles_are_exact() -> None:
    assert VERDICT_PRODUCING_GATES == frozenset(
        {"spec-review", "plan-review", "review-test-scaffolding"}
    )


def test_blocking_trigger_rows_keep_current_order_and_remedies() -> None:
    assert BLOCKING_TRIGGER_ROWS == (
        ("not-authorized", "heddle phase-exit"),
        ("milestone-out-of-sequence", "heddle milestone edit"),
        ("task-incomplete", "heddle task done"),
        ("verification-missing", "heddle verify --scope"),
        ("pending-decisions", "heddle decisions"),
        ("gate-not-converged", "heddle run-gate"),
        ("awaiting-human-authorization", "heddle phase-exit"),
        ("awaiting-human-completion", "heddle kickoff"),
        ("principles-not-ratified", "ratify docs/workflow/engineering-principles.md"),
        ("gate-cap-reached", "heddle decisions"),
        ("no-progress", "heddle decisions"),
        ("attempt-cap", "heddle decisions"),
    )
