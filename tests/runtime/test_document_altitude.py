"""AC-12/13 authoring, milestone and briefing composition on fresh hosts."""

import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.tiering_helpers import FEATURE, blank_host, confirmed, invoke


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
