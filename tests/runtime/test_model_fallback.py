"""Heddle's model fallback for Claude gate runs.

A Fable reviewer that fails for a provider reason is rerun once on Opus at
xhigh in the same command; both runs are recorded as the models that ran, and
the fallback serves the slot for the rest of the round.
"""

from __future__ import annotations

import pytest
import yaml

from tests.tiering_helpers import FABLE, OPUS, entry
from tests.tiering_review_helpers import (
    current_host,
    gate_command,
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
