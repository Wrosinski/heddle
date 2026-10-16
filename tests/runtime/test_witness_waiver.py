"""Owner waiver of the default post-review witness rerun."""

from __future__ import annotations

import json

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
        # Implement exit may accept an earlier witness only after a
        # formatting-only change, so current evidence there needs no waiver.
        ("implement", "workspace-invalid", "is current; no rerun waiver is needed"),
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
    ac = "AC-14" if change == "implement" else "AC-13"
    assert not refused.ok and refused.error.code == code, (
        f"FAIL {ac}: the {change} refusal is {code}: {refused.to_envelope()}"
    )
    assert message in refused.error.message, (
        f"FAIL {ac}: the {change} refusal says {message!r}: {refused.to_envelope()}"
    )
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


# Review-equivalent currency (AC-13) and implement-exit acceptance (AC-14).
# Activation seams: typed `changes` on a status row
# (`tests.change_impact_explanation_helpers.typed_changes`) and the optional
# manifest reference a waiver binds (`waiver_manifest_reference`).


def _status_row(scope: str) -> dict:
    from tests.change_impact_verify_helpers import status_rows

    return status_rows()[scope]


def test_ac13_waiver_stays_current_across_a_review_equivalent_change(
    tmp_path, monkeypatch
) -> None:
    from tests.change_impact_verify_helpers import reflow_review_fix

    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    decision_id = _waive(host.state, "acceptance")
    reflow_review_fix(host.root, 8)

    _readiness, row = _row(host.root, "acceptance", "complete")
    assert row.status == "content-stale", row
    assert row.waived_by == decision_id and row.action is None, (
        "FAIL AC-13: a formatting-only change voided the witness waiver: "
        f"waived_by={row.waived_by!r}"
    )


def test_ac13_non_equivalent_change_restores_the_rerun_naming_the_path(
    tmp_path, monkeypatch
) -> None:
    from tests.change_impact_explanation_helpers import typed_changes

    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    _waive(host.state, "acceptance")
    _review_fix(host.root, 9)

    _readiness, row = _row(host.root, "acceptance", "complete")
    assert row.waived_by is None and row.action is not None
    assert typed_changes(_status_row("acceptance"), ac="AC-13") == {
        ("content", "src/example.py")
    }, "FAIL AC-13: the restored rerun's explanation must name the changed path"


def test_ac13_legacy_waiver_stays_exact(tmp_path, monkeypatch) -> None:
    """Survivor pin: a waiver recorded before review equivalence compares exactly."""
    from tests.change_impact_verify_helpers import (
        LEGACY_WAIVER_KEYS,
        legacy_host,
        reflow_legacy_source,
    )

    host = legacy_host(tmp_path, monkeypatch)
    waiver = next(
        decision
        for decision in read(host.state)["decisions"]
        if decision["kind"] == "witness-waiver"
    )
    assert set(waiver["witness_waiver"]) == LEGACY_WAIVER_KEYS
    assert waiver["witness_waiver"]["scope"] == "live"
    row = _status_row("live")
    assert row.get("waived_rerun") == waiver["id"] and row["action"] is None, row

    reflow_legacy_source(host.root)

    row = _status_row("live")
    assert row.get("waived_rerun") is None and row["action"] is not None, (
        f"a legacy waiver stays exact across a review-equivalent change: {row}"
    )


def test_ac13_resolution_tolerates_a_review_equivalent_change(
    tmp_path, monkeypatch
) -> None:
    from tests.change_impact_verify_helpers import reflow_review_fix

    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    added = _propose("acceptance")
    assert added.ok, added.to_envelope()
    decision_id = read(host.state)["decisions"][-1]["id"]
    reflow_review_fix(host.root, 8)

    resolved = _resolve(decision_id)
    assert resolved.ok, (
        "FAIL AC-13: resolving a waiver after a formatting-only change must "
        f"succeed: {resolved.to_envelope()}"
    )
    _readiness, row = _row(host.root, "acceptance", "complete")
    assert row.waived_by == decision_id


def test_ac13_completion_retains_the_manifest_a_waiver_names(
    tmp_path, monkeypatch
) -> None:
    from tests.change_impact_verify_helpers import waiver_manifest_reference

    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    decision_id = _waive(host.state, "acceptance")
    waiver = read(host.state)["decisions"][-1]["witness_waiver"]
    reference = waiver_manifest_reference(waiver)
    _journal(host.root, decision_id, "acceptance", waiver["fact"])

    preview = host.complete(dry_run=True)
    assert preview.ok, preview.to_envelope()
    retained = {
        item["path"]: item for item in preview.data["retained_evidence"]["artifacts"]
    }
    assert reference["artifact"] in retained, (
        "FAIL AC-13: completion does not retain the manifest a waiver names"
    )
    # The bound source may equal a fact's manifest, so the roles can be shared.
    assert "waiver-evidence" in retained[reference["artifact"]]["roles"]
    assert retained[reference["artifact"]]["sha256"] == reference["artifact_sha256"]


def test_ac13_lane_waived_across_an_equivalent_change_never_qualifies_evidence(
    tmp_path, monkeypatch, run_cli
) -> None:
    """Survivor pin: a waiver never makes its lane disposition evidence."""
    from tests.change_impact_verify_helpers import reflow_review_fix
    from tests.tiering_review_helpers import dispose, disposition

    host, origin = _open_behavior_review(tmp_path, monkeypatch, run_cli)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")
    _waive(host.state, "acceptance")
    reflow_review_fix(host.root, 8)
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


def _implement_exit_actions() -> list:
    """Orient's actions once only the acceptance witness is stale."""
    from tests.change_impact_verify_helpers import verify_scope

    actions = execute(ops.Orient(feature=FEATURE)).next_actions
    assert verify_scope(actions[0].action) == "acceptance", [
        item.command for item in actions
    ]
    return actions


def test_ac14_formatting_only_change_lets_the_owner_accept_the_earlier_witness(
    tmp_path, monkeypatch
) -> None:
    from tests.change_impact_verify_helpers import (
        implement_exit_host,
        journal_waiver,
        propose_waiver,
        resolve_waiver,
        waiver_manifest_reference,
    )

    host = implement_exit_host(tmp_path, monkeypatch)
    # The formatter-shaped form of the passing source: redundant parentheses.
    (host.root / "src/example.py").write_text("VALUE = (7)\n")
    # m1 reruns on the new bytes, so only the acceptance witness is stale.
    verify(host.state, "m1")
    _readiness, row = _row(host.root, "acceptance", "implement")
    assert row.status == "content-stale"
    reasons = [item.reason for item in _implement_exit_actions()]
    assert any("witness-waiver" in reason for reason in reasons), (
        "FAIL AC-14: implement exit offers no witness-waiver beside the rerun "
        f"after a formatting-only change: {reasons}"
    )

    added = propose_waiver()
    assert added.ok, (
        "FAIL AC-14: a witness-waiver proposal at implement must succeed when "
        f"every change is formatting-only: {added.to_envelope()}"
    )
    decision = read(host.state)["decisions"][-1]
    binding = decision["witness_waiver"]
    reference = waiver_manifest_reference(binding)
    assert reference["source_sha256"] == binding["source_sha256"], reference
    assert (host.state.parent / reference["artifact"]).is_file(), reference
    resolved = resolve_waiver(decision["id"])
    assert resolved.ok, resolved.to_envelope()
    journal_waiver(host.root, host.state, decision["id"])
    status = execute(ops.Status(feature=FEATURE))
    assert status.data["decision_journal_audit"] == "complete", status.data

    exited = execute(ops.PhaseExit(feature=FEATURE))
    assert exited.ok, exited.to_envelope()
    assert read(host.state)["stage"] == "peer-review"


def _proposed_implement_waiver(tmp_path, monkeypatch):
    """Implement exit after a formatting-only change, with a proposed waiver."""
    from tests.change_impact_verify_helpers import (
        implement_exit_host,
        propose_waiver,
        waiver_manifest_reference,
    )

    host = implement_exit_host(tmp_path, monkeypatch)
    (host.root / "src/example.py").write_text("VALUE = (7)\n")
    verify(host.state, "m1")
    added = propose_waiver()
    assert added.ok, added.to_envelope()
    decision = read(host.state)["decisions"][-1]
    artifact = waiver_manifest_reference(decision["witness_waiver"])["artifact"]
    return host, decision["id"], artifact


def _damage(path, damage: str) -> None:
    if damage == "missing":
        path.unlink()
    else:
        content = path.read_bytes()
        path.unlink()
        path.write_bytes(content + b"# altered\n")


def _assert_names_the_manifest(result, decision_id: str, artifact: str) -> None:
    assert not result.ok, result.to_envelope()
    assert result.error.code == "workspace-invalid", result.to_envelope()
    message = result.error.message
    assert decision_id in message and artifact in message, (
        "FAIL AC-13: a damaged waiver manifest is an integrity error naming the "
        f"decision and the artifact: {result.to_envelope()}"
    )


@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_ac13_resolution_requires_the_manifest_the_proposal_names(
    tmp_path, monkeypatch, damage: str
) -> None:
    from tests.change_impact_verify_helpers import resolve_waiver

    host, decision_id, artifact = _proposed_implement_waiver(tmp_path, monkeypatch)
    _damage(host.state.parent / artifact, damage)
    before = host.state.read_bytes()

    resolved = resolve_waiver(decision_id)

    _assert_names_the_manifest(resolved, decision_id, artifact)
    assert host.state.read_bytes() == before
    exited = execute(ops.PhaseExit(feature=FEATURE))
    assert not exited.ok and read(host.state)["stage"] == "implement"


@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_ac13_resolved_waiver_requires_its_manifest_at_status_and_boundary(
    tmp_path, monkeypatch, damage: str
) -> None:
    from tests.change_impact_verify_helpers import journal_waiver, resolve_waiver

    host, decision_id, artifact = _proposed_implement_waiver(tmp_path, monkeypatch)
    resolved = resolve_waiver(decision_id)
    assert resolved.ok, resolved.to_envelope()
    journal_waiver(host.root, host.state, decision_id)
    _damage(host.state.parent / artifact, damage)
    before = host.state.read_bytes()

    for operation in (ops.Status(feature=FEATURE), ops.Orient(feature=FEATURE)):
        _assert_names_the_manifest(execute(operation), decision_id, artifact)
    _assert_names_the_manifest(
        execute(ops.PhaseExit(feature=FEATURE)), decision_id, artifact
    )
    assert host.state.read_bytes() == before


@pytest.mark.parametrize("change", ["content", "membership"])
def test_ac14_other_changes_refuse_the_proposal_naming_the_paths(
    tmp_path, monkeypatch, change: str
) -> None:
    from tests.change_impact_verify_helpers import implement_exit_host

    host = implement_exit_host(tmp_path, monkeypatch)
    if change == "content":
        changed = "src/example.py"
        # A new comment: a real content change under the Python rule.
        (host.root / changed).write_text("VALUE = 7  # behaviour note\n")
        verify(host.state, "m1")
    else:
        changed = "src/extra.py"
        _own(host.root, host.state, changed)
        verify(host.state, "m2")
    reasons = [item.reason for item in _implement_exit_actions()]
    assert not any("witness-waiver" in reason for reason in reasons), reasons
    before = host.state.read_bytes()

    refused = _propose("acceptance")

    assert not refused.ok and host.state.read_bytes() == before
    assert changed in json.dumps(refused.to_envelope()), (
        f"FAIL AC-14: the implement refusal must name the {change} path "
        f"{changed}: {refused.to_envelope()}"
    )
    exited = execute(ops.PhaseExit(feature=FEATURE))
    assert not exited.ok and host.state.read_bytes() == before, exited.to_envelope()
    assert "acceptance" in exited.error.message, exited.to_envelope()


def test_ac14_a_witness_that_never_passed_is_never_acceptable(
    tmp_path, monkeypatch
) -> None:
    """Survivor pin: failed witness evidence is never waivable, even at implement."""
    from tests.change_impact_verify_helpers import implement_exit_host

    host = implement_exit_host(tmp_path, monkeypatch)
    value = read(host.state)
    value["commands"]["acceptance_test"] = "python3 -c 'raise SystemExit(1)'"
    write(host.state, value)
    execute(ops.Verify(feature=FEATURE, scope="acceptance"))
    assert read(host.state)["verifications"][-1]["exit_code"] != 0
    (host.root / "src/example.py").write_text("VALUE = (7)\n")
    before = host.state.read_bytes()

    refused = _propose("acceptance")

    assert not refused.ok, refused.to_envelope()
    assert host.state.read_bytes() == before
    # Refused today because implement is not a waiver stage; refused after the
    # change because the evidence failed. Never because of what changed.
    message = refused.error.message
    assert "failed" in message or "pre-review witness run is mandatory" in message


def _changes(*kinds: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        changes=tuple(
            SimpleNamespace(kind=kind, path=f"src/{kind}.py") for kind in kinds
        )
    )


_POST_REVIEW = ("peer-review", "robustness", "complete")
_ROUTE_MATRIX = [
    ("implement", "content-stale", ("formatting-only",), "accept-prior"),
    ("implement", "content-stale", ("formatting-only", "content"), "rerun-only"),
    ("implement", "content-stale", (), "rerun-only"),
    ("implement", "source-set-stale", ("membership",), "rerun-only"),
    *(
        (stage, status, kinds, "waivable")
        for stage in _POST_REVIEW
        for status, kinds in (
            ("content-stale", ("formatting-only",)),
            ("content-stale", ("content",)),
            ("source-set-stale", ("membership",)),
        )
    ),
    *(
        (stage, status, (), "rerun-only")
        for stage in ("implement", *_POST_REVIEW)
        for status in ("command-stale", "failed", "missing", "unstable")
    ),
    ("scaffold", "content-stale", ("formatting-only",), "rerun-only"),
    ("complete", "fresh", (), None),
]


@pytest.mark.parametrize(("stage", "status", "kinds", "route"), _ROUTE_MATRIX)
def test_the_rerun_route_follows_the_stage_and_the_evidence(
    stage: str, status, kinds: tuple[str, ...], route: str | None
) -> None:
    from heddle.kernel.witness_waiver import witness_rerun_route

    explanation = _changes(*kinds) if kinds else None
    for scope in ("acceptance", "live"):
        row = VerificationFreshness(scope, status, None, 3, explanation=explanation)
        result = witness_rerun_route(stage, row)
        assert (result.route if result else None) == route, (scope, result)
        if result is not None and "content" in kinds and route == "rerun-only":
            assert "its source has content src/content.py" in result.reason
    # Smoke and milestone scopes have no owner alternative; a waived lane
    # carries no route.
    for scope in ("smoke", "m1"):
        row = VerificationFreshness(scope, status, None, 3, explanation=explanation)
        assert witness_rerun_route(stage, row) is None
    waived = VerificationFreshness(
        "acceptance", status, None, 3, waived_by="D1", explanation=explanation
    )
    assert witness_rerun_route(stage, waived) is None


def test_status_and_orient_state_the_route_until_a_waiver_binds(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _review_fix(host.root, 8)
    verify(host.state, "smoke")

    row = _status_row("acceptance")
    assert row["rerun_route"] == {
        "route": "waivable",
        "reason": "the post-review rerun is the default, and the owner may waive "
        "it with a witness-waiver decision",
    }
    assert "rerun_route" not in _status_row("smoke")
    code, out, _err = run_cli(["orient", "--feature", FEATURE])
    assert code == 0
    line = next(
        line
        for line in out.splitlines()
        if line.startswith("  verification acceptance")
    )
    assert line.endswith("; waivable: " + row["rerun_route"]["reason"]), line

    decision_id = _waive(host.state, "acceptance")
    row = _status_row("acceptance")
    assert row["waived_rerun"] == decision_id and "rerun_route" not in row, row

    _review_fix(host.root, 9)
    row = _status_row("acceptance")
    assert row["rerun_route"]["route"] == "waivable"
    assert (
        f"(witness waiver {decision_id} stopped binding when the lane changed after it)"
    ) in row["rerun_route"]["reason"]


@pytest.mark.parametrize("change", ["command-stale", "implement-content"])
def test_an_unwaivable_lane_states_why_and_its_refusal_says_the_same(
    tmp_path, monkeypatch, run_cli, change: str
) -> None:
    if change == "command-stale":
        host = final_host(tmp_path, monkeypatch)
        value = read(host.state)
        value["commands"]["acceptance_test"] += " # changed witness"
        write(host.state, value)
        expected = "the stored command changed since the last run"
    else:
        from tests.change_impact_verify_helpers import implement_exit_host

        host = implement_exit_host(tmp_path, monkeypatch)
        (host.root / "src/example.py").write_text("VALUE = 7  # behaviour note\n")
        verify(host.state, "m1")
        expected = "its source has content src/example.py"

    route = _status_row("acceptance")["rerun_route"]
    assert route["route"] == "rerun-only" and expected in route["reason"], route
    code, out, _err = run_cli(["orient", "--feature", FEATURE])
    assert code == 0
    assert "; rerun-only: " + route["reason"] in out, out
    before = host.state.read_bytes()

    refused = _propose("acceptance")

    assert not refused.ok and host.state.read_bytes() == before
    assert refused.error.message.endswith(route["reason"]), (
        "the refusal must read the route orient showed: "
        f"{refused.error.message!r} vs {route['reason']!r}"
    )
