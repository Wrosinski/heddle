"""
Trajectory-archival tests (Milestone 2).

Covers:
  AC-5  test_ac_5_compact_record      — committed compact per-feature record
  AC-6  test_ac_6_raw_relocate_prune  — raw .md relocation + retention pruning
  AC-7  test_ac_7_idempotent_scrub    — byte-identical re-run + path scrub

These tests drive `scripts/archive-gate-trajectory.py` through its CLI
(`<feature> --repo-root <dir> [--now <iso>] [--dry-run]`, exit 0/1/2) against
fixture repo roots, so no `src/gate/` import is needed and the script is
exercised end-to-end (AC action lines say "Run archive-gate-trajectory.py").

Red-phase note: `scripts/archive-gate-trajectory.py` does not exist until
Milestone 2 lands, so every `_run_archive` returns a non-zero exit and the
assertions below FAIL until the script + record projection land.

Record-shape reference: docs/workflow/workflow.md § Gate trajectories.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "archive-gate-trajectory.py"

SEVERITIES = ("critical", "important", "minor")
CLASSIFICATIONS = ("implement", "report", "ignore", "unknown")
SEV_CLASS_KEYS = tuple(f"{s}/{c}" for s in SEVERITIES for c in CLASSIFICATIONS)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _findings(*, crit_impl=0, imp_rep=0, min_ign=0, min_unknown=0, contradictions=0):
    """A populated FindingsInfo serialization (fixed-universe buckets)."""
    bsc = {k: 0 for k in SEV_CLASS_KEYS}
    bsc["critical/implement"] = crit_impl
    bsc["important/report"] = imp_rep
    bsc["minor/ignore"] = min_ign
    bsc["minor/unknown"] = min_unknown
    total = crit_impl + imp_rep + min_ign + min_unknown
    return {
        "by_severity": {
            "critical": crit_impl,
            "important": imp_rep,
            "minor": min_ign + min_unknown,
        },
        "by_classification": {
            "implement": crit_impl,
            "report": imp_rep,
            "ignore": min_ign,
            "unknown": min_unknown,
        },
        "by_severity_classification": bsc,
        "total": total,
        "contradictions": contradictions,
    }


def _summary(
    *,
    feature: str,
    gate: str,
    cli: str = "claude",
    milestone: str | None = None,
    iteration: int = 1,
    max_iterations: int = 5,
    satisfies: list[str] | None = None,
    status: str = "success",
    gate_exit: int = 0,
    cli_exit: int = 0,
    diff_bytes: int = 0,
    changed_files: list[str] | None = None,
    findings: dict | None = None,
    findings_note: str | None = None,
    omit_findings: bool = False,
    area: str = "workflow",
    repo_root: Path | None = None,
) -> dict:
    """A realistic *.gate-summary.json body. Extra fields (preflight, io, …) are
    present so the test can assert they are NOT carried into the curated
    committed record (I3 — curated projection, not a verbatim copy)."""
    body: dict = {
        "feature": feature,
        "gate": gate,
        "cli": cli,
        "plan": f"plans/{feature}.plan.md",
        "spec": (
            str(repo_root / "docs" / "features" / area / f"{feature}.md")
            if repo_root is not None
            else f"docs/features/{area}/{feature}.md"
        ),
        "milestone": {
            "number": milestone,
            "title": "",
            "satisfies_ac_ids": satisfies or [],
        },
        "status": status,
        "reason": status,
        "gate_exit": gate_exit,
        "cli_exit": cli_exit,
        "runtime": {
            "model": "claude-opus-4-8",
            "iteration": iteration,
            "max_iterations": max_iterations,
        },
        "diff": {
            "source": "branch",
            "source_detail": "",
            "bytes": diff_bytes,
            "changed_files": changed_files or [],
        },
        "preflight": {"status": "ok", "fatal_reason": None, "anomalies": ["noise"]},
        "io": {"output_bytes": 4096, "output_lines": 120},
    }
    if not omit_findings:
        body["findings"] = findings  # may be a dict or None (degraded)
        if findings_note is not None:
            body["findings_note"] = findings_note
    return body


def _write_json(path: Path, data: dict, *, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _write_plan(root: Path, feature: str, *, tier: str = "3") -> None:
    plan = root / "plans" / f"{feature}.plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "---\n"
        "type: implementation-plan\n"
        f"feature_name: {feature}\n"
        "status: in-progress\n"
        f"tier: {tier}\n"
        "---\n\n# plan\n"
    )


def _run_archive(root: Path, feature: str, *extra: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), feature, "--repo-root", str(root), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def _record(root: Path, feature: str) -> dict:
    return json.loads(
        (root / "docs" / "gate-trajectories" / f"{feature}.json").read_text()
    )


def _empty_record(feature: str, archived_at: str) -> dict:
    return {
        "feature": feature,
        "archived_at": archived_at,
        "runs": [],
        "events": [],
        "budget": {
            "runs_count": 0,
            "total_iterations": 0,
            "near_cap_runs": [],
            "near_cap": False,
        },
    }


def _seed_raw(traj: Path, feature: str, archived_at: str) -> None:
    """Write a committed record + its .raw/ archive so pruning has an input."""
    _write_json(traj / f"{feature}.json", _empty_record(feature, archived_at))
    (traj / ".raw" / feature).mkdir(parents=True, exist_ok=True)
    (traj / ".raw" / feature / "x.md").write_text("raw")


def _runs_by(record: dict, **match) -> list[dict]:
    out = []
    for run in record.get("runs", []):
        if all(run.get(k) == v for k, v in match.items()):
            out.append(run)
    return out


def test_archive_rejects_non_slug_feature(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    result = _run_archive(root, "../demo-feature")
    assert result.returncode == 2
    assert "bare slug" in result.stderr
    assert not (root / "docs" / "gate-trajectories" / "demo-feature.json").exists()


def test_archive_no_artifacts_noops_without_empty_record(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    feature = "missing-feature"
    result = _run_archive(root, feature)
    assert result.returncode == 0
    assert "no gate trajectory artifacts found" in result.stderr
    assert not (root / "docs" / "gate-trajectories" / f"{feature}.json").exists()


def test_archive_malformed_summary_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    feature = "bad-summary"
    plans = root / "plans"
    plans.mkdir(parents=True)
    (plans / f"{feature}.spec-review.gate-summary.json").write_text("{")

    result = _run_archive(root, feature)
    assert result.returncode == 1
    assert "malformed JSON" in result.stderr
    assert f"{feature}.spec-review.gate-summary.json" in result.stderr


# ---------------------------------------------------------------------------
# AC-5: compact committed record
# ---------------------------------------------------------------------------


def _build_ac5_fixture(root: Path, feature: str, *, with_events: bool) -> None:
    plans = root / "plans"
    _write_plan(root, feature, tier="3")
    (root / "docs" / "gate-trajectories").mkdir(parents=True, exist_ok=True)

    # Repeated (gate, cli) across two milestones — must be two distinct rows.
    _write_json(
        plans / f"{feature}.self-review-m1.gate-summary.json",
        _summary(
            feature=feature,
            gate="self-review",
            milestone="1",
            iteration=2,
            max_iterations=5,
            satisfies=["AC-1"],
            findings=_findings(crit_impl=1, imp_rep=2, min_ign=1),
        ),
    )
    _write_json(
        plans / f"{feature}.self-review-m2.gate-summary.json",
        _summary(
            feature=feature,
            gate="self-review",
            milestone="2",
            iteration=5,
            max_iterations=5,
            satisfies=["AC-2"],
            findings=_findings(crit_impl=0, imp_rep=1, min_ign=0),
        ),
    )
    # Non-milestone gate + its codex variant (two distinct runs by cli).
    _write_json(
        plans / f"{feature}.spec-review.gate-summary.json",
        _summary(
            feature=feature,
            gate="spec-review",
            iteration=3,
            findings=_findings(crit_impl=1, imp_rep=1, min_ign=1),
        ),
    )
    _write_json(
        plans / f"{feature}.spec-review.codex.gate-summary.json",
        _summary(
            feature=feature,
            gate="spec-review",
            cli="codex",
            iteration=3,
            findings=_findings(imp_rep=2),
            repo_root=root,
        ),
    )
    # Synthesis gate with contradictions (is_synthesis derived by suffix).
    _write_json(
        plans / f"{feature}.spec-review-synthesis.gate-summary.json",
        _summary(
            feature=feature,
            gate="spec-review-synthesis",
            iteration=4,
            findings=_findings(crit_impl=1, contradictions=2),
        ),
    )
    # Degraded summary: findings null + note (extraction failure).
    _write_json(
        plans / f"{feature}.plan-review.gate-summary.json",
        _summary(
            feature=feature,
            gate="plan-review",
            iteration=2,
            findings=None,
            findings_note="output present but no finding-ID headers parsed",
        ),
    )
    # Non-finding, single-shot gate (max_iterations <= 1 → excluded from near_cap;
    # findings/findings_note omitted entirely).
    _write_json(
        plans / f"{feature}.milestone-smoke.gate-summary.json",
        _summary(
            feature=feature,
            gate="milestone-smoke",
            milestone="1",
            iteration=1,
            max_iterations=1,
            omit_findings=True,
        ),
    )
    # Excluded rotations/temps — must NOT appear as runs.
    _write_json(
        plans / f"{feature}.spec-review.prev.gate-summary.json",
        _summary(feature=feature, gate="spec-review", iteration=2),
    )
    _write_json(
        plans / f"{feature}.plan-review.tmp.abcd.gate-summary.json",
        _summary(feature=feature, gate="plan-review", iteration=1),
    )
    # Duplicate (gate, cli, milestone, iteration) identity — keep newest mtime.
    _write_json(
        plans / f"{feature}.review-architecture.gate-summary.json",
        _summary(
            feature=feature,
            gate="review-architecture",
            iteration=1,
            findings=_findings(imp_rep=1),
        ),
        mtime=1_000_000.0,
    )
    _write_json(
        plans / f"{feature}.review-architecture.rerun.gate-summary.json",
        _summary(
            feature=feature,
            gate="review-architecture",
            iteration=1,
            findings=_findings(imp_rep=1),
        ),
        mtime=2_000_000.0,  # newer
    )
    if with_events:
        ledger = plans / f"{feature}.events.jsonl"
        ledger.write_text(
            '{"ts": "2026-06-01T00:00:00Z", "kind": "milestone-retry", '
            '"milestone": "2", "note": "verification retry after fix"}\n'
            '{"ts": "2026-06-01T01:00:00Z", "kind": "debug-loop", '
            '"milestone": null, "note": "traced flaky fixture"}\n'
            '{"ts": "not-a-date", "kind": "debug-loop", '
            '"milestone": null, "note": "bad timestamp"}\n'
            "this is not valid json and must be skipped non-fatally\n"
        )


def test_ac_5_compact_record(tmp_path: Path) -> None:
    """AC-5: archival writes a committed compact per-run ledger with area/tier
    derived, per-milestone rows preserved, codex its own run, rotations/temps
    excluded, duplicate identity deduped to newest mtime, per-run findings
    mirroring the serialization state, a curated (not verbatim) projection, and
    the full *.gate-summary.json files relocated into .raw/."""
    feature = "demo-feature"
    root = tmp_path / "repo"
    _build_ac5_fixture(root, feature, with_events=True)

    result = _run_archive(root, feature)
    assert result.returncode == 0, (
        f"FAIL AC-5: archival exit {result.returncode}\n{result.stderr}"
    )

    record = _record(root, feature)
    assert record["feature"] == feature
    assert record["area"] == "workflow"  # derived from relative and absolute spec paths
    assert record["tier"] == "3"  # derived from plan frontmatter

    runs = record["runs"]
    # Eight discoverable summaries collapse to seven runs after dedup; the two
    # excluded rotations/temps never appear.
    assert len(_runs_by(record, gate="self-review", milestone="1")) == 1
    assert len(_runs_by(record, gate="self-review", milestone="2")) == 1
    assert len(_runs_by(record, gate="spec-review", cli="claude")) == 1
    assert len(_runs_by(record, gate="spec-review", cli="codex")) == 1

    # Rotations/temps excluded.
    for run in runs:
        assert run["iteration"] != 2 or run["gate"] != "spec-review", (
            "FAIL AC-5: .prev rotation leaked into runs"
        )
        assert run["source_summary"].endswith(".gate-summary.json")
        assert "/" not in run["source_summary"]
        assert ".prev." not in run["source_summary"]
        assert ".tmp." not in run["source_summary"]

    # Duplicate identity deduped to a single newest-mtime run.
    arch = _runs_by(record, gate="review-architecture")
    assert len(arch) == 1, (
        "FAIL AC-5: duplicate (gate,cli,milestone,iteration) not deduped"
    )
    assert arch[0]["source_summary"].endswith(
        "review-architecture.rerun.gate-summary.json"
    )

    # is_synthesis derived by the -synthesis suffix rule.
    syn = _runs_by(record, gate="spec-review-synthesis")[0]
    assert syn["is_synthesis"] is True
    assert syn["findings"]["contradictions"] == 2
    assert all(not r["is_synthesis"] for r in _runs_by(record, gate="spec-review"))

    # Per-run findings mirror the three serialization states.
    populated = _runs_by(record, gate="self-review", milestone="1")[0]["findings"]
    assert populated["total"] == sum(populated["by_severity_classification"].values())
    assert "contradictions" in populated

    degraded = _runs_by(record, gate="plan-review")[0]
    assert degraded["findings"] is None
    assert degraded.get("findings_note")

    nonfinding = _runs_by(record, gate="milestone-smoke")[0]
    assert "findings" not in nonfinding

    # Curated projection (I3): bulky/uncurated summary fields are NOT embedded.
    for run in runs:
        assert "preflight" not in run
        assert "io" not in run
        # No raw review prose embedded in the committed record (I2).
        for value in run.values():
            assert not (isinstance(value, str) and len(value) > 2000)

    # Budget rollup.
    budget = record["budget"]
    assert budget["runs_count"] == len(runs)
    assert budget["total_iterations"] == sum(r["iteration"] for r in runs)
    # near_cap reflects the 5/5 self-review-reader run; the single-shot gate
    # (max_iterations == 1) is excluded even though 1/1 >= 0.8.
    assert budget["near_cap"] is True
    assert "milestone-smoke" not in json.dumps(budget["near_cap_runs"])

    # Events: well-formed lines archived; malformed JSON and malformed timestamp
    # lines are skipped non-fatally.
    assert len(record["events"]) == 2

    # Full *.gate-summary.json files relocated into the gitignored .raw archive.
    raw_dir = root / "docs" / "gate-trajectories" / ".raw" / feature
    relocated = list(raw_dir.glob("*.gate-summary.json"))
    assert relocated, "FAIL AC-5: original summaries not relocated into .raw/"
    remaining_eligible = [
        path
        for path in (root / "plans").glob(f"{feature}.*.gate-summary.json")
        if ".prev." not in path.name and ".tmp." not in path.name
    ]
    assert not remaining_eligible, (
        "FAIL AC-5: eligible summaries remained in plans/ after archival"
    )
    assert (raw_dir / f"{feature}.review-architecture.gate-summary.json").exists(), (
        "FAIL AC-5: deduped-away duplicate summary was not relocated"
    )

    # No-events variant: events == [], budget from summaries alone, exit 0.
    feature2 = "demo-no-events"
    root2 = tmp_path / "repo2"
    _build_ac5_fixture(root2, feature2, with_events=False)
    result2 = _run_archive(root2, feature2)
    assert result2.returncode == 0
    record2 = _record(root2, feature2)
    assert record2["events"] == []
    assert record2["budget"]["runs_count"] == len(record2["runs"])


# ---------------------------------------------------------------------------
# AC-6: raw relocation + retention pruning
# ---------------------------------------------------------------------------


def test_ac_6_raw_relocate_prune(tmp_path: Path) -> None:
    """AC-6: bulky review .md outputs are moved into the gitignored .raw/ archive,
    and .raw/ is pruned to the retention window — kept iff within the 10 most
    recent by archived_at OR within 90 days of the --now prune-run time (union).
    A deterministic --now exercises the 90-day boundary."""
    root = tmp_path / "repo"
    target = "target-feature"
    traj = root / "docs" / "gate-trajectories"
    traj.mkdir(parents=True, exist_ok=True)
    _write_plan(root, target, tier="3")

    # Target feature: one summary + two bulky review .md outputs to relocate.
    plans = root / "plans"
    _write_json(
        plans / f"{target}.spec-review.gate-summary.json",
        _summary(
            feature=target,
            gate="spec-review",
            iteration=2,
            findings=_findings(crit_impl=1),
        ),
    )
    big = "x" * 50_000
    (plans / f"{target}.spec-review.md").write_text(f"# Review\n\n{big}\n")
    (plans / f"{target}.spec-review.codex.md").write_text(f"# Codex Review\n\n{big}\n")
    notes = plans / f"{target}.notes.md"
    notes.write_text("not a review artifact\n")

    now = "2026-06-15"  # 90-day window boundary == 2026-03-17
    # 10 recent committed records (within 90 days) -> kept by rank.
    for i in range(10):
        _seed_raw(traj, f"recent-{i:02d}", f"2026-06-{14 - i:02d}")
    # rank-11, within 90 days of --now -> kept by the 90-day union arm.
    _seed_raw(traj, "within-window", "2026-03-20")
    # exactly 90 days before --now (2026-06-15 - 90d = 2026-03-17), rank-excluded:
    # "within 90 days" is inclusive, so this boundary feature is KEPT (review —
    # pins the inclusive/exclusive semantics; a `< 90` off-by-one would prune it).
    _seed_raw(traj, "boundary-exact", "2026-03-17")
    # beyond 90 days AND beyond rank-10 -> pruned (beyond both arms).
    _seed_raw(traj, "beyond-both", "2026-03-10")

    result = _run_archive(root, target, "--now", now)
    assert result.returncode == 0, (
        f"FAIL AC-6: exit {result.returncode}\n{result.stderr}"
    )

    raw = traj / ".raw"
    # Target's review .md files relocated, not left in plans/.
    target_raw = list((raw / target).glob("*.md"))
    assert target_raw, "FAIL AC-6: target review .md not relocated into .raw/"
    assert not (plans / f"{target}.spec-review.md").exists()
    assert notes.exists(), "FAIL AC-6: unrelated feature-prefixed markdown relocated"

    # Union retention: within-window survives (90-day arm), beyond-both pruned.
    assert (raw / "within-window").exists(), (
        "FAIL AC-6: rank-11 within-90-days feature pruned (90-day union arm broken)"
    )
    assert (raw / "boundary-exact").exists(), (
        "FAIL AC-6: feature at exactly 90 days before --now was pruned — "
        "'within 90 days' must be inclusive (off-by-one in the boundary compare)"
    )
    assert not (raw / "beyond-both").exists(), (
        "FAIL AC-6: feature beyond rank-10 AND beyond 90 days not pruned"
    )

    # Committed artifacts remain compact (no bulky prose committed; I2).
    committed = (traj / f"{target}.json").read_text()
    assert "x" * 2000 not in committed

    # --- review: isolate the RANK arm independently of the 90-day arm ---
    # A corpus of features ALL beyond the 90-day window but, being < 10 total,
    # all within the "10 most recent by archived_at" — so they survive only via
    # the rank arm. A regression that kept the union's 90-day arm alone (keep
    # iff within 90 days) would prune them all.
    root_b = tmp_path / "repo_rank"
    traj_b = root_b / "docs" / "gate-trajectories"
    traj_b.mkdir(parents=True, exist_ok=True)
    target_b = "rank-target"
    _write_plan(root_b, target_b, tier="3")
    _write_json(
        root_b / "plans" / f"{target_b}.spec-review.gate-summary.json",
        _summary(
            feature=target_b,
            gate="spec-review",
            iteration=1,
            findings=_findings(crit_impl=1),
        ),
    )
    for name, when in (
        ("old-a", "2025-01-01"),
        ("old-b", "2025-02-01"),
        ("old-c", "2025-03-01"),
    ):
        _seed_raw(traj_b, name, when)
    result_b = _run_archive(root_b, target_b, "--now", now)
    assert result_b.returncode == 0, f"FAIL AC-6 rank-arm: exit {result_b.returncode}"
    for name in ("old-a", "old-b", "old-c"):
        assert (traj_b / ".raw" / name).exists(), (
            f"FAIL AC-6: rank arm dropped — {name} (beyond 90 days but within the "
            "10 most recent by archived_at) was pruned"
        )


# ---------------------------------------------------------------------------
# AC-7: idempotent (byte-identical) + path scrub
# ---------------------------------------------------------------------------


def test_ac_7_idempotent_scrub(tmp_path: Path) -> None:
    """AC-7: a second archival run is byte-identical (archived_at preserved, not
    refreshed by a distinct --now), repo-root-prefixed absolute paths in event
    notes are relativized, and a post-cleanup re-run (sources already deleted) is
    a safe byte-identical no-op exiting 0."""
    root = tmp_path / "repo"
    feature = "idem-feature"
    plans = root / "plans"
    traj = root / "docs" / "gate-trajectories"
    traj.mkdir(parents=True, exist_ok=True)
    _write_plan(root, feature, tier="3")

    _write_json(
        plans / f"{feature}.spec-review.gate-summary.json",
        _summary(
            feature=feature,
            gate="spec-review",
            iteration=2,
            findings=_findings(crit_impl=1, imp_rep=1),
        ),
    )
    # An event note carrying a repo-root-prefixed absolute path (scrub target).
    abs_path = f"{root}/plans/{feature}.spec-review.md"
    ledger = plans / f"{feature}.events.jsonl"
    ledger.write_text(
        '{"ts": "2026-06-01T00:00:00Z", "kind": "re-plan", "milestone": null, '
        f'"note": "re-planned after reading {abs_path}"}}\n'
    )

    # First archival.
    r1 = _run_archive(root, feature, "--now", "2026-06-10")
    assert r1.returncode == 0, (
        f"FAIL AC-7: first archival exit {r1.returncode}\n{r1.stderr}"
    )
    record_path = traj / f"{feature}.json"
    first_bytes = record_path.read_bytes()

    # Path scrub: the absolute path is stored repo-relative in the committed record.
    record = json.loads(first_bytes)
    note = record["events"][0]["note"]
    assert str(root) not in note, "FAIL AC-7: repo-root absolute path not scrubbed"
    assert f"plans/{feature}.spec-review.md" in note

    # Second archival with a DISTINCT --now: byte-identical (archived_at preserved).
    r2 = _run_archive(root, feature, "--now", "2026-07-20")
    assert r2.returncode == 0
    assert record_path.read_bytes() == first_bytes, (
        "FAIL AC-7: re-archival not byte-identical (archived_at refreshed?)"
    )

    # State (b): after cleanup — source summaries deleted; re-run is a safe no-op.
    for stale in plans.glob(f"{feature}.*.gate-summary.json"):
        stale.unlink()
    ledger.unlink()
    r3 = _run_archive(root, feature, "--now", "2026-08-01")
    assert r3.returncode == 0, f"FAIL AC-7(b): post-cleanup re-run exit {r3.returncode}"
    assert record_path.read_bytes() == first_bytes, (
        "FAIL AC-7(b): post-cleanup re-run mutated the committed record"
    )


def test_ac_7_recreates_record_from_raw_after_crash_window(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    feature = "crash-window"
    plans = root / "plans"
    traj = root / "docs" / "gate-trajectories"
    traj.mkdir(parents=True, exist_ok=True)
    _write_plan(root, feature, tier="3")
    _write_json(
        plans / f"{feature}.spec-review.gate-summary.json",
        _summary(
            feature=feature,
            gate="spec-review",
            iteration=2,
            findings=_findings(crit_impl=1),
        ),
    )

    first = _run_archive(root, feature, "--now", "2026-06-10")
    assert first.returncode == 0, first.stderr
    record_path = traj / f"{feature}.json"
    record_path.unlink()

    recovered = _run_archive(root, feature, "--now", "2026-06-11")
    assert recovered.returncode == 0, recovered.stderr
    record = _record(root, feature)
    runs = _runs_by(record, gate="spec-review")
    assert len(runs) == 1
    assert runs[0]["source_summary"] == f"{feature}.spec-review.gate-summary.json"


# ---------------------------------------------------------------------------
# Harness back-port core: prompt_version carried into the curated projection
# ---------------------------------------------------------------------------


def test_prompt_version_carried_into_projected_runs(tmp_path: Path) -> None:
    """`runtime.prompt_version` is the trajectory bucketing key (R6): archival
    carries it into the committed run; summaries written before the tag
    existed project it as None rather than being rejected."""
    feature = "versioned-feature"
    root = tmp_path / "repo"
    _write_plan(root, feature)
    plans = root / "plans"

    tagged = _summary(feature=feature, gate="spec-review", iteration=1)
    tagged["runtime"]["prompt_version"] = "58095f6e79d02f39"
    _write_json(plans / f"{feature}.spec-review.gate-summary.json", tagged)
    untagged = _summary(feature=feature, gate="self-review", milestone="1")
    _write_json(plans / f"{feature}.self-review-m1.gate-summary.json", untagged)

    result = _run_archive(root, feature)
    assert result.returncode == 0, result.stderr

    record = _record(root, feature)
    assert _runs_by(record, gate="spec-review")[0]["prompt_version"] == (
        "58095f6e79d02f39"
    )
    assert _runs_by(record, gate="self-review")[0]["prompt_version"] is None
