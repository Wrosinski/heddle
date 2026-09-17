"""
Phase sessions run uncapped by default (user directive, 2026-07-06).

commands dogfood: the scaffold session hit ``max_turns=100`` mid-red-scaffold
(18 ACs of pinned tests), the CLI exited 1, and the driver hard-halted
with ``phase session failed with exit 1`` — no session fact, no fold, a
designed rail killing healthy work. Contract now: ``max_turns`` defaults
to None (no ``--max-turns`` flag is passed at all); an explicit integer
in ``.heddle.yaml`` opts a cap back in (``null`` = uncapped, same as
absent); the per-session budget (default $150) is the sole rail.
"""

from __future__ import annotations

from pathlib import Path

from heddle.driver import sessions
from heddle.kernel.project_config import DEFAULT_AUTOPILOT


def _spec(max_turns: int | None) -> sessions.PhaseSessionSpec:
    return sessions.PhaseSessionSpec(
        stage="scaffold",
        feature="demo",
        prompt_path=Path("/tmp/prompt.md"),
        allowed_tools=("Read",),
        deny_rules=(),
        max_turns=max_turns,
        max_budget_usd=150.0,
    )


def test_default_config_is_uncapped_with_150_budget():
    assert DEFAULT_AUTOPILOT.max_turns is None, (
        "FAIL: the default must be uncapped — the scaffold session died at "
        "the old max_turns=100 mid-red-scaffold"
    )
    assert DEFAULT_AUTOPILOT.max_budget_usd == 150.0


def test_uncapped_spec_omits_the_max_turns_flag_entirely():
    command = sessions._phase_session_command(
        _spec(None), sessions._autonomy_addendum_path()
    )
    assert "--max-turns" not in command, (
        "FAIL: max_turns=None must omit the flag, not pass a sentinel value"
    )
    budget_index = command.index("--max-budget-usd")
    assert command[budget_index + 1] == "150.00"


def test_explicit_turn_cap_still_opts_in():
    command = sessions._phase_session_command(
        _spec(40), sessions._autonomy_addendum_path()
    )
    turns_index = command.index("--max-turns")
    assert command[turns_index + 1] == "40"


def test_yaml_null_and_integer_max_turns_both_parse(tmp_path: Path):
    from heddle.kernel.project_config import load_project_config

    def _load(max_turns_line: str):
        (tmp_path / ".heddle.yaml").write_text(
            f"autopilot:\n  flow: hitl\n{max_turns_line}",
            encoding="utf-8",
        )
        return load_project_config(tmp_path)

    assert _load("  max_turns: null\n").autopilot.max_turns is None
    assert _load("  max_turns: 25\n").autopilot.max_turns == 25
    # Absent key → the uncapped default.
    assert _load("").autopilot.max_turns is None
