from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from heddle.contracts.review_assignments import ArtifactRef
from heddle.gate.results import (
    ReviewResult,
    bind_review_result,
    decode_review_result,
    serialize_review_result,
)
from heddle.gate.types import (
    DiffResult,
    FindingsInfo,
    GateCanonicalReview,
    GateContext,
    GateEngineFailure,
    GateExecutionConfig,
    GateOutcome,
    GateSummary,
    GateType,
    GateUsableReviewCapture,
    PreparedGateRun,
    ResolvedGateInvocation,
    ResolvedGatePrompt,
    ReviewBasis,
)
from heddle.kernel.project_config import KernelError

if TYPE_CHECKING:
    from heddle.contracts.review_assignments import RetainedReview
    from heddle.gate.retained import CapturedReview as CapturedReview
    from heddle.kernel.model import FeatureSnapshot
    from heddle.kernel.project_config import ProjectConfig
    from heddle.kernel.source_manifest import ObservedPath
    from heddle.kernel.state import GateRun

MAX_GATE_ARTIFACT_BYTES = 8 * 1024 * 1024


def artifact_identity(text: str) -> str:
    """Return SHA-256 over the exact UTF-8 bytes recorded for an artifact."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unrecorded_review_captures(
    workspace: Path,
    *,
    recorded_paths: set[str],
    feature: str,
    gate: str,
    cli: str | None,
    rejected_sha256: set[str] | frozenset[str] = frozenset(),
) -> tuple[GateOutcome, ...]:
    """Discover completed receipts after publication/ledger interruption.

    Never reconstruct a receipt from logs or staging files. Every candidate is
    contained, fully decoded and content-address checked before returning it.
    """
    from heddle.gate.retained import MAX_CAPTURE_BYTES, decode_capture

    outcomes = []
    for path in sorted((workspace / "reviews").glob("*.capture.json")):
        relative = path.relative_to(workspace).as_posix()
        if relative in recorded_paths:
            continue
        relative, text = read_contained_utf8_artifact(
            workspace, relative_path=relative, maximum=MAX_CAPTURE_BYTES
        )
        digest = artifact_identity(text)
        if not path.name.endswith(f".{digest}.capture.json"):
            raise _artifact_error("unrecorded capture filename differs from its bytes")
        if digest in rejected_sha256:
            continue  # Renaming a rejected response cannot restore its authority.
        capture = decode_capture(text.encode("utf-8"))
        invocation = capture.invocation
        if invocation.feature != feature or invocation.gate != gate:
            continue
        if cli is not None and invocation.execution.cli != cli:
            continue
        outcomes.append(
            GateUsableReviewCapture(
                capture=capture,
                artifact=ArtifactRef(relative, digest, "capture"),
                structure_warnings=capture.warnings,
            )
        )
    return tuple(outcomes)


def unrecorded_review_results(
    workspace: Path,
    *,
    recorded_paths: set[str],
    feature: str,
    gate: str,
    cli: str | None,
    rejected_sha256: set[str] | frozenset[str] = frozenset(),
) -> tuple[GateOutcome, ...]:
    """Discover canonical completions whose state publication was interrupted."""
    from heddle.gate.validation import validate_review_content

    outcomes = []
    for path in sorted((workspace / "reviews").glob("*.review.json")):
        relative = path.relative_to(workspace).as_posix()
        if relative in recorded_paths:
            continue
        relative, text = read_contained_utf8_artifact(workspace, relative_path=relative)
        digest = artifact_identity(text)
        if not path.name.endswith(f".{digest}.review.json"):
            raise _artifact_error(
                "unrecorded canonical review filename differs from its bytes"
            )
        if digest in rejected_sha256:
            continue
        result = decode_review_result(text.encode("utf-8"))
        invocation = result.invocation
        if invocation.feature != feature or invocation.gate != gate:
            continue
        if cli is not None and invocation.execution.cli != cli:
            continue
        validate_review_content(
            result.content, version=invocation.output_contract_version
        )
        projection = machine_projection_from_result(result)
        outcomes.append(
            GateCanonicalReview(
                result=result,
                artifact=ArtifactRef(relative, digest, "canonical"),
                structure_warnings=tuple(projection["structure_warnings"]),
            )
        )
    return tuple(outcomes)


def read_retained_capture(workspace: Path, retained: RetainedReview) -> CapturedReview:
    """Contained receipt readback, including complete byte and invocation bindings."""
    from heddle.gate.retained import MAX_CAPTURE_BYTES, decode_capture

    _, text = read_contained_utf8_artifact(
        workspace, relative_path=retained.artifact, maximum=MAX_CAPTURE_BYTES
    )
    if artifact_identity(text) != retained.artifact_sha256:
        raise _artifact_error(
            "retained response bytes differ from the recorded capture"
        )
    capture = decode_capture(text.encode("utf-8"))
    invocation = capture.invocation
    if (
        (invocation.assignment_id, invocation.round_number, invocation.reviewer_slot)
        != (retained.assignment_id, retained.round_number, retained.reviewer_slot)
        or invocation.input_hash != retained.input_hash
        or invocation.review_basis_hash != retained.review_basis_hash
        or capture.warnings != retained.warnings
    ):
        raise _artifact_error("retained response invocation or warning binding differs")
    return capture


def interpret_retained_response(
    workspace: Path,
    retained: RetainedReview,
    capture: CapturedReview,
    content: dict[str, Any],
    *,
    publish: bool,
) -> GateOutcome:
    """Validate captured operands, then optionally publish accepted canonical bytes."""
    from heddle.gate.findings import render_review_markdown
    from heddle.gate.io import publish_review_artifact
    from heddle.gate.retained import interpret_capture

    result = interpret_capture(capture, content)
    canonical = serialize_review_result(result)
    artifact = ""
    warnings: tuple[str, ...] = ()
    published_view: Path | None = None
    if publish:
        destination = workspace / retained.artifact
        name = destination.name.removesuffix(".capture.json") + ".interpreted.md"
        path, published_view, warnings = publish_review_artifact(
            destination.with_name(name),
            canonical,
            render_review_markdown(result),
            workspace=workspace,
        )
        artifact, observed = read_contained_utf8_artifact(workspace, produced_path=path)
        if observed.encode("utf-8") != canonical:
            raise _artifact_error("interpreted canonical publication readback differs")
    canonical_ref = ArtifactRef(
        artifact,
        hashlib.sha256(canonical).hexdigest(),
        "canonical",
        mode=_artifact_mode_for_workspace(workspace, artifact),
    )
    emitted: tuple[ArtifactRef, ...] = (canonical_ref,)
    if published_view is not None:
        emitted = (*emitted, _artifact_reference(workspace, published_view, "derived"))
    return GateCanonicalReview(
        result=result,
        artifact=canonical_ref,
        structure_warnings=(*capture.warnings, *warnings),
        artifacts=emitted,
    )


def read_contained_utf8_artifact(
    workspace_dir: Path,
    *,
    relative_path: str | None = None,
    produced_path: Path | None = None,
    maximum: int = MAX_GATE_ARTIFACT_BYTES,
) -> tuple[str, str]:
    """Read one workspace-contained regular artifact through descriptors.

    Exactly one locator is accepted. The trusted workspace anchor may itself
    resolve through the host path, but every descendant is opened relative to
    an already-open directory with no-follow semantics. Returns
    ``(workspace_relative_path, utf8_text)`` without reopening a checked path.
    """
    if (relative_path is None) == (produced_path is None):
        raise _artifact_error(
            "artifact read requires exactly one relative or produced path"
        )
    _require_descriptor_capabilities()
    if produced_path is not None:
        workspace_absolute = Path(os.path.abspath(workspace_dir))
        produced_absolute = Path(os.path.abspath(produced_path))
        try:
            relative_path = produced_absolute.relative_to(workspace_absolute).as_posix()
        except ValueError as error:
            raise _artifact_error(
                f"gate artifact is outside the feature workspace: {produced_path}"
            ) from error
    assert relative_path is not None
    parts = _artifact_parts(relative_path)
    descriptors: list[int] = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        current = os.open(workspace_dir, directory_flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(
                part,
                directory_flags | os.O_NOFOLLOW,
                dir_fd=current,
            )
            descriptors.append(current)
        artifact_fd = os.open(parts[-1], file_flags, dir_fd=current)
        descriptors.append(artifact_fd)
        before = os.fstat(artifact_fd)
        if not stat.S_ISREG(before.st_mode):
            raise _artifact_error(
                f"gate artifact is not a regular file: {relative_path}"
            )
        if before.st_size > maximum:
            raise _artifact_error(
                f"gate artifact exceeds the {maximum}-byte read limit: "
                f"{relative_path} ({before.st_size} bytes)"
            )
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(artifact_fd, 1024 * 1024):
            total += len(chunk)
            if total > maximum:
                raise _artifact_error(
                    f"gate artifact exceeded the {maximum}-byte read limit while being "
                    f"read: {relative_path}"
                )
            chunks.append(chunk)
        after = os.fstat(artifact_fd)
        payload = b"".join(chunks)
        if (
            len(payload) != before.st_size
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or after.st_mode != before.st_mode
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
        ):
            raise _artifact_error(
                f"gate artifact changed while being read: {relative_path}"
            )
        text = payload.decode("utf-8")
    except KernelError:
        raise
    except (OSError, UnicodeDecodeError) as error:
        raise _artifact_error(
            f"gate artifact could not be read ({relative_path}): {error}"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)
    return relative_path, text


def _require_descriptor_capabilities() -> None:
    required = (
        hasattr(os, "O_NOFOLLOW"),
        hasattr(os, "O_DIRECTORY"),
        os.open in os.supports_dir_fd,
    )
    if not all(required):
        raise _artifact_error(
            "descriptor-relative no-follow artifact reads are unsupported"
        )


def _artifact_parts(relative_path: str) -> tuple[str, ...]:
    if not relative_path or "\0" in relative_path:
        raise _artifact_error(f"gate artifact path is invalid: {relative_path!r}")
    parts = tuple(relative_path.split("/"))
    if relative_path.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise _artifact_error(
            f"gate artifact path is not normalized and contained: {relative_path!r}"
        )
    return parts


def _artifact_error(message: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=message,
        hint="restore a contained regular UTF-8 review artifact and rerun the gate",
    )


def build_gate_context(
    snapshot: FeatureSnapshot,
    config: ProjectConfig,
    gate_type: GateType,
    cli: str = "claude",
    *,
    milestone_id: str | None = None,
    basis_only: bool = False,
    source_observations: dict[str, ObservedPath] | None = None,
) -> GateContext:
    """Build the engine ``GateContext`` from a kernel ``FeatureSnapshot`` — the
    runtime seam. The runtime reaches the engine through this single
    sanctioned ``heddle.gate.entry`` module; the context is built from
    the kernel snapshot, never the engine's legacy frontmatter parsers.
    ``cli`` scopes the disposition rerun ledger to the invoking lane. A
    second-suite first review must not be framed as a rerun of
    the other lane's completed review."""
    from heddle.gate.context import build_context_from_snapshot

    return build_context_from_snapshot(
        snapshot,
        config,
        gate_type,
        cli=cli,
        milestone_id=milestone_id,
        basis_only=basis_only,
        source_observations=source_observations,
    )


def build_gate_lock_context(
    snapshot: FeatureSnapshot,
    config: ProjectConfig,
    gate_type: GateType,
    *,
    milestone_id: str | None = None,
) -> GateContext:
    """Resolve only the snapshot metadata needed to acquire the execution lock."""
    from heddle.gate.context import build_lock_context_from_snapshot

    return build_lock_context_from_snapshot(
        snapshot, config, gate_type, milestone_id=milestone_id
    )


def featureless_gate_context(gate_type: GateType, repo_root: Path) -> GateContext:
    """Build the fact-free context used only for guidelines preview."""
    from heddle.gate.types import GateContext

    return GateContext(
        gate_type=gate_type,
        feature="[feature]",
        repo_root=repo_root,
        plan_path=Path("[plan-path]"),
        spec_path=Path("[spec-path]"),
        milestone="[milestone]",
    )


def prepare_current_review_basis(
    context: GateContext, invocation: ResolvedGateInvocation
) -> ReviewBasis:
    """Observe the stable basis without preparing rerun or producer history."""
    from heddle.gate.preparation import prepare_review_basis

    with _resolved_gate_sources(
        context, context.gate_type, invocation.exec_config.cli, basis_only=True
    ) as (
        prompt,
        diff,
    ):
        return prepare_review_basis(
            context,
            gate_type=context.gate_type,
            invocation=invocation,
            prompt=prompt,
            diff=diff,
        )


def validate_current_review_fields(result: ReviewResult, basis: ReviewBasis) -> None:
    """Validate role fields against their matching current source basis."""
    from heddle.gate.validation import validate_review_fields

    try:
        if (
            result.invocation.output_contract_version,
            result.invocation.output_contract_sha256,
        ) != (basis.output_contract_version, basis.output_contract_sha256):
            raise ValueError(
                "canonical output contract differs from its matching current basis"
            )
        validate_review_fields(result.content, basis)
    except ValueError as error:
        raise _artifact_error(
            f"canonical review has invalid role content: {error}"
        ) from error


def prepare_gate_run(
    context: GateContext,
    *,
    gate_type: GateType,
    exec_config: GateExecutionConfig | None = None,
    invocation: ResolvedGateInvocation | None = None,
) -> PreparedGateRun:
    """Prepare the exact immutable semantic input for one gate lane."""
    from heddle.gate.preparation import prepare_gate_run as _prepare

    if invocation is None:
        if exec_config is None:
            raise TypeError("prepare_gate_run requires an invocation or exec_config")
        from heddle.gate.types import ResolvedGateInvocation

        invocation = ResolvedGateInvocation(exec_config, frozenset())
    elif exec_config is not None and exec_config != invocation.exec_config:
        raise KernelError(
            code="workspace-invalid",
            message="prepared invocation does not match its execution configuration",
            hint="prepare the selected gate again",
        )
    exec_config = invocation.exec_config
    with _resolved_gate_sources(context, gate_type, exec_config.cli) as (prompt, diff):
        return _prepare(
            context,
            gate_type=gate_type,
            prompt=prompt,
            diff=diff,
            invocation=invocation,
        )


@contextmanager
def _resolved_gate_sources(
    context: GateContext,
    gate_type: GateType,
    cli: str,
    *,
    basis_only: bool = False,
) -> Iterator[tuple[ResolvedGatePrompt, DiffResult]]:
    """Resolve the declared diff/resources for full and basis-only preparation."""
    from heddle.gate.diff import resolve_diff
    from heddle.gate.preparation import gate_requires_diff_resolution
    from heddle.gate.types import DiffResult

    baseline_probe = None
    if gate_type.requires_milestone and context.workspace_dir is not None:
        baseline_probe = (
            (context.workspace_dir / "state.yaml")
            .resolve()
            .relative_to(context.repo_root.resolve())
            .as_posix()
        )
    with tempfile.TemporaryDirectory(prefix="heddle-gate-prepare-") as temp:
        if gate_requires_diff_resolution(gate_type):
            diff = resolve_diff(
                None,
                context.repo_root,
                Path(temp),
                owned_paths=(
                    context.owned_paths if baseline_probe is not None else None
                ),
                feature_baseline=context.source_baseline,
                feature_baseline_probe=baseline_probe,
            )
        else:
            empty_diff = Path(temp) / "not-required.diff"
            empty_diff.write_bytes(b"")
            diff = DiffResult("none", "", empty_diff, ())
        from heddle.gate.prompt import resolve_gate_prompt

        prompt = resolve_gate_prompt(
            context,
            gate_type,
            cli=cli,
            basis_only=basis_only,
        )
        yield prompt, diff


def machine_projection_from_result(result: ReviewResult) -> dict[str, Any]:
    from heddle.gate.findings import (
        structured_details,
        structured_findings,
        structured_verdict,
    )

    findings = structured_findings(result.content)
    verdict = structured_verdict(result.content)
    return {
        "status": verdict.status if verdict is not None else None,
        "verdict": {
            "status": verdict.status if verdict is not None else None,
            "rerun_recommended": verdict.rerun_recommended
            if verdict is not None
            else None,
        },
        "findings": _findings_dict(findings),
        "severity_classification": _severity_classification_dict(findings),
        "report_findings": [
            {
                "id": item.id,
                "title": item.title,
                "severity": item.severity,
                "recommendation": item.recommendation,
                **({"route": item.route} if item.route is not None else {}),
            }
            for item in structured_details(result.content)
            if item.classification == "report"
        ],
        "structure_warnings": [],
    }


def retained_review_result(
    artifact_text: str,
    run: GateRun,
    *,
    feature: str,
    gate: str,
    scope: str,
    prepared: PreparedGateRun | None = None,
) -> ReviewResult:
    """Validate retained canonical content and its accepted event bindings."""
    from heddle.gate.validation import validate_review_content

    try:
        result = decode_review_result(artifact_text.encode("utf-8"))
        validate_review_content(
            result.content, version=result.invocation.output_contract_version
        )
        binding = result.invocation
        if (
            (binding.feature, binding.gate, binding.scope) != (feature, gate, scope)
            or binding.execution.cli != run.cli
            or asdict(binding.execution) != dict(run.execution_config or {})
            or binding.review_basis_hash != run.review_basis_hash
            or binding.input_hash != run.input_hash
            or binding.review_policy_id != run.review_policy_id
            or binding.prompt_version != run.prompt_version
            or binding.effective_prompt_sha256 != run.effective_prompt_sha256
            or binding.assignment_id != run.assignment_id
            or binding.round_number != run.round_number
            or binding.reviewer_slot != run.reviewer_slot
        ):
            raise ValueError(
                "canonical invocation binding differs from its accepted event"
            )
        projection = machine_projection_from_result(result)
        expected_reports = [
            {
                "finding_id": item["id"],
                "title": item["title"],
                "severity": item["severity"],
            }
            for item in projection["report_findings"]
        ]
        if (
            projection["findings"] != dict(run.findings)
            or expected_reports != [asdict(item) for item in run.report_findings]
            or projection["verdict"] != dict(run.verdict)
        ):
            raise ValueError(
                "canonical review projection differs from its accepted event"
            )
        if prepared is not None:
            validate_review_replay(result, prepared, feature)
        return result
    except ValueError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"retained canonical review {run.artifact} is invalid: {error}",
            hint=(
                "restore the recorded canonical JSON and its exact bindings "
                "before reviewing again; generated views cannot repair authority"
            ),
        ) from error


def validate_review_replay(
    result: ReviewResult, prepared: PreparedGateRun, feature: str
) -> None:
    from heddle.gate.validation import validate_review

    if (
        result.invocation
        != bind_review_result(result.content, prepared, feature).invocation
    ):
        raise ValueError(
            "canonical invocation binding differs from the exact prepared replay"
        )
    validate_review(result.content, prepared)


@contextmanager
def gate_lock_for_runtime(
    context: GateContext, invocation: ResolvedGateInvocation
) -> Iterator[Path]:
    """Expose one prepared lane's execution lock to the runtime seam."""
    from heddle.gate.io import gate_lock

    path = gate_lock_path_for_runtime(context, invocation)
    with gate_lock(path):
        yield path


def gate_lock_path_for_runtime(
    context: GateContext, invocation: ResolvedGateInvocation
) -> Path:
    """Return the lock path for the context's current mutable identity."""
    from heddle.gate.cli import GateArgs
    from heddle.gate.io import build_gate_lock_path

    args = GateArgs(
        gate_name=context.gate_type.name,
        cli=invocation.exec_config.cli,
        feature=context.feature,
        plan_path=context.plan_path,
        spec_path=context.spec_path,
    )
    return build_gate_lock_path(context, args)


def run_gate_for_runtime(
    gate_type: GateType,
    context: GateContext,
    *,
    feature: str | None = None,
    progress: Callable[[str], None] | None = None,
    iteration: int = 1,
    max_iterations: int | None = 5,
) -> GateOutcome:
    """Execute under runtime's gate lock and return a validated outcome.

    The caller retains the gate/scope lock through recording. The engine has
    no runtime writer, callback or state-lock responsibility.
    """
    from heddle.gate.cli import GateArgs

    prepared = context.prepared_run
    if prepared is None:
        raise KernelError(
            code="workspace-invalid",
            message="gate execution requires a prepared invocation",
            hint="prepare the selected gate again",
        )
    args = GateArgs(
        gate_name=gate_type.name,
        cli=prepared.invocation.exec_config.cli,
        feature=feature,
        plan_path=context.plan_path,
        spec_path=context.spec_path,
        iteration=iteration,
        max_iterations=max_iterations,
        preserve_success_artifacts=True,
    )
    from heddle.gate.runner import GateRunner

    # Truncated-output retry + attempt archival, re-anchored here from the
    # deleted legacy CLI entry (AC-11 — `entry.main` was the sole
    # production caller): a truncated review re-runs while attempts remain,
    # with each failed attempt's artifacts archived beside the output.
    for attempt in range(1, args.max_attempts + 1):
        runner = GateRunner(gate_type, args, context=context, progress=progress)
        summary, gate_exit = runner.run()
        if summary.reason != "truncated-output" or attempt >= args.max_attempts:
            break
        _archive_attempt(summary, attempt)

    output_path = Path(summary.io.output) if summary.io.output else None
    if gate_exit == 0 and summary.retained_review is not None:
        from heddle.gate.retained import serialize_capture

        primary_path = _workspace_relpath(output_path, context)
        primary = ArtifactRef(
            primary_path,
            hashlib.sha256(serialize_capture(summary.retained_review)).hexdigest(),
            "capture",
            mode=_artifact_mode(context, primary_path),
        )
        artifacts = _emitted_gate_artifacts(context, summary, primary)
        return GateUsableReviewCapture(
            capture=summary.retained_review,
            artifact=primary,
            structure_warnings=tuple(summary.output_contract.structure_warnings),
            artifacts=artifacts,
        )
    report_findings: list[dict] = []
    artifact_sha256: str | None
    if gate_exit == 0:
        try:
            if output_path is None:
                raise _artifact_error("clean gate completion has no review artifact")
            if summary.review_result is None:
                raise _artifact_error(
                    "native completion is missing its validated review result"
                )
            artifact_relpath = _workspace_relpath(output_path, context)
            artifact_sha256 = hashlib.sha256(
                serialize_review_result(summary.review_result)
            ).hexdigest()
            projection = machine_projection_from_result(summary.review_result)
            findings = projection["findings"]
            report_findings = projection["report_findings"]
            structure_warnings = [
                *projection["structure_warnings"],
                *summary.output_contract.structure_warnings,
            ]
            expected_reports = findings["by_classification"].get("report")
            if type(expected_reports) is not int or expected_reports < 0:
                raise _artifact_error(
                    "gate REPORT aggregate is not a nonnegative exact integer"
                )
            if len(report_findings) != expected_reports:
                raise _artifact_error(
                    "gate REPORT projection is incomplete: aggregate reports "
                    f"{expected_reports}, projected {len(report_findings)}"
                )
        except (KernelError, OSError, UnicodeDecodeError, MemoryError) as error:
            from heddle.gate.summary import write_summary

            detail = error.message if isinstance(error, KernelError) else str(error)
            summary.status = "failure"
            summary.reason = f"invalid-output-structure: {detail}"
            summary.gate_exit = gate_exit = 1
            summary.findings = summary.verdict = None
            summary.findings_note = summary.verdict_note = (
                "unavailable: final artifact validation failed"
            )
            summary.output_contract.structure_errors.append(detail)
            report_findings = []
            try:
                write_summary(Path(summary.io.summary), summary)
            except OSError as publication_error:
                # The error outcome remains the recording input even if its
                # diagnostic file cannot be refreshed. Keep both causes there.
                summary.reason += (
                    f"; could not publish gate summary {summary.io.summary}: "
                    f"{publication_error}"
                )
    if gate_exit != 0:
        artifact_sha256 = None
        rerun = (
            summary.verdict.rerun_recommended if summary.verdict is not None else None
        )
        findings = _findings_dict(summary.findings)
        artifact_relpath = _workspace_relpath(output_path, context)
        output_contract = getattr(summary, "output_contract", None)
        structure_warnings = list(getattr(output_contract, "structure_warnings", ()))

    if gate_exit == 0:
        assert summary.review_result is not None and artifact_sha256 is not None
        primary = ArtifactRef(
            artifact_relpath,
            artifact_sha256,
            "canonical",
            mode=_artifact_mode(context, artifact_relpath),
        )
        return GateCanonicalReview(
            result=summary.review_result,
            artifact=primary,
            structure_warnings=tuple(structure_warnings),
            artifacts=_emitted_gate_artifacts(context, summary, primary),
        )
    failure_artifacts = _emitted_gate_artifacts(context, summary, None)
    return GateEngineFailure(
        gate_exit=gate_exit,
        reason=str(summary.reason or "execution-failure"),
        rerun_recommended=rerun,
        structure_warnings=tuple(structure_warnings),
        artifacts=failure_artifacts,
    )


def _findings_dict(findings: FindingsInfo | None) -> dict[str, Any]:
    """The run-fact ``findings`` projection of the engine ``FindingsInfo``
    (drops ``by_severity_classification`` — the run fact carries the four §6.2
    keys)."""
    if findings is None:
        return {
            "by_severity": {"critical": 0, "important": 0, "minor": 0},
            "by_classification": {
                "implement": 0,
                "report": 0,
                "ignore": 0,
                "unknown": 0,
            },
            "total": 0,
            "contradictions": 0,
        }
    return {
        "by_severity": dict(findings.by_severity),
        "by_classification": dict(findings.by_classification),
        "total": findings.total,
        "contradictions": findings.contradictions,
    }


def _severity_classification_dict(findings: FindingsInfo | None) -> dict[str, int]:
    """The engine's joint finding counts for runtime policy decisions only."""
    if findings is None:
        return {
            f"{severity}/{classification}": 0
            for severity in ("critical", "important", "minor")
            for classification in ("implement", "report", "ignore", "unknown")
        }
    return dict(findings.by_severity_classification)


def _workspace_relpath(output_path: Path | None, context: GateContext) -> str:
    if output_path is None:
        return ""
    # E1: the snapshot-declared workspace is authoritative — plan_path is None
    # for gates that run before plan.md exists (spec-review), and its parent
    # only coincides with the workspace on the legacy layout.
    workspace = context.workspace_dir
    if workspace is None and context.plan_path is not None:
        workspace = context.plan_path.parent
    if workspace is not None:
        try:
            return output_path.resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            pass
    return output_path.name


def _workspace_dir(context: GateContext) -> Path:
    workspace = context.workspace_dir
    if workspace is None and context.plan_path is not None:
        workspace = context.plan_path.parent
    if workspace is None:
        raise _artifact_error("gate context has no feature workspace")
    return Path(workspace)


def _artifact_mode(context: GateContext, relative: str) -> int | None:
    if not relative:
        return None
    path = _workspace_dir(context) / relative
    try:
        metadata = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return stat.S_IMODE(metadata.st_mode)


def _artifact_mode_for_workspace(workspace: Path, relative: str) -> int | None:
    path = workspace / relative
    try:
        metadata = path.lstat()
    except OSError:
        return None
    return stat.S_IMODE(metadata.st_mode) if stat.S_ISREG(metadata.st_mode) else None


def _artifact_reference(workspace: Path, path: Path, role: str) -> ArtifactRef:
    relative, text = read_contained_utf8_artifact(workspace, produced_path=path)
    return ArtifactRef(
        relative,
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        cast(Any, role),
        mode=_artifact_mode_for_workspace(workspace, relative),
    )


def _emitted_gate_artifacts(
    context: GateContext,
    summary: GateSummary,
    primary: ArtifactRef | None,
) -> tuple[ArtifactRef, ...]:
    """Inventory runtime-produced files from concrete GatePaths, never suffix scans."""
    workspace = _workspace_dir(context)
    candidates: list[tuple[Path, str]] = []
    if primary is not None:
        candidates.append((workspace / primary.path, primary.role))
    produced = summary.artifacts
    candidates.extend((Path(path), "derived") for path in produced.published_views)
    if produced.log:
        rolling_log = Path(produced.log)
        _, log_text = read_contained_utf8_artifact(workspace, produced_path=rolling_log)
        from heddle.gate.io import publish_content_addressed_log

        immutable_log = publish_content_addressed_log(
            rolling_log, log_text.encode("utf-8"), workspace=workspace
        )
        candidates.append((immutable_log, "log"))
    for raw, role in (
        (produced.output, "temporary"),
        (produced.prompt, "temporary"),
        (produced.raw_out, "temporary"),
        (produced.last_message, "temporary"),
        (produced.json_message, "temporary"),
        (produced.command_output, "temporary"),
        (produced.events_jsonl, "temporary"),
    ):
        if raw:
            candidates.append((Path(raw), role))
    inventory: list[ArtifactRef] = []
    by_path: dict[str, ArtifactRef] = {}
    for path, role in candidates:
        if not path.exists():
            continue
        try:
            relative = (
                path.resolve(strict=True)
                .relative_to(workspace.resolve(strict=True))
                .as_posix()
            )
        except (OSError, ValueError) as error:
            raise _artifact_error(
                f"emitted artifact is outside its workspace: {path}"
            ) from error
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise _artifact_error(f"emitted artifact is not a regular file: {relative}")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                content = stream.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        reference = ArtifactRef(
            relative,
            hashlib.sha256(content).hexdigest(),
            cast(Any, role),
            mode=stat.S_IMODE(metadata.st_mode),
        )
        previous = by_path.get(relative)
        if previous is not None:
            if previous.sha256 != reference.sha256:
                raise _artifact_error(f"emitted artifact identity changed: {relative}")
            continue
        by_path[relative] = reference
        inventory.append(reference)
    return tuple(inventory)


def _archive_attempt(summary: GateSummary, attempt: int) -> None:
    output = summary.io.output
    log = summary.io.log
    if output:
        source = summary.io.output
        target = source.removesuffix(".md") + f".attempt{attempt}.md"
        try:
            shutil.copyfile(source, target)
        except OSError:
            # Best-effort: archiving a truncated attempt's artifacts must never
            # fail the run. A missing source or
            # any other I/O error (ENOSPC/EACCES/EDQUOT) is swallowed so the
            # retry loop proceeds and the run fact is still recorded downstream
            # — this call sits outside GateRunner.run()'s own catch-all.
            pass
    if log:
        target = log.removesuffix(".log") + f".attempt{attempt}.log"
        try:
            shutil.copyfile(log, target)
        except OSError:
            pass


def execution_config_projection(
    invocation: ResolvedGateInvocation,
) -> dict[str, Any]:
    """Return the stable four-axis execution projection."""
    return asdict(invocation.exec_config)


def execution_projection(invocation: ResolvedGateInvocation) -> dict[str, Any]:
    """Return public execution evidence, including explicit overrides."""
    return {
        **execution_config_projection(invocation),
        "overridden_axes": sorted(invocation.explicit_axes),
    }


def prompt_identity_projection(
    *,
    prompt_version: str | None,
    effective_prompt_sha256: str | None,
    review_basis_hash: str | None = None,
    input_hash: str | None = None,
    review_policy_id: str | None = None,
    output_contract_version: str | None = None,
    output_contract_sha256: str | None = None,
) -> dict[str, str | None]:
    """Return the stable public prompt and run identity projection."""
    return {
        "prompt_version": prompt_version,
        "effective_prompt_sha256": effective_prompt_sha256,
        "review_basis_hash": review_basis_hash,
        "input_hash": input_hash,
        **{
            name: value
            for name, value in (
                ("review_policy_id", review_policy_id),
                ("output_contract_version", output_contract_version),
                ("output_contract_sha256", output_contract_sha256),
            )
            if value is not None
        },
    }
