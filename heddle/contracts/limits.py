"""Shared host-policy session-timeout defaults.

This is the single binding for the run-gate CLI (`heddle/gate/cli.py`) and the
`verify` subprocess (`heddle/runtime/write_path.py`). `verify` inherits
run-gate's host-policy timeout defaults instead of duplicating them across the
runtime/gate import boundary.
Per-invocation env vars (`GATE_*` / `HEDDLE_VERIFY_*`) still override.
"""

DEFAULT_SESSION_HARD_TIMEOUT_S = 2700
DEFAULT_SESSION_INACTIVITY_TIMEOUT_S = 900
