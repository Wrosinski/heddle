"""Atomic authoring surface for milestone-less feature inputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from heddle.contracts import operations as ops
from heddle.contracts.result import HeddleResult
from heddle.kernel.project_config import KernelError
from heddle.kernel.source_manifest import normalize_source_paths
from heddle.kernel.write_path import set_feature_inputs
from heddle.runtime.write_args import parse_write_args, usage_failure
from heddle.runtime.write_path import emit_result, run_state_mutation

_MAX_INPUT_BYTES = 1024 * 1024
_USAGE = "usage: heddle feature inputs set --from-file <path>|- [--feature <slug>]"


def parse_feature_inputs(payload: bytes) -> tuple[str, ...]:
    """Decode one complete JSON array and return its canonical path set."""
    if len(payload) > _MAX_INPUT_BYTES:
        raise ValueError("feature input payload exceeds 1 MiB")
    try:
        text = payload.decode("utf-8")
        start = len(text) - len(text.lstrip())
        value, end = json.JSONDecoder().raw_decode(text, start)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(
            f"feature input payload is not one UTF-8 JSON value: {error}"
        ) from error
    if text[end:].strip():
        raise ValueError("feature input payload carries trailing JSON data")
    if not isinstance(value, list) or not value:
        raise ValueError("feature input payload must be a non-empty JSON array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("feature input paths must be non-blank strings")
    try:
        return normalize_source_paths(value)
    except KernelError as error:
        raise ValueError(error.message) from error


def run_feature_inputs_set(args: list[str], json_mode: bool) -> int:
    parsed, values, _positionals, failure = parse_write_args(
        args,
        value_flags={
            "--from-file": (
                "--from-file requires a path or -",
                _USAGE,
            )
        },
        unknown_hint=_USAGE,
        allow_positionals=False,
    )
    if failure is not None:
        return emit_result(failure, json_mode)
    assert parsed is not None
    if parsed.dry_run:
        return emit_result(
            usage_failure("feature inputs set does not support --dry-run", _USAGE),
            json_mode,
        )
    source = values["--from-file"]
    if source is None:
        return emit_result(
            usage_failure("feature inputs set requires --from-file", _USAGE),
            json_mode,
        )
    try:
        payload = _read_payload(source)
        paths = parse_feature_inputs(payload)
    except (OSError, ValueError) as error:
        return emit_result(
            usage_failure(
                f"invalid feature input payload {source!r}: {error}",
                "provide one non-empty UTF-8 JSON array of safe repo-relative paths",
            ),
            json_mode,
        )
    from heddle.runtime.application import execute

    return emit_result(
        execute(
            ops.FeatureInputsSet(
                paths,
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
            )
        ),
        json_mode,
    )


def feature_inputs_set(operation: ops.FeatureInputsSet) -> HeddleResult:
    try:
        paths = normalize_source_paths(operation.paths)
    except KernelError as error:
        return usage_failure(error.message, error.hint)
    return run_state_mutation(
        operation,
        command_name="feature inputs set",
        render=lambda document: set_feature_inputs(document, paths),
        extra={"feature_inputs": list(paths)},
    )


def _read_payload(source: str) -> bytes:
    if source == "-":
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        value = stream.read(_MAX_INPUT_BYTES + 1)
        return value.encode("utf-8") if isinstance(value, str) else value
    with Path(source).open("rb") as stream:
        return stream.read(_MAX_INPUT_BYTES + 1)
