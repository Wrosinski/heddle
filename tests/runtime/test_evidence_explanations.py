"""Actionable evidence explanations and guidance."""

from __future__ import annotations

from pathlib import Path

import pytest

from heddle.contracts import operations as ops
from heddle.runtime import application
from heddle.runtime.verification import verification_status_payload
from tests.structured_review_helpers import finding
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    gate_command,
    provider_transport,
    review_content,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    (
        "status",
        "applicability",
        "expected_state",
        "expected_cause",
        "action",
    ),
    [
        (
            "missing",
            "required",
            "missing",
            "evidence has not been produced",
            ops.CommandAction(ops.Verify("m1")),
        ),
        (
            "content-stale",
            "required",
            "stale",
            "relevant content changed",
            ops.CommandAction(ops.Verify("m1")),
        ),
        (
            "failed",
            "required",
            "invalid",
            "recorded command failed",
            ops.CommandAction(ops.Verify("m1")),
        ),
        ("missing", "not-applicable", "not-applicable", None, None),
        (
            "missing",
            "scope-not-defined",
            "scope-not-defined",
            "verification scope is not declared",
            None,
        ),
    ],
)
def test_ac7_verification_payload_covers_failure_classes(
    status, applicability, expected_state, expected_cause, action
):
    """AC-7: missing, stale, invalid, and limited rows retain legal remedies."""
    from heddle.kernel.verification import VerificationFreshness

    row = verification_status_payload(
        (
            VerificationFreshness(
                "m1",
                status,
                action,
                3,
                applicability=applicability,
                detail_limit=(
                    "scope-level cause only" if status == "content-stale" else None
                ),
            ),
        )
    )[0]
    assert row["freshness"] == status
    assert row["evidence_state"] == expected_state
    assert row["applicability"] == applicability
    assert row["cause"] == expected_cause
    assert row["action"] == ("heddle verify --scope m1" if action else None)
    if status == "content-stale":
        assert row["detail_limit"] == "scope-level cause only"


def test_ac7_verification_payload_distinguishes_cause_and_legal_remedy():
    """AC-7 red pin: one stale row exposes cause, action, and detail limit."""
    from heddle.kernel.verification import VerificationFreshness

    row = verification_status_payload(
        (
            VerificationFreshness(
                "m1",
                "content-stale",
                ops.CommandAction(ops.Verify("m1")),
                3,
                applicability="required",
                detail_limit="scope-level cause only",
            ),
        )
    )[0]
    assert row["evidence_state"] == "stale"
    assert row["cause"] == "relevant content changed"
    assert row["action"] == "heddle verify --scope m1"
    assert row["detail_limit"] == "scope-level cause only"


@pytest.mark.parametrize(
    (
        "status",
        "subject",
        "dependencies",
        "failed_reference",
        "cause",
        "detail_limit",
    ),
    [
        (
            "content-stale",
            "run-1#finding-1",
            ("docs/changed-citation.md",),
            None,
            "relevant content changed",
            "only the recorded citation dependency is known",
        ),
        (
            "failed",
            "verification:m1",
            (),
            "verification/m1.log#exit_code=1",
            "recorded command failed",
            None,
        ),
    ],
)
def test_ac7_causal_explanations_bind_original_dependency_and_failed_reference(
    status, subject, dependencies, failed_reference, cause, detail_limit
):
    """AC-7 red: stale and invalid rows retain concrete causal identities."""
    from heddle.kernel.readiness import EvidenceExplanation
    from heddle.kernel.verification import VerificationFreshness

    remedy = ops.CommandAction(ops.Verify("m1", feature="demo", expect_revision=7))
    explanation = EvidenceExplanation(
        subject=subject,
        cause=cause,
        changed_dependencies=dependencies,
        failed_reference=failed_reference,
        remedy=remedy,
        detail_limit=detail_limit,
    )
    row = verification_status_payload(
        (
            VerificationFreshness(
                "m1",
                status,
                remedy,
                3,
                applicability="required",
                explanation=explanation,
            ),
        )
    )[0]

    assert row["evidence_state"] == (
        "stale" if status == "content-stale" else "invalid"
    )
    assert row["affected_original_work"] == subject
    assert row["changed_dependencies"] == list(dependencies)
    assert row["failed_reference"] == failed_reference
    assert row["cause"] == cause
    assert row["action"].startswith("heddle verify --scope m1")
    assert row["detail_limit"] == detail_limit


def test_ac12_packaged_guidance_uses_the_typed_action_contract():
    """AC-12 red: packaged guidance states the complete delivered input contract."""
    paths = (
        "heddle/resources/implement.briefing.md",
        "heddle/resources/peer-review.briefing.md",
    )
    corpus = "\n".join((ROOT / path).read_text() for path in paths).lower()
    authoring_fields = {
        "feature",
        "work",
        "references",
        "input_schema",
        "expected_revision",
    }
    decision_fields = {"feature", "decision_id", "choices", "routes_to"}
    for phrase in (
        "authoring action",
        "decision action",
        "lead authors the payload",
        "driver pauses",
        "current authority",
        "bounded non-progress",
    ):
        assert phrase in corpus
    assert all(field in corpus for field in authoring_fields | decision_fields)
    assert "incomplete command" not in corpus


def test_ac12_publicly_derived_actions_round_trip_through_wire(
    tmp_path, monkeypatch, run_cli
):
    """AC-12 red: public derivation, wire encoding, and decoding agree exactly."""
    _host, _path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    public = application.execute(ops.Status(feature=V7_FEATURE))
    authoring = public.next_actions[0].action
    assert isinstance(authoring, ops.AuthoringAction)
    wire = public.to_envelope()["next_actions"][0]["action"]
    assert ops.action_from_payload(wire) == authoring
    assert set(wire) == {
        "kind",
        "feature",
        "work",
        "references",
        "input_schema",
        "expected_revision",
    }

    from tests.runtime.test_action_contracts import _pending_cap_host

    (tmp_path / "decision").mkdir()
    _host, _path, _calls, _origin, decision_id = _pending_cap_host(
        tmp_path / "decision", monkeypatch, run_cli
    )
    decision_result = application.execute(ops.Orient(feature=V7_FEATURE))
    decision = decision_result.next_actions[0].action
    assert isinstance(decision, ops.DecisionAction)
    assert decision.decision_id == decision_id
    decision_wire = decision_result.to_envelope()["next_actions"][0]["action"]
    assert ops.action_from_payload(decision_wire) == decision
    assert set(decision_wire) == {
        "kind",
        "feature",
        "decision_id",
        "choices",
        "routes_to",
    }


def test_ac7_public_review_explanations_keep_manual_and_command_remedies(
    tmp_path, monkeypatch, run_cli
):
    """AC-7: legal non-authoring remedies never cross an internal wire decoder."""
    from tests.runtime.test_action_contracts import _pending_cap_host
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import amend

    host, path, _calls, _origin, decision_id = _pending_cap_host(
        tmp_path, monkeypatch, run_cli
    )
    resolved = application.execute(
        ops.ResolveDecision(
            decision_id=decision_id,
            kind="continue-review",
            rationale="Owner authorizes one bounded follow-up",
            routes_to="docs/features/runtime/operational-example.md",
            feature=V7_FEATURE,
        )
    )
    assert resolved.ok, resolved.to_envelope()
    source = host / "src/example.py"
    source.write_text(source.read_text() + "\n# citation changed\n")

    manual = application.execute(ops.Status(feature=V7_FEATURE))
    assert manual.ok, manual.to_envelope()
    row = next(
        item
        for item in manual.data["review_closure"]["assignments"]
        if item["role"] == "spec-review"
    )
    assert row["evidence_explanations"][0]["remedy"]["kind"] == "manual"
    assert (
        next(
            item
            for item in manual.data["review_status"]
            if item["gate"] == "spec-review"
        )["explanation"]
        is None
    )
    assert application.execute(ops.Orient(feature=V7_FEATURE)).ok

    assert amend(path, entry("spec-review", limit=2), revision=2).ok
    command = application.execute(ops.Status(feature=V7_FEATURE))
    assert command.ok, command.to_envelope()
    row = next(
        item
        for item in command.data["review_closure"]["assignments"]
        if item["role"] == "spec-review"
    )
    remedy = row["evidence_explanations"][0]["remedy"]
    assert remedy["kind"] == "command"
    assert remedy["operation"]["name"] == "review round-open"


@pytest.mark.parametrize("genuinely_open", [False, True])
def test_changed_citation_at_round_limit_routes_to_resubmission_not_stop(
    tmp_path, monkeypatch, run_cli, genuinely_open
):
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        review_status,
        runs,
    )

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    findings = [finding("SP-I1", classification="implement")]
    if genuinely_open:
        findings.append(finding("SP-I2", classification="implement"))
    calls = provider_transport(monkeypatch, review_content(findings=findings))
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    rows = [disposition(origin), disposition(origin, "@coverage", status="settled")]
    if genuinely_open:
        rows.append(disposition(origin, "SP-I2", status="retained"))
    assert dispose(path, rows).ok
    source = host / "src/example.py"
    source.write_text(source.read_text() + "\n# citation changed\n")

    row = review_status(path)
    assert row["stop_reason"] == "round-limit" and not row["closed"]
    action = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0].action
    if genuinely_open:
        assert row["next_step"] == "decision"
        assert isinstance(action, ops.CommandAction)
        assert isinstance(action.operation, ops.ReviewRoundOpen)
        return
    assert row["next_step"] == "disposition" and row["evidence_state"] == "stale"
    assert isinstance(action, ops.AuthoringAction)
    assert action.work == "review-disposition"
    assert set(action.references) == {f"{origin}#SP-I1", f"{origin}#@coverage"}
    assert dispose(path, rows).ok
    assert review_status(path)["closed"] and len(calls) == 1


def test_recorded_report_row_outside_its_decision_route_is_explained(
    tmp_path, monkeypatch, run_cli
):
    from tests.operational_model_helpers import SPEC, read, write
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        review_status,
        runs,
    )

    host, path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="report")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    owner = read(path)["decisions"][0]["id"]
    resolved = application.execute(
        ops.ResolveDecision(
            owner,
            "disposition",
            "Synthetic owner selects the module.",
            SPEC,
            feature=V7_FEATURE,
        )
    )
    assert resolved.ok, resolved.to_envelope()
    code, verified = gate_command(run_cli, "verify", "--scope", "m1")
    assert code == 0 and verified["ok"], verified
    waiting = disposition(
        origin,
        status="awaiting-decision",
        evidence_kind="verification",
        verification_scope="m1",
    )
    coverage = disposition(origin, "@coverage", status="settled")
    assert dispose(path, [waiting, coverage]).ok
    # Authored history: a row recorded before input refused this shape.
    value = read(path)
    value["review_assignments"]["dispositions"][0]["status"] = "addressed"
    write(path, value)

    row = review_status(path)
    assert row["open_refs"] == [[origin, "SP-I1"]]
    assert row["evidence_state"] == "invalid"
    [explanation] = row["evidence_explanations"]
    assert explanation["subject"] == f"{origin}#SP-I1"
    assert "resolved owner decision" in explanation["cause"]
    assert explanation["remedy"]["kind"] == "authoring"
