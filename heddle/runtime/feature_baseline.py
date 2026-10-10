"""The recorded re-point of a missing or diverged feature baseline."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.result import ExitCode, HeddleError, HeddleResult
from heddle.gate import entry
from heddle.runtime.clock import utc_now_minutes
from heddle.runtime.feature_context import ResolvedSnapshotContext
from heddle.runtime.write_args import parse_write_args, usage_failure
from heddle.runtime.write_path import emit_result, run_state_mutation

_COMMAND = "feature baseline set"
_USAGE = (
    "usage: heddle feature baseline set --commit <rev> --reason <text> "
    "[--expect-revision <n>] [--dry-run] [--feature <slug>]"
)


def run_feature_baseline_set(args: list[str], json_mode: bool) -> int:
    parsed, values, _positionals, failure = parse_write_args(
        args,
        value_flags={
            "--commit": ("--commit requires a revision", _USAGE),
            "--reason": ("--reason requires a text", _USAGE),
        },
        unknown_hint=_USAGE,
        allow_positionals=False,
    )
    if failure is not None:
        return emit_result(failure, json_mode)
    assert parsed is not None
    commit, reason = values["--commit"], values["--reason"]
    if commit is None or reason is None:
        return emit_result(
            usage_failure(
                "feature baseline set requires --commit and --reason", _USAGE
            ),
            json_mode,
        )
    from heddle.runtime.application import execute

    return emit_result(
        execute(
            ops.FeatureBaselineSet(
                commit,
                reason,
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def set_feature_baseline(operation: ops.FeatureBaselineSet) -> HeddleResult:
    """Record a reachable ancestor of HEAD in place of an unavailable baseline.

    The previous and new baselines, the reason and the time are appended to
    ``source_baseline_history``. A feature without a recorded baseline, a
    baseline that is still available and a commit that is not an ancestor of
    HEAD are refused without a write.
    """
    reason = operation.reason.strip()
    if not reason or not operation.commit.strip():
        return usage_failure(
            "feature baseline set requires a commit and a non-blank reason", _USAGE
        )
    # Filled by the precheck, then read by the render and the success data.
    extra: dict[str, Any] = {}

    def precheck(context: ResolvedSnapshotContext) -> HeddleResult | None:
        return _repoint(context, operation.commit, reason, extra)

    def render(document: dict[str, Any]) -> dict[str, Any]:
        entry = deepcopy(extra["history_entry"])
        document["source_baseline"] = dict(entry["to"])
        document["source_baseline_history"] = [
            *document.get("source_baseline_history", []),
            entry,
        ]
        return document

    return run_state_mutation(
        operation,
        command_name=_COMMAND,
        render=render,
        extra=extra,
        precheck=precheck,
        baseline_repoint=True,
    )


def _repoint(
    context: ResolvedSnapshotContext,
    revision: str,
    reason: str,
    extra: dict[str, Any],
) -> HeddleResult | None:
    recorded = context.snapshot.state.source_baseline
    if recorded is None:
        return _refused(
            context,
            f"feature {context.feature} has no recorded source baseline to re-point",
            "a feature admitted without a recorded baseline keeps its earlier "
            "test selection; there is nothing to re-point",
        )
    root = context.config.root
    unavailable = entry.unavailable_feature_baseline(root, recorded)
    if unavailable is None:
        return _refused(
            context,
            f"the recorded feature baseline {recorded.oid} is still available; a "
            "re-point replaces only a baseline that is missing or diverged",
            "keep the recorded baseline while HEAD still contains it",
        )
    try:
        target = entry.repoint_target(root, revision)
    except entry.RepointTargetError as error:
        return _refused(
            context,
            str(error),
            f"name a commit that HEAD contains, then rerun `{entry.REPOINT_COMMAND}`",
        )
    previous = {"kind": recorded.kind, "oid": recorded.oid}
    replacement = {"kind": "git-commit", "oid": target}
    extra.update(
        {
            "source_baseline": replacement,
            "previous_baseline": previous,
            "unavailable_reason": unavailable.reason,
            "history_entry": {
                "from": previous,
                "to": replacement,
                "reason": reason,
                "at": utc_now_minutes(),
            },
        }
    )
    return None


def _refused(context: ResolvedSnapshotContext, message: str, hint: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="workspace-invalid", message=message, hint=hint),
        exit_code=ExitCode.FATAL,
        diagnostics=context.diagnostics,
    )
