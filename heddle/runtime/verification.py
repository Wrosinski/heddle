"""Runtime composition for verification source declarations and observations."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from heddle.contracts.decisions import AcceptDegradedSmoke, AcceptPriorWitness
from heddle.contracts.operations import (
    CommandAction,
    ManualAction,
    Verify,
    action_command,
)
from heddle.contracts.result import Diagnostic, Severity
from heddle.io import git
from heddle.io.source import capture_path, observe_source, resolve_source_definition
from heddle.kernel.managed_regions import authored_plan_observation
from heddle.kernel.project_config import (
    KernelError,
    ProjectConfig,
    find_project_root,
    load_project_config,
)
from heddle.kernel.readiness import EvidenceExplanation
from heddle.kernel.review_equivalence import rule_id_for
from heddle.kernel.smoke_disposition import (
    validate_smoke_proposal,
    verification_fact_identity,
)
from heddle.kernel.source_manifest import (
    EvidenceReference,
    ObservedPath,
    SourceChange,
    SourceDefinition,
    SourceEvidence,
    byte_sort_key,
    changed_paths,
    compare_source_evidence,
    decode_evidence_reference,
    decode_source_evidence,
    encode_source_evidence,
    evidence_reference,
    normalize_milestone_source_paths,
    normalize_paths,
    normalize_source_paths,
    safe_relative_parts,
    stamp_equivalence,
)
from heddle.kernel.state import SourceBaseline, StateFile
from heddle.kernel.test_bindings import (
    inspect_python_test_source,
    parse_primary_test_target,
    resolve_primary_test_binding,
    selector_rebind_pairs,
)
from heddle.kernel.verification import (
    SCOPE_COMMAND_KEYS,
    CoverageReconciliation,
    GateSourceInventory,
    GitDiagnostics,
    SourceDeclaration,
    VerificationFreshness,
    assess_verification,
    assess_verification_fact,
    latest_verification_fact,
    reconcile_source_coverage,
    required_verification_scopes,
    resolve_source_declaration,
    verification_command_for_scope,
)
from heddle.kernel.witness_waiver import (
    IMPLEMENT_EXIT_STAGE,
    WAIVABLE_STATUSES,
    WAIVER_EVIDENCE_KEY,
    acceptable_prior_witness,
    validate_witness_waiver_proposal,
    waivable_witness_rerun,
    waiver_source_evidence,
)


def observe_current_source(
    root: Path,
    declaration: SourceDeclaration,
    *,
    with_diagnostics: bool = True,
    scope: str | None = None,
    source_observations: dict[str, ObservedPath] | None = None,
) -> tuple[SourceDefinition, SourceEvidence, GitDiagnostics]:
    """Resolve current eligible leaves, content identity, and Git diagnostics."""
    try:
        normalize = (
            normalize_source_paths
            if declaration.kind == "feature-inputs"
            else normalize_paths
        )
        declaration = SourceDeclaration(declaration.kind, normalize(declaration.paths))
        inventory = inventory_source_paths(root, declaration)
        leaves = tuple(
            sorted(
                set(inventory.tracked_paths)
                | set(inventory.untracked_nonignored_paths),
                key=byte_sort_key,
            )
        )
        definition = resolve_source_definition(
            root,
            declaration.kind,
            declaration.paths,
            eligible_paths=leaves,
            inventory_error=inventory.leaf_error,
        )
        manifest = observe_source(root, definition, observations=source_observations)
        diagnostics = (
            observe_git_diagnostics(root, declaration.paths)
            if with_diagnostics
            else GitDiagnostics(None, None)
        )
        return definition, manifest, diagnostics
    except KernelError as error:
        raise _source_error(
            scope or declaration.kind, error.message, hint=error.hint
        ) from error


def proven_selector_rebinds(
    root: Path, current: str, supplied: str
) -> frozenset[tuple[str, str]]:
    """Prove each re-pointed test target renames a selector its file lost."""
    proven: set[tuple[str, str]] = set()
    for old, new in selector_rebind_pairs(current, supplied) or ():
        old_binding = parse_primary_test_target("", old)
        new_binding = parse_primary_test_target("", new)
        try:
            captured = capture_path(root, old_binding.path)
        except KernelError:
            continue
        if captured.kind != "file":
            continue
        inspection = inspect_python_test_source(captured.content)
        if inspection.inventory is None or "[" in old_binding.selector:
            continue
        old_resolution = resolve_primary_test_binding(
            old_binding, inspection, policy="repository"
        )
        new_resolution = resolve_primary_test_binding(
            new_binding, inspection, policy="repository"
        )
        if old_resolution.kind is None and new_resolution.kind is not None:
            proven.add((old, new))
    return frozenset(proven)


def assess_current_verification(
    root: Path, state: StateFile, scope: str
) -> VerificationFreshness:
    """Resolve and observe one scope before applying the pure matrix."""
    return assess_current_verifications(root, state, (scope,))[0]


def assess_recorded_verification(
    root: Path,
    state: StateFile,
    scope: str,
    fact_index: int,
    *,
    source_observations: dict[str, ObservedPath] | None = None,
) -> VerificationFreshness:
    """Assess one exact append-only fact against the current source.

    Command handlers use this after publication so a later concurrent attempt
    cannot substitute its outcome for the attempt the handler actually ran.
    """
    if fact_index < 0 or fact_index >= len(state.verifications):
        raise _source_error(
            scope,
            f"recorded verification fact index is unavailable: {fact_index}",
            hint="restore the append-only verification history and retry",
        )
    fact = state.verifications[fact_index]
    if fact.scope != scope:
        raise _source_error(
            scope,
            f"recorded verification fact {fact_index} belongs to {fact.scope}",
            hint="restore the append-only verification history and retry",
        )
    command = verification_command_for_scope(state, scope)
    declaration = resolve_source_declaration(state, scope)
    _definition, current, _git = observe_current_source(
        root,
        declaration,
        with_diagnostics=False,
        scope=scope,
        source_observations=source_observations,
    )
    workspace = root / load_project_config(root).layout.plans / state.feature
    before = read_source_evidence(workspace, fact.evidence.before)
    after = read_source_evidence(workspace, fact.evidence.after)
    return assess_verification_fact(
        fact,
        scope=scope,
        command=command,
        before=before,
        after=after,
        current=current,
        fact_index=fact_index,
    )


def assess_current_verifications(
    root: Path,
    state: StateFile,
    scopes: tuple[str, ...],
    *,
    source_observations: dict[str, ObservedPath] | None = None,
) -> tuple[VerificationFreshness, ...]:
    """Assess scopes while observing each shared declaration only once."""
    observations: dict[
        SourceDeclaration, tuple[SourceDefinition, SourceEvidence, GitDiagnostics]
    ] = {}
    statuses: list[VerificationFreshness] = []
    for scope in scopes:
        try:
            command = verification_command_for_scope(state, scope)
        except KernelError as error:
            # An unconfigured current command is pending work, not unreadable state.
            # Keep invalid scopes and all historical decoding refusals loud.
            empty_milestone_scope = scope.startswith("m") and not state.milestones
            if state.feature_policy is None or (
                scope not in SCOPE_COMMAND_KEYS and not empty_milestone_scope
            ):
                raise
            statuses.append(
                VerificationFreshness(
                    scope,
                    "missing",
                    ManualAction(error.hint),
                    None,
                )
            )
            continue
        declaration = resolve_source_declaration(state, scope)
        observation = observations.get(declaration)
        if observation is None:
            observation = observe_current_source(
                root,
                declaration,
                with_diagnostics=False,
                scope=scope,
                source_observations=source_observations,
            )
            observations[declaration] = observation
        definition, manifest, _git = observation
        fact = latest_verification_fact(state.verifications, scope=scope)
        before = after = None
        if fact is not None:
            workspace = root / load_project_config(root).layout.plans / state.feature
            before = read_source_evidence(workspace, fact.evidence.before)
            after = read_source_evidence(workspace, fact.evidence.after)
        status = assess_verification(
            state.verifications,
            scope,
            command,
            before,
            after,
            manifest,
        )
        if status.status in {"content-stale", "source-set-stale"}:
            assert after is not None
            status = _stale_explanation(root, scope, status, after, manifest)
        statuses.append(status)
    for index, status in enumerate(statuses):
        if status.scope != "smoke" or status.status != "failed":
            continue
        for decision in reversed(state.decisions):
            disposition = decision.smoke_disposition
            if (
                disposition is None
                or decision.status != "resolved"
                or decision.resolution_source != "user"
                or not isinstance(decision.resolution, AcceptDegradedSmoke)
                or decision.escalation_class != 2
                or not (decision.resolved_at or "").strip()
                or disposition["stage"] != state.stage
            ):
                continue
            try:
                current = prepare_smoke_disposition(
                    root,
                    state,
                    {
                        key: disposition[key]
                        for key in (
                            "stage",
                            "attribution",
                            "baseline_evidence",
                            "outside_paths",
                            "owned_scopes",
                        )
                    },
                    source_observations=source_observations,
                )
            except KernelError:
                continue
            if current == dict(disposition):
                statuses[index] = replace(
                    status, action=None, accepted_decision=decision.id
                )
                break
    for index, status in enumerate(statuses):
        if not (
            waivable_witness_rerun(state.stage, status)
            or acceptable_prior_witness(state.stage, status)
        ):
            continue
        waiver = _current_witness_waiver(root, state, status.scope, source_observations)
        if waiver is not None:
            statuses[index] = replace(status, action=None, waived_by=waiver)
    return tuple(
        replace(
            status,
            action=CommandAction(
                Verify(
                    status.scope,
                    feature=state.feature,
                    expect_revision=state.revision,
                )
            ),
        )
        if status.action is not None
        else status
        for status in statuses
    )


def _stale_explanation(
    root: Path,
    scope: str,
    status: VerificationFreshness,
    recorded: SourceEvidence,
    current: SourceEvidence,
) -> VerificationFreshness:
    """Name each change from the fact's after-run manifest to current source."""
    changes = compare_source_evidence(
        recorded, stamp_changed_equivalence(root, current, recorded)
    ).changes
    dependencies = changed_paths(changes)
    source_set = status.status == "source-set-stale"
    detail_limit = (
        "only the verification source-set identity is retained"
        if source_set and not dependencies
        else _equivalence_unavailable(recorded, changes)
    )
    formatting_only = bool(changes) and all(
        change.kind == "formatting-only" for change in changes
    )
    return replace(
        status,
        detail_limit=(
            detail_limit if source_set or detail_limit else status.detail_limit
        ),
        explanation=EvidenceExplanation(
            subject=f"verification:{scope}",
            cause=(
                "declared verification source set changed"
                if source_set
                else "formatting-only change to relevant content"
                if formatting_only
                else "relevant content changed"
            ),
            changed_dependencies=dependencies,
            failed_reference=None,
            remedy=status.action,
            detail_limit=detail_limit,
            changes=changes,
        ),
    )


def _equivalence_unavailable(
    recorded: SourceEvidence, changes: tuple[SourceChange, ...]
) -> str | None:
    """Name content changes the run's manifest cannot tell from formatting.

    A run recorded before review equivalence, or under another rule, carries no
    identity to compare, so its rule-bearing changes stay content.
    """
    prior = {row.path: row for row in recorded.observations}
    paths = []
    for change in changes:
        if change.kind != "content" or change.path is None:
            continue
        rule = rule_id_for(change.path)
        identity = prior[change.path].equivalence
        if rule is not None and (identity is None or identity.rule != rule):
            paths.append(change.path)
    if not paths:
        return None
    return (
        "equivalence unavailable: the run recorded no review-equivalence "
        "identity under the current rule for " + ", ".join(paths) + ", so a "
        "formatting-only change there cannot be told from a content change"
    )


def prepare_smoke_disposition(
    root: Path,
    state: StateFile,
    value: object,
    *,
    source_observations: dict[str, ObservedPath] | None = None,
) -> dict:
    """Bind a human attribution to current, independently observed evidence."""
    proposal = validate_smoke_proposal(state, value)
    ownership = resolve_source_declaration(state, "smoke").paths
    bindings = {}
    for scope in ("smoke", "acceptance", *proposal["owned_scopes"]):
        declaration = resolve_source_declaration(state, scope)
        _definition, manifest, _git = observe_current_source(
            root,
            declaration,
            with_diagnostics=False,
            scope=scope,
            source_observations=source_observations,
        )
        fact = latest_verification_fact(state.verifications, scope=scope)
        before = after = None
        if fact is not None:
            workspace = root / load_project_config(root).layout.plans / state.feature
            before = read_source_evidence(workspace, fact.evidence.before)
            after = read_source_evidence(workspace, fact.evidence.after)
        status = assess_verification(
            state.verifications,
            scope,
            verification_command_for_scope(state, scope),
            before,
            after,
            manifest,
            check_process_result=scope != "smoke",
        )
        if status.status != "fresh" or status.fact_index is None:
            raise _source_error(
                scope,
                "cannot bind a disposition to stale, unstable, unbound "
                "or failing supporting evidence",
            )
        fact = state.verifications[status.fact_index]
        if scope == "smoke" and fact.exit_code == 0:
            raise _source_error(
                scope, "a degraded disposition requires a failed process"
            )
        bindings[scope] = verification_fact_identity(fact)
    return {**proposal, "ownership": list(ownership), "bindings": bindings}


@dataclass(frozen=True)
class PreparedWitnessWaiver:
    """A waiver's exact binding and the current raw manifest of its source."""

    binding: dict[str, Any]
    manifest: SourceEvidence


def prepare_witness_waiver(
    root: Path,
    state: StateFile,
    value: object,
    *,
    source_observations: dict[str, ObservedPath] | None = None,
) -> PreparedWitnessWaiver:
    """Bind a waived rerun to the latest passing fact and current source.

    At implement exit the earlier witness stands in only when every change
    since its run is formatting-only.
    """
    proposal = validate_witness_waiver_proposal(state, value)
    scope = proposal["scope"]
    declaration = resolve_source_declaration(state, scope)
    _definition, manifest, _git = observe_current_source(
        root,
        declaration,
        with_diagnostics=False,
        scope=scope,
        source_observations=source_observations,
    )
    fact = latest_verification_fact(state.verifications, scope=scope)
    before = after = None
    if fact is not None:
        workspace = root / load_project_config(root).layout.plans / state.feature
        before = read_source_evidence(workspace, fact.evidence.before)
        after = read_source_evidence(workspace, fact.evidence.after)
    status = assess_verification(
        state.verifications,
        scope,
        verification_command_for_scope(state, scope),
        before,
        after,
        manifest,
    )
    if status.status == "fresh":
        raise _source_error(scope, "is current; no rerun waiver is needed")
    if status.status not in WAIVABLE_STATUSES or status.fact_index is None:
        raise _source_error(
            scope,
            f"cannot waive a rerun of {status.status} evidence; only a stable "
            "passing run with the current command can stand in for a rerun",
        )
    fact_index = status.fact_index
    if state.stage == IMPLEMENT_EXIT_STAGE:
        assert after is not None
        status = _stale_explanation(root, scope, status, after, manifest)
        if not acceptable_prior_witness(state.stage, status):
            changes = status.explanation.changes if status.explanation else ()
            named = ", ".join(
                f"{change.kind} {change.path}" if change.path else change.kind
                for change in changes
                if change.kind != "formatting-only"
            )
            raise _source_error(
                scope,
                "cannot accept the earlier witness at implement exit: only "
                f"formatting-only changes qualify, and its source has {named}",
                hint=f"rerun the lane with `heddle verify --scope {scope}`",
            )
    return PreparedWitnessWaiver(
        {
            **proposal,
            "fact": verification_fact_identity(state.verifications[fact_index]),
            "ownership": list(declaration.paths),
            "source_sha256": manifest.source_sha256,
        },
        manifest,
    )


def bind_witness_waiver(
    root: Path, prepared: PreparedWitnessWaiver
) -> tuple[dict[str, Any], SourceEvidence]:
    """The binding a new waiver records and the stamped manifest it names.

    Naming the manifest lets the waiver stay current across a review-equivalent
    change; the caller publishes the manifest before recording the decision.
    """
    manifest = stamp_recorded_equivalence(root, prepared.manifest)
    reference = asdict(evidence_reference(manifest))
    return {**prepared.binding, WAIVER_EVIDENCE_KEY: reference}, manifest


def witness_waiver_current(
    root: Path,
    state: StateFile,
    waiver: Mapping[str, Any],
    prepared: PreparedWitnessWaiver,
    *,
    subject: str,
) -> bool:
    """Whether a recorded binding still holds for the prepared evidence.

    Lane, fact and ownership compare exactly. The source holds when it is
    raw-equal, or review-equivalent to the manifest the binding names; a
    binding recorded without one compares exactly.
    """
    current = prepared.binding
    if any(waiver[key] != current[key] for key in ("scope", "fact", "ownership")):
        return False
    if waiver["source_sha256"] == current["source_sha256"]:
        return True
    reference = waiver_source_evidence(waiver)
    if reference is None:
        return False
    workspace = root / load_project_config(root).layout.plans / state.feature
    recorded = read_named_source_evidence(
        workspace,
        reference.artifact,
        reference.artifact_sha256,
        subject=subject,
        source_sha256=reference.source_sha256,
    )
    assert recorded is not None
    return compare_source_evidence(
        recorded, stamp_changed_equivalence(root, prepared.manifest, recorded)
    ).equivalent


def _current_witness_waiver(
    root: Path,
    state: StateFile,
    scope: str,
    source_observations: dict[str, ObservedPath] | None,
) -> str | None:
    """Return the latest user waiver still bound to this lane's evidence."""
    for decision in reversed(state.decisions):
        waiver = decision.witness_waiver
        if (
            waiver is None
            or waiver["scope"] != scope
            or decision.status != "resolved"
            or decision.resolution_source != "user"
            or not isinstance(decision.resolution, AcceptPriorWitness)
            or decision.escalation_class != 5
            or not (decision.resolved_at or "").strip()
        ):
            continue
        try:
            current = prepare_witness_waiver(
                root,
                state,
                {"scope": scope},
                source_observations=source_observations,
            )
        except KernelError:
            return None
        return (
            decision.id
            if witness_waiver_current(
                root, state, waiver, current, subject=f"witness waiver {decision.id}"
            )
            else None
        )
    return None


def reconcile_current_source(
    root: Path,
    state: StateFile,
    scope: str,
    *,
    baseline_probe: str,
    runtime_owned_roots: tuple[str, ...],
    plan_path: str,
    excluded_paths: tuple[str, ...] = (),
    workspace: str | None = None,
) -> CoverageReconciliation:
    """Fail closed unless the final declaration covers feature-baseline changes."""
    declaration = resolve_source_declaration(state, scope)
    reconciliation = _observe_source_coverage(
        root,
        state,
        declaration,
        baseline_probe=baseline_probe,
        plan_path=plan_path,
        runtime_owned_roots=runtime_owned_roots,
        excluded_paths=excluded_paths,
    )
    if reconciliation.status != "complete":
        action = reconciliation.action or "correct verification ownership and retry"
        normalized_workspace = workspace.rstrip("/") if workspace else None
        if normalized_workspace is not None and any(
            path == normalized_workspace or path.startswith(f"{normalized_workspace}/")
            for path in reconciliation.unresolved_paths
        ):
            reviews = f"{normalized_workspace}/reviews/"
            action += (
                f". An optional lead-authored review record that supports native "
                f"review closure belongs under {reviews} with its final location "
                "and bytes settled before evidence binding; a product assessment "
                "or product source remains owned outside the protected workflow "
                "workspace. Resolve every listed path by its actual role, preserve "
                "already bound evidence, and do not add the workspace to milestone "
                "ownership"
            )
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"verification source for {scope!r} has incomplete final "
                f"coverage: {reconciliation.status}"
            ),
            hint=action,
        )
    return reconciliation


def _observe_source_coverage(
    root: Path,
    state: StateFile,
    declaration: SourceDeclaration,
    *,
    baseline_probe: str,
    plan_path: str,
    runtime_owned_roots: tuple[str, ...],
    excluded_paths: tuple[str, ...],
) -> CoverageReconciliation:
    inventory = inventory_source_paths(
        root,
        declaration,
        baseline_probe,
        source_baseline=state.source_baseline,
    )
    from heddle.runtime.source_attribution import qualified_attribution_paths

    attributed = qualified_attribution_paths(
        root, state, plan_path=plan_path, owned=declaration.paths
    )
    return reconcile_source_coverage(
        declaration,
        inventory,
        runtime_owned_roots,
        excluded_paths=(*excluded_paths, *attributed),
    )


def coverage_diagnostics(
    config: ProjectConfig, state: StateFile
) -> tuple[Diagnostic, ...]:
    """Observe early coverage without granting proof or requiring ownership yet."""
    paths = tuple(
        sorted(
            {
                path
                for milestone in state.milestones
                for path in normalize_milestone_source_paths(
                    milestone.owns, feature=state.feature
                )
            },
            key=byte_sort_key,
        )
    )
    controls = workflow_control_paths(config, state)
    observation = _observe_source_coverage(
        config.root,
        state,
        SourceDeclaration("feature-owned-union", paths),
        baseline_probe=f"{config.layout.plans}/{state.feature}/state.yaml",
        plan_path=f"{config.layout.plans}/{state.feature}/plan.md",
        runtime_owned_roots=controls.roots,
        excluded_paths=controls.exact,
    )
    if observation.status == "complete":
        return ()
    return (
        Diagnostic(
            Severity.ADVISORY,
            f"source-coverage-{observation.status}",
            (
                f"Source coverage is {observation.status}: {observation.action}. "
                "Declare product paths in milestone owns or attribute genuinely "
                "outside-feature work; "
                "coverage remains mandatory at completion."
            ),
        ),
    )


def verification_statuses(
    root: Path, state: StateFile
) -> tuple[VerificationFreshness, ...]:
    verify_named_source_evidence(root, state)
    return assess_current_verifications(
        root, state, required_verification_scopes(state)
    )


def verify_named_source_evidence(root: Path, state: StateFile) -> None:
    """Every basis manifest a disposition row names is present and intact.

    A verification fact's own manifests are read when its freshness is
    assessed. Each disposition manifest is checked once per call, whatever the
    number of rows naming it, and re-read only when its file changed since this
    process last found it intact; a missing or altered one fails naming the
    first row that names it.
    """
    named = {}
    for row in state.review_assignments.dispositions:
        reference = row.basis_evidence
        if reference is not None and reference.artifact not in named:
            named[reference.artifact] = (
                reference.artifact_sha256,
                f"disposition {row.run_id}#{row.finding_id}",
            )
    if not named:
        return
    workspace = root / load_project_config(root).layout.plans / state.feature
    for artifact, (artifact_sha256, subject) in named.items():
        key = (str(workspace / artifact), artifact_sha256)
        try:
            metadata = (workspace / artifact).lstat()
            stamp = (metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        except OSError:
            stamp = None
        if stamp is not None and _INTACT_EVIDENCE.get(key) == stamp:
            continue
        read_named_source_evidence(
            workspace, artifact, artifact_sha256, subject=subject, decode=False
        )
        if stamp is not None:
            if len(_INTACT_EVIDENCE) >= _INTACT_EVIDENCE_LIMIT:
                _INTACT_EVIDENCE.clear()
            _INTACT_EVIDENCE[key] = stamp


# Named evidence this process found intact, by path and digest, with the file
# identity it was read at; a changed file is read again.
_INTACT_EVIDENCE: dict[tuple[str, str], tuple[int, int, int]] = {}
_INTACT_EVIDENCE_LIMIT = 4096


def read_named_source_evidence(
    workspace: Path,
    artifact: str,
    artifact_sha256: str,
    *,
    subject: str,
    source_sha256: str | None = None,
    decode: bool = True,
) -> SourceEvidence | None:
    """Read a source-evidence artifact a record names, checked against its digest.

    A record's named manifest is required evidence: a missing, unreadable or
    altered one is an integrity failure naming the record and the artifact,
    never a fallback to raw comparison.
    """
    try:
        content = _read_regular_bytes(
            _evidence_path(workspace, artifact), label="source evidence"
        )
    except FileNotFoundError as error:
        raise _named_evidence_error(subject, artifact, "is missing") from error
    except (KernelError, OSError) as error:
        raise _named_evidence_error(subject, artifact, "is unreadable") from error
    if hashlib.sha256(content).hexdigest() != artifact_sha256:
        raise _named_evidence_error(subject, artifact, "has altered bytes")
    if not decode:
        return None
    try:
        evidence = decode_source_evidence(content)
    except (KernelError, ValueError) as error:
        raise _named_evidence_error(subject, artifact, "is malformed") from error
    if source_sha256 is not None and evidence.source_sha256 != source_sha256:
        raise _named_evidence_error(
            subject, artifact, "contradicts the recorded source identity"
        )
    return evidence


def _named_evidence_error(subject: str, artifact: str, problem: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"{subject} names source evidence {artifact}, which {problem}",
        hint=(
            "restore the published artifact from version control or a backup; "
            "recorded source evidence is never recomputed"
        ),
    )


# Freshness statuses whose recorded evidence a later change made stale.
STALE_FRESHNESS = frozenset({"content-stale", "command-stale", "source-set-stale"})


def verification_status_payload(
    statuses: tuple[VerificationFreshness, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    causes = {
        "missing": "evidence has not been produced",
        "content-stale": "relevant content changed",
        "command-stale": "verification command changed",
        "source-set-stale": "declared verification source set changed",
        "failed": "recorded command failed",
        "unstable": "relevant content changed during verification",
    }
    for status in statuses:
        evidence_state = (
            status.applicability
            if status.applicability in {"not-applicable", "scope-not-defined"}
            else "current"
            if status.status == "fresh"
            else "stale"
            if status.status in STALE_FRESHNESS
            else "missing"
            if status.status == "missing"
            else "invalid"
        )
        explanation = status.explanation
        row: dict[str, Any] = {
            "scope": status.scope,
            "freshness": status.status,
            "evidence_state": evidence_state,
            "applicability": status.applicability,
            "cause": (
                explanation.cause
                if explanation is not None
                else None
                if status.applicability == "not-applicable"
                else "verification scope is not declared"
                if status.applicability == "scope-not-defined"
                else causes.get(status.status)
            ),
            "action": action_command(status.action)
            if status.action is not None
            else None,
        }
        if status.detail_limit is not None:
            row["detail_limit"] = status.detail_limit
        if explanation is not None:
            row.update(
                affected_original_work=explanation.subject,
                changed_dependencies=list(explanation.changed_dependencies),
                failed_reference=explanation.failed_reference,
                detail_limit=explanation.detail_limit,
                changes=[asdict(change) for change in explanation.changes],
            )
        if status.accepted_decision:
            row["accepted_degraded"] = status.accepted_decision
        if status.waived_by:
            row["waived_rerun"] = status.waived_by
        rows.append(row)
    return rows


def _source_error(scope: str, problem: str, *, hint: str | None = None) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"verification source for {scope!r} {problem}",
        hint=hint or "repair the verification source declaration and retry",
    )


def replace_file_bytes(path: Path, content: bytes) -> None:
    """Durably replace a private staging path with completed bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def stamp_recorded_equivalence(
    root: Path,
    evidence: SourceEvidence,
    *,
    observed: Mapping[str, ObservedPath] | None = None,
) -> SourceEvidence:
    """Stamp every rule-bearing file observation, as each written record does.

    Identity comes from the bytes ``observed`` holds for a path, else a re-read
    at ``root``; with neither matching an observation's raw digest it records
    none, so that path compares raw.
    """
    return _stamp_from_root(
        root, evidence, {row.path for row in evidence.observations}, observed
    )


def stamp_changed_equivalence(
    root: Path, current: SourceEvidence, recorded: SourceEvidence
) -> SourceEvidence:
    """Current evidence stamped for the paths that differ raw from ``recorded``.

    Reads build raw identity only. This computes identity on
    demand, and only where it can bridge the difference: a file observation of
    the same path, kind and mode whose content digest differs and whose
    recorded observation carries an identity under the rule this Heddle uses.
    """
    prior = {row.path: row for row in recorded.observations}
    changed: set[str] = set()
    for row in current.observations:
        before = prior.get(row.path)
        if (
            before is not None
            and before.equivalence is not None
            and row.kind == "file"
            and (before.kind, before.executable) == (row.kind, row.executable)
            and before.content_sha256 != row.content_sha256
            and before.equivalence.rule == rule_id_for(row.path)
        ):
            changed.add(row.path)
    return _stamp_from_root(root, current, changed)


def _stamp_from_root(
    root: Path,
    evidence: SourceEvidence,
    paths: set[str],
    observed: Mapping[str, ObservedPath] | None = None,
) -> SourceEvidence:
    contents: dict[str, Iterable[bytes]] = {}
    for row in evidence.observations:
        if row.path not in paths or row.kind != "file" or rule_id_for(row.path) is None:
            continue
        contents[row.path] = _candidate_contents(
            root, row.path, (observed or {}).get(row.path)
        )
    return stamp_equivalence(evidence, contents)


def _candidate_contents(
    root: Path, path: str, held: ObservedPath | None
) -> Iterator[bytes]:
    """Held bytes first; the re-read at ``root`` only when they do not match."""
    if held is not None:
        yield from _observed_contents(held)
    try:
        captured = capture_path(root, path)
    except KernelError:
        return
    yield from _observed_contents(captured)


def _observed_contents(captured: ObservedPath) -> Iterator[bytes]:
    """The re-read bytes, then the authored text a plan observation holds."""
    yield captured.content
    yield authored_plan_observation(captured).content


def publish_source_evidence(
    workspace: Path,
    evidence: SourceEvidence,
    *,
    root: Path | None = None,
    observed: Mapping[str, ObservedPath] | None = None,
) -> tuple[EvidenceReference, bool]:
    """Stamp, publish create-only and return the stamped evidence's reference.

    Every published manifest records the equivalence identity of each
    rule-bearing file observation, from the bytes ``observed`` holds or else
    re-read at ``root``, else at the project enclosing ``workspace``.
    """
    project = root if root is not None else find_project_root(workspace)
    if project is None:
        raise _source_error(
            "evidence",
            f"has no enclosing Heddle project: {workspace}",
            hint="publish source evidence from a workspace inside a Heddle project",
        )
    return publish_stamped_evidence(
        workspace, stamp_recorded_equivalence(project, evidence, observed=observed)
    )


def publish_stamped_evidence(
    workspace: Path, evidence: SourceEvidence
) -> tuple[EvidenceReference, bool]:
    """Publish an already stamped manifest create-only, exactly as given."""
    content = encode_source_evidence(evidence)
    reference = evidence_reference(evidence)
    target = _evidence_path(workspace, reference.artifact)
    try:
        reused = _publish_evidence_bytes(target, content)
        readback = read_source_evidence(workspace, reference)
    except KernelError:
        raise
    except OSError as error:
        raise _source_error(
            "evidence",
            f"publication failure for {reference.artifact}: {error}",
            hint="restore a writable verification directory and retry",
        ) from error
    if readback != evidence:
        raise _source_error(
            "evidence",
            f"published artifact contradicts {reference.artifact}",
        )
    return reference, reused


def _publish_evidence_bytes(target: Path, content: bytes) -> bool:
    """Create one content-addressed file; exact concurrent reuse is a no-op."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.{secrets.token_hex(8)}.stage")
    try:
        replace_file_bytes(staging, content)
        decode_source_evidence(_read_regular_bytes(staging, label="staged evidence"))
        created = False
        try:
            os.link(staging, target, follow_symlinks=False)
            created = True
        except FileExistsError:
            pass
        actual = _read_regular_bytes(target, label="source evidence")
        if actual != content:
            raise _source_error(
                "evidence",
                f"content-addressed conflict or tampered bytes at {target}",
            )
        directory = os.open(
            target.parent,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return not created
    finally:
        staging.unlink(missing_ok=True)


def read_source_evidence(
    workspace: Path, reference: EvidenceReference | dict[str, Any]
) -> SourceEvidence:
    """Resolve a contained reference and validate bytes plus semantic identity."""
    parsed = _decode_evidence_reference(reference)
    target = _evidence_path(workspace, parsed.artifact)
    try:
        content = _read_regular_bytes(target, label="source evidence")
    except FileNotFoundError as error:
        raise _source_error(
            "evidence", f"is missing required artifact {parsed.artifact}"
        ) from error
    digest = hashlib.sha256(content).hexdigest()
    if digest != parsed.artifact_sha256:
        raise _source_error("evidence", f"has tampered bytes at {parsed.artifact}")
    evidence = decode_source_evidence(content)
    if evidence.source_sha256 != parsed.source_sha256:
        raise _source_error(
            "evidence",
            f"reference contradicts source identity for {parsed.artifact}",
        )
    expected = evidence_reference(evidence)
    if expected != parsed:
        raise _source_error(
            "evidence", f"reference contradicts artifact identity for {parsed.artifact}"
        )
    return evidence


def _decode_evidence_reference(
    value: EvidenceReference | dict[str, Any],
) -> EvidenceReference:
    try:
        return decode_evidence_reference(value)
    except KernelError as error:
        raise _source_error("evidence", error.message, hint=error.hint) from error


def _evidence_path(workspace: Path, relative: str) -> Path:
    parts = safe_relative_parts(relative)
    if not relative.startswith("verification/source-evidence."):
        raise _source_error("evidence", "artifact path is outside verification")
    workspace = workspace.resolve()
    target = workspace.joinpath(*parts)
    for length in range(1, len(parts)):
        ancestor = workspace.joinpath(*parts[:length])
        try:
            if stat.S_ISLNK(ancestor.lstat().st_mode):
                raise _source_error(
                    "evidence", f"artifact ancestor is a symlink: {ancestor}"
                )
        except FileNotFoundError:
            continue
    return target


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise _source_error("evidence", f"{label} is a symlink: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise _source_error(
            "evidence", f"{label} has a conflicting type; expected regular file: {path}"
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise _source_error("evidence", f"could not read {path}: {error}") from error


def inventory_source_paths(
    repo_root: Path,
    declaration: SourceDeclaration,
    baseline_probe: str | None = None,
    *,
    source_baseline: SourceBaseline | None = None,
) -> GateSourceInventory:
    """Return Git-owned leaf and feature-baseline facts for verification."""
    root = repo_root.resolve()
    tracked_paths, tracked_warning = git.tracked_files(root)
    untracked_paths, untracked_warning = git.discover_untracked_files(root)
    tracked = _existing_leaves(
        root,
        _declared_candidates(tracked_paths, declaration.paths),
    )
    untracked_leaves = _existing_leaves(
        root,
        _declared_candidates(untracked_paths, declaration.paths),
    )

    # Current exact files include ignored declarations. Missing identity markers
    # are selected by resolve_source_definition, never classified as current leaves.
    leaf_error = tracked_warning or untracked_warning
    direct = _direct_declared_leaves(root, declaration.paths)
    tracked = tuple(sorted(set(tracked) | set(direct), key=byte_sort_key))

    if baseline_probe is None and source_baseline is None:
        return GateSourceInventory(
            tracked,
            untracked_leaves,
            None,
            "baseline-probe-missing",
            None,
            leaf_error,
        )
    baseline_label = (
        source_baseline.oid if source_baseline is not None else baseline_probe
    )
    if leaf_error:
        return GateSourceInventory(
            tracked,
            untracked_leaves,
            None,
            "git-unavailable",
            baseline_label,
            leaf_error,
        )

    try:
        baseline = git.resolve_feature_baseline(
            root,
            source_baseline,
            legacy_state_probe=baseline_probe,
        )
    except git.FeatureBaselineError as error:
        return GateSourceInventory(
            tracked,
            untracked_leaves,
            None,
            error.reason,
            baseline_label,
            None,
        )
    with _git_workspace_errors():
        changed_text, changed_warning = git.run_git(
            ["git", "diff", "--name-status", "-z", "-M", baseline, "--"],
            root,
        )
    if changed_warning:
        return GateSourceInventory(
            tracked,
            untracked_leaves,
            None,
            "git-query-failed",
            baseline_label,
            None,
        )
    with _git_workspace_errors():
        changed = set(git.changed_name_status_paths(changed_text))
    changed.update(untracked_paths)
    return GateSourceInventory(
        tracked,
        untracked_leaves,
        tuple(sorted(changed, key=byte_sort_key)),
        None,
        baseline_label,
        None,
    )


@dataclass(frozen=True)
class WorkflowControlPaths:
    roots: tuple[str, ...]
    exact: tuple[str, ...]


def workflow_control_paths(
    config: ProjectConfig, state: StateFile
) -> WorkflowControlPaths:
    """Return this feature's exact control files and generated control roots."""
    workspace = f"{config.layout.plans}/{state.feature}"
    from heddle.runtime import intake

    research: tuple[str, ...] = ()
    intake_file = intake.intake_path(config, state.feature)
    if intake_file.exists():
        document = intake.read_intake(config, state.feature)
        binding = {
            "path": intake_file.relative_to(config.root).as_posix(),
            "sha256": intake.intake_digest(document),
        }
        if state.intake != binding:
            raise intake.invalid(
                "available intake does not match the state's admission binding"
            )
        research = (document["research"]["reference"],)
    exact = normalize_paths(
        {
            f".heddle/intake/{state.feature}.yaml",
            f"{workspace}/state.yaml",
            f"{workspace}/plan.md",
            f"{workspace}/brief.md",
            state.spec,
            f"{config.layout.plans}/{state.feature}.decision-journal.md",
            f"{config.layout.plans}/{state.feature}.friction-retrospective.md",
            f"{config.layout.plans}/gate-effectiveness.md",
            f"{config.layout.plans}/friction-log.md",
            f"{config.layout.plans}/.briefs/{state.feature}.md",
            f"{config.layout.specs}/_index.md",
            f"{config.layout.specs}/_descriptions.yaml",
            *research,
        }
    )
    roots = normalize_paths(
        (
            f"{workspace}/orchestration",
            f"{workspace}/reviews",
            f"{workspace}/verification",
            f"docs/gate-trajectories/.raw/{state.feature}",
        )
    )
    return WorkflowControlPaths(roots=roots, exact=exact)


def observe_git_diagnostics(
    repo_root: Path, declaration_paths: tuple[str, ...]
) -> GitDiagnostics:
    """Sample descriptive HEAD and dirty-state facts; never authorize."""
    root = repo_root.resolve()
    try:
        head, head_warning = git.run_git(["git", "rev-parse", "HEAD"], root)
    except git.GitError:
        return GitDiagnostics(None, None)
    if head_warning or not head.strip():
        return GitDiagnostics(None, None)
    try:
        status_text, status_warning = git.run_git(
            ["git", "status", "--porcelain", "--", *declaration_paths],
            root,
        )
    except git.GitError:
        return GitDiagnostics(head.strip(), None)
    if status_warning:
        return GitDiagnostics(head.strip(), None)
    return GitDiagnostics(head.strip(), bool(status_text.strip()))


def _existing_leaves(
    root: Path,
    paths: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    leaves: set[str] = set()
    for relative in paths:
        if not relative:
            continue
        absolute = root / relative
        try:
            metadata = os.lstat(absolute)
        except FileNotFoundError:
            continue
        except NotADirectoryError:
            continue
        except OSError as error:
            raise _inventory_path_error(relative, error) from error
        if not stat.S_ISDIR(metadata.st_mode):
            leaves.add(relative)
    return tuple(sorted(leaves, key=byte_sort_key))


def _declared_candidates(
    paths: list[str] | tuple[str, ...], declaration_paths: tuple[str, ...]
) -> tuple[str, ...]:
    """Limit fail-closed filesystem probes to paths a declaration can admit."""
    return tuple(
        path
        for path in paths
        if any(
            path == owner or path.startswith(f"{owner}/") for owner in declaration_paths
        )
    )


def _direct_declared_leaves(
    root: Path, paths: list[str] | tuple[str, ...]
) -> tuple[str, ...]:
    """Classify existing exact non-directory declarations, including ignored ones."""
    leaves: set[str] = set()
    for relative in paths:
        if not relative:
            continue
        try:
            metadata = os.lstat(root / relative)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise _inventory_path_error(relative, error) from error
        if not stat.S_ISDIR(metadata.st_mode):
            leaves.add(relative)
    return tuple(sorted(leaves, key=byte_sort_key))


def _inventory_path_error(relative: str, error: OSError) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"cannot inspect verification source path {relative!r}: {error}",
        hint="restore readable repository inputs and rerun verification",
    )


@contextmanager
def _git_workspace_errors() -> Iterator[None]:
    """Map shared Git I/O failures to verification workspace diagnostics."""
    try:
        yield
    except git.GitError as error:
        raise KernelError(
            code="workspace-invalid",
            message=str(error),
            hint=(
                "repair the Git repository and its UTF-8 paths/content, "
                "then retry verification"
            ),
        ) from error
