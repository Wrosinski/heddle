"""write-path runtime seams and command handlers."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from secrets import token_hex
from typing import Any

import yaml

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Conflict,
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.contracts.schemas import (
    FLOW_AUTO,
    FLOW_CHANGE_PENDING,
    FLOW_HITL,
    SESSION_PROGRESS_FIELD_TYPES,
    SESSION_PROGRESS_KEYS,
    STAGES,
)
from heddle.kernel.model import (
    FeatureSnapshot,
    is_terminal,
    resolve_snapshot,
)
from heddle.kernel.project_config import (
    KernelError,
)
from heddle.kernel.readiness import Boundary
from heddle.kernel.review_assignments import (
    PHASE_BOUNDARIES,
    validate_retained_transition,
)
from heddle.kernel.spec_lifecycle import parse_spec_frontmatter, render_lifecycle
from heddle.kernel.state import (
    StateFile,
    VerificationEvidence,
    parse_state_document,
    validate_state_transition,
)
from heddle.kernel.write_path import (
    add_milestone,
    advance_milestone,
    append_session,
    append_verification,
    apply_authorization_grant,
    apply_phase_exit,
    edit_milestone,
    mutate_task,
    set_command,
    unset_command,
)
from heddle.runtime import application
from heddle.runtime.auto_close import (
    open_decision_refs,
)
from heddle.runtime.clock import utc_now_minutes, utc_now_seconds
from heddle.runtime.diagnostics import blocking_failure, conflict_failure, write_failure
from heddle.runtime.feature_context import (
    ResolvedSnapshotContext,
    resolve_write_context,
)
from heddle.runtime.flow_changes import (
    append_authorization_grant,
    apply_pending_flow_change,
    pending_flow_change,
    phase_exit_grant_blockers,
    supersede_pending_flow_changes,
)
from heddle.runtime.locking import state_recording_lock
from heddle.runtime.output import emit_envelope
from heddle.runtime.readiness import (
    assess_current_readiness,
    readiness_failure,
    readiness_status_block,
)
from heddle.runtime.state_store import (
    StateCommit,
    accepted_state_error,
    commit_state,
    read_state_document,
)
from heddle.runtime.verification import (
    assess_current_verification,
    assess_recorded_verification,
    observe_current_source,
    publish_source_evidence,
    resolve_source_declaration,
    verification_command_for_scope,
)
from heddle.runtime.verify_exec import (
    run_verification_command,
    verification_fact,
    verification_log_relpath,
)
from heddle.runtime.write_args import (
    parse_commands_set,
    parse_commands_unset,
    parse_common,
    parse_common_with_positionals,
    parse_flow_set,
    parse_milestone_add,
    parse_milestone_edit,
    parse_phase_exit,
    parse_session_log,
    parse_verify,
    usage_failure,
    validate_milestone_payload,
)


@dataclass(frozen=True)
class _PhaseExitTransition:
    before_stage: str
    after_stage: str
    would_write: bool


def stamp_lifecycle(
    spec_path: Path,
    value: str = "complete",
) -> bool:
    try:
        original = spec_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"spec frontmatter target is unreadable: {error}",
            hint="restore a readable Feature Spec",
        ) from error
    new_text = render_lifecycle(original, spec_path, value)
    if new_text == original:
        return False
    temp_path = spec_path.with_name(f"{spec_path.name}.{os.getpid()}.tmp")
    try:
        try:
            temp_path.write_text(new_text, encoding="utf-8")
            os.chmod(temp_path, spec_path.stat().st_mode & 0o777)
            os.replace(temp_path, spec_path)
        except OSError as error:
            # A write-side failure (disk full, read-only spec dir,
            # permission) must route through run_phase_exit's structured
            # KernelError handler — mirroring _read_spec_frontmatter's read-side
            # guard — rather than crashing phase-exit with a raw traceback.
            raise KernelError(
                code="workspace-invalid",
                message=f"spec frontmatter target is unwritable: {error}",
                hint="check that the feature spec directory is writable",
            ) from error
    finally:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
    return True


def _read_spec_frontmatter(spec_path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"spec frontmatter target is unreadable: {error}",
            hint="check that the feature spec exists and is readable UTF-8",
        ) from error

    return parse_spec_frontmatter(text, spec_path)


def milestone_advance(operation: ops.MilestoneAdvance) -> HeddleResult:
    parsed = operation
    extra: dict[str, Any] = {}

    def render(document: dict[str, Any]) -> dict[str, Any]:
        current = next(
            (
                milestone
                for milestone in document.get("milestones", [])
                if milestone.get("status") == "current"
            ),
            None,
        )
        next_document = advance_milestone(
            document,
            allow_complete_reconciliation=(
                document.get("stage") == "complete"
                and not is_terminal(_state_for_close_guard(document))
            ),
        )
        if current is not None and next_document != document:
            landed = next(
                milestone
                for milestone in next_document["milestones"]
                if milestone.get("id") == current.get("id")
            )
            if landed.get("status") == "done":
                from heddle.runtime.review_assignments import (
                    build_boundary_acceptance,
                )

                receipt = build_boundary_acceptance(
                    context.config,
                    context.snapshot,
                    document,
                    boundary="milestone",
                    scope=str(current["id"]),
                    stage_after=str(next_document["stage"]),
                    accepted_at=utc_now_minutes(),
                )
                if receipt is not None:
                    next_document["review_assignments"]["acceptances"].append(receipt)
        extra["milestone_advance_summary"] = _milestone_advance_summary(
            document, next_document
        )
        return next_document

    context_result = resolve_write_context(parsed.feature, parsed.expect_revision)
    if isinstance(context_result, HeddleResult):
        return context_result
    context = context_result
    block = _not_authorized_block(context.snapshot)
    if block is not None:
        return block
    precheck = _advance_readiness_precheck(context)
    if precheck is not None:
        return precheck
    if parsed.dry_run:
        return _dry_run_result(
            context,
            render,
            command_name="milestone advance",
            extra=extra,
            factual_projection=True,
        )
    return _commit_result(
        context,
        render,
        command_name="milestone advance",
        extra=extra,
        post_write_guidance=True,
    )


def _milestone_advance_summary(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    before_current = next(
        (
            str(row.get("id"))
            for row in before.get("milestones", [])
            if row.get("status") == "current"
        ),
        None,
    )
    after_current = next(
        (
            str(row.get("id"))
            for row in after.get("milestones", [])
            if row.get("status") == "current"
        ),
        None,
    )
    if before == after:
        outcome = "unchanged"
        milestone_id = None
        reason = (
            "all milestones are done"
            if before.get("milestones")
            and all(row.get("status") == "done" for row in before["milestones"])
            else "no milestone is eligible to advance"
        )
    elif before_current is not None:
        outcome = "milestone-completed"
        milestone_id = before_current
        reason = f"completed current milestone {before_current}"
    else:
        outcome = "milestone-promoted"
        milestone_id = after_current
        reason = f"promoted milestone {after_current} to current"
    return {
        "outcome": outcome,
        "milestone_id": milestone_id,
        "current_before": before_current,
        "current_after": after_current,
        "reason": reason,
    }


def _state_for_close_guard(document: dict[str, Any]) -> StateFile:
    """Strictly decode a mutation document for close-reconciliation guards."""
    return parse_state_document(document, source=Path("state.yaml"))


def _advance_readiness_precheck(
    context: ResolvedSnapshotContext,
) -> HeddleResult | None:
    state = context.snapshot.state
    for milestone in state.milestones:
        if milestone.status != "done":
            continue
        try:
            resolve_source_declaration(state, milestone.id)
            verification_command_for_scope(state, milestone.id)
        except KernelError as error:
            return write_failure(error, context.diagnostics)
    current = context.snapshot.current_milestone
    if current is None or any(task.status != "done" for task in current.tasks):
        return None
    return _readiness_boundary_block(
        context, boundary=Boundary(context.snapshot.stage, current.id)
    )


def commands_set(operation: ops.CommandsSet) -> HeddleResult:
    parsed = operation
    key, shell_command = operation.key, operation.command
    return _run_state_mutation(
        parsed,
        command_name="commands set",
        render=lambda document: set_command(
            document, key=key, shell_command=shell_command
        ),
        extra={"command_key": key, "shell_command": shell_command},
    )


def commands_unset(operation: ops.CommandsUnset) -> HeddleResult:
    return _run_state_mutation(
        operation,
        command_name="commands unset",
        render=lambda document: unset_command(document, key=operation.key),
        extra={"command_key": operation.key},
    )


def milestone_add(operation: ops.MilestoneAdd) -> HeddleResult:
    parsed = operation
    payload = ops.decoded_payload(operation.milestone)
    affected: dict[str, Any] = {}

    def render(document: dict[str, Any]) -> dict[str, Any]:
        new_document = add_milestone(document, payload)
        affected["milestone"] = new_document["milestones"][-1]
        return new_document

    return _emit_milestone_mutation(
        parsed,
        render=render,
        affected=affected,
        next_actions_for=lambda milestone: (
            NextAction(
                action=ops.CommandAction(ops.Validate(feature=operation.feature)),
                reason=(
                    f"author the matching '### Milestone {milestone['id']}:' "
                    "section in the implementation plan — validate enforces "
                    "the cross-plane id-join"
                ),
            ),
        ),
    )


def milestone_edit(operation: ops.MilestoneEdit) -> HeddleResult:
    parsed = operation
    ref = operation.milestone_id
    payload = {
        key: value
        for key, value in ops.decoded_payload(operation.patch).items()
        if value is not None
    }
    affected: dict[str, Any] = {}

    def render(document: dict[str, Any]) -> dict[str, Any]:
        new_document = edit_milestone(document, ref, payload)
        affected["milestone"] = next(
            milestone
            for milestone in new_document["milestones"]
            if milestone.get("id") == ref
        )
        return new_document

    return _emit_milestone_mutation(parsed, render=render, affected=affected)


def _emit_milestone_mutation(
    parsed: ops.MilestoneAdd | ops.MilestoneEdit,
    *,
    render: Callable[[dict[str, Any]], dict[str, Any]],
    affected: dict[str, Any],
    next_actions_for: Callable[[dict[str, Any]], tuple[NextAction, ...]] | None = None,
) -> HeddleResult:
    context_result = resolve_write_context(parsed.feature, parsed.expect_revision)
    if isinstance(context_result, HeddleResult):
        return context_result
    context = context_result
    write = preview_state_write if parsed.dry_run else commit_state
    try:
        commit = write(
            context.state_path,
            expect_revision=context.snapshot.state.revision,
            transform=render,
        )
    except Conflict as error:
        return conflict_failure(error)
    except KernelError as error:
        return write_failure(error, context.diagnostics)

    data: dict[str, Any] = {
        "feature": context.feature,
        "milestone": affected["milestone"],
        "revision": commit.revision,
    }
    if parsed.dry_run:
        data["dry_run"] = True
    milestone = affected["milestone"]
    next_actions = next_actions_for(milestone) if next_actions_for else ()
    return HeddleResult.success(
        data,
        diagnostics=context.diagnostics,
        next_actions=next_actions,
    )


def _read_milestone_payload(
    from_file: str | None, *, command_name: str
) -> dict[str, Any] | HeddleResult:
    """Read the ``--from-file <path|->`` milestone payload — the
    ``_read_session_entry`` reader conventions: unreadable file, bad
    YAML, or a non-mapping are usage exit 2 with state untouched."""
    if from_file == "-":
        text = sys.stdin.read()
    else:
        assert from_file is not None  # the parsers require --from-file
        try:
            text = Path(from_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            return usage_failure(
                f"cannot read milestone payload {from_file!r}: {error}",
                "provide a readable UTF-8 YAML payload",
            )
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return usage_failure(
            f"malformed milestone YAML: {error}",
            "fix the YAML payload and retry",
        )
    if not isinstance(payload, dict):
        return usage_failure(
            f"{command_name} payload must be a mapping",
            "shape the payload per the milestone contract table",
        )
    return payload


def task(operation: ops.TaskAdd | ops.TaskCurrent | ops.TaskDone) -> HeddleResult:
    verb = (
        "add"
        if isinstance(operation, ops.TaskAdd)
        else "current"
        if isinstance(operation, ops.TaskCurrent)
        else "done"
    )

    def render(document: dict[str, Any]) -> dict[str, Any]:
        if isinstance(operation, ops.TaskAdd):
            return mutate_task(document, "add", text=operation.text)
        if isinstance(operation, ops.TaskDone):
            current = next(
                (m for m in document["milestones"] if m["status"] == "current"), None
            )
            if current is None or current["id"] != operation.milestone_id:
                raise KernelError(
                    code="milestone-out-of-sequence",
                    message="task milestone is not the current milestone",
                    hint="resolve the current milestone before completing its task",
                )
        return mutate_task(document, verb, task_ref=operation.task_id)

    return _run_state_mutation(operation, command_name=f"task {verb}", render=render)


def verify(operation: ops.Verify) -> HeddleResult:
    parsed = operation
    scope = operation.scope
    # Resolve and schema-validate the write context (resolve_snapshot)
    # before any stored-command lookup, so a schema-ahead state.yaml fails
    # workspace-invalid (exit 3) rather than masking as a usage error.
    context_result = resolve_write_context(parsed.feature, parsed.expect_revision)
    if isinstance(context_result, HeddleResult):
        return context_result
    context = context_result
    block = _not_authorized_block(context.snapshot)
    if block is not None:
        return block

    resolved_scope = scope
    if resolved_scope is None:
        if context.snapshot.current_milestone is None:
            return usage_failure(
                "verify requires --scope when no milestone is current",
                ops.VERIFICATION_SCOPE_GUIDANCE,
            )
        resolved_scope = context.snapshot.current_milestone.id

    try:
        command = verification_command_for_scope(context.snapshot.state, resolved_scope)
        declaration = resolve_source_declaration(context.snapshot.state, resolved_scope)
        # A new attempt may supersede stale content, but it cannot silently
        # overwrite or recreate damaged evidence required by existing history.
        assess_current_verification(
            context.config.root, context.snapshot.state, resolved_scope
        )
    except KernelError as error:
        return write_failure(error, context.diagnostics)
    if parsed.dry_run:
        return HeddleResult.success(
            {
                "feature": context.feature,
                "command": "verify",
                "dry_run": True,
                "revision": context.snapshot.state.revision + 1,
                "wrote": False,
                "mutation_summary": {
                    "command": "verify",
                    "would_write": True,
                },
                "verification": {
                    "scope": resolved_scope,
                    "command": command,
                    **(
                        {
                            "source_set_kind": declaration.kind,
                            "declaration_paths": list(declaration.paths),
                        }
                        if declaration is not None
                        else {}
                    ),
                },
            },
            diagnostics=context.diagnostics,
        )

    at = utc_now_seconds()
    post_revision = context.snapshot.state.revision + 1
    log_relpath = verification_log_relpath(
        resolved_scope,
        at,
        post_revision,
        attempt_id=token_hex(8),
    )
    log_path = context.state_path.parent / log_relpath
    try:
        _before_definition, before_evidence, _git = observe_current_source(
            context.config.root, declaration, scope=resolved_scope
        )
        before_reference, _before_reused = publish_source_evidence(
            context.state_path.parent, before_evidence
        )
        exit_code = run_verification_command(context.config.root, command, log_path)
        _after_definition, after_evidence, _after_git = observe_current_source(
            context.config.root,
            declaration,
            with_diagnostics=False,
            scope=resolved_scope,
        )
        after_reference, _after_reused = publish_source_evidence(
            context.state_path.parent, after_evidence
        )
        evidence = VerificationEvidence(
            schema="heddle.verification-evidence/v1",
            before=before_reference,
            after=after_reference,
        )
    except KernelError as error:
        return write_failure(error, context.diagnostics)
    fact = verification_fact(
        resolved_scope,
        command,
        exit_code=exit_code,
        at=at,
        revision=post_revision,
        evidence=evidence,
        log=log_relpath,
    )
    fact_index = len(context.snapshot.state.verifications)
    try:
        commit = commit_state(
            context.state_path,
            expect_revision=context.snapshot.state.revision,
            transform=lambda document: append_verification(document, fact),
        )
    except Conflict as error:
        result = conflict_failure(error)
    except KernelError as error:
        result = write_failure(error, context.diagnostics)
    else:
        try:
            current = resolve_snapshot(context.config, context.feature)
            assessment = assess_recorded_verification(
                context.config.root,
                current.state,
                resolved_scope,
                fact_index,
            )
            recorded = current.state.verifications[fact_index]
            if (
                recorded.command != command
                or recorded.exit_code != exit_code
                or recorded.at != at
                or recorded.log != log_relpath
            ):
                raise KernelError(
                    code="workspace-invalid",
                    message=(
                        "recorded verification fact no longer matches the "
                        "completed attempt"
                    ),
                    hint="restore the append-only verification history and retry",
                )
        except KernelError as error:
            result = write_failure(error, context.diagnostics)
            assert result.error is not None
            result = replace(
                result,
                error=replace(
                    result.error,
                    details={
                        **result.error.details,
                        **_verification_attempt_details(
                            context.feature,
                            resolved_scope,
                            command,
                            exit_code,
                            log_relpath,
                            recorded=True,
                            revision=commit.revision,
                            fact_index=fact_index,
                        ),
                    },
                ),
            )
        else:
            verification = {
                **fact,
                "fact_index": fact_index,
                "status": assessment.status,
            }
            if exit_code == 0 and assessment.status == "fresh":
                result = _mutation_success(
                    context,
                    command_name="verify",
                    revision=commit.revision,
                    wrote=commit.wrote,
                    extra={"verification": verification},
                )
            else:
                result = HeddleResult.failure(
                    HeddleError(
                        code="verification-failed",
                        message=(
                            f"verification for {resolved_scope} produced "
                            f"unusable evidence ({assessment.status})"
                        ),
                        hint=(
                            f"inspect {log_relpath}, repair the check or command, "
                            f"then retry verification for {resolved_scope}"
                        ),
                        details=_verification_attempt_details(
                            context.feature,
                            resolved_scope,
                            command,
                            exit_code,
                            log_relpath,
                            recorded=True,
                            revision=commit.revision,
                            fact_index=fact_index,
                            status=assessment.status,
                        ),
                    ),
                    exit_code=ExitCode.FATAL,
                    diagnostics=context.diagnostics,
                    next_actions=(
                        NextAction(
                            action=ops.CommandAction(
                                ops.Verify(resolved_scope, feature=context.feature)
                            ),
                            reason=(
                                f"repair the check or command before retrying "
                                f"verification for {resolved_scope}"
                            ),
                        ),
                    ),
                )
    if result.error is not None and not result.error.details.get("recorded"):
        result = replace(
            result,
            error=replace(
                result.error,
                details={
                    **result.error.details,
                    **_verification_attempt_details(
                        context.feature,
                        resolved_scope,
                        command,
                        exit_code,
                        log_relpath,
                        recorded=False,
                        evidence=fact["evidence"],
                    ),
                },
            ),
            diagnostics=(
                *result.diagnostics,
                Diagnostic(
                    severity=Severity.ADVISORY,
                    code="verification-log-unrecorded",
                    message=(
                        f"verification log retained at {log_relpath}; "
                        "no verification fact was recorded"
                    ),
                    source=log_path.relative_to(context.config.root).as_posix(),
                ),
            ),
        )
    return result


def _verification_attempt_details(
    feature: str,
    scope: str,
    command: str,
    child_exit_code: int,
    log: str,
    *,
    recorded: bool,
    revision: int | None = None,
    fact_index: int | None = None,
    status: str | None = None,
    evidence: Any | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "feature": feature,
        "scope": scope,
        "command": command,
        "child_exit_code": child_exit_code,
        "log": log,
        "recorded": recorded,
    }
    if revision is not None:
        details["revision"] = revision
    if fact_index is not None:
        details["fact_index"] = fact_index
    if status is not None:
        details["status"] = status
    if evidence is not None:
        details["evidence"] = evidence
    return details


def record_session(operation: ops.RecordSession) -> HeddleResult:
    parsed = operation
    entry = {
        key: value
        for key, value in ops.decoded_payload(operation.session).items()
        if value is not None
    }
    context_result = resolve_write_context(parsed.feature, parsed.expect_revision)
    if isinstance(context_result, HeddleResult):
        return context_result
    context = context_result

    # Interactive ``session log`` payloads predate the driver's provenance
    # fields and commonly omit ``stage``.  Bind that omission to the locked
    # snapshot so the next orient can distinguish a resumed stage from a fresh
    # one.  Preserve an explicitly supplied stage and leave historical revision
    # and advancement fields unset when the caller did not observe them.
    entry.setdefault("stage", context.snapshot.stage)

    block = _not_authorized_block(context.snapshot)
    if block is not None:
        return block
    if parsed.dry_run:
        return _dry_run_result(
            context,
            lambda document: append_session(document, entry),
            command_name="session log",
            extra={"session": entry},
        )
    return _commit_result(
        context,
        lambda document: append_session(document, entry),
        command_name="session log",
        extra={"session": entry},
    )


def _mutation_success(
    context: ResolvedSnapshotContext,
    *,
    command_name: str,
    revision: int,
    wrote: bool,
    extra: Mapping[str, Any] | None = None,
) -> HeddleResult:
    data: dict[str, Any] = {
        "feature": context.feature,
        "command": command_name,
        "revision": revision,
        "wrote": wrote,
        "mutation_summary": {"command": command_name, "wrote": wrote},
    }
    if extra:
        data.update(extra)
    return HeddleResult.success(data, diagnostics=context.diagnostics)


def flow_set(operation: ops.FlowSet) -> HeddleResult:
    parsed = operation
    mode = operation.flow
    extra: dict[str, Any] = {}

    def render(document: dict[str, Any]) -> dict[str, Any]:
        pending = pending_flow_change(document)
        current_or_pending_mode = (
            pending.get("to") if pending is not None else document.get("flow")
        )
        if current_or_pending_mode == mode:
            return document
        now = utc_now_minutes()
        supersede_pending_flow_changes(document, at=now)
        if document.get("flow") == mode:
            return document
        flow_change = {
            "from": str(document.get("flow") or FLOW_HITL),
            "to": mode,
            "at": now,
            "stage": str(document.get("stage")),
            "source": "user",
            "status": FLOW_CHANGE_PENDING,
            "applies": "next-boundary",
        }
        document.setdefault("flow_changes", []).append(flow_change)
        extra["flow_change"] = flow_change
        return document

    return _run_state_mutation(
        parsed,
        command_name="flow set",
        render=render,
        extra=extra,
    )


def phase_exit(operation: ops.PhaseExit) -> HeddleResult:
    parsed = operation
    through = operation.through
    if through is not None and through not in STAGES:
        return usage_failure(
            f"unknown phase-exit target {through!r}",
            f"--through must be one of {', '.join(STAGES)}",
        )
    context_result = resolve_write_context(parsed.feature, parsed.expect_revision)
    if isinstance(context_result, HeddleResult):
        return context_result
    context = context_result

    # A state restored behind its current lifecycle stage can be repaired by an
    # explicit user `phase-exit --through <current-stage>` re-grant. It is the
    # sole mutation allowed through not-authorized; it changes no stage and
    # applies no pending flow change. Every other command/path keeps the
    # hard authorization guard.
    is_current_stage_regrant = (
        "not-authorized" in context.snapshot.blocking_conditions
        and through == context.snapshot.stage
    )
    block = _not_authorized_block(context.snapshot)
    if block is not None and not is_current_stage_regrant:
        return block

    extra: dict[str, Any] = {}
    # Derive the grant source and the blocking set this exit is judged
    # against from a SINGLE effective flow — the flow the phase being exited ran
    # under (snapshot.flow). A pending flow change still applies AT this boundary
    # (apply_pending_flow_change, governing the NEXT phase), but it does not
    # retroactively re-flow this exit's authorization. Keeping both under the
    # current flow (a) keeps the grant source honest — a hitl exit is always
    # source=user (a human ran phase-exit; the driver is blocked on
    # awaiting-human-authorization in hitl and never reaches here), an auto exit
    # is source=policy — and (b) closes the self-grant: the model cannot mint a
    # policy crossing INTO auto by parking a pending switch, because crossing a
    # hitl boundary is source=user and auto preconditions (e.g.
    # principles-not-ratified) are enforced at the first auto-governed boundary.
    effective_flow = context.snapshot.flow
    grant_blockers = phase_exit_grant_blockers(context.snapshot)
    # E2 (grant-ladder gap): at the implement boundary with milestones
    # still open, the derived blocking set can be empty while the real exit is
    # refused by _implement_exit_block — leaving a hitl user no way to record
    # a forward grant. A forward --through therefore also downgrades to
    # authorization-only when the implement completeness gate would refuse the
    # exit; eligible boundaries keep the AC-11 exit-with-batch-grant behavior,
    # and auto flow keeps refusing (policy grants require a clean boundary).
    forward_grant = through is not None and STAGES.index(through) > STAGES.index(
        context.snapshot.stage
    )
    authorization_only = (
        effective_flow == FLOW_HITL
        and forward_grant
        and (
            bool(grant_blockers)
            or _implement_exit_block(context, context.snapshot.stage) is not None
        )
    )
    if authorization_only:
        extra["authorization_only"] = True

    def render(document: dict[str, Any]) -> dict[str, Any]:
        next_document = (
            apply_authorization_grant(document, through=through)
            if authorization_only and through is not None
            else apply_phase_exit(document, through=through)
        )
        if next_document != document:
            now = utc_now_minutes()
            stage_advanced = next_document.get("stage") != document.get("stage")
            if stage_advanced:
                boundary = str(document.get("stage"))
                if boundary in PHASE_BOUNDARIES:
                    from heddle.runtime.review_assignments import (
                        build_boundary_acceptance,
                    )

                    receipt = build_boundary_acceptance(
                        context.config,
                        context.snapshot,
                        document,
                        boundary=boundary,
                        scope=None,
                        stage_after=str(next_document.get("stage")),
                        accepted_at=now,
                    )
                    if receipt is not None:
                        next_document["review_assignments"]["acceptances"].append(
                            receipt
                        )
                apply_pending_flow_change(next_document, at=now)
            append_authorization_grant(
                next_document,
                through=str(next_document.get("authorized_through")),
                source=(
                    "policy"
                    if stage_advanced and effective_flow == FLOW_AUTO
                    else "user"
                ),
                at=now,
            )
        extra["phase_exit_summary"] = _phase_exit_summary(
            context, document, next_document
        )
        extra.update(_phase_exit_affected_facts(next_document))
        return next_document

    try:
        transition = _phase_exit_transition(
            context,
            through,
            authorization_only=authorization_only,
        )
    except Conflict as error:
        return conflict_failure(error)
    except KernelError as error:
        return write_failure(error, context.diagnostics)

    # entering `complete` only advances/authorizes — close
    # validation and the lifecycle stamp belong to qualifying Phase 9
    # the explicit feature complete operation, not this boundary.
    if not is_current_stage_regrant and not authorization_only:
        if transition.would_write:
            if grant_blockers:
                return blocking_failure(context.snapshot, code=grant_blockers[0])
            implement_block = _implement_exit_block(context, transition.before_stage)
            if implement_block is not None:
                return implement_block
        evidence_block = _readiness_boundary_block(context)
        if evidence_block is not None:
            return evidence_block

    if not parsed.dry_run:
        try:
            commit = commit_state(
                context.state_path,
                expect_revision=context.snapshot.state.revision,
                transform=render,
            )
        except Conflict as error:
            return conflict_failure(error)
        except KernelError as error:
            return write_failure(error, context.diagnostics)

        data = {
            "feature": context.feature,
            "command": "phase-exit",
            "revision": commit.revision,
            "wrote": commit.wrote,
            "mutation_summary": {
                "command": "phase-exit",
                "wrote": commit.wrote,
            },
        }
        data.update(extra)
        return _post_write_result(
            context,
            data,
            committed_revision=commit.revision,
        )

    return _dry_run_result(
        context,
        render,
        command_name="phase-exit",
        extra=extra,
        factual_projection=True,
        projected_fact_keys=("stage", "authorized_through"),
    )


def preview_state_write(
    state_path: Path,
    *,
    expect_revision: int,
    transform: Callable[[dict[str, Any]], dict[str, Any]],
) -> StateCommit:
    with state_recording_lock(state_path):
        document = _load_expected_state(state_path, expect_revision)
        if document["completion"] is not None:
            raise accepted_state_error(document["feature"])
        new_document = transform(deepcopy(document))
        wrote = new_document != document
        if wrote:
            new_document["revision"] = expect_revision + 1
            new_document["updated"] = utc_now_minutes()
        validate_state_transition(document, new_document)
        validate_retained_transition(document, new_document)
        parse_state_document(new_document, source=state_path)
    return StateCommit(
        revision=expect_revision + 1 if wrote else expect_revision,
        wrote=wrote,
    )


def _phase_exit_transition(
    context: ResolvedSnapshotContext,
    through: str | None,
    *,
    authorization_only: bool = False,
) -> _PhaseExitTransition:
    document = _load_expected_state(context.state_path, context.snapshot.state.revision)
    next_document = (
        apply_authorization_grant(deepcopy(document), through=through)
        if authorization_only and through is not None
        else apply_phase_exit(deepcopy(document), through=through)
    )
    return _PhaseExitTransition(
        before_stage=str(document.get("stage")),
        after_stage=str(next_document.get("stage")),
        would_write=next_document != document,
    )


def _phase_exit_summary(
    context: ResolvedSnapshotContext,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    stage_changed = before.get("stage") != after.get("stage")
    authorization_changed = before.get("authorized_through") != after.get(
        "authorized_through"
    )
    outcome = (
        "stage-advanced"
        if stage_changed
        else "authorization-only"
        if authorization_changed
        else "unchanged"
    )
    return {
        "outcome": outcome,
        "advanced": stage_changed,
        "authorization_changed": authorization_changed,
        "what_was_produced": (
            f"advanced from {before.get('stage')} to {after.get('stage')}"
            if stage_changed
            else "updated the authorization ceiling"
            if authorization_changed
            else "no operational state change"
        ),
        "artifacts": [
            context.snapshot.spec_path,
            f"{context.config.layout.plans}/{context.feature}/plan.md",
        ],
        "open_questions_or_decisions": open_decision_refs(before),
        "stage_before": before.get("stage"),
        "stage_after": after.get("stage"),
        "authorized_through_before": before.get("authorized_through"),
        "authorized_through_after": after.get("authorized_through"),
        "exited_stage": before.get("stage"),
        "next_stage": after.get("stage") or before.get("stage"),
    }


def _phase_exit_affected_facts(document: dict[str, Any]) -> dict[str, str]:
    return {
        "stage": str(document.get("stage")),
        "authorized_through": str(document.get("authorized_through")),
    }


type _StateMutationOperation = (
    ops.CommandsSet
    | ops.CommandsUnset
    | ops.MilestoneAdvance
    | ops.TaskAdd
    | ops.TaskCurrent
    | ops.TaskDone
    | ops.FlowSet
    | ops.FeatureInputsSet
)


def _run_state_mutation(
    parsed: _StateMutationOperation,
    *,
    command_name: str,
    render: Callable[[dict[str, Any]], dict[str, Any]],
    extra: Mapping[str, Any] | None = None,
    precheck: Callable[[ResolvedSnapshotContext], HeddleResult | None] | None = None,
) -> HeddleResult:
    context_result = resolve_write_context(parsed.feature, parsed.expect_revision)
    if isinstance(context_result, HeddleResult):
        return context_result
    context = context_result
    block = _not_authorized_block(context.snapshot)
    if block is not None:
        return block
    if precheck is not None:
        precheck_block = precheck(context)
        if precheck_block is not None:
            return precheck_block
    # FlowSet and FeatureInputsSet have no dry-run option.
    if getattr(parsed, "dry_run", False):
        return _dry_run_result(context, render, command_name=command_name, extra=extra)
    return _commit_result(context, render, command_name=command_name, extra=extra)


def run_state_mutation(
    parsed: _StateMutationOperation,
    *,
    command_name: str,
    render: Callable[[dict[str, Any]], dict[str, Any]],
    extra: Mapping[str, Any] | None = None,
    precheck: Callable[[ResolvedSnapshotContext], HeddleResult | None] | None = None,
) -> HeddleResult:
    """Public CAS mutation seam shared by focused runtime command modules."""
    return _run_state_mutation(
        parsed,
        command_name=command_name,
        render=render,
        extra=extra,
        precheck=precheck,
    )


def _commit_result(
    context: ResolvedSnapshotContext,
    render: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    command_name: str,
    extra: Mapping[str, Any] | None = None,
    post_write_guidance: bool = False,
) -> HeddleResult:
    try:
        commit = commit_state(
            context.state_path,
            expect_revision=context.snapshot.state.revision,
            transform=render,
        )
    except Conflict as error:
        result = conflict_failure(error)
    except KernelError as error:
        result = write_failure(error, context.diagnostics)
    else:
        result = _mutation_success(
            context,
            command_name=command_name,
            revision=commit.revision,
            wrote=commit.wrote,
            extra=extra,
        )
        if post_write_guidance:
            assert result.data is not None
            result = _post_write_result(
                context,
                dict(result.data),
                committed_revision=commit.revision,
            )
    return result


def _post_write_result(
    context: ResolvedSnapshotContext,
    data: dict[str, Any],
    *,
    committed_revision: int,
) -> HeddleResult:
    """Attach fresh guidance without rewriting this invocation's mutation facts."""
    try:
        observed = resolve_snapshot(context.config, context.feature)
        assessment = assess_current_readiness(
            context.config,
            observed,
            purpose="transition-result",
        )
    except KernelError as error:
        diagnostic = Diagnostic(
            severity=Severity.ADVISORY,
            code="transition-guidance-unavailable",
            message=(
                f"revision {committed_revision} was committed, but fresh guidance "
                f"could not be assessed: {error.message}"
            ),
            source=context.state_path.as_posix(),
        )
        return HeddleResult.success(
            data,
            diagnostics=(*context.diagnostics, diagnostic),
            next_actions=(
                NextAction(
                    ops.CommandAction(ops.Status(feature=context.feature)),
                    "re-read current state before taking another workflow action",
                ),
            ),
            exit_code=ExitCode.ADVISORY,
        )

    data["guidance_observation"] = {
        "revision": observed.state.revision,
        "stage": observed.stage,
        "newer_than_committed": observed.state.revision != committed_revision,
        "assessment_identity": assessment.observation_identity,
    }
    return HeddleResult.success(
        data,
        diagnostics=context.diagnostics,
        next_actions=_bind_action_revisions(
            assessment.next_actions, observed.state.revision
        ),
    )


def _bind_action_revisions(
    actions: tuple[NextAction, ...], revision: int
) -> tuple[NextAction, ...]:
    bound: list[NextAction] = []
    for item in actions:
        action = item.action
        if isinstance(action, ops.CommandAction):
            operation: Any = action.operation
            fields = vars(operation)
            if "expect_revision" in fields and fields["expect_revision"] is None:
                action = ops.CommandAction(replace(operation, expect_revision=revision))
        bound.append(NextAction(action, item.reason))
    return tuple(bound)


def _readiness_boundary_block(
    context: ResolvedSnapshotContext, *, boundary: Boundary | None = None
) -> HeddleResult | None:
    try:
        assessment = assess_current_readiness(
            context.config,
            context.snapshot,
            boundary=boundary,
            purpose="transition",
        )
    except KernelError as error:
        return readiness_failure(error, context.diagnostics)
    return readiness_status_block(assessment, context.diagnostics)


def _dry_run_result(
    context: ResolvedSnapshotContext,
    render: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    command_name: str,
    extra: Mapping[str, Any] | None = None,
    factual_projection: bool = False,
    projected_fact_keys: tuple[str, ...] = (),
) -> HeddleResult:
    try:
        preview = preview_state_write(
            context.state_path,
            expect_revision=context.snapshot.state.revision,
            transform=render,
        )
    except Conflict as error:
        return conflict_failure(error)
    except KernelError as error:
        return write_failure(error, context.diagnostics)

    data = {
        "feature": context.feature,
        "command": command_name,
        "dry_run": True,
        "revision": (
            context.snapshot.state.revision if factual_projection else preview.revision
        ),
        "wrote": False,
        "mutation_summary": {
            "command": command_name,
            "would_write": preview.wrote,
        },
    }
    if factual_projection:
        data["projected_revision"] = preview.revision
    if extra:
        data.update(extra)
    if projected_fact_keys:
        actual = {
            "stage": context.snapshot.stage,
            "authorized_through": context.snapshot.state.authorized_through,
        }
        for key in projected_fact_keys:
            if key in data:
                data[f"projected_{key}"] = data.pop(key)
            if key in actual:
                data[key] = actual[key]
    return HeddleResult.success(data, diagnostics=context.diagnostics)


def _not_authorized_block(snapshot: FeatureSnapshot) -> HeddleResult | None:
    if "not-authorized" not in snapshot.blocking_conditions:
        return None
    return blocking_failure(snapshot, code="not-authorized")


def _implement_exit_block(
    context: ResolvedSnapshotContext, before_stage: str
) -> HeddleResult | None:
    """The boundary rule: exiting ``implement`` requires every milestone
    ``done``. The ``blocking_conditions`` only flags
    *inconsistent done* milestones (and gate-not-converged is itself gated on
    all-done), so phase-exit adds this completeness gate. The precise sub-reason
    reuses the ``milestone advance`` guard vocabulary; an empty Tier 1 skeleton
    is vacuously done (§4.2)."""
    if before_stage != "implement":
        return None
    document = _load_expected_state(context.state_path, context.snapshot.state.revision)
    milestones = document.get("milestones", [])
    if all(milestone.get("status") == "done" for milestone in milestones):
        return None
    current = context.snapshot.current_milestone
    if current is not None:
        open_task = next(
            (task for task in current.tasks if task.status != "done"),
            None,
        )
        if open_task is not None:
            return _implement_exit_failure(
                code="task-incomplete",
                message=(
                    "cannot exit implement while the current milestone has open tasks"
                ),
                hint="complete the current milestone tasks before exiting implement",
                action=ops.SessionAction(context.feature, "stage-work"),
                reason=f"complete task {open_task.id} before exiting implement",
            )
        if not current.tasks:
            return _implement_exit_failure(
                code="task-incomplete",
                message="cannot exit implement before the current milestone has tasks",
                hint="expand tasks for the current milestone before exiting implement",
                action=ops.SessionAction(context.feature, "task-expansion"),
                reason=f"expand tasks for {current.id} before exiting implement",
            )
    return _implement_exit_failure(
        code="milestone-out-of-sequence",
        message="cannot exit implement while milestones remain to advance",
        hint="run heddle milestone advance until every milestone is done",
        action=ops.CommandAction(ops.MilestoneAdvance(feature=context.feature)),
        reason="complete the remaining milestones before exiting implement",
    )


def _implement_exit_failure(
    *,
    code: str,
    message: str,
    hint: str,
    action: ops.Action,
    reason: str,
) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code=code, message=message, hint=hint),
        exit_code=ExitCode.FATAL,
        next_actions=(NextAction(action=action, reason=reason),),
    )


def _read_session_entry(from_file: str | None) -> dict[str, Any] | HeddleResult:
    if from_file == "-":
        text = sys.stdin.read()
    elif from_file is not None:
        path = Path(from_file)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            return usage_failure(
                f"cannot read session payload {from_file!r}: {error}",
                "provide a readable UTF-8 YAML payload",
            )
    else:
        return usage_failure(
            "session log requires --from-file",
            "provide a structured YAML session payload",
        )
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return usage_failure(
            f"malformed session YAML: {error}",
            "fix the YAML payload and retry",
        )
    if not isinstance(payload, dict):
        return usage_failure(
            "session payload must be a mapping",
            "shape the payload with the seven session fields",
        )
    required_strings = ("started_at", "ended_at", "key_context", "next_steps")
    required_lists = ("completed", "started", "blockers")
    for field in required_strings:
        if not isinstance(payload.get(field), str):
            return usage_failure(
                f"session field {field!r} must be a string",
                "provide all required session string fields",
            )
    for field in required_lists:
        value = payload.get(field)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            return usage_failure(
                f"session field {field!r} must be a list of strings",
                "provide completed, started, and blockers as YAML lists",
            )
    for field, expected_type in SESSION_PROGRESS_FIELD_TYPES.items():
        if field not in payload:
            continue
        value = payload[field]
        if expected_type is int:
            valid = isinstance(value, int) and not isinstance(value, bool)
        else:
            valid = isinstance(value, expected_type)
        if not valid:
            return usage_failure(
                f"session field {field!r} must be a {expected_type.__name__}",
                "provide optional session marker fields with the expected types",
            )
    entry = {
        "started_at": payload["started_at"],
        "ended_at": payload["ended_at"],
        "completed": payload["completed"],
        "started": payload["started"],
        "key_context": payload["key_context"],
        "next_steps": payload["next_steps"],
        "blockers": payload["blockers"],
    }
    for field in SESSION_PROGRESS_KEYS:
        if field in payload:
            entry[field] = payload[field]
    return entry


def _load_expected_state(state_path: Path, expect_revision: int) -> dict[str, Any]:
    document = read_state_document(state_path)
    revision = document.get("revision")
    if revision != expect_revision:
        raise Conflict(
            f"{state_path}: expected revision {expect_revision}, found {revision!r}"
        )
    return document


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def emit_result(result: HeddleResult, json_mode: bool) -> int:
    """Public result-emission seam for focused runtime command modules."""
    return _emit(result, json_mode)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        error = result.error
        repairs = error.details.get("repairs")
        if (
            isinstance(repairs, list)
            and repairs
            and all(isinstance(repair, dict) for repair in repairs)
        ):
            suffix = "repair" if len(repairs) == 1 else "repairs"
            print(
                f"heddle: error[{error.code}]: completion preflight found "
                f"{len(repairs)} {suffix}",
                file=sys.stderr,
            )
            for repair in repairs:
                print(
                    f"  [{repair.get('check')}/{repair.get('cause')}] "
                    f"{repair.get('path')}: {repair.get('message')}",
                    file=sys.stderr,
                )
                print(f"    hint: {repair.get('hint')}", file=sys.stderr)
        else:
            print(f"heddle: error[{error.code}]: {error.message}", file=sys.stderr)
            print(f"  hint: {error.hint}", file=sys.stderr)
        for action in result.next_actions:
            print(f"  next: {action.command} - {action.reason}", file=sys.stderr)
    else:
        data = result.data or {}
        command = data.get("command", "write")
        feature = data.get("feature")
        dry_run = data.get("dry_run") is True
        prefix = f"{command}{' preview' if dry_run else ''} [{feature}]"
        phase = data.get("phase_exit_summary")
        milestone = data.get("milestone_advance_summary")
        if isinstance(phase, dict):
            stage_after = (
                data.get("projected_stage") if dry_run else phase.get("stage_after")
            )
            authorized_after = (
                data.get("projected_authorized_through")
                if dry_run
                else phase.get("authorized_through_after")
            )
            effect = (
                f"stage {phase.get('stage_before')} -> {stage_after}; "
                "authorization "
                f"{phase.get('authorized_through_before')} -> {authorized_after}"
            )
        elif isinstance(milestone, dict):
            effect = str(milestone.get("reason"))
        else:
            effect = f"revision {data.get('revision')}"
        if dry_run and "projected_revision" in data:
            would_write = data.get("mutation_summary", {}).get("would_write")
            suffix = (
                f"revision {data.get('revision')} "
                f"(projected {data.get('projected_revision')}); "
                f"would write {str(would_write).lower()}"
            )
        else:
            suffix = (
                f"revision {data.get('revision')}; "
                f"wrote {str(data.get('wrote')).lower()}"
            )
        if isinstance(phase, dict) or isinstance(milestone, dict):
            print(f"{prefix}: {effect}; {suffix}")
        else:
            print(f"{command} [{feature}] revision {data.get('revision')}")
        for action in result.next_actions:
            print(f"  next: {action.command} - {action.reason}")
    for diagnostic in result.diagnostics:
        print(f"note: {diagnostic.code}: {diagnostic.message}", file=sys.stderr)


# External adapters parse once and render the original application result.
def run_milestone_advance(args: list[str], json_mode: bool) -> int:
    parsed, failure = parse_common(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    return _emit(
        application.execute(
            ops.MilestoneAdvance(
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def run_commands_set(args: list[str], json_mode: bool) -> int:
    parsed, key, command, failure = parse_commands_set(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None and key is not None and command is not None
    return _emit(
        application.execute(
            ops.CommandsSet(
                key,
                command,
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def run_commands_unset(args: list[str], json_mode: bool) -> int:
    parsed, key, failure = parse_commands_unset(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None and key is not None
    return _emit(
        application.execute(
            ops.CommandsUnset(
                key,
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def _milestone_input(payload: dict[str, Any]) -> ops.MilestoneInput:
    return ops.MilestoneInput(
        title=payload["title"],
        complexity=payload["complexity"],
        estimated_hours=tuple(payload["estimated_hours"])
        if "estimated_hours" in payload
        else None,
        verification=ops.VerificationInput(**payload["verification"]),
        satisfies=tuple(payload.get("satisfies", ())),
        depends_on=tuple(payload.get("depends_on", ())),
        owns=tuple(payload.get("owns", ())),
    )


def run_milestone_add(args: list[str], json_mode: bool) -> int:
    parsed, path, failure = parse_milestone_add(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    payload = _read_milestone_payload(path, command_name="milestone add")
    if isinstance(payload, HeddleResult):
        return _emit(payload, json_mode)
    failure = validate_milestone_payload(payload, require_all=True, require_hours=False)
    if failure is not None:
        return _emit(failure, json_mode)
    return _emit(
        application.execute(
            ops.MilestoneAdd(
                _milestone_input(payload),
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def run_milestone_edit(args: list[str], json_mode: bool) -> int:
    parsed, ref, path, failure = parse_milestone_edit(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None and ref is not None
    payload = _read_milestone_payload(path, command_name="milestone edit")
    if isinstance(payload, HeddleResult):
        return _emit(payload, json_mode)
    failure = validate_milestone_payload(payload, require_all=False)
    if failure is not None:
        return _emit(failure, json_mode)
    values = dict(payload)
    for key in ("satisfies", "depends_on", "owns", "estimated_hours"):
        if key in values:
            values[key] = tuple(values[key])
    if "verification" in values:
        values["verification"] = ops.VerificationInput(**values["verification"])
    return _emit(
        application.execute(
            ops.MilestoneEdit(
                ref,
                ops.MilestonePatch(**values),
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def run_task(verb: str, args: list[str], json_mode: bool) -> int:
    parsed, positionals, failure = parse_common_with_positionals(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    operation: ops.TaskAdd | ops.TaskCurrent | ops.TaskDone
    if verb == "add" and positionals:
        operation = ops.TaskAdd(
            " ".join(positionals),
            feature=parsed.feature,
            expect_revision=parsed.expect_revision,
            dry_run=parsed.dry_run,
        )
    elif verb in {"current", "done"} and len(positionals) == 1:
        if verb == "current":
            operation = ops.TaskCurrent(
                positionals[0],
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        else:
            context = resolve_write_context(parsed.feature, parsed.expect_revision)
            if isinstance(context, HeddleResult):
                return _emit(context, json_mode)
            current = context.snapshot.current_milestone
            if current is None:
                return _emit(
                    usage_failure(
                        "task done requires a current milestone",
                        "advance a milestone before completing its tasks",
                    ),
                    json_mode,
                )
            operation = ops.TaskDone(
                current.id,
                positionals[0],
                feature=context.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
    else:
        required = "task text" if verb == "add" else "exactly one task reference"
        placeholder = "text" if verb == "add" else "task-ref"
        return _emit(
            usage_failure(
                f"task {verb} requires {required}",
                f"usage: heddle task {verb} <{placeholder}>",
            ),
            json_mode,
        )
    return _emit(application.execute(operation), json_mode)


def run_verify(args: list[str], json_mode: bool) -> int:
    parsed, scope, expected_revision, failure = parse_verify(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    return _emit(
        application.execute(
            ops.Verify(
                scope,
                feature=parsed.feature,
                expect_revision=expected_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def run_session_log(args: list[str], json_mode: bool) -> int:
    parsed, path, failure = parse_session_log(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    entry = _read_session_entry(path)
    if isinstance(entry, HeddleResult):
        return _emit(entry, json_mode)
    for key in ("completed", "started", "blockers"):
        entry[key] = tuple(entry[key])
    return _emit(
        application.execute(
            ops.RecordSession(
                ops.SessionInput(**entry),
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def run_flow_set(args: list[str], json_mode: bool) -> int:
    parsed, mode, failure = parse_flow_set(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None and mode is not None
    return _emit(
        application.execute(
            ops.FlowSet(
                mode,
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
            )
        ),
        json_mode,
    )


def run_phase_exit(args: list[str], json_mode: bool) -> int:
    parsed, through, failure = parse_phase_exit(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    return _emit(
        application.execute(
            ops.PhaseExit(
                through,
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )
