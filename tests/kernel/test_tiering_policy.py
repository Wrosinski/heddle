"""AC-2/4/12 discriminators: approved axes, matrix and current vocabulary."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from tests.tiering_helpers import (
    ASTRA,
    FABLE,
    OPUS,
    ROLES,
    SOL,
    SOL_XHIGH,
    api,
    axes,
    entry,
    policy,
)


@pytest.mark.parametrize(
    "scope,complexity,testability,base",
    [
        ("small", "low", "full", "light"),
        ("small", "high", "full", "light"),
        ("small", "low", "partial", "light"),
        ("small", "high", "partial", "light"),
        ("small", "low", "none", "full"),
        ("small", "high", "none", "full"),
        ("medium", "low", "full", "light"),
        ("medium", "low", "partial", "light"),
        ("medium", "low", "none", "full"),
        ("medium", "high", "full", "full"),
        ("medium", "high", "partial", "full"),
        ("medium", "high", "none", "full"),
        ("large", "low", "full", "full"),
        ("large", "low", "partial", "full"),
        ("large", "low", "none", "full"),
        ("large", "high", "full", "full"),
        ("large", "high", "partial", "full"),
        ("large", "high", "none", "full"),
    ],
)
def test_ac2_all_axis_boundaries_recommend_the_declared_base(
    scope, complexity, testability, base
):
    result = api("heddle.kernel.feature_policy").recommend_policy(
        axes(scope, complexity, testability)
    )
    assert result.base == base
    assert {row.role for row in result.entries} == set(ROLES)
    # Literal schedule oracle, independent of production recommendation data.
    enabled = {
        "spec-review",
        "plan-review",
        "review-test-scaffolding",
        "milestone-review",
        *(
            ("peer-review-sequential",)
            if base == "light"
            else ("behavior-review", "complexity-review")
        ),
    }
    reviewers = {
        "spec-review": ASTRA,
        "plan-review": SOL,
        "review-test-scaffolding": SOL_XHIGH,
        "milestone-review": SOL,
        "peer-review-sequential": OPUS,
        "behavior-review": OPUS,
        "complexity-review": SOL,
        "robustness-analysis": SOL_XHIGH,
    }
    for row in result.entries:
        actual = asdict(row)
        trigger = actual.pop("trigger")
        assert trigger is None  # Axes alone cannot name an integration gap.
        active = row.role in enabled
        doc = row.role in {"spec-review", "plan-review"}
        dual = doc and scope == "large" and complexity == "high"
        convergence = (
            row.role == "review-test-scaffolding"
            and scope == "large"
            and complexity == "high"
        )
        expected = {
            "role": row.role,
            "scope": "milestone" if row.role == "milestone-review" else "feature",
            "mode": "off"
            if not active
            else "convergence"
            if convergence
            else "upper-limit",
            "limit": None
            if not active or convergence
            else 3
            if doc and complexity == "high"
            else 2,
            "minimum_rounds": 1 if active else 0,
            "primary": reviewers[row.role],
            "secondary": FABLE if dual else None,
        }
        assert actual == expected, row.role


def test_ac2_large_high_default_roles_tuples_and_dual_first_schedule():
    result = api("heddle.kernel.feature_policy").recommend_policy(
        axes("large", "high", "partial")
    )
    rows = {row.role: row for row in result.entries}
    for role, primary in (("spec-review", ASTRA), ("plan-review", SOL)):
        assert asdict(rows[role].primary) == primary
        assert asdict(rows[role].secondary) == FABLE
        assert (rows[role].mode, rows[role].limit) == ("upper-limit", 3)
    assert rows["review-test-scaffolding"].mode == "convergence"
    assert rows["review-test-scaffolding"].limit is None
    assert rows["review-test-scaffolding"].secondary is None
    assert asdict(rows["review-test-scaffolding"].primary) == SOL_XHIGH
    assert asdict(rows["milestone-review"].primary) == SOL
    assert asdict(rows["behavior-review"].primary) == OPUS
    assert asdict(rows["complexity-review"].primary) == SOL
    assert rows["peer-review-sequential"].mode == "off"
    assert rows["robustness-analysis"].trigger is None
    assert rows["robustness-analysis"].mode == "off"
    assert asdict(rows["robustness-analysis"].primary) == SOL_XHIGH


def test_ac2_confirmed_overrides_not_base_recommendations_are_effective():
    kernel = api("heddle.kernel.feature_policy")
    chosen = policy(
        overrides={
            "spec-review": entry(
                "spec-review", mode="off", limit=None, minimum_rounds=0
            ),
            "behavior-review": entry("behavior-review", limit=9, primary=SOL_XHIGH),
            "review-test-scaffolding": entry("review-test-scaffolding", primary=FABLE),
        }
    )
    resolved = kernel.effective_policy(chosen)
    assert resolved.entries == chosen.entries
    assert resolved.base == "light"
    assert {row.role: row for row in resolved.entries}["spec-review"].mode == "off"
    assert kernel.recommend_policy(axes("large", "high", "none")).base == "full"
    assert kernel.effective_policy(chosen).entries == chosen.entries


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "upper-limit", "limit": 0},
        {"mode": "upper-limit", "limit": -1},
        {"mode": "upper-limit", "limit": True},
        {"mode": "upper-limit", "limit": 1.5},
        {"mode": "convergence", "limit": 3},
        {"mode": "off", "limit": None, "minimum_rounds": 1},
        {"mode": "upper-limit", "limit": 2, "minimum_rounds": 3},
        {"mode": "pretend-pass"},
        {"scope": "milestone"},
        {"role": "review-synthesis"},
    ],
)
def test_ac2_impossible_matrix_cells_refuse_without_silent_repair(changes):
    kernel = api("heddle.kernel.feature_policy")
    original = policy()
    bad = replace(original.entries[0], **changes)
    candidate = replace(original, entries=(bad, *original.entries[1:]))
    with pytest.raises(ValueError, match="policy|gate|limit|round|mode|scope|role"):
        kernel.effective_policy(candidate)
    assert original.entries[0].limit == 2


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "approval", "rationale", "cli", "effort"]
)
def test_ac2_incomplete_policy_or_execution_identity_is_rejected(mutation):
    kernel = api("heddle.kernel.feature_policy")
    candidate = policy()
    if mutation == "missing":
        candidate = replace(candidate, entries=candidate.entries[:-1])
    elif mutation == "duplicate":
        candidate = replace(
            candidate, entries=(*candidate.entries, candidate.entries[0])
        )
    elif mutation == "approval":
        candidate = replace(candidate, approval="")
    elif mutation == "rationale":
        candidate = replace(candidate, axes=replace(candidate.axes, scope_rationale=""))
    else:
        reviewer = replace(
            candidate.entries[0].primary,
            **(
                {"cli": "unregistered"}
                if mutation == "cli"
                else {"reasoning_effort": "infinite"}
            ),
        )
        candidate = replace(
            candidate,
            entries=(
                replace(candidate.entries[0], primary=reviewer),
                *candidate.entries[1:],
            ),
        )
    with pytest.raises(ValueError):
        kernel.effective_policy(candidate)


def test_ac4_budget_counts_dual_first_calls_and_symbolic_milestones():
    kernel = api("heddle.kernel.feature_policy")
    chosen = policy(
        overrides={"spec-review": entry("spec-review", limit=7, secondary=FABLE)}
    )
    budget = kernel.effective_policy(chosen).budget
    # Spec 8 + plan 2 + scaffold 2 + integrated peer 2; milestone 2 each.
    assert (budget.feature_maximum, budget.per_milestone_maximum) == (14, 2)
    assert budget.maximum_for(3) == 20
    assert budget.minimum_for(3) == 8  # Five feature slots plus three milestone slots.
    unbounded = replace(
        chosen,
        entries=(
            replace(chosen.entries[0], mode="convergence", limit=None),
            *chosen.entries[1:],
        ),
    )
    assert kernel.effective_policy(unbounded).budget.maximum_for(3) is None


def test_ac2_overlay_can_be_selected_with_peers_off_but_trigger_names_its_gap():
    kernel = api("heddle.kernel.feature_policy")
    chosen = policy(
        overrides={
            "peer-review-sequential": entry(
                "peer-review-sequential", mode="off", limit=None, minimum_rounds=0
            ),
            "robustness-analysis": entry(
                "robustness-analysis",
                primary=SOL_XHIGH,
                trigger={
                    "gate": "robustness-analysis",
                    "gap": "Interrupted commit publication",
                    "references": ["src/example.py"],
                },
            ),
        }
    )
    assert kernel.effective_policy(chosen).entries == chosen.entries
    assert kernel.effective_policy(chosen).final_verification_stage == "robustness"
    without_gap = replace(chosen.entries[-1], trigger=None)
    with pytest.raises(ValueError, match="conditional trigger"):
        kernel.effective_policy(
            replace(chosen, entries=(*chosen.entries[:-1], without_gap))
        )
    bad = replace(chosen.entries[-1], trigger={"gate": "plan-review", "gap": ""})
    with pytest.raises(ValueError, match="trigger|gap|gate"):
        kernel.effective_policy(replace(chosen, entries=(*chosen.entries[:-1], bad)))


@pytest.mark.parametrize("milestones", [True, -1, 1.5, "1"])
@pytest.mark.parametrize("method", ["minimum_for", "maximum_for"])
def test_ac4_budget_contract_rejects_invalid_milestone_counts(milestones, method):
    budget = api().CallBudget(2, 1, 3, None)
    with pytest.raises(ValueError, match="milestone count"):
        getattr(budget, method)(milestones)


def test_ac4_unbounded_milestone_budget_only_applies_to_present_milestones():
    budget = api().CallBudget(2, 1, 3, None)
    assert budget.maximum_for(0) == 3
    assert budget.maximum_for(1) is None
    assert budget.minimum_for(0) == 2


@pytest.mark.parametrize("complexity", ["low", "high"])
def test_ac12_v8_milestones_do_not_require_hour_estimates(complexity):
    from heddle.kernel.state import parse_state_document
    from tests.operational_model_helpers import document
    from tests.tiering_helpers import wire_policy

    value = document(schema="heddle.state/v9", feature_policy=wire_policy())
    value.pop("tier", None)  # v8 has one policy owner, not a second tier scheduler.
    value["milestones"][0]["complexity"] = complexity
    value["milestones"][0].pop("estimated_hours")
    state = parse_state_document(value, source=Path("authored-v8.yaml"))
    assert state.milestones[0].complexity == complexity
    assert state.milestones[0].estimated_hours is None


def test_ac12_v8_rejects_medium_complexity():
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.state import parse_state_document
    from tests.operational_model_helpers import document
    from tests.tiering_helpers import wire_policy

    value = document(schema="heddle.state/v9", feature_policy=wire_policy())
    value["milestones"][0]["complexity"] = "medium"
    value.pop("tier", None)
    with pytest.raises(KernelError, match="complexity|medium"):
        parse_state_document(value, source=Path("current.yaml"))


def test_v8_policy_round_trip_has_one_typed_owner():
    from heddle.kernel.state import parse_state_document
    from tests.operational_model_helpers import document
    from tests.tiering_helpers import wire_policy

    value = document(schema="heddle.state/v9", feature_policy=wire_policy())
    value.pop("tier", None)
    parsed = parse_state_document(value, source=Path("v8.yaml"))
    assert parsed.feature_policy == policy()
    assert parsed.milestones[0].estimated_hours == tuple(
        value["milestones"][0]["estimated_hours"]
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema",), "unknown-policy/v1"),
        (("extra",), True),
        (("revision",), True),
        (("approval",), ""),
        (("axes", "scope"), []),
        (("axes", "scope_rationale"), None),
        (("axes", "extra"), "unsupported"),
        (("entries",), "not-a-list"),
        (("entries", 0, "role"), []),
        (("entries", 0, "scope"), "milestone"),
        (("entries", 0, "mode"), []),
        (("entries", 0, "limit"), True),
        (("entries", 0, "primary", "cli"), []),
        (("entries", 0, "primary", "model"), None),
        (("entries", 0, "primary", "reasoning_effort"), []),
        (("entries", 0, "primary", "extra"), "unsupported"),
        (("entries", 0, "trigger"), "not-a-mapping"),
    ],
)
def test_v8_policy_decoder_rejects_malformed_fields_as_kernel_errors(path, value):
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.state import parse_state_document
    from tests.operational_model_helpers import document
    from tests.tiering_helpers import wire_policy

    wire = wire_policy()
    parent = wire
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = value
    state = document(schema="heddle.state/v9", feature_policy=wire)
    state.pop("tier", None)
    with pytest.raises(KernelError, match="policy"):
        parse_state_document(state, source=Path("malformed-v8.yaml"))


@pytest.mark.parametrize(
    "mutation", ["tier", "missing-policy", "hours-null", "hours-bool"]
)
def test_v8_refuses_second_owner_missing_policy_and_invalid_optional_hours(mutation):
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.state import parse_state_document
    from tests.operational_model_helpers import document
    from tests.tiering_helpers import wire_policy

    state = document(schema="heddle.state/v9", feature_policy=wire_policy())
    if mutation == "tier":
        state["tier"] = 3
    if mutation != "tier":
        state.pop("tier", None)
    if mutation == "missing-policy":
        state.pop("feature_policy")
    elif mutation.startswith("hours-"):
        state["milestones"][0]["estimated_hours"] = (
            None if mutation == "hours-null" else [True, 2]
        )
    with pytest.raises(KernelError, match="tier|feature_policy|estimated_hours"):
        parse_state_document(state, source=Path("malformed-v8.yaml"))


def test_v8_snapshot_uses_confirmed_policy_without_legacy_tier(
    host_copy, kernel_corpus
):
    import yaml

    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from tests.tiering_helpers import wire_policy

    host = host_copy(kernel_corpus.tiny)
    path = host / "plans/sample-feature/state.yaml"
    value = yaml.safe_load(path.read_text())
    value.update(
        schema="heddle.state/v9",
        stage="spec-review",
        feature_policy=wire_policy(),
        gates=[],
    )
    value.pop("tier", None)
    path.write_text(yaml.safe_dump(value))
    before = path.read_bytes()
    snapshot = resolve_snapshot(load_project_config(host), "sample-feature")
    assert snapshot.state.feature_policy is not None
    assert snapshot.state.feature_policy.revision == 1
    assert path.read_bytes() == before
