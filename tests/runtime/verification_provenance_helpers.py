"""Small fixture builder shared by provenance runtime and local-live tests."""

from __future__ import annotations

import shutil
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests/fixtures/workspaces/tiny"


def write_yaml(path: Path, document: dict) -> None:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def make_current_host(
    parent: Path,
    *,
    tier: int = 1,
    milestones: bool = True,
    name: str = "host",
) -> tuple[Path, Path]:
    from tests.tiering_helpers import ROLES, entry, policy

    host = parent / name
    shutil.copytree(TINY, host)
    state_path = host / "plans/sample-feature/state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    current_policy = policy(
        overrides={
            role: entry(role, mode="off", limit=None, minimum_rounds=0)
            for role in ROLES
        }
    )
    state.update(
        {
            "schema": "heddle.state/v9",
            "completion": None,
            "flow": "hitl",
            "authorizations": [
                {
                    "through": "implement",
                    "source": "user",
                    "at": state["updated"],
                }
            ],
            "feature_inputs": ["src.py"],
            "commands": {
                "smoke_test": "python3 -c 'print(1)'",
                "dev_setup": "",
                "test_command": "python3 -c 'print(1)'",
                "lint_command": "",
                "acceptance_test": "python3 -c 'print(1)'",
                "live_e2e_test": "",
            },
            "feature_policy": {
                "schema": "heddle.feature-policy/v1",
                **asdict(current_policy),
            },
            "review_assignments": {
                "schema": "heddle.review-assignments/v3",
                "assignments": [],
                "attempts": [],
                "dispositions": [],
                "acceptances": [],
            },
        }
    )
    state.pop("tier", None)
    if milestones:
        state["milestones"] = [state["milestones"][0]]
        milestone = state["milestones"][0]
        milestone["owns"] = ["src.py"]
        milestone["verification"] = {
            "command": "python3 -c 'print(2)'",
            "expected": "pass",
        }
        milestone["status"] = "current"
        for task in milestone.get("tasks", []):
            task["status"] = "done"
    else:
        state["milestones"] = []
    state["verifications"] = []
    write_yaml(state_path, state)
    (host / "src.py").write_text("value = 1\n", encoding="utf-8")
    return host, state_path


def convert_current_fixture_to_v8(host: Path, state_path: Path) -> None:
    """
    Restamp an empty authored fixture at the explicit operational core cutover.

        This is test setup, not a product migration path. It refuses historical
        review or verification facts so no old authority is reinterpreted.

    """
    from heddle.contracts.schemas import STATE_SCHEMA_V9, STATE_SCHEMA_VERSIONS
    from heddle.kernel.state import parse_state_document

    state = yaml.safe_load(state_path.read_text())
    if state["schema"] == "heddle.state/v9":
        assert state["review_assignments"]["schema"] == ("heddle.review-assignments/v3")
        parse_state_document(state, source=state_path)
        return
    assert state["schema"] == "heddle.state/v7"
    assert not state["gates"] and not state["verifications"]
    old_assignments = state.get("review_assignments") or {}
    assert not any(
        old_assignments.get(key)
        for key in ("sources", "retained", "interpretations", "dispositions")
    )
    state["schema"] = "heddle.state/v9"
    state["review_assignments"] = {
        "schema": "heddle.review-assignments/v3",
        "assignments": deepcopy(old_assignments.get("assignments", [])),
        "attempts": [],
        "dispositions": [],
        "acceptances": [],
    }
    assert STATE_SCHEMA_V9 in STATE_SCHEMA_VERSIONS
    parse_state_document(state, source=state_path)
    write_yaml(state_path, state)


def damage_latest_evidence(state_path: Path, damage: str) -> None:
    document = yaml.safe_load(state_path.read_text())
    reference = document["verifications"][-1]["evidence"]["before"]
    artifact = state_path.parent / reference["artifact"]
    if damage == "missing":
        artifact.unlink()
    elif damage == "tampered":
        artifact.write_bytes(artifact.read_bytes() + b"# changed\n")
    elif damage == "contradictory":
        reference["source_sha256"] = "f" * 64
        document["verifications"][-1]["evidence"]["after"] = deepcopy(reference)
        write_yaml(state_path, document)
    else:
        raise AssertionError(f"unknown evidence damage {damage!r}")


def expanded_evidence_projection(state_path: Path) -> dict:
    """Return the exact state with every reference replaced by decoded content."""
    from heddle.runtime.verification import read_source_evidence

    document = yaml.safe_load(state_path.read_text())
    expanded = deepcopy(document)
    for fact in expanded["verifications"]:
        for endpoint in ("before", "after"):
            reference = fact["evidence"][endpoint]
            fact["evidence"][endpoint] = asdict(
                read_source_evidence(state_path.parent, reference)
            )
    return expanded


def arm_completion_boundary(host: Path, state_path: Path) -> None:
    """Author a pending close fixture; acceptance and archival use public commands."""
    import subprocess

    state = yaml.safe_load(state_path.read_text())
    state.update(stage="complete", authorized_through="complete")
    state["authorizations"] = [
        {"through": "complete", "source": "user", "at": state["updated"]}
    ]
    for milestone in state["milestones"]:
        milestone["status"] = "done"
        for task in milestone["tasks"]:
            task["status"] = "done"
    write_yaml(state_path, state)
    feature = state["feature"]
    (state_path.parent / "brief.md").write_text(
        "# Brief\n\nExercise verification-backed completion.\n"
    )
    (host / f"plans/{feature}.decision-journal.md").write_text("# Decision Journal\n")
    (host / f"plans/{feature}.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        + "\n\n".join(
            f"## {heading}\n\nNone in the authored fixture."
            for heading in (
                "Tooling fights",
                "Prompt gaps",
                "Validator false-positives",
                "Escalations that should have been policy-resolvable",
            )
        )
        + "\n"
    )
    if not (host / ".git").exists():
        for args in (
            ("init", "-q"),
            ("config", "user.email", "fixture@example.test"),
            ("config", "user.name", "Completion fixture"),
            ("add", "."),
            ("commit", "-qm", "authored pending completion fixture"),
        ):
            subprocess.run(["git", *args], cwd=host, capture_output=True, check=True)
