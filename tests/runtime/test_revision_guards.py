"""Public caller-revision guards for single-state mutation commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.runtime.write_path_helpers import copy_host

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"
FEATURE = "sample-feature"

NEWLY_GUARDED_COMMANDS = (
    "commands set",
    "commands unset",
    "milestone add",
    "milestone edit",
    "milestone advance",
    "task add",
    "task current",
    "task done",
    "session log",
    "phase-exit",
    "flow set",
    "feature inputs set",
    "decisions add",
    "decisions record-policy",
    "decisions resolve",
)


def _write_payloads(tmp_path: Path) -> dict[str, Path]:
    payloads = {
        "milestone": {
            "title": "Revision guarded milestone",
            "complexity": "low",
            "verification": {"command": "true", "expected": "pass"},
        },
        "patch": {"title": "Revision guarded title"},
        "session": {
            "started_at": "2026-09-14T10:00Z",
            "ended_at": "2026-09-14T10:01Z",
            "completed": [],
            "started": [],
            "key_context": "revision guard",
            "next_steps": "continue",
            "blockers": [],
        },
    }
    paths: dict[str, Path] = {}
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.yaml"
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        paths[name] = path
    json_payloads = {
        "inputs": ["src/example_app/parser.py"],
        "decision": {
            "schema": "heddle.decision-batch/v1",
            "decisions": [
                {
                    "kind": "question",
                    "class": 1,
                    "source": "session",
                    "title": "Choose a revision-safe path",
                    "question": "Which path should own the change?",
                    "options": ["Current owner"],
                    "routes_to": ["docs/decision.md"],
                }
            ],
        },
        "policy": {
            "schema": "heddle.policy-batch/v1",
            "resolutions": [
                {
                    "id": "POL-REVISION-GUARD",
                    "title": "Revision guard policy",
                    "question": "How should callers guard writes?",
                    "options": ["Current revision"],
                    "choice": "Current revision",
                    "rationale": "Refuse stale caller intent.",
                    "basis": "Engineering principles",
                    "reversibility": "Reversible CLI contract",
                    "confidence": "high",
                    "scope": "single-state mutations",
                    "routes_to": ["docs/decision.md"],
                }
            ],
        },
    }
    for name, payload in json_payloads.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    return paths


def _command_arguments(name: str, paths: dict[str, Path]) -> list[str]:
    return {
        "commands set": ["commands", "set", "smoke_test", "--command", "true"],
        "commands unset": ["commands", "unset", "smoke_test"],
        "milestone add": ["milestone", "add", "--from-file", str(paths["milestone"])],
        "milestone edit": [
            "milestone",
            "edit",
            "m1",
            "--from-file",
            str(paths["patch"]),
        ],
        "milestone advance": ["milestone", "advance"],
        "task add": ["task", "add", "revision guarded task"],
        "task current": ["task", "current", "t2"],
        "task done": ["task", "done", "t2"],
        "session log": ["session", "log", "--from-file", str(paths["session"])],
        "phase-exit": ["phase-exit"],
        "flow set": ["flow", "set", "auto"],
        "feature inputs set": [
            "feature",
            "inputs",
            "set",
            "--from-file",
            str(paths["inputs"]),
        ],
        "decisions add": [
            "decisions",
            "add",
            "--input-json",
            str(paths["decision"]),
        ],
        "decisions record-policy": [
            "decisions",
            "record-policy",
            "--from-file",
            str(paths["policy"]),
        ],
        "decisions resolve": [
            "decisions",
            "resolve",
            "d1",
            "--kind",
            "disposition",
            "--resolution",
            "Resolved by the owner",
            "--routes-to",
            "docs/decision.md",
        ],
    }[name]


@pytest.mark.parametrize("command_name", NEWLY_GUARDED_COMMANDS)
def test_stale_cli_revision_refuses_each_single_state_mutation_without_effects(
    command_name,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / FEATURE / "state.yaml"
    before = state_path.read_bytes()
    revision = yaml.safe_load(before)["revision"]
    journal = host / "plans" / f"{FEATURE}.decision-journal.md"
    assert not journal.exists()
    paths = _write_payloads(tmp_path)

    code, out, _err = run_cli(
        [
            *_command_arguments(command_name, paths),
            "--feature",
            FEATURE,
            "--expect-revision",
            str(revision + 1),
            "--json",
        ]
    )
    result = envelope_tools.parse(out)

    assert code == 5 and result["error"]["code"] == "conflict", (
        command_name,
        result,
    )
    assert state_path.read_bytes() == before, command_name
    assert not journal.exists(), command_name


@pytest.mark.parametrize(
    "command",
    (
        ["commands", "set"],
        ["commands", "unset"],
        ["milestone", "add"],
        ["milestone", "edit"],
        ["milestone", "advance"],
        ["task", "add"],
        ["task", "current"],
        ["task", "done"],
        ["session", "log"],
        ["phase-exit"],
        ["flow", "set"],
        ["feature", "inputs", "set"],
        ["decisions", "add"],
        ["decisions", "record-policy"],
        ["decisions", "resolve"],
        ["verify"],
        ["feature", "complete"],
        ["feature", "start", "demo"],
        ["feature", "policy"],
        ["feature", "reassess"],
        ["review", "disposition"],
        ["review", "round-open"],
        ["review", "interpret"],
    ),
    ids=lambda command: "-".join(command),
)
@pytest.mark.parametrize("value", ("invalid", "-1"))
def test_common_revision_parser_rejects_invalid_values_before_workspace_access(
    command, value, run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    code, out, _err = run_cli([*command, "--expect-revision", value, "--json"])
    result = envelope_tools.parse(out)
    assert code == 2 and result["error"]["code"] == "usage", (command, result)


def test_matching_guard_covers_preview_apply_and_stale_noop(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    host = copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / FEATURE / "state.yaml"
    before = state_path.read_bytes()
    revision = yaml.safe_load(before)["revision"]
    command = [
        "commands",
        "set",
        "smoke_test",
        "--command",
        "true",
        "--feature",
        FEATURE,
        "--expect-revision",
        str(revision),
        "--json",
    ]

    code, out, _err = run_cli([*command, "--dry-run"])
    preview = envelope_tools.parse(out)
    assert code == 0 and preview["data"]["dry_run"] is True, preview
    assert state_path.read_bytes() == before

    code, out, _err = run_cli(command)
    applied = envelope_tools.parse(out)
    assert code == 0 and applied["data"]["revision"] == revision + 1, applied
    published = state_path.read_bytes()

    code, out, _err = run_cli(command)
    stale = envelope_tools.parse(out)
    assert code == 5 and stale["error"]["code"] == "conflict", stale
    assert state_path.read_bytes() == published


def test_task_done_reuses_guard_after_current_milestone_lookup(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    from heddle.runtime import application
    from heddle.runtime.state_store import commit_state

    host = copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    state_path = host / "plans" / FEATURE / "state.yaml"
    before = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    real_execute = application.execute

    def race(operation):
        def publish_unrelated(document):
            document["commands"]["smoke_test"] = "concurrent check"
            return document

        commit_state(
            state_path,
            expect_revision=before["revision"],
            transform=publish_unrelated,
        )
        return real_execute(operation)

    monkeypatch.setattr(application, "execute", race)
    code, out, _err = run_cli(
        [
            "task",
            "done",
            "t2",
            "--feature",
            FEATURE,
            "--expect-revision",
            str(before["revision"]),
            "--json",
        ]
    )
    result = envelope_tools.parse(out)
    after = yaml.safe_load(state_path.read_text(encoding="utf-8"))

    assert code == 5 and result["error"]["code"] == "conflict", result
    assert after["revision"] == before["revision"] + 1
    assert after["commands"]["smoke_test"] == "concurrent check"
    task = next(
        task
        for milestone in after["milestones"]
        if milestone["id"] == "m1"
        for task in milestone["tasks"]
        if task["id"] == "t2"
    )
    assert task["status"] == "current"


def test_feature_prepare_still_rejects_a_revision_guard(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    payload = tmp_path / "intake.yaml"
    payload.write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, _err = run_cli(
        [
            "feature",
            "prepare",
            "demo",
            "--area",
            "runtime",
            "--from-file",
            str(payload),
            "--expect-revision",
            "0",
            "--json",
        ]
    )
    result = envelope_tools.parse(out)
    assert code == 2 and result["error"]["code"] == "usage", result
    assert "does not accept --expect-revision" in result["error"]["message"]
