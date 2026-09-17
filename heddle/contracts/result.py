"""The result envelope and its constituents.

Every dispatcher-served command returns one ``HeddleResult``; JSON mode
serializes it via ``to_envelope`` into exactly the five wire keys
``{ok, schema_version, data | error, diagnostics, next_actions}``.
Values here encode ratified decisions — each carries its citation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import PurePosixPath
from typing import Any

from heddle.contracts.operations import Action, action_command, action_payload

# Decision Log (schema-identifier spellings): heddle.<artifact>/v<N>.
ENVELOPE_SCHEMA_VERSION = "heddle.envelope/v2"


class Conflict(Exception):
    """A compare-and-swap or create-only publication lost a race."""


class ExitCode(IntEnum):
    """Process exit codes (workflow-model §12.3)."""

    OK = 0  # success
    INTERNAL = 1  # internal error
    USAGE = 2  # usage error
    FATAL = 3  # contract violation / blocked operation
    ADVISORY = 4  # advisory findings only
    CONFLICT = 5  # CAS conflict (retryable)


class Severity(StrEnum):
    """Diagnostic severities (workflow-model §12.3)."""

    FATAL = "fatal"
    ADVISORY = "advisory"
    INFO = "info"


# the blocking conditions (workflow-model §12.1) plus
# usage/internal/conflict/feature-ambiguous, not-implemented,
# the non-blocking ledger-drift validation code, and the
# workspace-exists collision code (a healthy workspace already
# occupies the slug; neither usage-2 nor retryable conflict-5).
ERROR_CODES: frozenset[str] = frozenset(
    {
        "not-authorized",
        "gate-not-converged",
        "pending-decisions",
        "verification-missing",
        "milestone-out-of-sequence",
        "task-incomplete",
        "workspace-invalid",
        "workspace-exists",
        "awaiting-human-authorization",
        # the Phase 9 HITL handoff
        # blocker, a blocking condition and therefore an error code.
        "awaiting-human-completion",
        "principles-not-ratified",
        "gate-cap-reached",
        "no-progress",
        "attempt-cap",
        "usage",
        "internal",
        "conflict",
        "feature-ambiguous",
        "not-implemented",
        "ledger-drift",
        # Decision-resolution input and immutable-history errors.
        "invalid-route",
        "decision-already-resolved",
        # Product follow-up 01: a verification attempt can be durably recorded
        # while its exact evidence remains unusable for command success.
        "verification-failed",
        # Product follow-up 02: a structurally supported disposition batch
        # reports every rejected row without publishing any of them.
        "disposition-batch-invalid",
    }
)


COMPLETION_REPAIR_CAUSES: Mapping[str, frozenset[str]] = {
    "retrospective": frozenset({"missing", "unreadable", "empty", "missing-headings"}),
    "decision-journal": frozenset({"missing", "unreadable", "audit-incomplete"}),
    "close-configuration": frozenset({"suite-or-user-grant-required"}),
    "spec-stamp": frozenset({"unreadable", "invalid-frontmatter"}),
}


@dataclass(frozen=True)
class CompletionRepair:
    """One stable, repository-relative completion-preflight repair."""

    check: str
    cause: str
    path: str
    code: str
    message: str
    hint: str

    def __post_init__(self) -> None:
        if self.check not in COMPLETION_REPAIR_CAUSES:
            raise ValueError(f"unknown completion repair check {self.check!r}")
        if self.cause not in COMPLETION_REPAIR_CAUSES[self.check]:
            raise ValueError(
                f"invalid {self.check} completion repair cause {self.cause!r}"
            )
        relative = PurePosixPath(self.path)
        if not self.path or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("completion repair path must be repository-relative")
        if self.code not in ERROR_CODES:
            raise ValueError(f"unknown completion repair error code {self.code!r}")
        if not self.message or not self.hint:
            raise ValueError("completion repair message and hint must be non-empty")

    def to_payload(self) -> dict[str, str]:
        return {
            "check": self.check,
            "cause": self.cause,
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class HeddleError:
    code: str  # machine-stable, ∈ ERROR_CODES
    message: str
    hint: str  # names the remediation
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(
                f"unknown error code {self.code!r}: expected one of the pinned "
                f"error-code vocabulary {sorted(ERROR_CODES)}"
            )
        if not isinstance(self.details, Mapping) or not all(
            isinstance(key, str) for key in self.details
        ):
            raise ValueError("error details must be a JSON-object-like mapping")


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str
    message: str
    source: str | None = None


@dataclass(frozen=True)
class NextAction:
    action: Action
    reason: str  # one sentence

    @property
    def command(self) -> str:
        return action_command(self.action)


@dataclass(frozen=True)
class HeddleResult:
    ok: bool
    data: Mapping[str, Any] | None
    error: HeddleError | None
    diagnostics: tuple[Diagnostic, ...]
    next_actions: tuple[NextAction, ...]
    exit_code: ExitCode

    def __post_init__(self) -> None:
        if (self.data is None) == (self.error is None):
            raise ValueError(
                "envelope carries data xor error: exactly one of "
                f"data/error must be present (got data={self.data!r}, "
                f"error={self.error!r})"
            )

    @classmethod
    def success(
        cls,
        data: Mapping[str, Any],
        *,
        diagnostics: tuple[Diagnostic, ...] = (),
        next_actions: tuple[NextAction, ...] = (),
        exit_code: ExitCode = ExitCode.OK,
    ) -> HeddleResult:
        return cls(
            ok=True,
            data=data,
            error=None,
            diagnostics=diagnostics,
            next_actions=next_actions,
            exit_code=exit_code,
        )

    @classmethod
    def failure(
        cls,
        error: HeddleError,
        *,
        exit_code: ExitCode,
        diagnostics: tuple[Diagnostic, ...] = (),
        next_actions: tuple[NextAction, ...] = (),
    ) -> HeddleResult:
        return cls(
            ok=False,
            data=None,
            error=error,
            diagnostics=diagnostics,
            next_actions=next_actions,
            exit_code=exit_code,
        )

    def to_envelope(self) -> dict[str, Any]:
        """Serialize the five wire keys; ``exit_code`` is process-level state
        for the dispatcher and never serializes."""
        envelope: dict[str, Any] = {
            "ok": self.ok,
            "schema_version": ENVELOPE_SCHEMA_VERSION,
        }
        if self.error is None:
            envelope["data"] = dict(self.data or {})
        else:
            envelope["error"] = {
                "code": self.error.code,
                "message": self.error.message,
                "hint": self.error.hint,
                "details": dict(self.error.details),
            }
        envelope["diagnostics"] = [
            {
                "severity": diagnostic.severity.value,
                "code": diagnostic.code,
                "message": diagnostic.message,
                **(
                    {"source": diagnostic.source}
                    if diagnostic.source is not None
                    else {}
                ),
            }
            for diagnostic in self.diagnostics
        ]
        envelope["next_actions"] = [
            {
                "action": action_payload(action.action),
                "command": action.command,
                "reason": action.reason,
            }
            for action in self.next_actions
        ]
        return envelope
