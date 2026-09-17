"""Progress streaming during gate execution."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"


def _run_gate_landed() -> bool:
    from heddle.runtime.contracts import COMMAND_SURFACE

    run_gate = next((c for c in COMMAND_SURFACE if c.name == "run-gate"), None)
    return run_gate is not None and any(f.name == "--json" for f in run_gate.flags)


# Feature landed: a regression that removed the integration surface must FAIL here, not
# silently skip the module (review integration; plan write T5 — retire red-phase
# skip sentinels to hard asserts at completion).
assert _run_gate_landed(), (
    "run-gate must return its JSON envelope — a regression here must "
    "fail loudly, not skip this module"
)


def test_ac09_monitor_exposes_checkpoints_as_the_streaming_source() -> None:
    # AC-9 anchor: the streaming source exists — MonitorResult carries the
    # checkpoint list the runtime surfaces as progress.
    from heddle.io.process import MonitorResult

    assert "checkpoints" in MonitorResult.__dataclass_fields__, (
        "FAIL: MonitorResult must expose `checkpoints` — the progress source "
        "run-gate streams (REQ-9/AC-9)"
    )
