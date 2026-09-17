"""Headless phase-session spawning for ``heddle drive``."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heddle.driver.tempfiles import empty_temp_path
from heddle.io.claude_events import last_result_event_from_path
from heddle.io.process import MonitorConfig, run_monitored
from heddle.kernel.model import FeatureSnapshot
from heddle.kernel.project_config import KernelError, ProjectConfig

READ_BASELINE = (
    "Read",
    "Grep",
    "Glob",
    "Bash(heddle status:*)",
    "Bash(heddle orient:*)",
    "Bash(heddle kickoff:*)",
    "Bash(heddle session log:*)",
    "Bash(heddle decisions add:*)",
    "Bash(heddle decisions record-policy:*)",
)

RUN_GATE_TOOL = "Bash(heddle run-gate:*)"
INTERPRET_REVIEW_TOOL = "Bash(heddle review interpret:*)"
_PROTECTED_DOCUMENTS = (
    "docs/design/architecture.md",
    "docs/design/workflow-model.md",
    "docs/workflow/engineering-principles.md",
)
_BOUNDARY_DENIALS = (
    "Bash(heddle phase-exit*)",
    "Bash(heddle milestone advance*)",
    "Bash(heddle decisions resolve*)",
    "Bash(heddle flow set*)",
)
PHASE_SESSION_HARD_TIMEOUT_S = 2700
PHASE_SESSION_INACTIVITY_TIMEOUT_S = 900
PHASE_SESSION_POLL_S = 0.1
# Session-side Bash tool timeout envelope: the CLI's 120s Bash default
# kills any `heddle run-gate` a session runs itself — gate runs take 5-15
# minutes, and the review/implement allowlists carry `Bash(heddle
# run-gate:*)` for exactly those calls — wasting the paid attempt with no
# fact recorded. Default = the 15-minute "normal gate timing" ceiling; max =
# the gate runner's own hard timeout so a session can extend explicitly.
PHASE_SESSION_BASH_DEFAULT_TIMEOUT_MS = 900_000
PHASE_SESSION_BASH_MAX_TIMEOUT_MS = 2_700_000
CLAUDE_PRINT = "--print"
CLAUDE_OUTPUT_FORMAT = "--output-format"
CLAUDE_STREAM_JSON = "stream-json"
CLAUDE_PERMISSION_MODE = "--permission-mode"
CLAUDE_AUTO = "auto"
CLAUDE_MODEL = "--model"
CLAUDE_EFFORT = "--effort"
# `--print --output-format stream-json` hard-requires --verbose (CLI startup
# errors otherwise); the gate runner uses the same bounded fallback.
CLAUDE_VERBOSE = "--verbose"
# Without an explicit pin the session inherits ambient operator settings, which
# are not a stable execution contract. Fable 5.1 is the default phase-session
# model. ``HEDDLE_PHASE_SESSION_MODEL`` overrides it at launch when a supported
# fallback is needed; unset the variable to restore the default.
# Read once at import, so the drive process must be launched with the var set.
DEFAULT_PHASE_SESSION_MODEL = "claude-fable-5-1"
PHASE_SESSION_MODEL = os.environ.get(
    "HEDDLE_PHASE_SESSION_MODEL", DEFAULT_PHASE_SESSION_MODEL
)
PHASE_SESSION_EFFORT = "high"
CLAUDE_APPEND_SYSTEM_PROMPT = "--append-system-prompt"
CLAUDE_ALLOWED_TOOLS = "--allowedTools"
CLAUDE_DISALLOWED_TOOLS = "--disallowedTools"
CLAUDE_MAX_TURNS = "--max-turns"
CLAUDE_MAX_BUDGET_USD = "--max-budget-usd"


@dataclass(frozen=True)
class PhaseSessionSpec:
    stage: str
    feature: str
    prompt_path: Path
    allowed_tools: tuple[str, ...]
    deny_rules: tuple[str, ...]
    # None means uncapped. The budget is the default session rail; a turn cap
    # is an explicit configuration opt-in.
    max_turns: int | None
    max_budget_usd: float


@dataclass(frozen=True)
class _ClaudeArg:
    label: str
    help_tokens: tuple[str, ...]
    render: Callable[[PhaseSessionSpec, Path], tuple[str, ...]]


@dataclass(frozen=True)
class SessionResult:
    exit_code: int
    termination: str
    output: Mapping[str, Any]


_MILESTONE_TOOLS = (
    "Bash(heddle milestone add:*)",
    "Bash(heddle milestone edit:*)",
)

# Command preapprovals retain the existing stage policy. File preapprovals
# below follow the host and feature. Auto may approve other calls; these rules
# and the explicit denials do not confine arbitrary subprocess I/O.
_STAGE_COMMANDS: dict[str, tuple[str, ...]] = {
    "specify": (
        *_MILESTONE_TOOLS,
        "Bash(heddle search:*)",
        "Bash(heddle validate:*)",
    ),
    "spec-review": (
        RUN_GATE_TOOL,
        INTERPRET_REVIEW_TOOL,
        *_MILESTONE_TOOLS,
        "Bash(heddle validate:*)",
    ),
    "plan-review": (
        RUN_GATE_TOOL,
        INTERPRET_REVIEW_TOOL,
        *_MILESTONE_TOOLS,
        "Bash(heddle validate:*)",
    ),
    "scaffold": (
        RUN_GATE_TOOL,
        INTERPRET_REVIEW_TOOL,
        "Bash(heddle commands set:*)",
        "Bash(heddle milestone edit:*)",
    ),
    "implement": (
        "Bash(heddle task:*)",
        "Bash(heddle verify:*)",
        RUN_GATE_TOOL,
        INTERPRET_REVIEW_TOOL,
    ),
    # robustness is Phase 8 only — the HITL Phase 9 session owns the
    # friction-retrospective and decision-journal artifacts, so the auto
    # profile through robustness carries no Phase 9 writer.
    "peer-review": (
        RUN_GATE_TOOL,
        INTERPRET_REVIEW_TOOL,
        "Bash(heddle verify:*)",
        "Bash(heddle validate:*)",
    ),
    "robustness": (
        RUN_GATE_TOOL,
        INTERPRET_REVIEW_TOOL,
        "Bash(heddle verify:*)",
        "Bash(heddle validate:*)",
    ),
}


def _static_arg(
    *argv: str,
) -> Callable[[PhaseSessionSpec, Path], tuple[str, ...]]:
    def _render(_spec: PhaseSessionSpec, _addendum: Path) -> tuple[str, ...]:
        return argv

    return _render


def _append_system_prompt_arg(
    _spec: PhaseSessionSpec, addendum: Path
) -> tuple[str, ...]:
    return (CLAUDE_APPEND_SYSTEM_PROMPT, str(addendum))


def _allowed_tools_arg(spec: PhaseSessionSpec, _addendum: Path) -> tuple[str, ...]:
    return (CLAUDE_ALLOWED_TOOLS, ",".join(spec.allowed_tools))


def _deny_rules_arg(spec: PhaseSessionSpec, _addendum: Path) -> tuple[str, ...]:
    return (CLAUDE_DISALLOWED_TOOLS, ",".join(spec.deny_rules))


def _max_turns_arg(spec: PhaseSessionSpec, _addendum: Path) -> tuple[str, ...]:
    if spec.max_turns is None:
        return ()
    return (CLAUDE_MAX_TURNS, str(spec.max_turns))


def _max_budget_arg(spec: PhaseSessionSpec, _addendum: Path) -> tuple[str, ...]:
    return (CLAUDE_MAX_BUDGET_USD, f"{spec.max_budget_usd:.2f}")


_CLAUDE_ARGS = (
    _ClaudeArg("claude -p/--print", (CLAUDE_PRINT,), _static_arg(CLAUDE_PRINT)),
    _ClaudeArg(
        "--output-format stream-json",
        (CLAUDE_OUTPUT_FORMAT, CLAUDE_STREAM_JSON),
        _static_arg(CLAUDE_OUTPUT_FORMAT, CLAUDE_STREAM_JSON),
    ),
    _ClaudeArg(
        "--verbose (required by --print + stream-json)",
        (CLAUDE_VERBOSE,),
        _static_arg(CLAUDE_VERBOSE),
    ),
    _ClaudeArg(
        "--permission-mode auto",
        (CLAUDE_PERMISSION_MODE,),
        _static_arg(CLAUDE_PERMISSION_MODE, CLAUDE_AUTO),
    ),
    _ClaudeArg(
        "--model claude-fable-5-1",
        (CLAUDE_MODEL,),
        _static_arg(CLAUDE_MODEL, PHASE_SESSION_MODEL),
    ),
    _ClaudeArg(
        "--effort high",
        (CLAUDE_EFFORT,),
        _static_arg(CLAUDE_EFFORT, PHASE_SESSION_EFFORT),
    ),
    _ClaudeArg(
        "--append-system-prompt",
        (CLAUDE_APPEND_SYSTEM_PROMPT,),
        _append_system_prompt_arg,
    ),
    _ClaudeArg("--allowedTools", (CLAUDE_ALLOWED_TOOLS,), _allowed_tools_arg),
    _ClaudeArg(
        "permissions.deny (--disallowedTools)",
        (CLAUDE_DISALLOWED_TOOLS,),
        _deny_rules_arg,
    ),
    _ClaudeArg("--max-turns", (CLAUDE_MAX_TURNS,), _max_turns_arg),
    _ClaudeArg("--max-budget-usd", (CLAUDE_MAX_BUDGET_USD,), _max_budget_arg),
)


def _permission_error(field: str, value: str, reason: str) -> KernelError:
    if field == "root":
        remedy = "Move the repository to a representable host-root path."
    elif field == "owns":
        remedy = "Correct the owns declaration with heddle milestone edit."
    elif field == "feature_inputs":
        remedy = "Correct the declaration with heddle feature inputs set."
    elif field.startswith("layout."):
        remedy = "Correct the layout declaration in .heddle.yaml."
    else:
        remedy = f"Correct the {field} path."
    if field != "root":
        remedy += (
            " Use a representable repository-relative path "
            "and an inside-root symlink target."
        )
    return KernelError(
        "workspace-invalid",
        f"cannot prepare session permission for {field}={value!r}: {reason}",
        remedy,
    )


def _spelling_problem(value: str) -> str | None:
    if any(char in "?,(){}\\" or ord(char) < 32 or ord(char) == 127 for char in value):
        return "unsupported character in permission path"
    if any(part != part.strip(" ") for part in value.split("/")):
        return "leading or trailing space in a path component"
    return None


def _permission_path(
    root: Path,
    value: str,
    field: str,
    *,
    child: str | None = None,
    reject_root: bool = False,
) -> Path:
    """Validate a declaration and its resolved target before encoding a rule."""
    if problem := _spelling_problem(str(root)):
        raise _permission_error("root", str(root), problem)
    relative = Path(value)
    if not value or relative.is_absolute():
        raise _permission_error(field, value, "path must be repository-relative")
    if ".." in relative.parts:
        raise _permission_error(field, value, "parent path segments are not allowed")
    if problem := _spelling_problem(value):
        raise _permission_error(field, value, problem)
    target = root / relative
    if child is not None:
        target /= child
    try:
        target = target.resolve()
    except OSError as error:
        raise _permission_error(
            field, value, f"cannot resolve path: {error}"
        ) from error
    if not target.is_relative_to(root):
        raise _permission_error(
            field, value, f"resolved path is outside the host root: {target}"
        )
    if reject_root and target == root:
        raise _permission_error(
            field, value, "root-wide ownership or tree preapproval is not allowed"
        )
    if problem := _spelling_problem(str(target)):
        raise _permission_error(field, value, f"resolved path has {problem}: {target}")
    return target


def _edit_rule(path: Path, suffix: str = "") -> str:
    # Claude's absolute rules start with //; only our suffix introduces a glob.
    literal = str(path).translate({ord(char): "\\" + char for char in "*[]!"})
    return f"Edit(/{literal}{suffix})"


def uniform_deny_rules(config: ProjectConfig) -> tuple[str, ...]:
    root = config.root.resolve()

    def deny(path: str, field: str, suffix: str = "") -> tuple[str, ...]:
        target = _permission_path(root, path, field)
        return tuple(
            _edit_rule(spelling, suffix)
            for spelling in dict.fromkeys((root / path, target))
        )

    documents = tuple(
        rule
        for path in _PROTECTED_DOCUMENTS
        for rule in deny(path, "protected document")
    )
    # Edit covers built-in Write too. State workspaces are flat under plans.
    return (
        documents
        + deny(config.layout.plans, "layout.plans", "/*/state.yaml")
        + _BOUNDARY_DENIALS
    )


def allowed_tools_for_stage(
    config: ProjectConfig, snapshot: FeatureSnapshot
) -> tuple[str, ...]:
    if snapshot.stage == "complete":
        # Phase 9 has no headless executor, regardless of authorization.
        raise ValueError(
            "no phase-session permission profile exists for stage 'complete' "
            f"(feature {snapshot.feature!r}): Phase 9 is human-owned — drive "
            "hands off via heddle kickoff"
        )
    stage = snapshot.stage if snapshot.stage in _STAGE_COMMANDS else "implement"
    root = config.root.resolve()
    files = [
        _edit_rule(
            _permission_path(root, snapshot.workspace, "workspace", child="plan.md")
        )
    ]
    if stage != "plan-review":
        files.append(_edit_rule(_permission_path(root, snapshot.spec_path, "spec")))
    if stage not in {"peer-review", "robustness"}:
        files.append(
            _edit_rule(
                _permission_path(
                    root, snapshot.workspace, "workspace", child="brief.md"
                )
            )
        )
    if stage == "specify":
        files.append(
            _edit_rule(
                _permission_path(
                    root,
                    config.layout.specs,
                    "layout.specs",
                    child="_descriptions.yaml",
                )
            )
        )
    if stage == "scaffold":
        tests = _permission_path(
            root, config.layout.tests, "layout.tests", reject_root=True
        )
        files.append(_edit_rule(tests, "/**"))
    if stage in {"implement", "peer-review", "robustness"}:
        field = "owns"
        if stage == "implement":
            owned = snapshot.owned_paths
        else:
            owned = tuple(
                path
                for milestone in snapshot.state.milestones
                for path in milestone.owns
            )
        for value in owned:
            path = _permission_path(root, value, field, reject_root=True)
            # Missing ownership is an exact file declaration, never its parent.
            files.append(_edit_rule(path, "/**" if path.is_dir() else ""))
    commands = _STAGE_COMMANDS[stage]
    if snapshot.state.feature_policy is not None and RUN_GATE_TOOL in commands:
        # Current review stages own evidence-bound lead dispositions. Historical
        # profiles keep their old authority; round opening/owner decisions are
        # not added by this briefing-delivery preapproval. An allowance only
        # records an already ratified cap increase, so stages whose briefing
        # explains that route must be able to invoke it.
        commands += (
            "Bash(heddle review disposition:*)",
            "Bash(heddle review allowance:*)",
        )
    return READ_BASELINE + commands + tuple(dict.fromkeys(files))


# A minimal spec used only to render representative argv values for the
# parse-acceptance fallback probe; nothing in it is ever executed.
_PROBE_SPEC = PhaseSessionSpec(
    stage="probe",
    feature="probe",
    prompt_path=Path("/dev/null"),
    allowed_tools=("Read",),
    deny_rules=("Edit",),
    max_turns=1,
    max_budget_usd=0.01,
)


def _parse_accepts(resolved: str, argv: tuple[str, ...]) -> bool:
    """Probe flags hidden from help but still registered.

    A registered flag parses cleanly and fails at the local no-input gate,
    while an unregistered one fails at parse with `unknown option` (and an
    unsupported choice value with `is invalid`). No prompt is supplied and
    stdin is closed, so the invocation never reaches the API."""
    try:
        probe = subprocess.run(
            [resolved, *argv, "--print"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    combined = f"{probe.stdout}\n{probe.stderr}"
    return "unknown option" not in combined and "is invalid" not in combined


def probe_claude_capabilities(cli_bin: str = "claude") -> str | None:
    resolved = shutil.which(cli_bin)
    if resolved is None:
        return "claude binary"
    try:
        probe = subprocess.run(
            [resolved, "--help"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "claude --help capability probe"
    help_text = f"{probe.stdout}\n{probe.stderr}"
    if not help_text.strip():
        return "claude --help output"
    addendum = _autonomy_addendum_path()
    for arg in _CLAUDE_ARGS:
        if all(token in help_text for token in arg.help_tokens):
            continue
        if _parse_accepts(resolved, arg.render(_PROBE_SPEC, addendum)):
            continue
        return arg.label
    return None


def probe_codex_capabilities(cli_bin: str = "codex") -> str | None:
    """Presence probe for the Codex lane (gate-to-CLI configuration).

    The gate runner spawns ``codex exec`` with per-run flags the engine
    already owns; startup only needs the binary to exist so an unattended
    drive halts before its first Codex-lane gate, not during it.
    """
    if shutil.which(cli_bin) is None:
        return "codex binary"
    return None


def spawn_phase_session(
    spec: PhaseSessionSpec, *, scratch_dir: Path | None = None
) -> SessionResult:
    raw_out = empty_temp_path(prefix="heddle-phase-", suffix=".jsonl", dir=scratch_dir)
    cmd = _phase_session_command(spec, _autonomy_addendum_path())
    monitor = run_monitored(
        cmd,
        raw_out,
        MonitorConfig(
            hard_timeout_s=PHASE_SESSION_HARD_TIMEOUT_S,
            inactivity_timeout_s=PHASE_SESSION_INACTIVITY_TIMEOUT_S,
            poll_s=PHASE_SESSION_POLL_S,
        ),
        stdin_source=spec.prompt_path,
    )
    return SessionResult(
        exit_code=monitor.exit_code,
        termination=monitor.termination.value,
        output=last_result_event_from_path(raw_out),
    )


def _autonomy_addendum_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent / "resources" / ("autonomy-addendum.md")
    )


def _phase_session_command(spec: PhaseSessionSpec, addendum: Path) -> list[str]:
    # env-prefix pattern (same as the gate runner's `env -u CLAUDECODE ...`):
    # the Bash timeout envelope must reach the session's tool harness, not the
    # CLI argv.
    command = [
        "env",
        f"BASH_DEFAULT_TIMEOUT_MS={PHASE_SESSION_BASH_DEFAULT_TIMEOUT_MS}",
        f"BASH_MAX_TIMEOUT_MS={PHASE_SESSION_BASH_MAX_TIMEOUT_MS}",
        "claude",
    ]
    for arg in _CLAUDE_ARGS:
        command.extend(arg.render(spec, addendum))
    return command
