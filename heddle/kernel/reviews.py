"""Pure review identity, ownership, convergence and completed-run arithmetic."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from heddle.contracts.review_assignments import ROLE_STAGES
from heddle.contracts.schemas import DECISION_RESOLUTION_KINDS, STAGES
from heddle.kernel.project_config import KernelError

if TYPE_CHECKING:
    from heddle.kernel.state import (
        DecisionFact,
        GateFact,
        GateRun,
        ReportFinding,
        StateFile,
    )

STAGE_GATES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "spec-review": frozenset({"spec-review"}),
        "plan-review": frozenset({"plan-review"}),
        "scaffold": frozenset({"review-test-scaffolding"}),
        "implement": frozenset({"milestone-review"}),
        "peer-review": frozenset({"peer-review-sequential"}),
        "robustness": frozenset({"robustness-analysis"}),
    }
)
_GATE_STAGE = {gate: stage for stage, gates in STAGE_GATES.items() for gate in gates}
_GATE_STAGE.update(ROLE_STAGES)


def finding_authority(state: StateFile, gate: str, *, run_id: str | None = None) -> str:
    del state, run_id
    return gate


def run_owner(state: StateFile, run_id: str | None) -> tuple[GateFact, GateRun] | None:
    return next(
        (
            (fact, run)
            for fact in state.gates
            for run in fact.runs
            if run.run_id == run_id
        ),
        None,
    )


def latest_non_error_run(state: StateFile, gate: str, scope: str) -> GateRun | None:
    return next(
        (
            run
            for fact in state.gates
            if fact.gate == gate and fact.scope == scope
            for run in reversed(fact.runs)
            if run.verdict.get("status") != "error"
        ),
        None,
    )


def completed_review_count(
    state: StateFile, gate: str, scope: str, cli: str | None = None
) -> int:
    return sum(
        run.verdict.get("status") != "error" and (cli is None or run.cli == cli)
        for fact in state.gates
        if fact.gate == gate and fact.scope == scope
        for run in fact.runs
    )


def finding_requires_owner(
    state: StateFile,
    gate: str,
    scope: str,
    finding: ReportFinding,
    *,
    run_id: str | None = None,
) -> bool:
    del state, gate, scope, run_id
    return finding.severity != "minor"


def run_actionability_coherent(state: StateFile, fact: GateFact, run: GateRun) -> bool:
    """A completed run retains all canonical REPORT metadata and exact owners.

    Missing decisions are readable evidence, but cannot authorize progression.
    Parsing rejects surplus or malformed owners before this predicate is used.
    """
    if run.verdict.get("status") == "error":
        return False
    if not _aggregate_coherent(run.findings):
        return False
    if len(run.report_findings) != run.findings["by_classification"].get("report", 0):
        return False
    expected = {
        finding.finding_id
        for finding in run.report_findings
        if finding_requires_owner(
            state, fact.gate, fact.scope, finding, run_id=run.run_id
        )
    }
    actual = [
        decision.origin_finding_id
        for decision in state.decisions
        if decision.kind == "finding" and decision.origin_run_id == run.run_id
    ]
    return len(actual) == len(expected) and set(actual) == expected


def actionability_coherent(state: StateFile, gate: str, scope: str) -> bool:
    run = latest_non_error_run(state, gate, scope)
    if run is None:
        return False
    owner = run_owner(state, run.run_id)
    return owner is not None and run_actionability_coherent(state, *owner)


def _aggregate_coherent(findings: Mapping[str, Any]) -> bool:
    severity = findings.get("by_severity")
    classification = findings.get("by_classification")
    total = findings.get("total")
    contradictions = findings.get("contradictions")
    return (
        isinstance(severity, Mapping)
        and isinstance(classification, Mapping)
        and set(severity) <= {"critical", "important", "minor"}
        and set(classification) <= {"implement", "report", "ignore", "unknown"}
        and all(
            type(n) is int and n >= 0
            for n in (*severity.values(), *classification.values())
        )
        and type(total) is int
        and total >= 0
        and type(contradictions) is int
        and contradictions >= 0
        and sum(severity.values()) == total
        and sum(classification.values()) == total
        and (contradictions == 0 or classification.get("report", 0) > 0)
    )


def gate_converged(state: StateFile, gate: str, scope: str) -> bool:
    from heddle.kernel.review_assignments import (
        assignment_for,
        authoritative_sources,
        closure_facts,
    )
    from heddle.kernel.review_closure import assess_review_closure

    assignment = assignment_for(state, gate, scope)
    source_ids = {
        source.run_id
        for source in authoritative_sources(state)
        if source.assignment_id == assignment.id
    }
    basis = next(
        (
            disposition.evidence_basis
            for disposition in reversed(state.review_assignments.dispositions)
            if disposition.run_id in source_ids
            and disposition.evidence_basis is not None
        ),
        "",
    )
    return assess_review_closure(
        closure_facts(state, assignment, current_basis=basis)
    ).closed


def retained_unconverged_review(
    state: StateFile, scopes: tuple[str, ...] | None = None
) -> tuple[str, str] | None:
    """Retain deciding obligations even when future gate selection is narrowed."""
    stage_gates = STAGE_GATES.get(state.stage, frozenset())
    return next(
        (
            (fact.gate, fact.scope)
            for fact in state.gates
            if fact.gate in stage_gates
            and (scopes is None or fact.scope in scopes)
            and not gate_converged(state, fact.gate, fact.scope)
        ),
        None,
    )


def review_refresh_required(
    state: StateFile,
    gate: str,
    scope: str,
    run_id: str | None,
    applicability: str,
) -> bool:
    del state, gate, scope, run_id
    return applicability != "current"


def validate_relationships(state: StateFile, *, source: Path) -> None:
    """Validate the current graph without inventing missing decision owners."""
    owners: set[tuple[str, str]] = set()
    runs: dict[str, tuple[GateFact, GateRun]] = {}
    for fact in state.gates:
        if (fact.gate, fact.scope) in owners:
            raise _invalid(
                f"duplicate gate/scope owner {(fact.gate, fact.scope)!r}", source
            )
        owners.add((fact.gate, fact.scope))
        for run in fact.runs:
            if run.run_id in runs:
                raise _invalid(f"duplicate run_id {run.run_id!r}", source)
            runs[run.run_id] = (fact, run)
            identifiers = [finding.finding_id for finding in run.report_findings]
            if len(set(identifiers)) != len(identifiers):
                raise _invalid(f"run {run.run_id}: duplicate finding_id", source)
            if run.verdict.get("status") == "error" and identifiers:
                raise _invalid(
                    f"error run {run.run_id} has trusted REPORT findings", source
                )
    decisions: dict[str, DecisionFact] = {}
    finding_owners: set[tuple[str | None, str | None]] = set()
    for decision in state.decisions:
        if not decision.id.strip() or decision.id in decisions:
            raise _invalid(f"empty or duplicate decision id {decision.id!r}", source)
        decisions[decision.id] = decision
        _validate_decision(state, decision, runs, source)
        if decision.kind == "finding":
            origin = (decision.origin_run_id, decision.origin_finding_id)
            if origin in finding_owners:
                raise _invalid(f"duplicate finding owner {origin!r}", source)
            finding_owners.add(origin)


def _validate_decision(
    state: StateFile,
    decision: DecisionFact,
    runs: Mapping[str, tuple[GateFact, GateRun]],
    source: Path,
) -> None:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise _invalid(f"decision {decision.id}: {message}", source)

    kind = decision.kind
    classes: dict[str, set[int | None]] = {
        "finding": {None, 4},
        "question": {1, 2, 5, 7, 8},
        "stage-cap": {3},
        "session-cap": {6, 7},
        "smoke-disposition": {2},
        "policy": {None},
    }
    require(
        decision.escalation_class in classes[kind], f"class is ineligible for {kind}"
    )
    if kind == "question" and decision.escalation_class == 7:
        require(
            decision.source == "driver",
            "verification retry question requires driver source",
        )
    origin_required = kind == "finding"
    require(
        bool(decision.origin_run_id) == origin_required,
        "invalid origin_run_id presence",
    )
    require(
        bool(decision.origin_finding_id) == (kind == "finding"),
        "invalid origin_finding_id presence",
    )
    require(
        (decision.target_stage is not None) == (kind in {"stage-cap", "session-cap"}),
        "invalid target_stage presence",
    )
    if decision.target_stage is not None:
        require(decision.target_stage in STAGES, "unknown target_stage")
    blockers = {
        "stage-cap": {"gate-cap-reached"},
        "session-cap": {"no-progress", "attempt-cap"},
    }
    require(
        decision.target_blocker in blockers.get(kind, {None}), "invalid target_blocker"
    )
    require(
        (decision.smoke_disposition is not None) == (kind == "smoke-disposition"),
        "invalid smoke_disposition presence",
    )
    if kind == "session-cap":
        require(
            decision.escalation_class
            == (6 if decision.target_blocker == "no-progress" else 7),
            "class does not match session blocker",
        )
    owner = runs.get(decision.origin_run_id or "")
    if origin_required:
        require(owner is not None, "origin_run_id does not name a recorded run")
        assert owner is not None
        fact, run = owner
        require(
            run.verdict.get("status") != "error", "origin_run_id names an error run"
        )
        finding = next(
            (
                f
                for f in run.report_findings
                if f.finding_id == decision.origin_finding_id
            ),
            None,
        )
        require(
            finding is not None,
            "origin_finding_id does not name a REPORT in origin run",
        )
        assert finding is not None
        require(
            finding_requires_owner(
                state, fact.gate, fact.scope, finding, run_id=run.run_id
            ),
            "finding belongs to a deferred or exempt result",
        )
    resolution = decision.resolution
    if decision.status == "pending":
        require(kind != "policy", "policy must be recorded resolved")
        require(
            all(
                value is None
                for value in (
                    resolution,
                    decision.rationale,
                    decision.resolution_source,
                    decision.resolved_at,
                )
            ),
            "pending resolution fields must be null",
        )
        require(
            decision.supersedes is None,
            "pending decision cannot supersede a resolution",
        )
        return
    require(decision.status == "resolved", "status must be pending or resolved")
    require(resolution is not None, "resolved decision lacks resolution")
    assert resolution is not None
    require(
        resolution.kind in DECISION_RESOLUTION_KINDS[kind], "ineligible resolution kind"
    )
    require(
        bool(decision.rationale and decision.rationale.strip()),
        "resolved rationale must be nonempty",
    )
    require(
        bool(decision.resolved_at and decision.resolved_at.strip()),
        "resolved_at must be nonempty",
    )
    require(
        bool(decision.routes_to) and all(route.strip() for route in decision.routes_to),
        "resolved routes_to must retain a permanent record",
    )
    require(
        decision.resolution_source == ("policy" if kind == "policy" else "user"),
        "ineligible resolution_source",
    )


def gate_stage(gate: str) -> str | None:
    return _GATE_STAGE.get(gate)


def _invalid(message: str, source: Path | None = None) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"{source}: {message}" if source else message,
        hint="repair the named current-state reference; preserve incompatible "
        "evidence and start a fresh workspace when needed",
    )
