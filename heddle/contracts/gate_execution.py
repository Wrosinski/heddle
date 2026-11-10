"""Shared gate-execution value domains and public validation messages."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

VALID_GATE_CLIS = frozenset({"claude", "codex"})
VALID_OVERRIDE_AXES = frozenset({"cli", "model", "reasoning_effort"})
VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
VALID_SANDBOXES_BY_CLI = {
    "claude": frozenset({"auto"}),
    "codex": frozenset({"danger-full-access"}),
}

CLI_DOMAIN_ERROR = "--cli must be one of claude, codex"
MODEL_FORMAT_ERROR = "--model must be non-empty with no leading or trailing whitespace"
REASONING_EFFORT_DOMAIN_ERROR = (
    "--reasoning-effort must be one of low, medium, high, xhigh"
)
SANDBOX_DOMAIN_ERROR = (
    "gate sandbox must be auto for claude or danger-full-access for codex"
)

# Heddle's explicit model fallback (owner ruling, 2026-11-10). A Claude call on
# a key model that fails for a provider reason is run again on the value
# (model, reasoning effort), and recorded as that model. Claude Code's own
# refusal fallback is switched off for every launch, so this table is the
# only place a review or drive session changes model.
MODEL_FALLBACKS: Mapping[tuple[str, str], tuple[str, str]] = MappingProxyType(
    {("claude", "claude-fable-5-2"): ("claude-opus-5-5", "xhigh")}
)

# Gate failure reasons raised by the provider call itself, where another model
# can succeed. Preflight, reviewed-source and capture-storage failures are not
# here: a different model would meet the same inputs. Nor is
# ``execution-failure``: the runner also reports a missing binary, a launch
# error and its own internal errors with it, and none of those reached a model.
FALLBACK_FAILURE_REASONS = frozenset(
    {
        "model-refusal",
        "model-substituted",
        "unsupported-model",
        "claude-overloaded",
        "exit",
        "budget-exhausted",
        "max-turns",
        "timeout",
        "inactivity-timeout",
        "empty-output",
        "truncated-output",
        "invalid-output-structure",
    }
)


def model_fallback(cli: str, model: str) -> tuple[str, str] | None:
    """The (model, reasoning effort) that reruns a failed call, if any."""
    return MODEL_FALLBACKS.get((cli, model))
