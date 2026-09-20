"""Check repository skill inventories and generate shared Codex mirrors."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_ROOT = REPO_ROOT / ".claude" / "skills"
CODEX_MIRROR_ROOT = REPO_ROOT / ".codex" / "skills"
CODEX_NATIVE_ROOT = REPO_ROOT / ".agents" / "skills"

SHARED_SKILLS = frozenset(
    {
        "architecture-proposal-review/SKILL.md",
        "new-feature/SKILL.md",
        "pre-implementation-analysis/SKILL.md",
        "root-cause-analysis/SKILL.md",
        "worktree-workflow/SKILL.md",
    }
)
CLAUDE_ONLY_SKILLS = frozenset({"implement-with-opus/SKILL.md"})
CODEX_NATIVE_SKILLS = frozenset({"implement-with-sol/SKILL.md"})


def _skill_files(root: Path) -> dict[str, Path]:
    # Preflight every parent before any directory walk or skill byte read.
    for path in (root.parent, root):
        if path.is_symlink():
            raise ValueError(f"unsafe symlink: {path.relative_to(REPO_ROOT)}")
        if path.exists() and not path.is_dir():
            raise ValueError(f"expected directory: {path.relative_to(REPO_ROOT)}")

    def fail_walk(error: OSError) -> None:
        raise error

    files = {}
    for current, directories, names in os.walk(
        root, followlinks=False, onerror=fail_walk
    ):
        for name in sorted(directories + names):
            path = Path(current) / name
            if path.is_symlink():
                raise ValueError(f"unsafe symlink: {path.relative_to(REPO_ROOT)}")
            if name == "SKILL.md":
                if not path.is_file():
                    raise ValueError(f"expected skill file: {path}")
                files[path.relative_to(root).as_posix()] = path
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generate",
        action="store_true",
        help="repair missing or stale shared Codex mirrors",
    )
    args = parser.parse_args()
    try:
        claude_files = _skill_files(CLAUDE_ROOT)
        codex_mirrors = _skill_files(CODEX_MIRROR_ROOT)
        codex_native = _skill_files(CODEX_NATIVE_ROOT)

        classifications = (SHARED_SKILLS, CLAUDE_ONLY_SKILLS, CODEX_NATIVE_SKILLS)
        if any(
            left & right
            for index, left in enumerate(classifications)
            for right in classifications[index + 1 :]
        ):
            raise ValueError("skill classifications overlap")

        expected_claude = SHARED_SKILLS | CLAUDE_ONLY_SKILLS
        _require_inventory(".claude/skills", claude_files, expected_claude)
        _require_inventory(".agents/skills", codex_native, CODEX_NATIVE_SKILLS)

        extra_mirrors = sorted(codex_mirrors.keys() - SHARED_SKILLS)
        if extra_mirrors:
            raise ValueError(
                "unknown mirror(s); preserve and reconcile before generation: "
                + ", ".join(f".codex/skills/{name}" for name in extra_mirrors)
            )
        # Complete every path scan before reading or writing any skill bytes.
        authored = {name: claude_files[name].read_bytes() for name in SHARED_SKILLS}
        changed = {
            name: content
            for name, content in authored.items()
            if name not in codex_mirrors or codex_mirrors[name].read_bytes() != content
        }
        if args.generate:
            for name, content in sorted(changed.items()):
                target = CODEX_MIRROR_ROOT / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            return 0
        if not changed:
            return 0
        print("ERROR: shared skill mirrors differ from their .claude/skills sources.")
        for name in sorted(changed):
            problem = "missing mirror" if name not in codex_mirrors else "content drift"
            print(f"  - {problem}: .codex/skills/{name}")
        print("Fix: run python scripts/check-skill-sync.py --generate.")
    except (OSError, ValueError) as error:
        print(f"ERROR: skill mirror check refused: {error}")
    return 1


def _require_inventory(
    label: str, files: dict[str, Path], expected: frozenset[str]
) -> None:
    missing = sorted(expected - files.keys())
    unknown = sorted(files.keys() - expected)
    if missing or unknown:
        details = [*(f"missing {label}/{name}" for name in missing)]
        details.extend(f"unclassified {label}/{name}" for name in unknown)
        raise ValueError("skill inventory mismatch: " + ", ".join(details))


if __name__ == "__main__":
    raise SystemExit(main())
