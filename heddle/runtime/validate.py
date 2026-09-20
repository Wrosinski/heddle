"""`heddle validate` runtime handler for ledger validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.contracts.schemas import STAGES
from heddle.io.git import GitError, path_has_git_history
from heddle.kernel.knowledge import read_plan_facts, read_spec_facts
from heddle.kernel.model import (
    FeatureSnapshot,
    is_terminal,
    list_feature_workspaces,
    resolve_snapshot,
)
from heddle.kernel.project_config import (
    KernelError,
    ProjectConfig,
    feature_state_path,
    load_project_config,
    load_project_config_from_cwd,
)
from heddle.runtime.cli_args import parse_feature_flag
from heddle.runtime.diagnostics import (
    schema_remediation_actions,
)
from heddle.runtime.guardrails import repo_relative_path, run_guardrails
from heddle.runtime.output import emit_envelope
from heddle.runtime.readiness import validate_retained_reviews

LEDGER_DRIFT = "ledger-drift"
WORKSPACE_INVALID = "workspace-invalid"
Check = Callable[["ValidationContext"], list[Diagnostic]]


@dataclass(frozen=True)
class ValidationContext:
    root: Path
    workspace_root: Path
    snapshot: FeatureSnapshot


def _drift_diagnostic(
    severity: Severity, code: str, message: str, fix: str
) -> Diagnostic:
    return Diagnostic(severity, code, f"{message}. Fix: {fix}")


def run_validate(args: list[str], json_mode: bool) -> int:
    feature, failure = parse_feature_flag(args, command="validate")
    if failure is not None:
        return _emit(failure, json_mode)
    from heddle.runtime.application import execute

    return _emit(execute(ops.Validate(feature)), json_mode)


def validate(operation: ops.Validate) -> HeddleResult:
    try:
        config = load_project_config_from_cwd()
        slugs = (
            (operation.feature,)
            if operation.feature is not None
            else list_feature_workspaces(config)
        )
    except KernelError as error:
        return _kernel_failure(error)

    diagnostics: list[Diagnostic] = []
    workspace_rows: list[dict[str, Any]] = []
    schema_actions: list[NextAction] = []

    for slug in slugs:
        try:
            snapshot = resolve_snapshot(config, slug)
        except KernelError as error:
            for action in schema_remediation_actions(error):
                if action not in schema_actions:
                    schema_actions.append(action)
            diagnostics.append(_workspace_invalid(config, slug, error))
            continue
        workspace_rows.append(_workspace_row(snapshot))
        try:
            diagnostics.extend(check_workspace(config.root, snapshot))
        except (OSError, ValueError) as error:
            # The per-workspace boundary catches only
            # KernelError, but a non-KernelError path fault in the cross-plane
            # checks — a null byte reaching Path.resolve() (a ValueError), a
            # symlink-loop OSError — would otherwise abort the whole sweep and
            # every guardrail. Isolate it to this workspace as a fatal so the
            # repository-wide run continues under the collect-then-report
            # contract.
            diagnostics.append(
                _drift_diagnostic(
                    Severity.FATAL,
                    WORKSPACE_INVALID,
                    f"{slug}: cross-plane checks could not complete: {error}",
                    "repair the workspace state.",
                )
            )

    diagnostics.extend(run_guardrails(config.root, config))
    result = _result(
        diagnostics=diagnostics,
        workspaces=workspace_rows,
        schema_actions=tuple(schema_actions),
    )
    return result


def _workspace_row(snapshot: FeatureSnapshot) -> dict[str, Any]:
    return {
        "feature": snapshot.feature,
        "workspace": snapshot.workspace,
        "control_plane": {
            "stage": snapshot.stage,
            "authorized_through": snapshot.authorized_through,
            "milestone_coverage": {
                milestone.id: list(milestone.satisfies)
                for milestone in snapshot.state.milestones
            },
        },
    }


def check_workspace(root: Path, snapshot: FeatureSnapshot) -> tuple[Diagnostic, ...]:
    """The public per-workspace cross-plane checks serve two consumers:
    ``run_validate``'s sweep loop and ``feature start`` self-validation."""
    diagnostics: list[Diagnostic] = []
    context = ValidationContext(
        root=root,
        workspace_root=root / snapshot.workspace,
        snapshot=snapshot,
    )
    checks = (
        (_check_retained_evidence, _check_verification_evidence)
        if is_terminal(snapshot.state)
        else _CHECKS
    )
    for check in checks:
        diagnostics.extend(check(context))
    return tuple(diagnostics)


def _check_verification_evidence(context: ValidationContext) -> list[Diagnostic]:
    try:
        from heddle.runtime.verification import verification_statuses

        verification_statuses(context.root, context.snapshot.state)
    except KernelError as error:
        return [
            Diagnostic(
                Severity.FATAL,
                WORKSPACE_INVALID,
                error.message,
            )
        ]
    return []


def _check_retained_evidence(context: ValidationContext) -> list[Diagnostic]:
    diagnostics = []
    state = context.snapshot.state
    try:
        validate_retained_reviews(context.root, context.snapshot)
    except KernelError as error:
        diagnostics.append(Diagnostic(Severity.FATAL, WORKSPACE_INVALID, error.message))
    logs = {fact.log for fact in state.verifications}
    assert state.completion is not None
    if state.completion.close_suite is not None:
        logs.add(state.completion.close_suite.log)
    missing = sum(not _artifact_exists(context.workspace_root, log) for log in logs)
    if missing:
        diagnostics.append(
            Diagnostic(
                Severity.INFO,
                "historical-logs-optional",
                f"{state.feature}: {missing} historical verification log(s) "
                "not retained; logs are optional after accepted completion",
            )
        )
    return diagnostics


def _state_location(context: ValidationContext) -> str:
    return f"{context.snapshot.workspace}state.yaml"


def _check_spec_ac_references(context: ValidationContext) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    root = context.root
    state = context.snapshot.state
    spec_path = repo_relative_path(root, state.spec)
    spec_ac_ids: set[str] = set()
    spec_readable = False

    if spec_path is None:
        diagnostics.append(
            _drift_diagnostic(
                Severity.FATAL,
                "spec-missing",
                f"{state.feature}: spec path is outside host layout: {state.spec}",
                "restore the spec file or update state.spec.",
            )
        )
    elif not spec_path.is_file():
        diagnostics.append(
            _drift_diagnostic(
                Severity.FATAL,
                "spec-missing",
                f"{state.feature}: spec path is missing: {state.spec}",
                "restore the spec file or update state.spec.",
            )
        )
    else:
        try:
            spec_facts = read_spec_facts(spec_path)
        except KernelError as error:
            diagnostics.append(_knowledge_invalid(state.feature, error))
        else:
            spec_ac_ids = set(spec_facts.ac_ids)
            spec_readable = True

    referenced_ac_ids: set[str] = set()
    state_location = _state_location(context)
    for milestone in state.milestones:
        for ac_id in milestone.satisfies:
            referenced_ac_ids.add(ac_id)
            # Only flag a missing AC when the spec was actually read. An
            # unresolvable/unreadable spec already reported spec-missing /
            # knowledge-invalid; firing ac-missing for every satisfies entry
            # would mislabel the cause (the AC may exist in the unread spec).
            if spec_readable and ac_id not in spec_ac_ids:
                diagnostics.append(
                    _drift_diagnostic(
                        Severity.FATAL,
                        "ac-missing",
                        (
                            f"{state.feature}: {state_location} milestone "
                            f"{milestone.id} references missing AC id {ac_id}"
                        ),
                        (
                            "add the AC to the spec or correct the "
                            "milestone satisfies list."
                        ),
                    )
                )
    for ac_id in sorted(spec_ac_ids - referenced_ac_ids):
        diagnostics.append(
            _drift_diagnostic(
                Severity.ADVISORY,
                "ac-unassigned",
                (f"{state.feature}: spec AC {ac_id} is not assigned to a milestone"),
                ("assign the AC to a milestone or remove it from the spec."),
            )
        )
    return diagnostics


def _check_owned_paths(context: ValidationContext) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    root = context.root
    state = context.snapshot.state
    for milestone in state.milestones:
        for owned in milestone.owns:
            owned_path = repo_relative_path(root, owned)
            if owned_path is not None and owned_path.exists():
                continue
            if owned_path is not None and _tracked_deletion(root, owned):
                # Deletion milestones list their targets in owns
                # so the gate diff stays in scope; once the deletion lands,
                # the entry is a record of sanctioned work, not drift.
                diagnostics.append(
                    _drift_diagnostic(
                        Severity.ADVISORY,
                        "owned-path-deleted",
                        (
                            f"{state.feature}: milestone {milestone.id} owns "
                            f"{owned}, which this feature's work deleted"
                        ),
                        (
                            "expected for a deletion milestone; drop the "
                            "path from the owns list at feature close."
                        ),
                    )
                )
                continue
            if (
                owned_path is not None
                and not (root / owned).is_symlink()
                and milestone.status in {"todo", "current"}
                and STAGES.index(state.stage) <= STAGES.index("implement")
            ):
                diagnostics.append(
                    Diagnostic(
                        Severity.INFO,
                        "owned-path-planned",
                        f"{state.feature}: milestone {milestone.id} plans missing "
                        f"path {owned}; confirm this declaration, then create it "
                        "or revise owns before milestone completion or final review",
                    )
                )
                continue
            diagnostics.append(
                _drift_diagnostic(
                    Severity.FATAL,
                    "owned-path-missing",
                    (
                        f"{state.feature}: milestone {milestone.id} owns "
                        f"missing path {owned}"
                    ),
                    ("create the owned path or update the milestone owns list."),
                )
            )
    return diagnostics


def _tracked_deletion(root: Path, relpath: str) -> bool:
    """A missing owned path that Git ever tracked is a deletion, not a typo.

    No repository history is not deletion evidence; Git faults
    fail validation. Never-tracked paths follow the milestone lifecycle.
    Shared Git I/O owns the subprocess."""
    try:
        return path_has_git_history(root, relpath)
    except GitError as error:
        raise KernelError(
            code="workspace-invalid",
            message=str(error),
            hint=(
                "repair the Git repository and its UTF-8 paths/content, "
                "then retry validation"
            ),
        ) from error


def _check_gate_artifacts(context: ValidationContext) -> list[Diagnostic]:
    state = context.snapshot.state
    if (
        not any(
            run.verdict.get("status") != "error"
            for gate in state.gates
            for run in gate.runs
        )
        and not state.review_assignments.retained
    ):
        return []
    diagnostics: list[Diagnostic] = []
    try:
        validate_retained_reviews(context.root, context.snapshot)
    except KernelError as error:
        diagnostics.append(
            _drift_diagnostic(
                Severity.FATAL,
                "artifact-required-invalid",
                f"{state.feature}: required gate evidence is unavailable or changed",
                error.message,
            )
        )
    for gate in state.gates:
        for run in gate.runs:
            if run.verdict.get("status") == "error":
                continue
            if not _artifact_exists(context.workspace_root, run.artifact):
                diagnostics.append(
                    _drift_diagnostic(
                        Severity.FATAL,
                        "gate-artifact-missing",
                        (
                            f"{state.feature}: gate artifact is missing: "
                            f"{context.workspace_root / run.artifact}"
                        ),
                        "restore the workspace artifact or update the gate run record",
                    )
                )
    return diagnostics


def _check_plan_sections(context: ValidationContext) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    state = context.snapshot.state
    workspace_root = context.workspace_root
    plan_path = workspace_root / "plan.md"
    state_location = _state_location(context)
    state_milestone_ids = {milestone.id for milestone in state.milestones}
    if not plan_path.is_file():
        diagnostics.append(
            _drift_diagnostic(
                Severity.FATAL,
                "plan-missing",
                f"{state.feature}: plan prose is missing: {plan_path}",
                "restore the plan document for this workspace.",
            )
        )
    else:
        try:
            plan_facts = read_plan_facts(plan_path)
        except KernelError as error:
            diagnostics.append(_knowledge_invalid(state.feature, error))
        else:
            section_ids = set(plan_facts.milestone_section_ids)
            for milestone_id in sorted(state_milestone_ids - section_ids):
                diagnostics.append(
                    _drift_diagnostic(
                        Severity.FATAL,
                        "plan-section-missing",
                        (
                            f"{state.feature}: state milestone {milestone_id} "
                            f"in {state_location} has no "
                            f"plan section in {plan_path}"
                        ),
                        ("add the plan section or remove the stale state milestone."),
                    )
                )
            for section_id in sorted(section_ids - state_milestone_ids):
                diagnostics.append(
                    _drift_diagnostic(
                        Severity.ADVISORY,
                        "plan-section-orphan",
                        (
                            f"{state.feature}: plan section {section_id} "
                            f"in {plan_path} has no state milestone in "
                            f"{state_location}"
                        ),
                        ("add the state milestone or remove the orphan section."),
                    )
                )
    return diagnostics


def _check_source_coverage(context: ValidationContext) -> list[Diagnostic]:
    from heddle.runtime.verification import coverage_diagnostics, workflow_control_paths

    try:
        config = load_project_config(context.root)
        state = context.snapshot.state
        diagnostics = list(coverage_diagnostics(config, state))
        controls = workflow_control_paths(config, state)
        dependencies = sorted(
            {
                path
                for milestone in state.milestones
                for path in milestone.owns
                if path in controls.exact
            }
        )
        if dependencies:
            diagnostics.append(
                Diagnostic(
                    Severity.INFO,
                    "explicit-record-dependency",
                    "Explicitly owned workflow records remain strict "
                    "proof dependencies: "
                    + ", ".join(dependencies)
                    + ". Coverage already accounts for them; keep owns only when "
                    "intentionally verifying these bytes.",
                )
            )
        return diagnostics
    except KernelError as error:
        return [Diagnostic(Severity.FATAL, WORKSPACE_INVALID, error.message)]


_CHECKS: tuple[Check, ...] = (
    _check_source_coverage,
    _check_verification_evidence,
    _check_spec_ac_references,
    _check_owned_paths,
    _check_gate_artifacts,
    _check_plan_sections,
)


def _artifact_exists(workspace_root: Path, relpath: str) -> bool:
    artifact = repo_relative_path(workspace_root, relpath)
    return artifact is not None and artifact.is_file()


def _result(
    *,
    diagnostics: list[Diagnostic],
    workspaces: list[dict[str, Any]],
    schema_actions: tuple[NextAction, ...],
) -> HeddleResult:
    fatal = [item for item in diagnostics if item.severity is Severity.FATAL]
    if fatal:
        fatal_codes = {item.code for item in fatal}
        only_workspace_invalid = fatal_codes == {WORKSPACE_INVALID}
        if only_workspace_invalid:
            error = HeddleError(
                code=WORKSPACE_INVALID,
                message=(
                    f"{len(fatal)} workspace(s) could not be validated: "
                    f"{fatal[0].message}"
                ),
                hint=(
                    schema_actions[0].command
                    if schema_actions
                    else "repair the reported invalid workspaces and rerun validate"
                ),
            )
        else:
            error = HeddleError(
                code=LEDGER_DRIFT,
                message=f"{len(fatal)} fatal ledger validation finding(s)",
                hint="repair the reported ledger drift and rerun validate",
            )
        return HeddleResult.failure(
            error,
            exit_code=ExitCode.FATAL,
            diagnostics=tuple(diagnostics),
            next_actions=schema_actions,
        )

    advisory = any(item.severity is Severity.ADVISORY for item in diagnostics)
    exit_code = ExitCode.ADVISORY if advisory else ExitCode.OK
    return HeddleResult.success(
        {"workspaces": workspaces},
        diagnostics=tuple(diagnostics),
        exit_code=exit_code,
    )


def _usage_failure(arg: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            code="usage",
            message=f"unknown validate argument {arg!r}",
            hint="usage: heddle validate [--json]",
        ),
        exit_code=ExitCode.USAGE,
    )


def _kernel_failure(error: KernelError) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            code=error.code,
            message=error.message,
            hint=error.hint,
            details=error.details,
        ),
        exit_code=ExitCode.FATAL,
        next_actions=schema_remediation_actions(error),
    )


def _workspace_invalid(
    config: ProjectConfig, slug: str, error: KernelError
) -> Diagnostic:
    state_path = feature_state_path(config, slug)
    fix = error.hint
    return _drift_diagnostic(
        Severity.FATAL,
        WORKSPACE_INVALID,
        f"{slug}: {state_path}: {error.message}",
        f"{fix}.",
    )


def _knowledge_invalid(slug: str, error: KernelError) -> Diagnostic:
    return _drift_diagnostic(
        Severity.FATAL,
        "knowledge-invalid",
        f"{slug}: {error.message}",
        "repair the referenced knowledge document.",
    )


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        print(f"heddle: error[{result.error.code}]: {result.error.message}")
        print(f"  hint: {result.error.hint}")
    else:
        count = len(result.diagnostics)
        print(f"validate: {count} finding(s)")
    for diagnostic in result.diagnostics:
        print(f"{diagnostic.severity.value}: {diagnostic.code}: {diagnostic.message}")
