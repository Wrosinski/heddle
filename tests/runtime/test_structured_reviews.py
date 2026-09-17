"""W3 composed acceptance; actual application, gate, state and projection owners."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from heddle.gate.cli import resolve_gate_execution
from heddle.gate.registry import GATES
from heddle.gate.types import GateInvocationOverrides
from tests.operational_model_helpers import FEATURE, application, cli, read, write
from tests.structured_review_helpers import (
    content,
    evidence,
    host_for_review,
    json_bytes,
    native_providers,
    runs,
)

pytestmark = pytest.mark.acceptance


def invoke(app, ops, *, gate="self-review", lane="claude", **kwargs):
    return app.execute(ops.RunGate(gate, feature=FEATURE, cli=lane, **kwargs))


def assert_completed(result, calls, expected_calls=1):
    assert result.ok and result.data["cached"] is False, (
        f"FAIL W3: valid native content did not complete: {result}"
    )
    assert len(calls) == expected_calls
    return result.data["run_id"]


def owners(path, run_id):
    return [
        d
        for d in read(path)["decisions"]
        if d.get("origin_run_id") == run_id and d["kind"] == "finding"
    ]


def view_path(canonical):
    return canonical.with_name(canonical.name.removesuffix(".review.json") + ".md")


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(("status",), id="argv0"),
        pytest.param(("orient",), id="argv1"),
        pytest.param(("kickoff",), id="argv2"),
        pytest.param(("phase-exit",), id="argv5"),
        pytest.param(("drive",), id="argv6"),
    ],
)
def test_ac11_old_v3_pass_refuses_all_public_entrypoints(
    tmp_path, monkeypatch, run_cli, argv
):
    from tests.operational_model_helpers import fact, run

    host, path = host_for_review(tmp_path)
    monkeypatch.chdir(host)
    code, control = cli(run_cli, "status", "--feature", FEATURE)
    assert code == 0 and control["ok"], control
    value = read(path)
    value["schema"] = "heddle.state/v3"
    old = run(status="pass")
    old["artifact"] = "reviews/old.md"
    value["gates"] = [fact(old, gate="self-review", scope="m1")]
    write(path, value)
    old_path = path.parent / "reviews/old.md"
    old_path.parent.mkdir(exist_ok=True)
    old_path.write_text("# Historical review\n\n**Status:** PASS\n")
    calls = native_providers(monkeypatch, tmp_path, content())

    def files():
        return {
            p.relative_to(host): p.read_bytes()
            for p in host.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(host).parts
        }

    before = files()
    code, rejected = cli(run_cli, *argv, "--feature", FEATURE)
    assert code == 3 and rejected["error"]["code"] == "workspace-invalid", rejected
    assert "heddle.state/v3" in rejected["error"]["message"]
    assert "fresh" in rejected["error"]["hint"].lower()
    assert files() == before and not calls


@pytest.mark.e2e
def test_ac11_native_current_close_then_old_state_refusal(
    tmp_path, monkeypatch, run_cli
):
    from tests.runtime.test_operational_model import native_tier1_close_fixture

    app, _ops, host, path, _spec, operation = native_tier1_close_fixture(
        tmp_path, monkeypatch, run_cli
    )
    value = read(path)
    assert value["schema"] == "heddle.state/v6", (
        "FAIL AC-11: native creation still admits the old state schema"
    )
    control = app.execute(replace(operation, dry_run=True))
    assert control.ok, control
    value["schema"] = "heddle.state/v3"
    write(path, value)
    before = path.read_bytes()
    result = app.execute(operation)
    assert not result.ok and result.error.code == "workspace-invalid"
    assert path.read_bytes() == before
    assert not list(host.glob("docs/completion-receipts/*native-example*"))


@pytest.mark.toolchain
def test_ac11_packaged_current_start_and_old_pass_refusal(tmp_path):
    from tests.operational_model_helpers import fact, fresh_host, run
    from tests.runtime.wheel_harness import (
        admit_installed_feature,
        build_installed_wheel,
        parse_envelope,
        snapshot_tree,
    )
    from tests.tiering_helpers import ROLES, entry, prepare_input, wire_policy

    installed = build_installed_wheel(tmp_path / "installed")
    host, _ = fresh_host(tmp_path, seed=False)
    feature = "packaged-current-example"
    created = admit_installed_feature(
        installed,
        host=host,
        cwd=host,
        payload_dir=tmp_path / "intake",
        feature=feature,
        area="runtime",
        prepare_payload=prepare_input(),
        policy_payload=wire_policy(
            overrides={
                role: entry(role, mode="off", limit=None, minimum_rounds=0)
                for role in ROLES
            }
        ),
    )
    assert created["ok"], created
    path = host / f"plans/{feature}/state.yaml"
    value = read(path)
    assert value["schema"] == "heddle.state/v10"
    assert "tier" not in value
    for command in ("status", "orient", "kickoff"):
        control = installed.run(command, "--feature", feature, "--json", cwd=host)
        assert control.returncode == 0 and parse_envelope(control)["ok"], control

    # Deliberately authored old-state evidence; never an operational converter.
    value["schema"] = "heddle.state/v3"
    value["gates"] = [fact(run(status="pass", artifact="reviews/old.md"))]
    write(path, value)
    old = path.parent / "reviews/old.md"
    old.parent.mkdir(exist_ok=True)
    old.write_text("# Historical review\n\n**Status:** PASS\n")
    before = snapshot_tree(host)
    for args in (
        ("status",),
        ("orient",),
        ("kickoff",),
        ("show-prompt", "spec-review", "--cli", "claude"),
        ("run-gate", "spec-review", "--cli", "claude"),
        ("phase-exit",),
        ("drive",),
    ):
        result = installed.run(*args, "--feature", feature, "--json", cwd=host)
        envelope = parse_envelope(result)
        assert result.returncode == 3, envelope
        assert envelope["error"]["code"] == "workspace-invalid", envelope
        assert "heddle.state/v3" in envelope["error"]["message"]
        assert snapshot_tree(host) == before
    assert not installed.forbidden_log.exists()
    assert not installed.network_log.exists()


def _document_producers(tmp_path, monkeypatch, *, role="spec-review", findings=()):
    app, ops = application()
    host, path = host_for_review(tmp_path, stage="spec-review", tier=3)
    monkeypatch.chdir(host)
    payload = content(
        role, findings=findings, status="pass_with_conditions" if findings else "pass"
    )
    calls = native_providers(monkeypatch, tmp_path, payload)
    sources = []
    for lane in ("claude", "codex"):
        result = invoke(app, ops, gate=role, lane=lane)
        assert_completed(result, calls, len(sources) + 1)
        sources.append((runs(path)[-1], deepcopy(payload)))
    return app, ops, host, path, sources


def _corrupt_synthesis_references(payload, mutation):
    summary = payload["synthesis"]
    row = summary["finding_coverage"][0]
    assessment = summary["assessment_coverage"][0]
    foreign_run = "11111111-1111-4111-8111-111111111111"
    derivation = {
        "finding_id": "SY-I1",
        "assessment_refs": [deepcopy(assessment["source"])],
    }
    conflict = {
        "id": "CON-1",
        "kind": "severity",
        "owner_finding_id": "SY-I1",
        "explanation": "An attributed difference requires its recorded owner.",
        "perspectives": [
            {
                "finding_ref": deepcopy(summary["finding_coverage"][index]["source"]),
                "assessment_ref": None,
                "position": position,
            }
            for index, position in ((0, "important"), (2, "minor"))
        ],
    }
    if mutation == "unknown-output":
        row["output_finding_id"] = "SY-I999"
    elif mutation == "duplicate-producer":
        summary["producer_runs"].append(summary["producer_runs"][0])
    elif mutation == "duplicate-finding-coverage":
        summary["finding_coverage"].append(deepcopy(row))
    elif mutation == "foreign-finding-run":
        row["source"]["run_id"] = foreign_run
    elif mutation == "disposed-output":
        row["disposition"] = "addressed"
    elif mutation in {
        "addressed-decision",
        "addressed-unobserved",
        "awaiting-without-origin",
        "settled-partial-origin",
    }:
        row["output_finding_id"] = None
        row["disposition"] = "addressed"
        if mutation == "addressed-decision":
            row.update(
                decision_id="decision-1", decision_origin=deepcopy(row["source"])
            )
        elif mutation == "addressed-unobserved":
            row["evidence"] = evidence(kind="speculation")
        elif mutation == "awaiting-without-origin":
            row.update(disposition="awaiting_decision", decision_id="decision-1")
        else:
            row.update(disposition="settled", decision_origin=deepcopy(row["source"]))
    elif mutation == "unknown-assessment-target":
        assessment["target"] = "dimension/not-an-output-assessment"
    elif mutation == "duplicate-assessment-coverage":
        summary["assessment_coverage"].append(deepcopy(assessment))
    elif mutation == "foreign-assessment-run":
        assessment["source"]["run_id"] = foreign_run
    elif mutation in {
        "unknown-derived-output",
        "duplicate-derived-output",
        "empty-derived-refs",
        "duplicate-derived-refs",
        "foreign-derived-ref",
    }:
        summary["derived_findings"] = [derivation]
        if mutation == "unknown-derived-output":
            derivation["finding_id"] = "SY-I999"
        elif mutation == "duplicate-derived-output":
            summary["derived_findings"].append(deepcopy(derivation))
        elif mutation == "empty-derived-refs":
            derivation["assessment_refs"] = []
        elif mutation == "duplicate-derived-refs":
            derivation["assessment_refs"] *= 2
        else:
            derivation["assessment_refs"][0]["assessment_id"] = "not-a-source"
    elif mutation == "output-without-provenance":
        summary["finding_coverage"] = []
    elif mutation in {
        "unknown-conflict-owner",
        "nonreport-conflict-owner",
        "duplicate-conflict-id",
        "perspective-both",
        "perspective-neither",
        "duplicate-perspective",
        "single-producer-conflict",
        "foreign-finding-perspective",
        "foreign-assessment-perspective",
    }:
        summary["conflicts"] = [conflict]
        perspective = conflict["perspectives"][0]
        if mutation == "unknown-conflict-owner":
            conflict["owner_finding_id"] = "SY-I999"
        elif mutation == "nonreport-conflict-owner":
            conflict["owner_finding_id"] = "SY-M1"
        elif mutation == "duplicate-conflict-id":
            summary["conflicts"].append(deepcopy(conflict))
        elif mutation == "perspective-both":
            perspective["assessment_ref"] = deepcopy(assessment["source"])
        elif mutation == "perspective-neither":
            perspective["finding_ref"] = None
        elif mutation == "duplicate-perspective":
            conflict["perspectives"] = [perspective, deepcopy(perspective)]
        elif mutation == "single-producer-conflict":
            conflict["perspectives"][1]["finding_ref"] = deepcopy(
                summary["finding_coverage"][1]["source"]
            )
        elif mutation == "foreign-finding-perspective":
            perspective["finding_ref"]["finding_id"] = "SP-I999"
        else:
            perspective["finding_ref"] = None
            perspective["assessment_ref"] = deepcopy(assessment["source"])
            perspective["assessment_ref"]["assessment_id"] = "not-a-source"
    elif mutation == "document-normalization":
        summary["severity_adjustments"] = [
            {
                "source": deepcopy(row["source"]),
                "original": "important",
                "normalized": "minor",
                "reason": "Unauthorized document calibration.",
            }
        ]
    else:
        raise AssertionError(f"Unknown synthesis corruption: {mutation}")


def _corrupt_retained_references(payload, mutation):
    from tests.structured_review_helpers import disposition, finding_ref

    source = finding_ref("11111111-1111-4111-8111-111111111111", "SP-I0")
    current = payload["findings"][0]["id"]
    regression = {"finding_id": current, "evidence": evidence()}
    retained = disposition(source, target=current)
    if mutation == "unknown-regression":
        payload["regressions"] = [dict(regression, finding_id="SP-I999")]
    elif mutation == "duplicate-regression":
        payload["regressions"] = [regression, deepcopy(regression)]
    elif mutation == "unsupported-regression-evidence":
        payload["regressions"] = [
            dict(regression, evidence=evidence(kind="speculation"))
        ]
    elif mutation == "foreign-retained-output":
        payload["prior_dispositions"] = [dict(retained, output_finding_id="SP-I999")]
    elif mutation == "disposed-output":
        payload["prior_dispositions"] = [dict(retained, disposition="addressed")]
    elif mutation == "addressed-decision":
        payload["prior_dispositions"] = [
            disposition(
                source,
                action="addressed",
                decision_id="decision-1",
                decision_origin=source,
            )
        ]
    elif mutation == "missing-decision-origin":
        payload["prior_dispositions"] = [
            disposition(source, action="awaiting_decision", decision_id="decision-1")
        ]
    elif mutation == "foreign-decision-origin":
        payload["prior_dispositions"] = [
            disposition(
                source,
                action="settled",
                decision_id="decision-1",
                decision_origin=finding_ref(source["run_id"], "SP-I999"),
            )
        ]
    elif mutation == "duplicate-disposition":
        payload["prior_dispositions"] = [retained, deepcopy(retained)]
    elif mutation == "retained-regression":
        payload["prior_dispositions"] = [retained]
        payload["regressions"] = [regression]
    else:
        raise AssertionError(f"Unknown corruption fixture: {mutation}")


def _validate_prepared_payload(role, payload, *, lane="claude"):
    from heddle.gate import entry, validation
    from heddle.runtime.feature_context import resolve_snapshot_from_cwd
    from tests.structured_review_helpers import result_api

    api = result_api()
    resolved = resolve_snapshot_from_cwd(FEATURE)
    assert hasattr(resolved, "snapshot"), resolved
    gate = GATES.get(role)
    context = entry.build_gate_context(
        resolved.snapshot, resolved.config, gate, cli=lane
    )
    invocation = resolve_gate_execution(
        gate,
        GateInvocationOverrides(cli=lane),
    )
    prepared = entry.prepare_gate_run(context, gate_type=gate, invocation=invocation)
    decoded = api.decode_review_content(
        json_bytes(payload), api.review_output_contract(role)
    )
    validation.validate_review(decoded, prepared)
    return prepared
