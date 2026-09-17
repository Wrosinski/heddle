"""
AC-14 full installed journeys: explicit local e2e execution grant required.

Collection is safe. Execution builds one offline wheel, installs copied runtime
dependencies, and uses temporary Git hosts and controlled provider processes.
No real provider, broad pytest child, network or operational feature mutation.
"""

import json

import pytest
import yaml

from tests.runtime.wheel_harness import build_installed_wheel, write_claude_shim
from tests.structured_review_helpers import finding
from tests.tiering_helpers import FABLE, entry, snapshot, wire_policy
from tests.tiering_history_helpers import legacy_host
from tests.tiering_installed_helpers import (
    FEATURE,
    admit,
    all_off,
    author_value,
    finish_value,
    isolated_env,
    journey,
)
from tests.tiering_review_helpers import review_content

pytestmark = [pytest.mark.acceptance, pytest.mark.e2e, pytest.mark.toolchain]


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("tiering-installed"))


def assert_isolated(installed):
    assert not installed.forbidden_log.exists(), "FAIL AC-14: checkout import/read"
    assert not installed.network_log.exists(), "FAIL AC-14: unapproved network attempt"


def test_ac14_installed_direct_choice_needs_no_formal_feature(installed, tmp_path):
    case = journey(installed, tmp_path)
    admit(case, route="direct")
    assert not case.state.exists()
    assert not (case.root / "plans" / FEATURE).exists()
    assert not (case.root / "docs/features/runtime" / f"{FEATURE}.md").exists()
    assert_isolated(installed)


def test_ac14_installed_all_off_journey_still_verifies_before_acceptance(
    installed, tmp_path
):
    case = journey(installed, tmp_path)
    admit(case, overrides=all_off())
    value = yaml.safe_load(case.state.read_text())
    assert value["schema"] == "heddle.state/v10" and "tier" not in value
    generated_spec = (case.root / "docs/features/runtime" / f"{FEATURE}.md").read_text()
    generated_plan = (case.state.parent / "plan.md").read_text()
    assert generated_spec.count("## Acceptance Criteria") == 1
    assert "## Design Commitments" in generated_spec
    assert "## Technical Architecture" in generated_plan
    assert "## Implementation Strategy" in generated_plan
    assert "## Acceptance Criteria" not in generated_plan
    assert "heddle:begin plan-status" in generated_plan
    assert "complexity: medium" not in generated_spec + generated_plan
    assert "{{tier}}" not in generated_spec + generated_plan
    author_value(case)
    finish_value(case)
    # Failed proof with unchanged source bytes is a different discriminator
    # from changed-command and expanded-source-set currency below.
    case.env["HEDDLE_FIXTURE_FAIL"] = "1"
    failed = case.run("verify", "--scope", "acceptance", expected=(3,))
    # The fact publishes, while the command truthfully reports unusable proof.
    assert not failed["ok"] and failed["error"]["code"] == "verification-failed"
    assert failed["error"]["details"]["child_exit_code"] == 1
    assert failed["error"]["details"]["recorded"] is True
    latest = yaml.safe_load(case.state.read_text())["verifications"][-1]
    assert latest["exit_code"] != 0
    assert (
        latest["evidence"]["before"]["source_sha256"]
        == latest["evidence"]["after"]["source_sha256"]
    )
    refused = case.run("feature", "complete", "--dry-run", expected=(3,))
    assert not refused["ok"] and "fail" in json.dumps(refused).lower()
    case.env["HEDDLE_FIXTURE_FAIL"] = "0"
    case.run("verify", "--scope", "acceptance")
    case.run(
        "commands", "set", "acceptance_test", "--command", "python3 -B tests/check.py"
    )
    refused = case.run("feature", "complete", "--dry-run", expected=(3,))
    assert not refused["ok"] and "command" in json.dumps(refused).lower()
    case.run(
        "commands", "set", "acceptance_test", "--command", "python3 tests/check.py"
    )
    expanded = case.root / "src/new.py"
    expanded.write_text("NEW = 1\n")
    refused = case.run("feature", "complete", "--dry-run", expected=(3,))
    assert not refused["ok"]
    assert any(word in json.dumps(refused).lower() for word in ("source", "stale"))
    assert yaml.safe_load(case.state.read_text())["completion"] is None
    expanded.unlink()  # Remove only this case's exact injected extra source.
    for scope in ("m1", "acceptance", "smoke"):
        case.run("verify", "--scope", scope)
    before = snapshot(case.root)
    preview = case.run("feature", "complete", "--dry-run")
    assert preview["ok"] and snapshot(case.root) == before
    accepted = case.run("feature", "complete", expected=(0, 4))
    assert accepted["data"]["accepted"]
    value = yaml.safe_load(case.state.read_text())
    assert value["gates"] == []
    assert {
        value["verifications"][i]["scope"]
        for i in value["completion"]["verification_indexes"]
    } >= {"acceptance", "smoke"}
    for index in value["completion"]["verification_indexes"]:
        fact = value["verifications"][index]
        assert fact["exit_code"] == 0 and fact["command"] == "python3 tests/check.py"
        evidence = fact["evidence"]
        assert evidence["schema"] == "heddle.verification-evidence/v1"
        reference = evidence["before"]
        manifest = yaml.safe_load(
            (case.state.parent / reference["artifact"]).read_text()
        )
        definition = manifest["definition"]
        assert "src" in definition["declaration_paths"]
        assert "src/example.py" in definition["paths"]
        assert "src/new.py" not in definition["paths"]
        assert len(reference["source_sha256"]) == 64
        assert evidence["before"] == evidence["after"]
    assert (
        case.root / f"docs/gate-trajectories/.raw/{FEATURE}/completion.tar.gz"
    ).is_file()
    assert_isolated(installed)


def test_ac14_installed_review_preserves_original_finding_across_disable_and_tamper(
    installed, tmp_path
):
    case = journey(installed, tmp_path)
    chosen = all_off()
    chosen["spec-review"] = entry("spec-review", primary=FABLE)
    admit(case, overrides=chosen)
    author_value(case)
    case.run("phase-exit", "--through", "complete")
    payload = review_content(
        findings=[finding("SP-I1", classification="implement")], altitude=True
    )
    payload["details"]["scope"]["integrated_acceptance"] = (
        "Read VALUE through tests/check.py."
    )
    calls = tmp_path / "provider-calls.jsonl"
    event = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": payload,
            }
        )
        + "\n"
    )
    shim = write_claude_shim(tmp_path / "bin", event, calls)
    case.env["PATH"] = f"{shim.parent}:{case.env['PATH']}"
    case.run("run-gate", "spec-review", expected=(0, 4))
    state = yaml.safe_load(case.state.read_text())
    source = state["gates"][0]["runs"][0]
    assert source["execution_config"]["model"] == FABLE["model"]
    assert len(calls.read_text().splitlines()) == 1
    amendment = case.payload(
        "disable.json", wire_policy(revision=2, overrides=all_off())
    )
    case.run("feature", "policy", FEATURE, "--from-file", amendment, feature=False)
    refusal = case.run("phase-exit", expected=(3, 4))
    assert not refusal["ok"]
    canonical = case.state.parent / source["artifact"]
    original = canonical.read_bytes()
    canonical.write_text(canonical.read_text().replace("VALUE", "TAMPERED"))
    rows = [
        {
            "run_id": source["run_id"],
            "finding_id": identifier,
            "status": status,
            "evidence_kind": "inspection",
            "references": ["src/example.py"],
            "reason": "Trace the original counterexample to its implementation",
        }
        for identifier, status in (("SP-I1", "addressed"), ("@coverage", "settled"))
    ]
    disposition = case.payload(
        "disposition.json",
        {"schema": "heddle.review-disposition-input/v1", "dispositions": rows},
    )
    before = case.state.read_bytes()
    revision = str(yaml.safe_load(before)["revision"])
    failed = case.run(
        "review",
        "disposition",
        "--input-json",
        disposition,
        "--expect-revision",
        revision,
        expected=(3,),
    )
    assert not failed["ok"] and case.state.read_bytes() == before
    canonical.write_bytes(original)
    closed = case.run(
        "review",
        "disposition",
        "--input-json",
        disposition,
        "--expect-revision",
        revision,
    )
    assert closed["data"]["closure"]["closed"]
    assert len(calls.read_text().splitlines()) == 1
    assert_isolated(installed)


def test_ac14_installed_v6_conversion_preserves_original_bytes(
    installed, tmp_path, monkeypatch
):
    import hashlib

    root, path = legacy_host(tmp_path, monkeypatch, stage="implement")
    original = path.read_bytes()
    payload = tmp_path / "migration.json"
    payload.write_text(
        json.dumps(
            {"schema": "heddle.migration-input/v1", "feature_policy": wire_policy()}
        )
    )
    before = snapshot(root)
    args = (
        "feature",
        "migrate",
        "operational-example",
        "--from-file",
        str(payload),
        "--expect-revision",
        "0",
        "--json",
    )
    env = isolated_env(installed, tmp_path)
    preview = installed.run(*args, "--dry-run", cwd=root, env=env)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert snapshot(root) == before
    converted = installed.run(*args, cwd=root, env=env)
    assert converted.returncode == 0, converted.stdout + converted.stderr
    assert yaml.safe_load(path.read_text())["schema"] == "heddle.state/v9"
    saved = (
        path.parent
        / "migration"
        / f"v6-state-{hashlib.sha256(original).hexdigest()}.yaml"
    )
    assert saved.read_bytes() == original
    assert_isolated(installed)
