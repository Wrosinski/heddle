"""
Contract tests for heddle/runtime/contracts.py — Milestone 1.

Covers: AC-2 (the manifest covers the full command surface per leaf verb —
full-payload lock per manifest/command alias/gate envelope/manifest lockstep), AC-11
(performance budgets).
Behavior contract: core-contract-rails

preimplementation state: imports heddle.runtime.contracts, which does not exist until
Milestone 1 — collection errors are the expected pre-implementation signal.
"""

from __future__ import annotations

import importlib.metadata
import json
import shlex
from dataclasses import fields, replace
from typing import get_args

import pytest

from heddle.contracts.schemas import (
    DECISION_BATCH_CLASSES,
    DECISION_BATCH_SOURCES,
    ESCALATION_CLASS_SUMMARIES,
)
from heddle.runtime.contracts import (
    COMMAND_SURFACE,
    DECISION_BATCH_INPUT_SCHEMA,
    MANIFEST_SCHEMA_VERSION,
    PERF_CI_CEILING_S,
    PERF_WARM_TARGET_MS,
    build_manifest,
)


def test_operation_names_cover_the_public_surface_without_extra_retier_leaf():
    from heddle.contracts import operations as ops

    names = {ops.operation_type_name(cls) for cls in get_args(ops.Operation.__value__)}
    assert names == {c.name for c in COMMAND_SURFACE} - {"help"}
    assert ops.operation_type_name(ops.FeatureStart) == "feature start"
    ordinary = {c.name for c in COMMAND_SURFACE if c.cli_binding is not None}
    assert ordinary == names - {"run-gate", "drive"}


def _revision_guard_operations():
    from heddle.contracts import operations as ops

    milestone = ops.MilestoneInput(
        title="Guarded milestone",
        complexity="low",
        verification=ops.VerificationInput(command="true", expected="pass"),
    )
    session = ops.SessionInput(
        started_at="2026-09-14T10:00Z",
        ended_at="2026-09-14T10:01Z",
        completed=(),
        started=(),
        key_context="revision guard",
        next_steps="continue",
        blockers=(),
    )
    return (
        ops.RecordReviewDisposition(expect_revision=7),
        ops.ReviewRoundOpen(expect_revision=7),
        ops.InterpretReview(expect_revision=7),
        ops.ReviewAllowance("spec-review", 2, "owner approval", "demo", 7),
        ops.FeatureStart("demo", expect_revision=7),
        ops.FeaturePolicy("demo", {}, expect_revision=7),
        ops.FeatureReassess("demo", {}, expect_revision=7),
        ops.FeatureComplete(expect_revision=7),
        ops.FeatureInputsSet(("src/demo.py",), expect_revision=7),
        ops.CommandsSet("smoke_test", "true", expect_revision=7),
        ops.CommandsUnset("smoke_test", expect_revision=7),
        ops.MilestoneAdd(milestone, expect_revision=7),
        ops.MilestoneEdit("m1", ops.MilestonePatch(title="Guarded"), expect_revision=7),
        ops.MilestoneAdvance(expect_revision=7),
        ops.TaskAdd("guarded task", expect_revision=7),
        ops.TaskCurrent("t1", expect_revision=7),
        ops.TaskDone("m1", "t1", expect_revision=7),
        ops.Verify("m1", expect_revision=7),
        ops.RecordSession(session, expect_revision=7),
        ops.PhaseExit(expect_revision=7),
        ops.FlowSet("auto", expect_revision=7),
        ops.DecisionsAdd((), expect_revision=7),
        ops.ResolveDecision(
            "d1", "disposition", "resolved", "docs/decision.md", expect_revision=7
        ),
        ops.RecordPolicy((), expect_revision=7),
    )


def test_every_typed_revision_guard_renders_exactly_once():
    from heddle.contracts import operations as ops

    guarded_types = {
        cls
        for cls in get_args(ops.Operation.__value__)
        if "expect_revision" in {field.name for field in fields(cls)}
    }
    operations = _revision_guard_operations()
    assert {type(operation) for operation in operations} == guarded_types
    for operation in operations:
        argv = shlex.split(ops.operation_command(operation))
        assert argv.count("--expect-revision") == 1, type(operation).__name__
        index = argv.index("--expect-revision")
        assert argv[index + 1] == "7"


def test_typed_invalid_revision_is_usage_before_service_loading(monkeypatch):
    from heddle.runtime import application

    def forbidden(_name):
        raise AssertionError("invalid typed input must not load a mutation service")

    monkeypatch.setattr(application, "import_module", forbidden)
    for invalid in (-1, "7", True):
        for operation in _revision_guard_operations():
            result = application.execute(
                replace(operation, expect_revision=invalid)  # type: ignore[arg-type]
            )
            assert not result.ok and result.error is not None
            assert result.error.code == "usage", type(operation).__name__
            assert result.exit_code == 2


# ---------------------------------------------------------------------------
# The pinned table follows the public command surface and workflow-model §15.
# This is the
# one-time locked transcription AC-2 exists to protect — entries are
# (name, mutating, dry_run, exit_codes, args, flags).
#
# Scaffold transcription note: arg names are encoded bare ("slug", not
# "<slug>"), mirroring FlagSpec's example which drops value placeholders
# ("--tier", not "--tier <N>"). Flag sets are compared exactly; `commands[]`
# order is compared exactly (= table order).
# ---------------------------------------------------------------------------
EXPECTED_SURFACE = [
    ("init", True, True, [0, 1, 2, 3], [], {"--dry-run", "--json", "--adopt-existing"}),
    ("orient", False, False, [0, 1, 2, 3], [], {"--feature", "--json"}),
    ("status", False, False, [0, 1, 2, 3], [], {"--all", "--feature", "--json"}),
    (
        "search",
        False,
        False,
        [0, 1, 2, 3],
        [("query", True)],
        {"--limit", "--titles-only", "--json"},
    ),
    (
        "review disposition",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {"--json", "--input-json", "--dry-run", "--feature", "--expect-revision"},
    ),
    (
        "review round-open",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {"--json", "--input-json", "--dry-run", "--feature", "--expect-revision"},
    ),
    (
        "review interpret",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {"--json", "--input-json", "--dry-run", "--feature", "--expect-revision"},
    ),
    (
        "review allowance",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {
            "--role",
            "--expect-revision",
            "--approval",
            "--json",
            "--limit",
            "--feature",
            "--dry-run",
        },
    ),
    (
        "feature prepare",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("slug", True)],
        {"--dry-run", "--from-file", "--area", "--json"},
    ),
    (
        "feature policy",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("slug", True)],
        {"--expect-revision", "--from-file", "--dry-run", "--json"},
    ),
    (
        "feature reassess",
        True,
        False,
        [0, 1, 2, 3, 5],
        [("slug", True)],
        {"--expect-revision", "--from-file", "--json"},
    ),
    (
        "feature start",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("slug", True)],
        {"--area", "--json", "--flow", "--expect-revision", "--dry-run"},
    ),
    ("feature switch", True, False, [0, 1, 2], [("slug", True)], {"--json"}),
    (
        "feature complete",
        True,
        True,
        [0, 1, 2, 3, 4, 5],
        [],
        {"--expect-revision", "--dry-run", "--feature", "--json"},
    ),
    (
        "feature inputs set",
        True,
        False,
        [0, 1, 2, 3, 5],
        [],
        {"--expect-revision", "--from-file", "--feature", "--json"},
    ),
    ("kickoff", False, False, [0, 1, 2, 3], [], {"--feature", "--json"}),
    (
        "run-gate",
        True,
        False,
        [0, 1, 2, 3, 4],
        [("gate", True)],
        {"--model", "--reasoning-effort", "--cli", "--feature", "--json"},
    ),
    ("run-gates", True, False, [0, 1, 2, 3, 4], [], {"--feature", "--json"}),
    ("drive", True, False, [0, 1, 2, 3, 4, 5], [], {"--until", "--feature", "--json"}),
    (
        "flow set",
        True,
        False,
        [0, 1, 2, 3, 5],
        [("mode", True)],
        {"--expect-revision", "--feature", "--json"},
    ),
    (
        "phase-exit",
        True,
        True,
        [0, 1, 2, 3, 4, 5],
        [],
        {"--through", "--json", "--expect-revision", "--feature", "--dry-run"},
    ),
    (
        "commands set",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("command-key", True)],
        {"--command", "--json", "--expect-revision", "--feature", "--dry-run"},
    ),
    (
        "commands unset",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("command-key", True)],
        {"--expect-revision", "--dry-run", "--feature", "--json"},
    ),
    (
        "milestone add",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {"--from-file", "--json", "--dry-run", "--feature", "--expect-revision"},
    ),
    (
        "milestone edit",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("milestone-ref", True)],
        {"--from-file", "--json", "--dry-run", "--feature", "--expect-revision"},
    ),
    (
        "milestone advance",
        True,
        True,
        [0, 1, 2, 3, 4, 5],
        [],
        {"--expect-revision", "--dry-run", "--feature", "--json"},
    ),
    (
        "task add",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("text", True)],
        {"--expect-revision", "--dry-run", "--feature", "--json"},
    ),
    (
        "task done",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("task-ref", True)],
        {"--expect-revision", "--dry-run", "--feature", "--json"},
    ),
    (
        "task current",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("task-ref", True)],
        {"--expect-revision", "--dry-run", "--feature", "--json"},
    ),
    (
        "verify",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {"--dry-run", "--json", "--scope", "--feature", "--expect-revision"},
    ),
    (
        "session log",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {"--dry-run", "--json", "--from-file", "--feature", "--expect-revision"},
    ),
    ("sync", True, True, [0, 1, 2, 3, 5], [], {"--dry-run", "--feature", "--json"}),
    ("decisions list", False, False, [0, 1, 2, 3], [], {"--feature", "--json"}),
    (
        "decisions add",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {"--json", "--input-json", "--expect-revision", "--feature", "--dry-run"},
    ),
    (
        "decisions record-policy",
        True,
        True,
        [0, 1, 2, 3, 5],
        [],
        {"--dry-run", "--json", "--from-file", "--feature", "--expect-revision"},
    ),
    (
        "decisions resolve",
        True,
        True,
        [0, 1, 2, 3, 5],
        [("id", True)],
        {
            "--dry-run",
            "--expect-revision",
            "--feature",
            "--json",
            "--kind",
            "--resolution",
            "--routes-to",
        },
    ),
    ("validate", False, False, [0, 1, 2, 3, 4], [], {"--feature", "--json"}),
    ("doctor", False, False, [0, 1, 2, 3, 4], [], {"--feature", "--json"}),
    (
        "show-prompt",
        False,
        False,
        [0, 1, 2, 3],
        [("gate", True)],
        {"--model", "--reasoning-effort", "--cli", "--feature", "--json"},
    ),
    ("help", False, False, [0, 1, 2], [], {"--json"}),
]

EXPECTED_NAMES = [row[0] for row in EXPECTED_SURFACE]

# manifest: the eight manifest fields per command entry plus input schema's additive
# `input_schema` is the additive input-contract field.
MANIFEST_ENTRY_KEYS = {
    "name",
    "summary",
    "mutating",
    "dry_run",
    "args",
    "flags",
    "exit_codes",
    "output_schema",
    "input_schema",
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return build_manifest()


@pytest.fixture(scope="module")
def entries_by_name(manifest) -> dict[str, dict]:
    return {entry["name"]: entry for entry in manifest["commands"]}


class TestAC2ManifestShape:
    """AC-2: manifest structure and ordering."""

    def test_manifest_top_level_keys(self, manifest):
        # manifest: {schema_version, runtime_version, commands}.
        assert set(manifest.keys()) == {
            "schema_version",
            "runtime_version",
            "commands",
        }
        assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION

    def test_manifest_schema_version_spelling(self):
        assert MANIFEST_SCHEMA_VERSION == "heddle.manifest/v0"

    def test_runtime_version_is_installed_distribution_version(self, manifest):
        # Assumption A5: the installed distribution is named `heddle`.
        assert manifest["runtime_version"] == importlib.metadata.version("heddle")

    def test_current_leaf_commands_in_table_order(self, manifest):
        # command alias: one entry per leaf verb; serialized in COMMAND_SURFACE order.
        assert [entry["name"] for entry in manifest["commands"]] == EXPECTED_NAMES

    def test_command_surface_matches_manifest_order(self, manifest):
        assert [contract.name for contract in COMMAND_SURFACE] == EXPECTED_NAMES

    def test_every_entry_has_exactly_the_eight_manifest_fields(self, manifest):
        for entry in manifest["commands"]:
            assert set(entry.keys()) == MANIFEST_ENTRY_KEYS, (
                f"FAIL [{entry.get('name')}]: entry must carry exactly the "
                "manifest fields plus input_schema (input schema)"
            )

    def test_nested_arg_and_flag_serialization_shapes(self, manifest):
        for entry in manifest["commands"]:
            for arg in entry["args"]:
                assert set(arg.keys()) == {"name", "summary", "required"}, (
                    f"FAIL [{entry['name']}]: args[] serialize as "
                    "{name, summary, required}"
                )
                assert isinstance(arg["required"], bool)
            for flag in entry["flags"]:
                assert set(flag.keys()) == {"name", "summary"}, (
                    f"FAIL [{entry['name']}]: flags[] serialize as {{name, summary}}"
                )


@pytest.mark.parametrize(
    ("name", "mutating", "dry_run", "exit_codes", "args", "flags"),
    EXPECTED_SURFACE,
    ids=EXPECTED_NAMES,
)
class TestAC2PerCommandPayloadLock:
    """AC-2: full-payload lock — every pinned value, per command."""

    def test_pinned_values(
        self, entries_by_name, name, mutating, dry_run, exit_codes, args, flags
    ):
        entry = entries_by_name[name]
        assert entry["mutating"] is mutating
        assert entry["dry_run"] is dry_run
        # Direct order + exact-type lock (review): sorted() would
        # accept wire reordering, and bool compares equal to int.
        assert entry["exit_codes"] == exit_codes
        assert all(type(code) is int for code in entry["exit_codes"])
        assert {flag["name"] for flag in entry["flags"]} == flags
        assert [(arg["name"], arg["required"]) for arg in entry["args"]] == args
        # Output schema identifiers are pinned for commands that return a public
        # payload; commands without a payload keep a null schema.
        expected_schema = {
            "init": "heddle.init/v0",
            "help": "heddle.manifest/v0",
            "feature start": "heddle.feature-start/v0",
            "milestone add": "heddle.milestone-add/v0",
            "milestone edit": "heddle.milestone-edit/v0",
            "status": "heddle.status/v1",
            "search": "heddle.search/v0",
            "orient": "heddle.orient/v0",
            "feature switch": "heddle.feature-switch/v0",
            "feature complete": "heddle.feature-complete/v0",
            "kickoff": "heddle.kickoff/v0",
            "doctor": "heddle.doctor/v0",
            "phase-exit": "heddle.phase-exit/v0",
            "milestone advance": "heddle.milestone-advance/v0",
            "task add": "heddle.task-add/v0",
            "task done": "heddle.task-done/v0",
            "task current": "heddle.task-current/v0",
            "verify": "heddle.verify/v0",
            "session log": "heddle.session-log/v0",
            "sync": "heddle.sync/v0",
            "validate": "heddle.validate/v0",
            "run-gate": "heddle.run-gate/v1",
            "decisions list": "heddle.decisions-list/v1",
            "decisions add": "heddle.decisions-add/v1",
            "decisions resolve": "heddle.decisions-resolve/v1",
            "decisions record-policy": "heddle.decisions-record-policy/v1",
        }.get(name)
        assert entry["output_schema"] == expected_schema
        # input schema: input_schema is null until a payload body is pinned — the
        # output_schema additive-flip convention, input side.
        expected_input = {
            "review disposition": "heddle.review-disposition-input/v1",
            "review interpret": "heddle.review-interpretation-input/v1",
            "feature prepare": "heddle.intake-input/v1",
            "feature policy": "heddle.feature-policy/v1",
            "decisions add": "heddle.decision-batch/v1",
            "decisions record-policy": "heddle.policy-batch/v1",
        }.get(name)
        if expected_input is None:
            assert entry["input_schema"] is None
        else:
            assert entry["input_schema"]["id"] == expected_input

    def test_summary_non_empty(
        self, entries_by_name, name, mutating, dry_run, exit_codes, args, flags
    ):
        # AC-2 locks structure (non-empty); prose fidelity is review-verified,
        # not mechanical (manifest lockstep).
        entry = entries_by_name[name]
        assert isinstance(entry["summary"], str) and entry["summary"].strip()
        for arg in entry["args"]:
            assert arg["summary"].strip()
        for flag in entry["flags"]:
            assert flag["summary"].strip()


class TestAC2TableRules:
    """AC-2: the pinned value rules behind the table."""

    def test_universal_exit_code_floor(self, manifest):
        for entry in manifest["commands"]:
            assert {0, 1, 2} <= set(entry["exit_codes"]), (
                f"FAIL [{entry['name']}]: every command includes exit codes 0, 1, 2"
            )

    def test_dry_run_iff_dry_run_flag(self, manifest):
        # UX bar item 10 with the two ratified exceptions encoded in the table.
        for entry in manifest["commands"]:
            flag_names = {flag["name"] for flag in entry["flags"]}
            assert entry["dry_run"] == ("--dry-run" in flag_names), (
                f"FAIL [{entry['name']}]: dry_run must advertise --dry-run "
                "(and vice versa)"
            )

    def test_json_universal(self, manifest):
        # gate envelope: gate (integration) promoted run-gate behind the envelope, so
        # --json is
        # now
        # universal across the WHOLE surface — the core run-gate exception is
        # retired (every command serializes its envelope under --json).
        for entry in manifest["commands"]:
            flag_names = {flag["name"] for flag in entry["flags"]}
            assert "--json" in flag_names, (
                f"FAIL [{entry['name']}]: --json is universal across the surface"
            )


class TestAC11PerformanceBudgets:
    """AC-11: identity performance budgets are encoded (no benchmark runs)."""

    def test_warm_target(self):
        assert PERF_WARM_TARGET_MS == 300

    def test_ci_ceiling(self):
        assert PERF_CI_CEILING_S == 2.0


class TestInputSchemaBody:
    """The decision-batch/v1 body derives enums from the schemas taxonomy."""

    ITEM_FIELDS = DECISION_BATCH_INPUT_SCHEMA["fields"]["decisions"]["items"]["fields"]

    def test_body_top_level_shape(self):
        assert set(DECISION_BATCH_INPUT_SCHEMA.keys()) == {
            "id",
            "media_type",
            "delivered_by",
            "summary",
            "fields",
            "notes",
        }
        assert DECISION_BATCH_INPUT_SCHEMA["id"] == "heddle.decision-batch/v1"
        assert set(DECISION_BATCH_INPUT_SCHEMA["fields"].keys()) == {
            "schema",
            "decisions",
        }

    def test_item_fields_match_validator_contract(self):
        # _payload_decision's enforced surface: six required fields plus
        # the ratified-but-unpersisted optional `context`.
        required = {name for name, spec in self.ITEM_FIELDS.items() if spec["required"]}
        assert required == {
            "kind",
            "class",
            "source",
            "title",
            "question",
            "options",
            "routes_to",
        }
        assert set(self.ITEM_FIELDS.keys()) == required | {
            "recommendation",
            "target_stage",
            "target_blocker",
            "smoke_disposition",
        }
        assert self.ITEM_FIELDS["recommendation"]["required"] is False

    def test_class_enum_matches_taxonomy(self):
        one_of = self.ITEM_FIELDS["class"]["one_of"]
        assert [option["value"] for option in one_of] == sorted(DECISION_BATCH_CLASSES)
        for option in one_of:
            assert option["summary"] == ESCALATION_CLASS_SUMMARIES[option["value"]]

    def test_source_enum_matches_vocabulary(self):
        one_of = self.ITEM_FIELDS["source"]["one_of"]
        assert tuple(option["value"] for option in one_of) == (DECISION_BATCH_SOURCES)

    def test_body_json_round_trips(self):
        # The manifest serializes the body verbatim — no int dict keys or
        # other JSON-lossy shapes may creep in (help ≡ manifest equality).
        assert (
            json.loads(json.dumps(DECISION_BATCH_INPUT_SCHEMA))
            == DECISION_BATCH_INPUT_SCHEMA
        )

    def test_manifest_entries_do_not_alias_the_body(self):
        # serialize_command deep-copies: mutating a manifest entry must
        # never corrupt the published contract for the process lifetime.
        entry = next(
            e for e in build_manifest()["commands"] if e["name"] == "decisions add"
        )
        assert entry["input_schema"] == DECISION_BATCH_INPUT_SCHEMA
        assert entry["input_schema"] is not DECISION_BATCH_INPUT_SCHEMA
        entry["input_schema"]["fields"].clear()
        assert DECISION_BATCH_INPUT_SCHEMA["fields"]


class TestSchemaValidatorConformance:
    """The bridge the body pins can't provide alone: a payload authored
    FROM the published schema passes the real validator, and violating
    each published constraint is actually enforced — schema and
    enforcement are one contract, exercised, not asserted."""

    ITEM_FIELDS = TestInputSchemaBody.ITEM_FIELDS

    @classmethod
    def _schema_authored_item(cls) -> dict:
        item: dict = {}
        for name, spec in cls.ITEM_FIELDS.items():
            if not spec["required"]:
                continue
            if "one_of" in spec:
                item[name] = spec["one_of"][0]["value"]
            elif spec["type"] == "list":
                item[name] = ["x"]
            else:
                item[name] = "x"
        return item

    @pytest.fixture
    def apply_item(self, tmp_path, monkeypatch, run_cli):
        from tests.tiering_review_helpers import current_host

        _host, state_path = current_host(tmp_path, monkeypatch)
        payload = tmp_path / "decision.json"

        def apply(item):
            payload.write_text(
                json.dumps(
                    {
                        "schema": DECISION_BATCH_INPUT_SCHEMA["id"],
                        "decisions": [item],
                    }
                )
            )
            before = state_path.read_bytes()
            code, out, _err = run_cli(
                [
                    "decisions",
                    "add",
                    "--input-json",
                    str(payload),
                    "--json",
                ]
            )
            if code:
                assert state_path.read_bytes() == before, out
            return code, json.loads(out)

        return apply

    def test_schema_authored_item_passes_the_validator(self, apply_item):
        code, result = apply_item(self._schema_authored_item())
        assert code == 0 and result["ok"], result

    def test_every_published_required_field_is_enforced(self, apply_item):
        for name, spec in self.ITEM_FIELDS.items():
            if spec["required"]:
                item = self._schema_authored_item()
                del item[name]
                code, result = apply_item(item)
                assert code == 2 and not result["ok"], (name, result)

    def test_every_published_non_empty_constraint_is_enforced(self, apply_item):
        for name, spec in self.ITEM_FIELDS.items():
            if spec.get("non_empty"):
                blank = "   "
            elif spec.get("items", {}).get("non_empty"):
                blank = ["   "]
            else:
                continue
            item = self._schema_authored_item()
            item[name] = blank
            code, result = apply_item(item)
            assert code == 2 and not result["ok"], (name, result)

    def test_enum_exclusions_are_enforced(self, apply_item):
        for name, value in (
            ("class", 4),
            ("source", "operator"),
            ("kind", "finding"),
            ("kind", []),
            ("kind", {}),
        ):
            item = self._schema_authored_item()
            item[name] = value
            code, result = apply_item(item)
            assert code == 2 and not result["ok"], (name, result)

    def test_optional_recommendation_type_and_closed_fields_are_enforced(
        self, apply_item
    ):
        item = self._schema_authored_item()
        item["recommendation"] = "Choose x"
        code, result = apply_item(item)
        assert code == 0 and result["ok"], result
        item["recommendation"] = {"nested": True}
        code, result = apply_item(item)
        assert code == 2 and not result["ok"], result
        del item["recommendation"]
        item["context"] = "Retired unpersisted field"
        code, result = apply_item(item)
        assert code == 2 and not result["ok"], result
