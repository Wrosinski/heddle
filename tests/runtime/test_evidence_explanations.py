"""Actionable evidence explanations and guidance.

The typed change tests reach not-yet-built behaviour only through the seams in
``tests.change_impact_explanation_helpers``: an explanation's ``changes`` and a
disposition row's ``basis_evidence``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heddle.contracts import operations as ops
from heddle.runtime import application
from heddle.runtime.verification import verification_status_payload
from tests.change_impact_explanation_helpers import (
    as_recorded_before_manifests,
    assert_names_exactly,
    assignment_row,
    basis_manifest,
    changed_components,
    disposition_rows,
    evidence_files,
    explanation_for,
    open_milestone_round,
    typed_changes,
    verification_row,
)
from tests.structured_review_helpers import finding
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    gate_command,
    provider_transport,
    review_content,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    (
        "status",
        "applicability",
        "expected_state",
        "expected_cause",
        "action",
    ),
    [
        (
            "missing",
            "required",
            "missing",
            "evidence has not been produced",
            ops.CommandAction(ops.Verify("m1")),
        ),
        (
            "content-stale",
            "required",
            "stale",
            "relevant content changed",
            ops.CommandAction(ops.Verify("m1")),
        ),
        (
            "failed",
            "required",
            "invalid",
            "recorded command failed",
            ops.CommandAction(ops.Verify("m1")),
        ),
        ("missing", "not-applicable", "not-applicable", None, None),
        (
            "missing",
            "scope-not-defined",
            "scope-not-defined",
            "verification scope is not declared",
            None,
        ),
    ],
)
def test_ac7_verification_payload_covers_failure_classes(
    status, applicability, expected_state, expected_cause, action
):
    """AC-7: missing, stale, invalid, and limited rows retain legal remedies."""
    from heddle.kernel.verification import VerificationFreshness

    row = verification_status_payload(
        (
            VerificationFreshness(
                "m1",
                status,
                action,
                3,
                applicability=applicability,
                detail_limit=(
                    "scope-level cause only" if status == "content-stale" else None
                ),
            ),
        )
    )[0]
    assert row["freshness"] == status
    assert row["evidence_state"] == expected_state
    assert row["applicability"] == applicability
    assert row["cause"] == expected_cause
    assert row["action"] == ("heddle verify --scope m1" if action else None)
    if status == "content-stale":
        assert row["detail_limit"] == "scope-level cause only"


def test_ac7_verification_payload_distinguishes_cause_and_legal_remedy():
    """AC-7 red pin: one stale row exposes cause, action, and detail limit."""
    from heddle.kernel.verification import VerificationFreshness

    row = verification_status_payload(
        (
            VerificationFreshness(
                "m1",
                "content-stale",
                ops.CommandAction(ops.Verify("m1")),
                3,
                applicability="required",
                detail_limit="scope-level cause only",
            ),
        )
    )[0]
    assert row["evidence_state"] == "stale"
    assert row["cause"] == "relevant content changed"
    assert row["action"] == "heddle verify --scope m1"
    assert row["detail_limit"] == "scope-level cause only"


@pytest.mark.parametrize(
    (
        "status",
        "subject",
        "dependencies",
        "failed_reference",
        "cause",
        "detail_limit",
    ),
    [
        (
            "content-stale",
            "run-1#finding-1",
            ("docs/changed-citation.md",),
            None,
            "relevant content changed",
            "only the recorded citation dependency is known",
        ),
        (
            "failed",
            "verification:m1",
            (),
            "verification/m1.log#exit_code=1",
            "recorded command failed",
            None,
        ),
    ],
)
def test_ac7_causal_explanations_bind_original_dependency_and_failed_reference(
    status, subject, dependencies, failed_reference, cause, detail_limit
):
    """AC-7 red: stale and invalid rows retain concrete causal identities."""
    from heddle.kernel.readiness import EvidenceExplanation
    from heddle.kernel.verification import VerificationFreshness

    remedy = ops.CommandAction(ops.Verify("m1", feature="demo", expect_revision=7))
    explanation = EvidenceExplanation(
        subject=subject,
        cause=cause,
        changed_dependencies=dependencies,
        failed_reference=failed_reference,
        remedy=remedy,
        detail_limit=detail_limit,
    )
    row = verification_status_payload(
        (
            VerificationFreshness(
                "m1",
                status,
                remedy,
                3,
                applicability="required",
                explanation=explanation,
            ),
        )
    )[0]

    assert row["evidence_state"] == (
        "stale" if status == "content-stale" else "invalid"
    )
    assert row["affected_original_work"] == subject
    assert row["changed_dependencies"] == list(dependencies)
    assert row["failed_reference"] == failed_reference
    assert row["cause"] == cause
    assert row["action"].startswith("heddle verify --scope m1")
    assert row["detail_limit"] == detail_limit


def test_ac12_packaged_guidance_uses_the_typed_action_contract():
    """AC-12 red: packaged guidance states the complete delivered input contract."""
    paths = (
        "heddle/resources/implement.briefing.md",
        "heddle/resources/peer-review.briefing.md",
    )
    corpus = "\n".join((ROOT / path).read_text() for path in paths).lower()
    authoring_fields = {
        "feature",
        "work",
        "references",
        "input_schema",
        "expected_revision",
    }
    decision_fields = {"feature", "decision_id", "choices", "routes_to"}
    for phrase in (
        "authoring action",
        "decision action",
        "lead authors the payload",
        "driver pauses",
        "current authority",
        "bounded non-progress",
    ):
        assert phrase in corpus
    assert all(field in corpus for field in authoring_fields | decision_fields)
    assert "incomplete command" not in corpus


def test_ac12_publicly_derived_actions_round_trip_through_wire(
    tmp_path, monkeypatch, run_cli
):
    """AC-12 red: public derivation, wire encoding, and decoding agree exactly."""
    _host, _path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    public = application.execute(ops.Status(feature=V7_FEATURE))
    authoring = public.next_actions[0].action
    assert isinstance(authoring, ops.AuthoringAction)
    wire = public.to_envelope()["next_actions"][0]["action"]
    assert ops.action_from_payload(wire) == authoring
    assert set(wire) == {
        "kind",
        "feature",
        "work",
        "references",
        "input_schema",
        "expected_revision",
    }

    from tests.runtime.test_action_contracts import _pending_cap_host

    (tmp_path / "decision").mkdir()
    _host, _path, _calls, _origin, decision_id = _pending_cap_host(
        tmp_path / "decision", monkeypatch, run_cli
    )
    decision_result = application.execute(ops.Orient(feature=V7_FEATURE))
    decision = decision_result.next_actions[0].action
    assert isinstance(decision, ops.DecisionAction)
    assert decision.decision_id == decision_id
    decision_wire = decision_result.to_envelope()["next_actions"][0]["action"]
    assert ops.action_from_payload(decision_wire) == decision
    assert set(decision_wire) == {
        "kind",
        "feature",
        "decision_id",
        "choices",
        "routes_to",
    }


def test_ac7_public_review_explanations_keep_manual_and_command_remedies(
    tmp_path, monkeypatch, run_cli
):
    """AC-7: legal non-authoring remedies never cross an internal wire decoder."""
    from tests.runtime.test_action_contracts import _pending_cap_host
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import amend

    host, path, _calls, _origin, decision_id = _pending_cap_host(
        tmp_path, monkeypatch, run_cli
    )
    resolved = application.execute(
        ops.ResolveDecision(
            decision_id=decision_id,
            kind="continue-review",
            rationale="Owner authorizes one bounded follow-up",
            routes_to="docs/features/runtime/operational-example.md",
            feature=V7_FEATURE,
        )
    )
    assert resolved.ok, resolved.to_envelope()
    source = host / "src/example.py"
    source.write_text(source.read_text() + "\n# citation changed\n")

    manual = application.execute(ops.Status(feature=V7_FEATURE))
    assert manual.ok, manual.to_envelope()
    row = next(
        item
        for item in manual.data["review_closure"]["assignments"]
        if item["role"] == "spec-review"
    )
    assert row["evidence_explanations"][0]["remedy"]["kind"] == "manual"
    assert (
        next(
            item
            for item in manual.data["review_status"]
            if item["gate"] == "spec-review"
        )["explanation"]
        is None
    )
    assert application.execute(ops.Orient(feature=V7_FEATURE)).ok

    assert amend(path, entry("spec-review", limit=2), revision=2).ok
    command = application.execute(ops.Status(feature=V7_FEATURE))
    assert command.ok, command.to_envelope()
    row = next(
        item
        for item in command.data["review_closure"]["assignments"]
        if item["role"] == "spec-review"
    )
    remedy = row["evidence_explanations"][0]["remedy"]
    assert remedy["kind"] == "command"
    assert remedy["operation"]["name"] == "review round-open"


@pytest.mark.parametrize("genuinely_open", [False, True])
def test_changed_citation_at_round_limit_routes_to_resubmission_not_stop(
    tmp_path, monkeypatch, run_cli, genuinely_open
):
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        review_status,
        runs,
    )

    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    findings = [finding("SP-I1", classification="implement")]
    if genuinely_open:
        findings.append(finding("SP-I2", classification="implement"))
    calls = provider_transport(monkeypatch, review_content(findings=findings))
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    rows = [disposition(origin), disposition(origin, "@coverage", status="settled")]
    if genuinely_open:
        rows.append(disposition(origin, "SP-I2", status="retained"))
    assert dispose(path, rows).ok
    source = host / "src/example.py"
    source.write_text(source.read_text() + "\n# citation changed\n")

    row = review_status(path)
    assert row["stop_reason"] == "round-limit" and not row["closed"]
    action = application.execute(ops.Status(feature=V7_FEATURE)).next_actions[0].action
    if genuinely_open:
        assert row["next_step"] == "decision"
        assert isinstance(action, ops.CommandAction)
        assert isinstance(action.operation, ops.ReviewRoundOpen)
        return
    assert row["next_step"] == "disposition" and row["evidence_state"] == "stale"
    assert isinstance(action, ops.AuthoringAction)
    assert action.work == "review-disposition"
    assert set(action.references) == {f"{origin}#SP-I1", f"{origin}#@coverage"}
    assert dispose(path, rows).ok
    assert review_status(path)["closed"] and len(calls) == 1


def test_recorded_report_row_outside_its_decision_route_is_explained(
    tmp_path, monkeypatch, run_cli
):
    from tests.operational_model_helpers import SPEC, read, write
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        review_status,
        runs,
    )

    host, path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="report")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    owner = read(path)["decisions"][0]["id"]
    resolved = application.execute(
        ops.ResolveDecision(
            owner,
            "disposition",
            "Synthetic owner selects the module.",
            SPEC,
            feature=V7_FEATURE,
        )
    )
    assert resolved.ok, resolved.to_envelope()
    code, verified = gate_command(run_cli, "verify", "--scope", "m1")
    assert code == 0 and verified["ok"], verified
    waiting = disposition(
        origin,
        status="awaiting-decision",
        evidence_kind="verification",
        verification_scope="m1",
    )
    coverage = disposition(origin, "@coverage", status="settled")
    assert dispose(path, [waiting, coverage]).ok
    # Authored history: a row recorded before input refused this shape.
    value = read(path)
    value["review_assignments"]["dispositions"][0]["status"] = "addressed"
    write(path, value)

    row = review_status(path)
    assert row["open_refs"] == [[origin, "SP-I1"]]
    assert row["evidence_state"] == "invalid"
    [explanation] = row["evidence_explanations"]
    assert explanation["subject"] == f"{origin}#SP-I1"
    assert "resolved owner decision" in explanation["cause"]
    assert explanation["remedy"]["kind"] == "authoring"


def _milestone_review_host(tmp_path, monkeypatch, *, owns):
    from tests.operational_model_helpers import read, write
    from tests.tiering_helpers import entry

    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review")},
    )
    value = read(path)
    value["milestones"][0]["owns"] = list(owns)
    write(path, value)
    return host, path


def test_ac6_disposition_basis_explanation_names_every_change_by_kind(
    tmp_path, monkeypatch, run_cli
):
    """AC-6 red: a stale assignment basis names each changed path by its kind."""
    import os

    from tests.operational_model_helpers import read, write
    from tests.tiering_review_helpers import dispose, disposition, runs

    owned = ("content", "gone", "linked", "mode")
    host, path = _milestone_review_host(
        tmp_path,
        monkeypatch,
        owns=(
            "src/example.py",
            "tests/check.py",
            *(f"src/{name}.py" for name in owned),
            "src/later.py",
        ),
    )
    for name in owned:
        (host / f"src/{name}.py").write_text(f"NAME = {name!r}\n")
    (host / "docs/notes.md").write_text("Stable notes outside the basis.\n")
    provider_transport(
        monkeypatch,
        review_content(
            "milestone-review",
            findings=[finding("MI-I1", classification="implement")],
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    assert dispose(
        path,
        [
            disposition(origin, "MI-I1"),
            disposition(origin, "@coverage", status="settled"),
        ],
    ).ok
    assert assignment_row("milestone-review", "m1")["closed"]

    (host / "src/content.py").write_text("NAME = 'changed'\n")
    (host / "src/later.py").write_text("LATER = 1\n")
    (host / "src/gone.py").unlink()
    (host / "src/linked.py").unlink()
    os.symlink("example.py", host / "src/linked.py")
    (host / "src/mode.py").chmod(0o755)
    value = read(path)
    value["milestones"][0]["owns"].append("docs/notes.md")
    write(path, value)

    row = assignment_row("milestone-review", "m1")
    assert not row["closed"] and row["evidence_state"] == "stale", row
    expected = {
        ("content", "src/content.py"),
        ("added", "src/later.py"),
        ("removed", "src/gone.py"),
        ("kind", "src/linked.py"),
        ("mode", "src/mode.py"),
        ("membership", "docs/notes.md"),
    }
    for finding_id in ("MI-I1", "@coverage"):
        assert_names_exactly(explanation_for(row, f"{origin}#{finding_id}"), expected)
    oriented = assignment_row("milestone-review", "m1", operation=ops.Orient)
    assert oriented["evidence_explanations"] == row["evidence_explanations"]


def test_ac6_disposition_basis_names_a_declaration_change_beside_a_reflow(
    tmp_path, monkeypatch, run_cli
):
    """AC-6: declaring a file the basis already covered, while that file is only
    reformatted, names the membership change and leaves the rows not current."""
    from tests.operational_model_helpers import read, write
    from tests.tiering_review_helpers import dispose, disposition, runs

    host, path = _milestone_review_host(
        tmp_path, monkeypatch, owns=("src/example.py", "tests/check.py", "src")
    )
    (host / "src/a.py").write_text("x='a'\n")
    provider_transport(
        monkeypatch,
        review_content(
            "milestone-review",
            findings=[finding("MI-I1", classification="implement")],
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    assert dispose(
        path,
        [
            disposition(origin, "MI-I1"),
            disposition(origin, "@coverage", status="settled"),
        ],
    ).ok
    assert assignment_row("milestone-review", "m1")["closed"]

    (host / "src/a.py").write_text('x = "a"\n')
    value = read(path)
    value["milestones"][0]["owns"].append("src/a.py")
    write(path, value)

    row = assignment_row("milestone-review", "m1")
    assert not row["closed"] and row["evidence_state"] == "stale", (
        f"FAIL AC-6: a declaration change beside a reflow left the basis current: {row}"
    )
    expected = {("formatting-only", "src/a.py"), ("membership", "src/a.py")}
    for finding_id in ("MI-I1", "@coverage"):
        assert_names_exactly(explanation_for(row, f"{origin}#{finding_id}"), expected)


@pytest.mark.parametrize("recorded", ["with-manifest", "before-manifests"])
def test_ac6_document_basis_explanation_or_its_unavailable_detail(
    tmp_path, monkeypatch, run_cli, recorded
):
    """AC-6 red: a row without a manifest says its detail is unavailable."""
    from tests.operational_model_helpers import SPEC
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        review_status,
        runs,
    )

    host, path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    assert dispose(
        path,
        [disposition(origin), disposition(origin, "@coverage", status="settled")],
    ).ok
    if recorded == "before-manifests":
        as_recorded_before_manifests(path)
    spec = host / SPEC
    spec.write_text(spec.read_text() + "\nA changed acceptance requirement.\n")

    row = review_status(path)
    assert row["evidence_state"] == "stale", row
    explanation = explanation_for(row, f"{origin}#SP-I1")
    if recorded == "with-manifest":
        assert_names_exactly(explanation, {("content", SPEC)})
        return
    assert typed_changes(explanation) == set(), (
        "FAIL AC-6: a row recorded before manifests invents typed changes"
    )
    detail = f"{explanation['cause']} {explanation['detail_limit']}".lower()
    assert "unavailable" in detail, (
        f"FAIL AC-6: a row without a manifest does not say its detail is "
        f"unavailable: {explanation}"
    )


@pytest.mark.parametrize("outcome", ["content-changed", "failed"])
def test_ac6_verification_backed_row_follows_its_verification_change_kind(
    tmp_path, monkeypatch, run_cli, outcome
):
    """AC-6 red: a verification-backed row is stale for a change, else invalid."""
    from tests.operational_model_helpers import SPEC
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        review_status,
        runs,
    )

    host, path = current_host(tmp_path, monkeypatch)
    provider_transport(
        monkeypatch,
        review_content(findings=[finding("SP-I1", classification="implement")]),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    code, verified = gate_command(run_cli, "verify", "--scope", "m1")
    assert code == 0 and verified["ok"], verified
    assert dispose(
        path,
        [
            disposition(
                origin,
                evidence_kind="verification",
                verification_scope="m1",
                references=[SPEC],
            ),
            disposition(origin, "@coverage", status="settled", references=[SPEC]),
        ],
    ).ok
    assert review_status(path)["closed"]
    source = host / "src/example.py"
    if outcome == "content-changed":
        source.write_text(source.read_text() + "# The value stays declared.\n")
    else:
        source.write_text("VALUE = 8\n")
        code, failed = gate_command(run_cli, "verify", "--scope", "m1")
        assert code != 0 and not failed["ok"], failed

    row = review_status(path)
    explanation = explanation_for(row, f"{origin}#SP-I1")
    if outcome == "content-changed":
        assert_names_exactly(explanation, {("content", "src/example.py")})
        assert row["evidence_state"] == "stale", (
            "FAIL AC-6: a verification-backed row whose scope went content-stale "
            f"reads {row['evidence_state']}, not stale"
        )
    else:
        assert_names_exactly(explanation, set())
        assert row["evidence_state"] == "invalid", row


@pytest.mark.parametrize("freshness", ["content-stale", "source-set-stale"])
def test_ac6_stale_verification_scope_names_its_typed_changes(
    tmp_path, monkeypatch, run_cli, freshness
):
    """AC-6 red: status and orient name a stale scope's changed paths by kind."""
    from tests.operational_model_helpers import read, write

    host, path = current_host(tmp_path, monkeypatch, stage="implement")
    value = read(path)
    value["milestones"][0]["owns"] = ["src", "tests/check.py"]
    write(path, value)
    helper = host / "src/helper.py"
    helper.write_text("HELPER = 1\n")
    code, verified = gate_command(run_cli, "verify", "--scope", "m1")
    assert code == 0 and verified["ok"], verified
    if freshness == "content-stale":
        helper.write_text("HELPER = 2\n")
    else:
        helper.unlink()

    row = verification_row("m1")
    assert row["freshness"] == freshness and row["evidence_state"] == "stale", row
    assert verification_row("m1", operation=ops.Orient) == row
    if freshness == "content-stale":
        assert_names_exactly(row, {("content", "src/helper.py")})
        return
    # A leaf deleted under an owned directory leaves the expanded path set, so it
    # is in only the recorded manifest: a membership change.
    assert_names_exactly(row, {("membership", "src/helper.py")})


def test_ac6_review_backed_row_names_the_changed_reviewer_component(
    tmp_path, monkeypatch, run_cli
):
    """AC-6 red: review evidence names the changed basis component and is stale.

    Reads never publish, so the current reviewer-basis manifest is not on disk.
    The expected component comes from the two recorded attempts instead: a first
    milestone title change between the rounds is their only basis difference.
    A second title change then makes the review-backed row stale.
    """
    from tests.operational_model_helpers import read, write
    from tests.structured_review_helpers import disposition as reviewer_disposition
    from tests.structured_review_helpers import finding_ref
    from tests.tiering_review_helpers import dispose, disposition

    def retitle(title):
        # The milestone title is a reviewer-basis fact, not a source path.
        value = read(path)
        value["milestones"][0]["title"] = title
        write(path, value)

    _host, path = _milestone_review_host(
        tmp_path, monkeypatch, owns=("src/example.py", "tests/check.py")
    )
    content = review_content("milestone-review")
    calls = provider_transport(monkeypatch, content)
    code, first = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 0 and first["data"]["accepted"], first
    origin = first["data"]["run_id"]
    cited = ["tests/check.py"]
    assert dispose(
        path,
        [
            disposition(
                origin,
                "@coverage",
                status="retained",
                requires_inspection=True,
                references=cited,
            )
        ],
    ).ok
    assert open_milestone_round(path).ok
    retitle("Declared value, first retitle")
    content["prior_dispositions"] = [
        reviewer_disposition(finding_ref(origin, "@coverage"), action="addressed")
    ]
    code, second = gate_command(run_cli, "run-gate", "milestone-review")
    assert code == 0 and second["data"]["accepted"], second
    later = second["data"]["run_id"]
    assert dispose(
        path,
        [
            disposition(
                origin,
                "@coverage",
                evidence_kind="review",
                review_run_id=later,
                requires_inspection=True,
                references=cited,
            ),
            disposition(later, "@coverage", references=cited),
        ],
    ).ok
    assert assignment_row("milestone-review", "m1")["closed"]
    title_components = changed_components(path, origin, later)
    assert len(title_components) == 1, (
        f"FAIL AC-6: a title change alters reviewer-basis components "
        f"{sorted(title_components)}, expected exactly one"
    )
    retitle("Declared value, second retitle")

    row = assignment_row("milestone-review", "m1")
    assert row["open_refs"] == [[origin, "@coverage"]], row
    assert_names_exactly(
        explanation_for(row, f"{origin}#@coverage"),
        {("component", name) for name in title_components},
    )
    assert row["evidence_state"] == "stale", (
        f"FAIL AC-6: changed review evidence reads {row['evidence_state']}, not stale"
    )
    assert len(calls) == 2


def test_ac9_disposition_write_publishes_its_named_manifest_and_dry_run_none(
    tmp_path, monkeypatch, run_cli
):
    """AC-9 red: the write publishes the manifest it names; a dry run publishes none."""
    from tests.tiering_review_helpers import dispose, disposition, runs

    _host, path = current_host(tmp_path, monkeypatch)
    provider_transport(monkeypatch, review_content())
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and result["ok"], result
    origin = runs(path)[0]["run_id"]
    rows = [disposition(origin, "@coverage", status="settled")]
    before = evidence_files(path)
    preview = dispose(path, rows, dry_run=True)
    assert preview.ok, preview.to_envelope()
    assert evidence_files(path) == before, (
        "FAIL AC-9: a disposition dry run published a manifest"
    )

    assert dispose(path, rows).ok
    [row] = disposition_rows(path)
    reference = basis_manifest(path, row)
    assert evidence_files(path) - before == {reference["artifact"]}, (
        "FAIL AC-9: the disposition write published files it does not name"
    )


def test_ac6_every_source_identity_difference_names_a_typed_change():
    """AC-6: a differing source identity always names its changes; a declaration
    or selection difference over the same observed paths is a membership change,
    so it is never equivalent, even beside a reflow of that same path."""
    from itertools import product

    from heddle.kernel.source_manifest import (
        ObservedPath,
        SourceChange,
        SourceDefinition,
        build_source_evidence,
        compare_source_evidence,
        stamp_equivalence,
    )

    directory = ObservedPath("src", "directory", False, b"")
    leaves = (
        ObservedPath("src/a.py", "file", False, b"a = 1\n"),
        ObservedPath("src/a.py", "file", False, b"a = 2\n"),
        ObservedPath("src/a.py", "file", True, b"a = 1\n"),
        ObservedPath("src/a.py", "missing", False, b""),
        ObservedPath("src/a.py", "file", False, b"x='a'\n"),
        ObservedPath("src/a.py", "file", False, b'x = "a"\n'),
    )
    definitions = (
        SourceDefinition("milestone-owns", ("src",), ("src", "src/a.py")),
        SourceDefinition("milestone-owns", ("src", "src/a.py"), ("src", "src/a.py")),
        SourceDefinition("milestone-owns", ("src", "src/a.py"), ("src",)),
        SourceDefinition("feature-inputs", ("src",), ("src", "src/a.py")),
        SourceDefinition("milestone-owns", ("src/a.py",), ("src/a.py",)),
    )
    # Written records carry identity, so a reflow can compare equivalent.
    manifests = [
        stamp_equivalence(
            build_source_evidence(
                definition,
                [
                    row
                    for row in (directory, leaf)
                    if row.path in {*definition.declaration_paths, *definition.paths}
                ],
            ),
            {leaf.path: (leaf.content,)},
        )
        for definition, leaf in product(definitions, leaves)
    ]
    unnamed = [
        (left.definition, right.definition)
        for left, right in product(manifests, repeat=2)
        if (left.source_sha256 != right.source_sha256)
        != bool(compare_source_evidence(left, right).changes)
    ]
    assert not unnamed, (
        "FAIL AC-6: a source identity difference names no typed change, or an "
        f"unchanged identity names one: {unnamed[:3]}"
    )
    redefined_equivalent = [
        (left.definition, right.definition, compare_source_evidence(left, right))
        for left, right in product(manifests, repeat=2)
        if left.definition != right.definition
        and compare_source_evidence(left, right).equivalent
    ]
    assert not redefined_equivalent, (
        "FAIL AC-6: a declaration or selection difference compared as "
        f"equivalent: {redefined_equivalent[:2]}"
    )

    declared_dir, declared_both = (
        build_source_evidence(definition, (directory, leaves[0]))
        for definition in definitions[:2]
    )
    for recorded, current in (
        (declared_dir, declared_both),
        (declared_both, declared_dir),
    ):
        comparison = compare_source_evidence(recorded, current)
        assert recorded.source_sha256 != current.source_sha256
        assert comparison.changes == (SourceChange("membership", "src/a.py"),), (
            f"FAIL AC-6: a declaration-only change is not named: {comparison}"
        )
        assert not comparison.equivalent, (
            "FAIL AC-6: a declaration-only change compared as equivalent"
        )

    declared_dir, declared_both = (
        stamp_equivalence(
            build_source_evidence(definition, (directory, leaf)),
            {leaf.path: (leaf.content,)},
        )
        for definition, leaf in zip(definitions[:2], leaves[4:], strict=True)
    )
    for recorded, current in (
        (declared_dir, declared_both),
        (declared_both, declared_dir),
    ):
        comparison = compare_source_evidence(recorded, current)
        assert comparison.changes == (
            SourceChange("formatting-only", "src/a.py"),
            SourceChange("membership", "src/a.py"),
        ), f"FAIL AC-6: a reflow masks the declaration change: {comparison}"
        assert not comparison.equivalent, (
            "FAIL AC-6: a declaration change beside a reflow compared as equivalent"
        )
