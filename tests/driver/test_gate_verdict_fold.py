"""
The driver folds an unconverged gate VERDICT into the next session attempt.

Found by the first real drive (commands-native-switchover, 2026-07-05): the
spec-review gate returned ``pass_with_conditions`` — run-gate's gate envelope
ADVISORY/4 exit — and the loop's generic nonzero-exit halt ended the run
instead of continuing the convergence loop. Contract (autopilot Decision Log,
"Gate-verdict continuation"): a driver-executed ``run-gate`` whose engine run
COMPLETED (``ok`` envelope) but whose verdict is not converged
(``pass_with_conditions`` → 4, clean-engine ``fail`` verdict → 3) folds into
the next attempt's kickoff — attempts are never blind — bounded by
``VALIDATION_ATTEMPT_LIMIT``. Engine errors (``ok: false``) keep halting
(AC-22); those runs are error-marked and review accounting-counted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import heddle.driver.loop as loop_mod
from heddle.contracts.operations import CommandAction, PhaseExit, RunGate, RunGates
from heddle.contracts.result import ExitCode, HeddleError, HeddleResult
from heddle.driver.sessions import SessionResult
from heddle.gate import entry
from heddle.gate.cli import resolve_gate_execution
from heddle.gate.registry import GATES
from heddle.gate.types import GateInvocationOverrides
from heddle.kernel.model import resolve_snapshot
from heddle.kernel.project_config import load_project_config
from heddle.runtime import recording
from tests.driver.constants import AUTO_TIER2_SLUG as SLUG
from tests.operational_model_helpers import new_run_id, read

_NOW = "2026-07-05T00:00Z"


def _findings(implement: int = 0, report: int = 0) -> dict[str, Any]:
    total = implement + report
    return {
        "by_severity": {"critical": 0, "important": total, "minor": 0},
        "by_classification": {
            "implement": implement,
            "report": report,
            "ignore": 0,
            "unknown": 0,
        },
        "total": total,
        "contradictions": 0,
    }


def _gate_result(status: str, *, findings: dict[str, Any]) -> HeddleResult:
    return HeddleResult.success(
        {
            "feature": SLUG,
            "gate": "spec-review",
            "scope": "feature",
            "status": status,
            "cached": False,
            "findings": findings,
        },
        exit_code=ExitCode.ADVISORY
        if status == "pass_with_conditions"
        else ExitCode.OK,
    )


def _error_result() -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            "gate-not-converged",
            "the spec-review gate run did not complete cleanly",
            "inspect the gate log/output and re-run",
        ),
        exit_code=ExitCode.FATAL,
    )


def _record_run(
    state_path: Path,
    host: Path,
    *,
    status: str,
    rerun: bool,
    cli: str = "codex",
    gate: str = "spec-review",
) -> str:
    from heddle.gate.results import serialize_review_result
    from tests.structured_review_helpers import (
        complete_fixture_coverage,
        complete_fixture_rerun,
        complete_fixture_verdict,
        finding,
    )
    from tests.tiering_review_helpers import review_content

    artifact_dir = host / "plans" / SLUG / "reviews"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prepared = _prepare(host, gate, cli)
    findings = [
        finding(
            f"SP-I{i + 1}",
            classification="implement",
            title=f"Required repair {i + 1}",
        )
        for i in range(10 if rerun else 0)
    ]
    payload = review_content(gate, findings=findings)
    complete_fixture_coverage(payload, prepared.ac_ids, prepared.active_rules)
    complete_fixture_rerun(payload, prepared.prior_reviews, prepared.review_decisions)
    complete_fixture_verdict(payload, bool(prepared.prior_reviews))
    from heddle.gate.results import bind_review_result, decode_review_content

    result = bind_review_result(
        decode_review_content(json.dumps(payload).encode(), prepared.output_contract),
        prepared,
        SLUG,
    )
    projection = entry.machine_projection_from_result(result)
    assert projection["verdict"] == {
        "status": status,
        "rerun_recommended": rerun,
    }
    artifact_bytes = serialize_review_result(result)
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    artifact = f"{gate}.{cli}.{digest}.review.json"
    (artifact_dir / artifact).write_bytes(artifact_bytes)
    accepted = recording.record_gate_run(
        state_path,
        gate=gate,
        scope="feature",
        run_fact={
            "run_id": new_run_id(),
            "report_findings": [],
            "at": _NOW,
            "cli": cli,
            "artifact": f"reviews/{artifact}",
            "input_hash": prepared.input_hash,
            "review_basis_hash": prepared.review_basis_hash,
            "prompt_version": prepared.prompt_version,
            "effective_prompt_sha256": prepared.effective_prompt_sha256,
            "execution_config": asdict(prepared.invocation.exec_config),
            "overridden_axes": ["cli"],
            "artifact_sha256": digest,
            "verdict": projection["verdict"],
            "findings": projection["findings"],
            "assignment_id": prepared.assignment_id,
            "round_number": prepared.round_number,
            "reviewer_slot": prepared.reviewer_slot,
        },
        decisions=[],
    )
    return accepted.run_id


def _prepare(host: Path, gate: str, cli: str):
    config = load_project_config(host)
    snapshot = resolve_snapshot(config, SLUG)
    gate_type = GATES.get(gate)
    assert gate_type is not None
    context = entry.build_gate_context(snapshot, config, gate_type, cli=cli)
    return entry.prepare_gate_run(
        context,
        gate_type=gate_type,
        invocation=resolve_gate_execution(
            gate_type,
            GateInvocationOverrides(cli=cli),
        ),
    )


def _record_converged_pipeline(state_path: Path, host: Path) -> None:
    """A compliant fold session applies and dispositions the original work."""
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    spec = host / read(state_path)["spec"]
    spec.write_text(
        spec.read_text() + "\nThe ten requested document repairs are applied.\n"
    )
    state = read(state_path)
    run_id = state["review_assignments"]["attempts"][-1]["attempt_id"]
    disposition = execute(
        ops.RecordReviewDisposition(
            feature=SLUG,
            expect_revision=state["revision"],
            payload={
                "schema": "heddle.review-disposition-input/v1",
                "dispositions": [
                    {
                        "run_id": run_id,
                        "finding_id": (
                            f"SP-I{index + 1}" if index < 10 else "@coverage"
                        ),
                        "status": "addressed" if index < 10 else "settled",
                        "evidence_kind": "inspection",
                        "references": [read(state_path)["spec"]],
                        "reason": (
                            "The fold session applied and inspected the "
                            "requested repair"
                        ),
                    }
                    for index in range(11)
                ],
            },
        )
    )
    assert disposition.ok, disposition.to_envelope()


def _arm_spec_review(auto_tier2_workspace) -> Path:
    return auto_tier2_workspace(
        chdir=False,
        stage="spec-review",
        authorized_through="spec-review",
    )


def _stub_sessions(
    monkeypatch,
    prompts: list[str],
    on_fold_prompt=None,
) -> None:
    """Session double. A COMPLIANT fold session follows the folded
    instruction — it re-converges the gate itself (the review-stage
    allowlist carries ``Bash(heddle run-gate:*)`` for exactly this), which
    also bumps revision so its session fact reads advanced."""

    def fake_spawn(spec, *, scratch_dir=None):
        text = Path(spec.prompt_path).read_text(encoding="utf-8")
        prompts.append(text)
        if on_fold_prompt is not None and (
            "completed without converging" in text
            or "Authoring work: review-disposition" in text
        ):
            on_fold_prompt()
        return SessionResult(exit_code=0, termination="completed", output={})

    monkeypatch.setattr(loop_mod, "spawn_phase_session", fake_spawn)
    monkeypatch.setattr(loop_mod, "probe_claude_capabilities", lambda: None)


def test_advisory_gate_verdict_folds_and_drive_continues_to_convergence(
    run_json, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = _arm_spec_review(auto_tier2_workspace)
    state_path = driver_corpus.state(host, SLUG)
    prompts: list[str] = []
    _stub_sessions(
        monkeypatch,
        prompts,
        on_fold_prompt=lambda: _record_converged_pipeline(state_path, host),
    )

    gate_calls: list[RunGate] = []

    def fake_run_gate(operation: RunGate):
        gate_calls.append(operation)
        _record_run(state_path, host, status="pass_with_conditions", rerun=True)
        return _gate_result("pass_with_conditions", findings=_findings(implement=10))

    monkeypatch.setattr(loop_mod.gate_run, "execute_gate", fake_run_gate)
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(
        ["drive", "--feature", SLUG, "--until", "plan-review", "--json"]
    )

    # Pre-fix behavior: the drive halted with the ADVISORY exit after the
    # first run; the convergence loop never continued.
    assert code == 0, (
        "FAIL: an advisory (pass_with_conditions) gate verdict must not halt "
        f"the drive — expected exit 0 at --until plan-review, got {code} with "
        f"{envelope!r}"
    )
    assert len(gate_calls) == 1, (
        "FAIL: after the compliant fold session converged the gate, the "
        f"driver must not re-execute run-gate, got {len(gate_calls)} run(s)"
    )
    folded = [
        p
        for p in prompts
        if "completed without converging" in p
        or "Authoring work: review-disposition" in p
    ]
    assert folded, (
        "FAIL: the reattempt session's kickoff must carry the folded gate "
        f"verdict problem; prompts seen: {len(prompts)}"
    )
    assert "spec-review" in folded[0] and "IMPLEMENT" in folded[0], (
        "FAIL: the folded problem must name the gate and the IMPLEMENT "
        "findings to apply"
    )
    state = driver_corpus.read_yaml(state_path)
    runs = [
        run
        for fact in state.get("gates", [])
        if fact.get("gate") == "spec-review"
        for run in fact.get("runs", [])
    ]
    assert len(runs) == 1 and runs[0]["verdict"]["status"] == (
        "pass_with_conditions"
    ), (
        "FAIL: the convergence loop must preserve the original advisory run "
        f"while native dispositions close its work, got {runs!r}"
    )
    assert state["stage"] == "plan-review", (
        "FAIL: after convergence the boundary must advance to plan-review, "
        f"got {state['stage']!r}"
    )


def test_engine_error_still_halts_the_drive(
    run_json, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = _arm_spec_review(auto_tier2_workspace)
    prompts: list[str] = []
    _stub_sessions(monkeypatch, prompts)

    gate_calls: list[RunGate] = []

    def fake_run_gate(operation: RunGate):
        gate_calls.append(operation)
        return _error_result()

    monkeypatch.setattr(loop_mod.gate_run, "execute_gate", fake_run_gate)
    monkeypatch.chdir(host)

    code, _envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])

    assert code == 3, (
        "FAIL AC-22: an engine error (ok: false envelope) must keep halting "
        f"the drive with the structured failure, got {code}"
    )
    assert len(gate_calls) == 1, (
        "FAIL: an engine error must not be folded into a retry loop by the "
        f"driver, got {len(gate_calls)} gate runs"
    )
    assert not any("completed without converging" in p for p in prompts), (
        "FAIL: no gate-verdict fold session may be spawned for an engine error"
    )


def test_gate_verdict_problem_names_milestone_scoped_artifact():
    envelope = {
        "ok": True,
        "data": {
            "feature": "demo",
            "gate": "code-quality",
            "scope": "m2",
            "status": "fail",
            "findings": {"by_classification": {"implement": 3, "report": 1}},
        },
    }
    problem = loop_mod._gate_verdict_problem(HeddleResult.success(envelope["data"]))
    assert "plans/demo.code-quality-m2.md" in problem.detail
    assert "3 IMPLEMENT" in problem.detail and "1 REPORT" in problem.detail


def test_gate_verdict_problem_prefers_the_envelope_artifact():
    """E1 rehome: run-gate now reports where it actually wrote (native →
    plans/<slug>/reviews/...); the fold must point the session there, not at
    the flat legacy construction."""
    envelope = {
        "ok": True,
        "data": {
            "feature": "demo",
            "gate": "code-quality",
            "scope": "m2",
            "status": "fail",
            "artifact": "plans/demo/reviews/code-quality-m2.md",
            "findings": {"by_classification": {"implement": 3, "report": 1}},
        },
    }
    problem = loop_mod._gate_verdict_problem(HeddleResult.success(envelope["data"]))
    assert "plans/demo/reviews/code-quality-m2.md" in problem.detail
    assert "plans/demo.code-quality-m2.md" not in problem.detail


def test_review_gate_verdict_problem_allows_next_rerun_before_cap():
    envelope = {
        "ok": True,
        "data": {
            "feature": "demo",
            "gate": "code-quality",
            "scope": "m2",
            "status": None,
            "attempt": 1,
            "max_attempts": 4,
            "findings": {"by_classification": {"implement": 1, "report": 0}},
        },
    }
    problem = loop_mod._gate_verdict_problem(HeddleResult.success(envelope["data"]))
    assert "re-run `heddle run-gate code-quality` once" in problem.detail
    assert "attempt 1/4" in problem.detail


def test_review_gate_verdict_problem_prohibits_attempt_five():
    envelope = {
        "ok": True,
        "data": {
            "feature": "demo",
            "gate": "self-review",
            "scope": "m2",
            "status": None,
            "attempt": 4,
            "max_attempts": 4,
            "findings": {"by_classification": {"implement": 1, "report": 0}},
        },
    }
    problem = loop_mod._gate_verdict_problem(HeddleResult.success(envelope["data"]))
    assert "completed review 4/4" in problem.detail
    assert "do not run the gate again" in problem.detail
    assert "resolve the recorded cap decision" in problem.detail


def test_session_advanced_counts_recorded_gate_runs_as_progress(
    run_json, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """The fold session's fact must read advanced: True purely from the gate
    run it recorded (gate recording never bumps the state revision), so two
    healthy convergence sessions never trip the class-6 no-progress guard."""
    host = _arm_spec_review(auto_tier2_workspace)
    state_path = driver_corpus.state(host, SLUG)
    prompts: list[str] = []
    _stub_sessions(
        monkeypatch,
        prompts,
        on_fold_prompt=lambda: _record_converged_pipeline(state_path, host),
    )

    def fake_run_gate(operation: RunGate):
        _record_run(state_path, host, status="pass_with_conditions", rerun=True)
        return _gate_result("pass_with_conditions", findings=_findings(implement=10))

    monkeypatch.setattr(loop_mod.gate_run, "execute_gate", fake_run_gate)
    monkeypatch.chdir(host)

    run_json(["drive", "--feature", SLUG, "--until", "plan-review", "--json"])

    sessions = driver_corpus.read_yaml(state_path).get("sessions", [])
    fold_facts = [
        s
        for s in sessions
        if s.get("stage") == "spec-review" and s.get("advanced") is True
    ]
    assert fold_facts, (
        "FAIL PR-14 note: a session whose only recorded work is a gate run "
        f"must read advanced: True, got {sessions!r}"
    )


def test_phase_session_command_carries_bash_timeout_envelope():
    """
    commands dogfood: without the env envelope, a session-run `heddle run-gate`
        dies at the CLI's 120s Bash default (gate runs take 5-15 min) — the paid
        attempt is killed mid-run and no fact records.
    """
    from heddle.driver import sessions

    spec = sessions.PhaseSessionSpec(
        stage="spec-review",
        feature="demo",
        prompt_path=Path("/tmp/prompt.md"),
        allowed_tools=("Read",),
        deny_rules=(),
        max_turns=1,
        max_budget_usd=1.0,
    )
    command = sessions._phase_session_command(spec, sessions._autonomy_addendum_path())
    assert command[0] == "env"
    envelope = command[1 : command.index("claude")]
    assert (
        f"BASH_DEFAULT_TIMEOUT_MS={sessions.PHASE_SESSION_BASH_DEFAULT_TIMEOUT_MS}"
        in envelope
    )
    assert (
        f"BASH_MAX_TIMEOUT_MS={sessions.PHASE_SESSION_BASH_MAX_TIMEOUT_MS}" in envelope
    )
    # The max must cover the gate runner's own hard timeout so an explicit
    # per-call timeout can ride out any completable gate run.
    assert (
        sessions.PHASE_SESSION_BASH_MAX_TIMEOUT_MS
        >= sessions.PHASE_SESSION_HARD_TIMEOUT_S * 1000
    )


def test_unconverged_gate_verdict_predicate_truth_table():
    gate_action = CommandAction(RunGate("spec-review", feature="demo"))
    batch_action = CommandAction(RunGates(feature="demo"))
    other_action = CommandAction(PhaseExit(feature="demo"))
    ok4 = HeddleResult.success({}, exit_code=ExitCode.ADVISORY)
    ok3 = HeddleResult.success({}, exit_code=ExitCode.FATAL)
    assert loop_mod._is_unconverged_gate_verdict(gate_action, ok4)
    assert loop_mod._is_unconverged_gate_verdict(gate_action, ok3)
    assert not loop_mod._is_unconverged_gate_verdict(
        gate_action, HeddleResult.success({})
    )
    assert not loop_mod._is_unconverged_gate_verdict(gate_action, _error_result())
    assert not loop_mod._is_unconverged_gate_verdict(
        gate_action,
        HeddleResult.failure(_error_result().error, exit_code=ExitCode.ADVISORY),
    )
    assert not loop_mod._is_unconverged_gate_verdict(other_action, ok4)
    assert loop_mod._is_unconverged_gate_verdict(batch_action, ok4)


def test_distinct_gate_batch_driver_accounts_all_members_then_reassesses_once(
    auto_tier2_workspace, monkeypatch
):
    """AC-7: a mixed batch reaches both remedies before later driver work."""
    from heddle.contracts import operations as ops
    from heddle.contracts.result import NextAction
    from heddle.kernel.project_config import load_project_config
    from heddle.kernel.readiness import Boundary, BoundaryAssessment

    host = _arm_spec_review(auto_tier2_workspace)
    config = load_project_config(host)
    operation = ops.RunGates(feature=SLUG)
    readiness_calls: list[str] = []

    def readiness(_config, snapshot, **_kwargs):
        readiness_calls.append("before" if not readiness_calls else "after")
        action = (
            NextAction(CommandAction(operation), "run the independent group")
            if len(readiness_calls) == 1
            else NextAction(
                ops.ManualAction("stop after accounting for both remedies"),
                "bounded test stop",
            )
        )
        return BoundaryAssessment(
            Boundary(snapshot.stage, None),
            (),
            (),
            () if len(readiness_calls) == 1 else ("bounded-stop",),
            (action,),
        )

    members = [
        {
            "gate": "behavior-review",
            "execution": "completed",
            "publication": "recorded",
            "next_actions": [
                {"command": f"heddle review disposition --feature {SLUG}"}
            ],
        },
        {
            "gate": "complexity-review",
            "execution": "failed",
            "publication": "recorded-error",
            "next_actions": [
                {"command": f"heddle run-gate complexity-review --feature {SLUG}"}
            ],
        },
    ]
    member_actions = tuple(
        NextAction(ops.ManualAction(row["next_actions"][0]["command"]), row["gate"])
        for row in members
    )
    failed = HeddleResult.failure(
        HeddleError(
            "gate-not-converged",
            "one batch member failed",
            "follow each member remedy",
            {"members": members, "selected_member": "complexity-review"},
        ),
        next_actions=member_actions,
        exit_code=ExitCode.FATAL,
    )
    advisory = HeddleResult.success(
        {"feature": SLUG, "members": members},
        next_actions=member_actions,
        exit_code=ExitCode.ADVISORY,
    )
    monkeypatch.setattr(loop_mod, "assess_current_readiness", readiness)
    monkeypatch.setattr(loop_mod, "first_actionable_blocker", lambda _snap: None)
    monkeypatch.setattr(loop_mod, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop_mod, "_auto_close_suite_result", lambda *_args: None)
    folded: list[loop_mod.ValidationProblem] = []

    def stop_after_fold(
        _config,
        _slug,
        _snap,
        _command,
        problem,
        **_kwargs,
    ):
        assert problem is not None
        folded.append(problem)
        return loop_mod._SessionStep(
            HeddleResult.failure(
                HeddleError("internal", "captured fold", "test stop"),
                exit_code=ExitCode.FATAL,
            ),
            None,
        )

    monkeypatch.setattr(loop_mod, "_execute_session_step", stop_after_fold)

    for batch_result in (failed, advisory):
        readiness_calls.clear()
        folded.clear()
        executed = []

        def execute(selected, *, outcome=batch_result, calls=executed):
            calls.append(selected)
            return outcome

        monkeypatch.setattr(loop_mod.application, "execute", execute)
        result = loop_mod.run_loop(config, SLUG, until=None)

        assert executed == [operation]
        assert readiness_calls == ["before", "after"]
        if batch_result is failed:
            assert not result.ok
            assert result.next_actions == failed.next_actions, (
                "FAIL AC-7: reassessment discarded ordered member remedies"
            )
        else:
            assert result is not advisory, (
                "FAIL AC-7: advisory batch halted before readiness reassessment"
            )
            assert len(folded) == 1
            assert all(
                role in folded[0].detail
                for role in ("behavior-review", "complexity-review")
            )
