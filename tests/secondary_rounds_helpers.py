"""Synthetic fixtures for secondary-reviewer-rounds-v1; no local run evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace

import yaml

ASTRA_HIGH = {"cli": "codex", "model": "gpt-6-astra", "reasoning_effort": "high"}
FABLE_HIGH = {"cli": "claude", "model": "claude-fable-5-1", "reasoning_effort": "high"}
SOL_HIGH = {"cli": "codex", "model": "gpt-6-sol", "reasoning_effort": "high"}
SOL_XHIGH = {**SOL_HIGH, "reasoning_effort": "xhigh"}
OPUS_XHIGH = {"cli": "claude", "model": "claude-opus-5-5", "reasoning_effort": "xhigh"}


def windowed(row, window):
    """Set an explicit window on a typed entry, failing clearly before m1."""
    assert "secondary_rounds" in {item.name for item in fields(row)}, (
        "FAIL secondary-rounds: policy entries have no secondary_rounds window"
    )
    return replace(row, secondary_rounds=window)


def _old_entry(role, mode, limit, minimum, primary, secondary=None):
    # The exact pre-change wire entry: every declared key, no window.
    return {
        "role": role,
        "scope": "milestone" if role == "milestone-review" else "feature",
        "mode": mode,
        "limit": limit,
        "minimum_rounds": minimum,
        "primary": dict(primary),
        "secondary": None if secondary is None else dict(secondary),
        "trigger": None,
    }


def old_shape_policy(*, revision=1, spec_limit=3, spec_minimum=1):
    """A literal pre-change policy: spec review dual, no secondary_rounds key."""
    return deepcopy(
        {
            "schema": "heddle.feature-policy/v1",
            "revision": revision,
            "axes": {
                "scope": "small",
                "complexity": "low",
                "testability": "full",
                "scope_rationale": "Localized responsibility unless this case "
                "states otherwise",
                "complexity_rationale": "Case declares the consequential uncertainty",
                "testability_rationale": "Case declares the feasible executable "
                "evidence",
            },
            "approval": "User explicitly confirmed this complete fixture matrix",
            "entries": [
                _old_entry(
                    "spec-review",
                    "upper-limit",
                    spec_limit,
                    spec_minimum,
                    ASTRA_HIGH,
                    FABLE_HIGH,
                ),
                _old_entry("plan-review", "upper-limit", 2, 1, SOL_HIGH),
                _old_entry("review-test-scaffolding", "upper-limit", 2, 1, SOL_XHIGH),
                _old_entry("milestone-review", "upper-limit", 2, 1, SOL_HIGH),
                _old_entry("peer-review-sequential", "upper-limit", 2, 1, OPUS_XHIGH),
                _old_entry("behavior-review", "off", None, 0, OPUS_XHIGH),
                _old_entry("complexity-review", "off", None, 0, SOL_HIGH),
                _old_entry("robustness-analysis", "off", None, 0, SOL_XHIGH),
            ],
        }
    )


def assert_old_shape(document):
    """The fixture proves its own precondition before any action."""
    for policy in (document["feature_policy"], *document.get("policy_history", [])):
        for row in policy["entries"]:
            assert "secondary_rounds" not in row, (
                f"FAIL AC-2: pre-change fixture already carries a window: {row}"
            )


def install_old_shape(path, **kwargs):
    """Replace the host's policy with the literal pre-change document."""
    value = yaml.safe_load(path.read_text())
    assert not value["review_assignments"]["assignments"], "install before review"
    value["feature_policy"] = old_shape_policy(**kwargs)
    value.pop("policy_history", None)
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    assert_old_shape(value)
    return value["feature_policy"]


def assignment_rounds(path, role="spec-review", scope="feature"):
    value = yaml.safe_load(path.read_text())
    return next(
        row["rounds"]
        for row in value["review_assignments"]["assignments"]
        if (row["role"], row["scope"]) == (role, scope)
    )


def slot_names(round_row):
    return [slot["name"] for slot in round_row["slots"]]


TARGET_MARKER = "Preserve originating run IDs and finding IDs: "
PRE_CHANGE_ROUND_KEYS = {
    "number",
    "policy_revision",
    "purpose",
    "reason",
    "scope_identity",
    "before_open",
    "slots",
    "scope_change",
}


def delivered_targets(text):
    """The verification targets a reviewer invocation was told to account for."""
    import json

    index = text.find(TARGET_MARKER)
    assert index >= 0, "FAIL AC-4: the invocation carries no verification targets"
    targets, _end = json.JSONDecoder().raw_decode(text[index + len(TARGET_MARKER) :])
    return [tuple(target) for target in targets]


def assert_pre_change_ledger(path):
    """Recorded rounds and slots keep exactly the pre-change record shape."""
    value = yaml.safe_load(path.read_text())
    for assignment in value["review_assignments"]["assignments"]:
        for row in assignment["rounds"]:
            assert set(row) == PRE_CHANGE_ROUND_KEYS, f"FAIL AC-2: round shape {row}"
            for slot in row["slots"]:
                assert set(slot) == {"name", "reviewer"}, f"FAIL AC-2: slot {slot}"
