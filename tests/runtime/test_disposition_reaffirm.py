"""AC-6: public lead attestation copies only still-qualifying effective rows."""

import json

import pytest

from tests.proof_continuity_helpers import (
    SPEC,
    affirm_all,
    command,
    execute,
    launch,
    ops,
    read,
)
from tests.structured_review_helpers import finding
from tests.tiering_helpers import snapshot
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    provider_transport,
    review_content,
    review_status,
)


def resolved(tmp_path, monkeypatch, capsys):
    root, path = current_host(tmp_path, monkeypatch)
    for name in ("first", "second", "superseded"):
        (root / f"docs/{name}.md").write_text(f"Stable {name} proof.\n")
    calls = provider_transport(
        monkeypatch,
        review_content(
            findings=[
                finding("PC-I1", classification="implement"),
                finding("PC-I2", classification="implement"),
            ]
        ),
    )
    code, response = launch(capsys)
    assert code == 4 and response["data"]["accepted"], response
    origin = response["data"]["run_id"]
    affirm_all(path, [(origin, "PC-I1")], references=("docs/superseded.md",))
    assert dispose(
        path, [disposition(origin, "PC-I1", references=["docs/first.md"])]
    ).ok
    assert dispose(
        path,
        [
            disposition(origin, "PC-I2", references=["docs/second.md"]),
            disposition(
                origin, "@coverage", status="settled", references=["tests/check.py"]
            ),
        ],
    ).ok
    assert review_status(path)["closed"]
    return root, path, origin, calls


def reaffirm(capsys, *flags):
    return command(
        capsys,
        "review",
        "reaffirm",
        "--role",
        "spec-review",
        "--scope",
        "feature",
        *flags,
    )


def test_reaffirm_preserves_captured_evidence_and_checks_latest_rows_before_noop(
    tmp_path, monkeypatch, capsys
):
    root, path, _origin, calls = resolved(tmp_path, monkeypatch, capsys)
    (root / "docs/superseded.md").write_text(
        "Invalid old citation must not poison its replacement.\n"
    )
    (root / SPEC).write_text((root / SPEC).read_text() + "\nClarify the contract.\n")
    # A current effective row is qualified but not copied with the two stale rows.
    assert dispose(
        path, [disposition(_origin, "PC-I1", references=["docs/first.md"])]
    ).ok
    before_rows = read(path)["review_assignments"]["dispositions"]
    before = snapshot(root)
    code, preview = reaffirm(capsys, "--dry-run")
    assert code == 0 and preview["ok"], preview
    assert snapshot(root) == before
    code, result = reaffirm(capsys, "--expect-revision", str(read(path)["revision"]))
    assert code == 0 and result["ok"], result
    rows = read(path)["review_assignments"]["dispositions"]
    assert rows[: len(before_rows)] == before_rows
    latest = {(r["run_id"], r["finding_id"]): r for r in before_rows}
    appended = rows[len(before_rows) :]
    assert len(appended) == 2
    assert {r["finding_id"] for r in appended} == {"PC-I2", "@coverage"}
    for row in appended:
        old = latest[(row["run_id"], row["finding_id"])]
        assert set(row) == set(old), "No new disposition schema fields are needed."
        # Only the existing recording time and current review basis may change.
        assert {k: v for k, v in row.items() if k not in {"at", "evidence_basis"}} == {
            k: v for k, v in old.items() if k not in {"at", "evidence_basis"}
        }
        assert row["evidence_basis"] != old["evidence_basis"]
    assert review_status(path)["closed"]
    before = snapshot(root)
    code, replay = reaffirm(capsys)
    assert code == 0 and replay["ok"] and snapshot(root) == before, replay
    (root / "docs/first.md").write_text("Changed citation at the same current basis.\n")
    before = snapshot(root)
    code, refused = reaffirm(capsys)
    assert code != 0 and not refused["ok"], refused
    assert "PC-I1" in json.dumps(refused) and snapshot(root) == before
    assert len(calls) == 1


@pytest.mark.parametrize("basis_changed", [False, True])
def test_one_failed_reference_refuses_the_entire_batch(
    tmp_path, monkeypatch, capsys, basis_changed
):
    root, path, _origin, calls = resolved(tmp_path, monkeypatch, capsys)
    code, activated = reaffirm(capsys)
    assert code == 0 and activated["ok"], activated
    if basis_changed:
        (root / SPEC).write_text((root / SPEC).read_text() + "\nClarification.\n")
    (root / "docs/second.md").write_text("The old second proof is no longer valid.\n")
    before = snapshot(root)
    code, refused = reaffirm(capsys)
    assert code != 0 and not refused["ok"], refused
    assert "PC-I2" in json.dumps(refused)
    assert snapshot(root) == before and len(calls) == 1


def test_superseding_retained_row_is_not_resurrected_and_missing_coverage_stays_open(
    tmp_path, monkeypatch, capsys
):
    root, path, origin, calls = resolved(tmp_path, monkeypatch, capsys)
    assert dispose(
        path,
        [
            disposition(origin, "PC-I1", status="retained"),
            disposition(origin, "@coverage", status="retained"),
        ],
    ).ok
    (root / SPEC).write_text((root / SPEC).read_text() + "\nClarification.\n")
    original = read(path)["review_assignments"]["dispositions"]
    code, result = reaffirm(capsys)
    assert code == 0 and result["ok"], result
    rows = read(path)["review_assignments"]["dispositions"]
    assert rows[: len(original)] == original
    assert [(r["run_id"], r["finding_id"]) for r in rows[len(original) :]] == [
        (origin, "PC-I2")
    ]
    assert sorted(review_status(path)["open_refs"]) == sorted(
        [[origin, "PC-I1"], [origin, "@coverage"]]
    )
    assert len(calls) == 1


def test_stale_cas_and_sealed_assignment_refuse_even_when_reaffirm_would_be_noop(
    tmp_path, monkeypatch, capsys
):
    root, path, _origin, calls = resolved(tmp_path, monkeypatch, capsys)
    code, activated = reaffirm(capsys)
    assert code == 0 and activated["ok"], activated
    before = snapshot(root)
    code, stale = reaffirm(capsys, "--expect-revision", str(read(path)["revision"] - 1))
    assert code != 0 and stale["error"]["code"] == "conflict", stale
    assert snapshot(root) == before
    sealed = execute(ops.PhaseExit(feature=V7_FEATURE, through="plan-review"))
    assert sealed.ok, sealed.to_envelope()
    before = snapshot(root)
    code, refused = reaffirm(capsys)
    assert code != 0 and not refused["ok"] and "seal" in json.dumps(refused).lower(), (
        refused
    )
    assert snapshot(root) == before and len(calls) == 1


def test_reaffirmation_requires_current_verification_without_running_it(
    tmp_path, monkeypatch, capsys
):
    from tests.readiness_helpers import verify

    root, path, origin, calls = resolved(tmp_path, monkeypatch, capsys)
    verify(path, "m1")
    assert dispose(
        path,
        [
            disposition(
                origin,
                f,
                evidence_kind="verification",
                verification_scope="m1",
                references=["tests/check.py"],
            )
            for f in ("PC-I1", "PC-I2", "@coverage")
        ],
    ).ok
    code, enabled = reaffirm(capsys)
    assert code == 0 and enabled["ok"], enabled
    (root / SPEC).write_text((root / SPEC).read_text() + "\nChanged owned contract.\n")
    before = snapshot(root)
    facts = read(path)["verifications"]
    code, refused = reaffirm(capsys)
    assert code != 0 and not refused["ok"], refused
    assert "m1" in json.dumps(refused) and snapshot(root) == before
    assert read(path)["verifications"] == facts
    verify(path, "m1")
    code, accepted = reaffirm(capsys)
    assert code == 0 and accepted["ok"], accepted
    assert review_status(path)["closed"] and len(calls) == 1


def test_empty_effective_selection_is_noop_not_implicit_coverage_acceptance(
    tmp_path, monkeypatch, capsys
):
    root, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content())
    code, reviewed = launch(capsys)
    assert code == 0 and reviewed["data"]["accepted"], reviewed
    origin = reviewed["data"]["run_id"]
    before = snapshot(root)
    code, result = reaffirm(capsys)
    assert code == 0 and result["ok"], result
    assert snapshot(root) == before
    assert review_status(path)["open_refs"] == [[origin, "@coverage"]]
    assert not read(path)["review_assignments"]["dispositions"] and len(calls) == 1


@pytest.mark.parametrize("mismatched_origin", [False, True])
def test_settled_report_and_awaiting_decision_rows_keep_their_original_authority(
    tmp_path, monkeypatch, capsys, mismatched_origin
):
    from tests.proof_continuity_helpers import write

    root, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(
        monkeypatch,
        review_content(
            findings=[
                finding("PC-R1"),
                finding("PC-R2"),
            ]
        ),
    )
    code, reviewed = launch(capsys)
    assert code == 4 and reviewed["data"]["accepted"], reviewed
    origin = reviewed["data"]["run_id"]
    decisions = {d["origin_finding_id"]: d["id"] for d in read(path)["decisions"]}

    def resolve(finding_id):
        result = execute(
            ops.ResolveDecision(
                decisions[finding_id],
                "disposition",
                "Synthetic owner selects the module.",
                SPEC,
                feature=V7_FEATURE,
            )
        )
        assert result.ok, result.to_envelope()

    resolve("PC-R1")
    result = dispose(
        path,
        [
            disposition(
                origin,
                "PC-R1",
                status="settled",
                evidence_kind="decision",
                decision_id=decisions["PC-R1"],
                references=["tests/check.py"],
            ),
            disposition(
                origin,
                "PC-R2",
                status="awaiting-decision",
                evidence_kind="decision",
                decision_id=decisions["PC-R2"],
                references=["tests/check.py"],
            ),
            disposition(origin, "@coverage", references=["tests/check.py"]),
        ],
    )
    assert result.ok, result.to_envelope()
    code, activated = reaffirm(capsys)
    assert code == 0 and activated["ok"], activated
    if mismatched_origin:
        resolve("PC-R2")
        # Authored negative ledger: an existing resolved decision, but belonging
        # to the other original. Neither an unknown ID nor a pending decision.
        value = read(path)
        value["review_assignments"]["dispositions"][0]["decision_id"] = decisions[
            "PC-R2"
        ]
        write(path, value)
    (root / SPEC).write_text((root / SPEC).read_text() + "\nClarification.\n")
    original = read(path)
    before = snapshot(root)
    code, result = reaffirm(capsys)
    if mismatched_origin:
        assert code != 0 and not result["ok"], result
        assert "PC-R1" in json.dumps(result) and snapshot(root) == before
    else:
        assert code == 0 and result["ok"], result
        prior = original["review_assignments"]["dispositions"]
        after = read(path)
        rows = after["review_assignments"]["dispositions"]
        assert rows[: len(prior)] == prior
        copies = {r["finding_id"]: r for r in rows[len(prior) :]}
        assert set(copies) == {"PC-R1", "@coverage"}
        assert copies["PC-R1"]["status"] == "settled"
        assert copies["PC-R1"]["decision_id"] == decisions["PC-R1"]
        assert copies["PC-R1"]["references"] == prior[0]["references"]
        assert after["decisions"] == original["decisions"]
        assert review_status(path)["open_refs"] == [[origin, "PC-R2"]]
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["stale-review", "wrong-evidence-kind"])
def test_required_inspection_is_requalified_and_never_demoted(
    tmp_path, monkeypatch, capsys, fault
):
    from tests.proof_continuity_helpers import write
    from tests.structured_review_helpers import disposition as reviewer_disposition
    from tests.structured_review_helpers import finding_ref
    from tests.tiering_review_helpers import open_round

    root, path = current_host(tmp_path, monkeypatch)
    content = review_content()
    calls = provider_transport(monkeypatch, content)
    code, first = launch(capsys)
    assert code == 0 and first["data"]["accepted"], first
    origin = first["data"]["run_id"]
    assert dispose(
        path,
        [
            disposition(
                origin,
                "@coverage",
                status="retained",
                requires_inspection=True,
                references=["tests/check.py"],
            )
        ],
    ).ok
    assert open_round(path).ok
    content["prior_dispositions"] = [
        reviewer_disposition(finding_ref(origin, "@coverage"), action="addressed"),
    ]
    code, second = launch(capsys)
    assert code == 0 and second["data"]["accepted"], second
    assert dispose(
        path,
        [
            disposition(
                origin,
                "@coverage",
                evidence_kind="review",
                requires_inspection=True,
                review_run_id=second["data"]["run_id"],
                references=["tests/check.py"],
            ),
            disposition(
                second["data"]["run_id"], "@coverage", references=["tests/check.py"]
            ),
        ],
    ).ok
    before = snapshot(root)
    code, activated = reaffirm(capsys)
    assert code == 0 and activated["ok"] and snapshot(root) == before, activated
    if fault == "stale-review":
        (root / SPEC).write_text(
            (root / SPEC).read_text() + "\nChanged review source.\n"
        )
    else:
        # Authored negative ledger pins the recorder's additional inspection
        # admission rule, not merely generic reference/hash qualification.
        value = read(path)
        row = value["review_assignments"]["dispositions"][-2]
        assert row["requires_inspection"] is True
        row.update(evidence_kind="inspection", review_run_id=None)
        write(path, value)
    before = snapshot(root)
    code, refused = reaffirm(capsys)
    assert code != 0 and not refused["ok"], refused
    assert origin in json.dumps(refused) and "@coverage" in json.dumps(refused)
    assert snapshot(root) == before and len(calls) == 2
