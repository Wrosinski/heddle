"""Status and orientation handlers.

The feature-scoped surfaces render exactly ONE kernel snapshot per
invocation: config discovery → resolution →
``resolve_snapshot`` → one current readiness assessment → one human rendering or
one success envelope. ``status --all`` is the portfolio exception:
it fans the same resolver over every workspace under ``layout.plans``
with no selection chain; each healthy row omits review and verification-source
observation. Reporting semantics: observed blocking conditions are payload data
with exit 0; resolution or evidence-integrity failures refuse with a remedy. The
handler owns the code→exit mapping: ``feature-ambiguous``/
``usage`` → 2, ``workspace-invalid`` → 3.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any

import heddle.contracts.result as contracts_result
from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleResult,
    Severity,
)
from heddle.kernel.model import (
    BLOCKING_TRIGGER_ROWS,
    FeatureSnapshot,
    derive_authoring_guidance,
    gate_convergence_phase,
    gate_decision_deferral_active,
    is_terminal,
    list_active_feature_workspaces,
    list_feature_workspaces,
    resolve_snapshot,
)
from heddle.kernel.project_config import (
    KernelError,
    ProjectConfig,
    load_project_config_from_cwd,
)
from heddle.kernel.source_manifest import ObservedPath
from heddle.kernel.state import INCOMPATIBLE_STATE_SCHEMA
from heddle.runtime import application
from heddle.runtime.audit import (
    DecisionJournalAudit,
    surfaced_decision_journal_audit,
)
from heddle.runtime.auto_close import close_obligation, close_obligation_text
from heddle.runtime.cli_args import parse_feature_flag
from heddle.runtime.diagnostics import kernel_error_result
from heddle.runtime.feature_context import (
    ResolveFeatureFailure,
    resolve_snapshot_from_cwd,
)
from heddle.runtime.identity import attach_runtime_identity
from heddle.runtime.output import emit_envelope
from heddle.runtime.readiness import assess_current_readiness, readiness_failure
from heddle.runtime.verification import verification_status_payload

# The handler-owned code→exit mapping for the read surfaces.
_READ_SURFACE_EXITS: Mapping[str, ExitCode] = {
    "feature-ambiguous": ExitCode.USAGE,
    "usage": ExitCode.USAGE,
    "workspace-invalid": ExitCode.FATAL,
}


def run_status(args: list[str], json_mode: bool) -> int:
    all_features = "--all" in args
    rest = [token for token in args if token != "--all"]
    feature, failure = parse_feature_flag(rest, command="status")
    if failure is not None:
        return _emit_failure(failure, json_mode)
    return emit_envelope(
        application.execute(ops.Status(feature, all_features)),
        json_mode,
        _render_read_result,
    )


def status(operation: ops.Status) -> HeddleResult:
    if operation.all_features:
        if operation.feature is not None:
            return _usage_failure(
                "--all and --feature are mutually exclusive",
                "use the portfolio view or select one feature",
            )
        return _portfolio()
    return _read_surface(
        operation.feature,
        _status_payload,
        include_journal_audit=True,
        purpose="status",
    )


def _portfolio() -> HeddleResult:
    try:
        config = load_project_config_from_cwd()
        data, diagnostics = _portfolio_payload(config)
    except KernelError as error:
        return kernel_error_result(error, exit_codes=_READ_SURFACE_EXITS)
    return HeddleResult.success(data, diagnostics=diagnostics)


def _portfolio_payload(
    config: ProjectConfig,
) -> tuple[dict[str, Any], tuple[Diagnostic, ...]]:
    """The portfolio fan-out — the peer of ``_status_payload`` and
    ``_orient_payload``: one healthy row is the snapshot projection without
    ``review_status`` or ``verification_status``; one ``KernelError`` row is
    `{feature, error}` plus one advisory."""
    features: list[dict[str, Any]] = []
    diagnostics: list[Diagnostic] = []
    # sorted() is the defensive layer for the slug-ascending contract; it must
    # not rest solely on the kernel's sort surviving future refactors.
    for slug in sorted(list_feature_workspaces(config)):
        try:
            snapshot = resolve_snapshot(config, slug)
            payload = _status_payload(snapshot)
            payload["close_obligation"] = close_obligation(config)
            audit = surfaced_decision_journal_audit(config, slug, snapshot.state)
            if audit is not None:
                payload["decision_journal_audit"] = audit.status
            diagnostics.extend(_unbacked_journal_diagnostics(slug, audit))
        except KernelError as error:
            code = (
                INCOMPATIBLE_STATE_SCHEMA
                if error.reason == INCOMPATIBLE_STATE_SCHEMA
                else error.code
            )
            features.append(
                {
                    "feature": slug,
                    "error": {"code": code, "message": error.message},
                }
            )
            diagnostics.append(
                Diagnostic(
                    severity=Severity.ADVISORY,
                    code="portfolio-row-error",
                    message=f"{slug}: {code}: {error.message}",
                )
            )
            continue
        features.append(payload)
    return {"features": features}, tuple(diagnostics)


def _render_portfolio(result: HeddleResult) -> None:
    data = result.data or {}
    rows = data.get("features", [])
    print(f"portfolio: {len(rows)} feature(s)")
    for row in rows:
        if "error" in row:
            print(
                f"  {row['feature']} — error[{row['error']['code']}]: "
                f"{row['error']['message']}"
            )
        else:
            print(
                f"  {row['feature']} — stage {row['stage']} "
                f"(authorized through {row['authorized_through']})"
            )
    for diagnostic in result.diagnostics:
        print(f"note: {diagnostic.code}: {diagnostic.message}")


def _usage_failure(message: str, hint: str) -> HeddleResult:
    return HeddleResult.failure(
        contracts_result.HeddleError(code="usage", message=message, hint=hint),
        exit_code=ExitCode.USAGE,
    )


def run_orient(args: list[str], json_mode: bool) -> int:
    feature, failure = parse_feature_flag(args, command="orient")
    if failure is not None:
        return _emit_failure(failure, json_mode)
    return emit_envelope(
        application.execute(ops.Orient(feature)), json_mode, _render_read_result
    )


def orient(operation: ops.Orient) -> HeddleResult:
    pending = _pending_intake_orient(operation.feature)
    if pending is not None:
        result = pending
    else:
        result = _read_surface(
            operation.feature,
            _orient_payload,
            include_journal_audit=False,
            purpose="orient",
        )
    return attach_runtime_identity(result)


def _pending_intake_orient(feature: str | None) -> HeddleResult | None:
    """Route pre-workspace formal admission through the public action protocol."""
    from heddle.runtime import intake

    try:
        config = load_project_config_from_cwd()
        workspaces = set(list_feature_workspaces(config))
        rows: tuple[dict[str, Any], ...]
        if feature is not None:
            if feature in workspaces:
                return None
            path = intake.intake_path(config, feature)
            if not path.is_file():
                return None
            document = intake.read_intake(config, feature)
            rows = (document,) if document["route"] == "heddle" else ()
        else:
            if list_active_feature_workspaces(config):
                return None
            rows = intake.pending_intakes(config)
        if not rows:
            return None
        if len(rows) == 1:
            row = rows[0]
            return HeddleResult.success(
                {
                    "feature": row["feature"],
                    "entry": "pending-intake",
                    "stage": "admission",
                    "revision": row["revision"],
                },
                next_actions=(intake.pending_intake_action(config, row),),
            )
        slugs = tuple(row["feature"] for row in rows)
        return HeddleResult.success(
            {
                "entry": "pending-intake-selection",
                "pending_intakes": list(slugs),
            },
            next_actions=(
                contracts_result.NextAction(
                    ops.DecisionAction(
                        feature="pending-intake",
                        decision_id="pending-intake-selection",
                        choices=slugs,
                        routes_to=tuple(
                            intake.intake_path(config, slug)
                            .relative_to(config.root)
                            .as_posix()
                            for slug in slugs
                        ),
                    ),
                    "Select one prepared intake before continuing admission",
                ),
            ),
        )
    except KernelError as error:
        return kernel_error_result(error, exit_codes=_READ_SURFACE_EXITS)


def _read_surface(
    feature: str | None,
    build_payload: Callable[[FeatureSnapshot], dict[str, Any]],
    *,
    include_journal_audit: bool,
    purpose: str,
) -> HeddleResult:
    resolved = resolve_snapshot_from_cwd(feature)
    if isinstance(resolved, ResolveFeatureFailure):
        return kernel_error_result(
            resolved.error,
            exit_codes=_READ_SURFACE_EXITS,
            diagnostics=resolved.diagnostics,
        )
    snapshot = resolved.snapshot
    if is_terminal(snapshot.state):
        from heddle.runtime.completion import completion_result

        observed = completion_result(resolved, dry_run=True)
        payload = build_payload(snapshot)
        payload["close_obligation"] = close_obligation(resolved.config)
        assert observed.data is not None
        payload.update(
            {
                key: observed.data[key]
                for key in (
                    "accepted",
                    "accepted_at",
                    "revision",
                    "effects",
                    "retained_evidence",
                )
                if key in observed.data
            }
        )
        if "effective_policy" in observed.data:
            payload["effective_policy"] = observed.data["effective_policy"]
        return HeddleResult.success(
            payload,
            diagnostics=observed.diagnostics,
            next_actions=observed.next_actions,
        )
    try:
        captures: dict[str, ObservedPath] = {}
        assignment_projection = None
        if snapshot.state.feature_policy is not None:
            from heddle.runtime.review_assignments import projection

            assignment_projection = projection(
                resolved.config, snapshot, source_observations=captures
            )
        assessment = assess_current_readiness(
            resolved.config,
            snapshot,
            source_observations=captures,
            assignment_projection=assignment_projection,
            purpose=purpose,
        )
        payload = build_payload(snapshot)
        payload["close_obligation"] = close_obligation(resolved.config)
        if not is_terminal(snapshot.state):
            if assignment_projection is not None:
                payload.update(assignment_projection)
            payload["review_status"] = [asdict(row) for row in assessment.reviews]
            payload["verification_status"] = verification_status_payload(
                assessment.verifications
            )
            payload["assessment"] = {
                "purpose": assessment.purpose,
                "original_obligations": [
                    list(reference) for reference in assessment.original_obligations
                ],
                "observation_identity": assessment.observation_identity,
            }
            combined = set(payload["blocking_conditions"]) | set(assessment.blockers)
            ordered = [code for code, _action in BLOCKING_TRIGGER_ROWS]
            payload["blocking_conditions"] = [
                code for code in ordered if code in combined
            ]
            payload["blocking_conditions"].extend(sorted(combined - set(ordered)))
    except KernelError as error:
        return readiness_failure(error, resolved.diagnostics)
    audit = surfaced_decision_journal_audit(
        resolved.config, snapshot.feature, snapshot.state
    )
    if include_journal_audit and audit is not None:
        payload["decision_journal_audit"] = audit.status
    return HeddleResult.success(
        payload,
        diagnostics=resolved.diagnostics
        + _unbacked_journal_diagnostics(snapshot.feature, audit),
        next_actions=assessment.next_actions,
    )


def _unbacked_journal_diagnostics(
    feature: str, audit: DecisionJournalAudit | None
) -> tuple[Diagnostic, ...]:
    if audit is None or not audit.unbacked:
        return ()
    return (
        Diagnostic(
            severity=Severity.ADVISORY,
            code="policy-journal-unbacked",
            message=f"{feature}: policy journal entries "
            "without accepted facts: "
            f"{', '.join(audit.unbacked)}; for nonconflict entries, retry the "
            "identical policy batch with "
            "heddle decisions record-policy "
            f"--feature {feature} --from-file <path>; journal-only conflict "
            "requires an owner ruling: record the owner question with "
            f"heddle decisions add --feature {feature} --input-json <path>",
        ),
    )


def _emit_failure(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_failure)


def _render_failure(result: HeddleResult) -> None:
    error = result.error
    assert error is not None  # failure results always carry an error
    lines = [f"heddle: error[{error.code}]: {error.message}"]
    lines.append(f"  hint: {error.hint}")
    lines.extend(
        f"  next: {action.command} — {action.reason}" for action in result.next_actions
    )
    lines.extend(
        # the human form, on failures too.
        f"note: {diagnostic.code}: {diagnostic.message}"
        for diagnostic in result.diagnostics
    )
    print("\n".join(lines))


def _status_payload(
    snapshot: FeatureSnapshot,
) -> dict[str, Any]:
    """The pinned ``heddle.status/v1`` payload (spec §Payloads,
    transcribed; AC-9 asserts full-payload equality)."""
    payload = _snapshot_base_payload(snapshot) | {
        "spec_path": snapshot.spec_path,
        "owned_paths": list(snapshot.owned_paths),
        "required_gates": list(snapshot.required_gates),
        "gates": _gate_facts_payload(snapshot),
        "artifacts": [
            {"gate": artifact.gate, "scope": artifact.scope, "path": artifact.path}
            for artifact in snapshot.artifacts
        ],
        "verification_commands": dict(snapshot.verification_commands),
    }
    return payload


def _gate_facts_payload(snapshot: FeatureSnapshot) -> list[dict[str, Any]]:
    """Serialize the durable gate authority facts used by status consumers."""
    facts: list[dict[str, Any]] = []
    for fact in snapshot.state.gates:
        runs: list[dict[str, Any]] = []
        for run in fact.runs:
            payload: dict[str, Any] = {
                "run_id": run.run_id,
                "report_findings": [asdict(finding) for finding in run.report_findings],
                "at": run.at,
                "cli": run.cli,
                "artifact": run.artifact,
                "input_hash": run.input_hash,
                "verdict": dict(run.verdict),
                "findings": dict(run.findings),
            }
            if run.review_policy_id is not None:
                payload["review_policy_id"] = run.review_policy_id
            if run.assignment_id is not None:
                payload.update(
                    assignment_id=run.assignment_id,
                    round_number=run.round_number,
                    reviewer_slot=run.reviewer_slot,
                )
            if run.review_basis_hash is not None:
                payload["review_basis_hash"] = run.review_basis_hash
            if run.execution_config is not None:
                payload["execution_config"] = dict(run.execution_config)
            if run.overridden_axes is not None:
                payload["overridden_axes"] = list(run.overridden_axes)
            if run.artifact_sha256 is not None:
                payload["artifact_sha256"] = run.artifact_sha256
            if run.prompt_version is not None:
                payload["prompt_version"] = run.prompt_version
            if run.effective_prompt_sha256 is not None:
                payload["effective_prompt_sha256"] = run.effective_prompt_sha256
            if run.failure_reason is not None:
                payload["failure_reason"] = run.failure_reason
            runs.append(payload)
        facts.append({"gate": fact.gate, "scope": fact.scope, "runs": runs})
    return facts


def _orient_payload(snapshot: FeatureSnapshot) -> dict[str, Any]:
    """The pinned ``heddle.orient/v0`` payload (spec §Payloads; AC-10)."""
    task = snapshot.current_task
    payload = _snapshot_base_payload(snapshot) | {
        "current_task": {"id": task.id, "text": task.text} if task else None,
        "entry": snapshot.entry,
        "next_steps": None if is_terminal(snapshot.state) else snapshot.next_steps,
        "latest_session": _latest_session_payload(snapshot),
    }
    return payload


def _snapshot_base_payload(snapshot: FeatureSnapshot) -> dict[str, Any]:
    authoring_guidance = derive_authoring_guidance(snapshot)
    payload: dict[str, Any] = {
        "feature": snapshot.feature,
        "workspace": snapshot.workspace,
        "stage": snapshot.stage,
        "authorized_through": snapshot.authorized_through,
        "flow": snapshot.flow,
        "authorizations": _authorization_payload(snapshot),
        "current_milestone": (
            snapshot.current_milestone.id if snapshot.current_milestone else None
        ),
        "blocking_conditions": list(snapshot.blocking_conditions),
        "gate_decision_deferral": gate_decision_deferral_active(snapshot),
        "gate_convergence_phase": gate_convergence_phase(
            snapshot.state, snapshot.required_gates
        ),
        "authoring_guidance": (
            asdict(authoring_guidance) if authoring_guidance is not None else None
        ),
    }
    if snapshot.state.completion is not None:
        payload.update(
            {
                "accepted": True,
                "accepted_at": snapshot.state.completion.accepted_at,
                "effects": {"status": "unassessed"},
            }
        )
    return payload


def _latest_session_payload(snapshot: FeatureSnapshot) -> dict[str, Any] | None:
    if not snapshot.state.sessions:
        return None
    session = snapshot.state.sessions[-1]
    return {
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "completed": list(session.completed),
        "started": list(session.started),
        "key_context": session.key_context,
        "next_steps": session.next_steps,
        "blockers": list(session.blockers),
    }


def _authorization_payload(snapshot: FeatureSnapshot) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    for grant in snapshot.authorizations:
        entry: dict[str, Any] = {"through": grant.through}
        if grant.source is not None:
            entry["source"] = grant.source
        if grant.at is not None:
            entry["at"] = grant.at
        grants.append(entry)
    return grants


# Human-line qualifiers for the
# kernel-derived convergence phase — the payload code stays the bare
# §12.1 `gate-not-converged` (fail-closed covers not-yet-run, but the
# bare code reads as a failed round on a fresh stage).
_GATE_PHASE_LABELS: Mapping[str, str] = {
    "not-run": "not yet run",
    "round-in-progress": "round in progress",
    "verdict-fail": "latest verdict: fail",
    "rerun-recommended": "rerun recommended",
}


def _blocking_label(code: str, *, deferral_active: bool, gate_phase: str | None) -> str:
    """The human blocking label: the payload code, plus a display-only
    qualifier for the two codes whose bare name under-describes the state
    (the pending-decisions deferral precedent)."""
    if code == "pending-decisions" and deferral_active:
        return f"{code} (deferred until implement boundary)"
    if code == "gate-not-converged" and gate_phase is not None:
        label = _GATE_PHASE_LABELS.get(gate_phase)
        if label is not None:
            return f"{code} ({label})"
    return code


def _render_read_result(result: HeddleResult) -> None:
    if result.error is not None:
        _render_failure(result)
        return
    data = result.data or {}
    if "features" in data:
        _render_portfolio(result)
        return
    suffix = f" — {data['entry']} entry" if "entry" in data else ""
    print(
        f"{data['feature']} — stage {data['stage']} "
        f"(authorized through {data['authorized_through']}){suffix}"
    )
    if "current_task" in data:
        task = data["current_task"]
        print(f"  task: {task['id']} — {task['text']}" if task else "  task: (none)")
        if data.get("next_steps") is not None:
            print(f"  next_steps: {data['next_steps']}")
    else:
        print(f"  milestone: {data['current_milestone'] or '(none)'}")
    guidance = data.get("authoring_guidance")
    if isinstance(guidance, dict):
        mode = "read-only" if guidance.get("read_only") else "authoring"
        print(
            f"  guidance ({mode}): {guidance['work']}; "
            f"briefing: {guidance['briefing_command']}"
        )
    obligation = data.get("close_obligation")
    if isinstance(obligation, dict):
        print(f"  {close_obligation_text(obligation)}")
    labels = (
        _blocking_label(
            code,
            deferral_active=data.get("gate_decision_deferral", False),
            gate_phase=data.get("gate_convergence_phase"),
        )
        for code in data["blocking_conditions"]
    )
    print(f"  blocking:  {', '.join(labels) or 'none'}")
    for action in result.next_actions:
        print(f"  next: {action.command} — {action.reason}")
    for row in data.get("review_status", []):
        print(
            f"  review {row['gate']}/{row['scope']}/{row['cli']}: "
            f"{row['execution']}, {row['disposition']}, {row['applicability']}"
        )
    for row in data.get("verification_status", []):
        suffix = f"; rerun: {row['action']}" if row["action"] else ""
        if row.get("accepted_degraded"):
            suffix += f"; accepted degraded by {row['accepted_degraded']} (still red)"
        print(f"  verification {row['scope']}: {row['freshness']}{suffix}")
    for diagnostic in result.diagnostics:
        print(f"note: {diagnostic.code}: {diagnostic.message}")
