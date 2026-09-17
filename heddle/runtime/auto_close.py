"""Read-only completion audits and the separately executed configured suite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import TYPE_CHECKING, Any

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    CompletionRepair,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
)
from heddle.kernel.state import AuthorizationFact, CloseSuiteFact
from heddle.runtime.audit import (
    decision_journal_path,
    decision_journal_text_audit,
    friction_retrospective_path,
)
from heddle.runtime.clock import utc_now_seconds
from heddle.runtime.verify_exec import run_verification_command

if TYPE_CHECKING:
    from heddle.runtime.feature_context import ResolvedSnapshotContext

FRICTION_RETRO_HEADINGS = (
    "Tooling fights",
    "Prompt gaps",
    "Validator false-positives",
    "Escalations that should have been policy-resolvable",
)


@dataclass(frozen=True)
class CompletionRepairObservation:
    repair: CompletionRepair
    next_action: NextAction
    exit_code: ExitCode = ExitCode.FATAL


def missing_retro_headings(text: str) -> tuple[str, ...]:
    present = {
        line.lstrip("#").strip().lower()
        for line in text.splitlines()
        if line.lstrip().startswith("#")
    }
    return tuple(
        heading for heading in FRICTION_RETRO_HEADINGS if heading.lower() not in present
    )


def effective_close_grant(
    authorizations: tuple[AuthorizationFact, ...],
) -> AuthorizationFact | None:
    """The rule is simple: the effective close grant is the LAST append-ordered
    authorization ``through == "complete"``; timestamps and current or
    pending flow never alter the selection."""
    for grant in reversed(authorizations):
        if grant.through == "complete":
            return grant
    return None


def close_audit_repairs(
    context: ResolvedSnapshotContext,
) -> tuple[CompletionRepairObservation, ...]:
    repairs: list[CompletionRepairObservation] = []
    retro = friction_retrospective_path(context.config, context.feature)
    retro_relative = retro.relative_to(context.config.root).as_posix()
    if not retro.is_file():
        repairs.append(
            _repair_observation(
                check="retrospective",
                cause="missing",
                path=retro_relative,
                message="close validation failed: friction retrospective is missing",
                hint="write the Phase 9 friction retrospective before the final close",
                action=ops.ManualAction(f"write {retro_relative}"),
                reason="provide the friction retrospective required at final close",
            )
        )
    else:
        try:
            retro_text = retro.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            repairs.append(
                _repair_observation(
                    check="retrospective",
                    cause="unreadable",
                    path=retro_relative,
                    message=(
                        "close validation failed: friction retrospective exists but "
                        "is unreadable"
                    ),
                    hint=(
                        "ensure the Phase 9 friction retrospective is readable UTF-8 "
                        "text"
                    ),
                    action=ops.ManualAction(f"write {retro_relative}"),
                    reason="provide a readable friction retrospective at final close",
                )
            )
        else:
            if not retro_text.strip():
                repairs.append(
                    _repair_observation(
                        check="retrospective",
                        cause="empty",
                        path=retro_relative,
                        message=(
                            "close validation failed: friction retrospective is empty"
                        ),
                        hint=(
                            "write the Phase 9 friction retrospective before the final "
                            "close"
                        ),
                        action=ops.ManualAction(f"write {retro_relative}"),
                        reason=(
                            "provide the friction retrospective required at final close"
                        ),
                    )
                )
            elif missing_headings := missing_retro_headings(retro_text):
                repairs.append(
                    _repair_observation(
                        check="retrospective",
                        cause="missing-headings",
                        path=retro_relative,
                        message=(
                            "close validation failed: friction retrospective is "
                            "missing "
                            f"required headings ({', '.join(missing_headings)})"
                        ),
                        hint=(
                            "the Phase 9 friction retrospective must record all four "
                            "categories: Tooling fights; Prompt gaps; Validator "
                            "false-positives; Escalations that should have been "
                            "policy-resolvable"
                        ),
                        action=ops.ManualAction(f"write {retro_relative}"),
                        reason=(
                            "record all four friction-retrospective categories before "
                            "the final close"
                        ),
                    )
                )

    journal = decision_journal_path(context.config, context.feature)
    journal_relative = journal.relative_to(context.config.root).as_posix()
    if not journal.is_file():
        repairs.append(
            _repair_observation(
                check="decision-journal",
                cause="missing",
                path=journal_relative,
                message="close validation failed: decision journal is missing",
                hint="write the decision journal before the final close",
                action=ops.ManualAction(f"write {journal_relative}"),
                reason="provide the decision journal required at final close",
            )
        )
    else:
        try:
            journal_text = journal.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            repairs.append(
                _repair_observation(
                    check="decision-journal",
                    cause="unreadable",
                    path=journal_relative,
                    message=(
                        "close validation failed: decision journal exists but is "
                        "unreadable"
                    ),
                    hint="ensure the decision journal is readable UTF-8 text",
                    action=ops.ManualAction(f"write {journal_relative}"),
                    reason="provide a readable decision journal at final close",
                )
            )
        else:
            audit = decision_journal_text_audit(journal_text, context.snapshot.state)
            if audit.status != "complete":
                details = []
                if audit.missing:
                    details.append(f"missing entries: {', '.join(audit.missing)}")
                if audit.malformed:
                    details.append(f"malformed entries: {', '.join(audit.malformed)}")
                suffix = f" ({'; '.join(details)})" if details else ""
                repairs.append(
                    _repair_observation(
                        check="decision-journal",
                        cause="audit-incomplete",
                        path=journal_relative,
                        message=(
                            "close validation failed: decision journal audit "
                            f"incomplete{suffix}"
                        ),
                        hint=(
                            "add one well-formed journal entry for each policy-sourced "
                            "decision fact"
                        ),
                        action=ops.ManualAction(f"write {journal_relative}"),
                        reason=(
                            "complete the decision journal audit before the final close"
                        ),
                    )
                )

    if not context.config.autopilot.test_command:
        grant = effective_close_grant(context.snapshot.state.authorizations)
        if grant is None or grant.source != "user":
            repairs.append(
                _repair_observation(
                    check="close-configuration",
                    cause="suite-or-user-grant-required",
                    path=".heddle.yaml",
                    message=(
                        "close validation failed: no configured test command and the "
                        "effective close grant is not user-sourced"
                    ),
                    hint=(
                        "set autopilot.test_command in .heddle.yaml before the final "
                        "close"
                    ),
                    action=ops.ManualAction(
                        "set autopilot.test_command in .heddle.yaml"
                    ),
                    reason="configure the suite required at final close",
                )
            )
    return tuple(repairs)


def _repair_observation(
    *,
    check: str,
    cause: str,
    path: str,
    message: str,
    hint: str,
    action: ops.Action,
    reason: str,
    code: str = "internal",
) -> CompletionRepairObservation:
    return CompletionRepairObservation(
        repair=CompletionRepair(check, cause, path, code, message, hint),
        next_action=NextAction(action=action, reason=reason),
    )


def run_close_suite(
    context: ResolvedSnapshotContext,
) -> CloseSuiteFact | HeddleResult | None:
    """Run one configured suite outside the acceptance transaction."""
    command = context.config.autopilot.test_command
    if not command:
        return None
    log_path = _close_suite_log_path(context)
    exit_code = run_verification_command(context.config.root, command, log_path)
    if exit_code != 0:
        return _close_validation_failure(
            message=(
                f"close validation failed: configured test command exited "
                f"{exit_code}; log: {log_path}"
            ),
            hint="make the configured autopilot test command pass before close",
            action=ops.ManualAction(command),
            reason="run the clean-venv suite required at final close",
        )
    return CloseSuiteFact(
        command, log_path.relative_to(context.state_path.parent).as_posix(), exit_code
    )


def _close_suite_log_path(context: ResolvedSnapshotContext) -> Path:
    return (
        context.state_path.parent
        / "verification"
        / f"final-close-{utc_now_seconds().replace(':', '')}-{token_hex(8)}.log"
    )


def _close_validation_failure(
    *,
    message: str,
    hint: str,
    action: ops.Action,
    reason: str,
) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="internal", message=message, hint=hint),
        exit_code=ExitCode.FATAL,
        next_actions=(NextAction(action=action, reason=reason),),
    )


def archive_trajectory_best_effort(config: Any, feature: str) -> dict[str, Any]:
    """Archive the feature's gate trajectory at final close, best-effort.

    Archival lands inside the close and must never block one: any
    I/O or malformed-input failure is reported in the close payload, not
    raised. The terminal state append has already committed when this
    runs."""
    from heddle.runtime.trajectory import archive_trajectory

    try:
        record_path = archive_trajectory(
            config.root,
            feature,
            now=datetime.now(UTC).date(),
            plans_dir=config.root / config.layout.plans,
        )
    except Exception as error:  # noqa: BLE001 — deliberate catch-all: the
        # terminal state append has already committed, so ANY archival fault
        # (I/O, malformed JSON, or a wrong-typed field in a hand-edited
        # summary) must degrade to a noted failure, never un-close the
        # feature. The error is recorded in the close payload, not silenced.
        return {"archived": False, "error": str(error)}
    if record_path is None:
        return {"archived": False, "error": "no gate trajectory artifacts found"}
    return {
        "archived": True,
        "record": record_path.relative_to(config.root).as_posix(),
    }


def open_decision_refs(document: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for index, decision in enumerate(document.get("decisions", []), start=1):
        # Canonical "resolved" predicate matches _blocking_conditions
        # (status == "resolved" ⇒ not open); resolved_at is schema-optional.
        if not isinstance(decision, dict) or decision.get("status") == "resolved":
            continue
        ref = decision.get("id") or f"decision-{index}"
        refs.append(str(ref))
    return refs
