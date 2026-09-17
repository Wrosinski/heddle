"""Current reviewer instructions align representation and evidence limits."""

from pathlib import Path

import pytest

from heddle.gate.prompt import expand_partials
from tests.tiering_helpers import ROLES

RESOURCES = Path(__file__).resolve().parents[2] / "heddle/resources"
PROMPTS = RESOURCES / "prompts"


@pytest.mark.parametrize("role", ROLES)
def test_current_reviewer_receives_one_evidence_bounded_remedy_rule(role):
    text = expand_partials((PROMPTS / f"{role}.md").read_text(), PROMPTS / "_partials")
    flat = " ".join(text.split())
    assert "a preferred resolution" in flat
    assert "exact recommended option" not in flat
    assert (
        flat.count(
            "Recommended repairs, decision alternatives, and enforcement suggestions "
            "must preserve every applicable commitment"
        )
        == 1
    )
    assert "including named failure and concurrency guarantees" in flat
    assert "otherwise state the required properties and unresolved conditions" in flat
    assert "without excluding valid ways to satisfy the contract" in flat
    assert "retain the supported finding and omit the generalized rule" in flat
    assert "An empty enforcement suggestions array is valid" in flat
    assert "[partial-" not in text


def test_shared_finding_example_does_not_demand_an_unproved_mechanism():
    flat = " ".join((RESOURCES / "prompt-authoring-standards.md").read_text().split())
    assert "without asking for clarification" not in flat
    assert "do not present an uncertain remedy as implementation-ready" in flat
    assert (
        "`decision.recommended` describes the preferred resolution in ordinary prose"
        in flat
    )
    assert "not an option-name selector" in flat
