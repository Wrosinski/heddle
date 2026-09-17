"""Revision rules and executable native routing."""

from __future__ import annotations

import shlex
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from shutil import copytree

import pytest
import yaml

from heddle.contracts.result import Conflict
from heddle.kernel.project_config import KernelError, load_project_config
from tests.runtime.write_path_helpers import copy_host, read_yaml

REPO = Path(__file__).resolve().parents[2]
TINY = REPO / "tests/fixtures/workspaces/tiny"


def _append_session(document: dict) -> dict:
    document["sessions"].append(
        {
            "started_at": "2026-09-12T00:00Z",
            "ended_at": "2026-09-12T00:01Z",
            "completed": ["one changed append"],
            "started": [],
            "key_context": "revision witness",
            "next_steps": "continue",
            "blockers": [],
            "stage": document["stage"],
        }
    )
    return document


def _incompatible(path: Path, *, feature: str = "legacy") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"schema: heddle.state/v6\nfeature: {feature}\n")


def test_ac1_changed_append_advances_once_and_exact_noop_does_not(tmp_path) -> None:
    from heddle.runtime.state_store import append_state

    host = copy_host(tmp_path, TINY)
    path = host / "plans/sample-feature/state.yaml"
    before = read_yaml(path)
    changed = append_state(path, transform=_append_session)
    after = read_yaml(path)
    assert changed.wrote is True
    assert changed.revision == before["revision"] + 1
    assert after["revision"] == before["revision"] + 1

    raw = path.read_bytes()
    noop = append_state(path, transform=lambda document: document)
    assert noop.wrote is False and noop.revision == after["revision"]
    assert path.read_bytes() == raw


def test_ac1_ac2_append_invalidates_stale_snapshot_without_losing_either_fact(
    tmp_path,
) -> None:
    from heddle.runtime.state_store import append_state, commit_state

    host = copy_host(tmp_path, TINY)
    path = host / "plans/sample-feature/state.yaml"
    before = read_yaml(path)
    append_state(path, transform=_append_session)

    def stale_task(document: dict) -> dict:
        changed = deepcopy(document)
        changed["milestones"][0]["tasks"][0]["status"] = "done"
        return changed

    with pytest.raises(Conflict, match="expected revision"):
        commit_state(path, expect_revision=before["revision"], transform=stale_task)
    after = read_yaml(path)
    assert after["sessions"][-1]["key_context"] == "revision witness"
    assert after["revision"] == before["revision"] + 1


def test_ac2_locked_append_wins_and_the_waiting_snapshot_conflicts(
    tmp_path, monkeypatch
) -> None:
    """Exercise the real directory lock rather than simulating a stale read."""
    from heddle.runtime import state_store

    host = copy_host(tmp_path, TINY)
    path = host / "plans/sample-feature/state.yaml"
    before = read_yaml(path)
    append_at_publish = threading.Event()
    release_append = threading.Event()
    original_publish = state_store._publish

    def blocked_publish(state_path, text, *, create):
        if state_path == path and not create:
            append_at_publish.set()
            assert release_append.wait(2), "test failed to release the append"
        return original_publish(state_path, text, create=create)

    def stale_task(document: dict) -> dict:
        document["milestones"][0]["tasks"][1]["status"] = "done"
        return document

    with monkeypatch.context() as patch:
        patch.setattr(state_store, "_publish", blocked_publish)
        with ThreadPoolExecutor(max_workers=2) as workers:
            append = workers.submit(
                state_store.append_state, path, transform=_append_session
            )
            assert append_at_publish.wait(2), "append never reached publication"
            snapshot = workers.submit(
                state_store.commit_state,
                path,
                expect_revision=before["revision"],
                transform=stale_task,
            )
            release_append.set()
            assert append.result(timeout=2).revision == before["revision"] + 1
            with pytest.raises(Conflict, match="expected revision"):
                snapshot.result(timeout=2)

    after = read_yaml(path)
    assert after["sessions"][-1]["key_context"] == "revision witness"
    assert after["milestones"][0]["tasks"][1]["status"] == "current"
    assert after["revision"] == before["revision"] + 1


def test_ac1_dry_run_and_prepublication_failure_never_advance_revision(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    """Survivor pin: previews and failed publication already avoid durable change."""
    from heddle.runtime import state_store

    host = copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    path = host / "plans/sample-feature/state.yaml"
    before = path.read_bytes()
    revision = read_yaml(path)["revision"]

    code, output, _error = run_cli(
        [
            "commands",
            "set",
            "smoke_test",
            "--command",
            "true",
            "--dry-run",
            "--json",
        ]
    )
    preview = envelope_tools.parse(output)
    assert code == 0 and preview["data"]["revision"] == revision + 1
    assert path.read_bytes() == before

    def fail_publish(*_args, **_kwargs):
        raise OSError("injected publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(state_store, "_write_text", fail_publish)
        with pytest.raises(KernelError, match="injected publication failure"):
            state_store.append_state(path, transform=_append_session)
    assert path.read_bytes() == before


def test_ac13_implicit_selection_excludes_only_typed_incompatible_history(
    tmp_path,
) -> None:
    from heddle.kernel.model import list_active_feature_workspaces

    host = copy_host(tmp_path, TINY)
    _incompatible(host / "plans/legacy/state.yaml")
    config = load_project_config(host)
    assert list_active_feature_workspaces(config) == ("sample-feature",)

    (host / "plans/sample-feature/state.yaml").unlink()
    (host / "plans/sample-feature/plan.md").unlink()
    (host / "plans/sample-feature").rmdir()
    assert list_active_feature_workspaces(config) == ()


def test_ac13_incompatible_pointer_falls_through_but_malformed_current_stops(
    tmp_path,
) -> None:
    from heddle.kernel.model import (
        active_feature_pointer_path,
        resolve_active_feature,
    )

    host = copy_host(tmp_path, TINY)
    (host / ".git").mkdir(exist_ok=True)
    _incompatible(host / "plans/legacy/state.yaml")
    config = load_project_config(host)
    pointer = active_feature_pointer_path(host)
    assert pointer is not None
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("legacy\n")
    resolution = resolve_active_feature(config, None)
    assert resolution.feature == "sample-feature"
    assert [item.code for item in resolution.diagnostics] == ["pointer-incompatible"]

    malformed = host / "plans/malformed/state.yaml"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("schema: heddle.state/v7\nfeature: [\n")
    with pytest.raises(KernelError) as caught:
        resolve_active_feature(config, None)
    assert caught.value.reason != "incompatible-state-schema"


def test_ac13_milestone_validate_next_action_round_trips_through_cli(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    from heddle.contracts import operations as ops
    from heddle.runtime.contracts import build_manifest

    host = copy_host(tmp_path, TINY)
    monkeypatch.chdir(host)
    action = ops.CommandAction(ops.Validate(feature="sample-feature"))
    command = ops.action_command(action)
    assert command == "heddle validate --feature sample-feature"
    contract = next(
        item for item in build_manifest()["commands"] if item["name"] == "validate"
    )
    assert any(flag["name"] == "--feature" for flag in contract["flags"])

    sample_state = read_yaml(host / "plans/sample-feature/state.yaml")
    for owned in {
        path for milestone in sample_state["milestones"] for path in milestone["owns"]
    }:
        destination = host / owned
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("# validate scope fixture\n")
    other = host / "plans/other-feature"
    copytree(host / "plans/sample-feature", other)
    other_state = read_yaml(other / "state.yaml")
    source_spec = host / other_state["spec"]
    other_state["feature"] = "other-feature"
    other_state["spec"] = "docs/features/example/other-feature.md"
    (host / other_state["spec"]).write_text(source_spec.read_text())
    (other / "state.yaml").write_text(yaml.safe_dump(other_state, sort_keys=False))

    code, output, _error = run_cli([*shlex.split(command)[1:], "--json"])
    envelope = envelope_tools.parse(output)
    assert code == 0, envelope
    assert [row["feature"] for row in envelope["data"]["workspaces"]] == [
        "sample-feature"
    ]


def test_ac13_milestone_mutations_reject_control_root_ownership() -> None:
    from heddle.kernel.write_path import add_milestone, edit_milestone

    document = read_yaml(TINY / "plans/sample-feature/state.yaml")
    original = deepcopy(document)
    invalid_path = "plans/sample-feature/plan.md"
    payload = {
        "title": "Invalid source owner",
        "complexity": "low",
        "verification": {"command": "true", "expected": "pass"},
        "owns": [invalid_path],
    }

    for mutate in (
        lambda: add_milestone(document, payload),
        lambda: edit_milestone(document, "m1", {"owns": [invalid_path]}),
    ):
        with pytest.raises(KernelError, match="overlaps a Heddle-owned root"):
            mutate()
        assert document == original


def test_ac13_fresh_implement_entry_routes_to_briefing_before_future_reviews(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import current_host

    _host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review", limit=2)},
    )
    document = yaml.safe_load(state_path.read_text())
    document["flow"] = "auto"
    document["authorizations"] = [
        {
            "through": "implement",
            "source": "policy",
            "at": document["updated"],
        }
    ]
    for milestone in document["milestones"]:
        milestone["status"] = "todo"
    state_path.write_text(yaml.safe_dump(document, sort_keys=False))

    code, output, _error = run_cli(
        ["orient", "--feature", "operational-example", "--json"]
    )
    envelope = envelope_tools.parse(output)
    assert code == 0, envelope
    assert envelope["data"]["current_milestone"] is None
    assert [row["command"] for row in envelope["next_actions"]] == [
        "heddle kickoff --feature operational-example"
    ], envelope


def test_ac13_current_milestone_routes_to_task_expansion_before_exit_evidence(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import current_host

    _host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review", limit=2)},
    )
    document = yaml.safe_load(state_path.read_text())
    current = next(row for row in document["milestones"] if row["status"] == "current")
    current["tasks"] = []
    state_path.write_text(yaml.safe_dump(document, sort_keys=False))

    code, output, _error = run_cli(
        ["orient", "--feature", "operational-example", "--json"]
    )
    envelope = envelope_tools.parse(output)
    assert code == 0, envelope
    assert envelope["data"]["current_milestone"] == current["id"]
    assert envelope["data"]["blocking_conditions"] == []
    assert envelope["next_actions"] == [
        {
            "action": {
                "kind": "session",
                "feature": "operational-example",
                "work": "task-expansion",
            },
            "command": "heddle kickoff --feature operational-example",
            "reason": f"expand tasks for {current['id']} (§4.2 task expansion)",
        }
    ]
