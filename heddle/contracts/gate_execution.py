"""Shared gate-execution value domains and public validation messages."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

VALID_GATE_CLIS = frozenset({"claude", "codex", "antigravity"})
VALID_OVERRIDE_AXES = frozenset({"cli", "model", "reasoning_effort"})
VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
VALID_SANDBOXES_BY_CLI = {
    "claude": frozenset({"auto"}),
    "codex": frozenset({"danger-full-access"}),
    # Each label is the lane CLI's own permission value; agy's is its flag.
    "antigravity": frozenset({"dangerously-skip-permissions"}),
}
# The executable each lane launches; the antigravity lane runs `agy`.
GATE_CLI_BINARIES: Mapping[str, str] = MappingProxyType(
    {"claude": "claude", "codex": "codex", "antigravity": "agy"}
)

CLI_DOMAIN_ERROR = "--cli must be one of claude, codex, antigravity"
MODEL_FORMAT_ERROR = "--model must be non-empty with no leading or trailing whitespace"
REASONING_EFFORT_DOMAIN_ERROR = (
    "--reasoning-effort must be one of low, medium, high, xhigh"
)
SANDBOX_DOMAIN_ERROR = (
    "gate sandbox must be auto for claude, danger-full-access for codex or "
    "dangerously-skip-permissions for antigravity"
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


# Model naming for new selections. An antigravity model is a Gemini 4 base
# name; its effort is the separate reasoning-effort axis. Flash, Lite and
# orchestrator models are refused, and the other lanes refuse Gemini models.
_GEMINI_4_MODEL = re.compile(r"gemini-4-[0-9a-z]+(?:-[0-9a-z]+)*")
_REFUSED_GEMINI_SEGMENT = re.compile(r"(?:^|-)(?:flash|lite|orchestrator)(?:-|$)")
_EFFORT_SUFFIX = re.compile(r"-(?:low|medium|high|xhigh|max)$")


def model_selection_refusal(
    cli: str, model: str, *, effort_axis: str
) -> tuple[str, str] | None:
    """The (message, hint) refusing a newly selected ``model`` on ``cli``.

    Policy confirmation and launch resolution call it; decoding stored history
    never does, so a revision recorded before these rules still loads.
    ``effort_axis`` names the reasoning effort as the caller's surface spells
    it. Returns None for an accepted selection.
    """
    if cli != "antigravity":
        if model.startswith("gemini-"):
            return (
                f"{cli} cannot run the Gemini model {model}",
                "Gemini models run on the antigravity lane; select that lane "
                "for this model",
            )
        return None
    suffix = _EFFORT_SUFFIX.search(model)
    base = model[: suffix.start()] if suffix else model
    if (
        _GEMINI_4_MODEL.fullmatch(base) is None
        or _REFUSED_GEMINI_SEGMENT.search(base) is not None
    ):
        return (
            f"antigravity model {model} is not a Gemini 4 model the lane accepts",
            "use a Gemini 4 base model such as gemini-4-neon; Flash, Lite, "
            "orchestrator and other providers' models are refused",
        )
    if suffix is not None:
        return (
            f"antigravity model {model} carries its reasoning effort in the name",
            f"pass the base model {base} and give the effort separately with "
            f"{effort_axis} (low, medium, high or xhigh)",
        )
    return None
