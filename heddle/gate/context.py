from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from heddle.gate.extraction import (
    extract_feature_spec_ac_ids,
    extract_milestone_section,
)
from heddle.gate.types import (
    GateContext,
    GateType,
    PriorReviewArtifact,
)
from heddle.io.source import capture_source_path
from heddle.kernel.project_config import KernelError
from heddle.kernel.source_manifest import ObservedPath, normalize_paths

if TYPE_CHECKING:
    from heddle.kernel.model import FeatureSnapshot
    from heddle.kernel.project_config import ProjectConfig
    from heddle.kernel.state import GateRun, Milestone, StateFile

# The native workspace's plan-prose document (workflow-model §4.1): the seam
# reads exactly one bounded `### Milestone <id>:` section from it.
PLAN_FILENAME = "plan.md"


def _selected_milestone(
    snapshot: FeatureSnapshot, gate_type: GateType, milestone_id: str | None = None
) -> Milestone | None:
    if milestone_id is not None:
        for candidate in snapshot.state.milestones:
            if candidate.id == milestone_id:
                return candidate
        raise KernelError(
            code="workspace-invalid",
            message=f"review milestone is unavailable: {milestone_id}",
            hint="select an existing milestone and retry the review",
        )
    selected = snapshot.current_milestone
    if selected is None and gate_type.requires_milestone:
        # Implement exit checks every milestone in file order. Once all are
        # done there is no current milestone, so target the same first
        # unconverged (gate, scope) pair that orient already selected.
        from heddle.kernel.model import gate_converged

        selected = next(
            (
                candidate
                for candidate in snapshot.state.milestones
                if not gate_converged(
                    snapshot.state,
                    gate_type.name,
                    candidate.id,
                )
            ),
            None,
        )
    return selected


def build_lock_context_from_snapshot(
    snapshot: FeatureSnapshot,
    config: ProjectConfig,
    gate_type: GateType,
    *,
    milestone_id: str | None = None,
) -> GateContext:
    """Name the gate lock from snapshot metadata without reading review inputs."""
    milestone = _selected_milestone(snapshot, gate_type, milestone_id)
    return GateContext(
        gate_type=gate_type,
        feature=snapshot.feature,
        repo_root=config.root,
        workspace_dir=config.root / snapshot.workspace,
        plan_path=None,
        spec_path=None,
        milestone=milestone.id if milestone is not None else None,
    )


def build_context_from_snapshot(
    snapshot: FeatureSnapshot,
    config: ProjectConfig,
    gate_type: GateType,
    cli: str = "claude",
    *,
    source_observations: dict[str, ObservedPath] | None = None,
    milestone_id: str | None = None,
    basis_only: bool = False,
) -> GateContext:
    """Build the engine ``GateContext`` from the kernel ``FeatureSnapshot`` plus
    one bounded ``extract_milestone_section`` read of the active feature's plan
    body — the runtime seam. Milestone
    facts (id, title, satisfies and owned paths) come from the snapshot, not
    from re-parsed plan frontmatter; the plan/spec prose is read for the
    engine's prompt builders."""
    repo_root = config.root
    plan_path = repo_root / snapshot.workspace / PLAN_FILENAME
    spec_path = repo_root / snapshot.spec_path
    observations = source_observations if source_observations is not None else {}
    plan_content = _document_content(repo_root, plan_path, observations)
    spec_content = _document_content(repo_root, spec_path, observations)

    milestone = _selected_milestone(snapshot, gate_type, milestone_id)
    milestone_id = milestone.id if milestone is not None else None
    milestone_section = (
        extract_milestone_section(plan_content, milestone_id)
        if plan_content and milestone_id
        else ""
    )
    scope = (
        milestone_id
        if gate_type.requires_milestone and milestone_id is not None
        else "feature"
    )
    workspace_dir = repo_root / snapshot.workspace
    assignment = None
    assignment_round = None
    reviewer_slot = None
    assignment_context = ""
    from dataclasses import asdict

    from heddle.kernel.review_assignments import assignment_for, launch_round

    assignment = assignment_for(snapshot.state, gate_type.name, scope)
    assignment_round = launch_round(snapshot.state, assignment)
    reviewer_slot = next(
        (s.name for s in assignment_round.slots if s.reviewer.cli == cli), None
    )
    assignment_context = json.dumps(
        {
            "assignment_id": assignment.id,
            **asdict(assignment_round),
            "lead_dispositions": [
                asdict(d)
                for d in snapshot.state.review_assignments.dispositions
                if any(
                    s.run_id == d.run_id
                    and s.assignment_id == assignment.id
                    and s.round_number < assignment_round.number
                    for s in snapshot.state.review_assignments.sources
                )
            ],
        },
        sort_keys=True,
    )
    prior_runs = _prior_lane_runs(snapshot.state, gate_type.name, scope, cli)
    return GateContext(
        review_assignment=assignment,
        assignment_round=assignment_round,
        reviewer_slot=reviewer_slot,
        gate_type=gate_type,
        feature=snapshot.feature,
        repo_root=repo_root,
        plan_path=plan_path if plan_path.is_file() else None,
        spec_path=spec_path if spec_path.is_file() else None,
        milestone=milestone_id,
        milestone_title=milestone.title if milestone is not None else "",
        milestone_section=milestone_section,
        satisfies_ac_ids=list(milestone.satisfies) if milestone is not None else [],
        owned_paths=list(milestone.owns) if milestone is not None else [],
        spec_ac_ids=extract_feature_spec_ac_ids(spec_content) if spec_content else [],
        plan_content=plan_content,
        spec_content=spec_content,
        source_observations=observations,
        operational_plan_facts=tuple(
            {
                "id": item.id,
                "title": item.title,
                "satisfies": list(item.satisfies),
                "owns": list(normalize_paths(item.owns)),
                "depends_on": list(item.depends_on),
                "verification": dict(item.verification),
                "tasks": [{"id": task.id, "text": task.text} for task in item.tasks],
            }
            for item in snapshot.state.milestones
        ),
        feature_owned_paths=tuple(
            sorted(
                {
                    path
                    for item in snapshot.state.milestones
                    for path in normalize_paths(item.owns)
                },
                key=str.encode,
            )
        ),
        source_baseline=snapshot.state.source_baseline,
        verification_commands=dict(snapshot.verification_commands),
        document_review_context=assignment_context,
        document_review_round=assignment_round.number,
        workspace_dir=workspace_dir,
        latest_lane_run_id=prior_runs[-1].run_id if prior_runs else None,
        prior_completed_runs=len(prior_runs),
        prior_review_artifacts=(
            ()
            if basis_only
            else _capture_prior_reviews(
                snapshot.state,
                gate_type.name,
                scope,
                workspace_dir,
                cli,
                feature=snapshot.feature,
            )
        ),
        prior_decisions=(
            [] if basis_only else _prior_decisions(snapshot.state, gate_type, scope)
        ),
    )


def _document_content(
    root: Path, path: Path, observations: dict[str, ObservedPath]
) -> str:
    relative = path.relative_to(root).as_posix()
    captured = capture_source_path(root, relative, observations)
    if captured.kind == "missing":
        return ""
    if captured.kind != "file":
        raise KernelError(
            code="workspace-invalid",
            message=f"review document is not a regular file: {relative}",
            hint="restore a regular UTF-8 governing document before review",
        )
    try:
        return captured.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"review document is not UTF-8: {relative}",
            hint="restore readable UTF-8 governing document bytes before review",
        ) from error


def _prior_lane_runs(
    state: StateFile, gate: str, scope: str, cli: str
) -> tuple[GateRun, ...]:
    from heddle.kernel.review_assignments import assignment_for, launch_round

    assignment = assignment_for(state, gate, scope)
    current = launch_round(state, assignment)
    prior_ids = {
        source.run_id
        for source in state.review_assignments.sources
        if source.assignment_id == assignment.id
        and source.round_number < current.number
    }
    return tuple(
        run
        for fact in state.gates
        if fact.scope == scope
        for run in fact.runs
        if run.verdict.get("status") != "error" and run.run_id in prior_ids
    )


def _capture_prior_reviews(
    state: StateFile,
    gate: str,
    scope: str,
    workspace_dir: Path,
    cli: str,
    *,
    feature: str,
) -> tuple[PriorReviewArtifact, ...]:
    """Read, hash and validate each accepted same-lane canonical result once."""
    from heddle.gate.entry import read_contained_utf8_artifact, retained_review_result

    evidence = []
    eligible_ids = {run.run_id for run in _prior_lane_runs(state, gate, scope, cli)}
    for fact in state.gates:
        if fact.scope != scope:
            continue
        for run in fact.runs:
            if run.run_id not in eligible_ids:
                continue
            source, content = read_contained_utf8_artifact(
                workspace_dir, relative_path=run.artifact
            )
            if (
                hashlib.sha256(content.encode("utf-8")).hexdigest()
                != run.artifact_sha256
            ):
                raise KernelError(
                    code="workspace-invalid",
                    message=(
                        "canonical review bytes differ from their accepted event: "
                        f"{source}"
                    ),
                    hint="restore the recorded canonical JSON before reviewing again",
                )
            result = retained_review_result(
                content, run, feature=feature, gate=fact.gate, scope=scope
            )
            evidence.append(
                PriorReviewArtifact(
                    source,
                    content,
                    run.run_id,
                    run.input_hash,
                    result,
                )
            )
    return tuple(evidence)


def _prior_decisions(state: Any, gate_type: GateType, scope: str) -> list[dict]:
    """Project decisions from exact origins in the gate's review pipeline."""
    from heddle.contracts.decisions import resolution_payload
    from heddle.kernel.reviews import run_owner

    sources = {gate_type.name}
    ledger = []
    prior_origins = {
        run.run_id for run in _prior_lane_runs(state, gate_type.name, scope, "")
    }
    sources.update(
        fact.gate
        for fact in state.gates
        if any(run.run_id in prior_origins for run in fact.runs)
    )
    for decision in state.decisions:
        if prior_origins is not None and decision.origin_run_id not in prior_origins:
            continue
        owner = run_owner(state, decision.origin_run_id)
        if owner is None or owner[0].gate not in sources or owner[0].scope != scope:
            continue
        entry = {
            "id": decision.id,
            "title": decision.title,
            "status": decision.status,
            "origin_run_id": decision.origin_run_id,
            "origin_finding_id": decision.origin_finding_id,
        }
        if decision.resolution is not None:
            entry["resolution"] = decision.rationale
            entry["resolution_effect"] = resolution_payload(decision.resolution)
            entry["resolution_source"] = decision.resolution_source
        if decision.recommendation:
            entry["recommendation"] = decision.recommendation
        ledger.append(entry)
    return ledger
