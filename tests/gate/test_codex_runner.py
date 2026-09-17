"""Tests for Codex gate runner integration."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


def _gate_paths(tmp_path: Path):
    from heddle.gate.io import GatePaths

    temp_dir = tmp_path / "tmp"
    return GatePaths(
        output=tmp_path / "out.md",
        log=tmp_path / "out.log",
        summary=tmp_path / "out.gate-summary.json",
        prompt=temp_dir / "prompt.md",
        raw_out=temp_dir / "raw.out",
        last_message=temp_dir / "last-message.txt",
        json_message=temp_dir / "json-message.txt",
        filtered_jsonl=temp_dir / "events.jsonl",
        command_output=temp_dir / "command-output.txt",
        temp_dir=temp_dir,
    )


@REQUIRES_IMPL
def test_gate_agent_env_marks_managed_reviewer_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.gate.runners.environment import gate_agent_env

    monkeypatch.setenv("EXISTING_ENV_VALUE", "kept")
    monkeypatch.delenv("PRIVATE_APP_SKIP_AGENT_STOP_LINT", raising=False)

    env = gate_agent_env()

    assert env["HEDDLE_AGENT_SESSION"] == "gate"
    assert "PRIVATE_APP_SKIP_AGENT_STOP_LINT" not in env
    assert env["EXISTING_ENV_VALUE"] == "kept"
    assert os.environ["EXISTING_ENV_VALUE"] == "kept"


@REQUIRES_IMPL
@pytest.mark.parametrize(
    ("model", "effort", "expected_model", "expected_effort"),
    (("gpt-test", "low", "gpt-test", "low"),),
)
def test_codex_command_pins_effective_model_and_effort(
    tmp_path: Path,
    model: str | None,
    effort: str | None,
    expected_model: str,
    expected_effort: str,
) -> None:
    from heddle.gate.cli import GateArgs, resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.runners.codex import _codex_command
    from heddle.gate.types import GateInvocationOverrides

    args = GateArgs(
        gate_name="spec-review",
        cli="codex",
        cli_bin="codex-test",
        workdir=tmp_path,
    )
    invocation = resolve_gate_execution(
        GATES["spec-review"],
        GateInvocationOverrides(
            cli="codex",
            model=model,
            reasoning_effort=effort,
        ),
    )
    prepared = SimpleNamespace(
        invocation=invocation,
        output_contract=GATES["spec-review"].output_contract,
    )
    command = _codex_command(args, _gate_paths(tmp_path), prepared)

    assert command[0:2] == ["codex-test", "exec"]
    assert command[command.index("-m") + 1] == expected_model
    assert (
        command[command.index("-c") + 1]
        == f'model_reasoning_effort="{expected_effort}"'
    )


@pytest.mark.parametrize("length", [1_048_575, 1_048_576, 1_048_577])
def test_codex_large_input_preserves_exact_capture_at_character_boundary(
    tmp_path: Path, length: int
) -> None:
    import hashlib
    import json

    from heddle.gate.runners.codex import _codex_stdin
    from heddle.gate.types import PromptTransport

    paths = _gate_paths(tmp_path / "host with spaces")
    paths.temp_dir.mkdir(parents=True)
    text = "é" * (length - 4) + "TAIL"
    raw = text.encode("utf-8")
    paths.prompt.write_bytes(raw)
    prepared = SimpleNamespace(
        transport=PromptTransport(system=None, stdin=text), diff_text=""
    )
    source = _codex_stdin(paths, prepared)
    assert paths.prompt.read_bytes() == raw
    assert prepared.transport.stdin == text
    if length <= 1_048_576:
        assert source == paths.prompt
        assert not (paths.temp_dir / "launch.md").exists()
        assert not (paths.temp_dir / "input-index.json").exists()
        return
    launch = source.read_text()
    assert len(launch) < 4096
    manifest = json.loads(launch.splitlines()[1])
    assert Path(manifest["input_file"]).read_bytes() == raw
    assert manifest["bytes"] == len(raw)
    assert manifest["sha256"] == hashlib.sha256(raw).hexdigest()
    index_bytes = Path(manifest["index_file"]).read_bytes()
    assert len(index_bytes) == manifest["index_bytes"]
    assert hashlib.sha256(index_bytes).hexdigest() == manifest["index_sha256"]
    assert "completely in bounded chunks" in launch
    assert "truncated tool response is not complete coverage" in launch
    assert "captured-file/v2" in paths.log.read_text()


@pytest.mark.parametrize(
    "mutation", ["missing", "changed", "existing-launch", "existing-index"]
)
def test_codex_large_input_refuses_bad_capture_or_launch_collision(
    tmp_path: Path, mutation: str
) -> None:
    from heddle.gate.runners.codex import _codex_stdin
    from heddle.gate.types import PromptTransport

    paths = _gate_paths(tmp_path)
    paths.temp_dir.mkdir()
    text = "x" * 1_048_577
    paths.prompt.write_text(text)
    prepared = SimpleNamespace(
        transport=PromptTransport(system=None, stdin=text), diff_text=""
    )
    launch = paths.temp_dir / "launch.md"
    index = paths.temp_dir / "input-index.json"
    if mutation == "missing":
        paths.prompt.unlink()
    elif mutation == "changed":
        paths.prompt.write_text("different capture")
    elif mutation == "existing-launch":
        launch.write_text("another writer")
    else:
        index.write_text("another index writer")
    expected = {
        "missing": FileNotFoundError,
        "changed": ValueError,
        "existing-launch": FileExistsError,
        "existing-index": FileExistsError,
    }[mutation]
    with pytest.raises(expected):
        _codex_stdin(paths, prepared)
    if mutation == "existing-launch":
        assert launch.read_text() == "another writer"
    if mutation == "existing-index":
        assert index.read_text() == "another index writer" and not launch.exists()


@pytest.mark.parametrize("tamper", ["none", "capture", "index", "missing-index"])
def test_codex_large_input_real_process_reads_capture_and_tamper_refuses(
    tmp_path: Path, tamper: str
) -> None:
    import json
    import sys

    from heddle.gate.cli import GateArgs, resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.runners.codex import execute_codex
    from heddle.gate.types import GateInvocationOverrides, PromptTransport

    paths = _gate_paths(tmp_path / "host with spaces")
    paths.temp_dir.mkdir(parents=True)
    # One oversized line exposes tools that accidentally rely on line counts.
    text = "instructions\n" + "x" * 1_048_577 + "\nTAIL\n"
    paths.prompt.write_text(text)
    prepared = SimpleNamespace(
        output_contract=GATES["spec-review"].output_contract,
        invocation=resolve_gate_execution(
            GATES["spec-review"], GateInvocationOverrides(cli="codex")
        ),
        transport=PromptTransport(system=None, stdin=text),
        diff_text="",
    )
    shim = tmp_path / "local-provider"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import hashlib,json,sys\nfrom pathlib import Path\n"
        "launch=sys.stdin.read()\nassert len(launch)<4096\n"
        "record=json.loads(launch.splitlines()[1])\n"
        "source=Path(record['input_file'])\n"
        "raw=source.read_bytes()\nassert len(raw)==record['bytes']\n"
        "assert hashlib.sha256(raw).hexdigest()==record['sha256']\n"
        "assert raw.startswith(b'instructions\\n') and raw.endswith(b'\\nTAIL\\n')\n"
        "index_path=Path(record['index_file'])\n"
        "encoded=index_path.read_bytes()\n"
        "assert len(encoded)==record['index_bytes']\n"
        "assert hashlib.sha256(encoded).hexdigest()==record['index_sha256']\n"
        "index=json.loads(encoded)\n"
        "assert index['bytes']==len(raw) and index['sha256']==record['sha256']\n"
        "assert sorted(index['read_order'])==list(range(len(index['ranges'])))\n"
        "parts={}\n"
        "for i in index['read_order']:\n"
        " row=index['ranges'][i]\n"
        " parts[i]=raw[row['start']:row['end']]\n"
        " assert parts[i].decode().encode()==parts[i]\n"
        "assert b''.join(parts[i] for i in sorted(parts))==raw\n"
        "Path(sys.argv[sys.argv.index('-o')+1]).write_text('{}')\n"
        + {
            "none": "",
            "capture": "source.write_text('tampered')\n",
            "index": "index_path.write_text('tampered')\n",
            "missing-index": "index_path.unlink()\n",
        }[tamper]
        + "print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':'{}'}}))\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    shim.chmod(0o755)
    args = GateArgs(
        gate_name="spec-review",
        cli="codex",
        cli_bin=str(shim),
        workdir=tmp_path,
        timeout_seconds=5,
        inactivity_timeout_seconds=3,
        poll_seconds=0.02,
        term_grace_seconds=0.1,
    )
    if tamper != "none":
        expected = FileNotFoundError if tamper == "missing-index" else ValueError
        message = "input-index" if tamper == "missing-index" else "captured Codex"
        with pytest.raises(expected, match=message):
            execute_codex(args, paths, prepared)
    else:
        result = execute_codex(args, paths, prepared)
        assert result.cli_exit == 0 and result.failure_reason is None
        assert paths.prompt.read_text() == text
        assert json.loads(paths.last_message.read_text()) == {}
