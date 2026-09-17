"""
Feature acceptance test — core Contract Rails, all ACs end-to-end.

One test method per AC (AC-level pass/fail granularity), exercising the REAL
installed `heddle` console script via subprocess — true process exit codes,
true stdout/stderr separation, zero mocks (core has no external services,
Assumption A3). Design and success criteria approved at the test scaffolding
checkpoint: the run passes iff every AC-named test passes (exit 0). The one
scoped exception: AC-4's injected-internal-error branch is verified at
milestone level (test_dispatch.py) — a subprocess cannot inject a handler
exception without a production debug seam.

Command: .venv/bin/python -m pytest tests/runtime/test_acceptance.py -q
Behavior contract: core-contract-rails

Red-phase note (historical): until Milestone 2 this module was gated by a
pytest.importorskip on heddle.runtime.dispatch (same rationale as
test_dispatch.py). The guard was retired to a hard import at feature
completion (Checkpoint 2 configuration / review) so an import-time break
fails loudly instead of silently skipping the acceptance suite.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Hard import (Checkpoint 2 configuration): the dispatcher must be importable for the
# acceptance suite to count at all — an import break fails loudly here.
import heddle.runtime.dispatch  # noqa: F401
from heddle.contracts import schemas
from heddle.contracts.result import (
    ERROR_CODES,
    ExitCode,
    HeddleError,
    Severity,
)
from heddle.runtime.contracts import (
    COMMAND_SURFACE,
    PERF_CI_CEILING_S,
    PERF_WARM_TARGET_MS,
    build_manifest,
)

HEDDLE_BIN = Path(sys.executable).parent / "heddle"

EXPECTED_NAMES = [contract.name for contract in COMMAND_SURFACE]


def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the real console script. Precondition: editable install in .venv."""
    assert HEDDLE_BIN.is_file(), (
        f"FAIL: {HEDDLE_BIN} missing — run the dev setup (pip install -e '.[dev]')"
    )
    return subprocess.run(
        [str(HEDDLE_BIN), *argv], capture_output=True, text=True, timeout=60
    )


def envelope_of(proc: subprocess.CompletedProcess[str]) -> dict:
    envelope = json.loads(proc.stdout.strip())
    assert isinstance(envelope, dict)
    return envelope


def assert_five_keys(envelope: dict) -> None:
    keys = set(envelope.keys())
    assert keys in (
        {"ok", "schema_version", "data", "diagnostics", "next_actions"},
        {"ok", "schema_version", "error", "diagnostics", "next_actions"},
    ), f"FAIL: not the five wire keys (data xor error): {sorted(keys)}"
    if "error" in envelope:
        assert set(envelope["error"].keys()) == {
            "code",
            "message",
            "hint",
            "details",
        }
        assert envelope["error"]["code"] in ERROR_CODES


def assert_no_json(text: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


@pytest.mark.acceptance
class TestFeatureAcceptance:
    """Does the feature work end-to-end? One test per AC."""

    def test_ac01_help_json_returns_manifest_in_envelope(self):
        proc = run(["help", "--json"])
        assert proc.returncode == 0
        envelope = envelope_of(proc)
        assert_five_keys(envelope)
        assert envelope["ok"] is True
        assert envelope["schema_version"] == "heddle.envelope/v2"
        data = envelope["data"]
        assert set(data.keys()) == {"schema_version", "runtime_version", "commands"}
        assert data["schema_version"] == "heddle.manifest/v0"
        assert data["runtime_version"] == importlib.metadata.version("heddle")

    def test_ac02_manifest_covers_full_surface_across_process_boundary(self):
        # The wire manifest equals the contract module's builder output —
        # the exhaustive per-command payload lock lives in test_contracts.py.
        data = envelope_of(run(["help", "--json"]))["data"]
        assert [entry["name"] for entry in data["commands"]] == EXPECTED_NAMES
        assert len(data["commands"]) == len(COMMAND_SURFACE)
        normalized = json.loads(json.dumps(build_manifest()))
        assert data == normalized

    def test_ac04_envelope_invariants_hold_across_the_dispatcher(self):
        # Success, structured failure, unknown, bare ± --json, bare noun,
        # noun+bad verb, position independence — every JSON response obeys
        # the five-key data-xor-error invariant with REQ-7 codes.
        cases: dict[str, tuple[list[str], int, str | None]] = {
            "success": (["help", "--json"], 0, None),
            # adoption lands init's planner first; dry-run is the safe live
            # dispatcher representative because it never mutates this host.
            "init-preview": (["init", "--dry-run", "--json"], 0, None),
            "unknown": (["frobnicate", "--json"], 2, "usage"),
            "bare-json": (["--json"], 2, "usage"),
            "bare-noun": (["milestone", "--json"], 2, "usage"),
            "bad-verb": (["feature", "bogus", "--json"], 2, "usage"),
        }
        for label, (argv, expected_code, error_code) in cases.items():
            proc = run(argv)
            assert proc.returncode == expected_code, (
                f"FAIL [{label}]: exit {proc.returncode}, expected {expected_code}"
            )
            envelope = envelope_of(proc)
            assert_five_keys(envelope)
            if error_code is not None:
                assert envelope["error"]["code"] == error_code, f"FAIL [{label}]"
        # Bare invocation without --json: usage on stderr, exit 2, stdout
        # empty (review model — usage text belongs on stderr only).
        bare = run([])
        assert bare.returncode == 2
        assert bare.stderr.strip()
        assert not bare.stdout.strip()
        # --json is positionally independent.
        assert envelope_of(run(["--json", "help"])) == envelope_of(
            run(["help", "--json"])
        )

    def test_ac05_exit_and_severity_vocabularies_pinned(self):
        assert {member.name: member.value for member in ExitCode} == {
            "OK": 0,
            "INTERNAL": 1,
            "USAGE": 2,
            "FATAL": 3,
            "ADVISORY": 4,
            "CONFLICT": 5,
        }
        assert {member.value for member in Severity} == {"fatal", "advisory", "info"}
        assert "not-implemented" in ERROR_CODES
        assert "ledger-drift" in ERROR_CODES
        with pytest.raises(ValueError):
            HeddleError(code="out-of-vocabulary", message="m", hint="h")

    def test_ac06_run_gate_joined_the_runtime_envelope(self):
        # `run-gate --json` emits the shared runtime envelope. It replaced the
        # direct engine passthrough and now reports resolution failures consistently.
        # a well-formed envelope on stdout. Driven with an unknown --feature so
        # the outcome is a deterministic resolution failure independent of the
        # repo's own workspaces (the verdict/exit behavior is owned by the
        # dedicated run-gate behavior suites).
        proc = run(
            ["run-gate", "--json", "--feature", "no-such-feature-xyz", "self-review"]
        )
        envelope = envelope_of(proc)
        assert_five_keys(envelope)
        assert envelope["ok"] is False, (
            "FAIL: an unknown feature must be a resolution failure (runtime envelope)"
        )
        assert proc.returncode in (2, 3), (
            f"FAIL: a resolution failure exits 2/3, got {proc.returncode}"
        )

    def test_ac09_gate_manifest_contract_matches_live_engine(self):
        from heddle.gate.types import GateType

        assert schemas.GATE_MANIFEST_FIELDS == tuple(
            field.name for field in dataclasses.fields(GateType)
        )

    def test_ac10_heddle_yaml_vocabulary_pinned_and_exercised(self, corpus):
        assert schemas.HEDDLE_YAML_SECTIONS == (
            "layout",
            "commands",
            "agents",
            "gates",
            "autopilot",
            # Additive adoption host-tooling increment 2 growth (owner-ruled
            # 2026-07-22): optional declared AGENTS.md byte-mirror.
            "sync",
        )
        corpus.assert_heddle_yaml_conforms(corpus.tiny / ".heddle.yaml")
        corpus.assert_heddle_yaml_conforms(corpus.golden / ".heddle.yaml")

    def test_ac11_performance_budgets_encoded(self):
        assert PERF_WARM_TARGET_MS == 300
        assert PERF_CI_CEILING_S == 2.0

    def test_ac12_human_output_paths_are_json_free(self):
        help_proc = run(["help"])
        assert help_proc.returncode == 0
        assert_no_json(help_proc.stdout)
        normalized = " ".join(help_proc.stdout.split())
        for contract in COMMAND_SURFACE:
            assert contract.name in help_proc.stdout
            assert " ".join(contract.summary.split()) in normalized, (
                f"FAIL: help must show {contract.name}'s summary (AC-12)"
            )
        preview_proc = run(["init", "--dry-run"])
        assert preview_proc.returncode == 0
        assert_no_json(preview_proc.stdout)
        assert "init dry-run" in preview_proc.stdout

    def test_ac13_fixture_corpus_is_sanitized(self, corpus):
        # review (b): banned real-host identifiers + positive example-domain
        # check — mirrors test_fixtures.py::TestAC13NoPrivateSourceStrings.
        banned = ("private-source-host", "sourceapp-")
        for path in sorted(corpus.root.rglob("*")):
            if path.is_file():
                haystack = path.read_text(encoding="utf-8").lower() + path.name
                assert not any(token in haystack for token in banned), (
                    f"FAIL: real-host identifier in {path}"
                )
        golden_config = corpus.load_yaml(corpus.golden / ".heddle.yaml")
        assert "example" in golden_config["layout"]["source"]

    def test_ac14_state_vocabulary_value_locked(self):
        # Representative value-locks; the exhaustive per-constant lock lives
        # in test_schemas.py::TestAC14StateVocabularyValueLock.
        known_state_schemas = getattr(
            schemas, "STATE_SCHEMA_VERSIONS", (schemas.STATE_SCHEMA_ID,)
        )
        current_schema = getattr(
            schemas, "CURRENT_STATE_SCHEMA", schemas.STATE_SCHEMA_ID
        )
        assert known_state_schemas == ("heddle.state/v9", "heddle.state/v10")
        assert current_schema == "heddle.state/v10"
        assert schemas.STATE_SCHEMA_ID == current_schema
        assert len(schemas.STAGES) == 8 and schemas.STAGES[-1] == "complete"
        assert len(schemas.STATE_V9_TOP_LEVEL_KEYS) == 18
        assert set(schemas.BLOCKING_CONDITIONS) <= ERROR_CODES
