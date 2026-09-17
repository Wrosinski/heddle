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
