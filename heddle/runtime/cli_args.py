"""Shared runtime CLI argument parsers."""

from __future__ import annotations

from dataclasses import dataclass

from heddle.contracts.gate_execution import (
    CLI_DOMAIN_ERROR,
    MODEL_FORMAT_ERROR,
    REASONING_EFFORT_DOMAIN_ERROR,
    VALID_GATE_CLIS,
    VALID_REASONING_EFFORTS,
)
from heddle.contracts.result import ExitCode, HeddleError, HeddleResult

GATE_VALUE_FLAGS = ("--feature", "--cli", "--model", "--reasoning-effort")


@dataclass(frozen=True)
class ParsedGateOptions:
    positionals: tuple[str, ...]
    feature: str | None
    cli: str | None
    model: str | None
    reasoning_effort: str | None


def parse_gate_options(
    tokens: list[str], *, usage: str
) -> tuple[ParsedGateOptions | None, HeddleResult | None]:
    """Parse the shared gate selector and independent public override axes."""
    values: dict[str, str | None] = {
        "--feature": None,
        "--cli": None,
        "--model": None,
        "--reasoning-effort": None,
    }
    seen: set[str] = set()
    positionals: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in GATE_VALUE_FLAGS:
            if token in seen:
                return None, _usage_failure(
                    f"duplicate option {token}",
                    f"supply {token} at most once",
                )
            seen.add(token)
            if index + 1 >= len(tokens):
                return None, _usage_failure(f"{token} requires a value", usage)
            values[token] = tokens[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            return None, _usage_failure(f"unrecognized argument {token!r}", usage)
        positionals.append(token)
        index += 1

    failure = validate_gate_overrides(
        cli=values["--cli"],
        model=values["--model"],
        reasoning_effort=values["--reasoning-effort"],
    )
    if failure is not None:
        return None, failure
    return (
        ParsedGateOptions(
            positionals=tuple(positionals),
            feature=values["--feature"],
            cli=values["--cli"],
            model=values["--model"],
            reasoning_effort=values["--reasoning-effort"],
        ),
        None,
    )


def validate_gate_overrides(
    *, cli: str | None, model: str | None, reasoning_effort: str | None
) -> HeddleResult | None:
    """Return the exact shared usage envelope for an invalid override."""
    if cli is not None and cli not in VALID_GATE_CLIS:
        return _usage_failure(
            CLI_DOMAIN_ERROR,
            "use an exact lowercase supported CLI",
        )
    if reasoning_effort is not None and reasoning_effort not in VALID_REASONING_EFFORTS:
        return _usage_failure(
            REASONING_EFFORT_DOMAIN_ERROR,
            "use an exact lowercase reasoning effort",
        )
    if model is not None and (not model or model != model.strip()):
        return _usage_failure(
            MODEL_FORMAT_ERROR,
            "supply the exact provider model ID",
        )
    return None


def parse_feature_flag(
    args: list[str], *, command: str
) -> tuple[str | None, HeddleResult | None]:
    """Recognize ``--feature <slug>``; any other token is a usage error."""
    explicit: str | None = None
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--feature":
            if index + 1 >= len(args):
                return None, _usage_failure(
                    "--feature requires a feature slug",
                    f"pass the workspace slug: heddle {command} --feature <slug>",
                )
            explicit = args[index + 1]
            index += 2
            continue
        return None, _usage_failure(
            f"unrecognized argument {token!r}",
            "supported flags: --json, --feature <slug>",
        )
    return explicit, None


def _usage_failure(message: str, hint: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="usage", message=message, hint=hint),
        exit_code=ExitCode.USAGE,
    )
