"""
Gate scope discipline — L3 structural presence + L4 domain leakage.

Regression contract: imported workflow invariants are present in their target
prompt/doc and, where shared, spliced via the expected
`[partial-]` token — a rule that silently falls out of a template is the
drift these tests exist to catch. L4 is the re-grounding checklist as an
executable check: the ported partials must not require unavailable source code.

Pure filesystem + in-process expansion; no CLI spawn, no paid call.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from heddle.gate.prompt import (
    PACKAGED_PROMPTS_DIR,
    expand_partials,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS = PACKAGED_PROMPTS_DIR
PARTIALS = PROMPTS / "_partials"
DOCS = REPO_ROOT / "docs" / "workflow"
RESOURCES = REPO_ROOT / "heddle" / "resources"


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _folded(path: Path) -> str:
    """Whitespace-folded text, for prose anchors that may wrap across lines."""
    return " ".join(_raw(path).split())


def _expanded(prompt_name: str) -> str:
    return expand_partials(_raw(PROMPTS / prompt_name), PARTIALS)


class TestL3ReviewPromptRules:
    """
    Scope, time-boundary, and principle-evidence rules inside the gate
    prompts.
    """

    def test_necessity_anchor_hard_edges_reach_both_gates(self) -> None:
        for name in ("spec-review.md", "plan-review.md"):
            text = _expanded(name)
            for anchor in (
                "names the concrete test or entrypoint",
                "Borderline items default into Deferred Scope",
                "Ancillary tooling requires recorded owner authorization",
            ):
                assert anchor in text, (
                    f"{name} lost time boundary/ancillary scope edge: {anchor!r}"
                )

    def test_principles_render_in_prompt_with_tiebreak(self) -> None:
        # principle evidence: a principle only binds if the reviewer reads it in-prompt.
        for name in ("spec-review.md", "plan-review.md"):
            text = _expanded(name)
            assert "Core first; scope is earned" in text, name
            assert "builds less now" in text, name

    def test_expansionary_option_scoring_is_gone_corpus_wide(self) -> None:
        # Regression rail: the old closer biased every REPORT toward the
        # most-built option; nothing may reintroduce it.
        offenders = [
            str(p.relative_to(REPO_ROOT))
            for p in sorted(PROMPTS.glob("*.md"))
            if "reusable, clean, robust outcomes" in _raw(p)
            or "rationale emphasizing long-term reusability" in _raw(p)
        ]
        assert not offenders, f"expansionary scoring resurfaced: {offenders}"


class TestL3DocRules:
    """
    Scope-ownership and capability checks in docs and
    briefings.
    """

    def test_brainstorming_has_per_part_authorization(self) -> None:
        assert "### Implementation Parts" in _raw(DOCS / "brainstorming-guidelines.md")
        guidelines = _folded(DOCS / "brainstorming-guidelines.md")
        assert "per part, not blanket" in guidelines, (
            "scope ownership brief-side rule missing"
        )

    def test_briefings_carry_their_bindings(self) -> None:
        specify = _folded(RESOURCES / "specify.briefing.md")
        assert "Approved MVP (from brief)" in specify, "brief drafting step"
        assert "per part, not blanket" in specify, (
            "scope ownership checkpoint ownership"
        )
        assert "necessity-anchor.md" in specify, "ancillary scope ancillary opt-in"
        implement = _folded(RESOURCES / "implement.briefing.md")
        assert "necessity-anchor.md" in implement, "ancillary scope build-time bind"
        complete = _folded(RESOURCES / "complete.briefing.md")
        assert "measured-gap rule" in complete, "R6 distillation step"


CONCEPT_SECTIONS = (
    "What we're building",
    "Approach",
    "Flow",
    "Contracts and interfaces",
    "State and ownership",
    "Assumptions",
    "Failure behaviour",
    "Proof sketch",
    "Unknowns and risks",
    "Future considerations",
    "Open decisions",
)


def _h2_body(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert match, f"missing ## {heading}"
    return match.group(1)


def _dimension(text: str, dimension: str) -> str:
    """Whitespace-folded body of one `- **<dimension>:**` bullet."""
    match = re.search(
        rf"^- \*\*{re.escape(dimension)}:\*\*(.*?)(?=^- \*\*|^\S|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"missing dimension {dimension}"
    return " ".join(match.group(1).split())


class TestL3ConceptNote:
    """
    The owner approves the concept before the spec, and the document gates hold
    the spec and plan to it (owner ruling 2026-10-01).
    """

    def test_both_brief_formats_carry_the_concept_sections(self) -> None:
        guidelines = _raw(DOCS / "brainstorming-guidelines.md")
        brief_format = guidelines.split("## Feature Brief Format", 1)[1]
        for source, text in (
            ("brief scaffold", _raw(RESOURCES / "brief.scaffold.md")),
            ("brief format", brief_format),
        ):
            labels = re.findall(
                r"^\d+\. \*\*(.+?):\*\*", _h2_body(text, "Concept"), re.MULTILINE
            )
            assert tuple(labels) == CONCEPT_SECTIONS, source

    def test_specify_carries_the_concept_from_checkpoint_to_spec(self) -> None:
        specify = _folded(RESOURCES / "specify.briefing.md")
        for anchor in (
            "the sections the workspace brief's `## Concept` defines",
            "in the admission-bound research reference, with its recorded "
            "approval, is the approved concept",
            "Obtain it before drafting the spec or plan",
            "record one class-8 `question` to approve or revise the brief's "
            "Concept and end the session without drafting either document",
            "do not demand a retroactive concept",
            "Approach, Flow, Contracts and interfaces, and State and ownership "
            "sections verbatim with its approval reference and a labeled "
            "concept delta",
            "elaborating the approved concept rather than restating it",
            "Follow the approved concept's approach and flow",
            "confirms concept deltas",
        ):
            assert anchor in specify, anchor

    def test_packaged_concept_review_is_opt_in_and_self_contained(self) -> None:
        specify = _folded(RESOURCES / "specify.briefing.md")
        for anchor in (
            "Run it only when the owner opts in; in a driven session, only when "
            "a recorded owner resolution asks for it",
            "codex exec --ephemeral --sandbox read-only -m gpt-6-astra",
            "rather than as a native gate",
            "Without Codex, use the reviewer the owner names",
            "challenge the approach against the strongest alternative",
            "the owner chooses the fallback and you confirm afterwards that the "
            "review changed no files",
            "hand the prompt to the owner",
            "never in the feature's `reviews/` directory",
            "not native review evidence and grants no approval",
        ):
            assert anchor in specify, anchor

    def test_spec_scaffold_retains_the_concept_inside_approved_mvp(self) -> None:
        mvp = _h2_body(
            _raw(RESOURCES / "feature-spec.scaffold.md"), "Approved MVP (from brief)"
        )
        concept = re.search(
            r"^### Approved concept\n(.*?)(?=^### |\Z)", mvp, re.M | re.S
        )
        assert concept, "Approved concept must sit inside Approved MVP"
        body = " ".join(concept.group(1).split())
        assert "Contracts and interfaces, and State and ownership sections" in body
        assert "verbatim" in body and "concept delta" in body

    def test_document_gates_hold_spec_and_plan_to_the_concept(self) -> None:
        coherence = _dimension(_expanded("spec-review.md"), "conceptual-coherence")
        for anchor in (
            "elaboration is not a departure",
            "without a labeled concept delta is Important IMPLEMENT",
            "Each AC traces to that concept (a flow step, contract, or state or "
            "existing-record effect)",
            "not a second finding",
            "IMPLEMENT when the AC follows from the approved concept, REPORT "
            "when deferring it is the owner's choice",
            "A spec without an Approved concept is assessed without one",
        ):
            assert anchor in coherence, anchor
        spec = " ".join(_expanded("spec-review.md").split())
        assert "each labeled concept delta for confirmation item by item" in spec
        soundness = _dimension(_expanded("plan-review.md"), "approach-soundness")
        for anchor in (
            "elaborating it is not a departure",
            "adopts its rejected alternative or contradicts its flow",
            "IMPLEMENT when restoring the approved concept still meets the "
            "contract, REPORT when the plan shows the approved concept cannot",
        ):
            assert anchor in soundness, anchor

    def test_leads_confirm_deltas_and_route_accepted_departures(self) -> None:
        lead = _folded(RESOURCES / "spec-review.briefing.md")
        for anchor in (
            "The owner confirms deltas item by item",
            "plan's Technical Architecture against the approved approach and flow",
            "matches its approved source",
            "needs no retroactive concept",
            "each concept delta the same way",
        ):
            assert anchor in lead, anchor
        plan_lead = _folded(RESOURCES / "plan-review.briefing.md")
        assert "accepts a departure from the spec's approved concept" in plan_lead

    def test_guidelines_and_skills_describe_the_concept_review(self) -> None:
        guidelines = _folded(DOCS / "brainstorming-guidelines.md")
        for anchor in (
            "run it only when the owner opts in",
            "codex exec --ephemeral --sandbox read-only -m gpt-6-astra",
            'model_reasoning_effort="xhigh"',
            "rather than as a native gate",
            "the lead confirms afterwards that the review changed no files",
            "before `heddle feature prepare` binds the research digest",
        ):
            assert anchor in guidelines, anchor
        for agent in ("claude", "codex"):
            skill = _folded(REPO_ROOT / f".{agent}/skills/new-feature/SKILL.md")
            assert "offer the opt-in concept review" in skill, agent


class TestL4DomainLeakage:
    """§9.1 L4: shared partials/patterns have no source-only dependencies.

    Ordinary domain prose is permitted. Workspace paths, imports and the named
    source helpers identify dependencies Heddle does not supply.
    """

    SOURCE_ONLY_DEPENDENCIES = (
        r"(?<![\w./-])/(?:[^/\s`]+/)*private_app(?:/[^\s`]+|(?=$|[\s`)]))",
        r"\b(?:import\s+private_app(?:\.[\w.]+)?|"
        r"from\s+private_app(?:\.[\w.]+)?\s+import)\b",
        r"\bscripts/(?:feature_search|validate_docs)\.py\b",
    )

    def _scan_paths(self) -> list[Path]:
        paths = sorted(PARTIALS.glob("*.md"))
        patterns_dir = REPO_ROOT / "docs" / "patterns"
        if patterns_dir.is_dir():
            paths += sorted(patterns_dir.rglob("*.md"))
        return paths

    def test_no_source_only_dependencies_in_ported_surfaces(self) -> None:
        regexes = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.SOURCE_ONLY_DEPENDENCIES
        ]
        hits: list[str] = []
        for path in self._scan_paths():
            text = _raw(path)
            for regex in regexes:
                for match in regex.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    hits.append(
                        f"{path.relative_to(REPO_ROOT)}:{line}: {match.group(0)}"
                    )
        assert not hits, f"L4 unavailable source-only dependencies: {hits}"

    def test_the_generalized_consumer_wording_stuck(self) -> None:
        # This existing survivor must remain collected at class scope.
        anchor = _raw(PARTIALS / "necessity-anchor.md")
        assert "external consumer" in anchor
        assert "non-Python consumer" not in anchor


@pytest.mark.parametrize(
    "dependency",
    [
        "Load /srv/private_app/private_rules.md before review.",
        "Run `from private_app.private_rules import validate` before review.",
        "Run `python scripts/feature_search.py` to locate the feature.",
    ],
)
def test_w5_ac11_domain_check_distinguishes_prose_from_unavailable_dependency(
    tmp_path, monkeypatch, dependency
):
    """A read-only policy test must reject the actual external dependency."""
    surface = tmp_path / "example.md"
    checker = TestL4DomainLeakage()
    monkeypatch.setattr(checker, "_scan_paths", lambda: [surface])
    monkeypatch.setattr(
        __import__(__name__, fromlist=["REPO_ROOT"]), "REPO_ROOT", tmp_path
    )
    surface.write_text(
        "Record a point-in-time observation of the local ledger.\n"
        "A BigQuery or Dagster consumer is ordinary domain context.\n"
        "Historical context: imported workflow invariant.\n"
    )
    checker.test_no_source_only_dependencies_in_ported_surfaces()
    surface.write_text(dependency + "\n")
    with pytest.raises(
        AssertionError, match="(?i)(dependency|unavailable|private_app)"
    ):
        checker.test_no_source_only_dependencies_in_ported_surfaces()


class TestConventionCheckerModeAll:
    """Every mode-all convention marker must survive independently of headings."""

    CHECKER = REPO_ROOT / "scripts" / "check-prompt-conventions.py"
    NATIVE_CLAUSES = (
        ("_partials/settled-ground.md", "settled ground"),
        ("_partials/settled-ground.md", "re-raise"),
        (
            "_partials/severity-threat-actor-anchor.md",
            "must name its threat actor",
        ),
        (
            "_partials/severity-threat-actor-anchor.md",
            "only the routing of out-of-model gaps changes",
        ),
        (
            "_partials/boundary-and-proof.md",
            "every state that crosses a boundary",
        ),
        (
            "_partials/boundary-and-proof.md",
            "able to fail for the real defect",
        ),
    )

    def _seed(self, tmp_path: Path, prompt_body: str) -> None:
        prompts = tmp_path / "prompts"
        (prompts / "_partials").mkdir(parents=True)
        (prompts / "conventions.yaml").write_text(
            "conventions:\n"
            "  Pair:\n"
            "    mode: all\n"
            "    keywords:\n"
            "      - alpha marker\n"
            "      - beta marker\n"
            "prompts:\n"
            "  fixture.md:\n"
            "    - Pair\n"
        )
        (prompts / "fixture.md").write_text(prompt_body)

    def _run(self, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.CHECKER), str(cwd / "prompts")],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    @pytest.mark.toolchain
    def test_mode_all_passes_when_every_marker_present(self, tmp_path: Path) -> None:
        self._seed(tmp_path, "alpha marker and beta marker\n")
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.toolchain
    def test_mode_all_fails_on_a_single_missing_marker(self, tmp_path: Path) -> None:
        self._seed(tmp_path, "alpha marker only\n")
        result = self._run(tmp_path)
        assert result.returncode == 1
        assert "beta marker" in result.stdout

    @pytest.mark.parametrize(("name", "marker"), NATIVE_CLAUSES)
    @pytest.mark.toolchain
    def test_deleting_each_native_clause_fails_the_live_guard(
        self, tmp_path: Path, name: str, marker: str
    ) -> None:
        shutil.copytree(PROMPTS, tmp_path / "prompts")
        path = tmp_path / "prompts" / name
        original = _raw(path)
        assert original.count(marker) == 1
        assert self._run(tmp_path).returncode == 0
        path.write_text(original.replace(marker, ""))
        result = self._run(tmp_path)
        assert result.returncode == 1
        assert "spec-review.md" in result.stdout and marker in result.stdout
