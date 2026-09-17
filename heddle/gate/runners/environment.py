from __future__ import annotations

import os

HEDDLE_AGENT_SESSION_ENV = "HEDDLE_AGENT_SESSION"
GATE_AGENT_SESSION = "gate"


def gate_agent_env() -> dict[str, str]:
    env = os.environ.copy()
    env[HEDDLE_AGENT_SESSION_ENV] = GATE_AGENT_SESSION
    return env
