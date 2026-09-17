"""Structural guard against unsupported gate vocabulary."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCANNED_ROOTS = (REPO_ROOT / "heddle", REPO_ROOT / "tests" / "fixtures")
SCANNED_SUFFIXES = (".py", ".yaml", ".yml")

# token -> (the contract it guards, how to fix a re-introduction)
RETIRED_TOKENS = {
    "detect_stale_milestone": (
        "REQ-3/AC-3: the stale-milestone re-detection is deleted",
        "remove it — `milestone advance` (M4) owns the current-milestone "
        "mutation; the engine does not re-detect staleness",
    ),
    "ALLOW_STALE_CURRENT_MILESTONE": (
        "REQ-3/AC-3: the stale-milestone escape hatch is deleted",
        "remove it — the hatch is unreachable once detect_stale_milestone is gone",
    ),
    "category_a": (
        "REQ-11/AC-11/finding taxonomy: the legacy finding taxonomy is replaced",
        "use the finding taxonomy shape {by_severity, by_classification, total, "
        "contradictions}",
    ),
    "category_b": (
        "REQ-11/AC-11/finding taxonomy: the legacy finding taxonomy is replaced",
        "use the finding taxonomy shape {by_severity, by_classification, total, "
        "contradictions}",
    ),
}


@pytest.mark.parametrize("token", sorted(RETIRED_TOKENS))
def test_no_retired_vocabulary_under_heddle_or_fixtures(token: str) -> None:
    contract, fix = RETIRED_TOKENS[token]
    offenders: list[str] = []
    for root in SCANNED_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if token in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"FAIL: retired M3 token {token!r} re-introduced in {offenders}. "
        f"{contract} — {fix}. This tripwire guards against re-adding "
        "vocabulary M3 deliberately removed; scan is tree-wide over heddle/ "
        "and tests/fixtures/."
    )
