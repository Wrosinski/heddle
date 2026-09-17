"""Runtime-owned workspace locking around kernel descriptor coordination."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from heddle.kernel.locking import exclusive_descriptor_lock
from heddle.kernel.project_config import KernelError


@contextmanager
def state_recording_lock(state_path: Path) -> Iterator[None]:
    """Serialize one workspace ledger's complete read-transform-replace."""
    descriptor = _open_workspace_descriptor(state_path)
    try:
        with exclusive_descriptor_lock(
            descriptor,
            on_wait=lambda: _emit_state_lock_wait(state_path),
        ):
            yield
    finally:
        os.close(descriptor)


def prepare_state_recording_lock(state_path: Path) -> None:
    """Validate that the stable workspace-directory lock can be opened."""
    descriptor = _open_workspace_descriptor(state_path)
    os.close(descriptor)


def _emit_state_lock_wait(state_path: Path) -> None:
    print(
        f"heddle run-gate: waiting for state recording lock {state_path.parent.name}…",
        file=sys.stderr,
    )


def _open_workspace_descriptor(state_path: Path) -> int:
    try:
        return os.open(state_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"workspace state lock could not be opened: {error}",
            hint="restore access to the feature workspace, then rerun the command",
        ) from error
