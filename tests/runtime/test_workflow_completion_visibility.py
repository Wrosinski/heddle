"""Workflow-usability close-obligation reporting and survivor contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.runtime import application
from tests.tiering_helpers import snapshot
from tests.tiering_review_helpers import V7_FEATURE, current_host

REPO = Path(__file__).resolve().parents[2]


def _configure_close_suite(host: Path, command: str | None) -> None:
    path = host / ".heddle.yaml"
    document = yaml.safe_load(path.read_text())
    autopilot = document.setdefault("autopilot", {})
    if command is None:
        autopilot.pop("test_command", None)
    else:
        autopilot["test_command"] = command
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("command", [None, "python -c 'raise SystemExit(0)'"])
def test_ac11_status_and_kickoff_report_close_obligation_without_running_it(
    tmp_path, monkeypatch, run_cli, command: str | None
) -> None:
    """AC-11 red: loaded host close config is visible on pure read surfaces."""
    host, _state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    sentinel = host / "close-suite-ran"
    configured = (
        f"python -c \"from pathlib import Path; Path('{sentinel}').write_text('ran')\""
        if command is not None
        else None
    )
    _configure_close_suite(host, configured)
    before = snapshot(host)

    status = application.execute(ops.Status(feature=V7_FEATURE))
    kickoff = application.execute(ops.Kickoff(feature=V7_FEATURE))

    expected = {
        "configured": configured is not None,
        "command": configured,
        "source": ".heddle.yaml:autopilot.test_command",
        "runs_at": "feature complete",
    }
    assert status.data["close_obligation"] == expected
    assert kickoff.data["close_obligation"] == expected
    state = "configured" if configured is not None else "absent"
    rendered_command = configured if configured is not None else "(none)"
    expected_line = (
        f"close obligation ({state}): {rendered_command}; "
        "source: .heddle.yaml:autopilot.test_command; runs at: feature complete"
    )
    for argv in (
        ["status", "--feature", V7_FEATURE],
        ["kickoff", "--feature", V7_FEATURE],
    ):
        code, output, error = run_cli(argv)
        assert code == 0, error
        assert expected_line in output
    assert snapshot(host) == before and not sentinel.exists()


def test_ac11_help_and_briefings_distinguish_all_three_proof_owners(run_cli) -> None:
    """AC-11 red: public wording names host, feature, and extra close duties."""
    code, output, error = run_cli(["feature", "complete", "--help"])
    assert code == 0
    surfaces = {"feature complete --help": output + error}
    surfaces.update(
        {
            name: (REPO / "heddle/resources" / name).read_text(encoding="utf-8")
            for name in (
                "specify.briefing.md",
                "scaffold.briefing.md",
                "implement.briefing.md",
                "complete.briefing.md",
                "heddle-yaml.scaffold.yaml",
            )
        }
    )
    required = (
        "host tooling",
        "feature proof",
        "autopilot.test_command",
        "additional close",
        "does not create a clean environment",
    )
    for surface, text in surfaces.items():
        folded = " ".join(text.split()).casefold()
        for phrase in required:
            assert phrase in folded, (
                f"FAIL AC-11: {surface} omits required wording {phrase!r}"
            )


def test_ac12_completion_preview_retains_execution_and_reporting_contracts(
    tmp_path, monkeypatch
) -> None:
    """AC-12 red: new visibility coexists with the existing dry-run contract."""
    from tests.tiering_completion_helpers import final_host

    completed = final_host(tmp_path, monkeypatch)
    config = yaml.safe_load((completed.root / ".heddle.yaml").read_text())
    command = config["autopilot"]["test_command"]
    before = snapshot(completed.root)
    result = completed.complete(dry_run=True)
    assert result.ok, result.to_envelope()
    assert result.data["dry_run"] is True
    assert result.data["accepted"] is False
    assert result.data["close_suite_command"] == command
    assert result.data["close_obligation"] == {
        "configured": True,
        "command": command,
        "source": ".heddle.yaml:autopilot.test_command",
        "runs_at": "feature complete",
    }
    assert snapshot(completed.root) == before


def test_ac12_survivor_completion_dry_run_reports_but_does_not_execute_suite(
    tmp_path, monkeypatch
) -> None:
    """AC-12 survivor: completion preview never launches the host command."""
    host, _state_path = current_host(tmp_path, monkeypatch, stage="complete")
    sentinel = host / "close-suite-ran"
    command = (
        f"python -c \"from pathlib import Path; Path('{sentinel}').write_text('ran')\""
    )
    _configure_close_suite(host, command)
    result = application.execute(ops.FeatureComplete(feature=V7_FEATURE, dry_run=True))
    # The fixture may have earlier completion blockers; preview still cannot run it.
    assert not sentinel.exists()
    if result.ok:
        assert result.data["dry_run"] is True
        assert result.data["close_suite_command"] == command


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize(
    "surface", ["preview", "write", "retry", "status", "kickoff", "portfolio"]
)
def test_proof_continuity_close_suite_projection_uses_execution_authority(
    tmp_path, monkeypatch, configured, surface
):
    """AC-9: prospective null vs recorded fact; no rerun on retry/read/config drift."""
    from tests.operational_model_helpers import read, write
    from tests.readiness_helpers import verify
    from tests.tiering_completion_helpers import final_host

    host = final_host(tmp_path, monkeypatch, verify_now=False)
    command = (
        'python3 -c "from pathlib import Path; '
        f"p=Path('{host.calls.relative_to(host.root).as_posix()}'); "
        "p.write_text((p.read_text() if p.exists() else '') + 'close\\n')\""
        if configured
        else None
    )
    _configure_close_suite(host.root, command)
    state = read(host.state)
    state["milestones"][0]["owns"].append(".heddle.yaml")
    write(host.state, state)
    verify(host.state, "m1", "m2", "acceptance", "smoke")
    if surface == "preview":
        before = snapshot(host.root)
        result = host.complete(dry_run=True)
        assert result.ok, result.to_envelope()
        expected_fact = None
        assert snapshot(host.root) == before
        assert host.suite_calls() == []
    else:
        accepted = host.complete()
        assert accepted.ok and accepted.data["accepted"], accepted.to_envelope()
        expected_fact = read(host.state)["completion"]["close_suite"]
        if surface == "write":
            result = accepted
        else:
            # A changed current configuration must not replace accepted execution.
            _configure_close_suite(host.root, "python3 -c 'raise SystemExit(99)'")
            before = snapshot(host.root)
            if surface == "retry":
                result = host.complete()
            elif surface == "portfolio":
                from dataclasses import replace

                result = application.execute(ops.Status(all_features=True))
                assert snapshot(host.root) == before
                assert result.ok, result.to_envelope()
                result = replace(
                    result,
                    data=next(
                        row
                        for row in result.data["features"]
                        if row["feature"] == V7_FEATURE
                    ),
                )
            else:
                operation = ops.Status if surface == "status" else ops.Kickoff
                result = application.execute(operation(feature=V7_FEATURE))
                assert snapshot(host.root) == before
            assert result.ok, result.to_envelope()
        assert host.suite_calls() == (["close"] if configured else [])
    assert result.data["close_suite_command"] == command
    assert result.data["close_suite"] == expected_fact
    assert result.data["close_obligation"] == {
        "configured": configured,
        "command": command,
        "source": ".heddle.yaml:autopilot.test_command",
        "runs_at": "feature complete",
    }
