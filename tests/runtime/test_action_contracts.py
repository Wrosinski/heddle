"""Complete command, authoring, and decision action contracts."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import yaml

from heddle.contracts import operations as ops
from heddle.contracts.result import HeddleResult, NextAction
from heddle.runtime import application
from tests.structured_review_helpers import finding
from tests.tiering_helpers import (
    blank_host,
    entry,
    prepare_input,
    snapshot,
    wire_policy,
)
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    review_status,
    runs,
)


def _action_type(name: str):
    action_type = getattr(ops, name, None)
    assert action_type is not None, f"missing typed {name} contract"
    return action_type


def test_ac1_ac2_pending_intake_routes_complete_admission_actions(
    tmp_path, monkeypatch
):
    """B1: prepare, orient, and policy form a guarded native admission chain."""
    host = blank_host(tmp_path, monkeypatch)
    (host / "brief.md").write_text("# Research\nOne declared behavior.\n")
    prepared = application.execute(
        ops.FeaturePrepare("pending-demo", "runtime", prepare_input())
    )
    assert prepared.ok, prepared.to_envelope()
    authoring = prepared.next_actions[0].action
    assert isinstance(authoring, ops.AuthoringAction)
    assert (
        authoring
        == application.execute(ops.Orient(feature="pending-demo"))
        .next_actions[0]
        .action
    )
    assert authoring.work == "feature-policy"
    assert authoring.references == (
        ".heddle/intake/pending-demo.yaml",
        "brief.md",
    )
    assert authoring.input_schema == "heddle.feature-policy/v1"
    assert authoring.expected_revision == prepared.data["revision"]

    confirmed = application.execute(
        ops.FeaturePolicy(
            "pending-demo",
            wire_policy(),
            expect_revision=authoring.expected_revision,
        )
    )
    assert confirmed.ok, confirmed.to_envelope()
    start = confirmed.next_actions[0].action
    assert isinstance(start, ops.CommandAction)
    assert isinstance(start.operation, ops.FeatureStart)
    assert start.operation == ops.FeatureStart(
        "pending-demo",
        area="runtime",
        flow="hitl",
        expect_revision=confirmed.data["revision"],
    )
    admitted = application.execute(start.operation)
    assert admitted.ok, admitted.to_envelope()
    assert isinstance(admitted.next_actions[0].action, ops.CommandAction)
    assert isinstance(admitted.next_actions[0].action.operation, ops.FeatureSwitch)


def test_ac2_pending_intake_action_revalidates_and_orient_selects_explicitly(
    tmp_path, monkeypatch
):
    """B1: stale admission conflicts and multiple intakes require a real choice."""
    host = blank_host(tmp_path, monkeypatch)
    (host / "brief.md").write_text("# Research\nOne declared behavior.\n")
    starts = []
    for slug in ("alpha-intake", "beta-intake"):
        prepared = application.execute(
            ops.FeaturePrepare(slug, "runtime", prepare_input())
        )
        assert prepared.ok, prepared.to_envelope()
        assert isinstance(prepared.next_actions[0].action, ops.AuthoringAction)
    before = snapshot(host)
    selection = application.execute(ops.Orient())
    action = selection.next_actions[0].action
    assert isinstance(action, ops.DecisionAction)
    assert action.decision_id == "pending-intake-selection"
    assert action.choices == ("alpha-intake", "beta-intake")
    assert snapshot(host) == before

    selected = application.execute(ops.Orient(feature="alpha-intake"))
    authoring = selected.next_actions[0].action
    assert isinstance(authoring, ops.AuthoringAction)
    confirmed = application.execute(
        ops.FeaturePolicy(
            "alpha-intake",
            wire_policy(),
            expect_revision=authoring.expected_revision,
        )
    )
    starts.append(confirmed.next_actions[0].action.operation)
    (host / "brief-2.md").write_text("# Changed research\nTwo behaviors.\n")
    changed_input = prepare_input()
    changed_input["research"] = {
        "reference": "brief-2.md",
        "summary": "Two declared behaviors",
    }
    changed = application.execute(
        ops.FeaturePrepare("alpha-intake", "runtime", changed_input)
    )
    assert changed.ok, changed.to_envelope()
    stale = application.execute(starts[0])
    assert not stale.ok and stale.error.code == "conflict"


def test_ac2_executable_actions_keep_complete_operations_and_revision_guards():
    """AC-2 survivor: complete command operations retain guarded wire inputs."""
    action = ops.CommandAction(
        ops.ReviewRoundOpen(
            feature="demo",
            payload={
                "schema": "heddle.review-round-input/v1",
                "role": "behavior-review",
                "scope": "feature",
                "purpose": "verification",
                "reason": "inspect the repaired behavior",
            },
            expect_revision=41,
        )
    )
    row = HeddleResult.success(
        {}, next_actions=(NextAction(action, "run"),)
    ).to_envelope()["next_actions"][0]
    assert row["action"]["operation"]["arguments"]["expect_revision"] == 41


def test_ac2_public_executable_action_progresses_or_revalidates(
    tmp_path, monkeypatch, run_cli
):
    """AC-2 survivor: a current action progresses and a stale one conflicts."""
    _host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", minimum_rounds=2, limit=3)},
    )
    provider_transport(monkeypatch, review_content())
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0, result
    run_id = runs(path)[0]["run_id"]
    assert dispose(path, [disposition(run_id, "@coverage", status="settled")]).ok

    advertised = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0]
    assert isinstance(advertised.action, ops.CommandAction)
    assert isinstance(advertised.action.operation, ops.ReviewRoundOpen)
    operation = advertised.action.operation
    assert operation.payload and operation.expect_revision is not None

    changed = application.execute(
        ops.CommandsSet("smoke_test", "true", feature=V7_FEATURE)
    )
    assert changed.ok
    stale = application.execute(operation)
    assert not stale.ok and stale.error.code == "conflict"
    current = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0]
    assert isinstance(current.action, ops.CommandAction)
    assert application.execute(current.action.operation).ok


def test_ac2_public_proof_action_is_complete_and_reauthorized(tmp_path, monkeypatch):
    """AC-2 red: proof guidance carries current feature, scope, and revision."""
    _host, path = current_host(tmp_path, monkeypatch, stage="peer-review")
    observed = application.execute(ops.Status(feature=V7_FEATURE))
    action = next(
        item
        for item in observed.next_actions
        if isinstance(item.action, ops.CommandAction)
        and isinstance(item.action.operation, ops.Verify)
    )
    operation = action.action.operation
    revision = yaml.safe_load(path.read_text())["revision"]
    assert operation.feature == V7_FEATURE
    assert operation.scope in {"acceptance", "smoke"}
    assert operation.expect_revision == revision

    changed = application.execute(
        ops.CommandsSet(
            "smoke_test",
            "python -c 'raise SystemExit(0)'",
            feature=V7_FEATURE,
            expect_revision=revision,
        )
    )
    assert changed.ok, changed.to_envelope()
    stale = application.execute(operation)
    assert not stale.ok and stale.error.code == "conflict"


def test_ac3_authoring_action_preserves_exact_original_work():
    """AC-3 structural red pin: authored work has no subjective payload field."""
    authoring_type = _action_type("AuthoringAction")
    action = authoring_type(
        "demo",
        "review-disposition",
        ("run-1#finding-1",),
        "heddle.review-disposition-input/v1",
        8,
    )
    assert (
        "payload"
        not in HeddleResult.success(
            {}, next_actions=(NextAction(action, "author"),)
        ).to_envelope()["next_actions"][0]["action"]
    )


def test_ac3_public_authoring_action_preserves_original_work(
    tmp_path, monkeypatch, run_cli
):
    """AC-3: assessment derives exact lead work without a subjective payload."""
    _host, path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    run_id = runs(path)[0]["run_id"]

    routed = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0]
    authoring_type = _action_type("AuthoringAction")
    assert isinstance(routed.action, authoring_type)
    row = application.execute(ops.Status(feature=V7_FEATURE)).to_envelope()[
        "next_actions"
    ][0]
    assert row["action"] == {
        "kind": "authoring",
        "feature": V7_FEATURE,
        "work": "review-disposition",
        "references": [f"{run_id}#SP-I1", f"{run_id}#@coverage"],
        "input_schema": "heddle.review-disposition-input/v1",
        "expected_revision": routed.action.expected_revision,
    }
    assert row["action"].get("payload") is None
    assert "--input-json -" in row["command"]
    assert len(runs(path)) == 1


def test_ac3_public_interpretation_action_preserves_capture_context(
    tmp_path, monkeypatch, run_cli
):
    """AC-3 red: retained capture identity reaches one lead authoring task."""
    from heddle.driver import loop
    from heddle.driver.sessions import SessionResult
    from heddle.kernel.project_config import load_project_config
    from tests.runtime.test_review_retention import retained_host

    host, path, calls, data, _payload = retained_host(tmp_path, monkeypatch, run_cli)
    routed = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0]
    authoring_type = _action_type("AuthoringAction")
    assert isinstance(routed.action, authoring_type)
    assert routed.action.work == "review-interpretation"
    assert routed.action.references == (
        data["run_id"],
        data["artifact"],
        data["capture_sha256"],
    )
    assert routed.action.input_schema == "heddle.review-interpretation-input/v1"
    assert (
        routed.action.expected_revision == yaml.safe_load(path.read_text())["revision"]
    )

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
    assert all(
        value in prompts[0]
        for value in (data["run_id"], data["artifact"], data["capture_sha256"])
    )
    assert len(calls) == 1 and path.read_bytes() == before


def test_ac4_pending_decision_is_never_serialized_as_a_resolution():
    """AC-4: a recommendation remains context for an explicit user choice."""
    from heddle.driver.loop import _resolution_action_for

    decision = SimpleNamespace(
        id="review#1",
        source="review#1",
        title="Choose the remediation",
        escalation_class=2,
        recommendation="apply the bounded repair",
        question="How should this be resolved?",
        options=("apply", "defer"),
        routes_to=("heddle/runtime/readiness.py",),
    )
    routed = _resolution_action_for("demo", decision)
    decision_action = _action_type("DecisionAction")
    assert isinstance(routed.action, decision_action)
    assert routed.action.decision_id == "review#1"
    assert routed.action.choices == ("apply", "defer")
    assert routed.action.routes_to == ("heddle/runtime/readiness.py",)
    assert "decisions resolve" not in routed.command


def _pending_cap_host(tmp_path, monkeypatch, run_cli):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    calls = provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    assert dispose(
        path,
        [
            disposition(origin, status="retained"),
            disposition(origin, "@coverage", status="settled"),
        ],
    ).ok
    stop = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0]
    assert isinstance(stop.action, ops.CommandAction)
    assert isinstance(stop.action.operation, ops.ReviewRoundOpen)
    assert not application.execute(stop.action.operation).ok
    decision_id = review_status(path)["decision_id"]
    assert decision_id
    return host, path, calls, origin, decision_id


def test_ac4_decision_action_pauses_and_continuation_is_single_use(
    tmp_path, monkeypatch, run_cli
):
    """AC-4 red: a cap decision pauses; one explicit continuation stays bounded."""
    from heddle.driver import loop
    from heddle.kernel.project_config import load_project_config
    from tests.tiering_review_helpers import amend

    host, path, calls, origin, decision_id = _pending_cap_host(
        tmp_path, monkeypatch, run_cli
    )
    status = application.execute(ops.Status(feature=V7_FEATURE))
    decision_type = _action_type("DecisionAction")
    action = status.next_actions[0].action
    assert isinstance(action, decision_type)
    assert action.feature == V7_FEATURE and action.decision_id == decision_id
    assert "continue-review" in " ".join(action.choices)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a pending decision launched a session or provider")

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    monkeypatch.setattr(loop, "spawn_phase_session", forbidden)
    before = path.read_bytes()
    for _ in range(2):
        paused = loop.run_loop(
            load_project_config(host), V7_FEATURE, until="plan-review"
        )
        assert not paused.ok and paused.error.code == "pending-decisions"
        assert path.read_bytes() == before
    assert len(calls) == 1

    resolution = ops.ResolveDecision(
        decision_id=decision_id,
        kind="continue-review",
        rationale="Owner authorizes one bounded follow-up on the original duty",
        routes_to="docs/features/runtime/operational-example.md",
        feature=V7_FEATURE,
    )
    resolved = application.execute(resolution)
    assert resolved.ok, resolved.to_envelope()
    after_resolution = path.read_bytes()
    assert application.execute(resolution).ok
    assert path.read_bytes() == after_resolution
    assert review_status(path)["next_step"] == "policy"
    assert amend(path, entry("spec-review", limit=2), revision=2).ok
    resumed = review_status(path)
    assert resumed["next_step"] == "run"
    assert [origin, "SP-I1"] in resumed["open_refs"]
    next_action = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0]
    assert isinstance(next_action.action, ops.CommandAction)
    assert isinstance(next_action.action.operation, ops.ReviewRoundOpen)


def test_ac11_driver_consumes_action_variants_without_class_repair():
    """AC-11 structural red pin: no command-class repair remains in the loop."""
    from heddle.driver import loop

    source = inspect.getsource(loop.run_loop)
    assert "InterpretReview, ops.RecordReviewDisposition" not in source
    assert "AuthoringAction" in source and "DecisionAction" in source


def test_ac11_driver_selects_the_shared_peer_boundary_action(tmp_path, monkeypatch):
    """AC-1/AC-11: the driver uses the same first final-boundary action as reads."""
    from heddle.driver import loop
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime.readiness import assess_current_readiness

    host, _path = current_host(tmp_path, monkeypatch, stage="peer-review")
    config = load_project_config(host)
    current = resolve_snapshot(config, V7_FEATURE)
    assessment = assess_current_readiness(config, current, purpose="driver")
    selected = loop._next_command(config, current, None)

    assert isinstance(selected, tuple)
    assert selected[0].action == assessment.next_actions[0].action
    assert isinstance(selected[0].action, ops.CommandAction)
    assert isinstance(selected[0].action.operation, ops.Verify)
    assert selected[0].action.operation.scope == "acceptance"
    assert selected[1] is None


def test_ac11_driver_consumes_authoring_variant_without_provider_retry(
    tmp_path, monkeypatch, run_cli
):
    """AC-11 survivor: prior repair passes author work to one lead session."""
    from heddle.driver import loop
    from heddle.driver.sessions import SessionResult
    from heddle.kernel.project_config import load_project_config

    host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    run_id = runs(path)[0]["run_id"]
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
    assert path.read_bytes() == before and len(calls) == 1


def test_ac11_driver_obeys_command_authoring_and_decision_boundaries(
    tmp_path, monkeypatch, run_cli
):
    """AC-11 red: driver behavior follows each supplied variant without repair."""
    from heddle.driver import loop
    from heddle.kernel.project_config import load_project_config

    host, path, calls, origin, decision_id = _pending_cap_host(
        tmp_path, monkeypatch, run_cli
    )
    public = application.execute(ops.Orient(feature=V7_FEATURE))
    action = public.next_actions[0].action
    assert isinstance(action, _action_type("DecisionAction"))
    assert action.decision_id == decision_id

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    before = path.read_bytes()
    paused = loop.run_loop(load_project_config(host), V7_FEATURE, until="plan-review")
    assert not paused.ok and paused.error.code == "pending-decisions"
    assert paused.next_actions[0].action == action
    assert path.read_bytes() == before and len(calls) == 1
    row = review_status(path)
    assert [origin, "SP-I1"] in row["open_refs"]
    assert row["stop_reason"] == "round-limit"
