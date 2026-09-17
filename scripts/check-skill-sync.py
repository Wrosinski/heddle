"""Check or generate repository skill mirrors from the authored Claude copies."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_ROOT = REPO_ROOT / ".claude" / "skills"
CODEX_ROOT = REPO_ROOT / ".codex" / "skills"


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
        "--generate", action="store_true", help="repair missing or stale Codex mirrors"
    )
    args = parser.parse_args()
    try:
        claude_files = _skill_files(CLAUDE_ROOT)
        codex_files = _skill_files(CODEX_ROOT)
        extra = sorted(codex_files.keys() - claude_files.keys())
        if extra:
            raise ValueError(
                "unknown mirror(s); preserve and reconcile before generation: "
                + ", ".join(f".codex/skills/{name}" for name in extra)
            )
        # Complete both path scans before reading or writing any skill bytes.
        authored = {name: path.read_bytes() for name, path in claude_files.items()}
        changed = {
            name: content
            for name, content in authored.items()
            if name not in codex_files or codex_files[name].read_bytes() != content
        }
        if args.generate:
            for name, content in sorted(changed.items()):
                target = CODEX_ROOT / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            return 0
        if not changed:
            return 0
        print("ERROR: skill mirrors differ from the authored .claude/skills copies.")
        for name in sorted(changed):
            problem = "missing mirror" if name not in codex_files else "content drift"
            print(f"  - {problem}: .codex/skills/{name}")
        print("Fix: run python scripts/check-skill-sync.py --generate.")
    except (OSError, ValueError) as error:
        print(f"ERROR: skill mirror check refused: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
