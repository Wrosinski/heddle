"""
P0 contracts for the mirrored analysis-skill refresh.

Behavior contract: direct-mirrored-skill-refresh

FG-2026-09-09 replaces RCA depth/fan-out proxies with causal evidence,
conditional delegation, and explicit incomplete-analysis outcomes. The
pre-implementation candidate was withheld; its baseline contract remains.
These are text-delivery guards; retained provider cases assess actual judgment.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_NAMES = (
    "new-feature",
    "pre-implementation-analysis",
    "root-cause-analysis",
)
EXPECTED_FRONTMATTER = {
    "new-feature": {
        "name": "new-feature",
        "description": (
            "Kick off a new feature through Phase 1 (Research & Brainstorming) and\n"
            "Phase 2 (Specification). Takes an optional feature brief path. Use when\n"
            "starting a new feature from scratch within the current session."
        ),
    },
    "pre-implementation-analysis": {
        "name": "pre-implementation-analysis",
        "description": (
            "Produce a structured pre-implementation analysis "
            "(dependencies, contracts,\n"
            "edge cases, risks, open questions) for a change whose blast radius is\n"
            "larger than one module. Analysis document only, no code."
        ),
    },
    "root-cause-analysis": {
        "name": "root-cause-analysis",
        "description": (
            "Root-cause a bug whose symptom may mislead: intermittent, flaky, or\n"
            'already "fixed" once and back. Produces the causal chain with evidence\n'
            "and a solution proposal, not the implementation."
        ),
    },
}


def _skill_path(agent: str, name: str) -> Path:
    return REPO_ROOT / f".{agent}" / "skills" / name / "SKILL.md"


def _skill_text(name: str) -> str:
    return _skill_path("codex", name).read_text(encoding="utf-8")


def _frontmatter(text: str) -> Mapping[str, object]:
    lines = text.splitlines()
    assert lines and lines[0] == "---", "FAIL AC-1: missing opening frontmatter fence"
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise AssertionError("FAIL AC-1: missing closing frontmatter fence") from exc
    parsed = yaml.safe_load("\n".join(lines[1:closing]))
    assert isinstance(parsed, Mapping), "FAIL AC-1: frontmatter is not a mapping"
    if isinstance(parsed.get("description"), str):
        parsed["description"] = " ".join(parsed["description"].split())
    return parsed


def _analysis_contract_errors(name: str, text: str) -> list[str]:
    text = " ".join(text.split())
    shared = {
        "delegation contract": (
            "## Delegation",
            "docs/workflow/subagent-exploration-patterns.md",
        ),
        "analysis authority": (
            "does not authorize implementation",
            "authorization already supplied",
        ),
    }
    if name == "pre-implementation-analysis":
        # Its candidate was withheld after comparative overclaims; retain its
        # existing delivered contract and historical guard.
        shared = {
            "model-neutral fan-out": ("fan out sub-agents", "agent team only if"),
            "proportional empty output": (
                'Sections with nothing to report say "None found"',
                "length follows the change footprint",
            ),
        }
    per_skill = {
        "pre-implementation-analysis": {
            "coverage lenses": (
                "six phases below are lenses on one change",
                "they do not gate each other",
            ),
            "completion bar": (
                "## Completion Bar",
                "Every file in the change scope",
                "Every integration point documents both contract directions",
                "Every open question carries a resolution approach",
                "Both the change itself and its blast radius are covered",
            ),
            "native search": ('heddle search "<intent>" --titles-only',),
        },
        "root-cause-analysis": {
            "evidence-based stopping": (
                "available in-scope checks are exhausted",
                "An unsupported causal link stays uncertain",
            ),
            "causal discrimination": (
                "no required number of whys, branches, or hypotheses",
                "reversion alone does not distinguish them",
                "An external cause can be established",
            ),
            "conditional delegation": (
                "while the caller continues useful work",
                "Intermittency or a prior failed fix alone does not require",
            ),
            "actionable recommendation": (
                "recommended change and its causal connection",
                "smallest meaningful check that distinguishes the diagnosis",
            ),
        },
    }
    errors: list[str] = []
    for label, fragments in shared.items():
        if not all(fragment in text for fragment in fragments):
            errors.append(label)
    for label, fragments in per_skill[name].items():
        if not all(fragment in text for fragment in fragments):
            errors.append(label)

    stale_proxies = {
        "pre-implementation-analysis": (
            "Work through each phase completely before moving to the next.",
        ),
        "root-cause-analysis": (
            "fewer than three levels",
            "If yes, you've addressed root cause",
            "enumerate at least two branches at each level",
        ),
    }
    if any(fragment in text for fragment in stale_proxies[name]):
        errors.append("arbitrary sequence or depth proxy")
    return errors


@pytest.mark.parametrize("name", ("pre-implementation-analysis", "root-cause-analysis"))
def test_w5_ac11_reflow_preserves_obligations_but_removal_does_not(name):
    import textwrap

    original = _skill_text(name)
    reflowed = "\n".join(
        textwrap.fill(line, width=45, break_long_words=False, break_on_hyphens=False)
        for line in original.splitlines()
    )
    assert not _analysis_contract_errors(name, reflowed), (
        "FAIL W5 AC-11: wrapping changed an obligation verdict"
    )
    clause, error = (
        ("fan out sub-agents", "model-neutral fan-out")
        if name == "pre-implementation-analysis"
        else ("docs/workflow/subagent-exploration-patterns.md", "delegation contract")
    )
    removed = original.replace(clause, "unbound delegation")
    assert error in _analysis_contract_errors(name, removed)


def test_p0_ac1_selected_skill_mirrors_have_valid_identity() -> None:
    """Survivor + AC-1: mirrors and trigger metadata stay valid."""
    for name in SKILL_NAMES:
        codex = _skill_path("codex", name)
        claude = _skill_path("claude", name)
        assert codex.read_bytes() == claude.read_bytes(), (
            f"FAIL AC-1: {name} Claude/Codex mirrors drifted"
        )
        metadata = _frontmatter(codex.read_text(encoding="utf-8"))
        expected = {
            **EXPECTED_FRONTMATTER[name],
            "description": " ".join(EXPECTED_FRONTMATTER[name]["description"].split()),
        }
        assert metadata == expected, (
            f"FAIL AC-1: {name} frontmatter is generic, copied, or unexpected"
        )


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_w5_ac11_description_reflow_preserves_trigger_identity(name):
    original = _frontmatter(_skill_text(name))
    reflowed = dict(original, description=original["description"].replace(" ", "  "))
    text = "---\n" + yaml.safe_dump(reflowed) + "---\n"
    assert _frontmatter(text) == original
    unrelated = dict(original, description="An unrelated skill trigger.")
    assert _frontmatter("---\n" + yaml.safe_dump(unrelated) + "---\n") != original


@pytest.mark.parametrize("name", ("pre-implementation-analysis", "root-cause-analysis"))
def test_p0_ac2_analysis_skills_carry_accepted_semantics(name: str) -> None:
    """Red discriminator: accepted semantics replace count/sequence proxies."""
    errors = _analysis_contract_errors(name, _skill_text(name))
    assert not errors, f"FAIL AC-2: {name} contract gaps: {', '.join(errors)}"


@pytest.mark.parametrize(
    ("name", "fragment", "expected_error"),
    (
        (
            "pre-implementation-analysis",
            "six phases below are lenses on one change",
            "coverage lenses",
        ),
        (
            "pre-implementation-analysis",
            "Every integration point documents both contract directions",
            "completion bar",
        ),
        (
            "root-cause-analysis",
            "available in-scope checks are exhausted",
            "evidence-based stopping",
        ),
        (
            "root-cause-analysis",
            "recommended change and its causal connection",
            "actionable recommendation",
        ),
    ),
)
def test_p0_ac2_each_load_bearing_clause_is_independently_required(
    name: str, fragment: str, expected_error: str
) -> None:
    """AC-2 mutation proof: removing one semantic axis is observable."""
    text = _skill_text(name)
    assert not _analysis_contract_errors(name, text), (
        f"FAIL AC-2: {name} must satisfy the base contract before mutation"
    )
    assert fragment in text
    defective = text.replace(fragment, "", 1)
    assert expected_error in _analysis_contract_errors(name, defective)


def test_p0_ac4_analysis_skills_exclude_source_only_policy() -> None:
    """AC-4: compatible analysis guidance carries no product/runtime policy."""
    forbidden = (
        "scripts/feature_search.py",
        "scripts/validate_docs.py",
        "phase_authorized_through",
        "PRIVATE_APP_SKIP",
        "claude-fable",
        "claude-opus",
        "import private_app",
        "prompts/phase12-kickoff.md",
    )
    for name in ("pre-implementation-analysis", "root-cause-analysis"):
        lowered = _skill_text(name).lower()
        found = [fragment for fragment in forbidden if fragment.lower() in lowered]
        assert not found, f"FAIL AC-4: {name} retains source-only policy: {found}"
