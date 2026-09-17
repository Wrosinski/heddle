"""Resolve CLI leaves from command metadata and lazily invoke their adapters.

Help stays on the metadata-only path. Gate and driver execution preserve their
raw-argv entry contracts; ordinary handlers decode arguments before calling the
typed application service. Unexpected adapter failures share one error envelope.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from typing import Any, cast

from heddle.contracts.operations import ManualAction
from heddle.contracts.result import (
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
)
from heddle.runtime import contracts
from heddle.runtime.contracts import COMMAND_SURFACE, CommandContract

_USAGE = "usage: heddle <command> [args...] [--json]"

# `-h`/`--help` are dispatcher conveniences like the bare-noun alias —
# recognized at any position among a RESOLVED command's arguments. Unlike
# --json they never join name resolution, so a bare noun + --help stays the
# verbs-hint usage error. Never contract table entries: the manifest
# stays byte-identical.
_HELP_TOKENS = ("-h", "--help")

_BY_NAME: dict[str, CommandContract] = {
    contract.name: contract for contract in COMMAND_SURFACE
}

# Noun → verbs, derived from the leaf spellings. `decisions`
# is also the single bare-noun alias.
_NOUN_VERBS: dict[str, list[str]] = {}
for _contract in COMMAND_SURFACE:
    if " " in _contract.name:
        _noun, _verb = _contract.name.split(" ", 1)
        _NOUN_VERBS.setdefault(_noun, []).append(_verb)

_COMMAND_WORD_LIMIT = max(len(contract.name.split()) for contract in COMMAND_SURFACE)


def _resolve_command_name(tokens: list[str]) -> str | None:
    """Longest-prefix leaf resolution over COMMAND_SURFACE."""
    for length in range(min(len(tokens), _COMMAND_WORD_LIMIT), 0, -1):
        candidate = " ".join(tokens[:length])
        if candidate in _BY_NAME:
            return candidate
    if tokens == ["decisions"]:
        return "decisions list"
    return None


def _print_envelope(result: HeddleResult) -> None:
    print(json.dumps(result.to_envelope()))


def run(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    json_mode, tokens = _split_global_flags(args)
    try:
        # Help stays on the cold metadata path. Execution handlers receive
        # their original argv and emit their original typed results; only
        # unexpected exceptions reach the shared envelope below.
        if _is_run_gate(args) and not _has_help_flag(args):
            from heddle.runtime.gate_run import run_gate

            return run_gate(args)
        if _is_drive(args) and not _has_help_flag(args):
            module = import_module("heddle.driver.cli")
            return cast(int, module.run_drive(args))
        return _dispatch(tokens, json_mode)
    except PackageNotFoundError as exc:
        # A missing or broken install is
        # Assumption A5's violation path — surface it with an actionable
        # hint instead of the generic internal advice. Single install-health
        # chokepoint for every dispatcher-served command.
        print(f"heddle: missing distribution metadata: {exc}", file=sys.stderr)
        failure = HeddleResult.failure(
            HeddleError(
                code="internal",
                message="the heddle distribution metadata is unavailable",
                hint='heddle is not installed; run `pip install -e ".[dev]"` '
                "in the repo root and retry",
            ),
            exit_code=ExitCode.INTERNAL,
        )
        if json_mode:
            _print_envelope(failure)
        return int(failure.exit_code)
    except Exception as exc:  # structured internal failure
        print(f"heddle: internal error: {exc}", file=sys.stderr)
        failure = HeddleResult.failure(
            HeddleError(
                code="internal",
                message="the dispatcher hit an unexpected error",
                hint="re-run with the same arguments; report if it persists",
            ),
            exit_code=ExitCode.INTERNAL,
        )
        if json_mode:
            _print_envelope(failure)
        return int(failure.exit_code)


def _is_run_gate(args: list[str]) -> bool:
    """The leading command (past any pre-token ``--json``) is ``run-gate`` —
    routed to its own handler, not the generic dispatcher."""
    for token in args:
        if token == "--json":
            continue
        return token == "run-gate"
    return False


def _is_drive(args: list[str]) -> bool:
    for token in args:
        if token == "--json":
            continue
        return token == "drive"
    return False


def _split_global_flags(args: list[str]) -> tuple[bool, list[str]]:
    """Split argv into (json_mode, dispatcher tokens). ``--json`` is recognized
    in any position for dispatcher-served commands."""
    return "--json" in args, [token for token in args if token != "--json"]


def _has_help_flag(args: list[str]) -> bool:
    """``-h``/``--help`` anywhere among a resolved command's arguments."""
    return any(token in _HELP_TOKENS for token in args)


def _dispatch(tokens: list[str], json_mode: bool) -> int:
    if not tokens:
        return _emit_usage(
            json_mode,
            message="no command given",
            hint="run `heddle help` to list the command surface",
        )
    if tokens[0] in _HELP_TOKENS:
        tokens = ["help"]

    name = _resolve_command_name(tokens)
    if name is None:
        if tokens[0] in _NOUN_VERBS:
            verbs = ", ".join(_NOUN_VERBS[tokens[0]])
            return _emit_usage(
                json_mode,
                message=f"`heddle {tokens[0]}` needs a recognized verb",
                hint=f"verbs for `{tokens[0]}`: {verbs}",
            )
        return _emit_usage(
            json_mode,
            message=f"unknown command {tokens[0]!r}",
            hint="run `heddle help` to list the command surface",
        )

    contract = _BY_NAME[name]
    rest = tokens[len(name.split()) :]
    # Per-command help must precede the routing chain: run-gate/drive have no
    # branch below (their execution is special-cased in run()), so a later
    # intercept would fall through to _emit_stub's false not-implemented.
    if _has_help_flag(rest):
        return _emit_command_help(contract, json_mode)
    if contract.name == "help":
        return _emit_help(json_mode)
    if contract.cli_binding is not None:
        module, function = contract.cli_binding
        handler = getattr(import_module(f"heddle.runtime.{module}"), function)
        # These existing family adapters own their subverb decoding. Ordinary
        # adapters receive only arguments after the resolved leaf name.
        if contract.name.startswith("task "):
            return cast(int, handler(contract.name.split()[1], rest, json_mode))
        if contract.name.startswith("decisions "):
            rest = [contract.name.split()[1], *rest]
        return cast(int, handler(rest, json_mode))
    return _emit_stub(contract, json_mode)


def _emit_help(json_mode: bool) -> int:
    if json_mode:
        _print_envelope(HeddleResult.success(contracts.build_manifest()))
    else:
        print(_render_help_listing())
    return int(ExitCode.OK)


def _emit_command_help(contract: CommandContract, json_mode: bool) -> int:
    if json_mode:
        # The payload is one heddle.manifest/v0 commands[] entry — no
        # new schema id is minted.
        _print_envelope(HeddleResult.success(contracts.serialize_command(contract)))
    else:
        print(_render_command_help(contract))
    return int(ExitCode.OK)


def _render_command_help(contract: CommandContract) -> str:
    usage = f"usage: heddle {contract.name}"
    for arg in contract.args:
        usage += f" <{arg.name}>" if arg.required else f" [<{arg.name}>]"
    if contract.flags:
        # FlagSpec carries no value-placeholder metadata, so the usage line
        # names positionals exactly and defers flags to their own section.
        usage += " [flags]"

    lines = [
        f"heddle {contract.name} — {contract.summary}",
        "",
        usage,
    ]
    if contract.args:
        width = max(len(arg.name) for arg in contract.args) + 2
        lines += ["", "arguments:"]
        lines.extend(
            f"  {f'<{arg.name}>':<{width}}  {arg.summary}" for arg in contract.args
        )
    if contract.flags:
        width = max(len(flag.name) for flag in contract.flags)
        lines += ["", "flags:"]
        lines.extend(
            f"  {flag.name:<{width}}  {flag.summary}" for flag in contract.flags
        )
    kind = "mutating command" if contract.mutating else "read-only command"
    if contract.dry_run:
        kind += " (--dry-run supported)"
    codes = ", ".join(str(code) for code in contract.exit_codes)
    lines += ["", f"{kind}; exit codes: {codes}"]
    if contract.output_schema is not None:
        lines.append(f"output schema: {contract.output_schema}")
    if contract.input_schema is not None:
        lines += ["", *_render_input_schema(contract.input_schema)]
    lines += ["", "run `heddle help` for the full command surface"]
    return "\n".join(lines)


def _render_input_schema(schema: Mapping[str, Any]) -> list[str]:
    """The payload-contract section of a command's human help — a
    generic rendering of the closed input-schema spec vocabulary."""
    lines = [
        f"input payload ({schema['id']}, via {schema['delivered_by']}) — "
        f"{schema['summary']}:"
    ]
    lines.extend(_schema_field_lines(schema["fields"], indent=2))
    lines.extend(f"  note: {note}" for note in schema.get("notes", []))
    if "example" in schema:
        lines.append("  example:")
        lines.extend(
            f"    {line}"
            for line in json.dumps(schema["example"], indent=2).splitlines()
        )
    return lines


def _schema_field_lines(fields: Mapping[str, Any], indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for name, spec in fields.items():
        qualifiers = [spec["type"], "required" if spec["required"] else "optional"]
        if spec.get("non_empty"):
            qualifiers.append("non-empty")
        if "const" in spec:
            qualifiers.append(f"exactly {spec['const']!r}")
        if "min_items" in spec:
            qualifiers.append(f">={spec['min_items']} entries")
        items = spec.get("items")
        if items and "fields" not in items:
            item_kind = items["type"]
            if items.get("non_empty"):
                item_kind = f"non-empty {item_kind}"
            qualifiers.append(f"of {item_kind}")
        lines.append(f"{pad}{name}  {', '.join(qualifiers)} — {spec['summary']}")
        for option in spec.get("one_of", ()):
            lines.append(f"{pad}    {option['value']}  {option['summary']}")
        if "fields" in spec:
            lines.extend(_schema_field_lines(spec["fields"], indent + 4))
        if items and "fields" in items:
            lines.extend(_schema_field_lines(items["fields"], indent + 4))
    return lines


def _emit_stub(contract: CommandContract, json_mode: bool) -> int:
    result = HeddleResult.failure(
        HeddleError(
            code="not-implemented",
            message=f"`heddle {contract.name}` is not implemented yet",
            hint=f"`{contract.name}` has no runtime adapter in this installation",
        ),
        exit_code=ExitCode.FATAL,
        next_actions=(
            NextAction(
                action=ManualAction("heddle help --json"),
                reason="enumerate the implemented command surface and contracts",
            ),
        ),
    )
    if json_mode:
        _print_envelope(result)
    else:
        print(_render_failure_text(result))
    return int(result.exit_code)


def _emit_usage(json_mode: bool, *, message: str, hint: str) -> int:
    result = HeddleResult.failure(
        HeddleError(code="usage", message=message, hint=hint),
        exit_code=ExitCode.USAGE,
        next_actions=(
            NextAction(ManualAction("heddle help"), reason="list the command surface"),
        ),
    )
    if json_mode:
        # Boundary condition: JSON mode wins over shim parity — the usage
        # failure is an envelope on stdout.
        _print_envelope(result)
    else:
        print(_render_failure_text(result), file=sys.stderr)
        print(_USAGE, file=sys.stderr)
    return int(result.exit_code)


def _render_help_listing() -> str:
    width = max(len(contract.name) for contract in COMMAND_SURFACE)
    lines = [
        "heddle — agent-workflow protocol runtime",
        "",
        _USAGE,
        "",
        "commands:",
        "",
    ]
    lines.extend(
        f"  {contract.name:<{width}}  {contract.summary}"
        for contract in COMMAND_SURFACE
    )
    lines += [
        "",
        "run `heddle <command> --help` for one command's usage, "
        "`heddle help --json` for every command's full contract",
    ]
    return "\n".join(lines)


def _render_failure_text(result: HeddleResult) -> str:
    error = result.error
    assert error is not None  # failure results always carry an error
    lines = [f"heddle: error[{error.code}]: {error.message}", f"  hint: {error.hint}"]
    lines.extend(
        f"  next: {action.command} — {action.reason}" for action in result.next_actions
    )
    return "\n".join(lines)
