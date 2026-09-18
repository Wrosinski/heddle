"""
The driver bounds consecutive failing driver-executed verification runs.

Found by the first real drive (commands-native-switchover, 2026-07-05): a verify
step SUCCEEDS as a command whenever the fact records — the failing exit lives
INSIDE the fact — so the loop's generic nonzero halt never fired, no session
ran (so the class-6 no-progress guard saw nothing), and a fast-failing stored
command looped the driver unboundedly: 245 junk verification facts at
~2/second before a manual kill. Contract (autopilot Decision Log, "Verify attempt
cap"): ``VERIFY_ATTEMPT_LIMIT`` consecutive failing verification facts for
one scope raise a class-7 attempt-cap decision batch and pause the drive; a
passing verification resets the scope's counter.
"""

from __future__ import annotations

import json
from typing import Any

import heddle.driver.loop as loop_mod
from tests.driver.constants import AUTO_TIER2_SLUG as SLUG

FAILING_COMMAND = "exit 4"
# Fails twice (exit 1), passes on the third run — a real sh -c chain with
# state on disk, so the reset path is exercised end-to-end.
PASS_ON_THIRD_COMMAND = (
    "c=$(cat verify-count 2>/dev/null || echo 0); c=$((c+1)); "
    'echo "$c" > verify-count; [ "$c" -ge 3 ]'
)


def _arm_verify_pending(auto_tier2_workspace, command: str, *, v2: bool = False):
    """
    Implement stage, core current with its only task done and no passing
        verification fact — the kernel derives ``heddle verify --scope core``.
    """

    def _mutate(state: dict[str, Any]) -> None:
        m1 = state["milestones"][0]
        m1["status"] = "current"
        m1["tasks"][0]["status"] = "done"
        m1["verification"] = {"command": command, "expected": "exit 0"}

    return auto_tier2_workspace(
        chdir=False,
        stage="implement",
        authorized_through="implement",
        mutate=_mutate,
    )


def _no_sessions(monkeypatch) -> None:
    def _fail_spawn(spec, *, scratch_dir=None):
        raise AssertionError(
            "no phase session may spawn on the driver-executed verify path"
        )

    monkeypatch.setattr(loop_mod, "spawn_phase_session", _fail_spawn)
    monkeypatch.setattr(loop_mod, "probe_claude_capabilities", lambda: None)
    monkeypatch.setattr(loop_mod, "probe_codex_capabilities", lambda: None)


def test_consecutive_verify_failures_raise_attempt_cap_batch(
    run_json, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = _arm_verify_pending(auto_tier2_workspace, FAILING_COMMAND)
    _no_sessions(monkeypatch)
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])

    # Pre-fix behavior: the loop re-derived `heddle verify --scope core`
    # forever — this call never returned.
    assert code != 0 and envelope["error"]["code"] == "attempt-cap", (
        "FAIL: bounded verify retries must pause the drive with the "
        f"attempt-cap blocker, got exit {code} with {envelope!r}"
    )
    detail = json.dumps(envelope)
    assert "m1" in detail and "verification" in detail, (
        "FAIL: the pause must name the failing scope so the operator knows "
        f"what to fix, got {envelope!r}"
    )

    state = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    facts = state.get("verifications", [])
    assert len(facts) == loop_mod.VERIFY_ATTEMPT_LIMIT, (
        "FAIL: exactly VERIFY_ATTEMPT_LIMIT failing facts may record before "
        f"the pause (m6b dogfood recorded 245), got {len(facts)}"
    )
    assert all(fact["exit_code"] == 4 for fact in facts)
    pending = [
        decision
        for decision in state.get("decisions", [])
        if decision.get("status") == "pending"
    ]
    assert len(pending) == 1, (
        f"FAIL: the pause must record ONE pending batch, got {pending!r}"
    )
    batch = pending[0]
    assert batch["class"] == 7 and batch["source"] == "driver", (
        f"FAIL: attempt-cap is the class-7 driver escalation, got {batch!r}"
    )
    assert "m1" in batch["question"], (
        "FAIL: the batch question must carry the failing-scope detail, got "
        f"{batch['question']!r}"
    )
    assert batch["kind"] == "question"
    assert batch.get("target_stage") is None and batch.get("target_blocker") is None
    assert state["sessions"] == []
    path = driver_corpus.state(host, SLUG)
    before = path.read_bytes()
    code, blocked, *_ = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 1 and blocked["error"]["code"] == "pending-decisions"
    assert path.read_bytes() == before
    route = state["spec"]
    arguments = [
        "decisions",
        "resolve",
        batch["id"],
        "--feature",
        SLUG,
        "--resolution",
        "Retry after repairing the verification command",
        "--routes-to",
        route,
        "--json",
    ]
    for kind in ("continue-session", "continue-stage", "extend-verdict"):
        code, refused, *_ = run_json([*arguments, "--kind", kind])
        assert code == 2 and refused["error"]["code"] == "usage", refused
        assert path.read_bytes() == before
    code, resolved, *_ = run_json([*arguments, "--kind", "disposition"])
    assert code == 0 and resolved["ok"], resolved
    settled = driver_corpus.read_yaml(path)
    assert settled["decisions"][-1]["resolution"] == {"kind": "disposition"}
    assert not settled["sessions"]
    before = path.read_bytes()
    code, retry, *_ = run_json([*arguments, "--kind", "disposition"])
    assert code == 0 and retry["data"]["mutation_summary"]["would_write"] is False
    assert path.read_bytes() == before


def test_passing_verification_resets_the_failure_counter(
    run_json, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = _arm_verify_pending(auto_tier2_workspace, PASS_ON_THIRD_COMMAND)
    _no_sessions(monkeypatch)

    # Sentinel: after core verifies, the kernel derives the required
    # code-quality gate — halt there so the test never runs a real engine.
    gate_calls: list[list[str]] = []

    def fake_run_gate(operation):
        from heddle.contracts.result import ExitCode, HeddleError, HeddleResult

        gate_calls.append(operation)
        return HeddleResult.failure(
            HeddleError(
                "gate-not-converged", "test sentinel: drive reached the gate", "n/a"
            ),
            exit_code=ExitCode.FATAL,
        )

    monkeypatch.setattr(loop_mod.gate_run, "execute_gate", fake_run_gate)
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])

    assert envelope["error"]["message"].startswith("test sentinel"), (
        "FAIL: two failures then a pass must NOT raise the cap — the drive "
        f"must continue past verify to the gate, got {envelope!r}"
    )
    assert gate_calls, "FAIL: the drive never reached the post-verify gate"
    state = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    facts = state.get("verifications", [])
    assert [fact["exit_code"] for fact in facts] == [1, 1, 0], (
        "FAIL: expected two failing facts then the passing one, got "
        f"{[fact['exit_code'] for fact in facts]!r}"
    )
    assert not state.get("decisions"), (
        "FAIL: no attempt-cap batch may record when the failure streak "
        f"breaks before the limit, got {state.get('decisions')!r}"
    )


def test_zero_exit_unstable_verification_counts_toward_attempt_cap(
    run_json, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = _arm_verify_pending(
        auto_tier2_workspace,
        "mkdir -p src/example && printf x >> src/example/demo.py",
        v2=True,
    )
    _no_sessions(monkeypatch)
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])

    assert code != 0 and envelope["error"]["code"] == "attempt-cap"
    state = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    facts = state["verifications"]
    assert len(facts) == loop_mod.VERIFY_ATTEMPT_LIMIT
    assert all(fact["exit_code"] == 0 for fact in facts)
    assert all(
        fact["evidence"]["before"]["source_sha256"]
        != fact["evidence"]["after"]["source_sha256"]
        for fact in facts
    )
    pending = [
        decision
        for decision in state["decisions"]
        if decision.get("status") == "pending"
    ]
    assert len(pending) == 1 and "unstable" in pending[0]["question"]


def test_drive_refreshes_content_stale_fact_before_milestone_transition(
    run_json,
    driver_corpus,
    monkeypatch,
    auto_tier2_workspace,
):
    host = _arm_verify_pending(auto_tier2_workspace, "true", v2=True)
    _no_sessions(monkeypatch)
    monkeypatch.chdir(host)
    (host / "src/example").mkdir(parents=True, exist_ok=True)
    (host / "src/example/demo.py").write_text("VALUE = 'baseline'\n", encoding="utf-8")
    code, envelope, _out, _err = run_json(
        ["verify", "--scope", "m1", "--feature", SLUG, "--json"]
    )
    assert code == 0, envelope
    (host / "src/example/demo.py").write_text("VALUE = 'changed'\n", encoding="utf-8")

    from heddle.contracts import operations as ops
    from heddle.contracts.result import ExitCode, HeddleError, HeddleResult

    real_execute = loop_mod.application.execute

    def stop_before_transition(operation):
        if isinstance(operation, (ops.RunGate, ops.MilestoneAdvance)):
            return HeddleResult.failure(
                HeddleError(
                    "workspace-invalid",
                    "stopped immediately before milestone transition",
                    "test sentinel",
                ),
                exit_code=ExitCode.FATAL,
            )
        return real_execute(operation)

    monkeypatch.setattr(loop_mod.application, "execute", stop_before_transition)
    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])

    assert code == 3 and envelope["error"]["message"].startswith("stopped immediately")
    state = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    facts = [fact for fact in state["verifications"] if fact["scope"] == "m1"]
    assert len(facts) == 2
    assert (
        facts[-1]["evidence"]["before"]["source_sha256"]
        == facts[-1]["evidence"]["after"]["source_sha256"]
    )
    assert (
        facts[-1]["evidence"]["before"]["source_sha256"]
        != facts[0]["evidence"]["before"]["source_sha256"]
    )
    assert state["milestones"][0]["status"] == "current"


def test_verify_attempt_limit_is_a_small_bound():
    assert loop_mod.VERIFY_ATTEMPT_LIMIT == 3, (
        "the cap exists to stop a ~2-facts/second runaway; keep it small "
        "and change it deliberately (M4A Decision Log)"
    )
