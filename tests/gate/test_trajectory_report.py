"""
Aggregate trajectory-report tests (Milestone 3).

Covers:
  AC-9   test_ac_9_aggregate_metrics — the v1 metric set, exact values
  AC-14  test_ac_14_corpus_edges     — empty + single-record corpora

These tests drive `scripts/gate-trajectory-report.py` through its CLI
(`--trajectories-root <dir>` over committed `*.json` records, exit 0/2) and
assert on the generated `_aggregate.md`.

Red-phase note: `scripts/gate-trajectory-report.py` does not exist until
Milestone 3 lands; every `_run_report` returns a non-zero exit (or the
output file is absent), so the assertions FAIL until the generator lands.

Formula reference: docs/workflow/workflow.md § Gate trajectories.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gate-trajectory-report.py"

SEVERITIES = ("critical", "important", "minor")
CLASSIFICATIONS = ("implement", "report", "ignore", "unknown")
SEV_CLASS_KEYS = tuple(f"{s}/{c}" for s in SEVERITIES for c in CLASSIFICATIONS)


def _proj(**nonzero: int) -> dict:
    """A curated run-findings projection (by_severity_classification + total +
    contradictions, the fields archival writes into the committed record)."""
    contradictions = nonzero.pop("contradictions", 0)
    bsc = {k: 0 for k in SEV_CLASS_KEYS}
    for key, val in nonzero.items():
        bsc[key.replace("__", "/")] = val
    return {
        "by_severity_classification": bsc,
        "total": sum(bsc.values()),
        "contradictions": contradictions,
    }


def _run(
    *,
    gate: str,
    iteration: int,
    max_iterations: int = 5,
    cli: str = "claude",
    milestone: str | None = None,
    is_synthesis: bool = False,
    findings: dict | None = None,
    findings_note: str | None = None,
) -> dict:
    run: dict[str, object] = {
        "gate": gate,
        "cli": cli,
        "milestone": milestone,
        "iteration": iteration,
        "max_iterations": max_iterations,
        "source_summary": f"x.{gate}.gate-summary.json",
        "satisfies_ac_ids": [],
        "is_synthesis": is_synthesis,
        "status": "success",
        "gate_exit": 0,
        "cli_exit": 0,
        "diff_bytes": 0,
        "changed_files": 0,
        "findings": findings,
    }
    if findings_note is not None:
        run["findings_note"] = findings_note
    return run


def _record(feature: str, area: str, runs: list[dict]) -> dict:
    near_runs = [
        r["source_summary"]
        for r in runs
        if r["max_iterations"] >= 2 and r["iteration"] / r["max_iterations"] >= 0.8
    ]
    return {
        "feature": feature,
        "area": area,
        "tier": "3",
        "archived_at": "2026-06-10",
        "runs": runs,
        "events": [],
        "budget": {
            "runs_count": len(runs),
            "total_iterations": sum(r["iteration"] for r in runs),
            "near_cap_runs": near_runs,
            "near_cap": bool(near_runs),
        },
    }


def _write_record(traj: Path, record: dict) -> None:
    traj.mkdir(parents=True, exist_ok=True)
    (traj / f"{record['feature']}.json").write_text(json.dumps(record, indent=2))


def _run_report(root: Path, *extra: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--trajectories-root", str(root), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def _aggregate_text(root: Path) -> str:
    return (root / "_aggregate.md").read_text()


def _heading_level(line: str) -> int:
    stripped = line.lstrip()
    return len(stripped) - len(stripped.lstrip("#")) if stripped.startswith("#") else 0


def _section(md: str, *keywords: str) -> str:
    """
    Return the block under the first heading whose text contains any keyword
        (case-insensitive), up to the next heading of the same-or-higher level — so
        numeric assertions can be scoped to the metric/section they verify (review)
        rather than matching anywhere in the document.
    """
    lines = md.splitlines()
    start = None
    start_level = 0
    for i, line in enumerate(lines):
        level = _heading_level(line)
        if level and any(k.lower() in line.lower() for k in keywords):
            start = i
            start_level = level
            break
    if start is None:
        return ""
    out = [lines[start]]
    for line in lines[start + 1 :]:
        level = _heading_level(line)
        if level and level <= start_level:
            break
        out.append(line)
    return "\n".join(out)


def _rows(section: str, needle: str, *, exclude: str | None = None) -> list[str]:
    return [
        ln
        for ln in section.splitlines()
        if needle in ln and (exclude is None or exclude not in ln)
    ]


def test_prompt_version_buckets(tmp_path: Path) -> None:
    """
    Harness back-port core: iterations bucket by (gate, prompt_version) so a
        wording edit is measurable; untagged legacy runs bucket as (untagged).
    """
    runs = [
        {**_run(gate="spec-review", iteration=2), "prompt_version": "aaaa111122223333"},
        {**_run(gate="spec-review", iteration=4), "prompt_version": "aaaa111122223333"},
        {**_run(gate="spec-review", iteration=1), "prompt_version": "bbbb111122223333"},
        _run(gate="self-review", iteration=3),  # legacy: no prompt_version key
    ]
    traj = tmp_path / "docs" / "gate-trajectories"
    _write_record(traj, _record("versioned", "workflow", runs))

    result = _run_report(traj)
    assert result.returncode == 0, result.stderr

    section = _section(_aggregate_text(traj), "prompt version")
    old_bucket = _rows(section, "aaaa111122223333")
    new_bucket = _rows(section, "bbbb111122223333")
    untagged = _rows(section, "(untagged)")
    assert len(old_bucket) == 1 and "| 2 | 3.0 | 4 |" in old_bucket[0]
    assert len(new_bucket) == 1 and "| 1 | 1.0 | 1 |" in new_bucket[0]
    assert len(untagged) == 1 and "self-review" in untagged[0]
