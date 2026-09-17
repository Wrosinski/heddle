from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


def _completed_monitor():
    from heddle.io.process import MonitorResult, Termination

    return MonitorResult(
        exit_code=0,
        termination=Termination.COMPLETED,
        saw_stream_activity=True,
        inactivity_seconds=0,
        inactivity_marker="",
        checkpoints=[],
    )


def _build_sample_context(gate_type, plan_path, spec_path):
    """Build context with explicit metadata from the fixed milestone-2 fixture."""
    from heddle.gate.extraction import (
        extract_feature_spec_ac_ids,
        extract_markdown_section,
        extract_milestone_section,
    )
    from heddle.gate.types import GateContext

    plan_content = plan_path.read_text()
    spec_content = spec_path.read_text()
    repo_root = plan_path.resolve().parent.parent
    block = extract_markdown_section(plan_content, "## Milestones") or plan_content
    milestone = "2"  # the sample fixture plan's in-progress milestone
    section = extract_milestone_section(block, milestone)
    return GateContext(
        gate_type=gate_type,
        workspace_dir=plan_path.resolve().parent,
        feature="sample-feature",
        repo_root=repo_root,
        plan_path=plan_path.resolve(),
        spec_path=spec_path.resolve(),
        milestone=milestone,
        tier="2",
        milestone_title="Milestone 2: Integration",
        milestone_section=section,
        satisfies_ac_ids=["AC-3", "AC-4"],
        owned_paths=["src/example/sample/core.py", "config/sample/default.yaml"],
        spec_ac_ids=extract_feature_spec_ac_ids(spec_content),
        plan_content=plan_content,
        spec_content=spec_content,
    )


def _prepare_sample_context(context, diff_path: Path):
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.preparation import prepare_gate_run
    from heddle.gate.prompt import resolve_gate_prompt
    from heddle.gate.types import DiffResult, GateInvocationOverrides

    invocation = resolve_gate_execution(
        context.gate_type, GateInvocationOverrides(cli="claude")
    )
    context.prepared_run = prepare_gate_run(
        context,
        gate_type=context.gate_type,
        invocation=invocation,
        prompt=resolve_gate_prompt(context, context.gate_type, cli="claude"),
        diff=DiffResult(
            source="provided-file",
            source_detail=str(diff_path),
            diff_path=diff_path,
            changed_files=(context.owned_paths[0],),
        ),
    )
    return context


class TestDiffHardening:
    @REQUIRES_IMPL
    def test_provided_diff_path_requires_existing_file(self, tmp_path: Path) -> None:
        from heddle.gate.diff import resolve_diff

        missing = tmp_path / "missing.diff"

        with pytest.raises(FileNotFoundError, match="Provided diff file not found"):
            resolve_diff(diff_path=missing, repo_root=tmp_path)

    @REQUIRES_IMPL
    def test_run_git_returns_empty_string_on_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from heddle.gate import diff as diff_module

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(
                cmd=kwargs["args"] if "args" in kwargs else args[0],
                timeout=kwargs["timeout"],
            )

        monkeypatch.setattr(diff_module.git.subprocess, "run", fake_run)

        data, _warning = diff_module._run_git_result(["git", "diff", "HEAD"], tmp_path)
        assert data == ""

    @REQUIRES_IMPL
    def test_resolve_diff_collects_git_failure_warnings(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from heddle.gate import diff as diff_module

        responses = iter(
            [
                ("", "git ls-files --others exited 1: untracked probe failed"),
                ("", "git diff HEAD exited 1: bad head"),
                ("", "git diff --cached exited 1: bad cache"),
                ("", "git diff HEAD^ HEAD exited 1: missing parent"),
            ]
        )

        monkeypatch.setattr(diff_module.git, "run_git", lambda *_: next(responses))

        result = diff_module.resolve_diff(diff_path=None, repo_root=tmp_path)

        assert result.source == "none"
        # The untracked-file probe (git ls-files --others) runs first, so its
        # failure warning is collected ahead of the three git-diff attempts.
        assert result.warnings == (
            "git ls-files --others exited 1: untracked probe failed",
            "git diff HEAD exited 1: bad head",
            "git diff --cached exited 1: bad cache",
            "git diff HEAD^ HEAD exited 1: missing parent",
        )


class TestGateDoctorInputValidation:
    @REQUIRES_IMPL
    @pytest.mark.parametrize(
        ("summary_name", "summary_content", "expected_text"),
        (
            ("missing.json", None, "cannot read summary"),
            ("invalid.json", "{not-json", "invalid json"),
        ),
    )
    def test_gate_doctor_reports_input_errors_cleanly(
        self,
        tmp_path: Path,
        summary_name: str,
        summary_content: str | None,
        expected_text: str,
    ) -> None:
        summary_path = tmp_path / summary_name
        if summary_content is not None:
            summary_path.write_text(summary_content)

        result = subprocess.run(
            [
                sys.executable,
                "scripts/gate_doctor.py",
                "--summary",
                str(summary_path),
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert expected_text in result.stderr.lower()

    @REQUIRES_IMPL
    def test_gate_doctor_handles_malformed_summary_payload(
        self,
        tmp_path: Path,
    ) -> None:
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "status": "failure",
                    "reason": "inactivity-timeout",
                    "feature": "test-feature",
                    "gate": "self-review",
                    "cli": "codex",
                    "codex_events": {"tool_event_count": "not-a-number"},
                    "io": {"raw_bytes": "nan"},
                    "runtime": {"inactivity_observed_seconds": "nan"},
                }
            )
        )

        result = subprocess.run(
            [
                sys.executable,
                "scripts/gate_doctor.py",
                "--summary",
                str(summary_path),
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["category"] == "malformed-summary"


class TestIoReset:
    @REQUIRES_IMPL
    def test_summary_emitter_handles_stdout_without_fileno(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sys
        from io import StringIO

        from heddle.gate.io import SummaryEmitter

        monkeypatch.setattr(sys, "stdout", StringIO())

        emitter = SummaryEmitter()
        emitter.emit("ignored")
        emitter.close()

    def test_atomic_summary_failure_preserves_previous_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from heddle.gate import io as gate_io
        from heddle.gate.summary import write_summary

        path = tmp_path / "gate.gate-summary.json"
        path.write_text('{"old": true}', encoding="utf-8")

        class Summary:
            @staticmethod
            def to_dict():
                return {"new": True}

        def fail_replace(_source, _destination):
            raise OSError("injected replace failure")

        monkeypatch.setattr(gate_io.os, "replace", fail_replace)
        with pytest.raises(OSError, match="injected replace failure"):
            write_summary(path, Summary())

        assert path.read_text(encoding="utf-8") == '{"old": true}'
        assert not list(tmp_path.glob(".gate.gate-summary.json.*.tmp"))


class TestGateLocking:
    @REQUIRES_IMPL
    def test_gate_lock_rejects_incomplete_ignore_before_creating_lock(
        self,
        tmp_path: Path,
    ) -> None:
        from heddle.gate.io import gate_lock
        from heddle.kernel.project_config import KernelError

        lock_path = tmp_path / ".gate-locks" / "sample.lock"
        lock_path.parent.mkdir()
        ignore = lock_path.parent / ".gitignore"
        ignore.write_bytes(b"")

        with pytest.raises(KernelError, match=r"must contain exactly") as caught:
            with gate_lock(lock_path):
                pass
        assert caught.value.code == "workspace-invalid"

        assert ignore.read_bytes() == b""
        assert not lock_path.exists()

    @REQUIRES_IMPL
    def test_gate_lock_cleans_atomic_ignore_staging_on_publish_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from heddle.gate.io import gate_lock

        lock_path = tmp_path / ".gate-locks" / "sample.lock"

        def fail_publish(_source: object, _destination: object) -> None:
            raise OSError("injected ignore publish failure")

        monkeypatch.setattr(os, "link", fail_publish)
        with pytest.raises(OSError, match="injected ignore publish failure"):
            with gate_lock(lock_path):
                pass

        assert not (lock_path.parent / ".gitignore").exists()
        assert not list(lock_path.parent.glob(".gitignore.*.tmp"))
        assert not lock_path.exists()

    @REQUIRES_IMPL
    def test_gate_lock_blocks_concurrent_processes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from heddle.gate.io import gate_lock

        repo_root = Path(__file__).resolve().parents[2]
        lock_path = tmp_path / ".gate-locks" / "sample.lock"
        ready_path = tmp_path / "ready"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                dedent("""
                    import sys
                    import time
                    from pathlib import Path

                    sys.path.insert(0, sys.argv[1])

                    from heddle.gate.io import gate_lock

                    lock_path = Path(sys.argv[2])
                    ready_path = Path(sys.argv[3])

                    with gate_lock(lock_path):
                        ready_path.write_text("locked")
                        time.sleep(0.9)
                    """),
                str(repo_root / "src"),
                str(lock_path),
                str(ready_path),
            ]
        )
        try:
            deadline = time.monotonic() + 5
            while not ready_path.exists():
                if time.monotonic() >= deadline:
                    raise AssertionError("child never acquired gate lock")
                time.sleep(0.05)

            started_at = time.monotonic()
            with gate_lock(lock_path):
                pass
            assert time.monotonic() - started_at >= 0.5
            assert "waiting for gate lock sample" in capsys.readouterr().err
        finally:
            child.wait(timeout=5)

    def test_gate_lock_repeats_wait_heartbeat(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from heddle.gate.io import gate_lock
        from heddle.kernel import locking

        attempts = 0
        real_flock = locking.fcntl.flock

        def contended_then_acquired(descriptor: int, operation: int) -> None:
            nonlocal attempts
            if operation & locking.fcntl.LOCK_NB and attempts < 3:
                attempts += 1
                raise BlockingIOError
            real_flock(descriptor, operation)

        monkeypatch.setattr(locking.fcntl, "flock", contended_then_acquired)
        monkeypatch.setattr(locking, "LOCK_HEARTBEAT_SECONDS", 0.0)
        monkeypatch.setattr(locking.time, "sleep", lambda _seconds: None)

        with gate_lock(tmp_path / ".gate-locks/sample.lock"):
            pass

        assert capsys.readouterr().err.count("waiting for gate lock sample") == 3


def test_rerun_publication_preserves_each_canonical_result_and_view(
    tmp_path: Path,
) -> None:
    from heddle.gate.io import GatePaths, publish_review_result

    workspace = tmp_path / "feature"
    reviews = workspace / "reviews"
    reviews.mkdir(parents=True)
    attempt = reviews / "attempt"
    attempt.mkdir()
    paths = GatePaths(
        output=reviews / "self-review.md",
        log=reviews / "self-review.log",
        summary=reviews / "self-review.gate-summary.json",
        prompt=attempt / "prompt.md",
        raw_out=attempt / "raw.out",
        last_message=attempt / "last-message.txt",
        json_message=attempt / "json-message.txt",
        filtered_jsonl=attempt / "events.jsonl",
        command_output=attempt / "command-output.txt",
        temp_dir=attempt,
    )
    first = b'{"content":{"summary":"First immutable result"}}\n'
    second = b'{"content":{"summary":"Second immutable result"}}\n'
    initial, warnings = publish_review_result(
        paths, first, "First view", workspace=workspace
    )
    assert not warnings
    changed, warnings = publish_review_result(
        paths, second, "Second view", workspace=workspace
    )
    assert not warnings
    assert initial.output != changed.output
    assert initial.output.read_bytes() == first
    assert changed.output.read_bytes() == second
    for published, expected in ((initial, "First view"), (changed, "Second view")):
        view = published.output.with_name(
            published.output.name.removesuffix(".review.json") + ".md"
        )
        assert view.read_text() == expected
    replay, warnings = publish_review_result(
        paths, first, "First view", workspace=workspace
    )
    assert not warnings and replay.output == initial.output
    assert (
        initial.output.read_bytes() == first and changed.output.read_bytes() == second
    )
