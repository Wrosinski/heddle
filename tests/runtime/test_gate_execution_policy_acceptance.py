"""Composed offline acceptance scaffold for gate execution policy (AC-1..12)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from heddle.gate import types as gate_types
from heddle.io.process import MonitorResult, Termination

pytestmark = pytest.mark.acceptance

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests/fixtures/workspaces/golden"


def _install_provider_monitors(monkeypatch) -> list[dict[str, Any]]:
    from heddle.gate.runners import claude as claude_runner
    from heddle.gate.runners import codex as codex_runner

    provider_calls: list[dict[str, Any]] = []

    def review_for(command: list[str], prompt: str) -> str:
        from tests.structured_review_helpers import (
            complete_fixture_coverage,
            complete_scripted_prompt,
            content,
        )

        schema = (
            json.loads(command[command.index("--json-schema") + 1])
            if "--json-schema" in command
            else json.loads(
                Path(command[command.index("--output-schema") + 1]).read_bytes()
            )
        )
        role = schema["properties"]["role"]["enum"][0]
        payload = content(role)
        complete_fixture_coverage(payload, ("AC-1", "AC-2", "AC-3", "AC-4"))
        return complete_scripted_prompt(json.dumps(payload), prompt)

    def fake_claude(command, raw_out, _config, *, stdin_source=None, **_kwargs):
        prompt = stdin_source.read_text(encoding="utf-8")
        review = review_for(command, prompt)
        provider_calls.append(
            {
                "cli": "claude",
                "command": tuple(command),
                "stdin": prompt,
            }
        )
        raw_out.write_text(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": json.loads(review),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return MonitorResult(
            exit_code=0,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )

    def fake_codex(
        command, raw_out, _config, *, stdin_source=None, env=None, **_kwargs
    ):
        prompt = stdin_source.read_text(encoding="utf-8")
        review = review_for(command, prompt)
        provider_calls.append(
            {
                "cli": "codex",
                "command": tuple(command),
                "stdin": prompt,
                "env": env,
            }
        )
        Path(command[command.index("-o") + 1]).write_text(review, encoding="utf-8")
        raw_out.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": review},
                }
            )
            + "\n"
            + json.dumps({"type": "turn.completed", "usage": {}})
            + "\n",
            encoding="utf-8",
        )
        return MonitorResult(
            exit_code=0,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )

    monkeypatch.setattr(claude_runner, "run_monitored", fake_claude)
    monkeypatch.setattr(codex_runner, "run_monitored", fake_codex)
    return provider_calls


def _run_json(run_cli, envelope_tools, args: list[str]) -> dict:
    code, stdout, stderr = run_cli(args)
    assert code == 0 and stderr == ""
    envelope = envelope_tools.parse(stdout)
    envelope_tools.assert_shape(envelope)
    return envelope["data"]


def test_acceptance_ac11_failure_guide() -> None:
    guide_text = (REPO_ROOT / "heddle/resources/gate-failure-guide.md").read_text(
        encoding="utf-8"
    )
    assert {f"## `{reason.value}`" for reason in gate_types.FailureReason} <= set(
        guide_text.splitlines()
    )
