"""
write foundation inner-loop write commands.

Behavior contract: write-write-path
Primary ACs: AC-5, AC-6, AC-7, AC-8, AC-9, AC-18.

preimplementation state: all command-facing tests fail while the write dispatcher
entries still
return the command availability not-implemented stub.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.runtime.write_path_helpers import (
    authored_clean_reviews,
    authored_milestone_verification,
)
from tests.runtime.write_path_helpers import (
    copy_host as _copy_host,
)
from tests.runtime.write_path_helpers import (
    read_yaml as _read_yaml,
)
from tests.runtime.write_path_helpers import (
    write_yaml as _write_yaml,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"
EDGE = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "edge"


def _make_tiny_completed_milestone_eligible(state_path: Path) -> None:
    state = _read_yaml(state_path)
    for milestone in state["milestones"]:
        if milestone["id"] == "m1":
            milestone["status"] = "current"
            for task in milestone.get("tasks", []):
                task["status"] = "done"
        if milestone["id"] == "m2":
            milestone["status"] = "todo"
    state["verifications"] = [
        authored_milestone_verification(state_path, state["milestones"][0])
    ]
    _write_yaml(state_path, state)
    authored_clean_reviews(state_path, milestone_ids=("m1",))


def _make_tiny_all_done(state_path: Path) -> None:
    state = _read_yaml(state_path)
    facts: list[dict[str, Any]] = []
    for milestone in state["milestones"]:
        milestone["status"] = "done"
        for task in milestone.get("tasks", []):
            task["status"] = "done"
        facts.append(authored_milestone_verification(state_path, milestone))
    state["verifications"] = facts
    _write_yaml(state_path, state)


def _make_tiny_no_current_milestone(state_path: Path) -> None:
    state = _read_yaml(state_path)
    for milestone in state["milestones"]:
        milestone["status"] = "done"
        for task in milestone.get("tasks", []):
            task["status"] = "done"
    _write_yaml(state_path, state)


def _session_payload(path: Path, *, key_context: str = "new session") -> Path:
    path.write_text(
        "\n".join(
            [
                "started_at: 2026-06-15T10:00Z",
                "ended_at: 2026-06-15T10:30Z",
                "completed:",
                "  - scaffolded tests",
                "started: []",
                f"key_context: {key_context}",
                "next_steps: continue with implementation",
                "blockers: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_ac04_ac05_milestone_advance_all_done_is_idempotent_noop(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    _make_tiny_all_done(state_path)
    before = _read_yaml(state_path)

    code, out, _err = run_cli(["milestone", "advance", "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 0 and envelope["ok"] is True, (
        "FAIL AC-4/AC-5: milestone advance on an all-done workspace must be "
        "an idempotent no-op success"
    )
    after = _read_yaml(state_path)
    assert after["revision"] == before["revision"], (
        "FAIL AC-4/AC-5: all-done milestone advance no-op must not bump revision"
    )


@pytest.mark.parametrize(
    ("slug", "expected_code"),
    [
        ("blocked-two-current", "milestone-out-of-sequence"),
        ("blocked-task-incomplete", "task-incomplete"),
        ("blocked-verification-missing", "verification-missing"),
        ("blocked-deps-unmet", "milestone-out-of-sequence"),
    ],
)
def test_ac05_milestone_advance_blocks_with_matching_condition(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slug: str,
    expected_code: str,
) -> None:
    host = _copy_host(tmp_path, EDGE)
    monkeypatch.chdir(host)
    state_path = host / "plans" / slug / "state.yaml"
    before = state_path.read_bytes()
    code, out, _err = run_cli(["milestone", "advance", "--feature", slug, "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == expected_code, (
        f"FAIL AC-5: {slug} must fail as {expected_code}, got exit {code} "
        f"and envelope {envelope!r}"
    )
    assert state_path.read_bytes() == before, (
        f"FAIL AC-5: blocked milestone advance mutated {slug}"
    )


def test_ac07_verify_runs_stored_command_and_appends_unique_log_facts(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, GOLDEN)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "nl-screening" / "state.yaml"
    calls: list[tuple[Any, Path]] = []

    def fake_run_monitored(command, raw_out, cfg, **_kwargs):
        calls.append((command, raw_out))
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_text("verification ok\n", encoding="utf-8")
        return SimpleNamespace(exit_code=0)

    monkeypatch.setattr("heddle.io.process.run_monitored", fake_run_monitored)
    for _ in range(2):
        code, out, _err = run_cli(
            ["verify", "--scope", "m2", "--feature", "nl-screening", "--json"]
        )
        assert code == 0 and envelope_tools.parse(out)["ok"] is True, (
            "FAIL AC-7: verify --scope m2 must record the stored command result"
        )

    state = _read_yaml(state_path)
    facts = [fact for fact in state["verifications"] if fact["scope"] == "m2"]
    assert len(facts) == 2, (
        f"FAIL AC-7: two verify invocations must append two m2 facts, got {facts!r}"
    )
    assert len({fact["log"] for fact in facts}) == 2, (
        "FAIL AC-7: verify log filenames must be unique, including same-second "
        "runs via the post-write revision"
    )
    logs_resolve = all(
        (host / "plans" / "nl-screening" / fact["log"]).is_file() for fact in facts
    )
    assert logs_resolve, (
        "FAIL AC-7: each verification fact's log path must resolve on disk"
    )
    assert calls, "FAIL AC-7: verify must call the monitored subprocess seam"


def test_ac07_ac18_verify_records_nonzero_exit_code(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    state = _read_yaml(state_path)
    state["milestones"][0]["verification"]["command"] = (
        f"{sys.executable} -c \"import sys; print('fail'); sys.exit(17)\""
    )
    _write_yaml(state_path, state)

    code, out, _err = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "verification-failed", (
        "FAIL AC-7/AC-18: verify must record a non-zero command result and "
        "return truthful verification failure"
    )
    assert envelope["error"]["details"] == {
        "feature": "sample-feature",
        "scope": "m1",
        "command": state["milestones"][0]["verification"]["command"],
        "child_exit_code": 17,
        "log": envelope["error"]["details"]["log"],
        "recorded": True,
        "revision": state["revision"] + 1,
        "fact_index": len(state["verifications"]),
        "status": "failed",
    }
    fact = _read_yaml(state_path)["verifications"][-1]
    assert fact["scope"] == "m1" and fact["exit_code"] == 17, (
        f"FAIL AC-7/AC-18: verification fact must store real exit_code 17, got {fact!r}"
    )


def test_failed_verify_short_circuits_a_shell_controlled_next_step(
    tmp_path: Path,
) -> None:
    host = _copy_host(tmp_path, TINY, "verify-shell-control")
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    state = _read_yaml(state_path)
    state["milestones"][0]["verification"]["command"] = "exit 5"
    _write_yaml(state_path, state)
    marker = host / "must-not-run"
    cli = "from heddle.cli import main; raise SystemExit(main())"
    command = (
        f"{shlex.quote(sys.executable)} -c {shlex.quote(cli)} "
        "verify --scope m1 --feature sample-feature --json"
        f" && touch {shlex.quote(str(marker))}"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)

    completed = subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=host,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 3
    assert not marker.exists()
    envelope = json.loads(completed.stdout)
    assert envelope["error"]["code"] == "verification-failed"
    assert envelope["error"]["details"]["child_exit_code"] == 5


def test_ac07_ac18_verify_timeout_records_timeout_exit_code(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    # Inject a short timeout via the env override rather than relying on a small
    # default: verify's default now inherits gate's run-gate host policy (REQ-7),
    # so the test pins its own bound instead of coupling to the production value.
    monkeypatch.setenv("HEDDLE_VERIFY_HARD_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("HEDDLE_VERIFY_INACTIVITY_TIMEOUT_SECONDS", "1")
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    state = _read_yaml(state_path)
    state["milestones"][0]["verification"]["command"] = (
        f'{sys.executable} -c "import time; time.sleep(30)"'
    )
    _write_yaml(state_path, state)

    code, out, _err = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "verification-failed", (
        "FAIL AC-7/AC-18: verify timeout must append a failing fact and fail "
        "the invocation"
    )
    assert envelope["error"]["details"]["recorded"] is True
    assert envelope["error"]["details"]["status"] == "failed"
    fact = _read_yaml(state_path)["verifications"][-1]
    assert fact["scope"] == "m1" and fact["exit_code"] not in (0, None), (
        "FAIL AC-7/AC-18: timeout fact must carry a non-zero timeout exit_code, "
        f"got {fact!r}"
    )


def test_ac07_ac18_verify_launch_failure_records_failing_fact(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # review: a stored command whose executable cannot be launched (missing
    # binary) is a *failing* verification, not a dispatcher internal error. The
    # fact must record a non-zero exit code and its log path must still resolve
    # on disk (run_monitored creates the raw log before the failed spawn, then
    # raises per gate's contract; the runtime records the failure code).
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    state = _read_yaml(state_path)
    state["milestones"][0]["verification"]["command"] = (
        "heddle-xs-r2-nonexistent-binary --go"
    )
    _write_yaml(state_path, state)

    code, out, _err = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "verification-failed", (
        "FAIL XS-R2: a launch failure must append a failing fact and return the "
        "typed proof failure"
    )
    assert envelope["error"]["details"]["recorded"] is True
    assert envelope["error"]["details"]["status"] == "failed"
    fact = _read_yaml(state_path)["verifications"][-1]
    assert fact["scope"] == "m1" and fact["exit_code"] not in (0, None), (
        f"FAIL XS-R2: launch failure must record a non-zero exit_code, got {fact!r}"
    )
    log = host / "plans" / "sample-feature" / fact["log"]
    assert log.is_file(), (
        "FAIL XS-R2: a launch-failure fact's log path must still resolve on disk"
    )


def test_ac07_ac18_verify_publish_failure_is_fatal_and_preserves_state(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import heddle.io.process as monitor_module

    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    verification_dir = state_path.parent / "verification"
    before_state = state_path.read_bytes()
    before_logs = {
        path.relative_to(verification_dir)
        for path in verification_dir.rglob("*.log")
        if path.is_file()
    }
    captures: list[Path] = []

    def fake_run_monitored(_argv, raw_out: Path, _cfg):
        captures.append(raw_out)
        raw_out.write_bytes(b"completed but unpublished\n")
        return SimpleNamespace(exit_code=0)

    def fail_publish(_captured: Path, _destination: Path) -> None:
        raise OSError("injected verification log publication failure")

    monkeypatch.setattr(monitor_module, "run_monitored", fake_run_monitored)
    monkeypatch.setattr(monitor_module, "install_monitored_output", fail_publish)

    code, out, _err = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        "FAIL: verification-log publication failure must halt as "
        f"workspace-invalid/3, got exit {code} / {envelope!r}"
    )
    assert state_path.read_bytes() == before_state, (
        "FAIL: a verification fact must not be appended when its log could not "
        "be published"
    )
    after_logs = {
        path.relative_to(verification_dir)
        for path in verification_dir.rglob("*.log")
        if path.is_file()
    }
    assert after_logs == before_logs, (
        "FAIL: failed publication must not leave a final verification log"
    )
    assert captures and all(not path.exists() for path in captures), (
        "FAIL: external captures must be cleaned after publication failure"
    )


def test_ac18_verify_validates_context_before_command_lookup(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # review: run_verify must schema-validate the resolved context *before* the
    # stored-command lookup. A schema-ahead state under a scope that also lacks
    # a stored command (acceptance) must fail workspace-invalid/exit 3, never be
    # masked as a usage error for the missing command.
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            "schema: heddle.state/v6", "schema: heddle.state/v99"
        ),
        encoding="utf-8",
    )
    before = state_path.read_bytes()

    code, out, _err = run_cli(
        ["verify", "--scope", "acceptance", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        "FAIL XS-R5: schema-ahead state must fail workspace-invalid before the "
        f"missing-command usage check, got exit {code} / {envelope!r}"
    )
    assert state_path.read_bytes() == before, (
        "FAIL XS-R5: workspace-invalid verify must not alter state.yaml"
    )


def test_ac05_milestone_advance_blocks_on_stuck_milestone_graph(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # review: a graph with no current milestone and no promotable todo (mutually
    # blocking dependencies) must fail milestone-out-of-sequence rather than
    # advance or silently no-op. This is the kernel's "no eligible milestone"
    # guard, distinct from the current-with-unmet-dep fixtures.
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    state = _read_yaml(state_path)
    ids = [milestone["id"] for milestone in state["milestones"]]
    assert len(ids) >= 2, "fixture must have at least two milestones"
    for milestone in state["milestones"]:
        milestone["status"] = "todo"
    state["milestones"][0]["depends_on"] = [ids[1]]
    state["milestones"][1]["depends_on"] = [ids[0]]
    _write_yaml(state_path, state)
    before = state_path.read_bytes()

    code, out, _err = run_cli(
        ["milestone", "advance", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "milestone-out-of-sequence", (
        f"FAIL XS-I9: a stuck milestone graph must block, got exit {code} / "
        f"{envelope!r}"
    )
    assert state_path.read_bytes() == before, (
        "FAIL XS-I9: blocked milestone advance must leave state.yaml unchanged"
    )


def test_ac08_session_log_appends_entry_and_orient_reads_latest(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, GOLDEN)
    monkeypatch.chdir(host)
    payload = _session_payload(
        tmp_path / "session.yaml", key_context="latest M4 session"
    )
    code, out, _err = run_cli(
        [
            "session",
            "log",
            "--feature",
            "nl-screening",
            "--from-file",
            str(payload),
            "--json",
        ]
    )
    assert code == 0 and envelope_tools.parse(out)["ok"] is True, (
        "FAIL AC-8: session log must append a structured session entry"
    )
    state = _read_yaml(host / "plans" / "nl-screening" / "state.yaml")
    expected_session = {
        "started_at": "2026-06-15T10:00Z",
        "ended_at": "2026-06-15T10:30Z",
        "completed": ["scaffolded tests"],
        "started": [],
        "key_context": "latest M4 session",
        "next_steps": "continue with implementation",
        "blockers": [],
        "stage": "implement",
    }
    assert state["sessions"][-1] == expected_session, (
        "FAIL AC-8: state.yaml must contain the appended session payload"
    )

    code, out, _err = run_cli(["orient", "--feature", "nl-screening", "--json"])
    orient = envelope_tools.parse(out)
    projected_session = dict(expected_session)
    projected_session.pop("stage")
    assert (
        code == 0
        and orient["data"]["next_steps"] == "continue with implementation"
        and orient["data"]["latest_session"] == projected_session
    ), (
        "FAIL AC-8: orient must read the full latest session after session log "
        "appends it"
    )


def test_ac08_session_log_binds_omitted_stage_for_off_gate_resume(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, GOLDEN)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "tier1-quickfix" / "state.yaml"
    state = _read_yaml(state_path)
    state["stage"] = "robustness"
    _write_yaml(state_path, state)
    payload = _session_payload(
        tmp_path / "robustness-session.yaml",
        key_context="completed off-gate robustness session",
    )

    code, out, _err = run_cli(
        [
            "session",
            "log",
            "--feature",
            "tier1-quickfix",
            "--from-file",
            str(payload),
            "--json",
        ]
    )
    assert code == 0 and envelope_tools.parse(out)["ok"] is True
    recorded = _read_yaml(state_path)["sessions"][-1]
    assert recorded["stage"] == "robustness", (
        "FAIL AC-8: an interactive session without explicit provenance must "
        "bind the locked active stage"
    )
    assert "revision_before" not in recorded
    assert "revision_after" not in recorded
    assert "advanced" not in recorded

    code, out, _err = run_cli(["orient", "--feature", "tier1-quickfix", "--json"])
    orient = envelope_tools.parse(out)
    assert code == 0 and orient["data"]["entry"] == "resume", (
        "FAIL AC-8: the session fact must prevent an off-gate stage from "
        "looping back to a fresh kickoff"
    )
    assert all(
        action["action"]["kind"] != "session" for action in orient["next_actions"]
    ), "FAIL AC-8: resumed robustness must route past kickoff"


def test_ac09_retired_guards_are_absent_and_invariants_are_owned_by_writers(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-9: the old policing guards are not ported; milestone/verification
    # invariants are enforced by the write itself.
    # review: scripts carry .py/.sh suffixes, so a bare-name membership check is
    # vacuously true and would never catch a re-ported guard. Normalize the
    # extension before asserting absence.
    script_names = {
        path.name.removesuffix(".py").removesuffix(".sh")
        for path in (REPO_ROOT / "scripts").glob("*")
    }
    assert "check-stale-current-milestone" not in script_names
    assert "check-verification-log-completeness" not in script_names

    host = _copy_host(tmp_path, EDGE)
    monkeypatch.chdir(host)
    slug = "blocked-verification-missing"
    state_path = host / "plans" / slug / "state.yaml"
    before = state_path.read_bytes()
    code, out, _err = run_cli(["milestone", "advance", "--feature", slug, "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "verification-missing", (
        "FAIL AC-9: verification completeness must be owned by milestone "
        "advance via recorded verification facts"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-9: invariant-owned failure mutated state.yaml"
    )


@pytest.mark.parametrize(
    ("argv", "expected_exit", "expected_code"),
    [
        (["milestone", "advance"], 2, "feature-ambiguous"),
        (
            ["milestone", "advance", "--feature", "blocked-not-authorized"],
            3,
            "not-authorized",
        ),
        (["milestone", "advance", "--feature", "gate-stage-converged"], 2, "usage"),
        (["task", "done", "missing", "--feature", "sample-feature"], 2, "usage"),
        (
            ["task", "current", "missing", "--feature", "sample-feature"],
            2,
            "usage",
        ),
        (["verify", "--feature", "tier1-quickfix"], 2, "usage"),
        (
            [
                "session",
                "log",
                "--from-file",
                "missing.yaml",
                "--feature",
                "nl-screening",
            ],
            2,
            "usage",
        ),
        (
            [
                "phase-exit",
                "--feature",
                "blocked-task-incomplete",
                "--through",
                "bogus-stage",
            ],
            2,
            "usage",
        ),
    ],
)
def test_ac18_inner_loop_error_contracts_leave_state_unchanged(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected_exit: int,
    expected_code: str,
) -> None:
    fixture = GOLDEN if "tier1-quickfix" in argv or "nl-screening" in argv else EDGE
    host = _copy_host(tmp_path, fixture)
    if "sample-feature" in argv:
        host = _copy_host(tmp_path, TINY, "tiny")
    monkeypatch.chdir(host)
    before = {
        path.relative_to(host): path.read_bytes() for path in host.rglob("state.yaml")
    }
    code, out, _err = run_cli([*argv, "--json"])
    envelope = envelope_tools.parse(out)
    assert code == expected_exit and envelope["error"]["code"] == expected_code, (
        f"FAIL AC-18: {argv} expected exit {expected_exit}/{expected_code}, "
        f"got exit {code} and {envelope!r}"
    )
    after = {
        path.relative_to(host): path.read_bytes() for path in host.rglob("state.yaml")
    }
    assert after == before, f"FAIL AC-18: error path {argv} mutated state.yaml"


def test_ac18_unparsable_state_exits_three_and_writes_nothing(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    state_path.write_text("schema: [\nrevision: 7\n", encoding="utf-8")
    before = state_path.read_bytes()

    code, out, _err = run_cli(
        ["task", "add", "bad state", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        "FAIL AC-18: unparsable state must fail workspace-invalid/exit 3"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-18: unparsable state path must not alter state.yaml"
    )


def test_current_verify_missing_stored_command_refuses_before_write(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Current provenance requires an explicit nonempty command; the removed
    # pre-v2 verification fallback no longer maps this to legacy usage.
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    before = state_path.read_bytes()

    code, out, _err = run_cli(
        ["verify", "--scope", "acceptance", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        "FAIL AC-18: verify for a valid state whose scope has no stored command "
        "must name the missing current declaration"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-18: verify missing-command refusal changed state.yaml"
    )


def test_ac18_task_add_to_non_current_milestone_is_usage(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    _make_tiny_no_current_milestone(state_path)
    before = state_path.read_bytes()

    code, out, _err = run_cli(
        ["task", "add", "should fail", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 2 and envelope["error"]["code"] == "usage", (
        "FAIL AC-18: task add with no current milestone must be usage/exit 2"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-18: rejected task add changed state.yaml"
    )


def test_ac18_task_done_with_no_current_milestone_is_usage(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    _make_tiny_no_current_milestone(state_path)
    before = state_path.read_bytes()

    code, out, _err = run_cli(
        ["task", "done", "t1", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 2 and envelope["error"]["code"] == "usage", (
        "FAIL AC-18: task done with no current milestone must be usage/exit 2"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-18: rejected task done changed state.yaml"
    )


def test_ac18_task_current_with_no_current_milestone_is_usage(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    _make_tiny_no_current_milestone(state_path)
    before = state_path.read_bytes()

    code, out, _err = run_cli(
        ["task", "current", "t1", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 2 and envelope["error"]["code"] == "usage", (
        "FAIL AC-18: task current with no current milestone must be usage/exit 2"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-18: rejected task current changed state.yaml"
    )


@pytest.mark.parametrize(
    ("payload_text", "description"),
    [
        ("started_at: 2026-06-15T10:00Z\n", "incomplete"),
        (
            "\n".join(
                [
                    "started_at: 2026-06-15T10:00Z",
                    "ended_at: 2026-06-15T10:30Z",
                    "completed: not-a-list",
                    "started: []",
                    "key_context: wrong typed session",
                    "next_steps: continue",
                    "blockers: []",
                    "",
                ]
            ),
            "wrong-typed",
        ),
    ],
)
def test_ac18_session_log_malformed_payload_is_usage(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload_text: str,
    description: str,
) -> None:
    host = _copy_host(tmp_path, GOLDEN)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "nl-screening" / "state.yaml"
    before = state_path.read_bytes()
    payload = tmp_path / "bad-session.yaml"
    payload.write_text(payload_text, encoding="utf-8")

    code, out, _err = run_cli(
        [
            "session",
            "log",
            "--from-file",
            str(payload),
            "--feature",
            "nl-screening",
            "--json",
        ]
    )
    envelope = envelope_tools.parse(out)
    assert code == 2 and envelope["error"]["code"] == "usage", (
        f"FAIL AC-18: {description} session payload must be usage/exit 2"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-18: rejected session log changed state.yaml"
    )


# --------------------------------------------------------------------------- #
# completion (kickoff-guidance-integration) — completion close write ownership (reader)
# --------------------------------------------------------------------------- #
# Red discriminators for AC-8/AC-9/AC-10/AC-13: the qualifying `session log`
# fact owns close validation, lifecycle stamping, and the terminal append.
# Every test below fails until milestone reader lands (most pass-cases append
# generically today; refusal-cases wrongly append; the stamp never happens on
# this path today). The fixture patches the write host's `complete-boundary`
# workspace to `stage: complete`.

COMPLETED_BOUNDARY_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "workspaces" / "completed-boundary"
)

_COMPLETION_SLUG = "complete-boundary"

_COMPLETION_RETRO_TEXT = (
    "# Friction Retrospective\n\n## Tooling fights\n\n## Prompt gaps\n\n"
    "## Validator false-positives\n\n## Escalations that should have been "
    "policy-resolvable\n"
)


def _completion_close_host(
    tmp_path: Path,
    *,
    stage: str = "complete",
    authorizations: list[dict[str, Any]] | None = None,
    flow: str | None = None,
    test_command: str | None = None,
    retro: bool = True,
    journal: bool = True,
    extra_sessions: list[dict[str, Any]] | None = None,
    pending_decision: bool = False,
    ratified_principles: bool = False,
) -> SimpleNamespace:
    host = _copy_host(tmp_path, COMPLETED_BOUNDARY_FIXTURE)
    state_path = host / "plans" / _COMPLETION_SLUG / "state.yaml"
    state = _read_yaml(state_path)
    state["stage"] = stage
    state["authorized_through"] = stage
    if authorizations is not None:
        state["authorizations"] = authorizations
    if flow is not None:
        state["flow"] = flow
    if extra_sessions:
        state.setdefault("sessions", []).extend(extra_sessions)
    if pending_decision:
        state.setdefault("decisions", []).append(
            {
                "id": "completion-open",
                "kind": "question",
                "class": 1,
                "resolution": None,
                "rationale": None,
                "resolution_source": None,
                "resolved_at": None,
                "source": "completion-open",
                "title": "an ordinary open question",
                "status": "pending",
                "routes_to": [f"plans/{_COMPLETION_SLUG}/plan.md"],
                "created_at": "2026-07-14T09:00Z",
            }
        )
    from tests.operational_model_helpers import authored_evidence

    state["commands"]["acceptance_test"] = "python3 -c 'print(1)'"
    state["verifications"] = [
        authored_milestone_verification(state_path, m) for m in state["milestones"]
    ]
    paths = sorted({path for m in state["milestones"] for path in m["owns"]})
    for scope, command_key in (
        ("acceptance", "acceptance_test"),
        ("smoke", "smoke_test"),
    ):
        state["verifications"].append(
            {
                "scope": scope,
                "command": state["commands"][command_key],
                "exit_code": 0,
                "at": "2026-07-14T09:00Z",
                "log": f"verification/{scope}.log",
                "evidence": authored_evidence(host, paths, kind="feature-owned-union"),
            }
        )
    for fact in state["verifications"]:
        log = state_path.parent / fact["log"]
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("Authored green fixture evidence.\n")
    _write_yaml(state_path, state)
    if test_command is not None:
        config_path = host / ".heddle.yaml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8")
            + f"\nautopilot:\n  test_command: {test_command!r}\n",
            encoding="utf-8",
        )
    retro_path = host / "plans" / f"{_COMPLETION_SLUG}.friction-retrospective.md"
    journal_path = host / "plans" / f"{_COMPLETION_SLUG}.decision-journal.md"
    if retro:
        retro_path.write_text(_COMPLETION_RETRO_TEXT, encoding="utf-8")
    if journal:
        journal_path.write_text("# Decision Journal\n", encoding="utf-8")
    if ratified_principles:
        principles = host / "docs" / "workflow" / "engineering-principles.md"
        principles.parent.mkdir(parents=True, exist_ok=True)
        principles.write_text(
            "---\nstatus: ratified\n---\n\n# Engineering Principles\n",
            encoding="utf-8",
        )
    spec_path = host / "plans" / ".." / "docs"  # placeholder never used
    spec_path = host / "docs" / "features" / "example" / f"{_COMPLETION_SLUG}.md"
    import subprocess

    from tests.runtime.git_helpers import initialize_git_repo

    initialize_git_repo(
        lambda *args: subprocess.run(
            ["git", *args], cwd=host, check=True, capture_output=True
        ),
        email="fixture@example.invalid",
        name="Close fixture",
    )
    return SimpleNamespace(
        host=host,
        state_path=state_path,
        spec_path=spec_path,
        retro_path=retro_path,
        journal_path=journal_path,
    )


def _completion_user_grant() -> list[dict[str, Any]]:
    return [{"through": "complete", "source": "user", "at": "2026-07-14T09:00Z"}]


def _completion_lifecycle(spec_path: Path) -> str:
    import re as _re

    match = _re.search(
        r"^lifecycle:\s*(\S+)$",
        spec_path.read_text(encoding="utf-8"),
        _re.MULTILINE,
    )
    assert match is not None, "FIXTURE ROT: spec frontmatter lost lifecycle"
    return match.group(1)


def _completion_submit(run_cli, envelope_tools, *extra: str):
    code, out, _err = run_cli(
        ["feature", "complete", "--feature", _COMPLETION_SLUG, "--json", *extra]
    )
    envelope = envelope_tools.parse(out)
    envelope_tools.assert_shape(envelope)
    return code, envelope


class TestCompletionFinalCompletionWrite:
    """Existing completion audits and grants now enter through explicit completion."""

    @pytest.mark.parametrize(
        ("audit_knob", "artifact"),
        [
            ({"retro": False}, "friction retrospective"),
            ({"journal": False}, "decision journal"),
        ],
        ids=["missing-retrospective", "missing-journal"],
    )
    def test_completion_real_first_acceptance_requires_each_audit(
        self, run_cli, envelope_tools, tmp_path, monkeypatch, audit_knob, artifact
    ):
        """
        AC-9: the REAL acceptance path (not only --dry-run) runs BOTH
                read-only audits — a missing retrospective or a missing journal each
                refuse the first qualifying submission without any write. The
                user-sourced grant relaxes the missing-suite bar (read model), so the
                refusal is attributable to the audit alone
                (review-test-scaffolding#2 review).
        """
        fixture = _completion_close_host(
            tmp_path, authorizations=_completion_user_grant(), **audit_knob
        )
        monkeypatch.chdir(fixture.host)
        before = fixture.state_path.read_bytes()
        code, envelope = _completion_submit(run_cli, envelope_tools)
        assert code != 0 and not envelope["ok"], (
            f"FAIL AC-9: a real first acceptance with a missing {artifact} "
            f"must refuse (both audits are required), got exit {code}: "
            f"{envelope.get('error')}"
        )
        assert fixture.state_path.read_bytes() == before, (
            f"FAIL AC-9: the refused acceptance (missing {artifact}) must "
            "not append or write state"
        )
        assert _completion_lifecycle(fixture.spec_path) == "active", (
            "FAIL AC-10: a refused acceptance must not stamp"
        )

    def test_completion_real_first_acceptance_refuses_sectionless_retrospective(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        """AC-9 (review-synthesis#3): the retrospective audit is
        content-level, not present-vs-absent — a PRESENT retrospective
        without the four required headings refuses the first qualifying
        submission on the session-log acceptance path, without any write."""
        fixture = _completion_close_host(
            tmp_path, authorizations=_completion_user_grant()
        )
        retro_path = (
            fixture.host / "plans" / f"{_COMPLETION_SLUG}.friction-retrospective.md"
        )
        retro_path.write_text(
            "prose only — no headings, so no category is recorded\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(fixture.host)
        before = fixture.state_path.read_bytes()
        code, envelope = _completion_submit(run_cli, envelope_tools)
        assert code != 0 and not envelope["ok"], (
            "FAIL AC-9: a present-but-sectionless retrospective must refuse "
            f"the first acceptance, got exit {code}: {envelope.get('error')}"
        )
        assert "required headings" in envelope["error"]["message"], (
            "FAIL AC-9: the refusal must be attributable to the content-level "
            f"heading audit, got: {envelope['error']}"
        )
        assert fixture.state_path.read_bytes() == before, (
            "FAIL AC-9: the refused acceptance must not append or write state"
        )
        assert _completion_lifecycle(fixture.spec_path) == "active", (
            "FAIL AC-10: a refused acceptance must not stamp"
        )

    def test_completion_real_first_acceptance_refuses_journal_without_policy_section(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        """AC-9 (completion enforce-later sweep): the journal audit is content-level
        too — a PRESENT journal that lacks the section for a policy-resolved
        decision refuses the first qualifying submission without any write."""
        fixture = _completion_close_host(
            tmp_path, authorizations=_completion_user_grant()
        )
        state = _read_yaml(fixture.state_path)
        state.setdefault("decisions", []).append(
            {
                "id": "completion-policy#1",
                "kind": "policy",
                "rationale": "resolved by policy during implement",
                "resolved_at": "2026-07-14T09:00Z",
                "source": "completion-policy#1",
                "title": "a policy-resolved decision needing a journal section",
                "status": "resolved",
                "resolution": {
                    "kind": "policy",
                    "choice": "local fixture choice",
                    "basis": "engineering-principles",
                    "reversibility": "reversible",
                    "confidence": "high",
                    "review_flag": "",
                    "scope": "implement",
                },
                "resolution_source": "policy",
                "routes_to": [f"plans/{_COMPLETION_SLUG}/plan.md"],
                "created_at": "2026-07-14T09:00Z",
            }
        )
        _write_yaml(fixture.state_path, state)
        monkeypatch.chdir(fixture.host)
        before = fixture.state_path.read_bytes()
        code, envelope = _completion_submit(run_cli, envelope_tools)
        assert code != 0 and not envelope["ok"], (
            "FAIL AC-9: a journal missing its policy-resolved section must "
            f"refuse the first acceptance, got exit {code}: "
            f"{envelope.get('error')}"
        )
        assert "journal" in envelope["error"]["message"].lower(), (
            "FAIL AC-9: the refusal must be attributable to the journal "
            f"audit, got: {envelope['error']}"
        )
        assert fixture.state_path.read_bytes() == before, (
            "FAIL AC-9: the refused acceptance must not append or write state"
        )

    def test_completion_completion_at_wrong_current_stage_is_usage(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        fixture = _completion_close_host(
            tmp_path, stage="robustness", authorizations=_completion_user_grant()
        )
        monkeypatch.chdir(fixture.host)
        before = fixture.state_path.read_bytes()
        code, envelope = _completion_submit(run_cli, envelope_tools)
        assert code == 2 and envelope["error"]["code"] == "usage", (
            "FAIL AC-8/REQ-10: the exact marker outside current stage "
            "complete must be refused without a state write"
        )
        assert fixture.state_path.read_bytes() == before

    def test_completion_live_ordinary_blocker_rejects_via_blocked_envelope(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        fixture = _completion_close_host(
            tmp_path, authorizations=_completion_user_grant(), pending_decision=True
        )
        monkeypatch.chdir(fixture.host)
        before = fixture.state_path.read_bytes()
        code, envelope = _completion_submit(run_cli, envelope_tools)
        assert code == 3 and envelope["error"]["code"] == "pending-decisions", (
            "FAIL AC-8/REQ-11: an otherwise qualifying submission over a live "
            "ordinary blocker must return the existing blocked-condition "
            "envelope naming that blocker (usage stays reserved for "
            f"malformed attempts), got exit {code}: {envelope.get('error')}"
        )
        assert any(
            "decisions" in action["command"] for action in envelope["next_actions"]
        ), "FAIL AC-8: the refusal must carry the ordinary blocker's remedy"
        assert fixture.state_path.read_bytes() == before, (
            "FAIL AC-8: the blocked acceptance attempt writes nothing"
        )
        assert _completion_lifecycle(fixture.spec_path) == "active"


class TestCompletionCloseGrantProvenance:
    """AC-9: the effective close grant is the LAST append-ordered
    authorization through `complete`; only explicit user source relaxes a
    missing suite configuration (REQ-13, read model)."""

    def test_completion_last_append_ordered_grant_wins_regardless_of_timestamps(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        # Timestamps deliberately mislead: the user fact carries the LATER
        # timestamp but the policy fact is LAST in append order. A trailing
        # non-complete grant proves the through == complete filter.
        fixture = _completion_close_host(
            tmp_path,
            authorizations=[
                {"through": "complete", "source": "user", "at": "2026-07-14T12:00Z"},
                {"through": "complete", "source": "policy", "at": "2026-07-10T00:00Z"},
                {"through": "robustness", "source": "user", "at": "2026-07-14T13:00Z"},
            ],
        )
        monkeypatch.chdir(fixture.host)
        before = fixture.state_path.read_bytes()
        code, envelope = _completion_submit(run_cli, envelope_tools)
        assert code != 0 and not envelope["ok"], (
            "FAIL AC-9/read model: a policy-sourced effective grant with no "
            "configured suite must take the conservative bar and refuse"
        )
        assert fixture.state_path.read_bytes() == before
        assert _completion_lifecycle(fixture.spec_path) == "active", (
            "FAIL AC-10: a refused acceptance must not stamp"
        )

    @pytest.mark.parametrize(
        "authorizations",
        [
            [],
            [{"through": "complete", "at": "2026-07-14T09:00Z"}],  # source absent
            [{"through": "robustness", "source": "user", "at": "2026-07-14T09:00Z"}],
        ],
        ids=["no-grants", "sourceless-grant", "no-complete-grant"],
    )
    def test_completion_missing_or_unknown_grant_takes_conservative_bar(
        self, run_cli, envelope_tools, tmp_path, monkeypatch, authorizations
    ):
        fixture = _completion_close_host(tmp_path, authorizations=authorizations)
        monkeypatch.chdir(fixture.host)
        before = fixture.state_path.read_bytes()
        code, envelope = _completion_submit(run_cli, envelope_tools)
        assert code != 0 and not envelope["ok"], (
            "FAIL AC-9: missing/unknown close provenance without a suite "
            "must refuse acceptance (conservative policy bar)"
        )
        assert fixture.state_path.read_bytes() == before
