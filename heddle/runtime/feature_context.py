"""Shared runtime feature-resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from heddle.contracts.result import Diagnostic, HeddleResult
from heddle.kernel.model import (
    FeatureSnapshot,
    is_terminal,
    resolve_active_feature,
    resolve_snapshot,
)
from heddle.kernel.project_config import (
    ConfigDiagnostic,
    KernelError,
    ProjectConfig,
    feature_state_path,
    load_project_config_from_cwd,
)
from heddle.runtime.diagnostics import (
    conflict_failure,
    envelope_diagnostics,
    write_failure,
)
from heddle.runtime.state_store import (
    accepted_state_error,
    check_expect_revision,
)


@dataclass(frozen=True)
class ResolvedFeatureContext:
    config: ProjectConfig
    feature: str
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class ResolvedSnapshotContext:
    config: ProjectConfig
    feature: str
    diagnostics: tuple[Diagnostic, ...]
    snapshot: FeatureSnapshot

    @property
    def state_path(self) -> Path:
        return feature_state_path(self.config, self.feature)


@dataclass(frozen=True)
class ResolveFeatureFailure:
    error: KernelError
    diagnostics: tuple[Diagnostic, ...]


def resolve_feature_from_cwd(
    explicit: str | None,
) -> ResolvedFeatureContext | ResolveFeatureFailure:
    held: tuple[ConfigDiagnostic, ...] = ()
    try:
        config = load_project_config_from_cwd()
        held = config.diagnostics
        resolution = resolve_active_feature(config, explicit)
        held = config.diagnostics + resolution.diagnostics
    except KernelError as error:
        return ResolveFeatureFailure(
            error=error, diagnostics=envelope_diagnostics(held)
        )
    return ResolvedFeatureContext(
        config=config,
        feature=resolution.feature,
        diagnostics=envelope_diagnostics(held),
    )


def resolve_snapshot_from_cwd(
    explicit: str | None,
    *,
    writable: bool = False,
) -> ResolvedSnapshotContext | ResolveFeatureFailure:
    resolved = resolve_feature_from_cwd(explicit)
    if isinstance(resolved, ResolveFeatureFailure):
        return resolved
    try:
        snapshot = resolve_snapshot(resolved.config, resolved.feature)
        if writable and is_terminal(snapshot.state):
            raise accepted_state_error(snapshot.feature)
    except KernelError as error:
        return ResolveFeatureFailure(error=error, diagnostics=resolved.diagnostics)
    return ResolvedSnapshotContext(
        config=resolved.config,
        feature=resolved.feature,
        diagnostics=resolved.diagnostics,
        snapshot=snapshot,
    )


def resolve_write_context(
    feature: str | None,
    expect_revision: int | None = None,
    *,
    allow_terminal: bool = False,
) -> ResolvedSnapshotContext | HeddleResult:
    """Resolve one current snapshot, then enforce write admission and caller CAS."""
    resolved = resolve_snapshot_from_cwd(feature, writable=not allow_terminal)
    if isinstance(resolved, ResolveFeatureFailure):
        return write_failure(resolved.error, resolved.diagnostics)
    if conflict := check_expect_revision(
        expect_revision, resolved.snapshot.state.revision
    ):
        return conflict_failure(conflict)
    return resolved
