"""Repository skill layout, inventory, and symlink-safety checks."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "check-skill-sync.py"
    shutil.copyfile(REPO / "scripts/check-skill-sync.py", script)
    agents = tmp_path / ".agents/skills"
    claude = tmp_path / ".claude/skills"
    shutil.copytree(REPO / ".agents/skills", agents, symlinks=True)
    shutil.copytree(REPO / ".claude/skills", claude, symlinks=True)
    return script, agents, claude


def run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=script.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )


def test_shared_skills_link_to_canonical_agents_directory(tmp_path: Path) -> None:
    script, agents, claude = fixture(tmp_path)
    assert run(script).returncode == 0

    target = claude / "root-cause-analysis"
    target.unlink()
    assert run(script).returncode != 0

    shutil.copytree(agents / "root-cause-analysis", target)
    copied = run(script)
    assert copied.returncode != 0
    assert "expected symlink" in copied.stdout


def test_unclassified_claude_skill_is_refused_while_local_agents_skill_passes(
    tmp_path: Path,
) -> None:
    script, agents, claude = fixture(tmp_path)
    local_agent_skill = agents / "local-only/SKILL.md"
    local_agent_skill.parent.mkdir()
    local_agent_skill.write_text("local checkout skill\n", encoding="utf-8")
    assert run(script).returncode == 0

    extra = claude / "operator-owned/SKILL.md"
    extra.parent.mkdir()
    extra.write_text("operator owned\n", encoding="utf-8")
    result = run(script)
    assert result.returncode != 0
    assert "unclassified" in result.stdout


def test_missing_claude_only_skill_is_refused(tmp_path: Path) -> None:
    script, _agents, claude = fixture(tmp_path)
    (claude / "implement-with-opus/SKILL.md").unlink()
    result = run(script)
    assert result.returncode != 0
    assert "missing" in result.stdout


@pytest.mark.parametrize(
    "case",
    [
        "escaping-shared-link",
        "wrong-shared-target",
        "symlinked-agents-file",
        "symlinked-agents-dir",
        "symlinked-agents-root",
        "symlinked-claude-root",
        "symlinked-claude-only",
    ],
)
def test_unsafe_or_escaping_symlinks_are_refused(tmp_path: Path, case: str) -> None:
    script, agents, claude = fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "SKILL.md"
    external.write_text("outside\n", encoding="utf-8")

    if case == "escaping-shared-link":
        target = claude / "root-cause-analysis"
        target.unlink()
        target.symlink_to(outside, target_is_directory=True)
    elif case == "wrong-shared-target":
        target = claude / "root-cause-analysis"
        target.unlink()
        target.symlink_to("../../.agents/skills/new-feature", target_is_directory=True)
    elif case == "symlinked-agents-file":
        skill_file = agents / "root-cause-analysis/SKILL.md"
        skill_file.unlink()
        skill_file.symlink_to(external)
    elif case == "symlinked-agents-dir":
        skill_dir = agents / "root-cause-analysis"
        shutil.rmtree(skill_dir)
        skill_dir.symlink_to(outside, target_is_directory=True)
    elif case == "symlinked-agents-root":
        external_tree = outside / "agents-skills"
        shutil.copytree(agents, external_tree, symlinks=True)
        shutil.rmtree(agents)
        agents.symlink_to(external_tree, target_is_directory=True)
    elif case == "symlinked-claude-root":
        external_tree = outside / "claude-skills"
        shutil.copytree(claude, external_tree, symlinks=True)
        shutil.rmtree(claude)
        claude.symlink_to(external_tree, target_is_directory=True)
    else:
        claude_only = claude / "implement-with-opus"
        shutil.rmtree(claude_only)
        claude_only.symlink_to(outside, target_is_directory=True)

    result = run(script)
    assert result.returncode != 0, f"unsafe symlink case was accepted: {case}"
    assert "unsafe symlink" in result.stdout


def test_legacy_codex_skills_directory_is_refused(tmp_path: Path) -> None:
    script, _agents, _claude = fixture(tmp_path)
    legacy = tmp_path / ".codex/skills/root-cause-analysis/SKILL.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy mirror\n", encoding="utf-8")
    result = run(script)
    assert result.returncode != 0
    assert "legacy .codex/skills" in result.stdout
