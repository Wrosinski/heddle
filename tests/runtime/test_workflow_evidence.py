"""Workflow-usability evidence atomicity and freshness boundaries."""

from __future__ import annotations

from pathlib import Path

import yaml

from heddle.kernel.managed_regions import replace_managed_region
from tests.structured_review_helpers import finding
from tests.tiering_helpers import snapshot
from tests.tiering_review_helpers import (
    current_host,
    dispose,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    review_status,
    runs,
)

REPO = Path(__file__).resolve().parents[2]


def _report_with_two_findings(tmp_path, monkeypatch, run_cli):
    host, state_path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(
        monkeypatch,
        review_content(
            findings=[
                finding("SP-I1", classification="implement"),
                finding("SP-I2", classification="implement"),
            ]
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in {0, 4} and result["ok"], result
    assert len(calls) == 1 and len(runs(state_path)) == 1
    return host, state_path, calls, result


def test_ac4_state_self_reference_rejects_mixed_batch_atomically(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-4 red: preview/apply name the self-invalidating row and write nothing."""
    host, state_path, calls, _result = _report_with_two_findings(
        tmp_path, monkeypatch, run_cli
    )
    run_id = runs(state_path)[0]["run_id"]
    self_reference = state_path.relative_to(host).as_posix()
    rows = [
        disposition(run_id, "SP-I1", status="retained"),
        disposition(
            run_id,
            "SP-I2",
            status="retained",
            references=[self_reference],
        ),
    ]
    before = snapshot(host)

    preview = dispose(state_path, rows, dry_run=True)
    applied = dispose(state_path, rows)

    for result in (preview, applied):
        assert not result.ok and result.error.code == "disposition-batch-invalid"
        rejected = result.error.details["rows"]
        assert len(rejected) == 1 and rejected[0]["row_index"] == 1
        assert rejected[0]["predicate"] == "self-mutating-reference"
        assert rejected[0]["reference"] == self_reference
        remedy = rejected[0]["remedy"].casefold()
        assert "stable" in remedy and "state" in remedy
    assert snapshot(host) == before and len(calls) == 1


def test_ac4_survivor_other_yaml_reference_remains_accepted(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-4 survivor: YAML is not rejected merely because state is YAML."""
    host, state_path, _calls, _result = _report_with_two_findings(
        tmp_path, monkeypatch, run_cli
    )
    evidence = host / "docs/stable-evidence.yaml"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("result: inspected\n", encoding="utf-8")
    run_id = runs(state_path)[0]["run_id"]
    before_revision = yaml.safe_load(state_path.read_text())["revision"]
    result = dispose(
        state_path,
        [
            disposition(
                run_id,
                "SP-I1",
                status="retained",
                references=["docs/stable-evidence.yaml"],
            )
        ],
    )
    assert result.ok, result.to_envelope()
    assert yaml.safe_load(state_path.read_text())["revision"] == before_revision + 1


def test_ac6_survivor_generated_status_exclusion_preserves_explicit_raw_reference(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-6 survivor: basis projection exclusions cannot weaken explicit evidence."""
    host, state_path = current_host(tmp_path, monkeypatch, stage="plan-review")
    plan = state_path.with_name("plan.md")
    plan.write_text(
        plan.read_text()
        + "\n<!-- heddle:begin plan-status -->\n"
        + "Generated status before inspection.\n"
        + "<!-- heddle:end plan-status -->\n"
    )
    provider_transport(
        monkeypatch,
        review_content(
            "plan-review",
            findings=[finding("PL-I1", classification="implement")],
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "plan-review")
    assert code == 4 and result["ok"], result
    run_id = runs(state_path)[0]["run_id"]
    reference = plan.relative_to(host).as_posix()
    closed = dispose(
        state_path,
        [
            disposition(run_id, "PL-I1", references=[reference]),
            disposition(run_id, "@coverage", status="settled"),
        ],
    )
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()

    plan.write_text(
        replace_managed_region(
            plan.read_text(),
            "plan-status",
            "Generated status changed after explicit inspection.\n",
        )
    )
    reopened = review_status(state_path, role="plan-review")
    assert not reopened["closed"]
    assert [run_id, "PL-I1"] in reopened["open_refs"]


def test_ac6_briefings_publish_safe_edit_sync_format_inspect_record_order() -> None:
    """AC-6 red: documentation makes the freshness-preserving order executable."""
    text = "\n".join(
        (REPO / "heddle/resources" / name).read_text(encoding="utf-8")
        for name in (
            "spec-review.briefing.md",
            "plan-review.briefing.md",
            "peer-review.briefing.md",
        )
    )
    folded = " ".join(text.split()).casefold()
    expected = "finish edits, sync, format, inspect, then record dispositions"
    assert expected in folded, "FAIL AC-6: guidance omits the safe evidence order"
    assert "formatting is invisible to evidence" not in folded
