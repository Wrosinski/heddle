"""Owner waiver of the default post-review witness rerun."""

from __future__ import annotations

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.contracts.decisions import DecisionInput
from heddle.kernel.readiness import Boundary
from heddle.kernel.verification import VerificationFreshness
from heddle.runtime.application import execute
from tests.operational_model_helpers import FEATURE, read, write
from tests.readiness_helpers import current_readiness, verify
from tests.tiering_completion_helpers import final_host

JOURNAL_ROUTE = f"plans/{FEATURE}.decision-journal.md"


def _propose(scope: object, *, decision_class: int = 5):
    return execute(
        ops.DecisionsAdd(
            decisions=(
                DecisionInput(
                    kind="witness-waiver",
                    escalation_class=decision_class,
                    source="session",
                    title=f"Waive the post-review {scope} rerun",
                    question=f"Rerun {scope} after review changes?",
                    options=("rerun", "waive"),
                    routes_to=(JOURNAL_ROUTE,),
                    recommendation="rerun",
                    witness_waiver={"scope": scope},
                ),
            ),
            feature=FEATURE,
        )
    )


def _resolve(decision_id: str):
    return execute(
        ops.ResolveDecision(
            decision_id=decision_id,
            kind="accept-prior-witness",
            rationale="Owner waived the rerun; review changes were local.",
            routes_to=JOURNAL_ROUTE,
            feature=FEATURE,
        )
    )


def _waive(path, scope: str) -> str:
    added = _propose(scope)
    assert added.ok, added.to_envelope()
    decision_id = read(path)["decisions"][-1]["id"]
    resolved = _resolve(decision_id)
    assert resolved.ok, resolved.to_envelope()
    return decision_id


def _journal(root, decision_id: str, scope: str, fact: str) -> None:
    journal = root / JOURNAL_ROUTE
    journal.write_text(
        journal.read_text() + f"\n## {decision_id}\n\n- choice: accept-prior-witness\n"
        f"- scope: {scope}\n- fact_sha256: {fact}\n"
    )


def _review_fix(root, round_number: int) -> None:
    # A passing review-driven edit: owned content changes, behaviour holds.
    (root / "src/example.py").write_text(f"VALUE = 7  # review fix {round_number}\n")


def _row(root, scope: str, stage: str):
    readiness = current_readiness(root, boundary=Boundary(stage, None))
    return readiness, next(row for row in readiness.verifications if row.scope == scope)


def _declare_live(path) -> None:
    value = read(path)
    # A declared local assertion, not authorization to call a provider.
    value["commands"]["live_e2e_test"] = "python3 tests/check.py"
    write(path, value)


def _own(root, path, source: str) -> None:
    (root / source).write_text("EXTRA = 7\n")
    value = read(path)
    value["milestones"][-1]["owns"].append(source)
    write(path, value)


def _journal_waivers(root, path) -> list[str]:
    identifiers = []
    for decision in read(path)["decisions"]:
        if decision["kind"] == "witness-waiver" and decision["status"] == "resolved":
            waiver = decision["witness_waiver"]
            _journal(root, decision["id"], waiver["scope"], waiver["fact"])
            identifiers.append(decision["id"])
    return identifiers


def test_waiver_stands_only_at_boundaries_and_never_as_evidence() -> None:
    waived = VerificationFreshness(
        "acceptance", "content-stale", None, 3, waived_by="D1"
    )
    assert waived.satisfies_boundary and not waived.authorizes
    stale = VerificationFreshness("acceptance", "content-stale", None, 3)
    assert not stale.satisfies_boundary


def test_stale_witness_defaults_to_rerun_and_names_the_waiver(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    readiness, row = _row(host.root, "acceptance", "complete")
    assert row.status == "content-stale" and row.action is not None
    assert "verification-missing" in readiness.blockers
    reasons = [action.reason for action in readiness.next_actions]
    assert any("owner may waive" in reason for reason in reasons), reasons


def test_owner_waiver_completes_and_is_recorded(tmp_path, monkeypatch) -> None:
    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    decision_id = _waive(host.state, "acceptance")
    readiness, row = _row(host.root, "acceptance", "complete")
    assert row.status == "content-stale" and row.waived_by == decision_id
    assert row.action is None and "verification-missing" not in readiness.blockers
    status = execute(ops.Status(feature=FEATURE))
    rows = {row["scope"]: row for row in status.data["verification_status"]}
    assert rows["acceptance"]["waived_rerun"] == decision_id
    assert rows["acceptance"]["freshness"] == "content-stale"

    waiver = read(host.state)["decisions"][-1]["witness_waiver"]
    _journal(host.root, decision_id, "acceptance", waiver["fact"])
    accepted = host.complete()
    assert accepted.ok, accepted.to_envelope()
    completion = read(host.state)["completion"]
    assert completion["waived_witness_decision_ids"] == [decision_id]


def test_completion_without_waiver_omits_the_waiver_field(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    accepted = host.complete()
    assert accepted.ok, accepted.to_envelope()
    assert "waived_witness_decision_ids" not in read(host.state)["completion"]


def test_later_relevant_edit_restores_the_default_rerun(tmp_path, monkeypatch) -> None:
    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    _waive(host.state, "acceptance")
    _review_fix(host.root, 9)
    _readiness, row = _row(host.root, "acceptance", "complete")
    assert row.waived_by is None and row.action is not None


def test_rerun_supersedes_the_waiver(tmp_path, monkeypatch) -> None:
    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    _waive(host.state, "acceptance")
    verify(host.state, "acceptance")
    _readiness, row = _row(host.root, "acceptance", "complete")
    assert row.status == "fresh" and row.waived_by is None


@pytest.mark.parametrize(
    ("change", "code", "message"),
    [
        ("none", "workspace-invalid", "is current; no rerun waiver is needed"),
        (
            "command",
            "workspace-invalid",
            "cannot waive a rerun of command-stale evidence",
        ),
        ("live-undeclared", "usage", "live scope requires a declared live_e2e_test"),
        ("implement", "usage", "the pre-review witness run is mandatory"),
        ("lanes", "usage", "scope must name one witness lane"),
        ("object", "usage", "scope must name one witness lane"),
    ],
    ids=[
        "current",
        "command-stale",
        "live-undeclared",
        "implement-stage",
        "lane-list",
        "lane-object",
    ],
)
def test_waiver_refuses_ineligible_evidence(
    tmp_path, monkeypatch, change: str, code: str, message: str
) -> None:
    host = final_host(tmp_path, monkeypatch)
    scope = {
        "live-undeclared": "live",
        # One waiver per lane: a decision never names several.
        "lanes": ["acceptance", "live"],
        "object": {"lane": "acceptance"},
    }.get(change, "acceptance")
    value = read(host.state)
    if change == "command":
        value["commands"]["acceptance_test"] += " # changed witness"
    if change == "implement":
        value["stage"] = "implement"
        value["authorized_through"] = "implement"
    write(host.state, value)
    before = host.state.read_bytes()
    refused = _propose(scope)
    assert not refused.ok and refused.error.code == code, refused.to_envelope()
    assert message in refused.error.message, refused.to_envelope()
    assert host.state.read_bytes() == before


def test_waiver_refuses_failed_evidence(tmp_path, monkeypatch) -> None:
    host = final_host(tmp_path, monkeypatch, verify_now=False)
    verify(host.state, "m1", "m2", "smoke")
    value = read(host.state)
    value["commands"]["acceptance_test"] = "python3 -c 'raise SystemExit(1)'"
    write(host.state, value)
    failed = execute(ops.Verify(feature=FEATURE, scope="acceptance"))
    assert read(host.state)["verifications"][-1]["exit_code"] != 0, failed
    refused = _propose("acceptance")
    assert not refused.ok
    assert "cannot waive a rerun of failed evidence" in refused.error.message


def test_waiver_requires_class_five(tmp_path, monkeypatch) -> None:
    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    refused = _propose("acceptance", decision_class=2)
    assert not refused.ok and "class is ineligible" in refused.error.message


def test_resolution_refuses_evidence_changed_since_proposal(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    added = _propose("acceptance")
    assert added.ok, added.to_envelope()
    decision_id = read(host.state)["decisions"][-1]["id"]
    _review_fix(host.root, 9)
    refused = _resolve(decision_id)
    assert not refused.ok
    assert "witness evidence changed" in refused.error.message
    assert "--kind disposition" in refused.error.hint


def test_completion_ledger_rejects_a_waiver_for_another_fact(
    tmp_path, monkeypatch
) -> None:
    from pathlib import Path

    from heddle.kernel.project_config import KernelError
    from heddle.kernel.state import parse_state_document

    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    decision_id = _waive(host.state, "acceptance")
    waiver = read(host.state)["decisions"][-1]["witness_waiver"]
    _journal(host.root, decision_id, "acceptance", waiver["fact"])
    assert host.complete().ok
    document = yaml.safe_load(host.state.read_text())
    document["decisions"][-1]["witness_waiver"]["fact"] = "0" * 64
    with pytest.raises(KernelError, match="latest scoped fact"):
        parse_state_document(document, source=Path("state.yaml"))


def test_completion_requires_the_waiver_in_the_decision_journal(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    decision_id = _waive(host.state, "acceptance")
    refused = host.complete()
    assert not refused.ok
    assert decision_id in str(refused.to_envelope()), refused.to_envelope()
    assert read(host.state)["completion"] is None


def test_one_waiver_carries_from_peer_review_through_robustness(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="peer-review")
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    decision_id = _waive(host.state, "acceptance")
    exited = execute(ops.PhaseExit(feature=FEATURE))
    assert exited.ok, exited.to_envelope()
    assert read(host.state)["stage"] == "robustness"
    readiness, row = _row(host.root, "acceptance", "robustness")
    assert row.waived_by == decision_id
    assert "verification-missing" not in readiness.blockers


def test_absent_waiver_keeps_historical_decision_receipt_identity() -> None:
    import hashlib
    import json
    from dataclasses import asdict, replace

    from heddle.kernel.review_assignments import acceptance_record_digest
    from heddle.kernel.state import DecisionFact

    fact = DecisionFact(
        "D1", "question", "session", "Pick", "resolved", None, "why", (), "t", "t"
    )
    historical = asdict(fact)
    del historical["witness_waiver"]  # Receipts sealed before the field existed.
    digest = hashlib.sha256(
        json.dumps(historical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert acceptance_record_digest(fact) == digest
    bound = replace(fact, witness_waiver={"scope": "acceptance"})
    assert acceptance_record_digest(bound) != digest


def test_each_lane_is_waived_separately_and_completion_names_both(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _declare_live(host.state)
    verify(host.state, "live")
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    acceptance = _waive(host.state, "acceptance")
    readiness, live_row = _row(host.root, "live", "complete")
    assert live_row.status == "content-stale" and live_row.waived_by is None
    assert "verification-missing" in readiness.blockers
    live = _waive(host.state, "live")
    readiness, live_row = _row(host.root, "live", "complete")
    assert live_row.waived_by == live
    assert "verification-missing" not in readiness.blockers

    assert _journal_waivers(host.root, host.state) == [acceptance, live]
    accepted = host.complete()
    assert accepted.ok, accepted.to_envelope()
    completion = read(host.state)["completion"]
    assert completion["waived_witness_decision_ids"] == [acceptance, live]


@pytest.mark.parametrize("evidence", ["missing", "unstable"])
def test_waiver_refuses_missing_and_unstable_evidence(
    tmp_path, monkeypatch, evidence: str
) -> None:
    host = final_host(tmp_path, monkeypatch, verify_now=False)
    verify(host.state, "m1", "m2", "smoke")
    if evidence == "unstable":
        value = read(host.state)
        # The witness passes but rewrites owned content while it runs.
        value["commands"]["acceptance_test"] = (
            "python3 tests/check.py && python3 -c "
            "\"open('src/other.py', 'a').write('# touched\\n')\""
        )
        write(host.state, value)
        unstable = execute(ops.Verify(feature=FEATURE, scope="acceptance"))
        assert unstable.error is not None and unstable.error.details["recorded"]
        assert _row(host.root, "acceptance", "complete")[1].status == "unstable"
    before = host.state.read_bytes()
    refused = _propose("acceptance")
    assert not refused.ok
    assert f"cannot waive a rerun of {evidence} evidence" in refused.error.message
    assert host.state.read_bytes() == before


def test_ownership_expansion_is_waivable_until_ownership_changes_again(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _own(host.root, host.state, "src/extra.py")
    verify(host.state, "smoke")
    _readiness, row = _row(host.root, "acceptance", "complete")
    assert row.status == "source-set-stale" and row.action is not None
    decision_id = _waive(host.state, "acceptance")
    waiver = read(host.state)["decisions"][-1]["witness_waiver"]
    assert "src/extra.py" in waiver["ownership"]
    _readiness, row = _row(host.root, "acceptance", "complete")
    assert row.waived_by == decision_id and row.action is None
    _own(host.root, host.state, "src/more.py")
    _readiness, row = _row(host.root, "acceptance", "complete")
    assert row.waived_by is None and row.action is not None


def test_waiver_stands_at_the_selected_robustness_boundary(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="robustness", overlay=True)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    decision_id = _waive(host.state, "acceptance")
    readiness, row = _row(host.root, "acceptance", "robustness")
    assert row.status == "content-stale" and row.waived_by == decision_id
    assert "verification-missing" not in readiness.blockers


def test_refusal_names_the_waiver_beside_the_default_rerun(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch, stage="peer-review")
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    refused = execute(ops.PhaseExit(feature=FEATURE))
    assert not refused.ok and refused.error.code == "verification-missing"
    assert "waive a witness lane" in refused.error.hint
    reasons = [action.reason for action in refused.next_actions]
    assert any("owner may waive" in reason for reason in reasons), reasons


def _open_behavior_review(tmp_path, monkeypatch, run_cli):
    from tests.structured_review_helpers import finding
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import (
        gate_command,
        provider_transport,
        review_content,
        runs,
    )

    host = final_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={"behavior-review": entry("behavior-review")},
    )
    provider_transport(
        monkeypatch,
        review_content(
            "behavior-review", findings=[finding("BE-I1", classification="implement")]
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "behavior-review")
    assert code in (0, 4), result
    return host, runs(host.state)[0]["run_id"]


def test_default_rerun_waits_behind_open_review_work(
    tmp_path, monkeypatch, run_cli
) -> None:
    from heddle.driver.loop import _next_command
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    host, _origin = _open_behavior_review(tmp_path, monkeypatch, run_cli)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    readiness = current_readiness(host.root, boundary=Boundary("peer-review", None))
    assert {"verification-missing", "gate-not-converged"} <= set(readiness.blockers)

    def rerun(action) -> bool:
        return (
            isinstance(action, ops.CommandAction)
            and isinstance(action.operation, ops.Verify)
            and action.operation.scope == "acceptance"
        )

    actions = [item.action for item in readiness.next_actions]
    assert any(rerun(action) for action in actions) and not rerun(actions[0])
    config = load_project_config(host.root)
    routed = _next_command(config, resolve_snapshot(config, FEATURE), None)
    assert isinstance(routed, tuple) and not rerun(routed[0].action), routed


def test_waived_lane_never_qualifies_as_disposition_evidence(
    tmp_path, monkeypatch, run_cli
) -> None:
    from tests.tiering_review_helpers import dispose, disposition

    host, origin = _open_behavior_review(tmp_path, monkeypatch, run_cli)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    _waive(host.state, "acceptance")
    before = host.state.read_bytes()
    rejected = dispose(
        host.state,
        [
            disposition(
                origin,
                "BE-I1",
                evidence_kind="verification",
                verification_scope="acceptance",
            ),
            disposition(origin, "@coverage", status="settled"),
        ],
    )
    assert not rejected.ok, rejected.to_envelope()
    row = rejected.error.details["rows"][0]
    assert row["predicate"] == "verification-not-qualifying"
    assert row["status"] == "content-stale"
    assert host.state.read_bytes() == before
