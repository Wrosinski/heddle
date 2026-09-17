"""``heddle kickoff`` — render the current stage's briefing.

A pure control-plane read: resolve the active feature, read its kernel snapshot
for the current stage, resolve the stage
briefing asset through the chain, and return the rendered
briefing under the envelope. It stores nothing and is idempotent given the
state. ``orient`` routes a freshly entered stage here so the route renders the
briefing instead of a not-implemented stub.
"""

from __future__ import annotations

import sys
from dataclasses import asdict

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    ExitCode,
    HeddleError,
    HeddleResult,
)
from heddle.kernel.decision_guidance import (
    compose_decision_guidance,
    load_decision_guidance,
)
from heddle.kernel.model import derive_authoring_guidance, is_terminal
from heddle.kernel.project_config import KernelError
from heddle.kernel.resources import resolve_resource
from heddle.runtime import application
from heddle.runtime.auto_close import close_obligation, close_obligation_text
from heddle.runtime.cli_args import parse_feature_flag
from heddle.runtime.diagnostics import kernel_error_result
from heddle.runtime.feature_context import (
    ResolveFeatureFailure,
    resolve_snapshot_from_cwd,
)
from heddle.runtime.output import emit_envelope

# the handler-owned code→exit mapping for the kickoff read surface,
# mirroring status/orient and run-gate's resolution failures.
_RESOLUTION_EXITS = {
    "feature-ambiguous": ExitCode.USAGE,
    "usage": ExitCode.USAGE,
    "workspace-invalid": ExitCode.FATAL,
}


def kickoff(operation: ops.Kickoff) -> HeddleResult:
    resolved = resolve_snapshot_from_cwd(operation.feature)
    if isinstance(resolved, ResolveFeatureFailure):
        return kernel_error_result(
            resolved.error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
        )
    config = resolved.config
    snapshot = resolved.snapshot
    if is_terminal(snapshot.state):
        from heddle.runtime.completion import completion_result

        observed = completion_result(resolved, dry_run=True)
        return HeddleResult.success(
            {
                **dict(observed.data or {}),
                "stage": snapshot.stage,
                "source": "state",
                "briefing": (
                    f"Feature {snapshot.feature} has accepted completion. "
                    "Acceptance is immutable. Follow the reported actions to repair "
                    "pending stamp, archive or cleanup effects.\n"
                ),
                "close_obligation": close_obligation(config),
            },
            diagnostics=observed.diagnostics,
            next_actions=observed.next_actions,
        )

    asset = f"{snapshot.stage}.briefing.md"
    try:
        briefing_path, source = resolve_resource(asset, config)
        briefing = briefing_path.read_text(encoding="utf-8")
        if not briefing.strip():
            raise KernelError(
                code="workspace-invalid",
                message=f"the {asset} stage briefing is blank",
                hint=f"restore the selected {asset} resource before starting a session",
            )
        policy = load_decision_guidance(config)
        briefing = compose_decision_guidance(policy, briefing)
        from heddle.runtime.review_assignments import projection

        policy_data = projection(config, snapshot)
    except KernelError as error:
        return kernel_error_result(
            error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
        )
    except (OSError, UnicodeDecodeError):
        return HeddleResult.failure(
            HeddleError(
                code="workspace-invalid",
                message=f"the {snapshot.stage} stage briefing is unreadable",
                hint=f"check the resolved {asset} resource",
            ),
            exit_code=ExitCode.FATAL,
            diagnostics=resolved.diagnostics,
        )

    authoring_guidance = derive_authoring_guidance(snapshot)
    assert authoring_guidance is not None
    result = HeddleResult.success(
        {
            "feature": resolved.feature,
            "stage": snapshot.stage,
            "source": source,
            "decision_policy_source": policy.source,
            "decision_policy_path": str(policy.path),
            "briefing": briefing,
            "authoring_guidance": asdict(authoring_guidance),
            "close_obligation": close_obligation(config),
            **policy_data,
        },
        diagnostics=resolved.diagnostics,
    )
    return result


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        error = result.error
        print(f"heddle: error[{error.code}]: {error.message}", file=sys.stderr)
        print(f"  hint: {error.hint}", file=sys.stderr)
        for action in result.next_actions:
            print(f"  next: {action.command} — {action.reason}", file=sys.stderr)
    else:
        data = result.data or {}
        briefing = data.get("briefing", "")
        print(briefing, end="")
        if briefing and not briefing.endswith("\n"):
            print()
        obligation = data.get("close_obligation")
        if isinstance(obligation, dict):
            print(close_obligation_text(obligation))
    for diagnostic in result.diagnostics:
        print(f"note: {diagnostic.code}: {diagnostic.message}", file=sys.stderr)


def run_kickoff(args: list[str], json_mode: bool) -> int:
    feature, failure = parse_feature_flag(args, command="kickoff")
    if failure is not None:
        return _emit(failure, json_mode)
    return _emit(application.execute(ops.Kickoff(feature)), json_mode)
