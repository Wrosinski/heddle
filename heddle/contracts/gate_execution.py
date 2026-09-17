"""Shared gate-execution value domains and public validation messages."""

from __future__ import annotations

VALID_GATE_CLIS = frozenset({"claude", "codex"})
VALID_OVERRIDE_AXES = frozenset({"cli", "model", "reasoning_effort"})
VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
VALID_SANDBOXES = frozenset({"read-only-tools", "danger-full-access"})

CLI_DOMAIN_ERROR = "--cli must be one of claude, codex"
MODEL_FORMAT_ERROR = "--model must be non-empty with no leading or trailing whitespace"
REASONING_EFFORT_DOMAIN_ERROR = (
    "--reasoning-effort must be one of low, medium, high, xhigh"
)
SANDBOX_DOMAIN_ERROR = "gate sandbox must be one of read-only-tools, danger-full-access"
