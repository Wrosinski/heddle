"""The Antigravity lane's stream audit: a pure function over one launch's events.

``audit_stream`` reads the parsed events in stream order and returns every
violation it finds, each tagged with the outcome-table row it feeds, the
coverage verdict and the redacted audit record. It orders nothing:
``classify_outcome`` in the runner owns precedence.

- **Steps.** A ``subagent`` step is an unapproved tool (row 2); a
  ``checkpoint`` step is compaction (row 4). Any step that names a tool,
  those two included, is also judged as a tool call, whatever its type; one
  that names none, of a type outside the known set, is listed in the record.
- **Tools.** Only the allowlist passes (row 2), and ``manage_task`` only with
  an ``Action`` that carries no input. Every tool's path parameters, allowed
  or not, are checked against the forbidden paths (row 1).
- **Executed text.** ``run_command``'s command line and every string
  parameter of ``send_command_input`` are read as shell commands, through
  ``review_scope``'s walker: for agy's data directory, process inspection,
  recursive searches rooted at ``/``, ``/home`` or the home directory, and
  ``heddle`` calls (row 1), and for the chunks they name.
- **``reviews/`` access** is the lane-neutral predicate's
  (``review_scope.reviews_access``), given each call's context.
- **Result shapes.** ``run_command`` output is shown text. The structured
  search and listing kinds, ``command_status`` and ``send_command_input``
  have no recorded shape, so their results are not inspected and each one
  draws the unrecognized-shape warning; so does a successful
  ``run_command``, ``command_status`` or structured call that shows no
  output. ``run_command`` and ``command_status`` text goes into the record as
  shown.
- **The record.** Every decoded string in it is made encodable (a lone
  surrogate becomes a ``\\uXXXX`` escape) and redacted before anything is
  serialized, so the gate log and the summary are built from text that holds
  no credential and always encodes.
- **Coverage** (row 6). A chunk is covered by a successful whole
  ``read_file`` that no write tool and no executed text targeted first, and
  only while every transport file still matches its frozen digest. A read is
  whole only when every parameter it carries is a path parameter: agy's
  range parameters were never observed, so any other parameter may narrow
  the read.

Each tool step is judged once, by its last update, in the order of its first
appearance. Paths are placed as the ``reviews/`` check places them. File
tools resolve relative paths against the launch directory; a ``run_command``
starts at its ``Cwd`` or the launch directory, a ``cd`` moves it for the
rest of that command, and ``send_command_input`` runs in the shell the
latest ``run_command`` left, moved by the input sent to it before. A
directory this audit cannot follow is unknown, and a relative name there
grounds nothing.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path
from typing import Any, Protocol

from heddle.gate.io import redact_text
from heddle.gate.runners.review_scope import (
    SimpleCommand,
    Word,
    ambiguous_warning,
    directory_after,
    named_words,
    resolve_name,
    reviews_access,
    simple_commands,
    unrecognized_warning,
)
from heddle.gate.types import FailureReason

LANE = "antigravity"
ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "find_by_name",
        "grep_search",
        "run_command",
        "command_status",
        "send_command_input",
        "manage_task",
        "wait",
        "wait_5_seconds",
        "write_to_file",
        "replace_file_content",
        "multi_replace_file_content",
        "sed_file",
        "notebook_edit",
        "notebook_execution",
        "finish",
    }
)
# The manage_task actions that deliver nothing to the task.
INPUTLESS_ACTIONS = frozenset({"status"})
WRITE_TOOLS = frozenset(
    {
        "write_to_file",
        "replace_file_content",
        "multi_replace_file_content",
        "sed_file",
        "notebook_edit",
        "notebook_execution",
    }
)
# Search tools that descend from their root.
RECURSIVE_TOOLS = frozenset({"grep_search", "find_by_name"})
# Kinds whose results show search or listing entries. No live call has
# recorded a result shape for any of them yet, so none is inspected.
STRUCTURED_KINDS = frozenset({"grep_search", "find_by_name", "list_dir"})
# Result-bearing kinds whose results this audit cannot inspect.
UNINSPECTED_KINDS = STRUCTURED_KINDS | {"command_status", "send_command_input"}
# Kinds whose successful call always shows a result, so an omitted one warns.
RESULT_KINDS = STRUCTURED_KINDS | {"run_command", "command_status"}
# Kinds whose shown text the record keeps.
SHOWN_KINDS = frozenset({"run_command", "command_status"})
# The step types agy is known to emit, the reference's list less `unknown`.
KNOWN_STEP_KINDS = frozenset(
    {
        "user_input",
        "agent_response",
        "tool",
        "finish",
        "system_message",
        "checkpoint",
        "subagent",
    }
)
SUCCESS_STATE = "DONE"

ROW_OUT_OF_SCOPE = 1
ROW_UNAPPROVED = 2
ROW_COMPACTED = 4

_PATH_PARAMETER = re.compile(r"(?:path|file|directory|dir|cwd)s?$", re.IGNORECASE)
_PROCESS_PATH = re.compile(r"/proc/(?:self|thread-self|\d+)(?:/|$)")
_PROCESS_TOOLS = frozenset({"ps", "pgrep", "pidof", "pstree", "top", "htop", "lsof"})
_ALWAYS_RECURSIVE = frozenset(
    {"rg", "ag", "ack", "find", "fd", "fdfind", "tree", "rgrep"}
)
_GREPS = frozenset({"grep", "egrep", "fgrep"})
_WRAPPERS = frozenset(
    {"env", "sudo", "nohup", "exec", "command", "builtin", "time", "nice", "xargs"}
)
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON = re.compile(r"python[0-9.]*")


class CaptureRoute(Protocol):
    """The frozen chunk plan, as preparation froze it."""

    @property
    def kind(self) -> str: ...

    @property
    def chunks(self) -> Sequence[Any]: ...


@dataclass(frozen=True)
class AuditViolation:
    reason: FailureReason
    row: int
    step: int | None
    detail: str


@dataclass(frozen=True)
class AuditResult:
    """Every violation with its row, the coverage verdict, the warnings, and
    ``record``: all of it with every call listed, redacted, as persisted."""

    violations: tuple[AuditViolation, ...]
    credited: tuple[tuple[str, int, int], ...]
    uncovered: tuple[tuple[int, int], ...]
    transport_intact: bool
    warnings: tuple[str, ...]
    record: dict[str, Any]


def audit_stream(
    events: Iterable[Mapping[str, Any]],
    *,
    launch_dir: Path,
    reviews_dir: Path,
    temp_dir: Path,
    route: CaptureRoute | None,
    chunk_paths: Sequence[Path],
    transport_intact: bool,
) -> AuditResult:
    """Audit one launch's parsed events; see the module docstring."""
    home = _normalized(Path.home())
    audit = _Audit(
        launch_dir=_normalized(launch_dir),
        reviews_dir=reviews_dir,
        temp_dir=temp_dir,
        home=home,
        data_dir=home / ".gemini" / "antigravity-cli",
        chunks=_chunk_targets(route, chunk_paths),
    )
    for step in _steps(events):
        audit.judge(step)
    return audit.result(transport_intact)


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Step:
    position: int
    index: int | None
    kind: str
    state: str | None
    tool: str
    parameters: dict[str, Any]
    output: Any
    error: Any

    @property
    def succeeded(self) -> bool:
        return self.state == SUCCESS_STATE and not self.error

    @property
    def ok(self) -> bool:
        return self.succeeded and self.output is not None


def _steps(events: Iterable[Mapping[str, Any]]) -> list[_Step]:
    """Each step's last update, in the order of its first appearance."""
    latest: dict[object, dict[str, Any]] = {}
    order: list[object] = []
    for number, event in enumerate(events):
        if event.get("event") != "step_update":
            continue
        update = event.get("step_update")
        if not isinstance(update, dict):
            continue
        index = update.get("step_index")
        key: object = index if isinstance(index, int) else f"event-{number}"
        if key not in latest:
            order.append(key)
        latest[key] = update
    steps = []
    for position, key in enumerate(order):
        update = latest[key]
        info = update.get("tool_info")
        info = info if isinstance(info, dict) else {}
        parameters = info.get("parameters")
        index = update.get("step_index")
        steps.append(
            _Step(
                position=position,
                index=index if isinstance(index, int) else None,
                kind=str(update.get("step_type") or ""),
                state=update.get("state"),
                tool=str(update.get("tool_name") or info.get("name") or ""),
                parameters=parameters if isinstance(parameters, dict) else {},
                output=info.get("output"),
                error=info.get("error"),
            )
        )
    return steps


# --------------------------------------------------------------------------- #
# The audit
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Chunk:
    path: str
    start: int
    end: int


class _Audit:
    def __init__(
        self,
        *,
        launch_dir: Path,
        reviews_dir: Path,
        temp_dir: Path,
        home: Path,
        data_dir: Path,
        chunks: dict[Path, _Chunk],
    ) -> None:
        self.launch_dir = launch_dir
        self.reviews_dir = reviews_dir
        self.temp_dir = temp_dir
        self.data_dir = data_dir
        self.chunks = chunks
        self.search_roots = frozenset({Path("/"), Path("/home"), home})
        self.violations: list[AuditViolation] = []
        self.warnings: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.unknown_steps: list[dict[str, Any]] = []
        # Each chunk's first targeting by a write or by executed text.
        self.targeted: dict[str, int] = {}
        self.reads: list[tuple[int, str]] = []
        # The directory of the shell the latest run_command started; None
        # when unknown, or when no shell was started.
        self.shell: Path | None = None

    # The verdicts ------------------------------------------------------- #
    def flag(self, reason: FailureReason, row: int, step: _Step, detail: str) -> None:
        self.violations.append(AuditViolation(reason, row, step.index, detail))

    def out_of_scope(self, step: _Step, detail: str) -> None:
        self.flag(FailureReason.OUT_OF_SCOPE_ACCESS, ROW_OUT_OF_SCOPE, step, detail)

    # Steps -------------------------------------------------------------- #
    def judge(self, step: _Step) -> None:
        if step.kind == "subagent":
            self.flag(
                FailureReason.ANTIGRAVITY_UNAPPROVED_TOOL,
                ROW_UNAPPROVED,
                step,
                "a subagent step delegates the review",
            )
        elif step.kind == "checkpoint":
            self.flag(
                FailureReason.ANTIGRAVITY_CONTEXT_COMPACTED,
                ROW_COMPACTED,
                step,
                "a checkpoint step compacted the context",
            )
        if step.tool or step.kind == "tool":
            self.judge_tool(step)
        elif step.kind not in KNOWN_STEP_KINDS:
            self.unknown_steps.append(
                {"step": step.index, "kind": step.kind, "state": step.state}
            )

    def judge_tool(self, step: _Step) -> None:
        call: dict[str, Any] = {
            "step": step.index,
            "tool": step.tool,
            "state": step.state,
            "parameters": step.parameters,
        }
        if step.tool in SHOWN_KINDS and isinstance(step.output, str):
            call["output"] = step.output
        self.calls.append(call)
        if step.tool not in ALLOWED_TOOLS:
            self.flag(
                FailureReason.ANTIGRAVITY_UNAPPROVED_TOOL,
                ROW_UNAPPROVED,
                step,
                f"tool {step.tool!r} is not on the allowlist",
            )
        elif step.tool == "manage_task":
            action = step.parameters.get("Action")
            if not isinstance(action, str) or action.lower() not in INPUTLESS_ACTIONS:
                self.flag(
                    FailureReason.ANTIGRAVITY_UNAPPROVED_TOOL,
                    ROW_UNAPPROVED,
                    step,
                    f"manage_task action {action!r} can deliver input the stream "
                    "never shows",
                )
        if step.tool == "send_command_input":
            self.judge_shell_input(step)
        elif step.tool == "run_command":
            self.judge_paths(step)
            self.judge_command(step)
        else:
            named = self.judge_paths(step)
            self.check_reviews(step, cwd=self.launch_dir, paths=named)
        if (step.tool in UNINSPECTED_KINDS and step.output is not None) or (
            step.tool in RESULT_KINDS and step.output is None and step.succeeded
        ):
            self.unrecognized(step)

    def judge_paths(self, step: _Step) -> list[str]:
        """Check each path parameter; return them as written."""
        named = [
            value
            for key, value in _strings(step.parameters)
            if _PATH_PARAMETER.search(key)
        ]
        for value in named:
            target = resolve_name(value.strip(), self.launch_dir)
            if target is None:
                continue
            if _within(target, self.data_dir):
                self.out_of_scope(step, f"{step.tool} names agy's data directory")
            if _PROCESS_PATH.match(str(target)):
                self.out_of_scope(step, f"{step.tool} inspects processes ({target})")
            if step.tool in RECURSIVE_TOOLS and target in self.search_roots:
                self.out_of_scope(
                    step, f"{step.tool} searches recursively from {target}"
                )
            chunk = self.chunks.get(target)
            if chunk is None:
                continue
            if step.tool in WRITE_TOOLS:
                self.targeted.setdefault(chunk.path, step.position)
            elif step.tool == "read_file" and step.ok and _whole(step):
                self.reads.append((step.position, chunk.path))
        return named

    # Executed text ------------------------------------------------------ #
    def judge_command(self, step: _Step) -> None:
        cwd_parameter = step.parameters.get("Cwd")
        start = (
            resolve_name(cwd_parameter.strip(), self.launch_dir)
            if isinstance(cwd_parameter, str) and cwd_parameter.strip()
            else self.launch_dir
        )
        command = step.parameters.get("CommandLine")
        text = command if isinstance(command, str) else ""
        self.judge_text(step, text, start)
        self.shell = directory_after(text, start)
        output: str | None = None
        if isinstance(step.output, str):
            output = _shown(step.output)
        elif step.output is not None:
            self.unrecognized(step)
        self.check_reviews(
            step,
            cwd=start,
            paths=[str(start)]
            if start is not None and isinstance(cwd_parameter, str)
            else [],
            command=text,
            output=output,
        )

    def judge_shell_input(self, step: _Step) -> None:
        """Every string parameter is input to the shell, in parameter order."""
        for _key, text in _strings(step.parameters):
            start = self.shell
            self.judge_text(step, text, start)
            self.check_reviews(step, cwd=start, command=text)
            self.shell = directory_after(text, start)

    def judge_text(self, step: _Step, text: str, cwd: Path | None) -> None:
        """Judge each simple command of one piece of executed text run from
        ``cwd``."""
        for command in simple_commands(text, cwd):
            self.judge_simple(step, command)

    def judge_simple(self, step: _Step, command: SimpleCommand) -> None:
        """The command name is checked for ``heddle`` and process tools; only
        the words that name a path (not flags, not a search pattern) are
        placed and checked against the forbidden paths and the chunks."""
        words = command.words
        named = list(named_words(words))
        targets = [
            target
            for word in named
            if (target := resolve_name(word.text, command.cwd)) is not None
        ]
        at = _command_at(words)
        if at is not None:
            name = os.path.basename(words[at].text)
            arguments = words[at + 1 :]
            if _invokes_heddle(name, arguments):
                self.out_of_scope(step, "the command invokes heddle")
            if name in _PROCESS_TOOLS:
                self.out_of_scope(step, f"the command inspects processes ({name})")
            if _recursive(name, arguments) and any(
                root in self.search_roots
                for root in _search_roots(name, arguments, named, command)
            ):
                self.out_of_scope(step, f"{name} searches recursively from a root")
        for target in targets:
            if _within(target, self.data_dir):
                self.out_of_scope(step, "the command names agy's data directory")
            if _PROCESS_PATH.match(str(target)):
                self.out_of_scope(step, f"the command inspects processes ({target})")
            chunk = self.chunks.get(target)
            if chunk is not None:
                self.targeted.setdefault(chunk.path, step.position)

    def unrecognized(self, step: _Step) -> None:
        """A result in no recorded shape: not inspected, and warned. No
        structured kind has a recorded shape, so their shown entries never
        reach the ``reviews/`` check."""
        self.warnings.append(unrecognized_warning(LANE, step.tool))

    # reviews/ access ---------------------------------------------------- #
    def check_reviews(
        self,
        step: _Step,
        *,
        cwd: Path | None,
        paths: Sequence[str] = (),
        command: str | None = None,
        output: str | None = None,
    ) -> None:
        access = reviews_access(
            reviews_dir=self.reviews_dir,
            temp_dir=self.temp_dir,
            cwd=cwd,
            paths=tuple(paths),
            command=command,
            output=output,
        )
        if access.named:
            self.out_of_scope(
                step, "the call names a path under the feature's reviews/"
            )
        if access.observed:
            self.out_of_scope(
                step, "the call shows an entry under the feature's reviews/"
            )
        if access.ambiguous:
            self.warnings.append(ambiguous_warning(LANE, step.tool))

    # The verdict -------------------------------------------------------- #
    def result(self, transport_intact: bool) -> AuditResult:
        credited: list[_Chunk] = []
        if transport_intact:
            for chunk in self.chunks.values():
                first = self.targeted.get(chunk.path)
                if any(
                    path == chunk.path and (first is None or position < first)
                    for position, path in self.reads
                ):
                    credited.append(chunk)
        uncovered = _merged(
            (chunk.start, chunk.end)
            for chunk in self.chunks.values()
            if chunk not in credited
        )
        record = _redacted(
            {
                "calls": self.calls,
                "violations": [
                    {
                        "reason": item.reason.value,
                        "row": item.row,
                        "step": item.step,
                        "detail": item.detail,
                    }
                    for item in self.violations
                ],
                "credited": [
                    {"path": chunk.path, "start": chunk.start, "end": chunk.end}
                    for chunk in credited
                ],
                "uncovered": [list(item) for item in uncovered],
                "transport_intact": transport_intact,
                "warnings": self.warnings,
                "unknown_steps": self.unknown_steps,
            }
        )
        return AuditResult(
            violations=tuple(self.violations),
            credited=tuple((chunk.path, chunk.start, chunk.end) for chunk in credited),
            uncovered=uncovered,
            transport_intact=transport_intact,
            warnings=tuple(record["warnings"]),
            record=record,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _chunk_targets(
    route: CaptureRoute | None, chunk_paths: Sequence[Path]
) -> dict[Path, _Chunk]:
    if route is None or route.kind != "chunked":
        return {}
    return {
        _normalized(path): _Chunk(str(path), int(chunk.start), int(chunk.end))
        for chunk, path in zip(route.chunks, chunk_paths, strict=True)
    }


def _strings(parameters: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Each string parameter value with its name, lists flattened."""
    found = []
    for key, value in parameters.items():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str):
                found.append((str(key), item))
    return found


def _whole(step: _Step) -> bool:
    """A read carrying nothing but path parameters."""
    return all(_PATH_PARAMETER.search(str(key)) for key in step.parameters)


def _shown(output: str) -> str:
    """``run_command``'s shown output without agy's header, LF line ends."""
    text = output.replace("\r\n", "\n")
    marker = "\nCommand output:\n"
    at = text.find(marker)
    return text[at + len(marker) :] if at >= 0 else text


def _normalized(path: Path) -> Path:
    return Path(os.path.normpath(path))


def _within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _merged(ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def _redacted(value: dict[str, Any]) -> dict[str, Any]:
    """``value`` with every string leaf made encodable, then redacted; keys
    stay as they are."""
    cleaned = _cleaned(value)
    assert isinstance(cleaned, dict)
    return cleaned


def _cleaned(value: Any) -> Any:
    if isinstance(value, str):
        return _redacted_text(value.encode("utf-8", "backslashreplace").decode())
    if isinstance(value, dict):
        return {key: _cleaned(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cleaned(item) for item in value]
    return value


def _redacted_text(text: str) -> str:
    """Decoded ``text`` redacted. The redactor declines a match that could
    start inside a serialized ``\\uXXXX`` escape; decoded text holds none,
    and no credential holds a backslash, so each backslash-free part is
    redacted alone."""
    return "\\".join(redact_text(part) for part in text.split("\\"))


# --------------------------------------------------------------------------- #
# Simple commands: the audit's checks over review_scope's walker words
# --------------------------------------------------------------------------- #
def _command_at(words: Sequence[Word]) -> int | None:
    """The index of the command name: after assignments and wrappers."""
    at = 0
    while at < len(words):
        text = words[at].text
        if _ASSIGNMENT.match(text):
            at += 1
        elif text in _WRAPPERS:
            at += 1
            while at < len(words) and (
                words[at].text.startswith("-") or _ASSIGNMENT.match(words[at].text)
            ):
                at += 1
        else:
            return at
    return None


def _invokes_heddle(name: str, arguments: Sequence[Word]) -> bool:
    if name == "heddle":
        return True
    if not _PYTHON.fullmatch(name):
        return False
    for number, word in enumerate(arguments):
        text = word.text
        if text == "-m":
            module = arguments[number + 1].text if number + 1 < len(arguments) else ""
        elif text.startswith("-m"):
            module = text[2:]
        elif text.startswith("-"):
            continue
        else:
            return False
        return module == "heddle" or module.startswith("heddle.")
    return False


def _search_roots(
    name: str, arguments: Sequence[Word], named: Sequence[Word], command: SimpleCommand
) -> list[Path]:
    """Where a recursive search starts: its root operands (``find``'s leading
    operands after its global options, otherwise the path-naming arguments),
    or, with none, the directory it runs in, unless it is a later pipeline
    stage, which filters its input and searches nothing. An operand or
    directory this audit cannot place yields nothing."""
    cwd = command.cwd
    if name == "find":
        operands = list(
            takewhile(
                lambda word: not word.text.startswith(("-", "(", "!")),
                arguments[_find_options_end(arguments) :],
            )
        )
    else:
        operands = [word for word in named if any(word is item for item in arguments)]
    if not operands:
        return [] if cwd is None or command.piped else [cwd]
    return [
        root for word in operands if (root := resolve_name(word.text, cwd)) is not None
    ]


def _find_options_end(arguments: Sequence[Word]) -> int:
    """The index after ``find``'s leading global options: ``-L``, ``-H``,
    ``-P``, ``-D <debug options>`` and ``-O<level>``."""
    at = 0
    while at < len(arguments):
        text = arguments[at].text
        if text in ("-L", "-H", "-P") or (text[:2] == "-O" and text[2:].isdigit()):
            at += 1
        elif text == "-D":
            at += 2
        else:
            break
    return min(at, len(arguments))


def _recursive(name: str, arguments: Sequence[Word]) -> bool:
    if name in _ALWAYS_RECURSIVE:
        return True
    letters = "rR" if name in _GREPS else "R" if name == "ls" else ""
    if not letters:
        return False
    for word in arguments:
        text = word.text
        if text in ("--recursive", "--dereference-recursive"):
            return True
        if text.startswith("-") and not text.startswith("--"):
            if any(letter in text[1:] for letter in letters):
                return True
    return False
