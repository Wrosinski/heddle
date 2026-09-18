"""AC-12/13 authoring, milestone and briefing composition on fresh hosts."""

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.tiering_helpers import FEATURE, blank_host, confirmed, invoke, snapshot

REVIEW_RECORD_GUIDANCE = (
    "An optional lead-authored assessment that supports native review closure "
    "is a workflow review record."
)
PRODUCT_ASSESSMENT_GUIDANCE = (
    "A product assessment remains an owned product artifact outside the "
    "protected workflow workspace."
)


@pytest.mark.parametrize("stage", ["spec-review", "plan-review"])
def test_review_kickoff_delivers_legal_assessment_location(
    tmp_path, monkeypatch, stage
) -> None:
    """AC-7: both public review briefings use Orient's workspace identity."""
    from tests.tiering_review_helpers import V7_FEATURE, current_host

    host, _path = current_host(tmp_path, monkeypatch, stage=stage)
    before = snapshot(host)

    orient = execute(ops.Orient(feature=V7_FEATURE))
    kickoff = execute(ops.Kickoff(feature=V7_FEATURE))

    assert orient.ok and kickoff.ok, (orient.to_envelope(), kickoff.to_envelope())
    assert orient.data["workspace"]
    text = kickoff.data["briefing"]
    assert REVIEW_RECORD_GUIDANCE in text
    assert PRODUCT_ASSESSMENT_GUIDANCE in text
    assert "data.workspace" in text
    assert "reviews/" in text
    assert "final" in text.lower() and "binding" in text.lower()
    assert "native dispositions" in text.lower()
    assert "plans/<slug>/reviews/" not in text
    assert "every assessment is a workflow review record" not in text.lower()
    assert "assessment is required" not in text.lower()
    assert snapshot(host) == before


def test_ac12_native_admission_renders_one_ac_home_and_distinct_document_owners(
    tmp_path, monkeypatch
):
    host = blank_host(tmp_path, monkeypatch)
    confirmed(host)
    result = invoke("FeatureStart", slug=FEATURE)
    assert result.ok, result.to_envelope()
    spec = (host / "docs/features/runtime" / f"{FEATURE}.md").read_text()
    plan = (host / "plans" / FEATURE / "plan.md").read_text()
    assert spec.count("## Acceptance Criteria") == 1
    assert "## Design Commitments" in spec
    assert "## Technical Architecture" in plan and "## Implementation Strategy" in plan
    assert "## Acceptance Criteria" not in plan
    assert "{{tier}}" not in plan and "Tier None" not in plan
    assert "heddle:begin plan-status" in plan
    assert "### Integrated Witness Proposal" in plan
    assert "per-attempt/aggregate" in plan
    assert "Assessment entry" in plan
    assert "e2e-plus-live posture" in spec
    brief = (host / "plans" / FEATURE / "brief.md").read_text()
    assert "## External Services (optional)" in brief


def test_ac12_native_milestone_authoring_needs_low_high_not_hours(
    tmp_path, monkeypatch
):
    host = blank_host(tmp_path, monkeypatch)
    confirmed(host)
    assert invoke("FeatureStart", slug=FEATURE).ok
    for complexity in ("low", "high"):
        milestone = ops.MilestoneInput(
            title=f"Concrete {complexity} part",
            complexity=complexity,
            verification=ops.VerificationInput(
                command="python3 tests/check.py", expected="pass"
            ),
        )
        result = execute(ops.MilestoneAdd(milestone, feature=FEATURE))
        assert result.ok, result.to_envelope()
    state = yaml.safe_load((host / "plans" / FEATURE / "state.yaml").read_text())
    assert [row["complexity"] for row in state["milestones"]] == ["low", "high"]
    assert all(row.get("estimated_hours") is None for row in state["milestones"])
    revised = execute(
        ops.MilestoneEdit("m1", ops.MilestonePatch(complexity="high"), feature=FEATURE)
    )
    assert revised.ok, revised.to_envelope()
    path = host / "plans" / FEATURE / "state.yaml"
    before = path.read_bytes()
    assert yaml.safe_load(before)["milestones"][0]["complexity"] == "high"
    refused = execute(
        ops.MilestoneEdit(
            "m1", ops.MilestonePatch(complexity="medium"), feature=FEATURE
        )
    )
    assert not refused.ok
    assert "complexity" in (refused.error.message + refused.error.hint).lower()
    assert path.read_bytes() == before
    refused = execute(
        ops.MilestoneAdd(
            ops.MilestoneInput(
                title="Invalid Medium",
                complexity="medium",
                verification=ops.VerificationInput(
                    command="python3 tests/check.py", expected="pass"
                ),
            ),
            feature=FEATURE,
        )
    )
    assert not refused.ok
    assert "complexity" in (refused.error.message + refused.error.hint).lower()
    assert path.read_bytes() == before


def test_ac12_kickoff_preserves_native_commands_and_scaffold_prerequisites(
    tmp_path, monkeypatch
):
    from tests.operational_model_helpers import FEATURE as V7_FEATURE
    from tests.tiering_review_helpers import current_host

    _host, path = current_host(tmp_path, monkeypatch, stage="scaffold")
    state = yaml.safe_load(path.read_text())
    state["commands"] = {
        "smoke_test": "python3 tests/check.py",
        "acceptance_test": "python3 tests/check.py",
    }
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    before = path.read_bytes()
    result = execute(ops.Kickoff(feature=V7_FEATURE))
    assert result.ok, result.to_envelope()
    text = result.data["briefing"]
    # Kickoff delivers navigation, not a copied command inventory. The fixture's
    # old command happened to match a removed example in the historical prose.
    assert "heddle status --json" in text
    assert "heddle commands set" in text
    assert "contract evidence" in text.lower()
    assert all(
        kind in text.lower() for kind in ("inspection", "collection", "execution")
    )
    status = execute(ops.Status(feature=V7_FEATURE))
    assert status.ok, status.to_envelope()
    assert status.data["verification_commands"] == state["commands"]
    assert "prerequisite" in text.lower()
    assert "Design Context derived" not in text
    assert path.read_bytes() == before


def test_alignment_assessment_edit_preserves_originating_review_freshness(
    tmp_path, monkeypatch, run_cli
):
    from tests.structured_review_helpers import disposition as reviewer_disposition
    from tests.structured_review_helpers import finding_ref
    from tests.tiering_helpers import entry, snapshot
    from tests.tiering_review_helpers import (
        current_host,
        dispose,
        disposition,
        gate_command,
        open_round,
        provider_transport,
        review_content,
        review_status,
    )

    role = "behavior-review"
    host, path = current_host(
        tmp_path, monkeypatch, stage="peer-review", overrides={role: entry(role)}
    )
    content = review_content(role)
    calls = provider_transport(monkeypatch, content)
    _, first = gate_command(run_cli, "run-gate", role)
    assert first["ok"] and first["data"]["accepted"], first
    origin = first["data"]["run_id"]
    assert dispose(
        path,
        [disposition(origin, "@coverage", status="retained", requires_inspection=True)],
    ).ok
    assert open_round(path, role=role).ok
    content["prior_dispositions"] = [
        reviewer_disposition(finding_ref(origin, "@coverage"), action="addressed")
    ]
    _, second = gate_command(run_cli, "run-gate", role)
    assert second["ok"] and second["data"]["accepted"], second
    reviewed = disposition(
        origin,
        "@coverage",
        evidence_kind="review",
        review_run_id=second["data"]["run_id"],
        requires_inspection=True,
    )
    closed = dispose(
        path, [reviewed, disposition(second["data"]["run_id"], "@coverage")]
    )
    assert closed.ok, closed.to_envelope()
    assert review_status(path, role=role)["closed"]

    # Recording the lead's final judgment is authored plan content even though
    # implementation bytes are unchanged. Existing review freshness still binds.
    plan = path.with_name("plan.md")
    plan.write_text(
        plan.read_text() + "\n### Verification Commands\n\n"
        "Assessment: lead inspected synthetic run fixture-run-1, "
        "artifact fixture-output.json; aligned with declared criteria.\n"
    )
    assert not review_status(path, role=role)["closed"]
    before = snapshot(host)
    for evidence, predicate in (
        (reviewed, "review-not-qualifying"),
        (
            disposition(origin, "@coverage", requires_inspection=True),
            "reviewer-inspection-not-qualifying",
        ),
    ):
        rejected = dispose(path, [evidence])
        assert not rejected.ok
        assert rejected.error.details["rows"][0]["predicate"] == predicate
        assert snapshot(host) == before
    assert len(calls) == 2
