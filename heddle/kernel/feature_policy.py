"""One pure resolver for recommendations, confirmed choices and call budgets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace
from typing import Any

from heddle.contracts.feature_policy import (
    COMPLEXITIES,
    CONFIRMED_POLICY_FIELDS,
    DOCUMENT_ROLES,
    FEATURE_AXES_FIELDS,
    GATE_POLICY_FIELDS,
    GATE_POLICY_OPTIONAL_FIELDS,
    MODES,
    POLICY_SCHEMA,
    REVIEWER_FIELDS,
    ROLES,
    SCOPES,
    SECONDARY_ROUNDS_ALL,
    TESTABILITIES,
    TRIGGER_FIELDS,
    CallBudget,
    ConfirmedPolicy,
    EffectivePolicy,
    FeatureAxes,
    GatePolicy,
    Recommendation,
    Reviewer,
    SecondarySuggestion,
)
from heddle.contracts.gate_execution import VALID_GATE_CLIS, VALID_REASONING_EFFORTS


class ReviewAllowancePolicyError(ValueError):
    """The requested allowance belongs on the complete policy surface."""


def validate_host_review_selection(
    role: str,
    reviewers: tuple[Reviewer, ...],
    *,
    gates_enabled: tuple[str, ...] | None,
    agents: Mapping[str, bool],
) -> None:
    """Host exclusions constrain execution; they never rewrite feature policy."""
    if gates_enabled is not None and role not in gates_enabled:
        raise ValueError(
            f"host excludes {role}; explicitly amend the feature policy "
            "or host constraint before execution"
        )
    for reviewer in reviewers:
        if agents.get(reviewer.cli) is False:
            raise ValueError(
                f"host disables {reviewer.cli}; the confirmed provider "
                "cannot be substituted"
            )


def _fields(
    value: object,
    model: type,
    field_inventory: tuple[str, ...],
    *,
    optional: tuple[str, ...] = (),
) -> dict[str, Any]:
    names = set(field_inventory)
    assert names == {field.name for field in fields(model)}
    if (
        not isinstance(value, dict)
        or set(value) - names
        or names - set(optional) - set(value)
    ):
        raise ValueError(f"policy {model.__name__} needs exactly its declared fields")
    return value


def parse_policy(value: object) -> ConfirmedPolicy:
    """Decode a closed versioned wire policy using the same semantic resolver."""
    if not isinstance(value, dict) or value.get("schema") != POLICY_SCHEMA:
        raise ValueError(f"policy requires schema {POLICY_SCHEMA}")
    data = _fields(
        {key: item for key, item in value.items() if key != "schema"},
        ConfirmedPolicy,
        CONFIRMED_POLICY_FIELDS,
    )
    axes = parse_axes(data["axes"])
    if not isinstance(data["entries"], (list, tuple)):
        raise ValueError("policy entries must be a sequence")
    entries = []
    for entry in data["entries"]:
        row = dict(
            _fields(
                entry,
                GatePolicy,
                GATE_POLICY_FIELDS,
                optional=GATE_POLICY_OPTIONAL_FIELDS,
            )
        )
        row["primary"] = Reviewer(**_fields(row["primary"], Reviewer, REVIEWER_FIELDS))
        if row.get("secondary") is not None:
            row["secondary"] = Reviewer(
                **_fields(row["secondary"], Reviewer, REVIEWER_FIELDS)
            )
        if row.get("trigger") is not None and not isinstance(row["trigger"], dict):
            raise ValueError("policy trigger must be a mapping")
        entries.append(GatePolicy(**row))
    confirmed = ConfirmedPolicy(
        data["revision"], axes, data["approval"], tuple(entries)
    )
    effective_policy(confirmed)
    return confirmed


def parse_axes(value: object) -> FeatureAxes:
    axes = FeatureAxes(**_fields(value, FeatureAxes, FEATURE_AXES_FIELDS))
    validate_axes(axes)
    return axes


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"policy {label} must be nonempty, trimmed text")


def _integer(value: object, minimum: int, label: str) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"policy {label} must be an integer >= {minimum}")


def validate_axes(axes: FeatureAxes) -> None:
    for field, domain in (
        ("scope", SCOPES),
        ("complexity", COMPLEXITIES),
        ("testability", TESTABILITIES),
    ):
        if (
            not isinstance(getattr(axes, field), str)
            or getattr(axes, field) not in domain
        ):
            raise ValueError(f"policy {field} must be one of {sorted(domain)}")
        _text(getattr(axes, field + "_rationale"), field + " rationale")


def _reviewer(value: Reviewer) -> None:
    if not isinstance(value.cli, str) or value.cli not in VALID_GATE_CLIS:
        raise ValueError(
            f"policy reviewer CLI must be one of {sorted(VALID_GATE_CLIS)}"
        )
    _text(value.model, "reviewer model")
    if (
        not isinstance(value.reasoning_effort, str)
        or value.reasoning_effort not in VALID_REASONING_EFFORTS
    ):
        raise ValueError("policy reviewer reasoning effort is unsupported")
    if (value.cli == "codex" and value.model.startswith("claude-")) or (
        value.cli == "claude" and value.model.startswith("gpt-")
    ):
        raise ValueError("policy reviewer model belongs to a different CLI")


def validate_gate_policy(row: GatePolicy) -> None:
    if not isinstance(row.role, str) or row.role not in ROLES:
        raise ValueError(f"policy has unknown or retired gate role {row.role!r}")
    scope = "milestone" if row.role == "milestone-review" else "feature"
    if row.scope != scope:
        raise ValueError(f"policy gate {row.role} requires scope {scope}")
    if not isinstance(row.mode, str) or row.mode not in MODES:
        raise ValueError(f"policy gate {row.role} has invalid mode {row.mode!r}")
    _integer(row.minimum_rounds, 0 if row.mode == "off" else 1, "minimum rounds")
    if row.mode == "off":
        if row.limit is not None or row.minimum_rounds != 0:
            raise ValueError("off policy gate cannot have a limit or passes")
    elif row.mode == "convergence":
        if row.limit is not None:
            raise ValueError("convergence policy has no round limit")
    else:
        _integer(row.limit, 1, "round limit")
        assert row.limit is not None
        if row.minimum_rounds > row.limit:
            raise ValueError("policy minimum rounds exceed round limit")
    _reviewer(row.primary)
    if row.secondary is not None:
        _reviewer(row.secondary)
        if row.secondary.cli == row.primary.cli:
            raise ValueError("policy secondary needs an independent reviewer CLI")
    _secondary_rounds(row)
    if row.trigger is not None:
        if set(row.trigger) != set(TRIGGER_FIELDS):
            raise ValueError("policy trigger needs gate, gap and references")
        if row.role != "robustness-analysis" or row.trigger["gate"] != row.role:
            raise ValueError("policy trigger must name the robustness-analysis gate")
        _text(row.trigger["gap"], "trigger integration gap")
        references = row.trigger["references"]
        if not isinstance(references, (list, tuple)) or not references:
            raise ValueError("policy trigger references must be a nonempty sequence")
        for reference in references:
            _text(reference, "trigger reference")
    elif row.role == "robustness-analysis" and row.mode != "off":
        raise ValueError("enabled robustness-analysis needs its conditional trigger")


def _secondary_rounds(row: GatePolicy) -> None:
    window = row.secondary_rounds
    if window != SECONDARY_ROUNDS_ALL and (type(window) is not int or window < 1):
        raise ValueError(
            f"policy gate {row.role} secondary_rounds must be a positive integer "
            f"or {SECONDARY_ROUNDS_ALL!r}, got {window!r}"
        )
    if window != 1 and row.secondary is None:
        raise ValueError(
            f"policy gate {row.role} secondary_rounds {window} needs a secondary "
            "reviewer"
        )
    if (
        type(window) is int
        and row.mode == "upper-limit"
        and row.limit is not None
        and window > row.limit
    ):
        raise ValueError(
            f"policy gate {row.role} secondary_rounds {window} exceeds the round "
            f"limit {row.limit}"
        )


def secondary_serves(policy: GatePolicy, number: int) -> bool:
    """The one rule for whether this entry's secondary serves round ``number``."""
    window = policy.secondary_rounds
    return policy.secondary is not None and (
        window == SECONDARY_ROUNDS_ALL or (isinstance(window, int) and number <= window)
    )


def _base(axes: FeatureAxes) -> str:
    return (
        "full"
        if axes.scope == "large"
        or axes.testability == "none"
        or (axes.scope == "medium" and axes.complexity == "high")
        else "light"
    )


def recommend_policy(axes: FeatureAxes) -> Recommendation:
    validate_axes(axes)
    base = _base(axes)
    astra = Reviewer("codex", "gpt-6-astra", "xhigh")
    fable = Reviewer("claude", "claude-fable-5-1", "xhigh")
    opus = Reviewer("claude", "claude-opus-5-5", "xhigh")
    reviewers = {
        "spec-review": astra,
        "plan-review": astra,
        "review-test-scaffolding": opus,
        "milestone-review": opus,
        "peer-review-sequential": opus,
        "behavior-review": opus,
        "complexity-review": opus,
        "robustness-analysis": astra,
    }
    secondary_reviewers = {
        "spec-review": fable,
        "plan-review": fable,
        "behavior-review": astra,
        "robustness-analysis": fable,
    }
    disabled = (
        {"behavior-review", "complexity-review"}
        if base == "light"
        else {"peer-review-sequential"}
    )
    # Axes contain no named integration gap. Only an explicit confirmed
    # gap-bearing selection can activate the conditional robustness overlay.
    disabled.add("robustness-analysis")
    large_high = axes.scope == "large" and axes.complexity == "high"
    entries = []
    for role in ROLES:
        mode = (
            "off"
            if role in disabled
            else "convergence"
            if role == "review-test-scaffolding" and large_high
            else "upper-limit"
        )
        entries.append(
            GatePolicy(
                role=role,
                scope="milestone" if role == "milestone-review" else "feature",
                mode=mode,
                limit=(3 if role in DOCUMENT_ROLES and axes.complexity == "high" else 2)
                if mode == "upper-limit"
                else None,
                minimum_rounds=0 if mode == "off" else 1,
                primary=reviewers[role],
                secondary=secondary_reviewers.get(role),
                secondary_rounds=SECONDARY_ROUNDS_ALL if role in DOCUMENT_ROLES else 1,
            )
        )
    suggestions = (
        SecondarySuggestion("review-test-scaffolding", astra, SECONDARY_ROUNDS_ALL),
    )
    return Recommendation(base, tuple(entries), suggestions)


def effective_policy(policy: ConfirmedPolicy) -> EffectivePolicy:
    _integer(policy.revision, 1, "revision")
    _text(policy.approval, "approval")
    validate_axes(policy.axes)
    for row in policy.entries:
        validate_gate_policy(row)
    roles = [row.role for row in policy.entries]
    if len(roles) != len(ROLES) or set(roles) != set(ROLES):
        raise ValueError("policy must contain each live gate role exactly once")
    totals: dict[str, tuple[int, int | None]] = {}
    for scope in ("feature", "milestone"):
        minimum = 0
        maximum: int | None = 0
        for row in policy.entries:
            if row.scope != scope or row.mode == "off":
                continue
            minimum += _calls(row, row.minimum_rounds)
            maximum = (
                None
                if maximum is None or row.limit is None
                else maximum + _calls(row, row.limit)
            )
        totals[scope] = minimum, maximum
    return EffectivePolicy(
        policy.revision,
        policy.axes,
        policy.approval,
        _base(policy.axes),
        policy.entries,
        CallBudget(
            totals["feature"][0],
            totals["milestone"][0],
            totals["feature"][1],
            totals["milestone"][1],
        ),
        "robustness"
        if any(
            row.role == "robustness-analysis" and row.mode != "off"
            for row in policy.entries
        )
        else "peer-review",
    )


def _calls(row: GatePolicy, rounds: int) -> int:
    """Primary calls plus the secondary calls its window schedules."""
    return rounds + sum(secondary_serves(row, n) for n in range(1, rounds + 1))


def increase_review_allowance(
    policy: ConfirmedPolicy,
    *,
    role: str,
    limit: int,
    approval: str,
) -> tuple[ConfirmedPolicy, bool]:
    """Raise one upper-limit role, or recognize its exact idempotent replay."""
    effective_policy(policy)
    _integer(limit, 1, "review allowance limit")
    _text(approval, "review allowance approval")
    selection = next((row for row in policy.entries if row.role == role), None)
    if selection is None:
        raise ReviewAllowancePolicyError(f"review allowance has unknown role {role!r}")
    if selection.mode != "upper-limit" or selection.limit is None:
        raise ReviewAllowancePolicyError(
            f"review allowance requires an upper-limit role; {role} is "
            f"{selection.mode!r}"
        )
    if limit == selection.limit:
        if approval == policy.approval:
            return policy, True
        raise ReviewAllowancePolicyError(
            "review allowance target already exists with different approval text"
        )
    if limit < selection.limit:
        raise ReviewAllowancePolicyError(
            f"review allowance must increase the current limit {selection.limit}"
        )
    amended = replace(
        policy,
        revision=policy.revision + 1,
        approval=approval,
        entries=tuple(
            replace(row, limit=limit) if row.role == role else row
            for row in policy.entries
        ),
    )
    effective_policy(amended)
    return amended, False
