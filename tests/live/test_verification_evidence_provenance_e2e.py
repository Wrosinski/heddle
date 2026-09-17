"""
Local process-real verification provenance smoke.

This lane has no external service prerequisite. It is excluded from default
execution and requires an exact `--test-band e2e --allow-e2e` invocation plus
an explicit local E2E grant.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.runtime.verification_provenance_helpers import make_current_host

pytestmark = [
    pytest.mark.e2e,
]


def _git(host: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=host, check=True)


def test_current_evidence_survives_workspace_cleanup(
    tmp_path: Path,
) -> None:
    """AC-3/7/8/9/10: real CLI verify emits durable bound evidence."""
    from heddle.runtime.verification import read_source_evidence
    from tests.runtime.verification_provenance_helpers import arm_completion_boundary

    def run(host: Path, *args: str) -> dict:
        process = subprocess.run(
            [sys.executable, "-m", "heddle", *args, "--json"],
            cwd=host,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert process.returncode in {0, 4}, (
            f"FAIL live: {' '.join(args)} exited {process.returncode}; "
            f"stdout={process.stdout!r}, stderr={process.stderr!r}"
        )
        return json.loads(process.stdout)

    host, state_path = make_current_host(tmp_path, milestones=True)
    arm_completion_boundary(host, state_path)
    scopes = ("m1", "acceptance", "smoke")

    for scope in scopes:
        run(host, "verify", "--scope", scope, "--feature", "sample-feature")
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    facts = state["verifications"]
    for fact, scope in zip(facts, scopes, strict=True):
        assert fact["scope"] == scope
        before = read_source_evidence(state_path.parent, fact["evidence"]["before"])
        after = read_source_evidence(state_path.parent, fact["evidence"]["after"])
        assert before == after
        assert before.definition.kind == (
            "milestone-owns" if scope == "m1" else "feature-owned-union"
        )
        assert before.definition.paths == ("src.py",)
        assert fact["log"].startswith("verification/")
        assert "autopilot/" not in fact["log"]

    accepted = run(host, "feature", "complete", "--feature", "sample-feature")
    assert accepted["data"]["accepted"]
    assert accepted["data"]["effects"]["archive"]["status"] == "complete"
    archive = host / "docs/gate-trajectories/.raw/sample-feature/completion.tar.gz"
    before = state_path.read_bytes(), archive.read_bytes()
    _git(host, "add", "-f", "plans", "docs")
    _git(host, "commit", "-qm", "retain accepted completion")
    retry = run(host, "feature", "complete", "--feature", "sample-feature")
    assert retry["data"]["effects"]["cleanup"]["status"] == "complete"
    assert (state_path.parent / "plan.md").exists()
    assert "plan.md" in retry["data"]["effects"]["cleanup"]["preserved"]
    assert (state_path.read_bytes(), archive.read_bytes()) == before
    assert run(host, "status", "--feature", "sample-feature")["data"]["accepted"]
