"""Project configuration loader.

`.heddle.yaml` discovery, parse against the vocabulary, pinned
defaults, and advisory diagnostics. The vocabulary key tuples stay in
``heddle.contracts.schemas``; this module
consumes them. Unknown sections/keys are advisory ``config-unknown-key``
diagnostics — forward compatibility for later-milestone config growth;
malformed YAML or a wrongly-typed pinned value is fatal
(``workspace-invalid``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from heddle.contracts.gates import GATE_CATALOG
from heddle.contracts.result import ERROR_CODES, Severity
from heddle.contracts.schemas import (
    AGENTS_KEYS,
    AUTOPILOT_KEYS,
    FLOW_HITL,
    FLOW_MODES,
    GATES_KEYS,
    HOST_COMMAND_KEYS,
    LAYOUT_KEYS,
    SYNC_KEYS,
    WORKSPACE_STATE,
)

# the host-contract filename (heddle-plan §The host contract).
HEDDLE_CONFIG_FILENAME = ".heddle.yaml"
# Default `sync.mirror`: every host keeps a
# byte-identical CLAUDE.md beside AGENTS.md unless it opts out with
# `sync: {mirror: null}`.
DEFAULT_SYNC_MIRROR = "CLAUDE.md"

# Feature Spec §Conceptual Design "Config loading" — the advisory
# diagnostic code for unknown sections/keys.
CONFIG_UNKNOWN_KEY = "config-unknown-key"
MISSING_PROJECT_ROOT = "missing-project-root"


class KernelError(Exception):
    """Kernel failure carrier — the pinned error object: ``code`` ∈
    ``ERROR_CODES``, ``message``, ``hint``, plus an optional ``candidates``
    payload (the `feature-ambiguous` slugs). It carries NO exit code —
    each handler owns the code→exit mapping, which is what lets
    ``feature switch`` map a config-load failure to usage/exit 2 while
    status/orient map the same code to exit 3 (Feature Spec §Architecture).
    """

    def __init__(
        self,
        code: str,
        message: str,
        hint: str,
        candidates: tuple[str, ...] = (),
        reason: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(
                f"unknown error code {code!r}: expected one of the pinned "
                f"error-code vocabulary {sorted(ERROR_CODES)}"
            )
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.candidates = candidates
        self.reason = reason
        self.details = dict(details or {})


@dataclass(frozen=True)
class LayoutConfig:
    # Field order = LAYOUT_KEYS order (heddle.contracts.schemas).
    specs: str
    plans: str
    prompts: str
    schemas: str
    tests: str
    source: str


@dataclass(frozen=True)
class ConfigDiagnostic:
    severity: str  # always "advisory" in v0 (Severity.ADVISORY) — both
    #                pinned diagnostics (config-unknown-key, pointer-stale)
    #                are advisory; no "info" case exists yet
    code: str  # "config-unknown-key" | "pointer-stale"
    message: str


@dataclass(frozen=True)
class AutopilotConfig:
    flow: str
    notify_command: str | None
    notify_timeout_s: int
    # None means phase sessions run without a turn cap. An explicit integer
    # opts a cap back in.
    max_turns: int | None
    max_budget_usd: float
    test_command: str | None
    section_present: bool


@dataclass(frozen=True)
class ProjectConfig:
    root: Path  # directory containing .heddle.yaml
    layout: LayoutConfig
    commands: Mapping[str, str]  # HOST_COMMAND_KEYS subset
    agents: Mapping[str, bool]  # AGENTS_KEYS subset
    gates_enabled: tuple[str, ...] | None  # None = section absent
    autopilot: AutopilotConfig
    diagnostics: tuple[ConfigDiagnostic, ...]
    # The single declared byte-mirror of AGENTS.md defaults to
    # DEFAULT_SYNC_MIRROR when the key
    # is absent; an explicit `mirror: null` opts out. None = no mirror; the
    # sync/init writers and the validate mirror-drift guardrail are skipped.
    sync_mirror: str | None = DEFAULT_SYNC_MIRROR


def feature_state_path(config: ProjectConfig, slug: str) -> Path:
    return config.root / config.layout.plans / slug / WORKSPACE_STATE


# Pinned defaults table (Feature Spec Decision Log "Config loader
# defaults"): layout defaults are the generic values (tiny's shape);
# commands/agents default empty; absent gates ⇒ gates_enabled = None
# (no host selection — the tier matrix alone governs required gates).
DEFAULT_LAYOUT = LayoutConfig(
    specs="docs/features",
    plans="plans",
    prompts=".heddle/prompts",
    schemas=".heddle/schemas",
    tests="tests",
    source="src",
)

DEFAULT_AUTOPILOT = AutopilotConfig(
    flow=FLOW_HITL,
    notify_command=None,
    notify_timeout_s=10,
    # No turn cap: a scaffold
    # session can die at max_turns=100 mid-red-scaffold — exit 1, hard halt,
    # no session fact); the budget is the sole per-session rail.
    max_turns=None,
    max_budget_usd=150.0,
    test_command=None,
    section_present=False,
)


def find_project_root(start: Path) -> Path | None:
    """Walk up from ``start`` to the nearest directory containing
    ``.heddle.yaml``; ``None`` when no ancestor has one."""
    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / HEDDLE_CONFIG_FILENAME).is_file():
            return candidate
    return None


def find_project_root_from_cwd() -> Path:
    try:
        cwd: Path | None = Path.cwd()
    except OSError:
        cwd = None
    root = find_project_root(cwd) if cwd is not None else None
    if root is None:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"no {HEDDLE_CONFIG_FILENAME} from the working directory "
                "upward — not inside a Heddle project"
            ),
            hint=(
                "run inside a Heddle project, or author "
                f"{HEDDLE_CONFIG_FILENAME} at the project root"
            ),
            reason=MISSING_PROJECT_ROOT,
        )
    return root


def load_project_config_from_cwd() -> ProjectConfig:
    return load_project_config(find_project_root_from_cwd())


def load_project_config(root: Path) -> ProjectConfig:
    """Parse ``<root>/.heddle.yaml`` against the vocabulary: pinned
    defaults for absent keys, advisory ``config-unknown-key`` diagnostics
    for unknown sections/keys, ``workspace-invalid`` for malformed YAML
    or wrong-typed values."""
    config_path = root / HEDDLE_CONFIG_FILENAME
    if not config_path.is_file():
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"no {HEDDLE_CONFIG_FILENAME} at {root} — not inside a Heddle project"
            ),
            hint=(
                "run from a directory inside a Heddle project, or author "
                f"{HEDDLE_CONFIG_FILENAME} at the project root"
            ),
        )
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        # Environment failures stay on the typed error
        # channel — the handlers map workspace-invalid to exit 3 (status/
        # orient) and usage/exit 2 (feature switch); never a generic
        # `internal` failure.
        raise KernelError(
            code="workspace-invalid",
            message=f"{config_path} is unreadable: {error}",
            hint=f"check that {HEDDLE_CONFIG_FILENAME} is readable UTF-8",
        ) from error
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"malformed YAML in {config_path}: {error}",
            hint=f"fix the YAML syntax in {config_path}",
        ) from error
    if document is None:
        document = {}  # empty file ⇒ the full defaults table
    if not isinstance(document, dict):
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"{config_path} must hold a YAML mapping of supported sections, "
                f"got {type(document).__name__}"
            ),
            hint=f"shape {HEDDLE_CONFIG_FILENAME} as `section: {{key: value}}`",
        )

    layout_values = {key: getattr(DEFAULT_LAYOUT, key) for key in LAYOUT_KEYS}
    commands: dict[str, str] = {}
    agents: dict[str, bool] = {}
    gates_enabled: tuple[str, ...] | None = None
    autopilot_values: dict[str, Any] = {
        key: getattr(DEFAULT_AUTOPILOT, key) for key in AUTOPILOT_KEYS
    }
    autopilot_section_present = False
    sync_mirror: str | None = DEFAULT_SYNC_MIRROR
    diagnostics: list[ConfigDiagnostic] = []

    # One pass in file order so diagnostics follow document order.
    for section, raw_value in document.items():
        if section == "layout":
            for key, value in _section_items(section, raw_value, config_path):
                if key not in LAYOUT_KEYS:
                    diagnostics.append(_unknown_key_diagnostic(section, key))
                    continue
                if not isinstance(value, str):
                    raise _wrong_type(
                        f"layout.{key}",
                        "a project-root-relative directory path string",
                        value,
                        config_path,
                    )
                if Path(value).is_absolute() or ".." in Path(value).parts:
                    # the value type is
                    # "project-root-relative directory path" — absolute or
                    # parent-traversing values would escape the project root.
                    raise KernelError(
                        code="workspace-invalid",
                        message=(
                            f"layout.{key} must be a project-root-relative, "
                            f"traversal-free path, got {value!r}"
                        ),
                        hint=f"fix layout.{key} in {config_path}",
                    )
                layout_values[key] = value
        elif section == "commands":
            for key, value in _section_items(section, raw_value, config_path):
                if key not in HOST_COMMAND_KEYS:
                    diagnostics.append(_unknown_key_diagnostic(section, key))
                    continue
                if not isinstance(value, str):
                    raise _wrong_type(
                        f"commands.{key}",
                        "a shell command line string",
                        value,
                        config_path,
                    )
                commands[key] = value
        elif section == "agents":
            for key, value in _section_items(section, raw_value, config_path):
                if key not in AGENTS_KEYS:
                    diagnostics.append(_unknown_key_diagnostic(section, key))
                    continue
                if not isinstance(value, bool):
                    raise _wrong_type(f"agents.{key}", "a bool", value, config_path)
                agents[key] = value
        elif section == "gates":
            for key, value in _section_items(section, raw_value, config_path):
                if key not in GATES_KEYS:
                    diagnostics.append(_unknown_key_diagnostic(section, key))
                    continue
                if not isinstance(value, list) or not all(
                    isinstance(item, str) for item in value
                ):
                    raise _wrong_type(
                        f"gates.{key}",
                        "a list of gate-name strings (file order preserved)",
                        value,
                        config_path,
                    )
                for name in value:
                    if name not in GATE_CATALOG:
                        hint = (
                            "replace the unknown name in gates.enabled with a "
                            "registered gate, or remove it; known gates: "
                            + ", ".join(sorted(GATE_CATALOG))
                        )
                        if name == "peer-review":
                            hint = (
                                "peer-review is a stage, not a gate; in gates.enabled "
                                "select peer-review-sequential, behavior-review or "
                                "complexity-review through the confirmed feature policy"
                            )
                        raise KernelError(
                            code="workspace-invalid",
                            message=(
                                f"unknown gate {name!r} in gates.enabled "
                                f"in {config_path}"
                            ),
                            hint=hint,
                        )
                gates_enabled = tuple(value)
        elif section == "sync":
            for key, value in _section_items(section, raw_value, config_path):
                if key not in SYNC_KEYS:
                    diagnostics.append(_unknown_key_diagnostic(section, key))
                    continue
                if value is None or value is False:
                    # Explicit opt-out of the default mirror.
                    sync_mirror = None
                    continue
                if not isinstance(value, str):
                    raise _wrong_type(
                        "sync.mirror",
                        "a project-root-relative file path string, or null "
                        "to disable the default mirror",
                        value,
                        config_path,
                    )
                if Path(value).is_absolute() or ".." in Path(value).parts:
                    # The same containment bar as layout.* applies: the mirror is
                    # compared byte-for-byte against AGENTS.md and must not
                    # escape the project root.
                    raise KernelError(
                        code="workspace-invalid",
                        message=(
                            "sync.mirror must be a project-root-relative, "
                            f"traversal-free path, got {value!r}"
                        ),
                        hint=f"fix sync.mirror in {config_path}",
                    )
                sync_mirror = value
        elif section == "autopilot":
            autopilot_section_present = True
            for key, value in _section_items(section, raw_value, config_path):
                if key not in AUTOPILOT_KEYS:
                    diagnostics.append(_unknown_key_diagnostic(section, key))
                    continue
                autopilot_values[key] = _coerce_autopilot_value(key, value, config_path)
        else:
            diagnostics.append(
                ConfigDiagnostic(
                    severity=Severity.ADVISORY.value,
                    code=CONFIG_UNKNOWN_KEY,
                    message=(
                        f"unknown section {section!r} in "
                        f"{HEDDLE_CONFIG_FILENAME} — ignored"
                    ),
                )
            )

    return ProjectConfig(
        root=root,
        layout=LayoutConfig(**layout_values),
        commands=commands,
        agents=agents,
        gates_enabled=gates_enabled,
        autopilot=AutopilotConfig(
            **autopilot_values,
            section_present=autopilot_section_present,
        ),
        diagnostics=tuple(diagnostics),
        sync_mirror=sync_mirror,
    )


def _section_items(
    section: str, raw_value: Any, config_path: Path
) -> tuple[tuple[str, Any], ...]:
    if raw_value is None:
        return ()  # a bare `section:` heading declares nothing
    if not isinstance(raw_value, dict):
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"section {section!r} in {config_path} must be a mapping, "
                f"got {type(raw_value).__name__}: {raw_value!r}"
            ),
            hint=f"shape the {section!r} section as `{section}: {{key: value}}`",
        )
    return tuple(raw_value.items())


def _unknown_key_diagnostic(section: str, key: str) -> ConfigDiagnostic:
    return ConfigDiagnostic(
        severity=Severity.ADVISORY.value,
        code=CONFIG_UNKNOWN_KEY,
        message=(
            f"unknown key {key!r} in section {section!r} of "
            f"{HEDDLE_CONFIG_FILENAME} — ignored"
        ),
    )


def _coerce_autopilot_value(
    key: str, value: Any, config_path: Path
) -> str | int | float | None:
    if key == "flow":
        if value not in FLOW_MODES:
            raise _wrong_type("autopilot.flow", "`hitl` or `auto`", value, config_path)
        return cast(str, value)
    if key in ("notify_command", "test_command"):
        if not isinstance(value, str):
            raise _wrong_type(
                f"autopilot.{key}", "a shell command string", value, config_path
            )
        return value
    if key == "max_turns":
        # Explicit `max_turns: null` = uncapped (same as the absent-key
        # default); an integer opts a cap back in.
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise _wrong_type(
                "autopilot.max_turns", "an integer or null", value, config_path
            )
        return value
    if key == "notify_timeout_s":
        if not isinstance(value, int) or isinstance(value, bool):
            raise _wrong_type(f"autopilot.{key}", "an integer", value, config_path)
        return value
    if key == "max_budget_usd":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise _wrong_type(
                "autopilot.max_budget_usd", "a number", value, config_path
            )
        return float(value)
    raise AssertionError(f"unhandled autopilot key: {key}")


def _wrong_type(
    dotted_key: str, expected: str, value: Any, config_path: Path
) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=(
            f"{dotted_key} must be {expected}, got {type(value).__name__}: {value!r}"
        ),
        hint=f"fix {dotted_key} in {config_path}",
    )
