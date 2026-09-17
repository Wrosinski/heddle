"""
Contract tests for heddle/contracts/schemas.py — Milestone 1.

Covers: AC-9 (gate manifest matches the live engine), AC-10
(vocabulary half — .heddle.yaml v0 pinned), AC-14 (state-vocabulary constants
value-locked), plus the import-boundary rule extracted at spec review
(heddle/runtime/ imports stdlib only at runtime).
Behavior contract: core-contract-rails

preimplementation state: imports heddle.contracts.schemas, which does not exist until
Milestone 1 — collection errors are the expected pre-implementation signal.
"""

from __future__ import annotations

import ast
import dataclasses
import sys
from pathlib import Path

import pytest

from heddle.contracts import schemas
from heddle.contracts.result import ERROR_CODES
from heddle.kernel.project_config import load_project_config
from tests.runtime.policy_helpers import (
    assert_runtime_write_seams,
    import_references,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestAC14StateVocabularyValueLock:
    """AC-14: each constant equals its literal pinned tuple (edit-detection).

    Literals are transcribed from the Feature Spec §Architecture
    (workflow-model §10.1, §4–§8, §12.1; workspace layout) — never imported, so a
    transcription typo in schemas.py fails loudly even when no fixture
    happens to exercise it.
    """

    def test_state_schema_id(self):
        known_state_schemas = getattr(
            schemas, "STATE_SCHEMA_VERSIONS", (schemas.STATE_SCHEMA_ID,)
        )
        current_schema = getattr(
            schemas, "CURRENT_STATE_SCHEMA", schemas.STATE_SCHEMA_ID
        )
        assert known_state_schemas == ("heddle.state/v9", "heddle.state/v10")
        assert schemas.STATE_SCHEMA_V9 == "heddle.state/v9"
        assert schemas.STATE_SCHEMA_V10 == "heddle.state/v10"
        assert current_schema == "heddle.state/v10"
        assert schemas.STATE_SCHEMA_ID == current_schema

    def test_state_schema_versions_and_source_baseline(self):
        """Local-history AC-1/AC-8: current v10 is additive over readable v9."""
        from heddle.kernel.state import parse_source_baseline

        assert getattr(schemas, "STATE_SCHEMA_V10", None) == "heddle.state/v10", (
            "FAIL local-history AC-1: the v10 state contract is not declared"
        )
        assert schemas.STATE_SCHEMA_VERSIONS == (
            "heddle.state/v9",
            "heddle.state/v10",
        )
        assert schemas.CURRENT_STATE_SCHEMA == "heddle.state/v10"
        assert "source_baseline" in getattr(schemas, "STATE_V10_TOP_LEVEL_KEYS", ())
        for length in (40, 64):
            assert (
                parse_source_baseline({"kind": "git-commit", "oid": "a" * length}).oid
                == "a" * length
            )
        for oid in ("a", "a" * 39, "a" * 41, "A" * 40, "g" * 40):
            with pytest.raises(ValueError, match="full 40- or 64-character"):
                parse_source_baseline({"kind": "git-commit", "oid": oid})

    def test_stages(self):
        assert schemas.STAGES == (
            "specify",
            "spec-review",
            "plan-review",
            "scaffold",
            "implement",
            "peer-review",
            "robustness",
            "complete",
        )

    def test_flow_modes(self):
        assert schemas.FLOW_HITL == "hitl"
        assert schemas.FLOW_AUTO == "auto"
        assert schemas.FLOW_MODES == ("hitl", "auto")
        assert schemas.FLOW_CHANGE_PENDING == "pending"
        assert schemas.FLOW_CHANGE_APPLIED == "applied"
        assert schemas.FLOW_CHANGE_SUPERSEDED == "superseded"
        assert schemas.POLICY_JOURNAL_FIELDS == (
            "title",
            "question",
            "options",
            "choice",
            "rationale",
            "basis",
            "reversibility",
            "confidence",
            "review_flag",
            "scope",
            "routes_to",
        )
        assert schemas.POLICY_RESOLUTION_REQUIRED_FIELDS == (
            "id",
            *(
                field
                for field in schemas.POLICY_JOURNAL_FIELDS
                if field != "review_flag"
            ),
        )

    def test_milestone_statuses(self):
        assert schemas.MILESTONE_STATUSES == ("todo", "current", "done")

    def test_state_top_level_keys(self):
        assert schemas.STATE_V9_TOP_LEVEL_KEYS == (
            "schema",
            "feature",
            "spec",
            "revision",
            "created",
            "updated",
            "stage",
            "authorized_through",
            "commands",
            "milestones",
            "gates",
            "verifications",
            "decisions",
            "sessions",
            "feature_inputs",
            "completion",
            "feature_policy",
            "review_assignments",
        )

    def test_state_optional_top_level_keys(self):
        assert schemas.STATE_V9_OPTIONAL_TOP_LEVEL_KEYS == (
            "flow",
            "authorizations",
            "flow_changes",
            "intake",
            "policy_history",
            "assessments",
        )

    def test_state_required_keys(self):
        assert schemas.STATE_REQUIRED_KEYS == (
            "schema",
            "feature",
            "spec",
            "revision",
            "created",
            "updated",
            "stage",
            "authorized_through",
        )

    def test_command_keys(self):
        assert schemas.COMMAND_KEYS == (
            "smoke_test",
            "dev_setup",
            "test_command",
            "lint_command",
            "acceptance_test",
            "live_e2e_test",
        )

    def test_milestone_keys(self):
        assert schemas.MILESTONE_KEYS == (
            "id",
            "title",
            "satisfies",
            "depends_on",
            "owns",
            "complexity",
            "estimated_hours",
            "verification",
            "status",
            "tasks",
        )

    def test_task_keys(self):
        assert schemas.TASK_KEYS == ("id", "text", "status")

    def test_gate_keys(self):
        assert schemas.GATE_KEYS == ("gate", "scope", "runs")

    def test_gate_run_keys(self):
        assert schemas.GATE_RUN_KEYS == (
            "run_id",
            "at",
            "cli",
            "artifact",
            "input_hash",
            "verdict",
            "findings",
            "report_findings",
        )

    def test_verification_keys(self):
        assert schemas.VERIFICATION_KEYS == (
            "scope",
            "command",
            "exit_code",
            "at",
            "log",
            "evidence",
        )

    def test_verification_provenance_keys(self):
        """Historical node now asserts the v8 evidence replacement."""
        assert schemas.VERIFICATION_EVIDENCE_KEYS == (
            "schema",
            "before",
            "after",
        )

    def test_verification_evidence_keys(self):
        self.test_verification_provenance_keys()

    def test_decision_keys(self):
        assert schemas.DECISION_KEYS == (
            "id",
            "kind",
            "class",
            "source",
            "title",
            "question",
            "options",
            "recommendation",
            "status",
            "resolution",
            "rationale",
            "resolution_source",
            "routes_to",
            "created_at",
            "resolved_at",
            "origin_run_id",
            "origin_finding_id",
            "target_stage",
            "target_blocker",
            "supersedes",
            "smoke_disposition",
        )

    def test_session_keys(self):
        assert schemas.SESSION_PROGRESS_FIELD_TYPES == {
            "stage": str,
            "revision_before": int,
            "revision_after": int,
            "advanced": bool,
        }
        assert schemas.SESSION_PROGRESS_KEYS == (
            "stage",
            "revision_before",
            "revision_after",
            "advanced",
        )
        assert schemas.SESSION_KEYS == (
            "started_at",
            "ended_at",
            "completed",
            "started",
            "key_context",
            "next_steps",
            "blockers",
            "stage",
            "revision_before",
            "revision_after",
            "advanced",
        )

    def test_blocking_conditions(self):
        assert schemas.BLOCKING_CONDITIONS == (
            "not-authorized",
            "gate-not-converged",
            "pending-decisions",
            "verification-missing",
            "milestone-out-of-sequence",
            "task-incomplete",
            "workspace-invalid",
            "awaiting-human-authorization",
            # additive vocabulary entry sanctioned by completion (kickoff-guidance-
            # integration REQ-5): the all-tier HITL completion handoff — derived
            # at `complete` until a qualifying completion fact exists.
            "awaiting-human-completion",
            "principles-not-ratified",
            "gate-cap-reached",
            "no-progress",
            "attempt-cap",
        )

    def test_workspace_layout_names(self):
        # workspace layout: the feature-workspace layout.
        assert schemas.WORKSPACE_PLAN == "plan.md"
        assert schemas.WORKSPACE_STATE == "state.yaml"
        assert schemas.WORKSPACE_BRIEF == "brief.md"
        assert schemas.WORKSPACE_REVIEWS_DIR == "reviews"
        assert schemas.WORKSPACE_VERIFICATION_DIR == "verification"

    def test_blocking_conditions_subset_of_error_codes(self):
        # result codes: the §12.1 vocabulary is a subset of the error-code vocabulary.
        assert set(schemas.BLOCKING_CONDITIONS) <= ERROR_CODES


class TestAC10HeddleYamlVocabulary:
    """AC-10 (vocabulary half): the configuration ratified spelling."""

    def test_sections(self):
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

    def test_layout_keys(self):
        assert schemas.LAYOUT_KEYS == (
            "specs",
            "plans",
            "prompts",
            "schemas",
            "tests",
            "source",
        )

    def test_host_command_keys(self):
        assert schemas.HOST_COMMAND_KEYS == ("test", "lint", "typecheck")

    def test_agents_keys(self):
        assert schemas.AGENTS_KEYS == ("claude", "codex")

    def test_gates_keys(self):
        assert schemas.GATES_KEYS == ("enabled",)

    def test_autopilot_keys(self):
        assert schemas.AUTOPILOT_KEYS == (
            "flow",
            "notify_command",
            "notify_timeout_s",
            "max_turns",
            "max_budget_usd",
            "test_command",
        )


class TestAC9GateManifestTripwire:
    """AC-9: the contract and the live engine cannot drift silently."""

    def test_gate_manifest_fields_pinned_literally(self):
        # gate manifest / workflow-model §6.1 — value-lock independent of the engine.
        assert schemas.GATE_MANIFEST_FIELDS == (
            "name",
            "requires_plan",
            "requires_milestone",
            "requires_diff",
            "supported_clis",
            "tool_permissions",
            "output_contract",
            "prompt_template",
            "milestone_output_naming",
            "context_builders",
            "preflight_applicable",
            "default_cli",
            "requires_spec",
            "source_selector",
            "lane_policies",
        )

    def test_gate_manifest_fields_match_live_gate_type(self):
        # REQ-12: ordered equality with the engine's GateType — fails if
        # either side gains, loses, renames, or reorders a field. The gate
        # import lives here, test-time only (schemas.py stays gate-free).
        from heddle.gate.types import GateType

        assert schemas.GATE_MANIFEST_FIELDS == tuple(
            field.name for field in dataclasses.fields(GateType)
        )


class TestRuntimeImportBoundarySelection:
    """Normal repository verification must retain the canonical boundary rail."""

    BOUNDARY_NODE = (
        "tests/runtime/test_boundaries.py::TestRuntimeImportBoundary::"
        "test_runtime_modules_import_only_declared_roots"
    )

    def test_normal_commands_select_canonical_runtime_boundary(self):
        config = load_project_config(REPO_ROOT)
        assert self.BOUNDARY_NODE in config.commands["test"].split()
        assert config.autopilot.test_command is not None
        assert self.BOUNDARY_NODE in config.autopilot.test_command.split()


class TestContractsLeafBoundary:
    """
    Pre-authoring hardening dependency boundary: heddle/contracts/ holds the contract
    leaves
        every layer imports, with only explicit same-layer dependencies. The
        package must never grow a dependency on any other heddle package or
        any third party (not even yaml) — that property is what lets kernel,
        runtime, gate, and driver all import it without layering cycles. This
        rail replaces the coverage the two modules had while they lived under
        heddle/runtime/ (core boundary above; reader REQ-18 named them as the
        kernel's only permitted runtime imports).
    """

    def test_contracts_modules_are_stdlib_only_leaves(self):
        contracts_dir = REPO_ROOT / "heddle" / "contracts"
        assert contracts_dir.is_dir(), "FAIL: heddle/contracts/ package missing"
        offenders: list[str] = []
        for module_path in sorted(contracts_dir.rglob("*.py")):
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                for module_ref in import_references(node):
                    root = module_ref.split(".")[0]
                    if (
                        module_path.relative_to(contracts_dir).as_posix(),
                        module_ref,
                    ) in {
                        ("review_authority.py", "heddle.contracts.gates"),
                        ("feature_policy.py", "heddle.contracts.gates"),
                        ("review_authority.py", "heddle.contracts.document_reviews"),
                        ("review_assignments.py", "heddle.contracts.feature_policy"),
                        ("operations.py", "heddle.contracts.decisions"),
                        ("result.py", "heddle.contracts.operations"),
                    }:
                        continue
                    if root not in sys.stdlib_module_names:
                        offenders.append(f"{module_path.name}: {module_ref}")
        assert not offenders, (
            "FAIL: heddle/contracts modules must import stdlib only, with "
            "the explicit catalog, assignment, operation and result contract edges; "
            "no other heddle.*, yaml, or third party. The contract layer is the "
            "bottom of the import graph (pre-M6 hardening dependency boundary); "
            f"offending imports: {offenders}"
        )


class TestEscalationTaxonomy:
    """input schema: the escalation taxonomy is single-sourced here — registry,
    one-line summaries, and the batch-class derivation stay in lockstep."""

    def test_registry_and_summaries_cover_the_eight_classes(self):
        assert set(schemas.ESCALATION_REGISTRY.keys()) == set(range(1, 9))
        assert set(schemas.ESCALATION_CLASS_SUMMARIES.keys()) == set(range(1, 9))

    def test_class_4_is_the_one_blocking_condition_class(self):
        assert schemas.ESCALATION_REGISTRY[4] == "blocking-condition"
        assert schemas.DECISION_BATCH_CLASSES == (1, 2, 3, 5, 6, 7, 8)

    def test_batch_sources_vocabulary(self):
        assert schemas.DECISION_BATCH_SOURCES == ("session", "driver")

    def test_derived_blocker_map_exact_order_and_membership(self):
        # Insertion order is CONTRACT: the resolve path writes one
        # continuation override per blocker in this order (class 3 pair),
        # and every key must be a real §12.1 blocking condition.
        assert list(schemas.DERIVED_BLOCKER_CLASSES.items()) == [
            ("gate-cap-reached", 3),
            ("no-progress", 6),
            ("attempt-cap", 7),
        ]
        assert set(schemas.DERIVED_BLOCKER_CLASSES) <= set(schemas.BLOCKING_CONDITIONS)

    @pytest.mark.parametrize(
        ("kind", "cls", "blocker", "resolution"),
        [
            ("stage-cap", 3, "gate-cap-reached", "continue-stage"),
            ("session-cap", 6, "no-progress", "continue-session"),
            ("session-cap", 7, "attempt-cap", "continue-session"),
        ],
    )
    def test_resolution_retains_exact_cap_target(
        self, run_cli, tmp_path, monkeypatch, kind, cls, blocker, resolution
    ):
        import yaml

        from tests.operational_model_helpers import FEATURE, SPEC, decision
        from tests.tiering_review_helpers import current_host

        _host, path = current_host(tmp_path, monkeypatch)
        document = yaml.safe_load(path.read_text())
        cap = decision(
            "opaque-cap",
            kind=kind,
            source="driver",
            **{"class": cls},
            target_stage="spec-review",
            target_blocker=blocker,
            question="Continue this exact boundary?",
            options=["Continue", "Hold"],
        )
        document["decisions"].append(cap)
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        code, result, _err = run_cli(
            [
                "decisions",
                "resolve",
                "opaque-cap",
                "--feature",
                FEATURE,
                "--kind",
                resolution,
                "--resolution",
                "Continue this boundary",
                "--routes-to",
                SPEC,
                "--json",
            ]
        )
        assert code == 0, result
        after = yaml.safe_load(path.read_text())
        settled = after["decisions"][-1]
        assert (settled["kind"], settled["class"], settled["target_blocker"]) == (
            kind,
            cls,
            blocker,
        )
        assert settled["resolution"]["kind"] == resolution
        assert "continuation_overrides" not in after


class TestRuntimeWriteSinkBan:
    """Runtime write/exec sinks stay confined to declared named seams.

    The shared structural oracle is reused here and by milestone acceptance
    tests; ``test_policy_locks.py`` independently pins its literal allowlist.
    """

    def test_runtime_modules_have_no_write_or_exec_sinks(self):
        assert_runtime_write_seams(REPO_ROOT / "heddle" / "runtime")
