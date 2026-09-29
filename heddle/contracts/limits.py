"""Shared host-policy timeout defaults.

The run-gate CLI (`heddle/gate/cli.py`) and the `verify` subprocess
(`heddle/runtime/verify_exec.py`) share the hard timeout. A gate reviewer may
run a command for up to `GATE_BASH_MAX_TIMEOUT_S`, which can produce no stream
activity, so gates use a longer inactivity window than `verify`, which keeps
the shorter one to catch a hung verification promptly. `HEDDLE_VERIFY_*`
environment variables still override the verify values.
"""

DEFAULT_SESSION_HARD_TIMEOUT_S = 2700
DEFAULT_SESSION_INACTIVITY_TIMEOUT_S = 900
GATE_BASH_DEFAULT_TIMEOUT_S = 600
GATE_BASH_MAX_TIMEOUT_S = 900
DEFAULT_GATE_INACTIVITY_TIMEOUT_S = 1200
