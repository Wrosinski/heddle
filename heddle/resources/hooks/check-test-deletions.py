#!/usr/bin/env python3
"""Block staged test deletions without an explicit bypass.

Packaged heddle workflow hook (stdlib-only, standalone-executable).
Test-preservation discipline: deleting a test file or removing a test
function/class must be deliberate — ``ALLOW_TEST_DELETION=1`` (paired
with the ``Bypass-Justification:`` trailer contract) is the escape valve.
"""

from __future__ import annotations

import os
import re
import subprocess

REMOVED_TEST = re.compile(r"^-\s*(def test_|async def test_|class Test)")


def _param(template: str) -> str:
    # A placeholder token ({{ ... }}) resolves to its default when unrendered;
    # `heddle init` replaces the whole token with the host's `.heddle.yaml`
    # value at projection time.
    if template.startswith("{{") and template.endswith("}}"):
        return template[2:-2].partition("|")[2]
    return template


TESTS_ROOT = _param("{{layout_tests|tests}}")


def _git_stdout(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args], check=False, capture_output=True, text=True, timeout=30
        )
    except OSError:
        return ""
    return result.stdout


def _removed_test_lines() -> list[str]:
    diff = _git_stdout(
        [
            "diff",
            "--cached",
            "--diff-filter=M",
            "-U0",
            "--",
            f":(glob){TESTS_ROOT}/**/*.py",
        ]
    )
    return [line for line in diff.splitlines() if REMOVED_TEST.match(line)]


def _deleted_test_files() -> list[str]:
    listing = _git_stdout(
        [
            "diff",
            "--cached",
            "--diff-filter=D",
            "--name-only",
            "--",
            f":(glob){TESTS_ROOT}/**/*.py",
        ]
    )
    return [line for line in listing.splitlines() if line.strip()]


def main() -> int:
    if os.environ.get("ALLOW_TEST_DELETION", "0") == "1":
        return 0
    deleted_files = _deleted_test_files()
    removed_tests = _removed_test_lines()
    if not deleted_files and not removed_tests:
        return 0

    print("ERROR: Test deletions detected.")
    if deleted_files:
        print("\nDeleted test files:")
        for path in deleted_files:
            print(path)
    if removed_tests:
        print("\nRemoved test functions:")
        for line in removed_tests:
            print(line)
    print("\nTo allow this, set ALLOW_TEST_DELETION=1")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
