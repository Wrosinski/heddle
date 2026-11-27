"""The Antigravity lane runner: one headless ``agy`` launch per review.

The runner owns its transport files and launch token in the run temp
directory. In order, it:

1. writes the launch message, the chunks and their index, and the
   ``$ref``-free schema copy (``write_transport``);
2. records the launch token;
3. launches ``agy`` under the gate marker with that fresh token, in the
   checkout root, through the shared monitor, which re-verifies the
   transport against preparation's frozen route immediately before the
   spawn;
4. reads the stream with bounds and classifies the outcome through one table
   (``classify_outcome``);
5. writes the selected response redacted, reaps the token and replaces the raw
   stream with its redacted copy (``finish_launch``). A stream that cannot be
   redacted fails the run and stays in the run temp directory, named by no
   artifact, for the next recovery pass.

Every hard failure sets a failure reason; ``response_warning`` is never set.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from heddle.contracts.gate_execution import GATE_CLI_BINARIES
from heddle.gate.cli import GateArgs
from heddle.gate.io import (
    GatePaths,
    append_log,
    finish_launch,
    redact_bytes,
    write_launch_record,
)
from heddle.gate.results import (
    MAX_EVENT_BYTES,
    MAX_REVIEW_BYTES,
    MAX_STREAM_BYTES,
    canonical_json,
    strict_json,
)
from heddle.gate.runners.environment import gate_agent_env
from heddle.gate.types import (
    AgyVersion,
    ExecutionResult,
    FailureReason,
    PreparedGateRun,
)
from heddle.io.process import (
    LAUNCH_TOKEN_ENV,
    MonitorConfig,
    MonitorResult,
    Termination,
    new_launch_token,
    run_bounded,
    run_monitored,
)

# The agy release this lane is tested against. Its flags, stream shapes and
# limits were measured on 1.5.16 and are confirmed on this release live.
TESTED_AGY_VERSION = "1.5.18"
VERSION_PROBE_TIMEOUT_S = 30.0
LAUNCH_MESSAGE_NAME = "launch.ndjson"
CHUNK_INDEX_NAME = "chunk-index.json"
SCHEMA_NAME = "review-schema.agy.json"
RESPONSE_CHANNEL = "antigravity.structured_output"
_VERSION = re.compile(r"\d+\.\d+\.\d+")
_QUOTA = re.compile(r"quota|exhaust", re.IGNORECASE)
_KEPT_DIAGNOSTIC_CHARS = 4096
_SKIP_BYTES = 1 << 20


def agy_data_dir() -> Path:
    """agy's own data directory: never read, and denied to the reviewer."""
    return Path.home() / ".gemini" / "antigravity-cli"


class TransportChanged(Exception):
    """A transport file differs from the frozen plan before launch."""


class AgyRunFailed(Exception):
    """A run that failed after ``agy --version`` was probed, by an I/O error
    or a changed transport file. It keeps the probed version, so the failure
    summary records it as a successful run's does. ``stream_kept`` holds once
    the launch token is recorded: the raw stream is then the launch's own,
    redacted or withheld by its cleanup, and no failure message replaces it."""

    def __init__(
        self, message: str, agy_version: AgyVersion, *, stream_kept: bool = False
    ) -> None:
        super().__init__(message)
        self.agy_version = agy_version
        self.stream_kept = stream_kept


# --------------------------------------------------------------------------- #
# Transport: planned (pure), written, re-verified
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Transport:
    """Every transport file of one launch with the bytes it must hold."""

    files: Mapping[Path, bytes]
    launch_message: Path
    schema: Path


def resolve_schema_refs(schema: Mapping[str, Any]) -> dict[str, Any]:
    """``schema`` with every local ``#/$defs/`` reference inlined, sibling
    keywords overriding the target's, and ``$defs`` dropped. Any other
    reference, or a reference cycle, is refused."""
    definitions = schema.get("$defs", {})

    def walk(value: Any, active: tuple[str, ...]) -> Any:
        if isinstance(value, list):
            return [walk(item, active) for item in value]
        if not isinstance(value, Mapping):
            return value
        reference = value.get("$ref")
        if reference is None:
            return {
                key: walk(item, active) for key, item in value.items() if key != "$defs"
            }
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise ValueError(f"unsupported schema reference {reference!r}")
        name = reference.removeprefix("#/$defs/")
        if name in active:
            raise ValueError(f"schema reference cycle through {name!r}")
        if name not in definitions:
            raise ValueError(f"schema reference to a missing definition {name!r}")
        target = walk(definitions[name], (*active, name))
        siblings = {
            key: walk(item, active)
            for key, item in value.items()
            if key not in {"$ref", "$defs"}
        }
        return {**target, **siblings}

    resolved = walk(schema, ())
    assert isinstance(resolved, dict)
    return resolved


def _launch_event(content: str) -> bytes:
    event = {"event": "user", "message": {"role": "user", "content": content}}
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


def _pointer(index: Path) -> str:
    """The chunked launch message: the index path alone on its line, the lane
    block's words for the files, and the chunk-reading rule itself, since the
    lane block arrives in the last chunk."""
    return "\n".join(
        (
            "Your review input arrives as files. The chunk index is:",
            "",
            str(index),
            "",
            "Read captured input that arrives as files, the chunk index this "
            "launch message names and the chunks that index lists, only with "
            "`view_file` (recorded as `read_file`), never through shell "
            "commands, listing or search tools. Read every chunk whole and in "
            "index order: a chunk left unread fails the review. Your "
            "instructions continue in those chunks; follow them once every "
            "chunk is read.",
            "",
        )
    )


def plan_transport(temp_dir: Path, prepared: PreparedGateRun) -> Transport:
    """The transport files for ``prepared``'s frozen route, as path → bytes.

    Chunks are the frozen route's slices of the frozen capture; a slice whose
    digest differs from the frozen one is refused before any file is written.
    """
    route = prepared.antigravity_route
    if route is None or route.kind == "refused":
        raise ValueError("the prepared Antigravity run has no deliverable route")
    if prepared.output_contract is None:
        raise ValueError("the prepared Antigravity run has no output contract")
    capture = prepared.transport.stdin.encode("utf-8")
    if route.size != len(capture):
        raise ValueError("the frozen route's size differs from the frozen capture")
    files: dict[Path, bytes] = {}
    message = temp_dir / LAUNCH_MESSAGE_NAME
    if route.kind == "inline":
        files[message] = _launch_event(prepared.transport.stdin)
    else:
        width = max(3, len(str(len(route.chunks))))
        entries = []
        for number, chunk in enumerate(route.chunks, start=1):
            piece = capture[chunk.start : chunk.end]
            if hashlib.sha256(piece).hexdigest() != chunk.sha256:
                raise ValueError(
                    f"chunk {number} of the frozen route differs from its digest"
                )
            path = temp_dir / f"chunk-{number:0{width}d}.txt"
            files[path] = piece
            entries.append(
                {
                    "path": str(path),
                    "start": chunk.start,
                    "end": chunk.end,
                    "sha256": chunk.sha256,
                }
            )
        index = temp_dir / CHUNK_INDEX_NAME
        files[index] = (
            json.dumps({"chunks": entries}, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        files[message] = _launch_event(_pointer(index))
    schema = temp_dir / SCHEMA_NAME
    files[schema] = (
        json.dumps(
            resolve_schema_refs(json.loads(prepared.output_contract.schema_json)),
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return Transport(files=files, launch_message=message, schema=schema)


def write_transport(transport: Transport) -> None:
    """Write every transport file; re-verification follows separately."""
    for path, data in transport.files.items():
        path.write_bytes(data)


def file_changes(files: Mapping[Path, bytes]) -> tuple[str, ...]:
    """One description per file that no longer holds its expected bytes."""
    changes = []
    for path, data in files.items():
        expected = hashlib.sha256(data).hexdigest()
        try:
            found = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            changes.append(f"{path} is unreadable: {error}")
            continue
        if found != expected:
            changes.append(f"{path} changed: sha256 {found}, expected {expected}")
    return tuple(changes)


# --------------------------------------------------------------------------- #
# Version, command line
# --------------------------------------------------------------------------- #
def probe_agy_version(cli_bin: str, env: Mapping[str, str]) -> AgyVersion:
    """Run ``agy --version``; any failure reads as an unavailable version."""
    try:
        done = run_bounded(
            [cli_bin, "--version"], timeout_s=VERSION_PROBE_TIMEOUT_S, env=env
        )
    except (OSError, subprocess.SubprocessError):
        return AgyVersion("unavailable", TESTED_AGY_VERSION, None)
    found = _VERSION.search(done.stdout) if done.returncode == 0 else None
    if found is None:
        return AgyVersion("unavailable", TESTED_AGY_VERSION, None)
    reported = found.group(0)
    state: Literal["matching", "differing"] = (
        "matching" if reported == TESTED_AGY_VERSION else "differing"
    )
    return AgyVersion(state, TESTED_AGY_VERSION, reported)


def _log_version(paths: GatePaths, version: AgyVersion) -> None:
    if version.state == "matching":
        append_log(
            paths.log, f"AgyVersion: {version.reported} (tested {version.tested})"
        )
    elif version.state == "differing":
        append_log(
            paths.log,
            f"AgyVersionWarning: agy reports version {version.reported}; this "
            f"lane was tested on {version.tested}",
        )
    else:
        append_log(
            paths.log,
            "AgyVersionWarning: the agy version is unknown (agy --version "
            f"reported none); this lane was tested on {version.tested}",
        )


def build_command(cli_bin: str, prepared: PreparedGateRun, schema: Path) -> list[str]:
    config = prepared.invocation.exec_config
    settings = {"permissions": {"deny": [f"read_file({agy_data_dir()})"]}}
    return [
        cli_bin,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
        config.model,
        "--effort",
        config.reasoning_effort,
        # The lane's sandbox label is agy's own permission flag.
        f"--{config.sandbox}",
        "--disable-slash-commands",
        "--settings",
        json.dumps(settings, separators=(",", ":")),
        "--json-schema",
        str(schema),
    ]


# --------------------------------------------------------------------------- #
# Stream reading (bounded) and outcome classification (pure)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AgyStream:
    """The facts read from one launch's stream.

    Every parseable event is kept even when another line is unparseable, so a
    terminal fault on a lower row of the outcome table never hides a higher
    one. ``faults`` names each transport fault.
    """

    events: tuple[dict[str, Any], ...] = ()
    agy_errors: tuple[str, ...] = ()
    model_refused: bool = False
    faults: tuple[str, ...] = ()

    @property
    def init_models(self) -> tuple[str | None, ...]:
        """The model each ``init`` event names; None where it names none, as a
        blank or non-text value does not."""
        return tuple(
            _model_name(body.get("model") if isinstance(body, dict) else None)
            for event in self.events
            if event.get("event") == "init"
            for body in (event.get("init"),)
        )

    @property
    def results(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            body if isinstance(body, dict) else {}
            for event in self.events
            if event.get("event") == "result"
            for body in (event.get("result"),)
        )


def _model_name(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def read_stream(path: Path) -> AgyStream:
    """Read the merged stdout and stderr stream within the shared bounds:
    ``MAX_EVENT_BYTES`` per line and ``MAX_STREAM_BYTES`` in all.

    A line shaped as JSON (opening with ``{``, ``[`` or ``"``), or one that
    parses as JSON, must be one event object; any other is an unparseable
    line. Plain-text lines are diagnostics, of which only ``AGY_ERROR`` lines
    and model refusals are facts.
    """
    events: list[dict[str, Any]] = []
    agy_errors: list[str] = []
    model_refused = False
    faults: list[str] = []
    if not path.exists():
        return AgyStream()
    total = 0
    with path.open("rb") as stream:
        while line := stream.readline(MAX_EVENT_BYTES + 1):
            total += len(line)
            oversized = len(line) > MAX_EVENT_BYTES
            if oversized:
                faults.append(f"a line exceeds the {MAX_EVENT_BYTES}-byte limit")
                rest = line
                while not rest.endswith(b"\n") and total <= MAX_STREAM_BYTES:
                    rest = stream.readline(_SKIP_BYTES)
                    if not rest:
                        break
                    total += len(rest)
            if total > MAX_STREAM_BYTES:
                faults.append(f"the stream exceeds the {MAX_STREAM_BYTES}-byte limit")
                break
            if oversized or not line.strip():
                continue
            try:
                text = line.decode("utf-8").strip()
            except UnicodeDecodeError:
                faults.append("a line is not UTF-8")
                continue
            if text.startswith(("{", "[", '"')):
                try:
                    events.append(strict_json(line, maximum=MAX_EVENT_BYTES))
                except ValueError as error:
                    faults.append(f"an unparseable line: {error}")
            elif text.startswith("AGY_ERROR:"):
                agy_errors.append(text[:_KEPT_DIAGNOSTIC_CHARS])
            elif "invalid model selection" in text:
                model_refused = True
            elif _is_json(text):
                faults.append("an unparseable line: a JSON value that is not an event")
    return AgyStream(
        events=tuple(events),
        agy_errors=tuple(agy_errors),
        model_refused=model_refused,
        faults=tuple(faults),
    )


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Outcome:
    reason: FailureReason
    detail: str


def _is_quota(line: str) -> bool:
    try:
        payload = json.loads(line.removeprefix("AGY_ERROR:"))
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("status") == "RESOURCE_EXHAUSTED":
        return True
    return _QUOTA.search(line) is not None


def classify_outcome(
    monitor: MonitorResult,
    stream: AgyStream,
    *,
    model: str,
    violations: Collection[FailureReason] = (),
    uncovered: bool = False,
) -> Outcome | None:
    """The lane's outcome table: the first matching row wins, None is success.

    ``violations`` holds the stream audit's out-of-scope, unapproved-tool and
    compaction findings, and ``uncovered`` its coverage verdict; this function
    alone orders them against the process, provider and terminal facts.
    Canonical validation of a successful object follows downstream.
    """
    results = stream.results
    denied = [
        result["denied_actions"] for result in results if result.get("denied_actions")
    ]
    # Row 1. The only deny rule is on agy's data directory, so a denial is a
    # forbidden read.
    if FailureReason.OUT_OF_SCOPE_ACCESS in violations or denied:
        detail = f"denied actions {denied}" if denied else "audited access"
        return Outcome(FailureReason.OUT_OF_SCOPE_ACCESS, detail)
    # Row 2.
    if FailureReason.ANTIGRAVITY_UNAPPROVED_TOOL in violations:
        return Outcome(FailureReason.ANTIGRAVITY_UNAPPROVED_TOOL, "audited tool")
    # Row 3. Only a named model counts; a blank one is a missing identity.
    others = [
        reported
        for reported in stream.init_models
        if reported is not None and reported != model
    ]
    if others:
        return Outcome(
            FailureReason.MODEL_SUBSTITUTED,
            f"init reported {others[0]!r}, launched {model!r}",
        )
    # Row 4.
    if FailureReason.ANTIGRAVITY_CONTEXT_COMPACTED in violations:
        return Outcome(FailureReason.ANTIGRAVITY_CONTEXT_COMPACTED, "checkpoint step")
    # Row 5: process, provider and terminal failures.
    if monitor.termination is Termination.TIMEOUT:
        return Outcome(FailureReason.TIMEOUT, "the monitor's hard timeout ended it")
    if monitor.termination is Termination.INACTIVITY:
        return Outcome(
            FailureReason.INACTIVITY_TIMEOUT,
            f"no stream activity for {monitor.inactivity_seconds} s",
        )
    if stream.agy_errors:
        quota = [line for line in stream.agy_errors if _is_quota(line)]
        if quota:
            return Outcome(FailureReason.ANTIGRAVITY_QUOTA, quota[0])
        return Outcome(FailureReason.ANTIGRAVITY_API_ERROR, stream.agy_errors[0])
    if stream.model_refused:
        return Outcome(FailureReason.UNSUPPORTED_MODEL, "invalid model selection")
    if monitor.exit_code != 0:
        return Outcome(FailureReason.NONZERO_EXIT, f"agy exited {monitor.exit_code}")
    if stream.faults:
        return Outcome(FailureReason.TRANSPORT_ERROR, stream.faults[0])
    if len(results) != 1:
        return Outcome(
            FailureReason.TRANSPORT_ERROR, f"{len(results)} result events, not one"
        )
    if model not in stream.init_models:
        return Outcome(
            FailureReason.TRANSPORT_ERROR, "no init event carries the launched model"
        )
    (result,) = results
    finished = result.get("finished_reason")
    if finished == "print_timeout":
        return Outcome(FailureReason.TIMEOUT, "finished_reason print_timeout")
    if finished:
        return Outcome(FailureReason.EXECUTION_FAILURE, f"finished_reason {finished}")
    if result.get("status") != "SUCCESS":
        return Outcome(
            FailureReason.EXECUTION_FAILURE, f"status {result.get('status')!r}"
        )
    # Row 6.
    if uncovered:
        return Outcome(
            FailureReason.ANTIGRAVITY_INCOMPLETE_COVERAGE,
            "a chunk has no credited read",
        )
    # Row 7.
    if not isinstance(result.get("structured_output"), dict):
        return Outcome(FailureReason.EMPTY_OUTPUT, "no structured object")
    return None


# --------------------------------------------------------------------------- #
# The launch
# --------------------------------------------------------------------------- #
def execute_antigravity(
    args: GateArgs,
    paths: GatePaths,
    prepared: PreparedGateRun,
    *,
    checkout_root: Path,
) -> ExecutionResult:
    """Launch ``agy`` once for ``prepared`` and classify what it returned.
    Once the version is probed, a failure is raised as ``AgyRunFailed``;
    after the launch token is recorded, ``_launch`` raises it itself."""
    cli_bin = args.cli_bin or GATE_CLI_BINARIES["antigravity"]
    environment = gate_agent_env()
    version = probe_agy_version(cli_bin, environment)
    _log_version(paths, version)
    try:
        return _launch(
            args,
            paths,
            prepared,
            checkout_root=checkout_root,
            cli_bin=cli_bin,
            environment=environment,
            version=version,
        )
    except (OSError, TransportChanged) as error:
        raise AgyRunFailed(str(error), version) from error


def _launch(
    args: GateArgs,
    paths: GatePaths,
    prepared: PreparedGateRun,
    *,
    checkout_root: Path,
    cli_bin: str,
    environment: dict[str, str],
    version: AgyVersion,
) -> ExecutionResult:
    config = prepared.invocation.exec_config
    transport = plan_transport(paths.temp_dir, prepared)
    write_transport(transport)
    token = new_launch_token()
    record = write_launch_record(paths.temp_dir, token=token, raw_stream=paths.raw_out)
    command = build_command(cli_bin, prepared, transport.schema)
    append_log(paths.log, f"AgyCmd: {shlex.join(command)}")
    monitor_config = MonitorConfig(
        hard_timeout_s=args.timeout_seconds,
        inactivity_timeout_s=args.inactivity_timeout_seconds,
        poll_s=args.poll_seconds,
        term_grace_s=args.term_grace_seconds,
    )
    # The run temp directory's copies of the frozen capture are re-verified
    # with the transport, as the Codex lane checks its captured prompt.
    capture = prepared.transport.stdin.encode("utf-8")
    verified = {**transport.files, paths.prompt: capture, paths.command_output: capture}

    def verify_before_spawn() -> None:
        # Immediately before the spawn: a change after it is the post-run
        # digest check's.
        changed = file_changes(verified)
        if changed:
            for change in changed:
                append_log(paths.log, f"TransportWarning: {change}")
            raise TransportChanged(
                "a transport file changed after it was written, so agy was not "
                f"launched: {'; '.join(changed)}"
            )

    try:
        monitor = run_monitored(
            command,
            paths.raw_out,
            monitor_config,
            stdin_source=transport.launch_message,
            env={**environment, LAUNCH_TOKEN_ENV: token},
            cwd=checkout_root,
            before_spawn=verify_before_spawn,
        )
        stream = read_stream(paths.raw_out)
        # The post-run digest check binds credited reads to the frozen bytes.
        for change in file_changes(transport.files):
            append_log(paths.log, f"TransportWarning: during the run, {change}")
        outcome = classify_outcome(monitor, stream, model=config.model)
        failure: FailureReason | None
        if outcome is None:
            failure = _write_response(paths, stream.results[0]["structured_output"])
        else:
            failure = outcome.reason
            append_log(paths.log, f"AgyOutcome: {outcome.reason} ({outcome.detail})")
    except (OSError, TransportChanged) as error:
        raise AgyRunFailed(str(error), version, stream_kept=True) from error
    finally:
        cleanup = finish_launch(paths.temp_dir, record, grace_s=args.term_grace_seconds)
        for warning in cleanup.warnings:
            append_log(paths.log, f"CleanupWarning: {warning}")
        if not cleanup.redacted:
            # Only verified redacted bytes leave the run temp directory: the
            # raw stream stays there for the next recovery pass, its launch
            # record says so, and no artifact names it (``unredacted_stream``).
            append_log(
                paths.log,
                "ExecutionFailure: the raw stream could not be redacted, so it "
                "is not kept or handed on",
            )
    if not cleanup.redacted:
        failure = failure or FailureReason.EXECUTION_FAILURE
    return ExecutionResult(
        monitor=monitor,
        cli_exit=monitor.exit_code,
        failure_reason=failure,
        raw_out_path=paths.raw_out,
        msg_out_path=paths.last_message,
        filtered_jsonl_path=None,
        codex_diagnostics=None,
        json_message_path=None,
        response_channel=RESPONSE_CHANNEL,
        agy_version=version,
    )


def _write_response(paths: GatePaths, value: dict[str, Any]) -> FailureReason | None:
    """Write the selected object canonically and redacted, for validation."""
    try:
        raw = canonical_json(value, newline=True)
    except (ValueError, TypeError, RecursionError) as error:
        append_log(paths.log, f"NativeContentError: {error}")
        return FailureReason.INVALID_OUTPUT_STRUCTURE
    if len(raw) > MAX_REVIEW_BYTES:
        append_log(paths.log, "NativeContentError: review content exceeds byte limit")
        return FailureReason.INVALID_OUTPUT_STRUCTURE
    paths.last_message.write_bytes(redact_bytes(raw))
    return None
