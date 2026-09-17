"""Shared envelope emission for CLI handlers.

Every handler emits the same way in JSON mode — one envelope line on
stdout, exit code from the result — and differs only in its human
rendering. This module owns the shared branch so handlers cannot drift
on the wire format.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from heddle.contracts.result import HeddleResult


def emit_envelope(
    result: HeddleResult,
    json_mode: bool,
    human: Callable[[HeddleResult], None],
) -> int:
    if json_mode:
        print(json.dumps(result.to_envelope()))
    else:
        human(result)
    return int(result.exit_code)
