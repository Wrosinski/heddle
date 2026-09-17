"""Archive accepted gate events and their validated canonical review facts.

Generated summaries and views are not inputs. Historical exported records remain
read-only inputs to aggregate reports. The close wrapper reports archival errors
without undoing an already committed terminal transition.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

from heddle.gate import entry
from heddle.gate.registry import VERDICT_PRODUCING_GATES
from heddle.kernel.feature_policy import effective_policy
from heddle.kernel.project_config import KernelError
from heddle.kernel.state import GateRun, read_state_file

TRAJECTORIES_DIR = Path("docs") / "gate-trajectories"
AGGREGATE_NAME = "_aggregate.md"
EVENT_KINDS = {"milestone-retry", "debug-loop", "re-plan"}


def archive_trajectory(
    root: Path,
    feature: str,
    *,
    now: date,
    dry_run: bool = False,
    plans_dir: Path | None = None,
) -> Path | None:
    """Project current accepted authority before publishing the archive.

    A pruned workspace is a no-op. A present invalid workspace or canonical
    result raises; no partial export replaces a previous successful record.
    The close caller supplies the configured plans directory.
    """
    workspace = (plans_dir or root / "plans") / feature
    state_path = workspace / "state.yaml"
    if not state_path.is_file():
        return None
    state = read_state_file(state_path)
    if state.feature != feature:
        raise KernelError(
            code="workspace-invalid",
            message=f"trajectory feature {feature} differs from state {state.feature}",
            hint="archive the feature named by the current workspace",
        )
    events_path = workspace / "events.jsonl"
    trajectories_dir = root / TRAJECTORIES_DIR
    record_path = trajectories_dir / f"{feature}.json"
    existing = _read_json(record_path) if record_path.exists() else {}
    completion = (
        {"accepted_at": state.completion.accepted_at, "revision": state.revision}
        if state.completion is not None
        else None
    )
    # This marker identifies an export of the immutable accepted ledger. It is
    # projection metadata, never completion authority. In-progress exports are
    # replaced once at acceptance; completed exports retain their original events.
    if existing.get("completion") is not None:
        if existing["completion"] != completion:
            raise ValueError(f"trajectory completion identity differs: {record_path}")
        if not dry_run:
            write_aggregate(trajectories_dir)
        return record_path
    if (
        not state.gates
        and not state.review_assignments.retained
        and not events_path.exists()
    ):
        return None
    policy_revisions: dict[tuple[str | None, int | None], int | None] = {
        (assignment.id, round_.number): round_.policy_revision
        for assignment in state.review_assignments.assignments
        for round_ in assignment.rounds
    }
    from heddle.runtime.review_assignments import ReviewEvidenceAccess, validate_sources

    # Validate canonical results and receipt/interpretation lineage through the same
    # contained evidence owner before replacing any export.
    validated = validate_sources(
        ReviewEvidenceAccess(
            root=root,
            workspace=workspace.relative_to(root).as_posix() + "/",
            feature=feature,
            state=state,
        )
    )
    captures = {}
    if state.review_assignments.retained:
        captures = {
            row.run_id: entry.read_retained_capture(workspace, row)
            for row in state.review_assignments.retained
        }
    runs = []
    for fact in state.gates:
        completed: Counter[str] = Counter()
        for run in fact.runs:
            verdict_gate = fact.gate in VERDICT_PRODUCING_GATES
            lane = run.cli if verdict_gate else "all"
            iteration = completed[lane] + 1
            result = None
            if run.verdict.get("status") != "error":
                result = validated[run.run_id]
            projected = _project_run(
                fact.gate, fact.scope, run, result, iteration=iteration
            )
            projected.update(
                policy_revision=policy_revisions.get(
                    (run.assignment_id, run.round_number)
                ),
                assignment_id=run.assignment_id,
                round_number=run.round_number,
                reviewer_slot=run.reviewer_slot,
            )
            runs.append(projected)
            if result is not None:
                completed[lane] += 1
    interpretations = {r.run_id: r for r in state.review_assignments.interpretations}
    accepted_ids = {r["run_id"] for r in runs}
    for row in state.review_assignments.retained:
        capture = captures[row.run_id]
        provenance = {
            "original_capture": row.artifact,
            "capture_sha256": row.artifact_sha256,
            "structure_warnings": list(row.warnings),
            "interpretation": asdict(interpretations[row.run_id])
            if row.run_id in interpretations
            else None,
        }
        if row.run_id in accepted_ids:
            next(r for r in runs if r["run_id"] == row.run_id).update(provenance)
            continue
        invocation = capture.invocation
        # Projection only: an unaccepted response is not a fabricated GateRun.
        runs.append(
            {
                **asdict(invocation),
                **provenance,
                "gate": invocation.gate,
                "cli": invocation.execution.cli,
                "milestone": invocation.scope
                if invocation.scope != "feature"
                else None,
                "run_id": row.run_id,
                "artifact": row.artifact,
                "artifact_sha256": row.artifact_sha256,
                "execution_config": asdict(invocation.execution),
                "iteration": row.round_number,
                "policy_revision": policy_revisions[
                    (row.assignment_id, row.round_number)
                ],
                "status": "retained",
                "accepted": False,
                "findings": None,
            }
        )
    spec_parts = Path(state.spec).parts
    record: dict[str, Any] = {
        "feature": feature,
        "area": spec_parts[2]
        if len(spec_parts) >= 3 and spec_parts[:2] == ("docs", "features")
        else None,
        "archived_at": existing.get("archived_at") or now.isoformat(),
        "runs": runs,
        "events": _read_events(events_path, root),
    }
    ledger = state.review_assignments
    assert state.feature_policy is not None
    record.update(
        schema="heddle.trajectory/v2",
        effective_policy=asdict(effective_policy(state.feature_policy)),
        policy_history=[asdict(policy) for policy in state.policy_history],
        assignments=[
            {
                "assignment_id": assignment.id,
                "role": assignment.role,
                "scope": assignment.scope,
                "policy_revision": assignment.policy_revision,
                "stop_decision_id": assignment.stop_decision_id,
            }
            for assignment in ledger.assignments
        ],
        rounds=[
            {
                **asdict(round_),
                "assignment_id": assignment.id,
                "role": assignment.role,
                "scope": assignment.scope,
            }
            for assignment in ledger.assignments
            for round_ in assignment.rounds
        ],
        sources=[asdict(source) for source in ledger.sources],
        dispositions=[asdict(row) for row in ledger.dispositions],
    )
    record["budget"] = {
        "rounds_count": len(record["rounds"]),
        "calls_count": len(runs),
        "note": (
            "Rounds are recorded assignments; calls are distinct recorded "
            "run identities, including errors and retained responses. "
            "Interpretation never adds a call. Unrecorded attempts are unavailable."
        ),
    }
    if completion is not None:
        record["completion"] = completion
    if dry_run:
        return record_path
    trajectories_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(record_path, record)
    write_aggregate(trajectories_dir)
    return record_path


def _project_run(
    gate: str,
    scope: str,
    run: GateRun,
    result: entry.ReviewResult | None,
    *,
    iteration: int,
) -> dict[str, Any]:
    """Project a validated accepted result, or an honest recorded error."""
    projection = (
        entry.machine_projection_from_result(result) if result is not None else None
    )
    binding = result.invocation if result is not None else None
    return {
        "gate": gate,
        "cli": run.cli,
        "milestone": scope if scope != "feature" else None,
        "iteration": iteration,
        "run_id": run.run_id,
        "artifact": run.artifact,
        "artifact_sha256": run.artifact_sha256,
        "execution_config": dict(run.execution_config)
        if run.execution_config is not None
        else None,
        "prompt_version": run.prompt_version,
        "effective_prompt_sha256": run.effective_prompt_sha256,
        "review_basis_hash": run.review_basis_hash,
        "input_hash": run.input_hash,
        "review_policy_id": run.review_policy_id,
        "output_contract_version": binding.output_contract_version if binding else None,
        "output_contract_sha256": binding.output_contract_sha256 if binding else None,
        "status": "success" if result is not None else "error",
        "verdict": dict(run.verdict),
        "failure_reason": run.failure_reason,
        "findings": {
            "by_severity_classification": projection["severity_classification"],
            "total": projection["findings"]["total"],
            "contradictions": projection["findings"]["contradictions"],
        }
        if projection is not None
        else None,
    }


def _read_events(path: Path, repo_root: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"unreadable events file {path}: {exc}") from exc
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Malformed or invalid lines are skipped, never fatal — the
            # record archives what is well-formed (reference-script parity;
            # the engine has no logging channel by design).
            continue
        if not _valid_event(event):
            continue
        event["note"] = _scrub(event["note"], repo_root)
        events.append(event)
    return events


def _valid_event(event: Any) -> bool:
    return (
        isinstance(event, dict)
        and _valid_timestamp(event.get("ts"))
        and event.get("kind") in EVENT_KINDS
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # OSError folds onto the ValueError channel deliberately — every
        # caller (best-effort close, wrapper CLI) treats both as "this
        # record/input is unusable", and the decode-guard rail requires
        # the local catch.
        raise ValueError(f"unreadable JSON in {path}: {exc}") from exc
    try:
        return cast(dict[str, Any], json.loads(text))
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


# ---------------------------------------------------------------------------
# Aggregate report (port of scripts/gate-trajectory-report.py rendering)
# ---------------------------------------------------------------------------


def write_aggregate(trajectories_dir: Path, *, since: datetime | None = None) -> Path:
    """Repair ``_aggregate.md`` only when its projected content differs."""
    records = _load_records(trajectories_dir, since=since)
    trajectories_dir.mkdir(parents=True, exist_ok=True)
    aggregate_path = trajectories_dir / AGGREGATE_NAME
    rendered = _render_report(records)
    if not aggregate_path.exists() or aggregate_path.read_bytes() != rendered.encode():
        aggregate_path.write_text(rendered, encoding="utf-8")
    return aggregate_path


def _load_records(root: Path, *, since: datetime | None) -> list[dict[str, Any]]:
    records = []
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("_"):
            continue
        record = _read_json(path)
        if record.get("schema") not in {None, "heddle.trajectory/v2"}:
            raise ValueError(f"unsupported trajectory schema in {path}")
        if since is not None and _archived_at(record) < since:
            continue
        records.append(record)
    return records


def _render_report(records: list[dict[str, Any]]) -> str:
    lines = [
        "# Gate Trajectory Aggregate",
        "",
        f"Features: {len(records)}",
        f"Feature names: "
        f"{', '.join(record.get('feature', '') for record in records) or 'none'}",
        "",
    ]
    current = [r for r in records if r.get("schema") == "heddle.trajectory/v2"]
    uninterpreted = sum(
        row.get("status") == "retained" for record in current for row in record["runs"]
    )
    if uninterpreted:
        lines.extend(
            [
                f"Uninterpreted retained responses: {uninterpreted}; excluded from "
                "finding totals, not zero-findings reviews.",
                "",
            ]
        )
    historical = [r for r in records if r.get("schema") != "heddle.trajectory/v2"]
    if current:
        lines.extend(_render_policy_selections(current))
        lines.extend(_render_rounds(current))
        lines.extend(_render_call_configurations(current))
        lines.extend(_render_dispositions(current))
    lines.extend(_render_iterations(historical))
    lines.extend(_render_prompt_versions(historical))
    lines.extend(_render_finding_mix(records))
    lines.extend(_render_near_cap(historical))
    # Collapse the trailing section spacer so the file ends with exactly one
    # newline — a well-formed text file end-of-file-fixer leaves alone.
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_policy_selections(records: list[dict[str, Any]]) -> list[str]:
    rows = []
    for record in records:
        policy = record["effective_policy"]
        for selection in policy["entries"]:
            mode = selection["mode"]
            label = "off (intentionally not run)" if mode == "off" else mode
            rows.append(
                f"| {record['feature']} | {policy['revision']} | {policy['base']} | "
                f"{selection['role']} | {label} |"
            )
    return _table_lines(
        "## Current policy selections (not verdicts)",
        "| Feature | Revision | Base | Role | Selection |",
        "| --- | ---: | --- | --- | --- |",
        rows,
        "| none | 0 | none | none | none |",
    )


def _render_rounds(records: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        for row in record["rounds"]:
            counts[row["role"]]["rounds"] += 1
            if row["purpose"] == "verification":
                counts[row["role"]]["targeted"] += 1
        for row in record["runs"]:
            counts[row["gate"]]["calls"] += 1
    return _table_lines(
        "## Recorded rounds and calls (v2)",
        "| Role | Rounds | Calls | Targeted verification rounds |",
        "| --- | ---: | ---: | ---: |",
        [
            f"| {role} | {row['rounds']} | {row['calls']} | {row['targeted']} |"
            for role, row in sorted(counts.items())
        ],
        "| none | 0 | 0 | 0 |",
    )


def _render_dispositions(records: list[dict[str, Any]]) -> list[str]:
    counts: Counter[str] = Counter(
        row["status"] for record in records for row in record["dispositions"]
    )
    return _table_lines(
        "## Recorded disposition history (not current closure)",
        "| Status | Facts |",
        "| --- | ---: |",
        [f"| {status} | {count} |" for status, count in sorted(counts.items())],
        "| none | 0 |",
    )


def _render_call_configurations(records: list[dict[str, Any]]) -> list[str]:
    return _table_lines(
        "## Recorded calls by prompt version and execution configuration (v2)",
        "| Role | CLI | Model | Effort | Sandbox | Prompt version | Calls |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
        [
            f"| {' | '.join(bucket)} | {len(calls)} |"
            for bucket, calls in sorted(_prompt_version_buckets(records).items())
        ],
        "| none | none | none | none | none | none | 0 |",
    )


def _render_iterations(records: list[dict[str, Any]]) -> list[str]:
    by_gate: dict[str, list[int]] = defaultdict(list)
    for run in _runs(records):
        by_gate[str(run.get("gate"))].append(int(run.get("iteration", 0)))
    rows = []
    for gate in sorted(by_gate):
        values = by_gate[gate]
        rows.append(
            f"| {gate} | {_fmt_ratio_one_decimal_half_up(sum(values), len(values))} | "
            f"{max(values)} |"
        )
    return _table_lines(
        "## Historical per-gate iterations-to-converge (unversioned)",
        "| Gate | Mean | Max |",
        "| --- | ---: | ---: |",
        rows,
        "| none | 0.0 | 0 |",
    )


def _render_prompt_versions(records: list[dict[str, Any]]) -> list[str]:
    """Render prompt trends without mixing unlike execution configurations."""
    by_bucket = _prompt_version_buckets(records)
    rows = []
    for bucket in sorted(by_bucket):
        gate, cli, model, effort, sandbox, version = bucket
        values = by_bucket[bucket]
        rows.append(
            f"| {gate} | {cli} | {model} | {effort} | {sandbox} | {version} | "
            f"{len(values)} | "
            f"{_fmt_ratio_one_decimal_half_up(sum(values), len(values))} | "
            f"{max(values)} |"
        )
    return _table_lines(
        "## Per-gate iterations by prompt version and execution configuration",
        (
            "| Gate | CLI | Model | Effort | Sandbox | Prompt version | "
            "Runs | Mean | Max |"
        ),
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |",
        rows,
        "| none | none | none | none | none | none | 0 | 0.0 | 0 |",
    )


def _prompt_version_buckets(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str, str, str, str, str], list[int]]:
    """Group trajectory iterations by exact known tuple and prompt version."""
    by_bucket: dict[tuple[str, str, str, str, str, str], list[int]] = defaultdict(list)
    for run in _runs(records):
        config = run.get("execution_config")
        if isinstance(config, dict):
            cli = str(config.get("cli") or "(unknown)")
            model = str(config.get("model") or "(unknown)")
            effort = str(config.get("reasoning_effort") or "(unknown)")
            sandbox = str(config.get("sandbox") or "(unknown)")
        else:
            cli = str(run.get("cli") or "(unknown)")
            model = effort = sandbox = "(unknown)"
        version = run.get("prompt_version") or "(untagged)"
        bucket = (
            str(run.get("gate")),
            cli,
            model,
            effort,
            sandbox,
            str(version),
        )
        by_bucket[bucket].append(int(run.get("iteration", 0)))
    return dict(by_bucket)


def _render_finding_mix(records: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for run in _runs(records):
        findings = run.get("findings")
        if not isinstance(findings, dict):
            continue
        gate = str(run.get("gate"))
        totals[gate] += int(findings.get("total", 0))
        for key, value in findings.get("by_severity_classification", {}).items():
            counts[gate][str(key)] += int(value)
    rows = []
    for gate in sorted(totals):
        total = totals[gate]
        ignore_count = sum(
            value for key, value in counts[gate].items() if _is_ignore_key(key)
        )
        packed = ", ".join(f"{key}={counts[gate][key]}" for key in sorted(counts[gate]))
        rows.append(
            f"| {gate} | {total} | "
            f"{_fmt_ratio_one_decimal_half_up(ignore_count, total)} | {packed} |"
        )
    return _table_lines(
        "## Per-gate finding mix",
        "| Gate | Total | Ignore share | Severity/classification counts |",
        "| --- | ---: | ---: | --- |",
        rows,
        "| none | 0 | 0.0 | none |",
    )


def _render_near_cap(records: list[dict[str, Any]]) -> list[str]:
    rows = []
    for record in sorted(records, key=lambda item: str(item.get("feature", ""))):
        budget = record.get("budget", {})
        if "near_cap" in budget and budget["near_cap"] is None:
            rows.append(f"| {record.get('feature')} | unavailable |")
            continue
        if not budget.get("near_cap"):
            continue
        runs = ", ".join(str(item) for item in budget.get("near_cap_runs", []))
        rows.append(f"| {record.get('feature')} | {runs} |")
    return _table_lines(
        "## Per-feature near-cap",
        "| Feature | Near-cap runs |",
        "| --- | --- |",
        rows,
        "| none | none |",
    )


def _runs(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for record in records:
        yield from record.get("runs", [])


def _archived_at(record: dict[str, Any]) -> datetime:
    value = record.get("archived_at")
    if not isinstance(value, str):
        return datetime.min.replace(tzinfo=UTC)
    try:
        return parse_iso_datetime(value)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def parse_iso_datetime(value: str) -> datetime:
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_ignore_key(key: str) -> bool:
    return key.rsplit("/", 1)[-1].lower() == "ignore"


def _table_lines(
    title: str,
    header: str,
    separator: str,
    rows: list[str],
    fallback: str,
) -> list[str]:
    lines = [title, "", header, separator]
    lines.extend(rows or [fallback])
    lines.append("")
    return lines


def _fmt_ratio_one_decimal_half_up(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0"
    value = Decimal(numerator) / Decimal(denominator)
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
