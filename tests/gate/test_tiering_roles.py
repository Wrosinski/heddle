"""AC-5/6 role catalog and output contract discriminators, no providers."""

import json

import pytest

from tests.tiering_helpers import ASTRA, RETIRED, ROLES, SOL, SOL_XHIGH
from tests.tiering_review_helpers import ROLE_DIMENSIONS, review_content


def test_ac5_launch_catalog_contains_exactly_the_eight_selected_role_kinds():
    from heddle.contracts.gates import GATE_CATALOG
    from heddle.gate.registry import GATES

    assert set(GATE_CATALOG) == set(ROLES), "FAIL AC-5: retired roles still launchable"
    assert set(GATES) == set(ROLES)


def test_ac5_codex_execution_matrix_keeps_astra_only_for_spec_review():
    from heddle.gate.cli import CLI_FALLBACKS, resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.types import GateInvocationOverrides

    expected = {
        "spec-review": ASTRA,
        "plan-review": SOL,
        "review-test-scaffolding": SOL_XHIGH,
        "milestone-review": SOL,
        "peer-review-sequential": SOL_XHIGH,
        "behavior-review": SOL_XHIGH,
        "complexity-review": SOL,
        "robustness-analysis": SOL_XHIGH,
    }
    for role, reviewer in expected.items():
        execution = resolve_gate_execution(
            GATES[role], GateInvocationOverrides(cli="codex")
        ).exec_config
        assert {
            "cli": execution.cli,
            "model": execution.model,
            "reasoning_effort": execution.reasoning_effort,
        } == reviewer
    assert CLI_FALLBACKS["codex"].model == SOL["model"]
    assert CLI_FALLBACKS["codex"].reasoning_effort == SOL["reasoning_effort"]


@pytest.mark.parametrize("role", RETIRED)
def test_ac5_retired_roles_have_no_current_output_contract(role):
    from heddle.gate.results import review_output_contract

    with pytest.raises(ValueError, match="retired|unknown|historical"):
        review_output_contract(role)


@pytest.mark.parametrize("role", ROLES)
def test_ac6_current_roles_use_v3_content_contract_and_no_synthesis(role):
    from heddle.gate.results import review_output_contract

    try:
        contract = review_output_contract(role)
    except ValueError as error:
        pytest.fail(f"FAIL AC-6: current role {role} has no output contract: {error}")
    assert contract.version == "heddle.review-content/v3"
    schema = json.loads(contract.schema_json)
    assert "synthesis" not in schema["properties"]
    assert schema["properties"]["role"]["enum"] == [role]


@pytest.mark.parametrize("role", tuple(ROLE_DIMENSIONS))
def test_ac6_new_roles_decode_their_own_coverage_dimensions(role):
    from heddle.gate.results import decode_review_content, review_output_contract

    try:
        contract = review_output_contract(role)
    except ValueError as error:
        pytest.fail(f"FAIL AC-6: {role} is not implemented: {error}")
    result = decode_review_content(json.dumps(review_content(role)).encode(), contract)
    assert result.role == role
    assert {row.id for row in result.details.dimensions} == set(ROLE_DIMENSIONS[role])
    assert [row.ac_id for row in result.details.ac_coverage] == ["AC-1"]


@pytest.mark.parametrize("role", tuple(ROLE_DIMENSIONS))
def test_ac6_cross_role_dimension_and_prose_only_coverage_refuse(role):
    from heddle.gate.results import decode_review_content, review_output_contract

    try:
        contract = review_output_contract(role)
    except ValueError as error:
        pytest.fail(f"FAIL AC-6: {role} is not implemented: {error}")
    wrong = review_content(role)
    wrong["details"]["dimensions"][0]["id"] = "made-up-role-dimension"
    with pytest.raises(ValueError):
        decode_review_content(json.dumps(wrong).encode(), contract)
    prose = review_content(role)
    prose["details"]["ac_coverage"] = "All tests passed"
    with pytest.raises(ValueError):
        decode_review_content(json.dumps(prose).encode(), contract)


def invocation(contract, *, current):
    row = {
        "feature": "example",
        "gate": contract.role,
        "scope": "feature",
        "execution": {
            "cli": "codex",
            "model": "gpt-6-astra",
            "reasoning_effort": "high",
            "sandbox": "danger-full-access",
        },
        "review_policy_id": None,
        "prompt_version": "authored-test",
        "effective_prompt_sha256": "a" * 64,
        "review_basis_hash": "b" * 64,
        "input_hash": "c" * 64,
        "output_contract_version": contract.version,
        "output_contract_sha256": contract.schema_sha256,
    }
    if current:
        row.update(
            assignment_id="assignment-1", round_number=1, reviewer_slot="primary"
        )
    return row


@pytest.mark.parametrize("fault", [None, "missing-slot", "zero-round", "old-schema"])
def test_ac6_v3_requires_runtime_assignment_round_and_slot(fault):
    from heddle.gate.results import decode_review_result, review_output_contract

    contract = review_output_contract("behavior-review")
    document = {
        "schema": "heddle.review-result/v3",
        "invocation": invocation(contract, current=True),
        "content": review_content("behavior-review"),
    }
    if fault == "missing-slot":
        document["invocation"].pop("reviewer_slot")
    elif fault == "zero-round":
        document["invocation"]["round_number"] = 0
    elif fault == "old-schema":
        document["schema"] = "heddle.review-result/v2"
    if fault is None:
        result = decode_review_result(json.dumps(document).encode())
        assert result.invocation.assignment_id == "assignment-1"
        assert result.invocation.round_number == 1
        assert result.invocation.reviewer_slot == "primary"
    else:
        with pytest.raises(ValueError):
            decode_review_result(json.dumps(document).encode())
