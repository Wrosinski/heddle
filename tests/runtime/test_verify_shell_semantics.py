"""
Verification commands run under ``sh -c`` (commands dogfood, user-sanctioned
2026-07-06).

The previous ``shlex.split`` + exec handed an ``&&``-chained verification
command to the FIRST program as literal arguments — commands core's chain became a
single pytest invocation exiting 4 (``unrecognized arguments: --files
--json``), forever. Milestone verification payloads are naturally authored
as chains (every commands milestone uses one); the command string is a trusted
authorship surface, same as ``autopilot.test_command``.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from heddle.kernel.project_config import KernelError
from heddle.runtime.verify_exec import (
    VERIFY_LAUNCH_FAILURE_EXIT,
    run_verification_command,
)


def _run(tmp_path: Path, command: str) -> int:
    return run_verification_command(tmp_path, command, tmp_path / "verify.log")


def test_and_chain_executes_as_a_shell_chain(tmp_path: Path) -> None:
    marker = tmp_path / "second-segment-ran"
    exit_code = _run(tmp_path, f"true && touch {marker} && exit 7")
    assert exit_code == 7, (
        "FAIL: an && chain must run under a shell and propagate the chain's "
        f"exit code, got {exit_code}"
    )
    assert marker.exists(), "FAIL: the second chain segment must execute"


def test_chain_short_circuits_on_failure(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    exit_code = _run(tmp_path, f"false && touch {marker}")
    assert exit_code == 1
    assert not marker.exists(), (
        "FAIL: && must short-circuit — the old exec-without-shell would have "
        "passed '&&' and the touch args to `false` and never created it, "
        "masking the semantics"
    )


def test_single_command_still_works(tmp_path: Path) -> None:
    assert _run(tmp_path, "true") == 0
    assert _run(tmp_path, "exit 3") == 3


def test_missing_executable_is_a_failing_verification(tmp_path: Path) -> None:
    # review contract preserved under sh -c: sh exits 127 for an unknown
    # command, matching VERIFY_LAUNCH_FAILURE_EXIT.
    exit_code = _run(tmp_path, "definitely-not-a-real-binary-xyz")
    assert exit_code == VERIFY_LAUNCH_FAILURE_EXIT


def test_empty_command_stays_failing(tmp_path: Path) -> None:
    # `sh -c ""` exits 0 — an empty stored command must NOT silently green
    # a milestone. Even this pre-launch failure must publish a resolvable empty
    # log because run_verify records the returned exit code as a fact.
    assert _run(tmp_path, "") == VERIFY_LAUNCH_FAILURE_EXIT
    log_path = tmp_path / "verify.log"
    assert log_path.is_file(), (
        "FAIL: an empty-command verification fact must not point at a missing log"
    )
    assert log_path.read_bytes() == b""
    log_path.unlink()

    assert _run(tmp_path, "   ") == VERIFY_LAUNCH_FAILURE_EXIT
    assert log_path.is_file(), (
        "FAIL: a whitespace-command verification fact must not point at a missing log"
    )
    assert log_path.read_bytes() == b""


def test_malformed_syntax_is_a_failing_verification(tmp_path: Path) -> None:
    # review contract preserved: an unbalanced quote is a failing
    # verification (sh syntax error, nonzero), never a raw exception.
    exit_code = _run(tmp_path, 'pytest "tests/unterminated')
    assert exit_code != 0


def test_log_is_published_only_after_the_command_finishes(tmp_path: Path) -> None:
    root = tmp_path / "host"
    root.mkdir()
    assert not (root / ".git").exists(), "test setup requires a non-git root"
    log_path = root / "verification" / "verify.log"
    log_path.parent.mkdir()
    command = f"test ! -e {shlex.quote(str(log_path))} && printf 'verification ok\\n'"

    exit_code = run_verification_command(root, command, log_path)

    assert exit_code == 0, (
        "FAIL: the final verification log must stay outside the worktree until "
        "the inspected command finishes"
    )
    assert log_path.read_text(encoding="utf-8") == "verification ok\n"
    assert not list(tmp_path.glob(".host.heddle-verify-*")), (
        "FAIL: external verification capture directories must be cleaned"
    )


def test_monitor_capture_is_external_and_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import heddle.runtime.verify_exec as verify_exec

    root = tmp_path / "non-git-host"
    root.mkdir()
    assert not (root / ".git").exists(), "test setup requires a non-git root"
    log_path = root / "verification" / "verify.log"
    log_path.parent.mkdir()
    captures: list[Path] = []

    def fake_run_monitored(_argv, raw_out: Path, _cfg):
        captures.append(raw_out)
        assert not raw_out.resolve().is_relative_to(root.resolve()), (
            "FAIL: monitored verification output must be captured outside the "
            "project root"
        )
        raw_out.write_bytes(b"external capture\n")
        return SimpleNamespace(exit_code=0)

    monkeypatch.setattr(verify_exec.process, "run_monitored", fake_run_monitored)

    assert run_verification_command(root, "true", log_path) == 0
    assert log_path.read_bytes() == b"external capture\n"
    assert captures and all(not path.exists() for path in captures), (
        "FAIL: completed external capture files must be removed after publication"
    )


def test_capture_base_inside_workspace_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import heddle.runtime.verify_exec as verify_exec

    root = tmp_path / "host"
    capture_base = root / "scratch"
    capture_base.mkdir(parents=True)
    log_path = root / "verification" / "verify.log"
    monkeypatch.setattr(verify_exec, "gettempdir", lambda: str(capture_base))

    with pytest.raises(KernelError) as excinfo:
        run_verification_command(root, "true", log_path)

    assert excinfo.value.code == "workspace-invalid", (
        "FAIL: TMPDIR inside the workspace must be refused before verification "
        f"runs, got {excinfo.value!r}"
    )
    assert not log_path.exists()


def test_launch_failure_installs_empty_log_and_cleans_external_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import heddle.runtime.verify_exec as verify_exec

    root = tmp_path / "host"
    root.mkdir()
    log_path = root / "verification" / "verify.log"
    log_path.parent.mkdir()
    captures: list[Path] = []

    def fail_after_open(_argv, raw_out: Path, _cfg):
        captures.append(raw_out)
        raw_out.touch()
        raise verify_exec.process.MonitorLaunchError("injected launch failure")

    monkeypatch.setattr(verify_exec.process, "run_monitored", fail_after_open)

    assert (
        run_verification_command(root, "true", log_path) == VERIFY_LAUNCH_FAILURE_EXIT
    )
    assert log_path.is_file(), (
        "FAIL: launch failure must still install the empty log referenced by its fact"
    )
    assert log_path.read_bytes() == b""
    assert captures and all(not path.exists() for path in captures), (
        "FAIL: launch-failure capture files must be cleaned after publication"
    )


def test_capture_io_failure_is_workspace_invalid_and_never_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import heddle.runtime.verify_exec as verify_exec

    root = tmp_path / "host"
    root.mkdir()
    log_path = root / "verification" / "verify.log"
    captures: list[Path] = []

    def fail_after_partial_capture(_argv, raw_out: Path, _cfg):
        captures.append(raw_out)
        raw_out.write_bytes(b"partial")
        raise OSError("injected capture I/O failure")

    monkeypatch.setattr(
        verify_exec.process, "run_monitored", fail_after_partial_capture
    )

    with pytest.raises(KernelError) as excinfo:
        run_verification_command(root, "true", log_path)

    assert excinfo.value.code == "workspace-invalid"
    assert not log_path.exists(), (
        "FAIL: partial capture bytes must never be published as exit-127 evidence"
    )
    assert captures and all(not path.exists() for path in captures), (
        "FAIL: failed external captures must be cleaned"
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HEDDLE_VERIFY_HARD_TIMEOUT_SECONDS", "not-a-number"),
        ("HEDDLE_VERIFY_INACTIVITY_TIMEOUT_SECONDS", "0"),
        ("HEDDLE_VERIFY_POLL_SECONDS", "-1"),
        ("HEDDLE_VERIFY_POLL_SECONDS", "nan"),
    ],
)
def test_invalid_verify_timeout_policy_is_typed_usage(
    name: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(KernelError, match=name) as raised:
        _run(tmp_path, "true")
    assert raised.value.code == "usage"
    assert "positive number" in raised.value.hint
