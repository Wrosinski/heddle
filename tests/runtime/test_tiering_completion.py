"""AC-10 real final-proof/acceptance boundaries with every reviewer optional."""

import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.operational_model_helpers import FEATURE, read, write
from tests.readiness_helpers import current_readiness, verify
from tests.tiering_completion_helpers import final_host
from tests.tiering_helpers import ROLES, entry, invoke, snapshot, wire_policy


def _temporary_attempt(relative: str, data: bytes, mode: int):
    from heddle.contracts.review_assignments import (
        ArtifactRef,
        AttemptInvocation,
        EngineFailure,
        ReviewAttempt,
    )

    return ReviewAttempt(
        attempt_id="00000000-0000-4000-8000-000000000777",
        assignment_id="local-history:milestone-review:m1:p1",
        round_number=1,
        reviewer_slot="primary",
        invocation=AttemptInvocation(
            cli="codex",
            model="fixture",
            reasoning_effort="high",
            sandbox="read-only",
            input_hash="1" * 64,
            review_basis_hash="2" * 64,
            prompt_version="fixture",
            effective_prompt_sha256="3" * 64,
        ),
        outcome=EngineFailure("fixture", "retained cleanup input"),
        created_at="2026-09-16T00:00Z",
        artifacts=(
            ArtifactRef(
                relative,
                hashlib.sha256(data).hexdigest(),
                "temporary",
                mode=mode,
            ),
        ),
    )


def _local_cleanup_fixture(tmp_path, monkeypatch):
    from heddle.contracts.review_assignments import review_attempt_document

    host = final_host(tmp_path, monkeypatch, verify_now=False)
    relative = "reviews/disposable.tmp"
    content = b"indexed disposable output\n"
    candidate = host.state.parent / relative
    candidate.parent.mkdir(exist_ok=True)
    candidate.write_bytes(content)
    candidate.chmod(0o640)
    attempt = _temporary_attempt(
        relative, content, stat.S_IMODE(candidate.stat().st_mode)
    )
    value = read(host.state)
    value["review_assignments"]["attempts"] = [review_attempt_document(attempt)]
    write(host.state, value)
    verify(host.state, "m1", "m2", "acceptance", "smoke")
    return host, candidate, relative, content


def test_local_history_completion_cleans_indexed_disposable_without_record_commit(
    tmp_path, monkeypatch
) -> None:
    """AC-5 red: public completion deletes after verified local retention."""
    host, candidate, relative, content = _local_cleanup_fixture(tmp_path, monkeypatch)
    authored = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in (host.state.parent / "plan.md", host.state.parent / "brief.md")
    }

    result = host.complete()

    assert result.ok, result.to_envelope()
    assert result.data["effects"]["cleanup"]["status"] == "complete"
    assert result.data["effects"]["cleanup"]["paths"] == [relative]
    assert not candidate.exists()
    accepted = host.state.read_bytes()
    with tarfile.open(host.archive, "r:gz") as archive:
        assert archive.extractfile("state.yaml").read() == accepted
        assert archive.extractfile(relative).read() == content
    for name, (data, mode) in authored.items():
        path = host.state.parent / name
        assert path.read_bytes() == data
        assert stat.S_IMODE(path.stat().st_mode) == mode


@pytest.mark.parametrize("authored_name", ["plan.md", "brief.md"])
def test_local_history_completion_requires_authored_archive_inputs(
    tmp_path, monkeypatch, authored_name
) -> None:
    """AC-5: destructive completion requires every authored workspace record."""
    host, candidate, _relative, _content = _local_cleanup_fixture(tmp_path, monkeypatch)
    (host.state.parent / authored_name).unlink()
    before = snapshot(host.root)

    refused = host.complete()

    assert not refused.ok
    assert authored_name in (refused.error.message + refused.error.hint)
    assert snapshot(host.root) == before
    assert candidate.exists()
    assert read(host.state)["completion"] is None
    assert not host.archive.exists()


def test_local_history_accepted_retry_reuses_verified_archive_after_partial_cleanup(
    tmp_path, monkeypatch
) -> None:
    """AC-6 red: public accepted retry treats archived absence as cleaned."""
    host, candidate, relative, _content = _local_cleanup_fixture(tmp_path, monkeypatch)
    first = host.complete()
    assert first.ok, first.to_envelope()
    assert first.data["effects"]["cleanup"]["status"] == "complete"
    assert first.data["effects"]["cleanup"]["paths"] == [relative]
    assert not candidate.exists()
    accepted = host.state.read_bytes()
    close_calls = tuple(host.suite_calls())

    retry = host.complete()

    assert retry.ok, retry.to_envelope()
    assert retry.data["effects"]["cleanup"] == {
        "status": "complete",
        "paths": [],
        "preserved": [],
    }
    assert host.state.read_bytes() == accepted
    assert tuple(host.suite_calls()) == close_calls


def _remove_archive_member(path: Path, removed: str) -> None:
    manifest_name = ".heddle-completion-archive.json"
    members = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if member.name in {removed, manifest_name}:
                continue
            stream = archive.extractfile(member) if member.isfile() else None
            members.append(
                (
                    member.name,
                    member.mode,
                    member.type,
                    member.linkname,
                    stream.read() if stream is not None else None,
                )
            )
        stream = archive.extractfile(manifest_name)
        assert stream is not None
        manifest = json.load(stream)
    manifest["entries"].pop(removed)
    replacement = path.with_name("replacement.tar.gz")
    with tarfile.open(replacement, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, mode, kind, linkname, data in members:
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.type = kind
            member.linkname = linkname
            if data is not None:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)
        data = json.dumps(manifest, sort_keys=True).encode()
        member = tarfile.TarInfo(manifest_name)
        member.mode, member.size = 0o644, len(data)
        archive.addfile(member, io.BytesIO(data))
    os.replace(replacement, path)


def test_local_history_accepted_retry_requires_archived_disposable_identity(
    tmp_path, monkeypatch
) -> None:
    """AC-6: an absent disposable is clean only when its archive identity exists."""
    host, candidate, relative, _content = _local_cleanup_fixture(tmp_path, monkeypatch)
    first = host.complete()
    assert first.ok and not candidate.exists()
    accepted = host.state.read_bytes()
    close_calls = tuple(host.suite_calls())
    _remove_archive_member(host.archive, relative)

    retry = host.complete()

    archive_effect = retry.data["effects"]["archive"]
    assert retry.ok and retry.data["accepted"] is True
    assert archive_effect["status"] == "conflict"
    assert relative in archive_effect["error"]
    assert str(host.archive) in archive_effect["error"]
    assert host.state.read_bytes() == accepted
    assert tuple(host.suite_calls()) == close_calls


@pytest.mark.parametrize("damage", ["candidate-bytes", "candidate-symlink", "archive"])
def test_local_history_public_completion_preserves_damaged_cleanup_inputs(
    tmp_path, monkeypatch, damage
) -> None:
    """AC-6 red: public cleanup preserves every input that loses qualification."""
    from heddle.runtime import completion

    host, candidate, relative, _content = _local_cleanup_fixture(tmp_path, monkeypatch)
    outside = host.state.parent / "reviews/operator-owned.txt"
    outside.write_text("operator owned\n")
    original = completion._publish_archive

    def damage_after_publish(*args, **kwargs):
        entries = original(*args, **kwargs)
        if damage == "candidate-bytes":
            candidate.write_bytes(b"changed after archive\n")
        elif damage == "candidate-symlink":
            candidate.unlink()
            candidate.symlink_to(outside)
        else:
            host.archive.write_bytes(host.archive.read_bytes() + b"corrupt")
        return entries

    monkeypatch.setattr(completion, "_publish_archive", damage_after_publish)

    result = host.complete()

    cleanup = result.data["effects"]["cleanup"]
    assert cleanup["status"] == "conflict"
    assert str(host.archive) in cleanup["error"]
    if damage != "archive":
        assert relative in cleanup["error"]
    instructions = [
        action.action.instruction
        for action in result.next_actions
        if isinstance(action.action, ops.ManualAction)
    ]
    assert any(
        str(host.archive) in instruction
        and "indexed and archived bytes/type/mode" in instruction
        for instruction in instructions
    )
    assert candidate.exists() or candidate.is_symlink()


def test_local_history_reserved_authored_paths_cannot_be_disposable() -> None:
    """AC-5 red: authored workspace records cannot enter cleanup authority."""
    from heddle.contracts.review_assignments import review_attempt_document

    attempt = _temporary_attempt("plan.md", b"# Authored plan\n", 0o644)
    with pytest.raises(ValueError, match="reserved authored"):
        review_attempt_document(attempt)


def test_unknown_file_is_preserved_and_reported_by_cleanup_retry(
    tmp_path, monkeypatch
) -> None:
    """Survivor pin for AC-6 paired with the public local-retention red case."""
    host = final_host(tmp_path, monkeypatch)
    accepted = host.complete()
    assert accepted.ok and accepted.data["accepted"]
    host.commit_retention()

    unknown = host.state.parent / "operator-notes.bin"
    unknown_bytes = b"operator-owned, unindexed evidence\x00\xff\n"
    unknown.write_bytes(unknown_bytes)
    unknown.chmod(0o640)
    unknown_mode = unknown.stat().st_mode

    retry = host.complete()

    relative = unknown.relative_to(host.state.parent).as_posix()
    assert retry.ok and retry.data["effects"]["cleanup"]["status"] == "complete"
    assert relative in retry.data["effects"]["cleanup"]["preserved"]
    assert unknown.is_file()
    assert unknown.read_bytes() == unknown_bytes
    assert unknown.stat().st_mode == unknown_mode


def test_distinct_milestone_evidence_allows_native_implementation_exit(
    tmp_path, monkeypatch, run_cli
):
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.kernel.reviews import gate_converged
    from heddle.runtime.readiness import assess_current_readiness
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        gate_command,
        provider_transport,
        review_content,
        runs,
    )

    host = final_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review")},
    )
    calls = provider_transport(monkeypatch, review_content("milestone-review"))
    for milestone_index in range(2):
        code, result = gate_command(run_cli, "run-gate", "milestone-review")
        assert code in (0, 4), result
        assert dispose(
            host.state,
            [
                disposition(
                    runs(host.state)[-1]["run_id"], "@coverage", status="settled"
                )
            ],
        ).ok
        sealed = execute(ops.MilestoneAdvance(feature=FEATURE))
        assert sealed.ok, sealed.to_envelope()
        if milestone_index == 0:
            promoted = execute(ops.MilestoneAdvance(feature=FEATURE))
            assert promoted.ok, promoted.to_envelope()
    config = load_project_config(host.root)
    snap = resolve_snapshot(config, FEATURE)
    bases = {d.evidence_basis for d in snap.state.review_assignments.dispositions}
    assert len(bases) == 2
    assert all(
        gate_converged(snap.state, "milestone-review", m.id)
        for m in snap.state.milestones
    )
    assert not assess_current_readiness(config, snap).blockers
    result = execute(ops.PhaseExit(feature=FEATURE))
    assert result.ok, result.to_envelope()
    assert yaml.safe_load(host.state.read_text())["stage"] == "peer-review"
    assert len(calls) == 2


@pytest.mark.parametrize("base", ["light", "full"])
def test_ac10_all_peer_gates_off_still_requires_current_acceptance_and_smoke(
    tmp_path, monkeypatch, base
):
    host = final_host(tmp_path, monkeypatch, base=base, verify_now=False)
    before = snapshot(host.root)
    missing = host.complete(dry_run=True)
    assert not missing.ok
    assert snapshot(host.root) == before
    verify(host.state, "m1", "m2", "acceptance", "smoke")
    accepted = host.complete()
    assert accepted.ok, accepted.to_envelope()
    assert accepted.data["accepted"]
    value = yaml.safe_load(host.state.read_text())
    assert value["completion"] is not None
    assert value["gates"] == []  # No substitute reviewer or invented pass.
    assert host.archive.is_file()


def test_ac10_changed_content_after_stage_entry_is_requalified_at_completion(
    tmp_path, monkeypatch
):
    host = final_host(tmp_path, monkeypatch)
    (host.root / "src/example.py").write_text("VALUE = 8\n")
    before = snapshot(host.root)
    refused = host.complete()
    assert not refused.ok
    assert snapshot(host.root) == before
    assert yaml.safe_load(host.state.read_text())["completion"] is None
    assert not host.archive.exists()


@pytest.mark.parametrize(
    "overlay,boundary", [(False, "peer-review"), (True, "robustness")]
)
def test_ac10_selected_overlay_moves_the_final_proof_boundary(
    tmp_path, monkeypatch, overlay, boundary
):
    from heddle.kernel.readiness import Boundary

    host = final_host(
        tmp_path, monkeypatch, stage="peer-review", overlay=overlay, verify_now=False
    )
    status = execute(ops.Status(feature=FEATURE))
    assert status.ok, status.to_envelope()
    assert status.data["effective_policy"]["final_verification_stage"] == boundary
    readiness = current_readiness(host.root, boundary=Boundary("peer-review", None))
    scopes = {verification.scope for verification in readiness.verifications}
    if overlay:
        assert "acceptance" not in scopes
    else:
        assert {"acceptance", "smoke"} <= scopes
        assert "verification-missing" in readiness.blockers


def test_ac10_declared_live_proof_and_user_owned_close_remain_independent(
    tmp_path, monkeypatch
):
    host = final_host(tmp_path, monkeypatch)
    value = yaml.safe_load(host.state.read_text())
    value["commands"]["live_e2e_test"] = "python3 tests/check.py"
    # This is a declared runtime proof scope, not authorization to call a provider.
    host.state.write_text(yaml.safe_dump(value, sort_keys=False))
    before = snapshot(host.root)
    refused = host.complete()
    assert (
        not refused.ok
        and "live" in (refused.error.message + refused.error.hint).lower()
    )
    assert snapshot(host.root) == before
    verify(host.state, "live")  # Exact inspected local assertion only.
    value = yaml.safe_load(host.state.read_text())
    value["authorizations"] = []
    value["authorized_through"] = "robustness"
    host.state.write_text(yaml.safe_dump(value, sort_keys=False))
    before = snapshot(host.root)
    refused = host.complete()
    assert not refused.ok
    assert snapshot(host.root) == before


def test_ac10_accepted_retry_repairs_effects_without_reopening_policy_or_proof(
    tmp_path, monkeypatch
):
    """Survivor pin for AC-6/AC-8: accepted retry does not reopen proof."""
    host = final_host(tmp_path, monkeypatch)
    original_replace = os.replace
    failed_effects = []

    def fail_stamp(source, target, *args, **kwargs):
        if Path(target) == host.spec:
            assert yaml.safe_load(host.state.read_text())["completion"] is not None
            failed_effects.append(str(target))
            raise OSError("injected accepted stamp publication failure")
        return original_replace(source, target, *args, **kwargs)

    original_spec = host.spec.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_stamp)
        accepted = host.complete()
    assert accepted.ok and accepted.data["accepted"], accepted.to_envelope()
    assert failed_effects == [str(host.spec)]
    assert accepted.data["effects"]["stamp"]["status"] == "conflict"
    assert host.spec.read_bytes() == original_spec
    value = yaml.safe_load(host.state.read_text())
    acceptance = value["completion"]
    proofs = value["verifications"]
    (host.root / "src/example.py").write_text("VALUE = 999\n")
    retry = host.complete()
    assert retry.ok, retry.to_envelope()
    after = yaml.safe_load(host.state.read_text())
    assert after["completion"] == acceptance
    assert after["verifications"] == proofs
    assert after["feature_policy"] == value["feature_policy"]
    assert retry.data["effects"]["stamp"]["status"] == "complete"
    assert host.spec.read_bytes() != original_spec
    assert host.archive.is_file()
    for operation in (
        ops.Status(feature=FEATURE),
        ops.Orient(feature=FEATURE),
        ops.Kickoff(feature=FEATURE),
        ops.Drive(feature=FEATURE),
    ):
        observed = execute(operation)
        assert observed.ok, observed.to_envelope()
        assert observed.data["effective_policy"] == retry.data["effective_policy"]
    assert yaml.safe_load(host.state.read_text())["completion"] == acceptance


def test_ac10_policy_amendment_cannot_launder_open_findings_at_completion(
    tmp_path, monkeypatch, run_cli
):
    from tests.structured_review_helpers import finding
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        gate_command,
        provider_transport,
        review_content,
        runs,
    )

    host = final_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={"behavior-review": entry("behavior-review")},
    )
    calls = provider_transport(
        monkeypatch,
        review_content(
            "behavior-review", findings=[finding("BE-I1", classification="implement")]
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "behavior-review")
    assert code in (0, 4), result
    origin = runs(host.state)[0]["run_id"]
    all_off = {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }
    value = yaml.safe_load(host.state.read_text())
    amended = invoke(
        "FeaturePolicy",
        slug=FEATURE,
        payload=wire_policy(revision=2, overrides=all_off),
        expect_revision=value["revision"],
    )
    assert amended.ok, amended.to_envelope()
    # Authored terminal boundary models migrated complete-but-unaccepted work;
    # completion must requalify instead of trusting an earlier stage transition.
    value = yaml.safe_load(host.state.read_text())
    value["stage"] = "complete"
    host.state.write_text(yaml.safe_dump(value, sort_keys=False))
    before = snapshot(host.root)
    from heddle.driver.loop import _next_command
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    config = load_project_config(host.root)
    boundary = resolve_snapshot(config, FEATURE)
    routed = _next_command(config, boundary, None)
    # Drive must surface the same repair, without executing a completion session/call.
    assert hasattr(routed, "ok") and not routed.ok
    assert routed.error.code == "gate-not-converged"
    assert any(
        isinstance(row.action, ops.AuthoringAction)
        and row.action.work == "review-disposition"
        for row in routed.next_actions
    )
    refused = host.complete()
    assert not refused.ok
    assert "review" in (refused.error.message + refused.error.hint).lower()
    assert snapshot(host.root) == before
    closed = dispose(
        host.state,
        [
            disposition(origin, "BE-I1"),
            disposition(origin, "@coverage", status="settled"),
        ],
    )
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    accepted = host.complete()
    assert accepted.ok and accepted.data["accepted"], accepted.to_envelope()
    assert len(calls) == 1


def test_ac10_overlay_fix_requires_fresh_proof_at_robustness_before_close(
    tmp_path, monkeypatch, run_cli
):
    from tests.structured_review_helpers import finding
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        gate_command,
        provider_transport,
        review_content,
        runs,
    )

    host = final_host(tmp_path, monkeypatch, stage="robustness", overlay=True)
    calls = provider_transport(
        monkeypatch,
        review_content(
            "robustness-analysis",
            findings=[finding("finding-1", classification="implement")],
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "robustness-analysis")
    assert code in (0, 4), result
    origin = runs(host.state)[0]["run_id"]
    source = host.root / "src/example.py"
    source.write_text("# Correction after the integration-failure review.\nVALUE = 7\n")
    closed = dispose(
        host.state,
        [
            disposition(origin, "finding-1"),
            disposition(origin, "@coverage", status="settled"),
        ],
    )
    assert closed.ok, closed.to_envelope()
    before = host.state.read_bytes()
    refused = execute(ops.PhaseExit(feature=FEATURE))
    assert not refused.ok
    assert "verif" in (refused.error.message + refused.error.hint).lower()
    assert host.state.read_bytes() == before
    verify(host.state, "m1", "m2", "acceptance", "smoke")
    advanced = execute(ops.PhaseExit(feature=FEATURE))
    assert advanced.ok, advanced.to_envelope()
    assert yaml.safe_load(host.state.read_text())["stage"] == "complete"
    accepted = host.complete()
    assert accepted.ok and accepted.data["accepted"], accepted.to_envelope()
    assert len(calls) == 1


def test_ac10_acceptance_binds_every_qualified_dual_assignment_source(
    tmp_path, monkeypatch, run_cli
):
    from tests.tiering_helpers import FABLE
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        gate_command,
        provider_transport,
        review_content,
        runs,
    )

    host = final_host(
        tmp_path,
        monkeypatch,
        overrides={"behavior-review": entry("behavior-review", secondary=FABLE)},
    )
    calls = provider_transport(monkeypatch, review_content("behavior-review"))
    for cli in ("codex", "claude"):
        code, result = gate_command(
            run_cli, "run-gate", "behavior-review", "--cli", cli
        )
        assert code in (0, 4), result
    sources = [row["run_id"] for row in runs(host.state)]
    closed = dispose(
        host.state,
        [disposition(run, "@coverage", status="settled") for run in sources],
    )
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    accepted = host.complete()
    assert accepted.ok and accepted.data["accepted"], accepted.to_envelope()
    fact = yaml.safe_load(host.state.read_text())["completion"]
    assert fact["review_run_ids"] == sources
    assert len(calls) == 2


def test_ac10_completed_milestone_open_duty_remains_at_next_milestone_boundary(
    tmp_path, monkeypatch, run_cli
):
    from heddle.kernel.reviews import gate_converged
    from heddle.kernel.state import parse_state_document
    from tests.structured_review_helpers import finding
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        gate_command,
        provider_transport,
        review_content,
        runs,
    )

    host = final_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review")},
    )
    calls = provider_transport(
        monkeypatch,
        review_content(
            "milestone-review", findings=[finding("MI-I1", classification="implement")]
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "milestone-review")
    assert code in (0, 4), result
    origin = runs(host.state)[0]["run_id"]
    before = snapshot(host.root)
    refused = execute(ops.MilestoneAdvance(feature=FEATURE))
    assert not refused.ok
    readiness = current_readiness(host.root)
    assert any(row.scope == "m1" and row.run_id == origin for row in readiness.reviews)
    assert "gate-not-converged" in readiness.blockers
    assert any("m1" in row.reason for row in readiness.next_actions)
    assert snapshot(host.root) == before and len(calls) == 1
    closed = dispose(
        host.state,
        [
            disposition(origin, "MI-I1"),
            disposition(origin, "@coverage", status="settled"),
        ],
    )
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    first_basis = yaml.safe_load(host.state.read_text())["review_assignments"][
        "dispositions"
    ][-1]["evidence_basis"]
    sealed = execute(ops.MilestoneAdvance(feature=FEATURE))
    assert sealed.ok, sealed.to_envelope()
    promoted = execute(ops.MilestoneAdvance(feature=FEATURE))
    assert promoted.ok, promoted.to_envelope()
    code, result = gate_command(run_cli, "run-gate", "milestone-review")
    assert code in (0, 4), result
    second = runs(host.state)[-1]["run_id"]
    closed = dispose(
        host.state,
        [
            disposition(second, "MI-I1"),
            disposition(second, "@coverage", status="settled"),
        ],
    )
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    value = yaml.safe_load(host.state.read_text())
    second_basis = value["review_assignments"]["dispositions"][-1]["evidence_basis"]
    sealed = execute(ops.MilestoneAdvance(feature=FEATURE))
    assert sealed.ok, sealed.to_envelope()
    bases = (first_basis, second_basis)
    assert len(set(bases)) == 2
    state = parse_state_document(
        yaml.safe_load(host.state.read_text()), source=host.state
    )
    assert gate_converged(state, "milestone-review", "m1")
    assert gate_converged(state, "milestone-review", "m2")
    assert [receipt.scope for receipt in state.review_assignments.acceptances] == [
        "m1",
        "m2",
    ]
    assert len(calls) == 2
