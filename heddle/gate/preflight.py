from __future__ import annotations

from collections.abc import Sequence

from heddle.gate.overlap import paths_overlap
from heddle.gate.types import FailureReason, GateType, PreflightResult


def preflight(
    gate_type: GateType,
    plan_path: str | None,
    milestone: str | None,
    milestone_section: str,
    satisfies_ac_ids: Sequence[str],
    diff_non_empty: bool,
    changed_files: Sequence[str],
    owned_paths: Sequence[str],
    overlap_files: Sequence[str],
    untracked_files: Sequence[str] | None = None,
    allowed_non_owned_paths: Sequence[str] | None = None,
) -> PreflightResult:
    if not (
        gate_type.requires_plan
        or gate_type.requires_milestone
        or gate_type.requires_diff
        or gate_type.preflight_applicable
    ):
        return PreflightResult(status="skipped", fatal_reason=None, anomalies=())

    if gate_type.requires_plan and not plan_path:
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.MISSING_PLAN_PATH.value,
            anomalies=("Gate requires an Implementation Plan but none was resolved.",),
        )

    if gate_type.requires_milestone and (not milestone or not milestone_section):
        milestone_anomalies = [
            (
                f"Missing Milestone {milestone} section in {plan_path}."
                if milestone and plan_path
                else (
                    "Milestone gate preflight could not resolve the current "
                    "milestone section."
                )
            )
        ]
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.MISSING_MILESTONE_SECTION.value,
            anomalies=tuple(milestone_anomalies),
        )

    if not gate_type.preflight_applicable:
        return PreflightResult(status="passed", fatal_reason=None, anomalies=())

    anomalies: list[str] = []
    if not satisfies_ac_ids:
        anomalies.append("Current milestone does not declare any Satisfies AC IDs.")
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.MISSING_SATISFIES_AC_IDS.value,
            anomalies=tuple(anomalies),
        )

    if not owned_paths:
        anomalies.append(
            "No milestone-owned file paths were extracted from the current "
            "milestone; overlap could not be assessed."
        )
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.MISSING_OWNED_PATHS.value,
            anomalies=tuple(anomalies),
        )

    hidden_owned_files = [
        path
        for path in _overlapping_files(untracked_files or [], owned_paths)
        if path not in changed_files
    ]
    if hidden_owned_files:
        anomalies.append(
            "Untracked milestone-owned files are hidden from the authoritative diff: "
            + ", ".join(hidden_owned_files)
        )
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.UNTRACKED_OWNED_FILES.value,
            anomalies=tuple(anomalies),
        )

    if not diff_non_empty:
        anomalies.append("Authoritative diff is empty.")
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.EMPTY_DIFF.value,
            anomalies=tuple(anomalies),
        )

    if not changed_files:
        anomalies.append("Authoritative diff produced no changed file paths.")
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.CHANGED_FILES_EMPTY.value,
            anomalies=tuple(anomalies),
        )

    if not overlap_files:
        anomalies.append(
            "Changed files do not overlap the milestone-owned paths extracted "
            "from the plan."
        )
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.ZERO_OVERLAP.value,
            anomalies=tuple(anomalies),
        )

    off_scope_files = _off_scope_files(
        changed_files,
        owned_paths,
        allowed_non_owned_paths or [],
    )
    if off_scope_files:
        anomalies.append(
            "Changed files outside milestone-owned paths: " + ", ".join(off_scope_files)
        )
        return PreflightResult(
            status="failed",
            fatal_reason=FailureReason.OFF_SCOPE_CHANGED_FILES.value,
            anomalies=tuple(anomalies),
        )

    return PreflightResult(status="passed", fatal_reason=None, anomalies=())


def _overlapping_files(files: Sequence[str], owned_paths: Sequence[str]) -> list[str]:
    return [
        path
        for path in files
        if any(paths_overlap(path, owned_path) for owned_path in owned_paths)
    ]


def _off_scope_files(
    changed_files: Sequence[str],
    owned_paths: Sequence[str],
    allowed_non_owned_paths: Sequence[str],
) -> list[str]:
    scoped_paths = [*owned_paths, *allowed_non_owned_paths]
    return [
        path
        for path in changed_files
        if not any(paths_overlap(path, scoped_path) for scoped_path in scoped_paths)
    ]
