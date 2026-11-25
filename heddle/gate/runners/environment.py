from __future__ import annotations

import os

from heddle.contracts.gates import GATE_AGENT_SESSION, HEDDLE_AGENT_SESSION_ENV


def gate_agent_env() -> dict[str, str]:
    env = os.environ.copy()
    env[HEDDLE_AGENT_SESSION_ENV] = GATE_AGENT_SESSION
    return env
