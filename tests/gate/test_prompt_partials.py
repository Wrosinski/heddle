"""
Contract for the prompt-partial include mechanism and the prompt-version
instrument.

A rule that binds many gates has to live in exactly one place *and* reach the
model in full. Those two goals pull against each other: hoisting the rule into
a shared reference doc and citing it by anchor satisfies the first and quietly
defeats the second, because a CLI-invoked gate agent does not reliably read a
reference file mid-run (prompt-authoring-standards.md, packaged).
`expand_partials` resolves the tension by splicing the shared text inline at
build time — one source on disk, full text in context.

`compute_prompt_version` is the measurement half: every gate summary carries a
16-char hash of the wording the gate actually ran under, so trajectory metrics
bucket by prompt version and a wording edit becomes measurable (R6).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from heddle.gate.prompt import (
    PACKAGED_PROMPTS_DIR,
    PARTIALS_DIRNAME,
    compute_prompt_version,
    expand_partials,
    packaged_prompt_path,
    partials_dir_for,
)
from heddle.gate.registry import GATES
from heddle.gate.types import (
    DiffResult,
    GateContext,
    GateType,
)
from heddle.kernel.project_config import KernelError

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_PARTIALS_DIR = PACKAGED_PROMPTS_DIR / PARTIALS_DIRNAME

# Iteration-scoped partials carry rerun/disposition context that only the
# self-consuming synthesis gates may splice: they are the gates that resolve
# their own prior output. Any other gate splicing one would ship rerun
# instructions to a reviewer that has no prior of its own.
ITERATION_SCOPED_PARTIAL_PREFIX = "iteration-"
ITERATION_CONTEXT_ALLOWLIST = frozenset(
    {
        "spec-review-synthesis",
        "plan-review-synthesis",
        "review-test-scaffolding-synthesis",
        "review-synthesis",
    }
)


def _template_gate(tmp_path: Path, template_body: str) -> GateType:
    template_path = tmp_path / "prompts" / "partials-fixture.md"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(template_body)
    return GateType(
        name="spec-review",
        requires_plan=False,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=("claude", "codex"),
        tool_permissions=(),
        output_contract=None,
        prompt_template="prompts/partials-fixture.md",
    )


def _ctx(gate_type: GateType, tmp_path: Path) -> GateContext:
    return GateContext(
        gate_type=gate_type,
        feature="sample-feature",
        repo_root=tmp_path.resolve(),
        plan_path=None,
        spec_path=None,
        milestone=None,
        tier="3",
    )


def _empty_diff(tmp_path: Path) -> DiffResult:
    diff_path = tmp_path / "empty.diff"
    diff_path.write_text("")
    return DiffResult(
        source="explicit",
        source_detail="",
        diff_path=diff_path,
        changed_files=[],
    )


class TestExpandPartials:
    def test_partial_is_spliced_inline(self, tmp_path: Path) -> None:
        partials = tmp_path / PARTIALS_DIRNAME
        partials.mkdir()
        (partials / "severity-taxonomy.md").write_text("Critical blocks merge.\n")

        rendered = expand_partials(
            "Before\n\n[partial-severity-taxonomy]\n\nAfter", partials
        )

        assert "Critical blocks merge." in rendered
        assert "[partial-severity-taxonomy]" not in rendered
        assert rendered.index("Before") < rendered.index("Critical blocks merge.")
        assert rendered.index("Critical blocks merge.") < rendered.index("After")

    def test_missing_partial_fails_loud(self, tmp_path: Path) -> None:
        """A gate that silently shipped without its severity taxonomy would
        still produce confident-looking output; the loss would surface only
        as degraded findings later. This is also the split-brain rail: a host
        overriding a template without its _partials/ fails at render time."""
        partials = tmp_path / PARTIALS_DIRNAME
        partials.mkdir()

        with pytest.raises(KernelError, match="partial-nope"):
            expand_partials("[partial-nope]", partials)

    def test_partial_may_carry_template_variables(self, tmp_path: Path) -> None:
        """Partials expand before variable substitution, so a shared block
        can reference `[plan-path]` and friends."""
        partials = tmp_path / PARTIALS_DIRNAME
        partials.mkdir()
        (partials / "plan-discovery.md").write_text("Use the plan at `[plan-path]`.")

        rendered = expand_partials("[partial-plan-discovery]", partials)

        assert "[plan-path]" in rendered

    def test_repeated_token_expands_at_every_site(self, tmp_path: Path) -> None:
        partials = tmp_path / PARTIALS_DIRNAME
        partials.mkdir()
        (partials / "rule.md").write_text("THE RULE")

        rendered = expand_partials("[partial-rule]\n\nmid\n\n[partial-rule]", partials)

        assert rendered.count("THE RULE") == 2

    def test_nested_partial_tokens_are_not_recursively_expanded(
        self, tmp_path: Path
    ) -> None:
        """
        The flat-only contract (review-core I-1, matching the pinned
                source): expansion is a single pass, so a partial referencing
                another partial ships the inner token literally — where the corpus
                render sweep flags it as a stray. Partials are leaf rule blocks; a
                deliberate move to nesting must change this pin, not discover it.
        """
        partials = tmp_path / PARTIALS_DIRNAME
        partials.mkdir()
        (partials / "outer.md").write_text("outer says: [partial-inner]")
        (partials / "inner.md").write_text("INNER")

        rendered = expand_partials("[partial-outer]", partials)

        assert rendered == "outer says: [partial-inner]"
        assert "INNER" not in rendered

    def test_partials_resolve_beside_the_resolved_template(self) -> None:
        """The same-root invariant: a template and its partials are one unit,
        whichever tree the template resolved from (repo prompts/, a host-tier
        override dir, or a future packaged floor)."""
        assert (
            partials_dir_for(Path("prompts/spec-review.md"))
            == Path("prompts") / PARTIALS_DIRNAME
        )
        assert partials_dir_for(
            Path("/host/.heddle/prompts/spec-review.prompt.md")
        ) == Path("/host/.heddle/prompts/_partials")


class TestComputePromptVersion:
    def _seed_repo(self, tmp_path: Path) -> GateType:
        gate_type = _template_gate(tmp_path, "Task text.\n\n[partial-anchor]\n")
        partials = tmp_path / "prompts" / PARTIALS_DIRNAME
        partials.mkdir(parents=True, exist_ok=True)
        (partials / "anchor.md").write_text("the spliced rule\n")
        (partials / "unreferenced.md").write_text("not spliced here\n")
        return gate_type

    def test_version_changes_when_a_spliced_partial_changes(
        self, tmp_path: Path
    ) -> None:
        gate_type = self._seed_repo(tmp_path)
        before = compute_prompt_version(gate_type, tmp_path)

        partial = tmp_path / "prompts" / PARTIALS_DIRNAME / "anchor.md"
        partial.write_text("the spliced rule, reworded\n")

        assert compute_prompt_version(gate_type, tmp_path) != before

    def test_version_stable_when_unspliced_content_changes(
        self, tmp_path: Path
    ) -> None:
        """Guards false attribution: an edit the gate never saw must not
        move its version bucket."""
        gate_type = self._seed_repo(tmp_path)
        before = compute_prompt_version(gate_type, tmp_path)

        unreferenced = tmp_path / "prompts" / PARTIALS_DIRNAME / "unreferenced.md"
        unreferenced.write_text("still not spliced — reflowed\n\n\n")
        (tmp_path / "unrelated.py").write_text("x = 1\n")

        assert compute_prompt_version(gate_type, tmp_path) == before

    def test_version_covers_the_packaged_standards_doc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A change to the standards doc changes what every gate is asked to
        do, so it belongs in every gate's version."""
        import heddle.gate.prompt as prompt_module

        gate_type = self._seed_repo(tmp_path)
        standards = tmp_path / "packaged-standards.md"
        standards.write_text("shared contract v1\n")
        monkeypatch.setattr(prompt_module, "PACKAGED_STANDARDS_DOC", standards)
        before = compute_prompt_version(gate_type, tmp_path)

        standards.write_text("shared contract v2\n")

        assert compute_prompt_version(gate_type, tmp_path) != before

    def test_missing_packaged_standards_declines_to_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The standards doc ships with the package, so absence means a
        broken installation; the stamp is declined (visible in the summary)
        rather than computed over a silently different input set."""
        import heddle.gate.prompt as prompt_module

        gate_type = self._seed_repo(tmp_path)
        monkeypatch.setattr(
            prompt_module, "PACKAGED_STANDARDS_DOC", tmp_path / "absent.md"
        )
        assert compute_prompt_version(gate_type, tmp_path) is None

    def test_unreadable_input_declines_to_stamp_instead_of_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """compute_prompt_version runs inside the failure-summary write, where
        a raise would mask the real error — a non-ENOENT OSError (permissions,
        a TOCTOU race after is_file) must decline the stamp, not crash."""
        import heddle.gate.prompt as prompt_module

        class _UnreadableDoc:
            def is_file(self) -> bool:
                return True

            def read_bytes(self) -> bytes:
                raise PermissionError("standards doc unreadable")

        gate_type = self._seed_repo(tmp_path)
        monkeypatch.setattr(prompt_module, "PACKAGED_STANDARDS_DOC", _UnreadableDoc())
        assert compute_prompt_version(gate_type, tmp_path) is None

    def test_unresolvable_template_yields_none(self, tmp_path: Path) -> None:
        """A missing prompt is already a loud failure at build time; the
        summary write must not become a second failure site."""
        gate_type = GateType(
            name="spec-review",
            requires_plan=False,
            requires_milestone=False,
            requires_diff=False,
            supported_clis=("claude",),
            tool_permissions=(),
            output_contract=None,
            prompt_template="prompts/no-such-template-anywhere.md",
        )
        assert compute_prompt_version(gate_type, tmp_path) is None


def test_captured_partial_expansion_is_single_pass() -> None:
    from heddle.gate.prompt import expand_captured_partials

    assert (
        expand_captured_partials(
            "[partial-outer]", {"outer": "[partial-inner]", "inner": "expanded"}
        )
        == "[partial-inner]"
    )


class TestConventionCheckerExpansion:
    """`scripts/check-prompt-conventions.py` must check the prompt as the
    model receives it: a required marker living inside a partial is found
    after expansion (lockstep with the partials engine — without this, the
    pre-commit gate red-flags every prompt that extracts a convention into a
    shared block)."""

    CHECKER = REPO_ROOT / "scripts" / "check-prompt-conventions.py"

    def _run(
        self, cwd: Path, prompts_dir: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        # Fixture trees pass their corpus explicitly; the live-corpus case
        # passes none so the checker resolves `layout.prompts` from config.
        args = [sys.executable, str(self.CHECKER)]
        if prompts_dir is not None:
            args.append(str(prompts_dir))
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def _seed(self, tmp_path: Path, prompt_body: str) -> None:
        prompts = tmp_path / "prompts"
        (prompts / PARTIALS_DIRNAME).mkdir(parents=True)
        (prompts / "conventions.yaml").write_text(
            "conventions:\n"
            "  VerifiedBy:\n"
            "    keywords:\n"
            "      - Verified-by\n"
            "prompts:\n"
            "  fixture.md:\n"
            "    - VerifiedBy\n"
        )
        (prompts / "fixture.md").write_text(prompt_body)

    @pytest.mark.toolchain
    def test_marker_inside_a_partial_is_found_after_expansion(
        self, tmp_path: Path
    ) -> None:
        self._seed(tmp_path, "# Fixture\n\n[partial-verified]\n")
        (tmp_path / "prompts" / PARTIALS_DIRNAME / "verified.md").write_text(
            "Cite `Verified-by` for every AC.\n"
        )

        result = self._run(tmp_path, tmp_path / "prompts")

        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.toolchain
    def test_missing_partial_is_a_checker_violation_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        self._seed(tmp_path, "# Fixture\n\n[partial-verified]\n")

        result = self._run(tmp_path, tmp_path / "prompts")

        assert result.returncode == 1
        assert "partial-verified" in result.stdout

    @pytest.mark.toolchain
    def test_flat_marker_still_passes(self, tmp_path: Path) -> None:
        self._seed(tmp_path, "# Fixture\n\nVerified-by: tests.\n")

        result = self._run(tmp_path, tmp_path / "prompts")

        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.toolchain
    def test_live_corpus_passes_the_expanded_check(self) -> None:
        """Regression: the real prompts/ tree satisfies conventions.yaml
        through the expanded path (flat markers keep passing)."""
        result = self._run(REPO_ROOT)

        assert result.returncode == 0, result.stdout + result.stderr


class TestPartialCorpusGuards:
    def test_every_referenced_partial_exists(self) -> None:
        """Every `[partial-*]` token in every registry prompt resolves. This
        turns a typo into a red test instead of a gate that runs without one
        of its rules."""
        for gate in sorted(GATES):
            template = packaged_prompt_path(GATES[gate].prompt_template).read_text(
                encoding="utf-8"
            )
            expand_partials(template, REPO_PARTIALS_DIR)  # raises on any miss

    def test_iteration_scoped_partials_only_in_allowlisted_gates(self) -> None:
        """Iteration/rerun context is synthesis-gate ground: the disposition
        machinery (decision deferral-D ledger, Iteration Disposition sections) belongs
        to
        the gates that consume their own prior output. A reviewer gate
        splicing an `iteration-*` partial would be told to re-review against
        a prior it does not have."""
        from heddle.gate.prompt import _PARTIAL_TOKEN

        violations: list[str] = []
        for gate in sorted(GATES):
            template = packaged_prompt_path(GATES[gate].prompt_template).read_text(
                encoding="utf-8"
            )
            for name in set(_PARTIAL_TOKEN.findall(template)):
                if (
                    name.startswith(ITERATION_SCOPED_PARTIAL_PREFIX)
                    and gate not in ITERATION_CONTEXT_ALLOWLIST
                ):
                    violations.append(f"{gate}: [partial-{name}]")
        assert not violations, (
            "iteration-scoped partials spliced outside the synthesis "
            f"allowlist: {violations}"
        )
