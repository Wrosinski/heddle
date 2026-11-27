from __future__ import annotations

import ctypes
import os
import re
import secrets
import select
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
# Cleanup scans and stops tokened processes again until a scan finds none, and
# starts no pass after this many grace periods.
_QUIESCENCE_GRACES = 2
# An observation whose process ID changed hands mid-read is retried this often.
_OBSERVE_ATTEMPTS = 3
_LAUNCH_TOKEN_BYTES = 16
_LAUNCH_TOKEN_SHAPE = re.compile(f"[0-9a-f]{{{2 * _LAUNCH_TOKEN_BYTES}}}")
# Linux reads each process's stat, status and environment from /proc;
# elsewhere `ps eww` finds tokened processes, and no start time binds them.
_PROC_TABLE = sys.platform.startswith("linux")


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
    before_spawn: Callable[[], None] | None = None,
) -> MonitorResult:
    """Run ``cmd`` with its output appended to ``raw_out``, enforcing the
    hard and inactivity timeouts of ``cfg``.

    ``before_spawn``, when given, runs once the launch's files are open,
    immediately before the process is spawned; whatever it raises propagates
    with nothing spawned.
    """
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
            if before_spawn is not None:
                before_spawn()
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
    return secrets.token_hex(_LAUNCH_TOKEN_BYTES)


def is_launch_token(value: object) -> bool:
    """``value`` has the exact shape ``new_launch_token`` returns."""
    return isinstance(value, str) and _LAUNCH_TOKEN_SHAPE.fullmatch(value) is not None


class ProcessState(StrEnum):
    """What one observation proves about a process."""

    ALIVE = "alive"
    GONE = "gone"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class ProcessIdentity:
    """One process across PID reuse: its ID and its start time in clock ticks
    since boot, None where none could be read."""

    pid: int
    start: int | None


@dataclass(frozen=True)
class _Stat:
    """One read of ``/proc/<pid>/stat``. Gone with a start time is a zombie,
    which has exited but still holds its ID; gone without one is a missing
    entry."""

    state: ProcessState
    start: int | None = None
    parent: int | None = None


def _read_stat(pid: int) -> _Stat:
    """The process's start time and parent from ``/proc``: gone when it has
    no entry or is a zombie, unverifiable when its stat cannot be read or
    parsed. A missing entry proves nothing by itself when the process table
    is absent; ``_held`` asks the kernel."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as stream:
            text = stream.read().decode("ascii", "replace")
    except (FileNotFoundError, ProcessLookupError):
        return _Stat(ProcessState.GONE)
    except OSError:
        return _Stat(ProcessState.UNVERIFIABLE)
    fields = text.rsplit(")", 1)[-1].split()
    if len(fields) < 20:
        return _Stat(ProcessState.UNVERIFIABLE)
    try:
        start, parent = int(fields[19]), int(fields[1])
    except ValueError:
        return _Stat(ProcessState.UNVERIFIABLE)
    exited = fields[0] in ("Z", "X", "x")
    return _Stat(
        ProcessState.GONE if exited else ProcessState.ALIVE, start=start, parent=parent
    )


def process_start_time(pid: int) -> int | None:
    """The process's start time in clock ticks since boot, or None when the
    process is gone, a zombie, or unreadable. With the process ID it names one
    process across PID reuse."""
    stat = _read_stat(pid)
    return stat.start if stat.state is ProcessState.ALIVE else None


def _held(pid: int) -> bool | None:
    """Whether any process holds ``pid``, asked of the kernel rather than of
    ``/proc``: None when it cannot tell."""
    pidfd_open = getattr(os, "pidfd_open", None)
    if pidfd_open is not None:
        try:
            os.close(pidfd_open(pid))
        except ProcessLookupError:
            return False
        except OSError:
            pass
        else:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def observe_process(pid: int, start: int | None) -> ProcessState:
    """Whether the process this ID and start time name is alive, proven gone,
    or unverifiable.

    Gone means the kernel says no process holds the ID, or the holder is a
    zombie, or its start time differs (the ID was reused). A missing
    ``/proc`` entry alone proves nothing, as the table may be absent or
    unreadable. A process that holds the ID but whose stat cannot be read, or
    that no recorded start time names, may be this one: unverifiable.
    """
    if pid <= 0:
        return ProcessState.GONE
    if _PROC_TABLE:
        stat = _read_stat(pid)
        if stat.state is ProcessState.ALIVE:
            if start is None:
                return ProcessState.UNVERIFIABLE
            return ProcessState.ALIVE if stat.start == start else ProcessState.GONE
        if stat.state is ProcessState.UNVERIFIABLE:
            return ProcessState.UNVERIFIABLE
        if stat.start is not None:
            return ProcessState.GONE
    return ProcessState.GONE if _held(pid) is False else ProcessState.UNVERIFIABLE


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
    the process table was read, every process in it was judged, and a final
    scan found no tokened process."""

    found: tuple[int, ...]
    survivors: tuple[int, ...]
    warnings: tuple[str, ...]

    @property
    def verified(self) -> bool:
        return not self.warnings


@dataclass(frozen=True)
class _Bound:
    """A tokened process held by a process descriptor opened before its
    environment was read: every signal goes through ``descriptor``, which
    names that process and never a later holder of its ID."""

    pid: int
    descriptor: int


@dataclass
class _Scan:
    tokened: list[_Bound]
    unverifiable: list[str]
    unreadable: str | None = None

    def close(self) -> None:
        for process in self.tokened:
            os.close(process.descriptor)


class _Retry:
    """The ID changed hands during one observation: observe it again."""


_RETRY = _Retry()


def _carries(environment: bytes, needle: bytes) -> bool:
    return needle in environment.split(b"\0")


def _exited(descriptor: int) -> bool:
    """The process a descriptor names has exited (a zombie has)."""
    poller = select.poll()
    poller.register(descriptor, select.POLLIN)
    return bool(poller.poll(0))


def _may_signal(pid: int) -> bool:
    """This user's signals reach the process, by the kernel's rule: the
    sender's real or effective ID equals the target's real or saved ID. An
    unreadable status counts as reachable."""
    if os.geteuid() == 0:
        return True
    try:
        with open(f"/proc/{pid}/status", "rb") as stream:
            status = stream.read().decode("ascii", "replace")
    except OSError:
        return True
    for line in status.splitlines():
        if line.startswith("Uid:"):
            ids = line.split()[1:]
            if len(ids) >= 3 and all(value.isdigit() for value in ids[:3]):
                target = {int(ids[0]), int(ids[2])}
                return bool(target & {os.getuid(), os.geteuid()})
    return True


def _judge_unreadable(
    pid: int, stat: _Stat, needle: bytes, launcher: ProcessIdentity
) -> str | None:
    """Judge a process whose environment cannot be read: None when it cannot
    carry the token, otherwise why it stays unverifiable.

    A process carries the token only by inheriting it from the launch, so it
    cannot when it started before the launcher, when this user's signals
    cannot reach it (it would have changed identity through a privileged
    program), or when its parent, which created it, is a readable process
    without the token other than the launcher, the one process that adds it.
    """
    assert stat.start is not None
    if launcher.start is not None and stat.start < launcher.start:
        return None
    if not _may_signal(pid):
        return None
    parent = stat.parent
    if parent is not None and parent not in (0, 1, launcher.pid):
        try:
            with open(f"/proc/{parent}/environ", "rb") as stream:
                inherited = stream.read()
        except OSError:
            pass
        else:
            if not _carries(inherited, needle):
                return None
    return (
        f"process {pid} may carry the token, but its environment cannot be "
        "read, so it was left running"
    )


def _observe_once(
    pid: int, needle: bytes, launcher: ProcessIdentity
) -> _Bound | str | _Retry | None:
    """One bound observation: open a process descriptor, read the start time,
    read the environment, read the start time again. Only when both reads
    agree and the descriptor's process is still running do the reads describe
    the process the descriptor names. None means gone or untokened; a string
    says why the process is unverifiable."""
    pidfd_open = getattr(os, "pidfd_open", None)
    descriptor: int | None = None
    if pidfd_open is not None:
        try:
            descriptor = pidfd_open(pid)
        except ProcessLookupError:
            return None
        except OSError as error:
            return f"process {pid} cannot be held by a process descriptor: {error}"
    bound = False
    try:
        first = _read_stat(pid)
        if first.state is ProcessState.GONE:
            return None
        if first.state is ProcessState.UNVERIFIABLE:
            return f"the stat of process {pid} cannot be read, so it was left running"
        environment: bytes | None
        try:
            with open(f"/proc/{pid}/environ", "rb") as stream:
                environment = stream.read()
        except (FileNotFoundError, ProcessLookupError):
            return None
        except PermissionError:
            environment = None
        except OSError as error:
            return f"the environment of process {pid} cannot be read: {error}"
        second = _read_stat(pid)
        if second.state is ProcessState.GONE:
            return None
        if second != first or (descriptor is not None and _exited(descriptor)):
            return _RETRY
        if environment is None:
            return _judge_unreadable(pid, first, needle, launcher)
        if not _carries(environment, needle):
            return None
        if descriptor is None:
            return (
                f"process {pid} carries the token, but this platform gives no "
                "process descriptor to signal it by, so it was left running"
            )
        bound = True
        return _Bound(pid, descriptor)
    finally:
        if descriptor is not None and not bound:
            os.close(descriptor)


def _observe(pid: int, needle: bytes, launcher: ProcessIdentity) -> _Bound | str | None:
    for _ in range(_OBSERVE_ATTEMPTS):
        observed = _observe_once(pid, needle, launcher)
        if not isinstance(observed, _Retry):
            return observed
    return f"process {pid} changed identity during every observation"


def _scan(variable: str, token: str, launcher: ProcessIdentity) -> _Scan:
    """Every other process whose environment carries ``variable=token``, each
    bound to a process descriptor, with the processes that could not be
    judged, or why the process table is unreadable. A listing without this
    process is no process table."""
    own = os.getpid()
    if not _PROC_TABLE:
        try:
            listing = run_bounded(
                ["ps", "axeww", "-o", "pid,command"], timeout_s=_PS_TIMEOUT_S
            )
        except (OSError, subprocess.SubprocessError) as error:
            return _Scan([], [], f"cannot enumerate the process table (ps): {error}")
        if listing.returncode != 0:
            return _Scan(
                [],
                [],
                f"cannot enumerate the process table (ps exited {listing.returncode})",
            )
        pids = parse_ps_eww(listing.stdout, variable=variable, token=token)
        return _Scan(
            [],
            [
                f"process {pid} carries the token, but without a start time its "
                "identity cannot be verified, so it was left running"
                for pid in sorted(pids - {own})
            ],
        )
    needle = f"{variable}={token}".encode()
    try:
        entries = os.listdir("/proc")
    except OSError as error:
        return _Scan([], [], f"cannot enumerate the process table (/proc): {error}")
    if str(own) not in entries:
        return _Scan(
            [],
            [],
            "cannot enumerate the process table (/proc): it does not list this process",
        )
    scan = _Scan([], [])
    for entry in entries:
        if not entry.isdigit() or int(entry) == own:
            continue
        observed = _observe(int(entry), needle, launcher)
        if isinstance(observed, _Bound):
            scan.tokened.append(observed)
        elif observed is not None:
            scan.unverifiable.append(observed)
    return scan


def _wait_exited(processes: list[_Bound], grace_s: float) -> list[_Bound]:
    deadline = time.monotonic() + max(grace_s, 0.0)
    while True:
        remaining = [
            process for process in processes if not _exited(process.descriptor)
        ]
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(_REAP_POLL_S)


def _stop(processes: list[_Bound], grace_s: float) -> list[_Bound]:
    """``SIGTERM``, then ``SIGKILL`` after ``grace_s``, each through the
    process's descriptor; the processes still running after both."""
    for process in processes:
        with suppress(OSError):
            signal.pidfd_send_signal(process.descriptor, signal.SIGTERM)
    remaining = _wait_exited(processes, grace_s)
    for process in remaining:
        with suppress(OSError):
            signal.pidfd_send_signal(process.descriptor, signal.SIGKILL)
    return _wait_exited(remaining, grace_s)


def reap_token(
    token: str,
    *,
    launcher: ProcessIdentity,
    variable: str = LAUNCH_TOKEN_ENV,
    grace_s: float = DEFAULT_REAP_GRACE_S,
) -> ReapResult:
    """Stop every process whose environment carries ``variable=token``:
    ``SIGTERM``, then ``SIGKILL`` for any still running after ``grace_s``.
    ``launcher`` is the process that set the token.

    The table is scanned again after every pass, so a process born during
    cleanup is stopped too, until a scan finds none. No pass starts after
    ``_QUIESCENCE_GRACES`` grace periods; tokened processes still appearing
    then leave cleanup unverified.

    Cleanup is warning-only: an unreadable process table, a process that
    cannot be judged or bound (left running), and a process that survives
    ``SIGKILL`` are reported in ``warnings`` and never raised.
    """
    bound_s = _QUIESCENCE_GRACES * max(grace_s, _REAP_POLL_S)
    deadline = time.monotonic() + bound_s
    found: set[int] = set()
    survivors: tuple[int, ...] = ()
    warnings: list[str] = []
    while True:
        scan = _scan(variable, token, launcher)
        try:
            if scan.unreadable is not None:
                warnings.append(scan.unreadable)
                break
            found.update(process.pid for process in scan.tokened)
            if not scan.tokened:
                warnings.extend(scan.unverifiable)
                break
            if time.monotonic() >= deadline:
                pids = ", ".join(str(process.pid) for process in scan.tokened)
                warnings.append(
                    f"tokened processes {pids} were still appearing when the "
                    f"{bound_s:g}-second cleanup bound ran out"
                )
                warnings.extend(scan.unverifiable)
                break
            left = _stop(scan.tokened, grace_s)
            if left:
                survivors = tuple(sorted(process.pid for process in left))
                warnings.extend(
                    f"tokened process {pid} survived SIGKILL and is still running"
                    for pid in survivors
                )
                warnings.extend(scan.unverifiable)
                break
        finally:
            scan.close()
    return ReapResult(
        found=tuple(sorted(found)), survivors=survivors, warnings=tuple(warnings)
    )
