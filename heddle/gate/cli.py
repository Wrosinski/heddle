from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from heddle.contracts.gate_execution import (
    MODEL_FORMAT_ERROR,
    REASONING_EFFORT_DOMAIN_ERROR,
    SANDBOX_DOMAIN_ERROR,
    VALID_GATE_CLIS,
    VALID_OVERRIDE_AXES,
    VALID_REASONING_EFFORTS,
    VALID_SANDBOXES,
)
from heddle.contracts.limits import (
    DEFAULT_SESSION_HARD_TIMEOUT_S,
    DEFAULT_SESSION_INACTIVITY_TIMEOUT_S,
)
from heddle.gate.types import (
    GateExecutionConfig,
    GateInvocationOverrides,
    GateType,
    ResolvedGateInvocation,
)
from heddle.kernel.project_config import KernelError

_DEFAULT_MAX_ITERATIONS = 5
_DEFAULT_TIMEOUT_SECONDS = DEFAULT_SESSION_HARD_TIMEOUT_S
_DEFAULT_INACTIVITY_TIMEOUT_SECONDS = DEFAULT_SESSION_INACTIVITY_TIMEOUT_S
_DEFAULT_POLL_SECONDS = 5.0


@dataclass
class GateArgs:
    gate_name: str
    cli: str = "claude"
    feature: str | None = None
    plan_path: Path | None = None
    spec_path: Path | None = None
    diff_path: Path | None = None
    cli_bin: str | None = None
    workdir: Path | None = None
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    inactivity_timeout_seconds: int = _DEFAULT_INACTIVITY_TIMEOUT_SECONDS
    poll_seconds: float = _DEFAULT_POLL_SECONDS
    term_grace_seconds: float = 10.0
    max_attempts: int = 2
    preserve_success_artifacts: bool = False
    iteration: int = 1
    max_iterations: int | None = _DEFAULT_MAX_ITERATIONS


CLI_FALLBACKS = {
    "claude": GateExecutionConfig(
        cli="claude",
        model="claude-opus-5-5",
        reasoning_effort="xhigh",
        sandbox="read-only-tools",
    ),
    "codex": GateExecutionConfig(
        cli="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        sandbox="danger-full-access",
    ),
}


def resolve_gate_execution(
    gate_type: GateType,
    overrides: GateInvocationOverrides,
) -> ResolvedGateInvocation:
    """Resolve one supported gate lane, with each public axis independent."""
    if overrides.cli is not None and overrides.cli not in VALID_GATE_CLIS:
        raise KernelError(
            code="usage",
            message=f"unsupported gate CLI: {overrides.cli}",
            hint="use claude or codex",
        )
    if overrides.model is not None and (
        not overrides.model or overrides.model != overrides.model.strip()
    ):
        raise KernelError(
            code="usage",
            message=MODEL_FORMAT_ERROR,
            hint="supply the exact provider model ID",
        )
    if (
        overrides.reasoning_effort is not None
        and overrides.reasoning_effort not in VALID_REASONING_EFFORTS
    ):
        raise KernelError(
            code="usage",
            message=REASONING_EFFORT_DOMAIN_ERROR,
            hint="use an exact lowercase reasoning effort",
        )
    cli = gate_type.default_cli if overrides.cli is None else overrides.cli
    if cli not in gate_type.supported_clis:
        supported = ", ".join(gate_type.supported_clis)
        raise KernelError(
            code="usage" if overrides.cli is not None else "workspace-invalid",
            message=(
                f"{gate_type.name} does not support --cli {cli}"
                if overrides.cli is not None
                else f"{gate_type.name} has invalid default CLI {cli}"
            ),
            hint=(
                f"supported runners: {supported}"
                if overrides.cli is not None
                else "fix the registered default CLI before running the gate"
            ),
        )
    fallback = CLI_FALLBACKS.get(cli)
    if fallback is None:
        raise KernelError(
            code="workspace-invalid",
            message=f"{gate_type.name} has no execution fallback for {cli}",
            hint="define one provider fallback before registering the lane",
        )
    matching = tuple(policy for policy in gate_type.lane_policies if policy.cli == cli)
    if len(matching) > 1:
        raise KernelError(
            code="workspace-invalid",
            message=f"{gate_type.name} has duplicate execution policy for {cli}",
            hint="keep exactly one policy entry for each supported lane",
        )
    policy = matching[0] if matching else None
    model = (
        overrides.model
        if overrides.model is not None
        else (policy.model if policy and policy.model is not None else fallback.model)
    )
    effort = (
        overrides.reasoning_effort
        if overrides.reasoning_effort is not None
        else (
            policy.reasoning_effort
            if policy and policy.reasoning_effort is not None
            else fallback.reasoning_effort
        )
    )
    sandbox = (policy.sandbox if policy else None) or fallback.sandbox
    if not model or model != model.strip():
        raise KernelError(
            code="workspace-invalid",
            message=f"registered gate {gate_type.name}/{cli}: {MODEL_FORMAT_ERROR}",
            hint="fix the registered lane or provider fallback before running the gate",
        )
    if effort not in VALID_REASONING_EFFORTS:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"registered gate {gate_type.name}/{cli}: "
                f"{REASONING_EFFORT_DOMAIN_ERROR}"
            ),
            hint="fix the registered lane or provider fallback before running the gate",
        )
    if sandbox not in VALID_SANDBOXES:
        raise KernelError(
            code="workspace-invalid",
            message=SANDBOX_DOMAIN_ERROR,
            hint="fix the registered lane or provider fallback before running the gate",
        )
    explicit_axes = frozenset(
        name for name in VALID_OVERRIDE_AXES if getattr(overrides, name) is not None
    )
    return ResolvedGateInvocation(
        exec_config=GateExecutionConfig(
            cli=cli,
            model=model,
            reasoning_effort=effort,
            sandbox=sandbox,
        ),
        explicit_axes=explicit_axes,
    )
