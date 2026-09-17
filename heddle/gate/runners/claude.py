from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from heddle.gate.cli import GateArgs
from heddle.gate.io import GatePaths, append_log
from heddle.gate.results import (
    MAX_EVENT_BYTES,
    MAX_REVIEW_BYTES,
    MAX_STREAM_BYTES,
    TruncatedReviewError,
    canonical_json,
)
from heddle.gate.runners.environment import (
    GATE_AGENT_SESSION,
    HEDDLE_AGENT_SESSION_ENV,
)
from heddle.gate.runners.structured import read_native_stream
from heddle.gate.types import ExecutionResult, FailureReason, GateType, PreparedGateRun
from heddle.io.claude_events import last_result_event
from heddle.io.process import MonitorConfig, MonitorResult, Termination, run_monitored

CLAUDE_MAX_TURNS = "100"
CLAUDE_MAX_BUDGET_USD = "15.00"


def classify_claude(
    monitor: MonitorResult,
    raw_out: Path,
    events: list[dict[str, Any]],
) -> FailureReason | None:
    content = _diagnostic_prefix(raw_out)
    if (
        monitor.termination is Termination.INACTIVITY or monitor.exit_code != 0
    ) and re.search(r'overloaded_error|"error_status":529', content):
        return FailureReason.OVERLOADED
    if monitor.termination is Termination.INACTIVITY:
        return FailureReason.INACTIVITY_TIMEOUT
    if monitor.termination is Termination.TIMEOUT:
        return FailureReason.TIMEOUT
    if monitor.exit_code != 0:
        if re.search(r'"error_max_budget"|"max_budget"', content):
            return FailureReason.BUDGET_EXHAUSTED
        if _model_unsupported(content):
            return FailureReason.UNSUPPORTED_MODEL
        return FailureReason.NONZERO_EXIT
    result_event = last_result_event(events)
    if result_event:
        subtype = str(result_event.get("subtype", ""))
        if subtype == "error_max_budget":
            return FailureReason.BUDGET_EXHAUSTED
        if subtype == "error_max_turns":
            return FailureReason.MAX_TURNS
        if subtype == "error_during_execution":
            return FailureReason.EXECUTION_FAILURE
    return None


def _model_unsupported(content: str) -> bool:
    return bool(
        re.search(
            r"unsupported model|model (?:is )?not supported|invalid model"
            r"|model\s+\S+\s+is not available(?:\s+for this account)?"
            r"|(?:do not|don't) have access to (?:the )?model",
            content,
            re.IGNORECASE,
        )
    )


def execute_claude(
    gate_type: GateType,
    args: GateArgs,
    paths: GatePaths,
    prompt_text: str,
    prepared: PreparedGateRun,
) -> ExecutionResult:
    _ = prompt_text
    execution_config = prepared.invocation.exec_config
    system_prompt = prepared.transport.system
    if system_prompt is None:
        raise ValueError("prepared Claude transport requires a system prompt")
    cli_bin = args.cli_bin or "claude"
    allowed_tools = ",".join(gate_type.tool_permissions)
    command = [
        "env",
        "-u",
        "CLAUDECODE",
        f"{HEDDLE_AGENT_SESSION_ENV}={GATE_AGENT_SESSION}",
        f"CLAUDE_CODE_EFFORT_LEVEL={execution_config.reasoning_effort}",
        cli_bin,
        "--print",
        "--input-format",
        "text",
        "--model",
        execution_config.model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--append-system-prompt",
        system_prompt,
        "--tools",
        allowed_tools,
        "--allowedTools",
        allowed_tools,
        "--max-turns",
        CLAUDE_MAX_TURNS,
        "--no-session-persistence",
        "--max-budget-usd",
        CLAUDE_MAX_BUDGET_USD,
    ]
    assert prepared.output_contract is not None
    command.extend(["--json-schema", prepared.output_contract.schema_json])
    append_log(paths.log, f"ClaudeCmd: {shlex.join(command)}")
    config = MonitorConfig(
        hard_timeout_s=args.timeout_seconds,
        inactivity_timeout_s=args.inactivity_timeout_seconds,
        poll_s=args.poll_seconds,
        term_grace_s=args.term_grace_seconds,
    )
    monitor = run_monitored(
        command,
        paths.raw_out,
        config,
        stdin_source=paths.prompt,
        activity_filter=_claude_meaningful_activity,
    )
    failure_reason: FailureReason | None
    response_warning = None
    response_channel = "claude.structured_output"
    try:
        events = read_native_stream(paths.raw_out).events
    except (ValueError, UnicodeError) as error:
        append_log(paths.log, f"NativeStreamError: {error}")
        events = []
        failure_reason = classify_claude(monitor, paths.raw_out, events) or (
            FailureReason.TRUNCATED_OUTPUT
            if isinstance(error, TruncatedReviewError)
            else FailureReason.INVALID_OUTPUT_STRUCTURE
        )
    else:
        failure_reason = classify_claude(monitor, paths.raw_out, events)
        terminals = [event for event in events if event.get("type") == "result"]
        if failure_reason is None:
            if (
                len(terminals) != 1
                or terminals[0].get("subtype") != "success"
                or terminals[0].get("is_error") is not False
            ):
                failure_reason = FailureReason.EXECUTION_FAILURE
            elif not isinstance(terminals[0].get("structured_output"), dict):
                if prepared.assignment_id is not None:
                    response_warning = (
                        "completed provider response has no native object"
                    )
                    response_channel = "claude.result-event"
                    # The exact event stream remains the authority; do not replace
                    # a missing structured channel with unrelated assistant prose.
                else:
                    failure_reason = FailureReason.INVALID_OUTPUT_STRUCTURE
            else:
                try:
                    raw = canonical_json(
                        terminals[0]["structured_output"], newline=True
                    )
                except (ValueError, TypeError, RecursionError) as error:
                    failure_reason = FailureReason.INVALID_OUTPUT_STRUCTURE
                    append_log(paths.log, f"NativeContentError: {error}")
                else:
                    if len(raw) > MAX_REVIEW_BYTES:
                        failure_reason = FailureReason.INVALID_OUTPUT_STRUCTURE
                        append_log(
                            paths.log,
                            "NativeContentError: review content exceeds byte limit",
                        )
                    else:
                        paths.last_message.write_bytes(raw)
    return ExecutionResult(
        monitor=monitor,
        cli_exit=monitor.exit_code,
        failure_reason=failure_reason,
        raw_out_path=paths.raw_out,
        msg_out_path=paths.last_message,
        filtered_jsonl_path=None,
        codex_diagnostics=None,
        json_message_path=None,
        response_warning=response_warning,
        response_channel=response_channel,
    )


def _claude_meaningful_activity(
    raw_out: Path,
    start_offset: int,
    end_offset: int,
) -> bool:
    if end_offset <= start_offset:
        return False
    with raw_out.open("rb") as handle:
        handle.seek(start_offset)
        chunk = handle.read(min(end_offset - start_offset, MAX_EVENT_BYTES))
    text = chunk.decode("utf-8", errors="ignore")
    saw_retry_only = False
    for line in text.splitlines():
        if not line.startswith("{"):
            return True
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return True
        if (
            isinstance(parsed, dict)
            and parsed.get("type") == "system"
            and parsed.get("subtype") == "api_retry"
        ):
            saw_retry_only = True
            continue
        return True
    return not saw_retry_only


def _diagnostic_prefix(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        return stream.read(MAX_STREAM_BYTES).decode("utf-8", errors="replace")
