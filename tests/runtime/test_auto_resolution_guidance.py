"""Operational guidance and briefings for automatic implementation resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_ac15_survivor_validate_is_strictly_clean(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    """Current host survivor: no blanket ADVISORY/4 tolerance remains."""
    from tests.runtime.validation_helpers import clean_current_host

    host = clean_current_host(tmp_path, ROOT / "tests/fixtures/workspaces/tiny")
    monkeypatch.chdir(host)
    code, out, _err = run_cli(["validate", "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 0 and envelope["ok"] is True
    assert envelope["diagnostics"] == []


# --------------------------------------------------------------------------- #
# completion (kickoff-guidance-integration) — current governing behavior (write)
# --------------------------------------------------------------------------- #
# Current-contract checks cover the workflow model, completion operational
# procedure, native Pattern 6 handoff, and completion guidance without reading
# the private feature records that originally introduced them.


def test_completion_workflow_model_documents_the_handoff_condition() -> None:
    """Survivor pin for AC-11: retain the documented completion handoff."""
    text = _normalized(ROOT / "docs" / "design" / "workflow-model.md").casefold()
    assert "awaiting-human-completion" in text, (
        "FAIL completion/AC-14: workflow-model.md must document the completion stage "
        "handoff blocking condition in its current-contract sections"
    )


@pytest.mark.parametrize(
    "doc",
    ["docs/workflow/document-structure.md"],
)
def test_completion_operational_docs_name_the_qualifying_fact(doc: str) -> None:
    text = _normalized(ROOT / doc).casefold()
    assert "heddle feature complete" in text, (
        f"FAIL W5/completion: {doc} must document the explicit acceptance operation "
        "in its completion stage operational procedures"
    )
    assert "completion.tar.gz" in text and "retai" in text


def test_decision_guidance_preserves_actor_and_authority_without_a_new_source() -> None:
    document = _normalized(
        ROOT / "docs" / "workflow" / "document-structure.md"
    ).casefold()
    addendum = _normalized(
        ROOT / "heddle" / "resources" / "autonomy-addendum.md"
    ).casefold()

    for text in (document, addendum):
        assert "explicit user" in text
        assert "standing user grant" in text
        assert "resolution_source: user" in text
        assert "record-policy" in text
        assert "escalat" in text
    assert "lead as actor" in document
    assert "field names the resolution path, not who reasoned" in addendum
    assert "generic autonomy" in addendum


def test_completion_pattern_six_is_translated_to_the_native_surface() -> None:
    """Survivor pin for AC-11: retain the native Pattern 6 handoff."""
    path = ROOT / "docs" / "workflow" / "subagent-exploration-patterns.md"
    raw = path.read_text(encoding="utf-8")
    sections = raw.split("### Pattern ")
    pattern_six = next(
        (section for section in sections if section.startswith("6")), None
    )
    assert pattern_six is not None, (
        "FIXTURE ROT: Pattern 6 heading not found in subagent-exploration-patterns.md"
    )
    normalized = " ".join(pattern_six.split()).casefold()
    assert "phase9-kickoff" not in normalized, (
        "FAIL completion/REQ-16: Pattern 6 must stop pointing at the retired "
        "prompts/phase9-kickoff.md source"
    )
    assert "complete.briefing" in normalized or "heddle kickoff" in normalized, (
        "FAIL completion: Pattern 6 must route completion stage through the native "
        "canonical "
        "briefing surface"
    )


def test_local_history_current_guidance_never_requires_record_commits(run_cli) -> None:
    """AC-11 red: current normative surfaces make workflow commits optional."""
    paths = (
        "docs/design/architecture.md",
        "docs/design/workflow-model.md",
        "docs/workflow/document-structure.md",
        "docs/workflow/workflow.md",
        "docs/workflow/subagent-exploration-patterns.md",
        "docs/testing/test-selection-map.md",
        "heddle/resources/complete.briefing.md",
    )
    forbidden = (
        "commit the accepted ledger",
        "accepted ledger and archive are committed",
        "pre-cleanup commit",
        "retention commit",
        "exact committed ledger/archive retention is required",
        "exact committed accepted ledger/archive bytes",
        "verify and commit the accepted ledger",
        "commit the accepted ledger and",
    )
    for relative in paths:
        text = _normalized(ROOT / relative).casefold()
        assert not [phrase for phrase in forbidden if phrase in text], (
            f"FAIL local-history AC-11: {relative} still requires a workflow "
            "record commit before cleanup"
        )
        required = (
            "source baseline",
            "record commit",
            "optional",
            "completion.tar.gz",
            "local retention",
            "recovery",
            "initial source commit",
        )
        assert not [token for token in required if token not in text], (
            f"FAIL local-history AC-11: {relative} omits required guidance semantics"
        )

    code, help_text, error = run_cli(["feature", "complete", "--help"])
    assert code == 0, error
    folded_help = " ".join(help_text.split()).casefold()
    assert "local retention" in folded_help
    assert "record commits optional" in folded_help
