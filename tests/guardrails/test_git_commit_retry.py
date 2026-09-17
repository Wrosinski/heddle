"""
Behavioral checks for scripts/git-commit-retry.sh.

The helper commits; when pre-commit hooks mutate staged files it re-stages
exactly those files and retries once. Any other failure propagates unchanged.
The repository commit-hook contract owns this behavior.

Asserted here, per that proposal's §9.2 test track:
  - a hook-mutated staged file is re-staged and the retry commit succeeds
  - only the mutated file is re-staged (no `git add -A`: untracked files and
    unstaged edits to other tracked files stay out of the commit)
  - exactly one retry — a hook that keeps failing gets no third attempt
  - a hook failure that mutates nothing propagates without any retry
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "git-commit-retry.sh"

HOOK_COUNTER = "hook-runs.count"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _install_hook(repo: Path, body: str) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    counter = f'echo run >> "$(git rev-parse --git-dir)/../{HOOK_COUNTER}"'
    hook.write_text(f"#!/usr/bin/env bash\n{counter}\n{body}\n")
    hook.chmod(0o755)


def _hook_runs(repo: Path) -> int:
    counter = repo / HOOK_COUNTER
    return len(counter.read_text().splitlines()) if counter.exists() else 0


def _retry_commit(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args], cwd=repo, capture_output=True, text=True
    )


def _committed(repo: Path, path: str) -> str:
    return _git(repo, "show", f"HEAD:{path}").stdout


def test_script_is_executable_and_byte_pinned() -> None:
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111, "script must be executable"
    blob = subprocess.run(
        ["git", "hash-object", str(SCRIPT)], capture_output=True, text=True
    ).stdout.strip()
    # Bump deliberately whenever the helper's bytes change.
    assert blob == "e19b86b504b5cdc5d1fade813f5ee6c43ec53bb3"


@pytest.mark.toolchain
def test_restages_only_mutated_file_and_retry_succeeds(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # Hook "formats" needs-format.txt: first run mutates it and fails,
    # second run finds it already formatted and passes.
    _install_hook(
        repo,
        "if ! grep -q formatted needs-format.txt 2>/dev/null; then\n"
        "  echo formatted >> needs-format.txt\n"
        "  exit 1\n"
        "fi\n"
        "exit 0",
    )
    (repo / "needs-format.txt").write_text("raw\n")
    (repo / "clean.txt").write_text("clean\n")
    _git(repo, "add", "needs-format.txt", "clean.txt")
    (repo / "seed.txt").write_text("unstaged edit\n")  # tracked, NOT staged
    (repo / "untracked.txt").write_text("junk\n")

    result = _retry_commit(repo, "-m", "formatted commit")

    assert result.returncode == 0, result.stderr
    assert _hook_runs(repo) == 2
    assert _committed(repo, "needs-format.txt") == "raw\nformatted\n"
    assert _committed(repo, "clean.txt") == "clean\n"
    # No `git add -A`: the unstaged edit and the untracked file stayed out.
    assert _committed(repo, "seed.txt") == "seed\n"
    status = _git(repo, "status", "--porcelain").stdout
    assert " M seed.txt" in status
    assert "?? untracked.txt" in status


@pytest.mark.toolchain
def test_persistently_failing_hook_gets_no_third_attempt(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _install_hook(repo, "echo again >> mutated.txt\nexit 1")
    (repo / "mutated.txt").write_text("v1\n")
    _git(repo, "add", "mutated.txt")

    result = _retry_commit(repo, "-m", "never lands")

    assert result.returncode != 0
    assert _hook_runs(repo) == 2, "exactly one retry — the helper must not loop"
    assert _git(repo, "log", "--oneline").stdout.count("\n") == 1  # seed only


@pytest.mark.toolchain
def test_non_mutating_failure_propagates_without_retry(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _install_hook(repo, "exit 1")
    (repo / "file.txt").write_text("v1\n")
    _git(repo, "add", "file.txt")

    result = _retry_commit(repo, "-m", "rejected")

    assert result.returncode != 0
    assert _hook_runs(repo) == 1, "nothing was mutated, so no retry"
    assert _git(repo, "log", "--oneline").stdout.count("\n") == 1


@pytest.mark.toolchain
def test_nothing_staged_fails_without_retry(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _install_hook(repo, "exit 0")

    result = _retry_commit(repo, "-m", "empty")

    assert result.returncode != 0
    assert _git(repo, "log", "--oneline").stdout.count("\n") == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
