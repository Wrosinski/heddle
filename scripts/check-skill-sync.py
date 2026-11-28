"""Check repository skill inventories and Claude compatibility symlinks."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_ROOT = REPO_ROOT / ".agents" / "skills"
CLAUDE_ROOT = REPO_ROOT / ".claude" / "skills"
LEGACY_CODEX_ROOT = REPO_ROOT / ".codex" / "skills"

SHARED_SKILLS = frozenset(
    {
        "architecture-proposal-review",
        "new-feature",
        "pre-implementation-analysis",
        "root-cause-analysis",
        "worktree-workflow",
    }
)
CLAUDE_ONLY_SKILLS = frozenset({"implement-with-opus"})


def _require_real_dir(path: Path) -> None:
    rel = path.relative_to(REPO_ROOT)
    if path.is_symlink():
        raise ValueError(f"unsafe symlink: {rel}")
    if not path.is_dir():
        raise ValueError(f"expected directory: {rel}")


def _require_real_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    _require_real_dir(skill_dir)
    skill_file = skill_dir / "SKILL.md"
    if skill_file.is_symlink():
        raise ValueError(f"unsafe symlink: {skill_file.relative_to(REPO_ROOT)}")
    if not skill_file.is_file():
        raise ValueError(f"missing skill file: {skill_file.relative_to(REPO_ROOT)}")


def _require_shared_symlink(name: str) -> None:
    link = CLAUDE_ROOT / name
    expected = f"../../.agents/skills/{name}"
    rel = link.relative_to(REPO_ROOT)
    if not link.is_symlink():
        if link.exists():
            raise ValueError(
                f"expected symlink {rel} -> {expected}, found regular path"
            )
        raise ValueError(f"missing shared skill symlink: {rel}")
    actual = os.readlink(link)
    if actual != expected:
        raise ValueError(
            f"unsafe symlink target for {rel}: {actual!r} (expected {expected!r})"
        )
    skill_file = link / "SKILL.md"
    if not skill_file.is_file():
        raise ValueError(f"broken shared skill symlink: {rel}")


def main() -> int:
    try:
        if SHARED_SKILLS & CLAUDE_ONLY_SKILLS:
            raise ValueError("skill classifications overlap")

        if LEGACY_CODEX_ROOT.is_symlink() or LEGACY_CODEX_ROOT.exists():
            raise ValueError(
                "legacy .codex/skills directory must be removed; "
                "shared skills live under .agents/skills"
            )

        for path in (
            AGENTS_ROOT.parent,
            AGENTS_ROOT,
            CLAUDE_ROOT.parent,
            CLAUDE_ROOT,
        ):
            _require_real_dir(path)

        for name in sorted(SHARED_SKILLS):
            _require_real_skill(AGENTS_ROOT, name)

        actual_claude = {entry.name for entry in CLAUDE_ROOT.iterdir()}
        expected_claude = SHARED_SKILLS | CLAUDE_ONLY_SKILLS
        missing = sorted(expected_claude - actual_claude)
        unknown = sorted(actual_claude - expected_claude)
        if missing or unknown:
            details = [*(f"missing .claude/skills/{name}" for name in missing)]
            details.extend(f"unclassified .claude/skills/{name}" for name in unknown)
            raise ValueError("skill inventory mismatch: " + ", ".join(details))

        for name in sorted(SHARED_SKILLS):
            _require_shared_symlink(name)

        for name in sorted(CLAUDE_ONLY_SKILLS):
            _require_real_skill(CLAUDE_ROOT, name)

        return 0
    except (OSError, ValueError) as error:
        print(f"ERROR: skill layout check failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
