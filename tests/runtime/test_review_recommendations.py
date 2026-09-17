"""Relaxed recommendation prose survives real recording, readback and replay."""

import json

import pytest
import yaml

from tests.structured_review_helpers import finding
from tests.tiering_helpers import ASTRA, OPUS, entry, snapshot
from tests.tiering_review_helpers import (
    current_host,
    gate_command,
    provider_transport,
    review_content,
    review_status,
    runs,
)


@pytest.mark.parametrize("selection", [ASTRA, OPUS], ids=["codex", "claude"])
def test_explanatory_recommendation_records_one_pending_owner_without_retries(
    tmp_path, monkeypatch, run_cli, selection
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={"behavior-review": entry("behavior-review", primary=selection)},
    )
    row = finding("BR-I1", classification="report")
    prose = "Prefer configuration, retaining the existing boundary and its guarantees."
    row["decision"]["recommended"] = prose
    calls = provider_transport(
        monkeypatch, review_content("behavior-review", findings=[row])
    )
    code, result = gate_command(run_cli, "run-gate", "behavior-review")
    assert code in (0, 4) and result["ok"], result
    assert len(calls) == 1
    recorded = runs(path)
    assert len(recorded) == 1 and recorded[0]["verdict"]["status"] != "error"
    canonical = host / result["data"]["artifact"]
    raw = canonical.read_bytes()
    value = json.loads(raw)
    assert value["invocation"]["output_contract_version"] == "heddle.review-content/v3"
    assert value["content"]["findings"][0]["decision"]["recommended"] == prose
    view = canonical.with_name(canonical.name.removesuffix(".review.json") + ".md")
    assert prose in view.read_text()
    decisions = yaml.safe_load(path.read_text())["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["origin_run_id"] == recorded[0]["run_id"]
    assert decisions[0]["origin_finding_id"] == "BR-I1"
    assert decisions[0]["status"] == "pending"
    status = review_status(path, role="behavior-review")
    assert not status["closed"]
    before = snapshot(host)
    replay_code, replay = gate_command(run_cli, "run-gate", "behavior-review")
    assert replay_code in (0, 4) and replay["ok"]
    assert replay["data"]["cached"] is True
    assert replay["data"]["run_id"] == recorded[0]["run_id"]
    assert len(calls) == 1 and snapshot(host) == before
    assert canonical.read_bytes() == raw
