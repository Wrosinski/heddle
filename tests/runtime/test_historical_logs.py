from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.operational_model_helpers import FEATURE, read
from tests.tiering_completion_helpers import final_host


def test_completed_host_validates_without_historical_logs_or_raw_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: Callable[[list[str]], tuple[int, str, str]],
) -> None:
    """Survivor pin for AC-8 paired with v9/v1 compatibility discriminators."""
    host = final_host(tmp_path, monkeypatch)
    accepted = host.complete()
    assert accepted.ok and accepted.data["accepted"]
    (host.root / "CLAUDE.md").symlink_to("AGENTS.md")
    state = read(host.state)
    logs = {fact["log"] for fact in state["verifications"]}
    logs.add(state["completion"]["close_suite"]["log"])
    assert logs and host.archive.is_file()
    for log in logs:
        (host.state.parent / log).unlink()
    host.archive.unlink()
    before = host.state.read_bytes()

    for command in (
        ["validate"],
        ["validate", "--feature", FEATURE],
        ["doctor", "--feature", FEATURE],
    ):
        code, output, _error = run_cli([*command, "--json"])
        result = json.loads(output)
        assert code == 0 and result["ok"], result
        missing = [
            row
            for row in result["diagnostics"]
            if row["code"] == "historical-logs-optional"
        ]
        assert len(missing) == 1
        assert missing[0]["severity"] == "info"
        assert f"{len(logs)} historical verification log(s)" in missing[0]["message"]
    assert host.state.read_bytes() == before

    code, output, _error = run_cli(["orient", "--json"])
    result = json.loads(output)
    assert code == 3 and "feature prepare" in result["error"]["hint"]


def test_missing_logs_still_block_unaccepted_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    state = read(host.state)
    assert state["completion"] is None
    (host.state.parent / state["verifications"][-1]["log"]).unlink()
    before = host.state.read_bytes()

    result = host.complete()

    assert not result.ok and int(result.exit_code) == 3
    assert result.error.code == "workspace-invalid"
    assert "retained evidence" in result.error.message
    assert host.state.read_bytes() == before
    assert not host.archive.exists()
