"""
AC-1--AC-7: one bounded batch for independent final-review gates.

The tests use the public typed application boundary.  Provider work is replaced
at the existing gate-engine seam, while preparation, review validation,
artifact identity, recording, CAS, readiness, and result folding remain real.
"""

from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
import os
import signal
import socket
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from tests.tiering_helpers import OPUS, SOL, entry
from tests.tiering_review_helpers import V7_FEATURE, current_host

GROUP = ("behavior-review", "complexity-review")


def _batch_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str = "peer-review",
):
    return current_host(
        tmp_path,
        monkeypatch,
        stage=stage,
        overrides={
            "peer-review-sequential": entry(
                "peer-review-sequential",
                mode="off",
                limit=None,
                minimum_rounds=0,
                primary=OPUS,
            ),
            "behavior-review": entry("behavior-review", primary=OPUS),
            "complexity-review": entry("complexity-review", primary=SOL),
        },
    )


def _operation(*, feature: str = V7_FEATURE):
    from heddle.contracts import operations as ops

    assert hasattr(ops, "RunGates"), "FAIL AC-7: typed RunGates operation is missing"
    return ops.RunGates(feature=feature)


def _execute_batch(*, feature: str = V7_FEATURE):
    from heddle.runtime.application import execute

    return execute(_operation(feature=feature))


def _canonical_outcome(gate_type, context, feature: str):
    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.gate.cli import GateArgs
    from heddle.gate.entry import machine_projection_from_result
    from heddle.gate.io import gate_artifact_location
    from heddle.gate.results import (
        bind_review_result,
        decode_review_content,
        serialize_review_result,
    )
    from heddle.gate.types import GateCanonicalReview
    from tests.structured_review_helpers import (
        complete_fixture_coverage,
        complete_fixture_rerun,
        complete_fixture_verdict,
    )
    from tests.tiering_review_helpers import review_content

    prepared = context.prepared_run
    assert prepared is not None
    payload = review_content(gate_type.name)
    complete_fixture_coverage(payload, prepared.ac_ids, prepared.active_rules)
    complete_fixture_rerun(payload, prepared.prior_reviews, prepared.review_decisions)
    complete_fixture_verdict(payload, bool(prepared.prior_reviews))
    result = bind_review_result(
        decode_review_content(json.dumps(payload).encode(), prepared.output_contract),
        prepared,
        feature,
    )
    raw = serialize_review_result(result)
    digest = hashlib.sha256(raw).hexdigest()
    workspace = context.workspace_dir or context.plan_path.parent
    args = GateArgs(
        gate_name=gate_type.name,
        cli=prepared.invocation.exec_config.cli,
        feature=feature,
    )
    directory, name = gate_artifact_location(context, args)
    directory.mkdir(parents=True, exist_ok=True)
    artifact = directory / f"{name}.{digest}.review.json"
    artifact.write_bytes(raw)
    projection = machine_projection_from_result(result)
    return GateCanonicalReview(
        result=result,
        artifact=ArtifactRef(
            artifact.relative_to(workspace).as_posix(), digest, "canonical"
        ),
        structure_warnings=tuple(projection["structure_warnings"]),
    )


def _install_ordered_engine(
    monkeypatch: pytest.MonkeyPatch,
    manager,
    *,
    first: str = "behavior-review",
    failure: str | tuple[str, ...] | None = None,
):
    """Install a fork-safe engine double with a deterministic two-party barrier."""
    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.gate.types import GateEngineFailure

    starts = manager.list()
    finishes = manager.list()
    barrier = manager.Barrier(2)
    first_finished = manager.Event()

    def run(gate_type, context, *, feature, **_kwargs):
        role = gate_type.name
        prepared = context.prepared_run
        assert prepared is not None
        starts.append(
            {
                "gate": role,
                "input_hash": prepared.input_hash,
                "assignment_id": prepared.assignment_id,
                "round_number": prepared.round_number,
                "reviewer_slot": prepared.reviewer_slot,
            }
        )
        barrier.wait(timeout=10)
        if role != first:
            assert first_finished.wait(timeout=10)
        try:
            failures = (failure,) if isinstance(failure, str) else failure or ()
            if role in failures:
                prepared = context.prepared_run
                assert prepared is not None
                workspace = context.workspace_dir or context.plan_path.parent
                diagnostic = workspace / "reviews" / f"{role}.controlled-failure.json"
                diagnostic.parent.mkdir(parents=True, exist_ok=True)
                diagnostic.write_text(json.dumps({"gate": role}) + "\n")
                raw = diagnostic.read_bytes()
                return GateEngineFailure(
                    gate_exit=1,
                    reason="execution-failure",
                    rerun_recommended=None,
                    structure_warnings=("controlled provider failure",),
                    artifacts=(
                        ArtifactRef(
                            diagnostic.relative_to(workspace).as_posix(),
                            hashlib.sha256(raw).hexdigest(),
                            "temporary",
                        ),
                    ),
                )
            return _canonical_outcome(gate_type, context, feature)
        finally:
            finishes.append(role)
            if role == first:
                first_finished.set()

    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
    return starts, finishes


def _gate_runs(state_path: Path) -> list[dict]:
    value = yaml.safe_load(state_path.read_text())
    return [
        {"gate": gate["gate"], **run} for gate in value["gates"] for run in gate["runs"]
    ]


def test_ac2_gate_type_lock_ignores_scope_cli_model_and_slot(tmp_path: Path) -> None:
    """The same gate type has one exclusion key for a feature."""
    from heddle.gate.entry import gate_lock_path_for_runtime
    from heddle.gate.registry import GATES
    from heddle.gate.types import (
        GateContext,
        GateExecutionConfig,
        ResolvedGateInvocation,
    )

    gate = GATES["milestone-review"]
    base = GateContext(
        gate_type=gate,
        feature="lock-example",
        repo_root=tmp_path,
        plan_path=tmp_path / "plans/lock-example/plan.md",
        spec_path=tmp_path / "docs/features/runtime/lock-example.md",
        milestone="m1",
        workspace_dir=tmp_path / "plans/lock-example",
    )
    first = gate_lock_path_for_runtime(
        base,
        ResolvedGateInvocation(
            GateExecutionConfig("claude", "reviewer-a", "high", "read-only-tools"),
            frozenset({"cli", "model", "reasoning_effort"}),
        ),
    )
    second = gate_lock_path_for_runtime(
        replace(base, milestone="m2"),
        ResolvedGateInvocation(
            GateExecutionConfig("codex", "reviewer-b", "xhigh", "danger-full-access"),
            frozenset({"cli", "model", "reasoning_effort"}),
        ),
    )
    assert first == second, "FAIL AC-2: gate-type lock varies by scope or CLI"


def test_ac7_typed_group_is_closed_and_round_trips_as_one_command() -> None:
    """Callers can select the declared batch, not roles or worker counts."""
    import dataclasses

    from heddle.contracts.operations import operation_command, operation_payload

    operation = _operation(feature="round-trip-example")
    assert [field.name for field in dataclasses.fields(operation)] == ["feature"]
    assert operation_command(operation) == (
        "heddle run-gates --feature round-trip-example"
    )
    assert operation_payload(operation) == {
        "name": "run-gates",
        "arguments": {"feature": "round-trip-example"},
    }


@pytest.mark.parametrize(
    "argv",
    (
        ["run-gates", "behavior-review"],
        ["run-gates", "--workers", "3"],
        ["run-gates", "--cli", "claude"],
    ),
)
def test_ac2_public_batch_rejects_role_worker_and_lane_selection_before_launch(
    argv: list[str],
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _batch_host(tmp_path, monkeypatch)
    _operation()
    from heddle.gate import entry

    calls = 0

    def unexpected(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("FAIL AC-2: unsupported batch reached a provider")

    monkeypatch.setattr(entry, "run_gate_for_runtime", unexpected)
    code, stdout, _stderr = run_cli([*argv, "--feature", V7_FEATURE, "--json"])
    assert code == 2 and calls == 0
    assert "unsupported" in stdout or "usage" in stdout


@pytest.mark.parametrize("disabled", GROUP)
def test_ac2_disabled_member_returns_the_current_single_gate_action(
    disabled: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enabled = next(role for role in GROUP if role != disabled)
    choices = {
        role: entry(
            role,
            mode="off" if role == disabled else "upper-limit",
            limit=None if role == disabled else 2,
            minimum_rounds=0 if role == disabled else 1,
            primary=OPUS if role == "behavior-review" else SOL,
        )
        for role in GROUP
    }
    current_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={
            "peer-review-sequential": entry(
                "peer-review-sequential",
                mode="off",
                limit=None,
                minimum_rounds=0,
                primary=OPUS,
            ),
            **choices,
        },
    )
    result = _execute_batch()
    assert not result.ok
    commands = [action.command for action in result.next_actions]
    gate_commands = [command for command in commands if "run-gate" in command]
    assert len(gate_commands) == 1
    assert f"heddle run-gate {enabled} " in gate_commands[0]
    assert gate_commands[0].endswith(f"--feature {V7_FEATURE}")


def test_ac2_completed_member_makes_remaining_work_serial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _batch_host(tmp_path, monkeypatch)
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute
    from tests.tiering_review_helpers import provider_transport, review_content

    calls = provider_transport(
        monkeypatch,
        lambda _cli, prompt: review_content(
            next(role for role in GROUP if role in prompt)
        ),
    )
    completed = execute(ops.RunGate("behavior-review", feature=V7_FEATURE))
    assert completed.ok and len(calls) == 1
    result = _execute_batch()
    assert not result.ok and len(calls) == 1
    gate_commands = [
        action.command for action in result.next_actions if "run-gate" in action.command
    ]
    assert len(gate_commands) == 1
    assert "heddle run-gate complexity-review " in gate_commands[0]
    assert gate_commands[0].endswith(f"--feature {V7_FEATURE}")


def test_ac2_stage_ineligible_batch_returns_only_current_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _batch_host(tmp_path, monkeypatch, stage="implement")
    result = _execute_batch()
    assert not result.ok
    commands = [action.command for action in result.next_actions]
    assert commands
    assert not any(gate in command for gate in GROUP for command in commands)


@pytest.mark.parametrize("first", GROUP)
def test_ac1_ac3_batch_overlaps_and_publishes_in_declared_order(
    first: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both workers start before either finishes; publication order is stable."""
    _host, state_path = _batch_host(tmp_path, monkeypatch)
    before = yaml.safe_load(state_path.read_text())["revision"]
    with multiprocessing.Manager() as manager:
        starts, finishes = _install_ordered_engine(monkeypatch, manager, first=first)
        result = _execute_batch()
        started_roles = tuple(row["gate"] for row in starts)
        assert started_roles == GROUP or started_roles == tuple(reversed(GROUP))
        assert tuple(finishes) == (first, next(role for role in GROUP if role != first))
        prepared_rows = {row["gate"]: dict(row) for row in starts}

    assert result.ok, result.to_envelope()
    members = result.data["members"]
    assert tuple(member["gate"] for member in members) == GROUP
    assert all(member["publication"] == "recorded" for member in members)
    assert all(member["execution"] == "completed" for member in members)
    assert len({member["input_hash"] for member in members}) == 2
    assert len({member["assignment_id"] for member in members}) == 2
    assert (
        len(
            {
                (
                    member["assignment_id"],
                    member["round_number"],
                    member["reviewer_slot"],
                )
                for member in members
            }
        )
        == 2
    )
    assert len({member["artifact"] for member in members}) == 2
    for member in members:
        prepared = prepared_rows[member["gate"]]
        assert member["input_hash"] == prepared["input_hash"]
        assert member["assignment_id"] == prepared["assignment_id"]
        assert member["round_number"] == prepared["round_number"]
        assert member["reviewer_slot"] == prepared["reviewer_slot"]
    runs = _gate_runs(state_path)
    assert [run["gate"] for run in runs[-2:]] == list(GROUP)
    state = yaml.safe_load(state_path.read_text())
    ledger = state["review_assignments"]
    assert len(runs) == len(ledger["assignments"]) == len(ledger["attempts"]) == 2
    assert len({row["id"] for row in ledger["assignments"]}) == 2
    assert len({row["attempt_id"] for row in ledger["attempts"]}) == 2
    for member in members:
        run = next(row for row in runs if row["gate"] == member["gate"])
        assert run["input_hash"] == member["input_hash"]
        assert run["assignment_id"] == member["assignment_id"]
        assert run["round_number"] == member["round_number"]
        assert run["reviewer_slot"] == member["reviewer_slot"]
        assert run["artifact"] == member["artifact"]
    after = yaml.safe_load(state_path.read_text())["revision"]
    assert after == before + 2, "FAIL AC-3: each real publication advances once"


def test_ac4_exact_replay_adds_no_provider_call_credit_or_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host, state_path = _batch_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        starts, _finishes = _install_ordered_engine(monkeypatch, manager)
        first = _execute_batch()
        assert first.ok, first.to_envelope()
        before = state_path.read_bytes()
        call_count = len(starts)
        replay = _execute_batch()
        assert replay.ok, replay.to_envelope()
        assert len(starts) == call_count == 2
    assert state_path.read_bytes() == before
    assert [member["reuse"] for member in replay.data["members"]] == [
        "exact",
        "exact",
    ]


@pytest.mark.parametrize("conflict_at", ("before-first", "between-members"))
def test_ac5_unrelated_write_refuses_stale_publication_without_latest_merge(
    conflict_at: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed artifact remains, while the stale coordinator CAS fails."""
    _host, state_path = _batch_host(tmp_path, monkeypatch)
    from heddle.contracts import operations as ops
    from heddle.runtime import application, gate_run

    original = gate_run._record
    publications = 0

    def conflicted_record(*args, **kwargs):
        nonlocal publications
        publications += 1
        inject = (conflict_at == "before-first" and publications == 1) or (
            conflict_at == "between-members" and publications == 2
        )
        if inject:
            unrelated = application.execute(
                ops.CommandsSet(
                    "lint_command",
                    ".venv/bin/ruff check heddle",
                    feature=V7_FEATURE,
                )
            )
            assert unrelated.ok, unrelated.to_envelope()
        return original(*args, **kwargs)

    monkeypatch.setattr(gate_run, "_record", conflicted_record)
    with multiprocessing.Manager() as manager:
        starts, _finishes = _install_ordered_engine(monkeypatch, manager)
        result = _execute_batch()
        assert not result.ok
        members = result.error.details["members"]
        assert tuple(member["gate"] for member in members) == GROUP
        expected_recorded = 0 if conflict_at == "before-first" else 1
        assert sum(member["publication"] == "recorded" for member in members) == (
            expected_recorded
        )
        assert sum(member["publication"] == "recoverable" for member in members) == (
            2 - expected_recorded
        )
        assert len(_gate_runs(state_path)) == expected_recorded

        monkeypatch.setattr(gate_run, "_record", original)
        provider_calls = len(starts)
        revision = yaml.safe_load(state_path.read_text())["revision"]
        recovered = _execute_batch()
        assert recovered.ok, recovered.to_envelope()
        assert len(starts) == provider_calls == 2
        expected_reuse = (
            ["recovered", "recovered"]
            if conflict_at == "before-first"
            else ["exact", "recovered"]
        )
        assert [member["reuse"] for member in recovered.data["members"]] == (
            expected_reuse
        )
        after_recovery = yaml.safe_load(state_path.read_text())["revision"]
        assert after_recovery == revision + (2 - expected_recorded)
        assert len(_gate_runs(state_path)) == 2


def test_ac6_mixed_engine_failure_keeps_completed_sibling_and_all_remedies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host, state_path = _batch_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        _install_ordered_engine(
            monkeypatch,
            manager,
            first="complexity-review",
            failure="complexity-review",
        )
        result = _execute_batch()

    assert not result.ok
    members = result.error.details["members"]
    assert tuple(member["gate"] for member in members) == GROUP
    assert members[0]["execution"] == "completed"
    assert members[0]["publication"] == "recorded"
    assert members[1]["execution"] == "failed"
    assert members[1]["publication"] == "recorded-error"
    assert members[0]["next_actions"] == []
    assert [action["command"] for action in members[1]["next_actions"]] == [
        f"heddle run-gate complexity-review --feature {V7_FEATURE}"
    ]
    assert result.error.details["selected_member"] == "complexity-review"
    assert len(_gate_runs(state_path)) == 2


def test_ac6_aggregate_failure_ties_select_declared_gate_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _batch_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        _install_ordered_engine(
            monkeypatch,
            manager,
            failure=GROUP,
        )
        result = _execute_batch()
    assert not result.ok
    assert result.error.details["selected_member"] == "behavior-review"
    assert [row["execution"] for row in result.error.details["members"]] == [
        "failed",
        "failed",
    ]


def test_ac6_publication_error_precedes_later_engine_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-member CAS error outranks a later member engine failure."""
    _batch_host(tmp_path, monkeypatch)
    from heddle.contracts import operations as ops
    from heddle.runtime import application, gate_run

    original = gate_run._record
    injected = False

    def conflict_before_first(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            changed = application.execute(
                ops.CommandsSet(
                    "lint_command",
                    ".venv/bin/ruff check heddle",
                    feature=V7_FEATURE,
                )
            )
            assert changed.ok, changed.to_envelope()
        return original(*args, **kwargs)

    monkeypatch.setattr(gate_run, "_record", conflict_before_first)
    with multiprocessing.Manager() as manager:
        _install_ordered_engine(
            monkeypatch,
            manager,
            failure="complexity-review",
        )
        result = _execute_batch()

    assert not result.ok
    assert result.error.code == "workspace-invalid"
    assert result.error.details["selected_member"] == "behavior-review"
    assert [row["execution"] for row in result.error.details["members"]] == [
        "completed",
        "failed",
    ]


def test_ac6_interrupt_reaps_unfinished_workers_and_reports_durable_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker-triggered interrupt cannot leak its unfinished sibling."""
    host, _state_path = _batch_host(tmp_path, monkeypatch)
    from heddle.gate import entry
    from heddle.io.process import MonitorConfig, run_monitored

    with multiprocessing.Manager() as manager:
        barrier = manager.Barrier(2)
        pids = manager.dict()
        provider_signal = tmp_path / "provider-started.sock"

        def interrupted_run(gate_type, context, *, feature, **_kwargs):
            role = gate_type.name
            pids[role] = os.getpid()
            control = None
            if role == "behavior-review":
                control = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                control.bind(str(provider_signal))
                control.settimeout(10)
            barrier.wait(timeout=10)
            if role == "complexity-review":
                command = (
                    "import os, socket, time; "
                    "channel = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); "
                    "channel.sendto(str(os.getpid()).encode(), "
                    f"{str(provider_signal)!r}); "
                    "channel.close(); time.sleep(30)"
                )
                run_monitored(
                    [sys.executable, "-c", command],
                    tmp_path / "interrupted-provider.log",
                    MonitorConfig(30, 30, poll_s=0.05, term_grace_s=0.2),
                )
                return _canonical_outcome(gate_type, context, feature)
            outcome = _canonical_outcome(gate_type, context, feature)
            assert control is not None
            pids["provider"] = int(control.recv(64).decode())
            control.close()
            provider_signal.unlink(missing_ok=True)
            os.kill(os.getppid(), signal.SIGINT)
            return outcome

        monkeypatch.setattr(entry, "run_gate_for_runtime", interrupted_run)
        result = _execute_batch()
        worker_pids = dict(pids)

    assert not result.ok
    members = result.error.details["members"]
    assert tuple(member["gate"] for member in members) == GROUP
    assert members[0]["publication"] == "recoverable"
    assert members[1]["execution"] == "interrupted"
    for pid in worker_pids.values():
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    artifact_dir = host / f"plans/{V7_FEATURE}/reviews"
    artifacts = {
        path.name: path.read_bytes() for path in artifact_dir.glob("*.review.json")
    }
    assert artifacts


def test_ac6_retained_imperfect_member_keeps_bytes_and_interpret_remedy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _batch_host(tmp_path, monkeypatch)
    from tests.tiering_review_helpers import provider_transport, review_content

    def response(_cli, prompt):
        if "complexity-review" in prompt:
            return {"incomplete": "preserve these exact provider bytes"}
        return review_content("behavior-review")

    provider_transport(monkeypatch, response)
    result = _execute_batch()
    assert result.ok and result.exit_code == 4
    members = result.data["members"]
    retained = next(row for row in members if row["gate"] == "complexity-review")
    assert retained["execution"] == "retained"
    assert retained["publication"] == "retained"
    assert [action["command"] for action in retained["next_actions"]] == [
        "heddle review interpret --input-json - --expect-revision 2 "
        f"--feature {V7_FEATURE}"
    ]
    state = yaml.safe_load(state_path.read_text())
    retained_attempts = [
        attempt
        for attempt in state["review_assignments"]["attempts"]
        if attempt["outcome"]["kind"] == "usable-capture"
    ]
    assert len(retained_attempts) == 1
    artifact = host / f"plans/{V7_FEATURE}" / retained["artifact"]
    capture = json.loads(artifact.read_text())
    provider_bytes = base64.b64decode(
        capture["response"]["selected_response"]["base64"]
    )
    assert (
        provider_bytes
        == json.dumps({"incomplete": "preserve these exact provider bytes"}).encode()
    )

    provider_calls = 0

    def unexpected(_cli, _prompt):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("FAIL AC-6: retained restart launched another provider")

    provider_transport(monkeypatch, unexpected)
    retry = _execute_batch()
    assert not retry.ok and provider_calls == 0
    assert any("review interpret" in action.command for action in retry.next_actions)


def test_ac7_readiness_emits_one_batch_action_and_cli_returns_all_members(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host, _state_path = _batch_host(tmp_path, monkeypatch)
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    status = execute(ops.Status(feature=V7_FEATURE))
    commands = [action.command for action in status.next_actions]
    assert commands.count(f"heddle run-gates --feature {V7_FEATURE}") == 1
    assert not any("run-gate behavior-review" in command for command in commands)
    assert not any("run-gate complexity-review" in command for command in commands)

    with multiprocessing.Manager() as manager:
        _install_ordered_engine(monkeypatch, manager)
        code, stdout, _stderr = run_cli(
            ["run-gates", "--feature", V7_FEATURE, "--json"]
        )
    envelope = envelope_tools.parse(stdout)
    assert code == 0 and envelope["ok"]
    assert tuple(member["gate"] for member in envelope["data"]["members"]) == GROUP


def test_ac4_ac6_source_drift_retains_outputs_without_review_credit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _batch_host(tmp_path, monkeypatch)
    from heddle.gate import entry

    with multiprocessing.Manager() as manager:
        _install_ordered_engine(monkeypatch, manager)
        engine = entry.run_gate_for_runtime

        def mutate_after_completion(*args, **kwargs):
            outcome = engine(*args, **kwargs)
            if args[0].name == "behavior-review":
                spec = host / f"docs/features/runtime/{V7_FEATURE}.md"
                spec.write_text(spec.read_text() + "\nmaterial drift\n")
            return outcome

        monkeypatch.setattr(entry, "run_gate_for_runtime", mutate_after_completion)
        result = _execute_batch()

    assert not result.ok
    assert not any(run.get("accepted", False) for run in _gate_runs(state_path))
    assert all(
        member["publication"] in {"recorded-error", "recoverable"}
        for member in result.error.details["members"]
    )
    artifact_dir = host / f"plans/{V7_FEATURE}/reviews"
    artifacts = {
        path.name: path.read_bytes() for path in artifact_dir.glob("*.review.json")
    }
    assert artifacts

    calls = 0

    def unexpected(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("FAIL AC-4: changed source launched a provider")

    monkeypatch.setattr(entry, "run_gate_for_runtime", unexpected)
    retry = _execute_batch()
    assert not retry.ok and calls == 0
    assert "changed" in repr(retry.to_envelope()).lower()
    assert {
        path.name: path.read_bytes() for path in artifact_dir.glob("*.review.json")
    } == artifacts


@pytest.mark.parametrize(
    ("dimension", "changes"),
    (
        ("source", {"review_basis_hash": "different-source"}),
        ("policy", {"assignment_id": "different-policy-assignment"}),
        ("slot", {"reviewer_slot": "secondary"}),
        ("boundary", {"scope": "different-boundary"}),
    ),
)
def test_ac4_canonical_recovery_rejects_each_bound_identity_dimension(
    dimension: str,
    changes: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public restart refuses every stale canonical binding without a call."""
    host, state_path = _batch_host(tmp_path, monkeypatch)
    from heddle.gate import entry
    from heddle.runtime import gate_run

    original_record = gate_run._record
    injected = False

    def conflict_before_first(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            from heddle.contracts import operations as ops
            from heddle.runtime.application import execute

            changed_state = execute(
                ops.CommandsSet(
                    "lint_command",
                    ".venv/bin/ruff check heddle",
                    feature=V7_FEATURE,
                )
            )
            assert changed_state.ok, changed_state.to_envelope()
        return original_record(*args, **kwargs)

    monkeypatch.setattr(gate_run, "_record", conflict_before_first)
    with multiprocessing.Manager() as manager:
        _install_ordered_engine(monkeypatch, manager)
        initial = _execute_batch()
    assert not initial.ok and not _gate_runs(state_path)
    artifact_dir = host / f"plans/{V7_FEATURE}/reviews"
    before = {
        path.name: path.read_bytes() for path in artifact_dir.glob("*.review.json")
    }
    assert len(before) == 2

    monkeypatch.setattr(gate_run, "_record", original_record)
    original_prepare = entry.prepare_gate_run

    def changed_prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        return replace(prepared, **changes)

    monkeypatch.setattr(entry, "prepare_gate_run", changed_prepare)
    provider_calls = 0

    def unexpected(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError(f"FAIL AC-4: {dimension} drift launched a provider")

    monkeypatch.setattr(entry, "run_gate_for_runtime", unexpected)
    retry = _execute_batch()
    assert not retry.ok and provider_calls == 0
    assert not _gate_runs(state_path)
    assert {
        path.name: path.read_bytes() for path in artifact_dir.glob("*.review.json")
    } == before
    refusal = repr(retry.to_envelope()).lower()
    assert any(token in refusal for token in ("binding", "changed", "identity"))
