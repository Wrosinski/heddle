from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.tiering_helpers import entry
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    review_status,
    runs,
)


def scaffold_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    return cast(
        tuple[Path, Path],
        current_host(
            tmp_path,
            monkeypatch,
            stage="scaffold",
            overrides={
                "review-test-scaffolding": entry(
                    "review-test-scaffolding", minimum_rounds=1, limit=2
                )
            },
        ),
    )


@pytest.mark.parametrize("declaration", ["plan", "command"])
def test_unwritten_tests_do_not_block_scaffold_orientation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declaration: str
) -> None:
    host, state_path = scaffold_host(tmp_path, monkeypatch)
    if declaration == "plan":
        plan = state_path.with_name("plan.md")
        plan.write_text(plan.read_text() + "\nPlanned: `tests/future.py`.\n")
    else:
        state = yaml.safe_load(state_path.read_text())
        state["commands"]["acceptance_test"] = "python3 tests/future.py"
        state_path.write_text(yaml.safe_dump(state, sort_keys=False))
    before = state_path.read_bytes()

    status = execute(ops.Status(feature=V7_FEATURE))
    orient = execute(ops.Orient(feature=V7_FEATURE))
    kickoff = execute(ops.Kickoff(feature=V7_FEATURE))

    for result in (status, orient, kickoff):
        assert result.ok, result.to_envelope()
    assert status.data is not None and kickoff.data is not None
    assert status.next_actions == orient.next_actions
    assert isinstance(status.next_actions[0].action, ops.SessionAction)
    assert "gate-not-converged" in status.data["blocking_conditions"]
    assert "Build the verification system" in kickoff.data["briefing"]
    assert state_path.read_bytes() == before
    assert not (host / "tests/future.py").exists()


def test_actual_scaffold_review_requires_declared_test_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: Any
) -> None:
    _host, state_path = scaffold_host(tmp_path, monkeypatch)
    plan = state_path.with_name("plan.md")
    plan.write_text(plan.read_text() + "\nPlanned: `tests/future.py`.\n")
    calls = provider_transport(monkeypatch, review_content("review-test-scaffolding"))

    code, result = gate_command(run_cli, "run-gate", "review-test-scaffolding")

    assert code != 0
    assert not result["ok"]
    assert "tests/future.py" in str(result)
    assert not calls


def test_removed_reviewed_test_reopens_coverage_without_blocking_orientation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: Any
) -> None:
    host, state_path = scaffold_host(tmp_path, monkeypatch)
    plan = state_path.with_name("plan.md")
    plan.write_text(plan.read_text() + "\nReviewed: `tests/check.py`.\n")
    provider_transport(monkeypatch, review_content("review-test-scaffolding"))
    assert gate_command(run_cli, "run-gate", "review-test-scaffolding")[0] == 0
    run_id = runs(state_path)[0]["run_id"]
    assert dispose(
        state_path,
        [
            disposition(
                run_id, "@coverage", status="settled", references=["tests/check.py"]
            )
        ],
    ).ok
    assert review_status(state_path, role="review-test-scaffolding")["closed"]
    test_path = host / "tests/check.py"
    original = test_path.read_bytes()
    test_path.unlink()

    row = review_status(state_path, role="review-test-scaffolding")

    assert not row["closed"]
    assert row["open_refs"] == [[run_id, "@coverage"]]
    assert execute(ops.Orient(feature=V7_FEATURE)).ok
    test_path.write_bytes(original)
    assert review_status(state_path, role="review-test-scaffolding")["closed"]
