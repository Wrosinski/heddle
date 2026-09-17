"""Reject accidental print() calls in non-CLI source modules."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SOURCE_ROOT = Path("heddle")
ALLOW_PATH_PREFIXES = (
    Path("heddle/runtime"),
    # Packaged workflow hooks are standalone CLI scripts by contract
    # Packaged hooks are command-line programs, so print() is their output channel.
    Path("heddle/resources/hooks"),
)
ALLOW_FILES = {
    Path("heddle/cli.py"),
}


def _candidate_files(argv: list[str]) -> list[Path]:
    if argv:
        return [
            Path(raw)
            for raw in argv
            if raw.endswith(".py") and Path(raw).parts[:1] == ("heddle",)
        ]
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _is_allowed_path(path: Path) -> bool:
    normalized = Path(path.as_posix())
    if normalized in ALLOW_FILES:
        return True
    return any(normalized.is_relative_to(prefix) for prefix in ALLOW_PATH_PREFIXES)


def _print_lines(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            lines.append(node.lineno)
    return lines


def main(argv: list[str]) -> int:
    violations: list[str] = []
    for path in _candidate_files(argv):
        if _is_allowed_path(path):
            continue
        for line in _print_lines(path):
            violations.append(f"{path}:{line}: print() is not allowed in source")

    if not violations:
        return 0

    print("ERROR: Accidental print() calls found in non-CLI source.")
    for violation in violations:
        print(f"  - {violation}")
    print("Use the runtime result/envelope emitters or logging instead.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
