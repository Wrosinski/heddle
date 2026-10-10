"""W5 shipped commands and permanent contract/evidence navigation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heddle.runtime.contracts import build_manifest
from tests.completion_helpers import completion_operation

REPO = Path(__file__).resolve().parents[2]
FEATURE = "completion-cleanup"
pytestmark = pytest.mark.acceptance


def test_ac7_public_completion_metadata_and_operation_agree(
    run_cli, tmp_path, monkeypatch
):
    from tests.completion_helpers import tree_bytes
    from tests.operational_model_helpers import FEATURE as FIXTURE_FEATURE
    from tests.operational_model_helpers import read
    from tests.tiering_completion_helpers import final_host

    entries = {entry["name"]: entry for entry in build_manifest()["commands"]}
    assert "feature complete" in entries, (
        "FAIL W5 AC-7: public completion command is absent"
    )
    entry = entries["feature complete"]
    assert entry["mutating"] and entry["dry_run"]
    assert 4 in entry["exit_codes"]
    assert entry["input_schema"] is None
    _app, operation = completion_operation()
    from heddle.contracts.operations import operation_command, operation_payload

    value = operation(feature="example", expect_revision=7, dry_run=True)
    assert operation_payload(value) == {
        "name": "feature complete",
        "arguments": {"feature": "example", "expect_revision": 7, "dry_run": True},
    }
    command = operation_command(value)
    assert (
        "feature complete" in command
        and "--dry-run" in command
        and "--expect-revision 7" in command
    )
    host = final_host(tmp_path, monkeypatch)
    before = tree_bytes(host.root)
    code, output, _error = run_cli(
        [
            "feature",
            "complete",
            "--feature",
            FIXTURE_FEATURE,
            "--expect-revision",
            str(read(host.state)["revision"]),
            "--dry-run",
            "--json",
        ]
    )
    envelope = json.loads(output)
    assert code == 0 and envelope["ok"] and not envelope["data"]["accepted"], envelope
    assert tree_bytes(host.root) == before


def test_ac7_completion_briefing_advertises_acceptance_and_retention_barrier():
    """AC-11 red: packaged completion guidance describes local retention."""
    text = " ".join(
        (REPO / "heddle/resources/complete.briefing.md").read_text().split()
    )
    assert "heddle feature complete" in text, (
        "FAIL W5 AC-7: complete briefing still uses narrative closure"
    )
    folded = text.casefold()
    assert "completion.tar.gz" in text and "local retention" in folded
    assert "git commit is optional" in folded
    assert "commit the accepted" not in folded
    assert "plan.md" in text and "brief.md" in text and "retain" in folded
    assert "receipt" not in text.lower() or "historical" in text.lower()


def test_local_history_completion_briefing_describes_optional_record_commits():
    """Feature-specific binding for the local-retention contract."""
    test_ac7_completion_briefing_advertises_acceptance_and_retention_barrier()


def test_ac9_completion_retains_every_manifest_a_disposition_names(
    run_cli, tmp_path, monkeypatch
):
    """AC-9 red: completion retains each named basis manifest and requires it."""
    from tests.change_impact_explanation_helpers import (
        basis_manifest,
        disposition_rows,
    )
    from tests.tiering_completion_helpers import final_host
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        gate_command,
        provider_transport,
        review_content,
        runs,
    )

    host = final_host(
        tmp_path,
        monkeypatch,
        overrides={"behavior-review": entry("behavior-review")},
    )
    calls = provider_transport(monkeypatch, review_content("behavior-review"))
    code, result = gate_command(run_cli, "run-gate", "behavior-review")
    assert code in (0, 4) and result["ok"], result
    [origin] = [row["run_id"] for row in runs(host.state)]
    assert dispose(host.state, [disposition(origin, "@coverage", status="settled")]).ok
    [row] = disposition_rows(host.state)
    reference = basis_manifest(host.state, row)

    preview = host.complete(dry_run=True)
    assert preview.ok, preview.to_envelope()
    retained = {
        item["path"]: item for item in preview.data["retained_evidence"]["artifacts"]
    }
    assert reference["artifact"] in retained, (
        "FAIL AC-9: completion does not retain the basis manifest a disposition names"
    )
    assert retained[reference["artifact"]]["sha256"] == reference["artifact_sha256"]

    (host.state.parent / reference["artifact"]).unlink()
    refused = host.complete()
    assert not refused.ok, (
        "FAIL AC-9: completion accepted without a manifest a disposition names"
    )
    assert reference["artifact"] in json.dumps(refused.to_envelope()), (
        f"FAIL AC-9: the refusal does not name the missing manifest: "
        f"{refused.to_envelope()['error']}"
    )
    assert len(calls) == 1
