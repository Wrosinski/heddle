"""Command metadata, lazy CLI bindings, manifest and performance budgets.

Operation spellings come from dependency-neutral contracts. One declaration per
CLI leaf owns its description and ordinary adapter binding. The explicit public
serializer excludes internal adapter bindings.
"""

from __future__ import annotations

import copy
import importlib.metadata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.schemas import (
    DECISION_BATCH_CLASSES,
    DECISION_BATCH_KINDS,
    DECISION_BATCH_SOURCES,
    ESCALATION_CLASS_SUMMARIES,
    POLICY_RESOLUTION_REQUIRED_FIELDS,
)

# Decision Log (schema-identifier spellings): heddle.<artifact>/v<N>.
MANIFEST_SCHEMA_VERSION = "heddle.manifest/v0"

# typical warm-run target and scaled-fixture CI ceiling for
# status/orient. These values are constants; performance tests own measurement.
PERF_WARM_TARGET_MS = 300
PERF_CI_CEILING_S = 2.0


@dataclass(frozen=True)
class ArgSpec:
    name: str
    summary: str
    required: bool


@dataclass(frozen=True)
class FlagSpec:
    name: str  # e.g. "--json", "--feature", "--flow"
    summary: str


@dataclass(frozen=True)
class CommandContract:
    name: str  # leaf spelling, e.g. "feature start"
    summary: str
    mutating: bool
    dry_run: bool  # advertises --dry-run support
    args: tuple[ArgSpec, ...]
    flags: tuple[FlagSpec, ...]
    exit_codes: tuple[int, ...]
    output_schema: str | None
    # the input-side analog of output_schema's null→id flip: an
    # inline payload-contract BODY (inputs are authored blind, so ids
    # alone don't serve them), None until pinned. Bodies are locked
    # transcriptions of ratified payload blocks.
    # Carrying a dict makes instances unhashable; nothing hashes them.
    input_schema: Mapping[str, Any] | None = None
    # Internal lazy CLI adapter; absent for help and the raw gate/drive entries.
    cli_binding: tuple[str, str] | None = None


# Universal flags.
_JSON = FlagSpec("--json", "emit the result envelope as JSON on stdout")
_FEATURE = FlagSpec("--feature", "target feature slug (overrides feature resolution)")
_EXPECT_REVISION = FlagSpec(
    "--expect-revision", "require the current owner revision before mutation"
)
_DRY_RUN = FlagSpec("--dry-run", "preview the mutation without writing")
_CLI = FlagSpec("--cli", "CLI runner (claude|codex)")
_MODEL = FlagSpec("--model", "exact provider model ID")
_REASONING_EFFORT = FlagSpec(
    "--reasoning-effort", "reasoning effort (low|medium|high|xhigh)"
)

# Recurring flag bundles; commands with extra flags keep inline tuples.
_FLAGS_JSON = (_JSON,)
_FLAGS_JSON_FEATURE = (_JSON, _FEATURE)
_FLAGS_JSON_DRY_RUN = (_JSON, _DRY_RUN)
_FLAGS_JSON_FEATURE_DRY_RUN = (_JSON, _FEATURE, _DRY_RUN)
_FLAGS_JSON_FEATURE_REVISION = (_JSON, _FEATURE, _EXPECT_REVISION)
_FLAGS_JSON_FEATURE_REVISION_DRY_RUN = (
    _JSON,
    _FEATURE,
    _EXPECT_REVISION,
    _DRY_RUN,
)

# Exit-code bundles per the pinned table rules (workflow-model §12.3,
# WM-6): universal floor, +3 fatal-capable, +4 advisory verdict, +5 CAS.
_EXIT_FLOOR = (0, 1, 2)
_EXIT_FATAL = (0, 1, 2, 3)
_EXIT_ADVISORY = (0, 1, 2, 3, 4)
_EXIT_CAS = (0, 1, 2, 3, 5)
_EXIT_TRANSITION = (0, 1, 2, 3, 4, 5)
_EXIT_DRIVE = (0, 1, 2, 3, 4, 5)

# the first pinned input-schema body: a locked transcription of the
# ratified `heddle.decision-batch/v1` block, with enums derived from the
# schemas.py taxonomy so the
# published contract cannot drift from the validator. Spec-key vocabulary
# (closed): type, required, summary; optional const, non_empty, one_of
# (list of {value, summary}), min_items, items (item spec; object items
# nest fields). JSON-safe by construction — the manifest round-trips it.
# Keyed by value so a vocabulary reorder can never swap the summaries.
_DECISION_SOURCE_SUMMARIES = {
    "session": "raised by a phase session",
    "driver": "raised by the drive loop",
}
_DECISION_KIND_SUMMARIES = {
    "question": "an explicit question",
    "stage-cap": "a reached stage error limit",
    "session-cap": "a reached session limit",
    "smoke-disposition": "an external failed smoke proposal",
}

DECISION_BATCH_INPUT_SCHEMA: dict[str, Any] = {
    "id": "heddle.decision-batch/v1",
    "media_type": "application/json",
    "delivered_by": "--input-json <path|->",
    "summary": "a batch of Category B decisions to record",
    "fields": {
        "schema": {
            "type": "string",
            "required": True,
            "const": "heddle.decision-batch/v1",
            "summary": "payload schema id, exact match",
        },
        "decisions": {
            "type": "list",
            "required": True,
            "min_items": 1,
            "summary": "decisions to record",
            "items": {
                "type": "object",
                "fields": {
                    "kind": {
                        "type": "string",
                        "required": True,
                        "one_of": [
                            {
                                "value": kind,
                                "summary": _DECISION_KIND_SUMMARIES[kind],
                            }
                            for kind in DECISION_BATCH_KINDS
                        ],
                        "summary": (
                            "explicit decision kind; finding and policy records "
                            "have dedicated owners"
                        ),
                    },
                    "smoke_disposition": {
                        "type": "object",
                        "required": False,
                        "summary": (
                            "class-2, stage-specific external-red proposal: stage, "
                            "attribution, baseline_evidence, outside_paths, "
                            "owned_scopes; "
                            "runtime binds current evidence; accept with exact "
                            "resolution accept-degraded-smoke"
                        ),
                    },
                    "class": {
                        "type": "integer",
                        "required": True,
                        "one_of": [
                            {
                                "value": escalation_class,
                                "summary": ESCALATION_CLASS_SUMMARIES[escalation_class],
                            }
                            for escalation_class in sorted(DECISION_BATCH_CLASSES)
                        ],
                        "summary": "escalation class (class 4 is recorded by "
                        "run-gate, never decisions add)",
                    },
                    "source": {
                        "type": "string",
                        "required": True,
                        "one_of": [
                            {
                                "value": source,
                                "summary": _DECISION_SOURCE_SUMMARIES[source],
                            }
                            for source in DECISION_BATCH_SOURCES
                        ],
                        "summary": "who raised it — the raiser, not the resolver",
                    },
                    "title": {
                        "type": "string",
                        "required": True,
                        "non_empty": True,
                        "summary": "short decision title",
                    },
                    "question": {
                        "type": "string",
                        "required": True,
                        "non_empty": True,
                        "summary": "what needs a user ruling",
                    },
                    "options": {
                        "type": "list",
                        "required": True,
                        "min_items": 1,
                        "items": {"type": "string", "non_empty": True},
                        "summary": "candidate resolutions",
                    },
                    "routes_to": {
                        "type": "list",
                        "required": True,
                        "min_items": 1,
                        "items": {"type": "string", "non_empty": True},
                        "summary": "permanent-record destination paths or sections",
                    },
                    "recommendation": {
                        "type": "string",
                        "required": False,
                        "summary": "authored recommended choice",
                    },
                    "target_stage": {
                        "type": "string",
                        "required": False,
                        "summary": "required for caps: the currently reached stage",
                    },
                    "target_blocker": {
                        "type": "string",
                        "required": False,
                        "summary": (
                            "required for stage/session caps: exact reached blocker; "
                            "omitted for verdict caps"
                        ),
                    },
                },
            },
        },
    },
    "notes": [
        "id is allocated opaquely; created_at and status: pending are recorded "
        "by the handler",
        "unknown fields are refused at every document boundary",
        "caps require their reached stage/blocker and the matching escalation "
        "class; runtime binds current evidence",
        "smoke-disposition requires class 2 and smoke_disposition; "
        "question excludes class 4",
        "class-7 question requires driver source, no target_stage or "
        "target_blocker, and resolves only by disposition",
        "resolve with required --kind, --resolution rationale and --routes-to",
    ],
}

POLICY_BATCH_INPUT_SCHEMA: dict[str, Any] = {
    "id": "heddle.policy-batch/v1",
    "media_type": "application/json",
    "delivered_by": "--from-file <path|->",
    "summary": "a closed batch of reversible ordinary policy resolutions",
    "fields": {
        "schema": {
            "type": "string",
            "required": True,
            "const": "heddle.policy-batch/v1",
            "summary": "payload schema id, exact match",
        },
        "resolutions": {
            "type": "list",
            "required": True,
            "min_items": 1,
            "summary": "policy resolutions with unique whitespace-free IDs",
            "items": {
                "type": "object",
                "fields": {
                    **{
                        name: {
                            "type": "string",
                            "required": True,
                            "non_empty": True,
                            "summary": f"nonempty single-line {name}",
                        }
                        for name in POLICY_RESOLUTION_REQUIRED_FIELDS
                        if name not in {"options", "routes_to"}
                    },
                    **{
                        name: {
                            "type": "list",
                            "required": True,
                            "min_items": 1,
                            "summary": "declared choices"
                            if name == "options"
                            else "truthful repository paths",
                            "items": {
                                "type": "string",
                                "non_empty": True,
                                "summary": "single-line value",
                            },
                        }
                        for name in ("options", "routes_to")
                    },
                    "review_flag": {
                        "type": "string",
                        "required": False,
                        "summary": "empty by default, or [REVIEW]",
                    },
                },
            },
        },
    },
    "notes": [
        "unknown fields are refused; callers cannot supply authority or provenance",
        "choice must name one option; reversibility must be reversible; "
        "confidence is high, medium or low",
        "scope is feature or the current milestone ID; irreversible, "
        "contract-changing and wider-scope choices must escalate",
        "basis names the decisive principle; none or conflict requires [REVIEW]; "
        "authored judgment remains author-trusted",
        "identical retry is a no-op or repairs an unbacked journal entry; "
        "changed data at an existing ID refuses",
    ],
}

# workflow-model §15. Summaries transcribe the public command surface
# (noun-group lines split per verb — adaptation, not invention). Exit-code
# sets follow the pinned rules: universal floor {0,1,2}; fatal-capable
# commands add 3; advisory-verdict commands (validate, doctor, run-gate)
# add 4; CAS-guarded state.yaml writers add 5.
COMMAND_SURFACE: tuple[CommandContract, ...] = (
    CommandContract(
        name=ops.operation_type_name(ops.Init),
        cli_binding=("init_host", "run_init"),
        summary="scaffold or upgrade host integration",
        mutating=True,
        dry_run=True,
        args=(),
        # init creates the workspace/config fresh — no prior revision to
        # CAS-guard, so no exit 5 (Feature Spec §Architecture table rules).
        flags=_FLAGS_JSON_DRY_RUN,
        exit_codes=_EXIT_FATAL,
        output_schema="heddle.init/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Orient),
        cli_binding=("status", "run_orient"),
        summary="minimum context before starting work",
        mutating=False,
        dry_run=False,
        args=(),
        flags=_FLAGS_JSON_FEATURE,
        exit_codes=_EXIT_FATAL,
        output_schema="heddle.orient/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Status),
        cli_binding=("status", "run_status"),
        summary="structured state for the active feature",
        mutating=False,
        dry_run=False,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec("--all", "portfolio view across features in flight"),
        ),
        exit_codes=_EXIT_FATAL,
        output_schema="heddle.status/v1",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Search),
        cli_binding=("search", "run_search"),
        summary="search Feature Specs and Heddle patterns by intent",
        mutating=False,
        dry_run=False,
        args=(ArgSpec("query", "non-empty natural AND-term query", True),),
        flags=(
            _JSON,
            FlagSpec("--limit", "result count from 1 through 25 (default 5)"),
            FlagSpec("--titles-only", "omit result bodies"),
        ),
        exit_codes=_EXIT_FATAL,
        output_schema="heddle.search/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.RecordReviewDisposition),
        cli_binding=("review_assignments", "run_disposition"),
        summary="record evidence-bound lead dispositions on original review findings",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            _DRY_RUN,
            FlagSpec("--input-json", "versioned disposition input file or - for stdin"),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.ReviewRoundOpen),
        cli_binding=("review_assignments", "run_round_open"),
        summary="open a later review round under the confirmed assignment allowance",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            _DRY_RUN,
            FlagSpec("--input-json", "versioned round input file or - for stdin"),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.InterpretReview),
        cli_binding=("review_assignments", "run_interpret"),
        summary="accept a capture-bound lead interpretation without a provider call",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            _DRY_RUN,
            FlagSpec(
                "--input-json", "versioned lead interpretation file or - for stdin"
            ),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
        input_schema={
            "id": "heddle.review-interpretation-input/v1",
            "media_type": "application/json",
            "delivered_by": "--input-json <path|->",
            "summary": "complete lead interpretation bound to the original capture",
            "fields": {
                "schema": {
                    "type": "string",
                    "required": True,
                    "const": "heddle.review-interpretation-input/v1",
                },
                **{
                    name: {"type": "string", "required": True, "summary": summary}
                    for name, summary in (
                        ("run_id", "original retained response ID"),
                        ("capture_sha256", "exact immutable capture SHA-256"),
                        ("author", "lead responsible for this interpretation"),
                        (
                            "reason",
                            "how the interpretation preserves the full response",
                        ),
                    )
                },
                "content": {
                    "type": "object",
                    "required": True,
                    "summary": "complete native content matching capture.schema_json",
                },
            },
            "notes": [
                "Read every original finding and limitation before interpreting.",
                "No invocation fields may be supplied or rebound to current source.",
                "Lead interpretation earns original independent-slot credit; "
                "REPORT decisions and closure remain separate.",
                "Dry-run validates without writing; exact replay adds no fact or call.",
            ],
        },
    ),
    CommandContract(
        name=ops.operation_type_name(ops.ReviewAllowance),
        cli_binding=("feature_policy", "run_review_allowance"),
        summary=(
            "raise one upper-limit role's quality round ceiling without invoking "
            "a provider"
        ),
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            _DRY_RUN,
            FlagSpec("--role", "confirmed review role to amend"),
            FlagSpec("--limit", "absolute positive quality round total"),
            FlagSpec("--approval", "non-empty owner approval record"),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.FeaturePrepare),
        cli_binding=("intake", "run_feature_prepare"),
        summary="record researched intake and a policy recommendation",
        mutating=True,
        dry_run=True,
        args=(ArgSpec("slug", "feature slug", True),),
        flags=(
            _JSON,
            FlagSpec("--area", "spec area segment"),
            FlagSpec("--from-file", "intake YAML file or - for stdin"),
            _DRY_RUN,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.FeaturePolicy),
        cli_binding=("feature_policy", "run_feature_policy"),
        summary="confirm or amend the complete feature policy",
        mutating=True,
        dry_run=True,
        args=(ArgSpec("slug", "feature slug", True),),
        flags=(
            _JSON,
            FlagSpec("--from-file", "complete policy YAML file or - for stdin"),
            _EXPECT_REVISION,
            _DRY_RUN,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.FeatureReassess),
        cli_binding=("feature_policy", "run_feature_reassess"),
        summary="record new axes and recommendation without changing authority",
        mutating=True,
        dry_run=False,
        args=(ArgSpec("slug", "feature slug", True),),
        flags=(
            _JSON,
            FlagSpec("--from-file", "assessment YAML file or - for stdin"),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.FeatureStart),
        cli_binding=("feature_start", "run_feature_start"),
        summary="admit confirmed intake into a feature workspace",
        mutating=True,
        dry_run=True,
        args=(ArgSpec("slug", "feature slug for the new workspace", True),),
        flags=(
            _JSON,
            FlagSpec("--area", "optional check against the confirmed intake area"),
            FlagSpec("--flow", "per-feature flow mode (hitl|auto)"),
            _EXPECT_REVISION,
            _DRY_RUN,
        ),
        exit_codes=_EXIT_CAS,
        output_schema="heddle.feature-start/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.FeatureSwitch),
        cli_binding=("pointer", "run_feature_switch"),
        summary="record the worktree's active feature",
        mutating=True,
        # Worktree-local pointer outside the control plane — trivially
        # idempotent, no --dry-run; no state.yaml read, so no exit 3/5.
        dry_run=False,
        args=(ArgSpec("slug", "feature slug to make active", True),),
        flags=_FLAGS_JSON,
        exit_codes=_EXIT_FLOOR,
        output_schema="heddle.feature-switch/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.FeatureComplete),
        cli_binding=("completion", "run_feature_complete"),
        summary=(
            "accept completion with local retention; record commits optional; "
            "retry stamp, archive and cleanup effects"
        ),
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            *_FLAGS_JSON_FEATURE_DRY_RUN,
            _EXPECT_REVISION,
        ),
        exit_codes=(0, 1, 2, 3, 4, 5),
        output_schema="heddle.feature-complete/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.FeatureInputsSet),
        cli_binding=("feature_inputs", "run_feature_inputs_set"),
        summary="replace milestone-less feature verification inputs",
        mutating=True,
        dry_run=False,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec("--from-file", "JSON path array (path or -)"),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Kickoff),
        cli_binding=("kickoff", "run_kickoff"),
        summary="render the current stage's briefing",
        mutating=False,
        dry_run=False,
        args=(),
        flags=_FLAGS_JSON_FEATURE,
        exit_codes=_EXIT_FATAL,
        output_schema="heddle.kickoff/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.RunGate),
        summary="run a gate through the runtime",
        mutating=True,
        # Execution is the act; re-run safety is the hash-keyed cache —
        # no --dry-run (Feature Spec Decision Log).
        dry_run=False,
        args=(ArgSpec("gate", "gate name to run", True),),
        # --json/--feature route through the runtime envelope; --cli remains
        # additive.
        flags=(_JSON, _FEATURE, _CLI, _MODEL, _REASONING_EFFORT),
        exit_codes=_EXIT_ADVISORY,
        output_schema="heddle.run-gate/v1",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.RunGates),
        cli_binding=("gate_run", "run_gates"),
        summary="run the declared independent review-gate group",
        mutating=True,
        dry_run=False,
        args=(),
        flags=_FLAGS_JSON_FEATURE,
        exit_codes=_EXIT_ADVISORY,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Drive),
        summary="execute the active feature's next actions until terminal or blocked",
        mutating=True,
        dry_run=False,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec("--until", "stop after reaching the named stage boundary"),
        ),
        exit_codes=_EXIT_DRIVE,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.FlowSet),
        cli_binding=("write_path", "run_flow_set"),
        summary="set a feature's HITL/autopilot flow mode",
        mutating=True,
        dry_run=False,
        args=(ArgSpec("mode", "flow mode to activate (hitl|auto)", True),),
        flags=_FLAGS_JSON_FEATURE_REVISION,
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.PhaseExit),
        cli_binding=("write_path", "run_phase_exit"),
        summary="advance one legal stage and report the resulting guidance",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec(
                "--through",
                "set the permission ceiling; stage travel remains one legal step",
            ),
            _EXPECT_REVISION,
            _DRY_RUN,
        ),
        exit_codes=_EXIT_TRANSITION,
        output_schema="heddle.phase-exit/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.CommandsSet),
        cli_binding=("write_path", "run_commands_set"),
        summary="set one runtime-owned verification command",
        mutating=True,
        dry_run=True,
        args=(
            ArgSpec(
                "command-key",
                "verification command key from the state commands vocabulary",
                True,
            ),
        ),
        flags=(
            *_FLAGS_JSON_FEATURE_DRY_RUN,
            FlagSpec("--command", "exact shell command to store"),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.CommandsUnset),
        cli_binding=("write_path", "run_commands_unset"),
        summary=(
            "remove one runtime-owned command; absent is a no-op, not a proof waiver"
        ),
        mutating=True,
        dry_run=True,
        args=(ArgSpec("command-key", "existing state command vocabulary key", True),),
        flags=_FLAGS_JSON_FEATURE_REVISION_DRY_RUN,
        exit_codes=_EXIT_CAS,
        output_schema=None,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.MilestoneAdd),
        cli_binding=("write_path", "run_milestone_add"),
        summary="add a milestone skeleton",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            *_FLAGS_JSON_FEATURE_DRY_RUN,
            FlagSpec("--from-file", "milestone payload YAML (path or -)"),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema="heddle.milestone-add/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.MilestoneEdit),
        cli_binding=("write_path", "run_milestone_edit"),
        summary="edit a milestone skeleton",
        mutating=True,
        dry_run=True,
        args=(ArgSpec("milestone-ref", "milestone id to edit (m<N>)", True),),
        flags=(
            *_FLAGS_JSON_FEATURE_DRY_RUN,
            FlagSpec("--from-file", "partial milestone payload YAML (path or -)"),
            _EXPECT_REVISION,
        ),
        exit_codes=_EXIT_CAS,
        output_schema="heddle.milestone-edit/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.MilestoneAdvance),
        cli_binding=("write_path", "run_milestone_advance"),
        summary="complete the current or promote one eligible milestone",
        mutating=True,
        dry_run=True,
        args=(),
        flags=_FLAGS_JSON_FEATURE_REVISION_DRY_RUN,
        exit_codes=_EXIT_TRANSITION,
        output_schema="heddle.milestone-advance/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.TaskAdd),
        cli_binding=("write_path", "run_task"),
        summary="add a task to the current milestone",
        mutating=True,
        dry_run=True,
        args=(ArgSpec("text", "task text to append to the current milestone", True),),
        flags=_FLAGS_JSON_FEATURE_REVISION_DRY_RUN,
        exit_codes=_EXIT_CAS,
        output_schema="heddle.task-add/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.TaskDone),
        cli_binding=("write_path", "run_task"),
        summary="mark a task done",
        mutating=True,
        dry_run=True,
        args=(ArgSpec("task-ref", "task id to mark done", True),),
        flags=_FLAGS_JSON_FEATURE_REVISION_DRY_RUN,
        exit_codes=_EXIT_CAS,
        output_schema="heddle.task-done/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.TaskCurrent),
        cli_binding=("write_path", "run_task"),
        # Setting the ← current marker is an inner-loop
        # write; reading the current task is status/orient's job.
        summary="set the current-task marker",
        mutating=True,
        dry_run=True,
        args=(ArgSpec("task-ref", "task id to mark current", True),),
        flags=_FLAGS_JSON_FEATURE_REVISION_DRY_RUN,
        exit_codes=_EXIT_CAS,
        output_schema="heddle.task-current/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Verify),
        cli_binding=("write_path", "run_verify"),
        summary="run a stored verification command and record the fact",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec(
                "--scope",
                "verification scope (feature|m<N>|smoke|acceptance|live)",
            ),
            _EXPECT_REVISION,
            _DRY_RUN,
        ),
        exit_codes=_EXIT_CAS,
        output_schema="heddle.verify/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.RecordSession),
        cli_binding=("write_path", "run_session_log"),
        summary="append a structured Session Log entry",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec("--from-file", "session YAML payload path or - for stdin"),
            _EXPECT_REVISION,
            _DRY_RUN,
        ),
        exit_codes=_EXIT_CAS,
        output_schema="heddle.session-log/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Sync),
        cli_binding=("sync", "run_sync"),
        summary="refresh the generated managed blocks",
        mutating=True,
        dry_run=True,
        args=(),
        flags=_FLAGS_JSON_FEATURE_DRY_RUN,
        exit_codes=_EXIT_CAS,
        output_schema="heddle.sync/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.DecisionsList),
        cli_binding=("decisions", "run_decisions"),
        summary="list Category B decision batches",
        mutating=False,
        dry_run=False,
        args=(),
        flags=(_JSON, _FEATURE),
        exit_codes=_EXIT_FATAL,
        output_schema="heddle.decisions-list/v1",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.DecisionsAdd),
        cli_binding=("decisions", "run_decisions"),
        summary="record a Category B decision",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec("--input-json", "heddle.decision-batch/v1 JSON payload path or -"),
            _EXPECT_REVISION,
            _DRY_RUN,
        ),
        exit_codes=_EXIT_CAS,
        output_schema="heddle.decisions-add/v1",
        input_schema=DECISION_BATCH_INPUT_SCHEMA,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.RecordPolicy),
        cli_binding=("decisions", "run_decisions"),
        summary="record a restricted policy choice and matching journal entry",
        mutating=True,
        dry_run=True,
        args=(),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec("--from-file", "heddle.policy-batch/v1 JSON path or - for stdin"),
            _EXPECT_REVISION,
            _DRY_RUN,
        ),
        exit_codes=_EXIT_CAS,
        output_schema="heddle.decisions-record-policy/v1",
        input_schema=POLICY_BATCH_INPUT_SCHEMA,
    ),
    CommandContract(
        name=ops.operation_type_name(ops.ResolveDecision),
        cli_binding=("decisions", "run_decisions"),
        summary="resolve a recorded decision",
        mutating=True,
        dry_run=True,
        args=(ArgSpec("id", "decision id to resolve", True),),
        flags=(
            _JSON,
            _FEATURE,
            FlagSpec(
                "--kind",
                "required explicit resolution: disposition, continue-review (one next "
                "round for the current assignment stop; confirmed cap still applies), "
                "continue-stage, continue-session, or accept-degraded-smoke",
            ),
            FlagSpec(
                "--resolution",
                "required authored rationale; text never selects authority",
            ),
            FlagSpec("--routes-to", "permanent-record destination (§7)"),
            _EXPECT_REVISION,
            _DRY_RUN,
        ),
        exit_codes=_EXIT_CAS,
        output_schema="heddle.decisions-resolve/v1",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Validate),
        cli_binding=("validate", "run_validate"),
        summary="workflow, schema, and drift validation",
        mutating=False,
        dry_run=False,
        args=(),
        flags=_FLAGS_JSON_FEATURE,
        exit_codes=_EXIT_ADVISORY,
        output_schema="heddle.validate/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.Doctor),
        cli_binding=("doctor", "run_doctor"),
        summary="runtime and repo integration diagnostics",
        mutating=False,
        dry_run=False,
        args=(),
        # --feature scopes the parse/artifact/gate roll-up;
        # the manifest flip pins doctor's payload schema id.
        flags=_FLAGS_JSON_FEATURE,
        exit_codes=_EXIT_ADVISORY,
        output_schema="heddle.doctor/v0",
    ),
    CommandContract(
        name=ops.operation_type_name(ops.ShowPrompt),
        cli_binding=("show_prompt", "run_show_prompt"),
        summary="render a gate's guidelines prompt to stdout (a pure read)",
        mutating=False,
        dry_run=False,
        args=(ArgSpec("gate", "gate name whose prompt to render", True),),
        # --feature fills the real spec/plan/milestone/tier tokens; --cli
        # selects the lane phrasing (defaults to the gate's registry lane).
        flags=(_JSON, _FEATURE, _CLI, _MODEL, _REASONING_EFFORT),
        exit_codes=_EXIT_FATAL,
        output_schema=None,
    ),
    CommandContract(
        name="help",
        summary="enumerate the command surface and each command's contract",
        mutating=False,
        dry_run=False,
        args=(),
        flags=_FLAGS_JSON,
        exit_codes=_EXIT_FLOOR,
        output_schema="heddle.manifest/v0",
    ),
)


def serialize_command(contract: CommandContract) -> dict[str, Any]:
    # Internal adapter bindings never serialize into the public manifest.
    return {
        "name": contract.name,
        "summary": contract.summary,
        "mutating": contract.mutating,
        "dry_run": contract.dry_run,
        "args": [
            {"name": arg.name, "summary": arg.summary, "required": arg.required}
            for arg in contract.args
        ],
        "flags": [
            {"name": flag.name, "summary": flag.summary} for flag in contract.flags
        ],
        "exit_codes": list(contract.exit_codes),
        "output_schema": contract.output_schema,
        # Deep-copied so no consumer can mutate the published contract
        # through a manifest entry (the other fields are freshly built).
        "input_schema": copy.deepcopy(contract.input_schema),
    }


def build_manifest() -> dict[str, Any]:
    """The manifest: {schema_version, runtime_version, commands[]},
    serialized in COMMAND_SURFACE (= ratified table) order."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        # Assumption A5: the installed distribution is named `heddle`.
        "runtime_version": importlib.metadata.version("heddle"),
        "commands": [serialize_command(contract) for contract in COMMAND_SURFACE],
    }
