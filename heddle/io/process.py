from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Termination(StrEnum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    INACTIVITY = "inactivity"


@dataclass(frozen=True)
class MonitorConfig:
    hard_timeout_s: float
    inactivity_timeout_s: float
    poll_s: float = 5.0
    term_grace_s: float = 10.0


@dataclass(frozen=True)
class Marker:
    size: int
    mtime_ns: int

    def summary(self) -> str:
        return f"{self.size}:{self.mtime_ns // 1_000_000_000}"


@dataclass(frozen=True)
class MonitorCheckpoint:
    offset_bytes: int
    monotonic_s: float


@dataclass
class MonitorResult:
    exit_code: int
    termination: Termination
    saw_stream_activity: bool
    inactivity_seconds: int
    inactivity_marker: str
    checkpoints: list[MonitorCheckpoint]
    launch_time: float | None = None
    termination_time: float | None = None


class MonitorLaunchError(OSError):
    """The monitored subprocess could not be launched."""


def file_marker(path: Path) -> Marker:
    if not path.exists():
        return Marker(size=0, mtime_ns=0)
    stat_result = path.stat()
    return Marker(size=stat_result.st_size, mtime_ns=stat_result.st_mtime_ns)


def install_monitored_output(captured: Path, destination: Path) -> None:
    """Publish completed monitor output without exposing a partial file."""
    descriptor, raw_staged = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    staged = Path(raw_staged)
    try:
        shutil.copyfile(captured, staged)
        os.replace(staged, destination)
    finally:
        with suppress(OSError):
            staged.unlink(missing_ok=True)


def run_monitored(
    cmd: list[str],
    raw_out: Path,
    cfg: MonitorConfig,
    stdin_source: Path | None = None,
    activity_filter: Callable[[Path, int, int], bool] | None = None,
    env: dict[str, str] | None = None,
) -> MonitorResult:
    launched_at = time.monotonic()
    last_progress_at = launched_at
    saw_stream = False
    checkpoints: list[MonitorCheckpoint] = []

    stdin_fh = None
    proc = None
    previous_sigterm = None

    def terminate_monitor(_signum: int, _frame: object) -> None:
        raise SystemExit(128 + signal.SIGTERM)

    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, terminate_monitor)
    try:
        last_marker = file_marker(raw_out)
        stdin_fh = open(stdin_source, "rb") if stdin_source else None
        stdin_stream = stdin_fh if stdin_fh is not None else subprocess.DEVNULL
        with raw_out.open("ab", buffering=0) as sink:
            # subprocess-handler: allow monitored Popen; loop enforces timeouts
            proc = subprocess.Popen(
                cmd,
                stdin=stdin_stream,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=(
                    _set_pdeathsig if sys.platform.startswith("linux") else None
                ),
                env=env,
            )
            if stdin_fh is not None:
                stdin_fh.close()
                stdin_fh = None

            try:
                while True:
                    try:
                        proc.wait(timeout=cfg.poll_s)
                        finished_at = time.monotonic()
                        return MonitorResult(
                            exit_code=proc.returncode,
                            termination=Termination.COMPLETED,
                            saw_stream_activity=saw_stream,
                            inactivity_seconds=0,
                            inactivity_marker=last_marker.summary(),
                            checkpoints=checkpoints,
                            launch_time=launched_at,
                            termination_time=finished_at,
                        )
                    except subprocess.TimeoutExpired:
                        pass

                    now = time.monotonic()
                    marker = file_marker(raw_out)
                    if marker != last_marker:
                        meaningful_progress = True
                        if activity_filter is not None:
                            meaningful_progress = activity_filter(
                                raw_out,
                                last_marker.size,
                                marker.size,
                            )
                        last_marker = marker
                        if marker.size > 0:
                            saw_stream = True
                            if meaningful_progress:
                                last_progress_at = now
                                checkpoints.append(
                                    MonitorCheckpoint(
                                        offset_bytes=marker.size,
                                        monotonic_s=now,
                                    )
                                )

                    if now - launched_at >= cfg.hard_timeout_s:
                        _kill_group(proc, cfg.term_grace_s)
                        finished_at = time.monotonic()
                        return MonitorResult(
                            exit_code=124,
                            termination=Termination.TIMEOUT,
                            saw_stream_activity=saw_stream,
                            inactivity_seconds=0,
                            inactivity_marker=marker.summary(),
                            checkpoints=checkpoints,
                            launch_time=launched_at,
                            termination_time=finished_at,
                        )

                    idle = (
                        now - launched_at if not saw_stream else now - last_progress_at
                    )
                    if idle >= cfg.inactivity_timeout_s:
                        _kill_group(proc, cfg.term_grace_s)
                        finished_at = time.monotonic()
                        return MonitorResult(
                            exit_code=118,
                            termination=Termination.INACTIVITY,
                            saw_stream_activity=saw_stream,
                            inactivity_seconds=int(idle),
                            inactivity_marker=marker.summary(),
                            checkpoints=checkpoints,
                            launch_time=launched_at,
                            termination_time=finished_at,
                        )
            finally:
                if proc.poll() is None:
                    _kill_group(proc, cfg.term_grace_s)
    except OSError as error:
        if proc is None:
            raise MonitorLaunchError(str(error)) from error
        raise
    finally:
        if stdin_fh is not None:
            stdin_fh.close()
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)


def _kill_group(proc: subprocess.Popen[bytes], grace_s: float) -> None:
    if proc.poll() is not None:
        return
    pid = proc.pid
    try:
        os.killpg(pid, signal.SIGTERM)
    except (PermissionError, ProcessLookupError):
        try:
            proc.terminate()
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pid, signal.SIGKILL)
    except (PermissionError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        return


def _set_pdeathsig() -> None:
    libc = ctypes.CDLL(None)
    pr_set_pdeathsig = 1
    libc.prctl(pr_set_pdeathsig, signal.SIGTERM)
