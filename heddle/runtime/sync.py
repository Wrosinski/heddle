"""Managed projections for feature status and session entry.

Two managed regions are projected: plan-status in the feature plan and
session-entry in the root AGENTS.md."""

from __future__ import annotations

import errno
import os
import stat
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.kernel import managed_regions as _managed_regions
from heddle.kernel.model import (
    FeatureSnapshot,
    derive_next_actions,
    is_terminal,
    resolve_snapshot,
)
from heddle.kernel.project_config import KernelError, ProjectConfig
from heddle.runtime.diagnostics import kernel_error_result
from heddle.runtime.feature_context import (
    ResolveFeatureFailure,
    resolve_snapshot_from_cwd,
)
from heddle.runtime.output import emit_envelope

SESSION_ENTRY_ID = "session-entry"

_SYNC_EXITS = {
    "feature-ambiguous": ExitCode.USAGE,
    "usage": ExitCode.USAGE,
    "workspace-invalid": ExitCode.FATAL,
}
_UNSUPPORTED_HARD_LINK_ERRNOS = frozenset(
    {
        errno.EPERM,
        errno.EXDEV,
        errno.EOPNOTSUPP,
        errno.ENOTSUP,
    }
)


@dataclass(frozen=True)
class _SyncArgs:
    feature: str | None
    dry_run: bool


@dataclass(frozen=True)
class _Projection:
    block: str
    display_path: str
    path: Path
    before: str | None
    text: str

    @property
    def create(self) -> bool:
        return self.before is None

    @property
    def action(self) -> str:
        if self.create:
            return "created"
        return "unchanged" if self.text == self.before else "updated"


def _safe_inline(field: str, value: str) -> str:
    if "\r" in value or "\n" in value:
        raise KernelError(
            code="workspace-invalid",
            message=f"plan-status {field} field contains CR or LF",
            hint=f"repair the source state to remove CR and LF from {field}",
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"plan-status {field} field is not valid UTF-8 text",
            hint=(f"repair the source state to remove invalid Unicode from {field}"),
        ) from error
    return value


def render_plan_status(
    snapshot: FeatureSnapshot,
    next_actions: tuple[NextAction, ...],
) -> str:
    """Render the deterministic LF-terminated plan-status body."""
    feature = _safe_inline("feature", snapshot.feature)
    stage = _safe_inline("stage", snapshot.stage)
    authorized = _safe_inline("authorized", snapshot.authorized_through)
    milestone = _safe_inline(
        "milestone",
        snapshot.current_milestone.id if snapshot.current_milestone else "none",
    )
    conditions = "none"
    if snapshot.blocking_conditions:
        conditions = ", ".join(
            _safe_inline("condition", condition)
            for condition in snapshot.blocking_conditions
        )
    next_command = _safe_inline(
        "next", next_actions[0].command if next_actions else "none"
    )
    return (
        "\nGenerated status snapshot — not live. Operational facts live in\n"
        "`state.yaml`, which is authoritative; refresh this block with\n"
        f"`heddle sync --feature {feature}`.\n\n"
        f"- feature: {feature}\n"
        f"- stage: {stage} (authorized through {authorized})\n"
        f"- milestone: {milestone}\n"
        f"- conditions: {conditions}\n"
        f"- next: {next_command}\n\n"
    )


def render_session_entry() -> str:
    """Render the constant LF-terminated session-entry body."""
    return (
        "\nThis repository's feature workflow runs through the `heddle` CLI.\n\n"
        "To enter a feature-work session:\n\n"
        "1. Run `heddle orient`.\n"
        "2. Follow the `next_actions` it returns.\n"
        "3. Run `heddle kickoff` only when `orient` routes there.\n\n"
    )


def install_projection(path: Path, text: str, *, create: bool) -> None:
    """Install a projection by atomic update or exclusive creation."""
    descriptor: int | None = None
    temp_path: Path | None = None
    pending_error: KernelError | None = None
    cleanup_error: OSError | None = None
    try:
        try:
            descriptor, raw_temp_path = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(raw_temp_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as sink:
                descriptor = None
                sink.write(text)
            if create:
                try:
                    os.link(temp_path, path)
                except FileExistsError as error:
                    raise KernelError(
                        code="workspace-invalid",
                        message=f"projection target appeared during install: {path}",
                        hint=(
                            "re-run heddle sync — another writer created the "
                            "target after preflight"
                        ),
                    ) from error
                except OSError as error:
                    if error.errno in _UNSUPPORTED_HARD_LINK_ERRNOS:
                        raise KernelError(
                            code="workspace-invalid",
                            message=(
                                "projection target filesystem does not support "
                                "the required atomic exclusive hard-link "
                                f"creation: {path}: {type(error).__name__}: {error}"
                            ),
                            hint=(
                                "move the workspace to a filesystem supporting "
                                "same-filesystem hard links, then re-run heddle sync"
                            ),
                        ) from error
                    raise
                try:
                    temp_path.unlink()
                except OSError as error:
                    raise KernelError(
                        code="workspace-invalid",
                        message=(
                            f"projection target was created but staging cleanup "
                            f"failed: {path}; residual staging path: {temp_path}"
                        ),
                        hint=(
                            f"remove {temp_path}, verify {path}, then re-run "
                            "heddle sync"
                        ),
                    ) from error
                temp_path = None
            else:
                os.replace(temp_path, path)
                temp_path = None
        except KernelError as error:
            pending_error = error
        except (OSError, UnicodeError) as error:
            pending_error = KernelError(
                code="workspace-invalid",
                message=f"projection target is unwritable: {path}: {error}",
                hint=f"check that {path} is writable, then re-run heddle sync",
            )
            pending_error.__cause__ = error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as error:
                cleanup_error = error

    if pending_error is not None:
        if cleanup_error is not None and temp_path is not None:
            raise KernelError(
                code=pending_error.code,
                message=(
                    f"{pending_error.message}; staging cleanup also failed; "
                    f"residual staging path: {temp_path}: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                ),
                hint=(
                    f"{pending_error.hint}; remove {temp_path}, verify {path}, "
                    "then re-run heddle sync"
                ),
            ) from pending_error
        raise pending_error
    if cleanup_error is not None and temp_path is not None:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"projection staging cleanup failed for {path}; residual staging "
                f"path: {temp_path}: {type(cleanup_error).__name__}: {cleanup_error}"
            ),
            hint=f"remove {temp_path}, verify {path}, then re-run heddle sync",
        ) from cleanup_error


def _parse_sync_args(args: list[str]) -> _SyncArgs | HeddleResult:
    explicit: str | None = None
    dry_run = False
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--feature":
            if explicit is not None:
                return _usage_failure(
                    "--feature may be provided only once",
                    "pass exactly one target: heddle sync --feature <slug>",
                )
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                return _usage_failure(
                    "--feature requires a feature slug",
                    "usage: heddle sync [--feature <slug>] [--dry-run] [--json]",
                )
            explicit = args[index + 1]
            index += 2
            continue
        if token == "--dry-run":
            dry_run = True
            index += 1
            continue
        return _usage_failure(
            f"unrecognized argument {token!r}",
            "usage: heddle sync [--feature <slug>] [--dry-run] [--json]",
        )
    return _SyncArgs(feature=explicit, dry_run=dry_run)


def _usage_failure(message: str, hint: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="usage", message=message, hint=hint),
        exit_code=ExitCode.USAGE,
    )


def _path_error(display_path: str, error: KernelError) -> KernelError:
    return KernelError(
        code=error.code,
        message=f"{display_path}: {error.message}",
        hint=error.hint,
        details=error.details,
        candidates=error.candidates,
        reason=error.reason,
    )


def _target_lstat(path: Path, display_path: str) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"{display_path}: projection target cannot be inspected: "
                f"{type(error).__name__}: {error}"
            ),
            hint=f"make {display_path} accessible, then re-run heddle sync",
        ) from error


def _read_regular_utf8(path: Path, display_path: str) -> str:
    target_stat = _target_lstat(path, display_path)
    if target_stat is None:
        raise KernelError(
            code="workspace-invalid",
            message=f"{display_path}: projection target is absent",
            hint=f"restore {display_path} or repair the feature workspace, then retry",
        )
    if stat.S_ISLNK(target_stat.st_mode):
        raise KernelError(
            code="workspace-invalid",
            message=f"{display_path}: projection target is a symlink",
            hint=(
                f"move the symlink aside and restore {display_path} as a readable "
                "UTF-8 regular file"
            ),
        )
    if not stat.S_ISREG(target_stat.st_mode):
        raise KernelError(
            code="workspace-invalid",
            message=f"{display_path}: projection target is not a regular file",
            hint=f"move it aside and restore {display_path} as a regular file",
        )
    try:
        return path.read_text(encoding="utf-8", newline="")
    except UnicodeDecodeError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"{display_path}: projection target is not valid UTF-8",
            hint=f"repair {display_path} as valid UTF-8, then retry",
        ) from error
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"{display_path}: projection target is unreadable",
            hint=f"make {display_path} readable, then retry",
        ) from error


def _preflight_existing(
    *,
    block: str,
    display_path: str,
    path: Path,
    body: str,
) -> _Projection:
    before = _read_regular_utf8(path, display_path)
    try:
        projected = _managed_regions.replace_managed_region(before, block, body)
    except KernelError as error:
        raise _path_error(display_path, error) from error
    return _Projection(
        block=block,
        display_path=display_path,
        path=path,
        before=before,
        text=projected,
    )


def _preflight_agents(path: Path, body: str) -> _Projection:
    display_path = "AGENTS.md"
    if _target_lstat(path, display_path) is None:
        text = (
            f"{_managed_regions.begin_marker(SESSION_ENTRY_ID)}\n"
            f"{body}{_managed_regions.end_marker(SESSION_ENTRY_ID)}\n"
        )
        return _Projection(
            block=SESSION_ENTRY_ID,
            display_path=display_path,
            path=path,
            before=None,
            text=text,
        )
    return _preflight_existing(
        block=SESSION_ENTRY_ID,
        display_path=display_path,
        path=path,
        body=body,
    )


def _preflight_targets(
    *,
    plan_path: Path,
    plan_display: str,
    plan_body: str,
    agents_path: Path,
    session_body: str,
) -> tuple[tuple[_Projection, ...], tuple[KernelError, ...]]:
    projections: list[_Projection] = []
    errors: list[KernelError] = []
    try:
        projections.append(
            _preflight_existing(
                block=_managed_regions.PLAN_STATUS_ID,
                display_path=plan_display,
                path=plan_path,
                body=plan_body,
            )
        )
    except KernelError as error:
        errors.append(error)
    try:
        projections.append(_preflight_agents(agents_path, session_body))
    except KernelError as error:
        errors.append(error)
    return tuple(projections), tuple(errors)


def _fatal_diagnostic(error: KernelError) -> Diagnostic:
    return Diagnostic(
        severity=Severity.FATAL,
        code=error.code,
        message=f"{error.message}; hint: {error.hint}",
    )


def _kernel_failure(
    error: KernelError,
    *,
    diagnostics: tuple[Diagnostic, ...],
) -> HeddleResult:
    return kernel_error_result(
        error,
        exit_codes=_SYNC_EXITS,
        diagnostics=diagnostics,
    )


def _fresh_plan_body(config: ProjectConfig, feature: str) -> str:
    snapshot = resolve_snapshot(config, feature)
    if is_terminal(snapshot.state):
        from heddle.runtime.state_store import accepted_state_error

        raise accepted_state_error(feature)
    return render_plan_status(snapshot, derive_next_actions(snapshot))


def _target_matches_preflight(projection: _Projection) -> bool:
    target_stat = _target_lstat(projection.path, projection.display_path)
    if projection.before is None:
        return target_stat is None
    if target_stat is None or not stat.S_ISREG(target_stat.st_mode):
        return False
    try:
        current = projection.path.read_text(encoding="utf-8", newline="")
    except UnicodeDecodeError:
        return False
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"{projection.display_path}: projection target cannot be "
                f"re-read for the final check: {type(error).__name__}: {error}"
            ),
            hint=(f"make {projection.display_path} readable, then re-run heddle sync"),
        ) from error
    return current == projection.before


def _completed_diagnostics(
    completed: list[_Projection],
) -> tuple[Diagnostic, ...]:
    return tuple(
        Diagnostic(
            severity=Severity.INFO,
            code="sync-partial",
            message=f"{projection.display_path}: {projection.action}",
        )
        for projection in completed
    )


def _conflict_failure(
    projection: _Projection,
    feature: str,
    diagnostics: tuple[Diagnostic, ...],
) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            code="conflict",
            message=(
                f"{projection.display_path} changed between preflight and install"
            ),
            hint="re-run heddle sync — each target is idempotent",
        ),
        exit_code=ExitCode.CONFLICT,
        diagnostics=diagnostics,
        next_actions=(
            NextAction(
                action=ops.CommandAction(ops.Sync(feature=feature)),
                reason="retry the projection after the concurrent change",
            ),
        ),
    )


def _target_row(projection: _Projection, action: str) -> dict[str, str]:
    return {
        "block": projection.block,
        "path": projection.display_path,
        "action": action,
    }


def run_sync(args: list[str], json_mode: bool) -> int:
    """Refresh the plan-status and session-entry projections."""
    parsed = _parse_sync_args(args)
    if isinstance(parsed, HeddleResult):
        return _emit(parsed, json_mode)

    from heddle.runtime.application import execute

    return _emit(
        execute(ops.Sync(feature=parsed.feature, dry_run=parsed.dry_run)), json_mode
    )


def sync(parsed: ops.Sync) -> HeddleResult:
    resolved = resolve_snapshot_from_cwd(parsed.feature, writable=True)
    if isinstance(resolved, ResolveFeatureFailure):
        return _kernel_failure(
            resolved.error,
            diagnostics=resolved.diagnostics,
        )

    config = resolved.config
    feature = resolved.feature
    plan_display = (Path(config.layout.plans) / feature / "plan.md").as_posix()
    plan_path = config.root / plan_display
    agents_path = config.root / "AGENTS.md"
    try:
        plan_body = render_plan_status(
            resolved.snapshot, derive_next_actions(resolved.snapshot)
        )
    except KernelError as error:
        return _kernel_failure(
            _path_error(plan_display, error),
            diagnostics=resolved.diagnostics,
        )
    session_body = render_session_entry()
    projections, errors = _preflight_targets(
        plan_path=plan_path,
        plan_display=plan_display,
        plan_body=plan_body,
        agents_path=agents_path,
        session_body=session_body,
    )
    if errors:
        diagnostics = resolved.diagnostics + tuple(
            _fatal_diagnostic(error) for error in errors[1:]
        )
        return _kernel_failure(
            errors[0],
            diagnostics=diagnostics,
        )

    if parsed.dry_run:
        dry_run_rows = [
            _target_row(
                projection,
                "unchanged"
                if projection.action == "unchanged"
                else "would-create"
                if projection.create
                else "would-update",
            )
            for projection in projections
        ]
        return HeddleResult.success(
            {
                "feature": feature,
                "targets": dry_run_rows,
                "dry_run": True,
            },
            diagnostics=resolved.diagnostics,
        )

    completed: list[_Projection] = []
    rows: list[dict[str, str]] = []
    for projection in projections:
        completed_diagnostics = resolved.diagnostics + _completed_diagnostics(completed)
        try:
            fresh_plan_body = _fresh_plan_body(config, feature)
            fresh_session_body = render_session_entry()
        except KernelError as error:
            return _kernel_failure(
                _path_error(projection.display_path, error),
                diagnostics=completed_diagnostics,
            )
        try:
            target_matches = _target_matches_preflight(projection)
        except KernelError as error:
            return _kernel_failure(
                error,
                diagnostics=completed_diagnostics,
            )
        if (
            fresh_plan_body != plan_body
            or fresh_session_body != session_body
            or not target_matches
        ):
            return _conflict_failure(
                projection,
                feature,
                completed_diagnostics,
            )
        if projection.action != "unchanged":
            try:
                install_projection(
                    projection.path,
                    projection.text,
                    create=projection.create,
                )
            except KernelError as error:
                return _kernel_failure(
                    _path_error(projection.display_path, error),
                    diagnostics=completed_diagnostics,
                )
        completed.append(projection)
        rows.append(_target_row(projection, projection.action))

    return HeddleResult.success(
        {"feature": feature, "targets": rows},
        diagnostics=resolved.diagnostics,
    )


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        print(
            f"heddle: error[{result.error.code}]: {result.error.message}",
            file=sys.stderr,
        )
        print(f"  hint: {result.error.hint}", file=sys.stderr)
        for action in result.next_actions:
            print(
                f"  next: {action.command} — {action.reason}",
                file=sys.stderr,
            )
    else:
        for target in (result.data or {}).get("targets", []):
            print(f"{target['path']}: {target['action']}")
    for diagnostic in result.diagnostics:
        print(
            f"{diagnostic.severity.value}: {diagnostic.code}: {diagnostic.message}",
            file=sys.stderr,
        )
