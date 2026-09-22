"""AC-1/2/7 admission boundaries; disposable local hosts, no full lifecycle."""

import io
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import asdict

import pytest
import yaml

from tests.tiering_helpers import (
    ASTRA,
    FABLE,
    FEATURE,
    axes,
    blank_host,
    confirmed,
    entry,
    invoke,
    prepare_input,
    prepared,
    snapshot,
    wire_policy,
)


@pytest.fixture
def host(tmp_path, monkeypatch):
    return blank_host(tmp_path, monkeypatch)


def intake(host):
    return host / ".heddle" / "intake" / f"{FEATURE}.yaml"


def formal(host):
    return host / "plans" / FEATURE / "state.yaml"


def git_output(host, *args):
    return subprocess.run(
        ["git", *args],
        cwd=host,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_local_history_start_freezes_source_baseline(host):
    """AC-1 red discriminator: admission binds the current source commit."""
    from heddle.runtime import intake as intake_runtime

    confirmed(host)
    confirmed_policy = yaml.safe_load(intake(host).read_text())["feature_policy"]
    baseline = git_output(host, "rev-parse", "--verify", "HEAD^{commit}")

    started = invoke("FeatureStart", slug=FEATURE)

    assert started.ok, started.to_envelope()
    intake_value = yaml.safe_load(intake(host).read_text())
    state_value = yaml.safe_load(formal(host).read_text())
    expected = {"kind": "git-commit", "oid": baseline}
    assert intake_value["schema"] == "heddle.intake/v2", (
        "FAIL local-history AC-1: new admission did not upgrade intake to v2"
    )
    assert state_value["schema"] == "heddle.state/v10"
    assert intake_value["create_manifest"]["source_baseline"] == expected
    assert state_value["source_baseline"] == expected
    assert started.data["source_baseline"] == expected
    assert state_value["feature_policy"] == confirmed_policy
    assert state_value["intake"] == {
        "path": intake(host).relative_to(host).as_posix(),
        "sha256": intake_runtime.intake_digest(intake_value),
    }


def test_local_history_start_dry_run_observes_baseline_without_effects(host):
    """AC-2 red discriminator: dry-run reports the candidate and writes nothing."""
    confirmed(host)
    baseline = git_output(host, "rev-parse", "--verify", "HEAD^{commit}")
    before = snapshot(host)

    preview = invoke("FeatureStart", slug=FEATURE, dry_run=True)

    assert preview.ok, preview.to_envelope()
    assert preview.data["source_baseline"] == {
        "kind": "git-commit",
        "oid": baseline,
    }
    assert snapshot(host) == before


def test_local_history_start_revision_conflict_precedes_baseline_effects(host):
    """Survivor pin for AC-2: stale admission CAS remains effect-free."""
    confirmed(host)
    revision = yaml.safe_load(intake(host).read_text())["revision"]
    before = snapshot(host)

    refused = invoke("FeatureStart", slug=FEATURE, expect_revision=revision + 1)

    assert not refused.ok and refused.error.code == "conflict"
    assert snapshot(host) == before


def test_local_history_start_refuses_unborn_head_without_effects(host):
    """AC-2 red discriminator: no source commit means no admission effects."""
    confirmed(host)
    branch = git_output(host, "symbolic-ref", "-q", "HEAD")
    subprocess.run(["git", "update-ref", "-d", branch], cwd=host, check=True)
    before = snapshot(host)

    refused = invoke("FeatureStart", slug=FEATURE)

    assert not refused.ok
    assert "initial source commit" in (refused.error.message + refused.error.hint)
    assert snapshot(host) == before
    assert yaml.safe_load(intake(host).read_text())["create_manifest"] is None


def test_local_history_interrupted_admission_reuses_frozen_baseline(host, monkeypatch):
    """AC-2 red discriminator: retry cannot recapture a moving HEAD."""
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import feature_start

    confirmed(host)
    baseline = git_output(host, "rev-parse", "--verify", "HEAD^{commit}")
    original = feature_start.create_state

    def interrupted(*_args, **_kwargs):
        raise KernelError(
            code="workspace-invalid", message="injected interruption", hint="retry"
        )

    monkeypatch.setattr(feature_start, "create_state", interrupted)
    assert not invoke("FeatureStart", slug=FEATURE).ok
    manifest = yaml.safe_load(intake(host).read_text())["create_manifest"]
    assert manifest["source_baseline"]["oid"] == baseline
    (host / "src/after-admission.py").write_text("VALUE = 2\n")
    git_output(host, "add", "src/after-admission.py")
    git_output(host, "commit", "-m", "advance source after interrupted admission")
    assert git_output(host, "rev-parse", "HEAD") != baseline

    monkeypatch.setattr(feature_start, "create_state", original)
    recovered = invoke("FeatureStart", slug=FEATURE)

    assert recovered.ok, recovered.to_envelope()
    assert (
        yaml.safe_load(formal(host).read_text())["source_baseline"]["oid"] == baseline
    )


def test_local_history_v1_interrupted_manifest_refuses_without_guessing(
    host, monkeypatch
):
    """AC-2/AC-8 red discriminator: a v1 partial admission is not upgraded."""
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import feature_start

    confirmed(host)
    original = feature_start.create_state

    def interrupted(*_args, **_kwargs):
        raise KernelError(
            code="workspace-invalid", message="injected interruption", hint="retry"
        )

    monkeypatch.setattr(feature_start, "create_state", interrupted)
    assert not invoke("FeatureStart", slug=FEATURE).ok
    value = yaml.safe_load(intake(host).read_text())
    value["schema"] = "heddle.intake/v1"
    intake(host).write_text(yaml.safe_dump(value, sort_keys=False))
    before = snapshot(host)
    monkeypatch.setattr(feature_start, "create_state", original)

    refused = invoke("FeatureStart", slug=FEATURE)

    assert not refused.ok
    assert "v1" in (refused.error.message + refused.error.hint)
    assert snapshot(host) == before


def test_local_history_v1_admission_and_policy_status_compatibility(host):
    """AC-2/AC-8 red discriminator: safe v1 pre-manifest intake upgrades once."""
    confirmed(host)
    value = yaml.safe_load(intake(host).read_text())
    value["schema"] = "heddle.intake/v1"
    assert value["create_manifest"] is None
    intake(host).write_text(yaml.safe_dump(value, sort_keys=False))
    revised = wire_policy(
        revision=2, overrides={"spec-review": entry("spec-review", limit=7)}
    )
    amended = invoke(
        "FeaturePolicy",
        slug=FEATURE,
        payload=revised,
        expect_revision=value["revision"],
    )
    assert amended.ok, amended.to_envelope()
    assert amended.next_actions

    started = invoke("FeatureStart", slug=FEATURE)

    assert started.ok, started.to_envelope()
    observed = invoke("Status", feature=FEATURE)
    assert observed.ok, observed.to_envelope()
    assert observed.data["feature"] == FEATURE
    assert yaml.safe_load(intake(host).read_text())["schema"] == "heddle.intake/v2"
    assert yaml.safe_load(formal(host).read_text())["schema"] == "heddle.state/v10"


def test_local_history_v9_state_remains_v9_across_policy_write(host):
    """Survivor pin for AC-8 paired with the unsafe-v1 red discriminator."""
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    value = yaml.safe_load(formal(host).read_text())
    value["schema"] = "heddle.state/v9"
    value.pop("source_baseline", None)
    formal(host).write_text(yaml.safe_dump(value, sort_keys=False))
    revised = wire_policy(
        revision=2, overrides={"spec-review": entry("spec-review", limit=7)}
    )

    amended = invoke("FeaturePolicy", slug=FEATURE, payload=revised)

    assert amended.ok, amended.to_envelope()
    persisted = yaml.safe_load(formal(host).read_text())
    assert persisted["schema"] == "heddle.state/v9"
    assert "source_baseline" not in persisted


@pytest.mark.parametrize("schema", ["heddle.state/v8", "heddle.state/v99"])
def test_local_history_unsupported_state_versions_refuse_without_rewrite(host, schema):
    """Survivor pin for AC-8: unsupported history is preserved and refused."""
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    value = yaml.safe_load(formal(host).read_text())
    value["schema"] = schema
    formal(host).write_text(yaml.safe_dump(value, sort_keys=False))
    before = snapshot(host)

    refused = invoke("Status", feature=FEATURE)

    assert not refused.ok
    assert "schema" in (refused.error.message + refused.error.hint).lower()
    assert snapshot(host) == before


def test_local_history_does_not_change_init_footprint(tmp_path, monkeypatch):
    """Survivor pin for AC-9: init still leaves host ignore policy untouched."""
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    root = tmp_path / "fresh-init"
    root.mkdir()
    monkeypatch.chdir(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    before = snapshot(root)

    preview = execute(ops.Init(dry_run=True))

    assert preview.ok, preview.to_envelope()
    assert ".gitignore" not in {row["path"] for row in preview.data["targets"]}
    assert snapshot(root) == before
    applied = execute(ops.Init())
    assert applied.ok, applied.to_envelope()
    assert not (root / ".gitignore").exists()
    assert git_output(root, "status", "--porcelain", "--untracked-files=no") == ""
    assert git_output(root, "for-each-ref", "--format=%(refname):%(objectname)") == ""


def test_ac1_empty_host_doctor_routes_to_research_before_feature_creation(host):
    before = snapshot(host)
    observed = invoke("Doctor")
    assert observed.ok, observed.to_envelope()
    assert any("heddle feature prepare" in row.command for row in observed.next_actions)
    assert all("--tier" not in row.command for row in observed.next_actions)
    assert snapshot(host) == before


@pytest.mark.parametrize("complexity", ["low", "high"])
def test_ac12_milestone_cli_accepts_current_complexity_without_hours(
    host, monkeypatch, run_cli, complexity
):
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    payload = {
        "title": "Concrete owned part",
        "complexity": complexity,
        "verification": {"command": "python3 tests/check.py", "expected": "pass"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(yaml.safe_dump(payload)))
    code, out, _err = run_cli(
        [
            "milestone",
            "add",
            "--from-file",
            "-",
            "--feature",
            FEATURE,
            "--json",
        ]
    )
    assert code == 0, out
    milestone = json.loads(out)["data"]["milestone"]
    assert milestone["complexity"] == complexity and "estimated_hours" not in milestone
    before = formal(host).read_bytes()
    monkeypatch.setattr(sys, "stdin", io.StringIO("complexity: medium\n"))
    code, out, _err = run_cli(
        [
            "milestone",
            "edit",
            "m1",
            "--from-file",
            "-",
            "--feature",
            FEATURE,
            "--json",
        ]
    )
    assert code != 0 and "complexity" in json.loads(out)["error"]["message"]
    assert formal(host).read_bytes() == before


def test_ac1_direct_route_creates_no_formal_documents_or_gate_authority(host):
    prepared(host, route="direct")
    assert not formal(host).exists()
    assert not (host / "plans" / FEATURE).exists()
    assert not (host / "docs/features/runtime" / f"{FEATURE}.md").exists()
    before = snapshot(host)
    refused = invoke("FeatureStart", slug=FEATURE)
    assert not refused.ok
    assert "direct" in refused.error.message.lower()
    assert snapshot(host) == before


def test_ac1_unconfirmed_heddle_intake_cannot_publish_documents(host):
    prepared(host)
    before = snapshot(host)
    result = invoke("FeatureStart", slug=FEATURE)
    assert not result.ok
    assert "confirm" in (result.error.message + result.error.hint).lower()
    assert snapshot(host) == before


def test_ac1_confirmed_admission_transfers_policy_once_and_exact_retry_is_readback(
    host,
):
    selected = {
        "spec-review": entry("spec-review", primary=FABLE, limit=7),
        "plan-review": entry("plan-review", mode="off", limit=None, minimum_rounds=0),
    }
    confirmed(host, overrides=selected)
    before_start = intake(host).read_bytes()
    confirmed_policy = yaml.safe_load(before_start)["feature_policy"]
    result = invoke("FeatureStart", slug=FEATURE)
    assert result.ok, result.to_envelope()
    assert result.data["validated"] is True
    value = yaml.safe_load(formal(host).read_text())
    assert value["schema"] == "heddle.state/v10"
    assert "tier" not in value
    expected = json.loads(json.dumps(wire_policy(overrides=selected)))
    assert value["feature_policy"] == confirmed_policy == expected
    assert result.data["effective_policy"]["entries"] == confirmed_policy["entries"]
    assert (host / "docs/features/runtime" / f"{FEATURE}.md").is_file()
    assert (host / "plans" / FEATURE / "plan.md").is_file()
    assert before_start  # Confirmation existed before formal state.
    after = snapshot(host)
    replay = invoke("FeatureStart", slug=FEATURE)
    assert replay.ok, replay.to_envelope()
    assert replay.data["validated"] is True
    assert snapshot(host) == after


def test_ac1_admission_readback_does_not_hide_or_repair_missing_documents(host):
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    spec = host / "docs/features/runtime" / f"{FEATURE}.md"
    spec.unlink()
    before = snapshot(host)
    replay = invoke("FeatureStart", slug=FEATURE)
    assert not replay.ok and "spec" in replay.error.message
    assert snapshot(host) == before


def test_admission_accepts_conventional_brief_research_reference(host):
    reference = f"plans/.briefs/{FEATURE}.md"
    research = host / reference
    research.parent.mkdir(parents=True)
    research.write_text("# Research\nOne declared behavior.\n")
    research_bytes = research.read_bytes()
    payload = prepare_input()
    payload["research"]["reference"] = reference
    prepared_result = invoke(
        "FeaturePrepare", slug=FEATURE, area="runtime", payload=payload
    )
    assert prepared_result.ok, prepared_result.to_envelope()
    policy_result = invoke(
        "FeaturePolicy",
        slug=FEATURE,
        payload=wire_policy(),
        expect_revision=prepared_result.data["revision"],
    )
    assert policy_result.ok, policy_result.to_envelope()

    started = invoke("FeatureStart", slug=FEATURE)

    assert started.ok, started.to_envelope()
    assert started.data["validated"] is True
    for operation in ("Validate", "Orient"):
        result = invoke(operation, feature=FEATURE)
        assert result.ok, result.to_envelope()
    assert research.read_bytes() == research_bytes
    admitted = snapshot(host)
    replay = invoke("FeatureStart", slug=FEATURE)
    assert replay.ok, replay.to_envelope()
    assert replay.data["validated"] is True
    assert snapshot(host) == admitted


def test_ac1_empty_current_feature_reads_off_and_future_assignments_without_scope(
    host,
):
    from heddle.contracts import operations as ops

    confirmed(
        host,
        overrides={
            "spec-review": entry(
                "spec-review", mode="off", limit=None, minimum_rounds=0
            ),
            "plan-review": entry(
                "plan-review", mode="off", limit=None, minimum_rounds=0
            ),
            "milestone-review": entry(
                "milestone-review", mode="off", limit=None, minimum_rounds=0
            ),
        },
    )
    assert invoke("FeatureStart", slug=FEATURE).ok
    before = snapshot(host)

    for operation in (ops.Status(feature=FEATURE), ops.Orient(feature=FEATURE)):
        observed = invoke(type(operation).__name__, feature=FEATURE)
        assert observed.ok, observed.to_envelope()
        rows = observed.data["review_closure"]["assignments"]
        assert next(row for row in rows if row["role"] == "spec-review")["closed"]
        assert not next(
            row for row in rows if row["role"] == "review-test-scaffolding"
        )["closed"]
        assert observed.next_actions

    assert snapshot(host) == before


@pytest.mark.parametrize("target", ["spec", "plan", "state", "symlink"])
def test_ac1_admission_never_adopts_or_clobbers_unknown_existing_paths(host, target):
    confirmed(host)
    paths = {
        "spec": host / "docs/features/runtime" / f"{FEATURE}.md",
        "plan": host / "plans" / FEATURE / "plan.md",
        "state": formal(host),
        "symlink": host / "docs/features/runtime" / f"{FEATURE}.md",
    }
    path = paths[target]
    path.parent.mkdir(parents=True, exist_ok=True)
    if target == "symlink":
        path.symlink_to(host / "brief.md")
    else:
        path.write_text("User-owned content, never admission evidence.\n")
    before = snapshot(host)
    result = invoke("FeatureStart", slug=FEATURE)
    assert not result.ok
    assert snapshot(host) == before
    assert result.error.hint


def test_ac1_changed_research_refuses_old_confirmation_without_partial_admission(host):
    confirmed(host)
    (host / "brief.md").write_text("# Research\nA materially different contract.\n")
    before = snapshot(host)
    result = invoke("FeatureStart", slug=FEATURE)
    assert not result.ok
    assert "research" in (result.error.message + result.error.hint).lower()
    assert snapshot(host) == before


@pytest.mark.parametrize("tamper", [False, True])
def test_ac1_interrupted_state_publication_recovers_only_exact_manifest_owned_docs(
    host, monkeypatch, tamper
):
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import feature_start

    confirmed(host)
    original = feature_start.create_state

    def interrupted(*_args, **_kwargs):
        raise KernelError(
            code="workspace-invalid",
            message="injected state publication failure",
            hint="retry the same confirmed admission",
        )

    # One effect fails after document creation. Policy, manifest, rendering and
    # retry qualification remain real; no service result is substituted.
    monkeypatch.setattr(feature_start, "create_state", interrupted)
    failed = invoke("FeatureStart", slug=FEATURE)
    assert not failed.ok
    assert not formal(host).exists()
    spec = host / "docs/features/runtime" / f"{FEATURE}.md"
    assert spec.is_file()
    authored = spec.read_bytes()
    if tamper:
        spec.write_text("User edited the interrupted document.\n")
    before_retry = snapshot(host)
    monkeypatch.setattr(feature_start, "create_state", original)
    retried = invoke("FeatureStart", slug=FEATURE)
    if tamper:
        assert not retried.ok
        assert snapshot(host) == before_retry
    else:
        assert retried.ok, retried.to_envelope()
        assert spec.read_bytes() == authored
        after = snapshot(host)
        assert invoke("FeatureStart", slug=FEATURE).ok
        assert snapshot(host) == after


@pytest.mark.parametrize("filename", ["plan.md.interrupted.tmp", "operator-note.txt"])
def test_ac1_interrupted_admission_unknown_path_hint_preserves_before_retry(
    host, monkeypatch, filename
):
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import feature_start

    confirmed(host)
    original = feature_start.create_state

    def interrupted(*_args, **_kwargs):
        raise KernelError("workspace-invalid", "injected interruption", "retry")

    monkeypatch.setattr(feature_start, "create_state", interrupted)
    assert not invoke("FeatureStart", slug=FEATURE).ok
    assert not formal(host).exists()
    unknown = formal(host).parent / filename
    unknown.write_text("Unfamiliar content must survive.\n")
    unknown.chmod(0o640)
    monkeypatch.setattr(feature_start, "create_state", original)
    before = snapshot(host)

    for dry_run in (True, False):
        refused = invoke("FeatureStart", slug=FEATURE, dry_run=dry_run)
        assert not refused.ok and refused.error.code == "workspace-invalid"
        assert unknown.relative_to(host).as_posix() in refused.error.message
        assert "preserve" in refused.error.hint
        assert "outside the workspace" in refused.error.hint
        assert f"heddle feature start {FEATURE}" in refused.error.hint
        assert "feature prepare" not in refused.error.hint
        assert snapshot(host) == before

    # The operator preserves the explicitly identified obstruction outside the
    # workspace. Runtime never treats a .tmp suffix as disposal authority.
    preserved = host / filename
    unknown.rename(preserved)
    retried = invoke("FeatureStart", slug=FEATURE)
    assert retried.ok, retried.to_envelope()
    assert preserved.read_text() == "Unfamiliar content must survive.\n"
    assert preserved.stat().st_mode & 0o777 == 0o640


def test_ac1_prepare_dry_run_is_nonmutating_and_exact_choice_reuse_needs_no_reapproval(
    host,
):
    (host / "brief.md").write_text("# Research\nOne declared behavior.\n")
    before = snapshot(host)
    preview = invoke(
        "FeaturePrepare",
        slug=FEATURE,
        area="runtime",
        payload=prepare_input(),
        dry_run=True,
    )
    assert preview.ok, preview.to_envelope()
    assert snapshot(host) == before
    prepared(host)
    before = snapshot(host)
    reused = invoke(
        "FeaturePrepare", slug=FEATURE, area="runtime", payload=prepare_input()
    )
    assert reused.ok, reused.to_envelope()
    assert snapshot(host) == before


@pytest.mark.parametrize("after_start", [False, True])
def test_ac2_reassessment_keeps_confirmed_overrides_and_phase_authority(
    host, after_start
):
    confirmed(
        host,
        overrides={
            "spec-review": entry(
                "spec-review", mode="off", limit=None, minimum_rounds=0
            )
        },
    )
    if after_start:
        assert invoke("FeatureStart", slug=FEATURE).ok
    path = formal(host) if after_start else intake(host)
    before = yaml.safe_load(path.read_text())
    old_intake = intake(host).read_bytes()
    result = invoke(
        "FeatureReassess",
        slug=FEATURE,
        payload=asdict(axes("large", "high", "none")),
        expect_revision=before["revision"],
    )
    assert result.ok, result.to_envelope()
    after = yaml.safe_load(path.read_text())
    assert after["feature_policy"] == before["feature_policy"]
    for field in ("stage", "authorized_through", "commands"):
        assert after.get(field) == before.get(field)
    if after_start:
        assert intake(host).read_bytes() == old_intake


@pytest.mark.parametrize("after_start", [False, True])
def test_ac7_policy_revision_is_checked_at_the_actual_owner_and_intake_freezes(
    host, after_start
):
    confirmed(host)
    if after_start:
        assert invoke("FeatureStart", slug=FEATURE).ok
    path = formal(host) if after_start else intake(host)
    revision = yaml.safe_load(path.read_text())["revision"]
    before = snapshot(host)
    changed = wire_policy(
        revision=2, overrides={"spec-review": entry("spec-review", limit=7)}
    )
    stale = invoke(
        "FeaturePolicy", slug=FEATURE, payload=changed, expect_revision=revision - 1
    )
    assert not stale.ok
    assert "revision" in stale.error.message
    assert snapshot(host) == before
    old_intake = intake(host).read_bytes()
    accepted = invoke(
        "FeaturePolicy", slug=FEATURE, payload=changed, expect_revision=revision
    )
    assert accepted.ok, accepted.to_envelope()
    assert yaml.safe_load(path.read_text())["feature_policy"]["revision"] == 2
    if after_start:
        assert intake(host).read_bytes() == old_intake


def test_ac7_cli_and_typed_policy_preview_agree_without_writes(
    host, run_cli, monkeypatch
):
    prepared(host)
    payload = wire_policy()
    before = snapshot(host)
    typed = invoke("FeaturePolicy", slug=FEATURE, payload=payload, dry_run=True)
    assert typed.ok, typed.to_envelope()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, out, err = run_cli(
        ["feature", "policy", FEATURE, "--from-file", "-", "--dry-run", "--json"]
    )
    assert code == 0, (out, err)
    assert json.loads(out)["data"]["effective_policy"] == typed.data["effective_policy"]
    assert snapshot(host) == before


def test_ac1_legacy_tier_creation_is_a_write_free_usage_error(host, run_cli):
    before = snapshot(host)
    code, out, err = run_cli(
        ["feature", "start", FEATURE, "--area", "runtime", "--tier", "2", "--json"]
    )
    assert code != 0, (
        "FAIL AC-1: tier-only creation still bypasses explicit confirmation"
    )
    message = (out + err).lower()
    assert "unrecognized" in message and "--tier" in message
    assert snapshot(host) == before


def test_ac1_cli_start_consumes_confirmed_intake_without_legacy_flags(host, run_cli):
    confirmed(host)
    code, out, err = run_cli(["feature", "start", FEATURE, "--json"])
    assert code == 0, (out, err)
    assert yaml.safe_load(formal(host).read_text())["schema"] == "heddle.state/v10"


@pytest.mark.parametrize("fault", ["empty-history", "duplicate-revision", "area"])
def test_ac1_invalid_persisted_intake_refuses_before_admission_effects(host, fault):
    confirmed(host)
    document = yaml.safe_load(intake(host).read_text())
    if fault == "empty-history":
        document["policy_history"] = []
    elif fault == "duplicate-revision":
        document["policy_history"].append(document["feature_policy"])
    else:
        document["area"] = None
    intake(host).write_text(yaml.safe_dump(document))
    before = snapshot(host)
    result = invoke("FeatureStart", slug=FEATURE)
    assert not result.ok
    assert snapshot(host) == before


def test_ac1_interrupted_admission_binds_flow_for_exact_retry(host, monkeypatch):
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import feature_start

    confirmed(host)
    original = feature_start.create_state

    def interrupted(*_args, **_kwargs):
        raise KernelError(
            code="workspace-invalid", message="injected interruption", hint="retry"
        )

    monkeypatch.setattr(feature_start, "create_state", interrupted)
    assert not invoke("FeatureStart", slug=FEATURE, flow="hitl").ok
    before = snapshot(host)
    monkeypatch.setattr(feature_start, "create_state", original)
    refused = invoke("FeatureStart", slug=FEATURE, flow="auto")
    assert not refused.ok
    assert "flow" in refused.error.message
    assert snapshot(host) == before
    assert invoke("FeatureStart", slug=FEATURE).ok
    assert yaml.safe_load(formal(host).read_text())["flow"] == "hitl"


def test_ac7_start_replay_after_policy_amendment_is_current_owner_readback(host):
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    original_intake = intake(host).read_bytes()
    revised = wire_policy(
        revision=2, overrides={"spec-review": entry("spec-review", limit=7)}
    )
    assert invoke("FeaturePolicy", slug=FEATURE, payload=revised).ok
    before = snapshot(host)
    replay = invoke("FeatureStart", slug=FEATURE)
    assert replay.ok, replay.to_envelope()
    assert replay.data["effective_policy"]["entries"] == json.loads(
        json.dumps(revised["entries"])
    )
    assert snapshot(host) == before
    assert intake(host).read_bytes() == original_intake


@pytest.mark.parametrize("after_start", [False, True])
@pytest.mark.parametrize(
    "changes,diagnostic",
    [
        ({"limit": 0}, "round limit"),
        ({"limit": 1, "minimum_rounds": 2}, "minimum rounds"),
        ({"mode": "off", "limit": None, "minimum_rounds": 1}, "off policy"),
        ({"role": "review-synthesis"}, "retired gate role"),
        ({"scope": "milestone"}, "requires scope"),
        ({"secondary": {**ASTRA, "model": "gpt-6-sol"}}, "independent reviewer CLI"),
    ],
)
def test_ac2_invalid_confirmation_preserves_current_owner_and_can_be_corrected(
    host, after_start, changes, diagnostic
):
    if after_start:
        confirmed(host)
        assert invoke("FeatureStart", slug=FEATURE).ok
    else:
        prepared(host)
    path = formal(host) if after_start else intake(host)
    revision = yaml.safe_load(path.read_text())["revision"]
    valid = wire_policy(revision=2 if after_start else 1)
    invalid = deepcopy(valid)
    invalid["entries"][0].update(changes)
    before = snapshot(host)
    refused = invoke(
        "FeaturePolicy", slug=FEATURE, payload=invalid, expect_revision=revision
    )
    assert not refused.ok and refused.error.code == "workspace-invalid"
    assert diagnostic in refused.error.message
    assert "feature policy payload" in refused.error.hint
    assert "prepare" not in refused.error.hint
    assert snapshot(host) == before
    corrected = invoke(
        "FeaturePolicy", slug=FEATURE, payload=valid, expect_revision=revision
    )
    assert corrected.ok, corrected.to_envelope()
    assert yaml.safe_load(path.read_text())["feature_policy"]["revision"] == (
        2 if after_start else 1
    )


@pytest.mark.parametrize("after_start", [False, True])
def test_ac7_wrong_policy_revision_has_current_owner_remedy(host, after_start):
    confirmed(host)
    if after_start:
        assert invoke("FeatureStart", slug=FEATURE).ok
    before = snapshot(host)
    refused = invoke("FeaturePolicy", slug=FEATURE, payload=wire_policy(revision=9))
    assert not refused.ok and "policy revision must be 2" in refused.error.message
    assert "reread the current policy" in refused.error.hint
    assert "prepare" not in refused.error.hint
    assert snapshot(host) == before


@pytest.mark.parametrize(
    "fault,remedy",
    [
        ("identity", "workspace identity"),
    ],
)
def test_ac7_formal_policy_faults_do_not_offer_intake_repair(host, fault, remedy):
    from heddle.kernel.state import parse_state_document
    from tests.completion_helpers import accepted_completion_document

    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    path = formal(host)
    document = yaml.safe_load(path.read_text())
    if fault == "identity":
        document["feature"] = "different-feature"
        document["intake"]["path"] = ".heddle/intake/different-feature.yaml"
    elif fault == "accepted":
        # A valid authored terminal fact tests immutable ownership. No completion
        # operation, verifier or full feature journey executes in this test.
        document = accepted_completion_document()
        document.update(
            schema="heddle.state/v9", feature=FEATURE, feature_policy=wire_policy()
        )
        document.pop("tier", None)
    if fault != "corrupt":
        parse_state_document(document, source=path)  # Valid control, not corruption.
    path.write_text("[broken YAML" if fault == "corrupt" else yaml.safe_dump(document))
    before = snapshot(host)
    refused = invoke("FeaturePolicy", slug=FEATURE, payload=wire_policy(revision=2))
    assert not refused.ok and refused.error.code == "workspace-invalid"
    assert remedy.lower() in (refused.error.hint + refused.error.message).lower()
    assert "feature prepare" not in refused.error.hint
    assert snapshot(host) == before
