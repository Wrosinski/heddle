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
            "claude", "claude-opus-5-5", "xhigh", "auto"
        )
        expected[(gate, "codex")] = GateExecutionConfig(
            "codex", "gpt-6-astra", "high", "danger-full-access"
        )
    for gate in MILESTONE_GATES:
        # Owner rulings 2026-09-09 and 2026-09-22: milestone gates run Codex on
        # gpt-6-sol/xhigh.
        expected[(gate, "codex")] = GateExecutionConfig(
            "codex", "gpt-6-sol", "xhigh", "danger-full-access"
        )
    for gate in OPUS_SYNTHESIS_GATES:
        expected[(gate, "claude")] = GateExecutionConfig(
            "claude", "claude-opus-5-5", "high", "auto"
        )
    for gate in FABLE_SYNTHESIS_GATES:
        expected[(gate, "claude")] = GateExecutionConfig(
            "claude", "claude-fable-5-2", "xhigh", "auto"
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
        sandbox="auto",
    )
    with pytest.raises(FrozenInstanceError):
        policy.model = "changed"  # type: ignore[misc]
    invocation = _resolve("spec-review", model="fixture-model")
    with pytest.raises(FrozenInstanceError):
        invocation.explicit_axes = frozenset()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("gate", "model", "expected_effort"),
    [
        ("plan-review", "claude-opus-5-5", "high"),
    ],
)
def test_ac2_model_override_preserves_lane_effort(
    gate: str, model: str, expected_effort: str
) -> None:
    invocation = _resolve(gate, cli="claude", model=model)
    assert invocation.exec_config == GateExecutionConfig(
        "claude", model, expected_effort, "auto"
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
    fallback = GateExecutionConfig("claude", "fallback-model", "low", "auto")
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
        "gate sandbox must be auto for claude, danger-full-access for codex or "
        "dangerously-skip-permissions for antigravity"
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
                sandbox="auto",
            ),
        ),
        (
            "reasoning_effort",
            GateLanePolicy(
                cli="claude",
                model="fixture-model",
                reasoning_effort="maximum",
                sandbox="auto",
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


@pytest.mark.parametrize(
    ("cli", "sandbox"),
    (
        ("claude", "danger-full-access"),
        ("codex", "auto"),
        ("claude", "read-only-tools"),
    ),
)
def test_sandbox_domain_is_per_cli(cli: str, sandbox: str) -> None:
    gate = replace(
        GATES["spec-review"],
        supported_clis=(cli,),
        default_cli=cli,
        lane_policies=(
            GateLanePolicy(
                cli=cli,
                model="fixture-model",
                reasoning_effort="high",
                sandbox=sandbox,
            ),
        ),
    )

    with pytest.raises(KernelError) as caught:
        gate_cli.resolve_gate_execution(gate, GateInvocationOverrides())

    assert caught.value.code == "workspace-invalid"


def test_every_registered_lane_runs_commands() -> None:
    # Each lane's own permission value; a lane missing here fails, not defaults.
    expected_by_lane = {
        "claude": "auto",
        "codex": "danger-full-access",
        "antigravity": "dangerously-skip-permissions",
    }
    for name, gate in GATES.items():
        assert "Bash" in gate.tool_permissions, name
        for cli in gate.supported_clis:
            assert cli in expected_by_lane, f"no expected sandbox for {name}/{cli}"
            sandbox = _resolve(name, cli=cli).exec_config.sandbox
            assert sandbox == expected_by_lane[cli], (name, cli)


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


def test_ac5_recorded_gemini_review_on_codex_qualifies_while_a_new_launch_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli
) -> None:
    """A review recorded on codex with a gemini- model before the naming rule
    existed still qualifies as review evidence, because recomputing its basis
    resolves the recorded execution without the rule. Launching the same pair
    now is refused before any provider call."""
    from tests.structured_review_helpers import disposition as reviewer_disposition
    from tests.structured_review_helpers import finding, finding_ref
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import (
        current_host,
        dispose,
        disposition,
        gate_command,
        open_round,
        provider_transport,
        review_content,
        runs,
    )

    legacy_pair = {
        "cli": "codex",
        "model": "gemini-4-neon",
        "reasoning_effort": "high",
    }
    _host_root, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=legacy_pair, limit=3)},
    )

    def record(artifact) -> str:
        # The build before the rule: the launch records the pair as it ran.
        provider_transport(monkeypatch, artifact)
        with monkeypatch.context() as earlier_build:
            earlier_build.setattr(
                gate_cli, "model_selection_refusal", lambda *_args, **_kwargs: None
            )
            code, result = gate_command(run_cli, "run-gate", "spec-review")
        assert code != 3 and result["ok"], result
        run = runs(path)[-1]
        assert run["execution_config"]["model"] == "gemini-4-neon"
        return run["run_id"]

    origin = record(
        review_content(findings=[finding("SP-I1", classification="implement")])
    )
    recorded = dispose(
        path,
        [
            disposition(origin, status="retained", requires_inspection=True),
            disposition(origin, "@coverage", status="settled"),
        ],
    )
    assert recorded.ok, recorded.to_envelope()
    opened = open_round(path)
    assert opened.ok, opened.to_envelope()

    calls = provider_transport(monkeypatch, review_content())
    code, refused = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not refused["ok"], refused
    assert "gemini-4-neon" in refused["error"]["message"], refused
    assert calls == []

    second_round = review_content()
    second_round["prior_dispositions"] = [
        reviewer_disposition(finding_ref(origin, "SP-I1"), action="addressed"),
        reviewer_disposition(finding_ref(origin, "@coverage"), action="addressed"),
    ]
    second = record(second_round)
    recorded = dispose(
        path,
        [
            disposition(origin, "SP-I1", evidence_kind="review", review_run_id=second),
            disposition(second, "@coverage", status="settled"),
        ],
    )
    assert recorded.ok, recorded.to_envelope()
