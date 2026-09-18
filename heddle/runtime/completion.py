"""Human acceptance followed by recoverable stamp, retention and cleanup effects."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
import zlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import IO, Any

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    CompletionRepair,
    Conflict,
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.contracts.review_assignments import (
    RESERVED_AUTHORED_ARTIFACT_PATHS,
    ArtifactRef,
    ReviewAttempt,
)
from heddle.kernel.feature_policy import effective_policy
from heddle.kernel.model import is_terminal, resolve_snapshot
from heddle.kernel.project_config import KernelError, ProjectConfig
from heddle.kernel.readiness import Boundary, BoundaryAssessment
from heddle.kernel.spec_lifecycle import render_lifecycle
from heddle.kernel.state import (
    CompletionFact,
    SpecStampIdentity,
    StateFile,
    VerificationFact,
)
from heddle.kernel.write_path import accept_completion
from heddle.runtime.auto_close import (
    CompletionRepairObservation,
    archive_trajectory_best_effort,
    close_audit_repairs,
    close_obligation,
    run_close_suite,
)
from heddle.runtime.clock import utc_now_seconds
from heddle.runtime.diagnostics import blocking_failure, conflict_failure
from heddle.runtime.feature_context import (
    ResolvedSnapshotContext,
    resolve_write_context,
)
from heddle.runtime.locking import state_recording_lock
from heddle.runtime.readiness import (
    assess_current_readiness,
    readiness_failure,
    readiness_status_block,
)
from heddle.runtime.state_store import check_expect_revision, commit_state
from heddle.runtime.verification import (
    reconcile_current_source,
    required_verification_scopes,
    workflow_control_paths,
)
from heddle.runtime.write_args import parse_write_args, usage_failure
from heddle.runtime.write_path import emit_result

_MANIFEST = ".heddle-completion-archive.json"
_ARCHIVE_SCHEMA = "heddle.completion-archive/v1"
_USAGE = (
    "heddle feature complete [--feature <slug>] [--expect-revision <n>] [--dry-run]"
)
_SCRATCH_NAMES = frozenset(
    {
        "prompt.md",
        "raw.out",
        "last-message.txt",
        "json-message.txt",
        "events.jsonl",
        "command-output.txt",
    }
)


@dataclass(frozen=True)
class CompletionQualification:
    assessment: BoundaryAssessment
    stamp: SpecStampIdentity
    retained: dict[str, ArchiveEntry]


class CleanupArchiveConflict(ValueError):
    """Cleanup could not revalidate the archive that established binding."""


def run_feature_complete(args: list[str], json_mode: bool) -> int:
    parsed, _values, _positionals, failure = parse_write_args(
        args,
        value_flags={},
        unknown_hint=_USAGE,
        allow_positionals=False,
    )
    if failure is not None:
        return emit_result(failure, json_mode)
    assert parsed is not None
    from heddle.runtime.application import execute

    return emit_result(
        execute(
            ops.FeatureComplete(
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )


def complete_feature(operation: ops.FeatureComplete) -> HeddleResult:
    # An accepted retry bypasses current source, commands, audits and caller CAS.
    context = resolve_write_context(operation.feature, allow_terminal=True)
    if isinstance(context, HeddleResult):
        return context
    if is_terminal(context.snapshot.state):
        return completion_result(context, dry_run=operation.dry_run)
    state = context.snapshot.state
    if conflict := check_expect_revision(operation.expect_revision, state.revision):
        return conflict_failure(conflict)
    try:
        qualification = _qualify(context)
        if isinstance(qualification, HeddleResult):
            return qualification
        identity = qualification.stamp
        if operation.dry_run:
            retained_evidence = _retained_evidence_report(
                state,
                qualification.retained,
                workspace=context.snapshot.workspace,
                archive=archive_path(context.config, state.feature)
                .relative_to(context.config.root)
                .as_posix(),
                status="pending",
            )
            return HeddleResult.success(
                {
                    "feature": state.feature,
                    "accepted": False,
                    "accepted_at": None,
                    "revision": state.revision,
                    "wrote": False,
                    "dry_run": True,
                    "close_suite_command": context.config.autopilot.test_command
                    or None,
                    "close_obligation": close_obligation(context.config),
                    "effects": {
                        name: {"status": "pending", "paths": paths}
                        for name, paths in {
                            "stamp": [state.spec],
                            "archive": [
                                archive_path(context.config, state.feature)
                                .relative_to(context.config.root)
                                .as_posix()
                            ],
                            "cleanup": [context.snapshot.workspace],
                        }.items()
                    },
                    "retained_evidence": retained_evidence,
                },
                diagnostics=(
                    *context.diagnostics,
                    *_retained_evidence_diagnostics(retained_evidence),
                ),
            )
        suite = run_close_suite(context)
        if isinstance(suite, HeddleResult):
            return suite
        refreshed = resolve_write_context(state.feature, allow_terminal=True)
        if isinstance(refreshed, HeddleResult):
            return refreshed
        context = refreshed
        if is_terminal(context.snapshot.state):
            return completion_result(context)
        if conflict := check_expect_revision(
            state.revision, context.snapshot.state.revision
        ):
            raise conflict
        qualification = _qualify(context)
        if isinstance(qualification, HeddleResult):
            return qualification
        assessment = qualification.assessment
        state = context.snapshot.state
        if qualification.stamp != identity:
            raise Conflict(
                "Feature Spec changed during the close suite; reread and retry"
            )
        qualified_scopes = {(row.gate, row.scope) for row in assessment.reviews}
        qualified_assignments = {
            assignment.id
            for assignment in state.review_assignments.assignments
            if (assignment.role, assignment.scope) in qualified_scopes
        }
        from heddle.kernel.review_assignments import authoritative_sources

        completion_scopes = set(required_verification_scopes(state))
        fact = CompletionFact(
            accepted_at=utc_now_seconds(),
            authorization_index=max(
                i
                for i, grant in enumerate(state.authorizations)
                if grant.through == "complete"
            ),
            verification_indexes=tuple(
                row.fact_index
                for row in assessment.verifications
                if row.scope in completion_scopes and row.fact_index is not None
            ),
            review_run_ids=tuple(
                source.run_id
                for source in authoritative_sources(state)
                if source.assignment_id in qualified_assignments
            ),
            accepted_smoke_decision_id=next(
                (
                    row.accepted_decision
                    for row in assessment.verifications
                    if row.scope == "smoke"
                ),
                None,
            ),
            spec_stamp=identity,
            close_suite=suite,
        )
        committed = commit_state(
            context.state_path,
            expect_revision=state.revision,
            transform=lambda document: accept_completion(
                document, expected=state, completion=fact
            ),
        )
    except (Conflict, KernelError) as error:
        # A concurrent winner owns the one fact, even if its effects are pending.
        recovered = resolve_write_context(state.feature, allow_terminal=True)
        if not isinstance(recovered, HeddleResult) and is_terminal(
            recovered.snapshot.state
        ):
            return completion_result(recovered)
        return (
            conflict_failure(error)
            if isinstance(error, Conflict)
            else readiness_failure(error, context.diagnostics)
        )
    context = replace(context, snapshot=resolve_snapshot(context.config, state.feature))
    return completion_result(context, wrote=committed.wrote)


def _qualify(
    context: ResolvedSnapshotContext,
) -> CompletionQualification | HeddleResult:
    snapshot = context.snapshot
    state = snapshot.state
    if state.stage != "complete":
        return usage_failure(
            f"feature {state.feature} is at {state.stage}, not complete",
            "finish the current stage before accepting completion",
        )
    if any(m.status != "done" for m in state.milestones):
        return blocking_failure(snapshot, "milestone-out-of-sequence")
    ordinary = tuple(
        code
        for code in snapshot.blocking_conditions
        if code != "awaiting-human-completion"
    )
    if ordinary:
        return blocking_failure(snapshot, ordinary[0])
    if not any(grant.through == "complete" for grant in state.authorizations):
        return blocking_failure(snapshot, "not-authorized")
    repairs = close_audit_repairs(context)
    stamp = _prepare_stamp(context.config.root, context.config.root / state.spec)
    if isinstance(stamp, CompletionRepairObservation):
        repairs = (*repairs, stamp)
    if repairs:
        return _completion_repair_failure(repairs, context.diagnostics)
    assert isinstance(stamp, SpecStampIdentity)
    assessment = assess_current_readiness(
        context.config, snapshot, boundary=Boundary("complete", None)
    )
    if block := readiness_status_block(assessment, context.diagnostics):
        return block
    scopes = required_verification_scopes(state)
    controls = workflow_control_paths(context.config, state)
    reconcile_current_source(
        context.config.root,
        state,
        scopes[0],
        baseline_probe=context.state_path.relative_to(context.config.root).as_posix(),
        runtime_owned_roots=controls.roots,
        excluded_paths=controls.exact,
        workspace=context.snapshot.workspace,
    )
    if not archive_path(context.config, state.feature).exists():
        _validate_archive_inputs(context.config.root, context.state_path.parent, state)
    retained = _retained_evidence_snapshot(
        context.config.root, context.state_path.parent, state
    )
    return CompletionQualification(assessment, stamp, retained)


def archive_path(config: ProjectConfig, feature: str) -> Path:
    return config.root / "docs/gate-trajectories/.raw" / feature / "completion.tar.gz"


def _no_symlinks(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    if ".." in relative.parts:
        raise OSError(f"unsafe completion path: {path}")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise OSError(f"completion refuses symlink: {current}")


def _completion_repair_failure(
    observations: tuple[CompletionRepairObservation, ...],
    diagnostics: tuple[Diagnostic, ...],
) -> HeddleResult:
    first = observations[0]
    return HeddleResult.failure(
        HeddleError(
            code=first.repair.code,
            message=first.repair.message,
            hint=first.repair.hint,
            details={
                "repairs": [
                    observation.repair.to_payload() for observation in observations
                ]
            },
        ),
        exit_code=first.exit_code,
        diagnostics=diagnostics,
        next_actions=tuple(observation.next_action for observation in observations),
    )


def _prepare_stamp(
    root: Path, path: Path
) -> SpecStampIdentity | CompletionRepairObservation:
    try:
        _no_symlinks(root, path)
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise OSError(f"spec is not a regular file: {path}")
    except (OSError, ValueError) as error:
        raise KernelError(
            "workspace-invalid",
            f"cannot prepare completion stamp for {path}: {error}",
            "restore the exact readable Feature Spec and retry",
        ) from error
    relative = path.relative_to(root).as_posix()
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        message = f"cannot prepare completion stamp for {relative}: {error}"
        hint = "restore the exact readable Feature Spec and retry"
        return CompletionRepairObservation(
            repair=CompletionRepair(
                "spec-stamp",
                "unreadable",
                relative,
                "workspace-invalid",
                message,
                hint,
            ),
            next_action=NextAction(
                ops.ManualAction(f"write {relative}"),
                "provide a readable Feature Spec before accepting completion",
            ),
        )
    try:
        after = render_lifecycle(text, Path(relative), "complete").encode("utf-8")
    except KernelError as error:
        return CompletionRepairObservation(
            repair=CompletionRepair(
                "spec-stamp",
                "invalid-frontmatter",
                relative,
                error.code,
                error.message,
                error.hint,
            ),
            next_action=NextAction(
                ops.ManualAction(f"write {relative}"),
                "repair Feature Spec frontmatter before accepting completion",
            ),
        )
    return SpecStampIdentity(
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(after).hexdigest(),
        bool(mode & 0o111),
    )


def _stamp(
    root: Path, path: Path, identity: SpecStampIdentity, *, dry_run: bool
) -> str:
    _no_symlinks(root, path)
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or bool(mode & 0o111) != identity.executable:
        raise OSError(f"accepted spec type/executable mode changed: {path}")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest == identity.after_sha256:
        return "complete"
    if digest != identity.before_sha256:
        raise OSError(f"accepted spec bytes changed: {path}")
    if dry_run:
        return "pending"
    after = render_lifecycle(raw.decode("utf-8"), path, "complete").encode("utf-8")
    if hashlib.sha256(after).hexdigest() != identity.after_sha256:
        raise OSError(
            f"accepted spec stamp does not match its recorded postimage: {path}"
        )
    with NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(after)
    try:
        os.chmod(temporary, stat.S_IMODE(mode))
        _no_symlinks(root, path)
        if path.read_bytes() != raw or path.stat().st_mode != mode:
            raise OSError(f"accepted spec changed before publication: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return "complete"


@dataclass(frozen=True)
class ArchiveEntry:
    kind: str
    sha256: str
    mode: int


@dataclass(frozen=True)
class CleanupReport:
    removed: tuple[str, ...]
    preserved: tuple[tuple[str, str], ...]


def retained_evidence_paths(
    facts: tuple[VerificationFact, ...],
) -> tuple[str, ...]:
    """Return logs and immutable manifests required by verification facts."""
    paths: set[str] = set()
    for fact in facts:
        paths.add(fact.log)
        paths.add(fact.evidence.before.artifact)
        paths.add(fact.evidence.after.artifact)
    return tuple(sorted(paths))


def cleanup_artifacts(
    workspace: Path,
    artifacts: tuple[ArtifactRef, ...],
    *,
    accepted: bool,
    archive_validated: bool,
    retention_qualified: bool,
) -> CleanupReport:
    """Remove only indexed disposable artifacts after every cleanup barrier."""
    if not (accepted and archive_validated and retention_qualified):
        return CleanupReport((), ())
    removed: list[str] = []
    preserved: list[tuple[str, str]] = []
    for reference in artifacts:
        if reference.role not in {"derived", "temporary"}:
            continue
        path = workspace / reference.path
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            preserved.append((reference.path, "missing"))
            continue
        if not stat.S_ISREG(metadata.st_mode):
            preserved.append((reference.path, "type"))
            continue
        if stat.S_IMODE(metadata.st_mode) != reference.mode:
            preserved.append((reference.path, "mode"))
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != reference.sha256:
            preserved.append((reference.path, "bytes"))
            continue
        _no_symlinks(workspace, path)
        path.unlink()
        removed.append(reference.path)
    return CleanupReport(tuple(removed), tuple(preserved))


def _digest_stream(stream: IO[bytes]) -> str:
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _entry(root: Path, path: Path) -> ArchiveEntry:
    _no_symlinks(root, path.parent)
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return ArchiveEntry(
            "symlink",
            hashlib.sha256(os.readlink(path).encode()).hexdigest(),
            stat.S_IMODE(mode),
        )
    if stat.S_ISDIR(mode):
        return ArchiveEntry(
            "directory", hashlib.sha256(b"").hexdigest(), stat.S_IMODE(mode)
        )
    if not stat.S_ISREG(mode):
        raise OSError(f"unsupported archive entry type: {path}")
    with path.open("rb") as stream:
        return ArchiveEntry("file", _digest_stream(stream), stat.S_IMODE(mode))


def _workspace_entries(workspace: Path) -> dict[str, ArchiveEntry]:
    entries = {}

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(workspace).as_posix()
            entry = _entry(workspace, path)
            entries[relative] = entry
            if entry.kind == "directory":
                visit(path)

    visit(workspace)
    return entries


def _retained_evidence_snapshot(
    root: Path,
    workspace: Path,
    state: StateFile,
    *,
    attempt_references: tuple[ArtifactRef, ...] | None = None,
) -> dict[str, ArchiveEntry]:
    """Validate and snapshot every artifact that accepted completion must retain."""
    expected: dict[str, tuple[str, str, int | None]] = {}

    def register(
        path: str, sha256: str, kind: str = "file", mode: int | None = None
    ) -> None:
        identity = (sha256, kind, mode)
        previous = expected.get(path)
        if previous is not None and previous != identity:
            raise ValueError(f"conflicting retained artifact identity: {path}")
        expected[path] = identity

    observed: dict[str, ArchiveEntry] = {}
    try:
        if attempt_references is None:
            from heddle.kernel.review_assignments import attempt_artifacts

            attempt_references = attempt_artifacts(state.review_assignments.attempts)
        for reference in attempt_references:
            if reference.role in {"canonical", "capture", "evidence", "log"}:
                register(
                    reference.path,
                    reference.sha256,
                    reference.kind,
                    reference.mode,
                )
        for fact in state.verifications:
            for evidence_reference in (fact.evidence.before, fact.evidence.after):
                register(
                    evidence_reference.artifact,
                    evidence_reference.artifact_sha256,
                )
        for name in sorted(_retained_paths(state)):
            parsed = PurePosixPath(name)
            if (
                parsed.is_absolute()
                or ".." in parsed.parts
                or parsed.as_posix() != name
            ):
                raise OSError(f"unsafe retained evidence path: {name}")
            path = workspace / name
            _no_symlinks(root, path)
            entry = _entry(workspace, path)
            if entry.kind != "file":
                raise OSError(f"retained evidence is not a regular file: {path}")
            identity = expected.get(name)
            if identity is not None:
                sha256, kind, mode = identity
                if entry.sha256 != sha256 or entry.kind != kind:
                    raise OSError(f"retained evidence identity differs: {path}")
                if mode is not None and entry.mode != mode:
                    raise OSError(f"retained evidence mode differs: {path}")
            observed[name] = entry
    except (OSError, ValueError) as error:
        raise KernelError(
            "workspace-invalid",
            f"completion retained evidence is invalid: {error}",
            "restore every referenced review artifact, verification log and "
            "source-evidence manifest before accepting completion",
        ) from error
    return observed


def _retained_evidence_report(
    state: StateFile,
    observed: dict[str, ArchiveEntry],
    *,
    workspace: str,
    archive: str,
    status: str,
    archived: dict[str, ArchiveEntry] | None = None,
    attempt_references: tuple[ArtifactRef, ...] | None = None,
) -> dict[str, Any]:
    """Project validated retained identities and their direct state relationships."""
    if status not in {"pending", "archive-bound", "conflict"}:
        raise ValueError(f"unsupported retained-evidence status: {status}")
    if status == "archive-bound" and archived is None:
        raise ValueError("archive-bound retained evidence needs validated members")

    roles: dict[str, set[str]] = {
        "state.yaml": {"accepted-ledger" if state.completion is not None else "ledger"}
    }

    def add(path: str, role: str) -> None:
        roles.setdefault(path, set()).add(role)

    if attempt_references is None:
        from heddle.kernel.review_assignments import attempt_artifacts

        attempt_references = attempt_artifacts(state.review_assignments.attempts)
    for reference in attempt_references:
        if reference.role in {"canonical", "capture", "evidence", "log"}:
            add(reference.path, reference.role)
    for gate in state.gates:
        for run in gate.runs:
            if run.verdict.get("status") != "error":
                add(run.artifact, "review-record")
    for fact in state.verifications:
        add(fact.log, "verification-log")
        add(fact.evidence.before.artifact, "verification-evidence")
        add(fact.evidence.after.artifact, "verification-evidence")
    if state.completion is not None and state.completion.close_suite is not None:
        add(state.completion.close_suite.log, "close-suite-log")

    artifacts: list[dict[str, Any]] = []
    for path, entry in sorted(observed.items()):
        artifact_roles = roles.get(path)
        if not artifact_roles:
            raise ValueError(f"retained artifact has no report role: {path}")
        row: dict[str, Any] = {
            "path": path,
            "roles": sorted(artifact_roles),
            "kind": entry.kind,
            "sha256": entry.sha256,
            "mode": entry.mode,
        }
        if status == "archive-bound":
            assert archived is not None
            if archived.get(path) != entry:
                raise ValueError(f"validated archive member differs: {path}")
            row["archive_member"] = path
        artifacts.append(row)
    return {
        "status": status,
        "workspace": workspace,
        "archive": archive,
        "artifacts": artifacts,
    }


def _retained_evidence_diagnostics(
    report: dict[str, Any],
) -> tuple[Diagnostic, ...]:
    status = report["status"]
    diagnostics: list[Diagnostic] = []
    if status == "conflict":
        diagnostics.append(
            Diagnostic(
                Severity.ADVISORY,
                "completion-retained-evidence-conflict",
                "retained evidence conflict: verified archive membership is "
                "unavailable until the reported completion effect is repaired",
                report["archive"],
            )
        )
    for artifact in report["artifacts"]:
        message = (
            f"retained local evidence: {artifact['path']}; roles: "
            f"{', '.join(artifact['roles'])}"
        )
        member = artifact.get("archive_member")
        if member is not None:
            message += f"; verified archive member: {report['archive']}::{member}"
        else:
            message += f"; archive binding: {status}"
        diagnostics.append(
            Diagnostic(
                Severity.ADVISORY,
                "completion-retained-evidence",
                message,
                artifact["path"],
            )
        )
    return tuple(diagnostics)


def _required_authored_archive_inputs(
    root: Path, workspace: Path
) -> dict[str, ArchiveEntry]:
    """Observe the authored workspace records required by every archive."""
    observed: dict[str, ArchiveEntry] = {}
    for name in sorted(RESERVED_AUTHORED_ARTIFACT_PATHS):
        path = workspace / name
        _no_symlinks(root, path)
        entry = _entry(workspace, path)
        if entry.kind != "file":
            raise OSError(f"required authored archive input is not a file: {path}")
        observed[name] = entry
    return observed


def _validate_archive_inputs(
    root: Path, workspace: Path, state: StateFile
) -> dict[str, ArchiveEntry]:
    """Require authored and indexed inputs before first archive publication."""
    from heddle.kernel.review_assignments import attempt_artifacts

    try:
        authored = _required_authored_archive_inputs(root, workspace)
        for reference in attempt_artifacts(state.review_assignments.attempts):
            path = workspace / reference.path
            _no_symlinks(root, path)
            observed = _entry(workspace, path)
            if (
                observed.kind != reference.kind
                or observed.sha256 != reference.sha256
                or (reference.mode is not None and observed.mode != reference.mode)
            ):
                raise OSError(
                    "indexed archive input differs from its recorded "
                    f"bytes/type/mode: {path}"
                )
    except (OSError, ValueError) as error:
        raise KernelError(
            "workspace-invalid",
            f"completion archive input is invalid: {error}",
            "restore every indexed review artifact before publishing the "
            "completion archive",
        ) from error
    return authored


def _validate_archive_contract(
    path: Path,
    state: StateFile,
    authored: dict[str, ArchiveEntry],
    archived: dict[str, ArchiveEntry],
) -> None:
    """Bind required authored and indexed identities to one verified archive."""
    from heddle.kernel.review_assignments import attempt_artifacts

    for name, expected in authored.items():
        if archived.get(name) != expected:
            raise OSError(
                f"completion archive {path} does not retain required authored "
                f"input {name}"
            )
    for reference in attempt_artifacts(state.review_assignments.attempts):
        observed = archived.get(reference.path)
        if (
            observed is None
            or observed.kind != reference.kind
            or observed.sha256 != reference.sha256
            or (reference.mode is not None and observed.mode != reference.mode)
        ):
            raise OSError(
                f"completion archive {path} does not retain indexed artifact "
                f"{reference.path} with its recorded bytes/type/mode"
            )


def _validate_retained_archive(
    retained: dict[str, ArchiveEntry], archived: dict[str, ArchiveEntry]
) -> None:
    for name, expected in retained.items():
        if archived.get(name) != expected:
            raise OSError(
                f"completion archive does not retain the validated artifact: {name}"
            )


def _read_archive(path: Path, ledger: bytes) -> dict[str, ArchiveEntry]:
    # Tar stops at its end marker; consume gzip too so a truncated trailer
    # cannot qualify as verified retention before destructive cleanup.
    with gzip.open(path, "rb") as compressed:
        while compressed.read(1024 * 1024):
            pass
    entries = {}
    manifest = None
    with tarfile.open(path, "r:gz") as archive:
        seen = set()
        for member in archive:
            name = member.name
            parsed = PurePosixPath(name)
            if (
                name in seen
                or not name
                or parsed.is_absolute()
                or ".." in parsed.parts
                or parsed.as_posix() != name
            ):
                raise ValueError(f"unsafe or duplicate archive member: {name}")
            seen.add(name)
            if name == _MANIFEST:
                if not member.isfile():
                    raise ValueError("archive manifest is not a file")
                stream = archive.extractfile(member)
                assert stream is not None
                manifest = json.load(stream)
                continue
            if member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                digest, kind = _digest_stream(stream), "file"
            elif member.isdir():
                digest, kind = hashlib.sha256(b"").hexdigest(), "directory"
            elif member.issym():
                digest, kind = (
                    hashlib.sha256(member.linkname.encode()).hexdigest(),
                    "symlink",
                )
            else:
                raise ValueError(f"unsupported archive member type: {name}")
            entries[name] = ArchiveEntry(kind, digest, member.mode)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "accepted_state_sha256", "entries"}
        or manifest["schema"] != _ARCHIVE_SCHEMA
    ):
        raise ValueError("completion archive has no valid integrity manifest")
    expected = {
        name: {"kind": entry.kind, "sha256": entry.sha256, "mode": entry.mode}
        for name, entry in entries.items()
    }
    ledger_digest = hashlib.sha256(ledger).hexdigest()
    if (
        manifest["entries"] != expected
        or manifest["accepted_state_sha256"] != ledger_digest
        or entries.get("state.yaml", ArchiveEntry("missing", "", 0)).sha256
        != ledger_digest
    ):
        raise ValueError(
            "completion archive member or accepted-ledger integrity mismatch"
        )
    return entries


def _publish_archive(
    root: Path, workspace: Path, path: Path, ledger: bytes
) -> dict[str, ArchiveEntry]:
    _no_symlinks(root, path)
    if path.exists():
        return _read_archive(path, ledger)
    entries = _workspace_entries(workspace)
    if _MANIFEST in entries:
        raise ValueError(
            f"workspace contains reserved archive manifest name: {_MANIFEST}"
        )
    manifest = {
        "schema": _ARCHIVE_SCHEMA,
        "accepted_state_sha256": hashlib.sha256(ledger).hexdigest(),
        "entries": {
            name: {"kind": entry.kind, "sha256": entry.sha256, "mode": entry.mode}
            for name, entry in entries.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=path.parent, prefix=".completion.", suffix=".tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
    try:
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for name, entry in entries.items():
                source = workspace / name
                member = tarfile.TarInfo(name)
                member.mode = entry.mode
                if entry.kind == "directory":
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                elif entry.kind == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = os.readlink(source)
                    archive.addfile(member)
                else:
                    _no_symlinks(root, source)
                    with source.open("rb") as content:
                        member.size = os.fstat(content.fileno()).st_size
                        archive.addfile(member, content)
            data = json.dumps(manifest, sort_keys=True).encode()
            member = tarfile.TarInfo(_MANIFEST)
            member.mode, member.size = 0o644, len(data)
            archive.addfile(member, io.BytesIO(data))
        _read_archive(temporary, ledger)
        _no_symlinks(root, path)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass  # A concurrent create-only publisher must pass the same readback.
        return _read_archive(path, ledger)
    finally:
        temporary.unlink(missing_ok=True)


def _retained_paths(state: StateFile) -> set[str]:
    paths = {"state.yaml"}
    paths.update(retained_attempt_artifact_paths(state.review_assignments.attempts))
    if not state.review_assignments.attempts:
        paths.update(row.artifact for row in state.review_assignments.retained)
        paths.update(
            run.artifact
            for gate in state.gates
            for run in gate.runs
            if run.verdict.get("status") != "error"
        )
    paths.update(retained_evidence_paths(state.verifications))
    if state.completion is not None and state.completion.close_suite is not None:
        paths.add(state.completion.close_suite.log)
    return paths


def retained_attempt_artifact_paths(
    attempts: tuple[ReviewAttempt, ...],
) -> tuple[str, ...]:
    """Select immutable attempt evidence from explicit roles, in stable order."""
    from heddle.kernel.review_assignments import attempt_artifacts

    return tuple(
        reference.path
        for reference in attempt_artifacts(attempts)
        if reference.role in {"canonical", "capture", "evidence", "log"}
    )


def _cleanup_candidates(
    state: StateFile, entries: dict[str, ArchiveEntry]
) -> tuple[set[str], set[str]]:
    retained = _retained_paths(state) | set(RESERVED_AUTHORED_ARTIFACT_PATHS)
    from heddle.kernel.review_assignments import attempt_artifacts

    candidates = {
        reference.path
        for reference in attempt_artifacts(state.review_assignments.attempts)
        if reference.role in {"derived", "temporary"}
        and reference.mode is not None
        and reference.path not in RESERVED_AUTHORED_ARTIFACT_PATHS
    }
    files = {name for name, entry in entries.items() if entry.kind != "directory"}
    candidates &= files - retained
    return candidates, files - retained - candidates


def _cleanup(
    root: Path,
    workspace: Path,
    state: StateFile,
    archive: Path,
    ledger: bytes,
    entries: dict[str, ArchiveEntry],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    current = _workspace_entries(workspace)
    selected, preserved = _cleanup_candidates(state, current)
    candidates = sorted(selected)
    effect: dict[str, Any] = {
        "status": "complete",
        "paths": candidates,
        "preserved": sorted(preserved),
    }
    if not candidates:
        return effect
    ledger_path = workspace / "state.yaml"
    if ledger_path.read_bytes() != ledger:
        raise OSError(f"accepted local ledger changed before cleanup: {ledger_path}")
    try:
        archive_entries = _read_archive(archive, ledger)
    except (OSError, ValueError, EOFError, tarfile.TarError, zlib.error) as error:
        raise CleanupArchiveConflict(
            f"archive no longer matches accepted retention: {archive}: {error}"
        ) from error
    if archive_entries != entries:
        raise CleanupArchiveConflict(
            f"archive no longer matches accepted retention: {archive}"
        )
    from heddle.kernel.review_assignments import attempt_artifacts

    inventory = {
        reference.path: reference
        for reference in attempt_artifacts(state.review_assignments.attempts)
    }
    validated: list[tuple[str, Path]] = []
    for name in candidates:
        path = workspace / name
        _no_symlinks(root, path)
        expected = inventory.get(name)
        observed = _entry(workspace, path)
        if (
            expected is None
            or expected.kind != observed.kind
            or expected.sha256 != observed.sha256
            or expected.mode != observed.mode
            or name not in entries
            or observed != entries[name]
        ):
            raise OSError(
                f"cleanup candidate {path} differs from its indexed and archived "
                f"bytes/type/mode in {archive}; restore the candidate to those "
                "exact bytes/type/mode before retry"
            )
        validated.append((name, path))
    if dry_run:
        effect["status"] = "pending"
        return effect
    removed = []
    for name, path in validated:
        os.unlink(path)
        removed.append(name)
    # Only archived directories made empty by eligible removals are considered.
    directories = {
        parent
        for name in removed
        for parent in (workspace / name).parents
        if parent != workspace and parent.is_relative_to(workspace)
    }
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        if directory.relative_to(workspace).as_posix() in entries:
            _no_symlinks(root, directory)
            if not any(directory.iterdir()):
                directory.rmdir()
    effect["paths"] = removed
    return effect


def completion_result(
    context: ResolvedSnapshotContext, *, dry_run: bool = False, wrote: bool = False
) -> HeddleResult:
    """Observe or repair accepted effects without revisiting acceptance conditions."""
    state = context.snapshot.state
    assert state.completion is not None
    if state.feature_policy is None:
        return HeddleResult.success(
            {
                "feature": state.feature,
                "accepted": True,
                "accepted_at": state.completion.accepted_at,
                "revision": state.revision,
                "wrote": False,
                "dry_run": True,
                "effects": {
                    "historical": {
                        "status": "read-only",
                        "paths": [context.snapshot.workspace],
                    }
                },
            },
            diagnostics=context.diagnostics,
            next_actions=(
                NextAction(
                    ops.ManualAction(
                        f"Use the compatible v6 runtime for accepted {state.feature} "
                        "effect retries; "
                        "this runtime preserves that ledger as read-only history."
                    ),
                    "accepted v6 history is not current completion authority",
                ),
            ),
        )
    root, workspace = context.config.root, context.state_path.parent
    spec, archive = root / state.spec, archive_path(context.config, state.feature)
    workspace_identity = context.snapshot.workspace
    archive_identity = archive.relative_to(root).as_posix()
    retained: dict[str, ArchiveEntry] = {}
    retained_attempts: tuple[ArtifactRef, ...] = ()
    archived: dict[str, ArchiveEntry] | None = None
    binding_status = "pending"
    effects: dict[str, dict[str, Any]] = {
        "stamp": {"status": "pending", "paths": [state.spec]},
        "archive": {
            "status": "pending",
            "paths": [archive.relative_to(root).as_posix()],
        },
        "cleanup": {"status": "pending", "paths": [context.snapshot.workspace]},
    }
    trajectory = (
        {"assessed": False}
        if dry_run
        else archive_trajectory_best_effort(context.config, state.feature)
    )
    effect = effects["stamp"]
    try:
        _no_symlinks(root, workspace)
        with state_recording_lock(context.state_path):
            effect["status"] = _stamp(
                root, spec, state.completion.spec_stamp, dry_run=dry_run
            )
            if effect["status"] == "complete":
                effect = effects["archive"]
                ledger = context.state_path.read_bytes()
                from heddle.kernel.review_assignments import attempt_artifacts

                retained_attempts = attempt_artifacts(state.review_assignments.attempts)
                retained = _retained_evidence_snapshot(
                    root,
                    workspace,
                    state,
                    attempt_references=retained_attempts,
                )
                _no_symlinks(root, archive)
                authored = (
                    _required_authored_archive_inputs(root, workspace)
                    if archive.exists()
                    else _validate_archive_inputs(root, workspace, state)
                )
                if archive.exists() or not dry_run:
                    entries = (
                        _read_archive(archive, ledger)
                        if dry_run
                        else _publish_archive(root, workspace, archive, ledger)
                    )
                    _validate_archive_contract(archive, state, authored, entries)
                    _validate_retained_archive(retained, entries)
                    archived = entries
                    binding_status = "archive-bound"
                    effect["status"] = "complete"
                    effect = effects["cleanup"]
                    effects["cleanup"] = _cleanup(
                        root,
                        workspace,
                        state,
                        archive,
                        ledger,
                        entries,
                        dry_run=dry_run,
                    )
    except (
        OSError,
        ValueError,
        EOFError,
        tarfile.TarError,
        zlib.error,
        KernelError,
    ) as error:
        effect["status"] = "conflict"
        effect["error"] = str(error)
        if effect is effects["archive"] or isinstance(error, CleanupArchiveConflict):
            binding_status = "conflict"
            archived = None
    pending = [
        (name, value)
        for name, value in effects.items()
        if value["status"] != "complete"
    ]
    actions: list[NextAction] = []
    if pending:
        for name, value in pending:
            if not value.get("error") or value["status"] != "conflict":
                continue
            instruction = (
                "Restore every cleanup candidate to its indexed and archived "
                f"bytes/type/mode in {archive}, then retry"
                if name == "cleanup"
                else f"Inspect and repair {', '.join(value['paths'])}: {value['error']}"
            )
            actions.append(
                NextAction(
                    ops.ManualAction(instruction),
                    f"repair accepted {name} effect",
                )
            )
        actions.append(
            NextAction(
                ops.CommandAction(ops.FeatureComplete(feature=state.feature)),
                "retry pending effects of accepted completion",
            )
        )
    retained_evidence = _retained_evidence_report(
        state,
        retained,
        workspace=workspace_identity,
        archive=archive_identity,
        status=binding_status,
        archived=archived,
        attempt_references=retained_attempts,
    )
    return HeddleResult.success(
        {
            "feature": state.feature,
            "accepted": True,
            "accepted_at": state.completion.accepted_at,
            "revision": state.revision,
            "wrote": wrote,
            "dry_run": dry_run,
            "effects": effects,
            "retained_evidence": retained_evidence,
            "trajectory": trajectory,
            "effective_policy": ops.decoded_payload(
                effective_policy(state.feature_policy)
            ),
        },
        diagnostics=(
            *context.diagnostics,
            *_retained_evidence_diagnostics(retained_evidence),
            *(
                Diagnostic(
                    Severity.ADVISORY,
                    f"completion-{name}-{value['status']}",
                    value.get(
                        "error", f"accepted completion has {name} {value['status']}"
                    ),
                    value["paths"][0] if value["paths"] else None,
                )
                for name, value in pending
            ),
        ),
        next_actions=tuple(actions),
        exit_code=ExitCode.ADVISORY if pending else ExitCode.OK,
    )
