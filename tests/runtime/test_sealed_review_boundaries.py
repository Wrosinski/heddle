"""Native transition and reader contracts for sealed review boundaries."""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.kernel.project_config import KernelError
from heddle.runtime.application import execute
from tests.structured_review_helpers import finding
from tests.tiering_helpers import OPUS, ROLES, entry, invoke, snapshot, wire_policy
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


def _policy_for(*active_roles):
    selected = {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }
    selected.update({role: entry(role) for role in active_roles})
    return selected


def _close_review(path, monkeypatch, run_cli, *, role, findings=()):
    calls = provider_transport(monkeypatch, review_content(role, findings=findings))
    code, result = gate_command(run_cli, "run-gate", role)
    assert code in (0, 4), result
    origin = runs(path)[-1]["run_id"]
    rows = [disposition(origin, "@coverage", status="settled")]
    for row in findings:
        if row["classification"] == "implement":
            rows.insert(0, disposition(origin, row["id"]))
    accepted = dispose(path, rows)
    assert accepted.ok and accepted.data["closure"]["closed"], accepted.to_envelope()
    return calls, origin


def _closed_review(
    tmp_path,
    monkeypatch,
    run_cli,
    *,
    stage,
    role,
    findings=(),
    also_active=(),
    declared_scaffold_path=None,
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage=stage,
        overrides=_policy_for(role, *also_active),
    )
    if declared_scaffold_path is not None:
        plan = path.parent / "plan.md"
        plan.write_text(plan.read_text() + f"\n`{declared_scaffold_path}`\n")
    calls, origin = _close_review(
        path, monkeypatch, run_cli, role=role, findings=findings
    )
    return host, path, calls, origin


def test_ac1_scaffold_transition_seals_review_before_shared_path_changes(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="scaffold",
        role="review-test-scaffolding",
        declared_scaffold_path="tests/check.py",
    )
    before_revision = yaml.safe_load(path.read_text())["revision"]

    advanced = execute(ops.PhaseExit(feature=V7_FEATURE))

    assert advanced.ok, advanced.to_envelope()
    value = yaml.safe_load(path.read_text())
    assert value["stage"] == "implement"
    receipts = value["review_assignments"]["acceptances"]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["boundary"] == "scaffold"
    assert (receipt["revision_before"], receipt["revision_after"]) == (
        before_revision,
        before_revision + 1,
    )
    (host / "tests/check.py").write_text(
        (host / "tests/check.py").read_text() + "\n# next milestone scaffold\n"
    )
    observed = review_status(path, role="review-test-scaffolding")
    assert observed["closed"] and observed["evidence_state"] == "accepted"
    assert observed["acceptance"]["boundary"] == "scaffold"
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime.review_assignments import current_basis

    current = resolve_snapshot(load_project_config(host), V7_FEATURE)
    assignment = next(
        row
        for row in current.state.review_assignments.assignments
        if row.role == "review-test-scaffolding"
    )
    assert (
        current_basis(load_project_config(host), current, assignment)
        != receipt["assignments"][0]["source_identity"]
    )
    assert len(calls) == 1


def test_ac1_sealed_review_uses_its_recorded_policy_revision(
    tmp_path, monkeypatch, run_cli
):
    _host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="scaffold",
        role="review-test-scaffolding",
    )
    assert execute(ops.PhaseExit(feature=V7_FEATURE)).ok
    revised = _policy_for("review-test-scaffolding")
    revised["review-test-scaffolding"] = entry(
        "review-test-scaffolding", limit=2, minimum_rounds=2
    )

    amended = invoke(
        "FeaturePolicy",
        slug=V7_FEATURE,
        payload=wire_policy(revision=2, overrides=revised),
        expect_revision=yaml.safe_load(path.read_text())["revision"],
    )

    assert amended.ok, amended.to_envelope()
    status = execute(ops.Status(feature=V7_FEATURE))
    assert status.ok, status.to_envelope()
    sealed = next(
        row
        for row in status.data["review_closure"]["assignments"]
        if row["role"] == "review-test-scaffolding"
    )
    assert sealed["closed"] and sealed["evidence_state"] == "accepted"
    assert len(calls) == 1


def test_ac1_later_policy_does_not_create_a_past_boundary_duty(tmp_path, monkeypatch):
    _host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides=_policy_for(),
    )

    amended = invoke(
        "FeaturePolicy",
        slug=V7_FEATURE,
        payload=wire_policy(
            revision=2,
            overrides=_policy_for("review-test-scaffolding"),
        ),
        expect_revision=yaml.safe_load(path.read_text())["revision"],
    )

    assert amended.ok, amended.to_envelope()
    status = execute(ops.Status(feature=V7_FEATURE))
    assert status.ok, status.to_envelope()
    assert yaml.safe_load(path.read_text())["review_assignments"]["acceptances"] == []


def test_ac2_milestone_receipt_preserves_review_while_current_proof_stales(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="implement",
        role="milestone-review",
    )
    code, verified = gate_command(run_cli, "verify", "--scope", "m1")
    assert code == 0 and verified["ok"], verified

    advanced = execute(ops.MilestoneAdvance(feature=V7_FEATURE))

    assert advanced.ok, advanced.to_envelope()
    value = yaml.safe_load(path.read_text())
    receipt = value["review_assignments"]["acceptances"][-1]
    assert receipt["boundary"] == "milestone" and receipt["scope"] == "m1"
    assert [entry["assignment_id"] for entry in receipt["assignments"]] == [
        f"{V7_FEATURE}:milestone-review:m1:p1"
    ]
    assert value["milestones"][0]["status"] == "done"
    promoted = execute(ops.MilestoneAdvance(feature=V7_FEATURE))
    assert promoted.ok, promoted.to_envelope()
    value = yaml.safe_load(path.read_text())
    assert value["milestones"][1]["status"] == "current"
    assert len(value["review_assignments"]["acceptances"]) == 1
    (host / "src/example.py").write_text("VALUE = 8\n")
    status = execute(ops.Status(feature=V7_FEATURE))
    assert status.ok, status.to_envelope()
    review = next(
        row
        for row in status.data["review_closure"]["assignments"]
        if row["role"] == "milestone-review" and row["scope"] == "m1"
    )
    verification = next(
        row for row in status.data["verification_status"] if row["scope"] == "m1"
    )
    assert review["closed"] and review["evidence_state"] == "accepted"
    assert verification["freshness"] == "content-stale"
    assert len(calls) == 1


def test_ac3_survivor_open_review_duty_still_blocks_transition_without_writes(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="scaffold",
        overrides=_policy_for("review-test-scaffolding"),
    )
    calls = provider_transport(
        monkeypatch,
        review_content(
            "review-test-scaffolding",
            findings=[finding("TS-I1", classification="implement")],
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "review-test-scaffolding")
    assert code in (0, 4), result
    before = snapshot(host)

    refused = execute(ops.PhaseExit(feature=V7_FEATURE))

    assert not refused.ok
    assert "gate-not-converged" in {
        refused.error.code,
        *execute(ops.Status(feature=V7_FEATURE)).data["blocking_conditions"],
    }
    assert snapshot(host) == before and len(calls) == 1


def test_ac3_survivor_missing_reviewer_slot_blocks_without_receipt_or_write(
    tmp_path, monkeypatch, run_cli
):
    policy = _policy_for("review-test-scaffolding")
    policy["review-test-scaffolding"] = entry("review-test-scaffolding", secondary=OPUS)
    host, path = current_host(tmp_path, monkeypatch, stage="scaffold", overrides=policy)
    calls = provider_transport(monkeypatch, review_content("review-test-scaffolding"))
    code, result = gate_command(run_cli, "run-gate", "review-test-scaffolding")
    assert code in (0, 4), result
    before = snapshot(host)

    refused = execute(ops.PhaseExit(feature=V7_FEATURE))

    assert not refused.ok
    value = yaml.safe_load(path.read_text())
    assert value["review_assignments"].get("acceptances", []) == []
    assert snapshot(host) == before and len(calls) == 1


def test_ac3_survivor_changed_disposition_evidence_blocks_without_receipt_or_write(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="scaffold",
        role="review-test-scaffolding",
    )
    (host / "src/example.py").write_text("VALUE = 8\n")
    before = snapshot(host)

    refused = execute(ops.PhaseExit(feature=V7_FEATURE))

    assert not refused.ok
    assert "gate-not-converged" in {
        refused.error.code,
        *execute(ops.Status(feature=V7_FEATURE)).data["blocking_conditions"],
    }
    value = yaml.safe_load(path.read_text())
    assert value["review_assignments"].get("acceptances", []) == []
    assert snapshot(host) == before and len(calls) == 1


def test_ac4_preview_conflict_and_retry_create_exactly_one_receipt(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="scaffold",
        overrides=_policy_for("review-test-scaffolding"),
    )
    value = yaml.safe_load(path.read_text())
    value["flow"] = "hitl"
    value["authorized_through"] = "scaffold"
    value["authorizations"] = [
        {"through": "scaffold", "source": "user", "at": value["updated"]}
    ]
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    before_grant = yaml.safe_load(path.read_text())
    granted = execute(ops.PhaseExit(through="complete", feature=V7_FEATURE))
    assert granted.ok and granted.data["authorization_only"] is True
    authorized = yaml.safe_load(path.read_text())
    assert authorized["stage"] == "scaffold"
    assert authorized["review_assignments"].get("acceptances", []) == []
    expected = deepcopy(before_grant)
    expected["revision"] += 1
    expected["updated"] = authorized["updated"]
    expected["authorized_through"] = "complete"
    expected["authorizations"] = [
        *before_grant["authorizations"],
        {"through": "complete", "source": "user", "at": authorized["updated"]},
    ]
    assert authorized == expected
    calls, _origin = _close_review(
        path, monkeypatch, run_cli, role="review-test-scaffolding"
    )
    revision = yaml.safe_load(path.read_text())["revision"]
    before = snapshot(host)
    preview = execute(
        ops.PhaseExit(feature=V7_FEATURE, expect_revision=revision, dry_run=True)
    )
    assert preview.ok and snapshot(host) == before
    conflict = execute(ops.PhaseExit(feature=V7_FEATURE, expect_revision=revision - 1))
    assert not conflict.ok and conflict.error.code == "conflict"
    assert snapshot(host) == before

    first = execute(ops.PhaseExit(feature=V7_FEATURE, expect_revision=revision))
    assert first.ok, first.to_envelope()
    after = snapshot(host)
    retry = execute(ops.PhaseExit(feature=V7_FEATURE, expect_revision=revision))
    assert not retry.ok and retry.error.code == "conflict"
    assert snapshot(host) == after
    receipts = yaml.safe_load(path.read_text())["review_assignments"]["acceptances"]
    assert len(receipts) == 1 and len(calls) == 1


def test_ac5_sealed_receipt_and_supporting_rows_are_immutable(
    tmp_path, monkeypatch, run_cli
):
    from heddle.runtime.state_store import commit_state

    _host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="scaffold",
        role="review-test-scaffolding",
    )
    assert execute(ops.PhaseExit(feature=V7_FEATURE)).ok
    value = yaml.safe_load(path.read_text())
    revision = value["revision"]

    def mutation(kind, document):
        changed = deepcopy(document)
        ledger = changed["review_assignments"]
        if kind == "assignment-identity":
            ledger["acceptances"][0]["assignments"][0]["assignment_id"] = "borrowed"
        elif kind == "entry-digest":
            ledger["acceptances"][0]["assignments"][0]["digest"] = "0" * 64
        else:
            ledger["dispositions"][0]["reason"] = "forged"
        return changed

    for kind in ("assignment-identity", "entry-digest", "supporting-row"):
        with pytest.raises(KernelError, match="sealed|acceptance") as rejected:
            commit_state(
                path,
                expect_revision=revision,
                transform=lambda document, kind=kind: mutation(kind, document),
            )
        assert rejected.value.code == "workspace-invalid"
        assert "restore" in rejected.value.hint or "inspect" in rejected.value.hint
        assert yaml.safe_load(path.read_text()) == value
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("kind", "message"),
    (
        ("entry", "accepted assignment digest differs"),
        ("disposition", "acceptance record dispositions[0] digest differs"),
    ),
)
def test_ac5_read_path_rejects_direct_receipt_dependency_tampering(
    tmp_path, monkeypatch, run_cli, kind, message
):
    _host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="scaffold",
        role="review-test-scaffolding",
    )
    assert execute(ops.PhaseExit(feature=V7_FEATURE)).ok
    value = yaml.safe_load(path.read_text())
    if kind == "entry":
        value["review_assignments"]["acceptances"][0]["assignments"][0]["digest"] = (
            "0" * 64
        )
    else:
        value["review_assignments"]["dispositions"][0]["reason"] = "forged"
    path.write_text(yaml.safe_dump(value, sort_keys=False))

    refused = execute(ops.Status(feature=V7_FEATURE))

    assert not refused.ok and refused.error.code == "workspace-invalid"
    assert message in refused.error.message
    assert len(calls) == 1


def test_ac5_missing_receipt_artifact_is_refused_with_its_exact_path(
    tmp_path, monkeypatch, run_cli
):
    _host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="scaffold",
        role="review-test-scaffolding",
    )
    assert execute(ops.PhaseExit(feature=V7_FEATURE)).ok
    value = yaml.safe_load(path.read_text())
    attempt_id = value["review_assignments"]["acceptances"][0]["assignments"][0][
        "attempt_ids"
    ][0]
    attempt = next(
        row
        for row in value["review_assignments"]["attempts"]
        if row["attempt_id"] == attempt_id
    )
    artifact = next(
        row["path"] for row in attempt["artifacts"] if row["role"] == "canonical"
    )
    (path.parent / artifact).unlink()

    refused = execute(ops.Status(feature=V7_FEATURE))

    assert not refused.ok and refused.error.code == "workspace-invalid"
    assert artifact in refused.error.message + refused.error.hint
    assert len(calls) == 1


def test_ac6_status_and_orient_agree_on_historical_acceptance(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="scaffold",
        role="review-test-scaffolding",
    )
    assert execute(ops.PhaseExit(feature=V7_FEATURE)).ok
    (host / "src/example.py").write_text("VALUE = 8\n")

    rows = []
    for operation in (ops.Status(feature=V7_FEATURE), ops.Orient(feature=V7_FEATURE)):
        result = execute(operation)
        assert result.ok, result.to_envelope()
        rows.append(
            next(
                row
                for row in result.data["review_closure"]["assignments"]
                if row["role"] == "review-test-scaffolding"
            )
        )
    assert rows[0] == rows[1]
    assert rows[0]["closed"] and rows[0]["evidence_state"] == "accepted"
    assert rows[0]["acceptance"]["source_identity"]
    assert len(calls) == 1


def test_ac6_readiness_driver_and_completion_share_sealed_and_live_authority(
    tmp_path, monkeypatch, run_cli
):
    from heddle.driver.loop import _next_command
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.kernel.readiness import Boundary
    from heddle.kernel.review_assignments import validate_boundary_acceptances
    from heddle.runtime.readiness import assess_current_readiness

    host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="scaffold",
        role="review-test-scaffolding",
        also_active=("peer-review-sequential",),
    )
    assert execute(ops.PhaseExit(feature=V7_FEATURE)).ok
    (host / "tests/check.py").write_text(
        (host / "tests/check.py").read_text() + "\n# later shared-path edit\n"
    )
    config = load_project_config(host)
    snapshot_value = resolve_snapshot(config, V7_FEATURE)
    accepted = validate_boundary_acceptances(snapshot_value.state)
    assert accepted[f"{V7_FEATURE}:review-test-scaffolding:feature:p1"].boundary == (
        "scaffold"
    )

    readiness = assess_current_readiness(config, snapshot_value, purpose="status")
    prior = next(
        row for row in readiness.reviews if row.gate == "review-test-scaffolding"
    )
    assert prior.evidence_state.value == "accepted"
    routed = _next_command(config, snapshot_value, None)
    assert isinstance(routed, tuple)
    assert isinstance(routed[0].action, ops.SessionAction)
    assert "review-test-scaffolding" not in routed[0].reason

    completion = assess_current_readiness(
        config,
        snapshot_value,
        boundary=Boundary("complete", None),
        purpose="completion",
    )
    accepted_prior = next(
        row for row in completion.reviews if row.gate == "review-test-scaffolding"
    )
    final = next(
        row for row in completion.reviews if row.gate == "peer-review-sequential"
    )
    assert accepted_prior.evidence_state.value == "accepted"
    assert final.evidence_state.value != "accepted"
    assert any(row.status == "missing" for row in completion.verifications)
    assert {"gate-not-converged", "verification-missing"} <= set(completion.blockers)
    assert len(calls) == 1


def test_ac7_component_retention_selects_every_receipt_dependency(
    tmp_path, monkeypatch, run_cli
):
    from hashlib import sha256

    from heddle.kernel.state import parse_state_document
    from heddle.runtime.completion import _retained_paths

    _host, path, calls, _origin = _closed_review(
        tmp_path,
        monkeypatch,
        run_cli,
        stage="implement",
        role="milestone-review",
    )
    code, verified = gate_command(run_cli, "verify", "--scope", "m1")
    assert code == 0 and verified["ok"], verified
    assert execute(ops.MilestoneAdvance(feature=V7_FEATURE)).ok
    raw = yaml.safe_load(path.read_text())
    state = parse_state_document(raw, source=path)
    receipt = raw["review_assignments"]["acceptances"][-1]
    entry_row = receipt["assignments"][0]
    retained = _retained_paths(state)

    expected = {}
    for attempt in raw["review_assignments"]["attempts"]:
        if attempt["attempt_id"] in entry_row["attempt_ids"]:
            for artifact in attempt["artifacts"]:
                if artifact["role"] in {"canonical", "capture", "evidence", "log"}:
                    expected[artifact["path"]] = artifact["sha256"]
    for reference in entry_row["records"]:
        if reference["family"] != "verifications":
            continue
        verification = raw["verifications"][reference["index"]]
        for evidence in (
            verification["evidence"]["before"],
            verification["evidence"]["after"],
        ):
            expected[evidence["artifact"]] = evidence["artifact_sha256"]

    assert expected and set(expected) <= retained
    for relative, digest in expected.items():
        artifact = path.parent / relative
        assert sha256(artifact.read_bytes()).hexdigest() == digest
    assert len(calls) == 1


def test_ac8_public_feature_start_emits_only_current_receipt_formats(
    tmp_path, monkeypatch
):
    from tests.tiering_helpers import FEATURE, blank_host, confirmed

    host = blank_host(tmp_path, monkeypatch)
    confirmed(host)
    started = invoke("FeatureStart", slug=FEATURE)
    assert started.ok, started.to_envelope()
    state = yaml.safe_load((host / "plans" / FEATURE / "state.yaml").read_text())
    assert state["schema"] == "heddle.state/v10"
    assert state["review_assignments"] == {
        "schema": "heddle.review-assignments/v3",
        "assignments": [],
        "attempts": [],
        "dispositions": [],
        "acceptances": [],
    }
