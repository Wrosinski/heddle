"""Repository pytest integration."""

from __future__ import annotations

import os
from collections.abc import MutableMapping

import pytest

pytest_plugins = ("tests.pytest_policy",)

# Git hands these to every process it starts (hooks, aliases, `git bisect run`),
# as absolute paths when the repository is a linked worktree. Fixture hosts run
# `git init`, `git config` and `git add` with only a changed cwd, so an inherited
# value would redirect those writes into the real repository.
GIT_LOCATION_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def scrub_git_locations(environ: MutableMapping[str, str]) -> tuple[str, ...]:
    """Remove inherited git location overrides and return the names removed."""
    removed = tuple(name for name in GIT_LOCATION_VARIABLES if name in environ)
    for name in removed:
        del environ[name]
    return removed


def pytest_configure(config: pytest.Config) -> None:
    """Scrub before collection so no fixture can spawn git with an override."""
    scrub_git_locations(os.environ)
