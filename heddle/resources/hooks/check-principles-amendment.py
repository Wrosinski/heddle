#!/usr/bin/env python3
"""Require a Principles-Amendment trailer when the principles doc changes.

Packaged heddle workflow hook (stdlib-only, standalone-executable,
commit-msg stage). Amendments to the engineering principles are
owner-ratified; the ratification is recorded with a
``Principles-Amendment:`` commit trailer. There is deliberately no bypass
variable for this guard — the trailer is the mechanism.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TRAILER = re.compile(r"^Principles-Amendment:\s*\S", re.MULTILINE)


def _param(template: str) -> str:
    # A placeholder token ({{ ... }}) resolves to its default when unrendered;
    # `heddle init` replaces the whole token with the host's `.heddle.yaml`
    # value at projection time.
    if template.startswith("{{") and template.endswith("}}"):
        return template[2:-2].partition("|")[2]
    return template


PRINCIPLES_DOC = _param("{{principles_doc|docs/workflow/engineering-principles.md}}")


def _principles_doc_staged() -> bool:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--", PRINCIPLES_DOC],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError:
        return False
    return bool(result.stdout.strip())


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(f"Usage: {Path(sys.argv[0]).name} <commit-msg-file>")
        return 1
    if not _principles_doc_staged():
        return 0
    try:
        message = Path(argv[0]).read_text(encoding="utf-8")
    except OSError:
        message = ""
    if TRAILER.search(message):
        return 0

    print(f"ERROR: This commit changes {PRINCIPLES_DOC}.")
    print()
    print("Amendments to the engineering principles are owner-ratified. Record")
    print("the ratification with a trailer:")
    print()
    print("  Principles-Amendment: what changed and who ratified it")
    print()
    print("There is no bypass variable for this guard — the trailer is the mechanism.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
