"""Self-contained packaged generation-trigger contract tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from heddle.gate.extraction import extract_markdown_h2_section
from heddle.gate.prompt import (
    PACKAGED_STANDARDS_DOC,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

AUDIT_SURFACES = (
    "prompt wording",
    "analysis skills",
    "model and reasoning-effort defaults",
    "gate topology",
    "iteration and stop caps",
    "orchestration assumptions",
)

AUDIT_BOUNDARIES = (
    "separately owned proposals",
    "stage every candidate inertly",
    "must not silently change",
    "gates",
    "defaults",
    "stops",
    "caps",
    "skills",
    "prompts",
    "runtime",
    "runtime ownership",
)

MATERIALITY_TERMS = (
    "different model generation",
    "or family",
    "authoring",
    "review",
    "workflow-session",
    "provider change",
    "instruction-following",
    "refusal",
    "tool-use",
    "output behavior",
    "alias or patch rename",
    "without a behavior claim",
    "does not by itself require the full audit",
)


def _required_text(path: Path, *, ac: str) -> str:
    assert path.is_file(), f"FAIL {ac}: required artifact is missing: {path}"
    return path.read_text(encoding="utf-8")


def _trigger() -> str:
    text = _required_text(PACKAGED_STANDARDS_DOC, ac="AC-4")
    section = extract_markdown_h2_section(text, "Model-generation audit trigger")
    assert section, "FAIL AC-4: packaged Model-generation audit trigger is missing"
    return section


def _companion_trigger_boundary() -> str:
    text = _required_text(
        REPO_ROOT / "docs/workflow/prompt-refinement.md",
        ac="AC-4",
    )
    section = extract_markdown_h2_section(text, "When to use")
    assert section, "FAIL AC-4: checkout materiality boundary is missing"
    return section


def _trigger_errors(section: str, *, include_materiality: bool = True) -> list[str]:
    normalized = " ".join(section.casefold().split())
    required = (*AUDIT_SURFACES, *AUDIT_BOUNDARIES)
    if include_materiality:
        required = (*MATERIALITY_TERMS, *required)
    return [term for term in required if term not in normalized]


def test_survivor_packaged_standard_has_no_checkout_companion_dependency() -> None:
    """AC-6 survivor pin: installed policy remains self-contained."""
    text = PACKAGED_STANDARDS_DOC.read_text(encoding="utf-8")
    assert "docs/workflow/prompt-refinement.md" not in text
    assert "prompt-refinement.md" not in text


def test_ac4_trigger_audits_every_generation_sensitive_surface() -> None:
    missing = _trigger_errors(_trigger())
    assert not missing, f"FAIL AC-4: trigger omits required terms {missing}"


@pytest.mark.parametrize(
    "section_factory",
    (_trigger, _companion_trigger_boundary),
    ids=("packaged", "checkout"),
)
def test_ac4_materiality_boundary_matches_the_feature_contract(
    section_factory,
) -> None:
    section = section_factory()
    normalized = " ".join(section.casefold().split())
    missing = [term for term in MATERIALITY_TERMS if term not in normalized]
    assert not missing, (
        f"FAIL AC-4: model-generation materiality boundary omits {missing}"
    )


@pytest.mark.parametrize(
    "section_factory",
    (_trigger, _companion_trigger_boundary),
    ids=("packaged", "checkout"),
)
@pytest.mark.parametrize("removed", MATERIALITY_TERMS)
def test_ac4_each_materiality_term_is_independently_required(
    section_factory,
    removed: str,
) -> None:
    section = " ".join(section_factory().casefold().split())
    mutated = section.replace(removed, "[removed]")
    assert mutated != section, f"FAIL AC-4 fixture: section lacks {removed!r}"
    missing = [term for term in MATERIALITY_TERMS if term not in mutated]
    assert removed in missing, (
        f"FAIL AC-4: removing {removed!r} did not invalidate the materiality rule"
    )


@pytest.mark.parametrize("removed", (*AUDIT_SURFACES, *AUDIT_BOUNDARIES))
def test_ac4_each_trigger_obligation_is_independently_required(removed: str) -> None:
    section = _trigger()
    mutated = re.sub(re.escape(removed), "[removed]", section, flags=re.IGNORECASE)
    assert mutated != section, f"FAIL AC-4 fixture: trigger does not contain {removed}"
    assert removed in _trigger_errors(mutated), (
        f"FAIL AC-4: removing {removed!r} did not invalidate the trigger"
    )


def test_ac4_trigger_is_self_contained_and_vendor_neutral() -> None:
    section = _trigger()
    forbidden = (
        "docs/workflow/prompt-refinement.md",
        "prompt-refinement.md",
        "PrivateSource",
        "[LIB]",
        "Claude",
        "Anthropic",
        "OpenAI",
        "/srv/private-source",
    )
    offenders = [term for term in forbidden if term.casefold() in section.casefold()]
    assert not offenders, f"FAIL AC-4/AC-6: packaged trigger contains {offenders}"
