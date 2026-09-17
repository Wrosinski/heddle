"""Monitored verification execution and fact/log naming."""

from __future__ import annotations

import os
from contextlib import chdir
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import Any

import heddle.io.process as process
from heddle.contracts.limits import (
    DEFAULT_SESSION_HARD_TIMEOUT_S,
    DEFAULT_SESSION_INACTIVITY_TIMEOUT_S,
)
from heddle.io.process import MonitorConfig
from heddle.kernel.project_config import KernelError
from heddle.kernel.state import (
    VerificationEvidence,
    verification_evidence_payload,
)
from heddle.kernel.verification import SCOPE_COMMAND_KEYS

_VERIFY_HARD_TIMEOUT_ENV = "HEDDLE_VERIFY_HARD_TIMEOUT_SECONDS"
_VERIFY_INACTIVITY_TIMEOUT_ENV = "HEDDLE_VERIFY_INACTIVITY_TIMEOUT_SECONDS"
_VERIFY_POLL_ENV = "HEDDLE_VERIFY_POLL_SECONDS"
# `verify` inherits run-gate's host-policy timeout defaults rather
# than inventing its own — one binding in heddle/contracts/limits.py shared
# with heddle/gate/cli.py. The env vars above override
# for tests/ops.
_DEFAULT_VERIFY_HARD_TIMEOUT_S = float(DEFAULT_SESSION_HARD_TIMEOUT_S)
_DEFAULT_VERIFY_INACTIVITY_TIMEOUT_S = float(DEFAULT_SESSION_INACTIVITY_TIMEOUT_S)
_DEFAULT_VERIFY_POLL_S = 0.1
# Exit code recorded when the stored verify command fails to launch
# (shell "command not found" convention).
VERIFY_LAUNCH_FAILURE_EXIT = 127


def verification_command(document: dict[str, Any], scope: str) -> str | None:
    """The stored verification command for ``scope`` in a loaded state
    document (the caller owns loading, so the KernelError channel stays
    with the state readers in write_path.py)."""
    if scope.startswith("m"):
        for milestone in document.get("milestones", []):
            if milestone.get("id") == scope:
                verification = milestone.get("verification")
                if not isinstance(verification, dict):
                    return None
                command = verification.get("command")
                return command if isinstance(command, str) and command.strip() else None
        return None
    command_key = SCOPE_COMMAND_KEYS.get(scope)
    if command_key is None:
        return None
    commands = document.get("commands", {})
    if not isinstance(commands, dict):
        return None
    command = commands.get(command_key)
    return command if isinstance(command, str) and command.strip() else None


def run_verification_command(root: Path, command: str, log_path: Path) -> int:
    # Verification commands run under `sh -c`: milestone verification payloads
    # are naturally authored as
    # `&&` chains, and the previous shlex-split-and-exec handed the whole
    # chain to the FIRST program as literal arguments — the chain became one
    # pytest invocation exiting 4 forever. The command string is a trusted
    # authorship surface (sessions write it through milestone payloads, the
    # same trust as autopilot.test_command).
    # An empty/whitespace command must stay a failing verification — `sh -c ""`
    # would exit 0 and silently green a milestone. Run a synthetic failing shell
    # act so the result still receives the same resolvable empty evidence log as
    # every other completed verification attempt.
    shell_command = command if command.strip() else f"exit {VERIFY_LAUNCH_FAILURE_EXIT}"
    argv = ["/bin/sh", "-c", shell_command]
    try:
        capture_parent = Path(gettempdir()).resolve()
        workspace_root = root.resolve()
        if capture_parent == workspace_root or capture_parent.is_relative_to(
            workspace_root
        ):
            raise KernelError(
                code="workspace-invalid",
                message=(
                    "verification capture directory must be outside the "
                    f"workspace: {capture_parent}"
                ),
                hint="set TMPDIR to a writable directory outside the workspace",
            )
        # A verification command may inspect the worktree itself. Capture outside
        # the repository so the verifier's own in-flight log cannot perturb that
        # inspection, then atomically publish the completed evidence.
        with TemporaryDirectory(
            prefix="heddle-verify-",
            dir=capture_parent,
            ignore_cleanup_errors=True,
        ) as capture_dir:
            capture_path = Path(capture_dir) / log_path.name
            with chdir(root):
                try:
                    result = process.run_monitored(
                        argv,
                        capture_path,
                        MonitorConfig(
                            hard_timeout_s=_env_float(
                                _VERIFY_HARD_TIMEOUT_ENV,
                                _DEFAULT_VERIFY_HARD_TIMEOUT_S,
                            ),
                            inactivity_timeout_s=_env_float(
                                _VERIFY_INACTIVITY_TIMEOUT_ENV,
                                _DEFAULT_VERIFY_INACTIVITY_TIMEOUT_S,
                            ),
                            poll_s=_env_float(_VERIFY_POLL_ENV, _DEFAULT_VERIFY_POLL_S),
                        ),
                    )
                    exit_code = int(result.exit_code)
                except process.MonitorLaunchError:
                    # A launch failure (e.g. a missing executable in the
                    # stored command) is a *failing* verification, not a
                    # dispatcher internal error. The monitor opens the capture
                    # before spawning, so its empty evidence still installs.
                    exit_code = VERIFY_LAUNCH_FAILURE_EXIT
            log_path.parent.mkdir(parents=True, exist_ok=True)
            process.install_monitored_output(capture_path, log_path)
            return exit_code
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"verification evidence for {log_path} could not be captured "
                f"or published: {error}"
            ),
            hint="check that the verification directory is writable and retry",
        ) from error


def verification_fact(
    scope: str,
    command: str,
    *,
    exit_code: int,
    at: str,
    revision: int,
    evidence: VerificationEvidence,
    log: str | None = None,
) -> dict[str, Any]:
    fact = {
        "scope": scope,
        "command": command,
        "exit_code": exit_code,
        "at": at,
        "log": log
        if log is not None
        else verification_log_relpath(scope, at, revision),
    }
    fact["evidence"] = verification_evidence_payload(evidence)
    return fact


def verification_log_relpath(
    scope: str,
    at: str,
    revision: int,
    *,
    attempt_id: str | None = None,
) -> str:
    suffix = f"-a{attempt_id}" if attempt_id is not None else ""
    return f"verification/{scope}-{_filename_timestamp(at)}-r{revision}{suffix}.log"


def _filename_timestamp(at: str) -> str:
    return at.replace("-", "").replace(":", "")


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise KernelError(
            code="usage",
            message=f"{name} must be a positive number of seconds, got {value!r}",
            hint=f"set {name} to a positive number or unset it",
        ) from error
    if not isfinite(parsed) or parsed <= 0:
        raise KernelError(
            code="usage",
            message=f"{name} must be a positive number of seconds, got {value!r}",
            hint=f"set {name} to a positive number or unset it",
        )
    return parsed
