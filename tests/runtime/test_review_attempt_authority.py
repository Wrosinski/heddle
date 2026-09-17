"""Review attempt authority and explicit artifact roles."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, fields, replace
from pathlib import Path

import pytest
import yaml

from tests.runtime.write_path_helpers import copy_host, read_yaml

REPO = Path(__file__).resolve().parents[2]
TINY = REPO / "tests/fixtures/workspaces/tiny"


def _invocation():
    from heddle.contracts.review_assignments import AttemptInvocation

    return AttemptInvocation(
        cli="codex",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        sandbox="danger-full-access",
        input_hash="a" * 64,
        review_basis_hash="b" * 64,
        prompt_version="prompt-v1",
        effective_prompt_sha256="c" * 64,
    )


def _artifact(path: str, role: str):
    from heddle.contracts.review_assignments import ArtifactRef

    return ArtifactRef(path=path, sha256="d" * 64, role=role)


def _attempt(outcome=None):
    from heddle.contracts.review_assignments import (
        CanonicalReview,
        ReviewAttempt,
    )

    return ReviewAttempt(
        attempt_id="00000000-0000-4000-8000-000000000101",
        assignment_id="feature:plan-review:feature:p1",
        round_number=1,
        reviewer_slot="primary",
        invocation=_invocation(),
        outcome=outcome
        or CanonicalReview(_artifact("reviews/result.review.json", "canonical")),
        created_at="2026-09-12T00:00Z",
    )


def test_ac4_closed_outcomes_require_only_applicable_evidence() -> None:
    from heddle.contracts.review_assignments import (
        CanonicalReview,
        EngineFailure,
        UsableReviewCapture,
        parse_review_attempt,
        review_attempt_document,
    )

    outcomes = (
        EngineFailure(reason="transport-error", detail="connection closed"),
        UsableReviewCapture(_artifact("reviews/raw.capture.json", "capture")),
        CanonicalReview(_artifact("reviews/result.review.json", "canonical")),
    )
    assert {
        parse_review_attempt(review_attempt_document(_attempt(outcome))).outcome.kind
        for outcome in outcomes
    } == {"engine-failure", "usable-capture", "canonical-review"}

    contradictory = review_attempt_document(_attempt(outcomes[0]))
    contradictory["outcome"]["result"] = {
        "path": "reviews/forged.review.json",
        "sha256": "e" * 64,
        "role": "canonical",
    }
    with pytest.raises(ValueError, match="outcome"):
        parse_review_attempt(contradictory)

    from heddle.gate.types import (
        GateCanonicalReview,
        GateEngineFailure,
        GateUsableReviewCapture,
    )

    shapes = {
        cls.__name__: {field.name for field in fields(cls)}
        for cls in (
            GateEngineFailure,
            GateUsableReviewCapture,
            GateCanonicalReview,
        )
    }
    assert "capture" not in shapes["GateEngineFailure"]
    assert "result" not in shapes["GateEngineFailure"]
    assert "result" not in shapes["GateUsableReviewCapture"]
    assert "capture" not in shapes["GateCanonicalReview"]
    failure = GateEngineFailure(
        gate_exit=1,
        reason="transport-error",
        rerun_recommended=None,
        structure_warnings=(),
    )
    assert failure.status is None
    assert failure.verdict == {"status": "error", "rerun_recommended": None}
    assert failure.findings["total"] == 0
    with pytest.raises(TypeError):
        GateEngineFailure(
            gate_exit=1,
            reason="transport-error",
            rerun_recommended=None,
            structure_warnings=(),
            status="pass",  # type: ignore[call-arg]
            verdict={"accepted": True},  # type: ignore[call-arg]
        )


def test_ac5_attempt_publication_is_exactly_idempotent_and_conflicts_by_identity() -> (
    None
):
    from heddle.kernel.recording import record_attempt

    document = {"attempts": []}
    attempt = _attempt()
    first, replay = record_attempt(document, attempt)
    assert replay is False and len(first["attempts"]) == 1
    second, replay = record_attempt(first, attempt)
    assert replay is True and second == first

    changed = deepcopy(attempt)
    object.__setattr__(changed, "invocation", _invocation())
    object.__setattr__(
        changed,
        "outcome",
        type(attempt.outcome)(_artifact("reviews/different.review.json", "canonical")),
    )
    with pytest.raises(ValueError, match="attempt identity conflict"):
        record_attempt(first, changed)


def test_ac5_runtime_publication_counts_one_revision_event_slot_and_call(
    tmp_path,
) -> None:
    """The completed attempt is the runtime accounting boundary, not a pure row."""
    from heddle.runtime import recording

    assert not hasattr(recording, "publish_review_attempt")
    assert not hasattr(recording, "recover_review_attempt")

    host = copy_host(tmp_path, TINY)
    path = host / "plans/sample-feature/state.yaml"
    before = read_yaml(path)
    attempt = _attempt()

    first = recording._publish_review_attempt(path, attempt=attempt)
    after_first = read_yaml(path)
    replay = recording._publish_review_attempt(path, attempt=attempt)
    after_replay = read_yaml(path)

    assert first.wrote is True and first.replay is False
    assert first.revision == before["revision"] + 1
    assert replay.wrote is False and replay.replay is True
    assert after_replay == after_first
    from heddle.kernel.state import read_state_file

    assignment = after_first["review_assignments"]
    assert len(assignment["attempts"]) == 1
    assert set(assignment) == {
        "schema",
        "assignments",
        "attempts",
        "dispositions",
        "acceptances",
    }
    assert asdict(read_state_file(path).review_assignments.accounting) == {
        "provider_calls": 1,
        "completed_rounds": 1,
        "filled_slots": 1,
        "distinct_attempts": 1,
    }


def test_ac5_concurrent_exact_attempt_publication_is_one_native_fact(tmp_path) -> None:
    from heddle.runtime.recording import _publish_review_attempt

    host = copy_host(tmp_path, TINY)
    path = host / "plans/sample-feature/state.yaml"
    revision = read_yaml(path)["revision"]
    attempt = _attempt()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(
            future.result(timeout=3)
            for future in (
                workers.submit(_publish_review_attempt, path, attempt=attempt),
                workers.submit(_publish_review_attempt, path, attempt=attempt),
            )
        )
    state = read_yaml(path)
    assert sum(result.wrote for result in results) == 1
    assert sum(result.replay for result in results) == 1
    assert state["revision"] == revision + 1
    assert len(state["review_assignments"]["attempts"]) == 1


def test_ac5_distinct_completed_attempts_keep_distinct_credit(tmp_path) -> None:
    from heddle.runtime.recording import _publish_review_attempt

    host = copy_host(tmp_path, TINY)
    path = host / "plans/sample-feature/state.yaml"
    first = _attempt()
    second = replace(
        first,
        attempt_id="00000000-0000-4000-8000-000000000102",
        round_number=2,
    )
    _publish_review_attempt(path, attempt=first)
    _publish_review_attempt(path, attempt=second)
    from heddle.kernel.state import read_state_file

    assignment = read_yaml(path)["review_assignments"]
    assert [row["attempt_id"] for row in assignment["attempts"]] == [
        first.attempt_id,
        second.attempt_id,
    ]
    accounting = read_state_file(path).review_assignments.accounting
    assert accounting.provider_calls == 2
    assert accounting.distinct_attempts == 2


def test_ac5_existing_v1_ledger_is_rejected_without_mutation(
    tmp_path,
) -> None:
    from heddle.runtime.recording import _publish_review_attempt

    host = copy_host(tmp_path, TINY)
    path = host / "plans/sample-feature/state.yaml"
    document = read_yaml(path)
    document["review_assignments"] = {
        "schema": "heddle.review-assignments/v1",
        "assignments": [],
        "sources": [],
        "dispositions": [],
        "retained": [],
        "interpretations": [],
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False))

    before = path.read_bytes()
    with pytest.raises(Exception, match="ReviewAssignments requires exactly fields"):
        _publish_review_attempt(path, attempt=_attempt())
    assert path.read_bytes() == before


def test_ac5_public_gate_uses_attempt_authority_and_rejects_divergent_view(
    run_cli, tmp_path, monkeypatch
) -> None:
    from tests.tiering_helpers import entry as policy_entry
    from tests.tiering_review_helpers import (
        current_host,
        gate_command,
        provider_transport,
        review_content,
    )

    _host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": policy_entry("milestone-review", limit=1)},
    )
    calls = provider_transport(monkeypatch, review_content("milestone-review"))
    code, result = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 0, result
    assert len(calls) == 1

    state = read_yaml(path)
    attempt = state["review_assignments"]["attempts"][0]
    run = state["gates"][0]["runs"][0]
    source = attempt["source"]
    assert attempt["attempt_id"] == run["run_id"] == source["run_id"]
    assert attempt["outcome"]["kind"] == "canonical-review"
    assert {item["role"] for item in attempt["artifacts"]} >= {
        "canonical",
        "derived",
        "log",
        "temporary",
    }

    run["artifact"] = "reviews/forged.review.json"
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    tampered = path.read_bytes()
    code, output, _error = run_cli(
        ["status", "--feature", "operational-example", "--json"]
    )
    assert code == 3 and "review attempt" in output
    assert path.read_bytes() == tampered and len(calls) == 1


def test_ac6_interpretation_adds_lead_attribution_without_rebinding_original_work() -> (
    None
):
    from heddle.contracts.review_assignments import (
        LeadInterpretation,
        UsableReviewCapture,
    )
    from heddle.kernel.recording import interpret_attempt

    attempt = _attempt(
        UsableReviewCapture(_artifact("reviews/raw.capture.json", "capture"))
    )
    interpretation = LeadInterpretation(
        author="workflow-lead",
        reason="The complete usable response was normalized without a provider rerun.",
        result=_artifact("reviews/interpreted.review.json", "canonical"),
    )
    interpreted, replay = interpret_attempt(attempt, interpretation)
    assert replay is False
    assert interpreted.attempt_id == attempt.attempt_id
    assert interpreted.assignment_id == attempt.assignment_id
    assert interpreted.round_number == attempt.round_number
    assert interpreted.reviewer_slot == attempt.reviewer_slot
    assert interpreted.invocation == attempt.invocation
    assert interpreted.outcome == attempt.outcome
    assert interpreted.interpretation == interpretation
    assert interpret_attempt(interpreted, interpretation) == (interpreted, True)

    changed = deepcopy(interpretation)
    object.__setattr__(changed, "author", "different-lead")
    with pytest.raises(ValueError, match="interpretation identity conflict"):
        interpret_attempt(interpreted, changed)


def test_ac6_assignment_views_never_borrow_a_later_milestone_basis() -> None:
    from heddle.kernel.review_assignments import attempts_for_assignment

    first = _attempt()
    later = replace(
        first,
        attempt_id="00000000-0000-4000-8000-000000000102",
        assignment_id="feature:milestone-review:m2:p1",
    )
    assert attempts_for_assignment((first, later), first.assignment_id) == (first,)
    assert attempts_for_assignment((first, later), later.assignment_id) == (later,)


def test_ac6_original_findings_coverage_and_inspection_duties_survive_projection() -> (
    None
):
    from heddle.kernel.review_closure import derive_assignment_obligations

    first = _attempt()
    later = replace(
        first,
        attempt_id="00000000-0000-4000-8000-000000000102",
        assignment_id="feature:milestone-review:m2:p1",
    )
    canonical = {
        first.attempt_id: {
            "findings": [
                {
                    "id": "ORIGINAL-I1",
                    "severity": "important",
                    "classification": "implement",
                    "requires_inspection": True,
                }
            ],
            "coverage_required": True,
        },
        later.attempt_id: {"findings": [], "coverage_required": False},
    }
    obligations = derive_assignment_obligations(
        (first, later),
        assignment_id=first.assignment_id,
        canonical_reviews=canonical,
        dispositions=(),
        role_enabled=False,
    )
    assert [(item.finding_id, item.requires_inspection) for item in obligations] == [
        ("ORIGINAL-I1", True),
        ("@coverage", False),
    ]
    assert all(item.attempt_id == first.attempt_id for item in obligations)


def test_ac6_latest_disposition_can_clear_a_temporary_inspection_duty(
    run_cli, tmp_path, monkeypatch
) -> None:
    from tests.structured_review_helpers import finding
    from tests.tiering_review_helpers import (
        current_host,
        dispose,
        disposition,
        gate_command,
        provider_transport,
        review_content,
    )

    _host, path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), result
    origin = read_yaml(path)["gates"][0]["runs"][0]["run_id"]
    retained = dispose(
        path,
        [
            disposition(origin, status="retained", requires_inspection=True),
            disposition(origin, "@coverage", status="settled"),
        ],
    )
    assert retained.ok and not retained.data["closure"]["closed"]

    addressed = dispose(
        path,
        [disposition(origin, status="addressed", requires_inspection=False)],
    )
    assert addressed.ok, addressed.to_envelope()
    assert addressed.data["closure"]["closed"]


def test_ac10_artifact_inventory_is_role_typed_and_deduplicated() -> None:
    from heddle.contracts.review_assignments import (
        LeadInterpretation,
        UsableReviewCapture,
    )
    from heddle.kernel.recording import interpret_attempt
    from heddle.kernel.review_assignments import attempt_artifacts
    from heddle.runtime.completion import retained_attempt_artifact_paths

    capture = _artifact("reviews/raw.capture.json", "capture")
    result = _artifact("reviews/interpreted.review.json", "canonical")
    attempt = _attempt(UsableReviewCapture(capture))
    interpreted, _ = interpret_attempt(
        attempt,
        LeadInterpretation(author="lead", reason="usable output", result=result),
    )
    assert attempt_artifacts((interpreted, interpreted)) == (capture, result)
    assert retained_attempt_artifact_paths((interpreted, interpreted)) == (
        capture.path,
        result.path,
    )


def test_legacy_aliases_select_latest_without_weakening_immutable_paths():
    from heddle.contracts.review_assignments import ArtifactRef, CanonicalReview
    from heddle.kernel.review_assignments import attempt_artifacts

    old_log = ArtifactRef("reviews/plan-review.codex.log", "1" * 64, "log")
    new_log = replace(old_log, sha256="2" * 64)
    old_summary = ArtifactRef(
        "reviews/plan-review.codex.gate-summary.json", "3" * 64, "derived"
    )
    new_summary = replace(old_summary, sha256="4" * 64)
    first_result = _artifact("reviews/first.review.json", "canonical")
    second_result = ArtifactRef("reviews/second.review.json", "e" * 64, "canonical")
    first = replace(
        _attempt(CanonicalReview(first_result)),
        attempt_id="00000000-0000-4000-8000-000000000101",
        artifacts=(old_log, old_summary),
    )
    second = replace(
        _attempt(CanonicalReview(second_result)),
        attempt_id="00000000-0000-4000-8000-000000000102",
        round_number=2,
        artifacts=(new_log, new_summary),
    )

    assert attempt_artifacts((first, second)) == (
        new_log,
        new_summary,
        first_result,
        second_result,
    )


def test_immutable_cutover_supersedes_legacy_rolling_aliases() -> None:
    from heddle.contracts.review_assignments import ArtifactRef, CanonicalReview
    from heddle.kernel.review_assignments import attempt_artifacts

    old_log = ArtifactRef("reviews/plan-review.codex.log", "1" * 64, "log")
    old_summary = ArtifactRef(
        "reviews/plan-review.codex.gate-summary.json", "2" * 64, "derived"
    )
    first_result = _artifact("reviews/first.review.json", "canonical")
    second_result = ArtifactRef("reviews/second.review.json", "e" * 64, "canonical")
    third_result = ArtifactRef("reviews/third.review.json", "f" * 64, "canonical")
    immutable_log = ArtifactRef(
        f"reviews/plan-review.codex.{'3' * 64}.log", "3" * 64, "log"
    )
    immutable_summary = ArtifactRef(
        f"reviews/plan-review.codex.{'4' * 64}.review.gate-summary.json",
        "5" * 64,
        "derived",
    )
    first = replace(
        _attempt(CanonicalReview(first_result)),
        attempt_id="00000000-0000-4000-8000-000000000121",
        artifacts=(old_log, old_summary),
    )
    second = replace(
        _attempt(CanonicalReview(second_result)),
        attempt_id="00000000-0000-4000-8000-000000000122",
        round_number=2,
        artifacts=(
            replace(old_log, sha256="6" * 64),
            replace(old_summary, sha256="7" * 64),
        ),
    )
    third = replace(
        _attempt(CanonicalReview(third_result)),
        attempt_id="00000000-0000-4000-8000-000000000123",
        round_number=3,
        artifacts=(immutable_log, immutable_summary),
    )

    assert attempt_artifacts((first, second, third)) == (
        first_result,
        second_result,
        immutable_log,
        immutable_summary,
        third_result,
    )


@pytest.mark.parametrize("role", ["canonical", "capture", "temporary"])
def test_non_alias_artifact_identity_conflicts_remain_fatal(role: str) -> None:
    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.kernel.review_assignments import attempt_artifacts

    path = f"reviews/conflicting.{role}.json"
    first = replace(
        _attempt(),
        attempt_id="00000000-0000-4000-8000-000000000111",
        artifacts=(ArtifactRef(path, "1" * 64, role),),
    )
    second = replace(
        _attempt(),
        attempt_id="00000000-0000-4000-8000-000000000112",
        artifacts=(ArtifactRef(path, "2" * 64, role),),
    )

    with pytest.raises(ValueError, match=f"artifact identity conflict: {path}"):
        attempt_artifacts((first, second))


def test_ac10_cleanup_preserves_unindexed_suffix_match_and_selects_indexed_view(
    tmp_path,
) -> None:
    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.kernel.state import read_state_file
    from heddle.runtime.completion import _cleanup_candidates, _workspace_entries
    from heddle.runtime.recording import _publish_review_attempt

    host = copy_host(tmp_path, TINY)
    workspace = host / "plans/sample-feature"
    path = workspace / "state.yaml"
    reviews = workspace / "reviews"
    reviews.mkdir()
    indexed = reviews / "indexed.md"
    indexed.write_bytes(b"derived view\n")
    unknown = reviews / "plan-review.codex.log"
    unknown.write_bytes(b"unindexed operator artifact\n")
    reference = ArtifactRef(
        "reviews/indexed.md",
        hashlib.sha256(indexed.read_bytes()).hexdigest(),
        "derived",
        mode=indexed.stat().st_mode & 0o7777,
    )
    _publish_review_attempt(
        path,
        attempt=replace(_attempt(), artifacts=(reference,)),
    )

    state = read_state_file(path)
    candidates, preserved = _cleanup_candidates(state, _workspace_entries(workspace))
    assert reference.path in candidates
    assert "reviews/plan-review.codex.log" in preserved


def test_ac10_failed_view_publication_never_indexes_the_untouched_sidecar(
    run_cli, tmp_path, monkeypatch
) -> None:
    import os

    from heddle.kernel.state import read_state_file
    from heddle.runtime.completion import _cleanup_candidates, _workspace_entries
    from tests.tiering_helpers import entry as policy_entry
    from tests.tiering_review_helpers import (
        current_host,
        gate_command,
        provider_transport,
        review_content,
    )

    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": policy_entry("milestone-review", limit=1)},
    )
    workspace = path.parent
    operator_bytes = b"operator-owned sidecar\n"
    created: list[Path] = []
    real_replace = os.replace

    def fail_view_replace(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        if dst_dir_fd is not None and str(destination).endswith(".md"):
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dst_dir_fd,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(operator_bytes)
            created.append((workspace / "reviews" / str(destination)).resolve())
            raise OSError("injected generated-view publication failure")
        return real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr("heddle.gate.io.os.replace", fail_view_replace)
    calls = provider_transport(monkeypatch, review_content("milestone-review"))
    code, result = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 0 and result["data"]["accepted"], result
    assert len(calls) == 1 and len(created) == 1
    unknown = created[0]
    assert unknown.read_bytes() == operator_bytes

    state = read_state_file(path)
    inventory = state.review_assignments.attempts[0].artifacts
    relative = unknown.relative_to(workspace).as_posix()
    assert relative not in {item.path for item in inventory}
    candidates, preserved = _cleanup_candidates(state, _workspace_entries(workspace))
    assert relative not in candidates
    assert relative in preserved
