"""Gate execution policy resolution and immutable lane contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from heddle.gate import cli as gate_cli
from heddle.gate.io import GatePaths
from heddle.gate.registry import GATES
from heddle.gate.types import (
    GateExecutionConfig,
    GateInvocationOverrides,
    GateLanePolicy,
)
from heddle.kernel.project_config import KernelError

ORACLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures/gate-execution-policy/pre-activation-v1.json"
)
NON_SYNTHESIS_GATES = (
    "self-review",
    "code-quality",
    "spec-review",
    "plan-review",
    "robustness-analysis",
    "review-correctness",
    "review-architecture",
    "review-robustness",
    "review-tests",
    "review-test-scaffolding",
    "peer-review-sequential",
)
MILESTONE_GATES = ("code-quality", "self-review")
OPUS_SYNTHESIS_GATES = (
    "review-synthesis",
    "plan-review-synthesis",
    "review-test-scaffolding-synthesis",
)
FABLE_SYNTHESIS_GATES = ("spec-review-synthesis",)


def _selected_matrix() -> dict[tuple[str, str], GateExecutionConfig]:
    expected: dict[tuple[str, str], GateExecutionConfig] = {}
    for gate in NON_SYNTHESIS_GATES:
        expected[(gate, "claude")] = GateExecutionConfig(
            "claude", "claude-opus-5-5", "xhigh", "read-only-tools"
        )
        expected[(gate, "codex")] = GateExecutionConfig(
            "codex", "gpt-6-astra", "high", "danger-full-access"
        )
    for gate in MILESTONE_GATES:
        # Owner ruling 2026-09-09: milestone gates run Codex on gpt-5.6-sol/xhigh.
        expected[(gate, "codex")] = GateExecutionConfig(
            "codex", "gpt-5.6-sol", "xhigh", "danger-full-access"
        )
    for gate in OPUS_SYNTHESIS_GATES:
        expected[(gate, "claude")] = GateExecutionConfig(
            "claude", "claude-opus-5-5", "high", "read-only-tools"
        )
    for gate in FABLE_SYNTHESIS_GATES:
        expected[(gate, "claude")] = GateExecutionConfig(
            "claude", "claude-fable-5-1", "xhigh", "read-only-tools"
        )
    return expected


def _resolve(gate: str, **overrides: str):
    return gate_cli.resolve_gate_execution(
        GATES[gate], GateInvocationOverrides(**overrides)
    )


def _paths(tmp_path: Path) -> GatePaths:
    temp = tmp_path / "tmp"
    temp.mkdir(parents=True)
    prompt = temp / "prompt.md"
    prompt.write_text("review", encoding="utf-8")
    return GatePaths(
        output=tmp_path / "out.md",
        log=tmp_path / "out.log",
        summary=tmp_path / "summary.json",
        prompt=prompt,
        raw_out=temp / "raw.out",
        last_message=temp / "last-message.txt",
        json_message=temp / "message.json",
        filtered_jsonl=temp / "filtered.jsonl",
        command_output=temp / "command-output.txt",
        temp_dir=temp,
    )


def test_ac1_pre_activation_oracle_remains_frozen_historical_evidence() -> None:
    """The public tree must not retain the superseded pre-activation snapshot."""
    assert not ORACLE_PATH.exists()


def test_ac1_lane_policy_and_resolved_invocation_are_immutable() -> None:
    policy = GateLanePolicy(
        cli="claude",
        model="fixture-model",
        reasoning_effort="high",
        sandbox="read-only-tools",
    )
    with pytest.raises(FrozenInstanceError):
        policy.model = "changed"  # type: ignore[misc]
    invocation = _resolve("spec-review", model="fixture-model")
    with pytest.raises(FrozenInstanceError):
        invocation.explicit_axes = frozenset()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("gate", "model", "expected_effort"),
    [
        ("plan-review", "claude-opus-5-5", "xhigh"),
    ],
)
def test_ac2_model_override_preserves_lane_effort(
    gate: str, model: str, expected_effort: str
) -> None:
    invocation = _resolve(gate, cli="claude", model=model)
    assert invocation.exec_config == GateExecutionConfig(
        "claude", model, expected_effort, "read-only-tools"
    )
    assert invocation.explicit_axes == frozenset({"cli", "model"})


@pytest.mark.parametrize(
    ("axis", "overrides"),
    [
        ("cli", {"cli": ""}),
        ("model", {"cli": "claude", "model": ""}),
        ("reasoning_effort", {"cli": "claude", "reasoning_effort": ""}),
    ],
)
def test_ac2_explicit_empty_override_never_falls_back(
    axis: str, overrides: dict[str, str]
) -> None:
    with pytest.raises(KernelError) as caught:
        _resolve("spec-review", **overrides)
    assert caught.value.code == "usage", f"explicit empty {axis} selected a default"


def test_ac2_missing_lane_axes_use_cli_fallback_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = GateExecutionConfig("claude", "fallback-model", "low", "read-only-tools")
    monkeypatch.setattr(gate_cli, "CLI_FALLBACKS", {"claude": fallback})
    gate = replace(
        GATES["spec-review"],
        supported_clis=("claude",),
        default_cli="claude",
        lane_policies=(GateLanePolicy(cli="claude"),),
    )
    invocation = gate_cli.resolve_gate_execution(
        gate, GateInvocationOverrides(cli="claude")
    )
    assert invocation.exec_config == fallback
    assert invocation.explicit_axes == frozenset({"cli"})


@pytest.mark.parametrize("source", ("lane", "fallback"))
def test_ac2_invalid_sandbox_fails_before_execution(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = GateExecutionConfig("claude", "fixture-model", "high", "read-only")
    if source == "fallback":
        monkeypatch.setattr(gate_cli, "CLI_FALLBACKS", {"claude": invalid})
        lane_policies = (GateLanePolicy(cli="claude"),)
    else:
        lane_policies = (
            GateLanePolicy(
                cli="claude",
                model="fixture-model",
                reasoning_effort="high",
                sandbox="read-only",
            ),
        )
    gate = replace(
        GATES["spec-review"],
        supported_clis=("claude",),
        default_cli="claude",
        lane_policies=lane_policies,
    )

    with pytest.raises(KernelError) as caught:
        gate_cli.resolve_gate_execution(gate, GateInvocationOverrides(cli="claude"))

    assert caught.value.code == "workspace-invalid"
    assert caught.value.message == (
        "gate sandbox must be one of read-only-tools, danger-full-access"
    )
    assert caught.value.hint == (
        "fix the registered lane or provider fallback before running the gate"
    )


@pytest.mark.parametrize(
    ("axis", "policy"),
    (
        (
            "model",
            GateLanePolicy(
                cli="claude",
                model=" malformed-model",
                reasoning_effort="high",
                sandbox="read-only-tools",
            ),
        ),
        (
            "reasoning_effort",
            GateLanePolicy(
                cli="claude",
                model="fixture-model",
                reasoning_effort="maximum",
                sandbox="read-only-tools",
            ),
        ),
    ),
)
def test_ac2_invalid_registry_axes_are_workspace_defects(
    axis: str, policy: GateLanePolicy
) -> None:
    gate = replace(
        GATES["spec-review"],
        supported_clis=("claude",),
        default_cli="claude",
        lane_policies=(policy,),
    )

    with pytest.raises(KernelError) as caught:
        gate_cli.resolve_gate_execution(gate, GateInvocationOverrides())

    assert caught.value.code == "workspace-invalid", axis
    assert "registered gate spec-review/claude" in caught.value.message
    assert "registered lane or provider fallback" in caught.value.hint


def test_ac2_invalid_registered_default_cli_is_a_workspace_defect() -> None:
    gate = replace(
        GATES["spec-review"],
        supported_clis=("claude",),
        default_cli="codex",
    )

    with pytest.raises(KernelError) as caught:
        gate_cli.resolve_gate_execution(gate, GateInvocationOverrides())

    assert caught.value.code == "workspace-invalid"
    assert caught.value.message == "spec-review has invalid default CLI codex"


def test_ac1_no_parallel_model_or_effort_authority_survives() -> None:
    assert not hasattr(gate_cli, "_FABLE_GATES")
    assert not hasattr(gate_cli, "FABLE_MODEL")
    assert not hasattr(gate_cli, "DEFAULT_CLAUDE_GATE_MODEL")
    assert not hasattr(gate_cli, "DEFAULT_CODEX_GATE_MODEL")
