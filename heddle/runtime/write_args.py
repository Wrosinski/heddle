"""Argument parsing for the write-path command handlers.

Extracted move-only from ``write_path.py``: the
shared ``--feature``/``--expect-revision``/``--dry-run`` walker plus the
per-command parsers.
Pure argv → (ParsedCommon, values, positionals) | usage failure; no I/O,
no state reads. Session-payload reading stays in ``write_path.py`` (it
parses YAML, and the import-boundary rail pins the yaml exemption to that
file).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from heddle.contracts.operations import VERIFICATION_SCOPE_GUIDANCE
from heddle.contracts.result import ExitCode, HeddleError, HeddleResult
from heddle.contracts.schemas import COMMAND_KEYS, FLOW_MODES
from heddle.kernel.state import MILESTONE_VERIFICATION_KEYS

_FLOW_SET_USAGE = "usage: heddle flow set <hitl|auto>"
_FLOW_SET_USAGE_WITH_FEATURE = f"{_FLOW_SET_USAGE} [--feature <slug>]"

_MILESTONE_ADD_USAGE = (
    "usage: heddle milestone add --from-file <path>|- [--feature <slug>] [--dry-run]"
)
_MILESTONE_EDIT_USAGE = (
    "usage: heddle milestone edit <milestone-ref> --from-file <path>|- "
    "[--feature <slug>] [--dry-run]"
)
_COMMANDS_SET_USAGE = (
    "usage: heddle commands set <command-key> --command <shell-line> "
    "[--feature <slug>] [--dry-run]"
)

# The payload contract uses the existing MILESTONE_KEYS/kernel-
# Milestone shape exactly — nothing here is new vocabulary.
_MILESTONE_COMPLEXITIES = ("low", "medium", "high")
_MILESTONE_REQUIRED_FIELDS = (
    "title",
    "complexity",
    "estimated_hours",
    "verification",
)
_MILESTONE_OPTIONAL_FIELDS = ("satisfies", "depends_on", "owns")
_MILESTONE_KNOWN_FIELDS = frozenset(
    (*_MILESTONE_REQUIRED_FIELDS, *_MILESTONE_OPTIONAL_FIELDS)
)
_MILESTONE_EDIT_FIELDS = _MILESTONE_KNOWN_FIELDS | {"owns_append"}
_MILESTONE_FORBIDDEN_FIELDS = {
    "id": "milestone ids are assigned m<max+1> and immutable",
    "status": "`heddle milestone advance` owns milestone status",
    "tasks": "tasks are not a skeleton field — task expansion owns them",
}


@dataclass(frozen=True)
class ParsedCommon:
    feature: str | None
    expect_revision: int | None
    dry_run: bool


def usage_failure(message: str, hint: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="usage", message=message, hint=hint),
        exit_code=ExitCode.USAGE,
    )


def parse_common(args: list[str]) -> tuple[ParsedCommon | None, HeddleResult | None]:
    parsed, positionals, failure = parse_common_with_positionals(args)
    if failure is not None:
        return None, failure
    if positionals:
        return None, usage_failure(
            f"unexpected positional argument {positionals[0]!r}",
            "check command usage with heddle help",
        )
    return parsed, None


def parse_common_with_positionals(
    args: list[str],
) -> tuple[ParsedCommon | None, list[str], HeddleResult | None]:
    parsed, _values, positionals, failure = parse_write_args(
        args,
        value_flags={},
        unknown_hint="supported flags: --feature <slug>, --dry-run, --json",
        allow_positionals=True,
    )
    return parsed, positionals, failure


def parse_write_args(
    args: list[str],
    *,
    value_flags: Mapping[str, tuple[str, str]],
    unknown_hint: str,
    allow_positionals: bool,
) -> tuple[
    ParsedCommon | None,
    dict[str, str | None],
    list[str],
    HeddleResult | None,
]:
    feature: str | None = None
    expect_revision: int | None = None
    dry_run = False
    values: dict[str, str | None] = {flag: None for flag in value_flags}
    positionals: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--feature":
            if index + 1 >= len(args):
                return (
                    None,
                    values,
                    [],
                    usage_failure(
                        "--feature requires a value",
                        "pass the workspace slug after --feature",
                    ),
                )
            feature = args[index + 1]
            index += 2
            continue
        if token == "--expect-revision":
            if index + 1 >= len(args):
                return (
                    None,
                    values,
                    [],
                    usage_failure(
                        "--expect-revision requires a value",
                        "pass the current nonnegative integer revision",
                    ),
                )
            try:
                expect_revision = int(args[index + 1])
            except ValueError:
                return (
                    None,
                    values,
                    [],
                    usage_failure(
                        "--expect-revision must be an integer",
                        "pass the current nonnegative integer revision",
                    ),
                )
            if expect_revision < 0:
                return (
                    None,
                    values,
                    [],
                    usage_failure(
                        "--expect-revision must be nonnegative",
                        "pass the current nonnegative integer revision",
                    ),
                )
            index += 2
            continue
        if token == "--dry-run":
            dry_run = True
            index += 1
            continue
        if token in value_flags:
            message, hint = value_flags[token]
            if index + 1 >= len(args):
                return None, values, [], usage_failure(message, hint)
            values[token] = args[index + 1]
            index += 2
            continue
        if token.startswith("-") or not allow_positionals:
            return (
                None,
                values,
                [],
                usage_failure(
                    f"unrecognized argument {token!r}",
                    unknown_hint,
                ),
            )
        positionals.append(token)
        index += 1
    return (
        ParsedCommon(
            feature=feature,
            expect_revision=expect_revision,
            dry_run=dry_run,
        ),
        values,
        positionals,
        None,
    )


def parse_verify(
    args: list[str],
) -> tuple[ParsedCommon | None, str | None, int | None, HeddleResult | None]:
    parsed, values, _positionals, failure = parse_write_args(
        args,
        value_flags={
            "--scope": (
                "--scope requires a value",
                VERIFICATION_SCOPE_GUIDANCE,
            ),
        },
        unknown_hint=(
            "supported flags: --feature <slug>, --scope <scope>, "
            "--expect-revision <n>, --dry-run"
        ),
        allow_positionals=False,
    )
    if failure is not None:
        return None, None, None, failure
    assert parsed is not None
    return parsed, values["--scope"], parsed.expect_revision, None


def parse_commands_set(
    args: list[str],
) -> tuple[
    ParsedCommon | None,
    str | None,
    str | None,
    HeddleResult | None,
]:
    parsed, values, positionals, failure = parse_write_args(
        args,
        value_flags={
            "--command": (
                "--command requires a shell line",
                _COMMANDS_SET_USAGE,
            )
        },
        unknown_hint=_COMMANDS_SET_USAGE,
        allow_positionals=True,
    )
    if failure is not None:
        return None, None, None, failure
    if len(positionals) != 1:
        return (
            None,
            None,
            None,
            usage_failure(
                "commands set requires exactly one <command-key>",
                _COMMANDS_SET_USAGE,
            ),
        )
    key = positionals[0]
    if key not in COMMAND_KEYS:
        return (
            None,
            None,
            None,
            usage_failure(
                f"unknown verification command key {key!r}",
                f"choose one of {', '.join(COMMAND_KEYS)}",
            ),
        )
    shell_command = values["--command"]
    if shell_command is None or not shell_command.strip():
        return (
            None,
            None,
            None,
            usage_failure(
                "commands set requires a non-blank --command <shell-line>",
                _COMMANDS_SET_USAGE
                + "; use heddle commands unset <command-key> to remove",
            ),
        )
    return parsed, key, shell_command, None


def parse_commands_unset(
    args: list[str],
) -> tuple[ParsedCommon | None, str | None, HeddleResult | None]:
    usage = "usage: heddle commands unset <command-key> [--feature <slug>] [--dry-run]"
    parsed, _values, positionals, failure = parse_write_args(
        args, value_flags={}, unknown_hint=usage, allow_positionals=True
    )
    if failure is not None:
        return None, None, failure
    if len(positionals) != 1:
        return (
            None,
            None,
            usage_failure("commands unset requires exactly one <command-key>", usage),
        )
    key = positionals[0]
    if key not in COMMAND_KEYS:
        return (
            None,
            None,
            usage_failure(
                f"unknown verification command key {key!r}",
                f"choose one of {', '.join(COMMAND_KEYS)}",
            ),
        )
    return parsed, key, None


def parse_session_log(
    args: list[str],
) -> tuple[ParsedCommon | None, str | None, HeddleResult | None]:
    parsed, values, _positionals, failure = parse_write_args(
        args,
        value_flags={
            "--from-file": (
                "--from-file requires a path or -",
                "usage: heddle session log --from-file <path>|-",
            )
        },
        unknown_hint="usage: heddle session log --from-file <path>|-",
        allow_positionals=False,
    )
    if failure is not None:
        return None, None, failure
    from_file = values["--from-file"]
    if from_file is None:
        return (
            None,
            None,
            usage_failure(
                "session log requires --from-file <path> or --from-file -",
                "provide a structured YAML session payload",
            ),
        )
    return parsed, from_file, None


def parse_milestone_add(
    args: list[str],
) -> tuple[ParsedCommon | None, str | None, HeddleResult | None]:
    parsed, values, _positionals, failure = parse_write_args(
        args,
        value_flags={
            "--from-file": (
                "--from-file requires a path or -",
                _MILESTONE_ADD_USAGE,
            )
        },
        unknown_hint=_MILESTONE_ADD_USAGE,
        allow_positionals=False,
    )
    if failure is not None:
        return None, None, failure
    from_file = values["--from-file"]
    if from_file is None:
        return (
            None,
            None,
            usage_failure(
                "milestone add requires --from-file <path> or --from-file -",
                _MILESTONE_ADD_USAGE,
            ),
        )
    return parsed, from_file, None


def parse_milestone_edit(
    args: list[str],
) -> tuple[ParsedCommon | None, str | None, str | None, HeddleResult | None]:
    parsed, values, positionals, failure = parse_write_args(
        args,
        value_flags={
            "--from-file": (
                "--from-file requires a path or -",
                _MILESTONE_EDIT_USAGE,
            )
        },
        unknown_hint=_MILESTONE_EDIT_USAGE,
        allow_positionals=True,
    )
    if failure is not None:
        return None, None, None, failure
    if len(positionals) != 1:
        return (
            None,
            None,
            None,
            usage_failure(
                "milestone edit requires exactly one <milestone-ref>",
                _MILESTONE_EDIT_USAGE,
            ),
        )
    from_file = values["--from-file"]
    if from_file is None:
        return (
            None,
            None,
            None,
            usage_failure(
                "milestone edit requires --from-file <path> or --from-file -",
                _MILESTONE_EDIT_USAGE,
            ),
        )
    return parsed, positionals[0], from_file, None


def validate_milestone_payload(
    payload: Mapping[str, object], *, require_all: bool, require_hours: bool = True
) -> HeddleResult | None:
    """The payload shape gauntlet makes every violation usage exit 2,
    checked before any state read. WM §4.2 *invariants* (done-edit,
    missing depends_on refs) are the kernel transforms' concern."""
    # Reject non-string keys before any set arithmetic — YAML
    # admits `1: value`, and sorting/joining mixed key types would crash
    # the gauntlet instead of exiting 2.
    non_string_keys = [key for key in payload if not isinstance(key, str)]
    if non_string_keys:
        return usage_failure(
            f"milestone payload keys must be strings, got "
            f"{', '.join(repr(key) for key in non_string_keys)}",
            "quote YAML keys so they parse as strings",
        )
    for field, owner_hint in _MILESTONE_FORBIDDEN_FIELDS.items():
        if field in payload:
            return usage_failure(
                f"milestone payload must not carry {field!r}",
                owner_hint,
            )
    known_fields = _MILESTONE_KNOWN_FIELDS if require_all else _MILESTONE_EDIT_FIELDS
    unknown = sorted(set(payload) - known_fields)
    if unknown:
        return usage_failure(
            f"unknown milestone payload field(s): {', '.join(unknown)}",
            f"known milestone payload fields: {', '.join(sorted(known_fields))}",
        )
    if require_all:
        missing = [
            field
            for field in _MILESTONE_REQUIRED_FIELDS
            if field not in payload and (field != "estimated_hours" or require_hours)
        ]
        if missing:
            return usage_failure(
                f"milestone payload is missing required field(s): {', '.join(missing)}",
                "milestone add requires title, complexity and verification; "
                "historical state also requires estimated_hours",
            )
    elif not payload:
        return usage_failure(
            "milestone edit payload names no fields",
            "name at least one skeleton field to edit",
        )
    if "owns" in payload and "owns_append" in payload:
        return usage_failure(
            "milestone edit payload must not carry both 'owns' and 'owns_append'",
            "use owns to replace ownership or owns_append to preserve and extend it",
        )
    return _milestone_field_type_failure(payload)


def _milestone_field_type_failure(
    payload: Mapping[str, object],
) -> HeddleResult | None:
    if "title" in payload:
        title = payload["title"]
        if not isinstance(title, str) or not title.strip():
            return usage_failure(
                "milestone title must be a non-empty string",
                "provide title: <text>",
            )
    if "complexity" in payload and payload["complexity"] not in (
        _MILESTONE_COMPLEXITIES
    ):
        return usage_failure(
            f"milestone complexity must be one of {', '.join(_MILESTONE_COMPLEXITIES)}",
            f"got {payload['complexity']!r}",
        )
    if "estimated_hours" in payload:
        hours = payload["estimated_hours"]
        if not (
            isinstance(hours, list)
            and len(hours) == 2
            and all(
                isinstance(bound, int) and not isinstance(bound, bool)
                for bound in hours
            )
        ):
            return usage_failure(
                "milestone estimated_hours must be a [lo, hi] integer pair",
                "provide estimated_hours: [<lo>, <hi>]",
            )
    if "verification" in payload:
        verification = payload["verification"]
        # Require exactly the reader's key set — the state reader rejects
        # unknown verification keys (`_require_keys`), so an extra key
        # admitted here would write a state.yaml the next read refuses.
        if not (
            isinstance(verification, dict)
            and set(verification) == set(MILESTONE_VERIFICATION_KEYS)
            and all(
                isinstance(verification[key], str)
                for key in MILESTONE_VERIFICATION_KEYS
            )
        ):
            return usage_failure(
                "milestone verification must be a {command, expected} "
                "string mapping — exactly those two keys",
                "provide verification: {command: <cmd>, expected: <text>}",
            )
    for field in (*_MILESTONE_OPTIONAL_FIELDS, "owns_append"):
        if field in payload:
            value = payload[field]
            if not (
                isinstance(value, list) and all(isinstance(item, str) for item in value)
            ):
                return usage_failure(
                    f"milestone {field} must be a list of strings",
                    f"provide {field}: [<id>, ...] or omit it",
                )
    return None


def parse_flow_set(
    args: list[str],
) -> tuple[ParsedCommon | None, str | None, HeddleResult | None]:
    parsed, _values, positionals, failure = parse_write_args(
        args,
        value_flags={},
        unknown_hint=_FLOW_SET_USAGE_WITH_FEATURE,
        allow_positionals=True,
    )
    if failure is not None:
        return None, None, failure
    assert parsed is not None
    if parsed.dry_run:
        return (
            None,
            None,
            usage_failure(
                "flow set does not support --dry-run",
                _FLOW_SET_USAGE_WITH_FEATURE,
            ),
        )
    if len(positionals) != 1:
        return (
            None,
            None,
            usage_failure(
                "flow set requires exactly one mode",
                _FLOW_SET_USAGE,
            ),
        )
    mode = positionals[0]
    if mode not in FLOW_MODES:
        return (
            None,
            None,
            usage_failure(
                f"unknown flow mode {mode!r}",
                "choose `hitl` or `auto`",
            ),
        )
    return parsed, mode, None


def parse_phase_exit(
    args: list[str],
) -> tuple[ParsedCommon | None, str | None, HeddleResult | None]:
    parsed, values, _positionals, failure = parse_write_args(
        args,
        value_flags={
            "--through": (
                "--through requires a stage",
                "pass one canonical stage after --through",
            )
        },
        unknown_hint="usage: heddle phase-exit [--through <stage>] [--dry-run]",
        allow_positionals=False,
    )
    if failure is not None:
        return None, None, failure
    return parsed, values["--through"], None
