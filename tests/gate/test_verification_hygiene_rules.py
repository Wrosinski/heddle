"""
Prompt and resource contracts for verification hygiene.

Pure filesystem and in-process expansion; no CLI or provider launch.
"""

from __future__ import annotations

from pathlib import Path

from heddle.gate.prompt import PACKAGED_PROMPTS_DIR, expand_partials
from heddle.gate.registry import GATES

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS = PACKAGED_PROMPTS_DIR
PARTIALS = PROMPTS / "_partials"
DOCS = REPO_ROOT / "docs" / "workflow"
RESOURCES = REPO_ROOT / "heddle" / "resources"

# The full review corpus, derived from the gate registry so a future gate
# cannot silently escape the corpus-wide splice requirements (review-final
# finding 2). A gate that should legitimately skip the shared partials gets
# an entry here with a one-line justification.
SPLICE_EXEMPT: frozenset[str] = frozenset()
REVIEW_PROMPTS = tuple(
    sorted({Path(g.prompt_template).name for g in GATES.values()} - SPLICE_EXEMPT)
)


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _expanded(prompt_name: str) -> str:
    return expand_partials(_raw(PROMPTS / prompt_name), PARTIALS)


class TestUnifiedThreatModel:
    """Decision 2: one identically-worded threat-model rule everywhere."""

    def test_single_rule_corpus_wide(self) -> None:
        # The subsume's exit evidence: the trust-model statement lives in
        # exactly one place — the partial. An inline copy resurfacing in a
        # template recreates the two-rule contradiction Decision 2 rejected.
        # Corpus-relative, not repo-relative: the invariant is "exactly one
        # file in the corpus carries the rule", which must hold wherever the
        # corpus lives (A1 moved it into the package).
        carriers = [
            str(p.relative_to(PROMPTS))
            for p in sorted(PROMPTS.rglob("*.md"))
            if "single-user local" in _raw(p)
        ]
        assert carriers == ["_partials/severity-threat-actor-anchor.md"], (
            f"threat-model rule duplicated or lost: {carriers}"
        )

    def test_name_the_actor_layer(self) -> None:
        partial = _raw(PARTIALS / "severity-threat-actor-anchor.md")
        assert "must name its threat actor" in partial
        assert "trust model" in partial


class TestH1SettledGroundPartial:
    def test_every_review_prompt_splices_settled_ground(self) -> None:
        missing = [
            name
            for name in REVIEW_PROMPTS
            if "[partial-settled-ground]" not in _raw(PROMPTS / name)
        ]
        assert not missing, f"settled-ground splice missing from: {missing}"

    def test_no_self_classification_field(self) -> None:
        # Decision 1 rejected fin's label-and-inherit: the reviewer never
        # self-tags 'Disposition: carried (re-raise of D-<ID>)'.
        partial = _raw(PARTIALS / "settled-ground.md")
        assert "Disposition:" not in partial
        assert "carried (re-raise" not in partial
        assert "settled ground" in partial and "re-raise" in partial


class TestVerificationSweepGrafts:
    def test_implement_briefing_carries_build_time_sweeps(self) -> None:
        text = " ".join(_raw(RESOURCES / "implement.briefing.md").split())
        assert "blast-radius sweep" in text
        assert "class sweep" in text


class TestSmallHygieneRules:
    def test_implement_briefing_owns_at_edit_time(self) -> None:
        text = " ".join(_raw(RESOURCES / "implement.briefing.md").split())
        assert "amends `owns`" in text, "structured blockers"
        assert "heddle milestone edit" not in text, (
            "structured blockers: milestone writes are boundary-owned; the implement "
            "briefing must not instruct the session to run milestone edit"
        )
