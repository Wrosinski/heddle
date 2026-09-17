"""Drive application service and notification delivery."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace

from heddle.contracts.operations import Drive
from heddle.contracts.result import Diagnostic, ExitCode, HeddleResult, Severity
from heddle.driver import results as driver_results
from heddle.driver.loop import run_loop
from heddle.kernel.model import resolve_active_feature, resolve_snapshot
from heddle.kernel.project_config import (
    KernelError,
    ProjectConfig,
    load_project_config_from_cwd,
)


def drive(operation: Drive) -> HeddleResult:
    config = _resolve_config()
    if isinstance(config, HeddleResult):
        return config
    try:
        resolution = resolve_active_feature(config, operation.feature)
        resolve_snapshot(config, resolution.feature)
    except KernelError as error:
        return _with_notification(config, driver_results.kernel_failure(error))
    result = _with_review_count(
        run_loop(config, resolution.feature, until=operation.until)
    )
    return _with_notification(config, result)


def _with_notification(config: ProjectConfig, result: HeddleResult) -> HeddleResult:
    if _should_notify(result):
        result = replace(
            result, diagnostics=(*result.diagnostics, *_notify(config, result))
        )
    return result


def _should_notify(result: HeddleResult) -> bool:
    # notify on every pause (blocked / escalation / hard-halt errors,
    # surfaced at exit 1/3) AND on the terminal `complete` event. A bounded
    # `--until` stop is not terminal, so it stays quiet.
    if result.error is not None:
        return result.exit_code in {ExitCode.INTERNAL, ExitCode.FATAL}
    data = result.data
    return data is not None and data.get("status") == "complete"


def _with_review_count(result: HeddleResult) -> HeddleResult:
    # Surface the running [REVIEW] tally on the canonical
    # ``data.review_count`` path. The envelope carries data XOR error, so this
    # rides on success envelopes; pause/halt envelopes carry the same count as a
    # notify diagnostic instead.
    if result.data is None:
        return result
    return replace(result, data={**result.data, "review_count": _review_count(result)})


def _resolve_config() -> ProjectConfig | HeddleResult:
    try:
        return load_project_config_from_cwd()
    except KernelError as error:
        return driver_results.kernel_failure(error)


def _notify(config: ProjectConfig, result: HeddleResult) -> tuple[Diagnostic, ...]:
    command = config.autopilot.notify_command
    if not command:
        return ()
    timeout = float(config.autopilot.notify_timeout_s)
    payload = replace(
        result,
        diagnostics=(
            *result.diagnostics,
            Diagnostic(
                Severity.INFO,
                "review-count",
                f"[REVIEW] count: {_review_count(result)}",
            ),
        ),
    )
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload.to_envelope()),
            text=True,
            shell=True,
            cwd=config.root,
            timeout=timeout,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        return (
            Diagnostic(
                Severity.ADVISORY,
                "notify-timeout",
                f"notify command timed out after {timeout:g}s",
            ),
        )
    except OSError as error:
        return (
            Diagnostic(
                Severity.ADVISORY,
                "notify-failed",
                f"notify command could not run: {error}",
            ),
        )
    if completed.returncode != 0:
        return (
            Diagnostic(
                Severity.ADVISORY,
                "notify-failed",
                f"notify command exited {completed.returncode}",
            ),
        )
    return ()


def _review_count(result: HeddleResult) -> int:
    return sum(
        1
        for diagnostic in result.diagnostics
        if "[REVIEW]" in diagnostic.message or "review" in diagnostic.code.lower()
    )
