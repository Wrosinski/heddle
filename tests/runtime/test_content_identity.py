"""
W4 core real inventory, public verification and clean format refusal.
"""

from __future__ import annotations

import json

import pytest

from tests.content_identity_helpers import (
    current_source,
    git,
    git_host,
    require_current_state,
)
from tests.operational_model_helpers import FEATURE, application, read, write
from tests.tiering_review_helpers import (
    current_host,
    provider_transport,
    review_content,
)

pytestmark = pytest.mark.acceptance


def test_ac2_exact_ignored_and_never_tracked_missing(tmp_path):
    root = git_host(tmp_path / "host")
    (root / ".gitignore").write_text("ignored\n")
    (root / "ignored").write_bytes(b"one")
    declared, first = current_source(root, ("ignored", "never-added"))
    assert declared.paths == ("ignored", "never-added"), (
        "FAIL AC-2: exact ignored and never-tracked missing declarations were dropped"
    )
    assert declared.declaration_paths == ("ignored", "never-added")
    (root / "ignored").write_bytes(b"two")
    _, changed = current_source(root, ("ignored", "never-added"))
    assert first.source_sha256 != changed.source_sha256
    (root / "never-added").write_bytes(b"new")
    assert current_source(root, ("ignored", "never-added"))[1] != changed


def test_ac2_directory_deletion_is_commit_stable(tmp_path):
    root = git_host(tmp_path / "host")
    (root / "src").mkdir()
    (root / "src/a.py").write_bytes(b"A")
    (root / "src/b.py").write_bytes(b"B")
    git(root, "add", "src")
    git(root, "commit", "-qm", "two leaves")
    _, original = current_source(root, ("src",))
    (root / "src/a.py").unlink()
    declared, removed = current_source(root, ("src",))
    assert declared.paths == ("src/b.py",), (
        "FAIL AC-2: a deleted directory descendant still enters working identity"
    )
    assert removed.source_sha256 != original.source_sha256
    exact, missing = current_source(root, ("src/a.py",))
    assert exact.paths == ("src/a.py",)
    git(root, "add", "-u", "src")
    git(root, "commit", "-qm", "delete a")
    assert current_source(root, ("src",))[1] == removed
    assert current_source(root, ("src/a.py",))[1] == missing


def test_ac2_empty_directory_missing_root_and_generated_children(tmp_path):
    root = git_host(tmp_path / "host")
    (root / ".gitignore").write_text("*.generated\n")
    (root / "src").mkdir()
    declared, empty = current_source(root, ("src",))
    assert declared.paths == ()
    assert declared.declaration_paths == ("src",)
    (root / "src/ignored.generated").write_bytes(b"generated")
    assert current_source(root, ("src",))[1] == empty
    (root / "src/new.py").write_bytes(b"eligible")
    added_definition, added = current_source(root, ("src",))
    assert added_definition.paths == ("src/new.py",)
    assert added.source_sha256 != empty.source_sha256
    (root / "src/new.py").unlink()
    assert current_source(root, ("src",))[1] == empty
    (root / "src/ignored.generated").unlink()
    (root / "src").rmdir()
    assert current_source(root, ("src",))[1].source_sha256 != empty.source_sha256


def test_ac2_same_selected_leaves_do_not_erase_declaration_identity(tmp_path):
    root = git_host(tmp_path / "host")
    (root / "src").mkdir()
    (root / "src/a.py").write_bytes(b"A")
    directory, first = current_source(root, ("src",))
    exact, second = current_source(root, ("src/a.py",))
    assert directory.paths == exact.paths == ("src/a.py",)
    assert first.source_sha256 != second.source_sha256, (
        "FAIL AC-2: directory and exact declarations collapse into one identity"
    )
    assert current_source(root, ("src/",))[1] == first


@pytest.mark.parametrize("outcome", ["stable", "failed", "unstable", "restored"])
def test_ac3_real_verify_preserves_process_and_sample_truth(
    tmp_path, monkeypatch, outcome
):
    require_current_state()
    app, ops = application()
    host, path = current_host(tmp_path, monkeypatch, stage="implement")
    commands = {
        "stable": "python3 -c 'print(17)'",
        "failed": "python3 -c 'print(23); raise SystemExit(7)'",
        "unstable": (
            "python3 -c 'from pathlib import Path; "
            'Path("src/example.py").write_bytes(b"CHANGED")\''
        ),
        "restored": (
            "python3 -c 'from pathlib import Path; "
            'p=Path("src/example.py"); old=p.read_bytes(); '
            'p.write_bytes(b"TRANSIENT"); p.write_bytes(old)\''
        ),
    }
    state = read(path)
    state["milestones"][0]["verification"]["command"] = commands[outcome]
    write(path, state)
    monkeypatch.chdir(host)
    result = app.execute(ops.Verify(feature=FEATURE, scope="m1"))
    if outcome in {"failed", "unstable"}:
        assert not result.ok and result.error is not None, result
        assert result.error.code == "verification-failed"
        assert result.error.details["status"] == outcome
        assert result.error.details["recorded"] is True
    else:
        assert result.ok, result
    fact = read(path)["verifications"][-1]
    assert fact["exit_code"] == (7 if outcome == "failed" else 0)
    evidence = fact["evidence"]
    assert evidence["schema"] == "heddle.verification-evidence/v1"
    assert (
        evidence["before"]["source_sha256"] != evidence["after"]["source_sha256"]
    ) is (outcome == "unstable")
    assert (path.parent / fact["log"]).is_file()
    assert "-a" in fact["log"]
    if outcome in {"stable", "failed"}:
        assert (
            str(17 if outcome == "stable" else 23)
            in (path.parent / fact["log"]).read_text()
        )
    # Restoring between samples is deliberately indistinguishable from stability.
    if outcome == "restored":
        assert (host / "src/example.py").read_bytes() == b"VALUE = 7\n"


@pytest.mark.parametrize(
    "operation", ["status", "orient", "verify", "run-gate", "phase-exit"]
)
def test_ac4_old_state_refuses_public_entrypoints_before_effects(
    tmp_path, monkeypatch, operation
):
    require_current_state()
    app, ops = application()
    host, path = current_host(tmp_path, monkeypatch, stage="implement")
    monkeypatch.chdir(host)
    calls = provider_transport(monkeypatch, review_content("milestone-review"))
    assert app.execute(ops.Status(feature=FEATURE)).ok
    state = read(path)
    state["schema"] = "heddle.state/v4"
    write(path, state)
    before = {
        p.relative_to(host): p.read_bytes()
        for p in path.parent.rglob("*")
        if p.is_file()
    }
    requested = {
        "status": ops.Status(feature=FEATURE),
        "orient": ops.Orient(feature=FEATURE),
        "verify": ops.Verify(feature=FEATURE, scope="m1"),
        "run-gate": ops.RunGate("milestone-review", feature=FEATURE, cli="codex"),
        "phase-exit": ops.PhaseExit(feature=FEATURE),
    }[operation]
    rejected = app.execute(requested)
    assert not rejected.ok and rejected.error.code == "workspace-invalid", rejected
    assert "v4" in rejected.error.message
    assert "fresh" in rejected.error.hint.lower()
    assert calls == []
    assert {
        p.relative_to(host): p.read_bytes()
        for p in path.parent.rglob("*")
        if p.is_file()
    } == before


@pytest.mark.parametrize("evidence", ["retired-reference", "null"])
def test_ac4_old_or_unbound_evidence_cannot_authorize_current_state(
    tmp_path, monkeypatch, evidence
):
    require_current_state()
    app, ops = application()
    host, path = current_host(tmp_path, monkeypatch, stage="implement")
    monkeypatch.chdir(host)
    assert app.execute(ops.Verify(feature=FEATURE, scope="m1")).ok
    assert app.execute(ops.Status(feature=FEATURE)).ok
    state = read(path)
    if evidence == "null":
        state["verifications"][-1]["evidence"] = None
    else:
        state["verifications"][-1]["evidence"]["before"]["schema"] = (
            "heddle.source-evidence-ref/v0"
        )
    write(path, state)
    before = path.read_bytes()
    refused = app.execute(ops.Status(feature=FEATURE))
    assert not refused.ok and refused.error.code == "workspace-invalid"
    assert "evidence" in (refused.error.message + refused.error.hint).lower()
    assert path.read_bytes() == before


@pytest.mark.toolchain
def test_ac4_ac14_installed_current_start_and_old_identity_refusal(tmp_path):
    from tests.operational_model_helpers import fresh_host
    from tests.runtime.wheel_harness import (
        build_installed_wheel,
        parse_envelope,
        snapshot_tree,
    )
    from tests.tiering_helpers import prepare_input, wire_policy

    require_current_state()
    installed = build_installed_wheel(tmp_path / "installed")
    host, _ = fresh_host(tmp_path, seed=False)
    feature = "packaged-content-example"
    (host / "brief.md").write_text("# Research\nOne packaged current-format feature.\n")
    prepared = tmp_path / "prepare.json"
    prepared.write_text(json.dumps(prepare_input()))
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(wire_policy()))
    for command in (
        ("prepare", feature, "--area", "runtime", "--from-file", str(prepared)),
        ("policy", feature, "--from-file", str(policy)),
    ):
        result = installed.run("feature", *command, "--json", cwd=host)
        assert result.returncode == 0 and parse_envelope(result)["ok"]
    created = installed.run(
        "feature",
        "start",
        feature,
        "--json",
        cwd=host,
    )
    assert created.returncode == 0 and parse_envelope(created)["ok"]
    path = host / f"plans/{feature}/state.yaml"
    state = read(path)
    assert state["schema"] == "heddle.state/v10" and "tier" not in state
    for command in ("status", "orient", "kickoff"):
        result = installed.run(command, "--feature", feature, "--json", cwd=host)
        assert result.returncode == 0 and parse_envelope(result)["ok"]
    state["schema"] = "heddle.state/v4"
    write(path, state)
    before = snapshot_tree(host)
    for command in (
        ("status",),
        ("orient",),
        ("kickoff",),
        ("show-prompt", "milestone-review"),
        ("run-gate", "milestone-review"),
        ("verify", "--scope", "feature"),
        ("phase-exit",),
        ("drive",),
    ):
        result = installed.run(*command, "--feature", feature, "--json", cwd=host)
        envelope = parse_envelope(result)
        assert (
            result.returncode == 3 and envelope["error"]["code"] == "workspace-invalid"
        )
        assert "v4" in envelope["error"]["message"]
        assert snapshot_tree(host) == before
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()
