from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from heddle.gate.overlap import paths_overlap
from heddle.gate.types import DiffResult
from heddle.io import git
from heddle.kernel.project_config import KernelError
from heddle.kernel.state import SourceBaseline

MAX_GATE_DIFF_BYTES = 8 * 1024 * 1024
# The typed reason a recorded feature baseline refusal carries.
BASELINE_UNAVAILABLE = "baseline-unavailable"
REPOINT_COMMAND = "heddle feature baseline set"
REPOINT_REMEDY = f"{REPOINT_COMMAND} --commit <rev> --reason <text>"
_REASON_TEXT = {
    "missing": "missing from local Git history",
    "diverged": "diverged: it is not an ancestor of HEAD",
}


@dataclass(frozen=True)
class UnavailableBaseline:
    """A recorded feature baseline that no longer qualifies, and why."""

    oid: str
    reason: Literal["missing", "diverged"]
    detail: str

    @property
    def explanation(self) -> str:
        return (
            f"the recorded feature baseline {self.oid} is {_REASON_TEXT[self.reason]}"
        )


@dataclass(frozen=True)
class BaselineTestChange:
    """One path under the tests root whose content differs from the baseline.

    ``status`` is Git's letter (``A``, ``D``, ``M``, ``T``), with a rename split
    into its deleted and added ends and ``?`` for an untracked path;
    ``baseline_mode`` is the path's mode at the baseline, when it had one.
    """

    path: str
    status: str
    baseline_mode: str | None


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
        raise _baseline_preparation_error(
            error, recorded=feature_baseline is not None
        ) from error


def unavailable_feature_baseline(
    repo_root: Path, feature_baseline: SourceBaseline
) -> UnavailableBaseline | None:
    """Name why a recorded baseline no longer resolves, or None while it does."""
    try:
        git.resolve_feature_baseline(repo_root, feature_baseline)
    except git.FeatureBaselineError as error:
        return UnavailableBaseline(
            feature_baseline.oid, _unavailable_reason(error), str(error)
        )
    return None


class RepointTargetError(ValueError):
    """A re-point target that names no commit, or one HEAD does not contain."""


def repoint_target(repo_root: Path, revision: str) -> str:
    """Resolve a re-point target to a commit that is an ancestor of HEAD."""
    try:
        target = git.resolve_commit(repo_root, revision)
    except git.GitError as error:
        raise RepointTargetError(
            f"--commit {revision!r} does not name a commit: {error}"
        ) from error
    try:
        git.resolve_feature_baseline(repo_root, SourceBaseline("git-commit", target))
    except git.FeatureBaselineError as error:
        if error.reason == "baseline-diverged":
            raise RepointTargetError(
                f"commit {target} is not an ancestor of HEAD"
            ) from error
        raise RepointTargetError(
            f"commit {target} cannot be a feature baseline: {error}"
        ) from error
    return target


def qualify_feature_baseline(repo_root: Path, feature_baseline: SourceBaseline) -> str:
    """Return the recorded baseline commit, or the milestone gates' refusal."""
    baseline, _warning = _feature_baseline_commit(repo_root, feature_baseline, None)
    assert baseline is not None
    return baseline


def baseline_test_changes(
    repo_root: Path, feature_baseline: SourceBaseline, *, tests_root: str
) -> tuple[BaselineTestChange, ...]:
    """Every path under ``tests_root`` whose content differs from the baseline.

    One ``git diff --raw -M <baseline>`` against the working tree gives both
    ends of a rename, deletions and changes, and untracked files under the
    root join, so history that leaves content equal to the baseline is
    invisible. An unavailable baseline is refused, naming its reason and the
    re-point.
    """
    root = repo_root.resolve()
    baseline = qualify_feature_baseline(root, feature_baseline)
    changed, changed_warning = _run_git_result(
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--raw",
            "-z",
            "-M",
            "--no-abbrev",
            baseline,
            "--",
            tests_root,
        ],
        root,
        enforce_cap=False,
    )
    untracked, untracked_warning = _run_git_result(
        [
            "git",
            "--literal-pathspecs",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            tests_root,
        ],
        root,
        enforce_cap=False,
    )
    warning = changed_warning or untracked_warning
    if warning is not None:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"cannot list tests under {tests_root} that differ from the "
                f"feature baseline {baseline}: {warning}"
            ),
            hint="restore a readable Git working tree, then retry",
        )
    try:
        records = git.changed_raw_records(changed)
    except git.GitError as error:
        raise _git_preparation_error(error) from error
    untracked_paths = tuple(path for path in untracked.split("\0") if path)
    changes: dict[str, BaselineTestChange] = {}
    for record in records:
        if record.status in {"R", "C"}:
            old, new = record.paths
            if record.status == "R":
                changes[old] = BaselineTestChange(old, "D", record.source_mode)
            changes[new] = BaselineTestChange(new, "A", None)
            continue
        (path,) = record.paths
        changes[path] = BaselineTestChange(
            path,
            record.status,
            None if record.status == "A" else record.source_mode,
        )
    for path in untracked_paths:
        changes.setdefault(path, BaselineTestChange(path, "?", None))
    return tuple(changes[path] for path in sorted(changes, key=str.encode))


def baseline_test_paths(
    repo_root: Path, feature_baseline: SourceBaseline, *, tests_root: str
) -> tuple[str, ...]:
    """The paths of ``baseline_test_changes``, byte-sorted."""
    return tuple(
        change.path
        for change in baseline_test_changes(
            repo_root, feature_baseline, tests_root=tests_root
        )
    )


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


def _baseline_preparation_error(
    error: git.FeatureBaselineError, *, recorded: bool
) -> KernelError:
    if not recorded:
        return KernelError(
            code="workspace-invalid",
            message=str(error),
            hint=(
                "restore the recorded baseline commit and its ancestry from the "
                "original checkout or repository history, then retry without "
                "changing the baseline"
            ),
        )
    reason = _unavailable_reason(error)
    return KernelError(
        code="workspace-invalid",
        message=str(error),
        hint=(
            f"the recorded feature baseline is {_REASON_TEXT[reason]}; restore the "
            "recorded baseline commit and its ancestry from the original checkout "
            "or repository history, or record a reachable ancestor of HEAD with "
            f"`{REPOINT_REMEDY}`"
        ),
        reason=BASELINE_UNAVAILABLE,
        details={"baseline_reason": reason},
    )


def _unavailable_reason(
    error: git.FeatureBaselineError,
) -> Literal["missing", "diverged"]:
    """A baseline that is not an ancestor diverged; any other no longer resolves."""
    return "diverged" if error.reason == "baseline-diverged" else "missing"


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
