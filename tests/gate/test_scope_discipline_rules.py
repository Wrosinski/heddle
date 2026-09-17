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
    """Every native iteration clause must survive independently of headings."""

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
