from __future__ import annotations

import os
import shutil
import traceback
from pathlib import Path
from typing import Any

from heddle.gate.cli import GateArgs
from heddle.gate.findings import (
    render_review_markdown,
    structured_findings,
    structured_verdict,
)
from heddle.gate.io import (
    GatePaths,
    append_log,
    build_gate_paths,
    cleanup_previous_artifacts,
    cleanup_temp_dir,
    publish_review_result,
    rotate_existing_artifacts,
)
from heddle.gate.preparation import prepared_validation_error
from heddle.gate.results import (
    TruncatedReviewError,
    bind_review_result,
    decode_review_content,
    read_bounded,
    serialize_review_result,
)
from heddle.gate.runners.claude import execute_claude
from heddle.gate.runners.codex import execute_codex
from heddle.gate.summary import build_summary, write_summary
from heddle.gate.types import (
    DiffResult,
    ExecutionResult,
    FailureReason,
    GateContext,
    GateSummary,
    GateType,
    OverlapResult,
    PreflightResult,
    PreparedGateRun,
    ValidationResult,
)
from heddle.gate.validation import validate_review
from heddle.io.process import MonitorResult, Termination
from heddle.kernel.project_config import KernelError


class GateRunner:
    def __init__(
        self,
        gate_type: GateType,
        args: GateArgs,
        context: GateContext,
        progress: Any = None,
    ):
        self.gate_type = gate_type
        self.args = args
        # The kernel-built GateContext is required: the runtime seam
        # (`build_context_from_snapshot` → `run_gate_for_runtime`) is the only
        # production constructor path, and the engine never parses plan
        # frontmatter.
        self._context = context
        # Band-4: an optional progress sink the runtime supplies to surface
        # the monitor's checkpoints as they are observed.
        self._progress = progress

    def run(self) -> tuple[GateSummary, int]:
        resolved_ctx = self._context
        prepared = resolved_ctx.prepared_run
        if prepared is None:
            raise KernelError(
                code="workspace-invalid",
                message="gate execution requires a prepared invocation",
                hint="prepare the selected gate again",
            )
        prepared_error = prepared_validation_error(
            self.gate_type,
            resolved_ctx,
            prepared,
        )
        if prepared_error is not None:
            raise prepared_error
        paths: GatePaths | None = None
        diff: DiffResult | None = None
        overlap: OverlapResult | None = None
        preflight_result: PreflightResult | None = None
        log_started = False
        try:
            paths = build_gate_paths(resolved_ctx, self.args, truncate_log=False)
            prepared_diff_path = paths.temp_dir / "prepared.diff"
            prepared_diff_path.write_text(
                prepared.diff_text,
                encoding="utf-8",
            )
            diff = DiffResult(
                source=prepared.diff.source,
                source_detail=prepared.diff.source_detail,
                diff_path=prepared_diff_path,
                changed_files=prepared.diff.changed_files,
                untracked_files=prepared.diff.untracked_files,
                warnings=prepared.diff.warnings,
            )
            overlap = prepared.overlap
            preflight_result = prepared.preflight
            paths.log.write_text("")
            log_started = True
            self._log_start(resolved_ctx, paths, diff)
            prompt_text = prepared.transport.stdin
            paths.prompt.write_text(prompt_text)
            append_log(paths.log, f"Prompt:    {paths.prompt}")
            if "AUTHORITATIVE INPUT RESOLUTION OVERRIDE FOR CODEX" in prompt_text:
                append_log(paths.log, "CodexEmbedMilestoneContext: 1")
            append_log(paths.log, f"Preflight: {preflight_result.status}")

            if preflight_result.fatal_reason:
                return self._failure_exit(
                    ctx=resolved_ctx,
                    paths=paths,
                    diff=diff,
                    overlap=overlap,
                    preflight_result=preflight_result,
                    reason=preflight_result.fatal_reason,
                    rotate_output=False,
                    preserve_artifacts=False,
                    cleanup_temp=True,
                )

            execution = self._execute(paths, prompt_text, prepared)
            if self._progress is not None:
                for checkpoint in execution.monitor.checkpoints:
                    self._progress(checkpoint)
            if execution.failure_reason is not None:
                return self._failure_exit(
                    ctx=resolved_ctx,
                    paths=paths,
                    diff=diff,
                    overlap=overlap,
                    preflight_result=preflight_result,
                    reason=execution.failure_reason.value,
                    execution=execution,
                    rotate_output=False,
                )

            if execution.response_warning is not None:
                return self._retained_success(
                    resolved_ctx,
                    paths,
                    diff,
                    overlap,
                    preflight_result,
                    execution,
                    prepared,
                    execution.response_warning,
                )

            candidate = execution.msg_out_path
            if (
                candidate is None
                or not candidate.exists()
                or candidate.stat().st_size == 0
            ):
                return self._failure_exit(
                    ctx=resolved_ctx,
                    paths=paths,
                    diff=diff,
                    overlap=overlap,
                    preflight_result=preflight_result,
                    reason=FailureReason.EMPTY_OUTPUT.value,
                    execution=execution,
                    validation=ValidationResult(passed=False, structure_errors=[]),
                    rotate_output=False,
                )

            return self._native_success(
                resolved_ctx,
                paths,
                diff,
                overlap,
                preflight_result,
                execution,
                prepared,
                candidate,
            )
        except Exception as exc:
            if isinstance(exc, KernelError):
                raise
            if paths is not None and not log_started:
                # build_gate_paths(truncate_log=False) defers truncation to
                # paths.log.write_text(""); an exception before that point
                # must still start this run's log fresh rather than append to
                # the prior run's content.
                paths.log.write_text("")
            return self._handle_unexpected_exception(
                exc,
                ctx=resolved_ctx,
                paths=paths,
                diff=diff,
                overlap=overlap,
                preflight_result=preflight_result,
            )

    def _native_success(
        self,
        ctx: GateContext,
        paths: GatePaths,
        diff: DiffResult,
        overlap: OverlapResult,
        preflight_result: PreflightResult,
        execution: ExecutionResult,
        prepared: PreparedGateRun,
        candidate: Path,
    ) -> tuple[GateSummary, int]:
        try:
            if prepared.output_contract is None:
                raise ValueError("prepared run has no output contract")
            content = decode_review_content(
                read_bounded(candidate), prepared.output_contract
            )
            validate_review(content, prepared)
            result = bind_review_result(content, prepared, ctx.feature)
            canonical = serialize_review_result(result)
            view = render_review_markdown(result)
        except ValueError as error:
            append_log(paths.log, f"ReviewValidationError: {error}")
            if prepared.assignment_id is not None:
                return self._retained_success(
                    ctx,
                    paths,
                    diff,
                    overlap,
                    preflight_result,
                    execution,
                    prepared,
                    str(error),
                )
            return self._failure_exit(
                ctx=ctx,
                paths=paths,
                diff=diff,
                overlap=overlap,
                preflight_result=preflight_result,
                reason=(
                    FailureReason.TRUNCATED_OUTPUT
                    if isinstance(error, TruncatedReviewError)
                    else FailureReason.INVALID_OUTPUT_STRUCTURE
                ).value,
                execution=execution,
                validation=ValidationResult(
                    passed=False, structure_errors=[str(error)]
                ),
                rotate_output=False,
            )
        try:
            paths, warnings = publish_review_result(
                paths,
                canonical,
                view,
                workspace=ctx.workspace_dir or ctx.repo_root / "plans" / ctx.feature,
            )
        except OSError as error:
            append_log(paths.log, f"CanonicalPublicationError: {error}")
            return self._failure_exit(
                ctx=ctx,
                paths=paths,
                diff=diff,
                overlap=overlap,
                preflight_result=preflight_result,
                reason=(
                    f"{FailureReason.EXECUTION_FAILURE.value}: "
                    f"canonical review publication failed: {error}"
                ),
                execution=execution,
                validation=ValidationResult(
                    passed=False,
                    structure_errors=[f"canonical review publication failed: {error}"],
                ),
                rotate_output=False,
            )
        for warning in warnings:
            append_log(paths.log, f"ViewPublicationWarning: {warning}")
        summary = build_summary(
            ctx=ctx,
            args=self.args,
            paths=paths,
            diff=diff,
            overlap=overlap,
            preflight=preflight_result,
            execution=execution,
            validation=ValidationResult(
                passed=True, structure_errors=[], structure_warnings=list(warnings)
            ),
            status="success",
            reason="success",
            gate_exit=0,
            findings=structured_findings(content),
            verdict=structured_verdict(content),
            preserve_artifacts=self.args.preserve_success_artifacts,
            prepared=prepared,
        )
        summary.review_result = result
        content_summary = paths.output.with_suffix(".gate-summary.json")
        summary.artifacts.published_views = (
            *summary.artifacts.published_views,
            str(content_summary),
        )
        write_summary(paths.summary, summary)
        # Preserve each native execution's diagnostic projection for trajectory
        # archival; the canonical result already has an immutable digest path.
        write_summary(content_summary, summary)
        append_log(paths.log, f"CanonicalResult: {paths.output}")
        append_log(paths.log, "Status:    success")
        cleanup_previous_artifacts(paths)
        if not self.args.preserve_success_artifacts:
            cleanup_temp_dir(paths)
        return summary, 0

    def _retained_success(
        self,
        ctx: GateContext,
        paths: GatePaths,
        diff: DiffResult,
        overlap: OverlapResult,
        preflight_result: PreflightResult,
        execution: ExecutionResult,
        prepared: PreparedGateRun,
        warning: str,
    ) -> tuple[GateSummary, int]:
        from heddle.gate.retained import capture_review, capture_view, serialize_capture

        try:
            capture = capture_review(prepared, execution, warning)
            paths, publication_warnings = publish_review_result(
                paths,
                serialize_capture(capture),
                capture_view(capture),
                workspace=ctx.workspace_dir or ctx.repo_root / "plans" / ctx.feature,
                kind="capture",
            )
        except (OSError, ValueError) as error:
            append_log(paths.log, f"CaptureStorageError: {error}")
            return self._failure_exit(
                ctx=ctx,
                paths=paths,
                diff=diff,
                overlap=overlap,
                preflight_result=preflight_result,
                reason=FailureReason.CAPTURE_STORAGE.value,
                execution=execution,
                validation=ValidationResult(False, [str(error)]),
                rotate_output=False,
                preserve_artifacts=True,
            )
        warnings = [*capture.warnings, *publication_warnings]
        summary = build_summary(
            ctx=ctx,
            args=self.args,
            paths=paths,
            diff=diff,
            overlap=overlap,
            preflight=preflight_result,
            execution=execution,
            validation=ValidationResult(False, [], warnings),
            status="retained",
            reason="review-interpretation-required",
            gate_exit=0,
            findings_note="unavailable: retained response awaits lead interpretation",
            verdict_note="unavailable: retained response is not an accepted review",
            preserve_artifacts=True,
            prepared=prepared,
        )
        summary.retained_review = capture
        content_summary = paths.output.with_suffix(".gate-summary.json")
        summary.artifacts.published_views = (
            *summary.artifacts.published_views,
            str(content_summary),
        )
        write_summary(paths.summary, summary)
        write_summary(content_summary, summary)
        append_log(paths.log, f"RetainedResponse: {paths.output}")
        append_log(
            paths.log, "Status: retained; interpret without another provider call"
        )
        return summary, 0

    def _execute(
        self,
        paths: GatePaths,
        prompt_text: str,
        prepared: PreparedGateRun,
    ) -> ExecutionResult:
        paths.command_output.write_text(prompt_text)
        cli = prepared.invocation.exec_config.cli
        cli_bin = self.args.cli_bin or cli
        if not _binary_available(cli_bin):
            return self._launch_failure(paths, f"CLI binary not found: {cli_bin}")
        if cli == "claude":
            try:
                return execute_claude(
                    self.gate_type,
                    self.args,
                    paths,
                    prompt_text,
                    prepared,
                )
            except OSError as exc:
                return self._launch_failure(paths, str(exc))
        if cli == "codex":
            try:
                return execute_codex(self.args, paths, prepared)
            except OSError as exc:
                return self._launch_failure(paths, str(exc))
        raise ValueError(f"unsupported cli: {cli}")

    def _failure_exit(
        self,
        *,
        ctx: GateContext,
        paths: GatePaths,
        diff: DiffResult,
        overlap: OverlapResult,
        preflight_result: PreflightResult,
        reason: str,
        execution: ExecutionResult | None = None,
        validation: ValidationResult | None = None,
        rotate_output: bool,
        preserve_artifacts: bool = True,
        log_status: bool = True,
        cleanup_temp: bool = False,
    ) -> tuple[GateSummary, int]:
        """The one failure exit: preserve or rotate
        artifacts as directed, resolve prior findings, write the failure
        summary, and return exit 1. Preflight/execution failures preserve the
        prior completed output; validation failure keeps its historical
        no-status-log behavior."""
        rotate_existing_artifacts(
            paths,
            include_output=rotate_output,
            include_summary=True,
        )
        prepared = ctx.prepared_run
        assert prepared is not None
        findings = None
        findings_note = "unavailable: native review did not validate"
        summary = build_summary(
            ctx=ctx,
            args=self.args,
            paths=paths,
            diff=diff,
            overlap=overlap,
            preflight=preflight_result,
            execution=execution,
            validation=validation,
            status="failure",
            reason=reason,
            gate_exit=1,
            findings=findings,
            findings_note=findings_note,
            preserve_artifacts=preserve_artifacts,
            prepared=prepared,
        )
        write_summary(paths.summary, summary)
        if log_status:
            append_log(paths.log, f"Status:    failure ({reason})")
        if cleanup_temp:
            cleanup_temp_dir(paths)
        return summary, 1

    def _handle_unexpected_exception(
        self,
        exc: Exception,
        *,
        ctx: GateContext,
        paths: GatePaths | None,
        diff: DiffResult | None,
        overlap: OverlapResult | None,
        preflight_result: PreflightResult | None,
    ) -> tuple[GateSummary, int]:
        resolved_ctx = ctx
        prepared = ctx.prepared_run
        assert prepared is not None
        resolved_paths = paths or build_gate_paths(resolved_ctx, self.args)
        rotate_existing_artifacts(
            resolved_paths,
            include_output=False,
            include_summary=True,
        )
        append_log(
            resolved_paths.log,
            f"InternalError: {exc}\n{traceback.format_exc().rstrip()}",
        )
        execution = self._unexpected_execution(resolved_paths)
        summary = build_summary(
            ctx=resolved_ctx,
            args=self.args,
            paths=resolved_paths,
            diff=diff or self._empty_diff(resolved_paths),
            overlap=overlap or OverlapResult(status="unknown", files=()),
            preflight=self._with_internal_error_anomaly(preflight_result, exc),
            execution=execution,
            validation=None,
            status="failure",
            reason=FailureReason.EXECUTION_FAILURE.value,
            gate_exit=1,
            preserve_artifacts=True,
            prepared=prepared,
        )
        write_summary(resolved_paths.summary, summary)
        append_log(
            resolved_paths.log,
            f"Status:    failure ({FailureReason.EXECUTION_FAILURE.value})",
        )
        return summary, 1

    def _log_start(self, ctx: GateContext, paths: GatePaths, diff: DiffResult) -> None:
        append_log(paths.log, f"Feature:   {ctx.feature}")
        append_log(paths.log, f"Gate:      {self.gate_type.name}")
        append_log(paths.log, f"CLI:       {self.args.cli}")
        append_log(paths.log, f"Spec:      {ctx.spec_path or '<none>'}")
        append_log(paths.log, f"Plan:      {ctx.plan_path or '<none>'}")
        append_log(paths.log, f"Milestone: {ctx.milestone or '<none>'}")
        source_detail = f" ({diff.source_detail})" if diff.source_detail else ""
        append_log(paths.log, f"Diff:      {diff.source}{source_detail}")
        for warning in diff.warnings:
            append_log(paths.log, f"DiffWarning: {warning}")
        if diff.untracked_files:
            append_log(
                paths.log,
                "Untracked: "
                + ", ".join(diff.untracked_files[:20])
                + (" ..." if len(diff.untracked_files) > 20 else ""),
            )
        append_log(paths.log, f"Output:    {paths.output}")
        append_log(paths.log, f"Summary:   {paths.summary}")
        append_log(paths.log, "")

    def _launch_failure(self, paths: GatePaths, message: str) -> ExecutionResult:
        append_log(paths.log, f"ExecutionFailure: {message}")
        paths.raw_out.write_text(message.rstrip() + "\n")
        return ExecutionResult(
            monitor=MonitorResult(
                exit_code=127,
                termination=Termination.COMPLETED,
                saw_stream_activity=False,
                inactivity_seconds=0,
                inactivity_marker="",
                checkpoints=[],
            ),
            cli_exit=127,
            failure_reason=FailureReason.EXECUTION_FAILURE,
            raw_out_path=paths.raw_out,
            msg_out_path=paths.last_message,
            filtered_jsonl_path=paths.filtered_jsonl,
            codex_diagnostics=None,
            json_message_path=paths.json_message,
        )

    def _unexpected_execution(self, paths: GatePaths) -> ExecutionResult:
        return ExecutionResult(
            monitor=MonitorResult(
                exit_code=1,
                termination=Termination.COMPLETED,
                saw_stream_activity=False,
                inactivity_seconds=0,
                inactivity_marker="",
                checkpoints=[],
            ),
            cli_exit=1,
            failure_reason=FailureReason.EXECUTION_FAILURE,
            raw_out_path=paths.raw_out,
            msg_out_path=paths.last_message,
            filtered_jsonl_path=paths.filtered_jsonl,
            codex_diagnostics=None,
            json_message_path=paths.json_message,
        )

    def _empty_diff(self, paths: GatePaths) -> DiffResult:
        paths.raw_out.parent.mkdir(parents=True, exist_ok=True)
        empty_diff = paths.temp_dir / "empty.diff"
        empty_diff.write_text("")
        return DiffResult(
            source="none",
            source_detail="",
            diff_path=empty_diff,
            changed_files=(),
        )

    def _with_internal_error_anomaly(
        self,
        preflight_result: PreflightResult | None,
        exc: Exception,
    ) -> PreflightResult:
        anomalies = (
            list(preflight_result.anomalies) if preflight_result is not None else []
        )
        anomalies.append(f"internal-error: {exc}")
        return PreflightResult(
            status=(
                preflight_result.status if preflight_result is not None else "not-run"
            ),
            fatal_reason=(
                preflight_result.fatal_reason if preflight_result is not None else None
            ),
            anomalies=tuple(anomalies),
        )


def _workflow_control_paths(ctx: GateContext) -> list[str]:
    # The native workspace directory
    # (state.yaml, reviews/, verification/) is a workflow control surface
    # exactly like the plan and spec — its churn rides every milestone's
    # diff (gate recording, session facts) and must never read as
    # off-scope-changed-files. Legacy contexts carry no workspace_dir.
    paths: list[str] = []
    for path in (ctx.plan_path, ctx.spec_path, ctx.workspace_dir):
        if path is None:
            continue
        try:
            paths.append(path.resolve().relative_to(ctx.repo_root).as_posix())
        except ValueError:
            continue
    return paths


def _binary_available(cli_bin: str) -> bool:
    path = Path(cli_bin)
    if path.is_absolute() or path.parent != Path("."):
        return path.exists() and os.access(path, os.X_OK)
    return shutil.which(cli_bin) is not None
