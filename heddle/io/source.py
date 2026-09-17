"""Direct point observations of working content, without filesystem snapshot claims."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from heddle.kernel.project_config import KernelError
from heddle.kernel.source_manifest import (
    ObservedPath,
    SourceDefinition,
    SourceEvidence,
    SourceSetKind,
    build_source_evidence,
    byte_sort_key,
    safe_relative_parts,
)


def capture_path(root: Path, relative: str) -> ObservedPath:
    """Capture raw bytes or an explicit kind, never following a static symlink."""
    metadata = _source_metadata(root, relative)
    if metadata is None:
        return ObservedPath(relative, "missing", False, b"")
    path = root / relative
    try:
        if stat.S_ISREG(metadata.st_mode):
            return ObservedPath(
                relative, "file", bool(metadata.st_mode & 0o111), path.read_bytes()
            )
        if stat.S_ISLNK(metadata.st_mode):
            return ObservedPath(
                relative, "symlink", False, os.fsencode(os.readlink(path))
            )
        if stat.S_ISDIR(metadata.st_mode):
            return ObservedPath(relative, "directory", False, b"")
        raise KernelError(
            code="workspace-invalid",
            message=f"source is not a regular file, directory, or symlink: {relative}",
            hint="remove unsupported special files from the source declaration",
        )
    except OSError as error:
        raise _observation_error(relative, error) from error


def observe_source(
    root: Path,
    definition: SourceDefinition,
    *,
    observations: dict[str, ObservedPath] | None = None,
) -> SourceEvidence:
    """Observe declared roots and selected leaves once in canonical path order."""
    paths = sorted(
        set(definition.declaration_paths) | set(definition.paths), key=byte_sort_key
    )
    return build_source_evidence(
        definition,
        (
            capture_path(root, path)
            if observations is None
            else capture_source_path(root, path, observations)
            for path in paths
        ),
    )


def _observation_error(relative: str, error: OSError) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"source could not be inspected ({relative}): {error}",
        hint="restore readable repository inputs and retry the observation",
    )


def resolve_source_definition(
    root: Path,
    kind: SourceSetKind,
    declarations: tuple[str, ...],
    *,
    eligible_paths: tuple[str, ...] | None = None,
    inventory_error: str | None = None,
) -> SourceDefinition:
    """Select current eligible leaves while retaining every exact declaration."""
    from heddle.io import git
    from heddle.kernel.source_manifest import normalize_paths

    declarations = normalize_paths(declarations)
    if eligible_paths is None:
        try:
            tracked, tracked_error = git.tracked_files(root)
            untracked, untracked_error = git.discover_untracked_files(root)
        except git.GitError as error:
            raise KernelError(
                code="workspace-invalid",
                message=str(error),
                hint="repair the Git inventory and its UTF-8 paths, then retry",
            ) from error
        eligible_paths = tuple(set(tracked) | set(untracked))
        inventory_error = tracked_error or untracked_error
    selected: set[str] = set()
    for declared in declarations:
        if source_path_kind(root, declared) != "directory":
            selected.add(declared)
            continue
        if inventory_error:
            raise KernelError(
                code="workspace-invalid",
                message=(
                    f"cannot expand Git-backed directory {declared}: {inventory_error}"
                ),
                hint="restore the Git repository (`git status`) and retry verification",
            )
        for path in eligible_paths:
            if path.startswith(declared + "/") and source_path_kind(root, path) not in {
                "directory",
                "missing",
            }:
                selected.add(path)
    return SourceDefinition(
        kind, declarations, tuple(sorted(selected, key=byte_sort_key))
    )


def source_path_kind(root: Path, relative: str) -> str:
    """Classify eligibility without opening content or following static ancestors."""
    metadata = _source_metadata(root, relative)
    if metadata is None:
        return "missing"
    mode = metadata.st_mode
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    raise KernelError(
        code="workspace-invalid",
        message=f"unsupported source type: {relative}",
        hint="remove unsupported special files from the source declaration",
    )


def capture_source_path(
    root: Path, relative: str, observations: dict[str, ObservedPath]
) -> ObservedPath:
    """Share an explicitly command-local capture across its declared consumers."""
    captured = observations.get(relative)
    if captured is None:
        captured = capture_path(root, relative)
        observations[relative] = captured
    return captured


def _source_metadata(root: Path, relative: str) -> os.stat_result | None:
    parts = safe_relative_parts(relative)
    try:
        for length in range(1, len(parts)):
            ancestor = root.joinpath(*parts[:length])
            if stat.S_ISLNK(ancestor.lstat().st_mode):
                raise KernelError(
                    code="workspace-invalid",
                    message=f"source ancestor is a symlink: {'/'.join(parts[:length])}",
                    hint="declare the leaf symlink itself or restore source parents",
                )
        return root.joinpath(*parts).lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise _observation_error(relative, error) from error
