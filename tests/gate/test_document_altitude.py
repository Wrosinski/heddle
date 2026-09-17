"""AC-12/13 delivered contracts; owner review and organic feedback judge fitness."""

import json
import re
from pathlib import Path

import pytest

from tests.tiering_review_helpers import ALTITUDE_DIMENSIONS, review_content

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("role", ("spec-review", "plan-review"))
def test_ac13_current_document_dimensions_match_the_revised_responsibilities(role):
    from heddle.gate.results import decode_review_content, review_output_contract

    contract = review_output_contract(role)
    assert contract.version == "heddle.review-content/v3"
    payload = review_content(role, altitude=True)
    decoded = decode_review_content(json.dumps(payload).encode(), contract)
    assert {row.id for row in decoded.details.dimensions} == set(
        ALTITUDE_DIMENSIONS[role]
    )
    if role == "spec-review":
        assert decoded.details.ac_specificity[0].assessment == "concrete"
        assert decoded.details.scope.approved_mvp_present
    else:
        assert decoded.details.ac_mapping[0].ac_id == "AC-1"
        assert decoded.details.dependencies[0].depends_on == ("m1",)


@pytest.mark.parametrize(
    "role,retired",
    [
        ("spec-review", "enforcement-awareness"),
        ("plan-review", "milestone-sizing"),
        ("plan-review", "document-size"),
    ],
)
def test_ac13_retired_dimensions_are_not_current_schema_obligations(role, retired):
    from heddle.gate.results import review_output_contract

    contract = review_output_contract(role)
    schema = json.loads(contract.schema_json)
    dimensions = schema["properties"]["details"]["properties"]["dimensions"]["items"][
        "properties"
    ]["id"]["enum"]
    assert retired not in dimensions, (
        "FAIL AC-13: obsolete criterion remains authoritative"
    )


def test_ac12_shipped_spec_template_has_product_commitments_and_one_ac_home():
    from heddle.gate.extraction import extract_markdown_section

    text = (ROOT / "heddle/resources/feature-spec.scaffold.md").read_text()
    for heading in (
        "## Purpose",
        "## Approved MVP (from brief)",
        "## Conceptual Design",
        "## Design Commitments",
        "## Acceptance Criteria",
        "## Decision Log",
        "## Outcomes & Retrospective",
    ):
        assert extract_markdown_section(text, heading), f"FAIL AC-12: missing {heading}"
    assert text.count("## Acceptance Criteria") == 1
    assert "## Architecture" not in text
    assert "### Requirements (EARS)" not in text
    assert "Priority" in text and "Verified-by" in text and "AC-" in text


def test_ac12_shipped_plan_template_owns_approach_without_duplicate_contract():
    text = (ROOT / "heddle/resources/plan.scaffold.md").read_text()
    assert "## Technical Architecture" in text
    assert "## Implementation Strategy" in text
    assert "### Integrated Witness Proposal" in text
    assert "Milestone <id>:" in text
    assert "heddle:begin plan-status" in text
    for retired in (
        "## Design Context",
        "## Active Enforcement Rules",
        "## Idempotence",
        "## Acceptance Criteria",
        "{{tier}}",
    ):
        assert retired not in text


def test_ac12_scaffold_review_reads_plan_test_boundary_and_binds_fresh_changes(
    tmp_path, monkeypatch
):
    """Native capture/preparation, with no gate execution or proof command run."""
    from heddle.gate import entry
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.types import GateInvocationOverrides
    from tests.content_identity_helpers import capture_context
    from tests.operational_model_helpers import FEATURE, SPEC, read, write
    from tests.tiering_review_helpers import current_host

    host, state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    state = read(state_path)
    state["commands"] = {
        "smoke_test": "python3 tests/check.py",
        "acceptance_test": "python3 tests/check.py",
    }
    write(state_path, state)
    state_before = state_path.read_bytes()
    spec_path = host / SPEC
    spec_text = (
        "# Product\n\n## Design Commitments\n\n"
        "The publication boundary preserves the last accepted VALUE on failure.\n\n"
        "## Acceptance Criteria\n\n### AC-1: Declared value\n\n"
        "Priority: MUST\nAction: Read VALUE.\nExpected: The value is 7.\n"
        "Failure behavior follows Design Commitments.\n"
        "Verified-by: tests/check.py\n\n## Decision Log\n\n"
        "SPEC_DECISION_NOT_SCAFFOLD_INPUT\n"
    )
    spec_path.write_text(spec_text)
    plan_path = host / f"plans/{FEATURE}/plan.md"
    plan_text = (
        "# Plan\n\n## Technical Architecture\n\n"
        "The public read_value boundary owns VALUE; test through that boundary.\n\n"
        "## Verification and Environment\n\n"
        "### Integrated Witness Proposal\n\n"
        "E2e boundary: query -> published artifact.\n"
        "Checkpoint ruling: D-WITNESS-1 in state.yaml; implementation and final "
        "e2e execution granted. No external service, so live is inapplicable.\n\n"
        "## Implementation Strategy\n\n"
        "### Verification Commands\n\n"
        "Inspect the native command facts; tests/check.py is the local witness.\n"
        "Contract evidence (inspection): public help exposes query VALUE; the "
        "fixture needs a readable record; the expected red assertion reports "
        "VALUE 7 but observes the missing query boundary. Collection has not "
        "established execution.\n\n"
        "### Live E2E Test Prerequisites\n\n"
        "No external execution is authorized by this fixture.\n\n"
        "## AC Coverage Matrix\n\nAC-1: tests/check.py\n\n"
        "## Non-review Notes\n\nPLAN_NOTE_NOT_SCAFFOLD_INPUT\n"
    )
    plan_path.write_text(plan_text)

    def prepare(context):
        invocation = resolve_gate_execution(
            context.gate_type,
            GateInvocationOverrides(cli="codex"),
        )
        return entry.prepare_gate_run(
            context, gate_type=context.gate_type, invocation=invocation
        )

    context = capture_context(host, role="review-test-scaffolding", lane="codex")
    first = prepare(context)
    prompt = first.transport.stdin
    assert "===== BEGIN PLAN SECTION: Technical Architecture =====" in prompt
    assert "The public read_value boundary owns VALUE" in prompt
    assert "===== BEGIN FEATURE SPEC SECTION: Acceptance Criteria =====" in prompt
    assert "Expected: The value is 7." in prompt
    assert "Failure behavior follows Design Commitments." in prompt
    assert "only their explicitly referenced defining commitments" in prompt
    assert "Read only the Acceptance Criteria section" not in prompt
    assert "Do not read other Feature Spec sections" not in prompt
    assert first.ac_ids == ("AC-1",)
    for heading in (
        "Integrated Witness Proposal",
        "Verification Commands",
        "Live E2E Test Prerequisites",
        "AC Coverage Matrix",
    ):
        assert f"===== BEGIN PLAN SECTION: {heading} =====" in prompt
    assert "===== BEGIN VERIFICATION COMMAND FACTS =====" in prompt
    assert '"smoke_test":"python3 tests/check.py"' in prompt
    assert "===== BEGIN RELEVANT TEST FILES =====\ntests/check.py" in prompt
    assert "Contract evidence (inspection): public help exposes query VALUE" in prompt
    assert "Collection has not established execution" in prompt
    assert "E2e boundary: query -> published artifact." in prompt
    assert "D-WITNESS-1 in state.yaml" in prompt
    assert "implementation and final e2e execution granted" in prompt
    assert "Follow its exact decision IDs in workspace state" in " ".join(
        first.prompt.effective_instructions.split()
    )
    assert "live_e2e_prerequisites_confirmed" not in first.prompt.effective_instructions
    instructions = " ".join(first.prompt.effective_instructions.split())
    assert "Require smoke_test and acceptance_test" in instructions
    assert "live_e2e_test when live is declared" in instructions
    assert "the exact native live/declined-live ruling" in instructions
    scaffold_context = next(
        section
        for section in first.runtime_sections
        if section.startswith(
            "Resolved authoritative inputs for review-test-scaffolding:"
        )
    )
    assert "SPEC_DECISION_NOT_SCAFFOLD_INPUT" not in scaffold_context
    assert "===== BEGIN ADJUDICATED DECISION GROUND =====" in prompt
    assert "SPEC_DECISION_NOT_SCAFFOLD_INPUT" in prompt
    for omitted in (
        "The publication boundary preserves the last accepted VALUE on failure.",
        "PLAN_NOTE_NOT_SCAFFOLD_INPUT",
    ):
        assert omitted not in prompt

    plan_path.write_text(plan_text.replace("read_value", "query_value"))
    captured = prepare(context)
    assert captured.transport == first.transport
    assert captured.review_basis_hash == first.review_basis_hash
    fresh = prepare(capture_context(host, role="review-test-scaffolding", lane="codex"))
    assert "The public query_value boundary owns VALUE" in fresh.transport.stdin
    assert "The public read_value boundary owns VALUE" not in fresh.transport.stdin
    assert fresh.prompt_version == first.prompt_version
    assert fresh.effective_prompt_sha256 != first.effective_prompt_sha256
    assert fresh.review_basis_hash != first.review_basis_hash
    assert fresh.input_hash != first.input_hash

    # A witness-only edit must change delivered evidence and current identity;
    # the earlier captured input remains stable. No provider or witness runs.
    plan_path.write_text(
        plan_text.replace("read_value", "query_value").replace(
            "query -> published artifact", "query -> recovered artifact"
        )
    )
    witness_changed = prepare(
        capture_context(host, role="review-test-scaffolding", lane="codex")
    )
    assert "query -> recovered artifact" in witness_changed.transport.stdin
    assert "query -> published artifact" not in witness_changed.transport.stdin
    assert witness_changed.review_basis_hash != fresh.review_basis_hash
    assert witness_changed.effective_prompt_sha256 != fresh.effective_prompt_sha256
    assert witness_changed.input_hash != fresh.input_hash
    assert witness_changed.prompt_version == fresh.prompt_version
    assert prepare(context).transport == first.transport

    # The model may read defining references from the resolved spec. The existing
    # complete-spec capture must bind those bytes even though the inline slice
    # stays AC-only; no second reference parser or copied commitments are needed.
    captured_spec = next(
        row for row in first.reviewed_inputs if row.name == "reviewed/spec"
    )
    assert captured_spec.content == spec_text
    spec_path.write_text(
        spec_text.replace("preserves the last accepted", "returns the previous")
    )
    changed_spec = prepare(
        capture_context(host, role="review-test-scaffolding", lane="codex")
    )
    assert changed_spec.review_basis_hash != witness_changed.review_basis_hash
    assert changed_spec.input_hash != witness_changed.input_hash
    assert prepare(context).review_basis_hash == first.review_basis_hash
    assert state_path.read_bytes() == state_before


@pytest.mark.parametrize(
    "asset,retired_rule",
    [
        ("specify.briefing.md", "Design Context derived from the spec"),
        (
            "implement.briefing.md",
            "Read the plan's Quick Orientation, current Design Context",
        ),
        ("prompts/spec-review.md", "Preserve Requirements/EARS"),
        ("prompts/plan-review.md", "Check the plan's size mechanically"),
        (
            "prompts/plan-review.md",
            "Design Context block: populated with Feature Spec content",
        ),
    ],
)
def test_ac12_retired_authoring_requirements_are_removed_at_the_actual_consumers(
    asset, retired_rule
):
    text = (ROOT / "heddle/resources" / asset).read_text()
    assert retired_rule not in text, (
        f"FAIL AC-12: {asset} still instructs {retired_rule!r}"
    )


AUTHORING_OBLIGATIONS = {
    "Author the Feature Spec": (
        "Purpose,",
        "`## Approved MVP (from brief)`:",
        "Conceptual Design:",
        "Design Commitments:",
        "One canonical Acceptance Criteria list:",
        "A Decision Log",
    ),
    "Author the Implementation Plan": (
        "Quick Orientation:",
        "Codebase Context:",
        "Technical Architecture:",
        "Implementation Strategy:",
        "Verification and Environment:",
        "The managed plan-status region.",
    ),
}


def assert_authoring_obligations(text):
    """
    Guard the authored positive inventory, not a ban on historical vocabulary.

        This checks the declared authoring, not arbitrary natural-language
        meaning. Delivered reviewer dimensions and template headings have their own
        tests above; owner review still judges prose outside the enumerated inventory.

    """
    from heddle.gate.extraction import extract_markdown_h2_section

    for section, expected in AUTHORING_OBLIGATIONS.items():
        body = extract_markdown_h2_section(text, section)
        obligations = re.findall(r"^- (.+)$", body, re.MULTILINE)
        assert len(obligations) == len(expected), (section, obligations)
        assert all(
            row.startswith(prefix)
            for row, prefix in zip(obligations, expected, strict=True)
        ), (section, obligations)


def test_ac12_authoring_obligation_inventory_keeps_contract_and_approach_separate():
    text = (ROOT / "heddle/resources/specify.briefing.md").read_text()
    assert_authoring_obligations(text)
    # An explicit retirement note is legitimate even with the old vocabulary.
    assert_authoring_obligations(
        text + "\nEARS and copied Design Context are retired.\n"
    )


@pytest.mark.parametrize(
    "requirement",
    [
        "An EARS list must accompany the Acceptance Criteria.",
        "Populate Design Context with the spec's commitments before implementation.",
    ],
)
def test_ac12_authoring_inventory_rejects_reintroduced_obligations_under_new_wording(
    requirement,
):
    text = (ROOT / "heddle/resources/specify.briefing.md").read_text()
    # Both are different wording from the old phrase checks; adding a positive
    # authoring duty cannot pass merely because that exact old sentence vanished.
    changed = text.replace(
        "## Validate and hand off", f"- {requirement}\n\n## Validate and hand off"
    )
    with pytest.raises(AssertionError):
        assert_authoring_obligations(changed)


@pytest.mark.parametrize("role", ("spec-review", "plan-review"))
def test_ac13_delivered_document_prompts_use_current_native_dimensions(
    tmp_path, monkeypatch, role
):
    """Delivered dimensions match the codec; this does not grade model judgment."""
    from heddle.gate import entry
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.types import GateInvocationOverrides
    from tests.content_identity_helpers import capture_context
    from tests.tiering_review_helpers import current_host

    host, state_path = current_host(tmp_path, monkeypatch, stage=role)
    before = state_path.read_bytes()
    context = capture_context(host, role=role, lane="codex")
    invocation = resolve_gate_execution(
        context.gate_type,
        GateInvocationOverrides(cli="codex"),
    )
    prepared = entry.prepare_gate_run(
        context, gate_type=context.gate_type, invocation=invocation
    )
    # Check the actual expanded role instructions, not schema text elsewhere in
    # the transport that could mask a missing or obsolete review dimension.
    text = prepared.prompt.effective_instructions
    dimensions = re.findall(r"^- \*\*([a-z-]+):\*\*", text, re.MULTILINE)
    assert dimensions == list(ALTITUDE_DIMENSIONS[role])
    assert "[partial-" not in text
    assert prepared.output_contract.version == "heddle.review-content/v3"
    assert prepared.ac_ids == ("AC-1",)
    assert state_path.read_bytes() == before


def test_ac12_current_milestone_delivers_only_its_ac_slice_and_binds_changes(
    tmp_path, monkeypatch
):
    from heddle.gate import entry
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.types import GateInvocationOverrides
    from tests.content_identity_helpers import capture_context
    from tests.operational_model_helpers import FEATURE, SPEC
    from tests.tiering_review_helpers import current_host

    host, state_path = current_host(tmp_path, monkeypatch, stage="implement")
    before = state_path.read_bytes()
    spec = host / SPEC
    spec.write_text(
        "# Product\n\n## Architecture\n\nOBSOLETE_ARCHITECTURE\n\n"
        "## Acceptance Criteria\n\n### AC-1: Current milestone\n\n"
        "Action: Publish a value.\nExpected: CURRENT_AC_CONTRACT.\n\n"
        "### AC-2: Unrelated milestone\n\nExpected: UNRELATED_AC_CONTRACT.\n"
    )
    plan = host / f"plans/{FEATURE}/plan.md"
    plan.write_text(plan.read_text() + "\n## Active Enforcement Rules\n\nOLD_RULES\n")

    def prepare():
        context = capture_context(host, role="milestone-review", lane="codex")
        invocation = resolve_gate_execution(
            context.gate_type,
            GateInvocationOverrides(cli="codex"),
        )
        return entry.prepare_gate_run(
            context, gate_type=context.gate_type, invocation=invocation
        )

    first = prepare()
    prompt = first.transport.stdin
    assert "===== BEGIN FEATURE SPEC ACCEPTANCE CRITERIA:" in prompt
    ac_slice = prompt.split("===== BEGIN FEATURE SPEC ACCEPTANCE CRITERIA:", 1)[
        1
    ].split("===== END FEATURE SPEC ACCEPTANCE CRITERIA =====", 1)[0]
    assert "CURRENT_AC_CONTRACT" in ac_slice
    assert "UNRELATED_AC_CONTRACT" not in ac_slice
    assert "current milestone's satisfied Acceptance Criteria" in prompt
    assert "their explicitly referenced defining commitments" in prompt
    # The actual diff may contain edits anywhere in owned documents. It must
    # not revive retired mandatory context sections outside that diff.
    context_only = prompt.split("===== BEGIN AUTHORITATIVE DIFF =====", 1)[0]
    assert "OBSOLETE_ARCHITECTURE" not in context_only
    assert "OLD_RULES" not in context_only
    assert first.ac_ids == ("AC-1",)
    spec.write_text(
        spec.read_text().replace("CURRENT_AC_CONTRACT", "REVISED_AC_CONTRACT")
    )
    changed = prepare()
    assert "REVISED_AC_CONTRACT" in changed.transport.stdin
    assert changed.review_basis_hash != first.review_basis_hash
    assert changed.effective_prompt_sha256 != first.effective_prompt_sha256
    assert changed.input_hash != first.input_hash
    assert state_path.read_bytes() == before
