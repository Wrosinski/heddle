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


LEGACY_PAIR = {"cli": "codex", "model": "gemini-4-neon", "reasoning_effort": "high"}
# The routed standalone slot command names the frozen tuple itself.
LEGACY_ARGS = (
    "--cli",
    "codex",
    "--model",
    "gemini-4-neon",
    "--reasoning-effort",
    "high",
)
LEGACY_REFUSAL = "codex cannot run the Gemini model gemini-4-neon"


def _legacy_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, secondary=None):
    """A host whose spec-review primary was confirmed as codex with
    gemini-4-neon before the naming rule existed."""
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import current_host

    return current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": entry(
                "spec-review", primary=LEGACY_PAIR, secondary=secondary, limit=3
            )
        },
    )


def _record_before_the_rule(monkeypatch, run_cli, path: Path, artifact, *args) -> str:
    """Launch the legacy slot as the build before the rule did; the run is
    recorded as it ran. Returns its run id."""
    from heddle.runtime import gate_run
    from tests.tiering_review_helpers import gate_command, provider_transport, runs

    provider_transport(monkeypatch, artifact)
    with monkeypatch.context() as earlier_build:
        earlier_build.setattr(
            gate_run, "model_selection_refusal", lambda *_args, **_kwargs: None
        )
        code, result = gate_command(run_cli, "run-gate", "spec-review", *args)
    assert code != 3 and result["ok"], result
    run = runs(path)[-1]
    assert run["execution_config"]["model"] == "gemini-4-neon"
    return run["run_id"]


def test_ac5_recorded_gemini_review_on_codex_qualifies_while_a_new_launch_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli
) -> None:
    """A review recorded on codex with a gemini- model before the naming rule
    existed still qualifies as review evidence, because recomputing its basis
    resolves the recorded execution without the rule. A fresh launch of the
    same pair for the next round is refused before any provider call."""
    from tests.structured_review_helpers import disposition as reviewer_disposition
    from tests.structured_review_helpers import finding, finding_ref
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        gate_command,
        open_round,
        provider_transport,
        review_content,
    )

    _host_root, path = _legacy_host(tmp_path, monkeypatch)

    def record(artifact) -> str:
        return _record_before_the_rule(monkeypatch, run_cli, path, artifact)

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
    assert refused["error"]["message"] == LEGACY_REFUSAL, refused
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


@pytest.mark.parametrize("form", ["plain", "routed"])
def test_ac5_exact_replay_of_a_recorded_legacy_review_returns_the_cached_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli, form
) -> None:
    """An exact replay is not a provider call: replaying a review recorded on
    codex with a gemini- model returns the cached success, whether the command
    names no reviewer or the routed slot tuple."""
    from tests.tiering_review_helpers import (
        gate_command,
        provider_transport,
        review_content,
    )

    _host_root, path = _legacy_host(tmp_path, monkeypatch)
    args = LEGACY_ARGS if form == "routed" else ()
    origin = _record_before_the_rule(
        monkeypatch, run_cli, path, review_content(), *args
    )
    before = path.read_bytes()
    calls = provider_transport(monkeypatch, review_content())
    code, replay = gate_command(run_cli, "run-gate", "spec-review", *args)
    assert code == 0 and replay["ok"] and replay["data"]["cached"] is True, (
        f"FAIL AC-5: the exact replay of a recorded legacy review must return "
        f"its cached success: {replay}"
    )
    assert replay["data"]["run_id"] == origin
    assert calls == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("form", ["plain", "routed"])
def test_ac5_show_prompt_previews_a_legacy_frozen_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli, form
) -> None:
    """A preview is not a provider call: show-prompt resolves the frozen codex
    and gemini-4-neon slot as confirmed."""
    from tests.tiering_review_helpers import gate_command

    _host_root, path = _legacy_host(tmp_path, monkeypatch)
    before = path.read_bytes()
    args = LEGACY_ARGS if form == "routed" else ()
    code, shown = gate_command(run_cli, "show-prompt", "spec-review", *args)
    assert code == 0 and shown["ok"], (
        f"FAIL AC-5: show-prompt must preview the legacy frozen slot: {shown}"
    )
    assert path.read_bytes() == before


def test_ac5_fresh_launch_of_a_legacy_frozen_slot_is_refused_before_any_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli
) -> None:
    """A cache miss on the legacy slot would call the provider, so the naming
    rule refuses it first, leaving no attempt and no ledger row."""
    from tests.tiering_review_helpers import (
        gate_command,
        provider_transport,
        review_content,
    )

    _host_root, path = _legacy_host(tmp_path, monkeypatch)
    before = path.read_bytes()
    calls = provider_transport(monkeypatch, review_content())
    code, refused = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not refused["ok"], (
        f"FAIL AC-5: a fresh launch of codex with gemini-4-neon must be refused: "
        f"{refused}"
    )
    error = refused["error"]
    assert (error["code"], error["message"]) == ("workspace-invalid", LEGACY_REFUSAL)
    assert "antigravity lane" in error["hint"], error
    assert calls == []
    assert path.read_bytes() == before


def test_ac5_run_gates_with_a_legacy_member_on_a_cache_miss_is_refused_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each batch member is prepared before any provider call; the legacy
    member's refusal refuses the batch, naming that member."""
    import multiprocessing

    from tests.concurrent_review_helpers import execute_batch, install_counting_engine
    from tests.tiering_helpers import FABLE

    _host_root, path = _legacy_host(tmp_path, monkeypatch, secondary=FABLE)
    before = path.read_bytes()
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        result = execute_batch()
        started = list(calls)
    assert not result.ok, (
        "FAIL AC-5: a batch holding a fresh codex gemini-4-neon member must be "
        f"refused: {result.to_envelope()}"
    )
    assert result.error is not None
    assert result.error.message == LEGACY_REFUSAL, result.to_envelope()
    assert result.error.details["selected_member"] == {
        "gate": "spec-review",
        "scope": "feature",
        "reviewer_slot": "primary",
    }
    assert started == []
    assert path.read_bytes() == before
