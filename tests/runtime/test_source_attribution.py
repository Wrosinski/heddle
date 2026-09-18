from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from dataclasses import replace

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.kernel.project_config import KernelError, load_project_config
from heddle.kernel.state import read_state_file, validate_state_transition
from heddle.runtime.application import execute
from heddle.runtime.source_attribution import qualified_attribution_paths
from heddle.runtime.verification import (
    reconcile_current_source,
    workflow_control_paths,
)
from tests.runtime.verification_provenance_helpers import (
    arm_completion_boundary,
    make_current_host,
)


@pytest.fixture
def host(tmp_path, monkeypatch):
    root, state_path = make_current_host(tmp_path)
    (root / "origin.md").write_text("Other feature's accepted scope.\n")
    (root / "deleted.txt").write_text("retired by the other feature\n")
    for args in (
        ("init",),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
        ("add", "."),
        ("commit", "-qm", "baseline"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "outside.txt").write_text("other feature\n")
    (root / "another.txt").write_text("second other feature\n")
    monkeypatch.chdir(root)
    return root, state_path


def payload(*paths):
    return {
        "schema": "heddle.source-attribution-input/v1",
        "attributions": [
            {
                "paths": [path],
                "references": ["origin.md"],
                "reason": "Belongs to the independently accepted other feature.",
            }
            for path in paths
        ],
    }


def attribute(*paths, **kwargs):
    return execute(
        ops.AttributeSources(payload(*paths), feature="sample-feature", **kwargs)
    )


def coverage(root, state_path):
    state = read_state_file(state_path)
    controls = workflow_control_paths(load_project_config(root), state)
    return reconcile_current_source(
        root,
        state,
        "acceptance",
        baseline_probe=state_path.relative_to(root).as_posix(),
        runtime_owned_roots=controls.roots,
        excluded_paths=controls.exact,
    )


def test_native_batch_preserves_ownership_and_proof_and_is_idempotent(host):
    root, state_path = host
    before = yaml.safe_load(state_path.read_text())
    with pytest.raises(KernelError, match="incomplete final coverage"):
        coverage(root, state_path)
    result = attribute("outside.txt", "another.txt", dry_run=True)
    assert result.ok, result
    assert yaml.safe_load(state_path.read_text()) == before
    result = attribute("outside.txt", "another.txt", expect_revision=before["revision"])
    assert result.ok, result
    after = yaml.safe_load(state_path.read_text())
    for key in ("source_baseline", "milestones", "verifications", "feature_inputs"):
        assert after.get(key) == before.get(key)
    assert len(after["source_attributions"]) == 2
    assert coverage(root, state_path).status == "complete"
    revision = after["revision"]
    assert attribute("outside.txt", "another.txt", expect_revision=revision).ok
    assert yaml.safe_load(state_path.read_text()) == after
    result = attribute("outside.txt", expect_revision=revision - 1)
    assert not result.ok and result.error.code == "conflict"


@pytest.mark.parametrize("change", ["content", "mode", "evidence", "symlink"])
def test_changed_binding_blocks_until_fresh_explicit_attribution(host, change):
    root, state_path = host
    assert attribute("outside.txt", "another.txt").ok
    outside = root / "outside.txt"
    if change == "content":
        outside.write_text("later unrelated edit\n")
    elif change == "mode":
        outside.chmod(0o755)
    elif change == "evidence":
        (root / "origin.md").write_text("changed scope evidence\n")
    else:
        outside.unlink()
        outside.symlink_to("another.txt")
    with pytest.raises(KernelError, match="attribution.*(stale|changed)"):
        coverage(root, state_path)
    paths = ["outside.txt", "another.txt"]
    if change == "evidence":
        paths.append("origin.md")
    result = attribute(*paths)
    assert result.ok, result
    assert coverage(root, state_path).status == "complete"
    (root / "unclassified.txt").write_text("new work\n")
    with pytest.raises(KernelError, match="incomplete final coverage"):
        coverage(root, state_path)


def test_deletion_binding_and_later_ownership_do_not_hide_owned_changes(host):
    root, state_path = host
    (root / "deleted.txt").unlink()
    assert attribute("outside.txt", "another.txt", "deleted.txt").ok
    assert coverage(root, state_path).status == "complete"
    (root / "deleted.txt").write_text("restored\n")
    with pytest.raises(KernelError, match="stale"):
        coverage(root, state_path)
    state = read_state_file(state_path)
    expanded = replace(
        state,
        milestones=(replace(state.milestones[0], owns=("src.py", "deleted.txt")),),
    )
    assert qualified_attribution_paths(root, expanded) == ("another.txt", "outside.txt")
    assert expanded.verifications == ()


@pytest.mark.parametrize(
    "invalid",
    ["src.py", "unknown.txt", "../escape", ".", "outside.txt/child"],
)
def test_invalid_path_keeps_entire_batch_atomic(host, invalid):
    root, state_path = host
    (root / "src.py").write_text("owned change\n")
    before = state_path.read_bytes()
    result = attribute("another.txt", invalid)
    assert not result.ok
    assert state_path.read_bytes() == before


@pytest.mark.parametrize(
    "reference", ["missing.md", ".", "plans/sample-feature/state.yaml"]
)
def test_invalid_evidence_cannot_authorize_exclusion(host, reference):
    _root, state_path = host
    before = state_path.read_bytes()
    value = payload("outside.txt")
    value["attributions"][0]["references"] = [reference]
    result = execute(ops.AttributeSources(value, feature="sample-feature"))
    assert not result.ok
    assert state_path.read_bytes() == before


def test_cli_and_append_only_retention(host, run_cli, tmp_path):
    _root, state_path = host
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload("outside.txt", "another.txt")))
    code, out, _err = run_cli(
        [
            "feature",
            "sources",
            "attribute",
            "--from-file",
            str(source),
            "--feature",
            "sample-feature",
            "--json",
        ]
    )
    assert code == 0, out
    assert json.loads(out)["ok"]
    before = yaml.safe_load(state_path.read_text())
    for changed in ([], [dict(before["source_attributions"][0], reason="rewritten")]):
        after = deepcopy(before)
        after["source_attributions"] = changed
        with pytest.raises(KernelError, match="append-only"):
            validate_state_transition(before, after)
    tampered = deepcopy(before)
    tampered["source_attributions"][0]["source"]["source_sha256"] = "f" * 64
    state_path.write_text(yaml.safe_dump(tampered))
    with pytest.raises(KernelError):
        read_state_file(state_path)


def test_attribution_does_not_supply_missing_completion_verification(host):
    root, state_path = host
    arm_completion_boundary(root, state_path)
    result = attribute("outside.txt", "another.txt")
    assert result.ok, result
    assert coverage(root, state_path).status == "complete"
    result = execute(ops.FeatureComplete(feature="sample-feature", dry_run=True))
    assert not result.ok
    assert result.error.code == "verification-missing", result
    assert read_state_file(state_path).completion is None
