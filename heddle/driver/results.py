"""Shared result constructors for ``heddle.driver``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
)
from heddle.kernel.model import KernelError

USAGE_KERNEL_CODES = {"usage", "feature-ambiguous"}


def failure(
    *,
    code: str,
    message: str,
    hint: str,
    exit_code: ExitCode,
    diagnostics: tuple[Diagnostic, ...] = (),
    next_actions: tuple[NextAction, ...] = (),
    details: Mapping[str, Any] | None = None,
) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code=code, message=message, hint=hint, details=details or {}),
        exit_code=exit_code,
        diagnostics=diagnostics,
        next_actions=next_actions,
    )


def kernel_failure(error: KernelError) -> HeddleResult:
    return failure(
        code=error.code,
        message=error.message,
        hint=error.hint,
        details=error.details,
        exit_code=(
            ExitCode.USAGE if error.code in USAGE_KERNEL_CODES else ExitCode.FATAL
        ),
    )


def usage(message: str, hint: str) -> HeddleResult:
    return failure(
        code="usage",
        message=message,
        hint=hint,
        exit_code=ExitCode.USAGE,
    )
