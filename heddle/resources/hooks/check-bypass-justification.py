#!/usr/bin/env python3
"""Require an explicit justification when ALLOW_* bypasses are active.

Packaged heddle workflow hook (stdlib-only, standalone-executable,
commit-msg stage). The ``ALLOW_*=1`` -> ``Bypass-Justification:`` trailer
contract is a heddle workflow convention: any active ``ALLOW_*``
environment bypass must be justified either by a non-empty
``Bypass-Justification:`` commit trailer or a ``BYPASS_JUSTIFICATION``
environment value.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _active_bypasses() -> list[str]:
    active: list[str] = []
    for name, value in os.environ.items():
        if not name.startswith("ALLOW_"):
            continue
        if value.strip().lower() in {"", "0", "false", "no"}:
            continue
        active.append(name)
    return sorted(active)


def _message_has_trailer(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.startswith("Bypass-Justification:") and line.partition(":")[2].strip()
        for line in text.splitlines()
    )


def main(argv: list[str]) -> int:
    active = _active_bypasses()
    if not active:
        return 0
    if os.environ.get("BYPASS_JUSTIFICATION", "").strip():
        return 0
    message_paths = [Path(raw) for raw in argv] or [Path(".git/COMMIT_EDITMSG")]
    if any(_message_has_trailer(path) for path in message_paths):
        return 0

    print("ERROR: Active ALLOW_* bypass requires a justification.")
    print(f"Active bypasses: {', '.join(active)}")
    print("Add a Bypass-Justification: commit trailer or set BYPASS_JUSTIFICATION.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
