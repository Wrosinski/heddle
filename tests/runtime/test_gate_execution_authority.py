"""Prepared gate authority, public overrides, and durable evidence contracts."""

from __future__ import annotations

import inspect
import shutil
from dataclasses import fields
from pathlib import Path

import pytest

from heddle.contracts.operations import CommandAction, RunGate, action_command
from heddle.contracts.schemas import GATE_RUN_OPTIONAL_KEYS
from heddle.gate import cli as gate_cli
from heddle.gate import types as gate_types
from heddle.gate.registry import GATES
from heddle.gate.types import (
    PreparedGateRun,
)
from heddle.runtime import gate_run, show_prompt
from heddle.runtime.contracts import COMMAND_SURFACE

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_STATE = (
    REPO_ROOT / "tests/fixtures/workspaces/golden/plans/nl-screening/state.yaml"
)


def _flags(command: str) -> set[str]:
    contract = next(item for item in COMMAND_SURFACE if item.name == command)
    return {flag.name for flag in contract.flags}


def _resolve(gate: str, **values: str):
    resolver = getattr(gate_cli, "resolve_gate_execution", None)
    override_type = getattr(gate_types, "GateInvocationOverrides", None)
    assert callable(resolver) and override_type is not None, (
        "FAIL AC-2: typed resolver is absent"
    )
    return resolver(GATES[gate], override_type(**values))


def test_ac4_run_and_show_manifests_expose_all_public_override_axes(run_cli) -> None:
    expected = {"--cli", "--model", "--reasoning-effort"}
    assert expected <= _flags("run-gate"), (
        "FAIL AC-4: run-gate override flags incomplete"
    )
    assert expected <= _flags("show-prompt"), (
        "FAIL AC-4: show-prompt override flags incomplete"
    )
    assert {"--model", "--reasoning-effort"}.isdisjoint(_flags("drive"))
    for action in ("run-gate", "show-prompt"):
        code, stdout, stderr = run_cli([action, "--help"])
        assert code == 0 and stderr == ""
        assert all(flag in stdout for flag in expected), (
            f"FAIL AC-4: {action} help omits a public override flag"
        )


def test_ac3_execution_interfaces_require_prepared_authority() -> None:
    from heddle.gate import entry
    from heddle.gate.entry import run_gate_for_runtime
    from heddle.gate.runners.claude import execute_claude
    from heddle.gate.runners.codex import execute_codex
    from heddle.gate.summary import build_summary

    for function in (entry.run_gate_for_runtime, run_gate_for_runtime):
        assert "cli" not in inspect.signature(function).parameters
    for function in (execute_claude, execute_codex, build_summary):
        prepared = inspect.signature(function).parameters["prepared"]
        assert prepared.default is inspect.Parameter.empty
    assert not hasattr(gate_cli, "effective_exec_config")
    assert not hasattr(gate_cli, "effective_model")
    assert not hasattr(gate_cli, "effective_reasoning_effort")
    assert {"model", "reasoning_effort", "sandbox"}.isdisjoint(
        field.name for field in fields(gate_cli.GateArgs)
    )


_PUBLIC_INVALID_FORMS = (
    (
        "spec-review",
        ("--model",),
        "--model requires a value",
        "usage: heddle {action} <gate> [--feature <slug>] [--cli claude|codex] "
        "[--model <id>] [--reasoning-effort low|medium|high|xhigh] [--json]",
    ),
    (
        "spec-review",
        ("--cli", "CLAUDE"),
        "--cli must be one of claude, codex",
        "use an exact lowercase supported CLI",
    ),
    (
        "spec-review",
        ("--cli", "claude", "--cli", "claude"),
        "duplicate option --cli",
        "supply --cli at most once",
    ),
    (
        "spec-review",
        ("--model", "m", "--model", "m"),
        "duplicate option --model",
        "supply --model at most once",
    ),
    (
        "spec-review",
        ("--reasoning-effort", "high", "--reasoning-effort", "high"),
        "duplicate option --reasoning-effort",
        "supply --reasoning-effort at most once",
    ),
    (
        "spec-review",
        ("--reasoning-effort", "HIGH"),
        "--reasoning-effort must be one of low, medium, high, xhigh",
        "use an exact lowercase reasoning effort",
    ),
    (
        "spec-review",
        ("--reasoning-effort", ""),
        "--reasoning-effort must be one of low, medium, high, xhigh",
        "use an exact lowercase reasoning effort",
    ),
    (
        "spec-review",
        ("--model", ""),
        "--model must be non-empty with no leading or trailing whitespace",
        "supply the exact provider model ID",
    ),
    (
        "spec-review",
        ("--model", " padded "),
        "--model must be non-empty with no leading or trailing whitespace",
        "supply the exact provider model ID",
    ),
)


@pytest.mark.parametrize("action", ["run-gate", "show-prompt"])
@pytest.mark.parametrize(("gate", "tokens", "message", "hint"), _PUBLIC_INVALID_FORMS)
def test_ac2_public_invalid_forms_fail_before_reads_and_writes(
    action: str,
    gate: str,
    tokens: tuple[str, ...],
    message: str,
    hint: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.gate import entry
    from heddle.gate import prompt as prompt_module

    host = tmp_path / "host"
    shutil.copytree(REPO_ROOT / "tests/fixtures/workspaces/golden", host)
    monkeypatch.chdir(host)
    state_path = host / "plans/nl-screening/state.yaml"
    before = state_path.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("FAIL AC-2/4: invalid public input reached resources")

    monkeypatch.setattr(entry, "build_gate_context", forbidden)
    monkeypatch.setattr(entry, "prepare_gate_run", forbidden)
    monkeypatch.setattr(prompt_module, "resolve_gate_prompt", forbidden)
    code, stdout, stderr = run_cli(
        [action, gate, "--feature", "nl-screening", *tokens, "--json"]
    )
    envelope = envelope_tools.parse(stdout)
    envelope_tools.assert_shape(envelope)
    assert code == 2 and stderr == ""
    assert envelope["error"] == {
        "code": "usage",
        "message": message,
        "hint": hint.format(action=action),
        "details": {},
    }
    assert state_path.read_bytes() == before


def test_ac2_invalid_override_human_mode_uses_only_stderr(run_cli) -> None:
    code, stdout, stderr = run_cli(["show-prompt", "spec-review", "--cli", "CLAUDE"])

    assert code == 2
    assert stdout == ""
    assert stderr == (
        "heddle: error[usage]: --cli must be one of claude, codex\n"
        "  hint: use an exact lowercase supported CLI\n"
    )


@pytest.mark.parametrize(
    ("parser", "tokens", "flag"),
    [
        (
            gate_run._parse,
            ["run-gate", "spec-review", "--cli", "claude", "--cli", "claude"],
            "--cli",
        ),
        (
            gate_run._parse,
            ["run-gate", "spec-review", "--model", "m", "--model", "m"],
            "--model",
        ),
        (
            show_prompt._parse,
            ["spec-review", "--reasoning-effort", "high", "--reasoning-effort", "high"],
            "--reasoning-effort",
        ),
    ],
)
def test_ac2_duplicate_public_options_have_the_exact_usage_failure(
    parser, tokens: list[str], flag: str
) -> None:
    result = parser(tokens)
    failure = result[-1]
    assert failure is not None and failure.error is not None
    assert failure.error.code == "usage"
    assert failure.error.message == f"duplicate option {flag}"
    assert failure.error.hint == f"supply {flag} at most once"


def test_ac2_parser_preserves_model_and_effort_without_normalizing() -> None:
    _json, parsed, failure = gate_run._parse(
        [
            "run-gate",
            "spec-review",
            "--cli",
            "claude",
            "--model",
            "claude-sentinel",
            "--reasoning-effort",
            "low",
        ]
    )
    assert failure is None and parsed is not None
    assert parsed.model == "claude-sentinel"
    assert parsed.reasoning_effort == "low"


def test_ac3_prepared_run_owns_all_semantic_execution_inputs() -> None:
    field_names = {field.name for field in fields(PreparedGateRun)}
    assert {
        "invocation",
        "prompt_version",
        "effective_prompt_sha256",
        "runtime_sections",
    } <= field_names, (
        "FAIL AC-3: PreparedGateRun cannot be the sole downstream semantic authority"
    )
    assert {"feature", "transport"} <= field_names


def test_ac6_run_fact_schema_is_additive_and_exact() -> None:
    assert set(GATE_RUN_OPTIONAL_KEYS) == {
        "prompt_version",
        "effective_prompt_sha256",
        "review_policy_id",
        "review_basis_hash",
        "execution_config",
        "overridden_axes",
        "artifact_sha256",
        "failure_reason",
    }, "FAIL AC-6: state cannot retain the prepared tuple and artifact identity"


@pytest.mark.parametrize(
    ("tokens", "message", "hint"),
    [
        (
            ["run-gate", "spec-review", "--reasoning-effort", "HIGH"],
            "--reasoning-effort must be one of low, medium, high, xhigh",
            "use an exact lowercase reasoning effort",
        ),
        (
            ["run-gate", "spec-review", "--model", " padded "],
            "--model must be non-empty with no leading or trailing whitespace",
            "supply the exact provider model ID",
        ),
    ],
)
def test_ac2_invalid_override_domains_match_the_public_contract(
    tokens: list[str], message: str, hint: str
) -> None:
    _json, _parsed, failure = gate_run._parse(tokens)
    assert failure is not None and failure.error is not None
    assert failure.error.code == "usage"
    assert failure.error.message == message
    assert failure.error.hint == hint


@pytest.mark.parametrize("parser", [gate_run._parse, show_prompt._parse])
def test_ac2_empty_model_and_effort_fail_on_both_public_surfaces(parser) -> None:
    prefix = ["run-gate"] if parser is gate_run._parse else []
    for flag in ("--model", "--reasoning-effort"):
        result = parser([*prefix, "spec-review", flag, ""])
        failure = result[-1]
        assert failure is not None and failure.error is not None
        assert failure.error.code == "usage"
        assert failure.error.message.startswith(flag)


def test_ac4_survivor_driver_route_preserves_action_local_overrides() -> None:
    """The action itself owns every invocation override at the application edge."""
    operation = RunGate(
        "spec-review",
        feature="fixture",
        cli="claude",
        model="sentinel-model",
        reasoning_effort="low",
    )
    action = CommandAction(operation)
    assert action.operation is operation
    assert action_command(action) == (
        "heddle run-gate spec-review --cli claude --model sentinel-model "
        "--reasoning-effort low --feature fixture"
    )
