"""AC-2/4/12 discriminators: approved axes, matrix and current vocabulary."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, fields, replace
from pathlib import Path

import pytest

from tests.tiering_helpers import (
    ASTRA,
    FABLE,
    OPUS,
    ROLES,
    SOL_XHIGH,
    api,
    axes,
    entry,
    policy,
)

ASTRA_XHIGH = {**ASTRA, "reasoning_effort": "xhigh"}
FABLE_HIGH = {**FABLE, "reasoning_effort": "high"}


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
    assert [asdict(item) for item in result.suggestions] == (
        []
        if base == "full"
        else [
            {
                "role": "review-test-scaffolding",
                "secondary": ASTRA_XHIGH,
                "secondary_rounds": "all",
            }
        ]
    )
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
        "spec-review": ASTRA_XHIGH,
        "plan-review": ASTRA_XHIGH,
        "review-test-scaffolding": OPUS,
        "milestone-review": OPUS,
        "peer-review-sequential": OPUS,
        "behavior-review": OPUS,
        "complexity-review": OPUS,
        "robustness-analysis": ASTRA_XHIGH,
    }
    secondaries = {
        "spec-review": FABLE_HIGH,
        "plan-review": FABLE_HIGH,
        "behavior-review": ASTRA_XHIGH,
        "robustness-analysis": FABLE_HIGH,
        # The full base reviews scaffolded tests and milestones with a second
        # model.
        **(
            {"review-test-scaffolding": ASTRA_XHIGH, "milestone-review": ASTRA_XHIGH}
            if base == "full"
            else {}
        ),
    }
    every_round = {"spec-review", "plan-review"} | (
        {"review-test-scaffolding", "behavior-review"} if base == "full" else set()
    )
    for row in result.entries:
        actual = asdict(row)
        trigger = actual.pop("trigger")
        assert trigger is None  # Axes alone cannot name an integration gap.
        active = row.role in enabled
        expected = {
            "role": row.role,
            "scope": "milestone" if row.role == "milestone-review" else "feature",
            # Every active role reviews to convergence, without a round limit.
            "mode": "convergence" if active else "off",
            "limit": None,
            "minimum_rounds": 1 if active else 0,
            "primary": reviewers[row.role],
            "secondary": secondaries.get(row.role),
            "secondary_rounds": "all" if row.role in every_round else 1,
        }
        assert actual == expected, f"FAIL AC-8: {row.role} recommendation"
    effective = api("heddle.kernel.feature_policy").effective_policy(
        policy(
            assessment=axes(scope, complexity, testability),
            overrides={row.role: row for row in result.entries},
        )
    )
    assert effective.budget.feature_minimum == (6 if base == "light" else 9)
    # The full base's milestone secondary serves each milestone's first round.
    assert effective.budget.per_milestone_minimum == (1 if base == "light" else 2)
    # AC-7: convergence sets no call ceiling.
    assert effective.budget.feature_maximum is None, "FAIL AC-7: default budget"


def test_ac2_large_high_default_roles_tuples_and_dual_first_schedule():
    result = api("heddle.kernel.feature_policy").recommend_policy(
        axes("large", "high", "partial")
    )
    rows = {row.role: row for row in result.entries}
    for role in ("spec-review", "plan-review"):
        assert asdict(rows[role].primary) == ASTRA_XHIGH
        assert asdict(rows[role].secondary) == FABLE_HIGH
        assert (rows[role].mode, rows[role].limit) == ("convergence", None)
    assert rows["review-test-scaffolding"].mode == "convergence"
    assert rows["review-test-scaffolding"].limit is None
    assert asdict(rows["review-test-scaffolding"].secondary) == ASTRA_XHIGH
    assert rows["review-test-scaffolding"].secondary_rounds == "all"
    assert result.suggestions == ()
    assert asdict(rows["review-test-scaffolding"].primary) == OPUS
    assert asdict(rows["milestone-review"].primary) == OPUS
    assert asdict(rows["milestone-review"].secondary) == ASTRA_XHIGH
    assert rows["milestone-review"].secondary_rounds == 1
    assert asdict(rows["behavior-review"].primary) == OPUS
    assert asdict(rows["behavior-review"].secondary) == ASTRA_XHIGH
    assert rows["behavior-review"].secondary_rounds == "all"
    assert asdict(rows["complexity-review"].primary) == OPUS
    assert rows["peer-review-sequential"].mode == "off"
    assert rows["robustness-analysis"].trigger is None
    assert rows["robustness-analysis"].mode == "off"
    assert asdict(rows["robustness-analysis"].primary) == ASTRA_XHIGH
    assert asdict(rows["robustness-analysis"].secondary) == FABLE_HIGH


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
        overrides={
            "spec-review": entry("spec-review", limit=7, secondary=FABLE),
            "behavior-review": entry(
                "behavior-review",
                mode="off",
                limit=None,
                minimum_rounds=0,
                primary=OPUS,
                secondary=ASTRA_XHIGH,
            ),
            "robustness-analysis": entry(
                "robustness-analysis",
                mode="off",
                limit=None,
                minimum_rounds=0,
                primary=ASTRA_XHIGH,
                secondary=FABLE_HIGH,
            ),
        }
    )
    budget = kernel.effective_policy(chosen).budget
    # Spec 8 + plan 2 + scaffold 2 + integrated peer 2; milestone 2 each.
    assert (budget.feature_maximum, budget.per_milestone_maximum) == (14, 2)
    assert budget.maximum_for(3) == 20
    assert budget.minimum_for(3) == 8  # Five feature slots plus three milestone slots.
    # Disabled primary/secondary selections add no calls to either bound.
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


# secondary-reviewer-rounds-v1 (AC-1, AC-7, AC-8): the window on a role entry.
ASTRA_SUGGESTION = {"cli": "codex", "model": "gpt-6-astra", "reasoning_effort": "xhigh"}


def _windowed_wire(value, *, role="spec-review", secondary=FABLE, **kw):
    """A literal wire policy whose one entry states an explicit window."""
    chosen = entry(role, secondary=secondary, **kw)
    document = {
        "schema": "heddle.feature-policy/v1",
        **asdict(policy(overrides={role: chosen})),
    }
    for row in document["entries"]:
        row.pop("secondary_rounds", None)
        if row["role"] == role:
            row["secondary_rounds"] = value
    return document


def _window_of(confirmed, role="spec-review"):
    row = next(row for row in confirmed.entries if row.role == role)
    assert hasattr(row, "secondary_rounds"), (
        "FAIL AC-1: policy entries have no secondary_rounds window"
    )
    return row.secondary_rounds


@pytest.mark.parametrize(
    "value,kw",
    [
        ("all", {}),
        (1, {}),
        (2, {}),
        (1, {"secondary": None}),
        (7, {"mode": "convergence", "limit": None}),
        ("all", {"mode": "convergence", "limit": None}),
        (5, {"mode": "off", "limit": None, "minimum_rounds": 0}),
    ],
)
def test_ac1_window_values_inside_the_rule_decode(value, kw):
    kernel = api("heddle.kernel.feature_policy")
    try:
        decoded = kernel.parse_policy(_windowed_wire(value, **kw))
    except ValueError as error:
        pytest.fail(f"FAIL AC-1: valid window {value!r} refused: {error}")
    assert _window_of(decoded) == value


def test_ac1_absent_window_decodes_as_one():
    kernel = api("heddle.kernel.feature_policy")
    document = _windowed_wire(1)
    for row in document["entries"]:
        row.pop("secondary_rounds", None)
    decoded = kernel.parse_policy(document)
    assert {_window_of(decoded, role) for role in ROLES} == {1}


@pytest.mark.parametrize(
    "value,kw",
    [
        (0, {}),
        (-1, {}),
        (True, {}),
        (False, {}),
        (1.5, {}),
        ("2", {}),
        (None, {}),
        ("", {}),
        ("ALL", {}),
        ("every", {}),
        (3, {}),
        (2, {"secondary": None}),
        ("all", {"secondary": None}),
        (3, {"mode": "off", "limit": None, "minimum_rounds": 0, "secondary": None}),
    ],
    ids=lambda item: repr(item) if not isinstance(item, dict) else "-".join(item),
)
def test_ac1_window_values_outside_the_rule_are_refused_naming_role_and_field(
    value, kw
):
    kernel = api("heddle.kernel.feature_policy")
    with pytest.raises(ValueError) as refused:
        kernel.parse_policy(_windowed_wire(value, **kw))
    message = str(refused.value)
    assert "spec-review" in message and "secondary_rounds" in message, (
        f"FAIL AC-1: refusal of {value!r} must name the role and field: {message}"
    )


@pytest.mark.parametrize(
    "window,expected",
    [
        (None, (True, False, False, False, False)),
        (1, (True, False, False, False, False)),
        (3, (True, True, True, False, False)),
        ("all", (True, True, True, True, True)),
    ],
)
def test_ac7_window_predicate_counts_rounds_from_the_first(window, expected):
    kernel = api("heddle.kernel.feature_policy")
    assert hasattr(kernel, "secondary_serves"), (
        "FAIL AC-7: the policy resolver has no secondary window predicate"
    )
    row = entry("spec-review", limit=5, secondary=FABLE)
    if window is not None:
        row = replace(row, secondary_rounds=window)
    assert tuple(kernel.secondary_serves(row, n) for n in range(1, 6)) == expected
    primary_only = entry("spec-review", limit=5)
    assert not any(kernel.secondary_serves(primary_only, n) for n in range(1, 6))


# Literal Light fixture: spec, plan, scaffold and peer each add 1 to 2 calls.
@pytest.mark.parametrize(
    "spec,minimum,maximum",
    [
        ({"limit": 3, "minimum_rounds": 2}, 5, 9),
        ({"limit": 3, "minimum_rounds": 2, "secondary": FABLE}, 6, 10),
        ({"limit": 3, "minimum_rounds": 2, "secondary": FABLE, "window": 2}, 7, 11),
        ({"limit": 3, "minimum_rounds": 2, "secondary": FABLE, "window": 3}, 7, 12),
        ({"limit": 3, "minimum_rounds": 2, "secondary": FABLE, "window": "all"}, 7, 12),
        ({"limit": 3, "minimum_rounds": 1, "secondary": FABLE, "window": "all"}, 5, 12),
        (
            {
                "mode": "convergence",
                "limit": None,
                "minimum_rounds": 2,
                "secondary": FABLE,
                "window": "all",
            },
            7,
            None,
        ),
        (
            {
                "mode": "off",
                "limit": None,
                "minimum_rounds": 0,
                "secondary": FABLE,
                "window": "all",
            },
            3,
            6,
        ),
    ],
    ids=[
        "survivor-primary-only",
        "survivor-window-absent",
        "window-2",
        "window-at-limit",
        "all",
        "all-minimum-1",
        "convergence-all",
        "off-retains-secondary",
    ],
)
def test_ac7_budget_adds_the_rounds_the_secondary_serves(spec, minimum, maximum):
    kernel = api("heddle.kernel.feature_policy")
    spec = dict(spec)
    window = spec.pop("window", None)
    row = entry("spec-review", **spec)
    if window is not None:
        assert "secondary_rounds" in {f.name for f in fields(row)}, (
            "FAIL AC-7: policy entries have no secondary_rounds window"
        )
        row = replace(row, secondary_rounds=window)
    budget = kernel.effective_policy(policy(overrides={"spec-review": row})).budget
    assert (budget.feature_minimum, budget.feature_maximum) == (minimum, maximum), (
        f"FAIL AC-7: window {window!r} budget"
    )


def test_ac8_suggestions_name_the_scaffolding_secondary_without_selecting_it():
    from heddle.contracts import operations as ops

    kernel = api("heddle.kernel.feature_policy")
    # Only the light base suggests it; the full base selects it.
    for assessment in (axes(), axes("small", "high", "partial")):
        result = kernel.recommend_policy(assessment)
        assert result.base == "light"
        payload = ops.decoded_payload(result)
        assert "suggestions" in payload, (
            "FAIL AC-8: the recommendation carries no suggestions"
        )
        assert payload["suggestions"] == [
            {
                "role": "review-test-scaffolding",
                "secondary": ASTRA_SUGGESTION,
                "secondary_rounds": "all",
            }
        ]
        rows = {row.role: row for row in result.entries}
        assert rows["review-test-scaffolding"].secondary is None
        confirmed = kernel.effective_policy(
            policy(assessment=assessment, overrides=rows)
        )
        scaffold = next(
            row for row in confirmed.entries if row.role == "review-test-scaffolding"
        )
        assert scaffold.secondary is None
        adopted = replace(
            scaffold,
            secondary=api().Reviewer(**ASTRA_SUGGESTION),
            secondary_rounds="all",
        )
        kernel.effective_policy(
            policy(
                assessment=assessment,
                overrides={**rows, "review-test-scaffolding": adopted},
            )
        )


# antigravity-reviewer-lane-v1, milestone m2 (AC-4, AC-5). Model validation
# applies to new selections at policy confirmation; stored history decodes as
# today (`heddle.kernel.feature_policy._reviewer` stays unchanged).
NEON = {"cli": "antigravity", "model": "gemini-4-neon", "reasoning_effort": "high"}
CODEX_GEMINI = {"cli": "codex", "model": "gemini-4-neon", "reasoning_effort": "high"}
CLAUDE_GEMINI = {
    "cli": "claude",
    "model": "gemini-4-neon",
    "reasoning_effort": "high",
}
REFUSED_ANTIGRAVITY_MODELS = (
    "gemini-4-neon-high",
    "gemini-3.8-flash",
    "gemini-4-flash",
    "gemini-4-lite",
    "gemini-pro-orchestrator",
    "claude-opus-5-5",
)


def _confirm(tmp_path, monkeypatch, reviewer):
    """Confirm a policy whose spec-review primary is ``reviewer`` on a
    prepared intake; returns the typed result."""
    import yaml

    from tests.tiering_helpers import (
        FEATURE,
        blank_host,
        invoke,
        prepared,
        wire_policy,
    )

    host = blank_host(tmp_path, monkeypatch)
    prepared(host)
    intake = host / ".heddle" / "intake" / f"{FEATURE}.yaml"
    return invoke(
        "FeaturePolicy",
        slug=FEATURE,
        payload=wire_policy(
            overrides={"spec-review": entry("spec-review", primary=reviewer)}
        ),
        expect_revision=yaml.safe_load(intake.read_text())["revision"],
    )


def _refusal_text(result, selection) -> str:
    assert not result.ok, (
        f"FAIL AC-5: confirming {selection} must be refused, got {result.to_envelope()}"
    )
    return f"{result.error.message} {result.error.hint}"


@pytest.mark.parametrize("model", ["gemini-4-neon", "gemini-4-hydrogen"])
def test_ac5_confirmation_accepts_gemini_4_base_names(tmp_path, monkeypatch, model):
    """Red: the lane is outside the reviewer CLI domain today."""
    result = _confirm(tmp_path, monkeypatch, {**NEON, "model": model})
    assert result.ok, f"FAIL AC-5: confirming {model} refused: {result.to_envelope()}"


@pytest.mark.parametrize("model", REFUSED_ANTIGRAVITY_MODELS)
def test_ac5_confirmation_refuses_other_names_naming_the_model(
    tmp_path, monkeypatch, model
):
    """Red: today the lane is refused as a CLI, without naming the model."""
    text = _refusal_text(
        _confirm(tmp_path, monkeypatch, {**NEON, "model": model}), model
    )
    assert model in text, f"FAIL AC-5: the refusal must name {model}: {text}"


def test_ac5_confirmation_effort_suffixed_name_hints_base_name_and_effort(
    tmp_path, monkeypatch
):
    """Red: the refusal points at the base name and the reasoning effort."""
    text = _refusal_text(
        _confirm(tmp_path, monkeypatch, {**NEON, "model": "gemini-4-neon-high"}),
        "gemini-4-neon-high",
    )
    assert re.search(r"gemini-4-neon(?![\w.-])", text) and re.search(
        r"reasoning[-_ ]effort", text
    ), (
        "FAIL AC-5: an effort-suffixed name needs a hint to pass the base name "
        f"gemini-4-neon with its reasoning effort: {text}"
    )


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_ac5_confirmation_accepts_the_domain_efforts(tmp_path, monkeypatch, effort):
    """Red: the lane is outside the reviewer CLI domain today."""
    result = _confirm(tmp_path, monkeypatch, {**NEON, "reasoning_effort": effort})
    assert result.ok, f"FAIL AC-5: effort {effort} refused: {result.to_envelope()}"


def test_ac5_confirmation_refuses_max_effort(tmp_path, monkeypatch):
    """Red: today the lane is refused before its effort is read."""
    text = _refusal_text(
        _confirm(tmp_path, monkeypatch, {**NEON, "reasoning_effort": "max"}), "max"
    )
    assert "effort" in text.lower(), (
        f"FAIL AC-5: max must be refused as a reasoning effort: {text}"
    )


@pytest.mark.parametrize(
    "reviewer", [CODEX_GEMINI, CLAUDE_GEMINI], ids=["codex", "claude"]
)
def test_ac5_confirmation_refuses_gemini_models_on_other_lanes(
    tmp_path, monkeypatch, reviewer
):
    """Red: today a gemini- model confirms under codex or claude."""
    text = _refusal_text(_confirm(tmp_path, monkeypatch, reviewer), reviewer)
    assert "gemini-4-neon" in text, f"FAIL AC-5: refusal must name the model: {text}"


@pytest.mark.parametrize("held", ["history", "current"])
def test_ac5_survivor_stored_codex_gemini_revision_still_loads(
    tmp_path, monkeypatch, capsys, held
):
    """Survivor: a stored policy revision holding codex + gemini-4-neon, in
    history or as the current revision, decodes and the feature loads."""
    from heddle.cli import main
    from heddle.contracts.feature_policy import Reviewer
    from heddle.kernel.state import parse_state_document
    from tests.operational_model_helpers import FEATURE, document, fresh_host
    from tests.tiering_helpers import wire_policy

    legacy = wire_policy(
        overrides={"spec-review": entry("spec-review", primary=CODEX_GEMINI)}
    )
    if held == "history":
        current = wire_policy(revision=2)
        value = document(feature_policy=current)
        value["policy_history"] = [legacy, current]
    else:
        value = document(feature_policy=legacy)
    value.pop("tier", None)
    parsed = parse_state_document(value, source=Path("stored.yaml"))
    assert parsed.policy_history[0].entries[0].primary == Reviewer(**CODEX_GEMINI)

    host, _state = fresh_host(tmp_path, state=value)
    monkeypatch.chdir(host)
    capsys.readouterr()
    code = main(["status", "--feature", FEATURE, "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0 and envelope["ok"] is True, envelope


@pytest.mark.parametrize("lane", ["codex", "antigravity"])
def test_ac4_disabled_lane_slot_is_refused_before_execution(
    tmp_path, monkeypatch, capsys, lane
):
    """A slot on a lane whose `agents` key is false is refused at launch,
    without a provider call and without rewriting policy. The codex case is
    the survivor the new lane is pinned against; the antigravity case is red,
    because today the stored lane fails to decode instead."""
    import yaml

    from heddle.cli import main
    from heddle.gate.runner import GateRunner
    from tests.tiering_helpers import snapshot
    from tests.tiering_review_helpers import V7_FEATURE, current_host

    overrides = (
        {"spec-review": entry("spec-review", primary=NEON)}
        if lane == "antigravity"
        else {}
    )
    host, _path = current_host(tmp_path, monkeypatch, overrides=overrides)
    config_path = host / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["agents"] = {**config["agents"], lane: False}
    config_path.write_text(yaml.safe_dump(config))
    executed: list[str] = []

    def forbidden(_self, *_args, **_kwargs):
        executed.append(lane)
        pytest.fail(f"FAIL AC-4: the disabled {lane} slot reached execution")

    monkeypatch.setattr(GateRunner, "_execute", forbidden)
    before = snapshot(host)
    capsys.readouterr()
    code = main(["run-gate", "spec-review", "--feature", V7_FEATURE, "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code != 0 and not executed, envelope
    message = envelope["error"]["message"]
    assert f"host disables {lane};" in message, (
        f"FAIL AC-4: the {lane} slot must be refused because the host disables "
        f"it: {message}"
    )
    assert snapshot(host) == before
