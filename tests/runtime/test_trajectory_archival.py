"""
adoption host-tooling increment 3: trajectory archival as an engine close surface.

Covers ``heddle.runtime.trajectory.archive_trajectory`` /
``write_aggregate`` against tmp native workspaces, and the best-effort
close wrapper ``auto_close.archive_trajectory_best_effort`` (owner rulings
6/7: fixed ``docs/gate-trajectories/`` output, archival inside the close,
never blocking it — RR-core posture). The CLI wrapper scripts keep their own
end-to-end coverage in ``tests/gate/``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from heddle.kernel.project_config import load_project_config
from heddle.runtime import auto_close
from heddle.runtime.trajectory import archive_trajectory, write_aggregate


def test_archive_non_native_or_empty_returns_none(tmp_path: Path) -> None:
    assert archive_trajectory(tmp_path, "ghost", now=date(2026, 7, 22)) is None

    # A native workspace with no artifacts and no events archives nothing.
    workspace = tmp_path / "plans" / "bare"
    workspace.mkdir(parents=True)
    from tests.operational_model_helpers import document, write

    write(workspace / "state.yaml", document(feature="bare", tier=2))
    assert archive_trajectory(tmp_path, "bare", now=date(2026, 7, 22)) is None


def test_write_aggregate_empty_corpus(tmp_path: Path) -> None:
    aggregate = write_aggregate(tmp_path / "docs" / "gate-trajectories")

    text = aggregate.read_text(encoding="utf-8")
    assert "Features: 0" in text
    assert "| none | 0.0 | 0 |" in text


def _config(tmp_path: Path, config_text: str = "") -> object:
    (tmp_path / ".heddle.yaml").write_text(config_text, encoding="utf-8")
    return load_project_config(tmp_path)


def test_best_effort_nothing_to_archive_never_raises(tmp_path: Path) -> None:
    config = _config(tmp_path)

    result = auto_close.archive_trajectory_best_effort(config, "ghost")

    assert result["archived"] is False
    assert "no gate trajectory artifacts" in result["error"]
