"""AC-3/5/6/7 composed gate/recording boundaries; only provider transport faked."""

import io
import json
import sys
from collections import Counter

import pytest
import yaml

from tests.structured_review_helpers import finding
from tests.tiering_helpers import (
    ASTRA,
    FABLE,
    OPUS,
    RETIRED,
    SOL,
    SOL_XHIGH,
    entry,
    invoke,
    snapshot,
)
from tests.tiering_review_helpers import (
    V7_FEATURE,
    amend,
    current_host,
    dispose,
    disposition,
    gate_command,
    open_round,
    provider_transport,
    review_content,
    review_status,
    runs,
)


def test_verification_disposition_without_scope_is_structured_and_write_free(
    tmp_path, monkeypatch, run_cli
):
    host, state_path = current_host(tmp_path, monkeypatch, stage="spec-review")
    calls = provider_transport(
        monkeypatch,
        review_content(
            "spec-review", findings=[finding("SP-I1", classification="implement")]
        ),
    )
    code, review = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4) and review["ok"] and len(calls) == 1
    before = snapshot(host)
    result = invoke(
        "RecordReviewDisposition",
        feature=V7_FEATURE,
        payload={
            "schema": "heddle.review-disposition-input/v1",
            "dispositions": [
                {
                    "run_id": review["data"]["run_id"],
                    "finding_id": "SP-I1",
                    "status": "addressed",
                    "evidence_kind": "verification",
                    "references": ["src/example.py"],
                    "reason": "A verification claim must name its exact scope.",
                }
            ],
        },
        expect_revision=yaml.safe_load(state_path.read_text())["revision"],
    )
    assert not result.ok and result.error.code == "disposition-batch-invalid"
    assert result.error.details["rows"] == [
        {
            "row_index": 0,
            "run_id": review["data"]["run_id"],
            "finding_id": "SP-I1",
            "evidence_kind": "verification",
            "predicate": "verification-scope-missing",
            "field": "verification_scope",
            "reference": None,
            "verification_scope": None,
            "status": None,
            "remedy": "supply a nonempty verification_scope",
        }
    ]
    assert snapshot(host) == before and len(calls) == 1


def test_disposition_batch_reports_each_rejected_row_once_without_writing(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[
            finding(identifier, classification="implement")
            for identifier in ("SP-I1", "SP-I2")
        ],
    )
    origin = runs(path)[0]["run_id"]
    rows = [
        disposition(origin, "SP-I1"),
        disposition("00000000-0000-4000-8000-ffffffffffff", "SP-I2"),
        disposition(origin, "SP-I2", references=[]),
        disposition(origin, "SP-I9", evidence_kind="unsupported"),
        disposition(origin, "@coverage", status="settled"),
        disposition(origin, "@coverage", status="settled"),
    ]
    before = snapshot(host)

    preview = dispose(path, rows, dry_run=True)
    applied = dispose(path, rows)

    assert not preview.ok and not applied.ok
    assert preview.error.code == applied.error.code == "disposition-batch-invalid"
    assert preview.error.details | {"dry_run": False} == applied.error.details
    details = applied.error.details
    assert details | {"rows": None} == {
        "feature": V7_FEATURE,
        "revision": yaml.safe_load(path.read_text())["revision"],
        "dry_run": False,
        "row_count": 6,
        "rejected_count": 5,
        "wrote": False,
        "rows": None,
    }
    assert [row["row_index"] for row in details["rows"]] == [1, 2, 3, 4, 5]
    assert [row["predicate"] for row in details["rows"]] == [
        "unknown-origin",
        "missing-evidence",
        "malformed-field",
        "duplicate-origin",
        "duplicate-origin",
    ]
    assert details["rows"][2]["evidence_kind"] is None
    assert details["rows"][3]["reference"] == "5"
    assert details["rows"][4]["reference"] == "4"
    assert snapshot(host) == before and len(calls) == 1


def test_disposition_batch_exposes_existing_verification_status_only_on_bad_row(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[
            finding(identifier, classification="implement")
            for identifier in ("SP-I1", "SP-I2")
        ],
    )
    origin = runs(path)[0]["run_id"]
    code, verified = gate_command(run_cli, "verify", "--scope", "m1")
    assert code == 0 and verified["ok"], verified
    (host / "src/example.py").write_text("VALUE = 8\n")
    before = snapshot(host)

    result = dispose(
        path,
        [
            disposition(
                origin,
                "SP-I1",
                evidence_kind="verification",
                verification_scope="m1",
            ),
            disposition(origin, "SP-I2"),
        ],
    )

    assert not result.ok and result.error.code == "disposition-batch-invalid"
    assert result.error.details["rejected_count"] == 1
    row = result.error.details["rows"][0]
    assert (row["row_index"], row["predicate"]) == (
        0,
        "verification-not-qualifying",
    )
    assert row["verification_scope"] == row["reference"] == "m1"
    assert row["status"] == "content-stale"
    assert snapshot(host) == before and len(calls) == 1


def test_disposition_batch_human_output_uses_one_based_rows(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    origin = runs(path)[0]["run_id"]
    payload = {
        "schema": "heddle.review-disposition-input/v1",
        "dispositions": [
            disposition(origin, references=[]),
            disposition("00000000-0000-4000-8000-ffffffffffff"),
        ],
    }
    before = snapshot(host)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    code, out, err = run_cli(
        [
            "review",
            "disposition",
            "--feature",
            V7_FEATURE,
            "--input-json",
            "-",
            "--expect-revision",
            str(yaml.safe_load(path.read_text())["revision"]),
        ]
    )

    assert code == 3 and out == ""
    assert "row 1: missing-evidence:" in err
    assert "row 2: unknown-origin:" in err
    assert snapshot(host) == before and len(calls) == 1


def test_disposition_qualification_and_read_explanation_share_one_result(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops
    from heddle.kernel import review_assignments as core
    from heddle.runtime import review_assignments
    from heddle.runtime.feature_context import resolve_snapshot_from_cwd

    host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    origin = runs(path)[0]["run_id"]
    assert dispose(path, [disposition(origin)]).ok
    (host / "src/example.py").write_text("VALUE = 8\n")
    resolved = resolve_snapshot_from_cwd(V7_FEATURE)
    frame = review_assignments._Qualification(resolved.config, resolved.snapshot)
    assignment = core.assignment_for(resolved.snapshot.state, "spec-review", "feature")
    recorded = resolved.snapshot.state.review_assignments.dispositions[-1]

    qualified = review_assignments._qualify_evidence(
        frame, recorded, frame.basis(assignment)
    )
    explanation = review_assignments._evidence_explanation(
        frame,
        recorded,
        frame.basis(assignment),
        ops.ManualAction("resubmit the disposition"),
    )

    assert qualified.predicate == "reference-changed"
    assert explanation.cause == qualified.cause == "referenced evidence changed"
    assert (
        explanation.changed_dependencies
        == qualified.changed_dependencies
        == ("src/example.py",)
    )
    assert explanation.failed_reference is None
    assert len(calls) == 1


@pytest.mark.parametrize("opening", [False, True])
def test_ac7_native_review_cli_and_typed_preview_share_one_owner(
    tmp_path, monkeypatch, run_cli, opening
):
    host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    origin = runs(path)[0]["run_id"]
    if opening:
        payload = {
            "schema": "heddle.review-round-input/v1",
            "role": "spec-review",
            "scope": "feature",
            "purpose": "verification",
            "reason": "Inspect the original SP-I1 counterexample",
        }
        name, verb = "ReviewRoundOpen", "round-open"
    else:
        payload = {
            "schema": "heddle.review-disposition-input/v1",
            "dispositions": [disposition(origin)],
        }
        name, verb = "RecordReviewDisposition", "disposition"
    revision = yaml.safe_load(path.read_text())["revision"]
    before = snapshot(host)
    typed = invoke(
        name,
        feature=V7_FEATURE,
        payload=payload,
        expect_revision=revision,
        dry_run=True,
    )
    assert typed.ok, typed.to_envelope()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, out, _err = run_cli(
        [
            "review",
            verb,
            "--feature",
            V7_FEATURE,
            "--input-json",
            "-",
            "--expect-revision",
            str(revision),
            "--dry-run",
            "--json",
        ]
    )
    assert code == 0, out
    assert json.loads(out) == typed.to_envelope()
    assert snapshot(host) == before and len(calls) == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, out, _err = run_cli(
        [
            "review",
            verb,
            "--feature",
            V7_FEATURE,
            "--input-json",
            "-",
            "--expect-revision",
            str(revision),
            "--json",
        ]
    )
    assert code == 0, out
    after = path.read_bytes()
    assert yaml.safe_load(after)["revision"] == revision + 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, out, _err = run_cli(
        [
            "review",
            verb,
            "--feature",
            V7_FEATURE,
            "--input-json",
            "-",
            "--expect-revision",
            str(revision),
            "--json",
        ]
    )
    assert code != 0 and not json.loads(out)["ok"]
    assert path.read_bytes() == after and len(calls) == 1


@pytest.mark.parametrize("verb", ["disposition", "round-open"])
@pytest.mark.parametrize("payload", ["[", "[]", '{"schema":"unknown"}'])
def test_ac7_malformed_review_cli_is_nonmutating(
    tmp_path, monkeypatch, run_cli, verb, payload
):
    host, _path = current_host(tmp_path, monkeypatch)
    before = snapshot(host)
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    code, out, _err = run_cli(
        [
            "review",
            verb,
            "--feature",
            V7_FEATURE,
            "--input-json",
            "-",
            "--json",
        ]
    )
    assert code != 0 and not json.loads(out)["ok"]
    assert snapshot(host) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "frozen-policy",
        "wrong-scope",
        "zero-attempt-round",
        "missing-attempt",
        "duplicate-attempt",
        "unknown-decision",
        "same-round-proof",
    ],
)
def test_ac6_decoder_rejects_forged_assignment_relationships(
    tmp_path, monkeypatch, run_cli, mutation
):
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.state import parse_state_document

    _host, path, _calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    origin = runs(path)[0]["run_id"]
    assert dispose(path, [disposition(origin)]).ok
    before = path.read_bytes()
    value = yaml.safe_load(before)
    ledger = value["review_assignments"]
    if mutation == "frozen-policy":
        ledger["assignments"][0]["rounds"][0]["slots"][0]["reviewer"]["model"] = (
            "forged"
        )
        value["gates"][0]["runs"][0]["execution_config"]["model"] = "forged"
    elif mutation == "wrong-scope":
        ledger["assignments"][0]["scope"] = "m1"
        value["gates"][0]["scope"] = "m1"
    elif mutation == "zero-attempt-round":
        ledger["attempts"][0]["round_number"] = 0
    elif mutation == "missing-attempt":
        ledger["attempts"] = []
    elif mutation == "duplicate-attempt":
        ledger["attempts"].append(ledger["attempts"][0])
    elif mutation == "unknown-decision":
        ledger["dispositions"][0].update(
            evidence_kind="decision", decision_id="decision-999"
        )
    else:
        ledger["dispositions"][0].update(evidence_kind="review", review_run_id=origin)
    with pytest.raises(KernelError, match="assignment|attempt"):
        parse_state_document(value, source=path)
    assert path.read_bytes() == before


def completed_report(tmp_path, monkeypatch, run_cli, *, findings=()):
    host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content(findings=findings))
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 3, 4) and result["ok"], result
    assert len(calls) == 1
    assert len(runs(path)) == 1
    return host, path, calls, result


def qualification_counts(monkeypatch):
    """Structural ownership/freshness witnesses, not elapsed-time benchmarks.

    The plan's qualification contract requires one shared read frame per
    command and a fresh frame after writing; these count the actual owners.
    """
    from heddle.io import source
    from heddle.runtime import review_assignments

    counts = {key: Counter() for key in ("captures", "bases", "closures", "canonical")}

    def observe(module, name, counter, key):
        original = getattr(module, name)

        def counted(*args, **kwargs):
            counts[counter][key(*args, **kwargs)] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)

    observe(source, "capture_path", "captures", lambda root, path: path)
    observe(
        review_assignments,
        "current_basis",
        "bases",
        lambda config, snap, assignment, **kw: assignment.id,
    )
    observe(
        review_assignments,
        "assess_review_closure",
        "closures",
        lambda facts: "assessment",
    )
    observe(
        review_assignments,
        "validate_sources",
        "canonical",
        lambda access: "snapshot",
    )
    return counts


@pytest.mark.parametrize("command", ["Status", "Orient"])
def test_ac7_read_qualification_is_once_per_scope_and_fresh_per_command(
    tmp_path, monkeypatch, run_cli, command
):
    host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    origin = runs(path)[0]["run_id"]
    assert dispose(
        path,
        [
            disposition(origin),
            disposition(origin, "@coverage", status="settled"),
        ],
    ).ok
    counts = qualification_counts(monkeypatch)
    before = path.read_bytes()
    for fresh in (True, False):
        if not fresh:
            (host / "src/example.py").write_text("VALUE = 2\n")
        for counter in counts.values():
            counter.clear()
        result = invoke(command, feature=V7_FEATURE)
        assert result.ok, result.to_envelope()
        rows = result.data["review_closure"]["assignments"]
        reviewed = next(row for row in rows if row["role"] == "spec-review")
        assert reviewed["closed"] is fresh
        if not fresh:
            assert [origin, "SP-I1"] in reviewed["open_refs"]
        assert counts["bases"] == Counter({reviewed["assignment_id"]: 1})
        assert counts["canonical"] == {"snapshot": 1}
        assert counts["closures"] == {"assessment": len(rows)}
        assert counts["captures"]["src/example.py"] == 1
        assert set(counts["captures"].values()) == {1}
        assert path.read_bytes() == before and len(calls) == 1


def test_ac6_batch_qualification_reuses_original_snapshot_then_refreshes_after_write(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[
            finding(identifier, classification="implement")
            for identifier in ("SP-I1", "SP-I2")
        ],
    )
    origin = runs(path)[0]["run_id"]
    counts = qualification_counts(monkeypatch)
    result = dispose(
        path,
        [
            disposition(origin, identifier)
            for identifier in ("SP-I1", "SP-I2", "@coverage")
        ],
    )
    assert result.ok, result.to_envelope()
    assert result.data["closure"]["closed"]
    rows = result.data["review_closure"]["assignments"]
    # Pre-write assignment once for all findings; a distinct post-write frame
    # sees the newly accepted dispositions and projects actual closure.
    assert counts["canonical"] == {"snapshot": 2}
    assert counts["closures"] == {"assessment": 1 + len(rows)}
    assignment_id = next(
        row["assignment_id"] for row in rows if row["role"] == "spec-review"
    )
    assert counts["bases"][assignment_id] == 2
    assert all(
        count == 1
        for identity, count in counts["bases"].items()
        if identity != assignment_id
    )
    assert counts["captures"]["src/example.py"] == 2
    assert len(calls) == 1 and (host / "src/example.py").is_file()


@pytest.mark.parametrize(
    "role,stage,reviewer",
    [
        ("spec-review", "spec-review", ASTRA),
        ("plan-review", "plan-review", FABLE),
        ("review-test-scaffolding", "scaffold", SOL_XHIGH),
        ("milestone-review", "implement", SOL),
        ("peer-review-sequential", "peer-review", OPUS),
        ("behavior-review", "peer-review", OPUS),
        ("complexity-review", "peer-review", SOL),
        ("robustness-analysis", "robustness", SOL_XHIGH),
    ],
)
def test_ac5_only_confirmed_role_tuple_reaches_real_adapter(
    tmp_path, monkeypatch, run_cli, role, stage, reviewer
):
    trigger = (
        {
            "gate": role,
            "gap": "Interrupted publication",
            "references": ["src/example.py"],
        }
        if role == "robustness-analysis"
        else None
    )
    _host, path = current_host(
        tmp_path,
        monkeypatch,
        stage=stage,
        overrides={role: entry(role, primary=reviewer, trigger=trigger)},
    )
    calls = provider_transport(monkeypatch, review_content(role))
    code, result = gate_command(run_cli, "show-prompt", role)
    assert code == 0, result
    for key, expected in reviewer.items():
        assert result["data"]["execution"][key] == expected
    code, result = gate_command(run_cli, "run-gate", role)
    assert code in (0, 4), result
    assert len(calls) == 1
    assert calls[0][0] == reviewer["cli"]
    assert reviewer["model"] in calls[0][2]
    assert (
        runs(path)[0]["execution_config"]["reasoning_effort"]
        == reviewer["reasoning_effort"]
    )


@pytest.mark.parametrize("role", RETIRED)
def test_ac5_retired_launch_refuses_before_capability_or_provider_work(
    tmp_path, monkeypatch, run_cli, role
):
    host, _path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content())
    before = snapshot(host)
    code, result = gate_command(run_cli, "run-gate", role)
    assert code != 0
    assert "retired" in (result["error"]["message"] + result["error"]["hint"]).lower()
    assert not calls
    assert snapshot(host) == before


def test_ac5_off_needs_no_capability_and_selected_provider_never_falls_back(
    tmp_path, monkeypatch, run_cli
):
    off = entry("spec-review", mode="off", limit=None, minimum_rounds=0)
    host, path = current_host(tmp_path, monkeypatch, overrides={"spec-review": off})
    calls = provider_transport(monkeypatch, review_content())
    monkeypatch.setattr("heddle.gate.runner._binary_available", lambda _name: False)
    before = snapshot(host)
    code, status = gate_command(run_cli, "status")
    assert code == 0, status
    code, refused = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0
    assert "off" in (refused["error"]["message"] + refused["error"]["hint"]).lower()
    assert not calls and snapshot(host) == before
    value = yaml.safe_load(path.read_text())
    value["feature_policy"]["entries"][0].update(
        mode="upper-limit", limit=2, minimum_rounds=1
    )
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    code, unavailable = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not calls
    assert "codex" in json.dumps(unavailable).lower()


@pytest.mark.parametrize("constraint", ["gate", "provider"])
def test_ac5_changed_host_constraint_refuses_before_execution_without_policy_rewrite(
    tmp_path, monkeypatch, run_cli, constraint
):
    host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content())
    config_path = host / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text())
    if constraint == "gate":
        config["gates"] = {"enabled": []}
    else:
        config["agents"] = {"codex": False}
    config_path.write_text(yaml.safe_dump(config))
    before = snapshot(host)
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and "host" in result["error"]["message"]
    assert not calls and snapshot(host) == before
    assert not runs(path)


def test_ac5_dual_contexts_are_independent_and_partial_round_blocks_disposition(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1, secondary=FABLE)},
    )
    secret_marker = "SECOND_REVIEWER_MUST_NOT_SEE_THIS_FRESH_FINDING"

    def report(cli, _prompt):
        items = (
            [finding("SP-I1", classification="implement", title=secret_marker)]
            if cli == "codex"
            else []
        )
        return review_content(findings=items)

    calls = provider_transport(monkeypatch, report)
    code, first = gate_command(run_cli, "run-gate", "spec-review", "--cli", "codex")
    assert code in (0, 4), first
    origin = runs(path)[0]["run_id"]
    before = path.read_bytes()
    early = dispose(path, [disposition(origin)])
    assert not early.ok
    assert path.read_bytes() == before
    code, second = gate_command(run_cli, "run-gate", "spec-review", "--cli", "claude")
    assert code in (0, 4), second
    assert len(calls) == 2
    assert secret_marker not in calls[1][1]
    accepted = runs(path)
    assert {run["round_number"] for run in accepted} == {1}
    assert len({run["assignment_id"] for run in accepted}) == 1
    assert {run["reviewer_slot"] for run in accepted} == {"primary", "secondary"}
    assert (host / "src/example.py").read_text() == "VALUE = 7\n"


def test_ac5_targeted_rounds_keep_all_origins_and_original_secondary_inspection(
    tmp_path, monkeypatch, run_cli
):
    from tests.structured_review_helpers import disposition as reviewer_disposition
    from tests.structured_review_helpers import finding_ref

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=4, secondary=FABLE)},
    )
    phase = {"value": 1}
    origins = {}

    def report(cli, _prompt):
        if phase["value"] == 1:
            identifier = "SP-I1" if cli == "codex" else "SP-I2"
            return review_content(
                findings=[
                    finding(
                        identifier,
                        classification="implement",
                        title="PRIMARY_ORIGINAL_CONCERN"
                        if cli == "codex"
                        else "SECONDARY_ORIGINAL_CONCERN",
                    )
                ]
            )
        result = review_content()
        result["prior_dispositions"] = [
            reviewer_disposition(finding_ref(run_id, identifier), action="addressed")
            for run_id, identifier in (
                (origins["primary"], "SP-I1"),
                (origins["secondary"], "SP-I2"),
                *((run["run_id"], "@coverage") for run in runs(path)),
            )
        ]
        return result

    calls = provider_transport(monkeypatch, report)
    for cli in ("codex", "claude"):
        code, result = gate_command(run_cli, "run-gate", "spec-review", "--cli", cli)
        assert code in (0, 4), result
    initial = runs(path)
    origins.update({row["reviewer_slot"]: row["run_id"] for row in initial})
    expected = {(origins["primary"], "SP-I1"), (origins["secondary"], "SP-I2")}
    recorded = dispose(
        path,
        [
            disposition(origins["primary"], status="retained"),
            disposition(
                origins["secondary"],
                "SP-I2",
                status="retained",
                requires_inspection=True,
            ),
            *[
                disposition(row["run_id"], "@coverage", status="settled")
                for row in initial
            ],
        ],
    )
    assert recorded.ok, recorded.to_envelope()
    assert expected <= {tuple(ref) for ref in recorded.data["closure"]["open_refs"]}
    routed = invoke("Status", feature=V7_FEATURE).next_actions[0]
    from heddle.contracts import operations as ops

    assert isinstance(routed.action, ops.CommandAction)
    assert isinstance(routed.action.operation, ops.ReviewRoundOpen)
    assert routed.action.operation.payload["purpose"] == "verification"
    assert amend(path, entry("spec-review", limit=4, primary=SOL), revision=2).ok
    phase["value"] = 2
    opened = open_round(
        path,
        reason=(
            "Verify both exact original concerns; secondary inspection remains required"
        ),
    )
    assert opened.ok, opened.to_envelope()
    code, result = gate_command(run_cli, "run-gate", "spec-review", "--cli", "codex")
    assert code in (0, 4), result
    second = runs(path)[-1]
    assert second["round_number"] == 2
    assert second["assignment_id"] == initial[0]["assignment_id"]
    assert second["execution_config"]["model"] == SOL["model"]
    assert len(calls) == 3
    prompt = calls[-1][1]
    assert "verification" in prompt.lower()
    for run_id, finding_id in expected:
        assert run_id in prompt and finding_id in prompt
    assert "SECONDARY_ORIGINAL_CONCERN" in prompt
    assert expected <= {tuple(ref) for ref in review_status(path)["open_refs"]}
    before = path.read_bytes()
    wrong = dispose(
        path,
        [
            disposition(
                origins["secondary"],
                "SP-I2",
                evidence_kind="review",
                review_run_id=second["run_id"],
            )
        ],
    )
    assert not wrong.ok and path.read_bytes() == before
    repaired_primary = dispose(
        path,
        [
            disposition(origins["primary"]),
            disposition(second["run_id"], "@coverage", status="settled"),
        ],
    )
    assert repaired_primary.ok and not repaired_primary.data["closure"]["closed"]
    assert [origins["secondary"], "SP-I2"] in repaired_primary.data["closure"][
        "open_refs"
    ]
    assert amend(path, entry("spec-review", limit=4, primary=FABLE), revision=3).ok
    phase["value"] = 3
    assert open_round(
        path, reason="Originating Fable independently verifies its remaining duty"
    ).ok
    code, result = gate_command(run_cli, "run-gate", "spec-review", "--cli", "claude")
    assert code in (0, 4), result
    third = runs(path)[-1]
    assert third["round_number"] == 3 and len(calls) == 4
    closed = dispose(
        path,
        [
            disposition(
                origins["secondary"],
                "SP-I2",
                evidence_kind="review",
                review_run_id=third["run_id"],
            ),
            disposition(third["run_id"], "@coverage", status="settled"),
        ],
    )
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    assert runs(path)[:2] == initial
    assert (host / "src/example.py").is_file()


def test_ac6_accepted_v3_result_and_exact_replay_add_no_call_event_or_decision(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, first = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="report")],
    )
    accepted = runs(path)[0]
    before = snapshot(host)
    code, replay = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), replay
    assert replay["data"]["cached"] is True
    assert len(calls) == 1 and snapshot(host) == before
    assert len(yaml.safe_load(path.read_text())["decisions"]) == 1
    assert accepted["assignment_id"] and accepted["round_number"] == 1
    assert accepted["reviewer_slot"] == "primary"
    # Canonical JSON, not the mutable Markdown rendering, is the source.
    canonical = list((host / "plans" / V7_FEATURE / "reviews").glob("*.review.json"))
    assert len(canonical) == 1
    bound = json.loads(canonical[0].read_text())
    assert bound["schema"] == "heddle.review-result/v3"
    assert bound["invocation"]["assignment_id"] == accepted["assignment_id"]
    assert first["data"]["cached"] is False


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_ac6_v3_markdown_view_has_no_closure_decision_or_replay_authority(
    tmp_path, monkeypatch, run_cli, mutation
):
    import hashlib

    host, path, calls, _first = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    accepted = runs(path)[0]
    origin = accepted["run_id"]
    closed = dispose(path, [disposition(origin), disposition(origin, "@coverage")])
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    canonical = path.parent / accepted["artifact"]
    raw = canonical.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == accepted["artifact_sha256"]
    view = canonical.with_name(canonical.name.removesuffix(".review.json") + ".md")
    assert view.is_file()
    expected = review_status(path)
    if mutation == "edit":
        view.write_text(
            "# FAIL\nCritical: invented blocker\nREPORT: require a new decision\n"
        )
    else:
        view.unlink()
    before = snapshot(host)
    for command in ("Status", "Orient"):
        observed = invoke(command, feature=V7_FEATURE)
        assert observed.ok, observed.to_envelope()
        selected = next(
            row
            for row in observed.data["review_closure"]["assignments"]
            if row["role"] == "spec-review"
        )
        assert selected == expected and selected["closed"]
    code, replay = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4) and replay["data"]["cached"], replay
    assert snapshot(host) == before and canonical.read_bytes() == raw
    assert len(calls) == 1 and runs(path) == [accepted]
    assert yaml.safe_load(path.read_text())["decisions"] == []


@pytest.mark.parametrize(
    "severity,classification,required",
    [
        ("minor", "report", False),
        ("important", "ignore", False),
        ("important", "implement", True),
        ("critical", "ignore", True),
    ],
)
def test_ac3_native_severity_translation_keeps_coverage_independent(
    tmp_path, monkeypatch, run_cli, severity, classification, required
):
    _host, path, calls, _first = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", severity=severity, classification=classification)],
    )
    accepted = runs(path)[0]
    origin = accepted["run_id"]
    content = json.loads((path.parent / accepted["artifact"]).read_text())["content"]
    assert content["findings"][0]["severity"] == severity
    assert content["findings"][0]["classification"] == classification
    initial = review_status(path)
    assert [origin, "@coverage"] in initial["open_refs"] and not initial["closed"]
    coverage = dispose(path, [disposition(origin, "@coverage", status="settled")])
    assert coverage.ok, coverage.to_envelope()
    assert coverage.data["closure"]["closed"] is not required
    assert coverage.data["closure"]["open_refs"] == (
        [[origin, "SP-I1"]] if required else []
    )
    assert yaml.safe_load(path.read_text())["decisions"] == []
    if required:
        settled = dispose(path, [disposition(origin)])
        assert settled.ok and settled.data["closure"]["closed"], settled.to_envelope()
    assert len(calls) == 1 and runs(path) == [accepted]


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-origin",
        "empty-evidence",
        "omitted-coverage",
        "canonical-tamper",
        "stale-revision",
    ],
)
def test_ac6_invalid_or_incomplete_native_dispositions_cannot_publish_closure(
    tmp_path, monkeypatch, run_cli, mutation
):
    host, path, _calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    origin = runs(path)[0]["run_id"]
    rows = [disposition(origin), disposition(origin, "@coverage", status="settled")]
    revision = None
    if mutation == "unknown-origin":
        rows[0]["run_id"] = "00000000-0000-4000-8000-ffffffffffff"
    elif mutation == "empty-evidence":
        rows[0]["references"] = []
    elif mutation == "omitted-coverage":
        rows.pop()
    elif mutation == "canonical-tamper":
        canonical = next(
            (host / "plans" / V7_FEATURE / "reviews").glob("*.review.json")
        )
        canonical.write_text(canonical.read_text().replace("VALUE", "TAMPERED"))
    else:
        revision = yaml.safe_load(path.read_text())["revision"] - 1
    before = path.read_bytes()
    result = dispose(path, rows, expect_revision=revision)
    if mutation == "omitted-coverage":
        assert result.ok and not result.data["closure"]["closed"], result.to_envelope()
    else:
        assert not result.ok
        assert path.read_bytes() == before


def test_ac3_native_lead_closes_bound_obligations_without_synthesis_or_extra_call(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    origin = runs(path)[0]["run_id"]
    rows = [disposition(origin), disposition(origin, "@coverage", status="settled")]
    preview_bytes = path.read_bytes()
    preview = dispose(path, rows, dry_run=True)
    assert preview.ok and path.read_bytes() == preview_bytes
    accepted = dispose(path, rows)
    assert accepted.ok, accepted.to_envelope()
    assert accepted.data["closure"]["closed"]
    after = snapshot(host)
    replay = dispose(path, rows)
    assert replay.ok, replay.to_envelope()
    assert snapshot(host) == after
    assert len(calls) == 1
    assert {gate["gate"] for gate in yaml.safe_load(path.read_text())["gates"]} == {
        "spec-review"
    }


def test_ac7_readers_agree_and_do_not_confirm_repair_or_launch(
    tmp_path, monkeypatch, run_cli
):
    host, _path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=7, primary=FABLE)},
    )
    calls = provider_transport(monkeypatch, review_content())
    before = snapshot(host)
    policies = []
    for command in ("status", "orient", "kickoff", "show-prompt"):
        args = (command, "spec-review") if command == "show-prompt" else (command,)
        code, result = gate_command(run_cli, *args)
        assert code == 0, result
        policies.append(result["data"]["effective_policy"])
    assert all(policy == policies[0] for policy in policies)
    row = next(row for row in policies[0]["entries"] if row["role"] == "spec-review")
    assert row["limit"] == 7 and row["primary"] == FABLE
    assert not calls and snapshot(host) == before


@pytest.mark.parametrize(
    "condition,next_step",
    [
        ("partial", "run"),
        ("pending-decision", "decision"),
        ("stale-evidence", "disposition"),
        ("spent", "decision"),
    ],
)
def test_ac7_blocked_readers_share_original_work_budget_and_legal_remedy(
    tmp_path, monkeypatch, run_cli, condition, next_step
):
    from heddle.contracts import operations as ops
    from heddle.driver import loop
    from heddle.runtime.application import execute
    from tests.readiness_helpers import current_readiness

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": entry(
                "spec-review",
                limit=1 if condition == "spent" else 3,
                secondary=FABLE if condition == "partial" else None,
            )
        },
    )
    calls = provider_transport(
        monkeypatch,
        review_content(
            findings=[
                finding(
                    "SP-I1",
                    classification="report"
                    if condition == "pending-decision"
                    else "implement",
                )
            ]
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review", "--cli", "codex")
    assert code in (0, 4), result
    origin = runs(path)[0]["run_id"]
    if condition in {"spent", "stale-evidence"}:
        recorded = dispose(
            path,
            [
                disposition(
                    origin, status="retained" if condition == "spent" else "addressed"
                ),
                disposition(origin, "@coverage", status="settled"),
            ],
        )
        assert recorded.ok, recorded.to_envelope()
        if condition == "stale-evidence":
            assert recorded.data["closure"]["closed"]
            (host / "src/example.py").write_text("VALUE = 8\n")
        else:
            assert not open_round(path).ok
    monkeypatch.setattr(loop, "probe_claude_capabilities", lambda: None)
    monkeypatch.setattr(loop, "probe_codex_capabilities", lambda: None)
    before = snapshot(host)
    projections = []
    for command, tail in (
        ("status", ()),
        ("orient", ()),
        ("kickoff", ()),
        ("show-prompt", ("spec-review",)),
        ("drive", ("--until", "spec-review")),
    ):
        code, result = gate_command(run_cli, command, *tail)
        assert code == 0, result
        projections.append(
            (result["data"]["effective_policy"], result["data"]["review_closure"])
        )
    typed = execute(ops.Status(feature=V7_FEATURE)).to_envelope()
    assert (
        typed["data"]["effective_policy"],
        typed["data"]["review_closure"],
    ) == projections[0]
    assert all(row == projections[0] for row in projections)
    selected = next(
        row for row in projections[0][1]["assignments"] if row["role"] == "spec-review"
    )
    assert not selected["closed"] and [origin, "SP-I1"] in selected["open_refs"]
    assert selected["rounds_used"] == selected["calls_completed"] == 1
    assert selected["rounds_remaining"] == (0 if condition == "spent" else 2)
    assert selected["next_step"] == next_step
    if condition == "partial":
        assert selected["missing_slots"] == ["secondary"]
    elif condition in {"pending-decision", "spent"}:
        owner = next(
            row
            for row in yaml.safe_load(path.read_text())["decisions"]
            if row["id"] == selected["decision_id"]
        )
        assert owner["status"] == "pending"
    readiness = current_readiness(host)
    assert readiness.blockers
    remedy = str(readiness.next_actions).lower()
    if condition == "partial":
        assert "claude" in remedy
    elif condition == "stale-evidence":
        assert "disposition" in remedy
    else:
        assert selected["decision_id"].lower() in remedy
    assert snapshot(host) == before and len(calls) == 1


def test_ac4_retained_output_is_not_new_important_discovery(
    tmp_path, monkeypatch, run_cli
):
    from tests.structured_review_helpers import disposition as prior_disposition
    from tests.structured_review_helpers import finding_ref

    _host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=9)},
    )
    original = {}

    def report(_cli, _prompt):
        result = review_content(findings=[finding("SP-I1", classification="implement")])
        if original:
            result["prior_dispositions"] = [
                prior_disposition(
                    finding_ref(original["run_id"], "SP-I1"),
                    target="SP-I1",
                )
            ]
        return result

    calls = provider_transport(monkeypatch, report)
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), result
    original.update(run_id=runs(path)[0]["run_id"])
    assert open_round(path, purpose="discovery").ok
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), result
    from heddle.kernel.state import read_state_file

    sources = read_state_file(path).review_assignments.sources
    assert [source.new_important for source in sources] == [1, 0]
    status = review_status(path)
    assert status["stop_reason"] is None and not status["closed"]
    assert [original["run_id"], "SP-I1"] in status["open_refs"]
    assert len(calls) == 2


def test_ac4_material_contract_change_is_explicit_and_not_ordinary_progress(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=9)},
    )
    calls = provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), result
    original = runs(path)[0]["run_id"]
    (host / "src/example.py").write_text("VALUE = 8\n")
    payload = {
        "schema": "heddle.review-round-input/v1",
        "role": "spec-review",
        "scope": "feature",
        "purpose": "discovery",
        "reason": "Review the explicitly changed VALUE contract",
        "scope_change": {
            "reason": "The required VALUE contract changed from seven to eight",
            "references": ["src/example.py"],
        },
    }
    opened = invoke(
        "ReviewRoundOpen",
        feature=V7_FEATURE,
        payload=payload,
        expect_revision=yaml.safe_load(path.read_text())["revision"],
    )
    assert opened.ok, opened.to_envelope()
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), result
    value = yaml.safe_load(path.read_text())
    rounds = value["review_assignments"]["assignments"][0]["rounds"]
    assert rounds[0]["scope_identity"] != rounds[1]["scope_identity"]
    assert rounds[1]["scope_change"]["reason"] == payload["scope_change"]["reason"]
    status = review_status(path)
    assert status["stop_reason"] is None and [original, "SP-I1"] in status["open_refs"]
    before = path.read_bytes()
    repeated = invoke(
        "ReviewRoundOpen",
        feature=V7_FEATURE,
        payload=payload,
        expect_revision=value["revision"],
    )
    assert not repeated.ok and "already captured" in repeated.error.message
    assert path.read_bytes() == before and len(calls) == 2


def test_ac4_round_creation_and_decoding_reject_over_budget_allocation(
    tmp_path, monkeypatch, run_cli
):
    from copy import deepcopy

    from heddle.kernel import review_assignments as core
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.state import parse_state_document

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    calls = provider_transport(monkeypatch, review_content())
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0, result
    original = yaml.safe_load(path.read_text())
    current = parse_state_document(original, source=path)
    assignment = current.review_assignments.assignments[0]
    before = snapshot(host)
    with pytest.raises(KernelError, match="confirmed round limit"):
        core.new_round(current, assignment)
    forged = deepcopy(original)
    extra = deepcopy(forged["review_assignments"]["assignments"][0]["rounds"][0])
    extra["number"] = 2
    forged["review_assignments"]["assignments"][0]["rounds"].append(extra)
    with pytest.raises(KernelError, match="confirmed round limit"):
        parse_state_document(forged, source=path)
    assert snapshot(host) == before and len(calls) == 1


@pytest.mark.parametrize("limit", [1, 3])
def test_ac4_closed_assignment_cannot_open_another_round(
    tmp_path, monkeypatch, run_cli, limit
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=limit)},
    )
    calls = provider_transport(monkeypatch, review_content())
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0, result
    origin = runs(path)[0]["run_id"]
    closed = dispose(path, [disposition(origin, "@coverage", status="settled")])
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    before = snapshot(host)
    refused = open_round(path, purpose="independent-pass")
    assert not refused.ok and "closed" in refused.error.message
    assert snapshot(host) == before
    code, replay = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and replay["data"]["cached"], replay
    assert runs(path)[0]["run_id"] == origin and len(calls) == 1


def test_ac7_fixed_pass_remedy_opens_then_executes_the_next_round(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops
    from heddle.driver import loop
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime.application import execute
    from tests.readiness_helpers import current_readiness

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=5, minimum_rounds=2)},
    )
    calls = provider_transport(monkeypatch, review_content())
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0, result
    original = runs(path)[0]
    disposed = dispose(
        path, [disposition(original["run_id"], "@coverage", status="settled")]
    )
    assert disposed.ok and not disposed.data["closure"]["closed"]
    before = snapshot(host)
    readiness = current_readiness(host)
    action = readiness.next_actions[0]
    assert isinstance(action.action, ops.CommandAction)
    assert isinstance(action.action.operation, ops.ReviewRoundOpen)
    config = load_project_config(host)
    current = resolve_snapshot(config, V7_FEATURE)
    routed = loop._next_command(config, current, None)
    assert routed[0] == action and snapshot(host) == before
    opened = execute(action.action.operation)
    assert opened.ok and opened.data["round_number"] == 2, opened.to_envelope()
    ready = current_readiness(host).next_actions[0]
    assert isinstance(ready.action.operation, ops.RunGate)
    code, second = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and not second["data"]["cached"], second
    accepted = runs(path)
    assert [row["round_number"] for row in accepted] == [1, 2]
    assert accepted[0] == original and len(calls) == 2
    closed = dispose(
        path, [disposition(accepted[-1]["run_id"], "@coverage", status="settled")]
    )
    assert closed.ok and closed.data["closure"]["closed"], closed.to_envelope()
    assert closed.data["closure"]["rounds_used"] == 2
    assert closed.data["closure"]["rounds_remaining"] == 3
    assert yaml.safe_load(path.read_text())["decisions"] == []


@pytest.mark.parametrize("stop", ["round-limit", "no-progress", "no-decrease"])
@pytest.mark.parametrize("generic_first", [False, True])
def test_ac4_native_stops_record_one_decision_and_preserve_original_refs(
    tmp_path, monkeypatch, run_cli, stop, generic_first
):
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute
    from tests.readiness_helpers import current_readiness

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": entry("spec-review", limit=1 if stop == "round-limit" else 9)
        },
    )
    generation = {"number": 1}

    def report(_cli, _prompt):
        from tests.structured_review_helpers import disposition as reviewer_disposition
        from tests.structured_review_helpers import finding_ref

        identifier = f"SP-I{generation['number']}"
        result = review_content(
            findings=(
                []
                if stop != "no-decrease" and generation["number"] > 1
                else [finding(identifier, classification="implement")]
            )
        )
        if generation["number"] > 1 and stop != "no-decrease":
            # Reviewer accounting does not replace the lead's retained resolution
            # or constitute material progress on that still-open original.
            result["prior_dispositions"] = [
                reviewer_disposition(
                    finding_ref(run_id, finding_id), action="addressed"
                )
                for run_id, finding_id in (
                    (origin, "SP-I1"),
                    *((run["run_id"], "@coverage") for run in runs(path)),
                )
            ]
        return result

    calls = provider_transport(monkeypatch, report)
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), result
    origin = runs(path)[0]["run_id"]
    assert dispose(
        path,
        [
            disposition(origin, status="retained"),
            disposition(origin, "@coverage", status="settled"),
        ],
    ).ok
    if stop != "round-limit":
        opened = open_round(
            path,
            purpose="discovery" if stop == "no-decrease" else "verification",
            reason="Explicit same-assignment comparison without a changed contract",
        )
        assert opened.ok, opened.to_envelope()
        generation["number"] = 2
        code, result = gate_command(run_cli, "run-gate", "spec-review")
        assert code in (0, 4), result
        latest = runs(path)[-1]["run_id"]
        assert dispose(path, [disposition(latest, "@coverage", status="settled")]).ok
    accepted = runs(path)
    before = snapshot(host)
    action = current_readiness(host).next_actions[0]
    assert isinstance(action.action, ops.CommandAction)
    assert isinstance(action.action.operation, ops.ReviewRoundOpen)
    assert snapshot(host) == before
    attempted = execute(action.action.operation)
    assert not attempted.ok
    status = review_status(path)
    assert status["stop_reason"] == stop and not status["closed"]
    assert [origin, "SP-I1"] in status["open_refs"]
    decisions = yaml.safe_load(path.read_text())["decisions"]
    owner = next(row for row in decisions if row["id"] == status["decision_id"])
    assert owner["status"] == "pending"
    assert owner["kind"] in {"verdict-cap", "stage-cap", "question"}
    assert any(owner["id"] in str(action) for action in attempted.next_actions)
    before = path.read_bytes()
    repeated = open_round(path)
    assert not repeated.ok and path.read_bytes() == before
    assert runs(path) == accepted
    assert len(calls) == (1 if stop == "round-limit" else 2)
    assert (host / "src/example.py").is_file()

    if generic_first:
        generic = execute(
            ops.ResolveDecision(
                decision_id=owner["id"],
                kind="disposition",
                rationale="Record the owner assessment; no continuation was authorized",
                routes_to="docs/features/runtime/operational-example.md",
                feature=V7_FEATURE,
            )
        )
        assert generic.ok, generic.to_envelope()
        disposition_route = review_status(path)
        assert disposition_route["next_step"] == "disposition"
        action = current_readiness(host).next_actions[0]
        assert isinstance(action.action, ops.AuthoringAction)
        assert action.action.work == "review-disposition"
        assert not open_round(path).ok
        replacement = review_status(path)["decision_id"]
        assert replacement not in (None, owner["id"])
        owner = next(
            d
            for d in yaml.safe_load(path.read_text())["decisions"]
            if d["id"] == replacement
        )

    resolution = ops.ResolveDecision(
        decision_id=owner["id"],
        kind="continue-review",
        rationale="Owner authorizes one more bounded inspection of the original work",
        routes_to="docs/features/runtime/operational-example.md",
        feature=V7_FEATURE,
    )
    resolved = execute(resolution)
    assert resolved.ok, resolved.to_envelope()
    if stop == "round-limit":
        blocked = open_round(path)
        assert not blocked.ok and "confirmed cap" in blocked.error.message
        assert "None" not in blocked.error.hint
        assert review_status(path)["next_step"] == "policy"
        assert isinstance(
            current_readiness(host).next_actions[0].action, ops.ManualAction
        )
        assert amend(path, entry("spec-review", limit=2), revision=2).ok
    assert review_status(path)["next_step"] == "run"
    purpose = "discovery" if stop == "no-decrease" else "verification"
    resumed = open_round(path, purpose=purpose)
    assert resumed.ok, resumed.to_envelope()
    generation["number"] += 1
    assert gate_command(run_cli, "run-gate", "spec-review")[0] in (0, 4)
    assert dispose(
        path, [disposition(runs(path)[-1]["run_id"], "@coverage", status="settled")]
    ).ok
    before = path.read_bytes()
    assert execute(resolution).ok  # Replay cannot buy another round.
    assert path.read_bytes() == before
    assert review_status(path)["stop_reason"] == stop
    assert not open_round(path, purpose=purpose).ok
    later = review_status(path)
    assert later["decision_id"] not in (None, owner["id"])
    decisions = yaml.safe_load(path.read_text())["decisions"]
    assert len(decisions) == 2 + generic_first
    assert decisions[-2]["status"] == "resolved"
    assert decisions[-2]["resolution"] == {
        "kind": "continue-review",
        "assignment_id": later["assignment_id"],
        "stopped_round": len(accepted),
    }
    assert decisions[-1]["status"] == "pending"
    assert runs(path)[: len(accepted)] == accepted
    assert [origin, "SP-I1"] in later["open_refs"]
    assert len(calls) == len(accepted) + 1


def test_policy_amendment_cannot_move_a_pending_stop_to_another_round(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    _host, path = current_host(
        tmp_path, monkeypatch, overrides={"spec-review": entry("spec-review", limit=1)}
    )
    calls = provider_transport(monkeypatch, review_content())
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 0
    assert not open_round(path).ok
    owner = review_status(path)["decision_id"]
    assert amend(path, entry("spec-review", limit=2), revision=2).ok
    before = path.read_bytes()
    blocked = open_round(path)
    assert not blocked.ok and owner in blocked.error.message
    assert path.read_bytes() == before and len(calls) == 1
    resolution = execute(
        ops.ResolveDecision(
            decision_id=owner,
            kind="disposition",
            rationale="Owner confirms the explicit cap amendment for remaining work",
            routes_to="docs/features/runtime/operational-example.md",
            feature=V7_FEATURE,
        )
    )
    assert resolution.ok, resolution.to_envelope()
    assert open_round(path).ok


def test_ac4_native_amendments_keep_consumption_and_only_explicit_increase_adds_budget(
    tmp_path, monkeypatch, run_cli
):
    _host, path, calls, _result = completed_report(
        tmp_path,
        monkeypatch,
        run_cli,
        findings=[finding("SP-I1", classification="implement")],
    )
    origin = runs(path)[0]["run_id"]
    initial = review_status(path)
    initial_assignment = initial["assignment_id"]
    assert initial["rounds_used"] == 1
    for revision, selection, remaining in (
        (2, entry("spec-review", mode="off", limit=None, minimum_rounds=0), 0),
        (3, entry("spec-review", primary=FABLE, limit=2), 1),
        (4, entry("spec-review", primary=FABLE, limit=7), 6),
    ):
        result = amend(path, selection, revision=revision)
        assert result.ok, result.to_envelope()
        current = review_status(path)
        assert current["assignment_id"] == initial_assignment
        assert current["rounds_used"] == current["calls_completed"] == 1
        assert current["rounds_remaining"] == remaining
        assert [origin, "SP-I1"] in current["open_refs"]
        assert not current["closed"]
    assert len(calls) == 1 and len(runs(path)) == 1
    state = yaml.safe_load(path.read_text())
    assert state["feature_policy"]["revision"] == 4
    assert [row["revision"] for row in state["policy_history"]] == [1, 2, 3, 4]


def test_ac4_provider_error_retry_fills_same_quality_round(
    tmp_path, monkeypatch, run_cli
):
    _host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content(), error_attempts=1)
    failed_code, failed = gate_command(run_cli, "run-gate", "spec-review")
    assert failed_code != 0, failed
    failed_runs = runs(path)
    assert len(failed_runs) == 1
    assert failed_runs[0]["verdict"]["status"] == "error"
    code, retried = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0, retried
    recorded = runs(path)
    assert len(recorded) == 2 and len(calls) == 2
    assert {row["round_number"] for row in recorded} == {1}
    assert len({row["assignment_id"] for row in recorded}) == 1
    assert sum(row["verdict"]["status"] != "error" for row in recorded) == 1


def test_ac4_confirmed_rounds_exceed_legacy_auto_cap_without_spending_sessions(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from tests.readiness_helpers import current_readiness
    from tests.structured_review_helpers import disposition as retained_finding
    from tests.structured_review_helpers import finding_ref

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=9, secondary=FABLE)},
    )

    def response(_cli, _prompt):
        content = review_content(
            findings=[finding("SP-I1", classification="implement")]
        )
        previous = [run for run in runs(path) if run["cli"] == _cli]
        if previous:
            content["prior_dispositions"] = [
                retained_finding(
                    finding_ref(previous[-1]["run_id"], "SP-I1"), target="SP-I1"
                )
            ]
        return content

    calls = provider_transport(monkeypatch, response)
    for number in range(1, 6):
        if number > 1:
            opened = open_round(path, purpose="independent-pass")
            assert opened.ok, opened.to_envelope()
        for cli in ("codex", "claude") if number == 1 else ("codex",):
            code, result = gate_command(
                run_cli, "run-gate", "spec-review", "--cli", cli
            )
            assert code in (0, 4) and result["ok"], result
        current = resolve_snapshot(load_project_config(host), V7_FEATURE)
        assert current.state.flow == "auto"
        assert "review-cap-reached" not in current.blocking_conditions
        assert current.state.sessions == ()
    assert len(calls) == 6
    counted = review_status(path)
    assert (
        counted["rounds_used"],
        counted["calls_completed"],
        counted["rounds_remaining"],
    ) == (5, 6, 4)
    opened = open_round(path, purpose="independent-pass")
    assert opened.ok, opened.to_envelope()
    ready = current_readiness(host)
    assert isinstance(ready.next_actions[0].action.operation, ops.RunGate)
    recorded = runs(path)
    accounting = review_status(path)
    for number in range(5):
        revision = yaml.safe_load(path.read_text())["revision"]
        logged = invoke(
            "RecordSession",
            feature=V7_FEATURE,
            session=ops.SessionInput(
                f"2026-09-11T12:{number:02d}Z",
                f"2026-09-11T12:{number:02d}Z",
                (f"Progress checkpoint {number}",),
                (),
                "Quality calls remain accounted",
                "Continue the separately approved quality round",
                (),
                stage="spec-review",
                revision_before=revision,
                revision_after=revision,
                advanced=True,
            ),
        )
        assert logged.ok, logged.to_envelope()
        state = resolve_snapshot(load_project_config(host), V7_FEATURE)
        assert ("attempt-cap" in state.blocking_conditions) is (number == 4)
        assert "no-progress" not in state.blocking_conditions
        assert "review-cap-reached" not in state.blocking_conditions
    assert review_status(path) == accounting
    assert runs(path) == recorded and len(calls) == 6
    attempts = yaml.safe_load(path.read_text())["review_assignments"]["attempts"]
    indexed_logs = [
        artifact
        for attempt in attempts
        for artifact in attempt["artifacts"]
        if artifact["role"] == "log"
    ]
    assert len(indexed_logs) == 6
    assert len({artifact["path"] for artifact in indexed_logs}) == 6
    assert all(artifact["sha256"] in artifact["path"] for artifact in indexed_logs)
    assert all(
        not artifact["path"].endswith(("spec-review.log", "spec-review.codex.log"))
        for artifact in indexed_logs
    )
    assert all(
        artifact["path"]
        not in {
            "reviews/spec-review.gate-summary.json",
            "reviews/spec-review.codex.gate-summary.json",
        }
        for attempt in attempts
        for artifact in attempt["artifacts"]
    )


def test_ac4_provider_error_budget_stays_separate_from_confirmed_quality_allowance(
    tmp_path, monkeypatch, run_cli
):
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    host, path = current_host(
        tmp_path, monkeypatch, overrides={"spec-review": entry("spec-review", limit=9)}
    )
    calls = provider_transport(monkeypatch, review_content(), error_attempts=5)
    for _attempt in range(5):
        code, result = gate_command(run_cli, "run-gate", "spec-review")
        assert code != 0, result
    state = resolve_snapshot(load_project_config(host), V7_FEATURE)
    assert "gate-cap-reached" in state.blocking_conditions
    assert "review-cap-reached" not in state.blocking_conditions
    assert "attempt-cap" not in state.blocking_conditions
    status = review_status(path)
    assert (status["rounds_used"], status["calls_completed"]) == (1, 0)
    assert status["missing_slots"] == ["primary"]
    assert len(calls) == len(runs(path)) == 5 and state.state.sessions == ()


@pytest.mark.parametrize("operation", ["disposition", "policy"])
def test_ac7_locked_transform_preserves_same_revision_native_secondary_append(
    tmp_path, monkeypatch, run_cli, operation
):
    from heddle.runtime import feature_policy, review_assignments

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", secondary=FABLE)},
    )
    calls = provider_transport(monkeypatch, review_content())
    code, result = gate_command(run_cli, "run-gate", "spec-review", "--cli", "codex")
    assert code == 0, result
    primary = runs(path)[0]
    revision = yaml.safe_load(path.read_text())["revision"]
    rows = [disposition(primary["run_id"], "@coverage", status="settled")]
    if operation == "disposition":
        before = snapshot(host)
        refused = dispose(path, rows, expect_revision=revision)
        assert not refused.ok and refused.error.code == "disposition-batch-invalid"
        assert refused.error.details["rows"][0]["predicate"] == "incomplete-round"
        assert "secondary" in refused.error.details["rows"][0]["remedy"]
        assert snapshot(host) == before
    owner = review_assignments if operation == "disposition" else feature_policy
    commit = owner.commit_state
    concurrent = {}

    def append_before_lock(*args, **kwargs):
        # Inject only the scheduling interleaving. The second provider transport,
        # canonical acceptance and revisioned append_state path all execute normally.
        code, accepted = gate_command(
            run_cli, "run-gate", "spec-review", "--cli", "claude"
        )
        assert code == 0, accepted
        current = yaml.safe_load(path.read_text())
        assert current["revision"] == revision + 1
        concurrent.update(state=current, run=runs(path)[-1])
        return commit(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(owner, "commit_state", append_before_lock)
        changed = (
            dispose(path, rows, expect_revision=revision)
            if operation == "disposition"
            else amend(path, entry("spec-review", limit=7, secondary=FABLE), revision=2)
        )
    assert not changed.ok and changed.exit_code == 5, changed.to_envelope()
    after = yaml.safe_load(path.read_text())
    assert after["revision"] == revision + 1
    assert runs(path) == [primary, concurrent["run"]]
    assert (
        after["review_assignments"]["attempts"]
        == concurrent["state"]["review_assignments"]["attempts"]
    )
    assert (
        after["review_assignments"]["assignments"]
        == concurrent["state"]["review_assignments"]["assignments"]
    )
    status = review_status(path)
    assert (status["rounds_used"], status["calls_completed"]) == (1, 2)
    assert [concurrent["run"]["run_id"], "@coverage"] in status["open_refs"]
    assert [primary["run_id"], "@coverage"] in status["open_refs"]
    assert after["feature_policy"]["revision"] == 1
    assert len(calls) == 2


@pytest.mark.parametrize("operation", ["disposition", "policy"])
def test_ac2_snapshot_conflicts_after_revisioned_native_secondary_append(
    tmp_path, monkeypatch, run_cli, operation
):
    """Current name for the historical interleaving after the revision cutover."""
    test_ac7_locked_transform_preserves_same_revision_native_secondary_append(
        tmp_path, monkeypatch, run_cli, operation
    )


@pytest.mark.parametrize(
    "mutation", ["missing-dimension", "duplicate-ac", "source-changed"]
)
def test_ac6_invalid_output_or_changed_source_cannot_publish_accepted_authority(
    tmp_path, monkeypatch, run_cli, mutation
):
    host, path = current_host(tmp_path, monkeypatch)

    def report(_cli, _prompt):
        payload = review_content()
        if mutation == "missing-dimension":
            payload["details"]["dimensions"].pop()
        elif mutation == "duplicate-ac":
            payload["details"]["ac_specificity"] *= 2
        else:
            spec = host / "docs/features/runtime" / f"{V7_FEATURE}.md"
            spec.write_text(spec.read_text() + "\nExpected: The value is now 9.\n")
        return payload

    provider_transport(monkeypatch, report)
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0, result
    assert not [row for row in runs(path) if row["verdict"]["status"] != "error"]
    assert yaml.safe_load(path.read_text())["decisions"] == []


@pytest.mark.parametrize("selected,codex_probes", [(ASTRA, 1), (FABLE, 0)])
def test_ac7_public_driver_observes_the_same_selected_matrix_before_running(
    tmp_path, monkeypatch, run_cli, selected, codex_probes
):
    from heddle.driver import loop

    host, _path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=selected)},
    )
    probes = []
    monkeypatch.setattr(
        loop, "probe_claude_capabilities", lambda: probes.append("claude")
    )
    monkeypatch.setattr(
        loop, "probe_codex_capabilities", lambda: probes.append("codex")
    )
    calls = provider_transport(monkeypatch, review_content())
    before = snapshot(host)
    status_code, status = gate_command(run_cli, "status")
    code, driven = gate_command(run_cli, "drive", "--until", "spec-review")
    assert status_code == code == 0, driven
    assert driven["data"]["effective_policy"] == status["data"]["effective_policy"]
    assert probes.count("codex") == codex_probes
    assert probes.count("claude") == 1  # The independent headless session capability.
    assert not calls and snapshot(host) == before
