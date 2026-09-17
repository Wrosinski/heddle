"""
Targeted verification-command authoring through the runtime write.
"""

from __future__ import annotations

from pathlib import Path

from tests.runtime.write_path_helpers import read_yaml
from tests.tiering_review_helpers import V7_FEATURE, current_host


def _state(host: Path) -> Path:
    return host / "plans" / V7_FEATURE / "state.yaml"


def test_commands_set_records_one_exact_command(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    host, _path = current_host(tmp_path, monkeypatch)
    monkeypatch.chdir(host)
    before = read_yaml(_state(host))

    command = "pytest -q tests/runtime/test_status.py"
    code, out, _err = run_cli(
        [
            "commands",
            "set",
            "smoke_test",
            "--command",
            command,
            "--json",
        ]
    )

    assert code == 0
    envelope = envelope_tools.parse(out)
    assert envelope["data"]["command_key"] == "smoke_test"
    assert envelope["data"]["shell_command"] == command
    after = read_yaml(_state(host))
    assert after["commands"]["smoke_test"] == command
    assert after["revision"] == before["revision"] + 1


def test_commands_set_dry_run_is_non_mutating(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    host, _path = current_host(tmp_path, monkeypatch)
    monkeypatch.chdir(host)
    before = read_yaml(_state(host))

    code, out, _err = run_cli(
        [
            "commands",
            "set",
            "acceptance_test",
            "--command",
            "pytest -q -m acceptance",
            "--dry-run",
            "--json",
        ]
    )

    assert code == 0
    assert envelope_tools.parse(out)["data"]["dry_run"] is True
    assert read_yaml(_state(host)) == before


def test_commands_set_rejects_unknown_key_before_writing(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    host, _path = current_host(tmp_path, monkeypatch)
    monkeypatch.chdir(host)
    before = read_yaml(_state(host))

    code, out, _err = run_cli(
        [
            "commands",
            "set",
            "not_a_command",
            "--command",
            "true",
            "--json",
        ]
    )

    assert code == 2
    assert envelope_tools.parse(out)["error"]["code"] == "usage"
    assert read_yaml(_state(host)) == before
