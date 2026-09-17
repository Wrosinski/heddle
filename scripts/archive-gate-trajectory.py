from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

FEATURE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def main() -> int:
    args = _parse_args()
    repo_root = args.repo_root.resolve()
    archive_feature(
        feature=args.feature,
        repo_root=repo_root,
        now=_parse_date(args.now),
        dry_run=args.dry_run,
    )
    return 0


def archive_feature(
    *,
    feature: str,
    repo_root: Path,
    now: date,
    dry_run: bool,
) -> None:
    plans_dir = repo_root / "plans"
    trajectories_dir = repo_root / "docs" / "gate-trajectories"
    raw_dir = trajectories_dir / ".raw" / feature
    record_path = trajectories_dir / f"{feature}.json"
    if _is_native_workspace(plans_dir, feature):
        # The engine owns the native workspace path; record
        # projection + aggregate regen live there; this wrapper adds only
        # the legacy `.raw` housekeeping (relocation + retention pruning).
        from heddle.runtime.trajectory import archive_trajectory

        result = archive_trajectory(repo_root, feature, now=now, dry_run=dry_run)
        if result is None:
            if not record_path.exists():
                logger.warning("no gate trajectory artifacts found for {}", feature)
            return
        if dry_run:
            return
        _relocate_raw_artifacts(plans_dir, raw_dir, feature)
        _prune_raw_archives(trajectories_dir, now)
        return
    # Legacy flat layout (frozen here; the engine imports nothing legacy).
    plans_summaries = _discover_plan_summaries(plans_dir, feature)
    events_path = plans_dir / f"{feature}.events.jsonl"
    tier_source = plans_dir / f"{feature}.plan.md"
    existing_bytes = record_path.read_bytes() if record_path.exists() else None

    if not plans_summaries and existing_bytes is not None:
        return
    summaries = _dedupe_summary_items(
        [*plans_summaries, *_discover_raw_summaries(raw_dir)]
    )
    if not summaries and not events_path.exists():
        logger.warning("no gate trajectory artifacts found for {}", feature)
        return

    existing = _read_json(record_path) if record_path.exists() else {}
    archived_at = existing.get("archived_at") or now.isoformat()
    runs = [_project_run(item.path, item.data, repo_root) for item in summaries]
    events = _read_events(events_path, repo_root)
    record = {
        "feature": feature,
        "area": _derive_area([item.data for item in summaries], repo_root),
        "tier": _read_tier(tier_source),
        "archived_at": archived_at,
        "runs": runs,
        "events": events,
        "budget": _budget(runs),
    }

    if dry_run:
        return

    trajectories_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(record_path, record)
    _relocate_raw_artifacts(plans_dir, raw_dir, feature)
    _prune_raw_archives(trajectories_dir, now)


@dataclass
class SummaryItem:
    path: Path
    data: dict[str, Any]

    @property
    def identity(self) -> tuple[str, str, str | None, int]:
        runtime = self.data.get("runtime", {})
        milestone = self.data.get("milestone", {})
        return (
            str(self.data.get("gate")),
            str(self.data.get("cli")),
            milestone.get("number") if isinstance(milestone, dict) else None,
            int(runtime.get("iteration", 1)),
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("feature")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--now", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not FEATURE_PATTERN.fullmatch(args.feature):
        parser.error(
            "feature must be a bare slug containing only letters, numbers, '.', "
            "'_', or '-'"
        )
    return args


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _discover_plan_summaries(plans_dir: Path, feature: str) -> list[SummaryItem]:
    return _dedupe_summaries(plans_dir.glob(f"{feature}.*.gate-summary.json"))


def _is_native_workspace(plans_dir: Path, feature: str) -> bool:
    """A native workspace is plans/<feature>/ carrying a
    state.yaml; the engine (heddle.runtime.trajectory) owns its archival.
    The legacy layout wrote artifacts flat as
    plans/<feature>.<gate>.gate-summary.json."""
    return (plans_dir / feature / "state.yaml").is_file()


def _discover_raw_summaries(raw_dir: Path) -> list[SummaryItem]:
    return _dedupe_summaries(raw_dir.glob("*.gate-summary.json"))


def _dedupe_summaries(paths: Iterable[Path]) -> list[SummaryItem]:
    return _dedupe_summary_items(
        SummaryItem(path=path, data=_read_json(path))
        for path in paths
        if ".prev." not in path.name and ".tmp." not in path.name
    )


def _dedupe_summary_items(items: Iterable[SummaryItem]) -> list[SummaryItem]:
    by_identity: dict[tuple[str, str, str | None, int], SummaryItem] = {}
    for item in items:
        current = by_identity.get(item.identity)
        if (
            current is None
            or item.path.stat().st_mtime_ns >= current.path.stat().st_mtime_ns
        ):
            by_identity[item.identity] = item
    return sorted(by_identity.values(), key=lambda item: item.path.name)


def _project_run(
    path: Path, summary: dict[str, Any], repo_root: Path
) -> dict[str, Any]:
    runtime = summary.get("runtime", {})
    milestone = summary.get("milestone", {})
    diff = summary.get("diff", {})
    run: dict[str, Any] = {
        "gate": summary.get("gate"),
        "cli": summary.get("cli"),
        "milestone": milestone.get("number") if isinstance(milestone, dict) else None,
        "iteration": int(runtime.get("iteration", 1)),
        "source_summary": path.name,
        "max_iterations": int(runtime.get("max_iterations", 1)),
        "prompt_version": runtime.get("prompt_version"),
        "satisfies_ac_ids": (
            milestone.get("satisfies_ac_ids", []) if isinstance(milestone, dict) else []
        ),
        "is_synthesis": str(summary.get("gate", "")).endswith("-synthesis"),
        "status": summary.get("status"),
        "gate_exit": summary.get("gate_exit"),
        "cli_exit": summary.get("cli_exit"),
        "diff_bytes": int(diff.get("bytes", 0)),
        "changed_files": len(diff.get("changed_files", [])),
    }
    if "findings" in summary:
        findings = summary.get("findings")
        run["findings"] = (
            _project_findings(findings) if isinstance(findings, dict) else None
        )
        if summary.get("findings_note"):
            run["findings_note"] = summary.get("findings_note")
    return run


def _project_findings(findings: dict[str, Any]) -> dict[str, Any]:
    return {
        "by_severity_classification": findings.get("by_severity_classification", {}),
        "total": findings.get("total", 0),
        "contradictions": findings.get("contradictions", 0),
    }


def _read_events(path: Path, repo_root: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping malformed event line {} in {}", line_number, path)
            continue
        if not _valid_event(event):
            logger.warning("skipping invalid event line {} in {}", line_number, path)
            continue
        event["note"] = _scrub(event["note"], repo_root)
        events.append(event)
    return events


def _valid_event(event: Any) -> bool:
    return (
        isinstance(event, dict)
        and _valid_timestamp(event.get("ts"))
        and event.get("kind") in {"milestone-retry", "debug-loop", "re-plan"}
        and (event.get("milestone") is None or isinstance(event.get("milestone"), str))
        and isinstance(event.get("note"), str)
        and event["note"].strip() != ""
    )


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _derive_area(summaries: list[dict[str, Any]], repo_root: Path) -> str | None:
    for summary in summaries:
        spec = summary.get("spec")
        if not isinstance(spec, str):
            continue
        parts = _relative_path(Path(spec), repo_root).parts
        if len(parts) >= 3 and parts[0] == "docs" and parts[1] == "features":
            return parts[2]
    return None


def _read_tier(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        if line.startswith("tier:"):
            return line.split(":", 1)[1].strip().strip('"')
    return None


def _budget(runs: list[dict[str, Any]]) -> dict[str, Any]:
    near_cap_runs = [
        run["source_summary"]
        for run in runs
        if run["max_iterations"] >= 2
        and run["iteration"] / run["max_iterations"] >= 0.8
    ]
    return {
        "runs_count": len(runs),
        "total_iterations": sum(run["iteration"] for run in runs),
        "near_cap_runs": near_cap_runs,
        "near_cap": bool(near_cap_runs),
    }


def _relocate_raw_artifacts(
    plans_dir: Path,
    raw_dir: Path,
    feature: str,
) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        path
        for path in plans_dir.glob(f"{feature}.*.gate-summary.json")
        if ".prev." not in path.name and ".tmp." not in path.name
    }
    for pattern in _review_output_patterns(feature):
        paths.update(plans_dir.glob(pattern))
    for path in sorted(paths):
        if path.exists():
            shutil.move(str(path), raw_dir / path.name)


def _review_output_patterns(feature: str) -> tuple[str, ...]:
    return (
        f"{feature}.spec-review*.md",
        f"{feature}.plan-review*.md",
        f"{feature}.review-*.md",
        f"{feature}.impl-review*.md",
        f"{feature}.code-quality-m*.md",
        f"{feature}.self-review-m*.md",
        f"{feature}.robustness-analysis*.md",
        f"{feature}.review-test-scaffolding*.md",
    )


def _prune_raw_archives(trajectories_dir: Path, now: date) -> None:
    raw_root = trajectories_dir / ".raw"
    if not raw_root.exists():
        return
    records = _archived_dates(trajectories_dir)
    ranked = {
        feature
        for feature, _ in sorted(
            records.items(), key=lambda item: item[1], reverse=True
        )[:10]
    }
    cutoff = now - timedelta(days=90)
    for raw_feature in raw_root.iterdir():
        if not raw_feature.is_dir():
            continue
        archived_at = records.get(raw_feature.name)
        keep = raw_feature.name in ranked or (
            archived_at is not None and archived_at >= cutoff
        )
        if not keep:
            shutil.rmtree(raw_feature)


def _archived_dates(trajectories_dir: Path) -> dict[str, date]:
    records = {}
    for path in trajectories_dir.glob("*.json"):
        if path.name.startswith("_"):
            continue
        data = _read_json(path)
        archived_at = data.get("archived_at")
        if isinstance(archived_at, str):
            records[path.stem] = _parse_date(archived_at)
    return records


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {path}: {exc}") from exc


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)


def _scrub(value: str, repo_root: Path) -> str:
    root = str(repo_root)
    if value == root:
        return "."
    return value.replace(root + "/", "")


def _relative_path(path: Path, repo_root: Path) -> Path:
    try:
        return path.relative_to(repo_root)
    except ValueError:
        return path


if __name__ == "__main__":
    raise SystemExit(main())
