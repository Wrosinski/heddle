"""Ensure gate and shared I/O subprocess use stays inside sanctioned helpers."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

DEFAULT_SCAN_ROOTS = (Path("heddle/gate"), Path("heddle/io"))
_SCAN_ROOT_PARTS = {root.parts for root in DEFAULT_SCAN_ROOTS}
SANCTIONED = {
    Path("heddle/io/process.py"): {"Popen"},
    Path("heddle/io/git.py"): {"run"},
}
SUBPROCESS_CALLS = {"Popen", "run", "call", "check_call", "check_output"}


def _candidate_files(argv: list[str]) -> list[Path]:
    if argv:
        return [
            Path(raw)
            for raw in argv
            if raw.endswith(".py") and Path(raw).parts[:2] in _SCAN_ROOT_PARTS
        ]
    return sorted(path for root in DEFAULT_SCAN_ROOTS for path in root.rglob("*.py"))


def _subprocess_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    aliases = {"subprocess"}
    direct_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" and alias.asname:
                    aliases.add(alias.asname)
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_CALLS:
                    direct_names.add(alias.asname or alias.name)
    return aliases, direct_names


def _called_subprocess_member(
    node: ast.Call, aliases: set[str], direct_names: set[str]
) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in direct_names:
        return func.id
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in SUBPROCESS_CALLS:
        return None
    if isinstance(func.value, ast.Name) and func.value.id in aliases:
        return func.attr
    return None


def main(argv: list[str]) -> int:
    violations: list[str] = []
    for path in _candidate_files(argv):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as error:
            violations.append(f"{path}: cannot parse Python source: {error}")
            continue
        aliases, direct_names = _subprocess_aliases(tree)
        allowed_members = SANCTIONED.get(Path(path.as_posix()), set())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            member = _called_subprocess_member(node, aliases, direct_names)
            if member is None or member in allowed_members:
                continue
            violations.append(
                f"{path}:{node.lineno}: subprocess.{member} is not sanctioned"
            )

    if not violations:
        return 0

    print("ERROR: Unsanctioned subprocess calls found.")
    for violation in violations:
        print(f"  - {violation}")
    print("Use heddle.io.process.run_monitored or heddle.io.git.run_git.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
