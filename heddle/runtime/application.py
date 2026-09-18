"""One lazy application entry for CLI adapters and internal typed callers."""

from __future__ import annotations

from importlib import import_module
from typing import cast

from heddle.contracts import operations as ops
from heddle.contracts.result import HeddleResult
from heddle.runtime.write_args import usage_failure

_BINDINGS: dict[type, tuple[str, str]] = {
    ops.FeatureStart: ("feature_start", "feature_start"),
    ops.FeaturePrepare: ("intake", "prepare_feature"),
    ops.FeaturePolicy: ("feature_policy", "confirm_policy"),
    ops.FeatureReassess: ("feature_policy", "reassess_feature"),
    ops.FeatureInputsSet: ("feature_inputs", "feature_inputs_set"),
    ops.AttributeSources: ("source_attribution", "attribute_sources"),
    ops.FeatureSwitch: ("pointer", "feature_switch"),
    ops.FeatureComplete: ("completion", "complete_feature"),
    ops.Search: ("search", "search"),
    ops.ShowPrompt: ("show_prompt", "show_prompt"),
    ops.RecordReviewDisposition: ("review_assignments", "record_disposition"),
    ops.ReviewRoundOpen: ("review_assignments", "open_review_round"),
    ops.InterpretReview: ("review_assignments", "interpret_review"),
    ops.ReviewAllowance: ("feature_policy", "review_allowance"),
    ops.Doctor: ("doctor", "doctor"),
    ops.Init: ("init_host", "initialize"),
    ops.Validate: ("validate", "validate"),
    ops.Sync: ("sync", "sync"),
    ops.Status: ("status", "status"),
    ops.Orient: ("status", "orient"),
    ops.Kickoff: ("kickoff", "kickoff"),
    ops.Drive: ("drive", "drive"),
    ops.RunGate: ("gate_run", "execute_gate"),
    ops.RunGates: ("gate_run", "execute_gates"),
    ops.Verify: ("write_path", "verify"),
    ops.RecordSession: ("write_path", "record_session"),
    ops.ResolveDecision: ("decisions", "resolve_decision"),
    ops.DecisionsList: ("decisions", "decisions_list"),
    ops.DecisionsAdd: ("decisions", "decisions_add"),
    ops.RecordPolicy: ("decisions", "record_policy"),
    ops.CommandsSet: ("write_path", "commands_set"),
    ops.CommandsUnset: ("write_path", "commands_unset"),
    ops.MilestoneAdd: ("write_path", "milestone_add"),
    ops.MilestoneEdit: ("write_path", "milestone_edit"),
    ops.MilestoneAdvance: ("write_path", "milestone_advance"),
    ops.TaskAdd: ("write_path", "task"),
    ops.TaskCurrent: ("write_path", "task"),
    ops.TaskDone: ("write_path", "task"),
    ops.PhaseExit: ("write_path", "phase_exit"),
    ops.FlowSet: ("write_path", "flow_set"),
}


def execute(operation: ops.Operation) -> HeddleResult:
    binding = _BINDINGS.get(type(operation))
    if binding is None:
        return usage_failure(
            f"unsupported operation {type(operation).__name__}",
            "use a supported typed application operation",
        )
    expect_revision = getattr(operation, "expect_revision", None)
    if expect_revision is not None and (
        type(expect_revision) is not int or expect_revision < 0
    ):
        return usage_failure(
            "expected revision must be a nonnegative integer",
            "pass the current nonnegative integer revision",
        )
    module, name = binding
    service = getattr(import_module(f"heddle.runtime.{module}"), name)
    return cast(HeddleResult, service(operation))
