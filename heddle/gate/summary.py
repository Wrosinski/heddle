from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from heddle.gate.cli import GateArgs, resolve_gate_execution
from heddle.gate.io import GatePaths
from heddle.gate.types import (
    ArtifactInfo,
    CodexDiagnosis,
    CodexEventInfo,
    DiffResult,
    DiffSummary,
    ExecutionResult,
    FindingsInfo,
    GateContext,
    GateSummary,
    IOInfo,
    MilestoneInfo,
    OutputContractInfo,
    OverlapResult,
    OwnershipInfo,
    PreflightInfo,
    PreflightResult,
    PreparedGateRun,
    RuntimeInfo,
    ValidationResult,
    VerdictInfo,
)
from heddle.io.process import MonitorResult, Termination


def build_summary(
    *,
    ctx: GateContext,
    args: GateArgs,
    paths: GatePaths,
    diff: DiffResult,
    overlap: OverlapResult,
    preflight: PreflightResult,
    execution: ExecutionResult | None,
    validation: ValidationResult | None,
    status: str,
    reason: str,
    gate_exit: int,
    findings: FindingsInfo | None = None,
    findings_note: str | None = None,
    verdict: VerdictInfo | None = None,
    verdict_note: str | None = None,
    preserve_artifacts: bool = False,
    prepared: PreparedGateRun,
) -> GateSummary:
    validation_result = validation or ValidationResult(passed=True, structure_errors=[])
    execution_result = execution or _empty_execution(paths)
    codex_events, codex_diagnosis = _codex_sections(execution_result)
    execution_config = prepared.invocation.exec_config
    prompt_version = prepared.prompt_version
    effective_prompt_sha256 = prepared.effective_prompt_sha256
    review_basis_hash = prepared.review_basis_hash
    input_hash = prepared.input_hash
    return GateSummary(
        feature=ctx.feature,
        gate=ctx.gate_type.name,
        cli=execution_config.cli,
        plan=str(ctx.plan_path or ""),
        spec=str(ctx.spec_path or ""),
        milestone=MilestoneInfo(
            number=ctx.milestone,
            title=ctx.milestone_title,
            satisfies_ac_ids=ctx.satisfies_ac_ids,
        ),
        status=status,
        reason=reason,
        gate_exit=gate_exit,
        cli_exit=execution_result.cli_exit,
        runtime=RuntimeInfo(
            model=execution_config.model,
            reasoning_effort=execution_config.reasoning_effort,
            sandbox=execution_config.sandbox,
            workdir=str(args.workdir or ctx.repo_root),
            timeout_seconds=args.timeout_seconds,
            inactivity_seconds=args.inactivity_timeout_seconds,
            inactivity_observed_seconds=execution_result.monitor.inactivity_seconds,
            inactivity_marker=execution_result.monitor.inactivity_marker,
            iteration=args.iteration,
            max_iterations=args.max_iterations,
            prompt_version=prompt_version,
            effective_prompt_sha256=effective_prompt_sha256,
            review_basis_hash=review_basis_hash,
            input_hash=input_hash,
            review_policy_id=None,
            output_contract_version=prepared.output_contract_version,
            output_contract_sha256=prepared.output_contract_sha256,
        ),
        diff=DiffSummary(
            source=diff.source,
            source_detail=diff.source_detail,
            bytes=_size(diff.diff_path),
            changed_files=list(diff.changed_files),
            untracked_files=list(diff.untracked_files),
        ),
        ownership=OwnershipInfo(
            milestone_owned_paths=ctx.owned_paths,
            overlap_status=overlap.status,
            overlap_files=list(overlap.files),
        ),
        preflight=PreflightInfo(
            status=preflight.status,
            fatal_reason=preflight.fatal_reason,
            anomalies=list(preflight.anomalies),
        ),
        output_contract=OutputContractInfo(
            required_sections=[],
            structure_errors=validation_result.structure_errors,
            structure_warnings=validation_result.structure_warnings,
        ),
        io=IOInfo(
            output=str(paths.output),
            log=str(paths.log),
            summary=str(paths.summary),
            raw_bytes=_size(execution_result.raw_out_path),
            filtered_bytes=_size(execution_result.filtered_jsonl_path),
            last_message_bytes=_size(execution_result.msg_out_path),
            json_message_bytes=_size(execution_result.json_message_path),
            output_bytes=_size(paths.output),
            output_lines=_line_count(paths.output),
        ),
        codex_events=codex_events,
        artifacts=ArtifactInfo(
            output=str(paths.output),
            log=str(paths.log),
            summary=str(paths.summary),
            prompt=str(paths.prompt) if preserve_artifacts else None,
            raw_out=str(paths.raw_out) if preserve_artifacts else None,
            last_message=str(paths.last_message) if preserve_artifacts else None,
            json_message=str(paths.json_message) if preserve_artifacts else None,
            command_output=str(paths.command_output) if preserve_artifacts else None,
            events_jsonl=str(paths.filtered_jsonl) if preserve_artifacts else None,
            published_views=tuple(str(path) for path in paths.published_views),
        ),
        codex_diagnosis=codex_diagnosis,
        findings=findings,
        findings_note=findings_note,
        verdict=verdict,
        verdict_note=verdict_note,
    )


def write_summary(path: Path, summary: GateSummary) -> None:
    from heddle.gate.io import atomic_write_text

    atomic_write_text(path, json.dumps(summary.to_dict(), indent=2))


def _codex_sections(
    execution: ExecutionResult,
) -> tuple[CodexEventInfo, CodexDiagnosis | None]:
    if execution.codex_diagnostics is None:
        return (
            CodexEventInfo(
                tool_event_count=0,
                message_event_count=0,
                turn_completed_count=0,
                transport_error_count=0,
            ),
            None,
        )
    return execution.codex_diagnostics.event_info, execution.codex_diagnostics.diagnosis


def _empty_execution(paths: GatePaths) -> ExecutionResult:
    return ExecutionResult(
        monitor=MonitorResult(
            exit_code=0,
            termination=Termination.COMPLETED,
            saw_stream_activity=False,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        ),
        cli_exit=0,
        failure_reason=None,
        raw_out_path=paths.raw_out,
        msg_out_path=paths.last_message,
        filtered_jsonl_path=paths.filtered_jsonl,
        codex_diagnostics=None,
        json_message_path=paths.json_message,
    )


def _size(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    return path.stat().st_size


def _line_count(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    return len(path.read_text().splitlines())


def resolve_exec_config(
    cli: str,
    *,
    gate: str = "",
) -> dict[str, str]:
    """Return today's registry-owned default tuple for one gate lane.

    Producer freshness and cache expectations compare stored evidence against
    this current-policy projection. Recorded configuration always comes from
    ``PreparedGateRun`` instead.
    """
    from heddle.gate.registry import GATES
    from heddle.gate.types import GateInvocationOverrides

    gate_type = GATES[gate]
    invocation = resolve_gate_execution(
        gate_type,
        GateInvocationOverrides(
            cli=cli,  # type: ignore[arg-type]
        ),
    )
    return asdict(invocation.exec_config)
