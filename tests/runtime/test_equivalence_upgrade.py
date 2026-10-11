"""AC-15: records written before change-impact re-binding stay valid after it.

The fixture ``tests/fixtures/workspaces/change-impact-upgrade/`` was recorded by
the build that preceded change-impact re-binding; no part of it was made by
editing a current state. A one-off recording script, kept outside the
repository, drove one synthetic host through that build's public application
operations, doubling only the provider transport with this suite's own
provider doubles:

1. adopt the host, commit a baseline, then commit a second test module, so a
   baseline-relative test selection holds more tests than the declared one;
   seed a feature whose source baseline is the first commit;
2. attribute an outside path to another feature;
3. close spec review with inspection, decision and contract rows, and exit
   through plan review, which seals the spec-review receipt;
4. bind the declared test and capture the host around a scaffolding review
   with two findings: before any attempt (``scaffold-unopened``), with an
   attempt and no disposition (``scaffold-attempt-only``) and with one of its
   three rows (``scaffold-partial``); close it and exit, which seals its
   receipt;
5. prove the milestone, close its review with a verification row, seal it,
   prove acceptance and live, and exit implement;
6. make a review fix, re-prove, close peer review and exit to robustness;
7. record a failing smoke run with an accepted degraded-smoke decision, and a
   live witness waiver;
8. run two robustness rounds whose rows hold review and unavailable evidence,
   and capture the host with one row still open (``robustness-open``).

Each snapshot holds the exact working tree, and ``history.json`` holds the Git
commits; ``tests.change_impact_upgrade_helpers.rebuild`` replays them with a
fixed identity and dates, so every object id, the feature baseline included,
is the recorded one. The script then rebuilt each snapshot in a fresh process
without any double, checked that the rebuilt host reports what the journey's
own host reported, and stored that build's status projection and validation
codes under ``recorded``. Provider scratch directories hold role-temporary
artifacts, which a host may delete; the fixture does not retain them.

Activation seams (absent before the change):

- the disposition row field ``basis_evidence``, read from ``state.yaml``
  through ``_basis_evidence``;
- the re-binding row marker ``rebound_by``, read from ``state.yaml``;
- the typed ``changes`` on an evidence explanation, read through
  ``tests.change_impact_explanation_helpers.typed_changes``.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.change_impact_upgrade_helpers import (
    FEATURE,
    SNAPSHOTS,
    SPEC,
    RecordedHost,
    rebuild,
    status_projection,
    validation_codes,
)
from tests.structured_review_helpers import finding
from tests.tiering_helpers import SOL_XHIGH
from tests.tiering_review_helpers import (
    dispose,
    disposition,
    provider_transport,
    review_content,
)

SCAFFOLDING = f"{FEATURE}:review-test-scaffolding:feature:p1"
PEER = f"{FEATURE}:peer-review-sequential:feature:p1"
ROBUSTNESS = f"{FEATURE}:robustness-analysis:feature:p1"
# A test that differs from the feature baseline and no declaration names: the
# baseline-relative recipe selects it, the legacy recipe does not.
UNDECLARED_TEST = "tests/extra_check.py"
# Review-equivalent to the recorded review fix under the Python rule.
REFORMATTED_SOURCE = "VALUE  =  7  # review fix\n"
# Not review-equivalent to it: a literal value changes.
EDITED_SOURCE = "VALUE = 8  # review fix\n"


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    def load(name: str) -> RecordedHost:
        host = rebuild(name, tmp_path)
        monkeypatch.chdir(host.root)
        return host

    return load


def _ok(result):
    assert result.ok, result.to_envelope()
    return result


def _status():
    return _ok(execute(ops.Status(feature=FEATURE)))


def _closure(status, assignment_id: str) -> dict[str, Any]:
    return next(
        row
        for row in status.data["review_closure"]["assignments"]
        if row["assignment_id"] == assignment_id
    )


def _stale_subjects(row: dict[str, Any]) -> dict[str, list[Any]]:
    return {
        explanation["subject"]: explanation["changed_dependencies"]
        for explanation in row["evidence_explanations"]
    }


def _recorded_open_refs(host: RecordedHost, assignment_id: str) -> list[list[str]]:
    return next(
        row["open_refs"]
        for row in host.record["status"]["review_closure"]
        if row["assignment_id"] == assignment_id
    )


def _dispositions(host: RecordedHost) -> list[dict[str, Any]]:
    return yaml.safe_load(host.state.read_text())["review_assignments"]["dispositions"]


def _basis_evidence(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("basis_evidence")
    assert isinstance(evidence, dict) and evidence.get("artifact"), (
        "FAIL AC-15: a disposition recorded after the upgrade carries no "
        "basis_evidence reference to its basis manifest"
    )
    return evidence


def _without_revision(projection: dict[str, Any]) -> dict[str, Any]:
    return {
        **projection,
        "next_actions": [
            re.sub(r"--expect-revision \d+", "--expect-revision N", command)
            for command in projection["next_actions"]
        ],
    }


def _change_undeclared_test(host: RecordedHost) -> None:
    target = host.root / UNDECLARED_TEST
    text = target.read_text()
    assert "> 0" in text
    target.write_text(text.replace("> 0", ">= 7"))


def _settle_scaffolding(host: RecordedHost) -> None:
    rows = []
    for origin, finding_id in _recorded_open_refs(host, SCAFFOLDING):
        if finding_id == "@coverage":
            rows.append(
                disposition(origin, finding_id, status="settled", references=[SPEC])
            )
        else:
            rows.append(
                disposition(
                    origin,
                    finding_id,
                    evidence_kind="contract",
                    references=["tests/check.py"],
                )
            )
    _ok(dispose(host.state, rows))


def _record_session(host: RecordedHost) -> None:
    """An unrelated write: it re-parses and re-commits every recorded row."""
    _ok(
        execute(
            ops.RecordSession(
                ops.SessionInput(
                    started_at="2026-09-20T09:00Z",
                    ended_at="2026-09-20T09:30Z",
                    completed=("Read the open review state",),
                    started=(),
                    key_context="Records predate change-impact re-binding",
                    next_steps="Continue the feature",
                    blockers=(),
                ),
                feature=FEATURE,
                expect_revision=yaml.safe_load(host.state.read_text())["revision"],
            )
        )
    )


def _scaffold_receipt(host: RecordedHost) -> dict[str, Any]:
    acceptances = yaml.safe_load(host.state.read_text())["review_assignments"][
        "acceptances"
    ]
    receipt = next(row for row in acceptances if row["boundary"] == "scaffold")
    (entry,) = receipt["assignments"]
    assert entry["assignment_id"] == SCAFFOLDING
    return entry


@pytest.mark.parametrize("snapshot", SNAPSHOTS)
def test_ac15_upgrade_keeps_every_recorded_status_and_freshness_result(
    snapshot, recorded
) -> None:
    """Survivor pin: the new build reports what the pre-change build reported.

    Every closure, review status and verification freshness result, every
    acceptance identity and every next action is the recorded one, so nothing
    reads stale, missing or invalid because of the upgrade, and validation
    reports the recorded diagnostics.
    """
    host = recorded(snapshot)

    status = _status()
    assert status_projection(status.to_envelope()) == host.record["status"]
    validated = _ok(execute(ops.Validate(feature=FEATURE)))
    assert validation_codes(validated.to_envelope()) == host.record["validation_codes"]


def test_ac15_v1_source_evidence_artifacts_reencode_byte_exactly(recorded) -> None:
    """Survivor pin: each recorded v1 manifest decodes and re-encodes to its bytes.

    This covers the verification facts' manifests and the source evidence a
    recorded attribution embeds.
    """
    from heddle.kernel.source_manifest import (
        decode_source_evidence,
        encode_source_evidence,
    )

    host = recorded("robustness-open")
    artifacts = sorted((host.workspace / "verification").glob("source-evidence.*.yaml"))
    assert len(artifacts) == 4
    for artifact in artifacts:
        raw = artifact.read_bytes()
        evidence = decode_source_evidence(raw)
        assert evidence.schema == "heddle.source-evidence/v1"
        assert encode_source_evidence(evidence) == raw, artifact.name

    # An attribution embeds its evidence in state in canonical JSON form.
    (attribution,) = yaml.safe_load(host.state.read_text())["source_attributions"]
    for side in ("source", "references"):
        embedded = attribution[side]
        assert embedded["schema"] == "heddle.source-evidence/v1"
        raw = (
            json.dumps(
                embedded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode()
        assert encode_source_evidence(decode_source_evidence(raw)) == raw, side


def test_ac15_unrelated_write_keeps_every_recorded_row_and_validates(
    recorded,
) -> None:
    """Survivor pin: a write after the upgrade leaves pre-upgrade records alone.

    The write succeeds, every recorded ledger row, decision, verification fact
    and attribution keeps its exact content (so receipt and decision digests
    still match), and historical validation and status are unchanged.
    """
    host = recorded("robustness-open")
    before = yaml.safe_load(host.state.read_text())

    _record_session(host)

    after = yaml.safe_load(host.state.read_text())
    assert after["revision"] == before["revision"] + 1
    for key in (
        "review_assignments",
        "decisions",
        "verifications",
        "source_attributions",
        "source_baseline",
        "gates",
        "milestones",
    ):
        assert after[key] == before[key], key
    validated = _ok(execute(ops.Validate(feature=FEATURE)))
    assert validation_codes(validated.to_envelope()) == host.record["validation_codes"]
    assert _without_revision(status_projection(_status().to_envelope())) == (
        _without_revision(host.record["status"])
    )


def test_ac15_degraded_smoke_binding_waiver_and_receipts_verify_unchanged(
    recorded,
) -> None:
    """Survivor pin: recorded bindings and receipt digests recompute exactly.

    The degraded-smoke acceptance and the witness waiver bind verification
    fact identities, and receipts digest ledger rows; the new build must
    compute the same identities from the same records.
    """
    from heddle.kernel.review_assignments import (
        acceptance_record_digest,
        accepted_assignment_digest,
    )
    from heddle.kernel.smoke_disposition import (
        validate_smoke_bindings,
        verification_fact_identity,
    )
    from heddle.kernel.state import read_state_file
    from heddle.kernel.witness_waiver import validate_witness_bindings

    host = recorded("robustness-open")
    state = read_state_file(host.state)
    smoke = next(row for row in state.decisions if row.kind == "smoke-disposition")
    waiver = next(row for row in state.decisions if row.kind == "witness-waiver")
    assert smoke.smoke_disposition is not None and waiver.witness_waiver is not None

    validate_smoke_bindings(state, smoke.smoke_disposition)
    validate_witness_bindings(state, waiver.witness_waiver)
    latest = {fact.scope: fact for fact in state.verifications}
    bindings = dict(smoke.smoke_disposition["bindings"])
    assert {scope: verification_fact_identity(latest[scope]) for scope in bindings} == (
        bindings
    )
    assert verification_fact_identity(latest["live"]) == waiver.witness_waiver["fact"]

    ledger = state.review_assignments
    families = {
        "decisions": state.decisions,
        "dispositions": ledger.dispositions,
        "verifications": state.verifications,
    }
    sealed = [(receipt.boundary, receipt.scope) for receipt in ledger.acceptances]
    assert sealed == [("spec-review", None), ("scaffold", None), ("milestone", "m1")]
    for receipt in ledger.acceptances:
        for entry in receipt.assignments:
            assert accepted_assignment_digest(entry) == entry.digest
            for record in entry.records:
                row = families[record.family][record.index]
                assert acceptance_record_digest(row) == record.sha256

    rows = {row["scope"]: row for row in _status().data["verification_status"]}
    assert rows["smoke"]["accepted_degraded"] == smoke.id
    assert rows["live"]["waived_rerun"] == waiver.id


@pytest.mark.parametrize("snapshot", ["scaffold-attempt-only", "scaffold-partial"])
def test_ac15_pre_upgrade_scaffolding_assignment_keeps_legacy_recipe_through_sealing(
    snapshot, recorded
) -> None:
    """Survivor pin: an assignment opened before the upgrade stays legacy.

    ``scaffold-attempt-only`` is legacy through its attempt alone, since every
    row is written after the upgrade; ``scaffold-partial`` also through its
    pre-upgrade row. A changed test that differs from the feature baseline
    would join a baseline-relative basis, but the legacy recipe keeps the
    recorded basis for every row and for the sealed receipt.
    """
    host = recorded(snapshot)
    legacy = host.record["assignment_bases"][SCAFFOLDING]
    origin = _recorded_open_refs(host, SCAFFOLDING)[0][0]
    _change_undeclared_test(host)

    assert _closure(_status(), SCAFFOLDING)["evidence_basis"] == legacy
    _settle_scaffolding(host)
    closure = _closure(_status(), SCAFFOLDING)
    assert closure["closed"] and closure["evidence_basis"] == legacy
    bases = {
        row["evidence_basis"] for row in _dispositions(host) if row["run_id"] == origin
    }
    assert bases == {legacy}

    _ok(execute(ops.PhaseExit(feature=FEATURE)))
    assert yaml.safe_load(host.state.read_text())["stage"] == "implement"
    assert _scaffold_receipt(host)["source_identity"] == legacy
    _ok(execute(ops.Validate(feature=FEATURE)))


def test_ac15_scaffolding_assignment_opened_after_upgrade_gets_baseline_relative_recipe(
    recorded, monkeypatch
) -> None:
    """An assignment first reviewed after the upgrade covers baseline changes.

    Changing a test that differs from the feature baseline, which no
    declaration names, makes its rows stale; the legacy recipe ignores it.
    """
    host = recorded("scaffold-unopened")
    provider_transport(
        monkeypatch,
        review_content(
            "review-test-scaffolding",
            findings=[finding("SC-I1", classification="implement")],
        ),
    )
    _ok(execute(ops.RunGate("review-test-scaffolding", feature=FEATURE, **SOL_XHIGH)))
    origin = _closure(_status(), SCAFFOLDING)["open_refs"][0][0]
    _ok(
        dispose(
            host.state,
            [
                disposition(
                    origin,
                    "SC-I1",
                    evidence_kind="contract",
                    references=["tests/check.py"],
                ),
                disposition(origin, "@coverage", status="settled", references=[SPEC]),
            ],
        )
    )
    assert _closure(_status(), SCAFFOLDING)["closed"]

    _change_undeclared_test(host)

    closure = _closure(_status(), SCAFFOLDING)
    assert not closure["closed"] and closure["evidence_state"] == "stale", (
        "FAIL AC-15: a scaffolding assignment opened after the upgrade stays "
        f"current when {UNDECLARED_TEST}, which differs from the feature "
        "baseline, changes; it still gets the legacy recipe"
    )


def test_ac15_post_upgrade_attempt_names_its_launch_manifest_when_tests_change(
    recorded, monkeypatch
) -> None:
    """Every attempt this build records names the manifest of its launch basis.

    A test module created under the tests root after the launch prepared its
    basis changes the selected tests, so the completed review is rejected. The
    rejected attempt still names the reviewer-basis manifest of the basis it
    launched, so the assignment keeps the baseline-relative recipe.
    """
    from heddle.gate import entry
    from heddle.kernel.review_assignments import (
        legacy_assignment,
        reviewer_basis_artifacts,
    )
    from heddle.kernel.source_manifest import decode_source_evidence
    from heddle.kernel.state import read_state_file

    host = recorded("scaffold-unopened")
    late = host.root / "tests" / "late_check.py"
    prepare = entry.prepare_gate_run

    def prepare_then_add_test(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        late.write_text("def test_late():\n    assert True\n")
        return prepared

    monkeypatch.setattr(entry, "prepare_gate_run", prepare_then_add_test)
    calls = provider_transport(
        monkeypatch,
        review_content(
            "review-test-scaffolding",
            findings=[finding("SC-I1", classification="implement")],
        ),
    )
    rejected = execute(
        ops.RunGate("review-test-scaffolding", feature=FEATURE, **SOL_XHIGH)
    )

    assert not rejected.ok and len(calls) == 1, rejected.to_envelope()
    state = read_state_file(host.state)
    (attempt,) = [
        row
        for row in state.review_assignments.attempts
        if row.assignment_id == SCAFFOLDING
    ]
    artifacts = reviewer_basis_artifacts(attempt)
    assert len(artifacts) == 1, (
        "FAIL AC-15: a post-upgrade scaffolding attempt was recorded without the "
        "reviewer-basis manifest of its launch basis"
    )
    assert not legacy_assignment(state, SCAFFOLDING), (
        "FAIL AC-15: a test created after preparation turned a post-upgrade "
        "scaffolding assignment legacy"
    )
    manifest = decode_source_evidence((host.workspace / artifacts[0].path).read_bytes())
    paths = {row.path for row in manifest.observations}
    assert UNDECLARED_TEST in paths and "tests/late_check.py" not in paths, paths

    monkeypatch.setattr(entry, "prepare_gate_run", prepare)
    late.unlink()
    _ok(execute(ops.RunGate("review-test-scaffolding", feature=FEATURE, **SOL_XHIGH)))
    origin = _closure(_status(), SCAFFOLDING)["open_refs"][0][0]
    _ok(
        dispose(
            host.state,
            [
                disposition(
                    origin,
                    "SC-I1",
                    evidence_kind="contract",
                    references=["tests/check.py"],
                ),
                disposition(origin, "@coverage", status="settled", references=[SPEC]),
            ],
        )
    )
    assert _closure(_status(), SCAFFOLDING)["closed"]
    _change_undeclared_test(host)
    closure = _closure(_status(), SCAFFOLDING)
    assert not closure["closed"] and closure["evidence_state"] == "stale", closure


def test_ac4_ac15_sealing_rebinds_only_equivalence_current_rows_beside_pre_feature_rows(
    recorded,
) -> None:
    """Sealing a mixed assignment re-binds only this build's equivalence-current rows.

    The recorded scaffolding assignment holds a pre-feature row. This build adds
    a row whose cited source then changes review-equivalently, so it stays
    current only by equivalence, and a row that stays current by raw identity.
    Phase exit appends exactly one re-binding row, for the first; pre-feature
    rows compare raw and get none. The receipt seals the current raw basis and
    covers the pre-feature row, the raw-current row and the re-binding row, and
    the next write passes historical validation.
    """
    host = recorded("scaffold-partial")
    legacy = host.record["assignment_bases"][SCAFFOLDING]
    refs = _recorded_open_refs(host, SCAFFOLDING)
    assert [finding_id for _origin, finding_id in refs] == ["SC-I2", "@coverage"]
    origin = refs[0][0]
    recorded_rows = _dispositions(host)
    (pre_feature,) = (
        index for index, row in enumerate(recorded_rows) if row["run_id"] == origin
    )
    assert recorded_rows[pre_feature]["finding_id"] == "SC-I1"

    _ok(
        dispose(
            host.state,
            [
                # src/example.py is outside the scaffolding basis; only the
                # row's own citation binds it.
                disposition(origin, "SC-I2", references=["src/example.py"]),
                disposition(origin, "@coverage", status="settled", references=[SPEC]),
            ],
        )
    )
    rows = _dispositions(host)
    by_equivalence, raw_current = len(recorded_rows), len(recorded_rows) + 1
    assert len(rows) == raw_current + 1
    assert "basis_evidence" not in rows[pre_feature]
    for index in (by_equivalence, raw_current):
        evidence = _basis_evidence(rows[index])
        assert evidence["source_sha256"] == rows[index]["evidence_basis"]

    (host.root / "src/example.py").write_text("VALUE  =  7\n")
    closure = _closure(_status(), SCAFFOLDING)
    assert closure["closed"] and closure["evidence_basis"] == legacy, (
        "FAIL AC-4: a row whose citation changed review-equivalently no longer "
        f"keeps its assignment closed: {closure['evidence_explanations']}"
    )

    _ok(execute(ops.PhaseExit(feature=FEATURE)))
    sealed_rows = _dispositions(host)
    assert sealed_rows[: len(rows)] == rows
    added = sealed_rows[len(rows) :]
    assert [(row["run_id"], row["finding_id"]) for row in added] == [
        (origin, "SC-I2")
    ], (
        "FAIL AC-4: sealing must add exactly one re-binding row, for the row "
        f"current only by equivalence, and none for pre-feature rows: {added}"
    )
    assert added[0].get("rebound_by"), (
        "FAIL AC-4: the sealing row carries no rebound_by marker"
    )
    entry = _scaffold_receipt(host)
    assert entry["source_identity"] == legacy
    sealed = {
        record["index"]
        for record in entry["records"]
        if record["family"] == "dispositions"
    }
    assert {pre_feature, raw_current, len(rows)} <= sealed

    _record_session(host)
    _ok(execute(ops.Validate(feature=FEATURE)))
    assert _closure(_status(), SCAFFOLDING)["evidence_state"] == "accepted"


def test_ac6_ac15_pre_feature_row_says_its_change_detail_is_unavailable(
    recorded,
) -> None:
    """A pre-feature row made stale by a real edit cannot type its changes.

    Its record predates manifests, so after a non-equivalent edit to the source
    it cites, its explanation lists no typed change and says that the detail is
    unavailable.
    """
    from tests.change_impact_explanation_helpers import typed_changes

    host = recorded("robustness-open")
    (row,) = (row for row in _dispositions(host) if row["finding_id"] == "PE-I1")
    assert "basis_evidence" not in row
    assert [reference[0] for reference in row["references"]] == ["src/example.py"]

    (host.root / "src/example.py").write_text(EDITED_SOURCE)

    peer = _closure(_status(), PEER)
    assert not peer["closed"]
    (explanation,) = (
        item
        for item in peer["evidence_explanations"]
        if item["subject"] == f"{row['run_id']}#PE-I1"
    )
    changes = typed_changes(explanation, ac="AC-6")
    assert changes == set(), (
        f"FAIL AC-15: a pre-feature row reports typed changes it cannot know: {changes}"
    )
    said = " ".join(
        str(explanation.get(key) or "") for key in ("cause", "detail_limit")
    )
    assert "unavailable" in said.lower(), (
        "FAIL AC-6: the stale pre-feature row does not say its change detail is "
        f"unavailable: {said!r}"
    )


def test_ac15_legacy_rows_compare_raw_beside_post_upgrade_rows(recorded) -> None:
    """Rows without a manifest compare raw; a post-upgrade row compares equivalence.

    A review-equivalent rewrite of a reviewed source makes every pre-upgrade
    row stale, with no typed changes because those records predate manifests,
    while the row recorded after the upgrade stays current.
    """
    host = recorded("robustness-open")
    ((origin, open_finding),) = _recorded_open_refs(host, ROBUSTNESS)
    assert open_finding == "RO-I2"
    _ok(
        dispose(
            host.state,
            [disposition(origin, "RO-I2", references=["src/example.py"])],
        )
    )
    assert _closure(_status(), ROBUSTNESS)["closed"]
    assert _closure(_status(), PEER)["closed"]

    (host.root / "src/example.py").write_text(REFORMATTED_SOURCE)

    status = _status()
    peer = _closure(status, PEER)
    assert not peer["closed"]
    assert len(_stale_subjects(peer)) == 2
    assert all(changes == [] for changes in _stale_subjects(peer).values())
    robustness = _stale_subjects(_closure(status, ROBUSTNESS))
    legacy = {subject for subject in robustness if subject != f"{origin}#RO-I2"}
    assert f"{origin}#RO-I1" in legacy
    assert all(robustness[subject] == [] for subject in legacy)
    assert f"{origin}#RO-I2" not in robustness, (
        "FAIL AC-15: the row recorded after the upgrade reads stale across a "
        "review-equivalent change, exactly like the legacy rows"
    )
    from tests.change_impact_explanation_helpers import typed_changes

    for explanation in peer["evidence_explanations"]:
        assert typed_changes(explanation, ac="AC-6") == set()
        said = " ".join(
            str(explanation.get(key) or "") for key in ("cause", "detail_limit")
        )
        assert "unavailable" in said.lower(), (
            "FAIL AC-6: a legacy row stale by raw comparison does not say its "
            f"change detail is unavailable: {said!r}"
        )


@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_ac15_post_upgrade_row_naming_a_damaged_manifest_fails_naming_both(
    damage, recorded
) -> None:
    """A recorded basis manifest is required: damage is an integrity failure.

    The failure names the row and the artifact, writes nothing and never falls
    back to the legacy raw comparison.
    """
    host = recorded("robustness-open")
    ((origin, _finding),) = _recorded_open_refs(host, ROBUSTNESS)
    _ok(
        dispose(
            host.state,
            [disposition(origin, "RO-I2", references=["src/example.py"])],
        )
    )
    evidence = _basis_evidence(_dispositions(host)[-1])
    artifact = host.workspace / evidence["artifact"]
    assert artifact.is_file()
    if damage == "missing":
        artifact.unlink()
    else:
        artifact.write_bytes(artifact.read_bytes() + b"# altered\n")
    before = host.state.read_bytes()

    for operation in (ops.Status(feature=FEATURE), ops.Validate(feature=FEATURE)):
        result = execute(operation)
        assert not result.ok
        error = result.to_envelope()["error"]
        assert error["code"] == "workspace-invalid", error
        assert "RO-I2" in error["message"], error
        assert artifact.name in error["message"], error
    assert host.state.read_bytes() == before
