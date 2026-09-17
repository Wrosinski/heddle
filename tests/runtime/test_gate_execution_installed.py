"""Installed-artifact failure guidance for gate execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from heddle.gate.types import FailureReason
from tests.runtime.wheel_harness import build_installed_wheel

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "heddle/resources/gate-failure-guide.md"
EMITTED_FAILURE_FAMILIES = (
    ("preflight", FailureReason.MISSING_MILESTONE_SECTION),
    ("preflight", FailureReason.MISSING_PLAN_PATH),
    ("preflight", FailureReason.EMPTY_DIFF),
    ("preflight", FailureReason.CHANGED_FILES_EMPTY),
    ("provider", FailureReason.OVERLOADED),
    ("transport", FailureReason.TRANSPORT_ERROR),
    ("budget", FailureReason.BUDGET_EXHAUSTED),
    ("turn", FailureReason.MAX_TURNS),
    ("timeout", FailureReason.TIMEOUT),
    ("inactivity", FailureReason.INACTIVITY_TIMEOUT),
    ("structure", FailureReason.INVALID_OUTPUT_STRUCTURE),
    ("generic-exit", FailureReason.NONZERO_EXIT),
)
RESERVED_FAILURE_REASONS = frozenset(
    {
        FailureReason.MISSING_TOOL_EVENTS.value,
        FailureReason.MISSING_TURN_COMPLETED.value,
    }
)


@pytest.mark.toolchain
def test_ac11_built_wheel_resolves_self_contained_failure_guide(
    tmp_path: Path,
) -> None:
    assert GUIDE.is_file(), "FAIL AC-11: source guide is absent"
    installed = build_installed_wheel(tmp_path)
    probe = installed.python_probe(
        "from importlib.resources import files; "
        "p=files('heddle').joinpath('resources/gate-failure-guide.md'); "
        "text=p.read_text(encoding='utf-8'); "
        "print(len(text), text.count('## `'))",
        cwd=tmp_path,
    )
    assert probe.returncode == 0, probe.stderr
    size, entries = (int(value) for value in probe.stdout.split())
    assert size > 0 and entries == len(FailureReason)
