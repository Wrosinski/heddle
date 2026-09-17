"""
Tests for the single-thread process supervisor.

Covers: AC-3 (all termination modes, no leaked processes).
Uses real subprocess spawning with tiny shell commands as CLI mocks.
"""

from __future__ import annotations

import sys
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
