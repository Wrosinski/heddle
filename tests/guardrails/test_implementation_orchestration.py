"""Provider adapter contracts for bounded implementation delegation."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
IMPLEMENT_BRIEFING = "heddle/resources/implement.briefing.md"
WORK_PACKAGE_BRIEF = "heddle/resources/work-package-brief.scaffold.md"


def markdown_frontmatter(relative: str) -> tuple[dict, str]:
    raw = (REPO / relative).read_text()
    assert raw.startswith("---\n")
    header, body = raw[4:].split("\n---\n", maxsplit=1)
    return yaml.safe_load(header), body


def test_provider_skills_are_explicit_and_share_the_canonical_contract():
    claude_metadata, claude_body = markdown_frontmatter(
        ".claude/skills/implement-with-opus/SKILL.md"
    )
    codex_metadata, codex_body = markdown_frontmatter(
        ".agents/skills/implement-with-sol/SKILL.md"
    )
    codex_ui = yaml.safe_load(
        (REPO / ".agents/skills/implement-with-sol/agents/openai.yaml").read_text()
    )

    assert claude_metadata["name"] == "implement-with-opus"
    assert claude_metadata["disable-model-invocation"] is True
    assert claude_metadata["effort"] == "xhigh"
    assert codex_metadata["name"] == "implement-with-sol"
    assert codex_ui["policy"]["allow_implicit_invocation"] is False
    assert "$implement-with-sol" in codex_ui["interface"]["default_prompt"]

    for body in (claude_body, codex_body):
        assert IMPLEMENT_BRIEFING in body
        assert WORK_PACKAGE_BRIEF in body
        assert "supervise multiple" in body
        assert "write ownership does\nnot overlap" in body


def test_provider_workers_pin_models_and_cannot_delegate_further():
    claude_metadata, claude_body = markdown_frontmatter(
        ".claude/agents/opus-implementer.md"
    )
    sol = tomllib.loads((REPO / ".codex/agents/sol-implementer.toml").read_text())

    assert claude_metadata["name"] == "opus-implementer"
    assert claude_metadata["model"] == "claude-opus-5-5"
    assert claude_metadata["effort"] == "xhigh"
    assert "Agent" not in {tool.strip() for tool in claude_metadata["tools"].split(",")}
    assert "do not\ndelegate it to another agent" in claude_body

    assert sol["name"] == "sol-implementer"
    assert sol["model"] == "gpt-5.6-sol"
    assert sol["model_reasoning_effort"] == "xhigh"
    assert sol["agents"]["enabled"] is False
    assert "do not\ndelegate it to other agents" in sol["developer_instructions"]


def test_implement_briefing_owns_the_shared_assignment_boundary():
    briefing = (REPO / IMPLEMENT_BRIEFING).read_text()
    scaffold = (REPO / WORK_PACKAGE_BRIEF).read_text()

    assert WORK_PACKAGE_BRIEF in briefing
    assert "supervise parallel implementation packages" in briefing
    assert "One owner may write a\ngiven path at a time." in briefing
    assert "Provider-specific skills and agent definitions" in " ".join(
        briefing.split()
    )
    assert "criterion-by-criterion evidence table" in scaffold
    assert "worktree" in scaffold
