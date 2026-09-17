"""Runtime envelope diagnostic adapters."""

from __future__ import annotations

from collections.abc import Mapping

from heddle.contracts import operations as ops
from heddle.contracts.operations import CommandAction, Init, ManualAction
from heddle.contracts.result import (
    Conflict,
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.contracts.schemas import BLOCKING_CONDITIONS
from heddle.kernel.model import FeatureSnapshot, derive_next_actions
from heddle.kernel.project_config import (
    MISSING_PROJECT_ROOT,
    ConfigDiagnostic,
    KernelError,
)
from heddle.kernel.state import INCOMPATIBLE_STATE_SCHEMA
from heddle.runtime.identity import attach_runtime_identity


def envelope_diagnostics(
    kernel_diagnostics: tuple[ConfigDiagnostic, ...],
) -> tuple[Diagnostic, ...]:
    return tuple(
        Diagnostic(
            severity=Severity(diagnostic.severity),
            code=diagnostic.code,
            message=diagnostic.message,
        )
        for diagnostic in kernel_diagnostics
    )


def kernel_error_result(
    error: KernelError,
    *,
    exit_codes: Mapping[str, ExitCode],
    diagnostics: tuple[Diagnostic, ...] = (),
    include_feature_switch_action: bool = True,
) -> HeddleResult:
    next_actions: tuple[NextAction, ...] = ()
    if include_feature_switch_action and error.code == "feature-ambiguous":
        next_actions = (
            NextAction(
                action=ManualAction(
                    "Select the active feature with heddle feature switch"
                ),
                reason=(
                    "select the active feature (candidates: "
                    f"{', '.join(error.candidates)})"
                ),
            ),
        )
    elif error.reason == MISSING_PROJECT_ROOT:
        next_actions = missing_project_root_next_actions()
    elif error.code == "workspace-invalid":
        next_actions = schema_remediation_actions(error)
    result = HeddleResult.failure(
        HeddleError(
            code=error.code,
            message=error.message,
            hint=error.hint,
            details=error.details,
        ),
        exit_code=exit_codes.get(error.code, ExitCode.FATAL),
        diagnostics=diagnostics,
        next_actions=next_actions,
    )
    if error.reason == INCOMPATIBLE_STATE_SCHEMA:
        return attach_runtime_identity(result)
    return result


def missing_project_root_next_actions() -> tuple[NextAction, ...]:
    """The first legal move for every project-scoped pre-init failure."""
    return (
        NextAction(
            action=CommandAction(Init(dry_run=True)),
            reason="initialize this Git host before running project-scoped commands",
        ),
    )


def schema_remediation_actions(error: KernelError) -> tuple[NextAction, ...]:
    """Preserve the schema owner's legal remedy without interpreting prose."""
    if error.reason != INCOMPATIBLE_STATE_SCHEMA:
        return ()
    return (
        NextAction(
            action=ManualAction(error.hint),
            reason="start current work without importing incompatible authority",
        ),
    )


_RESOLUTION_EXITS: Mapping[str, ExitCode] = {
    "feature-ambiguous": ExitCode.USAGE,
    "usage": ExitCode.USAGE,
    **{code: ExitCode.FATAL for code in BLOCKING_CONDITIONS},
}


def blocking_failure(
    snapshot: FeatureSnapshot, code: str | None = None
) -> HeddleResult:
    block_code = code or snapshot.blocking_conditions[0]
    return HeddleResult.failure(
        HeddleError(
            code=block_code,
            message=f"operation blocked by {block_code}",
            hint="follow next_actions, then retry the write command",
        ),
        exit_code=ExitCode.FATAL,
        next_actions=derive_next_actions(snapshot),
    )


def write_failure(
    error: KernelError, diagnostics: tuple[Diagnostic, ...] = ()
) -> HeddleResult:
    return kernel_error_result(
        error,
        exit_codes=_RESOLUTION_EXITS,
        diagnostics=diagnostics,
    )


def conflict_failure(error: Conflict) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            code="conflict",
            message=str(error),
            hint="re-read the feature state and retry the command",
        ),
        exit_code=ExitCode.CONFLICT,
        next_actions=(
            NextAction(
                action=ops.CommandAction(ops.Status()),
                reason="refresh the active feature state before retrying",
            ),
        ),
    )
