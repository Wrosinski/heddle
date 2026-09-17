#!/usr/bin/env python3
"""Prevent gate lock files from being committed.

Packaged heddle workflow hook (stdlib-only, standalone-executable).
Gate locks under ``<plans>/.gate-locks/`` are transient flock coordination
files (0-byte, opened "a+", never read for content, never durable
evidence). They are gitignored; this guard is defense-in-depth against a
force-add or an earlier commit re-introducing them.

Union semantics (documented policy): the staged-index scan
(``--diff-filter=d`` — lowercase d excludes deletions, so add/modify/
rename of a lock path is caught while ``git rm --cached`` untracking is
allowed; locks are identical 0-byte files, so a fresh add can be detected
as a rename from a staged deletion and ``=AM`` would miss it) PLUS the
tracked-file scan (catches locks already committed in history). Bypass:
``ALLOW_COMMITTED_GATE_LOCKS=1``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _param(template: str) -> str:
    # A placeholder token ({{ ... }}) resolves to its default when unrendered;
    # `heddle init` replaces the whole token with the host's `.heddle.yaml`
    # value at projection time.
    if template.startswith("{{") and template.endswith("}}"):
        return template[2:-2].partition("|")[2]
    return template


PLANS_ROOT = _param("{{layout_plans|plans}}")
LOCKS_DIR = f"{PLANS_ROOT}/.gate-locks"


def _is_lock_path(path: Path) -> bool:
    return ".gate-locks" in path.parts or path.suffix == ".lock"


def _git_lines(args: list[str]) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args], check=False, capture_output=True, text=True, timeout=30
        )
    except OSError:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _staged_locks() -> list[str]:
    return _git_lines(
        ["diff", "--cached", "--name-only", "--diff-filter=d", "--", f"{LOCKS_DIR}/"]
    )


def _tracked_locks() -> list[str]:
    # Scoped to the locks dir (symmetric with the staged scan). A repo-wide
    # `*.lock` pathspec would sweep legitimate tracked lockfiles — e.g. a
    # host's poetry.lock — into a false FATAL on every commit.
    return [
        line
        for line in _git_lines(["ls-files", "--", LOCKS_DIR])
        if _is_lock_path(Path(line))
    ]


def main(argv: list[str]) -> int:
    if os.environ.get("ALLOW_COMMITTED_GATE_LOCKS", "0") == "1":
        return 0
    candidates = [str(Path(raw)) for raw in argv if _is_lock_path(Path(raw))]
    candidates.extend(_staged_locks())
    candidates.extend(_tracked_locks())
    violations = sorted(set(candidates))
    if not violations:
        return 0

    print("ERROR: Gate lock files must not be committed.")
    for path in violations:
        print(f"  - {path}")
    print("These are transient flock coordination files (gitignored).")
    print("Unstage/untrack them: git rm --cached <file>.")
    print("To allow anyway, set ALLOW_COMMITTED_GATE_LOCKS=1")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
