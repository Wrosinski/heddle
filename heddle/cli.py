"""Top-level Heddle CLI.

Installed as the ``heddle`` console script. Delegates to the shared runtime
dispatcher (``heddle.runtime.dispatch``): ``help`` is served, ``run-gate``
forwards verbatim to the gate engine, and every other advertised command routes
through its registered runtime adapter or returns a structured
``not-implemented`` failure.
"""

from __future__ import annotations

import os
import sys

from heddle.contracts.result import ExitCode
from heddle.runtime.dispatch import run


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except BrokenPipeError:
        # A consumer closing stdout
        # early (`heddle help --json | head -1`) ends quietly, never with a
        # traceback. Repoint the stdout fd at devnull so the interpreter's
        # shutdown flush cannot re-raise, then exit 1 — the standard Python
        # CLI idiom for EPIPE.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass  # no real stdout fd (e.g. captured stream) — exit quietly
        return int(ExitCode.INTERNAL)
