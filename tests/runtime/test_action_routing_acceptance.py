"""Fast public-boundary acceptance for typed action routing."""

from __future__ import annotations

import pytest

from heddle.contracts import operations as ops
from heddle.runtime import application
from tests.structured_review_helpers import finding
from tests.tiering_helpers import entry
from tests.tiering_review_helpers import (
    V7_FEATURE,
    amend,
    current_host,
    dispose,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    review_status,
    runs,
)

pytestmark = pytest.mark.acceptance


def _open_original(tmp_path, monkeypatch, run_cli):
    host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    return host, path, calls, runs(path)[0]["run_id"]


def test_acceptance_ac1_ac5_surfaces_retain_an_original_duty_when_role_turns_off(
    tmp_path, monkeypatch, run_cli
):
    """AC-1/AC-5: public reads agree; Off does not waive original evidence."""
    _host, path, calls, run_id = _open_original(tmp_path, monkeypatch, run_cli)
    off = entry("spec-review", mode="off", limit=None, minimum_rounds=0)
    assert amend(path, off, revision=2).ok
    before = path.read_bytes()

    status = application.execute(ops.Status(feature=V7_FEATURE))
    orient = application.execute(ops.Orient(feature=V7_FEATURE))
    assert status.next_actions == orient.next_actions
    assert status.data["assessment"]["original_obligations"] == [
        [run_id, "SP-I1"],
        [run_id, "@coverage"],
    ]
    row = next(
        item
        for item in status.data["review_closure"]["assignments"]
        if item["role"] == "spec-review"
    )
    assert not row["closed"] and row["evidence_state"] != "not-applicable"
    assert path.read_bytes() == before and len(calls) == 1


def test_acceptance_ac2_ac9_action_progress_revalidation_and_freshness(
    tmp_path, monkeypatch, run_cli
):
    """AC-2/AC-9: current execution progresses, then the same action is stale."""
    _host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", minimum_rounds=2, limit=3)},
    )
    provider_transport(monkeypatch, review_content())
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 0
    run_id = runs(path)[0]["run_id"]
    assert dispose(path, [disposition(run_id, "@coverage", status="settled")]).ok

    before = application.execute(ops.Status(feature=V7_FEATURE))
    action = before.next_actions[0].action
    assert isinstance(action, ops.CommandAction)
    assert isinstance(action.operation, ops.ReviewRoundOpen)
    assert action.operation.expect_revision is not None
    identity = before.data["assessment"]["observation_identity"]
    progressed = application.execute(action.operation)
    assert progressed.ok, progressed.to_envelope()
    stale = application.execute(action.operation)
    assert not stale.ok and stale.error.code == "conflict"
    after = application.execute(ops.Status(feature=V7_FEATURE))
    assert after.data["assessment"]["observation_identity"] != identity


def test_acceptance_ac3_ac8_public_authoring_preserves_inputs_without_effects(
    tmp_path, monkeypatch, run_cli
):
    """AC-3/AC-8: qualification derives one complete lead task without writes."""
    _host, path, calls, run_id = _open_original(tmp_path, monkeypatch, run_cli)
    before = path.read_bytes()
    result = application.execute(ops.Status(feature=V7_FEATURE))
    action = result.next_actions[0].action
    assert isinstance(action, ops.AuthoringAction)
    assert action.work == "review-disposition"
    assert action.references == (f"{run_id}#SP-I1", f"{run_id}#@coverage")
    assert action.input_schema == "heddle.review-disposition-input/v1"
    assert action.expected_revision is not None
    assert path.read_bytes() == before and len(calls) == 1


def test_acceptance_ac4_ac11_decision_action_pauses_the_driver(
    tmp_path, monkeypatch, run_cli
):
    """AC-4/AC-11: a cap decision stays explicit and blocks repeated drive."""
    from heddle.driver import loop
    from heddle.kernel.project_config import load_project_config
    from tests.runtime.test_action_contracts import _pending_cap_host

    host, path, calls, origin, decision_id = _pending_cap_host(
        tmp_path, monkeypatch, run_cli
    )
    public = application.execute(ops.Orient(feature=V7_FEATURE))
    action = public.next_actions[0].action
    assert isinstance(action, ops.DecisionAction)
    assert action.decision_id == decision_id
    assert "continue-review" in " ".join(action.choices)

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    before = path.read_bytes()
    for _ in range(2):
        paused = loop.run_loop(
            load_project_config(host), V7_FEATURE, until="plan-review"
        )
        assert not paused.ok and paused.error.code == "pending-decisions"
        assert paused.next_actions[0].action == action
        assert path.read_bytes() == before
    assert [origin, "SP-I1"] in review_status(path)["open_refs"]
    assert len(calls) == 1


def test_acceptance_ac6_ac7_relevant_change_explains_only_affected_original(
    tmp_path, monkeypatch, run_cli
):
    """AC-6/AC-7: changed evidence names its dependency, origin, and remedy."""
    host, path = current_host(tmp_path, monkeypatch)
    first = host / "docs/first.md"
    second = host / "docs/second.md"
    first.write_text("First proof.\n")
    second.write_text("Second proof.\n")
    provider_transport(
        monkeypatch,
        review_content(
            findings=[
                finding("SP-I1", classification="implement"),
                finding("SP-I2", classification="implement"),
            ]
        ),
    )
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 4
    run_id = runs(path)[0]["run_id"]
    assert dispose(
        path,
        [
            disposition(run_id, "SP-I1", references=["docs/first.md"]),
            disposition(run_id, "SP-I2", references=["docs/second.md"]),
            disposition(
                run_id,
                "@coverage",
                status="settled",
                references=["docs/features/runtime/operational-example.md"],
            ),
        ],
    ).ok

    first.write_text("Changed first proof.\n")
    result = application.execute(ops.Status(feature=V7_FEATURE))
    row = next(
        item
        for item in result.data["review_closure"]["assignments"]
        if item["role"] == "spec-review"
    )
    assert row["open_refs"] == [[run_id, "SP-I1"]]
    explanation = row["evidence_explanations"][0]
    assert explanation["subject"] == f"{run_id}#SP-I1"
    assert explanation["changed_dependencies"] == ["docs/first.md"]
    assert explanation["cause"] == "referenced evidence changed"
    assert explanation["remedy"]["kind"] == "authoring"
    assert explanation["detail_limit"] is None


def test_acceptance_ac10_ac12_wire_and_integrity_share_real_owners(
    tmp_path, monkeypatch, run_cli
):
    """AC-10/AC-12: derived action wires round-trip; tampering fails closed."""
    _host, path, _calls, _run_id = _open_original(tmp_path, monkeypatch, run_cli)
    public = application.execute(ops.Status(feature=V7_FEATURE))
    wire = public.to_envelope()["next_actions"][0]["action"]
    assert ops.action_from_payload(wire) == public.next_actions[0].action
    assert set(wire) == {
        "kind",
        "feature",
        "work",
        "references",
        "input_schema",
        "expected_revision",
    }

    artifact = path.parent / runs(path)[0]["artifact"]
    artifact.write_text(artifact.read_text() + " ")
    before = path.read_bytes()
    refused = application.execute(ops.Status(feature=V7_FEATURE))
    assert not refused.ok and refused.error.code == "workspace-invalid"
    assert path.read_bytes() == before
