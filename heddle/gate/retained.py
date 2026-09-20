"""Immutable completed-response captures and explicit native interpretation.

No inferred findings, permissive Markdown parser, or model repair invocation.
The capture owns original bytes and frozen validation inputs; native codecs
continue to own the accepted interpretation.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from heddle.gate.results import (
    MAX_REVIEW_BYTES,
    MAX_STREAM_BYTES,
    REVIEW_CONTENT_VERSION,
    REVIEW_RESULT_VERSION,
    ReviewInvocation,
    ReviewResult,
    bind_review_invocation,
    canonical_json,
    decode_review_content,
    decode_review_invocation,
    decode_review_result,
    read_bounded,
    review_output_contract,
    serialize_review_result,
    strict_json,
)
from heddle.gate.types import (
    ActiveRule,
    ExecutionResult,
    PreparedGateRun,
    PriorReviewArtifact,
    PromptTransport,
    ReviewDecision,
    ReviewValidationInputs,
)
from heddle.gate.validation import validate_review
from heddle.io.process import Termination

CAPTURE_SCHEMA = "heddle.retained-review/v1"
MAX_CAPTURE_BYTES = 4 * MAX_STREAM_BYTES


@dataclass(frozen=True)
class CapturedReview:
    invocation: ReviewInvocation
    overridden_axes: tuple[str, ...]
    prompt: PromptTransport
    schema_json: str
    validation: ReviewValidationInputs
    raw_stream: bytes
    selected_response: bytes | None
    response_channel: str
    warnings: tuple[str, ...]


def capture_review(
    prepared: PreparedGateRun, execution: ExecutionResult, warning: str
) -> CapturedReview:
    if (
        execution.monitor.termination is not Termination.COMPLETED
        or execution.cli_exit != 0
        or execution.failure_reason is not None
    ):
        raise ValueError("only a completed provider response can be retained")
    invocation = bind_review_invocation(prepared, prepared.feature)
    assert invocation.assignment_id is not None
    assert prepared.output_contract is not None
    selected = execution.msg_out_path
    return CapturedReview(
        invocation,
        tuple(sorted(prepared.invocation.explicit_axes)),
        prepared.transport,
        prepared.output_contract.schema_json,
        ReviewValidationInputs(
            prepared.gate,
            prepared.ac_ids,
            prepared.active_rules,
            invocation.output_contract_version,
            invocation.assignment_id,
            prepared.prior_reviews,
            prepared.review_decisions,
            prepared.required_prior_references,
        ),
        read_bounded(execution.raw_out_path, MAX_STREAM_BYTES),
        read_bounded(selected, MAX_REVIEW_BYTES)
        if selected is not None and selected.exists()
        else None,
        execution.response_channel
        or f"{invocation.execution.cli}.native-selected-response",
        (warning,),
    )


def _bytes(value: bytes | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "base64": base64.b64encode(value).decode("ascii"),
        "bytes": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
    }


def serialize_capture(capture: CapturedReview) -> bytes:
    targets = capture.validation.required_prior_references
    value = {
        "schema": CAPTURE_SCHEMA,
        "invocation": asdict(capture.invocation),
        "overridden_axes": capture.overridden_axes,
        "prompt": asdict(capture.prompt),
        "schema_json": capture.schema_json,
        "validation": {
            "ac_ids": capture.validation.ac_ids,
            "active_rules": [asdict(r) for r in capture.validation.active_rules]
            if capture.validation.active_rules is not None
            else None,
            "prior_reviews": [
                {
                    "run_id": prior.run_id,
                    "path": prior.path,
                    "input_hash": prior.input_hash,
                    "result": serialize_review_result(prior.result).decode("utf-8"),
                }
                for prior in capture.validation.prior_reviews
            ],
            "review_decisions": [
                asdict(d) for d in capture.validation.review_decisions
            ],
            **({"required_prior_references": targets} if targets is not None else {}),
        },
        "completion": {"exit_code": 0, "termination": "completed"},
        "response": {
            "raw_stream": _bytes(capture.raw_stream),
            "selected_response": _bytes(capture.selected_response),
            "channel": capture.response_channel,
        },
        "warnings": capture.warnings,
    }
    raw = canonical_json(value, newline=True)
    if len(raw) > MAX_CAPTURE_BYTES:
        raise ValueError("retained review capture exceeds its byte limit")
    return raw


def _decode_bytes(value: Any, maximum: int) -> bytes:
    if not isinstance(value, dict) or set(value) != {"base64", "bytes", "sha256"}:
        raise ValueError("captured response needs exact bytes and hash")
    raw = base64.b64decode(value["base64"], validate=True)
    if (
        type(value["bytes"]) is not int
        or len(raw) != value["bytes"]
        or len(raw) > maximum
        or hashlib.sha256(raw).hexdigest() != value["sha256"]
    ):
        raise ValueError("captured response byte identity differs")
    return raw


def decode_capture(raw: bytes) -> CapturedReview:
    value = strict_json(raw, maximum=MAX_CAPTURE_BYTES)
    try:
        invocation = decode_review_invocation(value["invocation"])
        contract = review_output_contract(invocation.gate)
        if (
            value["schema"] != CAPTURE_SCHEMA
            or value["completion"] != {"exit_code": 0, "termination": "completed"}
            or type(value["completion"]["exit_code"]) is not int
            or invocation.output_contract_version != REVIEW_CONTENT_VERSION
            or value["schema_json"] != contract.schema_json
            or invocation.output_contract_sha256 != contract.schema_sha256
        ):
            raise ValueError("captured review contract or completion differs")
        validation, response = value["validation"], value["response"]
        targets = None
        if "required_prior_references" in validation:
            operand = validation["required_prior_references"]
            if not isinstance(operand, list) or any(
                not isinstance(row, list)
                or len(row) != 2
                or not all(isinstance(item, str) and item.strip() for item in row)
                for row in operand
            ):
                raise ValueError("invalid captured prior target references")
            targets = tuple((row[0], row[1]) for row in operand)
            if len(set(targets)) != len(targets):
                raise ValueError("captured prior target references must be distinct")
        prior_reviews = []
        for prior in validation["prior_reviews"]:
            result = decode_review_result(prior["result"].encode("utf-8"))
            prior_reviews.append(
                PriorReviewArtifact(
                    prior["path"],
                    prior["result"],
                    prior["run_id"],
                    prior["input_hash"],
                    result,
                )
            )
        assert invocation.assignment_id is not None
        capture = CapturedReview(
            invocation,
            tuple(value["overridden_axes"]),
            PromptTransport(**value["prompt"]),
            value["schema_json"],
            ReviewValidationInputs(
                invocation.gate,
                tuple(validation["ac_ids"]),
                tuple(ActiveRule(**r) for r in validation["active_rules"])
                if validation["active_rules"] is not None
                else None,
                invocation.output_contract_version,
                invocation.assignment_id,
                tuple(prior_reviews),
                tuple(ReviewDecision(**d) for d in validation["review_decisions"]),
                targets,
            ),
            _decode_bytes(response["raw_stream"], MAX_STREAM_BYTES),
            _decode_bytes(response["selected_response"], MAX_REVIEW_BYTES)
            if response["selected_response"] is not None
            else None,
            response["channel"],
            tuple(value["warnings"]),
        )
        if (
            not capture.warnings
            or not all(isinstance(w, str) and w.strip() for w in capture.warnings)
            or not isinstance(capture.response_channel, str)
            or not capture.response_channel
            or sorted(set(capture.overridden_axes)) != list(capture.overridden_axes)
            or not set(capture.overridden_axes) <= {"cli", "model", "reasoning_effort"}
            or not all(isinstance(ac, str) and ac for ac in capture.validation.ac_ids)
            or canonical_json(value)
            != canonical_json(
                strict_json(serialize_capture(capture), maximum=MAX_CAPTURE_BYTES)
            )
        ):
            raise ValueError("captured review shape or identity differs")
        return capture
    except (KeyError, TypeError, AttributeError, AssertionError) as error:
        raise ValueError(f"invalid retained review capture: {error}") from error


def interpret_capture(capture: CapturedReview, content: dict[str, Any]) -> ReviewResult:
    contract = review_output_contract(capture.invocation.gate)
    decoded = decode_review_content(canonical_json(content), contract)
    validate_review(decoded, capture.validation)
    return ReviewResult(REVIEW_RESULT_VERSION, capture.invocation, decoded)


def capture_view(capture: CapturedReview) -> str:
    """Human view is convenient, never the original-byte authority."""
    selected = capture.selected_response
    return (
        "# Retained review — interpretation required\n\n"
        + "\n".join(capture.warnings)
        + "\n\nThe immutable capture contains the complete original bytes. "
        "This UTF-8 view replaces undecodable bytes for display only. "
        "Findings are unavailable until native lead interpretation.\n\n"
        + (
            selected.decode("utf-8", errors="replace")
            if selected is not None
            else "Selected response channel was absent. Complete raw stream view:\n\n"
            + capture.raw_stream.decode("utf-8", errors="replace")
        )
    )
