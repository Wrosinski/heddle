"""Fixture git commands must target their hosts, never an inherited git dir."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import GIT_LOCATION_VARIABLES, scrub_git_locations

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_WITNESS = "test_fixture_git_init_lands_in_the_host"


def test_scrub_removes_only_git_location_overrides() -> None:
    kept = {"GIT_EDITOR": "true", "GIT_AUTHOR_NAME": "Fixture", "PATH": "/bin"}
    environ = {name: f"/decoy/{name}" for name in GIT_LOCATION_VARIABLES}
    environ.update(kept)
    assert scrub_git_locations(environ) == GIT_LOCATION_VARIABLES
    assert environ == kept
    assert scrub_git_locations(environ) == ()


def test_fixture_git_init_lands_in_the_host(tmp_path: Path) -> None:
    assert not set(GIT_LOCATION_VARIABLES) & set(os.environ)
    host = tmp_path / "host"
    host.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=host, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=host,
        check=True,
        capture_output=True,
        text=True,
    )
    assert Path(result.stdout.strip()) == host / ".git"


def test_inherited_git_locations_do_not_reach_fixtures(tmp_path: Path) -> None:
    """Git exports absolute locations to the processes it starts, such as a
    hook or `git bisect run`; from a linked worktree they name the real
    repository. A pytest child started that way must still init its hosts."""
    decoy = tmp_path / "decoy.git"
    environment = {
        **os.environ,
        "GIT_DIR": str(decoy),
        "GIT_WORK_TREE": str(tmp_path / "decoy-tree"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    node = f"{Path(__file__).relative_to(REPO_ROOT).as_posix()}::{HOST_WITNESS}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not decoy.exists()
