"""AC-11 actual policy/round/call projection and truthful legacy reporting."""

import json
from datetime import date

import pytest
import yaml

from heddle.runtime.trajectory import archive_trajectory, write_aggregate
from tests.operational_model_helpers import FEATURE
from tests.structured_review_helpers import finding
from tests.tiering_helpers import FABLE, entry, invoke, snapshot, wire_policy
from tests.tiering_review_helpers import (
    current_host,
    dispose,
    disposition,
    gate_command,
    open_round,
    provider_transport,
    review_content,
    runs,
)

DAY = date(2026, 9, 9)


@pytest.mark.parametrize("operation", ["orient"])
def test_ac11_current_milestone_projects_assignments_without_legacy_pipelines(
    tmp_path, monkeypatch, operation
):
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    host, state = current_host(tmp_path, monkeypatch, stage="implement")
    before = snapshot(host)
    requested = {
        "status": ops.Status(feature=FEATURE),
        "orient": ops.Orient(feature=FEATURE),
        "portfolio": ops.Status(all_features=True),
    }[operation]
    result = execute(requested)
    assert result.ok, result.to_envelope()
    data = result.data
    if operation == "portfolio":
        assert len(data["features"]) == 1
        data = data["features"][0]
        assert "error" not in data, data
    assert data["current_milestone"] == "m1"
    if operation != "orient":
        assert data["verdict_pipelines"] == []
    if operation != "portfolio":
        rows = data["review_closure"]["assignments"]
        milestones = [row for row in rows if row["role"] == "milestone-review"]
        assert [row["scope"] for row in milestones] == ["m1", "m2"]
        assert all(
            not row["closed"] and row["calls_completed"] == 0 for row in milestones
        )
    assert snapshot(host) == before


def test_ac11_dual_round_exports_two_calls_one_round_and_policy_amendment(
    tmp_path, monkeypatch, run_cli
):
    host, state = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", secondary=FABLE)},
    )
    round_number = {"value": 1}
    calls = provider_transport(
        monkeypatch,
        lambda cli, _prompt: review_content(
            findings=[finding("SP-I1", classification="implement")]
            if cli == "codex" and round_number["value"] == 1
            else []
        ),
    )
    for cli in ("codex", "claude"):
        code, result = gate_command(run_cli, "run-gate", "spec-review", "--cli", cli)
        assert code in (0, 4), result
    initial = runs(state)
    origin = next(row["run_id"] for row in initial if row["cli"] == "codex")
    recorded = dispose(
        state,
        [
            disposition(origin, status="retained"),
            *[
                disposition(row["run_id"], "@coverage", status="settled")
                for row in initial
            ],
        ],
    )
    assert recorded.ok, recorded.to_envelope()
    value = yaml.safe_load(state.read_text())
    changed = invoke(
        "FeaturePolicy",
        slug=FEATURE,
        payload=wire_policy(
            revision=2, overrides={"spec-review": entry("spec-review", limit=7)}
        ),
        expect_revision=value["revision"],
    )
    assert changed.ok, changed.to_envelope()
    opened = open_round(
        state, reason="Target the unresolved original SP-I1 integration concern"
    )
    assert opened.ok, opened.to_envelope()
    round_number["value"] = 2
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), result
    latest = runs(state)[-1]
    closed = dispose(
        state,
        [
            disposition(origin),
            disposition(latest["run_id"], "@coverage", status="settled"),
        ],
    )
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    before = state.read_bytes()
    destination = archive_trajectory(host, FEATURE, now=DAY)
    assert destination is not None
    record = json.loads(destination.read_text())
    assert len(record["runs"]) == 3
    assert len(record["rounds"]) == 2
    assert [row["round_number"] for row in record["runs"]] == [1, 1, 2]
    assert len({row["assignment_id"] for row in record["runs"]}) == 1
    assert [row["policy_revision"] for row in record["runs"]] == [1, 1, 2]
    assert record["effective_policy"]["revision"] == 2
    assert [row["revision"] for row in record["policy_history"]] == [1, 2]
    assert (
        record["effective_policy"]["axes"]
        == json.loads(json.dumps(wire_policy()))["axes"]
    )
    assert record["rounds"][-1]["purpose"] == "verification"
    assert [origin, "SP-I1"] in record["rounds"][-1]["before_open"]
    target = [
        row
        for row in record["dispositions"]
        if (row["run_id"], row["finding_id"]) == (origin, "SP-I1")
    ]
    assert [row["status"] for row in target] == ["retained", "addressed"]
    assert target[-1]["references"] and target[-1]["reason"]
    report = write_aggregate(destination.parent).read_text()
    assert "Features: 1" in report and FEATURE in report
    assert "verification" in report.lower() and "disposition" in report.lower()
    # The new aggregate labels rounds separately from provider calls and counts
    # the targeted pass, rather than treating the two initial lanes as two rounds.
    assert "| spec-review | 2 | 3 | 1 |" in report
    assert state.read_bytes() == before and len(calls) == 3
