"""Structural checks for removal of stale-gate bypass behavior."""

from __future__ import annotations


def test_ac03_detect_stale_milestone_symbol_is_gone() -> None:
    # AC-3: the stale re-detection helper is deleted, not relocated.
    from heddle.gate import preflight

    assert not hasattr(preflight, "detect_stale_milestone"), (
        "FAIL: heddle.gate.preflight.detect_stale_milestone still exists — "
        "REQ-3 deletes the stale-milestone re-detection (milestone advance "
        "owns that mutation from M4)"
    )
    assert not hasattr(preflight, "stale_milestone_anomaly"), (
        "FAIL: the stale-milestone anomaly builder must be deleted with its "
        "detector (AC-3)"
    )


def test_ac03_allow_stale_escape_hatch_is_gone() -> None:
    # AC-3: no ALLOW_STALE_CURRENT_MILESTONE escape hatch remains in source.
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    preflight_src = (repo_root / "heddle" / "gate" / "preflight.py").read_text(
        encoding="utf-8"
    )
    assert "ALLOW_STALE_CURRENT_MILESTONE" not in preflight_src, (
        "FAIL: the ALLOW_STALE_CURRENT_MILESTONE env escape hatch must be "
        "removed from preflight.py (AC-3); it is no longer reachable once "
        "detect_stale_milestone is gone"
    )
    runner_src = (repo_root / "heddle" / "gate" / "runner.py").read_text(
        encoding="utf-8"
    )
    assert "ALLOW_STALE_CURRENT_MILESTONE" not in runner_src, (
        "FAIL: the runner's pre-build stale gate must be removed (AC-3)"
    )


def test_ac03_no_stale_reason_can_be_produced() -> None:
    # AC-3: the `stale-current-milestone` preflight reason no longer exists in
    # the engine — a diff that overlaps a non-current milestone's owned paths
    # must NOT fail with that reason.
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    preflight_src = (repo_root / "heddle" / "gate" / "preflight.py").read_text(
        encoding="utf-8"
    )
    assert "stale-current-milestone" not in preflight_src, (
        "FAIL: the `stale-current-milestone` fatal reason must be removed "
        "from preflight.py (AC-3)"
    )
