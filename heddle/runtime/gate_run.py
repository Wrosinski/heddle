"""Typed gate application service: lock, prepare, execute, record, and return."""

from __future__ import annotations

import multiprocessing
import os
import queue
import signal
import sys
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from heddle.contracts import operations as ops
from heddle.contracts.gates import INDEPENDENT_GATE_GROUPS
from heddle.contracts.operations import RunGate, RunGates
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.contracts.review_assignments import (
    CanonicalReview,
    EngineFailure,
    RetainedReview,
    ReviewAssignment,
)
from heddle.contracts.schemas import WORKSPACE_STATE, normalize_decision_route
from heddle.gate import entry
from heddle.gate.preparation import gate_scope, prepared_validation_error
from heddle.gate.registry import GATES, VERDICT_PRODUCING_GATES
from heddle.gate.types import (
    FailureReason,
    GateEngineFailure,
    GateExecutionConfig,
    GateInvocationOverrides,
    GateOutcome,
    GateReportFinding,
    GateType,
    PreparedGateRun,
    ResolvedGateInvocation,
)
from heddle.kernel.model import FeatureSnapshot
from heddle.kernel.project_config import KernelError, ProjectConfig
from heddle.kernel.source_manifest import ObservedPath
from heddle.kernel.state import GateRun, StateFile
from heddle.runtime import recording
from heddle.runtime.cli_args import parse_gate_options
from heddle.runtime.clock import utc_now_minutes as _utc_now
from heddle.runtime.diagnostics import kernel_error_result
from heddle.runtime.feature_context import (
    ResolvedSnapshotContext,
    ResolveFeatureFailure,
    resolve_snapshot_from_cwd,
)
from heddle.runtime.locking import prepare_state_recording_lock
from heddle.runtime.output import emit_envelope
from heddle.runtime.readiness import (
    assess_current_readiness,
    readiness_failure,
    select_review_milestone,
)

# the handler-owned code→exit mapping for the run-gate read surface
# (resolution failures); mirrors status/orient's read-surface mapping.
_RESOLUTION_EXITS: Mapping[str, ExitCode] = {
    "feature-ambiguous": ExitCode.USAGE,
    "usage": ExitCode.USAGE,
    "workspace-invalid": ExitCode.FATAL,
}
_VERDICT_EXITS: Mapping[str, ExitCode] = {
    "fail": ExitCode.FATAL,
    "pass_with_conditions": ExitCode.ADVISORY,
    "pass": ExitCode.OK,
}
_LOG_SUFFIX = ".log"
_SUMMARY_SUFFIX = ".gate-summary.json"
_PHASE6_ACTIONABLE_KEYS = (
    "critical/implement",
    "important/implement",
    "critical/unknown",
    "important/unknown",
)


class _GateLockDrift(Exception):
    """The queued lock no longer names the refreshed mutable gate context."""

    def __init__(self, context: Any) -> None:
        super().__init__("gate lock identity changed while waiting")
        self.context = context


_USAGE = (
    "usage: heddle run-gate <gate> [--feature <slug>] [--cli claude|codex] "
    "[--model <id>] [--reasoning-effort low|medium|high|xhigh] [--json]"
)
_BATCH_USAGE = "usage: heddle run-gates [--feature <slug>] [--json]"
_DISTINCT_GATE_GROUP = INDEPENDENT_GATE_GROUPS[0]


def requires_codex_lane(required_gates: tuple[str, ...]) -> bool:
    """Whether any required gate's registry default lane is Codex.

    The drive startup probe's query — answered here so the driver reaches
    gate metadata only through its sanctioned ``heddle.runtime.gate_run``
    seam instead of importing the engine registry."""
    for gate in required_gates:
        gate_type = GATES.get(gate)
        if gate_type is not None and gate_type.default_cli == "codex":
            return True
    return False


def review_projection(snapshot: FeatureSnapshot) -> dict[str, Any]:
    """The driver reads the same qualified current matrix through its runtime seam."""
    if snapshot.state.feature_policy is None:
        return {}
    from heddle.kernel.project_config import load_project_config_from_cwd
    from heddle.runtime.review_assignments import projection

    return projection(load_project_config_from_cwd(), snapshot)


def run_gate(argv: list[str]) -> int:
    """Entry point for ``heddle run-gate`` (the dispatcher routes here)."""
    json_mode, parsed, failure = _parse(argv)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    from heddle.runtime.application import execute

    return _emit(execute(parsed), json_mode)


def run_gates(argv: list[str], json_mode: bool) -> int:
    """Decode the closed batch command served by the generic dispatcher."""
    feature: str | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token != "--feature":
            return _emit(
                _usage(f"unsupported run-gates argument {token!r}", _BATCH_USAGE),
                json_mode,
            )
        if feature is not None or index + 1 >= len(argv):
            return _emit(_usage("invalid --feature option", _BATCH_USAGE), json_mode)
        feature = argv[index + 1]
        index += 2
    return _emit(execute_gates(RunGates(feature=feature)), json_mode)


def _parse(
    argv: list[str],
) -> tuple[bool, RunGate | None, HeddleResult | None]:
    tokens = [token for token in argv if token != "run-gate"]
    json_mode = "--json" in tokens
    rest = [token for token in tokens if token != "--json"]
    options, failure = parse_gate_options(rest, usage=_USAGE)
    if failure is not None:
        return json_mode, None, failure
    assert options is not None
    if len(options.positionals) != 1:
        return (
            json_mode,
            None,
            _usage(
                "run-gate takes exactly one <gate> argument",
                _USAGE,
            ),
        )
    return (
        json_mode,
        RunGate(
            gate=options.positionals[0],
            cli=options.cli,
            model=options.model,
            reasoning_effort=options.reasoning_effort,
            feature=options.feature,
        ),
        None,
    )


def _resolve_invocation(
    snapshot: FeatureSnapshot,
    config: ProjectConfig,
    gate_type: GateType,
    parsed: RunGate,
) -> ResolvedGateInvocation:
    overrides = GateInvocationOverrides(
        cli=cast(Any, parsed.cli),
        model=parsed.model,
        reasoning_effort=cast(Any, parsed.reasoning_effort),
    )
    from heddle.runtime.review_assignments import resolve_invocation

    return resolve_invocation(snapshot, config, gate_type, overrides, admit=True)


def execute_gate(parsed: RunGate) -> HeddleResult:
    """Resolve a lock identity, then perform the authoritative read under it."""
    gate_type = GATES.get(parsed.gate)
    if gate_type is None:
        return _usage(
            f"unknown or retired gate {parsed.gate!r}",
            "run `heddle help` for the gate surface",
        )
    resolved = resolve_snapshot_from_cwd(parsed.feature, writable=True)
    if isinstance(resolved, ResolveFeatureFailure):
        return kernel_error_result(
            resolved.error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
            include_feature_switch_action=False,
        )
    try:
        pending = _retained_reuse(resolved, parsed)
        if pending is not None:
            return pending
        invocation = _resolve_invocation(
            resolved.snapshot, resolved.config, gate_type, parsed
        )
    except KernelError as error:
        return kernel_error_result(
            error,
            exit_codes=_RESOLUTION_EXITS,
            include_feature_switch_action=False,
        )
    try:
        milestone_id = (
            select_review_milestone(resolved.config, resolved.snapshot, parsed.gate)
            if gate_type.requires_milestone
            else None
        )
        context = entry.build_gate_lock_context(
            resolved.snapshot, resolved.config, gate_type, milestone_id=milestone_id
        )
    except (KernelError, OSError, UnicodeDecodeError, MemoryError) as error:
        typed_error = (
            error
            if isinstance(error, KernelError)
            else KernelError(
                code="workspace-invalid",
                message=f"gate context could not be constructed: {error}",
                hint=(
                    "restore readable UTF-8 plan and feature-spec inputs, "
                    "then rerun the gate"
                ),
            )
        )
        return readiness_failure(typed_error, resolved.diagnostics)
    try:
        prepare_state_recording_lock(
            resolved.config.root / resolved.snapshot.workspace / WORKSPACE_STATE
        )
    except (KernelError, OSError) as error:
        typed_error = (
            error
            if isinstance(error, KernelError)
            else KernelError(
                code="workspace-invalid",
                message=f"state lock metadata could not be prepared: {error}",
                hint="restore writable state-lock metadata, then rerun the gate",
            )
        )
        return kernel_error_result(
            typed_error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
            include_feature_switch_action=False,
        )

    lock_context = context
    for _lock_attempt in range(2):
        lock_stack = ExitStack()
        try:
            held_lock_path = lock_stack.enter_context(
                entry.gate_lock_for_runtime(lock_context, invocation)
            )
        except (KernelError, OSError) as error:
            typed_error = (
                error
                if isinstance(error, KernelError)
                else KernelError(
                    code="workspace-invalid",
                    message=f"gate lock could not be acquired: {error}",
                    hint="restore writable gate-lock metadata, then rerun the gate",
                )
            )
            return kernel_error_result(
                typed_error,
                exit_codes=_RESOLUTION_EXITS,
                diagnostics=resolved.diagnostics,
                include_feature_switch_action=False,
            )
        with lock_stack:
            try:
                # `_run_locked` deliberately re-resolves the snapshot and
                # captures semantic inputs in a fresh frame. Current-milestone
                # lock selection uses metadata; all-done scope selection also
                # observes readiness before the lock and rechecks it under lock.
                return _run_locked(
                    parsed,
                    gate_type=gate_type,
                    invocation=invocation,
                    held_lock_path=held_lock_path,
                )
            except _GateLockDrift as drift:
                lock_context = drift.context

    return kernel_error_result(
        KernelError(
            code="workspace-invalid",
            message="gate lock identity changed repeatedly while waiting",
            hint="rerun the gate after workflow milestone changes settle",
        ),
        exit_codes=_RESOLUTION_EXITS,
        diagnostics=resolved.diagnostics,
        include_feature_switch_action=False,
    )


@dataclass
class _BatchMember:
    parsed: RunGate
    gate_type: GateType
    invocation: ResolvedGateInvocation
    context: Any
    prepared: PreparedGateRun
    policy: _ReviewPolicy
    scope: str
    attempt: int
    reuse: str
    preparation_diagnostics: tuple[Diagnostic, ...] = ()
    completion: GateOutcome | ReuseResult | None = None
    decision: _GateDecision | None = None
    run_id: str | None = None
    publication: str = "pending"
    execution: str = "pending"
    error: HeddleError | None = None


def execute_gates(parsed: RunGates) -> HeddleResult:
    """Run the one declared independent review group with parent-owned writes."""
    resolved = resolve_snapshot_from_cwd(parsed.feature, writable=True)
    if isinstance(resolved, ResolveFeatureFailure):
        return kernel_error_result(
            resolved.error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
            include_feature_switch_action=False,
        )
    try:
        eligible, current_actions = _batch_readiness(resolved)
    except KernelError as error:
        return kernel_error_result(
            error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
            include_feature_switch_action=False,
        )
    try:
        prepare_state_recording_lock(
            resolved.config.root / resolved.snapshot.workspace / WORKSPACE_STATE
        )
        lock_inputs = []
        for gate in _DISTINCT_GATE_GROUP:
            gate_type = GATES[gate]
            operation = RunGate(gate, feature=resolved.feature)
            invocation = _resolve_invocation(
                resolved.snapshot, resolved.config, gate_type, operation
            )
            context = entry.build_gate_lock_context(
                resolved.snapshot, resolved.config, gate_type, milestone_id=None
            )
            lock_inputs.append((context, invocation))
    except (KernelError, OSError, UnicodeDecodeError, MemoryError) as error:
        if (
            not eligible
            and isinstance(error, KernelError)
            and error.message.startswith("review assignment:")
        ):
            return _batch_ineligible(resolved, current_actions)
        return _batch_input_failure(resolved, "batch lock inputs", error)

    members: list[_BatchMember]
    try:
        with ExitStack() as stack:
            for context, invocation in lock_inputs:
                stack.enter_context(entry.gate_lock_for_runtime(context, invocation))
            refreshed = resolve_snapshot_from_cwd(resolved.feature, writable=True)
            if isinstance(refreshed, ResolveFeatureFailure):
                return kernel_error_result(
                    refreshed.error,
                    exit_codes=_RESOLUTION_EXITS,
                    diagnostics=refreshed.diagnostics,
                    include_feature_switch_action=False,
                )
            resolved = refreshed
            eligible, current_actions = _batch_readiness(resolved)
            prepared = _prepare_batch_members(resolved)
            if isinstance(prepared, HeddleResult):
                if (
                    not eligible
                    and prepared.error is not None
                    and prepared.error.message.startswith("review assignment:")
                ):
                    return _batch_ineligible(resolved, current_actions)
                return prepared
            members = prepared
            # Current readiness controls provider admission, while exact and
            # orphan recovery remain valid after readiness has advanced beyond
            # the batch action.  This check is deliberately under both member
            # locks and after preparation has classified every member.
            if not eligible and any(member.reuse == "none" for member in members):
                return _batch_ineligible(resolved, current_actions)
            interrupted = _execute_batch_workers(resolved, members)
            if interrupted is not None:
                return interrupted
    except (KernelError, OSError, UnicodeDecodeError, MemoryError) as error:
        return _batch_input_failure(resolved, "batch admission", error)

    return _publish_batch(resolved, members)


def _prepare_batch_members(
    resolved: ResolvedSnapshotContext,
) -> list[_BatchMember] | HeddleResult:
    """Prepare both immutable inputs and admit every new call before launch."""
    snapshot, config = resolved.snapshot, resolved.config
    try:
        from heddle.runtime.review_assignments import (
            ReviewEvidenceAccess,
            validate_sources,
        )
        from heddle.runtime.verification import verification_statuses

        retained = validate_sources(
            ReviewEvidenceAccess(
                config.root,
                snapshot.workspace,
                snapshot.feature,
                snapshot.state,
            )
        )
        verification_statuses(config.root, snapshot.state)
        members = []
        for gate in _DISTINCT_GATE_GROUP:
            gate_type = GATES[gate]
            parsed = RunGate(gate, feature=resolved.feature)
            invocation = _resolve_invocation(snapshot, config, gate_type, parsed)
            members.append(
                _prepare_member(
                    resolved,
                    parsed,
                    gate_type,
                    invocation,
                    retained,
                    block_rejected_source_retry=True,
                )
            )
    except (KernelError, OSError, UnicodeDecodeError, ValueError, MemoryError) as error:
        return _batch_input_failure(resolved, "batch inputs", error)
    failure = next((item for item in members if isinstance(item, HeddleResult)), None)
    if failure is not None:
        return failure
    return cast(list[_BatchMember], members)


def _prepare_member(
    resolved: ResolvedSnapshotContext,
    parsed: RunGate,
    gate_type: GateType,
    invocation: ResolvedGateInvocation,
    retained: Mapping[str, entry.ReviewResult],
    *,
    held_lock_path: Path | None = None,
    block_rejected_source_retry: bool = False,
) -> _BatchMember | HeddleResult:
    snapshot, config = resolved.snapshot, resolved.config
    captures: dict[str, ObservedPath] = {}
    milestone_id = (
        select_review_milestone(
            config, snapshot, parsed.gate, source_observations=captures
        )
        if gate_type.requires_milestone
        else None
    )
    lock_context = entry.build_gate_lock_context(
        snapshot, config, gate_type, milestone_id=milestone_id
    )
    if held_lock_path is not None:
        expected_lock_path = entry.gate_lock_path_for_runtime(lock_context, invocation)
        if expected_lock_path != held_lock_path:
            raise _GateLockDrift(lock_context)
    context = entry.build_gate_context(
        snapshot,
        config,
        gate_type,
        cli=invocation.exec_config.cli,
        milestone_id=milestone_id,
        source_observations=captures,
    )
    scope = gate_scope(gate_type, context)
    policy = _review_policy(snapshot, parsed.gate, scope, invocation.exec_config.cli)
    prepared = entry.prepare_gate_run(
        context,
        gate_type=gate_type,
        invocation=invocation,
    )
    invalid = prepared_validation_error(gate_type, context, prepared)
    if invalid is not None:
        return kernel_error_result(
            invalid,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
            include_feature_switch_action=False,
        )
    context.prepared_run = prepared
    preparation_diagnostics = tuple(
        Diagnostic(
            severity=Severity.ADVISORY,
            code="optional-lane-unavailable",
            message=message,
        )
        for message in prepared.diagnostics
    )
    candidates = tuple(
        run
        for fact in snapshot.state.gates
        if fact.gate == parsed.gate and fact.scope == scope
        for run in fact.runs
    )
    if block_rejected_source_retry and _changed_source_attempt_is_pending(
        snapshot.state, policy.assignment
    ):
        return _batch_input_failure(
            resolved,
            "batch recovery",
            KernelError(
                code="workspace-invalid",
                message=f"{parsed.gate} has completed output bound to changed source",
                hint="preserve the completed artifact and repair its current binding",
            ),
        )
    recovered = _unrecorded_completion(resolved, parsed, prepared)
    if recovered is not None:
        attempt = _review_attempt(
            policy, snapshot.state, invocation.exec_config.cli, cached=False
        )
        if isinstance(attempt, HeddleResult):
            return attempt
        return _BatchMember(
            parsed,
            gate_type,
            invocation,
            context,
            prepared,
            policy,
            scope,
            attempt,
            "recovered",
            preparation_diagnostics,
            recovered,
        )
    reuse = (
        Execute()
        if prepared.preflight.fatal_reason is not None
        else decide_review_reuse(
            prepared,
            candidates,
            lambda run: retained[run.run_id],
        )
    )
    if isinstance(reuse, Repair):
        return kernel_error_result(
            reuse.error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
            include_feature_switch_action=False,
        )
    attempt = _review_attempt(
        policy,
        snapshot.state,
        invocation.exec_config.cli,
        cached=isinstance(reuse, ReuseResult),
    )
    if isinstance(attempt, HeddleResult):
        return attempt
    if isinstance(reuse, ReuseResult):
        return _BatchMember(
            parsed,
            gate_type,
            invocation,
            context,
            prepared,
            policy,
            scope,
            attempt,
            "exact",
            preparation_diagnostics,
            reuse,
            _cached_decision(policy, reuse),
            reuse.run.run_id,
            "exact",
            "completed",
        )
    return _BatchMember(
        parsed,
        gate_type,
        invocation,
        context,
        prepared,
        policy,
        scope,
        attempt,
        "none",
        preparation_diagnostics,
    )


def _changed_source_attempt_is_pending(
    state: StateFile, assignment: ReviewAssignment
) -> bool:
    return any(
        attempt.assignment_id == assignment.id
        and isinstance(attempt.outcome, EngineFailure)
        and attempt.outcome.reason
        in {
            FailureReason.REVIEWED_SOURCE_CHANGED.value,
            FailureReason.REVIEWED_SOURCE_UNAVAILABLE.value,
        }
        for attempt in state.review_assignments.attempts
    )


def _unrecorded_completion(
    resolved: ResolvedSnapshotContext,
    parsed: RunGate,
    prepared: PreparedGateRun,
) -> GateOutcome | None:
    """Resolve one gate's orphaned canonical/capture before provider admission."""
    snapshot = resolved.snapshot
    workspace = resolved.config.root / snapshot.workspace
    from heddle.kernel.review_assignments import attempt_artifacts, retained_responses

    recorded_paths = {
        item.path
        for item in attempt_artifacts(snapshot.state.review_assignments.attempts)
    }
    recorded_paths |= {
        run.artifact for fact in snapshot.state.gates for run in fact.runs
    }
    recorded_paths |= {row.artifact for row in retained_responses(snapshot.state)}
    rejected_sha256 = {
        item.sha256
        for attempt in snapshot.state.review_assignments.attempts
        if isinstance(attempt.outcome, EngineFailure)
        for item in attempt.artifacts
    }
    canonical = entry.unrecorded_review_results(
        workspace,
        recorded_paths=recorded_paths,
        rejected_sha256=rejected_sha256,
        feature=snapshot.feature,
        gate=parsed.gate,
        cli=prepared.invocation.exec_config.cli,
    )
    captures = entry.unrecorded_review_captures(
        workspace,
        recorded_paths=recorded_paths,
        rejected_sha256=rejected_sha256,
        feature=snapshot.feature,
        gate=parsed.gate,
        cli=prepared.invocation.exec_config.cli,
    )
    if len(canonical) + len(captures) > 1:
        raise ValueError(
            f"multiple unrecorded {parsed.gate} outputs need reconciliation"
        )
    if canonical:
        outcome = canonical[0]
        result = outcome.review_result
        assert result is not None
        entry.validate_review_replay(result, prepared, snapshot.feature)
        return outcome
    if captures:
        outcome = captures[0]
        capture = outcome.retained_review
        assert capture is not None
        invocation = capture.invocation
        expected = (
            prepared.feature,
            prepared.gate,
            prepared.scope,
            prepared.assignment_id,
            prepared.round_number,
            prepared.reviewer_slot,
            prepared.input_hash,
            prepared.review_basis_hash,
            prepared.prompt_version,
            prepared.effective_prompt_sha256,
            asdict(prepared.invocation.exec_config),
        )
        observed = (
            invocation.feature,
            invocation.gate,
            invocation.scope,
            invocation.assignment_id,
            invocation.round_number,
            invocation.reviewer_slot,
            invocation.input_hash,
            invocation.review_basis_hash,
            invocation.prompt_version,
            invocation.effective_prompt_sha256,
            asdict(invocation.execution),
        )
        if observed != expected:
            raise ValueError("retained invocation binding differs from prepared replay")
        return outcome
    return None


def _batch_worker(output: Any, member: _BatchMember, feature: str) -> None:
    """Execute one immutable member in an isolated process and return its outcome."""
    try:
        if hasattr(os, "setsid"):
            os.setsid()
        outcome = entry.run_gate_for_runtime(
            member.gate_type,
            member.context,
            feature=feature,
            progress=_progress_sink,
            iteration=member.attempt,
            max_iterations=member.policy.max_attempts,
        )
        output.put(("outcome", member.parsed.gate, outcome))
    except BaseException as error:  # noqa: BLE001 - cross-process error carrier
        output.put(
            (
                "error",
                member.parsed.gate,
                f"{type(error).__name__}: {error}",
            )
        )
    finally:
        output.close()
        output.join_thread()


def _execute_batch_workers(
    resolved: ResolvedSnapshotContext, members: list[_BatchMember]
) -> HeddleResult | None:
    pending = [member for member in members if member.reuse == "none"]
    if not pending:
        return None
    context = multiprocessing.get_context("fork")
    output = context.Queue()
    processes = [
        context.Process(
            target=_batch_worker,
            args=(output, member, resolved.feature),
            name=f"heddle-{member.parsed.gate}",
        )
        for member in pending
    ]
    received: dict[str, GateOutcome] = {}
    worker_errors: dict[str, str] = {}
    expected_revision = resolved.snapshot.state.revision
    next_publication = 0
    publication_failure: tuple[_BatchMember, HeddleError] | None = None
    try:
        for process in processes:
            process.start()
        while len(received) + len(worker_errors) < len(processes):
            try:
                kind, gate, value = output.get(timeout=0.2)
            except queue.Empty:
                if all(not process.is_alive() for process in processes):
                    break
                continue
            if kind == "outcome":
                received[gate] = value
                member = next(row for row in members if row.parsed.gate == gate)
                _accept_batch_outcome(resolved, member, value)
                while next_publication < len(members):
                    candidate = members[next_publication]
                    if candidate.completion is None:
                        break
                    if candidate.reuse == "exact":
                        next_publication += 1
                        continue
                    if publication_failure is None:
                        recorded = _record_batch_member(
                            resolved, candidate, expected_revision
                        )
                        if isinstance(recorded, HeddleError):
                            publication_failure = (candidate, recorded)
                        else:
                            expected_revision = recorded
                    else:
                        candidate.publication = "recoverable"
                    next_publication += 1
            else:
                worker_errors[gate] = value
        for process in processes:
            process.join(timeout=5)
        for member in pending:
            outcome = received.get(member.parsed.gate)
            if outcome is None:
                worker_errors.setdefault(
                    member.parsed.gate, "worker exited without a completion"
                )
                continue
        if worker_errors:
            selected = next(
                member for member in members if member.parsed.gate in worker_errors
            )
            selected.error = HeddleError(
                "internal",
                f"the {selected.parsed.gate} batch worker failed",
                worker_errors[selected.parsed.gate],
            )
            selected.execution = "failed"
            return _batch_failure(
                members,
                selected,
                selected.error,
                ExitCode.INTERNAL,
            )
        if publication_failure is not None:
            selected, publication_error = publication_failure
            return _batch_failure(members, selected, publication_error, ExitCode.FATAL)
    except KeyboardInterrupt:
        _terminate_batch_processes(processes)
        for member in members:
            if member.reuse != "none":
                continue
            try:
                recovered = _unrecorded_completion(
                    resolved, member.parsed, member.prepared
                )
            except (OSError, ValueError, KernelError):
                recovered = None
            if recovered is not None:
                member.completion = recovered
                member.execution = _execution_name(recovered)
                member.publication = "recoverable"
            else:
                member.execution = "interrupted"
                member.publication = "not-produced"
        selected = next(
            (member for member in members if member.execution == "interrupted"),
            members[0],
        )
        error = HeddleError(
            "internal",
            "the gate batch was interrupted",
            "restart run-gates to recover completed output and retry unfinished work",
        )
        selected.error = error
        return _batch_failure(members, selected, error, ExitCode.INTERNAL)
    finally:
        _terminate_batch_processes(processes)
        output.close()
        output.join_thread()
    return None


def _accept_batch_outcome(
    resolved: ResolvedSnapshotContext,
    member: _BatchMember,
    outcome: GateOutcome,
) -> None:
    if member.policy.assignment is not None and outcome.gate_exit == 0:
        outcome = _check_completed_source(
            resolved.snapshot,
            resolved.config,
            member.gate_type,
            member.scope,
            member.invocation,
            member.prepared.review_basis_hash,
            outcome,
        )
    member.completion = outcome
    member.decision = _completed_decision(
        member.policy,
        resolved.snapshot.state,
        member.parsed.gate,
        member.scope,
        member.attempt,
        outcome,
    )
    member.execution = _execution_name(outcome)


def _terminate_batch_processes(processes: list[Any]) -> None:
    for process in processes:
        if not process.is_alive():
            continue
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (PermissionError, ProcessLookupError):
            process.terminate()
    for process in processes:
        process.join(timeout=5)
        if process.is_alive():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                process.kill()
            process.join(timeout=5)


def _publish_batch(
    resolved: ResolvedSnapshotContext, members: list[_BatchMember]
) -> HeddleResult:
    expected_revision = resolved.snapshot.state.revision
    publication_failure: tuple[_BatchMember, HeddleError] | None = None
    for index, member in enumerate(members):
        if member.reuse == "exact" or member.publication != "pending":
            continue
        recorded = _record_batch_member(resolved, member, expected_revision)
        if isinstance(recorded, HeddleError):
            for remaining in members[index + 1 :]:
                if remaining.reuse != "exact":
                    remaining.publication = "recoverable"
            publication_failure = (member, recorded)
            break
        expected_revision = recorded

    if publication_failure is not None:
        selected, publication_error = publication_failure
        return _batch_failure(members, selected, publication_error, ExitCode.FATAL)

    refreshed = resolve_snapshot_from_cwd(resolved.feature, writable=True)
    if isinstance(refreshed, ResolveFeatureFailure):
        return kernel_error_result(
            refreshed.error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=refreshed.diagnostics,
            include_feature_switch_action=False,
        )
    member_actions = {
        member.parsed.gate: _batch_member_actions(refreshed, member)
        for member in members
    }
    failing = [
        member
        for member in members
        if member.execution in {"failed", "interrupted"}
        or (member.decision is not None and member.decision.exit_code == ExitCode.FATAL)
    ]
    if failing:
        selected = failing[0]
        aggregate_error = selected.error or HeddleError(
            "gate-not-converged",
            f"the {selected.parsed.gate} batch member requires repair",
            "follow every member remedy, then restart run-gates",
        )
        return _batch_failure(
            members,
            selected,
            aggregate_error,
            ExitCode.FATAL,
            actions=member_actions,
        )
    summaries = [
        _batch_member_summary(member, member_actions[member.parsed.gate])
        for member in members
    ]
    exit_code = (
        ExitCode.ADVISORY
        if any(
            member.decision is not None
            and member.decision.exit_code == ExitCode.ADVISORY
            for member in members
        )
        else ExitCode.OK
    )
    return HeddleResult.success(
        {
            "feature": resolved.feature,
            "gates": list(_DISTINCT_GATE_GROUP),
            "members": summaries,
            "revision": refreshed.snapshot.state.revision,
        },
        diagnostics=resolved.diagnostics,
        next_actions=_steering_next_actions(resolved.feature),
        exit_code=exit_code,
    )


def _record_batch_member(
    resolved: ResolvedSnapshotContext,
    member: _BatchMember,
    expected_revision: int,
) -> int | HeddleError:
    outcome = cast(GateOutcome, member.completion)
    try:
        accepted = _record(
            resolved.config.root / resolved.snapshot.workspace,
            member.parsed.gate,
            member.scope,
            member.invocation.exec_config.cli,
            member.prepared.input_hash,
            outcome,
            invocation=member.prepared.invocation,
            review_basis_hash=member.prepared.review_basis_hash,
            prompt_version=member.prepared.prompt_version,
            effective_prompt_sha256=member.prepared.effective_prompt_sha256,
            expected_revision=expected_revision,
            errored=outcome.gate_exit != 0,
            accept_minor=True,
            assignment_id=member.prepared.assignment_id,
            round_number=member.prepared.round_number,
            reviewer_slot=member.prepared.reviewer_slot,
        )
    except (
        KernelError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        MemoryError,
    ) as error:
        typed = (
            error
            if isinstance(error, KernelError)
            else KernelError(
                code="workspace-invalid",
                message=f"{member.parsed.gate} publication failed: {error}",
                hint="restart run-gates to recover the completed artifact",
            )
        )
        member.publication = "recoverable"
        member.error = HeddleError(typed.code, typed.message, typed.hint, typed.details)
        return member.error
    member.run_id = accepted.run_id
    if outcome.retained_review is not None:
        member.publication = "retained"
        member.execution = "retained"
    elif outcome.gate_exit != 0:
        member.publication = "recorded-error"
        member.execution = "failed"
    else:
        member.publication = "recorded"
        member.execution = "completed"
    return accepted.revision


def _batch_member_actions(
    resolved: ResolvedSnapshotContext, member: _BatchMember
) -> tuple[NextAction, ...]:
    outcome = member.completion
    if isinstance(outcome, ReuseResult):
        return ()
    if outcome is not None and outcome.retained_review is not None and member.run_id:
        from heddle.kernel.review_assignments import pending_retained
        from heddle.runtime.review_assignments import retained_response_result

        retained = next(
            row
            for row in pending_retained(resolved.snapshot.state)
            if row.run_id == member.run_id
        )
        return retained_response_result(
            resolved.config,
            resolved.snapshot,
            retained,
            cached=member.reuse == "recovered",
        ).next_actions
    if outcome is not None and outcome.gate_exit != 0:
        return (
            NextAction(
                ops.CommandAction(
                    RunGate(member.parsed.gate, feature=resolved.feature)
                ),
                f"retry the failed {member.parsed.gate} member",
            ),
        )
    return ()


def _batch_member_summary(
    member: _BatchMember, actions: tuple[NextAction, ...] = ()
) -> dict[str, Any]:
    outcome = member.completion
    artifact = (
        str(outcome.run.artifact)
        if isinstance(outcome, ReuseResult)
        else outcome.artifact_relpath
        if outcome is not None
        else ""
    )
    return {
        "gate": member.parsed.gate,
        "scope": member.scope,
        "assignment_id": member.prepared.assignment_id,
        "round_number": member.prepared.round_number,
        "reviewer_slot": member.prepared.reviewer_slot,
        "input_hash": member.prepared.input_hash,
        "execution": member.execution,
        "publication": member.publication,
        "reuse": member.reuse,
        "artifact": artifact,
        "run_id": member.run_id,
        "error": (
            {
                "code": member.error.code,
                "message": member.error.message,
                "hint": member.error.hint,
            }
            if member.error is not None
            else None
        ),
        "next_actions": [
            {
                "action": ops.action_payload(action.action),
                "command": action.command,
                "reason": action.reason,
            }
            for action in actions
        ],
    }


def _batch_failure(
    members: list[_BatchMember],
    selected: _BatchMember,
    error: HeddleError,
    exit_code: ExitCode,
    *,
    actions: Mapping[str, tuple[NextAction, ...]] | None = None,
) -> HeddleResult:
    per_member = actions or {}
    summaries = [
        _batch_member_summary(member, per_member.get(member.parsed.gate, ()))
        for member in members
    ]
    flattened = tuple(
        action
        for member in members
        for action in per_member.get(member.parsed.gate, ())
    )
    return HeddleResult.failure(
        HeddleError(
            error.code,
            error.message,
            error.hint,
            {"members": summaries, "selected_member": selected.parsed.gate},
        ),
        exit_code=exit_code,
        next_actions=flattened,
    )


def _execution_name(outcome: GateOutcome) -> str:
    if outcome.retained_review is not None:
        return "retained"
    return "failed" if outcome.gate_exit != 0 else "completed"


def _batch_readiness(
    resolved: ResolvedSnapshotContext,
) -> tuple[bool, tuple[NextAction, ...]]:
    assessment = assess_current_readiness(
        resolved.config, resolved.snapshot, purpose="boundary"
    )
    eligible = any(
        isinstance(next_action.action, ops.CommandAction)
        and isinstance(next_action.action.operation, RunGates)
        and next_action.action.operation.feature == resolved.feature
        for next_action in assessment.next_actions
    )
    actions = tuple(
        next_action
        for next_action in assessment.next_actions
        if not (
            isinstance(next_action.action, ops.CommandAction)
            and isinstance(next_action.action.operation, RunGates)
        )
    )
    return eligible, actions


def _batch_ineligible(
    resolved: ResolvedSnapshotContext, actions: tuple[NextAction, ...]
) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            "gate-not-converged",
            "the declared gate batch is not currently eligible",
            "follow the current single-gate action and reassess",
        ),
        exit_code=ExitCode.FATAL,
        diagnostics=resolved.diagnostics,
        next_actions=actions,
    )


def _batch_input_failure(
    resolved: ResolvedSnapshotContext, subject: str, error: BaseException
) -> HeddleResult:
    typed = (
        error
        if isinstance(error, KernelError)
        else KernelError(
            code="workspace-invalid",
            message=f"{subject} could not be resolved: {error}",
            hint="repair the declared batch inputs and retry",
        )
    )
    return kernel_error_result(
        typed,
        exit_codes=_RESOLUTION_EXITS,
        diagnostics=resolved.diagnostics,
        include_feature_switch_action=False,
    )


def _run_locked(
    parsed: RunGate,
    *,
    gate_type: GateType | None = None,
    invocation: ResolvedGateInvocation | None = None,
    held_lock_path: Path | None = None,
) -> HeddleResult:
    gate_type = gate_type or GATES.get(parsed.gate)
    if gate_type is None:
        return _usage(
            f"unknown gate {parsed.gate!r}",
            "run `heddle help` for the gate surface",
        )
    resolved = resolve_snapshot_from_cwd(parsed.feature, writable=True)
    if isinstance(resolved, ResolveFeatureFailure):
        return kernel_error_result(
            resolved.error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
            include_feature_switch_action=False,
        )
    config = resolved.config
    snapshot = resolved.snapshot

    try:
        pending = _retained_reuse(resolved, parsed)
        if pending is not None:
            return pending
        invocation = invocation or _resolve_invocation(
            snapshot, config, gate_type, parsed
        )
        from heddle.runtime.review_assignments import (
            ReviewEvidenceAccess,
            validate_sources,
        )
        from heddle.runtime.verification import verification_statuses

        retained = validate_sources(
            ReviewEvidenceAccess(
                config.root,
                snapshot.workspace,
                snapshot.feature,
                snapshot.state,
            )
        )
        verification_statuses(config.root, snapshot.state)
        prepared_member = _prepare_member(
            resolved,
            parsed,
            gate_type,
            invocation,
            retained,
            held_lock_path=held_lock_path,
        )
    except _GateLockDrift:
        raise
    except (
        KernelError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        MemoryError,
    ) as error:
        typed_error = (
            error
            if isinstance(error, KernelError)
            else KernelError(
                code="workspace-invalid",
                message=f"gate inputs could not be prepared: {error}",
                hint="restore every declared readable gate input, then rerun the gate",
            )
        )
        return kernel_error_result(
            typed_error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
            include_feature_switch_action=False,
        )
    if isinstance(prepared_member, HeddleResult):
        return prepared_member
    member = prepared_member

    completion: GateOutcome | ReuseResult
    if member.reuse == "exact":
        completion = cast(ReuseResult, member.completion)
        decision = cast(_GateDecision, member.decision)
        result_run_id = cast(str, member.run_id)
        result_revision = snapshot.state.revision
    else:
        try:
            if member.reuse == "none":
                outcome = entry.run_gate_for_runtime(
                    gate_type,
                    member.context,
                    feature=resolved.feature,
                    progress=_progress_sink,
                    iteration=member.attempt,
                    max_iterations=member.policy.max_attempts,
                )
            else:
                outcome = cast(GateOutcome, member.completion)
            _accept_batch_outcome(resolved, member, outcome)
            outcome = cast(GateOutcome, member.completion)
            recorded = _record_batch_member(resolved, member, snapshot.state.revision)
            if isinstance(recorded, HeddleError):
                return HeddleResult.failure(
                    recorded,
                    exit_code=_RESOLUTION_EXITS.get(recorded.code, ExitCode.FATAL),
                    diagnostics=resolved.diagnostics,
                )
            if outcome.retained_review is not None:
                from heddle.kernel.review_assignments import pending_retained
                from heddle.runtime.review_assignments import retained_response_result

                current = resolve_snapshot_from_cwd(resolved.feature)
                if isinstance(current, ResolveFeatureFailure):
                    raise current.error
                captured_response = next(
                    row
                    for row in pending_retained(current.snapshot.state)
                    if row.run_id == member.run_id
                )
                return retained_response_result(
                    current.config,
                    current.snapshot,
                    captured_response,
                    cached=member.reuse == "recovered",
                )
        except (KernelError, OSError, UnicodeDecodeError, MemoryError) as error:
            typed_error = (
                error
                if isinstance(error, KernelError)
                else KernelError(
                    code="workspace-invalid",
                    message=f"gate artifact could not be read: {error}",
                    hint=(
                        "check that the completed gate artifact exists and "
                        "is valid UTF-8, then rerun the gate"
                    ),
                )
            )
            return kernel_error_result(
                typed_error,
                exit_codes=_RESOLUTION_EXITS,
                diagnostics=resolved.diagnostics,
                include_feature_switch_action=False,
            )
        completion = outcome
        decision = cast(_GateDecision, member.decision)
        result_run_id = cast(str, member.run_id)
        result_revision = recorded
        if member.reuse == "recovered" and outcome.review_result is not None:
            refreshed = resolve_snapshot_from_cwd(resolved.feature)
            if isinstance(refreshed, ResolveFeatureFailure):
                return kernel_error_result(
                    refreshed.error,
                    exit_codes=_RESOLUTION_EXITS,
                    diagnostics=resolved.diagnostics,
                    include_feature_switch_action=False,
                )
            recorded_run = next(
                run
                for fact in refreshed.snapshot.state.gates
                if fact.gate == parsed.gate and fact.scope == member.scope
                for run in fact.runs
                if run.run_id == result_run_id
            )
            completion = ReuseResult(recorded_run, outcome.review_result)

    next_actions = (
        _steering_next_actions(resolved.feature)
        if isinstance(completion, ReuseResult) or completion.gate_exit == 0
        else ()
    )
    return _gate_result(
        parsed,
        resolved,
        member.prepared,
        member.policy,
        member.scope,
        member.attempt,
        completion,
        decision,
        run_id=result_run_id,
        revision=result_revision,
        preparation_diagnostics=member.preparation_diagnostics,
        next_actions=next_actions,
    )


def _check_completed_source(
    snapshot: FeatureSnapshot,
    config: ProjectConfig,
    gate_type: GateType,
    scope: str,
    invocation: ResolvedGateInvocation,
    basis: str,
    outcome: GateOutcome,
) -> GateOutcome:
    """Persist rejection through the ordinary error-event path, never as credit."""
    try:
        context = entry.build_gate_context(
            snapshot,
            config,
            gate_type,
            cli=invocation.exec_config.cli,
            milestone_id=scope if gate_type.requires_milestone else None,
            source_observations={},
            basis_only=True,
        )
        current = entry.prepare_current_review_basis(context, invocation)
        if current.review_basis_hash == basis:
            return outcome
        reason = FailureReason.REVIEWED_SOURCE_CHANGED
        warnings = ("reviewed source changed while the reviewer was running",)
    except (KernelError, OSError, ValueError, MemoryError) as error:
        reason = FailureReason.REVIEWED_SOURCE_UNAVAILABLE
        warnings = (f"reviewed source could not be revalidated: {error}",)
    return GateEngineFailure(
        gate_exit=1,
        reason=reason.value,
        rerun_recommended=None,
        structure_warnings=warnings,
        artifacts=outcome.artifacts,
    )


def _retained_reuse(
    resolved: ResolvedSnapshotContext, parsed: RunGate
) -> HeddleResult | None:
    """A completed slot awaiting interpretation cannot purchase another response."""
    from heddle.kernel.review_assignments import pending_retained, retained_responses
    from heddle.runtime.review_assignments import retained_response_result

    snapshot = resolved.snapshot
    if snapshot.state.feature_policy is not None:
        try:
            orphans = entry.unrecorded_review_captures(
                resolved.config.root / snapshot.workspace,
                recorded_paths={r.artifact for r in retained_responses(snapshot.state)}
                | {r.artifact for fact in snapshot.state.gates for r in fact.runs},
                rejected_sha256={
                    r.artifact_sha256
                    for fact in snapshot.state.gates
                    for r in fact.runs
                    if r.verdict.get("status") == "error" and r.artifact_sha256
                },
                feature=snapshot.feature,
                gate=parsed.gate,
                cli=parsed.cli,
            )
            if orphans:
                if len(orphans) != 1:
                    raise ValueError(
                        "multiple unrecorded responses need owner reconciliation"
                    )
                orphan = orphans[0]
                capture = orphan.retained_review
                assert capture is not None
                # Publication before ledger failure is recoverable only when the
                # captured basis still applies. Already indexed responses retain
                # their explicit old-input interpretation semantics below.
                invocation = ResolvedGateInvocation(
                    GateExecutionConfig(**asdict(capture.invocation.execution)),
                    frozenset(capture.overridden_axes),
                )
                checked = _check_completed_source(
                    snapshot,
                    resolved.config,
                    cast(GateType, GATES.get(parsed.gate)),
                    capture.invocation.scope,
                    invocation,
                    capture.invocation.review_basis_hash,
                    orphan,
                )
                if checked.gate_exit != 0:
                    raise ValueError(checked.structure_warnings[0])
                recovered = recording.record_retained_review(
                    resolved.config.root / snapshot.workspace / WORKSPACE_STATE,
                    outcome=orphan,
                    expected_revision=snapshot.state.revision,
                )
                refreshed = resolve_snapshot_from_cwd(snapshot.feature, writable=True)
                if isinstance(refreshed, ResolveFeatureFailure):
                    raise refreshed.error
                row = next(
                    r
                    for r in pending_retained(refreshed.snapshot.state)
                    if r.run_id == recovered.run_id
                )
                return retained_response_result(
                    refreshed.config, refreshed.snapshot, row, cached=True
                )
        except (OSError, ValueError) as error:
            raise KernelError(
                code="workspace-invalid",
                message=f"unrecorded completed response needs recovery: {error}",
                hint=(
                    "preserve the capture and repair recording; "
                    "do not rerun the provider"
                ),
            ) from error
    for retained in pending_retained(snapshot.state):
        assignment = next(
            a
            for a in snapshot.state.review_assignments.assignments
            if a.id == retained.assignment_id
        )
        slot = next(
            s
            for s in assignment.rounds[retained.round_number - 1].slots
            if s.name == retained.reviewer_slot
        )
        if assignment.role == parsed.gate and (
            parsed.cli is None or parsed.cli == slot.reviewer.cli
        ):
            return retained_response_result(
                resolved.config, snapshot, retained, cached=True
            )
    return None


@dataclass(frozen=True)
class _ReviewPolicy:
    verdict_gate: bool
    assignment: ReviewAssignment
    assignment_limit: int | None = None

    @property
    def max_attempts(self) -> int | None:
        return self.assignment_limit


@dataclass(frozen=True)
class _GateDecision:
    exit_code: ExitCode


def _review_policy(
    snapshot: FeatureSnapshot, gate: str, scope: str, cli: str
) -> _ReviewPolicy:
    del cli
    from heddle.kernel.review_assignments import assignment_for, selected_policy

    assignment = assignment_for(snapshot.state, gate, scope)
    selected = selected_policy(snapshot.state, gate)
    return _ReviewPolicy(
        verdict_gate=gate in VERDICT_PRODUCING_GATES,
        assignment=assignment,
        assignment_limit=selected.limit,
    )


def _review_attempt(
    policy: _ReviewPolicy,
    state: StateFile,
    cli: str,
    *,
    cached: bool,
) -> int | HeddleResult:
    """Admit the selected slot in the explicit current assignment round."""
    from heddle.kernel.review_assignments import launch_round

    current = launch_round(state, policy.assignment)
    if cached:
        return current.number
    slot = next(s.name for s in current.slots if s.reviewer.cli == cli)
    if state.review_assignments.attempts:
        accepted_slot = any(
            attempt.assignment_id == policy.assignment.id
            and attempt.round_number == current.number
            and attempt.reviewer_slot == slot
            and (
                isinstance(attempt.outcome, CanonicalReview)
                or attempt.interpretation is not None
            )
            for attempt in state.review_assignments.attempts
        )
    else:
        accepted_slot = any(
            source.assignment_id == policy.assignment.id
            and source.round_number == current.number
            and source.reviewer_slot == slot
            for source in state.review_assignments.sources
        )
    if accepted_slot:
        return _usage(
            "this round slot is already accepted and its input changed",
            "record native dispositions, then explicitly open the next review round",
        )
    return current.number


def _cached_decision(policy: _ReviewPolicy, reuse: ReuseResult) -> _GateDecision:
    projection = entry.machine_projection_from_result(reuse.result)
    return _GateDecision(
        _exit_for(
            verdict_gate=policy.verdict_gate,
            accept_minor=True,
            status=projection["status"],
            findings=projection["findings"],
            severity_classification=projection["severity_classification"],
            gate_exit=0,
        )
    )


def _completed_decision(
    policy: _ReviewPolicy,
    state: StateFile,
    gate: str,
    scope: str,
    attempt: int,
    outcome: GateOutcome,
) -> _GateDecision:
    """Compute recording flags and envelope semantics from the same completion."""
    if outcome.retained_review is not None:
        return _GateDecision(ExitCode.ADVISORY)
    exit_code = _exit_for(
        verdict_gate=policy.verdict_gate,
        accept_minor=True,
        status=outcome.status,
        findings=outcome.findings,
        severity_classification=outcome.severity_classification,
        gate_exit=outcome.gate_exit,
    )
    del state, gate, scope, attempt
    return _GateDecision(exit_code)


def _gate_result(
    parsed: RunGate,
    resolved: ResolvedSnapshotContext,
    prepared: PreparedGateRun,
    policy: _ReviewPolicy,
    scope: str,
    attempt: int,
    completion: GateOutcome | ReuseResult,
    decision: _GateDecision,
    *,
    run_id: str,
    revision: int,
    preparation_diagnostics: tuple[Diagnostic, ...],
    next_actions: tuple[NextAction, ...],
) -> HeddleResult:
    """Render recorded or reused evidence; all state observations are supplied."""
    if isinstance(completion, ReuseResult):
        projection = entry.machine_projection_from_result(completion.result)
        status, findings = projection["status"], projection["findings"]
        structure_warnings = projection["structure_warnings"]
        artifact_relpath = str(completion.run.artifact)
        gate_exit, failure_reason, is_cached = 0, "", True
    else:
        status, findings = completion.status, completion.findings
        structure_warnings = completion.structure_warnings
        artifact_relpath = completion.artifact_relpath
        gate_exit = completion.gate_exit
        failure_reason = str(completion.reason or "unknown failure")
        is_cached = False
    from heddle.kernel.review_assignments import (
        retained_responses,
        review_interpretations,
    )

    original = next(
        (r for r in retained_responses(resolved.snapshot.state) if r.run_id == run_id),
        None,
    )
    interpretation = next(
        (
            r
            for r in review_interpretations(resolved.snapshot.state)
            if r.run_id == run_id
        ),
        None,
    )
    if original is not None:
        structure_warnings = [*structure_warnings, *original.warnings]
    diagnostics = (
        resolved.diagnostics
        + preparation_diagnostics
        + tuple(
            Diagnostic(
                severity=Severity.ADVISORY,
                code="gate-output-structure",
                message=(
                    f"{parsed.gate} artifact {artifact_relpath} was accepted with "
                    f"recoverable structure drift: {warning}; inspect the original "
                    "review and normalize its presentation if coherence requires"
                )
                if gate_exit == 0
                else (
                    f"{parsed.gate} artifact {artifact_relpath} "
                    f"was not accepted: {warning}"
                ),
            )
            for warning in structure_warnings
        )
    )
    if gate_exit != 0:
        result = HeddleResult.failure(
            HeddleError(
                code="gate-not-converged",
                message=(
                    f"the {parsed.gate} gate run did not complete cleanly: "
                    f"{failure_reason}"
                ),
                hint=_failure_guidance_hint(failure_reason, artifact_relpath),
            ),
            exit_code=decision.exit_code,
            diagnostics=diagnostics,
        )
    else:
        result = HeddleResult.success(
            {
                "feature": resolved.feature,
                "gate": parsed.gate,
                "scope": scope,
                "status": status,
                "cached": is_cached,
                "run_id": run_id,
                "revision": revision,
                "accepted": gate_exit == 0,
                **(
                    {
                        "original_capture": resolved.snapshot.workspace
                        + original.artifact,
                        "interpretation": asdict(interpretation),
                    }
                    if original is not None and interpretation is not None
                    else {}
                ),
                "attempt": attempt,
                "max_attempts": policy.max_attempts,
                "rerun_allowed": policy.max_attempts is not None
                and attempt < policy.max_attempts,
                "cap_decision_recorded": False,
                "findings": findings,
                "execution": entry.execution_projection(prepared.invocation),
                "prompt_identity": entry.prompt_identity_projection(
                    prompt_version=prepared.prompt_version,
                    effective_prompt_sha256=prepared.effective_prompt_sha256,
                    review_basis_hash=prepared.review_basis_hash,
                    input_hash=prepared.input_hash,
                    review_policy_id=None,
                    output_contract_version=prepared.output_contract_version,
                    output_contract_sha256=prepared.output_contract_sha256,
                ),
                # Repo-relative artifact path for human/driver consumption
                # (the run fact itself records the workspace-relative form).
                "artifact": (
                    (Path(resolved.snapshot.workspace) / artifact_relpath).as_posix()
                    if artifact_relpath
                    else ""
                ),
            },
            diagnostics=diagnostics,
            exit_code=decision.exit_code,
            next_actions=next_actions,
        )
    return result


def _failure_guidance_hint(failure_reason: str, artifact_relpath: str) -> str:
    """Point a failed run at its durable evidence and packaged remedy."""
    if artifact_relpath:
        artifact = Path(artifact_relpath)
        summary = artifact.with_suffix(_SUMMARY_SUFFIX).as_posix()
        log = artifact.with_suffix(_LOG_SUFFIX).as_posix()
    else:
        summary = "the gate summary"
        log = "the gate log"
    return (
        f"inspect {summary} and {log}; then consult the `{failure_reason}` "
        "entry in the installed gate failure guide; run `heddle doctor` to "
        "check the installed guide before rerunning"
    )


def _steering_next_actions(feature: str) -> tuple[NextAction, ...]:
    """The kernel's §12.2 projection, re-resolved AFTER the run-fact write
    and attached to a completed run's envelope — the same walk orient and
    status surface (steering fix: the moment a dual-suite leg
    completes, the CLI itself names the next step). Takes the ALREADY
    resolved slug so the re-resolve never repeats pointer resolution.
    Guidance only: any fault degrades to no actions, never a failed run."""
    try:
        resolved = resolve_snapshot_from_cwd(feature)
        if isinstance(resolved, ResolveFeatureFailure):
            return ()
        from heddle.runtime.readiness import assess_current_readiness

        actions = assess_current_readiness(
            resolved.config,
            resolved.snapshot,
            purpose="command",
        ).next_actions
    except Exception:  # noqa: BLE001 — deliberate catch-all: the run fact
        # is already recorded; steering guidance must never turn a
        # completed gate run into a failure.
        return ()
    return actions


def _exit_for(
    *,
    verdict_gate: bool,
    accept_minor: bool,
    status: str | None,
    findings: Mapping[str, Any],
    severity_classification: Mapping[str, Any] | None,
    gate_exit: int,
) -> ExitCode:
    """Map engine completion + explicit gate kind onto the process exit.

    ``gate_exit != 0`` has first precedence and is always FATAL/3.
    Verdict gates fail closed on missing/unrecognized status.
    A clean non-verdict run uses the IMPLEMENT/unknown/contradiction rule.
    Phase 6 callers may opt into accepting minor findings: only the
    Critical/Important IMPLEMENT/unknown intersections plus contradictions are
    actionable; every Minor is accepted.
    """
    if gate_exit != 0:
        return ExitCode.FATAL
    if not verdict_gate and status == "fail":
        return ExitCode.ADVISORY
    if verdict_gate:
        if status is None:
            return ExitCode.FATAL
        return _VERDICT_EXITS.get(status, ExitCode.FATAL)
    classifications = findings.get("by_classification")
    if not isinstance(classifications, Mapping):
        return ExitCode.ADVISORY
    contradictions = findings.get("contradictions", 0)
    if type(contradictions) is not int or contradictions < 0:
        return ExitCode.ADVISORY
    if accept_minor:
        if not isinstance(severity_classification, Mapping):
            return ExitCode.ADVISORY
        counts = [severity_classification.get(key) for key in _PHASE6_ACTIONABLE_KEYS]
        if any(type(count) is not int or count < 0 for count in counts):
            return ExitCode.ADVISORY
        actionable = sum(cast(int, count) for count in counts) + contradictions
    else:
        counts = [classifications.get(key) for key in ("implement", "unknown")]
        if any(type(count) is not int or count < 0 for count in counts):
            return ExitCode.ADVISORY
        actionable = sum(cast(int, count) for count in counts) + contradictions
    return ExitCode.ADVISORY if actionable > 0 else ExitCode.OK


@dataclass(frozen=True)
class ReuseResult:
    run: GateRun
    result: entry.ReviewResult


@dataclass(frozen=True)
class Execute:
    """No latest same-lane completed event owns this exact input."""


@dataclass(frozen=True)
class Repair:
    error: KernelError


ResultReader = Callable[[GateRun], entry.ReviewResult]


def decide_review_reuse(
    prepared: PreparedGateRun,
    candidates: tuple[GateRun, ...],
    read_result: ResultReader,
) -> ReuseResult | Execute | Repair:
    """Select one same-lane event and validate its captured canonical replay.

    The reader supplies the event-bound result from the single contained source
    capture. Changed input executes; no older matching event can hide a newer
    completed input. Errors are attempts, never completed review candidates.
    """
    latest = next(
        (
            run
            for run in reversed(candidates)
            if run.cli == prepared.invocation.exec_config.cli
            and run.verdict.get("status") != "error"
        ),
        None,
    )
    if latest is None or latest.input_hash != prepared.input_hash:
        return Execute()
    try:
        result = read_result(latest)
        entry.validate_review_replay(result, prepared, prepared.feature)
    except (KernelError, OSError, ValueError, MemoryError) as error:
        return Repair(
            error
            if isinstance(error, KernelError)
            else KernelError(
                code="workspace-invalid",
                message=f"cached gate result could not be validated: {error}",
                hint=(
                    "restore the completed canonical gate result before "
                    "rerunning the gate"
                ),
            )
        )
    return ReuseResult(latest, result)


def _record(
    workspace_dir: Path,
    gate: str,
    scope: str,
    cli: str,
    input_hash: str,
    outcome: GateOutcome,
    *,
    invocation: ResolvedGateInvocation,
    review_basis_hash: str | None = None,
    prompt_version: str | None = None,
    effective_prompt_sha256: str | None = None,
    errored: bool = False,
    accept_minor: bool = False,
    expected_revision: int | None = None,
    assignment_id: str | None = None,
    round_number: int | None = None,
    reviewer_slot: str | None = None,
    interpretation: dict[str, Any] | None = None,
    recorded_at: str | None = None,
    review_policy_id: str | None = None,
) -> recording.GateRecording:
    if outcome.retained_review is not None:
        return recording.record_retained_review(
            workspace_dir / WORKSPACE_STATE,
            outcome=outcome,
            expected_revision=cast(int, expected_revision),
        )
    if not errored:
        expected_reports = outcome.findings["by_classification"].get("report")
        if (
            type(expected_reports) is not int
            or len(outcome.report_findings) != expected_reports
        ):
            raise KernelError(
                code="workspace-invalid",
                message="gate REPORT projection is incomplete",
                hint="repair the artifact/detail projection before recording",
            )
    now = recorded_at or _utc_now()
    execution_config = invocation.exec_config
    run_fact: dict[str, Any] = {
        "at": now,
        "cli": execution_config.cli,
        "artifact": outcome.artifact_relpath,
        "input_hash": input_hash,
        "execution_config": entry.execution_config_projection(invocation),
        "overridden_axes": sorted(invocation.explicit_axes),
        **(
            {"review_policy_id": review_policy_id}
            if review_policy_id is not None
            else {}
        ),
        **(
            {"review_basis_hash": review_basis_hash}
            if review_basis_hash is not None
            else {}
        ),
        **({"prompt_version": prompt_version} if prompt_version is not None else {}),
        **(
            {"effective_prompt_sha256": effective_prompt_sha256}
            if effective_prompt_sha256 is not None
            else {}
        ),
        # an engine run that did not complete cleanly is recorded with
        # the error marker so the gate cap can count it; the engine's own
        # (partial) verdict is never trustworthy on this path.
        "verdict": {"status": "error"} if errored else outcome.verdict,
        "findings": dict(outcome.findings),
        "report_findings": [
            {
                "finding_id": finding.finding_id,
                "title": finding.title,
                "severity": finding.severity,
            }
            for finding in outcome.report_findings
        ]
        if not errored
        else [],
        **(
            {"artifact_sha256": outcome.artifact_sha256}
            if outcome.artifact_sha256 is not None
            else {}
        ),
        **(
            {"failure_reason": str(outcome.reason or "unknown failure")}
            if errored
            else {}
        ),
    }
    if assignment_id is not None:
        run_fact.update(
            assignment_id=assignment_id,
            round_number=round_number,
            reviewer_slot=reviewer_slot,
        )
    decisions = [
        {
            "kind": "finding",
            "class": 4,
            "origin_finding_id": finding.finding_id,
            "source": f"{gate}#{seq}",
            "title": finding.title,
            **(
                {"recommendation": finding.recommendation}
                if finding.recommendation is not None
                else {}
            ),
            "status": "pending",
            "routes_to": _recorded_routes_to(finding),
            "created_at": now,
        }
        for seq, finding in enumerate(outcome.report_findings, start=1)
        if not errored and not (accept_minor and finding.severity == "minor")
    ]
    for decision in decisions:
        decision.update(
            rationale=None, resolution=None, resolution_source=None, resolved_at=None
        )
    return recording.record_gate_run(
        workspace_dir / WORKSPACE_STATE,
        gate=gate,
        scope=scope,
        run_fact=run_fact,
        decisions=decisions,
        expected_revision=expected_revision,
        interpretation=interpretation,
        artifacts=outcome.artifacts,
    )


def record_interpreted_review(
    snapshot: FeatureSnapshot,
    workspace: Path,
    retained: RetainedReview,
    capture: entry.CapturedReview,
    outcome: GateOutcome,
    *,
    author: str,
    reason: str,
) -> recording.GateRecording:
    """Use normal REPORT allocation with the original provider attempt identity."""
    invocation = capture.invocation
    return _record(
        workspace,
        invocation.gate,
        invocation.scope,
        invocation.execution.cli,
        invocation.input_hash,
        outcome,
        invocation=ResolvedGateInvocation(
            invocation.execution, frozenset(capture.overridden_axes)
        ),
        review_basis_hash=invocation.review_basis_hash,
        prompt_version=invocation.prompt_version,
        effective_prompt_sha256=invocation.effective_prompt_sha256,
        review_policy_id=invocation.review_policy_id,
        expected_revision=snapshot.state.revision,
        accept_minor=True,
        assignment_id=invocation.assignment_id,
        round_number=invocation.round_number,
        reviewer_slot=invocation.reviewer_slot,
        recorded_at=retained.at,
        interpretation={
            "run_id": retained.run_id,
            "capture_sha256": retained.artifact_sha256,
            "result_sha256": outcome.artifact_sha256,
            "author": author,
            "reason": reason,
            "at": _utc_now(),
        },
    )


def _recorded_routes_to(finding: GateReportFinding) -> list[str]:
    """Normalize one transcribed route; every incomplete shape fails closed."""
    raw_route = finding.route
    if not isinstance(raw_route, str):
        return []
    route = normalize_decision_route(raw_route)
    return [route] if route is not None else []


def _progress_sink(checkpoint: Any) -> None:
    """Surface one engine monitor checkpoint as progress — a
    terse stderr line so the envelope on stdout stays clean."""
    print("heddle run-gate: …", file=sys.stderr)


def _usage(message: str, hint: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="usage", message=message, hint=hint),
        exit_code=ExitCode.USAGE,
    )


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        error = result.error
        print(f"heddle: error[{error.code}]: {error.message}", file=sys.stderr)
        print(f"  hint: {error.hint}", file=sys.stderr)
    else:
        data = result.data or {}
        cached = " (cached)" if data.get("cached") else ""
        print(
            f"{data.get('gate')} [{data.get('feature')}] — "
            f"{data.get('status') or 'reported'}{cached}"
        )
    for diagnostic in result.diagnostics:
        print(f"note: {diagnostic.code}: {diagnostic.message}", file=sys.stderr)
