from __future__ import annotations

import tempfile
from pathlib import Path

from heddle.gate.overlap import paths_overlap
from heddle.gate.types import DiffResult
from heddle.io import git
from heddle.kernel.project_config import KernelError
from heddle.kernel.state import SourceBaseline

MAX_GATE_DIFF_BYTES = 8 * 1024 * 1024


def resolve_diff(
    diff_path: Path | None,
    repo_root: Path,
    temp_dir: Path | None = None,
    *,
    owned_paths: list[str] | None = None,
    feature_baseline: SourceBaseline | None = None,
    feature_baseline_probe: str | None = None,
) -> DiffResult:
    """Resolve the authoritative diff for a gate run.

    When ``owned_paths`` and ``feature_baseline_probe`` are both provided
    (milestone gates on native workspaces), the cascade runs in
    milestone-baseline mode: one path-scoped
    ``git diff <feature-baseline> -- <owned paths>`` is authoritative. It
    compares the workspace birth commit directly with the complete current
    working tree, so committed, staged, and unstaged owned changes cannot
    hide one another. Unowned workflow bookkeeping is excluded by the
    pathspec and recorded as a warning. ``HEAD^..HEAD`` is excluded in this
    mode — with committed milestone work an unrelated tip commit would
    poison it.
    """
    repo_root = repo_root.resolve()
    untracked_files, untracked_warning = git.discover_untracked_files(repo_root)
    if diff_path is not None:
        path = diff_path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Provided diff file not found: {path}")
        _require_bounded_diff_path(path)
        provided_warnings = [untracked_warning] if untracked_warning else []
        return DiffResult(
            source="provided-file",
            source_detail=str(path),
            diff_path=path,
            changed_files=tuple(extract_changed_files(path)),
            untracked_files=tuple(untracked_files),
            warnings=tuple(provided_warnings),
        )

    baseline_mode = bool(owned_paths) and (
        feature_baseline is not None or feature_baseline_probe is not None
    )
    warnings: list[str] = []
    if untracked_warning:
        warnings.append(untracked_warning)

    if baseline_mode:
        assert owned_paths
        untracked_owned = [
            path
            for path in untracked_files
            if any(paths_overlap(path, owned) for owned in owned_paths)
        ]
        baseline, baseline_warning = _feature_baseline_commit(
            repo_root, feature_baseline, feature_baseline_probe
        )
        if baseline_warning:
            warnings.append(baseline_warning)
        if baseline is None:
            dirty_owned, dirty_owned_warning = _run_git_result(
                ["git", "diff", "HEAD", "--", *owned_paths], repo_root
            )
            if dirty_owned_warning:
                warnings.append(dirty_owned_warning)
            if dirty_owned:
                path = _write_temp_diff(dirty_owned, temp_dir)
                return DiffResult(
                    source="git-diff-head",
                    source_detail="HEAD",
                    diff_path=path,
                    changed_files=tuple(extract_changed_files(path)),
                    untracked_files=tuple(untracked_files),
                    warnings=tuple(warnings),
                )
            return _empty_diff_result(temp_dir, untracked_files, warnings)

        dirty_owned, dirty_owned_warning = _run_git_result(
            ["git", "diff", "HEAD", "--", *owned_paths], repo_root
        )
        if dirty_owned_warning:
            warnings.append(dirty_owned_warning)
        dirty_all, dirty_all_warning = _run_git_result(
            ["git", "diff", "HEAD"], repo_root, enforce_cap=False
        )
        if dirty_all_warning:
            warnings.append(dirty_all_warning)
        if dirty_all and not dirty_owned:
            shown_files = extract_changed_files_text(dirty_all, limit=5)
            shown = ", ".join(shown_files[:5]) or "(unparsed)"
            warnings.append(
                "git-diff-head diff has no milestone-owned overlap "
                f"({shown}); not authoritative for a milestone gate — "
                "falling through"
            )

        data, warning = _run_git_result(
            ["git", "diff", baseline, "--", *owned_paths], repo_root
        )
        if warning:
            warnings.append(warning)
        changed_text, changed_warning = _run_git_result(
            [
                "git",
                "diff",
                "--name-status",
                "-z",
                "-M",
                baseline,
                "--",
                *owned_paths,
            ],
            repo_root,
            enforce_cap=False,
        )
        if changed_warning:
            warnings.append(changed_warning)
        try:
            changed_files = list(git.changed_name_status_paths(changed_text))
        except git.GitError as error:
            raise _git_preparation_error(error) from error
        changed_files.extend(
            path for path in untracked_owned if path not in changed_files
        )
        if untracked_owned:
            try:
                data += "".join(
                    git.diff_untracked_file(repo_root, path) for path in untracked_owned
                )
            except git.GitError as error:
                raise _git_preparation_error(error) from error
            _require_bounded_diff_text(
                data, source="feature-baseline diff with untracked source"
            )
        if data:
            path = _write_temp_diff(data, temp_dir)
            working_tree_suffix = (
                "WORKTREE" if dirty_owned or untracked_owned else "HEAD"
            )
            return DiffResult(
                source="git-diff-feature-baseline",
                source_detail=f"{baseline}..{working_tree_suffix}",
                diff_path=path,
                changed_files=tuple(changed_files),
                untracked_files=tuple(untracked_files),
                warnings=tuple(warnings),
            )
        warnings.append(
            f"feature-baseline diff {baseline[:12]}..WORKTREE is empty for "
            "the milestone-owned paths — no reviewable milestone work found"
        )
        return _empty_diff_result(temp_dir, untracked_files, warnings)

    rungs = [
        (
            "git-diff-head",
            "HEAD",
            ["git", "diff", "HEAD"],
        ),
        (
            "git-diff-cached",
            "--cached",
            ["git", "diff", "--cached"],
        ),
    ]
    rungs.append(
        (
            "git-diff-head-parent",
            "HEAD^..HEAD",
            ["git", "diff", "HEAD^", "HEAD"],
        )
    )
    for source, detail, command in rungs:
        data, warning = _run_git_result(command, repo_root)
        if warning:
            warnings.append(warning)
        if data:
            path = _write_temp_diff(data, temp_dir)
            changed = extract_changed_files(path)
            return DiffResult(
                source=source,
                source_detail=detail,
                diff_path=path,
                changed_files=tuple(changed),
                untracked_files=tuple(untracked_files),
                warnings=tuple(warnings),
            )

    return _empty_diff_result(temp_dir, untracked_files, warnings)


def _empty_diff_result(
    temp_dir: Path | None, untracked_files: list[str], warnings: list[str]
) -> DiffResult:
    path = _write_temp_diff("", temp_dir)
    return DiffResult(
        source="none",
        source_detail="",
        diff_path=path,
        changed_files=(),
        untracked_files=tuple(untracked_files),
        warnings=tuple(warnings),
    )


def _feature_baseline_commit(
    repo_root: Path,
    feature_baseline: SourceBaseline | None,
    legacy_state_probe: str | None,
) -> tuple[str | None, str | None]:
    """Resolve explicit v10 authority or the named v9 state-add fallback."""
    try:
        return (
            git.resolve_feature_baseline(
                repo_root,
                feature_baseline,
                legacy_state_probe=legacy_state_probe,
            ),
            None,
        )
    except git.FeatureBaselineError as error:
        raise _baseline_preparation_error(error) from error


def extract_changed_files(diff_path: Path) -> list[str]:
    return extract_changed_files_text(diff_path.read_text(encoding="utf-8"))


def extract_changed_files_text(content: str, *, limit: int | None = None) -> list[str]:
    files: list[str] = []
    for line in content.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        left = parts[2]
        if left.startswith("a/"):
            left = left[2:]
        if left not in files:
            files.append(left)
            if limit is not None and len(files) >= limit:
                break
    return files


def _run_git_result(
    command: list[str], repo_root: Path, *, enforce_cap: bool = True
) -> tuple[str, str | None]:
    try:
        output, warning = git.run_git(command, repo_root)
    except git.GitError as error:
        raise _git_preparation_error(error) from error
    if enforce_cap and command[:2] == ["git", "diff"]:
        _require_bounded_diff_text(output, source=" ".join(command))
    return output, warning


def _git_preparation_error(error: git.GitError) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=str(error),
        hint=(
            "rename or re-encode the affected tracked path/content as UTF-8 "
            "before reviewer presentation; raw repository bytes remain unchanged"
        ),
    )


def _baseline_preparation_error(error: git.FeatureBaselineError) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=str(error),
        hint=(
            "restore the recorded baseline commit and its ancestry from the original "
            "checkout or repository history, then retry without changing the baseline"
        ),
    )


def _require_bounded_diff_path(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        raise
    if size > MAX_GATE_DIFF_BYTES:
        raise _oversized_diff_error(str(path), size)


def _require_bounded_diff_text(content: str, *, source: str) -> None:
    try:
        size = len(content.encode("utf-8"))
    except MemoryError as error:
        raise _oversized_diff_error(source, None) from error
    if size > MAX_GATE_DIFF_BYTES:
        raise _oversized_diff_error(source, size)


def _oversized_diff_error(source: str, size: int | None) -> KernelError:
    measured = "available memory" if size is None else f"{size} bytes"
    return KernelError(
        code="workspace-invalid",
        message=(
            f"authoritative diff from {source} exceeds the "
            f"{MAX_GATE_DIFF_BYTES}-byte limit ({measured})"
        ),
        hint=(
            "narrow the milestone-owned paths or provide a smaller focused diff "
            "before launching the reviewer"
        ),
    )


def _write_temp_diff(content: str, temp_dir: Path | None) -> Path:
    if temp_dir is not None:
        temp_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".diff",
        prefix="gate-",
        dir=temp_dir,
        delete=False,
    )
    with handle:
        handle.write(content)
    return Path(handle.name)
