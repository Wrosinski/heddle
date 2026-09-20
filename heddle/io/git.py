"""Git execution and path inventory mechanics without workflow policy."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heddle.kernel.state import SourceBaseline

GIT_TIMEOUT_SECONDS = 30


class GitError(ValueError):
    """Git returned an undecodable or incomplete output stream."""


class FeatureBaselineError(GitError):
    """The authoritative feature baseline cannot support source comparison."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class GitStatusObservation:
    """Path-safe working-tree observation for an exact candidate set."""

    dirty_paths: tuple[str, ...]
    warning: str | None = None


def capture_head_commit(repo_root: Path) -> str:
    """Return the full commit identity at HEAD without changing repository state."""
    data, warning = run_git(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"], repo_root.resolve()
    )
    if warning is not None:
        raise GitError(
            "cannot capture the feature source baseline; create or restore an "
            f"initial source commit before admission ({warning})"
        )
    lines = data.splitlines()
    oid = lines[0].strip() if len(lines) == 1 else ""
    if not oid or re.fullmatch(r"[a-f0-9]+", oid) is None:
        raise GitError("git returned an invalid full commit identity for HEAD")
    return oid


def resolve_feature_baseline(
    repo_root: Path,
    source_baseline: SourceBaseline | None = None,
    *,
    legacy_state_probe: str | None = None,
) -> str:
    """Resolve and qualify the explicit v10 or state-add v9 commit boundary."""
    root = repo_root.resolve()
    if source_baseline is not None:
        if source_baseline.kind != "git-commit":
            raise FeatureBaselineError(
                "baseline-kind-invalid",
                f"feature baseline kind is unsupported: {source_baseline.kind}",
            )
        oid = source_baseline.oid
    else:
        if legacy_state_probe is None:
            raise FeatureBaselineError(
                "baseline-probe-missing",
                "feature baseline is unavailable: no explicit baseline or legacy "
                "state probe was supplied",
            )
        legacy_oid, warning = oldest_add_commit(root, legacy_state_probe)
        if legacy_oid is None:
            detail = f" ({warning})" if warning else ""
            raise FeatureBaselineError(
                "baseline-unresolvable",
                "feature baseline unresolvable: no commit adds "
                f"{legacy_state_probe}{detail}",
            )
        oid = legacy_oid

    kind, warning = run_git(["git", "cat-file", "-t", oid], root)
    if warning is not None:
        raise FeatureBaselineError(
            "baseline-missing",
            f"feature baseline {oid} is unavailable in local Git history ({warning})",
        )
    if kind.strip() != "commit":
        raise FeatureBaselineError(
            "baseline-noncommit",
            f"feature baseline {oid} names a {kind.strip() or 'noncommit'} object, "
            "not a commit",
        )
    canonical, warning = run_git(
        ["git", "rev-parse", "--verify", f"{oid}^{{commit}}"], root
    )
    if warning is not None or canonical.strip() != oid:
        raise FeatureBaselineError(
            "baseline-identity-invalid",
            f"feature baseline {oid} is not a complete commit identity",
        )
    _output, warning = run_git(
        ["git", "merge-base", "--is-ancestor", oid, "HEAD"], root
    )
    if warning is not None:
        raise FeatureBaselineError(
            "baseline-diverged",
            f"feature baseline {oid} is not an ancestor of current HEAD ({warning})",
        )
    return oid


def diff_untracked_file(repo_root: Path, relpath: str) -> str:
    """Render one untracked path as a binary-safe new-file patch."""
    command = ["git", "diff", "--no-index", "--binary", "--", "/dev/null", relpath]
    try:
        result = subprocess.run(
            command,
            cwd=repo_root.resolve(),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as error:
        raise GitError(f"cannot render untracked source {relpath}: {error}") from error
    if result.returncode not in {0, 1}:
        stderr = result.stderr.strip().replace("\n", " ")
        detail = f": {stderr[:200]}" if stderr else ""
        raise GitError(f"{' '.join(command)} exited {result.returncode}{detail}")
    return result.stdout


def run_git(command: list[str], repo_root: Path) -> tuple[str, str | None]:
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        if error.filename is not None and Path(error.filename) == repo_root:
            return "", f"Git working directory not found: {repo_root}"
        return "", f"{command[0]} executable not found"
    except subprocess.TimeoutExpired:
        return "", f"{' '.join(command)} timed out after {GIT_TIMEOUT_SECONDS}s"
    except UnicodeDecodeError as error:
        raise GitError(
            f"{' '.join(command)} produced a non-UTF-8 diff/input stream "
            f"at byte {error.start}"
        ) from error
    if result.returncode != 0:
        stderr = result.stderr.strip().replace("\n", " ")
        detail = f": {stderr[:200]}" if stderr else ""
        return "", f"{' '.join(command)} exited {result.returncode}{detail}"
    return result.stdout, None


def read_head_blob(repo_root: Path, relpath: str) -> bytes:
    """Read committed binary retention bytes without decoding an archive as text."""
    try:
        result = subprocess.run(
            ["git", "cat-file", "blob", f"HEAD:{relpath}"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(
            f"cannot read committed retention for {relpath}: {error}"
        ) from error
    if result.returncode != 0:
        raise GitError(
            f"HEAD does not retain {relpath}; commit the accepted ledger and archive"
        )
    return result.stdout


def _git_path_records(output: str) -> tuple[str, ...]:
    """Decode the NUL framing required of every Git pathname producer."""
    records = output.split("\0")
    return tuple(record for record in records if record)


def changed_name_status_paths(output: str) -> tuple[str, ...]:
    fields = _git_path_records(output)
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status_code = fields[index]
        count = 2 if status_code.startswith(("R", "C")) else 1
        selected = fields[index + 1 : index + 1 + count]
        if len(selected) != count:
            raise GitError(
                "git diff --name-status -z returned an incomplete path record"
            )
        paths.extend(selected)
        index += count + 1
    return tuple(paths)


def observe_dirty_paths(
    repo_root: Path, candidate_paths: tuple[str, ...]
) -> GitStatusObservation:
    """Return dirty candidate endpoints without treating Git failure as clean."""
    selected = tuple(sorted(set(candidate_paths), key=lambda value: value.encode()))
    if not selected:
        return GitStatusObservation(())
    try:
        output, warning = run_git(
            [
                "git",
                "--literal-pathspecs",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--",
                *selected,
            ],
            repo_root.resolve(),
        )
        if warning is not None:
            return GitStatusObservation((), warning)
        return GitStatusObservation(_porcelain_status_paths(output))
    except GitError as error:
        return GitStatusObservation((), str(error))


def _porcelain_status_paths(output: str) -> tuple[str, ...]:
    records = _git_path_records(output)
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4 or record[2] != " " or not record[3:]:
            raise GitError("git status --porcelain=v1 -z returned an invalid record")
        status, path = record[:2], record[3:]
        paths.append(path)
        index += 1
        if "R" in status or "C" in status:
            if index >= len(records) or not records[index]:
                raise GitError(
                    "git status --porcelain=v1 -z returned an incomplete rename record"
                )
            paths.append(records[index])
            index += 1
    return tuple(sorted(set(paths), key=lambda value: value.encode()))


def discover_untracked_files(repo_root: Path) -> tuple[list[str], str | None]:
    try:
        data, warning = run_git(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            repo_root.resolve(),
        )
    except GitError as error:
        return [], str(error)
    files = list(_git_path_records(data))
    return list(dict.fromkeys(files)), warning


def tracked_files(repo_root: Path) -> tuple[list[str], str | None]:
    try:
        data, warning = run_git(["git", "ls-files", "-z"], repo_root.resolve())
    except GitError as error:
        return [], str(error)
    return list(dict.fromkeys(_git_path_records(data))), warning


def oldest_add_commit(
    repo_root: Path, probe_relpath: str
) -> tuple[str | None, str | None]:
    """Return the oldest commit adding a repository path."""
    data, warning = run_git(
        ["git", "log", "--diff-filter=A", "--format=%H", "--", probe_relpath],
        repo_root,
    )
    if warning:
        return None, warning
    shas = [line.strip() for line in data.splitlines() if line.strip()]
    if not shas:
        return None, None
    # git log is newest-first; the workspace birth is the oldest add.
    return shas[-1], None


def path_has_git_history(repo_root: Path, relpath: str) -> bool:
    """Whether Git ever tracked a path; unavailable history returns false."""
    stdout, warning = run_git(
        ["git", "rev-list", "-1", "HEAD", "--", relpath], repo_root
    )
    return warning is None and bool(stdout.strip())
