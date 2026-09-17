"""
adoption steering fix (2026-07-22): completed run-gate envelopes name the
dual-suite round's next step (kernel §12.2 projection, re-resolved after
the fact write) and the synthesis convergence authority; status surfaces
per-pipeline progress rows. Guidance only — a steering fault must never
fail a completed run (RR-core posture).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tests.structured_review_helpers import finding, scripted_review

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"

PASSING_REVIEW = scripted_review(
    [
        finding(
            "XX-I1",
            classification="implement",
            severity="important",
            title="Tighten the wording",
            recommendation="Apply the fixture repair.",
            route=None,
        )
    ],
    summary="Tighten the wording",
)


def _spec_review_host(tmp_path: Path, monkeypatch) -> Path:
    """A golden copy armed at stage spec-review with an empty gate trail —
    the Tier-3 dual-suite round's starting position."""
    host = tmp_path / "host"
    shutil.copytree(GOLDEN, host)
    state_path = host / "plans" / "nl-screening" / "state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["stage"] = "spec-review"
    state["authorized_through"] = "spec-review"
    state["gates"] = []
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(host)
    return host


def _run_gate(run_cli, fake_gate_runner, gate: str, *, cli: str = "claude"):
    fake_gate_runner(review=PASSING_REVIEW)
    argv = ["run-gate", "--feature", "nl-screening"]
    if cli != "claude":
        argv.extend(["--cli", cli])
    argv.extend([gate, "--json"])
    return run_cli(argv)


def test_blocking_label_maps_every_kernel_phase():
    # Pins the phase→label table end to end: every kernel phase string has
    # a rendering, and an unqualified phase falls back to the bare code.
    from heddle.runtime.status import _GATE_PHASE_LABELS, _blocking_label

    assert set(_GATE_PHASE_LABELS) == {
        "not-run",
        "round-in-progress",
        "verdict-fail",
        "rerun-recommended",
    }
    for phase, label in _GATE_PHASE_LABELS.items():
        assert (
            _blocking_label(
                "gate-not-converged", deferral_active=False, gate_phase=phase
            )
            == f"gate-not-converged ({label})"
        )
    assert (
        _blocking_label("gate-not-converged", deferral_active=False, gate_phase=None)
        == "gate-not-converged"
    )
