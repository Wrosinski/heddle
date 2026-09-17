from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from heddle.gate.cli import GateArgs
from heddle.gate.input_navigation import (
    CODEX_INLINE_LIMIT,
    NAVIGATION_POLICY,
    navigation_index,
)
from heddle.gate.io import GatePaths, append_log
from heddle.gate.results import (
    MAX_STREAM_BYTES,
    TruncatedReviewError,
    read_bounded,
)
from heddle.gate.runners.codex_diagnostics import build_bundle
from heddle.gate.runners.environment import gate_agent_env
from heddle.gate.runners.structured import read_native_stream
from heddle.gate.types import ExecutionResult, FailureReason, PreparedGateRun
from heddle.io.process import MonitorConfig, Termination, run_monitored


def _validate_prompt_capture(paths: GatePaths, prepared: PreparedGateRun) -> None:
    if paths.prompt.read_bytes() != prepared.transport.stdin.encode("utf-8"):
        raise ValueError("captured Codex prompt changed; prepare the gate again")


def _validate_input_index(paths: GatePaths, prepared: PreparedGateRun) -> None:
    expected = navigation_index(prepared.transport.stdin, prepared.diff_text)
    if (paths.temp_dir / "input-index.json").read_bytes() != expected:
        raise ValueError("captured Codex input index changed; prepare the gate again")


def _codex_stdin(paths: GatePaths, prepared: PreparedGateRun) -> Path:
    text = prepared.transport.stdin
    if len(text) <= CODEX_INLINE_LIMIT:
        return paths.prompt
    _validate_prompt_capture(paths, prepared)
    captured = text.encode("utf-8")
    identity = hashlib.sha256(captured).hexdigest()
    launch = paths.temp_dir / "launch.md"
    index_path = paths.temp_dir / "input-index.json"
    index = navigation_index(text, prepared.diff_text)
    if launch.exists():
        raise FileExistsError(f"captured Codex launch already exists: {launch}")
    with index_path.open("xb") as handle:
        handle.write(index)
    instructions = (
        "This gate's complete captured review input exceeds the inline transport "
        "limit and is supplied in a local UTF-8 file.\n"
        + json.dumps(
            {
                "input_file": str(paths.prompt.resolve()),
                "bytes": len(captured),
                "sha256": identity,
                "index_file": str(index_path.resolve()),
                "index_bytes": len(index),
                "index_sha256": hashlib.sha256(index).hexdigest(),
            },
            ensure_ascii=True,
        )
        + "\nVerify both bindings, then use the index's ranges and read_order to "
        "navigate the exact captured file; paths and offsets are JSON data. "
        + NAVIGATION_POLICY
        + " Follow the gate instructions and supplied output schema.\n"
    )
    with launch.open("x", encoding="utf-8") as handle:
        handle.write(instructions)
    append_log(
        paths.log,
        f"CodexInputDelivery: captured-file/v2 launch={launch} "
        f"input={paths.prompt} bytes={len(captured)} sha256={identity} "
        f"index={index_path} index_sha256={hashlib.sha256(index).hexdigest()}",
    )
    return launch


@dataclass(frozen=True)
class CodexFacts:
    timed_out: bool
    inactive: bool
    cli_exit: int
    has_tool_events: bool
    has_message_events: bool
    has_turn_completed: bool
    last_message_nonempty: bool
    json_message_nonempty: bool
    model_unsupported: bool
    transport_error: bool


def classify_codex(facts: CodexFacts) -> FailureReason | None:
    if facts.inactive and facts.has_tool_events and not facts.has_message_events:
        return FailureReason.INACTIVITY_NO_FINAL
    if facts.inactive:
        return FailureReason.INACTIVITY_TIMEOUT
    if facts.timed_out:
        return FailureReason.TIMEOUT
    if facts.cli_exit != 0 and facts.model_unsupported:
        return FailureReason.UNSUPPORTED_MODEL
    if facts.cli_exit != 0 and facts.transport_error:
        return FailureReason.TRANSPORT_ERROR
    if facts.cli_exit != 0:
        return FailureReason.NONZERO_EXIT
    if not facts.last_message_nonempty and facts.json_message_nonempty:
        return FailureReason.OUTPUT_LAST_MSG_EMPTY
    if not facts.last_message_nonempty:
        return FailureReason.MISSING_FINAL_MESSAGE
    if not facts.has_message_events:
        return FailureReason.MISSING_MESSAGE_EVENT
    return None


def _codex_command(
    args: GateArgs, paths: GatePaths, prepared: PreparedGateRun
) -> list[str]:
    cli_bin = args.cli_bin or "codex"
    config = prepared.invocation.exec_config
    command = [
        cli_bin,
        "exec",
        "--json",
        "--ephemeral",
        "--sandbox",
        config.sandbox,
        "-o",
        str(paths.last_message),
        "-m",
        config.model,
        "-c",
        f'model_reasoning_effort="{config.reasoning_effort}"',
        "-C",
        str(args.workdir or paths.output.parent.parent),
        "-",
    ]
    command[-1:-1] = ["--output-schema", str(paths.temp_dir / "review-schema.json")]
    return command


def execute_codex(
    args: GateArgs, paths: GatePaths, prepared: PreparedGateRun
) -> ExecutionResult:
    assert prepared.output_contract is not None
    (paths.temp_dir / "review-schema.json").write_text(
        prepared.output_contract.schema_json, encoding="utf-8"
    )
    command = _codex_command(args, paths, prepared)
    config = MonitorConfig(
        hard_timeout_s=args.timeout_seconds,
        inactivity_timeout_s=args.inactivity_timeout_seconds,
        poll_s=args.poll_seconds,
        term_grace_s=args.term_grace_seconds,
    )
    stdin_source = _codex_stdin(paths, prepared)
    if stdin_source != paths.prompt:
        _validate_input_index(paths, prepared)
    monitor = run_monitored(
        command,
        paths.raw_out,
        config,
        stdin_source=stdin_source,
        env=gate_agent_env(),
    )
    if stdin_source != paths.prompt:
        _validate_prompt_capture(paths, prepared)
        _validate_input_index(paths, prepared)
    try:
        parsed = read_native_stream(paths.raw_out)
        if paths.last_message.exists():
            read_bounded(paths.last_message)
    except (ValueError, UnicodeError) as error:
        append_log(paths.log, f"NativeStreamError: {error}")
        failure = (
            FailureReason.TIMEOUT
            if monitor.termination is Termination.TIMEOUT
            else FailureReason.INACTIVITY_TIMEOUT
            if monitor.termination is Termination.INACTIVITY
            else FailureReason.NONZERO_EXIT
            if monitor.exit_code != 0
            else FailureReason.TRUNCATED_OUTPUT
            if isinstance(error, TruncatedReviewError)
            else FailureReason.INVALID_OUTPUT_STRUCTURE
        )
        return ExecutionResult(
            monitor=monitor,
            cli_exit=monitor.exit_code,
            failure_reason=failure,
            raw_out_path=paths.raw_out,
            msg_out_path=paths.last_message,
            filtered_jsonl_path=None,
            codex_diagnostics=None,
            json_message_path=None,
        )
    bundle = build_bundle(
        raw_out=paths.raw_out,
        msg_out=paths.last_message,
        filtered_jsonl=paths.filtered_jsonl,
        checkpoints=monitor.checkpoints,
        launch_time=monitor.launch_time,
        termination_time=monitor.termination_time,
        failed=monitor.exit_code != 0
        or monitor.termination is not Termination.COMPLETED,
        parsed=parsed,
    )
    json_text = "\n".join(
        [
            event["item"]["text"]
            for event in bundle.parse_result.events
            if event.get("type") == "item.completed"
            and isinstance(event.get("item"), dict)
            and event["item"].get("type") in {"agent_message", "assistant_message"}
            and isinstance(event["item"].get("text"), str)
        ]
    )
    paths.json_message.write_text(json_text)
    facts = CodexFacts(
        timed_out=monitor.termination is Termination.TIMEOUT,
        inactive=monitor.termination is Termination.INACTIVITY,
        cli_exit=monitor.exit_code,
        has_tool_events=bundle.event_info.tool_event_count > 0,
        has_message_events=bundle.event_info.message_event_count > 0,
        has_turn_completed=bundle.event_info.turn_completed_count > 0,
        last_message_nonempty=paths.last_message.exists()
        and paths.last_message.stat().st_size > 0,
        json_message_nonempty=paths.json_message.exists()
        and paths.json_message.stat().st_size > 0,
        model_unsupported=_raw_model_unsupported(paths.raw_out),
        transport_error=bundle.event_info.transport_error_count > 0,
    )
    failure_reason = classify_codex(facts)
    response_warning = None
    if failure_reason is None or (
        prepared.assignment_id is not None
        and failure_reason
        in {FailureReason.OUTPUT_LAST_MSG_EMPTY, FailureReason.MISSING_FINAL_MESSAGE}
    ):
        terminals = [
            event
            for event in bundle.parse_result.events
            if event.get("type") in {"turn.completed", "turn.failed"}
        ]
        if len(terminals) != 1 or terminals[0].get("type") != "turn.completed":
            failure_reason = FailureReason.MISSING_TURN_COMPLETED
        elif failure_reason is not None:
            response_warning = failure_reason.value
            failure_reason = None
    return ExecutionResult(
        monitor=monitor,
        cli_exit=monitor.exit_code,
        failure_reason=failure_reason,
        raw_out_path=paths.raw_out,
        msg_out_path=paths.last_message,
        filtered_jsonl_path=paths.filtered_jsonl,
        codex_diagnostics=bundle,
        json_message_path=paths.json_message,
        response_warning=response_warning,
        response_channel="codex.output-last-message",
    )


def _raw_model_unsupported(raw_out: Path) -> bool:
    content = (
        read_bounded(raw_out, MAX_STREAM_BYTES).decode("utf-8")
        if raw_out.exists()
        else ""
    )
    return bool(
        re.search(
            r"model is not supported when using Codex with a ChatGPT account"
            r"|unsupported for this account|unsupported model",
            content,
        )
    )
