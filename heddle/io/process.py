from __future__ import annotations

import ctypes
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# A launcher puts a fresh value of this variable into the environment of each
# process it starts, so every descendant that keeps its environment can be
# found and stopped by that value alone, however it detached.
LAUNCH_TOKEN_ENV = "HEDDLE_LAUNCH_TOKEN"
DEFAULT_REAP_GRACE_S = 5.0
_REAP_POLL_S = 0.02
# How long a timed-out bounded command's group gets between SIGTERM and SIGKILL.
_BOUNDED_KILL_GRACE_S = 2.0
_PS_TIMEOUT_S = 30.0
# Linux reads each process's environment from /proc; elsewhere `ps eww` does.
_PROC_ENVIRON = sys.platform.startswith("linux")


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
    cwd: Path | None = None,
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
                cwd=cwd,
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


@dataclass(frozen=True)
class BoundedResult:
    returncode: int
    stdout: str


def run_bounded(
    cmd: list[str], *, timeout_s: float, env: Mapping[str, str] | None = None
) -> BoundedResult:
    """Run a short command, no shell, with stdin closed, and return its exit
    status and standard output (standard error is discarded). Past
    ``timeout_s`` its process group is killed and ``subprocess.TimeoutExpired``
    propagates; a command that cannot start raises ``OSError``."""
    # subprocess-handler: allow bounded Popen; communicate enforces the timeout
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=(_set_pdeathsig if sys.platform.startswith("linux") else None),
        env=dict(env) if env is not None else None,
    )
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except BaseException:
        _kill_group(proc, _BOUNDED_KILL_GRACE_S)
        if proc.stdout is not None:
            proc.stdout.close()
        raise
    return BoundedResult(proc.returncode, out.decode("utf-8", "replace"))


def new_launch_token() -> str:
    """A fresh, unguessable value for one physical launch's token variable."""
    return secrets.token_hex(16)


def process_start_time(pid: int) -> int | None:
    """The process's start time in clock ticks since boot, or None when the
    process is gone, a zombie, or unreadable. With the process ID it names one
    process across PID reuse."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as stream:
            text = stream.read().decode("ascii", "replace")
    except OSError:
        return None
    fields = text.rsplit(")", 1)[-1].split()
    if len(fields) < 20 or fields[0] == "Z":
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def process_alive(pid: int, start: int | None) -> bool:
    """The process with this ID, and this start time when one is given, is
    running (a zombie is not). Where no process table can be read (no
    ``/proc``), a signal-0 probe answers, without the start-time check."""
    if pid <= 0:
        return False
    if os.path.isdir("/proc/self"):
        current = process_start_time(pid)
        return current is not None and (start is None or current == start)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def parse_ps_eww(output: str, *, variable: str, token: str) -> frozenset[int]:
    """The PIDs in ``ps axeww -o pid,command`` text whose environment carries
    exactly ``variable=token``: a whole word, never a longer value, a shorter
    one, or the same value under a variable whose name merely ends the same."""
    carried = f"{variable}={token}"
    found = set()
    for line in output.splitlines():
        words = line.split()
        if len(words) < 2 or not words[0].isdigit():
            continue
        if carried in words[1:]:
            found.add(int(words[0]))
    return frozenset(found)


@dataclass(frozen=True)
class ReapResult:
    """What one reaping pass found and stopped. ``verified`` holds only when
    the process table was read and no tokened process survived."""

    found: tuple[int, ...]
    survivors: tuple[int, ...]
    warnings: tuple[str, ...]

    @property
    def verified(self) -> bool:
        return not self.warnings


def _tokened_processes(
    variable: str, token: str
) -> tuple[dict[int, int | None], str | None]:
    """Every other process whose environment carries ``variable=token``, with
    its start time, or a warning naming why the process table is unreadable."""
    own = os.getpid()
    if _PROC_ENVIRON:
        needle = f"{variable}={token}".encode()
        try:
            entries = os.listdir("/proc")
        except OSError as error:
            return {}, f"cannot enumerate the process table (/proc): {error}"
        found: dict[int, int | None] = {}
        for entry in entries:
            if not entry.isdigit() or int(entry) == own:
                continue
            try:
                with open(f"/proc/{entry}/environ", "rb") as stream:
                    environment = stream.read()
            except OSError:
                # Gone since the listing, or another user's: neither carries
                # a token this user's launch set.
                continue
            if needle in environment.split(b"\0"):
                pid = int(entry)
                found[pid] = process_start_time(pid)
        return found, None
    try:
        listing = run_bounded(
            ["ps", "axeww", "-o", "pid,command"], timeout_s=_PS_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {}, f"cannot enumerate the process table (ps): {error}"
    if listing.returncode != 0:
        return {}, (
            f"cannot enumerate the process table (ps exited {listing.returncode})"
        )
    pids = parse_ps_eww(listing.stdout, variable=variable, token=token)
    return {pid: None for pid in pids if pid != own}, None


def _signal(pid: int, start: int | None, sig: signal.Signals) -> None:
    """Send ``sig`` to the process this ID and start time name, through a
    process descriptor where the platform has one, so a recycled ID is never
    signalled."""
    pidfd_open = getattr(os, "pidfd_open", None)
    send = getattr(signal, "pidfd_send_signal", None)
    if pidfd_open is not None and send is not None:
        try:
            descriptor = pidfd_open(pid)
        except OSError:
            return
        try:
            if process_alive(pid, start):
                send(descriptor, sig)
        except OSError:
            pass
        finally:
            os.close(descriptor)
        return
    with suppress(OSError):
        os.kill(pid, sig)


def _wait_gone(
    processes: dict[int, int | None], grace_s: float
) -> dict[int, int | None]:
    deadline = time.monotonic() + max(grace_s, 0.0)
    while True:
        remaining = {
            pid: start for pid, start in processes.items() if process_alive(pid, start)
        }
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(_REAP_POLL_S)


def reap_token(
    token: str,
    *,
    variable: str = LAUNCH_TOKEN_ENV,
    grace_s: float = DEFAULT_REAP_GRACE_S,
) -> ReapResult:
    """Stop every process whose environment carries ``variable=token``:
    ``SIGTERM``, then ``SIGKILL`` for any still running after ``grace_s``.

    Cleanup is warning-only: an unreadable process table, or a process that
    survives ``SIGKILL``, is reported in ``warnings`` and never raised.
    """
    processes, unreadable = _tokened_processes(variable, token)
    if unreadable is not None:
        return ReapResult(found=(), survivors=(), warnings=(unreadable,))
    for pid, start in processes.items():
        _signal(pid, start, signal.SIGTERM)
    remaining = _wait_gone(processes, grace_s)
    for pid, start in remaining.items():
        _signal(pid, start, signal.SIGKILL)
    survivors = tuple(sorted(_wait_gone(remaining, grace_s)))
    warnings = tuple(
        f"tokened process {pid} survived SIGKILL and is still running"
        for pid in survivors
    )
    return ReapResult(
        found=tuple(sorted(processes)), survivors=survivors, warnings=warnings
    )
