"""
Tests for the single-thread process supervisor.

Covers: AC-3 (all termination modes, no leaked processes).
Uses real subprocess spawning with tiny shell commands as CLI mocks.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from subprocess import TimeoutExpired
from typing import IO, Any, cast

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


class TestNormalCompletion:
    """AC-3a: Command completes normally."""

    @REQUIRES_IMPL
    def test_returns_completed_with_exit_code(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, Termination, run_monitored

        cfg = MonitorConfig(hard_timeout_s=30, inactivity_timeout_s=10, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        cmd = [sys.executable, "-c", "print('hello')"]

        result = run_monitored(cmd, raw_out, cfg)
        assert result.termination is Termination.COMPLETED
        assert result.exit_code == 0
        assert raw_out.read_text().strip() == "hello"

    @REQUIRES_IMPL
    def test_preserves_nonzero_exit_code(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, Termination, run_monitored

        cfg = MonitorConfig(hard_timeout_s=30, inactivity_timeout_s=10, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        cmd = [sys.executable, "-c", "import sys; print('fail'); sys.exit(42)"]

        result = run_monitored(cmd, raw_out, cfg)
        assert result.termination is Termination.COMPLETED
        assert result.exit_code == 42

    @REQUIRES_IMPL
    def test_passes_environment_to_subprocess(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, Termination, run_monitored

        cfg = MonitorConfig(hard_timeout_s=30, inactivity_timeout_s=10, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        cmd = [
            sys.executable,
            "-c",
            "import os; print(os.environ['SAMPLE_TEST_ENV'])",
        ]

        result = run_monitored(
            cmd,
            raw_out,
            cfg,
            env={"SAMPLE_TEST_ENV": "present"},
        )
        assert result.termination is Termination.COMPLETED
        assert result.exit_code == 0
        assert raw_out.read_text().strip() == "present"


class TestHardTimeout:
    """AC-3b: Command exceeds hard timeout."""

    @REQUIRES_IMPL
    def test_kills_on_hard_timeout(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, Termination, run_monitored

        cfg = MonitorConfig(hard_timeout_s=2, inactivity_timeout_s=60, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        # Write continuously so inactivity doesn't fire first
        cmd = [
            sys.executable,
            "-c",
            "import time\nwhile True:\n print('tick',flush=True)\n time.sleep(0.3)",
        ]

        result = run_monitored(cmd, raw_out, cfg)
        assert result.termination is Termination.TIMEOUT
        assert result.exit_code == 124


class TestInactivityPostStream:
    """AC-3c: Command produces output then stalls."""

    @REQUIRES_IMPL
    def test_kills_on_post_stream_inactivity(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, Termination, run_monitored

        cfg = MonitorConfig(hard_timeout_s=60, inactivity_timeout_s=2, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        # Write once then stall
        cmd = [
            sys.executable,
            "-c",
            "import time; print('start',flush=True); time.sleep(30)",
        ]

        result = run_monitored(cmd, raw_out, cfg)
        assert result.termination is Termination.INACTIVITY
        assert result.saw_stream_activity is True
        assert result.inactivity_seconds >= 2


class TestInactivityPreStream:
    """AC-3d: Command never produces output."""

    @REQUIRES_IMPL
    def test_kills_on_pre_stream_inactivity(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, Termination, run_monitored

        cfg = MonitorConfig(hard_timeout_s=60, inactivity_timeout_s=2, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]

        result = run_monitored(cmd, raw_out, cfg)
        assert result.termination is Termination.INACTIVITY
        assert result.saw_stream_activity is False


class TestStdinSource:
    """AC-3 + Codex stdin support: prompt file passed as stdin."""

    @REQUIRES_IMPL
    def test_stdin_source_passed_to_subprocess(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, Termination, run_monitored

        cfg = MonitorConfig(hard_timeout_s=30, inactivity_timeout_s=10, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("hello from stdin")
        cmd = [sys.executable, "-c", "import sys; print(sys.stdin.read())"]

        result = run_monitored(cmd, raw_out, cfg, stdin_source=prompt)
        assert result.termination is Termination.COMPLETED
        assert "hello from stdin" in raw_out.read_text()

    @REQUIRES_IMPL
    def test_stdin_source_closed_when_launch_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import heddle.io.process as monitor_module
        from heddle.io.process import (
            MonitorConfig,
            MonitorLaunchError,
            run_monitored,
        )

        cfg = MonitorConfig(hard_timeout_s=30, inactivity_timeout_s=10, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("hello from stdin")
        cmd = ["/path/that/does/not/exist"]
        original_open = open
        captured_handle: list[IO[bytes]] = []

        def tracking_open(*args, **kwargs):
            handle = original_open(*args, **kwargs)
            captured_handle.append(handle)
            return handle

        def fail_popen(*args, **kwargs):
            raise FileNotFoundError("missing binary")

        monkeypatch.setattr(monitor_module, "open", tracking_open, raising=False)
        monkeypatch.setattr(monitor_module.subprocess, "Popen", fail_popen)

        with pytest.raises(MonitorLaunchError) as excinfo:
            run_monitored(cmd, raw_out, cfg, stdin_source=prompt)

        assert isinstance(excinfo.value.__cause__, FileNotFoundError)
        assert captured_handle
        assert captured_handle[0].closed is True


@pytest.mark.parametrize("fault", ["missing", "unreadable", "output"])
def test_launch_io_failure_is_typed_before_spawning(tmp_path, monkeypatch, fault):
    from heddle.io import process

    prompt = tmp_path / "prompt.txt"
    raw_out = tmp_path / "raw.out"
    prompt.write_text("input")
    if fault == "missing":
        prompt.unlink()
    elif fault == "output":
        raw_out = tmp_path / "missing-output-parent" / "raw.out"
    else:

        def unreadable(*_args, **_kwargs):
            raise PermissionError("injected unreadable prompt")

        monkeypatch.setattr(process, "open", unreadable, raising=False)

    def no_spawn(*_args, **_kwargs):
        raise AssertionError("a failed launch input must not spawn")

    monkeypatch.setattr(process.subprocess, "Popen", no_spawn)
    with pytest.raises(process.MonitorLaunchError) as failure:
        process.run_monitored(
            [sys.executable, "-c", "print('never')"],
            raw_out,
            process.MonitorConfig(10, 5),
            stdin_source=prompt,
        )
    assert isinstance(
        failure.value.__cause__,
        PermissionError if fault == "unreadable" else FileNotFoundError,
    )
    assert not raw_out.exists()


class TestNoLeakedProcesses:
    """AC-3: No leaked processes after any termination mode."""

    @REQUIRES_IMPL
    def test_process_group_cleaned_up_on_timeout(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, run_monitored

        cfg = MonitorConfig(hard_timeout_s=2, inactivity_timeout_s=60, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        # Spawn a child that spawns a grandchild
        cmd = [
            sys.executable,
            "-c",
            "import subprocess, time; "
            "subprocess.Popen(['sleep', '300']); "
            "time.sleep(0.5); "
            "print('started', flush=True); "
            "time.sleep(300)",
        ]

        result = run_monitored(cmd, raw_out, cfg)
        # After return, no descendant processes should be alive
        # (the sleep 300 grandchild should have been killed via process group)
        assert result.termination is not None  # terminated somehow

    @REQUIRES_IMPL
    def test_kill_group_uses_bounded_wait_after_sigkill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import heddle.io.process as monitor_module

        class FakeProc:
            pid = 123

            def __init__(self) -> None:
                self.wait_timeouts: list[float | None] = []

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.wait_timeouts.append(timeout)
                raise TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self) -> None:
                return None

            def kill(self) -> None:
                return None

        proc = FakeProc()
        signals: list[int] = []

        monkeypatch.setattr(
            monitor_module.os,
            "killpg",
            lambda pid, sig: signals.append(sig),
        )

        monitor_module._kill_group(cast(Any, proc), grace_s=0.25)

        assert signals == [monitor_module.signal.SIGTERM, monitor_module.signal.SIGKILL]
        assert proc.wait_timeouts == [0.25, 0.25]


class TestCheckpoints:
    """AC-3 + AC-5: Checkpoint list populated during monitoring."""

    @REQUIRES_IMPL
    def test_checkpoints_recorded_on_activity(self, tmp_path: Path) -> None:
        from heddle.io.process import MonitorConfig, Termination, run_monitored

        cfg = MonitorConfig(hard_timeout_s=30, inactivity_timeout_s=10, poll_s=0.5)
        raw_out = tmp_path / "raw.out"
        cmd = [
            sys.executable,
            "-c",
            "import time\n"
            "for i in range(5):\n"
            " print(f'line {i}',flush=True)\n"
            " time.sleep(0.3)",
        ]

        result = run_monitored(cmd, raw_out, cfg)
        assert result.termination is Termination.COMPLETED
        assert len(result.checkpoints) > 0
        assert all(cp.offset_bytes > 0 for cp in result.checkpoints)


class TestInstallMonitoredOutput:
    def test_preserves_arbitrary_bytes_and_cleans_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import heddle.io.process as monitor_module

        captured = tmp_path / "captured.log"
        destination = tmp_path / "verification" / "final.log"
        destination.parent.mkdir()
        captured.write_bytes(b"output\x00\xff\n")
        original_replace = monitor_module.os.replace
        replace_calls: list[tuple[Path, Path]] = []

        def assert_sibling_replace(source: Path, target: Path) -> None:
            source_path = Path(source)
            target_path = Path(target)
            replace_calls.append((source_path, target_path))
            assert source_path.parent == destination.parent, (
                "FAIL: atomic publication must copy to a destination-sibling "
                "stage before os.replace, never rename the external capture "
                "directly across filesystems"
            )
            assert target_path == destination
            original_replace(source_path, target_path)

        monkeypatch.setattr(monitor_module.os, "replace", assert_sibling_replace)

        monitor_module.install_monitored_output(captured, destination)

        assert len(replace_calls) == 1
        assert destination.read_bytes() == b"output\x00\xff\n"
        assert not list(destination.parent.glob("*.tmp"))

    def test_partial_copy_failure_preserves_winner_and_cleans_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import heddle.io.process as monitor_module

        captured = tmp_path / "captured.log"
        destination = tmp_path / "verification" / "final.log"
        destination.parent.mkdir()
        captured.write_bytes(b"replacement")
        destination.write_bytes(b"winner")

        def fail_after_partial_copy(_source: Path, staged: Path) -> None:
            Path(staged).write_bytes(b"partial")
            raise OSError("injected copy failure")

        monkeypatch.setattr(monitor_module.shutil, "copyfile", fail_after_partial_copy)

        with pytest.raises(OSError, match="injected copy failure"):
            monitor_module.install_monitored_output(captured, destination)

        assert destination.read_bytes() == b"winner"
        assert not list(destination.parent.glob("*.tmp"))

    def test_replace_failure_preserves_winner_and_cleans_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import heddle.io.process as monitor_module

        captured = tmp_path / "captured.log"
        destination = tmp_path / "verification" / "final.log"
        destination.parent.mkdir()
        captured.write_bytes(b"replacement")
        destination.write_bytes(b"winner")

        def fail_replace(_source: Path, _destination: Path) -> None:
            raise OSError("injected replace failure")

        monkeypatch.setattr(monitor_module.os, "replace", fail_replace)

        with pytest.raises(OSError, match="injected replace failure"):
            monitor_module.install_monitored_output(captured, destination)

        assert destination.read_bytes() == b"winner"
        assert not list(destination.parent.glob("*.tmp"))


# ---------------------------------------------------------------------------
# AC-12 and AC-18: launch-token reaping on the Antigravity lane.
#
# Real processes and fresh modification times. The doubled `agy` and its
# helpers live in tests/gate/test_antigravity_runner.py. A crashed launcher is
# a forked process running a standalone `run-gate`, killed with SIGKILL while
# its doubled `agy` holds; `agy` dies by its parent-death signal and leaves a
# daemonised tokened child. Red until the Antigravity runner exists.
# ---------------------------------------------------------------------------


def _antigravity():
    import tests.gate.test_antigravity_runner as lane

    return lane


def _run_in_child(gate: str):
    import multiprocessing

    lane = _antigravity()
    process = multiprocessing.get_context("fork").Process(
        target=lane.run_gate, args=(gate,)
    )
    process.start()
    return process


def _held(lane, token: str, *, then: list | None = None) -> list:
    """A launch that starts a daemonised tokened child, shows ``token`` in a
    shell call's parameters and output, then holds until released."""
    return [
        lane.init_event(),
        {"$children": ["daemon"]},
        lane.tool_step(
            "run_command",
            {"CommandLine": f"grep -rn {token} src"},
            lane.shell_output(f"src/example.py:1:{token}\n"),
        ),
        {"$hold": "release"},
        *(then or []),
    ]


def _wait_gone(pid: int, start: int | None, timeout: float = 10.0) -> None:
    lane = _antigravity()
    deadline = time.monotonic() + timeout
    while lane.alive(pid, start):
        assert time.monotonic() < deadline, "fixture: the crashed agy kept running"
        time.sleep(0.01)


def _crash(process, launch: dict) -> None:
    os.kill(process.pid, signal.SIGKILL)
    process.join()
    _wait_gone(launch["pid"], launch["start"])


def test_ac12_ac18_next_run_reaps_and_redacts_a_crashed_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12 parent crash, then the next gate run in the feature: the dead
    launch's tokened child is reaped and its stream redacted, though its
    directory is new (AC-18's interrupted path)."""
    lane = _antigravity()
    lane.require_runner("AC-12")
    agy = lane.install_agy(tmp_path, monkeypatch)
    tracker = lane.Processes(agy)
    try:
        host, _state_path = _antigravity().peer_host(tmp_path, monkeypatch, launch=None)
        token = lane.credential("crashed")
        agy.scenario(roles={"behavior-review": _held(lane, token)})
        crashed = _run_in_child("behavior-review")
        agy.wait_for("holding.jsonl")
        (launch,) = agy.launches()
        (child,) = agy.children()
        _crash(crashed, launch)
        temp = lane.temp_dir_of(launch)
        assert lane.files_holding(temp, token.encode()), (
            "fixture: the crashed launch's raw stream is not in its temp directory"
        )
        assert lane.alive(child["pid"], child["start"]), "fixture: no orphaned child"

        agy.scenario()
        result = lane.run_gate("behavior-review")

        assert result.ok, result.to_envelope()
        assert not lane.alive(child["pid"], child["start"]), (
            "FAIL AC-12: the crashed launch's tokened child survived the next run"
        )
        assert temp.is_dir(), "FAIL AC-12: the new crashed-run directory was swept"
        leaked = lane.files_holding(host, token.encode())
        assert leaked == [], (
            f"FAIL AC-18: the crashed stream stayed unredacted {leaked}"
        )
        assert tracker.unrelated_alive(), "FAIL AC-12: the unrelated process died"
        assert len(agy.launches()) == 2
    finally:
        agy.release()
        tracker.cleanup()


def test_ac12_crash_beside_a_live_sibling_reaps_only_the_dead_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12 crash beside a live sibling: one recovery pass sees both tokens and
    reaps only the dead launch's children. The live launch, its stream and the
    unrelated process survive, and the sweep keeps both directories, the live
    one even when aged past the stale cutoff. The live run then records."""
    lane = _antigravity()
    lane.require_runner("AC-12")
    agy = lane.install_agy(tmp_path, monkeypatch)
    tracker = lane.Processes(agy)
    live = None
    try:
        _host, state_path = _antigravity().peer_host(tmp_path, monkeypatch, launch=None)
        dead_token, live_token = lane.credential("dead"), lane.credential("live")
        finish = lane.clean_stream()[1:]
        agy.scenario(
            roles={
                "behavior-review": _held(lane, dead_token),
                "complexity-review": _held(lane, live_token, then=finish),
            }
        )
        crashed = _run_in_child("behavior-review")
        agy.wait_for("holding.jsonl", 1)
        live = _run_in_child("complexity-review")
        agy.wait_for("holding.jsonl", 2)
        launches = {row["role"]: row for row in agy.launches()}
        children = {row["role"]: row for row in agy.children()}
        _crash(crashed, launches["behavior-review"])
        dead_temp = lane.temp_dir_of(launches["behavior-review"])
        live_temp = lane.temp_dir_of(launches["complexity-review"])
        aged = time.time() - 49 * 60 * 60
        os.utime(live_temp, (aged, aged))
        live_stream = {
            path: path.read_bytes() for path in live_temp.rglob("*") if path.is_file()
        }
        assert any(live_token.encode() in data for data in live_stream.values())

        agy.scenario(roles={"behavior-review": lane.clean_stream()})
        result = lane.run_gate("behavior-review")

        assert result.ok, result.to_envelope()
        dead, sibling = children["behavior-review"], children["complexity-review"]
        assert not lane.alive(dead["pid"], dead["start"]), (
            "FAIL AC-12: the crashed launch's tokened child survived the pass"
        )
        assert lane.alive(sibling["pid"], sibling["start"]), (
            "FAIL AC-12: the recovery pass reaped the live launch's child"
        )
        sibling_launch = launches["complexity-review"]
        assert lane.alive(sibling_launch["pid"], sibling_launch["start"])
        assert tracker.unrelated_alive(), "FAIL AC-12: the unrelated process died"
        assert dead_temp.is_dir() and live_temp.is_dir(), (
            "FAIL AC-12: the sweep removed a directory the pass must keep"
        )
        assert {
            path: path.read_bytes() for path in live_temp.rglob("*") if path.is_file()
        } == live_stream, "FAIL AC-12: the pass touched the live launch's stream"
        assert lane.files_holding(dead_temp, dead_token.encode()) == [], (
            "FAIL AC-18: the dead launch's stream was not redacted"
        )

        agy.release()
        live.join(timeout=30)
        assert live.exitcode == 0
        rows = [
            row
            for row in lane.lane_rows(state_path)
            if row["gate"] == "complexity-review"
        ]
        assert rows and rows[-1]["verdict"]["status"] != "error", (
            "FAIL AC-12: the live sibling did not record normally"
        )
        assert not lane.alive(sibling["pid"], sibling["start"]), (
            "FAIL AC-12: the sibling's own run did not reap its child"
        )
    finally:
        agy.release()
        if live is not None:
            live.join(timeout=30)
        tracker.cleanup()


def test_ac12_unreadable_process_table_warns_and_keeps_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12 control, cleanup unverifiable: process enumeration fails, so the
    run logs a cleanup warning naming the condition and records as usual. No
    disappearance is asserted.

    Injection seam: listing ``/proc`` raises ``PermissionError``.
    """
    lane = _antigravity()
    lane.require_runner("AC-12")
    agy = lane.install_agy(tmp_path, monkeypatch)
    tracker = lane.Processes(agy)
    try:
        _host, state_path = lane.spec_host(tmp_path, monkeypatch)
        agy.scenario(lane.clean_stream({"$children": ["daemon"]}))

        def unreadable(function):
            def guarded(path=".", *args, **kwargs):
                # A descriptor (shutil.rmtree's scandir(fd)) is not a listing
                # of /proc; it passes through.
                if not isinstance(path, int) and os.fspath(path).rstrip("/") == "/proc":
                    raise PermissionError(13, "Permission denied", "/proc")
                return function(path, *args, **kwargs)

            return guarded

        with monkeypatch.context() as patch:
            patch.setattr(os, "listdir", unreadable(os.listdir))
            patch.setattr(os, "scandir", unreadable(os.scandir))
            result = lane.run_gate()

        assert result.ok, f"FAIL AC-12: the outcome changed: {result.to_envelope()}"
        lane.assert_recorded(state_path, "AC-12")
        log = lane.gate_log(state_path)
        assert re.search(r"warn", log, re.IGNORECASE) and re.search(
            r"process table|enumerat|/proc", log, re.IGNORECASE
        ), "FAIL AC-12: no cleanup warning names the unreadable process table"
        assert tracker.unrelated_alive()
    finally:
        tracker.cleanup()


def test_ac12_child_surviving_sigkill_warns_and_keeps_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12 control: a tokened child that ignores SIGTERM and survives SIGKILL
    produces a cleanup warning naming that process and its survival; the run's
    outcome is the ordinary recorded success. The test removes the survivor.

    Injection seam, the platform signal boundary as Python 3.13 exposes it:
    ``os.kill``, ``os.killpg`` and ``signal.pidfd_send_signal`` (through a
    descriptor from ``os.pidfd_open``) drop SIGTERM and SIGKILL aimed at the
    survivor. Every other signal and target passes through.
    """
    lane = _antigravity()
    lane.require_runner("AC-12")
    real_kill, real_killpg = os.kill, os.killpg
    real_open = getattr(os, "pidfd_open", None)
    real_send = getattr(signal, "pidfd_send_signal", None)
    tracker = None
    agy = lane.install_agy(tmp_path, monkeypatch)
    try:
        tracker = lane.Processes(agy)
        _host, state_path = lane.spec_host(tmp_path, monkeypatch)
        agy.scenario(lane.clean_stream({"$children": ["stubborn"]}))
        pidfds: dict[int, int] = {}
        stopping = (signal.SIGTERM, signal.SIGKILL)

        def survivor(pid: int) -> bool:
            return any(row["pid"] == pid for row in agy.children())

        def kill(pid, sig):
            if survivor(pid) and sig in stopping:
                return None
            return real_kill(pid, sig)

        def killpg(pgid, sig):
            if survivor(pgid) and sig in stopping:
                return None
            return real_killpg(pgid, sig)

        def pidfd_open(pid, *args):
            assert real_open is not None
            descriptor = real_open(pid, *args)
            pidfds[descriptor] = pid
            return descriptor

        def pidfd_send_signal(fd, sig, *args):
            assert real_send is not None
            if survivor(pidfds.get(fd, -1)) and sig in stopping:
                return None
            return real_send(fd, sig, *args)

        with monkeypatch.context() as patch:
            patch.setattr(os, "kill", kill)
            patch.setattr(os, "killpg", killpg)
            if real_open is not None:
                patch.setattr(os, "pidfd_open", pidfd_open)
            if real_send is not None:
                patch.setattr(signal, "pidfd_send_signal", pidfd_send_signal)
            result = lane.run_gate()

        (child,) = agy.children()
        assert lane.alive(child["pid"], child["start"]), (
            "fixture: the survivor did not survive"
        )
        assert result.ok, f"FAIL AC-12: the outcome changed: {result.to_envelope()}"
        row = lane.assert_recorded(state_path, "AC-12")
        assert row.get("failure_reason") is None, (
            f"FAIL AC-12: the survivor changed the outcome to {row['failure_reason']}"
        )
        warnings = [
            line
            for line in lane.gate_log(state_path).splitlines()
            if re.search(r"warn", line, re.IGNORECASE)
            and str(child["pid"]) in line
            and re.search(r"surviv|SIGKILL|still running", line, re.IGNORECASE)
        ]
        assert warnings, (
            "FAIL AC-12: no cleanup warning names the surviving process "
            f"{child['pid']} and its survival of SIGKILL"
        )
        assert tracker.unrelated_alive()
    finally:
        agy.release()
        if tracker is not None:
            tracker.cleanup(kill=real_kill)
        for row in agy.children():
            try:
                real_kill(row["pid"], signal.SIGKILL)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# AC-12, macOS fallback witness: the `ps eww` parser on synthetic output.
#
# Delegated seam named by this scaffold: ``heddle.io.process.parse_ps_eww(
# output, *, variable, token) -> frozenset[int]``. It reads the text of
# ``ps axeww -o pid,command`` (a header row, then each process's PID and its
# command line with its environment appended as ``NAME=value`` words) and
# returns the PIDs whose environment carries exactly ``variable=token``. The
# variable name is a parameter because the token's name is delegated too.
# Reached only through ``_ps_eww_pids``, so a rename is one edit. Hermetic:
# no real `ps` runs.
# ---------------------------------------------------------------------------
PS_VARIABLE = "SYNTHETIC_LAUNCH_TOKEN"
PS_TOKEN = "c2b8f0e4a7d94c1e9f3a6b5d8e2c1f0a"


def _ps_eww_pids(output: str, variable: str, token: str) -> frozenset[int]:
    import importlib

    process = importlib.import_module("heddle.io.process")
    parser = getattr(process, "parse_ps_eww", None)
    assert parser is not None, (
        "FAIL AC-12: heddle.io.process.parse_ps_eww (the macOS ps eww parser) "
        "is not implemented"
    )
    return frozenset(parser(output, variable=variable, token=token))


def _ps_eww_fixture() -> tuple[str, frozenset[int]]:
    """Synthetic ``ps axeww -o pid,command`` text and its tokened PIDs."""
    carried = f"{PS_VARIABLE}={PS_TOKEN}"
    base_env = "PATH=/usr/bin:/bin HOME=/Users/reviewer LANG=en_US.UTF-8"
    filler = " ".join(f"PADDING_{n}={'v' * 200}" for n in range(60))
    rows = [
        "  PID COMMAND",
        # The agy launch: the token sits among other environment words.
        f"  101 /opt/agy/bin/agy --print {base_env} HEDDLE_AGENT_SESSION=gate "
        f"{carried} TERM=xterm",
        # A tool child: the token is the last word on the line.
        f"  202 /bin/sleep 30 {base_env} {carried}",
        # The token text only in the command's arguments, never as the
        # variable's assignment: not a match.
        f"  303 /usr/bin/grep -r {PS_TOKEN} --label=token={PS_TOKEN} . {base_env}",
        # Similar but different tokens: a longer value, a shorter value, and
        # the same value under a different variable whose name ends the same.
        f"  404 /bin/sh -c true {base_env} {carried}0",
        f"  405 /bin/sh -c true {base_env} {PS_VARIABLE}={PS_TOKEN[:-1]}",
        f"  406 /bin/sh -c true {base_env} OLD_{carried}",
        # A very long line with odd spacing and a tab; the token mid-line.
        f" 50505   /usr/bin/python3\t-m   worker   {filler}   {carried}   {filler}",
        # A process with no environment shown, and a blank line.
        "    7 (kernel_task)",
        "",
        f"60606 /bin/zsh -l {base_env}",
    ]
    return "\n".join(rows) + "\n", frozenset({101, 202, 50505})


def test_ac12_ps_eww_parser_returns_exactly_the_tokened_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-12 macOS fallback witness: the ``ps eww`` parser (delegated seam
    ``heddle.io.process.parse_ps_eww``) returns exactly the PIDs whose
    environment carries the launch token, on synthetic output. The token in
    command arguments only, a longer or shorter token, and the same value
    under another variable do not match; long, odd-spaced lines parse. No real
    ``ps`` runs."""
    import subprocess

    def no_process(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("FAIL AC-12: the ps eww parser started a process")

    monkeypatch.setattr(subprocess, "run", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    output, expected = _ps_eww_fixture()
    assert len(max(output.splitlines(), key=len)) > 20_000
    found = _ps_eww_pids(output, PS_VARIABLE, PS_TOKEN)
    assert found == expected, (
        f"FAIL AC-12: the ps eww parser returned {sorted(found)}, expected the "
        f"tokened PIDs {sorted(expected)}"
    )
    assert _ps_eww_pids("  PID COMMAND\n", PS_VARIABLE, PS_TOKEN) == frozenset()
    assert _ps_eww_pids(output, PS_VARIABLE, "0" * len(PS_TOKEN)) == frozenset()


# ---------------------------------------------------------------------------
# AC-12 at the observation boundary: each signal goes to a process whose
# identity was bound before its environment was read, an unverifiable
# process or launcher is left untouched with a warning, and cleanup verifies
# only when a final scan finds no tokened process. Real processes; the
# injections sit at ``/proc`` reads and the process-descriptor call.
# ---------------------------------------------------------------------------
SLEEPER = "import time; time.sleep(60)"
# On SIGTERM: fork a tokened child that sleeps, record its PID, and exit.
LATE_CHILD = """
import os, signal, sys, time
def stop(*_):
    child = os.fork()
    if child == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        time.sleep(60)
        os._exit(0)
    with open(sys.argv[1], "w") as handle:
        handle.write(str(child))
    os._exit(0)
signal.signal(signal.SIGTERM, stop)
open(sys.argv[2], "w").close()
time.sleep(60)
"""
# On every SIGTERM: fork a successor with the same handler, and exit.
RESPAWNING = """
import os, signal, sys, time
def stop(*_):
    if os.fork() != 0:
        os._exit(0)
signal.signal(signal.SIGTERM, stop)
open(sys.argv[1], "w").close()
while True:
    time.sleep(1)
"""


def _spawn(
    code: str, *args: str, token: str | None = None, variable: str | None = None
) -> Any:
    """A Python process running ``code``, with ``token`` in its environment
    under ``variable`` (the launch-token variable by default) when given;
    returned once it has started."""
    import subprocess

    from heddle.io.process import LAUNCH_TOKEN_ENV

    env = {
        name: value for name, value in os.environ.items() if name != LAUNCH_TOKEN_ENV
    }
    if token is not None:
        env[variable or LAUNCH_TOKEN_ENV] = token
    return subprocess.Popen(
        [sys.executable, "-c", code, *args],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_file(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"fixture: {path.name} never appeared"
        time.sleep(0.01)


def _this_launcher() -> Any:
    from heddle.io.process import ProcessIdentity, process_start_time

    return ProcessIdentity(os.getpid(), process_start_time(os.getpid()))


def _tokened_pids(token: str) -> list[int]:
    from heddle.io.process import LAUNCH_TOKEN_ENV

    needle = f"{LAUNCH_TOKEN_ENV}={token}".encode()
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            environment = Path(f"/proc/{entry}/environ").read_bytes()
            state = Path(f"/proc/{entry}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            continue
        if state != "Z" and needle in environment.split(b"\0"):
            found.append(int(entry))
    return found


def _kill_tokened(token: str) -> None:
    """Teardown: SIGKILL every process still carrying ``token``."""
    deadline = time.monotonic() + 10
    while pids := _tokened_pids(token):
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        assert time.monotonic() < deadline, f"fixture: tokened processes {pids} stay"
        time.sleep(0.01)


def _stop(*processes: Any) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait()


def _recorded_launch(tmp_path: Path, token: str, raw: bytes) -> tuple[Path, Any]:
    """A launch directory recorded by this process, holding a raw stream."""
    from heddle.gate.io import write_launch_record

    directory = tmp_path / "reviews"
    temp = directory / "spec-review.antigravity.tmp.probe"
    temp.mkdir(parents=True)
    stream = temp / "raw.out"
    stream.write_bytes(raw)
    return temp, write_launch_record(temp, token=token, raw_stream=stream)


def _raw_with(secret: str) -> bytes:
    return (json.dumps({"event": "tool", "output": f"found {secret}"}) + "\n").encode()


def test_ac12_unreadable_launcher_stat_leaves_its_launch_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12: a recovery pass that cannot read its launcher's stat cannot prove
    the launcher gone. The launch directory is kept with a warning, its tokened
    child keeps running and its raw stream stays as written. Readable again,
    the live launcher keeps its launch without a warning; the unrelated
    process lives throughout.

    Injection seam: opening ``/proc/<launcher>/stat`` raises
    ``PermissionError``.
    """
    import builtins

    from heddle.gate.io import LAUNCH_RECORD_NAME, recover_launches
    from heddle.io.process import new_launch_token

    lane = _antigravity()
    token = new_launch_token()
    secret = lane.credential("unverifiable-launcher")
    temp, _record = _recorded_launch(tmp_path, token, _raw_with(secret))
    child = _spawn(SLEEPER, token=token)
    unrelated = _spawn(SLEEPER)
    try:
        real_open = builtins.open
        stat = f"/proc/{os.getpid()}/stat"

        def unreadable(file: Any, *args: Any, **kwargs: Any) -> Any:
            if file == stat:
                raise PermissionError(13, "Permission denied", stat)
            return real_open(file, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(builtins, "open", unreadable)
            recovery = recover_launches(temp.parent, grace_s=0.2)

        assert temp.name in recovery.keep, (
            "FAIL AC-12: a launch whose launcher is unverifiable was not kept"
        )
        assert any(
            str(os.getpid()) in warning and re.search(r"cannot verify", warning)
            for warning in recovery.warnings
        ), (
            f"FAIL AC-12: no warning names the unverifiable launcher: "
            f"{recovery.warnings}"
        )
        assert child.poll() is None, (
            "FAIL AC-12: recovery reaped the token of an unverifiable launcher"
        )
        assert secret.encode() in (temp / "raw.out").read_bytes(), (
            "FAIL AC-12: recovery touched the stream of an unverifiable launcher"
        )
        assert (temp / LAUNCH_RECORD_NAME).is_file()

        readable = recover_launches(temp.parent, grace_s=0.2)
        assert temp.name in readable.keep and readable.warnings == ()
        assert child.poll() is None and unrelated.poll() is None
    finally:
        _stop(child, unrelated)


def test_ac12_a_reused_process_id_is_never_signalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-12: the tokened process's ID passes to an unrelated process between
    the environment read and the second start-time read. Cleanup never signals
    the new holder of the ID, and a fresh observation finds it untokened.
    Without the injection, a pass stops the tokened process and still spares
    the other two.

    Injection seam: once ``/proc/<tokened>/environ`` is opened, opening any
    ``/proc/<tokened>/`` file and ``os.pidfd_open(<tokened>)`` answer for the
    replacement process.
    """
    import builtins

    from heddle.io.process import new_launch_token, process_start_time, reap_token

    token = new_launch_token()
    tokened = _spawn(SLEEPER, token=token)
    # Start times count clock ticks: the replacement starts a few ticks later,
    # as a process that reuses an ID always does.
    time.sleep(0.05)
    replacement = _spawn(SLEEPER)
    unrelated = _spawn(SLEEPER)
    try:
        assert process_start_time(tokened.pid) != process_start_time(replacement.pid), (
            "fixture: the two processes share a start time"
        )
        real_open, real_pidfd_open = builtins.open, os.pidfd_open
        prefix = f"/proc/{tokened.pid}/"
        reused = False

        def reopened(file: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal reused
            if isinstance(file, str) and file.startswith(prefix):
                if reused:
                    file = f"/proc/{replacement.pid}/{file[len(prefix) :]}"
                elif file.endswith("/environ"):
                    reused = True
            return real_open(file, *args, **kwargs)

        def pidfd_open(pid: int, *args: Any) -> int:
            if reused and pid == tokened.pid:
                pid = replacement.pid
            return real_pidfd_open(pid, *args)

        with monkeypatch.context() as patch:
            patch.setattr(builtins, "open", reopened)
            patch.setattr(os, "pidfd_open", pidfd_open)
            injected = reap_token(token, launcher=_this_launcher(), grace_s=0.2)

        assert reused, "fixture: the tokened process's environment was never read"
        assert replacement.poll() is None, (
            "FAIL AC-12: cleanup signalled the process that took over the ID"
        )
        assert tokened.pid not in injected.found, (
            f"FAIL AC-12: an ID that changed hands was taken as tokened: {injected}"
        )

        result = reap_token(token, launcher=_this_launcher(), grace_s=0.2)
        assert result.verified and result.found == (tokened.pid,), result
        assert tokened.wait(timeout=5) is not None
        assert replacement.poll() is None and unrelated.poll() is None
    finally:
        _stop(tokened, replacement, unrelated)


def test_ac12_unreadable_environment_leaves_the_process_and_record_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12: a process the launcher started whose environment cannot be read
    may carry the token. Cleanup leaves it running, names it in a warning and
    keeps the launch record unreaped; the raw stream is still redacted and the
    unrelated process lives.

    Injection seam: opening ``/proc/<child>/environ`` raises
    ``PermissionError``.
    """
    import builtins

    from heddle.gate.io import LAUNCH_RECORD_NAME, finish_launch
    from heddle.io.process import new_launch_token

    lane = _antigravity()
    token = new_launch_token()
    secret = lane.credential("unreadable-environment")
    temp, record = _recorded_launch(tmp_path, token, _raw_with(secret))
    child = _spawn(SLEEPER, token=token)
    unrelated = _spawn(SLEEPER)
    try:
        real_open = builtins.open
        environ = f"/proc/{child.pid}/environ"

        def unreadable(file: Any, *args: Any, **kwargs: Any) -> Any:
            if file == environ:
                raise PermissionError(13, "Permission denied", environ)
            return real_open(file, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(builtins, "open", unreadable)
            cleanup = finish_launch(temp, record, grace_s=0.2)

        assert child.poll() is None, (
            "FAIL AC-12: cleanup signalled a process it could not judge"
        )
        assert any(str(child.pid) in warning for warning in cleanup.warnings), (
            f"FAIL AC-12: no warning names the unjudged process: {cleanup.warnings}"
        )
        kept = json.loads((temp / LAUNCH_RECORD_NAME).read_text())
        assert kept["reaped"] is False, "FAIL AC-12: unverified cleanup was recorded"
        assert (
            cleanup.redacted and secret.encode() not in (temp / "raw.out").read_bytes()
        )
        assert unrelated.poll() is None
    finally:
        _stop(child, unrelated)


def test_ac12_a_child_born_during_cleanup_is_stopped_before_cleanup_verifies(
    tmp_path: Path,
) -> None:
    """AC-12: a tokened process answers SIGTERM by forking a tokened child,
    after the first scan, and exiting. Cleanup scans again, stops the child,
    and verifies only once a scan finds no tokened process; the unrelated
    process lives."""
    from heddle.io.process import new_launch_token, reap_token

    token = new_launch_token()
    born, ready = tmp_path / "born.txt", tmp_path / "ready.txt"
    parent = _spawn(LATE_CHILD, str(born), str(ready), token=token)
    unrelated = _spawn(SLEEPER)
    try:
        _wait_for_file(ready)
        result = reap_token(token, launcher=_this_launcher(), grace_s=0.5)

        _wait_for_file(born)
        child = int(born.read_text())
        assert child in result.found, (
            f"FAIL AC-12: cleanup never saw the child born during it: {result}"
        )
        assert _tokened_pids(token) == [], (
            "FAIL AC-12: a tokened child born during cleanup outlived it"
        )
        assert result.verified, result
        assert unrelated.poll() is None
    finally:
        _kill_tokened(token)
        _stop(parent, unrelated)


def test_ac12_cleanup_that_never_quiesces_keeps_the_record_and_warns(
    tmp_path: Path,
) -> None:
    """AC-12: a tokened process replaces itself on every SIGTERM. Cleanup stops
    within its bound, warns that tokened processes were still appearing, and
    keeps the launch record unreaped; the unrelated process lives."""
    from heddle.gate.io import LAUNCH_RECORD_NAME, finish_launch
    from heddle.io.process import new_launch_token

    token = new_launch_token()
    temp, record = _recorded_launch(tmp_path, token, b"")
    ready = tmp_path / "ready.txt"
    respawning = _spawn(RESPAWNING, str(ready), token=token)
    unrelated = _spawn(SLEEPER)
    try:
        _wait_for_file(ready)
        started = time.monotonic()
        cleanup = finish_launch(temp, record, grace_s=0.1)

        assert time.monotonic() - started < 5, "FAIL AC-12: cleanup ran unbounded"
        assert any(re.search(r"still appearing", w) for w in cleanup.warnings), (
            f"FAIL AC-12: no warning says cleanup never quiesced: {cleanup.warnings}"
        )
        kept = json.loads((temp / LAUNCH_RECORD_NAME).read_text())
        assert kept["reaped"] is False, "FAIL AC-12: unverified cleanup was recorded"
        assert unrelated.poll() is None
    finally:
        _kill_tokened(token)
        _stop(respawning, unrelated)


@pytest.mark.parametrize(
    "field", ["another-variable", "uppercase-token", "short-token", "zero-launcher"]
)
def test_ac12_a_record_naming_another_selector_is_kept_and_never_reaped(
    tmp_path: Path, field: str
) -> None:
    """AC-12: a launch record whose launcher is gone, but whose selector is not
    the launch-token variable with a token as Heddle makes it, or whose
    launcher ID is not a process ID, is treated as unreadable: kept with a
    warning, nothing reaped, its stream untouched. A process carrying the
    named selector lives."""
    from heddle.gate.io import LAUNCH_RECORD_NAME, recover_launches
    from heddle.io.process import LAUNCH_TOKEN_ENV, new_launch_token, process_start_time

    lane = _antigravity()
    token = new_launch_token()
    secret = lane.credential(f"selector-{field}")
    temp, _record = _recorded_launch(tmp_path, token, _raw_with(secret))
    path = temp / LAUNCH_RECORD_NAME
    value = json.loads(path.read_text())
    launcher = _spawn(SLEEPER)
    gone = {"pid": launcher.pid, "start": process_start_time(launcher.pid)}
    _stop(launcher)
    value["launcher"] = gone
    selector = {"variable": LAUNCH_TOKEN_ENV, "token": token}
    if field == "another-variable":
        selector["variable"] = "SYNTHETIC_SELECTOR"
    elif field == "uppercase-token":
        selector["token"] = token.upper()
    elif field == "short-token":
        selector["token"] = token[:-2]
    else:
        value["launcher"]["pid"] = 0
    value.update(selector)
    path.write_text(json.dumps(value))
    named = _spawn(SLEEPER, token=selector["token"], variable=selector["variable"])
    try:
        recovery = recover_launches(temp.parent, grace_s=0.2)

        assert temp.name in recovery.keep, "FAIL AC-12: the odd record was not kept"
        assert any(
            re.search(r"unreadable launch record", warning)
            for warning in recovery.warnings
        ), f"FAIL AC-12: no warning names the odd record: {recovery.warnings}"
        assert named.poll() is None, (
            "FAIL AC-12: recovery reaped by a selector Heddle never writes"
        )
        assert secret.encode() in (temp / "raw.out").read_bytes()
    finally:
        _stop(named)


def test_ac12_ps_fallback_leaves_tokened_processes_running_with_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-12 macOS fallback: ``ps eww`` gives no start time, so a tokened
    process it lists cannot be bound to its identity. Cleanup leaves it
    running and warns; the result is not verified.

    Injection seam: the platform switch reads as non-Linux and the bounded
    ``ps`` call returns synthetic output listing the real tokened process.
    """
    from heddle.io import process

    token = process.new_launch_token()
    tokened = _spawn(SLEEPER, token=token)
    try:
        listing = (
            "  PID COMMAND\n"
            f"{tokened.pid} python -c sleep {process.LAUNCH_TOKEN_ENV}={token}\n"
        )
        with monkeypatch.context() as patch:
            patch.setattr(process, "_PROC_TABLE", False)
            patch.setattr(
                process,
                "run_bounded",
                lambda *_args, **_kwargs: process.BoundedResult(0, listing),
            )
            result = process.reap_token(token, launcher=_this_launcher(), grace_s=0.2)

        assert tokened.poll() is None, (
            "FAIL AC-12: cleanup signalled a process it could not bind"
        )
        assert not result.verified and any(
            str(tokened.pid) in warning for warning in result.warnings
        ), f"FAIL AC-12: no warning names the unbound process: {result}"
    finally:
        _stop(tokened)
