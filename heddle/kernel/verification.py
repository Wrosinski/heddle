"""Pure verification source-coverage and freshness decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from heddle.contracts.operations import Action, CommandAction, Verify
from heddle.kernel.project_config import KernelError
from heddle.kernel.source_manifest import (
    SourceEvidence,
    SourceSetKind,
    byte_sort_key,
    normalize_milestone_source_paths,
)
from heddle.kernel.state import Milestone, StateFile, VerificationFact

FreshnessStatus = Literal[
    "missing",
    "fresh",
    "failed",
    "unstable",
    "command-stale",
    "source-set-stale",
    "content-stale",
]
CoverageStatus = Literal["complete", "unresolved", "baseline-unavailable"]
SCOPE_COMMAND_KEYS: Mapping[str, str] = MappingProxyType(
    {
        "feature": "test_command",
        "smoke": "smoke_test",
        "acceptance": "acceptance_test",
        "live": "live_e2e_test",
    }
)
FINAL_SCOPES = frozenset({"smoke", "acceptance", "live"})


@dataclass(frozen=True)
class SourceDeclaration:
    kind: SourceSetKind
    paths: tuple[str, ...]


@dataclass(frozen=True)
class GitDiagnostics:
    head: str | None
    differs_from_head: bool | None


@dataclass(frozen=True)
class GateSourceInventory:
    tracked_paths: tuple[str, ...]
    untracked_nonignored_paths: tuple[str, ...]
    changed_since_baseline: tuple[str, ...] | None
    baseline_error: str | None
    baseline_probe: str | None
    leaf_error: str | None


@dataclass(frozen=True)
class CoverageReconciliation:
    status: CoverageStatus
    unresolved_paths: tuple[str, ...]
    action: str | None


@dataclass(frozen=True)
class VerificationFreshness:
    scope: str
    status: FreshnessStatus
    action: Action | None
    fact_index: int | None
    accepted_decision: str | None = None
    applicability: str = "required"
    explanation: Any | None = None
    detail_limit: str | None = None

    @property
    def authorizes(self) -> bool:
        return self.status == "fresh" or (
            self.scope == "smoke"
            and self.status == "failed"
            and self.accepted_decision is not None
        )


def milestone_for_scope(state: StateFile, scope: str) -> Milestone | None:
    """Return the milestone named by a milestone-like scope, if present."""
    if not scope.startswith("m"):
        return None
    return next(
        (milestone for milestone in state.milestones if milestone.id == scope),
        None,
    )


def resolve_source_declaration(state: StateFile, scope: str) -> SourceDeclaration:
    """Map one exact verification scope to its normalized raw declaration."""
    if scope.startswith("m"):
        milestone = milestone_for_scope(state, scope)
        if milestone is None:
            raise _source_error(scope, "does not name a milestone")
        paths = normalize_milestone_source_paths(milestone.owns, feature=state.feature)
        if not paths:
            raise _source_error(
                scope,
                "has empty milestone ownership; use "
                f"`heddle milestone edit {scope} --from-file -` with non-empty owns",
            )
        return SourceDeclaration("milestone-owns", paths)

    if scope == "feature":
        raise _source_error(scope, "is unsupported by the current milestone workflow")

    if scope in FINAL_SCOPES:
        return SourceDeclaration(
            "feature-owned-union", _feature_owned_union(state, scope)
        )
    raise _source_error(scope, "is not a known verification scope")


def verification_command_for_scope(state: StateFile, scope: str) -> str:
    """Resolve the one stored command belonging to an exact scope."""
    if scope.startswith("m"):
        milestone = milestone_for_scope(state, scope)
        if milestone is None:
            raise _source_error(scope, "does not name a milestone")
        command = milestone.verification.get("command", "")
        action = f"heddle milestone edit {scope} --from-file -"
    else:
        key = SCOPE_COMMAND_KEYS.get(scope)
        if key is None:
            raise _source_error(scope, "is not a known verification scope")
        command = state.commands.get(key, "")
        action = f"heddle commands set {key} --command <shell-line>"
    if not command.strip():
        raise KernelError(
            code="workspace-invalid",
            message=f"verification command for {scope!r} is empty",
            hint=f"declare it with `{action}` and retry",
        )
    return command


def _feature_owned_union(state: StateFile, scope: str) -> tuple[str, ...]:
    normalized = (
        path
        for milestone in state.milestones
        for path in normalize_milestone_source_paths(
            milestone.owns, feature=state.feature
        )
    )
    paths = tuple(sorted(set(normalized), key=byte_sort_key))
    if not paths:
        raise _source_error(
            scope,
            "has empty milestone ownership; declare non-empty owns with "
            "`heddle milestone edit <milestone-id> --from-file -`",
        )
    return paths


def _source_error(scope: str, problem: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"verification source for {scope!r} {problem}",
        hint="repair the verification source declaration and retry",
    )


def reconcile_source_coverage(
    declaration: SourceDeclaration,
    inventory: GateSourceInventory,
    excluded_roots: tuple[str, ...],
    excluded_paths: tuple[str, ...] = (),
) -> CoverageReconciliation:
    """Compare raw file/directory coverage with feature-baseline changes."""
    if inventory.changed_since_baseline is None:
        error = inventory.baseline_error or "baseline-unresolvable"
        probe = inventory.baseline_probe or "missing"
        return CoverageReconciliation(
            "baseline-unavailable",
            (),
            (
                f"baseline {error} for probe {probe}; restore the Git repository "
                "and feature baseline, then retry"
            ),
        )

    current_leaves = set(inventory.tracked_paths) | set(
        inventory.untracked_nonignored_paths
    )
    declared = tuple(declaration.paths)

    def excluded(path: str) -> bool:
        return path in excluded_paths or any(
            path == root or path.startswith(f"{root}/") for root in excluded_roots
        )

    def covered(path: str) -> bool:
        for owner in declared:
            if path == owner:
                return True
            # A declaration which is itself a current leaf is a file. Other
            # declarations are directory roots and continue to cover deleted
            # descendants during baseline reconciliation.
            if owner not in current_leaves and path.startswith(f"{owner}/"):
                return True
        return False

    unresolved = tuple(
        sorted(
            {
                path
                for path in inventory.changed_since_baseline
                if not excluded(path) and not covered(path)
            },
            key=byte_sort_key,
        )
    )
    if not unresolved:
        return CoverageReconciliation("complete", (), None)
    return CoverageReconciliation(
        "unresolved",
        unresolved,
        (
            "correct verification ownership for unresolved paths: "
            + ", ".join(unresolved)
        ),
    )


def assess_verification(
    facts: tuple[VerificationFact, ...],
    scope: str,
    current_command: str,
    before: SourceEvidence | None,
    after: SourceEvidence | None,
    current: SourceEvidence,
    *,
    check_process_result: bool = True,
) -> VerificationFreshness:
    """Return the single canonical freshness outcome for one scope."""
    index_and_fact = next(
        (
            (index, fact)
            for index, fact in reversed(tuple(enumerate(facts)))
            if fact.scope == scope
        ),
        None,
    )
    index = index_and_fact[0] if index_and_fact is not None else None
    fact = index_and_fact[1] if index_and_fact is not None else None
    return assess_verification_fact(
        fact,
        scope=scope,
        command=current_command,
        before=before,
        after=after,
        current=current,
        check_process_result=check_process_result,
        fact_index=index,
    )


def latest_verification_fact(
    facts: tuple[VerificationFact, ...], *, scope: str
) -> VerificationFact | None:
    return next((fact for fact in reversed(facts) if fact.scope == scope), None)


def assess_verification_fact(
    fact: VerificationFact | None,
    *,
    scope: str,
    command: str,
    before: SourceEvidence | None,
    after: SourceEvidence | None,
    current: SourceEvidence,
    check_process_result: bool = True,
    fact_index: int | None = None,
) -> VerificationFreshness:
    """Apply the one ordered freshness matrix to decoded evidence."""
    if fact is None:
        return _freshness(scope, "missing", fact_index)
    if before is None or after is None:
        raise ValueError("stored verification fact requires decoded evidence")
    if check_process_result and fact.exit_code != 0:
        return _freshness(scope, "failed", fact_index)
    if before.source_sha256 != after.source_sha256:
        return _freshness(scope, "unstable", fact_index)
    if fact.command != command:
        return _freshness(scope, "command-stale", fact_index)
    if before.definition != after.definition or after.definition != current.definition:
        return _freshness(scope, "source-set-stale", fact_index)
    if after.source_sha256 != current.source_sha256:
        return _freshness(scope, "content-stale", fact_index)
    return VerificationFreshness(scope, "fresh", None, fact_index)


def latest_verification_is_bound_to_declaration(state: StateFile, scope: str) -> bool:
    """Screen current facts before a runtime current-content observation.

    This is deliberately not a freshness verdict: only the runtime can observe
    current bytes. It prevents the pure snapshot/write models from treating a
    unavailable, failed, unstable, or command-mismatched attempt as provisionally
    usable while the runtime remains the transition authority.
    """
    fact = next(
        (fact for fact in reversed(state.verifications) if fact.scope == scope),
        None,
    )
    if fact is None:
        return False
    if (
        fact.exit_code != 0
        or fact.evidence.before.source_sha256 != fact.evidence.after.source_sha256
    ):
        return False
    try:
        resolve_source_declaration(state, scope)
        command = verification_command_for_scope(state, scope)
    except KernelError:
        return False
    return fact.command == command


def _freshness(
    scope: str, status: FreshnessStatus, fact_index: int | None
) -> VerificationFreshness:
    return VerificationFreshness(
        scope=scope,
        status=status,
        action=CommandAction(Verify(scope=scope)),
        fact_index=fact_index,
    )


def required_verification_scopes(
    state: StateFile, *, stage: str | None = None
) -> tuple[str, ...]:
    """Return the exact ordered verification rows for the reachable boundary."""
    stage = stage or state.stage
    if stage not in {"implement", "peer-review", "robustness", "complete"}:
        return ()
    milestone_ids = tuple(milestone.id for milestone in state.milestones)
    current = next(
        (
            milestone.id
            for milestone in state.milestones
            if milestone.status == "current"
        ),
        None,
    )
    all_done = all(milestone.status == "done" for milestone in state.milestones)
    if stage == "implement":
        if current is not None:
            return (current,)
        if not all_done:
            return ()
        return milestone_ids
    assert state.feature_policy is not None
    from heddle.kernel.feature_policy import effective_policy

    if (
        stage == "peer-review"
        and effective_policy(state.feature_policy).final_verification_stage
        == "robustness"
    ):
        return ()
    scopes = ["acceptance", "smoke"]
    if (
        stage in {"robustness", "complete"}
        and state.commands.get("live_e2e_test", "").strip()
    ):
        scopes.append("live")
    return tuple(scopes)
