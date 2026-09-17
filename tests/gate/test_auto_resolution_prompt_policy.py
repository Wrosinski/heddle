"""Prompt policy for automatic implementation-resolution reviews."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from heddle.gate.prompt import PACKAGED_STANDARDS_DOC

ROOT = Path(__file__).resolve().parent.parent.parent
HOMES = (PACKAGED_STANDARDS_DOC,)


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _word_shingles(text: str, size: int) -> set[str]:
    words = re.findall(r"\w+", text.casefold())
    return {
        " ".join(words[index : index + size]) for index in range(len(words) - size + 1)
    }


@pytest.mark.parametrize("path", HOMES, ids=lambda path: path.name)
def test_ac13_each_rubric_home_carries_the_complete_policy(path: Path) -> None:
    text = _normalized(path)
    assert "docs/workflow/engineering-principles.md" in text, (
        f"FAIL AC-13: {path} must cite the ratified principles by path"
    )
    assert "- **Recommended**:" in text
    assert "- **Deciding principle**:" in text
    assert re.search(r"decis\w* principle.{0,180}IMPLEMENT", text, re.I), (
        f"FAIL AC-13: {path} must state decisive-principle -> IMPLEMENT"
    )
    assert re.search(r"uncertain.{0,120}REPORT", text, re.I), (
        f"FAIL AC-13: {path} must retain the uncertain -> REPORT safety rule"
    )


def test_ac13_survivor_principles_are_not_embedded_in_prompt_builder() -> None:
    """
    Survivor pin: document authority keeps the ratified document as the one prose
    source.
    """
    builder = (ROOT / "heddle" / "gate" / "prompt.py").read_text(encoding="utf-8")
    principles = (ROOT / "docs" / "workflow" / "engineering-principles.md").read_text(
        encoding="utf-8"
    )
    canonical_labels = re.findall(r"\*\*([^*\n]+)\*\*", principles)
    assert canonical_labels, "FIXTURE: canonical principle labels must parse"
    embedded_labels = [
        label for label in canonical_labels if label.casefold() in builder.casefold()
    ]
    assert not embedded_labels, (
        "FAIL AC-13/document authority: prompt.py embeds canonical "
        "principle/inviolable labels "
        f"instead of citing the ratified document by path: {embedded_labels!r}"
    )
    shared = sorted(_word_shingles(builder, 8) & _word_shingles(principles, 8))
    assert not shared, (
        "FAIL AC-13/document authority: prompt.py embeds an 8-word span from the "
        "canonical "
        f"engineering-principles prose: {shared[:3]!r}"
    )


def test_ac13_survivor_principle_stays_out_of_finding_and_decision_schemas() -> None:
    """REQ-12: decision deferral does not confuse artifact audit with decision schema.

    The auto-policy journal basis field is a separate, typed
    surface; findings and generic decisions gain recommendation only.
    """
    import dataclasses

    from heddle.contracts.schemas import (
        DECISION_KEYS,
        DECISION_OPTIONAL_KEYS,
        POLICY_JOURNAL_FIELDS,
    )
    from heddle.gate.findings import FindingDetail
    from heddle.kernel.state import DecisionFact

    assert (
        "basis" in POLICY_JOURNAL_FIELDS and "principle" not in POLICY_JOURNAL_FIELDS
    ), "policy basis belongs to the dedicated policy resolution contract"
    finding_fields = {field.name for field in dataclasses.fields(FindingDetail)}
    decision_fields = {field.name for field in dataclasses.fields(DecisionFact)}
    assert "principle" not in finding_fields
    assert "principle" not in decision_fields
    assert "principle" not in DECISION_KEYS
    assert "principle" not in DECISION_OPTIONAL_KEYS
