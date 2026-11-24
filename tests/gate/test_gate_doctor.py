"""
Tests for gate_doctor.py (standalone diagnostic tool).

Covers: AC-10 (structured diagnosis output in --json and default modes).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


def _make_summary(
    status: str = "failure",
    reason: str = "inactivity-timeout",
    cli: str = "codex",
    **overrides: object,
) -> dict[str, object]:
    base: dict[str, object] = {
        "feature": "test-feature",
        "gate": "self-review",
        "cli": cli,
        "plan": "plans/test.plan.md",
        "spec": "docs/features/data/test.md",
        "milestone": {
            "number": "2",
            "title": "Integration",
            "satisfies_ac_ids": ["AC-1"],
        },
        "status": status,
        "reason": reason,
        "gate_exit": 1,
        "cli_exit": 118,
        "runtime": {
            "model": "gpt-5.5",
            "reasoning_effort": "high",
            "sandbox": "danger-full-access",
            "workdir": ".",
            "timeout_seconds": 2700,
            "inactivity_seconds": 900,
            "inactivity_observed_seconds": 900,
            "inactivity_marker": "50000:1711360000",
        },
        "diff": {
            "source": "git-diff-head",
            "source_detail": "HEAD",
            "bytes": 5000,
            "changed_files": ["src/foo.py"],
        },
        "ownership": {
            "milestone_owned_paths": ["src/foo.py"],
            "overlap_status": "matched",
            "overlap_files": ["src/foo.py"],
        },
        "preflight": {"status": "passed", "fatal_reason": None, "anomalies": []},
        "output_contract": {"required_sections": [], "structure_errors": []},
        "io": {
            "output": "plans/test.self-review-m2.codex.md",
            "log": "plans/test.self-review-m2.codex.log",
            "summary": "plans/test.self-review-m2.codex.gate-summary.json",
            "raw_bytes": 50000,
            "filtered_bytes": 45000,
            "last_message_bytes": 0,
            "json_message_bytes": 5000,
            "output_bytes": 0,
            "output_lines": 0,
        },
        "codex_events": {
            "tool_event_count": 50,
            "message_event_count": 0,
            "turn_completed_count": 0,
            "transport_error_count": 0,
        },
        "artifacts": {
            "output": "plans/test.self-review-m2.codex.md",
            "log": "plans/test.self-review-m2.codex.log",
            "summary": "plans/test.self-review-m2.codex.gate-summary.json",
            "prompt": None,
            "raw_out": None,
            "last_message": None,
            "json_message": None,
            "command_output": None,
            "events_jsonl": None,
        },
    }
    base.update(overrides)
    return base


class TestGateDoctorJsonMode:
    """AC-10: --json mode produces parseable structured diagnosis."""

    @REQUIRES_IMPL
    def test_json_output_has_diagnosis_fields(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        summary_path = tmp_path / "gate-summary.json"
        summary_path.write_text(json.dumps(_make_summary()))

        result = subprocess.run(
            [
                sys.executable,
                "scripts/gate_doctor.py",
                "--summary",
                str(summary_path),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "category" in data
        assert "description" in data
        assert "likely_cause" in data
        assert "recommended_action" in data
        assert "inspect_artifacts" in data

    @REQUIRES_IMPL
    def test_timeout_tool_loop_diagnosis(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        summary_path = tmp_path / "gate-summary.json"
        summary_path.write_text(
            json.dumps(
                _make_summary(
                    reason="inactivity-timeout",
                    codex_events={
                        "tool_event_count": 50,
                        "message_event_count": 0,
                        "turn_completed_count": 0,
                        "transport_error_count": 0,
                    },
                )
            )
        )

        result = subprocess.run(
            [
                sys.executable,
                "scripts/gate_doctor.py",
                "--summary",
                str(summary_path),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        assert data["category"] == "timeout-tool-loop"

    def test_capacity_diagnosis_from_summary(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        summary_path = tmp_path / "gate-summary.json"
        summary_path.write_text(
            json.dumps(
                _make_summary(
                    reason="codex-at-capacity",
                    codex_events={
                        "tool_event_count": 50,
                        "message_event_count": 0,
                        "turn_completed_count": 0,
                        "transport_error_count": 0,
                        "capacity_error_count": 1,
                    },
                )
            )
        )

        result = subprocess.run(
            [
                sys.executable,
                "scripts/gate_doctor.py",
                "--summary",
                str(summary_path),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        assert data["category"] == "capacity"
        assert "same authorized model" in data["recommended_action"]

    @REQUIRES_IMPL
    def test_success_diagnosis(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        summary_path = tmp_path / "gate-summary.json"
        summary_path.write_text(
            json.dumps(
                _make_summary(
                    status="success",
                    reason="success",
                    codex_events={
                        "tool_event_count": 10,
                        "message_event_count": 1,
                        "turn_completed_count": 1,
                        "transport_error_count": 0,
                    },
                )
            )
        )

        result = subprocess.run(
            [
                sys.executable,
                "scripts/gate_doctor.py",
                "--summary",
                str(summary_path),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        assert data["category"] == "clean"


class TestGateDoctorDefaultMode:
    """AC-10: Default mode produces human-readable formatted text."""

    @REQUIRES_IMPL
    def test_default_output_contains_diagnosis(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        summary_path = tmp_path / "gate-summary.json"
        summary_path.write_text(json.dumps(_make_summary()))

        result = subprocess.run(
            [sys.executable, "scripts/gate_doctor.py", "--summary", str(summary_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Gate Diagnosis" in result.stdout or "Diagnosis" in result.stdout
        assert (
            "inactivity-timeout" in result.stdout or "timeout" in result.stdout.lower()
        )

    @REQUIRES_IMPL
    def test_default_output_shows_artifacts(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        summary_path = tmp_path / "gate-summary.json"
        summary_path.write_text(json.dumps(_make_summary()))

        result = subprocess.run(
            [sys.executable, "scripts/gate_doctor.py", "--summary", str(summary_path)],
            capture_output=True,
            text=True,
        )
        assert "Artifact" in result.stdout or "artifact" in result.stdout.lower()


class TestAC4AntigravityDoctorRow:
    """antigravity-reviewer-lane-v1 AC-4 (m2): `heddle doctor` probes the lane's
    binary, `agy`, only when `agents.antigravity` is true. The row follows the
    existing runner convention (code `runner-<key>`, informational, source
    present or absent), and the exit code is what it would be without the
    line. `shutil.which` is doubled; nothing is spawned."""

    BIN = "/fixture/bin"

    def _doctor(self, host, monkeypatch, capsys, *, setting, agy_on_path):
        import shutil

        import yaml

        from heddle.cli import main

        config_path = host / ".heddle.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["agents"] = {"claude": True, "codex": True}
        if setting is not None:
            config["agents"]["antigravity"] = setting
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        available = {"claude", "codex", *(("agy",) if agy_on_path else ())}
        calls: list[str] = []

        def fake_which(name, *_args, **_kwargs):
            calls.append(name)
            return f"{self.BIN}/{name}" if name in available else None

        monkeypatch.setattr(shutil, "which", fake_which)
        capsys.readouterr()
        code = main(["doctor", "--json"])
        envelope = json.loads(capsys.readouterr().out)
        rows = [
            row for row in envelope["diagnostics"] if row["code"].startswith("runner-")
        ]
        return code, envelope, rows, calls

    def _host(self, tmp_path, monkeypatch):
        from tests.operational_model_helpers import fresh_host

        host, _state = fresh_host(tmp_path)
        monkeypatch.chdir(host)
        return host

    def _existing_rows(self, rows):
        return [row for row in rows if row["code"] != "runner-antigravity"]

    def _not_info(self, envelope):
        return [row for row in envelope["diagnostics"] if row["severity"] != "info"]

    @pytest.mark.parametrize("agy_on_path", [True, False], ids=["present", "absent"])
    def test_enabled_lane_reports_agy_at_info_with_exit_unchanged(
        self, tmp_path, monkeypatch, capsys, agy_on_path
    ):
        """Red: today the key is an unknown advisory and `agy` is never probed."""
        host = self._host(tmp_path, monkeypatch)
        baseline_code, baseline, baseline_rows, _ = self._doctor(
            host, monkeypatch, capsys, setting=None, agy_on_path=agy_on_path
        )
        code, envelope, rows, calls = self._doctor(
            host, monkeypatch, capsys, setting=True, agy_on_path=agy_on_path
        )
        lane = [row for row in rows if row["code"] == "runner-antigravity"]
        assert lane == [
            {
                "severity": "info",
                "code": "runner-antigravity",
                "message": (
                    f"agy executable found at {self.BIN}/agy"
                    if agy_on_path
                    else "agy executable not found on PATH"
                ),
                "source": "present" if agy_on_path else "absent",
            }
        ], f"FAIL AC-4: doctor must report agy for agents.antigravity: true: {rows}"
        assert "agy" in calls and "antigravity" not in calls, (
            f"FAIL AC-4: doctor must probe the agy binary, not the key: {calls}"
        )
        assert code == baseline_code, (
            f"FAIL AC-4: the lane's row changed doctor's exit {baseline_code} -> "
            f"{code}: {self._not_info(envelope)}"
        )
        assert envelope["data"]["healthy"] == baseline["data"]["healthy"]
        assert self._existing_rows(rows) == baseline_rows

    @pytest.mark.parametrize("agy_on_path", [True, False], ids=["present", "absent"])
    def test_disabled_lane_reports_nothing_with_exit_unchanged(
        self, tmp_path, monkeypatch, capsys, agy_on_path
    ):
        """Red: today `antigravity: false` is an unknown key whose advisory
        turns doctor's exit from 0 to 4."""
        host = self._host(tmp_path, monkeypatch)
        baseline_code, _, baseline_rows, _ = self._doctor(
            host, monkeypatch, capsys, setting=None, agy_on_path=agy_on_path
        )
        code, envelope, rows, calls = self._doctor(
            host, monkeypatch, capsys, setting=False, agy_on_path=agy_on_path
        )
        assert code == baseline_code, (
            f"FAIL AC-4: agents.antigravity: false changed doctor's exit "
            f"{baseline_code} -> {code}: {self._not_info(envelope)}"
        )
        assert rows == baseline_rows and "agy" not in calls

    @pytest.mark.parametrize("agy_on_path", [True, False], ids=["present", "absent"])
    def test_survivor_absent_key_reports_nothing_for_the_lane(
        self, tmp_path, monkeypatch, capsys, agy_on_path
    ):
        """Survivor: with the key absent doctor reports the existing runners
        only and never probes `agy`, as today."""
        host = self._host(tmp_path, monkeypatch)
        code, envelope, rows, calls = self._doctor(
            host, monkeypatch, capsys, setting=None, agy_on_path=agy_on_path
        )
        assert code == 0 and envelope["data"]["healthy"] is True, envelope
        assert [row["code"] for row in rows] == ["runner-claude", "runner-codex"]
        assert calls == ["claude", "codex"]
