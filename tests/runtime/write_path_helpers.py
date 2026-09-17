"""Shared synthetic-host helpers for write-path tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml


def copy_host(tmp_path: Path, src: Path, name: str = "host") -> Path:
    host = tmp_path / name
    shutil.copytree(src, host)
    return host


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def authored_milestone_verification(state_path, milestone, *, at="2026-06-15T10:00Z"):
    """Construct a current verification fixture for the declared milestone."""
    from tests.runtime.validation_helpers import ensure_files

    host = state_path.parents[2]
    ensure_files(host, list(milestone["owns"]))
    log = state_path.parent / f"verification/{milestone['id']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("Authored green fixture evidence.\n")
    return {
        "scope": milestone["id"],
        "command": milestone["verification"]["command"],
        "exit_code": 0,
        "at": at,
        "log": f"verification/{milestone['id']}.log",
        "evidence": authored_evidence(
            state_path, milestone["owns"], kind="milestone-owns"
        ),
    }


def authored_evidence(state_path, paths, *, kind):
    """Publish one immutable fixture manifest and return its compact binding."""
    from dataclasses import asdict

    from heddle.io.source import observe_source, resolve_source_definition
    from heddle.runtime.verification import publish_source_evidence

    host = state_path.parents[2]
    definition = resolve_source_definition(
        host, kind, tuple(paths), eligible_paths=tuple(paths)
    )
    evidence = observe_source(host, definition)
    reference, _reused = publish_source_evidence(state_path.parent, evidence)
    encoded = asdict(reference)
    return {
        "schema": "heddle.verification-evidence/v1",
        "before": encoded,
        "after": encoded,
    }


def authored_boundary_verifications(state_path):
    """Declare simple commands and construct current boundary test evidence."""
    from tests.runtime.validation_helpers import ensure_files

    host = state_path.parents[2]
    state = read_yaml(state_path)
    command = "python3 -c 'print(1)'"
    state["commands"].update(smoke_test=command, acceptance_test=command)
    for milestone in state["milestones"]:
        milestone["verification"]["command"] = command
    paths = sorted(
        {path for milestone in state["milestones"] for path in milestone["owns"]}
    )
    ensure_files(host, paths)
    state["verifications"] = [
        authored_milestone_verification(state_path, milestone)
        for milestone in state["milestones"]
        if milestone["status"] == "done"
    ]
    for scope in ("acceptance", "smoke"):
        state["verifications"].append(
            {
                "scope": scope,
                "command": command,
                "exit_code": 0,
                "at": "2026-06-15T10:00Z",
                "log": f"verification/{scope}.log",
                "evidence": authored_evidence(
                    state_path, paths, kind="feature-owned-union"
                ),
            }
        )
    for fact in state["verifications"]:
        log = state_path.parent / fact["log"]
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("Authored green fixture evidence.\n")
    write_yaml(state_path, state)


def complete_boundary_host(tmp_path, source):
    """Fresh current close fixture with source-bound facts and a Git birth."""
    import subprocess

    host = copy_host(tmp_path, source)
    authored_boundary_verifications(host / "plans/complete-boundary/state.yaml")
    authored_clean_reviews(host / "plans/complete-boundary/state.yaml")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "test@example.test"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-qm", "fixture birth"],
    ):
        subprocess.run(["git", *args], cwd=host, check=True)
    return host


def authored_clean_reviews(state_path, *, milestone_ids=()):
    """Give transition fixtures the current review evidence they require."""
    from tests.readiness_helpers import STAGE_REVIEWS, authored_current_event
    from tests.runtime.validation_helpers import ensure_files

    state = read_yaml(state_path)
    host = state_path.parents[2]
    ensure_files(host, [p for m in state["milestones"] for p in m["owns"]])
    lanes = (
        [
            (gate, "codex", scope)
            for scope in milestone_ids
            for gate in ("code-quality", "self-review")
        ]
        if milestone_ids
        else [
            (gate, cli, "feature")
            for gate, cli in STAGE_REVIEWS[(state["stage"], state["tier"])]
        ]
    )
    for gate, cli, scope in lanes:
        event = authored_current_event(state_path, gate, scope, cli=cli)
        fact = next(
            (f for f in state["gates"] if f["gate"] == gate and f["scope"] == scope),
            None,
        )
        if fact is None:
            fact = {"gate": gate, "scope": scope, "runs": []}
            state["gates"].append(fact)
        fact["runs"].append(event)
        write_yaml(state_path, state)
