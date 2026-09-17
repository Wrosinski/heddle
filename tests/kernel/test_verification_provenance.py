"""
Current provenance contracts; identity readiness supersedes old digest and budget pins.
"""

from __future__ import annotations

import os
import shutil
from copy import deepcopy
from dataclasses import MISSING, fields
from pathlib import Path

import pytest

from heddle.kernel.project_config import KernelError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY_STATE = (
    REPO_ROOT / "tests/fixtures/workspaces/tiny/plans/sample-feature/state.yaml"
)


def _current_state_fixture() -> dict:
    import yaml

    return yaml.safe_load(TINY_STATE.read_text(encoding="utf-8"))


def test_ac01_normalization_and_raw_manifest_observation(tmp_path: Path) -> None:
    from heddle.io.source import observe_source
    from heddle.kernel.source_manifest import (
        SourceDefinition,
        normalize_source_paths,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("b\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("a\n", encoding="utf-8")
    paths = normalize_source_paths(("src/b.py", "src/a.py", "missing.py"))
    assert paths == ("missing.py", "src/a.py", "src/b.py")

    definition = SourceDefinition("milestone-owns", paths, paths)
    first = observe_source(tmp_path, definition)
    assert first.schema == "heddle.source-evidence/v1"
    assert first.definition.paths == paths and len(first.source_sha256) == 64

    other = tmp_path / "other"
    shutil.copytree(tmp_path / "src", other / "src")
    assert observe_source(other, definition).source_sha256 == first.source_sha256
    source = tmp_path / "src" / "a.py"
    source.write_text("a\n", encoding="utf-8")
    stat_before = source.stat()
    os.utime(source, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns + 1_000_000))
    assert observe_source(tmp_path, definition).source_sha256 == first.source_sha256

    (tmp_path / "src" / "a.py").chmod(0o755)
    assert observe_source(tmp_path, definition).source_sha256 != first.source_sha256


def test_ac01_cross_layer_source_shapes_are_fully_typed_and_required() -> None:
    from typing import get_type_hints

    from heddle.kernel.source_manifest import SourceDefinition, SourceSetKind
    from heddle.kernel.verification import GateSourceInventory, SourceDeclaration

    assert get_type_hints(SourceDefinition)["kind"] is SourceSetKind
    assert get_type_hints(SourceDeclaration)["kind"] is SourceSetKind
    assert all(field.default is MISSING for field in fields(GateSourceInventory))


def test_ac01_manifest_distinguishes_missing_regular_symlink_and_special(
    tmp_path: Path,
) -> None:
    import os

    from heddle.io.source import observe_source
    from heddle.kernel.source_manifest import SourceDefinition

    definition = SourceDefinition("milestone-owns", ("subject",), ("subject",))
    missing = observe_source(tmp_path, definition)
    subject = tmp_path / "subject"
    subject.write_text("value\n", encoding="utf-8")
    regular = observe_source(tmp_path, definition)
    assert regular.source_sha256 != missing.source_sha256

    subject.unlink()
    target = tmp_path / "target"
    target.write_text("first\n", encoding="utf-8")
    subject.symlink_to("target")
    symlink = observe_source(tmp_path, definition)
    assert symlink.source_sha256 not in {
        missing.source_sha256,
        regular.source_sha256,
    }
    target.write_text("second\n", encoding="utf-8")
    assert observe_source(tmp_path, definition).source_sha256 == symlink.source_sha256

    subject.unlink()
    os.mkfifo(subject)
    with pytest.raises(KernelError, match="not a regular file, directory, or symlink"):
        observe_source(tmp_path, definition)


@pytest.mark.parametrize(
    "paths",
    [
        ("",),
        ("/absolute",),
        ("a/../b",),
        ("a", "a/"),
        ("plans",),
        ("plans/other/state.yaml",),
    ],
)
def test_ac01_invalid_or_heddle_owned_declarations_fail(paths: tuple[str, ...]) -> None:
    from heddle.kernel.source_manifest import normalize_source_paths

    with pytest.raises(KernelError) as raised:
        normalize_source_paths(paths)
    assert raised.value.code == "workspace-invalid"


def test_ac01_milestone_ownership_can_name_foreign_heddle_records() -> None:
    from heddle.kernel.source_manifest import normalize_milestone_source_paths

    assert normalize_milestone_source_paths(
        ("plans/retired-feature/state.yaml", "src/current.py"),
        feature="current-feature",
    ) == ("plans/retired-feature/state.yaml", "src/current.py")


@pytest.mark.parametrize(
    "path",
    [
        "plans",
        "plans/current-feature",
        "plans/current-feature/state.yaml",
        "plans/current-feature.decision-journal.md",
        "plans/current-feature.decision-journal.md/child",
        "plans/current-feature.friction-retrospective.md",
        "plans/gate-effectiveness.md",
        "plans/gate-effectiveness.md/child",
    ],
)
def test_ac01_milestone_ownership_rejects_current_workflow_controls(
    path: str,
) -> None:
    from heddle.kernel.source_manifest import normalize_milestone_source_paths

    with pytest.raises(KernelError, match="this feature's Heddle-owned controls"):
        normalize_milestone_source_paths((path,), feature="current-feature")


@pytest.mark.parametrize(
    "path",
    [
        "plans-archive/x.py",
        "plansible/x.py",
        "docs/verification-provenance-notes.md",
        "docs/verification/x.md",
        "docs",
        "docs/verification-provenance/x.json",
    ],
)
def test_ac01_near_miss_declarations_remain_valid(path: str) -> None:
    from heddle.kernel.source_manifest import normalize_source_paths

    assert normalize_source_paths((path,)) == (path,)


@pytest.mark.parametrize(
    "schema", ["heddle.state/v1", "heddle.state/v2", "heddle.state/v3"]
)
def test_ac02_prior_format_cannot_import_unbound_verification(schema) -> None:
    from heddle.kernel.state import parse_state_document

    document = _current_state_fixture()
    document["schema"] = schema
    document["verifications"] = [
        {
            "scope": "m1",
            "command": "pytest -q",
            "exit_code": 0,
            "at": "2026-09-04T00:00Z",
            "log": "verification/m1.log",
        }
    ]
    before = deepcopy(document)
    with pytest.raises(KernelError, match=schema):
        parse_state_document(document, source=Path("state.yaml"))
    assert document == before


@pytest.mark.parametrize(
    ("stored_owns", "stored_command", "stored_expected", "payload", "accepted"),
    [
        ([], "pytest -q", "pass", {"owns": ["src.py"]}, True),
        (
            ["src.py"],
            "pytest -q",
            "pass",
            {"owns": ["src.py", "tests/test_src.py"]},
            True,
        ),
        (
            ["src.py"],
            "pytest -q",
            "pass",
            {"owns": ["src.py", "plans/retired-feature/state.yaml"]},
            True,
        ),
        (
            ["src.py"],
            "   ",
            "",
            {"verification": {"command": "pytest -q", "expected": "pass"}},
            True,
        ),
        (
            ["src.py"],
            "",
            "pass",
            {"verification": {"command": "pytest -q", "expected": "pass"}},
            True,
        ),
        (
            ["src.py"],
            "",
            "pass",
            {"verification": {"command": "pytest -q", "expected": "different"}},
            False,
        ),
        (["old.py"], "pytest -q", "pass", {"owns": ["new.py"]}, False),
        (
            ["src.py", "tests/test_src.py"],
            "pytest -q",
            "pass",
            {"owns": ["tests/test_src.py"]},
            False,
        ),
        (
            ["src.py"],
            "pytest -q",
            "pass",
            {"verification": {"command": "other", "expected": "pass"}},
            False,
        ),
        (
            ["src.py"],
            "pytest tests/retired.py -q",
            "pass",
            {
                "owns": ["src.py", "tests/current.py"],
                "verification": {
                    "command": "pytest tests/current.py -q",
                    "expected": "pass",
                },
            },
            True,
        ),
        (
            ["src.py"],
            "pytest tests/retired.py -q",
            "pass",
            {
                "owns": ["src.py", "tests/current.py"],
                "verification": {
                    "command": "pytest tests/current.py -q",
                    "expected": "different",
                },
            },
            False,
        ),
        (
            ["src.py"],
            "",
            "",
            {
                "owns": ["src.py"],
                "verification": {"command": "pytest -q", "expected": "pass"},
            },
            False,
        ),
        ([], "", "", {"title": "replacement"}, False),
    ],
)
def test_ac02_done_milestone_repair_matrix(
    stored_owns: list[str],
    stored_command: str,
    stored_expected: str,
    payload: dict,
    accepted: bool,
) -> None:
    from heddle.kernel.write_path import edit_milestone

    document = _current_state_fixture()
    milestone = document["milestones"][0]
    milestone["status"] = "done"
    milestone["owns"] = stored_owns
    milestone["verification"] = {
        "command": stored_command,
        "expected": stored_expected,
    }
    before = deepcopy(document)

    if accepted:
        edited = edit_milestone(document, "m1", payload)
        assert edited != before
    else:
        with pytest.raises(KernelError) as raised:
            edit_milestone(document, "m1", payload)
        assert raised.value.code == "milestone-out-of-sequence"
    assert document == before


def test_ac02_current_state_requires_bound_evidence_references() -> None:
    from heddle.kernel.state import parse_state_document

    reference = {
        "schema": "heddle.source-evidence-ref/v1",
        "artifact": "verification/source-evidence." + "a" * 64 + ".yaml",
        "artifact_sha256": "a" * 64,
        "source_sha256": "b" * 64,
    }
    document = _current_state_fixture()
    document["verifications"] = [
        {
            "scope": "m1",
            "command": "pytest -q",
            "exit_code": 0,
            "at": "2026-09-04T00:00Z",
            "log": "verification/m1.log",
            "evidence": {
                "schema": "heddle.verification-evidence/v1",
                "before": reference,
                "after": reference,
            },
        }
    ]
    parsed = parse_state_document(document, source=Path("state.yaml"))
    evidence = parsed.verifications[0].evidence
    assert evidence.before == evidence.after
    assert evidence.before.source_sha256 == "b" * 64

    unbound = deepcopy(document)
    unbound["verifications"][0]["evidence"] = None
    with pytest.raises(KernelError, match="must bind current source|must be a mapping"):
        parse_state_document(unbound, source=Path("state.yaml"))

    invalid = deepcopy(document)
    invalid["verifications"][0]["evidence"]["before"]["artifact_sha256"] = "sha256:" + (
        "a" * 64
    )
    with pytest.raises(KernelError, match="64 lowercase hexadecimal"):
        parse_state_document(invalid, source=Path("state.yaml"))

    missing_feature_inputs = deepcopy(document)
    missing_feature_inputs.pop("feature_inputs")
    with pytest.raises(KernelError, match="feature_inputs"):
        parse_state_document(missing_feature_inputs, source=Path("state.yaml"))


def test_ac05_freshness_matrix_uses_latest_fact_and_one_precedence() -> None:
    from heddle.kernel.source_manifest import (
        ObservedPath,
        SourceDefinition,
        build_source_evidence,
        evidence_reference,
    )
    from heddle.kernel.state import VerificationEvidence, VerificationFact
    from heddle.kernel.verification import (
        assess_verification_fact,
        latest_verification_fact,
    )

    definition = SourceDefinition("milestone-owns", ("src/a.py",), ("src/a.py",))
    current = build_source_evidence(
        definition, (ObservedPath("src/a.py", "file", False, b"a\n"),)
    )

    def changed(content: bytes, *, path: str = "src/a.py"):
        other = SourceDefinition("milestone-owns", (path,), (path,))
        return build_source_evidence(
            other, (ObservedPath(path, "file", False, content),)
        )

    def fact(*, before=current, after=current, **changes):
        values = {
            "scope": "m1",
            "command": "pytest -q",
            "exit_code": 0,
            "at": "2026-09-04T00:00Z",
            "log": "verification/m1.log",
            "evidence": VerificationEvidence(
                schema="heddle.verification-evidence/v1",
                before=evidence_reference(before),
                after=evidence_reference(after),
            ),
        }
        values.update(changes)
        return VerificationFact(**values)

    def status(candidate, before=current, after=current, observed=current):
        return assess_verification_fact(
            candidate,
            scope="m1",
            command="pytest -q",
            before=before,
            after=after,
            current=observed,
        ).status

    assert status(None) == "missing"
    latest = latest_verification_fact((fact(), fact(exit_code=1)), scope="m1")
    assert status(latest) == "failed"
    different_content = changed(b"b\n")
    assert (
        status(fact(before=current, after=different_content), after=different_content)
        == "unstable"
    )
    assert status(fact(command="old")) == "command-stale"
    different_set = changed(b"a\n", path="src/b.py")
    assert status(fact(), observed=different_set) == "source-set-stale"
    assert status(fact(), observed=different_content) == "content-stale"
    assert status(fact()) == "fresh"
    assert status(fact(exit_code=1)) == "failed"
