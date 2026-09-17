"""Current review execution bindings; confirmed policy owns invocation choices."""

from __future__ import annotations

from collections.abc import Mapping

from heddle.contracts.gates import (
    FINDING_PRODUCING_GATES as FINDING_PRODUCING_GATES,
)
from heddle.contracts.gates import GATE_CATALOG
from heddle.contracts.gates import (
    VERDICT_PRODUCING_GATES as VERDICT_PRODUCING_GATES,
)
from heddle.gate.results import review_output_contract
from heddle.gate.types import GateLanePolicy, GateType

READ_ONLY = ("Read", "Grep", "Glob")
READ_ONLY_WITH_BASH = (*READ_ONLY, "Bash")
ALL_CLIS = ("claude", "codex")
CLAUDE_STANDARD_POLICY = GateLanePolicy(
    cli="claude",
    model="claude-opus-5",
    reasoning_effort="xhigh",
    sandbox="read-only-tools",
)
CODEX_SPEC_POLICY = GateLanePolicy(
    cli="codex",
    model="gpt-6-astra",
    reasoning_effort="high",
    sandbox="danger-full-access",
)
CODEX_POLICY = GateLanePolicy(
    cli="codex",
    model="gpt-5.6-sol",
    reasoning_effort="high",
    sandbox="danger-full-access",
)
CODEX_XHIGH_POLICY = GateLanePolicy(
    cli="codex",
    model="gpt-5.6-sol",
    reasoning_effort="xhigh",
    sandbox="danger-full-access",
)
DUAL_LANE_POLICIES = (CLAUDE_STANDARD_POLICY, CODEX_POLICY)
SPEC_LANE_POLICIES = (CLAUDE_STANDARD_POLICY, CODEX_SPEC_POLICY)
XHIGH_LANE_POLICIES = (CLAUDE_STANDARD_POLICY, CODEX_XHIGH_POLICY)

GATES: dict[str, GateType] = {
    "spec-review": GateType(
        name="spec-review",
        requires_spec=True,
        requires_plan=False,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=ALL_CLIS,
        lane_policies=SPEC_LANE_POLICIES,
        tool_permissions=READ_ONLY,
        output_contract=review_output_contract("spec-review"),
        prompt_template="prompts/spec-review.md",
        default_cli="codex",
    ),
    "plan-review": GateType(
        name="plan-review",
        requires_spec=True,
        requires_plan=True,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=ALL_CLIS,
        lane_policies=DUAL_LANE_POLICIES,
        tool_permissions=READ_ONLY,
        output_contract=review_output_contract("plan-review"),
        prompt_template="prompts/plan-review.md",
        default_cli="codex",
    ),
    "review-test-scaffolding": GateType(
        name="review-test-scaffolding",
        requires_spec=True,
        requires_plan=True,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=ALL_CLIS,
        lane_policies=XHIGH_LANE_POLICIES,
        tool_permissions=READ_ONLY,
        output_contract=review_output_contract("review-test-scaffolding"),
        prompt_template="prompts/review-test-scaffolding.md",
        context_builders=("test-scaffolding",),
        default_cli="codex",
    ),
    "milestone-review": GateType(
        name="milestone-review",
        source_selector="milestone-owns",
        requires_spec=True,
        requires_plan=True,
        requires_milestone=True,
        requires_diff=True,
        supported_clis=ALL_CLIS,
        lane_policies=DUAL_LANE_POLICIES,
        tool_permissions=READ_ONLY_WITH_BASH,
        output_contract=review_output_contract("milestone-review"),
        prompt_template="prompts/milestone-review.md",
        milestone_output_naming=True,
        context_builders=("milestone",),
        preflight_applicable=True,
        default_cli="codex",
    ),
    "peer-review-sequential": GateType(
        name="peer-review-sequential",
        source_selector="feature-owned-union",
        requires_spec=True,
        requires_plan=True,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=ALL_CLIS,
        lane_policies=XHIGH_LANE_POLICIES,
        tool_permissions=READ_ONLY,
        output_contract=review_output_contract("peer-review-sequential"),
        prompt_template="prompts/peer-review-sequential.md",
    ),
    "behavior-review": GateType(
        name="behavior-review",
        source_selector="feature-owned-union",
        requires_spec=True,
        requires_plan=True,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=ALL_CLIS,
        lane_policies=XHIGH_LANE_POLICIES,
        tool_permissions=READ_ONLY,
        output_contract=review_output_contract("behavior-review"),
        prompt_template="prompts/behavior-review.md",
    ),
    "complexity-review": GateType(
        name="complexity-review",
        source_selector="feature-owned-union",
        requires_spec=True,
        requires_plan=True,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=ALL_CLIS,
        lane_policies=DUAL_LANE_POLICIES,
        tool_permissions=READ_ONLY,
        output_contract=review_output_contract("complexity-review"),
        prompt_template="prompts/complexity-review.md",
        default_cli="codex",
    ),
    "robustness-analysis": GateType(
        name="robustness-analysis",
        source_selector="feature-owned-union",
        requires_spec=True,
        requires_plan=True,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=ALL_CLIS,
        lane_policies=XHIGH_LANE_POLICIES,
        tool_permissions=READ_ONLY,
        output_contract=review_output_contract("robustness-analysis"),
        prompt_template="prompts/robustness-analysis.md",
        default_cli="codex",
    ),
}


def validate_registry(gates: Mapping[str, GateType]) -> None:
    """Check execution bindings against current declarations."""
    if gates.keys() != GATE_CATALOG.keys():
        missing = sorted(GATE_CATALOG.keys() - gates.keys())
        extra = sorted(gates.keys() - GATE_CATALOG.keys())
        raise ValueError(
            f"gate registry/catalog mismatch: missing={missing}, extra={extra}"
        )
    for name, gate in gates.items():
        if gate.name != name:
            raise ValueError(f"{name}: registry differs from gate declaration")


validate_registry(GATES)
