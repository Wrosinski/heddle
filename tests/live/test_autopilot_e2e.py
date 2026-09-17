"""
Live E2E for autopilot — Autopilot: a real `heddle drive` run.

Command: `.venv/bin/python -m pytest --test-band live --allow-live --allow-e2e
tests/live/test_autopilot_e2e.py`.
Behavior contract: autopilot

Isolated from default runs: requires an explicit live E2E grant and both direct
permission flags. It exercises the same control flow as the acceptance test
but with a REAL headless `claude -p` phase session (no session-double, no mocks)
against a copied representative fixture workspace — the production-like check the
mocked acceptance test cannot give (real CLI argv handling, real stream-json,
real permission-mode behavior).

Prerequisites (the user provides a live runner — test scaffolding checkpoint): the
Claude
Code CLI installed + authenticated on the runner, and a per-session budget
(`--max-budget-usd` host-policy value; est. cost ~ one phase session's tokens per
boundary, bounded). Idempotent: each run copies the fixture into a fresh tmp_path,
and the driver is stateless (resume == re-invocation), so it is safe to re-run.

preimplementation state: `heddle drive` does not exist until foundation — these tests
SKIP by default
(the opt-in gate) and FAIL only when explicitly run before the driver lands.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.e2e,
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AUTO_FIXTURE = REPO_ROOT / "tests" / "driver" / "fixtures" / "workspaces" / "auto-tier2"
SLUG = "autopilot-demo"


def _run_drive_live(host: Path, *argv: str, timeout: int = 600):
    """Run the real heddle CLI as a subprocess inside the copied host."""
    proc = subprocess.run(
        [sys.executable, "-m", "heddle", *argv, "--json"],
        cwd=host,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    try:
        envelope = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"FAIL live: no clean JSON envelope; exit={proc.returncode}, "
            f"stdout={proc.stdout!r}, stderr={proc.stderr!r}"
        ) from exc
    return proc.returncode, envelope, proc.stderr


def _read_state(host: Path) -> dict:
    import yaml

    return yaml.safe_load(
        (host / "plans" / SLUG / "state.yaml").read_text(encoding="utf-8")
    )


def test_live_ac02_ac05_bounded_real_drive_advances_one_boundary(tmp_path):
    """
    Bounded live coverage of AC-5 (the `--until` stop) and AC-2 (the
        `source: policy` grant on a clean boundary) with a REAL headless claude phase
        session. `--until spec-review` keeps the per-run cost to a single phase
        segment. This is NOT full AC-9 (that would drive to completion and compare the
        golden HITL baseline — deferred per the AC Coverage Matrix / review).
    """
    host = shutil.copytree(AUTO_FIXTURE, tmp_path / "auto-tier2")
    code, envelope, _stderr = _run_drive_live(
        host, "drive", "--feature", SLUG, "--until", "spec-review"
    )
    assert code == 0, (
        f"FAIL live AC-5: a real bounded drive must advance cleanly (exit 0), "
        f"got {code}: {envelope.get('error')}"
    )
    state = _read_state(host)
    # AC-5: the bounded run stops EXACTLY at spec-review (does not overrun --until).
    assert state["authorized_through"] == "spec-review", (
        "FAIL live AC-5: a `--until spec-review` real drive must stop exactly at "
        f"spec-review, got {state['authorized_through']!r}"
    )
    # AC-2: the clean auto boundary records a source: policy grant.
    assert any(g.get("source") == "policy" for g in state.get("authorizations", [])), (
        "FAIL live AC-2: the real auto boundary must record a source: policy grant"
    )


def test_live_idempotent_reinvocation(tmp_path):
    """The stateless driver is re-invocable: a second bounded drive over the
    already-advanced workspace re-resolves and does not corrupt state."""
    host = shutil.copytree(AUTO_FIXTURE, tmp_path / "auto-tier2")
    # review: assert the first (setup) drive itself succeeded before taking the
    # baseline — a degraded first run would make the idempotency comparison
    # meaningless (it would compare two failures).
    first_code, first_env, _se = _run_drive_live(
        host, "drive", "--feature", SLUG, "--until", "spec-review"
    )
    assert first_code == 0 and first_env.get("ok") is True, (
        "FAIL live: the first idempotency-setup drive must itself succeed (exit 0, "
        f"ok envelope) before the baseline is captured; got {first_code}: "
        f"{first_env.get('error')}"
    )
    first = _read_state(host)
    code, _envelope, _stderr = _run_drive_live(
        host, "drive", "--feature", SLUG, "--until", "spec-review"
    )
    assert code == 0, (
        "FAIL live: re-invocation over an advanced workspace must be clean"
    )
    second = _read_state(host)
    assert second["authorized_through"] == first["authorized_through"], (
        "FAIL live: a bounded re-invocation must be idempotent (no further advance "
        "past --until)"
    )
