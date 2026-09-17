"""
Shared fixtures for the heddle/kernel tests (reader Kernel Read Model).

Behavior contract: reader-kernel-read-model
Scaffolded in test scaffolding (preimplementation state, pattern
docs/patterns/red-phase-always-green-scaffolding.md): foundation modules
(test_project_config.py, test_boundaries.py) bite directly — collection
errors / failures are the expected pre-implementation signal; integration modules
(test_state.py, test_model.py) gate on their own milestone's module via
pytest.importorskip and go live in the run that lands it.

With --import-mode=importlib and no __init__.py, test modules cannot import
conftest by name — shared helpers are handed to tests through fixtures
(the core tests/runtime/conftest.py idiom).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures"
WORKSPACES_ROOT = FIXTURES_ROOT / "workspaces"
TINY = WORKSPACES_ROOT / "tiny"
GOLDEN = WORKSPACES_ROOT / "golden"
EDGE = WORKSPACES_ROOT / "edge"
INVALID_STATES = FIXTURES_ROOT / "invalid-states"
CONFIGS = FIXTURES_ROOT / "configs"


def write_pointer(host_root: Path, slug: str) -> Path:
    """Fabricate the normal-checkout pointer record (feature resolution):
    `<root>/.git/heddle/active-feature` containing ``<slug>\\n``.

    Pure path fabrication — kernel *reads* need no real git binary
    (Assumption A3: the pointer-path derivation is pure Python over the
    `.git` directory / `.git` file layouts). The write-side contract against
    real git layouts is AC-11's job (tests/runtime/test_pointer.py).
    """
    pointer_dir = host_root / ".git" / "heddle"
    pointer_dir.mkdir(parents=True, exist_ok=True)
    pointer = pointer_dir / "active-feature"
    pointer.write_text(f"{slug}\n", encoding="utf-8")
    return pointer


@pytest.fixture
def kernel_corpus():
    """Fixture-corpus paths + the pointer fabrication helper."""
    return SimpleNamespace(
        repo_root=REPO_ROOT,
        workspaces=WORKSPACES_ROOT,
        tiny=TINY,
        golden=GOLDEN,
        edge=EDGE,
        invalid_states=INVALID_STATES,
        configs=CONFIGS,
        write_pointer=write_pointer,
    )


@pytest.fixture
def host_copy(tmp_path):
    """Copy a fixture host into tmp_path for cases that mutate the host
    (pointer records, stale-pointer workspace deletion, config swaps).
    Committed fixtures are never mutated in place (Idempotence & Recovery).
    """

    def _copy(src: Path, name: str = "host") -> Path:
        dest = tmp_path / name
        shutil.copytree(src, dest)
        return dest

    return _copy
