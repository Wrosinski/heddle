"""AC-6: public lead attestation copies only still-qualifying effective rows.

change-impact-rebinding-v1 AC-10 and AC-11: reaffirm writes every qualifying
row across the unsealed assignments at the current boundary and lists the
rest; readiness routes it as review-reaffirm authoring work.

Activation seams, each failing as an assertion until implemented:

- the role-free ``heddle review reaffirm`` form (a usage error today);
- the partial outcome: ``ok: true`` exit 4 with the qualifying rows written
  and the rest listed, exit 0 once nothing remains, and ``ok: false`` exit 1
  when no row is writable (today any refused row refuses the whole batch with
  exit 3);
- ``review-reaffirm`` authoring work in readiness, orient and sync's
  plan-status (today stale rows route ``review-disposition`` work).
"""

import json

import pytest

from heddle.cli import main
from tests.change_impact_routing_helpers import (
    PEER_CITATIONS,
    SPEC_CITATIONS,
    appended_rows,
    change_citation,
    disposed_peer_reviews,
    disposed_spec_review,
    listed_with,
    refused_changes,
    run_review,
    stale_basis,
)
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

# Reaffirm copies a row's resolution, evidence and citations unchanged.
COPIED_FIELDS = (
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
    assert preview["data"]["wrote"] is False
    assert preview["data"]["mutation_summary"] == {
        "command": "review reaffirm",
        "would_write": True,
    }
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
        # The resolution, evidence and citations are copied; only the
        # recording time, the current basis and that basis's record change.
        assert {k: row[k] for k in COPIED_FIELDS} == {k: old[k] for k in COPIED_FIELDS}
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
        # The other rows are re-recorded at the new basis, so the batch holds
        # one stale row: the one whose citation fails below.
        assert dispose(
            path,
            [
                disposition(_origin, "PC-I1", references=["docs/first.md"]),
                disposition(
                    _origin,
                    "@coverage",
                    status="settled",
                    references=["tests/check.py"],
                ),
            ],
        ).ok
    (root / "docs/second.md").write_text("The old second proof is no longer valid.\n")
    before = snapshot(root)
    code, refused = reaffirm(capsys)
    # With no row writable, partial reaffirm still refuses as a whole.
    assert code == 1 and not refused["ok"], (
        f"FAIL AC-10: with no row writable reaffirm must return ok:false exit 1, "
        f"got exit {code}: {refused}"
    )
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
    prior = read(path)
    code, refused = reaffirm(capsys)
    if fault == "stale-review":
        # Partial reaffirm: the review-backed row is refused and never
        # demoted, while the later run's own inspection row is written.
        assert code == 4 and refused["ok"], (
            f"FAIL AC-10: a refused row must not block the qualifying rows, "
            f"got exit {code}: {refused}"
        )
        assert [
            (r["run_id"], r["finding_id"]) for r in appended_rows(prior, read(path))
        ] == [(second["data"]["run_id"], "@coverage")]
        # The review it cites judged the old spec, so the spec is named.
        assert ("content", SPEC) in refused_changes(
            refused["data"], origin, "@coverage"
        ), "FAIL AC-10: the refused review-backed row must name its changed path"
    else:
        assert code != 0 and not refused["ok"], refused
        assert origin in json.dumps(refused) and "@coverage" in json.dumps(refused)
        assert snapshot(root) == before
    assert len(calls) == 2


def reaffirm_all(capsys, *flags):
    """The role-free form: every unsealed assignment at the current boundary."""
    return command(capsys, "review", "reaffirm", *flags)


def _written(before, after):
    return [(r["run_id"], r["finding_id"]) for r in appended_rows(before, after)]


def test_ac10_reaffirm_without_role_writes_every_qualifying_row_across_assignments(
    tmp_path, monkeypatch, capsys
):
    root, path, origins, calls = disposed_peer_reviews(tmp_path, monkeypatch)
    behavior, complexity = origins["behavior-review"], origins["complexity-review"]
    stale_basis(root)
    change_citation(root, "docs/second.md")
    revision = read(path)["revision"]
    before = snapshot(root)

    code, stale = reaffirm_all(capsys, "--expect-revision", str(revision - 1))
    assert code == 5 and stale["error"]["code"] == "conflict", (
        f"FAIL AC-10: reaffirm without a role must conflict on a stale revision, "
        f"got exit {code}: {stale}"
    )
    assert snapshot(root) == before

    code, preview = reaffirm_all(capsys, "--dry-run")
    assert code == 4 and preview["ok"], (
        f"FAIL AC-10: a dry run must preview the partial outcome, got exit {code}: "
        f"{preview}"
    )
    assert preview["data"]["wrote"] is False and snapshot(root) == before, (
        "FAIL AC-10: a reaffirm dry run wrote or published a file"
    )

    prior = read(path)
    code, result = reaffirm_all(capsys, "--expect-revision", str(revision))
    assert code == 4 and result["ok"], (
        f"FAIL AC-10: rows remain after a partial write, so reaffirm must return "
        f"ok:true exit 4, got exit {code}: {result}"
    )
    after = read(path)
    assert after["revision"] == revision + 1, (
        "FAIL AC-10: every qualifying row must be written in one compare-and-swap "
        "transaction"
    )
    written = _written(prior, after)
    assert sorted(written) == sorted(
        [
            (behavior, "BR-I1"),
            (behavior, "@coverage"),
            (complexity, "CR-I1"),
            (complexity, "@coverage"),
        ]
    ), f"FAIL AC-10: reaffirm must write exactly the qualifying rows: {written}"
    latest = {
        (r["run_id"], r["finding_id"]): r
        for r in prior["review_assignments"]["dispositions"]
    }
    for row in appended_rows(prior, after):
        old = latest[(row["run_id"], row["finding_id"])]
        assert {k: row[k] for k in COPIED_FIELDS} == {k: old[k] for k in COPIED_FIELDS}
        assert row["evidence_basis"] != old["evidence_basis"]
    assert ("content", "docs/second.md") in refused_changes(
        result["data"], complexity, "CR-I2"
    ), "FAIL AC-10: the refused row must be listed with its changed path"
    assert review_status(path, role="behavior-review")["closed"]
    assert review_status(path, role="complexity-review")["open_refs"] == [
        [complexity, "CR-I2"]
    ]

    assert dispose(
        path, [disposition(complexity, "CR-I2", references=["docs/second.md"])]
    ).ok
    before = snapshot(root)
    code, done = reaffirm_all(capsys)
    assert code == 0 and done["ok"], (
        f"FAIL AC-10: with nothing remaining reaffirm must exit 0, got exit {code}: "
        f"{done}"
    )
    assert snapshot(root) == before
    assert review_status(path, role="complexity-review")["closed"]
    assert len(calls) == 2


def test_ac10_reaffirm_without_role_and_no_writable_row_fails_and_writes_nothing(
    tmp_path, monkeypatch, capsys
):
    root, path, origins, calls = disposed_peer_reviews(tmp_path, monkeypatch)
    stale_basis(root)
    # Every stale row's citation changed, so no row is writable.
    change_citation(root, "docs/first.md", "docs/second.md", "tests/check.py")
    before = snapshot(root)
    code, refused = reaffirm_all(capsys)
    assert code == 1 and not refused["ok"], (
        f"FAIL AC-10: with no row writable reaffirm must return ok:false exit 1, "
        f"got exit {code}: {refused}"
    )
    assert snapshot(root) == before
    for role, citations in PEER_CITATIONS.items():
        for finding_id, reference in citations.items():
            changes = refused_changes(refused, origins[role], finding_id)
            assert ("content", reference) in changes, (
                f"FAIL AC-10: refused row {role} {finding_id} must name its "
                f"changed citation {reference}: {sorted(changes)}"
            )
    assert len(calls) == 2


def test_ac10_reaffirm_without_role_lists_a_missing_slot_assignment_with_its_reason(
    tmp_path, monkeypatch, capsys
):
    root, path, origins, calls = disposed_peer_reviews(
        tmp_path, monkeypatch, behavior_secondary=True
    )
    complexity = origins["complexity-review"]
    waiting = review_status(path, role="behavior-review")["assignment_id"]
    stale_basis(root)
    change_citation(root, "docs/second.md")
    prior = read(path)
    code, result = reaffirm_all(capsys)
    assert code == 4 and result["ok"], (
        f"FAIL AC-10: a missing-slot assignment must not block the other "
        f"assignment's qualifying rows, got exit {code}: {result}"
    )
    assert sorted(_written(prior, read(path))) == sorted(
        [(complexity, "CR-I1"), (complexity, "@coverage")]
    )
    assert listed_with(result["data"], waiting, "slot"), (
        "FAIL AC-10: the missing-slot assignment must be listed with its reason"
    )
    assert ("content", "docs/second.md") in refused_changes(
        result["data"], complexity, "CR-I2"
    )
    assert review_status(path, role="behavior-review")["missing_slots"] == ["secondary"]
    assert len(calls) == 2


def test_ac10_reaffirm_without_role_skips_a_sealed_assignment(
    tmp_path, monkeypatch, capsys
):
    from tests.tiering_helpers import SOL

    root, path, _spec_origin, spec_calls = disposed_spec_review(tmp_path, monkeypatch)
    sealed = execute(ops.PhaseExit(feature=V7_FEATURE, through="plan-review"))
    assert sealed.ok, sealed.to_envelope()
    plan_calls = provider_transport(
        monkeypatch,
        review_content(
            "plan-review", findings=[finding("PR-I1", classification="implement")]
        ),
    )
    plan_origin = run_review("plan-review", SOL)
    assert dispose(
        path,
        [
            disposition(plan_origin, "PR-I1", references=["docs/first.md"]),
            disposition(
                plan_origin,
                "@coverage",
                status="settled",
                references=["tests/check.py"],
            ),
        ],
    ).ok
    # The contract change stales the sealed spec-review rows and the open
    # plan-review rows alike.
    stale_basis(root)
    prior = read(path)
    code, result = reaffirm_all(capsys)
    assert code == 0 and result["ok"], (
        f"FAIL AC-10: reaffirm without a role must skip the sealed assignment and "
        f"write the open one, got exit {code}: {result}"
    )
    assert sorted(_written(prior, read(path))) == sorted(
        [(plan_origin, "PR-I1"), (plan_origin, "@coverage")]
    ), "FAIL AC-10: only the unsealed assignment's rows may be written"
    assert review_status(path, role="plan-review")["closed"]
    # With a role, the sealed assignment still refuses.
    before = snapshot(root)
    code, refused = reaffirm(capsys)
    assert code != 0 and not refused["ok"] and "seal" in json.dumps(refused).lower()
    assert snapshot(root) == before
    assert len(spec_calls) == 1 and len(plan_calls) == 1


def test_ac10_role_scoped_reaffirm_applies_the_same_partial_rule(
    tmp_path, monkeypatch, capsys
):
    root, path, origin, calls = disposed_spec_review(tmp_path, monkeypatch)
    stale_basis(root)
    change_citation(root, "docs/second.md")
    prior = read(path)
    code, result = reaffirm(capsys)
    assert code == 4 and result["ok"], (
        f"FAIL AC-10: role-scoped reaffirm must write the qualifying rows and list "
        f"the rest, got exit {code}: {result}"
    )
    assert sorted(_written(prior, read(path))) == sorted(
        [(origin, "PC-I1"), (origin, "@coverage")]
    )
    assert ("content", "docs/second.md") in refused_changes(
        result["data"], origin, "PC-I2"
    ), "FAIL AC-10: the refused row must be listed with its changed path"
    assert review_status(path)["open_refs"] == [[origin, "PC-I2"]]
    assert dispose(
        path, [disposition(origin, "PC-I2", references=["docs/second.md"])]
    ).ok
    code, done = reaffirm(capsys)
    assert code == 0 and done["ok"] and review_status(path)["closed"], done
    assert len(calls) == 1


def test_ac11_orient_routes_review_reaffirm_listing_rows_to_write_and_refuse(
    tmp_path, monkeypatch, capsys
):
    root, path, origin, calls = disposed_spec_review(tmp_path, monkeypatch)
    stale_basis(root)
    change_citation(root, "docs/second.md")
    before = snapshot(root)
    code, oriented = command(capsys, "orient")
    assert code == 0 and oriented["ok"], oriented
    first = oriented["next_actions"][0]
    action = first["action"]
    assert action["kind"] == "authoring" and action["work"] == "review-reaffirm", (
        f"FAIL AC-11: orient must route review-reaffirm authoring work while a "
        f"stale row is writable, got {action}"
    )
    references = action["references"]
    writable = [f"{origin}#PC-I1", f"{origin}#@coverage"]
    refused = f"{origin}#PC-I2"
    for reference in (*writable, refused):
        assert any(reference in item for item in references), (
            f"FAIL AC-11: review-reaffirm references must list {reference}: "
            f"{references}"
        )

    def forms(token):
        return {item.replace(token, "") for item in references if token in item}

    assert forms(refused).isdisjoint(set().union(*map(forms, writable))), (
        "FAIL AC-11: review-reaffirm references must distinguish the row it would "
        f"refuse from the rows it would write: {references}"
    )
    assert action["expected_revision"] == read(path)["revision"]
    assert first["command"].startswith("heddle review reaffirm"), first
    status = execute(ops.Status(feature=V7_FEATURE)).to_envelope()
    assert status["next_actions"][0]["action"] == action
    code = main(["orient", "--feature", V7_FEATURE])
    rendered = capsys.readouterr().out
    assert code == 0 and f"next: {first['command']}" in rendered, rendered
    assert snapshot(root) == before and len(calls) == 1


def test_ac11_with_no_writable_row_readiness_keeps_disposition_work(
    tmp_path, monkeypatch, capsys
):
    """Survivor pin: when every stale row fails requalification, readiness
    routes review-disposition work for all of them, as before this feature."""
    root, path, origin, calls = disposed_spec_review(tmp_path, monkeypatch)
    stale_basis(root)
    change_citation(root, *SPEC_CITATIONS.values())
    code, oriented = command(capsys, "orient")
    assert code == 0 and oriented["ok"], oriented
    action = oriented["next_actions"][0]["action"]
    assert action["kind"] == "authoring" and action["work"] == "review-disposition"
    assert action["references"] == [f"{origin}#{f}" for f in SPEC_CITATIONS]
    assert action["input_schema"] == "heddle.review-disposition-input/v1"
    assert len(calls) == 1


def test_ac11_sync_renders_the_review_reaffirm_command_in_plan_status(
    tmp_path, monkeypatch, capsys
):
    root, path, origin, calls = disposed_spec_review(tmp_path, monkeypatch)
    plan = path.parent / "plan.md"
    plan.write_text(
        plan.read_text()
        + "\n<!-- heddle:begin plan-status -->\nNative fixture status.\n"
        + "<!-- heddle:end plan-status -->\n"
    )
    stale_basis(root)
    change_citation(root, "docs/second.md")
    code, synced = command(capsys, "sync")
    assert code == 0 and synced["ok"], (
        f"FAIL AC-11: sync must render the routed action without error: {synced}"
    )
    routed = [
        line for line in plan.read_text().splitlines() if line.startswith("- next:")
    ]
    assert routed and "heddle review reaffirm" in routed[0], (
        f"FAIL AC-11: the plan-status next line must render review-reaffirm: {routed}"
    )
    code, oriented = command(capsys, "orient")
    assert code == 0 and oriented["next_actions"][0]["command"] in routed[0]
    assert len(calls) == 1
