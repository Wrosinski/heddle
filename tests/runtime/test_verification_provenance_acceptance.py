"""
Composed acceptance contract for verification evidence provenance.

The acceptance lane stays deliberately compact: it crosses source observation,
fact production, freshness, and durable publication without duplicating every
edge case owned by the focused milestone tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.runtime.verification_provenance_helpers import make_current_host

pytestmark = pytest.mark.acceptance


def test_ac01_through_ac10_bound_attempt_to_retained_completion(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.io.source import observe_source
    from heddle.kernel.source_manifest import SourceDefinition
    from heddle.kernel.state import read_state_file
    from heddle.kernel.verification import (
        GateSourceInventory,
        SourceDeclaration,
        assess_verification,
        reconcile_source_coverage,
    )
    from heddle.runtime.verification import read_source_evidence

    host, state_path = make_current_host(tmp_path, milestones=True)
    monkeypatch.chdir(host)
    code, out, _err = run_cli(
        [
            "verify",
            "--scope",
            "m1",
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 0, f"FAIL AC-3/7: real milestone verification failed: {out}"
    state = read_state_file(state_path)
    fact = state.verifications[-1]
    before_evidence = read_source_evidence(state_path.parent, fact.evidence.before)
    after_evidence = read_source_evidence(state_path.parent, fact.evidence.after)
    assert fact.command == "python3 -c 'print(2)'"
    assert before_evidence.definition.kind == "milestone-owns"
    assert before_evidence.definition.paths == ("src.py",)
    assert fact.log.startswith("verification/") and "autopilot/" not in fact.log

    legacy = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    legacy["schema"] = "heddle.state/v1"
    legacy.pop("feature_inputs")
    for legacy_fact in legacy["verifications"]:
        legacy_fact.pop("evidence")
    from heddle.kernel.project_config import KernelError

    incompatible = tmp_path / "incompatible.yaml"
    incompatible.write_text(yaml.safe_dump(legacy))
    before = incompatible.read_bytes()
    with pytest.raises(KernelError) as error:
        read_state_file(incompatible)
    assert "heddle.state/v1" in error.value.message
    assert "fresh workspace" in error.value.hint
    assert incompatible.read_bytes() == before

    coverage = reconcile_source_coverage(
        SourceDeclaration("milestone-owns", ("src.py",)),
        GateSourceInventory(
            tracked_paths=("src.py",),
            untracked_nonignored_paths=(),
            changed_since_baseline=("src.py",),
            baseline_error=None,
            baseline_probe="plans/sample-feature/state.yaml",
            leaf_error=None,
        ),
        ("plans", "docs/verification-provenance"),
    )
    assert coverage.status == "complete" and not coverage.unresolved_paths

    definition = SourceDefinition(
        "milestone-owns",
        before_evidence.definition.declaration_paths,
        before_evidence.definition.paths,
    )
    current = observe_source(host, definition)
    assert (
        assess_verification(
            state.verifications,
            "m1",
            fact.command,
            before_evidence,
            after_evidence,
            current,
        ).status
        == "fresh"
    )

    code, status_out, _err = run_cli(
        ["status", "--feature", "sample-feature", "--json"]
    )
    status = json.loads(status_out)
    assert code == 0 and status["data"]["verification_status"] == [
        {
            "scope": "m1",
            "freshness": "fresh",
            "evidence_state": "current",
            "applicability": "required",
            "cause": None,
            "action": None,
        }
    ]
    code, advance_out, _err = run_cli(
        ["milestone", "advance", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, advance_out
    code, exit_out, _err = run_cli(
        ["phase-exit", "--feature", "sample-feature", "--json"]
    )
    assert code == 0, f"FAIL AC-6: fresh Tier-1 evidence must allow exit: {exit_out}"

    import tarfile

    from tests.runtime.verification_provenance_helpers import arm_completion_boundary

    arm_completion_boundary(host, state_path)
    for scope in ("acceptance", "smoke"):
        code, verify_out, _ = run_cli(
            ["verify", "--scope", scope, "--feature", "sample-feature", "--json"]
        )
        assert code == 0, verify_out
    code, out, _ = run_cli(
        ["feature", "complete", "--feature", "sample-feature", "--json"]
    )
    accepted = json.loads(out)
    assert code == 0 and accepted["data"]["accepted"], out
    state = read_state_file(state_path)
    assert state.completion is not None
    assert state.completion.verification_indexes == (1, 2)
    archive = host / "docs/gate-trajectories/.raw/sample-feature/completion.tar.gz"
    with tarfile.open(archive) as retained:
        assert retained.extractfile("state.yaml").read() == state_path.read_bytes()
        assert retained.extractfile(state.verifications[0].log).read()

    code, validate_out, _err = run_cli(["validate", "--json"])
    validate_payload = json.loads(validate_out)
    assert code in {0, 4} and validate_payload["ok"], (
        f"FAIL AC-10: composed v8 host must validate without fatal findings: "
        f"{validate_out}"
    )
