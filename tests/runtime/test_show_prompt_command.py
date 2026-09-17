"""
Tests for ``heddle show-prompt`` — the gate-guidelines render command.

The command renders a gate's reviewer-facing guidelines (every ``[partial-*]``
block spliced inline, ``[var]`` tokens filled) as a pure read, so an
interactive Claude Code or Codex session can inspect the exact guidelines a
gate follows without launching the gate. Two layers are covered: the engine
render (``resolve_gate_prompt``) and the CLI surface
(``run_cli`` in-process, against the real dogfooded repo config + corpus).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from heddle.gate.prompt import resolve_gate_prompt
from heddle.gate.registry import GATES
from heddle.gate.types import GateContext
from heddle.runtime.contracts import COMMAND_SURFACE

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_FIXTURE_ROOT = REPO_ROOT / "tests/fixtures/workspaces/golden"
CLI_FIXTURE_FEATURE = "nl-screening"


def render_guidelines(gate_name, repo_root, *, cli="claude"):
    gate = GATES[gate_name]
    context = GateContext(
        gate_type=gate,
        feature="[feature]",
        repo_root=repo_root,
        plan_path=Path("[plan-path]"),
        spec_path=Path("[spec-path]"),
        milestone="[milestone]",
        tier="[tier]",
    )
    return resolve_gate_prompt(context, gate, cli=cli).effective_instructions


class TestCliSurface:
    """The dispatched command: clean stdout, envelope in --json, usage errors."""

    def test_human_mode_prints_the_prompt_alone_to_stdout(self, run_cli, monkeypatch):
        monkeypatch.chdir(CLI_FIXTURE_ROOT)
        code, out, _err = run_cli(
            [
                "show-prompt",
                "spec-review",
                "--feature",
                CLI_FIXTURE_FEATURE,
            ]
        )
        assert code == 0
        assert "# Spec Review" in out
        assert "[partial-" not in out
        # Clean stdout is the point: the prompt, not a JSON envelope.
        with pytest.raises(json.JSONDecodeError):
            json.loads(out.strip())

    def test_cli_flag_overrides_the_default_lane(
        self, run_cli, envelope_tools, monkeypatch
    ):
        monkeypatch.chdir(CLI_FIXTURE_ROOT)
        code, out, _err = run_cli(
            [
                "show-prompt",
                "spec-review",
                "--feature",
                CLI_FIXTURE_FEATURE,
                "--cli",
                "codex",
                "--json",
            ]
        )
        assert code == 0
        data = envelope_tools.parse(out)["data"]
        assert data["cli"] == "codex"

    def test_failed_preflight_is_a_read_only_preview(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        from heddle.gate import preparation as preparation_module
        from heddle.gate.runners import claude as claude_runner
        from heddle.gate.types import PreflightResult

        host = tmp_path / "host"
        shutil.copytree(CLI_FIXTURE_ROOT, host)
        state_path = host / "plans" / CLI_FIXTURE_FEATURE / "state.yaml"
        state_before = state_path.read_bytes()
        monkeypatch.chdir(host)
        monkeypatch.setattr(
            preparation_module,
            "preflight",
            lambda **_kwargs: PreflightResult(
                status="failed",
                fatal_reason="missing-milestone-section",
                anomalies=("Milestone section is absent.",),
            ),
        )

        def provider_must_not_run(*_args, **_kwargs):
            raise AssertionError("show-prompt must not launch a provider")

        monkeypatch.setattr(claude_runner, "run_monitored", provider_must_not_run)
        code, out, err = run_cli(
            [
                "show-prompt",
                "plan-review",
                "--feature",
                CLI_FIXTURE_FEATURE,
                "--json",
            ]
        )

        assert code == 0 and err == "", (out, err)
        envelope = envelope_tools.parse(out)
        assert envelope["data"]["prompt"].strip()
        assert envelope["data"]["preflight"] == {
            "status": "failed",
            "fatal_reason": "missing-milestone-section",
            "anomalies": ["Milestone section is absent."],
        }
        assert state_path.read_bytes() == state_before

    def test_unknown_gate_is_a_usage_error(self, run_cli, envelope_tools, monkeypatch):
        monkeypatch.chdir(REPO_ROOT)
        code, out, _err = run_cli(["show-prompt", "no-such-gate", "--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "usage"

    def test_unsupported_cli_is_a_usage_error(
        self, run_cli, envelope_tools, monkeypatch
    ):
        monkeypatch.chdir(REPO_ROOT)
        code, out, _err = run_cli(
            ["show-prompt", "spec-review", "--cli", "bogus", "--json"]
        )
        assert code == 2
        assert envelope_tools.parse(out)["error"]["code"] == "usage"

    def test_missing_gate_is_a_usage_error(self, run_cli, envelope_tools, monkeypatch):
        monkeypatch.chdir(REPO_ROOT)
        code, out, _err = run_cli(["show-prompt", "--json"])
        assert code == 2
        assert envelope_tools.parse(out)["error"]["code"] == "usage"


class TestContractSurface:
    """The command is registered as a read-only leaf (help/manifest auto-derive)."""

    def test_show_prompt_is_a_read_only_contract(self):
        contract = next((c for c in COMMAND_SURFACE if c.name == "show-prompt"), None)
        assert contract is not None
        assert contract.mutating is False
        assert contract.dry_run is False
        assert contract.output_schema is None
        assert [(arg.name, arg.required) for arg in contract.args] == [("gate", True)]
        assert {flag.name for flag in contract.flags} == {
            "--json",
            "--feature",
            "--cli",
            "--model",
            "--reasoning-effort",
        }
