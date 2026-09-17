"""Immutable source evidence and current-schema cutover behavior."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from heddle.kernel.project_config import KernelError

REPO = Path(__file__).resolve().parents[2]
TINY_STATE = REPO / "tests/fixtures/workspaces/tiny/plans/sample-feature/state.yaml"


def _v8_host(tmp_path, *, milestones=True, name="host"):
    from tests.runtime.verification_provenance_helpers import (
        convert_current_fixture_to_v8,
        make_current_host,
    )

    host, state_path = make_current_host(tmp_path, milestones=milestones, name=name)
    convert_current_fixture_to_v8(host, state_path)
    return host, state_path


def _evidence():
    from heddle.kernel.source_manifest import (
        ObservedPath,
        SourceDefinition,
        build_source_evidence,
    )

    definition = SourceDefinition(
        "milestone-owns", ("src/b.py", "src/a.py"), ("src/b.py", "src/a.py")
    )
    observations = (
        ObservedPath("src/b.py", "file", False, b"b\n"),
        ObservedPath("src/a.py", "file", True, b"a\n"),
    )
    return build_source_evidence(definition, observations)


def test_ac7_manifest_codec_is_canonical_and_preserves_source_identity() -> None:
    from heddle.gate.preparation import _captured_identity
    from heddle.kernel.source_manifest import (
        SOURCE_EVIDENCE_SCHEMA,
        ObservedPath,
        SourceDefinition,
        build_source_evidence,
        decode_source_evidence,
        encode_source_evidence,
    )

    evidence = _evidence()
    encoded = encode_source_evidence(evidence)
    decoded = decode_source_evidence(encoded)
    assert decoded == evidence
    assert decoded.schema == SOURCE_EVIDENCE_SCHEMA == "heddle.source-evidence/v1"
    assert decoded.definition.paths == ("src/a.py", "src/b.py")
    assert [item.path for item in decoded.observations] == ["src/a.py", "src/b.py"]
    assert len(decoded.source_sha256) == 64
    assert encode_source_evidence(decoded) == encoded

    captured = ObservedPath("docs/feature.md", "file", False, b"feature\n")
    definition = SourceDefinition("feature-inputs", (captured.path,), (captured.path,))
    assert (
        _captured_identity(captured, "feature").content
        == build_source_evidence(definition, (captured,)).source_sha256
    )

    mapping = yaml.safe_load(encoded)
    mapping["observations"].append(deepcopy(mapping["observations"][0]))
    with pytest.raises((KernelError, ValueError), match="duplicate"):
        decode_source_evidence(yaml.safe_dump(mapping).encode())


def test_ac7_publication_is_create_only_exactly_reusable_and_tamper_evident(
    tmp_path,
) -> None:
    from heddle.runtime.verification import (
        publish_source_evidence,
        read_source_evidence,
    )

    workspace = tmp_path / "plans/feature"
    workspace.mkdir(parents=True)
    evidence = _evidence()
    first, reused = publish_source_evidence(workspace, evidence)
    assert reused is False
    second, reused = publish_source_evidence(workspace, evidence)
    assert reused is True and second == first
    assert read_source_evidence(workspace, first) == evidence

    artifact = workspace / first.artifact
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(KernelError, match="identity|bytes|tamper"):
        read_source_evidence(workspace, first)
    with pytest.raises(KernelError, match="conflict|bytes|tamper"):
        publish_source_evidence(workspace, evidence)

    artifact.unlink()
    artifact.symlink_to(workspace / "outside")
    with pytest.raises(KernelError, match="symlink"):
        publish_source_evidence(workspace, evidence)


def test_ac7_publication_rejects_traversal_type_conflict_and_pre_reference_failure(
    tmp_path, monkeypatch
) -> None:
    from heddle.kernel.source_manifest import (
        EvidenceReference,
        SourceDefinition,
        build_source_evidence,
        evidence_reference,
    )
    from heddle.runtime import verification

    with pytest.raises(KernelError, match="relative|traversal|normalized"):
        build_source_evidence(
            SourceDefinition("milestone-owns", ("../outside",), ("../outside",)),
            (),
        )

    workspace = tmp_path / "plans/feature"
    workspace.mkdir(parents=True)
    reference = evidence_reference(_evidence())
    artifact = workspace / reference.artifact
    artifact.mkdir(parents=True)
    with pytest.raises(KernelError, match="type|regular|conflict"):
        verification.publish_source_evidence(workspace, _evidence())
    assert artifact.is_dir()

    artifact.rmdir()

    def fail_durable_publish(*_args, **_kwargs):
        raise OSError("injected manifest publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(verification, "replace_file_bytes", fail_durable_publish)
        with pytest.raises(KernelError, match="publication failure"):
            verification.publish_source_evidence(workspace, _evidence())
    assert not artifact.exists()
    assert isinstance(reference, EvidenceReference)


def test_ac7_simultaneous_same_manifest_publication_converges_without_clobber(
    tmp_path, monkeypatch
) -> None:
    from heddle.runtime import verification

    workspace = tmp_path / "plans/feature"
    workspace.mkdir(parents=True)
    evidence = _evidence()
    publication_barrier = threading.Barrier(2)
    original_publish = verification._publish_evidence_bytes

    def simultaneous_publish(*args, **kwargs):
        publication_barrier.wait(timeout=2)
        return original_publish(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(verification, "_publish_evidence_bytes", simultaneous_publish)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = tuple(
                future.result(timeout=3)
                for future in (
                    workers.submit(
                        verification.publish_source_evidence, workspace, evidence
                    ),
                    workers.submit(
                        verification.publish_source_evidence, workspace, evidence
                    ),
                )
            )
    assert results[0][0] == results[1][0]
    assert sorted(reused for _reference, reused in results) == [False, True]
    assert verification.read_source_evidence(workspace, results[0][0]) == evidence


def test_ac8_verification_fact_references_distinct_before_and_after_evidence() -> None:
    from heddle.kernel.source_manifest import EvidenceReference
    from heddle.kernel.state import VerificationEvidence, VerificationFact

    assert {field.name for field in fields(VerificationFact)} >= {
        "evidence",
        "exit_code",
    }
    assert "provenance" not in {field.name for field in fields(VerificationFact)}
    before = EvidenceReference(
        schema="heddle.source-evidence-ref/v1",
        artifact="verification/source-evidence." + "a" * 64 + ".yaml",
        artifact_sha256="a" * 64,
        source_sha256="b" * 64,
    )
    after = EvidenceReference(
        schema="heddle.source-evidence-ref/v1",
        artifact="verification/source-evidence." + "c" * 64 + ".yaml",
        artifact_sha256="c" * 64,
        source_sha256="d" * 64,
    )
    fact = VerificationFact(
        scope="m1",
        command="pytest tests/example.py",
        exit_code=1,
        at="2026-09-12T00:00Z",
        log="verification/m1.log",
        evidence=VerificationEvidence(
            schema="heddle.verification-evidence/v1", before=before, after=after
        ),
    )
    assert fact.exit_code == 1 and fact.evidence.before != fact.evidence.after


def test_ac10_manifest_references_are_retained_by_completion() -> None:
    from heddle.kernel.source_manifest import EvidenceReference
    from heddle.kernel.state import VerificationEvidence, VerificationFact
    from heddle.runtime.completion import retained_evidence_paths

    ref = EvidenceReference(
        schema="heddle.source-evidence-ref/v1",
        artifact="verification/source-evidence." + "a" * 64 + ".yaml",
        artifact_sha256="a" * 64,
        source_sha256="b" * 64,
    )
    fact = VerificationFact(
        scope="m1",
        command="pytest tests/example.py",
        exit_code=0,
        at="2026-09-12T00:00Z",
        log="verification/m1.log",
        evidence=VerificationEvidence(
            schema="heddle.verification-evidence/v1", before=ref, after=ref
        ),
    )
    assert retained_evidence_paths((fact,)) == (
        "verification/m1.log",
        ref.artifact,
    )


def test_ac10_archive_readback_contains_every_validated_retained_path() -> None:
    from heddle.runtime.completion import ArchiveEntry, _validate_retained_archive

    retained = {
        "verification/m1.log": ArchiveEntry("file", "a" * 64, 0o644),
        "verification/source-evidence.yaml": ArchiveEntry("file", "b" * 64, 0o600),
    }
    _validate_retained_archive(retained, dict(retained))
    with pytest.raises(OSError, match="does not retain"):
        _validate_retained_archive(
            retained, {"verification/m1.log": retained["verification/m1.log"]}
        )


@pytest.mark.parametrize("damage", [None, "bytes", "type", "mode"])
def test_ac10_cleanup_requires_indexed_role_exact_identity_and_all_barriers(
    damage, tmp_path
) -> None:
    import hashlib

    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.runtime.completion import cleanup_artifacts

    workspace = tmp_path / "plans/feature"
    workspace.mkdir(parents=True)
    candidate = workspace / "reviews/transient.tmp"
    candidate.parent.mkdir()
    original = b"temporary output\n"
    candidate.write_bytes(original)
    candidate.chmod(0o600)
    unknown = workspace / "reviews/unknown.tmp"
    unknown.write_bytes(b"unindexed\n")
    reference = ArtifactRef(
        path="reviews/transient.tmp",
        sha256=hashlib.sha256(original).hexdigest(),
        role="temporary",
        kind="file",
        mode=0o600,
    )

    blocked = cleanup_artifacts(
        workspace,
        (reference,),
        accepted=False,
        archive_validated=True,
        retention_qualified=True,
    )
    assert candidate.exists() and blocked.removed == ()

    if damage == "bytes":
        candidate.write_bytes(original + b"changed\n")
    elif damage == "type":
        candidate.unlink()
        candidate.mkdir()
    elif damage == "mode":
        candidate.chmod(0o644)
    report = cleanup_artifacts(
        workspace,
        (reference,),
        accepted=True,
        archive_validated=True,
        retention_qualified=True,
    )
    if damage is None:
        assert report.removed == (reference.path,) and not candidate.exists()
    else:
        assert report.removed == () and report.preserved == ((reference.path, damage),)
        assert candidate.exists()
    assert unknown.read_bytes() == b"unindexed\n"
    assert not tuple(workspace.rglob("*.tmp.*"))


@pytest.mark.parametrize("damage", ["missing", "tampered", "contradictory"])
def test_ac9_every_qualified_reader_rejects_invalid_required_evidence(
    damage, run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    from tests.runtime.verification_provenance_helpers import damage_latest_evidence

    host, state_path = _v8_host(tmp_path)
    monkeypatch.chdir(host)
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    damage_latest_evidence(state_path, damage)
    before = state_path.read_bytes()

    for arguments in (
        ("status", "--feature", "sample-feature"),
        ("orient", "--feature", "sample-feature"),
        ("doctor", "--feature", "sample-feature"),
        ("validate", "--feature", "sample-feature"),
        ("milestone", "advance", "--feature", "sample-feature"),
        ("verify", "--scope", "m1", "--feature", "sample-feature"),
    ):
        code, output, _error = run_cli([*arguments, "--json"])
        envelope = envelope_tools.parse(output)
        assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
        message = envelope["error"]["message"]
        assert damage in message or "evidence" in message
        assert state_path.read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "tampered", "contradictory"])
def test_ac9_exact_review_replay_rejects_invalid_evidence_before_provider_or_write(
    damage, run_cli, tmp_path, monkeypatch
) -> None:
    from tests.runtime.verification_provenance_helpers import (
        convert_current_fixture_to_v8,
        damage_latest_evidence,
    )
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import (
        current_host,
        gate_command,
        provider_transport,
        review_content,
    )

    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review", limit=1)},
    )
    convert_current_fixture_to_v8(host, state_path)
    calls = provider_transport(monkeypatch, review_content("milestone-review"))
    code, result = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 0, result
    assert len(calls) == 1
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "operational-example", "--json"]
    )
    assert code == 0, output
    damage_latest_evidence(state_path, damage)
    before = state_path.read_bytes()

    code, replay = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 3
    assert replay["error"]["code"] == "workspace-invalid"
    assert "evidence" in replay["error"]["message"]
    assert state_path.read_bytes() == before
    assert len(calls) == 1


def test_ac9_completion_rejects_invalid_manifest_before_acceptance(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    from tests.runtime.verification_provenance_helpers import (
        arm_completion_boundary,
        damage_latest_evidence,
    )

    host, state_path = _v8_host(tmp_path)
    monkeypatch.chdir(host)
    arm_completion_boundary(host, state_path)
    for scope in ("m1", "acceptance", "smoke"):
        code, output, _error = run_cli(
            ["verify", "--scope", scope, "--feature", "sample-feature", "--json"]
        )
        assert code == 0, output
    damage_latest_evidence(state_path, "tampered")
    before = state_path.read_bytes()
    code, output, _error = run_cli(
        ["feature", "complete", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(output)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
    assert "evidence" in envelope["error"]["message"]
    assert state_path.read_bytes() == before


@pytest.mark.parametrize("missing", ["historical-log", "historical-manifest"])
def test_ac10_completion_rejects_missing_referenced_history_before_acceptance(
    missing, run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    from tests.runtime.verification_provenance_helpers import arm_completion_boundary

    host, state_path = _v8_host(tmp_path)
    monkeypatch.chdir(host)
    arm_completion_boundary(host, state_path)
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    historical = yaml.safe_load(state_path.read_text())["verifications"][-1]

    (host / "src.py").write_text("VALUE = 2\n")
    for scope in ("m1", "acceptance", "smoke"):
        code, output, _error = run_cli(
            ["verify", "--scope", scope, "--feature", "sample-feature", "--json"]
        )
        assert code == 0, output

    current = yaml.safe_load(state_path.read_text())
    latest = {
        scope: next(
            fact
            for fact in reversed(current["verifications"])
            if fact["scope"] == scope
        )
        for scope in ("m1", "acceptance", "smoke")
    }
    if missing == "historical-log":
        target = historical["log"]
        assert target not in {fact["log"] for fact in latest.values()}
    else:
        target = historical["evidence"]["before"]["artifact"]
        current_manifests = {
            reference["artifact"]
            for fact in latest.values()
            for reference in (fact["evidence"]["before"], fact["evidence"]["after"])
        }
        assert target not in current_manifests
    (state_path.parent / target).unlink()

    before = state_path.read_bytes()
    code, output, _error = run_cli(
        ["feature", "complete", "--feature", "sample-feature", "--json"]
    )
    envelope = envelope_tools.parse(output)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
    assert "retained evidence" in envelope["error"]["message"]
    assert state_path.read_bytes() == before
    assert yaml.safe_load(before)["completion"] is None
    assert not (
        host / "docs/gate-trajectories/.raw/sample-feature/completion.tar.gz"
    ).exists()


def test_ac11_current_state_guidance_matches_v9_v10_compatibility() -> None:
    from heddle.contracts.schemas import CURRENT_STATE_SCHEMA, STATE_SCHEMA_VERSIONS
    from heddle.kernel.state import parse_state_document

    assert CURRENT_STATE_SCHEMA == "heddle.state/v10"
    assert STATE_SCHEMA_VERSIONS == ("heddle.state/v9", "heddle.state/v10")

    v9 = yaml.safe_load(TINY_STATE.read_text())
    parsed = parse_state_document(v9, source=Path("state.yaml"))
    assert parsed.schema == "heddle.state/v9"

    for schema in ("heddle.state/v8", "heddle.state/v11"):
        incompatible_state = deepcopy(v9)
        incompatible_state["schema"] = schema
        with pytest.raises(KernelError) as incompatible:
            parse_state_document(incompatible_state, source=Path("state.yaml"))
        assert incompatible.value.reason == "incompatible-state-schema"

    architecture = (REPO / "docs/design/architecture.md").read_text()
    workflow_model = " ".join(
        (REPO / "docs/design/workflow-model.md").read_text().split()
    )
    assert "Current state is `heddle.state/v10`" in architecture
    assert "current v10 features" in workflow_model
    assert "Active v9 ledgers remain readable and writable" in workflow_model
    assert "rejects v8, earlier, and future state schemas" in workflow_model


def test_ac12_repeated_real_verification_reuses_manifest_and_reduces_state_bytes(
    run_cli, tmp_path, monkeypatch
) -> None:
    from tests.runtime.verification_provenance_helpers import (
        expanded_evidence_projection,
    )

    host, state_path = _v8_host(tmp_path)
    monkeypatch.chdir(host)
    for _ in range(10):
        code, output, _error = run_cli(
            ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
        )
        assert code == 0, output

    document = yaml.safe_load(state_path.read_text())
    facts = document["verifications"][-10:]
    artifacts = {
        endpoint["artifact"]
        for fact in facts
        for endpoint in (fact["evidence"]["before"], fact["evidence"]["after"])
    }
    assert len(artifacts) == 1
    manifest_paths = tuple(
        state_path.parent.glob("verification/source-evidence.*.yaml")
    )
    actual_artifacts = {
        path.relative_to(state_path.parent).as_posix() for path in manifest_paths
    }
    assert actual_artifacts == artifacts
    compact_bytes = len(state_path.read_bytes()) + sum(
        len(path.read_bytes()) for path in manifest_paths
    )
    expanded = yaml.safe_dump(expanded_evidence_projection(state_path)).encode()
    expanded_bytes = len(expanded)
    assert compact_bytes < expanded_bytes


def test_ac12_current_parser_observation_status_and_orient_stay_within_ceiling(
    run_cli, tmp_path, monkeypatch
) -> None:
    from heddle.kernel.state import read_state_file
    from heddle.runtime.verification import assess_current_verification

    def exercise(host, state_path, *, populated):
        monkeypatch.chdir(host)
        if populated:
            code, output, _error = run_cli(
                [
                    "verify",
                    "--scope",
                    "m1",
                    "--feature",
                    "sample-feature",
                    "--json",
                ]
            )
            assert code == 0, output

        state = read_state_file(state_path)
        assessment = assess_current_verification(host, state, "m1")
        assert assessment.status == ("fresh" if populated else "missing")
        for command in ("status", "orient"):
            code, output, _error = run_cli(
                [command, "--feature", "sample-feature", "--json"]
            )
            assert code == 0, output
        return assessment.status

    empty_host, empty_state = _v8_host(tmp_path, milestones=False, name="empty-host")
    populated_host, populated_state = _v8_host(tmp_path, name="populated-host")
    assert exercise(empty_host, empty_state, populated=False) == "missing"
    assert exercise(populated_host, populated_state, populated=True) == "fresh"
