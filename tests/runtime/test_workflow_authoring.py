"""Workflow-usability authoring contracts and command routing."""

from __future__ import annotations

import io
import json
import shlex
import sys
from pathlib import Path

import yaml

from heddle.contracts import operations as ops
from heddle.contracts.feature_policy import (
    FEATURE_AXES_FIELDS,
    GATE_POLICY_FIELDS,
    GATE_POLICY_OPTIONAL_FIELDS,
    INTAKE_INPUT_FIELDS,
    INTAKE_RESEARCH_FIELDS,
    INTAKE_ROUTES,
    POLICY_INPUT_FIELDS,
    REVIEWER_FIELDS,
    TRIGGER_FIELDS,
)
from heddle.runtime import application
from heddle.runtime.contracts import build_manifest
from tests.structured_review_helpers import finding
from tests.tiering_helpers import blank_host, prepare_input, snapshot, wire_policy
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    runs,
)

REPO = Path(__file__).resolve().parents[2]


def _command(name: str) -> dict:
    return next(row for row in build_manifest()["commands"] if row["name"] == name)


def _assert_closed_field(
    fields: dict, name: str, *, required: bool, values: set[str] | None = None
) -> dict:
    field = fields[name]
    assert field["required"] is required, f"FAIL AC-1: {name} requiredness is hidden"
    if values is not None:
        options = field["one_of"]
        assert all(set(option) == {"value", "summary"} for option in options), (
            f"FAIL AC-1: {name} options do not use the public value/summary shape"
        )
        assert all(option["summary"].strip() for option in options)
        assert {option["value"] for option in options} == values, (
            f"FAIL AC-1: {name} does not publish its complete closed vocabulary"
        )
    return field


def test_ac1_prepare_and_policy_contract_examples_reach_real_decoders(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-1 red: public nested examples are complete, closed decoder inputs."""
    prepare_schema = _command("feature prepare")["input_schema"]
    policy_schema = _command("feature policy")["input_schema"]
    assert prepare_schema is not None, (
        "FAIL AC-1: feature prepare has no public intake payload contract"
    )
    assert policy_schema is not None, (
        "FAIL AC-1: feature policy has no public complete-policy contract"
    )
    assert prepare_schema["id"] == "heddle.intake-input/v1", (
        "FAIL AC-1: feature prepare publishes the wrong payload contract"
    )
    assert policy_schema["id"] == "heddle.feature-policy/v1", (
        "FAIL AC-1: feature policy publishes the wrong payload contract"
    )
    assert tuple(prepare_schema["fields"]) == INTAKE_INPUT_FIELDS
    assert tuple(policy_schema["fields"]) == POLICY_INPUT_FIELDS
    prepare_fields = prepare_schema["fields"]
    for name in ("schema", "route", "route_reason", "research", "axes"):
        _assert_closed_field(prepare_fields, name, required=True)
    _assert_closed_field(
        prepare_fields,
        "route",
        required=True,
        values=set(INTAKE_ROUTES),
    )
    research = prepare_fields["research"]["fields"]
    assert tuple(research) == INTAKE_RESEARCH_FIELDS
    for name in research:
        _assert_closed_field(research, name, required=True)
    axes = prepare_fields["axes"]["fields"]
    assert tuple(axes) == FEATURE_AXES_FIELDS
    for name in axes:
        _assert_closed_field(axes, name, required=True)
    _assert_closed_field(
        axes, "scope", required=True, values={"small", "medium", "large"}
    )
    _assert_closed_field(axes, "complexity", required=True, values={"low", "high"})
    _assert_closed_field(
        axes, "testability", required=True, values={"none", "partial", "full"}
    )
    policy_fields = policy_schema["fields"]
    for name in ("schema", "revision", "axes", "approval", "entries"):
        _assert_closed_field(policy_fields, name, required=True)
    policy_axes = policy_fields["axes"]["fields"]
    assert set(policy_axes) == set(axes)
    for name in policy_axes:
        _assert_closed_field(policy_axes, name, required=True)
    _assert_closed_field(
        policy_axes, "scope", required=True, values={"small", "medium", "large"}
    )
    _assert_closed_field(
        policy_axes, "complexity", required=True, values={"low", "high"}
    )
    _assert_closed_field(
        policy_axes, "testability", required=True, values={"none", "partial", "full"}
    )
    entry_fields = policy_fields["entries"]["items"]["fields"]
    assert tuple(entry_fields) == GATE_POLICY_FIELDS
    entry_requiredness = {
        name: name not in GATE_POLICY_OPTIONAL_FIELDS for name in GATE_POLICY_FIELDS
    }
    for name, required in entry_requiredness.items():
        _assert_closed_field(entry_fields, name, required=required)
    _assert_closed_field(
        entry_fields,
        "role",
        required=True,
        values={
            "spec-review",
            "plan-review",
            "review-test-scaffolding",
            "milestone-review",
            "peer-review-sequential",
            "behavior-review",
            "complexity-review",
            "robustness-analysis",
        },
    )
    _assert_closed_field(
        entry_fields, "scope", required=True, values={"feature", "milestone"}
    )
    _assert_closed_field(
        entry_fields,
        "mode",
        required=True,
        values={"off", "upper-limit", "convergence"},
    )
    reviewer_fields = entry_fields["primary"]["fields"]
    assert tuple(reviewer_fields) == REVIEWER_FIELDS
    for name in reviewer_fields:
        _assert_closed_field(reviewer_fields, name, required=True)
    secondary_fields = entry_fields["secondary"]["fields"]
    assert set(secondary_fields) == set(reviewer_fields)
    for name in secondary_fields:
        _assert_closed_field(secondary_fields, name, required=True)
    trigger_fields = entry_fields["trigger"]["fields"]
    assert tuple(trigger_fields) == TRIGGER_FIELDS
    for name in trigger_fields:
        _assert_closed_field(trigger_fields, name, required=True)
    assert prepare_schema["example"] and policy_schema["example"]
    for command, phrases in (
        (
            ["feature", "prepare", "--help"],
            ("reference", "summary", "scope_rationale", "example"),
        ),
        (
            ["feature", "policy", "--help"],
            ("role", "reasoning_effort", "trigger", "example"),
        ),
    ):
        code, output, error = run_cli(command)
        rendered = " ".join((output + error).split())
        assert code == 0
        for phrase in phrases:
            assert phrase in rendered, (
                f"FAIL AC-1: {' '.join(command)} omits public schema detail {phrase!r}"
            )

    host = blank_host(tmp_path, monkeypatch)
    (host / "brief.md").write_text(
        "# Synthetic research\nOne declared behavior.\n", encoding="utf-8"
    )
    prepared = application.execute(
        ops.FeaturePrepare("usability-example", "runtime", prepare_schema["example"])
    )
    assert prepared.ok, prepared.to_envelope()
    revision = prepared.data["revision"]
    confirmed = application.execute(
        ops.FeaturePolicy(
            "usability-example",
            policy_schema["example"],
            expect_revision=revision,
            dry_run=True,
        )
    )
    assert confirmed.ok and confirmed.data["wrote"], confirmed.to_envelope()
    assert not (host / "plans/usability-example/state.yaml").exists(), (
        "FAIL AC-3: a policy example used in preview must not grant approval"
    )


def test_ac1_ac3_disposition_contract_names_conditional_qualification(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-1/AC-3 red: evidence fields and qualifications are public and usable."""
    _host, state_path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in {0, 4} and result["ok"], result
    run_id = runs(state_path)[0]["run_id"]

    schema = _command("review disposition")["input_schema"]
    assert schema is not None, (
        "FAIL AC-1: review disposition has no public payload contract"
    )
    assert schema["id"] == "heddle.review-disposition-input/v1", (
        "FAIL AC-1: review disposition publishes the wrong payload contract"
    )
    item = schema["fields"]["dispositions"]["items"]
    fields = item["fields"]
    assert set(fields) == {
        "run_id",
        "finding_id",
        "status",
        "evidence_kind",
        "references",
        "reason",
        "requires_inspection",
        "decision_id",
        "review_run_id",
        "verification_scope",
    }
    required_disposition_fields = {
        "run_id",
        "finding_id",
        "status",
        "evidence_kind",
        "references",
        "reason",
    }
    for name in fields:
        _assert_closed_field(fields, name, required=name in required_disposition_fields)
    _assert_closed_field(
        fields,
        "status",
        required=True,
        values={"retained", "addressed", "awaiting-decision", "settled"},
    )
    _assert_closed_field(
        fields,
        "evidence_kind",
        required=True,
        values={
            "inspection",
            "contract",
            "verification",
            "review",
            "decision",
            "unavailable",
        },
    )
    notes = " ".join(schema["notes"]).casefold()
    assert all(
        phrase in notes
        for phrase in (
            "coverage",
            "review_run_id",
            "verification_scope",
            "inspection",
        )
    ), "FAIL AC-3: conditional evidence qualification is not documented"
    example = schema["example"]
    example["dispositions"][0]["run_id"] = run_id
    example["dispositions"][0]["references"] = ["src/example.py"]
    accepted = application.execute(
        ops.RecordReviewDisposition(
            feature=V7_FEATURE,
            payload=example,
            expect_revision=yaml.safe_load(state_path.read_text())["revision"],
            dry_run=True,
        )
    )
    assert accepted.ok, accepted.to_envelope()


def test_ac3_help_and_briefings_explain_approval_revision_and_evidence_qualification(
    run_cli,
) -> None:
    """AC-3 red: public guidance distinguishes recommendation from authority."""
    rendered = []
    for argv in (
        ["feature", "prepare", "--help"],
        ["feature", "policy", "--help"],
        ["review", "disposition", "--help"],
    ):
        code, output, error = run_cli(argv)
        assert code == 0
        rendered.append(output + error)
    rendered.extend(
        (REPO / "heddle/resources" / name).read_text(encoding="utf-8")
        for name in (
            "specify.briefing.md",
            "spec-review.briefing.md",
            "plan-review.briefing.md",
            "peer-review.briefing.md",
        )
    )
    guidance = " ".join(" ".join(rendered).split()).casefold()
    for phrase in (
        "recommendation",
        "does not grant approval",
        "complete policy",
        "owner revision",
        "@coverage",
        "review_run_id",
        "verification_scope",
        "requires_inspection",
        "clean later report",
    ):
        assert phrase in guidance, f"FAIL AC-3: public guidance omits {phrase!r}"


def test_ac2_policy_action_uses_its_emitted_command_and_real_input_channel(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-2 red: suggested commands are shell-safe parser-ready spellings."""
    host = blank_host(tmp_path, monkeypatch)
    (host / "brief.md").write_text("# Research\nOne declared behavior.\n")
    prepared = application.execute(
        ops.FeaturePrepare("quoted-feature", "runtime", prepare_input())
    )
    action = prepared.next_actions[0].action
    assert isinstance(action, ops.AuthoringAction)

    rendered = ops.action_command(action)
    assert action.work == "feature-policy"
    assert action.feature == "quoted-feature"
    assert action.expected_revision == prepared.data["revision"]
    assert shlex.split(rendered)[1:5] == [
        "feature",
        "policy",
        "quoted-feature",
        "--from-file",
    ]

    policy = _command("feature policy")["input_schema"]["example"]
    before = snapshot(host)
    monkeypatch.setattr(sys, "stdin", io.StringIO(yaml.safe_dump(policy)))
    argv = shlex.split(rendered)[1:] + ["--dry-run", "--json"]
    code, output, _error = run_cli(argv)
    assert code == 0 and '"ok": true' in output, (
        "FAIL AC-2: the advertised policy command did not reach its real parser"
    )
    assert snapshot(host) == before, "FAIL AC-2: policy preview wrote host state"
    stale = [
        str(action.expected_revision - 1)
        if part == str(action.expected_revision)
        else part
        for part in shlex.split(rendered)[1:]
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO(yaml.safe_dump(policy)))
    code, output, _error = run_cli(stale + ["--dry-run", "--json"])
    assert code == 5 and json.loads(output)["error"]["code"] == "conflict"
    assert snapshot(host) == before, "FAIL AC-2: stale policy preview wrote host state"


def test_ac2_survivor_emitted_disposition_action_is_dry_run_safe_and_cas_guarded(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-2 survivor: the actual disposition route retains parser and CAS identity."""
    host, state_path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    status = application.execute(ops.Status(feature=V7_FEATURE))
    action = status.next_actions[0].action
    assert isinstance(action, ops.AuthoringAction)
    assert action.work == "review-disposition"
    assert (
        action.expected_revision == yaml.safe_load(state_path.read_text())["revision"]
    )
    command = shlex.split(ops.action_command(action))[1:]
    payload = {
        "schema": "heddle.review-disposition-input/v1",
        "dispositions": [disposition(runs(state_path)[0]["run_id"], "SP-I1")],
    }
    before = state_path.read_bytes()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, output, _error = run_cli(command + ["--dry-run", "--json"])
    assert code == 0 and json.loads(output)["ok"]
    assert state_path.read_bytes() == before
    stale = [
        str(action.expected_revision - 1)
        if part == str(action.expected_revision)
        else part
        for part in command
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, output, _error = run_cli(stale + ["--dry-run", "--json"])
    assert code == 5 and json.loads(output)["error"]["code"] == "conflict"
    assert state_path.read_bytes() == before


def test_ac2_survivor_emitted_interpretation_action_is_dry_run_safe_and_cas_guarded(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-2 survivor: retained-response routing reaches the interpretation parser."""
    from tests.runtime.test_review_retention import retained_host

    host, state_path, _calls, _data, payload = retained_host(
        tmp_path, monkeypatch, run_cli
    )
    action = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0].action
    assert isinstance(action, ops.AuthoringAction)
    assert action.work == "review-interpretation"
    command = shlex.split(ops.action_command(action))[1:]
    before = state_path.read_bytes()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, output, _error = run_cli(command + ["--dry-run", "--json"])
    assert code == 0 and json.loads(output)["ok"]
    assert state_path.read_bytes() == before
    stale = [
        str(action.expected_revision - 1)
        if part == str(action.expected_revision)
        else part
        for part in command
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, output, _error = run_cli(stale + ["--dry-run", "--json"])
    assert code == 5 and json.loads(output)["error"]["code"] == "conflict"
    assert state_path.read_bytes() == before


def test_ac1_survivor_unknown_prepare_field_is_rejected_without_writing(
    tmp_path, monkeypatch
) -> None:
    """AC-1 survivor: closed payload validation remains fail-loud."""
    host = blank_host(tmp_path, monkeypatch)
    (host / "brief.md").write_text("# Research\nOne declared behavior.\n")
    payload = prepare_input()
    payload["invented"] = True
    result = application.execute(
        ops.FeaturePrepare("closed-contract", "runtime", payload)
    )
    assert not result.ok and result.error.code == "workspace-invalid"
    assert not (host / ".heddle/intake/closed-contract.yaml").exists()
    malformed = application.execute(
        ops.FeaturePrepare(
            "malformed-contract",
            "runtime",
            prepare_input(route="unsupported-route"),
        )
    )
    assert not malformed.ok and malformed.error.code == "workspace-invalid"
    assert not (host / ".heddle/intake/malformed-contract.yaml").exists()


def test_ac1_survivor_all_authoring_decoders_reject_unknown_fields(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-1 survivor: every advertised payload remains closed at its real decoder."""
    host = blank_host(tmp_path, monkeypatch)
    (host / "brief.md").write_text("# Research\nOne declared behavior.\n")
    prepare = prepare_input()
    prepared = application.execute(
        ops.FeaturePrepare("decoder-probe", "runtime", prepare)
    )
    assert prepared.ok
    policy = wire_policy()
    policy["invented"] = True
    rejected = application.execute(
        ops.FeaturePolicy(
            "decoder-probe",
            policy,
            expect_revision=prepared.data["revision"],
            dry_run=True,
        )
    )
    assert not rejected.ok and rejected.error.code == "workspace-invalid"
    malformed_policy = wire_policy()
    malformed_policy["entries"][0]["mode"] = "unsupported-mode"
    rejected = application.execute(
        ops.FeaturePolicy(
            "decoder-probe",
            malformed_policy,
            expect_revision=prepared.data["revision"],
            dry_run=True,
        )
    )
    assert not rejected.ok and rejected.error.code == "workspace-invalid"
    empty_trigger_policy = wire_policy()
    robustness = next(
        entry
        for entry in empty_trigger_policy["entries"]
        if entry["role"] == "robustness-analysis"
    )
    robustness.update(
        mode="upper-limit",
        limit=2,
        minimum_rounds=1,
        trigger={
            "gate": "robustness-analysis",
            "gap": "Synthetic cross-owner integration gap",
            "references": [],
        },
    )
    before = snapshot(host)
    rejected = application.execute(
        ops.FeaturePolicy(
            "decoder-probe",
            empty_trigger_policy,
            expect_revision=prepared.data["revision"],
            dry_run=True,
        )
    )
    assert not rejected.ok and rejected.error.code == "workspace-invalid"
    assert snapshot(host) == before

    review_root = tmp_path / "review"
    review_root.mkdir()
    _host, state_path = current_host(review_root, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"]
    disposition_payload = {
        "schema": "heddle.review-disposition-input/v1",
        "dispositions": [disposition(runs(state_path)[0]["run_id"], "SP-I1")],
    }
    disposition_payload["invented"] = True
    rejected = application.execute(
        ops.RecordReviewDisposition(
            feature=V7_FEATURE,
            payload=disposition_payload,
            expect_revision=yaml.safe_load(state_path.read_text())["revision"],
            dry_run=True,
        )
    )
    assert not rejected.ok and rejected.error.code == "workspace-invalid"
    malformed_disposition = {
        "schema": "heddle.review-disposition-input/v1",
        "dispositions": [
            disposition(
                runs(state_path)[0]["run_id"],
                "SP-I1",
                evidence_kind="unsupported-evidence",
            )
        ],
    }
    rejected = application.execute(
        ops.RecordReviewDisposition(
            feature=V7_FEATURE,
            payload=malformed_disposition,
            expect_revision=yaml.safe_load(state_path.read_text())["revision"],
            dry_run=True,
        )
    )
    assert not rejected.ok and rejected.error.code == "disposition-batch-invalid"
