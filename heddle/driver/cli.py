"""CLI handler for ``heddle drive``."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import heddle.driver.results as driver_results
from heddle.contracts.result import (
    HeddleResult,
)


@dataclass(frozen=True)
class _Parsed:
    feature: str | None
    until: str | None
    json_mode: bool


def run_drive(argv: list[str]) -> int:
    parsed, failure = _parse(argv)
    if failure is not None:
        return _emit(failure, parsed.json_mode)

    from heddle.contracts.operations import Drive
    from heddle.runtime.application import execute

    return _emit(
        execute(Drive(feature=parsed.feature, until=parsed.until)), parsed.json_mode
    )


def _parse(argv: list[str]) -> tuple[_Parsed, HeddleResult | None]:
    tokens = [token for token in argv if token != "drive"]
    json_mode = "--json" in tokens
    rest = [token for token in tokens if token != "--json"]
    feature: str | None = None
    until: str | None = None
    index = 0
    while index < len(rest):
        token = rest[index]
        if token in {"--feature", "--until"}:
            if index + 1 >= len(rest):
                return (
                    _Parsed(feature, until, json_mode),
                    driver_results.usage(
                        f"{token} requires a value", "pass a value after the flag"
                    ),
                )
            if token == "--feature":
                feature = rest[index + 1]
            else:
                until = rest[index + 1]
            index += 2
            continue
        return (
            _Parsed(feature, until, json_mode),
            driver_results.usage(
                f"unrecognized argument {token!r}",
                "usage: heddle drive [--feature <slug>] [--until <stage>] [--json]",
            ),
        )
    return _Parsed(feature, until, json_mode), None


def _emit(result: HeddleResult, json_mode: bool) -> int:
    _print(result, json_mode)
    return int(result.exit_code)


def _print(result: HeddleResult, json_mode: bool) -> None:
    if json_mode:
        _write_stdout(json.dumps(result.to_envelope()))
        return
    if result.error is not None:
        error = result.error
        _write_stdout(f"heddle: error[{error.code}]: {error.message}")
        _write_stdout(f"  hint: {error.hint}")
        # Same channels status.py's failure renderer prints — before this,
        # text mode silently dropped next_actions/diagnostics, which is
        # exactly where the blocked envelope carries its remediation
        # commands, as observed at a real pending-decisions pause.
        for action in result.next_actions:
            _write_stdout(f"  next: {action.command} — {action.reason}")
        for diagnostic in result.diagnostics:
            _write_stdout(f"  note: {diagnostic.code}: {diagnostic.message}")
    else:
        data = result.data or {}
        _write_stdout(f"drive [{data.get('feature')}] {data.get('status')}")


def _write_stdout(line: str) -> None:
    sys.stdout.write(f"{line}\n")
