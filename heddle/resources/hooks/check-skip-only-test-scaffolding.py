#!/usr/bin/env python3
"""Block newly-added skip-only or non-executable scaffolding in tests.

Packaged heddle workflow hook (stdlib-only, standalone-executable).
Directly enforces the phase-5 red-scaffold discipline: scaffolds must
fail, not skip. A strict xfail is allowed only around a test function with a
non-constant assertion; semantic AC fidelity remains a reviewer judgment.
Narrow skip allowances carry an explicit opt-in or host limitation reason.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys

SKIP_TOKENS = ("pytest.mark.skip", "pytest.mark.xfail", "pytest.skip(")
ALLOW_TOKENS = (
    "allow_module_level=True",
    "permission bits do not bind when running as root",
    "requires explicit opt-in",
)


def _param(template: str) -> str:
    # A placeholder token ({{ ... }}) resolves to its default when unrendered;
    # `heddle init` replaces the whole token with the host's `.heddle.yaml`
    # value at projection time.
    if template.startswith("{{") and template.endswith("}}"):
        return template[2:-2].partition("|")[2]
    return template


TESTS_ROOT = _param("{{layout_tests|tests}}")


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<line>\d+)(?:,\d+)? @@")


def _xfail_scaffold_is_executable(source: str) -> bool:
    """Standalone transcription of the gate-entry mechanical predicate."""
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return False
    functions = [
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test")
    ]
    if len(functions) != 1:
        return False
    nested_scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    stack: list[ast.AST] = list(functions[0].body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Assert) and any(
            isinstance(part, (ast.Name, ast.Call, ast.Attribute, ast.Subscript))
            for part in ast.walk(node.test)
        ):
            return True
        if isinstance(node, nested_scopes):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


def _staged_added_lines() -> list[tuple[str, int, str]] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--unified=0",
                "--",
                f":(glob){TESTS_ROOT}/**/*.py",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(
            f"ERROR: cannot inspect staged test scaffolding: {error}", file=sys.stderr
        )
        return None
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git diff exited {result.returncode}"
        print(
            f"ERROR: cannot inspect staged test scaffolding: {detail}",
            file=sys.stderr,
        )
        return None
    path = ""
    line_number = 0
    added: list[tuple[str, int, str]] = []
    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line.removeprefix("+++ b/")
            continue
        if match := _HUNK.match(line):
            line_number = int(match.group("line"))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append((path, line_number, line[1:]))
            line_number += 1
            continue
        if not line.startswith("-"):
            line_number += 1
    return added


def _staged_source(path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "show", f":{path}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(
            f"ERROR: cannot inspect staged test scaffolding: {error}",
            file=sys.stderr,
        )
        return None
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git show exited {result.returncode}"
        print(
            f"ERROR: cannot inspect staged test scaffolding: {detail}",
            file=sys.stderr,
        )
        return None
    return result.stdout


def _strict_xfail_index(path: str) -> tuple[tuple[int, int, bool], ...]:
    """Index strict executable-xfail validity for one staged test file."""
    source = _staged_source(path)
    if source is None:
        return ()
    try:
        module = ast.parse(source)
    except SyntaxError:
        return ()
    entries: list[tuple[int, int, bool]] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        function_source = ast.get_source_segment(source, node)
        executable = bool(
            function_source and _xfail_scaffold_is_executable(function_source)
        )
        for decorator in node.decorator_list:
            first = getattr(decorator, "lineno", -1)
            last = getattr(decorator, "end_lineno", first) or first
            if not isinstance(decorator, ast.Call):
                continue
            decorator_text = ast.get_source_segment(source, decorator) or ""
            if "pytest.mark.xfail" not in decorator_text:
                continue
            strict = next(
                (
                    keyword.value
                    for keyword in decorator.keywords
                    if keyword.arg == "strict"
                ),
                None,
            )
            entries.append(
                (
                    first,
                    last,
                    isinstance(strict, ast.Constant)
                    and strict.value is True
                    and executable,
                )
            )
    return tuple(entries)


def main() -> int:
    violations: list[str] = []
    xfail_indexes: dict[str, tuple[tuple[int, int, bool], ...]] = {}
    staged = _staged_added_lines()
    if staged is None:
        return 1
    for path, line_number, line in staged:
        if not any(token in line for token in SKIP_TOKENS):
            continue
        if any(token in line for token in ALLOW_TOKENS):
            continue
        if "pytest.mark.xfail" in line:
            index = xfail_indexes.get(path)
            if index is None:
                index = _strict_xfail_index(path)
                xfail_indexes[path] = index
            if any(
                first <= line_number <= last and valid for first, last, valid in index
            ):
                continue
        violations.append(
            f"{path}:{line_number}: added skip/xfail scaffolding: {line.strip()}"
        )

    if not violations:
        return 0

    print("ERROR: New skip-only test scaffolding needs explicit review.")
    for violation in violations:
        print(f"  - {violation}")
    print(
        "Prefer red tests; a strict xfail must wrap an executable non-constant "
        "assertion, while skips need a narrow opt-in/host limitation reason."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
