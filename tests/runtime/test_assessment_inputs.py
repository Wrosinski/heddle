"""Qualified assessment and narrow evidence-input behavior."""

from __future__ import annotations

import inspect
from dataclasses import fields
from datetime import date

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.runtime import application
from tests.structured_review_helpers import finding
from tests.tiering_helpers import ROLES, entry, snapshot
from tests.tiering_review_helpers import (
    V7_FEATURE,
    amend,
    current_host,
    dispose,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    runs,
)


def _enabled_scaffold_host(tmp_path, monkeypatch):
    return current_host(
        tmp_path,
        monkeypatch,
        stage="scaffold",
        overrides={
            "review-test-scaffolding": entry(
                "review-test-scaffolding", minimum_rounds=1, limit=2
            )
        },
    )


def _open_original_duty(tmp_path, monkeypatch, run_cli):
    host, state_path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    return host, state_path, calls, runs(state_path)[0]["run_id"]


def test_ac1_status_and_orient_share_fresh_authoring_route(tmp_path, monkeypatch):
    """AC-1 red: a fresh stage session outranks exit evidence on reads."""
    _host, state_path = _enabled_scaffold_host(tmp_path, monkeypatch)
    before = state_path.read_bytes()

    status = application.execute(ops.Status(feature=V7_FEATURE))
    orient = application.execute(ops.Orient(feature=V7_FEATURE))

    assert status.next_actions == orient.next_actions
    assert status.next_actions
    action = status.next_actions[0].action
    assert isinstance(action, ops.SessionAction)
    assert action == ops.SessionAction(V7_FEATURE, "stage-work")
    assert "gate-not-converged" in status.data["blocking_conditions"]
    assert state_path.read_bytes() == before


def test_ac1_reads_transition_and_driver_share_original_duty(
    tmp_path, monkeypatch, run_cli
):
    """AC-1 red: four consumers retain one blocker, duty, action, and purpose."""
    from heddle.driver import loop
    from heddle.driver.sessions import SessionResult
    from heddle.kernel.project_config import load_project_config

    host, state_path, calls, run_id = _open_original_duty(
        tmp_path, monkeypatch, run_cli
    )
    before = state_path.read_bytes()
    status = application.execute(ops.Status(feature=V7_FEATURE))
    orient = application.execute(ops.Orient(feature=V7_FEATURE))
    transition = application.execute(
        ops.PhaseExit(
            through="spec-review",
            feature=V7_FEATURE,
            expect_revision=yaml.safe_load(before)["revision"],
            dry_run=True,
        )
    )

    assert status.next_actions == orient.next_actions == transition.next_actions
    assert "gate-not-converged" in status.data["blocking_conditions"]
    assert status.data["assessment"]["purpose"] == "status"
    assert orient.data["assessment"]["purpose"] == "orient"
    assert status.data["assessment"]["original_obligations"] == [
        [run_id, "SP-I1"],
        [run_id, "@coverage"],
    ]

    prompts = []

    def session(spec, **_kwargs):
        prompts.append(spec.prompt_path.read_text())
        return SessionResult(exit_code=17, termination="completed", output={})

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    monkeypatch.setattr(loop, "spawn_phase_session", session)
    stopped = loop.run_loop(load_project_config(host), V7_FEATURE, until="plan-review")
    assert not stopped.ok and "17" in stopped.error.message
    assert len(prompts) == 1
    assert all(token in prompts[0] for token in (run_id, "SP-I1", "@coverage"))
    assert "observation purpose: driver" in prompts[0].lower()
    assert len(calls) == 1 and state_path.read_bytes() == before


def test_ac5_evidence_states_separate_applicability_from_integrity():
    """AC-5: the public vocabulary cannot alias absent scope with satisfaction."""
    from heddle.kernel import readiness

    evidence_state = getattr(readiness, "EvidenceState", None)
    assert evidence_state is not None
    assert {item.value for item in evidence_state} == {
        "not-applicable",
        "scope-not-defined",
        "missing",
        "current",
        "accepted",
        "stale",
        "invalid",
    }


def test_ac5_public_rows_distinguish_off_future_and_missing_proof(
    tmp_path, monkeypatch
):
    """AC-5 red: applicability stays separate across three real boundaries."""
    off = entry("spec-review", mode="off", limit=None, minimum_rounds=0)
    (tmp_path / "future").mkdir()
    _host, _path = current_host(
        tmp_path / "future",
        monkeypatch,
        stage="specify",
        overrides={"spec-review": off},
    )
    authored = application.execute(ops.Status(feature=V7_FEATURE))
    rows = authored.data["review_closure"]["assignments"]
    spec_row = next(row for row in rows if row["role"] == "spec-review")
    plan_row = next(row for row in rows if row["role"] == "plan-review")
    assert spec_row["evidence_state"] == "not-applicable"
    assert plan_row["evidence_state"] == "scope-not-defined"
    assert spec_row["closed"] and not plan_row["closed"]

    (tmp_path / "proof").mkdir()
    _host, _path = current_host(tmp_path / "proof", monkeypatch, stage="peer-review")
    missing = application.execute(ops.Status(feature=V7_FEATURE))
    verification = {row["scope"]: row for row in missing.data["verification_status"]}
    assert verification["acceptance"]["evidence_state"] == "missing"
    assert verification["smoke"]["evidence_state"] == "missing"
    assert "verification-missing" in missing.data["blocking_conditions"]
    assert all(verification[scope]["action"] for scope in ("acceptance", "smoke"))


def test_ac5_original_duty_survives_policy_disablement(tmp_path, monkeypatch, run_cli):
    """AC-5 red: Off changes scheduling without laundering original findings."""
    _host, state_path, calls, run_id = _open_original_duty(
        tmp_path, monkeypatch, run_cli
    )
    off = entry("spec-review", mode="off", limit=None, minimum_rounds=0)
    amended = amend(state_path, off, revision=2)
    assert amended.ok, amended.to_envelope()

    status = application.execute(ops.Status(feature=V7_FEATURE))
    row = next(
        item
        for item in status.data["review_closure"]["assignments"]
        if item["role"] == "spec-review"
    )
    assert row["open_refs"] == [[run_id, "SP-I1"], [run_id, "@coverage"]]
    assert row["evidence_state"] != "not-applicable"
    assert isinstance(status.next_actions[0].action, ops.AuthoringAction)
    assert len(calls) == 1


def test_ac6_assessment_keeps_causal_identity_on_each_evidence_row():
    """AC-6 red: explanations carry identities and causes, not inferred prose."""
    from heddle.kernel import readiness

    explanation_type = getattr(readiness, "EvidenceExplanation", None)
    assert explanation_type is not None
    assert {field.name for field in fields(explanation_type)} == {
        "subject",
        "cause",
        "changed_dependencies",
        "failed_reference",
        "remedy",
        "detail_limit",
    }


def test_ac6_simultaneous_assignments_expose_distinct_current_bases(
    tmp_path, monkeypatch, run_cli
):
    """AC-6 red: no global latest-basis value can join two assignment rows."""
    off = {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }
    _host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="spec-review",
        overrides={
            **off,
            "spec-review": entry("spec-review"),
            "milestone-review": entry("milestone-review"),
        },
    )
    provider_transport(monkeypatch, review_content("spec-review"))
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 0
    origin = runs(state_path)[-1]["run_id"]
    assert dispose(state_path, [disposition(origin, "@coverage", status="settled")]).ok
    for _stage in ("spec-review", "plan-review", "scaffold"):
        assert application.execute(ops.PhaseExit(feature=V7_FEATURE)).ok
    assert application.execute(ops.MilestoneAdvance(feature=V7_FEATURE)).ok

    provider_transport(monkeypatch, review_content("milestone-review"))
    assert gate_command(run_cli, "run-gate", "milestone-review")[0] == 0

    status = application.execute(ops.Status(feature=V7_FEATURE))
    rows = {
        (row["role"], row["scope"]): row
        for row in status.data["review_closure"]["assignments"]
    }
    feature_basis = rows[("spec-review", "feature")]["evidence_basis"]
    milestone_basis = rows[("milestone-review", "m1")]["evidence_basis"]
    assert feature_basis and milestone_basis and feature_basis != milestone_basis
    assert rows[("spec-review", "feature")]["evidence_state"] == "accepted"
    assert rows[("milestone-review", "m1")]["evidence_state"] == "current"
    accepted = runs(state_path)
    assert accepted[0]["input_hash"] != accepted[0]["review_basis_hash"]
    assert accepted[1]["input_hash"] != accepted[1]["review_basis_hash"]
    assert accepted[0]["review_basis_hash"] != accepted[1]["review_basis_hash"]


def test_ac6_scaffold_dispositions_follow_declared_tests_not_production(
    tmp_path, monkeypatch, run_cli
):
    """AC-6: scaffold closure follows its documents and declared test inputs."""
    host, state_path = _enabled_scaffold_host(tmp_path, monkeypatch)
    provider_transport(monkeypatch, review_content("review-test-scaffolding"))
    assert gate_command(run_cli, "run-gate", "review-test-scaffolding")[0] == 0
    run_id = runs(state_path)[0]["run_id"]
    row = disposition(
        run_id,
        "@coverage",
        status="settled",
        references=["tests/check.py"],
    )
    assert dispose(state_path, [row]).ok

    def scaffold_row():
        result = application.execute(ops.Status(feature=V7_FEATURE))
        assert result.ok, result.to_envelope()
        return next(
            item
            for item in result.data["review_closure"]["assignments"]
            if item["role"] == "review-test-scaffolding"
        )

    assert scaffold_row()["closed"]
    production = host / "src/example.py"
    production.write_text(production.read_text() + "\nVALUE_2 = 8\n")
    assert scaffold_row()["closed"]
    scaffold = host / "tests/check.py"
    scaffold.write_text(scaffold.read_text() + "\nassert True\n")
    assert scaffold_row()["open_refs"] == [[run_id, "@coverage"]]


def test_ac8_qualified_reads_use_narrow_validation_without_effects(
    tmp_path, monkeypatch, run_cli
):
    """AC-8 red: qualified reads validate evidence but invoke no effect owner."""
    from heddle.runtime import (
        decisions,
        feature_policy,
        gate_run,
        review_assignments,
        state_store,
    )

    _host, state_path = current_host(tmp_path, monkeypatch)
    provider_transport(monkeypatch, review_content())
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 0
    before = snapshot(state_path.parents[2])
    access_type = getattr(review_assignments, "ReviewEvidenceAccess", None)
    assert access_type is not None
    original = review_assignments.validate_sources
    validated = []

    def validate(access):
        assert isinstance(access, access_type)
        validated.append(access)
        return original(access)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a read surface invoked a workflow effect owner")

    with monkeypatch.context() as effects:
        effects.setattr(review_assignments, "validate_sources", validate)
        effects.setattr(state_store, "commit_state", forbidden)
        effects.setattr(gate_run, "execute_gate", forbidden)
        effects.setattr(feature_policy, "confirm_policy", forbidden)
        effects.setattr(decisions, "resolve_decision", forbidden)

        for operation in (
            ops.Status(feature=V7_FEATURE),
            ops.Orient(feature=V7_FEATURE),
        ):
            observed = application.execute(operation)
            assert observed.ok, observed.to_envelope()
    assert len(validated) == 2
    assert snapshot(state_path.parents[2]) == before


def test_ac8_portfolio_skips_manifests_and_reports_malformed_state(
    tmp_path, monkeypatch
):
    """AC-8 survivor: cheap listing skips evidence; malformed rows stay explicit."""
    from heddle.runtime import review_assignments

    _host, state_path = current_host(tmp_path, monkeypatch)

    def irrelevant(*_args, **_kwargs):
        raise AssertionError("portfolio loaded an irrelevant evidence manifest")

    monkeypatch.setattr(review_assignments, "validate_sources", irrelevant)
    listed = application.execute(ops.Status(all_features=True))
    assert listed.ok and listed.data["features"][0]["feature"] == V7_FEATURE

    state_path.write_text("schema: heddle.state/v9\nfeature: malformed\n")
    malformed = application.execute(ops.Status(all_features=True))
    assert malformed.ok
    row = malformed.data["features"][0]
    assert row["error"]["code"] == "workspace-invalid"
    assert state_path.read_text() == "schema: heddle.state/v9\nfeature: malformed\n"


def test_ac9_one_read_frame_reuses_source_observations(tmp_path, monkeypatch):
    """AC-9 survivor: one read shares its invocation-local observation map."""
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime import readiness, review_assignments

    host, _state_path = _enabled_scaffold_host(tmp_path, monkeypatch)
    config = load_project_config(host)
    feature_snapshot = resolve_snapshot(config, V7_FEATURE)
    seen: list[dict] = []
    original_projection = review_assignments.projection
    original_verifications = readiness.assess_current_verifications

    def projection(*args, source_observations=None, **kwargs):
        assert source_observations is not None
        seen.append(source_observations)
        return original_projection(
            *args, source_observations=source_observations, **kwargs
        )

    def verifications(*args, source_observations=None, **kwargs):
        assert source_observations is not None
        seen.append(source_observations)
        return original_verifications(
            *args, source_observations=source_observations, **kwargs
        )

    monkeypatch.setattr(review_assignments, "projection", projection)
    monkeypatch.setattr(readiness, "assess_current_verifications", verifications)
    readiness.assess_current_readiness(config, feature_snapshot)
    readiness.assess_current_readiness(config, feature_snapshot)
    assert len(seen) == 4
    assert seen[0] is seen[1]
    assert seen[2] is seen[3]
    assert seen[0] is not seen[2]


def test_ac9_execution_effect_forces_fresh_followup_assessment(
    tmp_path, monkeypatch, run_cli
):
    """AC-9: executing a displayed action cannot retain its pre-effect frame."""
    _host, state_path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", minimum_rounds=2, limit=3)},
    )
    provider_transport(monkeypatch, review_content())
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 0
    run_id = runs(state_path)[0]["run_id"]
    assert dispose(state_path, [disposition(run_id, "@coverage", status="settled")]).ok

    before = application.execute(ops.Status(feature=V7_FEATURE))
    action = before.next_actions[0]
    assert isinstance(action.action, ops.CommandAction)
    assert isinstance(action.action.operation, ops.ReviewRoundOpen)
    before_frame = before.data["assessment"]["observation_identity"]
    executed = application.execute(action.action.operation)
    assert executed.ok, executed.to_envelope()
    after = application.execute(ops.Status(feature=V7_FEATURE))
    assert after.data["assessment"]["observation_identity"] != before_frame
    row = next(
        item
        for item in after.data["review_closure"]["assignments"]
        if item["role"] == "spec-review"
    )
    assert row["rounds_used"] == 2 and row["next_step"] == "run"


def test_ac10_review_evidence_access_names_only_required_inputs():
    """AC-10: retained evidence reads accept a real narrow typed contract."""
    from heddle.runtime import readiness, review_assignments, trajectory

    access_type = getattr(review_assignments, "ReviewEvidenceAccess", None)
    assert access_type is not None
    assert {field.name for field in fields(access_type)} == {
        "root",
        "workspace",
        "feature",
        "state",
    }
    assert tuple(inspect.signature(review_assignments.validate_sources).parameters) == (
        "access",
    )
    assert "SimpleNamespace" not in inspect.getsource(readiness)
    assert "SimpleNamespace" not in inspect.getsource(trajectory)


@pytest.mark.parametrize("consumer", ["readiness", "trajectory"])
def test_ac10_consumers_preserve_containment_identity_and_integrity(
    tmp_path, monkeypatch, run_cli, consumer
):
    """AC-10 red: both consumers pass narrow inputs to the integrity owner."""
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import review_assignments, trajectory

    host, state_path = current_host(tmp_path, monkeypatch)
    provider_transport(monkeypatch, review_content())
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 0
    run = runs(state_path)[0]
    access_type = getattr(review_assignments, "ReviewEvidenceAccess", None)
    assert access_type is not None
    original = review_assignments.validate_sources
    received = []

    def validate(access):
        assert isinstance(access, access_type)
        assert access.root == host
        assert access.workspace == state_path.parent.relative_to(host).as_posix() + "/"
        assert access.feature == V7_FEATURE
        assert access.state.feature == V7_FEATURE
        received.append(access)
        return original(access)

    monkeypatch.setattr(review_assignments, "validate_sources", validate)
    if consumer == "readiness":
        result = application.execute(ops.Status(feature=V7_FEATURE))
        assert result.ok, result.to_envelope()
    else:
        assert (
            trajectory.archive_trajectory(
                host, V7_FEATURE, now=date(2026, 9, 13), dry_run=True
            )
            is not None
        )
    assert len(received) == 1

    artifact = state_path.parent / run["artifact"]
    artifact.write_text(artifact.read_text() + " ")
    with pytest.raises(KernelError, match="canonical.*changed|bytes differ"):
        original(received[0])
