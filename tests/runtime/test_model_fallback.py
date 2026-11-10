"""Heddle's model fallback for Claude gate runs.

A Fable reviewer that fails for a provider reason is rerun once on Opus at
xhigh in the same command; both runs are recorded as the models that ran, and
the fallback serves the slot for the rest of the round.
"""

from __future__ import annotations

import pytest
import yaml

from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import finding, finding_ref
from tests.tiering_helpers import FABLE, OPUS, entry, invoke
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    gate_command,
    open_round,
    provider_transport,
    review_content,
    review_status,
    runs,
)

EXECUTION_KEYS = ("cli", "model", "reasoning_effort")


def _host(tmp_path, monkeypatch, reviewer):
    return current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=reviewer)},
    )


def _launched(calls):
    """(model, effort, refusal fallback switch) for each provider call."""
    rows = []
    for _cli, _prompt, command in calls:
        env = dict(token.split("=", 1) for token in command if "=" in token)
        rows.append(
            (
                command[command.index("--model") + 1],
                env["CLAUDE_CODE_EFFORT_LEVEL"],
                env["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"],
            )
        )
    return rows


def _execution(run):
    return {key: run["execution_config"][key] for key in EXECUTION_KEYS}


def _binding(run):
    return (run["assignment_id"], run["round_number"], run["reviewer_slot"])


@pytest.mark.parametrize(
    ("transport", "reason"),
    (
        ({"refuse_models": (FABLE["model"],)}, "model-refusal"),
        ({"error_attempts": 1}, "exit"),
    ),
)
def test_failed_fable_review_reruns_once_on_opus_and_records_both(
    tmp_path, monkeypatch, run_cli, transport, reason
):
    _host_root, path = _host(tmp_path, monkeypatch, FABLE)
    calls = provider_transport(monkeypatch, review_content(), **transport)

    code, result = gate_command(run_cli, "run-gate", "spec-review")

    assert code == 0 and result["ok"] and result["data"]["accepted"], result
    assert _launched(calls) == [
        (FABLE["model"], FABLE["reasoning_effort"], "1"),
        (OPUS["model"], OPUS["reasoning_effort"], "1"),
    ]
    failed, accepted = runs(path)
    assert failed["verdict"]["status"] == "error"
    assert failed["failure_reason"] == reason
    assert _execution(failed) == FABLE
    assert accepted["verdict"]["status"] != "error"
    assert _execution(accepted) == OPUS
    assert _binding(failed) == _binding(accepted)
    assert result["data"]["run_id"] == accepted["run_id"]
    assert result["data"]["fallback_from"] == {
        "run_id": failed["run_id"],
        "execution": FABLE,
        "failure_reason": reason,
    }
    assert "model-fallback" in {row["code"] for row in result["diagnostics"]}
    assert review_status(path)["calls_completed"] == 1

    # The fallback serves the rest of the round: a rerun reuses its review.
    code, again = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and again["data"]["cached"], again
    assert again["data"]["run_id"] == accepted["run_id"]
    assert len(calls) == 2


def test_fallback_failure_fails_loudly_and_the_round_stays_on_the_fallback(
    tmp_path, monkeypatch, run_cli
):
    _host_root, path = _host(tmp_path, monkeypatch, FABLE)
    calls = provider_transport(
        monkeypatch,
        review_content(),
        refuse_models=(FABLE["model"], OPUS["model"]),
    )

    code, result = gate_command(run_cli, "run-gate", "spec-review")

    assert code != 0 and not result["ok"], result
    message = result["error"]["message"]
    assert "model-refusal" in message and FABLE["model"] in message
    assert result["error"]["details"]["fallback_from"]["execution"] == FABLE
    assert [_execution(run) for run in runs(path)] == [FABLE, OPUS]
    assert all(run["failure_reason"] == "model-refusal" for run in runs(path))

    code, retried = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not retried["ok"], retried
    assert [row[0] for row in _launched(calls)] == [
        FABLE["model"],
        OPUS["model"],
        OPUS["model"],
    ]


def test_a_reviewer_without_a_fallback_fails_on_its_own_model(
    tmp_path, monkeypatch, run_cli
):
    _host_root, path = _host(tmp_path, monkeypatch, OPUS)
    calls = provider_transport(
        monkeypatch, review_content(), refuse_models=(OPUS["model"],)
    )

    code, result = gate_command(run_cli, "run-gate", "spec-review")

    assert code != 0 and not result["ok"], result
    assert "model-refusal" in result["error"]["message"]
    assert "fallback_from" not in result["error"]["details"]
    assert [row[0] for row in _launched(calls)] == [OPUS["model"]]
    assert [run["failure_reason"] for run in runs(path)] == ["model-refusal"]


def test_state_refuses_a_fallback_run_without_a_recorded_provider_failure(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    _host_root, path = _host(tmp_path, monkeypatch, FABLE)
    provider_transport(monkeypatch, review_content(), refuse_models=(FABLE["model"],))
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0, result

    # Re-label the Fable failure as one a different model could not avoid.
    state = yaml.safe_load(path.read_text(encoding="utf-8"))
    state["gates"][0]["runs"][0]["failure_reason"] = "reviewed-source-changed"
    path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")

    status = execute(ops.Status(feature=result["data"]["feature"]))
    assert not status.ok
    assert "differs from frozen reviewer slot" in status.error.message


def test_a_round_on_the_fallback_refuses_an_explicit_fable_run_and_suggests_opus(
    tmp_path, monkeypatch, run_cli
):
    _host_root, _path = _host(tmp_path, monkeypatch, FABLE)
    calls = provider_transport(
        monkeypatch,
        review_content(),
        refuse_models=(FABLE["model"], OPUS["model"]),
    )
    gate_command(run_cli, "run-gate", "spec-review")

    code, refused = gate_command(
        run_cli,
        "run-gate",
        "spec-review",
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        FABLE["reasoning_effort"],
    )

    assert code != 0 and not refused["ok"], refused
    message = refused["error"]["message"]
    assert "model fallback claude-opus-5-5 at xhigh" in message
    assert "--model claude-fable-5-2 names the frozen reviewer" in message
    assert len(calls) == 2
    status = invoke("Status", feature=V7_FEATURE)
    command = next(
        action.command for action in status.next_actions if "run-gate" in action.command
    )
    assert f"--model {OPUS['model']} --reasoning-effort xhigh" in command


def test_a_fable_round_inspects_the_findings_of_an_opus_fallback_run(
    tmp_path, monkeypatch, run_cli
):
    _host_root, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=FABLE, limit=3)},
    )
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
        refuse_models=(FABLE["model"],),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 3 and result["ok"], result
    origin = runs(path)[-1]["run_id"]
    assert runs(path)[-1]["execution_config"]["model"] == OPUS["model"]
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

    second_round = review_content()
    second_round["prior_dispositions"] = [
        reviewer_disposition(finding_ref(origin, "SP-I1"), action="addressed"),
        reviewer_disposition(finding_ref(origin, "@coverage"), action="addressed"),
    ]
    calls = provider_transport(monkeypatch, second_round)
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 3 and result["ok"], result
    assert [call[2][call[2].index("--model") + 1] for call in calls] == [FABLE["model"]]
    second = runs(path)[-1]["run_id"]

    recorded = dispose(
        path,
        [
            disposition(origin, "SP-I1", evidence_kind="review", review_run_id=second),
            disposition(second, "@coverage", status="settled"),
        ],
    )
    assert recorded.ok, recorded.to_envelope()


def test_a_run_that_waited_on_the_lock_takes_the_round_s_current_reviewer(
    tmp_path, monkeypatch, run_cli
):
    from heddle.gate.cli import GateInvocationOverrides, resolve_gate_execution
    from heddle.runtime import gate_run

    _host_root, path = _host(tmp_path, monkeypatch, FABLE)
    calls = provider_transport(
        monkeypatch,
        review_content(),
        refuse_models=(FABLE["model"], OPUS["model"]),
    )
    gate_command(run_cli, "run-gate", "spec-review")
    del calls[:]

    # Resolved before the lock, as if another run's fallback had not happened.
    resolve = gate_run._resolve_invocation
    stale = []

    def before_the_lock(snapshot, config, gate_type, parsed):
        invocation = resolve(snapshot, config, gate_type, parsed)
        if not stale:
            stale.append(invocation)
            return resolve_gate_execution(gate_type, GateInvocationOverrides(**FABLE))
        return invocation

    monkeypatch.setattr(gate_run, "_resolve_invocation", before_the_lock)
    code, result = gate_command(run_cli, "run-gate", "spec-review")

    assert code != 0, result
    assert [call[2][call[2].index("--model") + 1] for call in calls] == [OPUS["model"]]
    assert runs(path)[-1]["execution_config"]["model"] == OPUS["model"]


def test_a_due_fallback_that_cannot_start_fails_loudly(tmp_path, monkeypatch, run_cli):
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import gate_run

    _host_root, path = _host(tmp_path, monkeypatch, FABLE)
    calls = provider_transport(
        monkeypatch, review_content(), refuse_models=(FABLE["model"],)
    )
    resolve = gate_run._resolve_invocation
    resolutions = []

    def third_fails(*args):
        resolutions.append(args)
        if len(resolutions) == 3:
            raise KernelError(
                code="workspace-invalid", message="injected", hint="injected hint"
            )
        return resolve(*args)

    monkeypatch.setattr(gate_run, "_resolve_invocation", third_fails)
    code, result = gate_command(run_cli, "run-gate", "spec-review")

    assert code != 0 and not result["ok"], result
    message = result["error"]["message"]
    assert "failed with model-refusal" in message
    assert "Heddle's model fallback could not start: injected" in message
    assert len(calls) == 1
    assert [run["failure_reason"] for run in runs(path)] == ["model-refusal"]


def test_a_local_launch_failure_keeps_the_frozen_reviewer(
    tmp_path, monkeypatch, run_cli
):
    _host_root, path = _host(tmp_path, monkeypatch, FABLE)
    calls = provider_transport(monkeypatch, review_content())
    monkeypatch.setattr("heddle.gate.runner._binary_available", lambda _name: False)

    code, result = gate_command(run_cli, "run-gate", "spec-review")

    assert code != 0 and not result["ok"], result
    assert "fallback_from" not in result["error"]["details"]
    assert [
        (run["execution_config"]["model"], run["failure_reason"]) for run in runs(path)
    ] == [(FABLE["model"], "execution-failure")]

    monkeypatch.setattr("heddle.gate.runner._binary_available", lambda _name: True)
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0, result
    assert [call[2][call[2].index("--model") + 1] for call in calls] == [FABLE["model"]]


def _run(run_id, *, execution, status="error", reason="model-refusal", round_=1):
    from heddle.kernel.state import GateRun

    return GateRun(
        run_id=run_id,
        report_findings=(),
        at="2026-11-10T00:00Z",
        cli=execution["cli"],
        artifact=f"reviews/{run_id}.json",
        input_hash="0" * 64,
        verdict={"status": status},
        findings={},
        execution_config={**execution, "sandbox": "auto"},
        failure_reason=reason if status == "error" else None,
        assignment_id="a",
        round_number=round_,
        reviewer_slot="primary",
    )


@pytest.mark.parametrize(
    ("recorded", "expected"),
    (
        ((), FABLE),
        ((_run("r1", execution=FABLE),), OPUS),
        ((_run("r1", execution=FABLE, reason="truncated-output"),), OPUS),
        ((_run("r1", execution=FABLE, round_=2),), FABLE),
        ((_run("r1", execution=FABLE, reason="reviewed-source-changed"),), FABLE),
        ((_run("r1", execution=FABLE, reason="missing-plan-path"),), FABLE),
        ((_run("r1", execution=FABLE, status="pass", reason=None),), FABLE),
        ((_run("r1", execution=FABLE, reason="execution-failure"),), FABLE),
        # A Fable retry accepted before the fallback existed keeps serving.
        (
            (
                _run("r1", execution=FABLE),
                _run("r2", execution=FABLE, status="pass", reason=None),
            ),
            FABLE,
        ),
        (
            (
                _run("r1", execution=FABLE),
                _run("r2", execution=OPUS),
                _run("r3", execution=OPUS),
            ),
            OPUS,
        ),
    ),
)
def test_round_reviewer_switches_only_after_a_provider_failure_in_that_round(
    recorded, expected
):
    from heddle.contracts.feature_policy import Reviewer
    from heddle.contracts.review_assignments import ReviewerSlot
    from heddle.kernel.review_assignments import round_reviewer

    slot = ReviewerSlot("primary", Reviewer(**FABLE))
    assert round_reviewer(recorded, "a", 1, slot) == Reviewer(**expected)


def test_a_reviewer_without_a_fallback_keeps_its_slot():
    from heddle.contracts.feature_policy import Reviewer
    from heddle.contracts.review_assignments import ReviewerSlot
    from heddle.kernel.review_assignments import round_reviewer

    slot = ReviewerSlot("primary", Reviewer(**OPUS))
    failed = (_run("r1", execution=OPUS),)
    assert round_reviewer(failed, "a", 1, slot) == Reviewer(**OPUS)


def test_every_fallback_reason_is_a_gate_failure_reason():
    from heddle.contracts.gate_execution import FALLBACK_FAILURE_REASONS
    from heddle.gate.types import FailureReason

    assert FALLBACK_FAILURE_REASONS <= {reason.value for reason in FailureReason}
