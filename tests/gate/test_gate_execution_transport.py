"""Prompt identity and provider transport contracts for gate execution."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from heddle.gate import prompt as prompt_module
from heddle.gate.preparation import prepare_gate_run
from heddle.gate.prompt import output_constraint, resolve_gate_prompt
from heddle.gate.types import (
    DiffResult,
    GateContext,
    GateExecutionConfig,
    GateType,
    ResolvedGateInvocation,
)
from heddle.kernel.project_config import KernelError


def _gate_and_context(tmp_path: Path, body: str) -> tuple[GateType, GateContext]:
    template = tmp_path / "prompts/policy-fixture.md"
    template.parent.mkdir(parents=True)
    template.write_text(body, encoding="utf-8")
    gate = GateType(
        name="spec-review",
        requires_plan=False,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=("claude", "codex"),
        tool_permissions=(),
        output_contract=None,
        prompt_template="prompts/policy-fixture.md",
    )
    return gate, GateContext(
        gate_type=gate,
        feature="fixture",
        repo_root=tmp_path,
        plan_path=None,
        spec_path=None,
        milestone=None,
    )


def _tier_gate_and_context(
    tmp_path: Path,
    resource_tier: str,
    body: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[GateType, GateContext, Path]:
    host = tmp_path / "host"
    package = tmp_path / "package"
    packaged_template = package / "prompts/policy-fixture.md"
    packaged_template.parent.mkdir(parents=True)
    packaged_template.write_text(body, encoding="utf-8")
    standard = package / "prompt-authoring-standards.md"
    standard.write_text("# Stable fixture standard\n", encoding="utf-8")
    monkeypatch.setattr(prompt_module, "_PACKAGED_RESOURCES_ROOT", package)
    monkeypatch.setattr(prompt_module, "PACKAGED_PROMPTS_DIR", package / "prompts")
    monkeypatch.setattr(prompt_module, "PACKAGED_STANDARDS_DOC", standard)

    if resource_tier == "generated":
        template = host / "prompts/policy-fixture.md"
        template.parent.mkdir(parents=True)
        template.write_text(body, encoding="utf-8")
    elif resource_tier == "override":
        (host / ".heddle.yaml").parent.mkdir(parents=True, exist_ok=True)
        (host / ".heddle.yaml").write_text(
            "layout:\n  prompts: .heddle/prompts\n", encoding="utf-8"
        )
        template = host / ".heddle/overrides/policy-fixture.prompt.md"
        template.parent.mkdir(parents=True)
        template.write_text(body, encoding="utf-8")
    else:
        template = packaged_template

    gate = GateType(
        name="policy-fixture",
        requires_plan=False,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=("claude", "codex"),
        tool_permissions=(),
        output_contract=None,
        prompt_template="prompts/policy-fixture.md",
    )
    context = GateContext(
        gate_type=gate,
        feature="fixture",
        repo_root=host,
        plan_path=None,
        spec_path=None,
        milestone=None,
    )
    return gate, context, template


def _prepare_identity(gate: GateType, context: GateContext, cli: str = "claude"):
    diff_path = context.repo_root / "prepared.diff"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text("", encoding="utf-8")
    config = GateExecutionConfig(
        cli=cli,
        model="fixture-model",
        reasoning_effort="xhigh",
        sandbox="auto" if cli == "claude" else "danger-full-access",
    )
    return prepare_gate_run(
        context,
        gate_type=gate,
        invocation=ResolvedGateInvocation(config, frozenset()),
        prompt=resolve_gate_prompt(context, gate, cli=cli),
        diff=DiffResult(
            source="fixture",
            source_detail="fixture",
            diff_path=diff_path,
            changed_files=(),
        ),
    )


@pytest.mark.parametrize("resource_tier", ["packaged", "generated", "override"])
def test_ac8_comment_removal_precedes_partial_discovery(
    tmp_path: Path, resource_tier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, context, _template = _tier_gate_and_context(
        tmp_path,
        resource_tier,
        "Visible instructions.\n<!-- [partial-do-not-load] -->\n",
        monkeypatch,
    )
    resolved = resolve_gate_prompt(context, gate, cli="claude")
    assert resolved.template.source == resource_tier
    assert not resolved.partials
    assert "partial-do-not-load" not in resolved.effective_instructions
    assert "<!--" not in resolved.effective_instructions


@pytest.mark.parametrize(
    "body",
    [
        "Visible --> stray\n",
        "Visible <!-- never closed\n",
        "<!-- outer <!-- nested -->\n",
    ],
)
@pytest.mark.parametrize("resource_tier", ["packaged", "generated", "override"])
def test_ac8_malformed_author_comments_fail_with_resource_name(
    tmp_path: Path,
    body: str,
    resource_tier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, context, _template = _tier_gate_and_context(
        tmp_path, resource_tier, body, monkeypatch
    )
    with pytest.raises(KernelError, match="policy-fixture.md") as caught:
        resolve_gate_prompt(context, gate, cli="claude")
    assert caught.value.code == "workspace-invalid"


@pytest.mark.parametrize("resource_tier", ["packaged", "generated", "override"])
def test_ac8_duplicate_partial_is_captured_once_and_expanded_twice(
    tmp_path: Path,
    resource_tier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, context, template = _tier_gate_and_context(
        tmp_path,
        resource_tier,
        "[partial-rule]\nBetween\n[partial-rule]\n",
        monkeypatch,
    )
    partials = template.parent / "_partials"
    partials.mkdir()
    (partials / "rule.md").write_text("ONE RULE\n", encoding="utf-8")
    resolved = resolve_gate_prompt(context, gate, cli="claude")
    assert len(resolved.partials) == 1
    assert resolved.effective_instructions.count("ONE RULE") == 2


@pytest.mark.parametrize("resource_tier", ["packaged", "generated", "override"])
def test_ac8_comment_only_edit_rotates_authoring_but_not_effective_identity(
    tmp_path: Path,
    resource_tier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, context, template = _tier_gate_and_context(
        tmp_path, resource_tier, "Visible\n<!-- first -->\n", monkeypatch
    )
    first = resolve_gate_prompt(context, gate, cli="claude")
    first_prepared = _prepare_identity(gate, context)
    assert first.template.source == resource_tier
    template.write_text("Visible\n<!-- second -->\n", encoding="utf-8")
    second = resolve_gate_prompt(context, gate, cli="claude")
    second_prepared = _prepare_identity(gate, context)
    assert first.prompt_version != second.prompt_version
    assert first.effective_prompt_sha256 == second.effective_prompt_sha256
    assert first_prepared.review_basis_hash == second_prepared.review_basis_hash
    assert first_prepared.input_hash == second_prepared.input_hash
    assert (
        second.effective_prompt_sha256
        == hashlib.sha256(second.effective_instructions.encode("utf-8")).hexdigest()
    )


@pytest.mark.parametrize(
    ("before", "after", "version_changes", "effective_changes"),
    [
        ("Visible wording.\n", "Changed wording.\n", True, True),
        ("Visible wording.\n", "\nVisible wording.\n\n", True, False),
        (
            "Visible wording.\n## Validation\nold\n",
            "Visible wording.\n## Validation\nnew\n",
            True,
            False,
        ),
        ("Visible café.\n", "Visible cafe\u0301.\n", True, True),
    ],
)
@pytest.mark.parametrize("resource_tier", ["packaged", "generated", "override"])
def test_ac8_template_mutation_truth_table(
    tmp_path: Path,
    before: str,
    after: str,
    version_changes: bool,
    effective_changes: bool,
    resource_tier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, context, template = _tier_gate_and_context(
        tmp_path, resource_tier, before, monkeypatch
    )
    first = resolve_gate_prompt(context, gate, cli="claude")
    first_prepared = _prepare_identity(gate, context)
    assert first.template.source == resource_tier
    template.write_text(after, encoding="utf-8")
    second = resolve_gate_prompt(context, gate, cli="claude")
    second_prepared = _prepare_identity(gate, context)
    assert (first.prompt_version != second.prompt_version) is version_changes
    assert (
        first.effective_prompt_sha256 != second.effective_prompt_sha256
    ) is effective_changes
    assert (
        first_prepared.review_basis_hash != second_prepared.review_basis_hash
    ) is effective_changes
    assert (
        first_prepared.input_hash != second_prepared.input_hash
    ) is effective_changes


@pytest.mark.parametrize("resource_tier", ["packaged", "generated", "override"])
def test_ac8_packaged_standard_edit_rotates_only_authoring_identity(
    tmp_path: Path,
    resource_tier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, context, _template = _tier_gate_and_context(
        tmp_path, resource_tier, "Visible instructions.\n", monkeypatch
    )
    first = resolve_gate_prompt(context, gate, cli="claude")
    first_prepared = _prepare_identity(gate, context)
    prompt_module.PACKAGED_STANDARDS_DOC.write_text(
        "# Changed fixture standard\n", encoding="utf-8"
    )
    second = resolve_gate_prompt(context, gate, cli="claude")
    second_prepared = _prepare_identity(gate, context)

    assert first.prompt_version != second.prompt_version
    assert first.effective_prompt_sha256 == second.effective_prompt_sha256
    assert first_prepared.review_basis_hash == second_prepared.review_basis_hash
    assert first_prepared.input_hash == second_prepared.input_hash


@pytest.mark.parametrize("resource_tier", ["packaged", "generated", "override"])
def test_ac8_partial_order_and_outer_whitespace_truth_table(
    tmp_path: Path, resource_tier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, context, template = _tier_gate_and_context(
        tmp_path,
        resource_tier,
        "[partial-first]\n[partial-second]\n",
        monkeypatch,
    )
    partials = template.parent / "_partials"
    partials.mkdir()
    (partials / "first.md").write_text("FIRST", encoding="utf-8")
    (partials / "second.md").write_text("SECOND", encoding="utf-8")
    baseline = resolve_gate_prompt(context, gate, cli="claude")

    (partials / "first.md").write_text("\n FIRST \n", encoding="utf-8")
    whitespace = resolve_gate_prompt(context, gate, cli="claude")
    assert whitespace.prompt_version == baseline.prompt_version
    assert whitespace.effective_prompt_sha256 == baseline.effective_prompt_sha256

    template.write_text("[partial-second]\n[partial-first]\n", encoding="utf-8")
    reordered = resolve_gate_prompt(context, gate, cli="claude")
    assert reordered.prompt_version != baseline.prompt_version
    assert reordered.effective_prompt_sha256 != baseline.effective_prompt_sha256


@pytest.mark.parametrize("resource_tier", ["packaged", "generated", "override"])
def test_ac8_missing_live_partial_fails_before_execution(
    tmp_path: Path, resource_tier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, context, _template = _tier_gate_and_context(
        tmp_path, resource_tier, "[partial-missing-live]\n", monkeypatch
    )
    with pytest.raises(KernelError) as caught:
        resolve_gate_prompt(context, gate, cli="claude")
    assert caught.value.code == "workspace-invalid"
    assert "missing-live" in caught.value.message


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_ac9_transport_reconstructs_one_logical_instruction_set(
    tmp_path: Path, cli: str
) -> None:
    gate, context = _gate_and_context(tmp_path, "Visible instructions.\n")
    resolved = resolve_gate_prompt(context, gate, cli=cli)
    transport = resolved.transport
    constraint = output_constraint(cli)
    reconstructed = "\n\n".join(
        part for part in (transport.system, transport.stdin) if part
    )
    assert reconstructed.count(constraint) == 1
    assert reconstructed == resolved.effective_instructions
    if cli == "claude":
        assert transport.system == constraint and constraint not in transport.stdin
    else:
        assert transport.system is None and transport.stdin.startswith(constraint)


def test_ac9_prepared_transport_is_derived_from_resolved_cli(tmp_path: Path) -> None:
    gate, context = _gate_and_context(tmp_path, "Visible instructions.\n")
    prepared = _prepare_identity(gate, context, cli="claude")
    codex_prepared = _prepare_identity(gate, context, cli="codex")

    assert prepared.transport.system == output_constraint("claude")
    assert output_constraint("claude") not in prepared.transport.stdin
    assert codex_prepared.transport.system is None
    assert codex_prepared.transport.stdin.startswith(output_constraint("codex"))
    assert codex_prepared.transport.stdin.count(output_constraint("codex")) == 1


def test_ac9_survivor_output_constraints_are_provider_specific() -> None:
    """Survivor pin: the two existing constraint wordings remain distinct."""
    claude = output_constraint("claude")
    codex = output_constraint("codex")
    assert claude and codex and claude != codex
    assert "structured review JSON object" in claude
    assert "final assistant message" in codex


# antigravity-reviewer-lane-v1, milestone m2: the lane's CLI domain, its
# per-CLI default (AC-4) and model validation for new launches (AC-5). Launch
# resolution is `heddle.gate.cli.resolve_gate_execution`; a policy slot's
# frozen reviewer reaches it as overrides too.
ANTIGRAVITY = "antigravity"
ACCEPTED_ANTIGRAVITY_MODELS = ("gemini-4-neon", "gemini-4-hydrogen")
REFUSED_ANTIGRAVITY_MODELS = (
    "gemini-4-neon-high",
    "gemini-3.8-flash",
    "gemini-4-flash",
    "gemini-4-lite",
    "gemini-pro-orchestrator",
    "claude-opus-5-5",
)
# The base name standing alone, not as the prefix of a suffixed name.
NEON_BASE_NAME = re.compile(r"gemini-4-neon(?![\w.-])")


def _resolve(gate_name: str = "spec-review", **overrides: str):
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.types import GateInvocationOverrides

    return resolve_gate_execution(
        GATES[gate_name], GateInvocationOverrides(**overrides)
    )


def _refusal(**overrides: str) -> str:
    try:
        resolved = _resolve(**overrides)
    except KernelError as error:
        return f"{error.message} {error.hint}"
    pytest.fail(
        f"FAIL AC-5: launch with {overrides} must be refused, resolved "
        f"{resolved.exec_config}"
    )


def test_ac4_antigravity_joins_the_gate_cli_domain() -> None:
    """Red: `antigravity` is accepted wherever a gate CLI is: the value domain,
    the sandbox table, the shared `--cli` validation and every registered gate."""
    from heddle.contracts.gate_execution import (
        CLI_DOMAIN_ERROR,
        VALID_GATE_CLIS,
        VALID_SANDBOXES_BY_CLI,
    )
    from heddle.gate.registry import GATES
    from heddle.runtime.cli_args import validate_gate_overrides

    assert ANTIGRAVITY in VALID_GATE_CLIS, "FAIL AC-4: VALID_GATE_CLIS lacks the lane"
    assert VALID_SANDBOXES_BY_CLI.get(ANTIGRAVITY), (
        "FAIL AC-4: VALID_SANDBOXES_BY_CLI has no antigravity entry"
    )
    assert ANTIGRAVITY in CLI_DOMAIN_ERROR, (
        f"FAIL AC-4: the --cli domain error must name the lane: {CLI_DOMAIN_ERROR}"
    )
    failure = validate_gate_overrides(
        cli=ANTIGRAVITY, model=None, reasoning_effort=None
    )
    assert failure is None, (
        f"FAIL AC-4: --cli antigravity is refused: {failure and failure.error}"
    )
    unsupported = sorted(
        name for name, gate in GATES.items() if ANTIGRAVITY not in gate.supported_clis
    )
    assert not unsupported, f"FAIL AC-4: gates without the lane: {unsupported}"


@pytest.mark.parametrize(
    "gate_name",
    [
        "spec-review",
        "plan-review",
        "review-test-scaffolding",
        "milestone-review",
        "peer-review-sequential",
        "behavior-review",
        "complexity-review",
        "robustness-analysis",
    ],
)
def test_ac4_cli_antigravity_without_model_resolves_to_neon_high(gate_name) -> None:
    """Red: the per-CLI default table gives the lane gemini-4-neon at
    high wherever it applies today, with only --cli explicit."""
    try:
        resolved = _resolve(gate_name, cli=ANTIGRAVITY)
    except KernelError as error:
        pytest.fail(
            f"FAIL AC-4: --cli antigravity without a model is refused for "
            f"{gate_name}: {error.message}"
        )
    config = resolved.exec_config
    assert (config.cli, config.model, config.reasoning_effort) == (
        ANTIGRAVITY,
        "gemini-4-neon",
        "high",
    ), f"FAIL AC-4: --cli antigravity resolved to {config}"
    assert resolved.explicit_axes == frozenset({"cli"})


def test_ac4_unsupported_cli_hint_names_every_lane() -> None:
    """Red: the `--cli` usage hint that names only Claude and Codex gains the
    third lane."""
    text = _refusal(cli="gemini")
    for lane in ("claude", "codex", ANTIGRAVITY):
        assert lane in text, f"FAIL AC-4: the --cli hint must name {lane}: {text}"


@pytest.mark.parametrize("model", ACCEPTED_ANTIGRAVITY_MODELS)
def test_ac5_launch_accepts_gemini_4_base_names(model) -> None:
    """Red: a Gemini 4 base name launches on the lane."""
    try:
        resolved = _resolve(cli=ANTIGRAVITY, model=model)
    except KernelError as error:
        pytest.fail(f"FAIL AC-5: launch with {model} refused: {error.message}")
    assert (resolved.exec_config.cli, resolved.exec_config.model) == (
        ANTIGRAVITY,
        model,
    )


@pytest.mark.parametrize("model", REFUSED_ANTIGRAVITY_MODELS)
def test_ac5_launch_refuses_other_names_naming_the_model(model) -> None:
    """Red: effort-suffixed, older, Flash, Lite, orchestrator and other
    providers' names are refused at launch, naming the refused model. Today
    the lane itself is refused, without naming the model."""
    text = _refusal(cli=ANTIGRAVITY, model=model)
    assert model in text, f"FAIL AC-5: the refusal must name {model}: {text}"


def test_ac5_launch_effort_suffixed_name_hints_base_name_and_effort() -> None:
    """Red: the refusal points at the base name and --reasoning-effort."""
    text = _refusal(cli=ANTIGRAVITY, model="gemini-4-neon-high")
    assert NEON_BASE_NAME.search(text) and "--reasoning-effort" in text, (
        "FAIL AC-5: an effort-suffixed name needs a hint to pass gemini-4-neon "
        f"with --reasoning-effort: {text}"
    )


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_ac5_launch_accepts_the_domain_efforts(effort) -> None:
    """Red: each existing effort launches on the lane."""
    try:
        resolved = _resolve(
            cli=ANTIGRAVITY, model="gemini-4-neon", reasoning_effort=effort
        )
    except KernelError as error:
        pytest.fail(f"FAIL AC-5: effort {effort} refused: {error.message}")
    assert resolved.exec_config.reasoning_effort == effort


def test_ac5_launch_refuses_max_effort_on_the_lane() -> None:
    """Red: `max` is outside the effort domain; today the lane is refused
    before the effort is checked."""
    from heddle.contracts.gate_execution import REASONING_EFFORT_DOMAIN_ERROR

    text = _refusal(cli=ANTIGRAVITY, model="gemini-4-neon", reasoning_effort="max")
    assert REASONING_EFFORT_DOMAIN_ERROR in text, (
        f"FAIL AC-5: max must be refused as an effort: {text}"
    )


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_ac5_launch_claude_and_codex_refuse_gemini_models(cli) -> None:
    """Red: today a gemini- model launches on either existing lane."""
    text = _refusal(cli=cli, model="gemini-4-neon")
    assert "gemini-4-neon" in text, f"FAIL AC-5: refusal must name the model: {text}"


@pytest.mark.parametrize(
    ("cli", "model"),
    [
        ("claude", "claude-opus-5-5"),
        ("claude", "claude-fable-5-2"),
        ("codex", "gpt-6.1-sol"),
        ("codex", "gpt-6-astra"),
    ],
)
def test_ac5_survivor_existing_lane_models_still_launch(cli, model) -> None:
    """Survivor: the new family rules leave existing selections launchable."""
    resolved = _resolve(cli=cli, model=model)
    assert (resolved.exec_config.cli, resolved.exec_config.model) == (cli, model)
