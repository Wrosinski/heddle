"""AC-5 through AC-10: allowance integration with review assignment authority."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
import yaml

from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import finding, finding_ref
from tests.tiering_helpers import entry, invoke, snapshot
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

APPROVAL = "Owner raises this role to three absolute quality rounds"


def _state(path):
    return yaml.safe_load(path.read_text())


def _allowance(path, *, role="spec-review", limit=3, approval=APPROVAL, dry_run=False):
    return invoke(
        "ReviewAllowance",
        role=role,
        limit=limit,
        approval=approval,
        feature=V7_FEATURE,
        expect_revision=_state(path)["revision"],
        dry_run=dry_run,
    )


def _settle_coverage(path, run_id, *, retain_finding=False):
    rows = [disposition(run_id, "@coverage", status="settled")]
    if retain_finding:
        rows.insert(0, disposition(run_id, status="retained"))
    result = dispose(path, rows)
    assert result.ok, result.to_envelope()
    return result


def test_ac5_increase_preserves_usage_origins_and_closed_work(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    calls = provider_transport(monkeypatch, review_content())
    code, first = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0, first
    accepted = runs(path)[0]
    closed = _settle_coverage(path, accepted["run_id"])
    assert closed.data["closure"]["closed"]
    from heddle.contracts.decisions import DecisionInput
    from tests.runtime.write_path_helpers import authored_evidence

    seeded = invoke(
        "DecisionsAdd",
        decisions=(
            DecisionInput(
                kind="question",
                escalation_class=2,
                source="session",
                title="Preserved independent decision",
                question="Should unrelated follow-up work remain deferred?",
                options=("Keep deferred",),
                routes_to=("docs/features/runtime/operational-example.md",),
            ),
        ),
        feature=V7_FEATURE,
    )
    assert seeded.ok, seeded.to_envelope()
    value = _state(path)
    verification_log = path.parent / "verification/preserved.log"
    verification_log.parent.mkdir(parents=True, exist_ok=True)
    verification_log.write_text("preserved verification\n")
    value["verifications"] = [
        {
            "scope": "acceptance",
            "command": "python3 -c 'print(1)'",
            "exit_code": 0,
            "at": "2026-09-13T00:00Z",
            "log": "verification/preserved.log",
            "evidence": authored_evidence(
                path, ("src/example.py",), kind="feature-owned-union"
            ),
        }
    ]
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    before = _state(path)
    before_files = snapshot(host)
    assert before["decisions"] and before["verifications"]

    amended = _allowance(path)
    assert amended.ok, amended.to_envelope()
    after = _state(path)
    current = review_status(path)
    assert current["assignment_id"] == accepted["assignment_id"]
    assert current["rounds_used"] == current["calls_completed"] == 1
    assert current["rounds_remaining"] == 2 and current["closed"]
    assert after["review_assignments"] == before["review_assignments"]
    assert after["gates"] == before["gates"]
    assert after["decisions"] == before["decisions"]
    assert after["verifications"] == before["verifications"]
    state_name = "plans/operational-example/state.yaml"
    assert {
        key: value for key, value in snapshot(host).items() if key != state_name
    } == {key: value for key, value in before_files.items() if key != state_name}
    assert len(calls) == 1
    # Closed, unsealed work may still take an explicit later verification
    # round; the allowance write itself opened none.
    assert review_status(path)["rounds_used"] == 1
    assert runs(path) == [accepted]


def test_ac6_milestone_role_preview_discloses_all_scopes_and_budget(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review", limit=1)},
    )
    calls = provider_transport(monkeypatch, review_content("milestone-review"))
    code, first = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 0, first
    accepted = runs(path)[0]
    assert accepted["round_number"] == 1 and first["data"]["scope"] == "m1"
    before = snapshot(host)
    preview = _allowance(
        path,
        role="milestone-review",
        limit=4,
        approval="Owner raises all milestone reviews to four quality rounds",
        dry_run=True,
    )
    assert preview.ok, preview.to_envelope()
    data = preview.data
    assert data["role_scope"] == "milestone"
    assert data["future_milestone_assignments"] is True
    assert {row["scope"] for row in data["affected_assignments"]} == {"m1", "m2"}
    assert all(
        row["quality_round_limit_after"] == 4 for row in data["affected_assignments"]
    )
    m1 = next(row for row in data["affected_assignments"] if row["scope"] == "m1")
    m2 = next(row for row in data["affected_assignments"] if row["scope"] == "m2")
    assert (m1["quality_rounds_used"], m1["provider_calls_completed"]) == (1, 1)
    assert m1["newly_available_quality_rounds"] == 3
    assert (m2["quality_rounds_used"], m2["provider_calls_completed"]) == (0, 0)
    assert m2["newly_available_quality_rounds"] == 3
    assert data["call_budget"]["before"] == {
        "feature_minimum": 1,
        "per_milestone_minimum": 1,
        "feature_maximum": 2,
        "per_milestone_maximum": 1,
    }
    assert data["call_budget"]["after"] == {
        "feature_minimum": 1,
        "per_milestone_minimum": 1,
        "feature_maximum": 2,
        "per_milestone_maximum": 4,
    }
    assert data["quality_round_limit"] == {"before": 1, "after": 4, "added": 3}
    assert snapshot(host) == before
    written = _allowance(
        path,
        role="milestone-review",
        limit=4,
        approval="Owner raises all milestone reviews to four quality rounds",
    )
    assert written.ok, written.to_envelope()
    transaction_fields = {"revision", "wrote", "dry_run", "readback"}
    assert {k: v for k, v in preview.data.items() if k not in transaction_fields} == {
        k: v for k, v in written.data.items() if k not in transaction_fields
    }
    assert len(calls) == 1 and runs(path) == [accepted]


def test_ac7_allowance_does_not_resolve_the_assignment_stop(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops

    _host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    calls = provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    assert gate_command(run_cli, "run-gate", "spec-review")[0] in (0, 4)
    origin = runs(path)[0]["run_id"]
    _settle_coverage(path, origin, retain_finding=True)
    assert not open_round(path).ok
    stopped = review_status(path)
    decision_id = stopped["decision_id"]
    assert decision_id is not None

    amended = _allowance(path)
    assert amended.ok, amended.to_envelope()
    still_stopped = review_status(path)
    assert still_stopped["decision_id"] == decision_id
    decision = next(
        row for row in _state(path)["decisions"] if row["id"] == decision_id
    )
    assert decision["status"] == "pending"
    assert not open_round(path).ok and len(calls) == 1

    resolved = invoke(
        "ResolveDecision",
        decision_id=decision_id,
        kind="continue-review",
        rationale="Owner separately authorizes one next round after raising the cap",
        routes_to="docs/features/runtime/operational-example.md",
        feature=V7_FEATURE,
    )
    assert resolved.ok, resolved.to_envelope()
    assert open_round(path).ok
    operation = ops.ReviewAllowance("spec-review", 3, APPROVAL, V7_FEATURE, 0)
    assert isinstance(operation, ops.ReviewAllowance)


def _create_stop(path, run_cli, monkeypatch, stop):
    generation = {"number": 1}

    def response(_cli, _prompt):
        if stop == "no-progress" and generation["number"] > 1:
            result = review_content()
            # A verification round accounts for every original target. Reviewer
            # accounting neither replaces the lead's retained resolution nor
            # counts as material progress on that still-open original.
            result["prior_dispositions"] = [
                reviewer_disposition(
                    finding_ref(run_id, finding_id), action="addressed"
                )
                for run_id, finding_id in (
                    (runs(path)[0]["run_id"], "SP-I1"),
                    *((run["run_id"], "@coverage") for run in runs(path)),
                )
            ]
            return result
        return review_content(
            findings=[
                finding(f"SP-I{generation['number']}", classification="implement")
            ]
        )

    calls = provider_transport(monkeypatch, response)
    assert gate_command(run_cli, "run-gate", "spec-review")[0] in (0, 4)
    origin = runs(path)[0]["run_id"]
    _settle_coverage(path, origin, retain_finding=True)
    if stop != "round-limit":
        opened = open_round(
            path,
            purpose="discovery" if stop == "no-decrease" else "verification",
        )
        assert opened.ok
        generation["number"] = 2
        assert gate_command(run_cli, "run-gate", "spec-review")[0] in (0, 4)
        _settle_coverage(path, runs(path)[-1]["run_id"])
    assert not open_round(path).ok
    assert review_status(path)["stop_reason"] == stop
    return calls


@pytest.mark.parametrize("stop", ["round-limit", "no-progress", "no-decrease"])
def test_ac7_every_stop_and_its_rounds_remain_unchanged_by_allowance(
    tmp_path, monkeypatch, run_cli, stop
):
    _host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": entry("spec-review", limit=1 if stop == "round-limit" else 9)
        },
    )
    calls = _create_stop(path, run_cli, monkeypatch, stop)
    before = _state(path)
    before_runs = runs(path)
    current_limit = next(
        row["limit"]
        for row in before["feature_policy"]["entries"]
        if row["role"] == "spec-review"
    )
    amended = _allowance(path, limit=current_limit + 1)
    assert amended.ok, amended.to_envelope()
    after = _state(path)
    assert after["decisions"] == before["decisions"]
    assert after["review_assignments"] == before["review_assignments"]
    assert runs(path) == before_runs
    assert len(calls) == len(before_runs)
    assert review_status(path)["stop_reason"] == stop


def test_ac8_allowance_preview_write_and_readback_have_no_execution_effect(
    tmp_path, monkeypatch
):
    host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content())
    from heddle.io import process

    def forbidden_subprocess(*_args, **_kwargs):
        raise AssertionError("review allowance must not launch a subprocess")

    monkeypatch.setattr(process.subprocess, "Popen", forbidden_subprocess)
    monkeypatch.setattr(process.subprocess, "run", forbidden_subprocess)
    before = snapshot(host)
    preview = _allowance(path, dry_run=True)
    assert preview.ok
    written = _allowance(path)
    assert written.ok
    replay = _allowance(path)
    assert replay.ok and replay.data["readback"] and not replay.data["wrote"]
    assert calls == []
    state_name = "plans/operational-example/state.yaml"
    assert {
        key: value for key, value in snapshot(host).items() if key != state_name
    } == {key: value for key, value in before.items() if key != state_name}
    state = _state(path)
    assert state["review_assignments"]["attempts"] == []
    assert state["gates"] == []
    assert state["decisions"] == [] and state["verifications"] == []


def test_ac8_spent_cap_exact_gate_replay_precedes_new_admission(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    calls = provider_transport(monkeypatch, review_content())
    code, first = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and first["data"]["cached"] is False, first
    attempts = deepcopy(_state(path)["review_assignments"]["attempts"])
    artifacts = {
        row["path"]: (path.parent / row["path"]).read_bytes()
        for row in attempts[0]["artifacts"]
    }
    before_spent_replay = snapshot(host)
    code, spent_replay = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and spent_replay["data"]["cached"] is True, spent_replay
    assert len(calls) == 1
    assert _state(path)["review_assignments"]["attempts"] == attempts
    assert {
        relative: (path.parent / relative).read_bytes() for relative in artifacts
    } == artifacts
    assert snapshot(host) == before_spent_replay

    assert _allowance(path, limit=3).ok
    before_replay = snapshot(host)

    code, replay = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and replay["data"]["cached"] is True, replay
    assert len(calls) == 1
    assert _state(path)["review_assignments"]["attempts"] == attempts
    assert {
        relative: (path.parent / relative).read_bytes() for relative in artifacts
    } == artifacts
    assert snapshot(host) == before_replay


def test_ac9_cap_readers_share_the_owner_allowance_remedy(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops
    from heddle.driver import loop
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from tests.readiness_helpers import current_readiness

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    assert gate_command(run_cli, "run-gate", "spec-review")[0] in (0, 4)
    origin = runs(path)[0]["run_id"]
    _settle_coverage(path, origin, retain_finding=True)
    assert not open_round(path).ok
    stopped = review_status(path)
    resolved = invoke(
        "ResolveDecision",
        decision_id=stopped["decision_id"],
        kind="continue-review",
        rationale="Owner separately authorizes one next review round",
        routes_to="docs/features/runtime/operational-example.md",
        feature=V7_FEATURE,
    )
    assert resolved.ok
    readiness = current_readiness(path.parents[2])
    action = readiness.next_actions[0]
    assert isinstance(action.action, ops.ManualAction)
    command = action.command
    for token in (
        "heddle review allowance",
        "--role spec-review",
        "--limit ABSOLUTE_TOTAL",
        "--approval OWNER_APPROVAL",
        f"--feature {V7_FEATURE}",
        f"--expect-revision {_state(path)['revision']}",
    ):
        assert token in command
    for name in ("Status", "Orient"):
        observed = invoke(name, feature=V7_FEATURE)
        assert observed.ok
        assert observed.next_actions[0].command == command
    before_driver = snapshot(host)
    config = load_project_config(host)
    current = resolve_snapshot(config, V7_FEATURE)
    routed = loop._next_command(config, current, None)
    assert isinstance(routed, tuple)
    assert routed[0].action == action.action
    assert routed[1] == "gate-not-converged"
    assert "Observation purpose: driver" in routed[0].reason
    assert snapshot(host) == before_driver


def test_ac10_controlled_new_round_calls_one_provider_and_retains_immutable_attempt(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    calls = provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    assert gate_command(run_cli, "run-gate", "spec-review")[0] in (0, 4)
    first = runs(path)[0]
    first_attempt = deepcopy(_state(path)["review_assignments"]["attempts"][0])
    first_artifacts = {
        row["path"]: (path.parent / row["path"]).read_bytes()
        for row in first_attempt["artifacts"]
    }
    assert first_artifacts
    assert all(
        hashlib.sha256(content).hexdigest()
        == next(
            row["sha256"]
            for row in first_attempt["artifacts"]
            if row["path"] == relative
        )
        for relative, content in first_artifacts.items()
    )
    _settle_coverage(path, first["run_id"], retain_finding=True)
    assert not open_round(path).ok
    stopped = review_status(path)
    assert _allowance(path).ok
    assert len(calls) == 1
    assert invoke(
        "ResolveDecision",
        decision_id=stopped["decision_id"],
        kind="continue-review",
        rationale="Owner separately authorizes the newly available round",
        routes_to="docs/features/runtime/operational-example.md",
        feature=V7_FEATURE,
    ).ok
    opened = open_round(path)
    assert opened.ok and opened.data["round_number"] == 2
    before_call = snapshot(host)
    code, second = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4) and second["data"]["cached"] is False
    assert len(calls) == 2
    attempts = _state(path)["review_assignments"]["attempts"]
    latest = attempts[-1]
    assert latest["round_number"] == 2
    logs = [row for row in latest["artifacts"] if row["role"] == "log"]
    assert len(logs) == 1 and logs[0]["sha256"] in logs[0]["path"]
    assert attempts[0] == first_attempt
    assert {
        relative: (path.parent / relative).read_bytes() for relative in first_artifacts
    } == first_artifacts
    assert all(
        hashlib.sha256((path.parent / row["path"]).read_bytes()).hexdigest()
        == row["sha256"]
        for row in latest["artifacts"]
    )
    assert snapshot(host) != before_call
    operation = ops.ReviewAllowance("spec-review", 3, APPROVAL, V7_FEATURE, 0)
    assert isinstance(operation, ops.ReviewAllowance)


def test_ac9_public_help_and_search_use_quality_round_vocabulary(run_cli):
    code, out, _err = run_cli(["help", "--json"])
    assert code == 0
    manifest = json.loads(out)
    command = next(
        row for row in manifest["data"]["commands"] if row["name"] == "review allowance"
    )
    text = json.dumps(command).lower()
    assert "quality round" in text and "provider" in text


def test_ac9_governing_guidance_uses_one_round_call_and_stop_model():
    from tests.runtime.wheel_harness import REPO_ROOT

    paths = (
        "docs/workflow/workflow.md",
        "docs/design/architecture.md",
        "heddle/resources/gate-failure-guide.md",
        "heddle/resources/scaffold.briefing.md",
        "heddle/resources/implement.briefing.md",
        "heddle/resources/peer-review.briefing.md",
    )
    for relative in paths:
        text = " ".join((REPO_ROOT / relative).read_text().lower().split())
        assert "review allowance" in text, relative
        assert "quality round" in text, relative
        assert "provider" in text, relative

    workflow = (REPO_ROOT / "docs/workflow/workflow.md").read_text().lower()
    assert "continue-review" in workflow
    assert "review round-open" in workflow
