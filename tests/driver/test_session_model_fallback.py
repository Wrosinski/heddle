"""Drive phase sessions follow Heddle's model fallback.

Claude Code's own refusal fallback is switched off for every phase session. A
failed Fable session is recorded with both models named, and the next phase
session the drive spawns runs on Opus at xhigh; a refusal with no fallback
halts the drive.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from heddle.driver import loop, sessions
from heddle.driver.sessions import SessionResult
from heddle.kernel.project_config import load_project_config
from tests.driver.constants import AUTO_TIER2_SLUG as SLUG
from tests.driver.helpers import arm_at_stage

FABLE = ("claude-fable-5-2", "high")
OPUS = ("claude-opus-5-5", "xhigh")


def _drive(monkeypatch, driver_corpus, auto_tier2_workspace, answer):
    host = auto_tier2_workspace(chdir=False)
    state_path = arm_at_stage(driver_corpus, host, SLUG, "implement")
    before = len(driver_corpus.read_yaml(state_path).get("sessions") or [])
    launched: list[tuple[str, str]] = []

    def session(spec, **_kwargs):
        launched.append((spec.model, spec.effort))
        return replace(answer(spec), model=spec.model, effort=spec.effort)

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    monkeypatch.setattr(loop, "spawn_phase_session", session)
    monkeypatch.chdir(host)
    result = loop.run_loop(load_project_config(host), SLUG, until="peer-review")
    facts = (driver_corpus.read_yaml(state_path).get("sessions") or [])[before:]
    return result, launched, [fact["key_context"] for fact in facts]


def _refused(_spec):
    return SessionResult(
        exit_code=0, termination="completed", output={}, model_failure="model-refusal"
    )


def _crashed(_spec):
    return SessionResult(exit_code=17, termination="completed", output={})


def test_a_refused_fable_session_is_recorded_and_the_next_runs_on_opus(
    monkeypatch, driver_corpus, auto_tier2_workspace
):
    def answer(spec):
        return _refused(spec) if spec.model == FABLE[0] else _crashed(spec)

    result, launched, contexts = _drive(
        monkeypatch, driver_corpus, auto_tier2_workspace, answer
    )

    assert launched == [FABLE, OPUS]
    assert contexts == [
        "autopilot session for implement on claude-fable-5-2 at high; failed "
        "with model-refusal; the next session runs on the model fallback "
        "claude-opus-5-5 at xhigh"
    ]
    # The replacement's own failure keeps its ordinary handling.
    assert not result.ok and "exit 17" in result.error.message


def test_a_crashed_fable_session_is_followed_by_an_opus_session(
    monkeypatch, driver_corpus, auto_tier2_workspace
):
    result, launched, contexts = _drive(
        monkeypatch, driver_corpus, auto_tier2_workspace, _crashed
    )

    assert launched == [FABLE, OPUS]
    assert len(contexts) == 1 and "failed with exit 17" in contexts[0]
    assert not result.ok and "exit 17" in result.error.message


def test_a_refusal_with_no_fallback_is_recorded_and_halts_the_drive(
    monkeypatch, driver_corpus, auto_tier2_workspace
):
    result, launched, contexts = _drive(
        monkeypatch, driver_corpus, auto_tier2_workspace, _refused
    )

    assert launched == [FABLE, OPUS]
    assert len(contexts) == 2
    assert contexts[1] == (
        "autopilot session for implement on claude-opus-5-5 at xhigh; failed "
        "with model-refusal"
    )
    assert not result.ok
    assert "claude-opus-5-5 at xhigh ended with model-refusal" in result.error.message


def test_every_phase_session_switches_off_the_cli_refusal_fallback():
    spec = sessions.PhaseSessionSpec(
        stage="implement",
        feature="demo",
        prompt_path=Path("/tmp/prompt.md"),
        allowed_tools=("Read",),
        deny_rules=(),
        max_turns=None,
        max_budget_usd=150.0,
    )
    addendum = sessions._autonomy_addendum_path()
    for model, effort in (FABLE, OPUS):
        command = sessions._phase_session_command(
            replace(spec, model=model, effort=effort), addendum
        )
        claude = command.index("claude")
        assert "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1" in command[:claude]
        assert command[command.index("--model") + 1] == model
        assert command[command.index("--effort") + 1] == effort


def test_the_session_stream_names_a_refusal_or_a_model_switch(tmp_path: Path):
    from heddle.io.claude_events import model_failure_from_path

    def stream(*events):
        path = tmp_path / f"stream-{len(list(tmp_path.iterdir()))}.jsonl"
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
        return path

    result = {"type": "result", "subtype": "success", "is_error": False}
    refused = {
        "type": "assistant",
        "parent_tool_use_id": None,
        "message": {"stop_reason": "refusal"},
    }
    switched = {"type": "system", "subtype": "model_refusal_fallback"}
    subagent = {**refused, "parent_tool_use_id": "toolu_1"}

    assert model_failure_from_path(stream(refused, result)) == "model-refusal"
    assert model_failure_from_path(stream(switched, result)) == "model-substituted"
    assert model_failure_from_path(stream(subagent, result)) is None
    assert model_failure_from_path(tmp_path / "absent.jsonl") is None
