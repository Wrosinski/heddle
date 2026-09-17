"""Focused P2/P3 runtime tests for verification evidence provenance."""

from __future__ import annotations

import inspect
import io
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.runtime.verification_provenance_helpers import (
    make_current_host as _v2_host,
)
from tests.runtime.verification_provenance_helpers import write_yaml as _write_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_ac03_verification_fact_records_complete_evidence_reference() -> None:
    from heddle.kernel.source_manifest import (
        ObservedPath,
        SourceDefinition,
        build_source_evidence,
        evidence_reference,
    )
    from heddle.kernel.state import VerificationEvidence
    from heddle.runtime.verify_exec import verification_fact, verification_log_relpath

    source = build_source_evidence(
        SourceDefinition("milestone-owns", ("src/a.py",), ("src/a.py",)),
        (ObservedPath("src/a.py", "file", False, b"a\n"),),
    )
    reference = evidence_reference(source)
    evidence = VerificationEvidence(
        schema="heddle.verification-evidence/v1",
        before=reference,
        after=reference,
    )
    fact = verification_fact(
        "m1",
        "pytest -q",
        exit_code=0,
        at="2026-09-04T00:00Z",
        revision=7,
        evidence=evidence,
        log="verification/m1-fixed-attempt.log",
    )
    assert fact["command"] == "pytest -q"
    assert fact["log"] == "verification/m1-fixed-attempt.log"
    assert verification_log_relpath(
        "m1", "2026-09-04T00:00Z", 7, attempt_id="one"
    ) != verification_log_relpath("m1", "2026-09-04T00:00Z", 7, attempt_id="two")
    assert fact["evidence"] == {
        "schema": "heddle.verification-evidence/v1",
        "before": {
            "schema": reference.schema,
            "artifact": reference.artifact,
            "artifact_sha256": reference.artifact_sha256,
            "source_sha256": reference.source_sha256,
        },
        "after": {
            "schema": reference.schema,
            "artifact": reference.artifact,
            "artifact_sha256": reference.artifact_sha256,
            "source_sha256": reference.source_sha256,
        },
    }


def test_ac03_real_verify_producer_binds_stable_and_unstable_attempts(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.runtime.verification import read_source_evidence

    host, state_path = _v2_host(tmp_path)
    from tests.runtime.verification_provenance_helpers import (
        convert_current_fixture_to_v8,
    )

    convert_current_fixture_to_v8(host, state_path)
    monkeypatch.chdir(host)

    code, out, _err = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, f"FAIL AC-3: stable verify failed: {out}"
    first = yaml.safe_load(state_path.read_text(encoding="utf-8"))["verifications"][-1]
    assert first["evidence"]["before"] == first["evidence"]["after"]
    assert first["command"] == "python3 -c 'print(2)'"
    observed = read_source_evidence(state_path.parent, first["evidence"]["before"])
    assert observed.definition.kind == "milestone-owns"
    assert observed.definition.paths == ("src.py",)
    assert observed.source_sha256 == first["evidence"]["before"]["source_sha256"]
    assert first["log"].startswith("verification/")
    assert "-a" in first["log"]
    assert "autopilot/" not in first["log"]
    assert Path(state_path.parent / first["log"]).is_file()

    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["milestones"][0]["verification"]["command"] = (
        'python3 -c "from pathlib import Path; '
        "Path('src.py').write_text('value = 2\\n')\""
    )
    _write_yaml(state_path, state)
    code, out, _err = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    unstable = envelope_tools.parse(out)
    assert code == 3 and unstable["error"]["code"] == "verification-failed", (
        f"FAIL AC-3: unstable attempt must append and fail truthfully: {out}"
    )
    assert unstable["error"]["details"]["status"] == "unstable"
    assert unstable["error"]["details"]["recorded"] is True
    second = yaml.safe_load(state_path.read_text(encoding="utf-8"))["verifications"][-1]
    assert second["evidence"]["before"] != second["evidence"]["after"]
    before_evidence = read_source_evidence(
        state_path.parent, second["evidence"]["before"]
    )
    after_evidence = read_source_evidence(
        state_path.parent, second["evidence"]["after"]
    )
    assert (
        before_evidence.definition.paths
        == after_evidence.definition.paths
        == ("src.py",)
    )
    assert before_evidence.source_sha256 != after_evidence.source_sha256

    before_dry = state_path.read_bytes()
    logs_before = set(state_path.parent.glob("verification/*.log"))
    code, dry_output, _err = run_cli(
        [
            "verify",
            "--scope",
            "m1",
            "--feature",
            "sample-feature",
            "--dry-run",
            "--json",
        ]
    )
    assert code == 0 and state_path.read_bytes() == before_dry
    dry = envelope_tools.parse(dry_output)["data"]["verification"]
    assert dry == {
        "scope": "m1",
        "command": state["milestones"][0]["verification"]["command"],
        "source_set_kind": "milestone-owns",
        "declaration_paths": ["src.py"],
    }
    assert set(state_path.parent.glob("verification/*.log")) == logs_before


def test_ac03_ac05_git_diagnostics_are_collected_only_before_command(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.kernel.state import read_state_file
    from heddle.kernel.verification import GitDiagnostics
    from heddle.runtime import verification
    from heddle.runtime.verification import assess_current_verification

    host, state_path = _v2_host(tmp_path)
    monkeypatch.chdir(host)
    calls: list[tuple[str, ...]] = []

    def diagnostics(_root: Path, paths: tuple[str, ...]) -> GitDiagnostics:
        calls.append(paths)
        return GitDiagnostics("a" * 40, True)

    monkeypatch.setattr(verification, "observe_git_diagnostics", diagnostics)
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
    assert assess_current_verification(host, state, "m1").status == "fresh"
    assert calls == [("src.py",)]
    assert state.verifications[-1].evidence.before == (
        state.verifications[-1].evidence.after
    )


@pytest.mark.parametrize(
    ("command", "expected_exit"),
    [("exit 7", 7), ("missing-heddle-verification-command", 127)],
)
def test_ac03_failed_process_attempts_keep_truthful_facts(
    command: str,
    expected_exit: int,
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["milestones"][0]["verification"]["command"] = command
    _write_yaml(state_path, state)
    monkeypatch.chdir(host)

    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    envelope = json.loads(output)
    assert code == 3 and not envelope["ok"], envelope
    error = envelope["error"]
    assert error["code"] == "verification-failed"
    assert error["details"]["child_exit_code"] == expected_exit
    assert error["details"]["recorded"] is True
    assert error["details"]["status"] == "failed"
    fact = yaml.safe_load(state_path.read_text(encoding="utf-8"))["verifications"][-1]
    assert fact["exit_code"] == expected_exit
    assert (state_path.parent / fact["log"]).is_file()
    assert (
        fact["evidence"]["before"]["source_sha256"]
        == fact["evidence"]["after"]["source_sha256"]
    )


def test_ac03_deleting_sole_leaf_records_bound_unstable_attempt(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["milestones"][0]["verification"]["command"] = "rm src.py"
    _write_yaml(state_path, state)
    monkeypatch.chdir(host)

    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    envelope = json.loads(output)
    assert code == 3 and not envelope["ok"], envelope
    error = envelope["error"]
    assert error["code"] == "verification-failed"
    assert error["details"]["child_exit_code"] == 0
    assert error["details"]["recorded"] is True
    assert error["details"]["status"] == "unstable"
    fact = yaml.safe_load(state_path.read_text(encoding="utf-8"))["verifications"][-1]
    assert fact["exit_code"] == 0
    from heddle.runtime.verification import read_source_evidence

    before_evidence = read_source_evidence(
        state_path.parent, fact["evidence"]["before"]
    )
    assert before_evidence.definition.paths == ("src.py",)
    assert (
        fact["evidence"]["before"]["source_sha256"]
        != fact["evidence"]["after"]["source_sha256"]
    )


def test_ac03_capture_and_cas_failures_append_no_fact(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import write_path

    host, state_path = _v2_host(tmp_path)
    monkeypatch.chdir(host)
    before = state_path.read_bytes()

    def capture_failure(*_args, **_kwargs) -> int:
        raise KernelError(
            code="workspace-invalid",
            message="capture failed",
            hint="repair capture",
        )

    original_runner = write_path.run_verification_command
    monkeypatch.setattr(write_path, "run_verification_command", capture_failure)
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 3 and "capture failed" in output
    assert state_path.read_bytes() == before

    monkeypatch.setattr(write_path, "run_verification_command", original_runner)

    def cas_failure(*_args, **_kwargs):
        raise write_path.Conflict("simulated verification CAS conflict")

    monkeypatch.setattr(write_path, "commit_state", cas_failure)
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    envelope = json.loads(output)
    assert code == 5 and "simulated verification CAS conflict" in output
    assert envelope["error"]["code"] == "conflict"
    assert envelope["error"]["details"]["recorded"] is False
    assert envelope["error"]["details"]["scope"] == "m1"
    assert envelope["error"]["details"]["evidence"]
    assert state_path.read_bytes() == before


def test_post_record_assessment_failure_preserves_recorded_fact_identity(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import write_path

    host, state_path = _v2_host(tmp_path)
    monkeypatch.chdir(host)
    before_count = len(yaml.safe_load(state_path.read_text())["verifications"])

    def assessment_failure(*_args, **_kwargs):
        raise KernelError(
            code="workspace-invalid",
            message="injected post-record evidence failure",
            hint="restore the recorded evidence",
        )

    monkeypatch.setattr(write_path, "assess_recorded_verification", assessment_failure)
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    envelope = json.loads(output)
    details = envelope["error"]["details"]

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
    assert details["recorded"] is True
    assert details["fact_index"] == before_count
    assert details["revision"] > 0
    state = yaml.safe_load(state_path.read_text())
    assert len(state["verifications"]) == before_count + 1
    assert state["verifications"][details["fact_index"]]["log"] == details["log"]


def test_ac04_reconciliation_fails_closed_on_omitted_changes() -> None:
    from heddle.kernel.verification import (
        GateSourceInventory,
        SourceDeclaration,
        reconcile_source_coverage,
    )

    declaration = SourceDeclaration("feature-owned-union", ("src", "tests/unit"))
    inventory = GateSourceInventory(
        tracked_paths=("src/a.py",),
        untracked_nonignored_paths=("tests/unit/test_a.py",),
        changed_since_baseline=(
            "src/a.py",
            "tests/unit/test_a.py",
            "docs/user.md",
            "plans/demo/state.yaml",
        ),
        baseline_error=None,
        baseline_probe="plans/demo/state.yaml",
        leaf_error=None,
    )
    result = reconcile_source_coverage(
        declaration,
        inventory,
        ("plans", "docs/verification-provenance"),
    )
    assert result.status == "unresolved"
    assert result.unresolved_paths == ("docs/user.md",)
    assert "ownership" in (result.action or "")


def test_ac04_reconciliation_exclusions_are_explicit_and_root_bounded() -> None:
    from heddle.kernel.verification import (
        GateSourceInventory,
        SourceDeclaration,
        reconcile_source_coverage,
    )

    inventory = GateSourceInventory(
        tracked_paths=(),
        untracked_nonignored_paths=(),
        changed_since_baseline=(
            "plans/demo/state.yaml",
            "docs/verification-provenance/demo.json",
            "plans-archive/keep.md",
            "docs/verification-provenance-notes.md",
        ),
        baseline_error=None,
        baseline_probe="plans/demo/state.yaml",
        leaf_error=None,
    )
    result = reconcile_source_coverage(
        SourceDeclaration("feature-owned-union", ("src",)),
        inventory,
        ("plans", "docs/verification-provenance"),
    )
    assert result.unresolved_paths == (
        "docs/verification-provenance-notes.md",
        "plans-archive/keep.md",
    )


@pytest.mark.parametrize(
    ("error", "probe"),
    [
        ("baseline-probe-missing", None),
        ("git-unavailable", "plans/demo/state.yaml"),
        ("baseline-unresolvable", "plans/demo/state.yaml"),
        ("git-query-failed", "plans/demo/state.yaml"),
    ],
)
def test_ac04_baseline_failures_name_cause_and_recovery(
    error: str, probe: str | None
) -> None:
    """Survivor pin for AC-3 paired with authoritative-baseline refusal."""
    from heddle.kernel.verification import (
        GateSourceInventory,
        SourceDeclaration,
        reconcile_source_coverage,
    )

    inventory = GateSourceInventory((), (), None, error, probe, None)
    result = reconcile_source_coverage(
        SourceDeclaration("feature-owned-union", ("src",)),
        inventory,
        ("plans", "docs/verification-provenance"),
    )
    assert result.status == "baseline-unavailable"
    assert error in (result.action or "")
    assert (probe or "missing") in (result.action or "")
    assert "repository" in (result.action or "")


def test_ac04_real_git_inventory_tracks_rename_delete_and_untracked(
    tmp_path: Path,
) -> None:
    """Survivor pin for AC-4 paired with the explicit-baseline red case."""
    from heddle.kernel.verification import (
        SourceDeclaration,
        reconcile_source_coverage,
    )
    from heddle.runtime.verification import (
        inventory_source_paths,
        observe_current_source,
    )

    host, state_path = _v2_host(tmp_path)
    source_dir = host / "src"
    source_dir.mkdir()
    (host / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (source_dir / "old.py").write_text("old\n", encoding="utf-8")
    (source_dir / "deleted.py").write_text("deleted\n", encoding="utf-8")
    (source_dir / "café.py").write_text("tracked unicode\n", encoding="utf-8")
    (source_dir / "outbound.py").write_text("move me\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=host, check=True)
    subprocess.run(
        ["git", "config", "user.email", "inventory@example.test"],
        cwd=host,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Inventory Test"], cwd=host, check=True
    )
    subprocess.run(["git", "add", "."], cwd=host, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=host, check=True)

    subprocess.run(["git", "mv", "src/old.py", "src/renamed.py"], cwd=host, check=True)
    (host / "elsewhere").mkdir()
    subprocess.run(
        ["git", "mv", "src/outbound.py", "elsewhere/moved.py"],
        cwd=host,
        check=True,
    )
    (source_dir / "deleted.py").unlink()
    (source_dir / "untracked.py").write_text("new\n", encoding="utf-8")
    (source_dir / "naïve.py").write_text("untracked unicode\n", encoding="utf-8")
    (host / "outside.py").write_text("outside\n", encoding="utf-8")
    ignored = source_dir / "__pycache__" / "ignored.pyc"
    ignored.parent.mkdir()
    ignored.write_bytes(b"first")
    declaration = SourceDeclaration("feature-owned-union", ("src",))
    inventory = inventory_source_paths(
        host,
        declaration,
        state_path.relative_to(host).as_posix(),
    )

    assert inventory.baseline_error is None
    assert inventory.leaf_error is None
    assert {
        "src/old.py",
        "src/renamed.py",
        "src/deleted.py",
        "src/outbound.py",
        "elsewhere/moved.py",
    }.issubset(set(inventory.changed_since_baseline or ()))
    assert {"src/untracked.py", "src/naïve.py"}.issubset(
        set(inventory.untracked_nonignored_paths)
    )
    assert "outside.py" in (inventory.changed_since_baseline or ())
    assert "src/café.py" in inventory.tracked_paths
    assert "src/deleted.py" not in inventory.tracked_paths
    assert "src/__pycache__/ignored.pyc" not in inventory.untracked_nonignored_paths
    definition, before, _diagnostics = observe_current_source(
        host, declaration, with_diagnostics=False, scope="acceptance"
    )
    assert {"src/café.py", "src/naïve.py", "src/untracked.py"}.issubset(
        definition.paths
    )
    ignored.write_bytes(b"second")
    _definition, ignored_after, _diagnostics = observe_current_source(
        host, declaration, with_diagnostics=False, scope="acceptance"
    )
    assert before.source_sha256 == ignored_after.source_sha256
    (source_dir / "untracked.py").write_text("changed\n", encoding="utf-8")
    _definition, after, _diagnostics = observe_current_source(
        host, declaration, with_diagnostics=False, scope="acceptance"
    )
    assert before.source_sha256 != after.source_sha256
    reconciliation = reconcile_source_coverage(
        declaration, inventory, ("plans", "docs/verification-provenance")
    )
    assert reconciliation.status == "unresolved"
    assert reconciliation.unresolved_paths == ("elsewhere/moved.py", "outside.py")


def test_local_history_shared_baseline_covers_cumulative_source(
    tmp_path: Path,
) -> None:
    """AC-3/AC-4 red: final coverage consumes the same typed baseline as gates."""
    from heddle.kernel import state as state_module
    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime.verification import inventory_source_paths

    assert "source_baseline" in inspect.signature(inventory_source_paths).parameters, (
        "FAIL local-history AC-3: final inventory has no typed baseline input"
    )
    assert hasattr(state_module, "SourceBaseline")
    host = tmp_path / "explicit-baseline"
    (host / "src").mkdir(parents=True)
    (host / "src/committed.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=host, check=True)
    subprocess.run(
        ["git", "config", "user.email", "baseline@example.invalid"],
        cwd=host,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Baseline Test"], cwd=host, check=True
    )
    subprocess.run(["git", "add", "src/committed.py"], cwd=host, check=True)
    subprocess.run(["git", "commit", "-qm", "source baseline"], cwd=host, check=True)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=host,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (host / "src/committed.py").write_text("VALUE = 2\n")
    subprocess.run(["git", "add", "src/committed.py"], cwd=host, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "committed feature work"], cwd=host, check=True
    )
    (host / "src/dirty.py").write_text("DIRTY = True\n")

    inventory = inventory_source_paths(
        host,
        SourceDeclaration("feature-owned-union", ("src",)),
        source_baseline=state_module.SourceBaseline("git-commit", baseline),
    )

    assert inventory.baseline_error is None
    assert {"src/committed.py", "src/dirty.py"}.issubset(
        set(inventory.changed_since_baseline or ())
    )


def test_ac01_exact_missing_declaration_has_one_identity_with_or_without_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime import verification
    from heddle.runtime.verification import observe_current_source

    host = tmp_path / "host"
    host.mkdir()
    exact = host / "deleted.py"
    exact.write_text("tracked then deleted\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=host, check=True)
    subprocess.run(
        ["git", "config", "user.email", "missing@example.test"], cwd=host, check=True
    )
    subprocess.run(["git", "config", "user.name", "Missing Test"], cwd=host, check=True)
    subprocess.run(["git", "add", "deleted.py"], cwd=host, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=host, check=True)
    exact.unlink()

    declaration = SourceDeclaration("milestone-owns", ("deleted.py",))
    healthy = verification.inventory_source_paths(host, declaration)
    assert healthy.tracked_paths == ()
    healthy_definition, healthy_manifest, _diagnostics = observe_current_source(
        host,
        declaration,
        with_diagnostics=False,
        scope="m1",
    )
    assert healthy_definition.paths == ("deleted.py",)

    monkeypatch.setattr(
        verification.git,
        "run_git",
        lambda *_args, **_kwargs: ("", "injected Git outage"),
    )
    unavailable = verification.inventory_source_paths(host, declaration)
    assert unavailable.leaf_error == "injected Git outage"
    unavailable_definition, unavailable_manifest, _diagnostics = observe_current_source(
        host,
        declaration,
        with_diagnostics=False,
        scope="m1",
    )
    assert healthy_definition == unavailable_definition
    assert healthy_manifest == unavailable_manifest


def test_ac01_resolved_milestone_source_observes_foreign_heddle_record(
    tmp_path: Path,
) -> None:
    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime.verification import observe_current_source

    host = tmp_path / "host"
    record = host / "plans" / "retired-feature" / "state.yaml"
    record.parent.mkdir(parents=True)
    record.write_text("retired: true\n", encoding="utf-8")

    definition, manifest, _diagnostics = observe_current_source(
        host,
        SourceDeclaration("milestone-owns", ("plans/retired-feature/state.yaml",)),
        with_diagnostics=False,
        scope="m1",
    )

    assert definition.paths == ("plans/retired-feature/state.yaml",)
    assert manifest.observations[0].path == "plans/retired-feature/state.yaml"


def test_ac01_inventory_permission_fault_is_typed_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime import verification

    target = tmp_path / "blocked.py"
    target.write_text("blocked\n", encoding="utf-8")
    real_lstat = verification.os.lstat

    def denied(path: Path):
        if Path(path) == target:
            raise PermissionError("injected denial")
        return real_lstat(path)

    monkeypatch.setattr(verification.os, "lstat", denied)
    with pytest.raises(KernelError, match="blocked.py.*injected denial") as raised:
        verification.inventory_source_paths(
            tmp_path,
            SourceDeclaration("milestone-owns", ("blocked.py",)),
        )
    assert raised.value.code == "workspace-invalid"


def test_ac01_git_fallback_inspection_error_names_scope_and_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime import verification

    target = tmp_path / "blocked.py"
    target.write_bytes(b"required")
    original = Path.lstat

    def denied(path):
        if path == target:
            raise PermissionError("injected denial")
        return original(path)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(
        KernelError,
        match="verification source for 'm1'.*blocked.py.*injected denial",
    ):
        verification.observe_current_source(
            tmp_path,
            SourceDeclaration("milestone-owns", ("blocked.py",)),
            scope="m1",
            with_diagnostics=False,
        )


def test_ac01_inventory_faults_are_scoped_to_the_declaration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime import verification

    (tmp_path / "src").mkdir()
    (tmp_path / "src/ok.py").write_text("ok\n", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    blocked = tmp_path / "vendor/blocked.py"
    blocked.write_text("blocked\n", encoding="utf-8")
    monkeypatch.setattr(
        verification.git,
        "tracked_files",
        lambda _root: (["src/ok.py", "vendor/blocked.py"], None),
    )
    monkeypatch.setattr(
        verification.git, "discover_untracked_files", lambda _root: ([], None)
    )
    real_lstat = verification.os.lstat

    def denied(path: Path):
        if Path(path) == blocked:
            raise PermissionError("injected denial")
        return real_lstat(path)

    monkeypatch.setattr(verification.os, "lstat", denied)
    inventory = verification.inventory_source_paths(
        tmp_path,
        SourceDeclaration("milestone-owns", ("src",)),
    )
    assert inventory.tracked_paths == ("src/ok.py",)
    with pytest.raises(KernelError, match="vendor/blocked.py.*injected denial"):
        verification.inventory_source_paths(
            tmp_path,
            SourceDeclaration("milestone-owns", ("vendor",)),
        )


def test_ac01_git_unavailable_rejects_directory_without_partial_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime import verification
    from heddle.runtime.verification import observe_current_source

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "inside.py").write_text("inside\n", encoding="utf-8")
    (tmp_path / "exact.py").write_text("exact\n", encoding="utf-8")
    (tmp_path / "literal-link").symlink_to("src")

    monkeypatch.setattr(
        verification.git,
        "run_git",
        lambda *_args, **_kwargs: ("", "git executable not found"),
    )

    mixed = SourceDeclaration("milestone-owns", ("exact.py", "src"))
    inventory = verification.inventory_source_paths(tmp_path, mixed)
    assert inventory.leaf_error == "git executable not found"
    with pytest.raises(KernelError, match="Git-backed directory src") as caught:
        observe_current_source(tmp_path, mixed, scope="m1")
    assert "git executable not found" in str(caught.value)

    for exact in ("exact.py", "literal-link", "missing.py"):
        definition, manifest, _diagnostics = observe_current_source(
            tmp_path,
            SourceDeclaration("milestone-owns", (exact,)),
            with_diagnostics=False,
            scope="m1",
        )
        assert definition.paths == (exact,)
        assert manifest.definition.paths == (exact,)


def test_ac06_ac07_scope_resolution_has_no_cross_fallback(tmp_path: Path) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.state import parse_state_document
    from heddle.kernel.verification import (
        resolve_source_declaration,
        verification_command_for_scope,
    )
    from heddle.runtime.verify_exec import verification_command

    _host, state_path = _v2_host(tmp_path)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    typed = parse_state_document(state, source=Path("state.yaml"))
    declaration = resolve_source_declaration(typed, "m1")
    assert declaration.kind == "milestone-owns"
    assert declaration.paths == ("src.py",)
    assert verification_command(state, "m1") == "python3 -c 'print(2)'"
    with pytest.raises(KernelError, match="unsupported"):
        resolve_source_declaration(typed, "feature")
    with pytest.raises(
        KernelError,
        match="verification source for 'bogus' is not a known verification scope",
    ) as caught:
        verification_command_for_scope(typed, "bogus")
    assert "None" not in str(caught.value)


def test_ac07_help_manifest_advertises_feature_verification_scope() -> None:
    """The retained node proves the feature scope is no longer advertised."""
    from heddle.contracts import operations as ops
    from heddle.runtime.contracts import build_manifest

    manifest = build_manifest()
    verify = next(row for row in manifest["commands"] if row["name"] == "verify")
    scope = next(flag for flag in verify["flags"] if flag["name"] == "--scope")
    assert scope["summary"] == ops.VERIFICATION_SCOPE_GUIDANCE


def test_ac10_preterminal_complete_can_advance_reconciliation_milestone(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def close_host(name: str) -> tuple[Path, Path]:
        host, state_path = _v2_host(tmp_path, name=name)
        monkeypatch.chdir(host)
        code, output, _error = run_cli(
            ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
        )
        assert code == 0, output
        (host / "reconciled.py").write_text("value = 1\n", encoding="utf-8")
        state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        state["stage"] = "complete"
        state["authorized_through"] = "complete"
        state["milestones"][0]["status"] = "done"
        reconciliation = deepcopy(state["milestones"][0])
        reconciliation.update(
            {
                "id": "m2",
                "title": "Close ownership reconciliation",
                "depends_on": ["m1"],
                "owns": ["reconciled.py"],
                "status": "todo",
            }
        )
        reconciliation.pop("tasks", None)
        state["milestones"].append(reconciliation)
        _write_yaml(state_path, state)
        return host, state_path

    host, state_path = close_host("preterminal")
    monkeypatch.chdir(host)
    code, output, _error = run_cli(
        ["milestone", "advance", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert state["milestones"][-1]["status"] == "current"

    from tests.tiering_completion_helpers import final_host

    (tmp_path / "accepted").mkdir()
    accepted = final_host(tmp_path / "accepted", monkeypatch)
    result = accepted.complete()
    assert result.ok and result.data["accepted"], result
    before = accepted.state.read_bytes()
    code, output, _error = run_cli(
        ["milestone", "advance", "--feature", result.data["feature"], "--json"]
    )
    assert code == 3 and "accepted" in output, output
    assert accepted.state.read_bytes() == before


def test_ac07_final_scope_union_ignores_feature_inputs_and_deduplicates(
    tmp_path: Path,
) -> None:
    from heddle.kernel.state import parse_state_document
    from heddle.kernel.verification import resolve_source_declaration

    _host, state_path = _v2_host(tmp_path)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["feature_inputs"] = ["legacy-only.py"]
    state["milestones"][0]["status"] = "done"
    second = deepcopy(state["milestones"][0])
    second.update(
        {
            "id": "m2",
            "depends_on": ["m1"],
            "owns": ["src.py/", "extra.py"],
            "status": "current",
        }
    )
    state["milestones"].append(second)
    typed = parse_state_document(state, source=state_path)

    declaration = resolve_source_declaration(typed, "acceptance")
    assert declaration.kind == "feature-owned-union"
    assert declaration.paths == ("extra.py", "src.py")
    assert "legacy-only.py" not in declaration.paths


@pytest.mark.parametrize("scope", ["acceptance", "smoke", "live"])
def test_ac07_empty_skeleton_final_scopes_require_milestone_ownership(
    scope: str,
    tmp_path: Path,
) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.state import read_state_file
    from heddle.kernel.verification import resolve_source_declaration

    _host, state_path = _v2_host(tmp_path, milestones=False)
    with pytest.raises(KernelError) as raised:
        resolve_source_declaration(read_state_file(state_path), scope)
    assert raised.value.code == "workspace-invalid"
    assert "heddle milestone edit <milestone-id> --from-file -" in raised.value.message


def test_ac07_feature_input_parser_is_strict_and_byte_sorted() -> None:
    from heddle.runtime.feature_inputs import parse_feature_inputs

    assert parse_feature_inputs(b'["z.py", "a.py"]') == ("a.py", "z.py")
    for payload in (
        b"[]",
        b"{}",
        b"not-json",
        b'["a.py", 1]',
        b'["/absolute.py"]',
        b'["a/../b.py"]',
        b'["a.py", "a.py/"]',
        b'["a.py", "a.py"]',
        b'["a.py"] trailing',
        b'["a.py", ""]',
        b'["a.py", "   "]',
        b'[["a.py"]]',
        b'["plans/x"]',
    ):
        with pytest.raises(ValueError):
            parse_feature_inputs(payload)

    with pytest.raises(ValueError, match="not one UTF-8 JSON value"):
        parse_feature_inputs(b"[" * 10_000 + b"]" * 10_000)


def test_ac07_feature_input_file_read_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.runtime import feature_inputs

    read_sizes: list[int] = []

    class TrackingReader(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return super().read(size)

    monkeypatch.setattr(
        Path,
        "open",
        lambda _path, _mode: TrackingReader(b"x" * (1024 * 1024 + 2)),
    )
    payload = feature_inputs._read_payload("large.json")
    assert len(payload) == 1024 * 1024 + 1
    assert read_sizes == [1024 * 1024 + 1]


def test_ac07_feature_inputs_cli_is_atomic_and_milestone_limited(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path, milestones=False)
    monkeypatch.chdir(host)
    payload = tmp_path / "inputs.json"
    payload.write_text('["z.py", "a.py"]', encoding="utf-8")
    (host / "a.py").write_text("a\n", encoding="utf-8")
    (host / "z.py").write_text("z\n", encoding="utf-8")

    code, out, _err = run_cli(
        [
            "feature",
            "inputs",
            "set",
            "--from-file",
            str(payload),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 0, f"FAIL AC-7: valid input replacement failed: {out}"
    stored = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert stored["feature_inputs"] == ["a.py", "z.py"]
    revision = stored["revision"]

    payload.write_text('["z.py", "a.py"]', encoding="utf-8")
    code, _out, _err = run_cli(
        [
            "feature",
            "inputs",
            "set",
            "--from-file",
            str(payload),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 0
    unchanged = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert unchanged["revision"] == revision

    payload.write_text('["plans"]', encoding="utf-8")
    before = state_path.read_bytes()
    code, _out, _err = run_cli(
        [
            "feature",
            "inputs",
            "set",
            "--from-file",
            str(payload),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 2 and state_path.read_bytes() == before

    monkeypatch.setattr("sys.stdin", io.StringIO('["src.py"]'))
    code, output, _error = run_cli(
        [
            "feature",
            "inputs",
            "set",
            "--from-file",
            "-",
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 0, output
    assert yaml.safe_load(state_path.read_text(encoding="utf-8"))["feature_inputs"] == [
        "src.py"
    ]

    for stream in (
        io.StringIO(""),
        io.TextIOWrapper(io.BytesIO(b"\xff"), encoding="utf-8"),
        io.StringIO("[" * 10_000 + "]" * 10_000),
    ):
        before = state_path.read_bytes()
        monkeypatch.setattr("sys.stdin", stream)
        code, _output, _error = run_cli(
            [
                "feature",
                "inputs",
                "set",
                "--from-file",
                "-",
                "--feature",
                "sample-feature",
                "--json",
            ]
        )
        assert code == 2 and state_path.read_bytes() == before


def test_ac07_feature_input_authoring_rejects_other_workspace_shapes(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(
        tmp_path,
        milestones=True,
        name="milestone-bearing",
    )
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["feature_inputs"] = ["legacy-inert.py"]
    _write_yaml(state_path, state)
    payload = tmp_path / "inputs-milestone-bearing.json"
    payload.write_text('["src.py"]', encoding="utf-8")
    before = state_path.read_bytes()
    monkeypatch.chdir(host)

    code, _output, _error = run_cli(
        [
            "feature",
            "inputs",
            "set",
            "--from-file",
            str(payload),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 2 and state_path.read_bytes() == before


def test_ac07_milestone_empty_union_fails_with_authoring_action(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path, tier=1)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["milestones"][0]["owns"] = []
    _write_yaml(state_path, state)
    monkeypatch.chdir(host)

    code, output, _error = run_cli(
        [
            "verify",
            "--scope",
            "acceptance",
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 3
    assert "heddle milestone edit <milestone-id> --from-file -" in output


@pytest.mark.parametrize("tier", [1, 3])
def test_ac02_ac06_done_milestone_repair_is_one_way(
    tier: int,
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path, tier=tier, name=f"host-{tier}")
    monkeypatch.chdir(host)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    milestone = state["milestones"][0]
    milestone["status"] = "done"
    milestone["owns"] = []
    milestone["verification"] = {"command": "   ", "expected": ""}
    _write_yaml(state_path, state)
    payload = tmp_path / f"repair-{tier}.yaml"
    payload.write_text(
        "owns:\n"
        "  - src.py\n"
        "verification:\n"
        "  command: python3 -c 'print(1)'\n"
        "  expected: pass\n",
        encoding="utf-8",
    )

    code, out, _err = run_cli(
        [
            "milestone",
            "edit",
            "m1",
            "--from-file",
            str(payload),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 0, f"FAIL AC-2/6: one-way repair failed: {out}"
    repaired = state_path.read_bytes()
    code, out, _err = run_cli(
        [
            "milestone",
            "edit",
            "m1",
            "--from-file",
            str(payload),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 3 and "milestone-out-of-sequence" in out
    assert state_path.read_bytes() == repaired


def test_ac02_ac06_done_milestone_ownership_can_only_expand(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path, name="ownership-expansion")
    monkeypatch.chdir(host)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["milestones"][0]["status"] = "done"
    _write_yaml(state_path, state)
    payload = tmp_path / "expand-ownership.yaml"
    payload.write_text(
        "owns:\n  - src.py\n  - tests/test_src.py\n",
        encoding="utf-8",
    )

    code, out, _err = run_cli(
        [
            "milestone",
            "edit",
            "m1",
            "--from-file",
            str(payload),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 0, f"FAIL AC-2/6: ownership expansion failed: {out}"
    expanded = state_path.read_bytes()
    payload.write_text("owns:\n  - tests/test_src.py\n", encoding="utf-8")

    code, out, _err = run_cli(
        [
            "milestone",
            "edit",
            "m1",
            "--from-file",
            str(payload),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 3 and "milestone-out-of-sequence" in out
    assert state_path.read_bytes() == expanded


@pytest.mark.parametrize("missing", ["verification", "command"])
def test_ac02_incompatible_milestone_refuses_without_a_write(
    missing: str,
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path, name=f"missing-{missing}")
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["schema"] = "heddle.state/v1"
    state.pop("feature_inputs")
    if missing == "verification":
        state["milestones"][0].pop("verification")
    else:
        state["milestones"][0]["verification"].pop("command")
    _write_yaml(state_path, state)
    before = state_path.read_bytes()
    monkeypatch.chdir(host)

    code, output, _error = run_cli(["migrate", "--json"])
    assert code == 2 and "usage" in output
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 3 and "workspace-invalid" in output
    assert "heddle.state/v1" in output and "fresh workspace" in output
    assert "heddle milestone edit" not in output
    assert state_path.read_bytes() == before


def test_ac05_ac06_content_staleness_blocks_then_rerun_allows_advance(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path)
    monkeypatch.chdir(host)
    run_cli(["verify", "--scope", "m1", "--feature", "sample-feature", "--json"])
    (host / "src.py").write_text("value = 2\n", encoding="utf-8")

    code, out, _err = run_cli(
        ["milestone", "advance", "--feature", "sample-feature", "--json"]
    )
    assert code == 3 and "verification-missing" in out
    assert "heddle verify --scope m1" in out

    run_cli(["verify", "--scope", "m1", "--feature", "sample-feature", "--json"])
    code, out, _err = run_cli(
        ["milestone", "advance", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, f"FAIL AC-6: fresh evidence must allow advance: {out}"


def test_ac06_advance_requires_current_scope_not_stale_done_scope(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    first = state["milestones"][0]
    first["status"] = "done"
    first["owns"] = ["first.py"]
    second = deepcopy(first)
    second.update(
        {
            "id": "m2",
            "title": "Current",
            "depends_on": ["m1"],
            "owns": ["second.py"],
            "status": "current",
        }
    )
    state["milestones"] = [first, second]
    _write_yaml(state_path, state)
    (host / "first.py").write_text("first = 1\n", encoding="utf-8")
    (host / "second.py").write_text("second = 1\n", encoding="utf-8")
    monkeypatch.chdir(host)

    for scope in ("m1", "m2"):
        code, output, _error = run_cli(
            ["verify", "--scope", scope, "--feature", "sample-feature", "--json"]
        )
        assert code == 0, output
    (host / "first.py").write_text("first = 2\n", encoding="utf-8")

    code, output, _error = run_cli(
        ["milestone", "advance", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    after = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert [milestone["status"] for milestone in after["milestones"]] == [
        "done",
        "done",
    ]


def test_ac06_advance_reports_deficient_done_milestone_declaration(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    first = state["milestones"][0]
    first["status"] = "done"
    first["owns"] = []
    second = deepcopy(first)
    second.update(
        {
            "id": "m2",
            "title": "Current",
            "depends_on": ["m1"],
            "owns": ["second.py"],
            "status": "current",
        }
    )
    state["milestones"] = [first, second]
    _write_yaml(state_path, state)
    (host / "second.py").write_text("second = 1\n", encoding="utf-8")
    monkeypatch.chdir(host)

    code, output, _error = run_cli(
        ["milestone", "advance", "--feature", "sample-feature", "--json"]
    )
    assert code == 3 and "workspace-invalid" in output
    assert "heddle milestone edit m1 --from-file -" in output


@pytest.mark.parametrize("command", ["", "   "])
def test_ac06_blank_milestone_command_names_recovery_action(
    command: str,
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["milestones"][0]["verification"]["command"] = command
    _write_yaml(state_path, state)
    monkeypatch.chdir(host)

    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 3
    assert "heddle milestone edit m1 --from-file -" in output


def test_ac03_unknown_milestone_scope_names_the_actual_problem(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path)
    monkeypatch.chdir(host)

    code, output, _error = run_cli(
        ["verify", "--scope", "m9", "--feature", "sample-feature", "--json"]
    )
    assert code == 3 and "does not name a milestone" in output
    assert "heddle milestone edit m9" not in output


def test_ac06_phase_exit_lists_all_stale_scopes_then_accepts_reruns(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path, tier=3)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["authorized_through"] = "peer-review"
    state["milestones"][0]["status"] = "done"
    _write_yaml(state_path, state)
    config_path = host / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gates"] = {"enabled": []}
    _write_yaml(config_path, config)
    monkeypatch.chdir(host)

    for scope in ("m1", "acceptance"):
        code, output, _error = run_cli(
            ["verify", "--scope", scope, "--feature", "sample-feature", "--json"]
        )
        assert code == 0, output
    (host / "src.py").write_text("value = 2\n", encoding="utf-8")
    code, output, _error = run_cli(
        ["verify", "--scope", "smoke", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output

    code, output, _error = run_cli(
        [
            "phase-exit",
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    envelope = envelope_tools.parse(output)
    assert code == 3 and envelope["error"]["code"] == "verification-missing"
    assert envelope.get("data") is None
    revision = yaml.safe_load(state_path.read_text())["revision"]
    assert [action["command"] for action in envelope["next_actions"]] == [
        f"heddle verify --scope m1 --expect-revision {revision} "
        "--feature sample-feature",
    ]

    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    code, output, _error = run_cli(
        [
            "phase-exit",
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 0, output
    assert yaml.safe_load(state_path.read_text(encoding="utf-8"))["stage"] == (
        "peer-review"
    )


@pytest.mark.parametrize("declared_live", [False, True])
def test_ac06_robustness_boundary_requires_only_declared_live(
    declared_live: bool,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(
        tmp_path,
        tier=3,
        name=f"live-{declared_live}",
    )
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["stage"] = "robustness"
    state["authorized_through"] = "complete"
    state["authorizations"] = [
        {
            "through": "complete",
            "source": "user",
            "at": "2026-09-04T12:00Z",
        }
    ]
    state["milestones"][0]["status"] = "done"
    state["commands"]["live_e2e_test"] = (
        "python3 -c 'print(1)'" if declared_live else ""
    )
    _write_yaml(state_path, state)
    config_path = host / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gates"] = {"enabled": []}
    _write_yaml(config_path, config)
    monkeypatch.chdir(host)

    for scope in ("m1", "acceptance", "smoke"):
        code, output, _error = run_cli(
            ["verify", "--scope", scope, "--feature", "sample-feature", "--json"]
        )
        assert code == 0, output
    code, output, _error = run_cli(
        ["phase-exit", "--feature", "sample-feature", "--json"]
    )
    if not declared_live:
        assert code == 0, output
        assert "heddle verify --scope live" not in output
        return

    envelope = envelope_tools.parse(output)
    assert code == 3 and envelope["error"]["code"] == "verification-missing"
    revision = yaml.safe_load(state_path.read_text())["revision"]
    assert [action["command"] for action in envelope["next_actions"]] == [
        f"heddle verify --scope live --expect-revision {revision} "
        "--feature sample-feature"
    ]
    code, output, _error = run_cli(
        ["verify", "--scope", "live", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    code, output, _error = run_cli(
        ["phase-exit", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    assert yaml.safe_load(state_path.read_text(encoding="utf-8"))["stage"] == (
        "complete"
    )


def test_ac07_empty_final_command_blocks_peer_review_boundary(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(
        tmp_path,
        milestones=True,
        name="empty-final-command",
    )
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["commands"]["acceptance_test"] = "   "
    state["stage"] = "peer-review"
    state["authorized_through"] = "robustness"
    state["milestones"][0]["status"] = "done"
    _write_yaml(state_path, state)
    config_path = host / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gates"] = {"enabled": []}
    _write_yaml(config_path, config)
    monkeypatch.chdir(host)
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    code, output, _error = run_cli(
        [
            "verify",
            "--scope",
            "acceptance",
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 3 and "workspace-invalid" in output
    assert "heddle commands set acceptance_test --command <shell-line>" in output


def test_ac05_driver_routes_current_content_staleness_to_one_rerun(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.contracts import operations as ops
    from heddle.contracts.result import NextAction
    from heddle.driver.loop import _next_command
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    host, _state_path = _v2_host(tmp_path)
    monkeypatch.chdir(host)
    code, _out, _err = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0
    (host / "src.py").write_text("value = 2\n", encoding="utf-8")

    config = load_project_config(host)
    snapshot = resolve_snapshot(config, "sample-feature")
    routed = _next_command(config, snapshot, None)
    assert routed == (
        NextAction(
            ops.CommandAction(
                ops.Verify(
                    "m1",
                    feature="sample-feature",
                    expect_revision=snapshot.state.revision,
                )
            ),
            "refresh m1 verification (content-stale)",
        ),
        None,
    )


def _tier3_close_host(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    live: bool,
) -> tuple[Path, Path]:
    host, state_path = _v2_host(tmp_path, tier=3, name=f"tier3-{live}")
    (state_path.parent / "brief.md").write_text(
        "# Brief\n\nExercise provenance-backed completion.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=host, check=True)
    subprocess.run(
        ["git", "config", "user.email", "provenance@example.test"],
        cwd=host,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Provenance Test"],
        cwd=host,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=host, check=True)
    subprocess.run(["git", "commit", "-qm", "feature birth"], cwd=host, check=True)

    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["stage"] = "complete"
    state["authorized_through"] = "complete"
    state["authorizations"] = [
        {
            "through": "complete",
            "source": "user",
            "at": "2026-09-04T12:00Z",
        }
    ]
    state["milestones"][0]["status"] = "done"
    state["commands"]["live_e2e_test"] = "python3 -c 'print(1)'" if live else ""
    _write_yaml(state_path, state)
    (host / "plans/sample-feature.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        "## Tooling fights\n\nNone.\n\n"
        "## Prompt gaps\n\nNone.\n\n"
        "## Validator false-positives\n\nNone.\n\n"
        "## Escalations that should have been policy-resolvable\n\nNone.\n",
        encoding="utf-8",
    )
    (host / "plans/sample-feature.decision-journal.md").write_text(
        "# Decision Journal\n", encoding="utf-8"
    )
    monkeypatch.chdir(host)
    scopes = (
        ("m1", "acceptance", "smoke", "live") if live else ("m1", "acceptance", "smoke")
    )
    for scope in scopes:
        code, output, _error = run_cli(
            ["verify", "--scope", scope, "--feature", "sample-feature", "--json"]
        )
        assert code == 0, output
    return host, state_path


def test_ac08_close_retains_authorizing_facts_and_archive_after_acceptance(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tarfile

    host, state_path = _tier3_close_host(run_cli, tmp_path, monkeypatch, live=False)
    spec_path = host / "docs/features/example/sample-feature.md"
    args = ["feature", "complete", "--feature", "sample-feature", "--json"]
    code, output, _ = run_cli(args)
    envelope = envelope_tools.parse(output)
    assert code == 0 and envelope["data"]["accepted"], output
    archive = host / "docs/gate-trajectories/.raw/sample-feature/completion.tar.gz"
    assert envelope["data"]["effects"]["archive"]["status"] == "complete"
    assert "lifecycle: complete" in spec_path.read_text()
    state = yaml.safe_load(state_path.read_text())
    indexes = state["completion"]["verification_indexes"]
    assert [state["verifications"][index]["scope"] for index in indexes] == [
        "acceptance",
        "smoke",
    ]
    with tarfile.open(archive) as retained:
        assert retained.extractfile("state.yaml").read() == state_path.read_bytes()
        assert retained.extractfile(state["verifications"][indexes[0]]["log"]).read()
    before = state_path.read_bytes(), archive.read_bytes()
    code, output, _ = run_cli(args)
    assert code == 0 and envelope_tools.parse(output)["data"]["wrote"] is False
    assert (state_path.read_bytes(), archive.read_bytes()) == before


@pytest.mark.parametrize("live", [False, True])
def test_ac08_tier3_close_binds_ordered_current_scope_indexes(
    live: bool, run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, state_path = _tier3_close_host(run_cli, tmp_path, monkeypatch, live=live)
    code, output, _ = run_cli(
        ["feature", "complete", "--feature", "sample-feature", "--json"]
    )
    assert code == 0 and envelope_tools.parse(output)["data"]["accepted"], output
    terminal = yaml.safe_load(state_path.read_text())
    scopes = ["acceptance", "smoke", *(["live"] if live else [])]
    indexes = terminal["completion"]["verification_indexes"]
    assert [terminal["verifications"][index]["scope"] for index in indexes] == scopes
    assert indexes == [
        max(i for i, f in enumerate(terminal["verifications"]) if f["scope"] == scope)
        for scope in scopes
    ]
    for surface in ("status", "orient"):
        code, output, _ = run_cli([surface, "--feature", "sample-feature", "--json"])
        result = envelope_tools.parse(output)
        assert code == 0 and result["data"]["accepted"], output
        assert result["data"]["blocking_conditions"] == []
        assert "verification_status" not in result["data"]
        assert result["next_actions"] == []


def test_ac08_archive_path_conflict_preserves_accepted_history(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, state_path = _tier3_close_host(run_cli, tmp_path, monkeypatch, live=False)
    spec = host / "docs/features/example/sample-feature.md"
    outside = tmp_path / "outside"
    outside.mkdir()
    from heddle.runtime import completion

    original = completion._publish_archive

    def collide(root, workspace, path, ledger):
        (host / "docs/gate-trajectories").mkdir(exist_ok=True)
        (host / "docs/gate-trajectories/.raw").symlink_to(
            outside, target_is_directory=True
        )
        return original(root, workspace, path, ledger)

    monkeypatch.setattr(completion, "_publish_archive", collide)
    code, output, _ = run_cli(
        ["feature", "complete", "--feature", "sample-feature", "--json"]
    )
    result = envelope_tools.parse(output)
    assert code == 4 and result["data"]["accepted"], output
    assert result["data"]["effects"]["archive"]["status"] == "conflict"
    assert yaml.safe_load(state_path.read_text())["completion"] is not None
    assert "lifecycle: complete" in spec.read_text()
    assert not list(outside.iterdir())
    assert (state_path.parent / "plan.md").exists()


def test_ac08_preacceptance_cas_failure_rechecks_coverage_on_retry(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.runtime import completion

    host, state_path = _tier3_close_host(run_cli, tmp_path, monkeypatch, live=False)
    spec = host / "docs/features/example/sample-feature.md"
    original = completion.commit_state
    before = state_path.read_bytes(), spec.read_bytes()
    args = ["feature", "complete", "--feature", "sample-feature", "--json"]

    def fail_commit(*args, **kwargs):
        raise completion.Conflict("simulated terminal CAS conflict")

    monkeypatch.setattr(completion, "commit_state", fail_commit)
    code, output, _ = run_cli(args)
    assert code == 5 and "simulated terminal CAS conflict" in output
    assert (state_path.read_bytes(), spec.read_bytes()) == before
    archive = host / "docs/gate-trajectories/.raw/sample-feature/completion.tar.gz"
    assert not archive.exists()
    monkeypatch.setattr(completion, "commit_state", original)
    outside = host / "outside.py"
    outside.write_text("unowned = True\n")
    sibling = host / "docs/features/example/unrelated.md"
    sibling.write_text("unowned docs change\n")
    code, output, _ = run_cli(args)
    assert code == 3 and "workspace-invalid" in output
    assert "outside.py" in output and "ownership" in output
    assert "docs/features/example/unrelated.md" in output
    outside.unlink()
    sibling.unlink()
    code, output, _ = run_cli(args)
    assert code == 0 and envelope_tools.parse(output)["data"]["accepted"], output
    assert archive.is_file()


def test_ac09_status_rows_are_ordered_and_actionable() -> None:
    from heddle.contracts.operations import CommandAction, Verify
    from heddle.kernel.verification import VerificationFreshness
    from heddle.runtime.verification import verification_status_payload

    rows = verification_status_payload(
        (
            VerificationFreshness("m1", "fresh", None, 0),
            VerificationFreshness(
                "acceptance",
                "content-stale",
                CommandAction(Verify("acceptance")),
                1,
            ),
        )
    )
    assert rows == [
        {
            "scope": "m1",
            "freshness": "fresh",
            "evidence_state": "current",
            "applicability": "required",
            "cause": None,
            "action": None,
        },
        {
            "scope": "acceptance",
            "freshness": "content-stale",
            "evidence_state": "stale",
            "applicability": "required",
            "cause": "relevant content changed",
            "action": "heddle verify --scope acceptance",
        },
    ]


def test_ac09_human_status_names_staleness_and_rerun(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, _state_path = _v2_host(tmp_path, tier=3)
    monkeypatch.chdir(host)
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    (host / "src.py").write_text("value = 2\n", encoding="utf-8")

    code, output, _error = run_cli(["status", "--feature", "sample-feature"])
    assert code == 0, output
    assert "verification m1: content-stale; rerun: heddle verify --scope m1" in output

    code, structured, _error = run_cli(
        ["status", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, structured
    expected = envelope_tools.parse(structured)["data"]["verification_status"]
    row = next(item for item in expected if item["scope"] == "m1")
    assert row["freshness"] == "content-stale"
    assert row["evidence_state"] == "stale"
    assert row["applicability"] == "required"
    assert row["cause"] == "relevant content changed"
    assert row["action"].startswith("heddle verify --scope m1 --expect-revision ")
    assert row["action"].endswith(" --feature sample-feature")
    code, structured, _error = run_cli(
        ["orient", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, structured
    assert envelope_tools.parse(structured)["data"]["verification_status"] == expected


def test_ac09_empty_directory_reports_staleness_and_verifies_current_definition(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, state_path = _v2_host(tmp_path, tier=3)
    source_dir = host / "src"
    source_dir.mkdir()
    sole_leaf = source_dir / "only.py"
    sole_leaf.write_text("value = 1\n", encoding="utf-8")
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["milestones"][0]["owns"] = ["src"]
    _write_yaml(state_path, state)
    subprocess.run(["git", "init", "-q"], cwd=host, check=True)
    subprocess.run(["git", "add", "."], cwd=host, check=True)
    monkeypatch.chdir(host)
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    sole_leaf.unlink()
    before = state_path.read_bytes()
    for command in ("status", "orient"):
        code, output, _error = run_cli(
            [command, "--feature", "sample-feature", "--json"]
        )
        envelope = envelope_tools.parse(output)
        assert code == 0 and envelope["ok"], envelope
        rows = envelope["data"]["verification_status"]
        row = next(item for item in rows if item["scope"] == "m1")
        assert row["freshness"] == "source-set-stale"
        assert row["evidence_state"] == "stale"
        assert row["applicability"] == "required"
        assert row["cause"] == "declared verification source set changed"
        assert row["changed_dependencies"] == ["src/only.py"]
        assert row["action"].startswith("heddle verify --scope m1 --expect-revision ")
        assert row["action"].endswith(" --feature sample-feature")
        assert state_path.read_bytes() == before
    code, output, _error = run_cli(
        ["verify", "--scope", "m1", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, output
    value = yaml.safe_load(state_path.read_text())
    fact = value["verifications"][-1]
    assert fact["exit_code"] == 0
    from heddle.runtime.verification import read_source_evidence

    evidence = read_source_evidence(state_path.parent, fact["evidence"]["before"])
    assert evidence.definition.declaration_paths == ("src",)
    assert evidence.definition.paths == ()
    assert fact["evidence"]["before"] == fact["evidence"]["after"]
    code, output, _error = run_cli(["status", "--feature", "sample-feature", "--json"])
    assert code == 0
    assert {
        "scope": "m1",
        "freshness": "fresh",
        "evidence_state": "current",
        "applicability": "required",
        "cause": None,
        "action": None,
    } in (envelope_tools.parse(output)["data"]["verification_status"])


def test_ac09_portfolio_status_skips_eager_verification_observation(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.runtime.verification_provenance_helpers import (
        convert_current_fixture_to_v8,
    )

    host, state_path = _v2_host(tmp_path)
    convert_current_fixture_to_v8(host, state_path)
    monkeypatch.chdir(host)

    def unexpected_observation(*_args, **_kwargs):
        raise AssertionError("portfolio must not inspect verification sources")

    monkeypatch.setattr(
        "heddle.runtime.status.assess_current_readiness", unexpected_observation
    )
    code, output, _error = run_cli(["status", "--all", "--json"])
    assert code == 0, output
    rows = envelope_tools.parse(output)["data"]["features"]
    assert len(rows) == 1 and "verification_status" not in rows[0]


@pytest.mark.parametrize(
    ("stage", "statuses", "live", "expected"),
    [
        ("implement", ("current", "todo"), False, ("m1",)),
        ("implement", ("done", "done"), False, ("m1", "m2")),
        ("implement", (), False, ()),
        ("peer-review", ("done", "done"), False, ("acceptance", "smoke")),
        ("robustness", ("done", "done"), False, ("acceptance", "smoke")),
        ("complete", ("done", "done"), True, ("acceptance", "smoke", "live")),
    ],
)
def test_ac09_required_status_scope_order(
    stage: str,
    statuses: tuple[str, ...],
    live: bool,
    expected: tuple[str, ...],
    tmp_path: Path,
) -> None:
    from heddle.kernel.state import parse_state_document
    from heddle.runtime.verification import required_verification_scopes

    _host, state_path = _v2_host(tmp_path)
    base = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    base["stage"] = stage
    base["authorized_through"] = stage
    base["commands"]["live_e2e_test"] = "python live.py" if live else ""
    template = base["milestones"][0]
    base["milestones"] = [
        {
            **deepcopy(template),
            "id": f"m{index + 1}",
            "depends_on": [],
        }
        for index in range(len(statuses))
    ]
    for milestone, status in zip(base["milestones"], statuses, strict=True):
        milestone["status"] = status
    base["verifications"] = []
    typed = parse_state_document(base, source=Path("state.yaml"))
    assert required_verification_scopes(typed) == expected


def test_ac09_canonical_documents_name_five_axes_and_close_order() -> None:
    paths = [
        REPO_ROOT / "docs/design/workflow-model.md",
        REPO_ROOT / "heddle/resources/implement.briefing.md",
        REPO_ROOT / "heddle/resources/peer-review.briefing.md",
        REPO_ROOT / "heddle/resources/robustness.briefing.md",
        REPO_ROOT / "heddle/resources/complete.briefing.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8").casefold()
        for anchor in (
            "content identity",
            "command adequacy",
            "semantic quality",
            "operational reliability",
            "tool behavior",
        ):
            assert anchor in text, f"FAIL AC-9: {path} lacks {anchor!r}"
    close_order_anchors = {
        paths[0]: (
            "heddle feature complete",
            "accepts one typed fact",
            (
                "only after local ledger validation and verified archival may "
                "cleanup remove"
            ),
            "may cleanup remove",
        ),
        paths[-1]: (
            "heddle feature complete",
            "one typed completion fact before any file effects",
            "git head",
            "pending/conflicting effects return accepted data",
        ),
    }
    for path, anchors in close_order_anchors.items():
        text = " ".join(path.read_text(encoding="utf-8").casefold().split())
        for anchor in anchors:
            assert anchor in text, f"FAIL W5/AC-9: {path} lacks {anchor!r}"
