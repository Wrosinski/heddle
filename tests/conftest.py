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


# A user-level ignore of `.heddle.yaml` would put fixture hosts into
# local-checkout mode and keep `git add` from staging the file. Command-scope
# config outranks `core.excludesFile` in every config file and the default
# `$XDG_CONFIG_HOME/git/ignore`.
GIT_IGNORE_ISOLATION = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.excludesFile",
    "GIT_CONFIG_VALUE_0": os.devnull,
}


def pytest_configure(config: pytest.Config) -> None:
    """Scrub before collection so no fixture can spawn git with an override
    or a developer's ignore rules."""
    scrub_git_locations(os.environ)
    os.environ.update(GIT_IGNORE_ISOLATION)
