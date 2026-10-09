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


def test_batch_progress_line_names_each_slot_reviewer_and_state() -> None:
    from heddle.runtime.gate_run import _batch_progress_line, _SlotProgress

    line = _batch_progress_line(
        150.4,
        [
            _SlotProgress("spec-review primary", "codex", "running"),
            _SlotProgress("spec-review secondary", "claude", "done", 112),
            _SlotProgress("behavior-review primary", "claude", "failed", 3725),
        ],
    )
    assert line == (
        "heddle run-gates: 2m30s elapsed; spec-review primary (codex) running; "
        "spec-review secondary (claude) done after 1m52s; "
        "behavior-review primary (claude) failed after 1h02m"
    )


def test_gate_progress_line_names_the_slot_cli_and_elapsed_time() -> None:
    from heddle.runtime.gate_run import _gate_progress_line

    assert _gate_progress_line("milestone-review", "claude", 42.9) == (
        "heddle run-gate: milestone-review (claude) running, 42s elapsed"
    )


def test_the_engine_surfaces_no_buffered_checkpoints_after_the_provider_exits() -> None:
    """Progress comes from the runtime's own interval line while the provider
    runs; replaying monitor checkpoints after exit printed them all at once."""
    import inspect

    from heddle.gate.entry import run_gate_for_runtime
    from heddle.gate.runner import GateRunner

    assert "progress" not in inspect.signature(run_gate_for_runtime).parameters
    assert "progress" not in inspect.signature(GateRunner).parameters
