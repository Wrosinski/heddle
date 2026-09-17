"""F1 component proof for truthful transition routing and results."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from heddle.contracts import operations as ops
from heddle.contracts.result import ExitCode
from heddle.kernel.model import resolve_snapshot
from heddle.kernel.project_config import KernelError, load_project_config
from heddle.kernel.write_path import set_command
from heddle.runtime import application
from heddle.runtime import write_path as transition_runtime
from heddle.runtime.state_store import commit_state
from tests.operational_model_helpers import FEATURE, read, write
from tests.readiness_helpers import verify
from tests.structured_review_helpers import finding
from tests.tiering_completion_helpers import final_host
from tests.tiering_helpers import entry
from tests.tiering_review_helpers import (
    current_host,
    gate_command,
    provider_transport,
    review_content,
    runs,
)


def _operation(result):
    assert len(result.next_actions) == 1, result.next_actions
    action = result.next_actions[0].action
    assert isinstance(action, ops.CommandAction), action
    return action.operation


@pytest.mark.parametrize("base", ["light", "full"])
def test_f1_a2_fresh_off_robustness_routes_to_one_guarded_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: str
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="robustness", base=base)
    before = resolve_snapshot(load_project_config(host.root), FEATURE)

    status = application.execute(ops.Status(feature=FEATURE))
    orient = application.execute(ops.Orient(feature=FEATURE))
    for result in (status, orient):
        operation = _operation(result)
        assert operation == ops.PhaseExit(
            feature=FEATURE, expect_revision=before.state.revision
        )

    transition = application.execute(_operation(status))
    assert transition.ok
    assert transition.data is not None
    assert transition.data["stage"] == "complete"
    assert transition.data["revision"] == before.state.revision + 1
    assert transition.data["phase_exit_summary"]["outcome"] == "stage-advanced"
    assert transition.data["phase_exit_summary"]["stage_before"] == "robustness"
    assert transition.data["phase_exit_summary"]["stage_after"] == "complete"
    assert isinstance(_operation(transition), ops.Kickoff)

    after = resolve_snapshot(load_project_config(host.root), FEATURE)
    assert after.stage == "complete"
    assert after.state.revision == before.state.revision + 1
    assert after.state.sessions == before.state.sessions


def test_f1_a12_driver_selects_wire_complete_guarded_exit_then_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.driver import loop

    host = final_host(tmp_path, monkeypatch, stage="robustness")
    config = load_project_config(host.root)
    before = resolve_snapshot(config, FEATURE)
    public = application.execute(ops.Status(feature=FEATURE))
    wire = public.to_envelope()["next_actions"][0]["action"]
    assert wire == {
        "kind": "command",
        "operation": {
            "name": "phase-exit",
            "arguments": {
                "through": None,
                "feature": FEATURE,
                "expect_revision": before.state.revision,
                "dry_run": False,
            },
        },
    }
    selected = loop._next_command(config, before, None)
    assert isinstance(selected, tuple)
    assert selected == (public.next_actions[0], None)

    operation = _operation(public)
    assert f"--expect-revision {before.state.revision}" in (
        ops.operation_command(operation)
    )
    advanced = application.execute(operation)
    assert advanced.ok
    completed = resolve_snapshot(config, FEATURE)
    paused = loop._next_command(config, completed, None)
    assert not isinstance(paused, tuple)
    assert not paused.ok and paused.error is not None
    assert paused.error.code == "awaiting-human-completion"
    assert isinstance(_operation(paused), ops.Kickoff)
    assert _operation(paused) == _operation(advanced)
    bounded = loop.run_loop(config, FEATURE, until="complete")
    assert bounded.ok and bounded.data is not None
    assert bounded.data["status"] == "until-reached"
    unbounded = loop.run_loop(config, FEATURE, until=None)
    assert not unbounded.ok and unbounded.error is not None
    assert unbounded.error.code == "awaiting-human-completion"
    assert resolve_snapshot(config, FEATURE).state.sessions == ()


@pytest.mark.parametrize("authorized_through", ["complete", "robustness"])
def test_f1_a3_stale_proof_prevents_off_robustness_shortcut_and_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized_through: str
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="robustness")
    state = read(host.state)
    state["authorized_through"] = authorized_through
    state["authorizations"][0]["through"] = authorized_through
    write(host.state, state)
    state_before = host.state.read_bytes()
    revision = read(host.state)["revision"]
    (host.root / "src/example.py").write_text("VALUE = 8\n", encoding="utf-8")

    status = application.execute(ops.Status(feature=FEATURE))
    operations = tuple(
        action.action.operation
        for action in status.next_actions
        if isinstance(action.action, ops.CommandAction)
    )
    assert operations
    assert all(isinstance(operation, ops.Verify) for operation in operations)
    assert all(operation.expect_revision == revision for operation in operations)

    refused = application.execute(
        ops.PhaseExit(feature=FEATURE, expect_revision=revision)
    )
    assert not refused.ok
    assert refused.error is not None
    assert refused.error.code == "verification-missing"
    assert host.state.read_bytes() == state_before


def test_f1_a5_invalid_policy_cannot_qualify_an_empty_robustness_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="robustness")
    malformed = read(host.state)
    malformed["feature_policy"]["entries"] = [
        row
        for row in malformed["feature_policy"]["entries"]
        if row["role"] != "robustness-analysis"
    ]
    write(host.state, malformed)
    before = host.state.read_bytes()

    result = application.execute(ops.Status(feature=FEATURE))

    assert not result.ok and result.error is not None
    assert result.error.code == "workspace-invalid"
    assert result.next_actions == ()
    assert host.state.read_bytes() == before


def test_f1_a4_turning_robustness_off_retains_native_original_duty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="robustness", overlay=True)
    calls = provider_transport(
        monkeypatch,
        review_content(
            "robustness-analysis",
            findings=[finding("RB-I1", classification="implement")],
        ),
    )
    code, envelope = gate_command(run_cli, "run-gate", "robustness-analysis")
    assert code == ExitCode.ADVISORY and envelope["ok"] is True
    run_id = runs(host.state)[-1]["run_id"]
    state = read(host.state)
    payload = deepcopy(state["feature_policy"])
    payload["revision"] = 2
    robustness = next(
        row for row in payload["entries"] if row["role"] == "robustness-analysis"
    )
    robustness.update(mode="off", limit=None, minimum_rounds=0, trigger=None)
    changed = application.execute(
        ops.FeaturePolicy(FEATURE, payload, expect_revision=state["revision"])
    )
    assert changed.ok, changed.to_envelope()
    before = host.state.read_bytes()

    result = application.execute(ops.Status(feature=FEATURE))

    assert result.ok and result.data is not None
    assert [run_id, "RB-I1"] in result.data["assessment"]["original_obligations"]
    assert not any(
        isinstance(action.action, ops.CommandAction)
        and isinstance(action.action.operation, ops.PhaseExit)
        for action in result.next_actions
    )
    assert host.state.read_bytes() == before
    assert len(calls) == 1


def test_f1_a11_enabled_robustness_and_other_fresh_stages_do_not_use_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled = final_host(
        tmp_path,
        monkeypatch,
        stage="robustness",
        overlay=True,
    )
    result = application.execute(ops.Status(feature=FEATURE))
    assert not any(
        isinstance(action.action, ops.CommandAction)
        and isinstance(action.action.operation, ops.PhaseExit)
        for action in result.next_actions
    )
    assert enabled.state.is_file()

    for stage in ("spec-review", "plan-review", "scaffold", "peer-review"):
        stage_root = tmp_path / stage
        stage_root.mkdir()
        host = final_host(stage_root, monkeypatch, stage=stage)
        stage_result = application.execute(ops.Status(feature=FEATURE))
        assert not any(
            isinstance(action.action, ops.CommandAction)
            and isinstance(action.action.operation, ops.PhaseExit)
            for action in stage_result.next_actions
        ), stage
        assert host.state.is_file()


def test_f1_a1_a7_phase_preview_separates_actual_and_projected_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="peer-review")
    before = read(host.state)
    state_bytes = host.state.read_bytes()

    preview = application.execute(
        ops.PhaseExit(
            through="complete",
            feature=FEATURE,
            expect_revision=before["revision"],
            dry_run=True,
        )
    )

    assert preview.ok and preview.data is not None
    assert preview.data["revision"] == before["revision"]
    assert preview.data["projected_revision"] == before["revision"] + 1
    assert preview.data["stage"] == "peer-review"
    assert preview.data["projected_stage"] == "robustness"
    assert preview.data["authorized_through"] == "complete"
    assert preview.data["projected_authorized_through"] == "complete"
    assert preview.data["mutation_summary"]["would_write"] is True
    assert preview.data["phase_exit_summary"]["outcome"] == "stage-advanced"
    assert preview.next_actions == ()
    assert host.state.read_bytes() == state_bytes


def test_f1_a7_human_preview_reports_the_same_transition_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="peer-review")
    revision = read(host.state)["revision"]

    code, stdout, stderr = run_cli(
        [
            "phase-exit",
            "--feature",
            FEATURE,
            "--expect-revision",
            str(revision),
            "--through",
            "complete",
            "--dry-run",
        ]
    )

    assert code == 0 and stderr == ""
    assert "phase-exit preview" in stdout
    assert "stage peer-review -> robustness" in stdout
    assert "authorization complete -> complete" in stdout
    assert f"revision {revision} (projected {revision + 1})" in stdout
    assert "would write true" in stdout


def test_f1_a7_unrelated_preview_preserves_its_human_and_json_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli
) -> None:
    import json

    host = final_host(tmp_path, monkeypatch, stage="robustness")
    before = host.state.read_bytes()
    revision = read(host.state)["revision"]
    command = [
        "commands",
        "set",
        "live_e2e_test",
        "--command",
        "python3 tests/check.py",
        "--feature",
        FEATURE,
        "--dry-run",
    ]
    code, stdout, stderr = run_cli(command)
    assert code == 0 and stderr == ""
    assert stdout == f"commands set [{FEATURE}] revision {revision + 1}\n"
    code, stdout, stderr = run_cli([*command, "--json"])
    assert code == 0 and stderr == ""
    data = json.loads(stdout)["data"]
    assert data["revision"] == revision + 1
    assert "projected_revision" not in data
    assert data["dry_run"] is True and data["wrote"] is False
    assert host.state.read_bytes() == before


def test_f1_a6_a7_phase_result_distinguishes_grant_only_and_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, path = current_host(
        tmp_path,
        monkeypatch,
        stage="scaffold",
        overrides={"review-test-scaffolding": entry("review-test-scaffolding")},
    )
    blocked = read(path)
    blocked["flow"] = "hitl"
    blocked["authorized_through"] = "scaffold"
    blocked["authorizations"] = [
        {"through": "scaffold", "source": "user", "at": blocked["updated"]}
    ]
    write(path, blocked)

    granted = application.execute(
        ops.PhaseExit(
            through="complete",
            feature=FEATURE,
            expect_revision=blocked["revision"],
        )
    )
    assert granted.ok and granted.data is not None
    assert granted.data["authorization_only"] is True
    assert granted.data["stage"] == "scaffold"
    assert granted.data["authorized_through"] == "complete"
    assert granted.data["phase_exit_summary"]["outcome"] == "authorization-only"
    assert read(path)["review_assignments"]["acceptances"] == []

    noop_root = tmp_path / "noop"
    noop_root.mkdir()
    off_host = final_host(noop_root, monkeypatch, stage="robustness")
    before_noop = read(off_host.state)
    noop = application.execute(
        ops.PhaseExit(
            through="robustness",
            feature=FEATURE,
            expect_revision=before_noop["revision"],
        )
    )
    assert noop.ok and noop.data is not None
    assert noop.data["wrote"] is False
    assert noop.data["revision"] == before_noop["revision"]
    assert noop.data["phase_exit_summary"]["outcome"] == "unchanged"
    assert read(off_host.state) == before_noop


def test_f1_a8_milestone_results_name_each_single_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={
            "milestone-review": entry(
                "milestone-review", mode="off", limit=None, minimum_rounds=0
            )
        },
    )
    value = read(path)
    value["milestones"][0]["tasks"] = [
        {"id": "m1-t1", "text": "finish part one", "status": "done"}
    ]
    write(path, value)
    verify(path, "m1")
    revision = read(path)["revision"]

    completed = application.execute(
        ops.MilestoneAdvance(feature=FEATURE, expect_revision=revision)
    )
    assert completed.ok and completed.data is not None
    assert completed.data["milestone_advance_summary"] == {
        "outcome": "milestone-completed",
        "milestone_id": "m1",
        "current_before": "m1",
        "current_after": None,
        "reason": "completed current milestone m1",
    }
    after_completed = read(path)
    assert [row["status"] for row in after_completed["milestones"]] == [
        "done",
        "todo",
    ]
    promoted_operation = _operation(completed)
    assert promoted_operation == ops.MilestoneAdvance(
        feature=FEATURE, expect_revision=after_completed["revision"]
    )

    promoted = application.execute(promoted_operation)
    assert promoted.ok and promoted.data is not None
    assert promoted.data["milestone_advance_summary"]["outcome"] == (
        "milestone-promoted"
    )
    assert promoted.data["milestone_advance_summary"]["milestone_id"] == "m2"
    assert [row["status"] for row in read(path)["milestones"]] == [
        "done",
        "current",
    ]
    assert host.is_dir()


def test_f1_a8_all_done_milestone_noop_explains_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="complete")
    before = read(host.state)

    result = application.execute(
        ops.MilestoneAdvance(feature=FEATURE, expect_revision=before["revision"])
    )

    assert result.ok and result.data is not None
    assert result.data["wrote"] is False
    assert result.data["revision"] == before["revision"]
    assert result.data["milestone_advance_summary"]["outcome"] == "unchanged"
    assert result.data["milestone_advance_summary"]["reason"] == (
        "all milestones are done"
    )
    assert read(host.state) == before


def test_f1_a10_post_commit_read_failure_preserves_committed_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="robustness")
    before = read(host.state)

    def fail_readback(_config, _feature):
        raise KernelError(
            "workspace-invalid",
            "injected post-commit read failure",
            "repair the state and re-read status",
        )

    monkeypatch.setattr(transition_runtime, "resolve_snapshot", fail_readback)
    result = application.execute(
        ops.PhaseExit(feature=FEATURE, expect_revision=before["revision"])
    )

    assert result.ok and result.exit_code == ExitCode.ADVISORY
    assert result.data is not None
    assert result.data["revision"] == before["revision"] + 1
    assert result.data["stage"] == "complete"
    assert result.data["phase_exit_summary"]["outcome"] == "stage-advanced"
    assert [item.code for item in result.diagnostics] == [
        "transition-guidance-unavailable"
    ]
    assert isinstance(_operation(result), ops.Status)
    after = read(host.state)
    assert after["revision"] == before["revision"] + 1
    assert after["stage"] == "complete"


def test_f1_a10_newer_post_commit_observation_is_not_attributed_to_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="robustness")
    before = read(host.state)
    real_resolve = transition_runtime.resolve_snapshot
    raced = False

    def competing_readback(config, feature):
        nonlocal raced
        if not raced:
            raced = True
            committed = real_resolve(config, feature)
            commit_state(
                host.state,
                expect_revision=committed.state.revision,
                transform=lambda document: set_command(
                    document,
                    key="live_e2e_test",
                    shell_command="python3 tests/check.py",
                ),
            )
        return real_resolve(config, feature)

    monkeypatch.setattr(transition_runtime, "resolve_snapshot", competing_readback)
    result = application.execute(
        ops.PhaseExit(feature=FEATURE, expect_revision=before["revision"])
    )

    assert result.ok and result.data is not None
    assert result.data["revision"] == before["revision"] + 1
    assert result.data["stage"] == "complete"
    assert result.data["guidance_observation"]["revision"] == (before["revision"] + 2)
    assert result.data["guidance_observation"]["newer_than_committed"] is True
    assert read(host.state)["commands"]["live_e2e_test"] == ("python3 tests/check.py")


def test_f1_a6_off_robustness_offers_human_exit_but_driver_cannot_self_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.driver import loop

    host = final_host(tmp_path, monkeypatch, stage="robustness")
    state = read(host.state)
    state["authorized_through"] = "robustness"
    state["authorizations"] = [
        {"through": "robustness", "source": "user", "at": state["updated"]}
    ]
    write(host.state, state)
    before = host.state.read_bytes()
    config = load_project_config(host.root)
    snapshot = resolve_snapshot(config, FEATURE)
    assert snapshot.blocking_conditions == ("awaiting-human-authorization",)

    status = application.execute(ops.Status(feature=FEATURE))
    orient = application.execute(ops.Orient(feature=FEATURE))
    operation = ops.PhaseExit(feature=FEATURE, expect_revision=state["revision"])
    for result in (status, orient):
        assert _operation(result) == operation
        assert "human authorization" in result.next_actions[0].reason

    # Exercise the actual boundary loop with startup probes controlled. Neither
    # a provider session nor a mutation is permitted while the grant is pending.
    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)

    def unexpected_work(*_args, **_kwargs):
        pytest.fail("driver attempted work before human authorization")

    with monkeypatch.context() as guarded:
        guarded.setattr(loop, "_execute_session_step", unexpected_work)
        guarded.setattr(application, "execute", unexpected_work)
        paused = loop.run_loop(config, FEATURE, until="complete")
    assert not paused.ok and paused.error is not None
    assert paused.error.code == "awaiting-human-authorization"
    assert _operation(paused) == operation
    assert host.state.read_bytes() == before

    advanced = application.execute(operation)
    assert advanced.ok and advanced.data is not None
    assert advanced.data["stage"] == "complete"
    after = read(host.state)
    assert after["revision"] == state["revision"] + 1
    assert after["authorizations"][-1]["source"] == "user"
    assert after["sessions"] == state["sessions"]
    assert after["completion"] is None
