"""State publication, write seams, dry-run, and idempotence contracts."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from tests.runtime.policy_helpers import assert_runtime_write_seams
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
RUNTIME_DIR = REPO_ROOT / "heddle" / "runtime"
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"
EDGE = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "edge"


def _session_payload(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "started_at: 2026-06-15T10:00Z",
                "ended_at: 2026-06-15T10:30Z",
                "completed: []",
                "started: []",
                "key_context: dry run probe",
                "next_steps: continue",
                "blockers: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _assert_dry_run_preview(
    envelope: dict[str, Any], before_state: dict[str, Any], label: str
) -> None:
    data = envelope["data"]
    assert data.get("dry_run") is True, (
        f"FAIL AC-3: dry-run command {label} must set data.dry_run=true"
    )
    assert data.get("revision") == before_state["revision"] + 1, (
        f"FAIL AC-3: dry-run command {label} must report the would-be revision"
    )
    assert data.get("mutation_summary"), (
        f"FAIL AC-3: dry-run command {label} must include mutation_summary"
    )


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


def test_ac01_commit_state_write_commits_on_match_and_conflicts_on_mismatch(
    tmp_path: Path,
) -> None:
    # AC-1: the shared runtime seam bumps revision on a matching CAS token and
    # rejects a stale token without changing the file.
    from heddle.runtime import write_path

    host = _copy_host(tmp_path, TINY)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    before = _read_yaml(state_path)

    def append_session(document: dict[str, Any]) -> dict[str, Any]:
        document["sessions"].append(
            {
                "started_at": "2026-06-15T10:00Z",
                "ended_at": "2026-06-15T10:30Z",
                "completed": [],
                "started": [],
                "key_context": "CAS probe",
                "next_steps": "continue",
                "blockers": [],
            }
        )
        return document

    result = write_path.commit_state(
        state_path,
        expect_revision=before["revision"],
        transform=append_session,
    )
    after = _read_yaml(state_path)
    assert result.wrote is True, "FAIL AC-1: matching CAS token must write"
    assert after["revision"] == before["revision"] + 1, (
        "FAIL AC-1: commit must increment revision exactly once"
    )
    assert after["updated"] != before["updated"], (
        "FAIL AC-1: commit must restamp updated"
    )

    stale_bytes = state_path.read_bytes()
    with pytest.raises(write_path.Conflict):
        write_path.commit_state(
            state_path,
            expect_revision=before["revision"],
            transform=append_session,
        )
    assert state_path.read_bytes() == stale_bytes, (
        "FAIL AC-1: CAS conflict must leave state.yaml byte-for-byte unchanged"
    )


def test_ac01_cli_conflict_routes_to_exit_five_and_writes_nothing(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-1: the runtime dispatch layer must translate a CAS conflict into the
    # public exit-5/error-code contract, not a generic block or crash.
    from heddle.runtime import write_path

    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    before = state_path.read_bytes()

    def forced_conflict(*_args, **_kwargs):
        raise write_path.Conflict("forced stale revision for AC-1")

    monkeypatch.setattr(write_path, "commit_state", forced_conflict)
    code, out, _err = run_cli(
        ["task", "done", "t2", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(out)
    assert code == 5 and envelope["error"]["code"] == "conflict", (
        "FAIL AC-1: CAS conflict must route to exit 5/error.code=conflict, "
        f"got exit {code} and envelope {envelope!r}"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-1: CLI-level CAS conflict must leave state.yaml unchanged"
    )


def test_ac02_runtime_writes_use_the_shared_state_publisher_and_lifecycle_owner() -> (
    None
):
    # W2 preserves atomic publication while consolidating state creation, CAS
    # and append-current behind one owner. Lifecycle stamping stays separate.
    assert_runtime_write_seams(RUNTIME_DIR)
    functions = {}
    for path in sorted(RUNTIME_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                functions[path.relative_to(RUNTIME_DIR).as_posix(), node.name] = node
    for module, name, write_method in (
        ("state_store.py", "_write_text", "write"),
        ("write_path.py", "stamp_lifecycle", "write_text"),
    ):
        function = functions[module, name]
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        writes = [
            node
            for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr == write_method
        ]
        replaces = [
            node
            for node in calls
            if isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        ]
        assert len(writes) == 1 and replaces, (module, name)


def test_ac04_convergent_commands_noop_and_appenders_append_distinct_facts(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-4: state-machine commands converge on re-run; appenders deliberately
    # append a distinct fact per invocation.
    host = _copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "sample-feature" / "state.yaml"

    code, out, _err = run_cli(["task", "done", "t2", "--json"])
    first = envelope_tools.parse(out)
    assert code == 0 and first["ok"] is True, (
        "FAIL AC-4: initial task done must succeed"
    )
    after_first = _read_yaml(state_path)

    code, out, _err = run_cli(["task", "done", "t2", "--json"])
    second = envelope_tools.parse(out)
    after_second = _read_yaml(state_path)
    assert code == 0 and second["ok"] is True, (
        "FAIL AC-4: re-running satisfied task done must be a no-op success"
    )
    assert after_second["revision"] == after_first["revision"], (
        "FAIL AC-4: no-op task done must not bump revision"
    )

    session_path = _session_payload(tmp_path / "session.yaml")
    before_sessions = len(_read_yaml(state_path)["sessions"])
    for _ in range(2):
        code, out, _err = run_cli(
            ["session", "log", "--from-file", str(session_path), "--json"]
        )
        assert code == 0 and envelope_tools.parse(out)["ok"] is True, (
            "FAIL AC-4: session log append must succeed"
        )
    state = _read_yaml(state_path)
    assert len(state["sessions"]) == before_sessions + 2, (
        "FAIL AC-4: session log is an appender; two invocations must record "
        "two distinct session facts"
    )
