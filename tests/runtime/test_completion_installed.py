"""W5 AC12–14: all lifecycle facts come from an isolated installed CLI."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tarfile
import textwrap
import time
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from tests.content_identity_helpers import git
from tests.runtime.wheel_harness import (
    build_installed_wheel,
    make_git_host,
    parse_envelope,
    snapshot_tree,
    write_claude_shim,
    write_executable,
)
from tests.tiering_helpers import FABLE, ROLES, entry, prepare_input, wire_policy
from tests.tiering_history_helpers import legacy_host
from tests.tiering_review_helpers import review_content

pytestmark = pytest.mark.acceptance
FEATURE = "completion-example"
SPEC = f"docs/features/runtime/{FEATURE}.md"
DOCUMENT_REVIEW_ROLES = (
    "spec-review",
    "plan-review",
    "review-test-scaffolding",
)
REVIEWED_ROLES = (
    *DOCUMENT_REVIEW_ROLES,
    "milestone-review",
    "peer-review-sequential",
)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("w5-installed"))


@dataclass
class Journey:
    installed: object
    root: Path
    nested: Path
    calls: Path
    providers: Path
    env: dict
    transcript: list

    @property
    def state(self):
        return self.root / f"journey-plans/{FEATURE}/state.yaml"

    @property
    def archive(self):
        return self.root / f"docs/gate-trajectories/.raw/{FEATURE}/completion.tar.gz"

    def read(self):
        return yaml.safe_load(self.state.read_text())

    def run(self, *args, expected=0, feature=True):
        argv = [*args, *(("--feature", FEATURE) if feature else ()), "--json"]
        result = self.installed.run(*argv, cwd=self.nested, env=self.env)
        self.record(result, cwd=self.nested)
        envelope = parse_envelope(result)
        allowed = (expected,) if isinstance(expected, int) else tuple(expected)
        assert result.returncode in allowed, f"FAIL W5 installed {args}: {envelope}"
        assert envelope["ok"] is (result.returncode in (0, 4)), envelope
        assert ("data" in envelope) != ("error" in envelope), envelope
        return envelope

    def payload(self, name, value):
        path = self.calls.parent / name
        path.write_text(json.dumps(value))
        return str(path)

    def record(self, result, *, cwd):
        self.transcript.append(
            {
                "argv": result.args,
                "cwd": str(cwd),
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        wheel = next((self.installed.root / "dist").glob("*.whl"))
        evidence = {
            "installed_wheel": str(wheel),
            "wheel_sha256": sha256(wheel.read_bytes()).hexdigest(),
            "installed_command": str(self.installed.command),
            "host": str(self.root),
            "commands": self.transcript,
            "state_sha256": sha256(self.state.read_bytes()).hexdigest()
            if self.state.exists()
            else None,
            "provider_calls": self.count(self.providers),
            "local_command_calls": self.count(self.calls),
        }
        (self.calls.parent / "journey-evidence.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )

    def count(self, path):
        return len(path.read_text().splitlines()) if path.exists() else 0

    def retain(self):
        git(self.root, "add", "-f", "journey-plans", "docs/gate-trajectories", SPEC)
        git(self.root, "commit", "-qm", "retain accepted ledger and archive")


def fixture_policy(*, reviewed):
    choices = {
        role: entry(
            role,
            mode="off",
            limit=None,
            minimum_rounds=0,
            primary=FABLE,
        )
        for role in ROLES
    }
    if reviewed:
        choices.update(
            {
                role: entry(
                    role,
                    limit=1,
                    minimum_rounds=1,
                    primary=FABLE,
                )
                for role in REVIEWED_ROLES
            }
        )
    return wire_policy(overrides=choices)


def git_authority(root):
    """Git facts Heddle must never mutate; source commits are made between probes."""
    return {
        "head": git(root, "rev-parse", "HEAD"),
        "refs": git(root, "for-each-ref", "--format=%(refname):%(objectname)"),
        "index": git(root, "ls-files", "--stage", "-z"),
        "ignore": (root / ".gitignore").read_bytes(),
    }


def admit(journey, *, slug, flow="hitl", reviewed=False):
    prepared = journey.payload(f"{slug}-prepare.json", prepare_input())
    journey.run(
        "feature",
        "prepare",
        slug,
        "--area",
        "runtime",
        "--from-file",
        prepared,
        feature=False,
    )
    policy = journey.payload(f"{slug}-policy.json", fixture_policy(reviewed=reviewed))
    journey.run("feature", "policy", slug, "--from-file", policy, feature=False)
    journey.run("feature", "start", slug, "--flow", flow, feature=False)


def start_host(installed, tmp_path, *, flow="hitl", reviewed=False):
    root, nested = make_git_host(tmp_path, "unlike-host")
    git(root, "config", "user.name", "Installed Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    calls = tmp_path / "local-commands.log"
    providers = tmp_path / "provider-calls.jsonl"
    journey = Journey(
        installed, root, nested, calls, providers, dict(installed.env), []
    )
    before = snapshot_tree(root)
    journey.run("init", "--dry-run", feature=False)
    assert snapshot_tree(root) == before
    journey.run("init", feature=False)
    (root / ".heddle.yaml").write_text(
        "layout:\n  plans: journey-plans\n"
        "autopilot:\n  test_command: python3 tests/check.py close\n"
    )
    (root / "docs/workflow/engineering-principles.md").write_text(
        "---\nstatus: ratified\n---\n\n# Engineering Principles\n\n"
        "## Principles, ranked\n\n1. Preserve host-owned bytes.\n"
    )
    (root / "brief.md").write_text("# Research\n\nDeliver one declared value.\n")
    (root / ".gitignore").write_text(
        ".heddle/intake/\n"
        "journey-plans/\n"
        "docs/features/\n"
        "docs/gate-trajectories/\n"
        "brief.md\n"
    )
    (root / "src").mkdir()
    (root / "src/bootstrap.py").write_text("BOOTSTRAPPED = True\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "synthetic source and host setup")
    before_admission = git_authority(root)
    admit(journey, slug=FEATURE, flow=flow, reviewed=reviewed)
    assert git_authority(root) == before_admission, (
        "FAIL local-history AC-9: admission mutated refs, index, or ignore policy"
    )
    return journey


def write_declared_documents(journey):
    (journey.root / SPEC).write_text(
        f"---\ntype: feature-spec\narea: runtime\nfeature_name: {FEATURE}\n"
        "lifecycle: active\n---\n\n# Declared value\n\n"
        "## Purpose\n\nDeliver VALUE = 7.\n\n## Acceptance Criteria\n\n"
        "### AC-1: Declared value\n\nPriority: MUST\n"
        "Action: Read VALUE.\nExpected: VALUE equals 7.\n"
        "Verified-by: tests/check.py::test_declared_value\n"
    )
    (journey.state.parent / "plan.md").write_text(
        "# Plan\n\n## Environment\n\npython3 tests/check.py verify\n\n"
        "### Milestone m1: Implement the declared value\n\nSatisfies: AC-1\n\n"
        "Change VALUE from 0 to 7 and exercise the actual check.\n"
    )
    (journey.state.parent / "brief.md").write_text("# Brief\n\nDeliver VALUE = 7.\n")


def native_pending_host(installed, tmp_path, *, reviewed=False, flow="hitl"):
    journey = start_host(installed, tmp_path, flow=flow, reviewed=reviewed)
    root = journey.root
    (root / "src").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "src/example.py").write_text("VALUE = 0\n")
    (root / "tests/check.py").write_text(
        "import runpy, sys\nfrom pathlib import Path\n"
        "def test_declared_value():\n"
        "    assert runpy.run_path('src/example.py')['VALUE'] == 7\n"
        "if __name__ == '__main__':\n"
        "    test_declared_value()\n"
        f"    with Path({str(journey.calls)!r}).open('a') as stream:\n"
        "        stream.write(sys.argv[1] + '\\n')\n"
    )
    write_declared_documents(journey)
    (root / f"journey-plans/{FEATURE}.decision-journal.md").write_text(
        "# Decision Journal\n\nNo policy decisions were made.\n"
    )
    (root / f"journey-plans/{FEATURE}.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        + "\n\n".join(
            f"## {heading}\n\nNone observed in this installed fixture."
            for heading in (
                "Tooling fights",
                "Prompt gaps",
                "Validator false-positives",
                "Escalations that should have been policy-resolvable",
            )
        )
        + "\n"
    )
    journey.run(
        "commands", "set", "test_command", "--command", "python3 tests/check.py verify"
    )
    for key, label in [("smoke_test", "smoke"), ("acceptance_test", "acceptance")]:
        journey.run(
            "commands", "set", key, "--command", f"python3 tests/check.py {label}"
        )
    milestone = tmp_path / "milestone.yaml"
    milestone.write_text(
        yaml.safe_dump(
            {
                "title": "Implement the declared value",
                "complexity": "low",
                "satisfies": ["AC-1"],
                "depends_on": [],
                "owns": [SPEC, "src/example.py", "tests/check.py"],
                "verification": {
                    "command": "python3 tests/check.py milestone",
                    "expected": "VALUE is 7",
                },
            }
        )
    )
    journey.run("milestone", "add", "--from-file", str(milestone))
    git(root, "add", "src", "tests")
    git(root, "commit", "-qm", "source-only feature implementation")
    after_source_commit = git_authority(root)
    for stage in ("spec-review", "plan-review", "scaffold", "implement"):
        journey.run("phase-exit", "--through", "complete")
        assert journey.read()["stage"] == stage
        role = {
            "spec-review": "spec-review",
            "plan-review": "plan-review",
            "scaffold": "review-test-scaffolding",
        }.get(stage)
        if reviewed and role:
            native_review(journey, tmp_path, role)
    journey.run("milestone", "advance")
    journey.run("task", "add", "Implement VALUE = 7")
    task_id = journey.read()["milestones"][0]["tasks"][-1]["id"]
    (root / "src/example.py").write_text("VALUE = 7\n")
    journey.run("task", "done", task_id)
    journey.run("verify", "--scope", "m1")
    if reviewed:
        native_review(journey, tmp_path, "milestone-review")
    journey.run("milestone", "advance")
    for scope in ("m1", "acceptance", "smoke"):
        journey.run("verify", "--scope", scope)
    journey.run("phase-exit")
    assert journey.read()["stage"] == "peer-review"
    if reviewed:
        native_review(journey, tmp_path, "peer-review-sequential")
    for stage in ("robustness", "complete"):
        journey.run("phase-exit")
        assert journey.read()["stage"] == stage
    assert git_authority(root) == after_source_commit, (
        "FAIL local-history AC-9: workflow progression mutated Git authority"
    )
    return journey


def native_review(journey, tmp_path, role):
    payload = review_content(role)
    if role == "plan-review":
        payload["details"]["ac_mapping"][0]["milestones"] = ["m1"]
        payload["details"]["dependencies"] = []
        payload["details"]["verification"] = (
            "Milestone m1 executes tests/check.py before final verification."
        )
    if role == "peer-review-sequential":
        payload["details"]["plan_context"] = (
            "Milestone m1 implements and verifies VALUE = 7."
        )
    event = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": payload,
            }
        )
        + "\n"
    )
    binary = write_claude_shim(tmp_path / "bin", event, journey.providers)
    journey.env["PATH"] = (
        str(binary.parent) + os.pathsep + journey.installed.env["PATH"]
    )
    journey.run(
        "run-gate",
        role,
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=(0, 4),
    )
    state = journey.read()
    run_id = next(gate for gate in state["gates"] if gate["gate"] == role)["runs"][-1][
        "run_id"
    ]
    disposition = journey.payload(
        f"{role}-disposition.json",
        {
            "schema": "heddle.review-disposition-input/v1",
            "dispositions": [
                {
                    "run_id": run_id,
                    "finding_id": "@coverage",
                    "status": "settled",
                    "evidence_kind": "inspection",
                    "references": ["tests/check.py"],
                    "reason": (
                        "The installed fixture has no live integration and its local "
                        "verification route is fully represented by tests/check.py."
                    ),
                }
            ],
        },
    )
    revision = str(state["revision"])
    closed = journey.run(
        "review",
        "disposition",
        "--input-json",
        disposition,
        "--expect-revision",
        revision,
    )
    assert closed["data"]["closure"]["closed"]


@pytest.mark.toolchain
@pytest.mark.e2e
def test_sealed_boundaries_installed_completion_retains_receipts_and_dependencies(
    installed, tmp_path
):
    journey = native_pending_host(installed, tmp_path, reviewed=True)
    before_completion = journey.read()
    ledger = before_completion["review_assignments"]
    assert ledger["schema"] == "heddle.review-assignments/v3"
    receipts = ledger["acceptances"]
    assert {(receipt["boundary"], receipt.get("scope")) for receipt in receipts} == {
        ("spec-review", None),
        ("plan-review", None),
        ("scaffold", None),
        ("milestone", "m1"),
    }
    assert all(receipt["assignments"] for receipt in receipts)
    assert not any(
        entry["assignment_id"].split(":")[1] == "peer-review-sequential"
        for receipt in receipts
        for entry in receipt["assignments"]
    )

    accepted = journey.run("feature", "complete", expected=4)

    assert accepted["data"]["accepted"] is True
    state_bytes = journey.state.read_bytes()
    archive_bytes = journey.archive.read_bytes()
    dependency_bytes = {}
    with tarfile.open(journey.archive, "r:gz") as archive:
        assert archive.extractfile("state.yaml").read() == state_bytes
        state = journey.read()
        for receipt in state["review_assignments"]["acceptances"]:
            for entry in receipt["assignments"]:
                for attempt_id in entry["attempt_ids"]:
                    attempt = next(
                        row
                        for row in state["review_assignments"]["attempts"]
                        if row["attempt_id"] == attempt_id
                    )
                    for artifact in attempt["artifacts"]:
                        if artifact["role"] not in {
                            "canonical",
                            "capture",
                            "evidence",
                            "log",
                        }:
                            continue
                        data = archive.extractfile(artifact["path"]).read()
                        assert sha256(data).hexdigest() == artifact["sha256"]
                        dependency_bytes[artifact["path"]] = data
                for reference in entry["records"]:
                    family = reference["family"]
                    rows = (
                        state["review_assignments"][family]
                        if family == "dispositions"
                        else state[family]
                    )
                    row = rows[reference["index"]]
                    canonical = json.dumps(
                        row, sort_keys=True, separators=(",", ":")
                    ).encode()
                    assert sha256(canonical).hexdigest() == reference["sha256"]
                    if family == "verifications":
                        for evidence in (
                            row["evidence"]["before"],
                            row["evidence"]["after"],
                        ):
                            data = archive.extractfile(evidence["artifact"]).read()
                            assert (
                                sha256(data).hexdigest() == evidence["artifact_sha256"]
                            )
                            dependency_bytes[evidence["artifact"]] = data

    journey.retain()
    retried = journey.run("feature", "complete")
    assert retried["data"]["effects"]["cleanup"]["status"] == "complete"
    assert journey.state.read_bytes() == state_bytes
    assert journey.archive.read_bytes() == archive_bytes
    with tarfile.open(journey.archive, "r:gz") as archive:
        assert archive.extractfile("state.yaml").read() == state_bytes
        for relative, data in dependency_bytes.items():
            assert archive.extractfile(relative).read() == data
    for relative, data in dependency_bytes.items():
        assert (journey.state.parent / relative).read_bytes() == data


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac12_fresh_native_installed_completion_retains_history_and_starts_next_feature(
    installed, tmp_path
):
    journey = native_pending_host(installed, tmp_path, reviewed=True)
    pending = journey.read()
    baseline = pending["source_baseline"]["oid"]
    head = git(journey.root, "rev-parse", "HEAD").decode().strip()
    assert baseline != head
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", baseline, head],
            cwd=journey.root,
            check=False,
        ).returncode
        == 0
    )
    spec = journey.root / SPEC
    spec_mode = spec.stat().st_mode
    retained_paths = (
        journey.state.parent / "plan.md",
        journey.state.parent / "brief.md",
        journey.root / f"journey-plans/{FEATURE}.decision-journal.md",
        journey.root / f"journey-plans/{FEATURE}.friction-retrospective.md",
    )
    retained = {
        path: (path.read_bytes(), path.stat().st_mode) for path in retained_paths
    }
    git_before_completion = git_authority(journey.root)
    before = snapshot_tree(journey.root)
    journey.run("feature", "complete", "--dry-run")
    assert snapshot_tree(journey.root) == before
    accepted = journey.run("feature", "complete")
    assert accepted["data"]["accepted"] is True
    assert accepted["data"]["effects"]["cleanup"]["status"] == "complete"
    assert accepted["data"]["effects"]["cleanup"]["paths"], (
        "FAIL local-history AC-5: installed proof needs nonempty bounded cleanup"
    )
    state_bytes = journey.state.read_bytes()
    fact = journey.read()["completion"]
    assert journey.calls.read_text().splitlines().count("close") == 1
    assert journey.count(journey.providers) == len(REVIEWED_ROLES)
    runs = [run for gate in journey.read()["gates"] for run in gate["runs"]]
    assert len(runs) == len(REVIEWED_ROLES)
    with tarfile.open(journey.archive, "r:gz") as archive:
        assert archive.extractfile("state.yaml").read() == state_bytes
        for run in runs:
            assert (
                archive.extractfile(run["artifact"]).read()
                == (journey.state.parent / run["artifact"]).read_bytes()
            )
    completed = journey.run("feature", "complete")
    assert completed["data"]["effects"]["cleanup"]["status"] == "complete"
    assert (
        journey.state.read_bytes() == state_bytes
        and journey.read()["completion"] == fact
    )
    assert (journey.state.parent / "plan.md").is_file()
    assert (journey.state.parent / "brief.md").is_file()
    for run in runs:
        assert (journey.state.parent / run["artifact"]).is_file()
    for path, (data, mode) in retained.items():
        assert path.read_bytes() == data
        assert path.stat().st_mode == mode
    assert b"lifecycle: complete" in spec.read_bytes()
    assert spec.stat().st_mode == spec_mode
    assert git_authority(journey.root) == git_before_completion, (
        "FAIL local-history AC-9: completion mutated refs, index, or ignore policy"
    )
    tracked = set(git(journey.root, "ls-files", "-z").decode().split("\0"))
    private_prefixes = (
        ".heddle/intake/",
        "journey-plans/",
        "docs/features/",
        "docs/gate-trajectories/",
    )
    assert not [path for path in tracked if path.startswith(private_prefixes)]
    assert journey.read()["source_baseline"]["kind"] == "git-commit"
    found = journey.run("search", "declared value", feature=False)
    assert any(row["path"] == SPEC for row in found["data"]["hits"])
    (journey.root / "src/example.py").write_text("VALUE = 8\n")
    for operation in ("status", "orient", "doctor"):
        journey.run(operation)
    journey.run("validate", feature=False)
    journey.run("drive")
    assert journey.state.read_bytes() == state_bytes
    (journey.root / "brief.md").write_text("# Research\n\nDeliver the next value.\n")
    admit(journey, slug="next-example")
    assert journey.run("status", feature=False)["data"]["feature"] == "next-example"
    journey.run("status")
    assert journey.run("status", feature=False)["data"]["feature"] == "next-example"
    assert journey.calls.read_text().splitlines().count("close") == 1
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac9_installed_completed_review_publication_recovers_without_provider_retry(
    installed, tmp_path
):
    """A published canonical result is recoverable before another paid attempt."""
    journey = start_host(installed, tmp_path, reviewed=True)
    (journey.root / "src").mkdir()
    (journey.root / "tests").mkdir()
    (journey.root / "src/example.py").write_text("VALUE = 7\n")
    (journey.root / "tests/check.py").write_text(
        "def test_declared_value():\n    assert True\n"
    )
    write_declared_documents(journey)
    for role in ("spec-review", "plan-review"):
        journey.run("phase-exit", "--through", "complete")
        native_review(journey, tmp_path, role)
    journey.run("phase-exit", "--through", "complete")
    assert journey.read()["stage"] == "scaffold"
    original_command = "python3 tests/check.py close"
    journey.run("commands", "set", "test_command", "--command", original_command)

    payload = review_content("review-test-scaffolding")
    event = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": payload,
            }
        )
        + "\n"
    )
    binary = write_claude_shim(tmp_path / "recovery-bin", event, journey.providers)
    journey.env["PATH"] = (
        str(binary.parent) + os.pathsep + journey.installed.env["PATH"]
    )
    provider_calls_before = journey.count(journey.providers)
    source = f"""
import os, sys
from pathlib import Path
from heddle.cli import main
from heddle.runtime import state_store
state = Path({str(journey.state)!r})
os.environ['PATH'] = {str(binary.parent)!r} + os.pathsep + os.environ['PATH']
publish = state_store._publish
def interrupt(path, text, *, create):
    if Path(path) == state and not create:
        raise OSError('injected canonical state publication failure')
    return publish(path, text, create=create)
state_store._publish = interrupt
sys.exit(main([
    'run-gate', 'review-test-scaffolding', '--feature', {FEATURE!r},
    '--cli', 'claude', '--model', {FABLE["model"]!r},
    '--reasoning-effort', 'high', '--json'
]))
"""
    interrupted = journey.installed.python_probe(source, cwd=journey.nested)
    journey.record(interrupted, cwd=journey.nested)
    assert interrupted.returncode == 3, (interrupted.stdout, interrupted.stderr)
    assert journey.count(journey.providers) == provider_calls_before + 1
    assert not any(
        gate["runs"]
        for gate in journey.read()["gates"]
        if gate["gate"] == "review-test-scaffolding"
    )
    orphans = list(
        journey.state.parent.glob("reviews/review-test-scaffolding.*.review.json")
    )
    assert len(orphans) == 1
    orphan_bytes = orphans[0].read_bytes()

    spec = journey.root / SPEC
    original_spec = spec.read_bytes()
    spec.write_bytes(original_spec + b"\nChanged reviewed source.\n")
    source_refusal = journey.run(
        "run-gate",
        "review-test-scaffolding",
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=(3, 4),
    )
    assert "exact prepared replay" in repr(source_refusal).lower()
    assert journey.count(journey.providers) == provider_calls_before + 1
    spec.write_bytes(original_spec)

    journey.run(
        "commands",
        "set",
        "test_command",
        "--command",
        "python3 tests/check.py changed-manifest",
    )
    manifest_refusal = journey.run(
        "run-gate",
        "review-test-scaffolding",
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=(3, 4),
    )
    assert "exact prepared replay" in repr(manifest_refusal).lower()
    assert journey.count(journey.providers) == provider_calls_before + 1
    journey.run("commands", "set", "test_command", "--command", original_command)

    recovered = journey.run(
        "run-gate",
        "review-test-scaffolding",
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=(0, 4),
    )
    assert recovered["data"]["cached"] is True
    assert journey.count(journey.providers) == provider_calls_before + 1
    assert orphans[0].read_bytes() == orphan_bytes
    scaffold = next(
        gate
        for gate in journey.read()["gates"]
        if gate["gate"] == "review-test-scaffolding"
    )
    assert len(scaffold["runs"]) == 1
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()


def interrupt(journey, cut):
    source = f"""
import os, sys, yaml
from pathlib import Path
from heddle.cli import main
from heddle.runtime import state_store
state = Path({str(journey.state)!r})
spec = Path({str(journey.root / SPEC)!r})
archive = Path({str(journey.archive)!r})
cut = {cut!r}
publish = state_store._publish
replace, link, unlink = os.replace, os.link, os.unlink
def cut_publish(path, text, **kwargs):
    accepting = path == state and yaml.safe_load(text).get("completion") is not None
    if accepting and cut == "preaccept": os._exit(88)
    publish(path, text, **kwargs)
    if accepting and cut == "postaccept": os._exit(88)
def cut_replace(source, target, *args, **kwargs):
    replace(source, target, *args, **kwargs)
    if Path(target) == spec and cut == "poststamp": os._exit(88)
def cut_link(source, target, *args, **kwargs):
    if Path(target) == archive and cut == "archive-failure":
        raise OSError("injected archive publication failure")
    link(source, target, *args, **kwargs)
    if Path(target) == archive and cut == "postarchive": os._exit(88)
def cut_unlink(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    eligible = Path(path).is_relative_to(state.parent / "reviews")
    if eligible and cut == "partial-cleanup":
        os._exit(88)
state_store._publish = cut_publish
os.replace, os.link, os.unlink = cut_replace, cut_link, cut_unlink
sys.exit(main(["feature", "complete", "--feature", {FEATURE!r}, "--json"]))
"""
    result = journey.installed.python_probe(source, cwd=journey.nested)
    journey.record(result, cwd=journey.nested)
    return result


@pytest.mark.parametrize(
    "cut",
    [
        "preaccept",
        "postaccept",
        "poststamp",
        "archive-failure",
        "postarchive",
        "partial-cleanup",
    ],
)
@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac13_installed_interruption_retry_preserves_acceptance(
    installed, tmp_path, cut
):
    journey = native_pending_host(
        installed, tmp_path, reviewed=cut == "partial-cleanup"
    )
    git_before_fault = git_authority(journey.root)
    journey.run("feature", "complete", "--dry-run")
    unknown = None
    unknown_bytes = b"operator-owned, unindexed evidence\x00\xff\n"
    unknown_mode = None
    if cut == "partial-cleanup":
        unknown = journey.state.parent / "operator-notes.bin"
        unknown.write_bytes(unknown_bytes)
        unknown.chmod(0o640)
        unknown_mode = unknown.stat().st_mode
    result = interrupt(journey, cut)
    assert result.returncode == (4 if cut == "archive-failure" else 88), (
        result.stdout,
        result.stderr,
    )
    if cut == "archive-failure":
        failure = parse_envelope(result)
        assert not journey.archive.exists(), "archive failure injection did not fire"
        assert "injected archive publication failure" in repr(failure)
    after_fault = journey.read().get("completion")
    accepted_bytes = journey.state.read_bytes()
    count_before = journey.calls.read_text().splitlines().count("close")
    recovered = journey.run("feature", "complete")
    assert recovered["data"]["accepted"] is True
    if cut == "partial-cleanup":
        assert unknown is not None and unknown_mode is not None
        relative = unknown.relative_to(journey.state.parent).as_posix()
        assert relative in recovered["data"]["effects"]["cleanup"]["preserved"]
        assert unknown.is_file()
        assert unknown.read_bytes() == unknown_bytes
        assert unknown.stat().st_mode == unknown_mode
    if cut == "preaccept":
        assert after_fault is None
        assert journey.calls.read_text().splitlines().count("close") == count_before + 1
    else:
        assert after_fault is not None and journey.state.read_bytes() == accepted_bytes
        assert journey.calls.read_text().splitlines().count("close") == count_before
    journey.run("feature", "complete")
    assert journey.archive.is_file() and journey.state.is_file()
    assert (journey.state.parent / "plan.md").is_file()
    assert (journey.state.parent / "brief.md").is_file()
    assert git_authority(journey.root) == git_before_fault
    assert journey.calls.read_text().splitlines().count("close") == (
        count_before + 1 if cut == "preaccept" else count_before
    )


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac13_installed_accepted_spec_conflict_is_repairable(installed, tmp_path):
    journey = native_pending_host(installed, tmp_path)
    journey.run("feature", "complete", "--dry-run")
    original = (journey.root / SPEC).read_bytes()
    result = interrupt(journey, "postaccept")
    assert result.returncode == 88, (result.stdout, result.stderr)
    state = journey.state.read_bytes()
    (journey.root / SPEC).write_bytes(original + b"operator changed accepted target\n")
    conflict = journey.run("feature", "complete", expected=4)
    assert conflict["data"]["effects"]["stamp"]["status"] == "conflict"
    assert conflict["next_actions"] and journey.state.read_bytes() == state
    journey.run("status")
    journey.run("orient")
    journey.run("drive", expected=4)
    (journey.root / SPEC).write_bytes(original)
    journey.run("feature", "complete", expected=4)
    journey.retain()
    journey.run("feature", "complete")
    assert journey.state.read_bytes() == state
    assert journey.calls.read_text().splitlines().count("close") == 1


@pytest.mark.parametrize("flow", ["hitl", "auto"])
@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac13_survivor_native_pending_handoff_precedes_provider_capability(
    installed, tmp_path, flow
):
    """Survivor: complete-stage human handoff already precedes capability calls."""
    journey = native_pending_host(installed, tmp_path, flow=flow)
    sentinel = (
        "#!/usr/bin/env python3\nfrom pathlib import Path\n"
        f"log = Path({str(journey.providers)!r})\n"
        "log.open('a').write('unexpected provider call\\n')\n"
        "raise SystemExit(22)\n"
    )
    for name in ("claude", "codex"):
        write_executable(tmp_path / "sentinels" / name, sentinel)
    journey.env["PATH"] = str(tmp_path / "sentinels") + os.pathsep + journey.env["PATH"]
    before = journey.state.read_bytes()
    count = journey.count(journey.calls)
    journey.run("drive", "--until", "complete")
    blocked = journey.run("drive", expected=1)
    assert "awaiting-human-completion" in repr(blocked)
    assert (
        journey.state.read_bytes() == before and journey.count(journey.calls) == count
    )
    assert journey.count(journey.providers) == 0


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac14_native_birth_and_old_format_refusal_are_separate_hosts(
    installed, tmp_path
):
    journey = start_host(installed, tmp_path)
    assert journey.read()["schema"] == "heddle.state/v10"
    assert journey.read()["review_assignments"]["schema"] == (
        "heddle.review-assignments/v3"
    )
    assert "tier" not in journey.read()
    assert journey.read()["completion"] is None
    # Authored incompatible specimen, never imported into the fresh host.
    old_root = tmp_path / "old-format-host"
    old_root.mkdir()
    (old_root / ".heddle.yaml").write_text("layout:\n  plans: plans\n")
    state = old_root / "plans/legacy/state.yaml"
    state.parent.mkdir(parents=True)
    old = deepcopy(journey.read())
    old["schema"] = "heddle.state/v8"
    old["feature"] = "legacy"
    old["review_assignments"]["schema"] = "heddle.review-assignments/v2"
    old["review_assignments"].pop("acceptances")
    state.write_text(yaml.safe_dump(old, sort_keys=False))
    before = snapshot_tree(old_root)
    result = installed.run("status", "--feature", "legacy", "--json", cwd=old_root)
    journey.record(result, cwd=old_root)
    value = parse_envelope(result)
    assert not value["ok"]
    assert "v8" in repr(value).lower() and "compatible" in repr(value).lower()
    assert snapshot_tree(old_root) == before
    # Preservation stays outside the candidate's operational plans discovery.
    history = journey.root / "docs/gate-trajectories/.raw/old-format"
    history.mkdir(parents=True)
    preserved = history / "state.yaml"
    preserved.write_bytes(state.read_bytes())
    history_before = snapshot_tree(history)
    portfolio = journey.run("status", "--all", feature=False)
    assert [row["feature"] for row in portfolio["data"]["features"]] == [FEATURE]
    journey.run("validate", feature=False)
    assert snapshot_tree(history) == history_before


@pytest.mark.toolchain
def test_ac14_survivor_retired_admission_keeps_historical_interpretation(
    installed, tmp_path, monkeypatch
):
    slug = "operational-model-cleanup"
    root, original = legacy_host(tmp_path, monkeypatch)
    value = yaml.safe_load(original.read_text())
    value["feature"] = slug
    workspace = root / "plans" / slug
    original.parent.rename(workspace)
    state = workspace / "state.yaml"
    state.write_text(yaml.safe_dump(value, sort_keys=False))
    before = state.read_bytes()
    result = installed.run(
        "run-gate", "spec-review", "--feature", slug, "--json", cwd=root
    )
    value = parse_envelope(result)
    assert not value["ok"] and "review assignment" in repr(value)
    assert state.read_bytes() == before
    probe = installed.python_probe(
        "import importlib.util; "
        "assert importlib.util.find_spec('heddle.contracts.review_authority') is None",
        cwd=root,
    )
    assert probe.returncode == 0, probe.stderr
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()


@pytest.mark.toolchain
@pytest.mark.e2e
def test_distinct_gate_concurrency_installed_partial_publication_recovery(
    installed, tmp_path, monkeypatch
):
    """AC-8: a built command recovers both completed outputs after a CAS loss."""
    from tests.runtime.test_distinct_gate_concurrency import (
        GROUP,
        V7_FEATURE,
        _batch_host,
    )

    host, state_path = _batch_host(tmp_path, monkeypatch)
    spec_path = host / f"docs/features/runtime/{V7_FEATURE}.md"
    spec_path.write_text(
        "---\n"
        f"feature: {V7_FEATURE}\n"
        "area: runtime\n"
        "lifecycle: active\n"
        "---\n\n" + spec_path.read_text()
    )
    bin_dir = tmp_path / "batch-provider-bin"
    calls = tmp_path / "batch-provider-calls.jsonl"
    start = tmp_path / "batch-start.sock"
    releases = {role: tmp_path / f"release-{role}.sock" for role in GROUP}

    payloads = {role: review_content(role) for role in GROUP}
    release_paths = {role: str(path) for role, path in releases.items()}
    source = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json
        import os
        import socket
        import sys
        from pathlib import Path

        cli = Path(sys.argv[0]).name
        prompt = sys.stdin.read()
        role = next(role for role in {GROUP!r} if role in prompt)
        with Path({str(calls)!r}).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({{"cli": cli, "role": role}}) + "\\n")
        releases = {release_paths!r}
        control = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        control.bind(releases[role])
        control.sendto(role.encode(), {str(start)!r})
        control.settimeout(10)
        control.recv(1)
        control.close()
        Path(releases[role]).unlink(missing_ok=True)
        payload = {payloads!r}[role]
        text = json.dumps(payload)
        if cli == "claude":
            print(json.dumps({{
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": payload,
            }}))
        else:
            output = Path(sys.argv[sys.argv.index("-o") + 1])
            output.write_text(text, encoding="utf-8")
            event = {{
                "type": "item.completed",
                "item": {{"type": "agent_message", "text": text}},
            }}
            print(json.dumps(event))
            print(json.dumps({{"type": "turn.completed", "usage": {{}}}}))
        """
    )
    for cli in ("claude", "codex"):
        write_executable(bin_dir / cli, source)
    env = dict(installed.env)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]

    start_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    start_socket.bind(str(start))
    start_socket.settimeout(10)
    release_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    admitted_revision = yaml.safe_load(state_path.read_text())["revision"]
    process = subprocess.Popen(
        [
            str(installed.command),
            "run-gates",
            "--feature",
            V7_FEATURE,
            "--json",
        ],
        cwd=host,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        started = {start_socket.recv(64).decode() for _index in GROUP}
        assert started == set(GROUP), f"FAIL AC-8: provider starts were {started}"
        release_socket.sendto(b"1", str(releases["behavior-review"]))

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if yaml.safe_load(state_path.read_text())["revision"] == (
                admitted_revision + 1
            ):
                break
            assert process.poll() is None, (
                "FAIL AC-8: batch exited before first publish"
            )
        else:
            raise AssertionError(
                "FAIL AC-8: first member did not publish in 10 seconds"
            )
        unrelated = installed.run(
            "commands",
            "set",
            "lint_command",
            "--command",
            ".venv/bin/ruff check heddle",
            "--feature",
            V7_FEATURE,
            "--json",
            cwd=host,
            env=env,
        )
        assert unrelated.returncode == 0, unrelated.stderr
        release_socket.sendto(b"1", str(releases["complexity-review"]))
        stdout, stderr = process.communicate(timeout=30)
    finally:
        start_socket.close()
        release_socket.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    first = parse_envelope(
        subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    )
    assert not first["ok"] and len(first["error"]["details"]["members"]) == 2
    assert [row["publication"] for row in first["error"]["details"]["members"]] == [
        "recorded",
        "recoverable",
    ]
    assert len(list((state_path.parent / "reviews").glob("*.review.json"))) == 2

    sentinel = (
        "#!/bin/sh\necho unexpected-provider-call >> " + str(calls) + "\nexit 22\n"
    )
    for cli in ("claude", "codex"):
        write_executable(bin_dir / cli, sentinel)
    before_calls = calls.read_text().splitlines()
    recovered = installed.run(
        "run-gates",
        "--feature",
        V7_FEATURE,
        "--json",
        cwd=host,
        env=env,
    )
    envelope = parse_envelope(recovered)
    assert recovered.returncode == 0 and envelope["ok"], envelope
    assert calls.read_text().splitlines() == before_calls
    value = yaml.safe_load(state_path.read_text())
    recorded = [
        {"gate": gate["gate"], **run} for gate in value["gates"] for run in gate["runs"]
    ]
    assert len(recorded) == 2
    assert tuple(member["reuse"] for member in envelope["data"]["members"]) == (
        "exact",
        "recovered",
    )
    artifact_evidence = {}
    for member in envelope["data"]["members"]:
        relative = member["artifact"]
        artifact = state_path.parent / relative
        raw = artifact.read_bytes()
        digest = sha256(raw).hexdigest()
        run = next(row for row in recorded if row["gate"] == member["gate"])
        assert run["artifact"] == relative
        assert run["artifact_sha256"] == digest
        artifact_evidence[relative] = (raw, digest)
    assert len(artifact_evidence) == 2

    disposition_path = tmp_path / "batch-dispositions.json"
    disposition_path.write_text(
        json.dumps(
            {
                "schema": "heddle.review-disposition-input/v1",
                "dispositions": [
                    {
                        "run_id": run["run_id"],
                        "finding_id": "@coverage",
                        "status": "settled",
                        "evidence_kind": "inspection",
                        "references": [
                            f"docs/features/runtime/{V7_FEATURE}.md",
                            f"plans/{V7_FEATURE}/plan.md",
                        ],
                        "reason": (
                            "The installed recovery journey exercises both gate "
                            "assignments through the public built command."
                        ),
                    }
                    for run in recorded
                ],
            }
        )
        + "\n"
    )
    disposed = installed.run(
        "review",
        "disposition",
        "--input-json",
        str(disposition_path),
        "--expect-revision",
        str(value["revision"]),
        "--feature",
        V7_FEATURE,
        "--json",
        cwd=host,
        env=env,
    )
    assert disposed.returncode == 0, disposed.stderr

    command = f"{installed.python} -c 'print(7)'"
    for key in ("smoke_test", "acceptance_test", "live_e2e_test"):
        configured = installed.run(
            "commands",
            "set",
            key,
            "--command",
            command,
            "--feature",
            V7_FEATURE,
            "--json",
            cwd=host,
            env=env,
        )
        assert configured.returncode == 0, configured.stderr
    for scope in ("m1", "m2", "acceptance", "smoke", "live"):
        verified = installed.run(
            "verify",
            "--scope",
            scope,
            "--feature",
            V7_FEATURE,
            "--json",
            cwd=host,
            env=env,
        )
        assert verified.returncode == 0, verified.stderr
    advanced = installed.run(
        "phase-exit",
        "--through",
        "complete",
        "--feature",
        V7_FEATURE,
        "--json",
        cwd=host,
        env=env,
    )
    assert advanced.returncode == 0, advanced.stderr
    assert yaml.safe_load(state_path.read_text())["stage"] == "robustness"
    finalized = installed.run(
        "phase-exit",
        "--feature",
        V7_FEATURE,
        "--json",
        cwd=host,
        env=env,
    )
    assert finalized.returncode == 0, finalized.stderr
    assert yaml.safe_load(state_path.read_text())["stage"] == "complete"
    (host / f"plans/{V7_FEATURE}.decision-journal.md").write_text(
        "# Decision Journal\n\nInstalled recovery evidence retained.\n"
    )
    (host / f"plans/{V7_FEATURE}.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        "## Tooling fights\n\nNone.\n\n"
        "## Prompt gaps\n\nNone.\n\n"
        "## Validator false-positives\n\nNone.\n\n"
        "## Escalations that should have been policy-resolvable\n\nNone.\n"
    )
    accepted = installed.run(
        "feature",
        "complete",
        "--feature",
        V7_FEATURE,
        "--json",
        cwd=host,
        env=env,
    )
    assert accepted.returncode in (0, 4), accepted.stderr
    archive = host / f"docs/gate-trajectories/.raw/{V7_FEATURE}/completion.tar.gz"
    assert archive.is_file()
    with tarfile.open(archive, "r:gz") as retained:
        names = set(retained.getnames())
        assert "state.yaml" in names
        state_bytes = retained.extractfile("state.yaml").read()
        assert (
            sha256(state_bytes).hexdigest()
            == sha256(state_path.read_bytes()).hexdigest()
        )
        for relative, (raw, digest) in artifact_evidence.items():
            assert relative in names
            archived = retained.extractfile(relative).read()
            assert archived == raw
            assert sha256(archived).hexdigest() == digest
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()
