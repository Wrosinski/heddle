"""Current-only runtime boundary and retained delivery contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import get_args

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.contracts.gates import GATE_CATALOG
from heddle.contracts.schemas import (
    STATE_SCHEMA_V9,
    STATE_SCHEMA_V10,
    STATE_SCHEMA_VERSIONS,
)
from heddle.kernel.project_config import KernelError
from heddle.kernel.state import parse_state_document
from heddle.runtime.contracts import COMMAND_SURFACE
from tests.operational_model_helpers import document
from tests.tiering_helpers import (
    FEATURE,
    blank_host,
    confirmed,
    entry,
    invoke,
    snapshot,
)
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    gate_command,
    provider_transport,
    review_content,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

RETIRED_MODULES = (
    "heddle.contracts.document_reviews",
    "heddle.contracts.migration",
    "heddle.contracts.review_authority",
    "heddle.kernel.document_reviews",
    "heddle.kernel.migration",
    "heddle.runtime.document_reviews",
    "heddle.runtime.migration",
    "heddle.runtime.migration_history",
    "heddle.gate.synthesis",
)
RETIRED_COMMANDS = {"review bootstrap", "feature migrate"}
RETIRED_FLAGS = {"--tier", "--retier"}
CURRENT_GATES = {
    "spec-review",
    "plan-review",
    "review-test-scaffolding",
    "milestone-review",
    "peer-review-sequential",
    "behavior-review",
    "complexity-review",
    "robustness-analysis",
}


def _retained_capture_host(tmp_path, monkeypatch, run_cli):
    host, _path = current_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={"behavior-review": entry("behavior-review")},
    )
    imperfect = {
        **review_content("behavior-review"),
        "extra_explanation": "retain the exact completed response",
    }
    calls = provider_transport(monkeypatch, imperfect)
    code, result = gate_command(run_cli, "run-gate", "behavior-review")
    assert result["ok"], result["error"]["message"]
    assert code == 4 and len(calls) == 1
    return host, host / result["data"]["artifact"]


@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_ac2_doctor_and_validate_agree_on_required_retained_capture(
    tmp_path, monkeypatch, run_cli, fault
):
    host, capture = _retained_capture_host(tmp_path, monkeypatch, run_cli)
    if fault == "missing":
        capture.unlink()
    else:
        capture.write_bytes(capture.read_bytes() + b" ")
    before = snapshot(host)

    doctor = invoke("Doctor", feature=V7_FEATURE)
    validate = invoke("Validate", feature=V7_FEATURE)

    doctor_codes = {row.code for row in doctor.diagnostics}
    validate_codes = {row.code for row in validate.diagnostics}
    assert "artifact-required-invalid" in doctor_codes
    assert "artifact-required-invalid" in validate_codes
    assert doctor.ok and not validate.ok
    assert snapshot(host) == before


def test_ac3_current_runtime_has_no_retired_modules_operations_or_commands():
    assert all(importlib.util.find_spec(name) is None for name in RETIRED_MODULES)
    operation_names = {member.__name__ for member in get_args(ops.Operation.__value__)}
    assert operation_names.isdisjoint(
        {"ReviewBootstrap", "FeatureMigrate", "FeatureRetier"}
    )
    commands = {contract.name: contract for contract in COMMAND_SURFACE}
    assert commands.keys().isdisjoint(RETIRED_COMMANDS)
    start_flags = {flag.name for flag in commands["feature start"].flags}
    assert start_flags.isdisjoint(RETIRED_FLAGS)


@pytest.mark.parametrize(
    "argv",
    [
        ["review", "bootstrap", "--json"],
        ["feature", "migrate", FEATURE, "--json"],
        ["feature", "start", FEATURE, "--tier", "1", "--json"],
        ["feature", "start", FEATURE, "--retier", "--json"],
    ],
)
def test_ac3_retired_public_commands_and_flags_refuse_without_writes(
    tmp_path, monkeypatch, run_cli, envelope_tools, argv
):
    host = blank_host(tmp_path, monkeypatch)
    before = snapshot(host)
    code, out, _err = run_cli(argv)
    envelope = envelope_tools.parse(out)
    assert code == 2
    assert envelope["error"]["code"] == "usage"
    if argv[:2] not in (["review", "bootstrap"], ["feature", "migrate"]):
        message = envelope["error"]["message"].lower()
        assert "unrecognized" in message and "retired" not in message
    assert snapshot(host) == before


def test_ac3_ac9_current_gate_and_prompt_inventory_has_no_legacy_launch_path():
    from heddle.gate.registry import GATES

    assert set(GATE_CATALOG) == set(GATES) == CURRENT_GATES
    contracts_source = (REPO_ROOT / "heddle/contracts/gates.py").read_text()
    assert "GATE_CATALOG_V6" not in contracts_source
    packaged = {
        path.name for path in (REPO_ROOT / "heddle/resources/prompts").glob("*.md")
    }
    assert packaged == {Path(gate.prompt_template).name for gate in GATES.values()}


def test_ac3_ac5_explicit_v6_state_is_unsupported_with_recovery_route(tmp_path):
    assert STATE_SCHEMA_VERSIONS == (STATE_SCHEMA_V9, STATE_SCHEMA_V10)
    with pytest.raises(KernelError) as caught:
        parse_state_document(
            document(schema="heddle.state/v6"), source=tmp_path / "state.yaml"
        )
    diagnostic = f"{caught.value.message} {caught.value.hint}".lower()
    assert "heddle.state/v6" in diagnostic
    assert "unsupported" in diagnostic
    assert "compatible" in diagnostic and "fresh" in diagnostic


def test_ac3_ac5_v8_migration_attribution_is_unsupported_not_malformed(tmp_path):
    current = document(schema=STATE_SCHEMA_V9)
    current["feature_policy"] = None
    current["migration"] = {"source_revision": 0}
    with pytest.raises(KernelError) as caught:
        parse_state_document(current, source=tmp_path / "state.yaml")
    diagnostic = f"{caught.value.message} {caught.value.hint}".lower()
    assert "migration" in diagnostic and "unsupported" in diagnostic
    assert "malformed" not in diagnostic
    assert "compatible" in diagnostic


def test_ac5_mixed_discovery_separates_current_unsupported_and_malformed(
    tmp_path, monkeypatch
):
    host = blank_host(tmp_path, monkeypatch)
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok

    old = document(schema="heddle.state/v6")
    old["feature"] = "legacy-example"
    old["spec"] = "docs/features/runtime/legacy-example.md"
    old_workspace = host / "plans" / old["feature"]
    old_workspace.mkdir(parents=True)
    old_path = old_workspace / "state.yaml"
    old_path.write_text(yaml.safe_dump(old, sort_keys=False))

    current_path = host / "plans" / FEATURE / "state.yaml"
    malformed = yaml.safe_load(current_path.read_text())
    malformed["feature"] = "malformed-current"
    malformed["spec"] = "docs/features/runtime/malformed-current.md"
    del malformed["stage"]
    malformed_workspace = host / "plans" / "malformed-current"
    malformed_workspace.mkdir()
    (malformed_workspace / "state.yaml").write_text(
        yaml.safe_dump(malformed, sort_keys=False)
    )
    before = snapshot(host)

    portfolio = invoke("Status", all_features=True)
    assert portfolio.ok, portfolio.to_envelope()
    rows = {row["feature"]: row for row in portfolio.data["features"]}
    assert "error" not in rows[FEATURE]
    assert rows[old["feature"]]["error"]["code"] == "incompatible-state-schema"
    assert rows["malformed-current"]["error"]["code"] == "workspace-invalid"
    assert "unsupported" in rows[old["feature"]]["error"]["message"].lower()
    assert "unsupported" not in rows["malformed-current"]["error"]["message"].lower()
    assert invoke("Status", feature=FEATURE).ok
    assert snapshot(host) == before


def test_ac8_state_loader_value_and_rejection_parity():
    from heddle.kernel import state

    capability = state.state_loader_capability()
    assert capability["safe"] is True
    assert capability["loader"] in {"CSafeLoader", "SafeLoader"}
    assert capability["accelerated"] is hasattr(yaml, "CSafeLoader")
    supported = "null: null\nbool: true\nint: 7\nlist: [x, 2]\nmap: {k: v}\n"
    assert state.decode_state_yaml(supported, loader=yaml.SafeLoader) == (
        state.decode_state_yaml(
            supported, loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        )
    )
    unsafe = "value: !!python/object:builtins.object {}\n"
    for loader in {yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)}:
        with pytest.raises(yaml.constructor.ConstructorError):
            state.decode_state_yaml(unsafe, loader=loader)


def test_ac8_doctor_reports_safe_loader_fallback_without_mutation(
    tmp_path, monkeypatch
):
    from heddle.kernel import state

    host = blank_host(tmp_path, monkeypatch)
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    monkeypatch.setattr(state, "STATE_YAML_LOADER", yaml.SafeLoader)
    monkeypatch.setattr(state, "STATE_LOADER_ACCELERATED", False)
    before = snapshot(host)
    result = invoke("Doctor", feature=FEATURE)
    assert result.ok
    assert any(row.code == "state-loader-fallback" for row in result.diagnostics)
    assert snapshot(host) == before


def test_ac8_state_transaction_uses_the_shared_safe_loader(monkeypatch):
    from heddle.kernel import state
    from heddle.runtime import state_store

    calls = []

    def decode(text):
        calls.append(text)
        return state.decode_state_yaml(text)

    monkeypatch.setattr(state_store, "decode_state_yaml", decode)
    document, raw = state_store.read_state_source(
        REPO_ROOT / "tests/fixtures/workspaces/tiny/plans/sample-feature/state.yaml"
    )
    assert document["schema"] == "heddle.state/v9"
    assert raw and len(calls) == 1


def test_ac9_current_inventory_writer_round_trips_isolated_nodes(tmp_path):
    from tests.guardrails import test_pytest_inventory as inventory

    destination = tmp_path / "current-inventory.txt.gz.b64"
    nodes = ["tests/example.py::test_two", "tests/example.py::test_one"]
    digest = inventory._write_current_inventory(nodes, destination=destination)

    assert inventory._inventory(destination, digest) == set(nodes)
    before = destination.read_bytes()
    assert inventory._write_current_inventory(nodes, destination=destination) == digest
    assert destination.read_bytes() == before


def test_ac3_ac9_retained_test_helpers_and_driver_fixtures_are_current():
    assert not (REPO_ROOT / "tests/document_review_helpers.py").exists()
    readiness_source = (REPO_ROOT / "tests/readiness_helpers.py").read_text()
    for retired in (
        "review_authority",
        "snapshot.tier",
        "synthesis_inputs",
        "declared_producer_lanes",
    ):
        assert retired not in readiness_source

    driver_hosts = REPO_ROOT / "tests/driver/fixtures/workspaces"
    state_paths = sorted(driver_hosts.glob("*/plans/*/state.yaml"))
    assert state_paths
    for path in state_paths:
        state = parse_state_document(
            yaml.safe_load(path.read_text(encoding="utf-8")), source=path
        )
        assert state.schema == STATE_SCHEMA_V9
    for path in driver_hosts.glob("*/.heddle.yaml"):
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert set(config.get("gates", {}).get("enabled", ())) <= CURRENT_GATES


def test_ac1_status_and_orient_never_launch_a_provider(tmp_path, monkeypatch):
    from heddle.gate import runner

    host = blank_host(tmp_path, monkeypatch)
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    calls: list[str] = []

    def forbidden(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("read surfaces cannot launch a provider")

    monkeypatch.setattr(runner, "execute_codex", forbidden)
    monkeypatch.setattr(runner, "execute_claude", forbidden)
    before = snapshot(host)
    assert invoke("Status", feature=FEATURE).ok
    assert invoke("Orient", feature=FEATURE).ok
    assert calls == [] and snapshot(host) == before


@pytest.mark.toolchain
def test_ac3_ac9_installed_wheel_exposes_only_current_resources(tmp_path):
    from tests.runtime.wheel_harness import build_installed_wheel

    installed = build_installed_wheel(tmp_path)
    help_result = installed.run("help", "--json", cwd=tmp_path)
    assert help_result.returncode == 0, help_result.stderr
    manifest = json.loads(help_result.stdout)["data"]
    names = {row["name"] for row in manifest["commands"]}
    assert names.isdisjoint(RETIRED_COMMANDS)
    probe_source = """
import importlib.util, json
from pathlib import Path
import heddle
from heddle.gate.registry import GATES
root = Path(heddle.__file__).parent
print(json.dumps({
    "gates": sorted(GATES),
    "prompts": sorted(p.name for p in (root / "resources/prompts").glob("*.md")),
    "retired_modules": [name for name in RETIRED if importlib.util.find_spec(name)],
}))
""".replace("RETIRED", repr(RETIRED_MODULES))
    probe = installed.python_probe(probe_source, cwd=tmp_path)
    assert probe.returncode == 0, probe.stderr
    report = json.loads(probe.stdout)
    assert set(report["gates"]) == CURRENT_GATES
    assert set(report["prompts"]) == {f"{name}.md" for name in CURRENT_GATES}
    assert report["retired_modules"] == []
