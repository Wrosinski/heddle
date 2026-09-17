"""Confirmed policy and reassessment writes at the feature's current owner."""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.feature_policy import ConfirmedPolicy
from heddle.contracts.result import Conflict, Diagnostic, HeddleResult, NextAction
from heddle.kernel.feature_policy import (
    ReviewAllowancePolicyError,
    effective_policy,
    increase_review_allowance,
    parse_axes,
    parse_policy,
    recommend_policy,
    validate_host_review_selection,
)
from heddle.kernel.project_config import (
    KernelError,
    ProjectConfig,
    feature_state_path,
    load_project_config_from_cwd,
)
from heddle.kernel.state import parse_state_document
from heddle.runtime import intake
from heddle.runtime.clock import utc_now_minutes
from heddle.runtime.diagnostics import conflict_failure, write_failure
from heddle.runtime.feature_context import (
    ResolveFeatureFailure,
    resolve_snapshot_from_cwd,
)
from heddle.runtime.locking import state_recording_lock
from heddle.runtime.output import emit_envelope
from heddle.runtime.state_store import (
    check_expect_revision,
    commit_state,
    read_state_document,
)
from heddle.runtime.write_args import usage_failure


def _transform(
    config: ProjectConfig,
    document: dict[str, Any],
    operation: ops.FeaturePolicy | ops.FeatureReassess,
    now: str,
) -> dict[str, Any]:
    result = deepcopy(document)
    if isinstance(operation, ops.FeatureReassess):
        axes = parse_axes(operation.payload)
        result.setdefault("assessments", []).append(
            {"axes": ops.decoded_payload(axes), "at": now}
        )
        return result
    policy = parse_policy(operation.payload)
    for row in policy.entries:
        if row.mode == "off":
            continue
        validate_host_review_selection(
            row.role,
            tuple(r for r in (row.primary, row.secondary) if r is not None),
            gates_enabled=config.gates_enabled,
            agents=config.agents,
        )
    wire = intake.policy_document(policy)
    previous = result.get("feature_policy")
    if wire == previous:
        return result
    expected = previous["revision"] + 1 if previous else 1
    if policy.revision != expected:
        raise KernelError(
            code="workspace-invalid",
            message=f"policy revision must be {expected}, got {policy.revision}",
            hint="reread the current policy, then submit the next policy revision",
        )
    history = result.setdefault("policy_history", [previous] if previous else [])
    history.append(wire)
    result["feature_policy"] = wire
    return result


def _result(
    operation: ops.FeaturePolicy | ops.FeatureReassess,
    document: dict[str, Any],
    revision: int,
    *,
    wrote: bool,
    next_actions: tuple[NextAction, ...] = (),
) -> HeddleResult:
    policy = document.get("feature_policy")
    data: dict[str, Any] = {
        "feature": operation.slug,
        "revision": revision,
        "wrote": wrote,
        "dry_run": getattr(operation, "dry_run", False),
        "effective_policy": intake.policy_projection(parse_policy(policy))
        if policy is not None
        else None,
    }
    if isinstance(operation, ops.FeatureReassess):
        data["recommendation"] = ops.decoded_payload(
            recommend_policy(parse_axes(operation.payload))
        )
    return HeddleResult.success(data, next_actions=next_actions)


def _change_state(
    config: ProjectConfig,
    path: Path,
    operation: ops.FeaturePolicy | ops.FeatureReassess,
) -> HeddleResult:
    before = read_state_document(path)
    if before["feature"] != operation.slug:
        raise KernelError(
            code="workspace-invalid",
            message="state feature does not match its path",
            hint="inspect and restore the intended feature workspace identity",
        )
    if before["completion"] is not None:
        raise KernelError(
            code="workspace-invalid",
            message="accepted state is immutable",
            hint="start a new feature for further policy or assessment changes",
        )
    if conflict := check_expect_revision(operation.expect_revision, before["revision"]):
        raise conflict
    changed = before

    def transform(document: dict[str, Any]) -> dict[str, Any]:
        nonlocal changed
        changed = _transform(config, document, operation, utc_now_minutes())
        return changed

    if getattr(operation, "dry_run", False):
        with state_recording_lock(path):
            current = read_state_document(path)
            if current["revision"] != before["revision"]:
                raise Conflict("state revision changed during policy preview")
            after = transform(current)
            parse_state_document(after, source=path)
            revision, wrote = current["revision"], after != current
    else:
        committed = commit_state(
            path, expect_revision=before["revision"], transform=transform
        )
        revision, wrote = committed.revision, committed.wrote
    return _result(operation, changed, revision, wrote=wrote)


def _change(operation: ops.FeaturePolicy | ops.FeatureReassess) -> HeddleResult:
    try:
        intake.validate_identity(operation.slug)
        config = load_project_config_from_cwd()
        state_path = feature_state_path(config, operation.slug)
        intake.safe_target(config.root, state_path)
        if state_path.exists():
            return _change_state(config, state_path, operation)
        path = intake.intake_path(config, operation.slug)
        intake.safe_target(config.root, path)
        dry_run = getattr(operation, "dry_run", False)
        # No directory creation: policy needs an already prepared intake.
        with nullcontext() if dry_run else state_recording_lock(path):
            if state_path.exists():
                return _change_state(config, state_path, operation)
            before = intake.read_intake(config, operation.slug)
            if before["route"] != "heddle":
                raise intake.invalid(
                    "direct route has no formal review-policy authority"
                )
            if before["create_manifest"] is not None:
                raise intake.invalid(
                    "admission has started; retry exact feature start "
                    "before amending policy"
                )
            if conflict := check_expect_revision(
                operation.expect_revision, before["revision"]
            ):
                raise conflict
            research = before["research"]
            if (
                intake.research_identity(config.root, research["reference"])
                != research["sha256"]
            ):
                raise intake.invalid(
                    "research changed; prepare and confirm the new research "
                    "before admission"
                )
            after = _transform(config, before, operation, utc_now_minutes())
            wrote = after != before
            if wrote:
                after["revision"] += 1
                after["updated"] = utc_now_minutes()
                if not dry_run:
                    intake.publish_intake(path, after)
            return _result(
                operation,
                after,
                after["revision"],
                wrote=wrote,
                next_actions=(intake.pending_intake_action(config, after),)
                if isinstance(operation, ops.FeaturePolicy)
                else (),
            )
    except Conflict as error:
        return conflict_failure(error)
    except KernelError as error:
        return write_failure(error)
    except ValueError as error:
        command = "policy" if isinstance(operation, ops.FeaturePolicy) else "reassess"
        return write_failure(
            KernelError(
                code="workspace-invalid",
                message=f"feature {command} input is invalid: {error}",
                hint=f"correct the feature {command} payload "
                "and retry at the current revision",
            )
        )
    except OSError as error:
        return write_failure(
            KernelError(
                code="workspace-invalid",
                message="feature policy workspace is unreadable or unwritable: "
                f"{error}",
                hint="repair workspace access, inspect the current revision, and retry",
            )
        )


def confirm_policy(operation: ops.FeaturePolicy) -> HeddleResult:
    return _change(operation)


def reassess_feature(operation: ops.FeatureReassess) -> HeddleResult:
    return _change(operation)


def _allowance_readback_before(
    document: dict[str, Any],
    current: ConfirmedPolicy,
    *,
    role: str,
) -> ConfirmedPolicy:
    history = document.get("policy_history")
    if not isinstance(history, list) or len(history) < 2:
        return current
    try:
        selection = next(row for row in current.entries if row.role == role)
        if selection.limit is None:
            return current
        previous = parse_policy(history[-2])
        candidate, replay = increase_review_allowance(
            previous,
            role=role,
            limit=selection.limit,
            approval=current.approval,
        )
    except (StopIteration, ValueError):
        return current
    return previous if not replay and candidate == current else current


def _allowance_result(
    operation: ops.ReviewAllowance,
    *,
    feature: str,
    before: ConfirmedPolicy,
    after: ConfirmedPolicy,
    revision: int,
    wrote: bool,
    readback: bool,
    impact: dict[str, Any],
    diagnostics: tuple[Diagnostic, ...] = (),
) -> HeddleResult:
    before_selection = next(row for row in before.entries if row.role == operation.role)
    after_selection = next(row for row in after.entries if row.role == operation.role)
    assert before_selection.limit is not None and after_selection.limit is not None
    before_effective = effective_policy(before)
    after_effective = effective_policy(after)
    return HeddleResult.success(
        {
            "feature": feature,
            "role": operation.role,
            "role_scope": after_selection.scope,
            "quality_round_limit": {
                "before": before_selection.limit,
                "after": after_selection.limit,
                "added": after_selection.limit - before_selection.limit,
            },
            "policy_revision": {
                "before": before.revision,
                "after": after.revision,
            },
            "call_budget": {
                "before": ops.decoded_payload(before_effective.budget),
                "after": ops.decoded_payload(after_effective.budget),
            },
            **impact,
            "effective_policy": ops.decoded_payload(after_effective),
            "revision": revision,
            "wrote": wrote,
            "dry_run": operation.dry_run,
            "readback": readback,
        },
        diagnostics=diagnostics,
    )


def review_allowance(operation: ops.ReviewAllowance) -> HeddleResult:
    if not isinstance(operation.feature, str) or not operation.feature.strip():
        return write_failure(
            KernelError(
                code="usage",
                message="review allowance requires an explicit feature",
                hint="pass the target feature slug with --feature",
            )
        )
    resolved = resolve_snapshot_from_cwd(operation.feature, writable=True)
    if isinstance(resolved, ResolveFeatureFailure):
        return write_failure(resolved.error, resolved.diagnostics)
    snapshot, config = resolved.snapshot, resolved.config
    path = config.root / snapshot.workspace / "state.yaml"
    try:
        if type(operation.expect_revision) is not int or operation.expect_revision < 0:
            raise KernelError(
                code="usage",
                message="review allowance requires a nonnegative expected revision",
                hint="pass --expect-revision with the current state revision",
            )
        if conflict := check_expect_revision(
            operation.expect_revision, snapshot.state.revision
        ):
            raise conflict
        before_policy: ConfirmedPolicy | None = None
        after_policy: ConfirmedPolicy | None = None
        impact: dict[str, Any] | None = None
        readback = False

        def transform(document: dict[str, Any]) -> dict[str, Any]:
            nonlocal before_policy, after_policy, impact, readback
            current = parse_state_document(document, source=path)
            if current.feature != snapshot.feature:
                raise KernelError(
                    code="workspace-invalid",
                    message="state feature does not match its path",
                    hint="inspect and restore the intended feature workspace identity",
                )
            if current.feature_policy is None:
                raise KernelError(
                    code="workspace-invalid",
                    message="review allowance requires an active confirmed policy",
                    hint="confirm the complete policy with heddle feature policy",
                )
            changed, readback = increase_review_allowance(
                current.feature_policy,
                role=operation.role,
                limit=operation.limit,
                approval=operation.approval,
            )
            before_policy = (
                _allowance_readback_before(
                    document, current.feature_policy, role=operation.role
                )
                if readback
                else current.feature_policy
            )
            after_policy = changed
            from heddle.runtime.review_assignments import allowance_impact

            impact = allowance_impact(
                replace(current, feature_policy=before_policy),
                replace(current, feature_policy=after_policy),
                role=operation.role,
            )
            if readback:
                return document
            result = deepcopy(document)
            wire = intake.policy_document(changed)
            result.setdefault(
                "policy_history", [intake.policy_document(current.feature_policy)]
            ).append(wire)
            result["feature_policy"] = wire
            return result

        if operation.dry_run:
            with state_recording_lock(path):
                current = read_state_document(path)
                if current["revision"] != snapshot.state.revision:
                    raise Conflict("state revision changed during allowance preview")
                changed = transform(current)
                parse_state_document(changed, source=path)
                revision, wrote = current["revision"], changed != current
        else:
            committed = commit_state(
                path,
                expect_revision=snapshot.state.revision,
                transform=transform,
            )
            revision, wrote = committed.revision, committed.wrote
        assert (
            before_policy is not None
            and after_policy is not None
            and impact is not None
        )
        return _allowance_result(
            operation,
            feature=snapshot.feature,
            before=before_policy,
            after=after_policy,
            revision=revision,
            wrote=wrote,
            readback=readback,
            impact=impact,
            diagnostics=resolved.diagnostics,
        )
    except Conflict as error:
        return conflict_failure(error)
    except ReviewAllowancePolicyError as error:
        return write_failure(
            KernelError(
                code="workspace-invalid",
                message=f"review allowance input is invalid: {error}",
                hint=(
                    "inspect the known role, or use heddle feature policy to lower "
                    "a limit or change role mode or approval"
                ),
            ),
            resolved.diagnostics,
        )
    except KernelError as error:
        return write_failure(error, resolved.diagnostics)
    except (OSError, ValueError, TypeError) as error:
        return write_failure(
            KernelError(
                code="workspace-invalid",
                message=f"review allowance input is invalid: {error}",
                hint=(
                    "use a known upper-limit role and a strictly higher absolute limit"
                ),
            ),
            resolved.diagnostics,
        )


def _parse_review_allowance(
    args: list[str],
) -> tuple[ops.ReviewAllowance | None, HeddleResult | None]:
    value_flags = {
        "--role",
        "--limit",
        "--approval",
        "--feature",
        "--expect-revision",
    }
    values: dict[str, str] = {}
    dry_run = False
    seen: set[str] = set()
    index = 0
    while index < len(args):
        token = args[index]
        if token in seen:
            return None, usage_failure(
                f"duplicate review allowance argument {token}",
                "provide each review allowance flag exactly once",
            )
        if token == "--dry-run":
            seen.add(token)
            dry_run = True
            index += 1
            continue
        if token not in value_flags:
            return None, usage_failure(
                f"unknown review allowance argument {token!r}",
                "use review allowance --help for the complete bounded surface",
            )
        seen.add(token)
        if index + 1 >= len(args):
            return None, usage_failure(
                f"{token} requires a value",
                "use review allowance --help for the complete bounded surface",
            )
        value = args[index + 1]
        if value in value_flags or value == "--dry-run":
            return None, usage_failure(
                f"{token} requires a value before {value}",
                "use review allowance --help for the complete bounded surface",
            )
        values[token] = value
        index += 2
    missing = [flag for flag in value_flags if flag not in values]
    if missing:
        return None, usage_failure(
            f"review allowance requires {', '.join(sorted(missing))}",
            "supply role, absolute limit, approval, feature and expected revision",
        )
    if not values["--approval"].strip():
        return None, usage_failure(
            "review allowance approval must be non-empty",
            "pass the explicit owner approval record with --approval",
        )
    try:
        limit = int(values["--limit"])
    except ValueError:
        limit = 0
    if limit <= 0:
        return None, usage_failure(
            "review allowance limit must be a positive integer",
            "pass an absolute positive integer with --limit",
        )
    try:
        revision = int(values["--expect-revision"])
    except ValueError:
        return None, usage_failure(
            "review allowance expected revision must be an integer",
            "pass the current state revision with --expect-revision",
        )
    if revision < 0:
        return None, usage_failure(
            "review allowance expected revision must be nonnegative",
            "pass the current state revision with --expect-revision",
        )
    return (
        ops.ReviewAllowance(
            role=values["--role"],
            limit=limit,
            approval=values["--approval"],
            feature=values["--feature"],
            expect_revision=revision,
            dry_run=dry_run,
        ),
        None,
    )


def _render_allowance(result: HeddleResult) -> None:
    stream = sys.stdout if result.ok else sys.stderr
    print(
        json.dumps(result.data if result.ok else result.to_envelope(), indent=2),
        file=stream,
    )


def run_review_allowance(args: list[str], json_mode: bool) -> int:
    operation, failure = _parse_review_allowance(args)
    if failure is not None:
        return emit_envelope(failure, json_mode, _render_allowance)
    assert operation is not None
    from heddle.runtime.application import execute

    return emit_envelope(execute(operation), json_mode, _render_allowance)


def run_feature_policy(args: list[str], json_mode: bool) -> int:
    return intake.run_feature_input(args, json_mode, "policy")


def run_feature_reassess(args: list[str], json_mode: bool) -> int:
    return intake.run_feature_input(args, json_mode, "reassess")
