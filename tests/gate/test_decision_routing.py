"""AC-3: standalone policy tier, exact capture and reusable review identity."""

import pytest

from heddle.gate.preparation import _canonical_logical_instructions
from heddle.gate.prompt import compute_prompt_version, resolve_gate_prompt
from heddle.gate.registry import GATES
from heddle.gate.types import GateContext
from heddle.kernel.project_config import KernelError


def context(tmp_path):
    (tmp_path / ".heddle.yaml").write_text("{}")
    return GateContext(GATES["spec-review"], "example", tmp_path, None, None, None)


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_policy_capture_changes_all_instruction_identities(tmp_path, cli):
    c = context(tmp_path)
    p = tmp_path / ".heddle/overrides/decision-routing.md"
    p.parent.mkdir(parents=True)
    p.write_text("Unique policy\n")
    first = resolve_gate_prompt(c, c.gate_type, cli=cli)
    assert first.logical_instructions.count("Unique policy") == 1
    assert first.decision_policy.source == "override"
    assert first.template.source == "packaged"
    assert first.prompt_version == compute_prompt_version(c.gate_type, tmp_path)
    stable = _canonical_logical_instructions(c, first, cli)
    p.write_text("Unique policy\n ")
    second = resolve_gate_prompt(c, c.gate_type, cli=cli)
    assert first.prompt_version != second.prompt_version
    assert first.effective_prompt_sha256 != second.effective_prompt_sha256
    assert stable != _canonical_logical_instructions(c, second, cli)
    assert stable == _canonical_logical_instructions(c, first, cli)
    assert first.transport.stdin == (
        first.logical_instructions if cli == "claude" else first.effective_instructions
    )


def test_template_override_does_not_move_common_policy_tree(tmp_path):
    c = context(tmp_path)
    p = tmp_path / ".heddle/overrides/spec-review.prompt.md"
    p.parent.mkdir(parents=True)
    p.write_text("Selected role [partial-local]")
    partial = p.parent / "_partials/local.md"
    partial.parent.mkdir()
    partial.write_text("Local partial [partial-not-expanded]")
    prompt = resolve_gate_prompt(c, c.gate_type)
    assert prompt.template.source == "override"
    assert prompt.decision_policy.source == "packaged"
    assert "# Decision routing" in prompt.logical_instructions
    assert (
        "Selected role Local partial [partial-not-expanded]"
        in prompt.logical_instructions
    )


def test_invalid_present_config_is_not_packaged_fallback(tmp_path):
    c = context(tmp_path)
    (tmp_path / ".heddle.yaml").write_text("layout: [invalid]")
    with pytest.raises(KernelError):
        resolve_gate_prompt(c, c.gate_type)


def test_configless_fixture_uses_explicit_default_policy(tmp_path):
    c = GateContext(GATES["spec-review"], "example", tmp_path, None, None, None)
    assert resolve_gate_prompt(c, c.gate_type).decision_policy.source == "packaged"
