"""The stateless ``heddle drive`` loop."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import heddle.driver.escalations as escalations
import heddle.driver.results as driver_results
import heddle.runtime.gate_run as gate_run
import heddle.runtime.verification as runtime_verification
from heddle.contracts import decisions as kinds
from heddle.contracts import operations as ops
from heddle.contracts.result import (
    ExitCode,
    HeddleResult,
    NextAction,
)
from heddle.contracts.schemas import (
    FLOW_AUTO,
    STAGES,
)
from heddle.driver.sessions import (
    PhaseSessionSpec,
    SessionResult,
    allowed_tools_for_stage,
    probe_claude_capabilities,
    probe_codex_capabilities,
    spawn_phase_session,
    uniform_deny_rules,
)
from heddle.driver.tempfiles import write_temp_text
from heddle.io.process import MonitorLaunchError
from heddle.kernel.model import (
    FeatureSnapshot,
    KernelError,
    ProjectConfig,
    first_actionable_blocker,
    is_terminal,
    milestones_landed_since,
    resolve_snapshot,
    stage_session_count,
)
from heddle.runtime import application
from heddle.runtime.clock import utc_now_seconds
from heddle.runtime.readiness import (
    assess_current_readiness,
    readiness_failure,
    readiness_status_block,
)

DRIVE_BLOCKED_PROCESS_EXIT = ExitCode.INTERNAL  # process exit 1: blocked/escalated
VALIDATION_ATTEMPT_LIMIT = 5
# Consecutive failing
# driver-executed verification runs for one scope before the loop raises an
# attempt-cap decision batch. Without this the loop re-ran a fast-failing
# verify unboundedly (~2 facts/second, 245 junk facts) — no session is
# involved, so the class-6 no-progress guard (which counts session facts)
# never fires.
VERIFY_ATTEMPT_LIMIT = 3
# Bound the run_loop CAS-conflict retry. A one-off conflict
# is retryable (exit 5, quiet), but a competing writer that keeps winning the CAS
# race would spin the loop forever; after this many consecutive conflicts the run
# escalates to a notify-worthy halt so an operator can intervene.
MAX_CONFLICT_RETRIES = 5
# The gate monitor reports a watchdog kill as a TIMEOUT
# (exit 124) or INACTIVITY (exit 118) termination. Treat both as a failed attempt
# that accrues toward the class-7 attempt cap instead of an unbounded FATAL halt.
_SESSION_TIMEOUT_TERMINATIONS = frozenset({"timeout", "inactivity"})


@dataclass(frozen=True)
class ValidationProblem:
    detail: str


Validator = Callable[
    [ProjectConfig, FeatureSnapshot, FeatureSnapshot], ValidationProblem | None
]


@dataclass(frozen=True)
class _SessionStep:
    halt_result: HeddleResult | None
    validation_problem: ValidationProblem | None


def run_loop(config: ProjectConfig, slug: str, *, until: str | None) -> HeddleResult:
    if until is not None and until not in STAGES:
        return driver_results.usage(
            f"unknown --until stage {until!r}", "choose a canonical stage"
        )

    try:
        startup_snap = resolve_snapshot(config, slug)
    except KernelError as error:
        return driver_results.kernel_failure(error)

    # An --until stage behind the current stage can never be reached, so
    # the loop would run unbounded. Reject it up front (exit 2, no work).
    if until is not None and STAGES.index(startup_snap.stage) > STAGES.index(until):
        return driver_results.usage(
            f"--until {until!r} is behind the current stage "
            f"{startup_snap.stage!r}; nothing to do",
            "choose a stage at or ahead of the current stage",
        )
    if is_terminal(startup_snap.state):
        return _accepted_result(startup_snap)

    # a complete-stage snapshot is resolved entirely by the
    # loop below — ordinary blocks, the HITL handoff, bounded --until, and
    # terminal success. Nothing at `complete` can execute a headless action,
    # so the capability probe and the auto close-suite requirement apply only
    # to snapshots that can.
    capability_checked = False
    capability_stage: str | None = None
    if startup_snap.stage != "complete":
        capability_failure = _capability_probe_result(False, startup_snap)
        if capability_failure is not None:
            return capability_failure
        capability_checked = True
        capability_stage = startup_snap.stage
        close_suite_failure = _auto_close_suite_result(config, startup_snap)
        if close_suite_failure is not None:
            return close_suite_failure

    validation_problem: ValidationProblem | None = None
    conflict_retries = 0
    verify_failures: dict[str, int] = {}

    # Confine every per-session temp (prompt / session-fact / raw JSONL) to
    # one scratch dir removed when the run ends, instead of leaking delete=False
    # temps that accumulate unbounded across an unattended run.
    with tempfile.TemporaryDirectory(prefix="heddle-drive-") as scratch_name:
        scratch_dir = Path(scratch_name)
        while True:
            try:
                snap = resolve_snapshot(config, slug)
            except KernelError as error:
                return driver_results.kernel_failure(error)

            if is_terminal(snap.state):
                return _accepted_result(snap)

            if _capability_probe_needed(capability_stage, snap):
                capability_failure = _capability_probe_result(False, snap)
                if capability_failure is not None:
                    return capability_failure
                capability_checked = True
                capability_stage = snap.stage

            if until is not None and snap.stage == until:
                return _success(snap, "until-reached")

            command_result = _next_command(config, snap, validation_problem)
            if isinstance(command_result, HeddleResult):
                return command_result

            next_action, blocker = command_result
            action = next_action.action
            if isinstance(action, ops.AuthoringAction):
                validation_problem = ValidationProblem(
                    detail=(
                        f"{next_action.reason}\n"
                        f"Authoring work: {action.work}\n"
                        f"References: {', '.join(action.references)}\n"
                        f"Input schema: {action.input_schema}\n"
                        f"Expected revision: {action.expected_revision}\n"
                        "Observation purpose: driver"
                    )
                )
                action = ops.SessionAction(snap.feature, "stage-work")
                next_action = NextAction(action, next_action.reason)
            if isinstance(action, ops.DecisionAction):
                return _blocked(next_action, blocker or "pending-decisions", snap=snap)
            if blocker is not None or isinstance(action, ops.ManualAction):
                raised = _raise_derived_batch_if_needed(slug, snap, blocker)
                if raised is not None:
                    return raised
                return _blocked(next_action, blocker, snap=snap)

            if isinstance(action, ops.SessionAction):
                session_step = _execute_session_step(
                    config,
                    slug,
                    snap,
                    next_action.command,
                    validation_problem,
                    capability_checked=capability_checked,
                    scratch_dir=scratch_dir,
                )
                if session_step.halt_result is not None:
                    return session_step.halt_result
                capability_checked = True
                validation_problem = session_step.validation_problem
                continue

            assert isinstance(action, ops.CommandAction)
            result = application.execute(action.operation)
            exit_code = result.exit_code

            if isinstance(action.operation, ops.RunGates) and not result.ok:
                # The batch result already carries every member's ordered remedy.
                # Observe the post-publication boundary exactly once before
                # returning it so later work cannot hide a sibling outcome.
                try:
                    current = resolve_snapshot(config, slug)
                    assess_current_readiness(config, current, purpose="driver")
                except KernelError as error:
                    return driver_results.kernel_failure(error)
                return result

            if exit_code == int(ExitCode.CONFLICT):
                # A CAS conflict is retryable, but a competing writer that
                # keeps winning the race must not spin the loop forever. Bound the
                # consecutive retries; once exhausted, escalate to a notify-worthy
                # halt so an operator can clear the contending process.
                conflict_retries += 1
                if conflict_retries >= MAX_CONFLICT_RETRIES:
                    return _persistent_conflict_failure(conflict_retries)
                continue
            conflict_retries = 0
            if _is_unconverged_gate_verdict(action, result):
                # A completed gate run whose verdict did not converge —
                # pass_with_conditions (ADVISORY/4) or a clean-engine fail
                # verdict (FATAL/3 on an ok envelope) — is a normal
                # convergence-loop outcome, not a halt: fold it into the next
                # attempt's kickoff so a stage session applies the findings
                # and re-converges the gate (attempts are never blind; bounded
                # by VALIDATION_ATTEMPT_LIMIT like every validation fold).
                # Engine errors (ok: false) keep halting below — those runs
                # are error-marked and counted toward the gate limit.
                if isinstance(action.operation, ops.RunGates):
                    try:
                        current = resolve_snapshot(config, slug)
                        assess_current_readiness(config, current, purpose="driver")
                    except KernelError as error:
                        return driver_results.kernel_failure(error)
                validation_problem = _gate_verdict_problem(result)
                continue
            verification = (result.data or {}).get("verification")
            verification_error = (
                result.error
                if result.error is not None
                and result.error.code == "verification-failed"
                and result.error.details.get("recorded") is True
                else None
            )
            if verification is None and verification_error is not None:
                verification = {
                    "scope": verification_error.details.get("scope"),
                    "exit_code": verification_error.details.get("child_exit_code"),
                    "log": verification_error.details.get("log"),
                    "fact_index": verification_error.details.get("fact_index"),
                }
            if verification is not None:
                # Recorded unusable proof is a typed command failure, but it
                # remains a normal bounded driver retry. Consume it before the
                # generic nonzero halt or the first attempt would bypass the
                # existing per-scope cap.
                scope = str(verification.get("scope", ""))
                try:
                    current_snapshot = resolve_snapshot(config, slug)
                    fact_index = verification.get("fact_index")
                    assessment = (
                        runtime_verification.assess_recorded_verification(
                            config.root,
                            current_snapshot.state,
                            scope,
                            fact_index,
                        )
                        if type(fact_index) is int
                        else runtime_verification.assess_current_verification(
                            config.root, current_snapshot.state, scope
                        )
                    )
                    if scope == "smoke" and type(fact_index) is int:
                        current_assessment = (
                            runtime_verification.assess_current_verification(
                                config.root, current_snapshot.state, scope
                            )
                        )
                        if current_assessment.fact_index == fact_index:
                            assessment = current_assessment
                    freshness_status = (
                        "fresh" if assessment.authorizes else assessment.status
                    )
                except KernelError as error:
                    return driver_results.kernel_failure(error)
                if freshness_status != "fresh":
                    verify_failures[scope] = verify_failures.get(scope, 0) + 1
                    if verify_failures[scope] >= VERIFY_ATTEMPT_LIMIT:
                        detail = (
                            f"verification for scope {scope} remained "
                            f"{freshness_status} for "
                            f"{verify_failures[scope]} consecutive times "
                            f"(latest exit {verification.get('exit_code')}, "
                            f"log {verification.get('log')}); fix the stored "
                            "verification command or the failing check, then "
                            "resolve and re-run drive"
                        )
                        return _raise_decision_batch(
                            slug,
                            current_snapshot,
                            "attempt-cap",
                            detail=detail,
                            verification_failure=True,
                        )
                else:
                    verify_failures.pop(scope, None)
                if verification_error is not None:
                    continue
            if exit_code != 0:
                return result


def _execute_session_step(
    config: ProjectConfig,
    slug: str,
    snap: FeatureSnapshot,
    next_action: str,
    validation_problem: ValidationProblem | None,
    *,
    capability_checked: bool,
    scratch_dir: Path | None = None,
) -> _SessionStep:
    halt_result = _capability_probe_result(capability_checked, snap)
    if halt_result is not None:
        return _SessionStep(halt_result, None)

    session_result = _run_phase_session(
        config, snap, next_action, validation_problem, scratch_dir=scratch_dir
    )
    if isinstance(session_result, HeddleResult):
        # a failed or degenerate kickoff halts before any spawn,
        # preserving the structured failure (never a minimal fallback prompt).
        return _SessionStep(session_result, None)
    if session_result.exit_code != 0:
        timeout_step = _handle_session_timeout(config, slug, snap, session_result)
        if timeout_step is not None:
            return timeout_step
        return _SessionStep(
            _hard_failure(
                f"phase session failed with exit {session_result.exit_code}",
                "inspect the phase-session output and retry drive",
            ),
            None,
        )

    post_snap = resolve_snapshot(config, slug)
    session_log_failure = _record_session_fact(slug, snap, post_snap)
    if session_log_failure is not None:
        return _SessionStep(session_log_failure, None)
    logged_snap = resolve_snapshot(config, slug)
    next_problem = _validate_session_result(config, snap, logged_snap)
    if next_problem is not None:
        current_state = logged_snap.state
        attempts = stage_session_count(current_state, logged_snap.stage, "attempt-cap")
        if attempts >= VALIDATION_ATTEMPT_LIMIT:
            return _SessionStep(
                _raise_decision_batch(
                    slug,
                    logged_snap,
                    "attempt-cap",
                    detail=next_problem.detail,
                ),
                next_problem,
            )
    return _SessionStep(None, next_problem)


def _handle_session_timeout(
    config: ProjectConfig,
    slug: str,
    snap: FeatureSnapshot,
    session_result: SessionResult,
) -> _SessionStep | None:
    # A watchdog kill (TIMEOUT exit 124 / INACTIVITY exit 118)
    # used to halt FATAL, demanding an unbounded human-driven retry of an
    # unattended run. Instead record a non-advancing session fact — a killed
    # session wrote nothing, so it reads as not-advanced — and let the loop
    # continue. The existing FLOW_AUTO derived guards then catch the stuck stage on
    # the next command derivation: no-progress (two consecutive non-advancing
    # sessions) or the class-7 attempt cap (five), escalating to the human decision
    # batch instead of an unbounded FATAL retry. Other non-zero exits (a genuine
    # session crash) keep falling through to the FATAL halt.
    if session_result.termination not in _SESSION_TIMEOUT_TERMINATIONS:
        return None
    post_snap = resolve_snapshot(config, slug)
    session_log_failure = _record_session_fact(slug, snap, post_snap)
    if session_log_failure is not None:
        return _SessionStep(session_log_failure, None)
    return _SessionStep(None, None)


def _auto_close_suite_result(
    config: ProjectConfig, snap: FeatureSnapshot
) -> HeddleResult | None:
    # An auto-flow feature must have autopilot.test_command configured, or
    # the Phase-9 additional close gate (_auto_close_block) is a silent no-op and
    # close trusts possibly-stale per-milestone verify facts. Require it at drive
    # startup — the same "the run cannot proceed without capability X" seam as the
    # capability probe — halting with a structured config error if absent.
    if snap.flow != FLOW_AUTO:
        return None
    if config.autopilot.test_command:
        return None
    return driver_results.usage(
        "flow: auto requires .heddle.yaml:autopilot.test_command (the additional "
        "close suite); without it the close gate is skipped",
        "set [autopilot] test_command in .heddle.yaml before driving an auto feature",
    )


def _capability_probe_result(
    capability_checked: bool, snap: FeatureSnapshot
) -> HeddleResult | None:
    if capability_checked:
        return None
    # probe the session CLI (Claude) capabilities. Gate runs follow
    # each gate's registry default lane (the gate-to-CLI configuration,
    # When any required gate defaults to the Codex lane,
    # the codex binary is probed too, so an unattended run halts at startup
    # instead of error-marking its first Phase-6 gate run mid-flight.
    missing = probe_claude_capabilities()
    if missing is not None:
        return _hard_failure(
            f"missing required Claude capability: {missing}",
            "install/update Claude Code so headless autopilot flags work",
        )
    if _requires_codex_lane(snap):
        missing = probe_codex_capabilities()
        if missing is not None:
            return _hard_failure(
                f"missing required Codex capability: {missing}",
                "install/update the codex CLI so the scheduled gate lanes can run",
            )
    return None


def _capability_probe_needed(
    checked_stage: str | None,
    snap: FeatureSnapshot,
) -> bool:
    """Re-probe when a drive session enters a stage with a new gate set."""
    return snap.stage != "complete" and snap.stage != checked_stage


def _requires_codex_lane(snap: FeatureSnapshot) -> bool:
    """Whether the effective scheduled gate/producer projection needs Codex."""
    assert snap.state.feature_policy is not None
    return any(
        reviewer is not None and reviewer.cli == "codex"
        for row in snap.state.feature_policy.entries
        if row.role in snap.required_gates and row.mode != "off"
        for reviewer in (row.primary, row.secondary)
    )


def _next_command(
    config: ProjectConfig,
    snap: FeatureSnapshot,
    validation_problem: ValidationProblem | None,
) -> tuple[NextAction, str | None] | HeddleResult:
    """Select one fresh assessment action, retaining driver-only retry behavior."""
    blocker = first_actionable_blocker(snap)
    if validation_problem is not None:
        if blocker is not None and blocker != "no-progress":
            try:
                assessment = assess_current_readiness(config, snap, purpose="driver")
            except KernelError as error:
                return readiness_failure(error)
            return (
                (assessment.next_actions[0], blocker)
                if assessment.next_actions
                else _success(snap, "complete")
            )
        # A known validation failure has its own five-session retry budget.
        # Two failed folds do not consume that budget; other blockers still win.
        attempts = stage_session_count(snap.state, snap.stage, "attempt-cap")
        if attempts >= VALIDATION_ATTEMPT_LIMIT:
            return _validation_failure(snap, validation_problem)
        return (
            NextAction(
                ops.SessionAction(snap.feature, "stage-work"),
                f"reattempt after validation problem: {validation_problem.detail}",
            ),
            None,
        )

    try:
        assessment = assess_current_readiness(config, snap, purpose="driver")
    except KernelError as error:
        return readiness_failure(error)
    if snap.stage == "complete" and blocker == "awaiting-human-completion":
        # Phase 9 stays human-owned. Report qualified repair operations but do
        # not turn them into automatic provider calls or a headless close session.
        blocked = readiness_status_block(assessment)
        if blocked is not None:
            return blocked
        if assessment.next_actions:
            return _blocked(assessment.next_actions[0], blocker, snap=snap)
        return _success(snap, "complete")
    if not assessment.next_actions:
        return _success(snap, "complete")

    # A pending owner decision must win over an earlier proof repair in the
    # runtime readiness ordering. Otherwise restarting the driver after it
    # raises an attempt-cap batch immediately executes more attempts instead
    # of presenting the decision it just recorded.
    if blocker == "pending-decisions":
        return (
            NextAction(
                ops.CommandAction(ops.DecisionsList(feature=snap.feature)),
                "resolve the pending decision before further automated work",
            ),
            blocker,
        )
    first = assessment.next_actions[0]
    action = first.action
    # A human-facing exit remedy can grant permission. Selecting it does not
    # authorize the driver to issue that grant on the human's behalf.
    if (
        blocker == "awaiting-human-authorization"
        and isinstance(action, ops.CommandAction)
        and isinstance(action.operation, ops.PhaseExit)
    ):
        return first, blocker
    selected_blocker = (
        assessment.blockers[0]
        if isinstance(action, ops.ManualAction) and assessment.blockers
        else None
        if isinstance(action, ops.DecisionAction)
        or (
            isinstance(action, ops.CommandAction)
            and not isinstance(action.operation, ops.DecisionsList)
        )
        else blocker
    )
    if assessment.original_obligations:
        references = ", ".join(
            f"{run_id}#{finding_id}"
            for run_id, finding_id in assessment.original_obligations
        )
        first = NextAction(
            first.action,
            first.reason
            + "\nObservation purpose: driver"
            + f"\nOriginal obligations: {references}",
        )
    return first, selected_blocker


def _is_unconverged_gate_verdict(
    action: ops.CommandAction, result: HeddleResult
) -> bool:
    """Completed unconverged gates fold into the next bounded phase session."""
    return (
        isinstance(action.operation, (ops.RunGate, ops.RunGates))
        and result.ok
        and result.exit_code in (ExitCode.FATAL, ExitCode.ADVISORY)
    )


def _gate_verdict_problem(result: HeddleResult) -> ValidationProblem:
    data = result.data or {}
    members = data.get("members")
    if isinstance(members, list):
        rows = []
        for member in members:
            if not isinstance(member, dict):
                continue
            actions = member.get("next_actions")
            commands = (
                [
                    str(action.get("command"))
                    for action in actions
                    if isinstance(action, dict) and action.get("command")
                ]
                if isinstance(actions, list)
                else []
            )
            findings = member.get("findings")
            classifications = (
                findings.get("by_classification", {})
                if isinstance(findings, dict)
                else {}
            )
            rows.append(
                f"{member.get('gate', 'gate')}: "
                f"run {member.get('run_id') or 'unrecorded'}, "
                f"execution {member.get('execution', 'unknown')}, "
                f"publication {member.get('publication', 'unknown')}, "
                f"findings {classifications}, "
                f"remedies {commands or ['reassess native readiness']}"
            )
        return ValidationProblem(
            detail=(
                "Completed gate batch requires convergence. Account for every "
                "member before reassessing readiness:\n" + "\n".join(rows)
            )
        )
    if data.get("interpretation_required"):
        return ValidationProblem(
            detail=(
                f"Completed review {data.get('run_id')} is retained at "
                f"{data.get('artifact')}; "
                "findings are unavailable, not zero. Read the full original response "
                "and submit a capture-bound native review interpret input. "
                "Do not rerun a provider for formatting."
            )
        )
    gate = data.get("gate", "gate")
    feature = data.get("feature", "")
    scope = data.get("scope")
    status = data.get("status", "unknown")
    counts = (data.get("findings") or {}).get("by_classification") or {}
    suffix = f"-{scope}" if scope and scope != "feature" else ""
    # run-gate reports where it actually wrote (E1: native artifacts live in
    # plans/<slug>/reviews/); the flat construction is the legacy fallback.
    artifact = data.get("artifact") or f"plans/{feature}.{gate}{suffix}.md"
    attempt = data.get("attempt")
    max_attempts = data.get("max_attempts")
    at_limit = (
        type(attempt) is int and type(max_attempts) is int and attempt >= max_attempts
    )
    if at_limit:
        resolution = (
            f"This was completed review {attempt}/{max_attempts}. Read the "
            f"review artifact {artifact}, apply or escalate every remaining "
            "Critical/Important IMPLEMENT or unknown finding, re-verify, and "
            "do not run the gate again; resolve the recorded cap decision "
            "before continuing"
        )
    else:
        budget = (
            f" (attempt {attempt}/{max_attempts})"
            if type(attempt) is int and type(max_attempts) is int
            else ""
        )
        resolution = (
            f"Read the review artifact {artifact}, apply every "
            "Critical/Important IMPLEMENT finding to the reviewed documents, "
            f"then re-run `heddle run-gate {gate}` once{budget}"
        )
    return ValidationProblem(
        detail=(
            f"required gate {gate} completed without converging (verdict: "
            f"{status}; findings: {counts.get('implement', 0)} IMPLEMENT, "
            f"{counts.get('report', 0)} REPORT). {resolution}"
        )
    )


def _run_phase_session(
    config: ProjectConfig,
    snap: FeatureSnapshot,
    next_action: str,
    problem: ValidationProblem | None,
    *,
    scratch_dir: Path | None = None,
) -> SessionResult | HeddleResult:
    try:
        allowed_tools = allowed_tools_for_stage(config, snap)
        deny_rules = uniform_deny_rules(config)
    except KernelError as error:
        return driver_results.kernel_failure(error)
    except ValueError as error:
        if snap.stage != "complete":
            raise
        return _hard_failure(
            str(error), "Use heddle kickoff for human-owned Phase 9 completion."
        )
    prompt_path = _write_prompt(snap, next_action, problem, scratch_dir=scratch_dir)
    if isinstance(prompt_path, HeddleResult):
        return prompt_path
    spec = PhaseSessionSpec(
        stage=snap.stage,
        feature=snap.feature,
        prompt_path=prompt_path,
        allowed_tools=allowed_tools,
        deny_rules=deny_rules,
        max_turns=config.autopilot.max_turns,
        max_budget_usd=config.autopilot.max_budget_usd,
    )
    try:
        return spawn_phase_session(spec, scratch_dir=scratch_dir)
    except MonitorLaunchError as error:
        return _hard_failure(
            f"phase session could not launch: {error}",
            "repair the session input/output access or executable, then retry drive",
        )


def _write_prompt(
    snap: FeatureSnapshot,
    next_action: str,
    problem: ValidationProblem | None,
    *,
    scratch_dir: Path | None = None,
) -> Path | HeddleResult:
    # Route the work prompt through the kickoff briefing bridge — the
    # rendered <stage>.briefing.md is the stage's instructions (artifacts are
    # the only inter-phase channel). Fail-loud: a failed or
    # degenerate kickoff halts the run instead of degrading to a minimal
    # header, and the raw briefing body is delivered byte-identically.
    briefing = _stage_briefing(snap.feature)
    if isinstance(briefing, HeddleResult):
        return briefing
    text = f"{briefing}\n\n---\n\n"
    text += (
        f"Feature: {snap.feature}\nStage: {snap.stage}\nNext action: {next_action}\n"
    )
    if problem is not None:
        text += f"\nPrevious validation problem:\n{problem.detail}\n"
    return write_temp_text(text, prefix="heddle-prompt-", suffix=".md", dir=scratch_dir)


def _stage_briefing(slug: str) -> str | HeddleResult:
    """Preserve the typed kickoff result or its exact nonblank briefing bytes."""
    result = application.execute(ops.Kickoff(feature=slug))
    if result.exit_code != ExitCode.OK or not result.ok:
        return result
    briefing = (result.data or {}).get("briefing")
    if not isinstance(briefing, str) or not briefing.strip():
        return driver_results.failure(
            code="internal",
            message=f"kickoff for feature {slug} returned no usable briefing",
            hint="inspect the direct kickoff result before spawning a session",
            exit_code=ExitCode.FATAL,
            diagnostics=result.diagnostics,
            next_actions=(
                NextAction(
                    ops.CommandAction(ops.Kickoff(feature=slug)),
                    "render the stage briefing directly to diagnose the kickoff result",
                ),
            ),
        )
    return briefing


def _record_session_fact(
    slug: str,
    snap: FeatureSnapshot,
    post_snap: FeatureSnapshot,
) -> HeddleResult | None:
    now = utc_now_seconds()
    operation = ops.RecordSession(
        ops.SessionInput(
            started_at=now,
            ended_at=now,
            completed=("phase session",),
            started=(),
            key_context=f"autopilot session for {snap.stage}",
            next_steps="",
            blockers=(),
            stage=snap.stage,
            revision_before=snap.state.revision,
            revision_after=post_snap.state.revision,
            advanced=_session_advanced(snap, post_snap),
        ),
        feature=slug,
    )
    result = application.execute(operation)
    if result.exit_code == ExitCode.CONFLICT:
        result = application.execute(operation)
    return result if result.exit_code != ExitCode.OK else None


def _session_advanced(pre: FeatureSnapshot, post: FeatureSnapshot) -> bool:
    return (
        post.state.revision != pre.state.revision
        or post.stage != pre.stage
        or post.authorized_through != pre.authorized_through
        or post.current_milestone != pre.current_milestone
        or post.current_task != pre.current_task
        # A recorded gate run is progress even though gate recording never
        # bumps the state revision: a session whose whole (correct) job is
        # "apply findings, re-converge the gate" must not read as a
        # no-progress attempt. Runaway gate loops stay guarded by the class-3
        # review/gate caps, which count non-converged/errored runs.
        or _gate_run_count(post.state) != _gate_run_count(pre.state)
    )


def _gate_run_count(state: Any) -> int:
    return sum(len(fact.runs) for fact in state.gates)


def _validate_session_result(
    config: ProjectConfig, pre: FeatureSnapshot, post: FeatureSnapshot
) -> ValidationProblem | None:
    validator = _STAGE_VALIDATORS.get(pre.stage)
    if validator is None:
        return None
    return validator(config, pre, post)


def _validate_specify_session(
    config: ProjectConfig, snap: FeatureSnapshot, _post: FeatureSnapshot
) -> ValidationProblem | None:
    duplicates = _duplicate_ac_ids(config.root / snap.spec_path)
    if duplicates:
        return ValidationProblem(
            detail=f"duplicate AC IDs are not unique: {', '.join(duplicates)}",
        )
    return None


def _validate_implement_session(
    _config: ProjectConfig, pre: FeatureSnapshot, post: FeatureSnapshot
) -> ValidationProblem | None:
    if len(post.state.sessions) <= len(pre.state.sessions):
        return ValidationProblem(
            detail="implement validator rejected the run: missing Session Log entry",
        )
    latest = post.state.sessions[-1]
    if latest.stage != pre.stage or latest.advanced is not True:
        return ValidationProblem(
            detail=(
                "implement validator rejected the run: missing advanced "
                "Session Log entry"
            ),
        )
    return None


_STAGE_VALIDATORS: dict[str, Validator] = {
    "specify": _validate_specify_session,
    "implement": _validate_implement_session,
}


def _duplicate_ac_ids(spec_path: Path) -> list[str]:
    try:
        text = spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    seen: set[str] = set()
    reported: set[str] = set()
    duplicates: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("### AC-"):
            continue
        ac_id = stripped.split(":", 1)[0].removeprefix("### ").strip()
        if ac_id in seen and ac_id not in reported:
            duplicates.append(ac_id)
            reported.add(ac_id)
        seen.add(ac_id)
    return duplicates


def _raise_derived_batch_if_needed(
    slug: str, snap: FeatureSnapshot, blocker: str | None
) -> HeddleResult | None:
    if blocker is None or blocker not in escalations.DERIVED_BLOCKER_CLASSES:
        return None
    return _raise_decision_batch(slug, snap, blocker)


def _raise_decision_batch(
    slug: str,
    snap: FeatureSnapshot,
    blocker: str,
    *,
    detail: str | None = None,
    verification_failure: bool = False,
) -> HeddleResult:
    escalation_class = escalations.DERIVED_BLOCKER_CLASSES[blocker]
    pending = any(
        decision.escalation_class == escalation_class and decision.status != "resolved"
        for decision in snap.state.decisions
    )
    if not pending:
        question = f"May autopilot continue past {blocker} at {snap.stage}?"
        if detail:
            question = f"{question} Validation failure: {detail}"
        kind: kinds.DecisionKind = (
            "question"
            if verification_failure
            else "stage-cap"
            if escalation_class == 3
            else "session-cap"
        )
        result = application.execute(
            ops.DecisionsAdd(
                (
                    kinds.DecisionInput(
                        kind=kind,
                        escalation_class=escalation_class,
                        source="driver",
                        title=f"Autopilot paused: {blocker}",
                        question=question,
                        options=("continue", "stop"),
                        routes_to=(snap.spec_path,),
                        target_stage=None if verification_failure else snap.stage,
                        target_blocker=None if verification_failure else blocker,
                    ),
                ),
                feature=slug,
                expect_revision=snap.state.revision,
            )
        )
        if not result.ok:
            return result
    reason = f"resolve blocking condition: {blocker}"
    if detail:
        reason = f"{reason}; validation failed: {detail}"
    # snap predates the batch this call just appended, so the enriched
    # pending-decisions enumeration would be stale here — stage/feature
    # context still applies via the message.
    return _blocked(
        NextAction(ops.CommandAction(ops.DecisionsList(feature=slug)), reason),
        blocker,
        snap=snap,
    )


def _accepted_result(snap: FeatureSnapshot) -> HeddleResult:
    observed = application.execute(
        ops.FeatureComplete(feature=snap.feature, dry_run=True)
    )
    if not observed.ok:
        return observed
    return replace(
        observed,
        data={
            **dict(observed.data or {}),
            "stage": snap.stage,
            "authorized_through": snap.authorized_through,
            "status": "complete"
            if observed.exit_code == ExitCode.OK
            else "accepted-pending",
            "blocking_conditions": [],
        },
    )


def _success(snap: FeatureSnapshot, status: str) -> HeddleResult:
    return HeddleResult.success(
        {
            "feature": snap.feature,
            "stage": snap.stage,
            "authorized_through": snap.authorized_through,
            "status": status,
            "blocking_conditions": list(snap.blocking_conditions),
            **gate_run.review_projection(snap),
        }
    )


def _blocked(
    next_action: NextAction,
    blocker: str | None = None,
    snap: FeatureSnapshot | None = None,
) -> HeddleResult:
    code = blocker or "internal"
    message = f"drive is blocked by {code}"
    hint = "resolve the blocking condition, then re-run heddle drive"
    next_actions: tuple[NextAction, ...] = (next_action,)
    if snap is not None:
        # The paused human's first two questions: where is the run, and
        # where do I look? The bare code+hint left both unanswered at the
        # first real pending-decisions pause.
        message = (
            f"drive is blocked by {code} at stage {snap.stage} (feature {snap.feature})"
        )
        if code == "awaiting-human-completion":
            # Phase 9 has no headless executor: hand the operator a complete
            # bootstrap into the existing orient -> kickoff protocol.
            hint = (
                "start a fresh interactive agent session in this repository for "
                f"feature {snap.feature}; ask it to run `heddle orient --feature "
                f"{snap.feature}`, follow the returned next_actions, and use the "
                "kickoff briefing as the canonical Phase 9 runbook through its "
                "user-owned checkpoints"
            )
        elif code == "awaiting-human-authorization":
            # The projected command may be
            # remaining stage WORK (grant == stage authorizes it) — the
            # grant itself is the boundary step, so the hint names both.
            hint = (
                "complete the projected stage work (next_actions), then "
                "record the user grant via heddle phase-exit and re-run "
                "heddle drive"
            )
        elif code == "pending-decisions":
            pending = [
                decision
                for decision in snap.state.decisions
                if decision.status != "resolved"
            ]
            if pending:
                landed_notes = tuple(
                    milestones_landed_since(snap.state, decision)
                    for decision in pending
                )
                state_doc = f"{str(snap.workspace).rstrip('/')}/state.yaml"
                hint = (
                    f"{len(pending)} pending decision batch(es) in "
                    f"{state_doc} (decisions[]) — read each batch's "
                    "question there, resolve it, then re-run heddle drive"
                )
                if any(landed_notes):
                    # A friction retrospective found that deferred gate
                    # recommendations age while milestones land — say so once
                    # per envelope, not on every reason line.
                    hint += (
                        "; milestones landed after some batches were raised — "
                        "re-verify each recommendation against the current "
                        "code before resolving"
                    )
                next_actions = tuple(
                    _resolution_action_for(snap.feature, decision, note)
                    for decision, note in zip(pending, landed_notes, strict=True)
                )
    return driver_results.failure(
        code=code,
        message=message,
        hint=hint,
        exit_code=DRIVE_BLOCKED_PROCESS_EXIT,
        next_actions=next_actions,
    )


def _decision_provenance(decision: Any, landed_since: tuple[str, ...] = ()) -> str:
    """Parenthetical provenance for a pause enumeration line. Gate-recorded
    batches carry no escalation class and a source equal to their own id, so
    each part renders only when it adds information — never
    "(class None, from code-quality#3)" on the code-quality#3 line itself.
    ``landed_since`` (kernel-derived) names milestones done after the batch
    was raised: the resolution-time staleness cue for deferred batches."""
    parts = []
    if decision.escalation_class is not None:
        parts.append(f"class {decision.escalation_class}")
    if decision.source and decision.source != decision.id:
        parts.append(f"from {decision.source}")
    if landed_since:
        parts.append(f"{', '.join(landed_since)} landed since raised")
    return f" ({', '.join(parts)})" if parts else ""


def _resolution_action_for(
    feature: str, decision: Any, landed_since: tuple[str, ...] = ()
) -> NextAction:
    """Render a truthful pending-decision action without inventing inputs."""
    reason = (
        f"{decision.id}{_decision_provenance(decision, landed_since)}: {decision.title}"
    )
    choices = tuple(decision.options)
    if decision.recommendation:
        reason += f"; recommendation: {decision.recommendation}"
    return NextAction(
        ops.DecisionAction(
            feature=feature,
            decision_id=decision.id,
            choices=choices,
            routes_to=tuple(decision.routes_to),
        ),
        reason,
    )


def _hard_failure(message: str, hint: str) -> HeddleResult:
    return driver_results.failure(
        code="workspace-invalid",
        message=message,
        hint=hint,
        exit_code=ExitCode.FATAL,
    )


def _persistent_conflict_failure(attempts: int) -> HeddleResult:
    # A single CAS conflict is retryable (exit 5, quiet), but a conflict
    # that survives the bounded retry budget is no longer self-clearing — it needs
    # human attention. Escalate to a FATAL halt so notify fires (a plain
    # retryable conflict stays silent) and name the contending-writer remediation.
    return driver_results.failure(
        code="conflict",
        message=(
            f"drive halted after {attempts} consecutive state-write conflicts; "
            "another process keeps winning the CAS race"
        ),
        hint=(
            "stop any other heddle process writing this feature's state, then "
            "re-run heddle drive"
        ),
        exit_code=ExitCode.FATAL,
        next_actions=(
            NextAction(
                action=ops.CommandAction(ops.Status()),
                reason="inspect the feature state and the contending writer",
            ),
        ),
    )


def _validation_failure(
    snap: FeatureSnapshot, problem: ValidationProblem
) -> HeddleResult:
    return driver_results.failure(
        code="internal",
        message=f"validation failed for {snap.stage}: {problem.detail}",
        hint="the next autopilot session must address this validation problem",
        exit_code=ExitCode.INTERNAL,
        next_actions=(
            NextAction(
                action=ops.SessionAction(snap.feature, "stage-work"),
                reason=f"reattempt after validation problem: {problem.detail}",
            ),
        ),
    )
