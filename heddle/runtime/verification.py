"""Runtime composition for verification source declarations and observations."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from heddle.contracts.decisions import AcceptDegradedSmoke
from heddle.contracts.operations import (
    CommandAction,
    ManualAction,
    Verify,
    action_command,
)
from heddle.contracts.result import Diagnostic, Severity
from heddle.io import git
from heddle.io.source import observe_source, resolve_source_definition
from heddle.kernel.project_config import KernelError, ProjectConfig, load_project_config
from heddle.kernel.readiness import EvidenceExplanation
from heddle.kernel.smoke_disposition import (
    validate_smoke_proposal,
    verification_fact_identity,
)
from heddle.kernel.source_manifest import (
    EvidenceReference,
    ObservedPath,
    SourceDefinition,
    SourceEvidence,
    byte_sort_key,
    decode_evidence_reference,
    decode_source_evidence,
    encode_source_evidence,
    evidence_reference,
    normalize_milestone_source_paths,
    normalize_paths,
    normalize_source_paths,
    safe_relative_parts,
)
from heddle.kernel.state import SourceBaseline, StateFile
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
        if status.status == "source-set-stale":
            assert after is not None
            dependencies = tuple(
                sorted(
                    set(after.definition.paths) ^ set(manifest.definition.paths),
                    key=byte_sort_key,
                )
            )
            detail_limit = (
                None
                if dependencies
                else "only the verification source-set identity is retained"
            )
            status = replace(
                status,
                detail_limit=detail_limit,
                explanation=EvidenceExplanation(
                    subject=f"verification:{scope}",
                    cause="declared verification source set changed",
                    changed_dependencies=dependencies,
                    failed_reference=None,
                    remedy=status.action,
                    detail_limit=detail_limit,
                ),
            )
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


def reconcile_current_source(
    root: Path,
    state: StateFile,
    scope: str,
    *,
    baseline_probe: str,
    runtime_owned_roots: tuple[str, ...],
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

    attributed = qualified_attribution_paths(root, state, owned=declaration.paths)
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
    return assess_current_verifications(
        root, state, required_verification_scopes(state)
    )


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
            if status.status in {"content-stale", "command-stale", "source-set-stale"}
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
            )
        if status.accepted_decision:
            row["accepted_degraded"] = status.accepted_decision
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


def publish_source_evidence(
    workspace: Path, evidence: SourceEvidence
) -> tuple[EvidenceReference, bool]:
    """Publish canonical evidence create-only and return its validated reference."""
    content = encode_source_evidence(evidence)
    reference = evidence_reference(evidence)
    target = _evidence_path(workspace, reference.artifact)
    try:
        reused = _publish_evidence_bytes(target, content)
        observed = read_source_evidence(workspace, reference)
    except KernelError:
        raise
    except OSError as error:
        raise _source_error(
            "evidence",
            f"publication failure for {reference.artifact}: {error}",
            hint="restore a writable verification directory and retry",
        ) from error
    if observed != evidence:
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
        (
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
        )
    )
    roots = normalize_paths(
        (
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
