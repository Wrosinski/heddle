"""
W1-B AC-1..4: real driver preparation, hermetic provider boundary.

New behavior is red through the existing loop, without depending on the new
helper signature. Provider effects alone are replaced by a capture function.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from heddle.contracts.result import HeddleResult
from heddle.driver import loop, sessions
from heddle.kernel.model import resolve_snapshot
from heddle.kernel.project_config import load_project_config
from tests.driver.constants import AUTO_TIER2_SLUG as SLUG


def permission_host(root: Path, *, foreign: bool = True):
    """A disposable host shared by composed tests and the explicit live probe."""
    fixture = Path(__file__).parent / "fixtures/workspaces/auto-tier2"
    shutil.copytree(fixture, root)
    config_path = root / ".heddle.yaml"
    config_data = yaml.safe_load(config_path.read_text())
    if foreign:
        config_data["layout"].update(
            source="application",
            tests="checks",
            specs="design/specs",
            plans="work-items",
        )
    else:
        config_data["layout"]["source"] = "heddle"
    config_path.write_text(yaml.safe_dump(config_data, sort_keys=False))
    config = load_project_config(root)
    old_state_path = root / "plans" / SLUG / "state.yaml"
    state = yaml.safe_load(old_state_path.read_text())
    old_spec = root / state["spec"]
    state["spec"] = f"{config.layout.specs}/example/{SLUG}.md"
    target_spec = root / state["spec"]
    target_spec.parent.mkdir(parents=True, exist_ok=True)
    if target_spec != old_spec:
        shutil.copyfile(old_spec, target_spec)
    state.update(stage="implement", authorized_through="complete", flow="hitl")
    state["milestones"][0].update(
        status="current", owns=["feature code", "scripts/check.sh", "pyproject.toml"]
    )
    state["milestones"][1].update(status="todo", owns=["support/repair.py"])
    workspace = root / config.layout.plans / SLUG
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "state.yaml").write_text(yaml.safe_dump(state, sort_keys=False))
    for name in ("feature code/a.py", "scripts/check.sh", "pyproject.toml"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    (root / config.layout.tests).mkdir(parents=True, exist_ok=True)
    return config, resolve_snapshot(config, SLUG)


def symlink_denied_paths(config):
    """The static alias scenario shared by the argv test and native proof."""
    document = config.root / "docs/design/architecture.md"
    target = config.root / "protected-contract.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("KEEP\n")
    document.symlink_to(target)
    plans = config.root / config.layout.plans
    ledger = config.root / "ledger"
    plans.rename(ledger)
    plans.symlink_to(ledger, target_is_directory=True)


@pytest.fixture
def host_case(tmp_path):
    return permission_host(tmp_path / "host")


@pytest.fixture
def prepare(monkeypatch, tmp_path):
    """Capture the unchanged real loop's outgoing PhaseSessionSpec."""
    calls = []
    prompts = []

    def write_prompt(*args, **kwargs):
        prompts.append(args)
        return tmp_path / "prompt.md"

    def spawn(spec, **kwargs):
        calls.append(spec)
        return sessions.SessionResult(0, "completed", {})

    monkeypatch.setattr(loop, "_write_prompt", write_prompt)
    monkeypatch.setattr(loop, "spawn_phase_session", spawn)

    def run(config, snapshot):
        calls.clear()
        prompts.clear()
        result = loop._run_phase_session(config, snapshot, "heddle kickoff", None)
        return result, list(calls), list(prompts)

    return run


def file_grants(profile):
    return {
        rule
        for rule in profile.allowed_tools
        if rule in {"Edit", "Write"} or rule.startswith(("Edit(", "Write("))
    }


def expected_files(config, *paths):
    return {f"Edit(/{config.root.resolve()}/{path})" for path in paths}


def implementation_docs(snapshot):
    return (
        snapshot.spec_path,
        f"{snapshot.workspace}brief.md",
        f"{snapshot.workspace}plan.md",
    )


def assert_refusal(outcome, *, field, value, reason, remedy):
    result, calls, prompts = outcome
    assert not calls and not prompts, "FAIL AC-2: invalid path reached prompt/spawn"
    assert isinstance(result, HeddleResult) and result.exit_code == 3
    assert result.error is not None and result.error.code == "workspace-invalid"
    assert field in result.error.message
    if value is not None:
        assert repr(value) in result.error.message
    assert reason in result.error.message.lower(), "FAIL AC-2: missing refusal cause"
    assert remedy in result.error.hint, "FAIL AC-2: missing actionable repair"


# Claude 2.1.261 reports static path denials as paired tool errors; its
# terminal permission_denials list may contain only classifier denials.
STATIC_PATH_DENIAL = (
    "<tool_use_error>File is in a directory that is denied by your "
    "permission settings.</tool_use_error>"
)


def assert_probe_evidence(exit_code, events, targets, observed):
    """AC-4's observation oracle; model explanation is never permission proof."""
    assert exit_code == 0, f"FAIL AC-4: provider process exited {exit_code}"
    terminals = [event for event in events if event.get("type") == "result"]
    assert (
        len(terminals) == 1
        and terminals[0].get("subtype") == "success"
        and not terminals[0].get("is_error")
    ), "FAIL AC-4: missing or unsuccessful terminal result"
    attempts = [
        block
        for event in events
        if event.get("type") == "assistant"
        for block in event.get("message", {}).get("content", [])
        if block.get("type") == "tool_use"
    ]
    assert len(attempts) == len(targets), (
        "FAIL AC-4: missing/extra actual tool attempts"
    )
    assert all(attempt.get("name") == "Write" for attempt in attempts), (
        "FAIL AC-4: unexpected alternate tool"
    )
    attempted = [attempt.get("input", {}).get("file_path") for attempt in attempts]
    assert sorted(attempted) == sorted(targets.values()), (
        "FAIL AC-4: expected exactly one Write attempt for every target"
    )
    results = [
        block
        for event in events
        if event.get("type") == "user"
        for block in event.get("message", {}).get("content", [])
        if block.get("type") == "tool_result"
    ]
    by_id = {result.get("tool_use_id"): result for result in results}
    attempt_ids = {attempt.get("id") for attempt in attempts}
    assert None not in attempt_ids and len(attempt_ids) == len(attempts)
    assert len(results) == len(by_id) == len(attempts) and set(by_id) == attempt_ids, (
        "FAIL AC-4: missing, duplicate or unmatched paired tool results"
    )
    outcomes = {
        attempt["input"]["file_path"]: by_id[attempt["id"]] for attempt in attempts
    }
    denied = {
        item.get("tool_input", {}).get("file_path")
        for item in terminals[0].get("permission_denials", [])
        if item.get("tool_name") == "Write"
    }
    denied.update(
        path
        for path, result in outcomes.items()
        if result.get("is_error") is True
        and result.get("content") == STATIC_PATH_DENIAL
    )
    assert (
        observed["allowed"] == "OK\n"
        and targets["allowed"] not in denied
        and not outcomes[targets["allowed"]].get("is_error")
    ), "FAIL AC-4: authorized write did not succeed"
    for name in (
        "architecture-contract",
        "workflow-contract",
        "principles",
        "state",
    ):
        assert (
            targets[name] in denied
            and observed[name] == "KEEP\n"
            and outcomes[targets[name]].get("is_error") is True
        ), f"FAIL AC-4: protected target {name} was not denied and preserved"
    assert observed["unlisted"] in (None, "OUTSIDE\n"), (
        "FAIL AC-4: unexpected unlisted write content"
    )
    assert (
        (observed["unlisted"] is None)
        == (targets["unlisted"] in denied)
        == bool(outcomes[targets["unlisted"]].get("is_error"))
    ), "FAIL AC-4: unlisted outcome and denial evidence disagree"


@pytest.mark.acceptance
@pytest.mark.parametrize("denial_channel", ["summary", "tool-result"])
@pytest.mark.parametrize("unlisted_allowed", [False, True])
def test_ac4_acceptance_observations_require_actual_attempts(
    unlisted_allowed, denial_channel
):
    """Oracle fixtures cover denial channels, auto outcomes and invalid evidence."""
    names = (
        "allowed",
        "architecture-contract",
        "workflow-contract",
        "principles",
        "state",
        "unlisted",
    )
    targets = {name: f"/disposable/{name}" for name in names}
    attempts = [
        {"type": "tool_use", "id": name, "name": "Write", "input": {"file_path": path}}
        for name, path in targets.items()
    ]
    protected = list(names[1:5])
    denied_names = protected + ([] if unlisted_allowed else ["unlisted"])
    summarized = (
        denied_names
        if denial_channel == "summary"
        else ([] if unlisted_allowed else ["unlisted"])
    )
    events = [
        {"type": "assistant", "message": {"content": attempts}},
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": name,
                        "is_error": name in denied_names,
                        "content": STATIC_PATH_DENIAL
                        if name in protected and denial_channel == "tool-result"
                        else "permission denied"
                        if name in denied_names
                        else "file created",
                    }
                    for name in names
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "permission_denials": [
                {
                    "tool_name": "Write",
                    "tool_use_id": name,
                    "tool_input": {"file_path": targets[name]},
                }
                for name in summarized
            ],
        },
    ]
    observed = {name: "KEEP\n" for name in protected}
    observed.update(allowed="OK\n", unlisted="OUTSIDE\n" if unlisted_allowed else None)
    assert_probe_evidence(0, events, targets, observed)
    with pytest.raises(AssertionError, match="process exited"):
        assert_probe_evidence(7, events, targets, observed)
    with pytest.raises(AssertionError, match="terminal"):
        assert_probe_evidence(0, events[:1], targets, observed)
    with pytest.raises(AssertionError, match="actual tool attempts"):
        assert_probe_evidence(0, events[1:], targets, observed)
    with pytest.raises(AssertionError, match="paired tool results"):
        assert_probe_evidence(0, [events[0], events[2]], targets, observed)
    with pytest.raises(AssertionError, match="protected target"):
        assert_probe_evidence(0, events, targets, {**observed, "state": "CHANGED\n"})
    with pytest.raises(AssertionError, match="authorized write"):
        assert_probe_evidence(0, events, targets, {**observed, "allowed": None})
    with pytest.raises(AssertionError, match="disagree"):
        assert_probe_evidence(
            0,
            events,
            targets,
            {**observed, "unlisted": None if unlisted_allowed else "OUTSIDE\n"},
        )
    generic_error = deepcopy(events)
    generic_error[1]["message"]["content"][1]["content"] = "File has not been read yet."
    generic_error[2]["permission_denials"] = []
    with pytest.raises(AssertionError, match="protected target"):
        assert_probe_evidence(0, generic_error, targets, observed)


@pytest.mark.live
def test_ac4_live_generated_permissions(tmp_path):
    """The standalone native verification command remains a live-only lane."""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.driver.permission_probe",
            "--output-dir",
            str(tmp_path / "live-evidence"),
        ],
        cwd=root,
        check=False,
    )
    assert result.returncode == 0, "FAIL AC-4: see retained native probe evidence"
    records = list((tmp_path / "live-evidence").glob("attempt-*/result.json"))
    assert len(records) == 1, "FAIL AC-4: missing or ambiguous retained result"
    record = json.loads(records[0].read_text())
    assert record["passed"] is True and record["provider_attempted"] is True
    assert record["exit_code"] == 0
    assert record["cli_version"] and record["argv"] and record["observed_files"]
    host_root = Path(record["targets"]["allowed"]).parent.parent
    for name in ("architecture-contract", "state"):
        assert record["resolved_targets"][name] != record["targets"][name]
    assert {
        f"Edit(/{host_root}/protected-contract.md)",
        f"Edit(/{host_root}/ledger/*/state.yaml)",
    } <= set(record["deny_rules"])
    assert {
        f"Edit(/{host_root}/docs/design/**)",
        f"Edit(/{host_root}/docs/workflow/**)",
        f"Edit(/{host_root}/ledger/**)",
        f"Edit(/{host_root}/protected-contract.md)",
    } <= set(record["allowed_tools"])
    assert (records[0].parent / "stdout.jsonl").stat().st_size > 0
    assert (records[0].parent / "stderr.txt").is_file()
    assert (records[0].parent / "prompt.txt").read_text() == record["prompt"]
