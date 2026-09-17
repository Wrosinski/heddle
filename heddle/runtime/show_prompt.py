"""``heddle show-prompt`` — render a gate's guidelines prompt (a pure read).

The handler resolves the gate from the engine registry (through the sanctioned
``heddle.gate.entry`` API), resolves the repo root — and, when
``--feature`` is given, the feature snapshot — then renders the gate's
reviewer-facing guidelines (every ``[partial-*]`` block spliced inline,
``[var]`` tokens filled) to stdout. An interactive Claude Code or Codex session
can then run ``heddle show-prompt <gate>`` and read the exact guidelines a gate
would follow, without launching the gate. It renders and emits only: no gate
state is touched (no run fact, no cache, no decisions), so this stays a pure
read surface — the runtime write-seam allowlist gains no entry.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from heddle.contracts import operations as ops
from heddle.contracts.result import ExitCode, HeddleError, HeddleResult
from heddle.gate import entry
from heddle.gate.cli import resolve_gate_execution
from heddle.gate.preparation import input_contract_for
from heddle.gate.prompt import resolve_gate_prompt
from heddle.gate.registry import GATES
from heddle.gate.types import GateContext, GateInvocationOverrides, GateType
from heddle.kernel.model import is_terminal
from heddle.kernel.project_config import KernelError, load_project_config_from_cwd
from heddle.runtime.cli_args import parse_gate_options
from heddle.runtime.diagnostics import envelope_diagnostics, kernel_error_result
from heddle.runtime.feature_context import (
    ResolveFeatureFailure,
    resolve_snapshot_from_cwd,
)
from heddle.runtime.output import emit_envelope

# The handler-owned code→exit mapping for this read surface, mirroring
# run-gate/status/orient.
_RESOLUTION_EXITS: Mapping[str, ExitCode] = {
    "feature-ambiguous": ExitCode.USAGE,
    "usage": ExitCode.USAGE,
    "workspace-invalid": ExitCode.FATAL,
}

_USAGE = (
    "usage: heddle show-prompt <gate> [--feature <slug>] [--cli claude|codex] "
    "[--model <id>] [--reasoning-effort low|medium|high|xhigh] [--json]"
)


@dataclass(frozen=True)
class _Parsed:
    gate: str
    # None when the invoker passed no --cli; resolved to the gate's registry
    # default_cli after the gate-type lookup, matching run-gate.
    cli: str | None
    model: str | None
    reasoning_effort: str | None
    feature: str | None


def run_show_prompt(rest: list[str], json_mode: bool) -> int:
    """Entry point for ``heddle show-prompt`` (the dispatcher routes here)."""
    parsed, failure = _parse(rest)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    from heddle.runtime.application import execute

    return _emit(
        execute(
            ops.ShowPrompt(
                parsed.gate,
                parsed.feature,
                parsed.cli,
                parsed.model,
                parsed.reasoning_effort,
            )
        ),
        json_mode,
    )


def _parse(rest: list[str]) -> tuple[_Parsed | None, HeddleResult | None]:
    # `--json` is already stripped by the generic dispatch path before the
    # handler is reached, so only the positional and value flags remain.
    options, failure = parse_gate_options(rest, usage=_USAGE)
    if failure is not None:
        return None, failure
    assert options is not None
    if len(options.positionals) != 1:
        return None, _usage("show-prompt takes exactly one <gate> argument")
    return (
        _Parsed(
            gate=options.positionals[0],
            cli=options.cli,
            model=options.model,
            reasoning_effort=options.reasoning_effort,
            feature=options.feature,
        ),
        None,
    )


def show_prompt(parsed: ops.ShowPrompt) -> HeddleResult:
    gate_type = GATES.get(parsed.gate)
    if gate_type is None:
        return _usage(
            f"unknown gate {parsed.gate!r}",
            hint="run `heddle help` for the gate surface",
        )
    snapshot = None
    preflight_projection = None
    if parsed.feature is not None:
        # --feature fills the real spec/plan/milestone/tier tokens from the same
        # GateContext the run path builds.
        resolved = resolve_snapshot_from_cwd(parsed.feature)
        if isinstance(resolved, ResolveFeatureFailure):
            return kernel_error_result(
                resolved.error,
                exit_codes=_RESOLUTION_EXITS,
                diagnostics=resolved.diagnostics,
                include_feature_switch_action=False,
            )
        config = resolved.config
        snapshot = resolved.snapshot
        diagnostics = resolved.diagnostics
        if is_terminal(snapshot.state):
            return _usage(
                f"feature {snapshot.feature} is accepted; "
                "there is no active review prompt",
                hint=f"use heddle show-prompt {parsed.gate} for permanent guidelines",
            )
    else:
        # Guidelines-only: no feature needed. Just the repo root (so host
        # overrides and the repo prompt corpus both resolve); the [var] tokens
        # stay literal.
        try:
            config = load_project_config_from_cwd()
        except KernelError as error:
            return kernel_error_result(
                error,
                exit_codes=_RESOLUTION_EXITS,
                include_feature_switch_action=False,
            )
        diagnostics = envelope_diagnostics(config.diagnostics)

    try:
        overrides = GateInvocationOverrides(
            cli=cast(Any, parsed.cli),
            model=parsed.model,
            reasoning_effort=cast(Any, parsed.reasoning_effort),
        )
        if snapshot is not None and snapshot.state.feature_policy is not None:
            from heddle.runtime.review_assignments import resolve_invocation

            invocation = resolve_invocation(snapshot, config, gate_type, overrides)
        else:
            invocation = resolve_gate_execution(gate_type, overrides)
    except KernelError as error:
        return kernel_error_result(
            error,
            exit_codes=_RESOLUTION_EXITS,
            include_feature_switch_action=False,
        )
    cli = invocation.exec_config.cli

    try:
        contract = input_contract_for(gate_type)
        if snapshot is None:
            runnable_facts = (
                contract.requires_spec,
                contract.requires_plan,
                contract.requires_milestone,
                contract.requires_diff,
                bool(contract.context_builders),
            )
            if any(runnable_facts):
                raise KernelError(
                    code="workspace-invalid",
                    message=(
                        f"{parsed.gate} requires feature-bound inputs and has "
                        "no runnable featureless identity"
                    ),
                    hint=(
                        "provide --feature <slug> so every declared gate input "
                        "can be prepared"
                    ),
                )
            context = _featureless_context(gate_type, config.root)
            resolved_prompt = resolve_gate_prompt(
                context,
                gate_type,
                cli=cli,
            )
            prompt_text = resolved_prompt.effective_instructions
            prompt_source = resolved_prompt.template.source
            decision_policy = resolved_prompt.decision_policy
            run_identity = None
            identity_status = "no run identity was prepared"
            prompt_identity = entry.prompt_identity_projection(
                prompt_version=resolved_prompt.prompt_version,
                effective_prompt_sha256=resolved_prompt.effective_prompt_sha256,
            )
        else:
            from heddle.runtime.readiness import select_review_milestone

            context = entry.build_gate_context(
                snapshot,
                config,
                gate_type,
                cli=cli,
                milestone_id=select_review_milestone(config, snapshot, gate_type.name)
                if gate_type.requires_milestone
                else None,
            )
            prepared = entry.prepare_gate_run(
                context,
                gate_type=gate_type,
                invocation=invocation,
            )
            preflight_projection = {
                "status": prepared.preflight.status,
                "fatal_reason": prepared.preflight.fatal_reason,
                "anomalies": list(prepared.preflight.anomalies),
            }
            prompt_text = prepared.prompt.effective_instructions
            prompt_source = prepared.prompt.template.source
            decision_policy = prepared.prompt.decision_policy
            run_identity = prepared.input_hash
            identity_status = "prepared"
            prompt_identity = entry.prompt_identity_projection(
                prompt_version=prepared.prompt_version,
                effective_prompt_sha256=prepared.effective_prompt_sha256,
                review_basis_hash=prepared.review_basis_hash,
                input_hash=prepared.input_hash,
                review_policy_id=None,
                output_contract_version=prepared.output_contract_version,
                output_contract_sha256=prepared.output_contract_sha256,
            )
        policy_data = {}
        if snapshot is not None and snapshot.state.feature_policy is not None:
            from heddle.runtime.review_assignments import projection

            policy_data = projection(config, snapshot)
    except (KernelError, OSError, UnicodeDecodeError) as error:
        typed_error = (
            error
            if isinstance(error, KernelError)
            else KernelError(
                code="workspace-invalid",
                message=f"gate prompt could not be rendered: {error}",
                hint=(
                    "ensure the gate prompt corpus resolves: the installed "
                    f"package ships {parsed.gate}.md as the floor, and a host "
                    f"projection (prompts/{parsed.gate}.md) or a .heddle "
                    "override takes precedence over it"
                ),
            )
        )
        return kernel_error_result(
            typed_error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=diagnostics,
            include_feature_switch_action=False,
        )

    result = HeddleResult.success(
        {
            "gate": parsed.gate,
            "cli": cli,
            "feature": parsed.feature,
            "prompt": prompt_text,
            "prompt_source": prompt_source,
            "decision_policy_source": decision_policy.source,
            "decision_policy_path": str(decision_policy.path),
            "run_identity": run_identity,
            "identity_status": identity_status,
            "execution": entry.execution_projection(invocation),
            "prompt_identity": prompt_identity,
            **policy_data,
            **(
                {"preflight": preflight_projection}
                if preflight_projection is not None
                else {}
            ),
        },
        diagnostics=diagnostics,
    )
    return result


def _featureless_context(gate_type: GateType, repo_root: Path) -> GateContext:
    return entry.featureless_gate_context(gate_type, repo_root)


def _usage(message: str, *, hint: str = _USAGE) -> HeddleResult:
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
        # Clean stdout is the whole point: the rendered prompt and nothing else,
        # so an interactive session reads it verbatim. Diagnostics go to stderr.
        print(data.get("prompt", ""))
    for diagnostic in result.diagnostics:
        print(f"note: {diagnostic.code}: {diagnostic.message}", file=sys.stderr)
