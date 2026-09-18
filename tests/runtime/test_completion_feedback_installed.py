"""Installed acceptance for truthful completion-feedback contracts."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tarfile
import tempfile
from hashlib import sha256
from pathlib import Path

import pytest

from tests.content_identity_helpers import git
from tests.runtime import test_completion_installed as installed_flow
from tests.runtime.test_completion_installed import FEATURE, start_host
from tests.runtime.wheel_harness import (
    REPO_ROOT,
    build_installed_wheel,
    snapshot_tree,
    write_claude_shim,
)
from tests.tiering_helpers import FABLE
from tests.tiering_review_helpers import review_content

pytestmark = [pytest.mark.acceptance, pytest.mark.e2e, pytest.mark.toolchain]


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("completion-feedback-wheel"))


def _review_briefing_contract(journey, stage: str) -> None:
    before = snapshot_tree(journey.root)
    orient = journey.run("orient")
    kickoff = journey.run("kickoff")
    assert journey.read()["stage"] == stage
    assert orient["data"]["workspace"] == f"journey-plans/{FEATURE}/"
    text = kickoff["data"]["briefing"]
    assert (
        "An optional lead-authored assessment that supports native review closure "
        "is a workflow review record."
    ) in text
    assert (
        "A product assessment remains an owned product artifact outside the "
        "protected workflow workspace."
    ) in text
    assert "data.workspace" in text
    assert "reviews/" in text
    assert "native dispositions" in text.lower()
    assert "plans/<slug>/reviews/" not in text
    assert "every assessment is a workflow review record" not in text.lower()
    assert "assessment is required" not in text.lower()
    assert snapshot_tree(journey.root) == before


def _retained_review(journey, tmp_path, role: str) -> None:
    content = review_content(role)
    imperfect = {**content, "extra_explanation": "retain these complete bytes"}
    event = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": imperfect,
            }
        )
        + "\n"
    )
    binary = write_claude_shim(tmp_path / "imperfect-bin", event, journey.providers)
    journey.env["PATH"] = (
        str(binary.parent) + os.pathsep + journey.installed.env["PATH"]
    )
    retained = journey.run(
        "run-gate",
        role,
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=4,
    )
    data = retained["data"]
    assert data["accepted"] is False and data["capture_sha256"]
    interpretation = journey.payload(
        f"{role}-interpretation.json",
        {
            "schema": "heddle.review-interpretation-input/v1",
            "run_id": data["run_id"],
            "capture_sha256": data["capture_sha256"],
            "author": "lead",
            "reason": "Preserved all content and removed only extra presentation.",
            "content": content,
        },
    )
    journey.run("review", "interpret", "--input-json", interpretation)
    state = journey.read()
    disposition = journey.payload(
        f"{role}-disposition.json",
        {
            "schema": "heddle.review-disposition-input/v1",
            "dispositions": [
                {
                    "run_id": data["run_id"],
                    "finding_id": "@coverage",
                    "status": "settled",
                    "evidence_kind": "inspection",
                    "references": ["tests/check.py"],
                    "reason": "The installed fixture has complete local coverage.",
                }
            ],
        },
    )
    closed = journey.run(
        "review",
        "disposition",
        "--input-json",
        disposition,
        "--expect-revision",
        str(state["revision"]),
    )
    assert closed["data"]["closure"]["closed"]


def _expected_roles(state: dict) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {"state.yaml": {"accepted-ledger"}}

    def add(reference: dict, role: str | None = None) -> None:
        selected = reference.get("role") if role is None else role
        if selected in {"canonical", "capture", "evidence", "log"}:
            roles.setdefault(reference["path"], set()).add(selected)

    for attempt in state["review_assignments"]["attempts"]:
        for reference in attempt["artifacts"]:
            add(reference)
        outcome = attempt["outcome"]
        for key in ("result", "capture"):
            if isinstance(outcome.get(key), dict):
                add(outcome[key])
        interpretation = attempt.get("interpretation")
        if isinstance(interpretation, dict):
            add(interpretation["result"])
    for gate in state["gates"]:
        for run in gate["runs"]:
            if run["verdict"].get("status") != "error":
                roles.setdefault(run["artifact"], set()).add("review-record")
    for fact in state["verifications"]:
        roles.setdefault(fact["log"], set()).add("verification-log")
        for boundary in ("before", "after"):
            artifact = fact["evidence"][boundary]["artifact"]
            roles.setdefault(artifact, set()).add("verification-evidence")
    close_suite = state["completion"].get("close_suite")
    if close_suite is not None:
        roles.setdefault(close_suite["log"], set()).add("close-suite-log")
    return roles


def _publish_evidence(
    journey, wheel: Path, report: dict, *, base: Path | None = None
) -> Path:
    if base is None:
        base = (
            REPO_ROOT
            / "plans/completion-feedback-contracts-v1/archive/acceptance"
            / "installed-completion-feedback"
        )
    ledger = next(row for row in report["artifacts"] if row["path"] == "state.yaml")
    artifacts = {
        wheel.name: wheel.read_bytes(),
        "completion.tar.gz": journey.archive.read_bytes(),
        "transcript.json": (json.dumps(journey.transcript, indent=2) + "\n").encode(),
        "retained-evidence.json": (json.dumps(report, indent=2) + "\n").encode(),
    }
    manifest = {
        "schema": "heddle.completion-feedback-acceptance/v1",
        "feature": FEATURE,
        "fixture_ledger_identity": ledger["sha256"],
        "artifacts": {
            name: sha256(content).hexdigest() for name, content in artifacts.items()
        },
    }
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode()
    package = {**artifacts, "manifest.json": manifest_bytes}
    ledger_directory = base / ledger["sha256"]
    destination = ledger_directory / sha256(manifest_bytes).hexdigest()

    def validate_existing() -> Path:
        assert destination.is_dir(), f"evidence collision at {destination}"
        for name, expected in package.items():
            path = destination / name
            assert path.is_file() and path.read_bytes() == expected, (
                f"non-identical evidence collision at {path}"
            )
        return destination

    if destination.exists():
        return validate_existing()

    ledger_directory.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".pending-", dir=ledger_directory))
    try:
        for name, content in package.items():
            (staged / name).write_bytes(content)
        try:
            staged.rename(destination)
        except OSError:
            if destination.exists():
                return validate_existing()
            raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return destination


def test_completion_feedback_contract_installed(
    installed, tmp_path, monkeypatch
) -> None:
    """AC-1..AC-8: one installed CLI crosses every feature boundary."""
    review = start_host(installed, tmp_path / "review-host", reviewed=False)
    review.run("phase-exit", "--through", "complete")
    _review_briefing_contract(review, "spec-review")
    review.run("phase-exit", "--through", "complete")
    _review_briefing_contract(review, "plan-review")

    original_review = installed_flow.native_review

    def retained_then_canonical(journey, fixture_path, role):
        if role == "spec-review":
            return _retained_review(journey, fixture_path, role)
        return original_review(journey, fixture_path, role)

    monkeypatch.setattr(installed_flow, "native_review", retained_then_canonical)
    journey = installed_flow.native_pending_host(
        installed, tmp_path / "completion-host", reviewed=True
    )
    misplaced = journey.state.parent / "lead-assessment.md"
    misplaced.write_text("# Optional lead review assessment\n")
    misplaced_relative = misplaced.relative_to(journey.root).as_posix()
    git(journey.root, "add", "-f", misplaced_relative)
    repair_baseline = snapshot_tree(journey.root)
    repair_calls = journey.count(journey.calls)
    for flags in (("--dry-run",), ()):
        refused = journey.run("feature", "complete", *flags, expected=3)
        assert refused["error"]["code"] == "workspace-invalid"
        repair = refused["error"]["message"] + "\n" + refused["error"]["hint"]
        assert "journey-plans/completion-example/lead-assessment.md" in repair
        assert "journey-plans/completion-example/reviews/" in repair
        assert "review record" in repair.lower()
        assert snapshot_tree(journey.root) == repair_baseline
        assert journey.count(journey.calls) == repair_calls
        assert journey.read()["completion"] is None
    git(journey.root, "reset", "--", misplaced_relative)
    legal = journey.state.parent / "reviews/lead-assessment.md"
    legal.parent.mkdir(exist_ok=True)
    misplaced.rename(legal)
    session = journey.payload(
        "terminal-session.yaml",
        {
            "started_at": "2026-09-18T08:00Z",
            "ended_at": "2026-09-18T08:30Z",
            "completed": ["finished implementation"],
            "started": [],
            "key_context": "installed terminal handoff",
            "next_steps": "stale historical handoff",
            "blockers": [],
        },
    )
    journey.run("session", "log", "--from-file", session)
    before_completion = journey.read()
    cleanup_candidates = sorted(
        {
            reference["path"]
            for attempt in before_completion["review_assignments"]["attempts"]
            for reference in attempt["artifacts"]
            if reference["role"] in {"derived", "temporary"}
            and (journey.state.parent / reference["path"]).is_file()
        }
    )
    assert cleanup_candidates
    unknown = journey.state.parent / "operator-notes.bin"
    unknown.write_bytes(b"operator-owned\x00evidence\n")

    completed = journey.run("feature", "complete", expected=(0, 4))
    report = completed["data"]["retained_evidence"]
    assert report["status"] == "archive-bound"
    assert report["workspace"] == f"journey-plans/{FEATURE}/"
    assert [row["path"] for row in report["artifacts"]] == sorted(
        {row["path"] for row in report["artifacts"]}
    )
    assert all(row.get("archive_member") == row["path"] for row in report["artifacts"])
    accepted_state = journey.read()
    expected_roles = _expected_roles(accepted_state)
    assert {row["path"] for row in report["artifacts"]} == set(expected_roles)
    for row in report["artifacts"]:
        path = journey.state.parent / row["path"]
        assert row["roles"] == sorted(expected_roles[row["path"]])
        assert len(row["roles"]) == len(set(row["roles"]))
        assert row["kind"] == "file"
        assert row["sha256"] == sha256(path.read_bytes()).hexdigest()
        assert row["mode"] == stat.S_IMODE(path.stat().st_mode)
        assert row["archive_member"] == row["path"]
    capture = next(row for row in report["artifacts"] if "capture" in row["roles"])
    assert "operator-notes.bin" not in {row["path"] for row in report["artifacts"]}
    assert completed["data"]["effects"]["cleanup"]["paths"] == cleanup_candidates
    preserved = set(completed["data"]["effects"]["cleanup"]["preserved"])
    assert {"operator-notes.bin", "reviews/lead-assessment.md"} <= preserved
    assert preserved.isdisjoint(cleanup_candidates)
    assert preserved.isdisjoint(row["path"] for row in report["artifacts"])
    assert all(
        not (journey.state.parent / path).exists() for path in cleanup_candidates
    )
    with tarfile.open(journey.archive, "r:gz") as archive:
        assert (
            archive.extractfile("reviews/lead-assessment.md").read()
            == legal.read_bytes()
        )

    tree_after_completion = snapshot_tree(journey.root)
    state_after_completion = journey.state.read_bytes()
    archive_after_completion = journey.archive.read_bytes()
    session_after_completion = accepted_state["sessions"]
    provider_calls = journey.count(journey.providers)
    close_suite_calls = journey.count(journey.calls)

    for command in ("status", "orient", "kickoff"):
        observed = journey.run(command)
        assert observed["data"]["retained_evidence"] == report
    orient = journey.run("orient")
    assert orient["data"]["next_steps"] is None
    assert orient["data"]["latest_session"]["next_steps"] == "stale historical handoff"

    for command in (
        ("feature", "complete"),
        ("status",),
        ("orient",),
        ("kickoff",),
    ):
        human = installed.run(
            *command, "--feature", FEATURE, cwd=journey.nested, env=journey.env
        )
        journey.record(human, cwd=journey.nested)
        assert human.returncode in (0, 4), human.stdout + human.stderr
        rendered = human.stdout + human.stderr
        assert "stale historical handoff" not in rendered
        assert f"retained local evidence: {capture['path']}" in rendered
        assert f"roles: {', '.join(capture['roles'])}" in rendered
        assert (
            f"verified archive member: {report['archive']}::{capture['path']}"
            in rendered
        )

    assert snapshot_tree(journey.root) == tree_after_completion
    assert journey.state.read_bytes() == state_after_completion
    assert journey.archive.read_bytes() == archive_after_completion
    assert journey.read()["sessions"] == session_after_completion
    assert journey.count(journey.providers) == provider_calls
    assert journey.count(journey.calls) == close_suite_calls

    archive_bytes = journey.archive.read_bytes()
    journey.archive.write_bytes(archive_bytes + b"corrupt")
    damaged_tree = snapshot_tree(journey.root)
    damaged_state = journey.state.read_bytes()
    damaged_provider_calls = journey.count(journey.providers)
    damaged_close_calls = journey.count(journey.calls)
    for command in ("status", "orient", "kickoff"):
        pending = journey.run(command, expected=(0, 4))
        if command == "orient":
            assert pending["data"]["next_steps"] is None
            assert (
                pending["data"]["latest_session"]["next_steps"]
                == "stale historical handoff"
            )
        assert pending["data"]["retained_evidence"]["status"] == "conflict"
        assert any(
            action["reason"] == "retry pending effects of accepted completion"
            for action in pending["next_actions"]
        )
    for command in ("status", "orient", "kickoff"):
        human = installed.run(
            command, "--feature", FEATURE, cwd=journey.nested, env=journey.env
        )
        journey.record(human, cwd=journey.nested)
        rendered = human.stdout + human.stderr
        assert human.returncode == 0, rendered
        assert "stale historical handoff" not in rendered
        assert "retry pending effects" in rendered
        assert "retained evidence conflict" in rendered.lower()
    assert snapshot_tree(journey.root) == damaged_tree
    assert journey.state.read_bytes() == damaged_state
    assert journey.count(journey.providers) == damaged_provider_calls
    assert journey.count(journey.calls) == damaged_close_calls
    journey.archive.write_bytes(archive_bytes)
    repaired = journey.run("feature", "complete", expected=(0, 4))
    assert repaired["data"]["retained_evidence"] == report

    wheel = next((installed.root / "dist").glob("heddle-*.whl"))
    collision_base = tmp_path / "publication-collision"
    first_receipt = _publish_evidence(journey, wheel, report, base=collision_base)
    (first_receipt / "native-binding.json").write_text(
        '{"schema":"test-existing-binding/v1"}\n'
    )
    first_snapshot = snapshot_tree(first_receipt)
    changed_wheel = tmp_path / "changed-candidate" / wheel.name
    changed_wheel.parent.mkdir()
    changed_wheel.write_bytes(wheel.read_bytes() + b"changed candidate\n")
    second_receipt = _publish_evidence(
        journey, changed_wheel, report, base=collision_base
    )
    assert second_receipt != first_receipt
    assert snapshot_tree(first_receipt) == first_snapshot
    assert not (second_receipt / "native-binding.json").exists()

    _publish_evidence(journey, wheel, report)
