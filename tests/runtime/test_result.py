"""
Contract tests for heddle/contracts/result.py — Milestone 1.

Covers: AC-5 (exit-code/severity/error vocabularies pinned), AC-4
(envelope-level invariants: five wire keys, data xor error).
Behavior contract: core-contract-rails

preimplementation state: this module imports heddle.contracts.result, which does not
exist
until Milestone 1 — collection errors here are the expected pre-implementation
signal.
"""

from __future__ import annotations

import json

import pytest

from heddle.contracts.operations import CommandAction, ManualAction, Status
from heddle.contracts.result import (
    ENVELOPE_SCHEMA_VERSION,
    ERROR_CODES,
    CompletionRepair,
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)

# REQ-7: the blocking conditions (workflow-model §12.1) + the result codes codes +
# not-implemented (command availability) + validation's non-blocking validate umbrella
# `ledger-drift`.
# Transcribed literally — value-lock, not import echo.
PINNED_ERROR_CODES = frozenset(
    {
        "not-authorized",
        "gate-not-converged",
        "pending-decisions",
        "verification-missing",
        "milestone-out-of-sequence",
        "task-incomplete",
        "workspace-invalid",
        # additive vocabulary entry sanctioned by authoring CP1 R2 (workspace
        # collision): the
        # feature-start collision code — a healthy workspace already
        # occupies the slug.
        "workspace-exists",
        "awaiting-human-authorization",
        # additive vocabulary entry sanctioned by completion (kickoff-guidance-
        # integration REQ-5): the completion HITL handoff blocker, a result codes
        # blocking condition and therefore an error code.
        "awaiting-human-completion",
        "principles-not-ratified",
        # additive vocabulary entry sanctioned by review attempts/review accounting
        # (review gate cap)
        "gate-cap-reached",
        "no-progress",
        "attempt-cap",
        "usage",
        "internal",
        "conflict",
        "feature-ambiguous",
        "not-implemented",
        "ledger-drift",
        # decision deferral-C/AD-14: truthful route input vs immutable resolution
        # history.
        "invalid-route",
        "decision-already-resolved",
        "verification-failed",
        "disposition-batch-invalid",
    }
)


class TestAC5Vocabularies:
    """AC-5: exit-code and severity vocabularies are pinned."""

    def test_exit_codes_pinned_exactly(self):
        # workflow-model §12.3 — the six process exit codes.
        expected = {
            "OK": 0,
            "INTERNAL": 1,
            "USAGE": 2,
            "FATAL": 3,
            "ADVISORY": 4,
            "CONFLICT": 5,
        }
        assert {member.name: member.value for member in ExitCode} == expected

    def test_severity_values_pinned_exactly(self):
        # workflow-model §12.3 — fatal | advisory | info.
        assert {member.value for member in Severity} == {"fatal", "advisory", "info"}

    def test_error_codes_equal_req7_vocabulary(self):
        assert ERROR_CODES == PINNED_ERROR_CODES
        assert len(ERROR_CODES) == 24

    def test_unknown_error_code_raises_value_error(self):
        with pytest.raises(ValueError):
            HeddleError(code="bogus-code", message="m", hint="h")

    def test_every_pinned_code_constructs(self):
        for code in PINNED_ERROR_CODES:
            error = HeddleError(code=code, message="m", hint="h")
            assert error.code == code


class TestAC5Serialization:
    """AC-5: constituent wire shapes."""

    def test_next_action_serializes_command_reason(self):
        # workflow-model §12.3 / REQ-13.
        result = HeddleResult.success(
            {"k": "v"},
            next_actions=(
                NextAction(action=CommandAction(Status(feature="demo")), reason="r"),
            ),
        )
        entry = result.to_envelope()["next_actions"][0]
        assert entry == {
            "action": {
                "kind": "command",
                "operation": {
                    "name": "status",
                    "arguments": {"feature": "demo", "all_features": False},
                },
            },
            "command": "heddle status --feature demo",
            "reason": "r",
        }

    def test_diagnostic_serializes_severity_code_message(self):
        result = HeddleResult.success(
            {"k": "v"},
            diagnostics=(Diagnostic(severity=Severity.INFO, code="c", message="m"),),
        )
        entry = result.to_envelope()["diagnostics"][0]
        assert entry == {"severity": "info", "code": "c", "message": "m"}

    def test_error_object_serializes_exactly_code_message_hint(self):
        result = HeddleResult.failure(
            HeddleError(code="usage", message="m", hint="h"),
            exit_code=ExitCode.USAGE,
        )
        assert result.to_envelope()["error"] == {
            "code": "usage",
            "message": "m",
            "hint": "h",
            "details": {},
        }

    def test_error_details_preserve_structured_context(self):
        result = HeddleResult.failure(
            HeddleError(
                code="verification-failed",
                message="m",
                hint="h",
                details={"scope": "m1", "recorded": True},
            ),
            exit_code=ExitCode.FATAL,
        )
        assert result.to_envelope()["error"]["details"] == {
            "scope": "m1",
            "recorded": True,
        }

    def test_completion_repair_serializes_the_closed_nested_contract(self):
        repair = CompletionRepair(
            check="spec-stamp",
            cause="invalid-frontmatter",
            path="docs/features/runtime/demo.md",
            code="workspace-invalid",
            message="frontmatter is malformed",
            hint="repair the YAML frontmatter",
        )

        assert repair.to_payload() == {
            "check": "spec-stamp",
            "cause": "invalid-frontmatter",
            "path": "docs/features/runtime/demo.md",
            "code": "workspace-invalid",
            "message": "frontmatter is malformed",
            "hint": "repair the YAML frontmatter",
        }
        with pytest.raises(ValueError, match="repository-relative"):
            CompletionRepair(
                "spec-stamp",
                "invalid-frontmatter",
                "../outside.md",
                "workspace-invalid",
                "frontmatter is malformed",
                "repair the YAML frontmatter",
            )


class TestAC4EnvelopeInvariants:
    """AC-4 (envelope-level): five wire keys, data xor error, JSON-safe."""

    def test_success_envelope_has_exactly_five_keys(self):
        envelope = HeddleResult.success({"k": "v"}).to_envelope()
        assert set(envelope.keys()) == {
            "ok",
            "schema_version",
            "data",
            "diagnostics",
            "next_actions",
        }
        assert envelope["ok"] is True
        assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION

    def test_failure_envelope_has_exactly_five_keys(self):
        envelope = HeddleResult.failure(
            HeddleError(code="internal", message="m", hint="h"),
            exit_code=ExitCode.INTERNAL,
        ).to_envelope()
        assert set(envelope.keys()) == {
            "ok",
            "schema_version",
            "error",
            "diagnostics",
            "next_actions",
        }
        assert envelope["ok"] is False

    def test_envelope_schema_version_spelling(self):
        # Decision Log: heddle.<artifact>/v<N> naming scheme.
        assert ENVELOPE_SCHEMA_VERSION == "heddle.envelope/v2"

    def test_exit_code_is_not_serialized(self):
        envelope = HeddleResult.success({}, exit_code=ExitCode.OK).to_envelope()
        assert "exit_code" not in envelope

    def test_both_data_and_error_raises(self):
        with pytest.raises(ValueError):
            HeddleResult(
                ok=True,
                data={"k": "v"},
                error=HeddleError(code="usage", message="m", hint="h"),
                diagnostics=(),
                next_actions=(),
                exit_code=ExitCode.OK,
            )

    def test_neither_data_nor_error_raises(self):
        with pytest.raises(ValueError):
            HeddleResult(
                ok=True,
                data=None,
                error=None,
                diagnostics=(),
                next_actions=(),
                exit_code=ExitCode.OK,
            )

    def test_envelope_is_json_serializable(self):
        envelope = HeddleResult.success(
            {"k": "v"},
            diagnostics=(Diagnostic(Severity.ADVISORY, "c", "m"),),
            next_actions=(NextAction(ManualAction("heddle help"), "r"),),
        ).to_envelope()
        assert json.loads(json.dumps(envelope)) == envelope
