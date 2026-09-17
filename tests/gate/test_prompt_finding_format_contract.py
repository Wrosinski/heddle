"""
Prompt-to-result finding-format contract guard.

The finding-count telemetry only works if the format a gate *prompt* instructs
the LLM to emit is a format `gate.findings.extract_findings` can *parse*. Those
two artifacts drift independently, and nothing previously checked the seam:
`test_gate_findings.py` feeds the extractor a synthesized canonical output, so it
proves "if a gate emits `#### XX-C1:`, it is counted" but never that each gate's
real prompt actually emits that form. The gap let several prompts ship findings
the extractor scored 0 for (bracketed IDs, bullet-not-heading findings, and
self-review emitting no IDs at all).

All review roles pin explicit schema field guidance and round-trip a literal
finding through the codec and typed count/detail projections. Legacy grammar
pins remain only until gate removes the retired reader.

This module closes the Markdown seam in both directions:

* round-trip — every finding-producing gate's actual prompt, with code fences
  stripped (the inverse of the extractor's own fence isolation, so template
  examples shown inside a ```markdown block are exercised), must yield >= 1
  extractor-counted finding.
* extractor grammar pin — the bracket / contradiction tolerance the round-trip
  relies on is asserted directly, so narrowing the regex fails here rather than
  silently re-zeroing a conforming prompt.

Grammar and prefix reference:
heddle/resources/prompt-authoring-standards.md#finding-ids.
"""

from __future__ import annotations

import json
import re

import pytest

from heddle.gate.findings import (
    structured_details,
    structured_findings,
)
from heddle.gate.prompt import (
    packaged_prompt_path,
)
from heddle.gate.registry import FINDING_PRODUCING_GATES, GATES
from heddle.gate.results import ReviewOutputContract, decode_review_content


def _native_finding_sample(gate: str, text: str):
    """Pin native instruction fields and exercise their actual count projection."""
    from tests.structured_review_helpers import content, finding

    contract = GATES[gate].output_contract
    assert isinstance(contract, ReviewOutputContract)
    assert "Return exactly one JSON object" in text
    prefixes = {
        "self-review": "SR",
        "code-quality": "CQ",
        "spec-review": "SP",
        "plan-review": "PL",
        "review-test-scaffolding": "TS",
        "review-correctness": "CR",
        "review-tests": "TR",
        "review-architecture": "AR",
        "review-robustness": "RR",
        "robustness-analysis": "RA",
        "peer-review-sequential": "PR",
        "spec-review-synthesis": "SSR",
        "plan-review-synthesis": "SPR",
        "review-test-scaffolding-synthesis": "TSS",
        "review-synthesis": "SY",
    }
    prefix = prefixes[gate]
    assert f"{prefix}-<C|I|M><n>" in text
    assert "Use the schema's lowercase enum values" in text
    for field in ("severity", "classification", "confidence", "location", "evidence"):
        assert re.search(rf"\b{field}\b", text)
    sample = content(
        gate, findings=[finding(f"{prefix}-I1")], status="pass_with_conditions"
    )
    return decode_review_content(json.dumps(sample).encode(), contract)


# Equation-bearing gates may not be exempted from the prompt/extractor seam:
# every finding they instruct a reviewer to emit must affect the recorded
# actionability equation and REPORT decision projection.
EXEMPT_GATES: dict[str, str] = {}

CONTRACT_GATES = sorted(set(FINDING_PRODUCING_GATES) - set(EXEMPT_GATES))
RETAINED_LEGACY_FORMAT_GATES = sorted(
    {"peer-review-sequential", "review-test-scaffolding", "robustness-analysis"}
    & set(CONTRACT_GATES)
)


def test_exemptions_and_contract_set_are_well_formed() -> None:
    """Guard against rot in the parametrization itself: every exempt gate is a
    real finding-producing gate, and the two sets partition FINDING_PRODUCING."""
    unknown = set(EXEMPT_GATES) - set(FINDING_PRODUCING_GATES)
    assert not unknown, f"EXEMPT_GATES names non-finding gates: {sorted(unknown)}"
    assert set(CONTRACT_GATES) | set(EXEMPT_GATES) == set(FINDING_PRODUCING_GATES)


@pytest.mark.parametrize("gate", RETAINED_LEGACY_FORMAT_GATES)
def test_prompt_emits_extractor_countable_findings(gate: str) -> None:
    """
    Every non-exempt finding-producing gate's prompt must document a finding
        format the native projection counts. Fails for the right reason: no IDs, bullet
        (not
        heading) findings, or an ID grammar the extractor rejects all yield total 0.
    """
    prompt_path = packaged_prompt_path(GATES[gate].prompt_template)
    assert prompt_path.is_file(), f"{gate}: prompt missing at {prompt_path}"

    text = prompt_path.read_text(encoding="utf-8")
    native = _native_finding_sample(gate, text)
    info = structured_findings(native)
    assert info.total == 1
    assert info.by_severity_classification["important/report"] == 1


def test_equation_bearing_gates_have_no_format_exemptions() -> None:
    """A format exemption would let an equation-bearing gate converge empty."""
    assert EXEMPT_GATES == {}


@pytest.mark.parametrize("gate", RETAINED_LEGACY_FORMAT_GATES)
def test_every_finding_prompt_teaches_recommendation_and_single_route(
    gate: str,
) -> None:
    """A prompt's canonical finding block must feed both optional fields."""
    prompt_path = packaged_prompt_path(GATES[gate].prompt_template)
    text = prompt_path.read_text(encoding="utf-8")

    native = _native_finding_sample(gate, text)
    flat = " ".join(text.split())
    assert "Every REPORT has a `decision`" in flat
    assert "finding's `recommendation` states the proposed resolution" in flat
    assert "repo-relative single-file `route` or null" in flat
    details = structured_details(native)
    assert len(details) == 1
    assert details[0].recommendation == native.findings[0].recommendation
    assert native.findings[0].decision is not None
    assert details[0].route == native.findings[0].decision.route
