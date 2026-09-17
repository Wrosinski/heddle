"""Controlled local E2E coverage for typed action delivery."""

from __future__ import annotations

import pytest

from heddle.contracts import operations as ops
from heddle.driver import loop
from heddle.driver.sessions import SessionResult
from heddle.kernel.project_config import load_project_config
from heddle.runtime import application
from tests.structured_review_helpers import finding
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    gate_command,
    provider_transport,
    review_content,
    runs,
)


@pytest.mark.e2e
def test_controlled_review_action_reaches_lead_once(tmp_path, monkeypatch, run_cli):
    """AC-3/AC-11: native facts reach one lead session without provider retry."""
    host, path = current_host(tmp_path, monkeypatch)
    provider_calls = provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    run_id = runs(path)[0]["run_id"]
    public = application.execute(ops.Orient(feature=V7_FEATURE)).to_envelope()
    assert public["next_actions"][0]["action"]["kind"] == "authoring"

    prompts = []

    def session(spec, **_kwargs):
        prompts.append(spec.prompt_path.read_text())
        return SessionResult(exit_code=17, termination="completed", output={})

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    monkeypatch.setattr(loop, "spawn_phase_session", session)
    before = path.read_bytes()
    stopped = loop.run_loop(load_project_config(host), V7_FEATURE, until="plan-review")
    assert not stopped.ok and "17" in stopped.error.message
    assert len(prompts) == 1
    assert run_id in prompts[0] and "SP-I1" in prompts[0] and "@coverage" in prompts[0]
    assert len(provider_calls) == 1 and path.read_bytes() == before
