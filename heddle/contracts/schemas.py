"""Schema vocabularies for the ratified v0 wire formats.

Deliberately plain tuples/frozensets/constants, not typed model classes —
the typed resolver model is the kernel (Feature Spec Decision Log: "Schema
vocabularies as constants, not typed models"). Fixtures and contract tests
assert against these; the kernel later consumes the same constants.

This module imports stdlib only — never ``heddle.gate`` (the tripwire
against ``GateType`` lives in tests/runtime/test_schemas.py, test-time only).
"""

from __future__ import annotations

from pathlib import PurePosixPath

# workflow-model §10.1 — the state.yaml schema ids (ratified spelling).
STATE_SCHEMA_V9 = "heddle.state/v9"
STATE_SCHEMA_V10 = "heddle.state/v10"
CURRENT_STATE_SCHEMA = STATE_SCHEMA_V10
STATE_SCHEMA_ID = CURRENT_STATE_SCHEMA
STATE_SCHEMA_VERSIONS = (STATE_SCHEMA_V9, STATE_SCHEMA_V10)

# workflow-model §2 / §10.1 — the eight stage slugs.
STAGES = (
    "specify",
    "spec-review",
    "plan-review",
    "scaffold",
    "implement",
    "peer-review",
    "robustness",
    "complete",
)

# per-feature flow modes.
FLOW_HITL = "hitl"
FLOW_AUTO = "auto"
FLOW_MODES = (FLOW_HITL, FLOW_AUTO)
FLOW_CHANGE_PENDING = "pending"
FLOW_CHANGE_APPLIED = "applied"
FLOW_CHANGE_SUPERSEDED = "superseded"
POLICY_JOURNAL_FIELDS = (
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
POLICY_RESOLUTION_REQUIRED_FIELDS = (
    "id",
    *(field for field in POLICY_JOURNAL_FIELDS if field != "review_flag"),
)

# workflow-model §4.1 — milestone statuses; tasks reuse the same set.
MILESTONE_STATUSES = ("todo", "current", "done")

# Current state required top-level keys.
STATE_V9_TOP_LEVEL_KEYS = (
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
STATE_V10_TOP_LEVEL_KEYS = (*STATE_V9_TOP_LEVEL_KEYS, "source_baseline")

COMPLETION_KEYS = (
    "accepted_at",
    "authorization_index",
    "verification_indexes",
    "review_run_ids",
    "accepted_smoke_decision_id",
    "spec_stamp",
    "close_suite",
)
SPEC_STAMP_KEYS = ("before_sha256", "after_sha256", "executable")
CLOSE_SUITE_KEYS = ("command", "log", "exit_code")

# Additive current metadata.
STATE_V9_OPTIONAL_TOP_LEVEL_KEYS = (
    "flow",
    "authorizations",
    "flow_changes",
    "intake",
    "policy_history",
    "assessments",
)

# workflow-model §10.1 — the scalar facts; the six collections may be empty,
# but fixture state files carry all fifteen keys explicitly.
STATE_REQUIRED_KEYS = (
    "schema",
    "feature",
    "spec",
    "revision",
    "created",
    "updated",
    "stage",
    "authorized_through",
)

# workflow-model §10.1 — the commands block.
COMMAND_KEYS = (
    "smoke_test",
    "dev_setup",
    "test_command",
    "lint_command",
    "acceptance_test",
    "live_e2e_test",
)

# workflow-model §4.1–4.2 — "tasks" present once expanded.
MILESTONE_KEYS = (
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

# workflow-model §4.1 — expanded task entries.
TASK_KEYS = ("id", "text", "status")

# workflow-model §6.2 — gate facts and their run entries.
GATE_KEYS = ("gate", "scope", "runs")
GATE_RUN_KEYS = (
    "run_id",
    "at",
    "cli",
    "artifact",
    "input_hash",
    "verdict",
    "findings",
    "report_findings",
)
REPORT_FINDING_KEYS = ("finding_id", "title", "severity")
# Completed reviews bind one native result and its captured preparation.
# Error events may lack these fields when preparation/publication did not finish.
GATE_RUN_COMPLETED_KEYS = (
    "review_basis_hash",
    "execution_config",
    "overridden_axes",
    "artifact_sha256",
    "prompt_version",
    "effective_prompt_sha256",
)
GATE_RUN_OPTIONAL_KEYS = (
    "review_policy_id",
    *GATE_RUN_COMPLETED_KEYS,
    "failure_reason",
)
GATE_RUN_ASSIGNMENT_KEYS = ("assignment_id", "round_number", "reviewer_slot")

# workflow-model §5 — verification facts.
VERIFICATION_KEYS = ("scope", "command", "exit_code", "at", "log", "evidence")
VERIFICATION_EVIDENCE_KEYS = ("schema", "before", "after")

# workflow-model §7 — decision facts.
DECISION_KEYS = (
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
DECISION_OPTIONAL_KEYS = (
    "smoke_disposition",
    "class",
    "question",
    "options",
    "recommendation",
    "origin_run_id",
    "origin_finding_id",
    "target_stage",
    "target_blocker",
    "supersedes",
)

RESOLUTION_PAYLOAD_KEYS = {
    "disposition": ("kind",),
    "continue-review": ("kind", "assignment_id", "stopped_round"),
    "continue-stage": ("kind", "review_run_count_before", "gate_run_count_before"),
    "continue-session": ("kind", "session_count_before"),
    "accept-degraded-smoke": ("kind",),
    "policy": (
        "kind",
        "choice",
        "basis",
        "reversibility",
        "confidence",
        "review_flag",
        "scope",
    ),
}
DECISION_RESOLUTION_KINDS = {
    "finding": ("disposition",),
    "question": (
        "disposition",
        "continue-review",
    ),
    "stage-cap": ("disposition", "continue-stage"),
    "session-cap": ("disposition", "continue-session"),
    "smoke-disposition": ("disposition", "accept-degraded-smoke"),
    "policy": ("policy",),
}

# The eight-class escalation taxonomy is single-sourced here: class →
# surfacing mechanism, plus
# the one-line meanings the help surface publishes. The driver registry
# (heddle.driver.escalations) re-exports; these summaries are the compressed
# public trigger semantics.
ESCALATION_REGISTRY: dict[int, str] = {
    1: "decisions-batch",
    2: "decisions-batch",
    3: "decisions-batch",
    4: "blocking-condition",
    5: "decisions-batch",
    6: "decisions-batch",
    7: "decisions-batch",
    8: "decisions-batch",
}
ESCALATION_CLASS_SUMMARIES: dict[int, str] = {
    1: "ratified-contract conflict (work would require editing a ratified contract)",
    2: "a discovery invalidates a prior user decision's analysis",
    3: "a required gate cap was reached (driver surfaces the batch)",
    4: "gate-recorded REPORT findings (run-gate records these, never decisions add)",
    5: "live-E2E prerequisite not pre-authorized",
    6: "no progress across two sessions (driver-raised)",
    7: "session attempt cap or verification retry limit reached (driver-raised)",
    8: "irreversible open question no principle covers",
}
DECISION_BATCH_CLASSES: tuple[int, ...] = tuple(
    escalation_class
    for escalation_class, mechanism in ESCALATION_REGISTRY.items()
    if mechanism == "decisions-batch"
)
# decision-batch/v0 — the raiser vocabulary (the raiser, never the resolver).
DECISION_BATCH_SOURCES = ("session", "driver")
DECISION_BATCH_KINDS = (
    "question",
    "stage-cap",
    "session-cap",
    "smoke-disposition",
)

# Kernel-derived blocker → the escalation class whose resolution clears it.
# INSERTION
# ORDER IS CONTRACT: the decisions resolve path writes one continuation
# override per blocker in this order.
DERIVED_BLOCKER_CLASSES: dict[str, int] = {
    "gate-cap-reached": 3,
    "no-progress": 6,
    "attempt-cap": 7,
}


def normalize_decision_route(route: str) -> str | None:
    """Return a truthful repo-relative route or ``None``.

    Normalization strips boundary whitespace only. Absolute paths and raw
    ``.``/``..`` POSIX segments fail closed; no route is invented or resolved
    against the current working directory.
    """
    normalized = route.strip()
    if not normalized or PurePosixPath(normalized).is_absolute():
        return None
    if any(segment in {".", ".."} for segment in normalized.split("/")):
        return None
    return normalized


# workflow-model §2.2 / authorization facts.
AUTHORIZATION_KEYS = ("through", "source", "at")
AUTHORIZATION_OPTIONAL_KEYS = ("source", "at")

# workflow-model §8 — session facts.
SESSION_PROGRESS_FIELD_TYPES = {
    "stage": str,
    "revision_before": int,
    "revision_after": int,
    "advanced": bool,
}
SESSION_PROGRESS_KEYS = tuple(SESSION_PROGRESS_FIELD_TYPES)
SESSION_OPTIONAL_KEYS = SESSION_PROGRESS_KEYS
SESSION_KEYS = (
    "started_at",
    "ended_at",
    "completed",
    "started",
    "key_context",
    "next_steps",
    "blockers",
    *SESSION_PROGRESS_KEYS,
)

# workflow-model §12.1 — the blocking-condition vocabulary, a subset
# of the error-code vocabulary (heddle.contracts.result.ERROR_CODES).
BLOCKING_CONDITIONS = (
    "not-authorized",
    "gate-not-converged",
    "pending-decisions",
    "verification-missing",
    "milestone-out-of-sequence",
    "task-incomplete",
    "workspace-invalid",
    "awaiting-human-authorization",
    # the all-tier HITL Phase 9
    # handoff — derived at `complete` until a qualifying completion fact
    # exists.
    "awaiting-human-completion",
    "principles-not-ratified",
    "gate-cap-reached",
    "no-progress",
    "attempt-cap",
)

# the feature-workspace layout.
WORKSPACE_PLAN = "plan.md"
WORKSPACE_STATE = "state.yaml"
WORKSPACE_BRIEF = "brief.md"
WORKSPACE_REVIEWS_DIR = "reviews"
WORKSPACE_VERIFICATION_DIR = "verification"

# The .heddle.yaml v0 vocabulary follows the ratified example. The additive
# `sync` section declares an optional byte-mirror of AGENTS.md.
HEDDLE_YAML_SECTIONS = (
    "layout",
    "commands",
    "agents",
    "gates",
    "autopilot",
    "sync",
)
LAYOUT_KEYS = ("specs", "plans", "prompts", "schemas", "tests", "source")
HOST_COMMAND_KEYS = ("test", "lint", "typecheck")
AGENTS_KEYS = ("claude", "codex")
GATES_KEYS = ("enabled",)
AUTOPILOT_KEYS = (
    "flow",
    "notify_command",
    "notify_timeout_s",
    "max_turns",
    "max_budget_usd",
    "test_command",
)
SYNC_KEYS = ("mirror",)

# workflow-model §6.1 — the gate-manifest format as contract; ordered
# equality with heddle.gate.types.GateType field names is asserted by the
# AC-9 tripwire test.
GATE_MANIFEST_FIELDS = (
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
