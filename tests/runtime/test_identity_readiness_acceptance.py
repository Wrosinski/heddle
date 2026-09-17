"""Integrated acceptance for identity readiness and final handoff."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tests.tiering_helpers import OPUS, entry, snapshot
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    gate_command,
    provider_transport,
    review_content,
)

ROOT = Path(__file__).resolve().parents[2]


def test_ac1_ac2_gate_and_command_boundaries_are_composed() -> None:
    from heddle.gate import entry, extraction
    from heddle.runtime.contracts import build_manifest

    assert callable(getattr(extraction, "extract_explicit_test_paths", None))
    assert not hasattr(entry, "gate_type_for")
    assert not hasattr(entry, "resolve_gate_prompt")
    commands = {row["name"]: row for row in build_manifest()["commands"]}
    assert commands["run-gate"]["mutating"] is True
    assert {flag["name"] for flag in commands["run-gate"]["flags"]} >= {
        "--cli",
        "--model",
        "--reasoning-effort",
        "--feature",
    }


def test_ac3_through_ac6_public_host_review_and_reporting_flow(
    tmp_path, monkeypatch, run_cli, envelope_tools
) -> None:
    from heddle.io import claude_events
    from heddle.runtime.trajectory import archive_trajectory

    host, _state = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=OPUS)},
    )
    plan = host / "plans" / V7_FEATURE / "plan.md"
    plan.write_text(
        plan.read_text()
        + "\n<!-- heddle:begin plan-status -->\nNative fixture status.\n"
        + "<!-- heddle:end plan-status -->\n"
    )
    before_sync = snapshot(host)
    sync_code, sync_out, _ = run_cli(
        ["sync", "--feature", V7_FEATURE, "--dry-run", "--json"]
    )
    assert sync_code == 0, sync_out
    assert envelope_tools.parse(sync_out)["data"]["dry_run"] is True
    assert snapshot(host) == before_sync

    calls = provider_transport(monkeypatch, review_content())
    code, reviewed = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and reviewed["data"]["accepted"], reviewed
    assert len(calls) == 1
    assert claude_events.last_result_event(
        [{"type": "assistant"}, {"type": "result", "subtype": "success"}]
    ) == {"type": "result", "subtype": "success"}

    report = archive_trajectory(host, V7_FEATURE, now=date(2026, 9, 13))
    assert report is not None
    budget = json.loads(report.read_text())["budget"]
    assert budget["rounds_count"] == 1 and budget["calls_count"] == 1
    assert "near_cap" not in budget and "near_cap_runs" not in budget


def test_ac7_through_ac10_resources_and_support_bindings_are_current(
    tmp_path: Path, monkeypatch
) -> None:
    from heddle.contracts.schemas import CURRENT_STATE_SCHEMA
    from heddle.kernel.project_config import load_project_config
    from heddle.kernel.resources import resolve_resource
    from heddle.runtime.contracts import build_manifest

    monkeypatch.chdir(tmp_path)
    config = load_project_config(ROOT)
    assert config.root == ROOT
    for asset in ("model-prompting-principles.md", "prompt-authoring-standards.md"):
        path, source = resolve_resource(asset, config)
        assert path.is_file() and path.read_text().strip()
        assert source in {"override", "generated", "packaged"}
    commands = {row["name"] for row in build_manifest()["commands"]}
    assert {"help", "init", "status", "orient", "review allowance"} <= commands
    assert (ROOT / "tests/fixtures/pytest-inventory-current.txt.gz.b64").is_file()
    assert CURRENT_STATE_SCHEMA == "heddle.state/v10"
