"""Composed fast acceptance for the durable operational core."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.tiering_helpers import entry
from tests.tiering_review_helpers import (
    current_host,
    gate_command,
    provider_transport,
    review_content,
)


def test_publication_attempt_and_manifest_boundaries_compose(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    from tests.runtime.verification_provenance_helpers import (
        convert_current_fixture_to_v8,
    )

    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review", limit=1)},
    )
    convert_current_fixture_to_v8(host, state_path)
    calls = provider_transport(monkeypatch, review_content("milestone-review"))
    initial = yaml.safe_load(state_path.read_text())

    code, review = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 0, review
    after_review = yaml.safe_load(state_path.read_text())
    # AC-1: the accepted review is one changed native publication.
    assert after_review["revision"] == initial["revision"] + 1
    assert after_review["review_assignments"]["schema"] == (
        "heddle.review-assignments/v3"
    )
    assert len(after_review["review_assignments"]["attempts"]) == 1
    assert len(calls) == 1
    attempt = after_review["review_assignments"]["attempts"][0]
    # AC-3/AC-4: completed output is durable and has one closed outcome.
    assert attempt["outcome"]["kind"] == "canonical-review"
    result = state_path.parent / attempt["outcome"]["result"]["path"]
    assert result.is_file()
    # AC-5: exact replay adds no attempt, revision, or provider call.
    code, replay = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 0 and replay["data"]["cached"] is True
    assert yaml.safe_load(state_path.read_text()) == after_review
    assert len(calls) == 1
    # AC-6: the original assignment and reviewer slot remain authoritative.
    assert attempt["assignment_id"].endswith(":milestone-review:m1:p1")
    assert attempt["reviewer_slot"] == "primary"
    assert attempt["invocation"]["cli"] == "codex"

    payload = tmp_path / "session.yaml"
    payload.write_text(
        "started_at: 2026-09-12T00:00Z\n"
        "ended_at: 2026-09-12T00:01Z\n"
        "completed: [composed append]\n"
        "started: []\n"
        "key_context: acceptance\n"
        "next_steps: continue\n"
        "blockers: []\n"
    )
    code, output, _error = run_cli(
        [
            "session",
            "log",
            "--from-file",
            str(payload),
            "--feature",
            "operational-example",
            "--json",
        ]
    )
    assert code == 0, output
    after_session = yaml.safe_load(state_path.read_text())
    # AC-2: an eligible append uses locked-latest state and preserves the attempt.
    assert after_session["revision"] == after_review["revision"] + 1
    assert after_session["review_assignments"] == after_review["review_assignments"]

    code, output, _error = run_cli(
        [
            "verify",
            "--scope",
            "m1",
            "--feature",
            "operational-example",
            "--json",
        ]
    )
    assert code == 0, output
    after_verify = yaml.safe_load(state_path.read_text())
    fact = after_verify["verifications"][-1]
    # AC-8: command, exit, log and stable before/after evidence remain bound.
    assert fact["exit_code"] == 0
    assert fact["command"] == after_verify["milestones"][0]["verification"]["command"]
    assert (state_path.parent / fact["log"]).is_file()
    assert fact["evidence"]["before"] == fact["evidence"]["after"]
    artifact = state_path.parent / fact["evidence"]["before"]["artifact"]
    # AC-7: the fact refers to one durable immutable evidence artifact.
    assert artifact.is_file()

    code, output, _error = run_cli(
        ["status", "--feature", "operational-example", "--json"]
    )
    status = envelope_tools.parse(output)
    # AC-9: the qualified public reader validates the required reference.
    assert code == 0 and status["data"]["verification_status"][0] == {
        "scope": "m1",
        "freshness": "fresh",
        "evidence_state": "current",
        "applicability": "required",
        "cause": None,
        "action": None,
    }
    terminal = yaml.safe_load(state_path.read_text())
    # AC-10: completion retention derives the log and shared manifest paths.
    from heddle.kernel.state import parse_state_document
    from heddle.runtime.completion import retained_evidence_paths

    typed = parse_state_document(terminal, source=state_path)
    assert retained_evidence_paths(typed.verifications) == (
        fact["log"],
        fact["evidence"]["before"]["artifact"],
    )
    # AC-11/AC-13: the cutover keeps policy and user authority in one current state.
    assert terminal["schema"] == "heddle.state/v9"
    assert terminal["feature_policy"] == initial["feature_policy"]
    assert terminal["authorizations"] == initial["authorizations"]
    # AC-12: expanded observations live once in the manifest, never in each fact.
    evidence = yaml.safe_load(artifact.read_text())
    assert evidence["observations"]
    assert all("observations" not in item for item in terminal["verifications"])
