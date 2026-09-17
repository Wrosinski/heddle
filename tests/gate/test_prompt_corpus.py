"""
commands core — prompt-corpus render + citation sweeps (AC-1..AC-5).

Behavior contract: commands-native-switchover
Milestone: core (Machine-consumed prompt refresh + render sweep); this file is
the REQ-4 corpus test, pinned home per the plan, joined to core's `owns` at
implement time (PL-C2 owns lifecycle).

preimplementation state (scaffold stage): the live corpus still carries the legacy
mechanics — CLAUDE.md citations (REQ-3a), dead env-var fallbacks (A4),
plan-YAML-header extraction prose (REQ-1), the flat pre-E1 artifact layout in
prompts/_index.md (REQ-5), and the stale enforcement-baseline lines (REQ-6) —
so the AC-2..AC-5 tests fail until core lands. AC-1's render sweep is the
permanent P2-2 tripwire between a token typo and a corrupted paid gate run;
it pins the contract from day one (pass-triage: if green pre-core it is an
invariant guard, not a tautology — the renderer is silent on misses and the
sweep is the only thing that would catch a regression).

Everything here is pure filesystem + in-process rendering: no CLI spawn, no
paid call, no ambient-cwd heddle invocation (REQ-4/REQ-31). The corpus under
test is the repo's own `prompts/` tree; the render context is a fabricated
fixture context anchored to tmp_path.
"""

from __future__ import annotations

import functools
import re
import shutil
from pathlib import Path

import pytest

from heddle.gate import prompt as prompt_mod
from heddle.gate.cli import resolve_gate_execution
from heddle.gate.preparation import prepare_gate_run
from heddle.gate.registry import GATES
from heddle.gate.types import (
    DiffResult,
    GateContext,
    GateInvocationOverrides,
)
from tests.tiering_helpers import ROLES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_PATH = prompt_mod.PACKAGED_PROMPTS_DIR / "_index.md"
BASELINE_PATH = REPO_ROOT / "docs" / "workflow" / "enforcement-baseline.md"

# The renderer's token grammar (gate/prompt.py:104) — the P2-2 contract's
# subject. Uppercase/dotted bracket text (`[N]`, `[.codex]`) is outside it.
TOKEN_RE = re.compile(r"\[([a-z0-9_-]+)\]")

# P2-2 class-(b) whitelist: legitimate bracket prose in rendered output.
# Starts empty by contract; every entry must carry a one-line justification.
STRAY_TOKEN_WHITELIST: dict[str, str] = {}

# Any-extension file citation — AC-2's Expected covers every docs/ and
# templates/ citation, not only .md targets (review path).
CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_/.-])(?:docs|templates)/[A-Za-z0-9_/.-]+\.[A-Za-z0-9]+"
)

# REQ-3 broken citation targets (a)-(c); (d) is the anchor test below and
# The remaining command-side citation behavior is covered by runtime tests.
BROKEN_CITATION_TARGETS = (
    "CLAUDE.md",
    "docs/patterns/_shared/transactional-wipe-commit-before-rebuild.md",
    "docs/patterns/workflow/non-tautological-fixture-design.md",
)

LEGACY_INVOCATIONS = (
    "run-gate.sh",
    "run_gate.py",
    "run-kickoff.sh",
    "python -m heddle.gate",
    "gpt-5.4",
)
DEAD_ENV_FALLBACKS = ("IMPLEMENTATION_PLAN", "FEATURE_SPEC")  # A4
HEADER_EXTRACTION_MARKERS = (
    "YAML header",
    "grep -m1",
)


def _registry_prompt_paths() -> list[Path]:
    return sorted(
        {prompt_mod.packaged_prompt_path(g.prompt_template) for g in GATES.values()}
    )


@functools.cache
def _source_text(path: Path) -> str:
    """Single decode point for corpus sources (not mutated during the run)."""
    return path.read_text(encoding="utf-8")


def _corpus_hits(tokens: tuple[str, ...]) -> list[str]:
    """Every (file, line) in current registry prompt sources hitting a token."""
    hits: list[str] = []
    for path in _registry_prompt_paths():
        for lineno, line in enumerate(_source_text(path).splitlines(), start=1):
            for token in tokens:
                if token in line:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {token}")
    return hits


def _fixture_context(gate_name: str, tmp_path: Path) -> GateContext:
    workspace = tmp_path / "plans" / "m6b-fixture"
    (workspace / "reviews").mkdir(parents=True, exist_ok=True)
    plan_path = workspace / "plan.md"
    plan_path.write_text("# Implementation Plan: m6b-fixture\n", encoding="utf-8")
    spec_path = tmp_path / "docs" / "m6b-fixture.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("# m6b-fixture\n", encoding="utf-8")
    context = GateContext(
        gate_type=GATES[gate_name],
        feature="m6b-fixture",
        repo_root=tmp_path,
        plan_path=plan_path,
        spec_path=spec_path,
        feature_owned_paths=(spec_path.relative_to(tmp_path).as_posix(),),
        milestone="m1",
        # New-role engine contexts are tier-free under v7. Native assignment
        # admission is covered by test_tiering_dispositions; this fixture only
        # exercises rendering, retaining legacy tier context for old roles.
        tier=None
        if gate_name in {"milestone-review", "behavior-review", "complexity-review"}
        else "3",
        plan_content=plan_path.read_text(encoding="utf-8"),
        spec_content=spec_path.read_text(encoding="utf-8"),
        workspace_dir=workspace,
    )
    for synthesis_input in context.gate_type.synthesis_inputs:
        if synthesis_input.optional:
            continue
        cli = synthesis_input.cli or "claude"
        suffix = ".codex.md" if cli == "codex" else ".md"
        artifact = workspace / "reviews" / f"{synthesis_input.gate_name}{suffix}"
        artifact.write_text(f"# {synthesis_input.gate_name}\n", encoding="utf-8")
    return context


def _captured_render(
    gate_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, set[str]]:
    """Render one registry prompt through the prepared production path.

    Returns (rendered prompt body, variable-name set), both captured
    test-side from the single `render_template` call prompt resolution makes —
    the sweep asserts on exactly what production ships, with production's
    own arguments (Interfaces/P2-2: never a production constant extraction).
    """
    captured: dict[str, object] = {}
    real_render = prompt_mod.render_template

    def _spy(template: str, variables: dict[str, str], **kwargs: bool) -> str:
        result = real_render(template, variables, **kwargs)
        if "rendered" not in captured:
            captured["rendered"] = result
            captured["variables"] = dict(variables)
        return result

    monkeypatch.setattr(prompt_mod, "render_template", _spy)
    shutil.copytree(prompt_mod.PACKAGED_PROMPTS_DIR, tmp_path / "prompts")
    ctx = _fixture_context(gate_name, tmp_path)
    from tests.structured_review_helpers import prepare_render_producers

    prepare_render_producers(ctx)
    diff_path = tmp_path / "scaffold.diff"
    diff_path.write_text("", encoding="utf-8")
    diff = DiffResult(
        source="working-tree",
        source_detail="scaffold fixture (empty)",
        diff_path=diff_path,
        changed_files=[],
    )
    invocation = resolve_gate_execution(
        ctx.gate_type, GateInvocationOverrides(cli="claude")
    )
    prepare_gate_run(
        ctx,
        gate_type=ctx.gate_type,
        invocation=invocation,
        prompt=prompt_mod.resolve_gate_prompt(ctx, ctx.gate_type, cli="claude"),
        diff=diff,
    )
    assert "rendered" in captured, (
        f"FAIL AC-1: preparing {gate_name} never reached render_template — "
        "the interception seam moved; repoint the sweep (P2-2)"
    )
    variables = captured["variables"]
    assert isinstance(variables, dict)
    rendered = captured["rendered"]
    assert isinstance(rendered, str)
    return rendered, set(variables)


def _non_link_stray_tokens(rendered: str) -> list[str]:
    """Grammar matches outside Markdown link constructs (P2-2 class b).

    A Markdown link construct is exactly: a token immediately followed by
    `(` (inline link) or `[` (reference link), or a token at line start
    immediately followed by `:` (reference definition).
    """
    strays: list[str] = []
    for match in TOKEN_RE.finditer(rendered):
        following = rendered[match.end() : match.end() + 1]
        if following in ("(", "["):
            continue
        line_start = rendered.rfind("\n", 0, match.start()) + 1
        if match.start() == line_start and following == ":":
            continue
        if match.group(1) in STRAY_TOKEN_WHITELIST:
            continue
        strays.append(match.group(0))
    return strays


class TestAC1RenderSweep:
    """AC-1: all current registry prompts render clean per the token contract."""

    def test_registry_names_current_prompt_templates(self) -> None:
        paths = _registry_prompt_paths()
        assert set(GATES) == set(ROLES)
        assert {p.name for p in paths} == {f"{role}.md" for role in ROLES}
        missing = [str(p) for p in paths if not p.is_file()]
        assert not missing, f"FAIL AC-1: registry prompts missing on disk: {missing}"


class TestAC2CitationSweep:
    """AC-2: every docs/templates citation in the 15 prompts resolves."""

    def test_every_cited_path_resolves_from_repo_root(self) -> None:
        # Scan the prompt as the model receives it: citations that live in
        # a spliced partial are part of the contract too (back-port L3).
        partials_dir = prompt_mod.PACKAGED_PROMPTS_DIR / "_partials"
        broken: list[str] = []
        for path in _registry_prompt_paths():
            expanded = prompt_mod.expand_partials(_source_text(path), partials_dir)
            for cited in sorted(set(CITATION_RE.findall(expanded))):
                if not (REPO_ROOT / cited).is_file():
                    broken.append(f"{path.relative_to(REPO_ROOT)} -> {cited}")
        assert not broken, (
            "FAIL AC-2: prompt citations that do not resolve from the repo "
            f"root: {broken} (REQ-3; the sweep also fails when a later "
            "milestone deletes a still-cited file — Boundary conditions)"
        )

    def test_broken_targets_are_gone(self) -> None:
        hits = _corpus_hits(BROKEN_CITATION_TARGETS)
        assert not hits, (
            f"FAIL AC-2: the REQ-3(a-c) broken citation targets are still cited: {hits}"
        )

    def test_convergence_loop_anchor_resolves_if_cited(self) -> None:
        # REQ-3(d): the workflow.md "§ Review Gate Convergence Loop" citation
        # must resolve (anchor restored at the reader refresh) or be gone at core.
        anchor = "Review Gate Convergence Loop"
        citing = [
            str(p.relative_to(REPO_ROOT))
            for p in _registry_prompt_paths()
            if anchor in _source_text(p)
        ]
        if citing:
            workflow = REPO_ROOT / "docs" / "workflow" / "workflow.md"
            assert anchor in _source_text(workflow), (
                f"FAIL AC-2: {citing} cite the workflow.md '§ {anchor}' anchor "
                "but no such heading exists (REQ-3d: verify/restore the anchor "
                "or drop the citation)"
            )


class TestAC3NoLegacyMechanics:
    """AC-3: zero legacy-mechanics instructions across the 15 prompts."""

    def test_no_legacy_invocations(self) -> None:
        hits = _corpus_hits(LEGACY_INVOCATIONS)
        assert not hits, (
            f"FAIL AC-3: legacy invocation instructions remain: {hits} "
            "(INV-5 native translation table)"
        )

    def test_no_dead_env_var_fallbacks(self) -> None:
        hits = _corpus_hits(DEAD_ENV_FALLBACKS)
        assert not hits, (
            f"FAIL AC-3: dead env-var fallback clauses remain: {hits} "
            "(Assumption A4 — nothing sets these; every fallback clause drops "
            "at refresh)"
        )

    def test_no_plan_header_extraction_prose(self) -> None:
        hits = _corpus_hits(HEADER_EXTRACTION_MARKERS)
        assert not hits, (
            f"FAIL AC-3: plan-YAML-header extraction prose remains: {hits} "
            "(REQ-1 — the uniform Gate Runtime Manifest direction replaces "
            "every instance; review-test-scaffolding reads milestone "
            "verification payloads from state.yaml/the manifest)"
        )

    def test_scaffold_review_prompt_declares_structured_assessment_fields(self) -> None:
        """The promoted instructions name the concrete infrastructure contract."""
        gate = GATES["review-test-scaffolding"]
        prompt = _source_text(prompt_mod.packaged_prompt_path(gate.prompt_template))
        for field in ("ac_tests", "infrastructure", "dimensions"):
            assert field in prompt, f"Prompt omits structured assessment field {field}"

    def test_scaffold_review_prompt_honors_labelled_survivor_pins(self) -> None:
        """The review gate and scaffold briefing share the dual-phase rule."""
        prompt = _source_text(
            prompt_mod.PACKAGED_PROMPTS_DIR / "review-test-scaffolding.md"
        )
        normalized = " ".join(prompt.split())
        required = (
            "red discriminator",
            "labelled survivor pin",
            "same AC also has a red discriminator",
            "Do not flag a justified, labelled survivor pin",
        )
        missing = [phrase for phrase in required if phrase not in normalized]
        assert not missing, (
            "review-test-scaffolding regressed to an all-red rule and now "
            f"contradicts the scaffold briefing: {missing}"
        )

    def test_feature_scoped_gate_templates_avoid_milestone_token(self) -> None:
        """
        `[milestone]` is only guaranteed a value for the per-milestone
                implement gates (`requires_milestone=True`); a feature-scoped gate
                can run with no current milestone, rendering the token as an empty
                backticked value in its prompt (adoption live finding).
        """
        from heddle.gate.registry import GATES

        offenders = [
            name
            for name, gate in GATES.items()
            if not gate.requires_milestone
            and "[milestone]"
            in _source_text(prompt_mod.packaged_prompt_path(gate.prompt_template))
        ]
        assert not offenders, (
            "feature-scoped gate templates reference the `[milestone]` "
            f"token, which renders empty when no milestone is current: "
            f"{offenders}"
        )


class TestAC5EnforcementBaselineTrue:
    """
    AC-5: enforcement-baseline.md is true; hook-green half rides the
        milestone verification command (pre-commit hook run standalone, PL-core).
    """

    def test_last_updated_reflects_the_m6b_edit(self) -> None:
        text = _source_text(BASELINE_PATH)
        match = re.search(r"^Last updated: (\d{4}-\d{2}-\d{2})$", text, re.MULTILINE)
        assert match, "FAIL AC-5: enforcement-baseline.md lost its Last-updated line"
        assert match.group(1) >= "2026-07-05", (
            f"FAIL AC-5: 'Last updated: {match.group(1)}' predates the m6b "
            "edit — REQ-6 refreshes the stale claim"
        )

    def test_manual_legacy_process_claim_is_gone(self) -> None:
        text = _source_text(BASELINE_PATH)
        assert "manual legacy process" not in text, (
            "FAIL AC-5: the closing note still claims this repository uses "
            "the manual legacy process (REQ-6 — false once the drive runs "
            "the native surface)"
        )
