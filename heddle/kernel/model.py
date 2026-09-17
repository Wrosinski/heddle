"""Kernel state derivations.

The one resolver: active-feature resolution, the snapshot
derivations (workflow-model §12), the WM-8 stage × tier gate matrix,
§6.2 gate convergence, and the §12.2 ``next_actions`` lookup. Every
encoded value is a transcription of a ratified contract — citation
comments per constant, value-locks in tests/kernel/test_transcriptions.py
(pattern contract-value-citation-lock). Read-only: resolving touches
``.heddle.yaml``, the pointer, and one ``state.yaml`` — never a
knowledge-plane document.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from heddle.contracts import operations as ops
from heddle.contracts.gates import (
    VERDICT_PRODUCING_GATES as VERDICT_PRODUCING_GATES,
)
from heddle.contracts.result import NextAction, Severity
from heddle.contracts.schemas import FLOW_AUTO, FLOW_HITL, STAGES, WORKSPACE_STATE
from heddle.kernel.blockers import (
    STAGE_CAP_LIMIT as STAGE_CAP_LIMIT,
)
from heddle.kernel.blockers import (
    attempt_cap_reached,
    gate_cap_reached,
    no_progress_reached,
)
from heddle.kernel.blockers import (
    errored_gate_run_count as errored_gate_run_count,
)
from heddle.kernel.blockers import (
    stage_session_count as stage_session_count,
)
from heddle.kernel.project_config import (
    ConfigDiagnostic,
    KernelError,
    ProjectConfig,
    feature_state_path,
    find_project_root,
    load_project_config,
)
from heddle.kernel.reviews import (
    finding_requires_owner,
    latest_non_error_run,
    retained_unconverged_review,
    run_owner,
)
from heddle.kernel.reviews import (
    gate_converged as gate_converged,
)
from heddle.kernel.state import (
    INCOMPATIBLE_STATE_SCHEMA,
    AuthorizationFact,
    Milestone,
    StateFile,
    Task,
    read_state_file,
)
from heddle.kernel.verification import latest_verification_is_bound_to_declaration

__all__ = [
    "ActiveFeatureResolution",
    "ConfigDiagnostic",
    "FeatureSnapshot",
    "GateArtifact",
    "KernelError",
    "ProjectConfig",
    "StateFile",
    "active_feature_pointer_path",
    "derive_next_actions",
    "errored_gate_run_count",
    "feature_state_path",
    "find_project_root",
    "first_actionable_blocker",
    "gate_converged",
    "gate_convergence_phase",
    "gate_decision_deferral_active",
    "list_feature_workspaces",
    "list_active_feature_workspaces",
    "load_project_config",
    "milestones_landed_since",
    "is_terminal",
    "read_active_feature_pointer",
    "resolve_active_feature",
    "resolve_snapshot",
]

# workflow-model §3 — the one matrix row whose gates are required per
# milestone (every other cell's scope is "feature").
PER_MILESTONE_GATE_STAGES = frozenset({"implement"})
ENGINEERING_PRINCIPLES_REL = Path("docs/workflow/engineering-principles.md")

# workflow-model §12.1 — payload blocking conditions in trigger-table ROW
# order. workspace-invalid is the command failure itself, never a payload
# entry. The paired value is the §12.1 remedy command for family-A rows; the
# two id-carrying remedies are completed at derivation time.
BLOCKING_TRIGGER_ROWS = (
    ("not-authorized", "heddle phase-exit"),
    ("milestone-out-of-sequence", "heddle milestone edit"),
    ("task-incomplete", "heddle task done"),
    ("verification-missing", "heddle verify --scope"),
    ("pending-decisions", "heddle decisions"),
    ("gate-not-converged", "heddle run-gate"),
    ("awaiting-human-authorization", "heddle phase-exit"),
    # the Phase 9 handoff derives
    # AFTER the ordinary correctness rows above it; its remedy is the kickoff
    # render of the complete briefing — the human-owned Phase 9 protocol.
    ("awaiting-human-completion", "heddle kickoff"),
    (
        "principles-not-ratified",
        f"ratify {ENGINEERING_PRINCIPLES_REL.as_posix()}",
    ),
    ("gate-cap-reached", "heddle decisions"),
    ("no-progress", "heddle decisions"),
    ("attempt-cap", "heddle decisions"),
)
BLOCKING_REMEDIES = dict(BLOCKING_TRIGGER_ROWS)

# the pointer's location relative to the worktree's private git
# directory (`git rev-parse --git-path heddle/active-feature` is the
# semantic spec the pure-Python derivation must match, Assumption A3).
_POINTER_RELATIVE_PARTS = ("heddle", "active-feature")


@dataclass(frozen=True)
class GateArtifact:
    gate: str
    scope: str
    path: str  # from gate run facts


@dataclass(frozen=True)
class FeatureSnapshot:
    feature: str
    spec_path: str  # state.spec, repo-relative
    workspace: str  # "<layout.plans>/<slug>/"
    stage: str
    authorized_through: str
    flow: str
    authorizations: tuple[AuthorizationFact, ...]
    current_milestone: Milestone | None
    current_task: Task | None
    owned_paths: tuple[str, ...]  # current milestone's owns
    required_gates: tuple[str, ...]  # confirmed roles; historical tiers are decode-only
    artifacts: tuple[GateArtifact, ...]
    verification_commands: Mapping[str, str]  # state.commands
    blocking_conditions: tuple[str, ...]  # ⊆ BLOCKING_CONDITIONS
    entry: str  # "fresh" | "resume" (WM-10)
    next_steps: str | None  # latest session's next_steps
    state: StateFile  # full typed facts


@dataclass(frozen=True)
class ActiveFeatureResolution:  # chain outcome
    feature: str  # the resolved slug
    diagnostics: tuple[ConfigDiagnostic, ...]  # e.g. pointer-stale


def list_feature_workspaces(config: ProjectConfig) -> tuple[str, ...]:
    """Feature workspaces under ``layout.plans``, by ``state.yaml``
    presence (flat legacy files in the plans directory are not
    workspaces)."""
    plans_root = config.root / config.layout.plans
    if not plans_root.is_dir():
        return ()
    try:
        return tuple(
            sorted(
                entry.name
                for entry in plans_root.iterdir()
                if entry.is_dir() and (entry / WORKSPACE_STATE).is_file()
            )
        )
    except OSError as error:
        # Enumeration failures stay on the typed error
        # channel (the handlers own the code→exit mapping).
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"cannot scan {config.layout.plans}/ for feature workspaces: {error}"
            ),
            hint=f"check that {plans_root} is readable",
        ) from error


def list_active_feature_workspaces(config: ProjectConfig) -> tuple[str, ...]:
    """Exclude terminals and typed incompatible history from implicit selection."""
    active: list[str] = []
    for slug in list_feature_workspaces(config):
        try:
            state = read_state_file(feature_state_path(config, slug))
        except KernelError as error:
            if error.reason == INCOMPATIBLE_STATE_SCHEMA:
                continue
            raise
        if not is_terminal(state):
            active.append(slug)
    return tuple(active)


def active_feature_pointer_path(root: Path) -> Path | None:
    """The pointer's home for the two supported layouts (Assumption A3):
    `.git` directory → `.git/heddle/active-feature`; `.git` file with
    `gitdir: <path>` (linked worktree) → that private git directory.
    No `.git` (or an unreadable `gitdir:` line) ⇒ ``None`` — the pointer
    link of the chain is skipped."""
    git_entry = root / ".git"
    if git_entry.is_dir():
        return git_entry.joinpath(*_POINTER_RELATIVE_PARTS)
    if git_entry.is_file():
        try:
            first_line = git_entry.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None  # unreadable worktree metadata ⇒ skip the pointer link
        if first_line and first_line[0].startswith("gitdir:"):
            git_dir = Path(first_line[0].removeprefix("gitdir:").strip())
            if not git_dir.is_absolute():
                git_dir = root / git_dir
            return git_dir.joinpath(*_POINTER_RELATIVE_PARTS)
    return None


def read_active_feature_pointer(root: Path) -> str | None:
    pointer_path = active_feature_pointer_path(root)
    if pointer_path is None or not pointer_path.is_file():
        return None
    try:
        slug = pointer_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        # An unreadable or undecodable pointer behaves as
        # "no pointer" — the chain falls through to uniqueness/ambiguity
        # (the same guard active_feature_pointer_path applies to its `.git`
        # read). Session state stays repairable via `feature switch`.
        return None
    return slug or None


def resolve_active_feature(
    config: ProjectConfig, explicit: str | None
) -> ActiveFeatureResolution:
    """The chain in order: explicit flag (read-only — never re-pins)
    > recorded pointer > uniqueness inference > ``feature-ambiguous``.
    A stale pointer (names no visible workspace) falls through with a
    ``pointer-stale`` advisory."""
    workspaces = list_feature_workspaces(config)
    if explicit is not None:
        if explicit not in workspaces:
            raise KernelError(
                code="usage",
                message=(
                    f"unknown feature {explicit!r}: no workspace at "
                    f"{config.layout.plans}/{explicit}/"
                ),
                hint=f"known workspaces: {', '.join(workspaces) or '(none)'}",
            )
        return ActiveFeatureResolution(feature=explicit, diagnostics=())

    diagnostics: tuple[ConfigDiagnostic, ...] = ()
    pointer = read_active_feature_pointer(config.root)
    if pointer is not None:
        if pointer in workspaces:
            try:
                pointed_state = read_state_file(feature_state_path(config, pointer))
            except KernelError as error:
                if error.reason != INCOMPATIBLE_STATE_SCHEMA:
                    raise
                diagnostics = (
                    ConfigDiagnostic(
                        severity=Severity.ADVISORY.value,
                        code="pointer-incompatible",
                        message=(
                            f"the recorded pointer names {pointer!r}, which uses "
                            "an incompatible state schema; falling through to "
                            "active uniqueness inference"
                        ),
                    ),
                )
            else:
                if not is_terminal(pointed_state):
                    return ActiveFeatureResolution(feature=pointer, diagnostics=())
                diagnostics = (
                    ConfigDiagnostic(
                        severity=Severity.ADVISORY.value,
                        code="pointer-inactive",
                        message=(
                            f"the recorded pointer names {pointer!r}, which has "
                            "accepted completion; falling through to active "
                            "uniqueness inference"
                        ),
                    ),
                )
        else:
            diagnostics = (
                ConfigDiagnostic(
                    severity=Severity.ADVISORY.value,
                    code="pointer-stale",
                    message=(
                        f"the recorded pointer names {pointer!r}, which has no "
                        "workspace; falling through to active uniqueness inference"
                    ),
                ),
            )

    workspaces = list_active_feature_workspaces(config)
    if len(workspaces) == 1:
        return ActiveFeatureResolution(feature=workspaces[0], diagnostics=diagnostics)
    if not workspaces:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"no active feature workspaces under {config.layout.plans}/; "
                "retained accepted features require explicit --feature for history"
            ),
            hint=(
                "start new work with heddle feature prepare <slug> "
                "--area <area>; "
                "see heddle feature start --help for the inputs"
            ),
        )
    raise KernelError(
        code="feature-ambiguous",
        message=(f"multiple feature workspaces are visible: {', '.join(workspaces)}"),
        hint="select the active feature: heddle feature switch <slug>",
        candidates=workspaces,
    )


def resolve_snapshot(config: ProjectConfig, slug: str) -> FeatureSnapshot:
    """Resolve one feature's snapshot from the control plane alone:
    the workspace's ``state.yaml`` plus the already-loaded config, never a
    knowledge-plane document."""
    state = read_state_file(feature_state_path(config, slug))
    if state.feature != slug:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"feature workspace identity mismatch: {config.layout.plans}/"
                f"{slug}/state.yaml declares feature {state.feature!r}, "
                f"expected {slug!r}"
            ),
            hint=(
                f"repair state.yaml feature to {slug!r} so it matches the "
                "workspace directory"
            ),
        )
    current_milestone = _current_milestone(state)
    current_task = _current_task(current_milestone)
    from heddle.contracts.review_assignments import ROLE_STAGES

    assert state.feature_policy is not None
    required_gates = tuple(
        row.role
        for row in state.feature_policy.entries
        if ROLE_STAGES[row.role] == state.stage and row.mode != "off"
    )
    flow = _effective_flow(state, config)
    principles_precondition_passed = _auto_principles_precondition_passed(config, flow)
    return FeatureSnapshot(
        feature=state.feature,
        spec_path=state.spec,
        workspace=f"{config.layout.plans}/{slug}/",
        stage=state.stage,
        authorized_through=state.authorized_through,
        flow=flow,
        authorizations=state.authorizations,
        current_milestone=current_milestone,
        current_task=current_task,
        owned_paths=current_milestone.owns if current_milestone else (),
        required_gates=required_gates,
        artifacts=tuple(
            GateArtifact(gate=fact.gate, scope=fact.scope, path=run.artifact)
            for fact in state.gates
            for run in fact.runs
            # error-marked runs are failure tallies, not reviewable
            # artifacts — a failed attempt may legitimately leave none, so
            # they never surface on the artifact read paths (status/doctor).
            if run.verdict.get("status") != "error"
        ),
        verification_commands=state.commands,
        blocking_conditions=_blocking_conditions(
            state,
            required_gates,
            flow=flow,
            autopilot_section_present=config.autopilot.section_present,
            principles_precondition_passed=principles_precondition_passed,
        ),
        entry=_session_entry(state, required_gates),
        next_steps=state.sessions[-1].next_steps if state.sessions else None,
        state=state,
    )


def _effective_flow(state: StateFile, config: ProjectConfig) -> str:
    return state.flow or config.autopilot.flow


def _auto_principles_precondition_passed(config: ProjectConfig, flow: str) -> bool:
    return flow != FLOW_AUTO or _principles_ratified(config.root)


def first_actionable_blocker(snapshot: FeatureSnapshot) -> str | None:
    """The family-A blocking code the §12.2 blocked row is emitted for —
    the first snapshot blocking condition in trigger-table row order,
    skipping ``gate-not-converged`` (family B: run-gate clears it, so it
    never blocks the driver). The driver classifies blockers through this
    seam instead of parsing the row's reason prose,
    so the prose is display-only. For
    ``awaiting-human-authorization`` with stage work remaining,
    ``derive_next_actions`` projects the work action instead of the
    blocked row — classification and projection are decoupled for exactly
    that code; every other code still maps to its blocked row."""
    defer_gate_decisions = gate_decision_deferral_active(snapshot)
    return next(
        (
            code
            for code in snapshot.blocking_conditions
            if code != "gate-not-converged"
            and not (code == "pending-decisions" and defer_gate_decisions)
        ),
        None,
    )


def gate_decision_deferral_active(snapshot: FeatureSnapshot) -> bool:
    """Whether every pending fact is positively proven gate provenance.

    The raw ``pending-decisions`` blocking condition remains truthful. This
    implement-only predicate changes action selection while a milestone is
    open; every unsupported or mixed pending set fails closed.
    """
    state = snapshot.state
    # Strong origin identity does not change the explicit auto-flow prohibition.
    if snapshot.flow == FLOW_AUTO:
        return False
    if snapshot.stage != "implement" or not state.milestones:
        return False
    if not any(milestone.status != "done" for milestone in state.milestones):
        return False
    pending = tuple(
        decision for decision in state.decisions if decision.status != "resolved"
    )
    if not pending:
        return False
    assert state.feature_policy is not None
    required_gates = frozenset(
        row.role
        for row in state.feature_policy.entries
        if row.role == "milestone-review" and row.mode != "off"
    )
    milestone_ids = frozenset(milestone.id for milestone in state.milestones)
    return all(
        _has_gate_decision_provenance(
            decision,
            state,
            required_gates=required_gates,
            milestone_ids=milestone_ids,
        )
        for decision in pending
    )


def _has_gate_decision_provenance(
    decision: Any,
    state: StateFile,
    *,
    required_gates: frozenset[str],
    milestone_ids: frozenset[str],
) -> bool:
    if decision.kind != "finding":
        return False
    owner = run_owner(state, decision.origin_run_id)
    return owner is not None and (
        owner[0].gate in required_gates
        and owner[0].scope in milestone_ids
        and owner[1].verdict.get("status") != "error"
        and any(
            finding.finding_id == decision.origin_finding_id
            and finding_requires_owner(
                state, owner[0].gate, owner[0].scope, finding, run_id=owner[1].run_id
            )
            for finding in owner[1].report_findings
        )
    )


def _utc_minute(value: str | None) -> datetime | None:
    """Parse one ISO-8601 UTC fact timestamp at either recorded precision
    (§10.1 minute facts, second-precision verification facts), truncated to
    the minute; None for malformed or naive values (fail-quiet display
    input — lexicographic comparison would misorder mixed precisions)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).replace(second=0, microsecond=0)


def _earliest_passing_minute(state: StateFile, milestone_id: str) -> datetime | None:
    """Minute of the earliest passing verification fact for one milestone;
    None when there is none or ANY passing fact's timestamp is unparseable.
    Bailing (not skipping) on a malformed fact is deliberate: a skipped
    earliest fact could promote a later one into a false landed-since."""
    earliest: datetime | None = None
    for fact in state.verifications:
        if fact.scope != milestone_id or fact.exit_code != 0:
            continue
        at = _utc_minute(fact.at)
        if at is None:
            return None
        if earliest is None or at < earliest:
            earliest = at
    return earliest


def milestones_landed_since(state: StateFile, decision: Any) -> tuple[str, ...]:
    """Milestone ids (plan order) that went ``done`` after a pending decision
    was raised — the resolution-time staleness note: a deferred gate
    recommendation aged out while
    later milestones landed). Derived, never stored: a milestone counts iff
    it is ``done`` and its EARLIEST passing milestone-scoped verification
    fact (the advance precondition; post-done re-verifications move only the
    latest) sits in a strictly later minute than ``created_at`` — same-minute
    ties are unordered at the facts' recorded precision and never count."""
    if decision.status == "resolved":
        return ()
    raised = _utc_minute(decision.created_at)
    if raised is None:
        return ()
    return tuple(
        milestone.id
        for milestone in state.milestones
        if milestone.status == "done"
        and (landed := _earliest_passing_minute(state, milestone.id)) is not None
        and landed > raised
    )


def is_terminal(state: StateFile) -> bool:
    """Historical acceptance is one immutable typed value."""
    return state.completion is not None


def derive_next_actions(snapshot: FeatureSnapshot) -> tuple[NextAction, ...]:
    """Select the first ordered workflow action, retaining independent blockers."""
    if is_terminal(snapshot.state):
        return ()
    code = first_actionable_blocker(snapshot)
    if code == "awaiting-human-authorization":
        action = _stage_work_action(snapshot)
        if action is not None:
            return (action,)
    if code is not None:
        return (
            NextAction(
                blocker_remedy(snapshot.state, code),
                f"resolve blocking condition: {code}",
            ),
        )
    action = _stage_work_action(snapshot)
    if action is not None:
        return (action,)
    if snapshot.stage == "complete":
        return ()
    return (
        NextAction(
            ops.CommandAction(ops.PhaseExit(feature=snapshot.feature)),
            f"all {snapshot.stage} exit requirements are met",
        ),
    )


def _stage_work_action(snapshot: FeatureSnapshot) -> NextAction | None:
    if snapshot.entry == "fresh":
        return NextAction(
            ops.SessionAction(snapshot.feature, "stage-work"),
            f"render the {snapshot.stage} briefing — fresh stage, no facts yet",
        )
    if snapshot.stage == "implement":
        return _implement_next_action(snapshot)
    unconverged = _first_unconverged_gate(snapshot.state, snapshot.required_gates)
    if unconverged is not None:
        return _gate_convergence_action(snapshot.state, *unconverged)
    return None


def _gate_convergence_action(state: StateFile, gate: str, scope: str) -> NextAction:
    suffix = "" if scope == "feature" else f" for {scope}"
    return NextAction(
        ops.CommandAction(ops.RunGate(gate, feature=state.feature)),
        f"converge the required {gate} gate{suffix}",
    )


def gate_convergence_phase(
    state: StateFile, required_gates: tuple[str, ...]
) -> str | None:
    """Where the first unconverged required gate stands, or ``None`` when
    every required gate is converged. Display-only qualifier for the
    fail-closed ``gate-not-converged`` blocking code:
    a fresh stage's bare code reads as a failed round when no gate has run
    yet); the arithmetic is the same §6.2 convergence reading the blocking
    derivation uses. Phases:
    ``not-run`` (no completed run for the gate), ``verdict-fail`` (the deciding
    verdict is a fail), or ``rerun-recommended`` (the deciding verdict passed
    but recommends another round). ``None`` also covers states no label fits."""
    unconverged = _first_unconverged_gate(state, required_gates)
    if unconverged is None:
        return None
    gate, scope = unconverged
    verdict = _latest_non_error_verdict(state, gate, scope)
    if verdict is None:
        return "not-run"
    if verdict.get("status") == "fail":
        return "verdict-fail"
    if verdict.get("rerun_recommended") is True:
        return "rerun-recommended"
    # Off-contract verdict shape (unconverged via the §6.2 identity test
    # without a true rerun flag) — decline to qualify.
    return None


def blocker_remedy(state: StateFile, code: str) -> ops.Action:
    if code == "task-incomplete":
        for milestone in state.milestones:
            if milestone.status == "done":
                for task in milestone.tasks:
                    if task.status != "done":
                        return ops.ManualAction(
                            f"Repair unfinished task {task.id} in completed milestone "
                            f"{milestone.id}; task done targets the current milestone"
                        )
        return ops.ManualAction("Complete the unfinished task")
    if code == "verification-missing":
        unverified = _first_unverified_done_milestone(state)
        return ops.CommandAction(
            ops.Verify(
                scope=unverified.id if unverified else None,
                feature=state.feature,
                expect_revision=state.revision,
            )
        )
    if code in {"not-authorized", "awaiting-human-authorization"}:
        return ops.CommandAction(ops.PhaseExit(feature=state.feature))
    if code == "awaiting-human-completion":
        return ops.CommandAction(ops.Kickoff(feature=state.feature))
    if code in {
        "pending-decisions",
        "gate-cap-reached",
        "no-progress",
        "attempt-cap",
    }:
        decision = next(
            (item for item in state.decisions if item.status != "resolved"), None
        )
        if decision is not None:
            return ops.DecisionAction(
                feature=state.feature,
                decision_id=decision.id,
                choices=decision.options,
                routes_to=decision.routes_to,
            )
        return ops.CommandAction(ops.DecisionsList(feature=state.feature))
    if code == "milestone-out-of-sequence":
        return ops.ManualAction("Repair milestone ordering with heddle milestone edit")
    if code == "gate-not-converged":
        return ops.ManualAction("Run the required unconverged review gate")
    if code == "principles-not-ratified":
        return ops.ManualAction(f"ratify {ENGINEERING_PRINCIPLES_REL.as_posix()}")
    raise ValueError(f"unknown blocking condition {code!r}")


def _implement_next_action(snapshot: FeatureSnapshot) -> NextAction | None:
    state = snapshot.state
    milestone = snapshot.current_milestone
    if milestone is not None:
        if not milestone.tasks:
            return NextAction(
                ops.SessionAction(snapshot.feature, "task-expansion"),
                f"expand tasks for {milestone.id} (§4.2 task expansion)",
            )
        task = snapshot.current_task or next(
            (task for task in milestone.tasks if task.status == "todo"), None
        )
        if task is not None:
            return NextAction(
                ops.SessionAction(snapshot.feature, "stage-work"),
                f"implement task {task.id}: {task.text}",
            )
        if not _milestone_verified(state, milestone.id):
            return NextAction(
                ops.CommandAction(
                    ops.Verify(
                        scope=milestone.id,
                        feature=state.feature,
                        expect_revision=state.revision,
                    )
                ),
                f"record the verification fact for {milestone.id}",
            )
        unconverged = _first_unconverged_gate(
            state, snapshot.required_gates, milestones=(milestone.id,)
        )
        if unconverged is not None:
            return _gate_convergence_action(state, *unconverged)
        return NextAction(
            ops.CommandAction(ops.MilestoneAdvance(feature=state.feature)),
            "advance the converged milestone",
        )
    if any(entry.status == "todo" for entry in state.milestones):
        return NextAction(
            ops.CommandAction(ops.MilestoneAdvance(feature=state.feature)),
            "promote the next dependency-satisfied todo milestone",
        )
    unconverged = _first_unconverged_gate(state, snapshot.required_gates)
    if unconverged is not None:
        return _gate_convergence_action(state, *unconverged)
    return None


def _current_milestone(state: StateFile) -> Milestone | None:
    """§4.2 one-current rule; when more than one is `current` (the
    reportable §12.1 case) the FIRST in milestones order is projected —
    deterministic reading of abnormal state (degraded projection)."""
    for milestone in state.milestones:
        if milestone.status == "current":
            return milestone
    return None


def _current_task(milestone: Milestone | None) -> Task | None:
    if milestone is None:
        return None
    for task in milestone.tasks:
        if task.status == "current":
            return task
    return None


def _blocking_conditions(
    state: StateFile,
    required_gates: tuple[str, ...],
    *,
    flow: str,
    autopilot_section_present: bool,
    principles_precondition_passed: bool,
) -> tuple[str, ...]:
    """The payload blocking codes per the §12.1 trigger table, emitted in
    trigger-table ROW order. Family A fires whenever the fact
    inconsistency is present; family B (gate-not-converged) fires at
    exit-readiness."""
    if is_terminal(state):
        return ()
    fired: list[str] = []
    by_id = {milestone.id: milestone for milestone in state.milestones}

    if STAGES.index(state.stage) > STAGES.index(state.authorized_through):
        fired.append("not-authorized")

    current_count = sum(
        1 for milestone in state.milestones if milestone.status == "current"
    )
    deps_unmet = any(
        milestone.status in ("current", "done")
        and any(
            by_id.get(dep) is None or by_id[dep].status != "done"
            for dep in milestone.depends_on
        )
        for milestone in state.milestones
    )
    if current_count > 1 or deps_unmet:
        fired.append("milestone-out-of-sequence")

    if _first_incomplete_task_in_done_milestone(state) is not None:
        fired.append("task-incomplete")

    if _first_unverified_done_milestone(state) is not None:
        fired.append("verification-missing")

    if any(decision.status != "resolved" for decision in state.decisions):
        fired.append("pending-decisions")

    exit_ready = state.stage != "implement" or all(
        milestone.status == "done" for milestone in state.milestones
    )
    if exit_ready and _first_unconverged_gate(state, required_gates) is not None:
        fired.append("gate-not-converged")

    if _requires_human_authorization_gate(state, flow, autopilot_section_present):
        fired.append("awaiting-human-authorization")

    # `complete` is the human-owned Phase 9 stage — the handoff
    # derives after the ordinary rows above until a qualifying completion
    # fact exists, and the five auto-execution-only conditions below never
    # derive at complete.
    if state.stage == "complete":
        if not is_terminal(state):
            fired.append("awaiting-human-completion")
        return tuple(fired)

    if not principles_precondition_passed:
        fired.append("principles-not-ratified")

    if flow == FLOW_AUTO and gate_cap_reached(state, required_gates):
        fired.append("gate-cap-reached")

    if flow == FLOW_AUTO and no_progress_reached(state):
        fired.append("no-progress")

    if flow == FLOW_AUTO and attempt_cap_reached(state):
        fired.append("attempt-cap")

    return tuple(fired)


def _requires_human_authorization_gate(
    state: StateFile, flow: str, autopilot_section_present: bool
) -> bool:
    hitl_flow_active = flow == FLOW_HITL
    m4a_hitl_gate_enabled = state.flow == FLOW_HITL or (
        state.flow is None and autopilot_section_present
    )
    awaiting_boundary_grant = (
        state.stage != "complete" and not _authorized_past_current_boundary(state)
    )
    return hitl_flow_active and m4a_hitl_gate_enabled and awaiting_boundary_grant


def _authorized_past_current_boundary(state: StateFile) -> bool:
    return STAGES.index(state.authorized_through) > STAGES.index(state.stage)


def _principles_ratified(root: Path) -> bool:
    path = root / ENGINEERING_PRINCIPLES_REL
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    delimiter = "---\n"
    if not text.startswith(delimiter):
        return False
    parts = text.split(delimiter, 2)
    if len(parts) != 3:
        return False
    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return False
    return isinstance(frontmatter, dict) and frontmatter.get("status") == "ratified"


def _first_incomplete_task_in_done_milestone(state: StateFile) -> Task | None:
    for milestone in state.milestones:
        if milestone.status != "done":
            continue
        for task in milestone.tasks:
            if task.status != "done":
                return task
    return None


def _first_unverified_done_milestone(state: StateFile) -> Milestone | None:
    for milestone in state.milestones:
        if milestone.status == "done" and not _milestone_verified(state, milestone.id):
            return milestone
    return None


def _milestone_verified(state: StateFile, milestone_id: str) -> bool:
    return latest_verification_is_bound_to_declaration(state, milestone_id)


def _first_unconverged_gate(
    state: StateFile,
    required_gates: tuple[str, ...],
    milestones: tuple[str, ...] | None = None,
) -> tuple[str, str] | None:
    """The first (gate, scope) pair lacking a converged run, scanning
    required scopes in order — per-milestone gates over milestones in
    file order (restrictable via ``milestones``), feature-scope gates
    over the single "feature" scope. Returns ``None`` when every
    required gate is converged."""
    per_milestone = state.stage in PER_MILESTONE_GATE_STAGES
    if per_milestone:
        scopes = (
            milestones
            if milestones is not None
            else tuple(milestone.id for milestone in state.milestones)
        )
        for scope in scopes:
            for gate in required_gates:
                if not gate_converged(state, gate, scope):
                    return (gate, scope)
        return retained_unconverged_review(state, scopes)
    for gate in required_gates:
        if not gate_converged(state, gate, "feature"):
            return (gate, "feature")
    return retained_unconverged_review(state)


def _latest_non_error_verdict(
    state: StateFile, gate: str, scope: str
) -> Mapping[str, Any] | None:
    """The latest non-error run's verdict for a (gate, scope) fact, or
    ``None`` when no completed run exists. Error-marked runs (engine
    did not complete cleanly) are invisible here — they exist only as the
    fact trail the gate cap counts; "latest" is append order over the
    remaining runs."""
    run = latest_non_error_run(state, gate, scope)
    if run is None:
        return None
    verdict: Mapping[str, Any] = run.verdict
    return verdict


def _session_entry(state: StateFile, required_gates: tuple[str, ...]) -> str:
    """WM-10, pinned v0 test: fresh ⇔ no gate-run fact for a gate
    required by the current stage, and — when `stage: implement` — no
    milestone is current/done, no task is done, and no verification
    fact exists."""
    if any(session.stage == state.stage for session in state.sessions):
        return "resume"
    if any(
        fact.gate in required_gates
        # error-marked runs are invisible here too — a stage whose only
        # fact is a failed engine attempt has no usable work to resume.
        and any(run.verdict.get("status") != "error" for run in fact.runs)
        for fact in state.gates
    ):
        return "resume"
    if state.stage == "implement":
        if any(
            milestone.status in ("current", "done") for milestone in state.milestones
        ):
            return "resume"
        if any(
            task.status == "done"
            for milestone in state.milestones
            for task in milestone.tasks
        ):
            return "resume"
        if state.verifications:
            return "resume"
    return "fresh"
