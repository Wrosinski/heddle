"""Claude gate runner: the command-execution posture reaches the child process."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from heddle.contracts.limits import (
    DEFAULT_GATE_INACTIVITY_TIMEOUT_S,
    DEFAULT_SESSION_HARD_TIMEOUT_S,
    GATE_BASH_DEFAULT_TIMEOUT_S,
    GATE_BASH_MAX_TIMEOUT_S,
)
from heddle.gate.cli import GateArgs, resolve_gate_execution
from heddle.gate.io import GatePaths
from heddle.gate.registry import GATES
from heddle.gate.runners.claude import execute_claude
from heddle.gate.types import GateInvocationOverrides
from tests.driver.helpers import write_executable_script
from tests.proof_continuity_helpers import command
from tests.tiering_helpers import OPUS, entry
from tests.tiering_review_helpers import (
    current_host,
    provider_transport,
    review_content,
    review_status,
)

OBSERVED_ENV = (
    "BASH_DEFAULT_TIMEOUT_MS",
    "BASH_MAX_TIMEOUT_MS",
    "HEDDLE_AGENT_SESSION",
    "CLAUDECODE",
)


def _paths(tmp_path: Path) -> GatePaths:
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    prompt = temp_dir / "prompt.md"
    prompt.write_text("review", encoding="utf-8")
    return GatePaths(
        output=tmp_path / "out.md",
        log=tmp_path / "out.log",
        summary=tmp_path / "out.gate-summary.json",
        prompt=prompt,
        raw_out=temp_dir / "raw.out",
        last_message=temp_dir / "last-message.txt",
        json_message=temp_dir / "json-message.txt",
        filtered_jsonl=temp_dir / "events.jsonl",
        command_output=temp_dir / "command-output.txt",
        temp_dir=temp_dir,
    )


def _recording_claude(tmp_path: Path, record: Path) -> Path:
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": {},
    }
    return write_executable_script(
        tmp_path / "claude",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "sys.stdin.read()\n"
        f"with open({str(record)!r}, 'w', encoding='utf-8') as stream:\n"
        "    json.dump({'argv': sys.argv[1:], 'env': "
        f"{{name: os.environ.get(name) for name in {OBSERVED_ENV!r}}}}}, stream)\n"
        f"print({json.dumps(result)!r})\n",
    )


def _flag(argv: list[str], name: str) -> str:
    assert argv.count(name) == 1, name
    return argv[argv.index(name) + 1]


@pytest.mark.parametrize(
    "gate_name",
    sorted(name for name, gate in GATES.items() if "claude" in gate.supported_clis),
)
def test_every_claude_gate_runs_commands_under_auto_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate_name: str
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    record = tmp_path / "record.json"
    gate_type = GATES[gate_name]
    prepared = SimpleNamespace(
        invocation=resolve_gate_execution(
            gate_type, GateInvocationOverrides(cli="claude")
        ),
        transport=SimpleNamespace(system="constraint"),
        output_contract=SimpleNamespace(schema_json="{}"),
        assignment_id=None,
    )
    args = GateArgs(
        gate_name=gate_name,
        cli_bin=str(_recording_claude(tmp_path, record)),
        workdir=tmp_path,
        poll_seconds=0.05,
    )

    execute_claude(gate_type, args, _paths(tmp_path), "review", prepared)  # type: ignore[arg-type]

    observed = json.loads(record.read_text(encoding="utf-8"))
    argv = observed["argv"]
    assert _flag(argv, "--tools") == "Read,Grep,Glob,Bash"
    assert _flag(argv, "--allowedTools") == "Read,Grep,Glob"
    assert _flag(argv, "--permission-mode") == "auto"
    assert _flag(argv, "--max-turns") == "500"
    assert _flag(argv, "--max-budget-usd") == "50.00"
    assert observed["env"] == {
        "BASH_DEFAULT_TIMEOUT_MS": "600000",
        "BASH_MAX_TIMEOUT_MS": "900000",
        "HEDDLE_AGENT_SESSION": "gate",
        "CLAUDECODE": None,
    }


def test_gate_timeouts_keep_commands_inside_the_inactivity_window() -> None:
    assert GATE_BASH_DEFAULT_TIMEOUT_S <= GATE_BASH_MAX_TIMEOUT_S
    assert GATE_BASH_MAX_TIMEOUT_S < DEFAULT_GATE_INACTIVITY_TIMEOUT_S
    assert DEFAULT_GATE_INACTIVITY_TIMEOUT_S < DEFAULT_SESSION_HARD_TIMEOUT_S
    args = GateArgs(gate_name="spec-review")
    assert args.inactivity_timeout_seconds == DEFAULT_GATE_INACTIVITY_TIMEOUT_S
    assert args.timeout_seconds == DEFAULT_SESSION_HARD_TIMEOUT_S


def test_public_claude_gate_records_auto_and_reads_the_retired_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=OPUS)},
    )
    calls = provider_transport(monkeypatch, review_content())

    code, response = command(capsys, "run-gate", "spec-review")

    assert code == 0 and response["data"]["accepted"], response
    launched = list(calls[0][2])
    assert _flag(launched, "--permission-mode") == "auto"
    assert _flag(launched, "--tools") == "Read,Grep,Glob,Bash"
    state = yaml.safe_load(path.read_text(encoding="utf-8"))
    run = state["gates"][-1]["runs"][-1]
    assert run["execution_config"]["sandbox"] == "auto"
    closure = review_status(path)

    artifact = path.parent / run["artifact"]
    original = artifact.read_bytes()
    result = json.loads(original)
    assert original == _canonical(result)
    result["invocation"]["execution"]["sandbox"] = "read-only-tools"
    historical = _canonical(result)
    old_digest = hashlib.sha256(original).hexdigest()
    new_digest = hashlib.sha256(historical).hexdigest()
    artifact.unlink()
    artifact.with_name(artifact.name.replace(old_digest, new_digest)).write_bytes(
        historical
    )
    path.write_text(
        yaml.safe_dump(_retire_auto(state, old_digest, new_digest), sort_keys=False),
        encoding="utf-8",
    )

    assert review_status(path) == closure


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _retire_auto(value: object, old_digest: str, new_digest: str) -> object:
    if isinstance(value, dict):
        return {
            key: "read-only-tools"
            if key == "sandbox" and item == "auto"
            else _retire_auto(item, old_digest, new_digest)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_retire_auto(item, old_digest, new_digest) for item in value]
    if isinstance(value, str):
        return value.replace(old_digest, new_digest)
    return value
