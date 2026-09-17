"""Recommendation prose is not a machine option selector; history stays strict."""

import json
from dataclasses import asdict

import pytest

from tests.structured_review_helpers import finding
from tests.tiering_helpers import ROLES
from tests.tiering_review_helpers import review_content

PROSE = "Prefer configuration, keeping the current public boundary; see the trade-offs."


def report():
    row = finding("BR-I1", classification="report")
    row["decision"]["recommended"] = PROSE
    return row


def decode(payload, contract):
    from heddle.gate.results import decode_review_content
    from heddle.gate.validation import validate_review_content

    result = decode_review_content(json.dumps(payload).encode(), contract)
    validate_review_content(result, version=contract.version)
    return result


def invocation(contract):
    return {
        "feature": "example",
        "gate": contract.role,
        "scope": "feature",
        "execution": {
            "cli": "claude",
            "model": "fixture-reviewer",
            "reasoning_effort": "high",
            "sandbox": "isolated fixture",
        },
        "review_policy_id": None,
        "prompt_version": "authored-test",
        "effective_prompt_sha256": "a" * 64,
        "review_basis_hash": "b" * 64,
        "input_hash": "c" * 64,
        "output_contract_version": contract.version,
        "output_contract_sha256": contract.schema_sha256,
        "assignment_id": "assignment-1",
        "round_number": 1,
        "reviewer_slot": "primary",
    }


@pytest.mark.parametrize("role", ROLES)
def test_current_recommendation_prose_survives_all_role_codecs(role):
    from heddle.gate.results import review_output_contract

    contract = review_output_contract(role)
    assert contract.version == "heddle.review-content/v3"
    payload = review_content(role, findings=[report()])
    result = decode(payload, contract)
    # Native tuples are immutable; their JSON projection preserves all text.
    assert json.loads(json.dumps(asdict(result))) == payload
    assert result.findings[0].decision.recommended == PROSE


@pytest.mark.parametrize(
    "fault",
    [
        "missing-decision",
        "one-option",
        "duplicate-option",
        "blank-recommendation",
        "unknown-origin",
        "duplicate-finding",
    ],
)
def test_relaxed_recommendation_preserves_other_review_invariants(fault):
    from heddle.gate.results import review_output_contract

    payload = review_content("behavior-review", findings=[report()])
    decode(payload, review_output_contract("behavior-review"))
    decision = payload["findings"][0]["decision"]
    if fault == "missing-decision":
        payload["findings"][0]["decision"] = None
    elif fault == "one-option":
        decision["options"] = decision["options"][:1]
    elif fault == "duplicate-option":
        decision["options"] = [decision["options"][0]] * 2
    elif fault == "blank-recommendation":
        decision["recommended"] = "   "
    elif fault == "unknown-origin":
        payload["enforcement_suggestions"] = [
            {
                "finding_id": "unknown",
                "rule": "Keep the property",
                "mechanism": "test",
                "location": None,
                "rationale": "Fixture",
                "evidence": payload["findings"][0]["evidence"],
            }
        ]
    else:
        payload["findings"] *= 2
    with pytest.raises(ValueError):
        decode(payload, review_output_contract("behavior-review"))
