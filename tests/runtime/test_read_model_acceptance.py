"""
Integrated agreement checks for status, orient, and the kernel reader.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACES_ROOT = REPO_ROOT / "tests" / "fixtures" / "workspaces"
TINY = WORKSPACES_ROOT / "tiny"
GOLDEN = WORKSPACES_ROOT / "golden"

# The payload keys the two JSON surfaces actually share (AC-12 mechanism 1).
SHARED_KEYS = (
    "feature",
    "stage",
    "authorized_through",
    "tier",
    "current_milestone",
    "blocking_conditions",
)

# (host source, feature slug, pointer to fabricate or None for uniqueness).
CORPUS_CASES = [
    pytest.param(TINY, "sample-feature", None, id="tiny-sample-feature"),
    pytest.param(GOLDEN, "nl-screening", "nl-screening", id="golden-nl-screening"),
    pytest.param(
        GOLDEN, "tier1-quickfix", "tier1-quickfix", id="golden-tier1-quickfix"
    ),
]


def make_host(tmp_path: Path, src: Path, pointer: str | None) -> Path:
    host = tmp_path / "host"
    shutil.copytree(src, host)
    if pointer is not None:
        pointer_dir = host / ".git" / "heddle"
        pointer_dir.mkdir(parents=True)
        (pointer_dir / "active-feature").write_text(f"{pointer}\n", encoding="utf-8")
    return host


def instrument_resolve_snapshot(monkeypatch) -> list:
    """
    Count kernel snapshot resolutions while delegating to the real one.

        Patches the kernel definition site, the package-level re-export (if
        heddle.kernel re-exports the symbol), and EVERY loaded heddle.runtime
        submodule whose bound `resolve_snapshot` is the real kernel function —
        so the count holds regardless of which handler module imported the
        symbol or how the surface set grows (scaffolding review review +
        iteration-2 review).

    """
    import sys

    import heddle.kernel as kernel_package
    import heddle.kernel.model as kernel_model
    import heddle.runtime.status  # noqa: F401 — ensure the handler is loaded

    calls: list = []
    real = kernel_model.resolve_snapshot

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(kernel_model, "resolve_snapshot", counting)
    if getattr(kernel_package, "resolve_snapshot", None) is real:
        monkeypatch.setattr(kernel_package, "resolve_snapshot", counting)
    for name, module in list(sys.modules.items()):
        if not name.startswith("heddle.runtime"):
            continue
        if module is not None and getattr(module, "resolve_snapshot", None) is real:
            monkeypatch.setattr(module, "resolve_snapshot", counting)
    return calls


def assert_json_free(text: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


@pytest.mark.acceptance
class TestAC12AgreementBar:
    """AC-12: the three surfaces agree on every exit-bar field across the
    fixture corpus (Checkpoint 1 — state visibility)."""

    @pytest.mark.parametrize(("src", "slug", "pointer"), CORPUS_CASES)
    def test_each_invocation_resolves_exactly_one_snapshot(
        self, run_cli, tmp_path, monkeypatch, src, slug, pointer
    ):
        # Mechanism 2a: agreement by construction — one resolve_snapshot per
        # handler invocation, on every exit-bar surface.
        host = make_host(tmp_path, src, pointer)
        monkeypatch.chdir(host)
        calls = instrument_resolve_snapshot(monkeypatch)
        surfaces = (["status"], ["status", "--json"], ["orient"], ["orient", "--json"])
        for argv in surfaces:
            calls.clear()
            code, _out, _err = run_cli(argv)
            assert code == 0, f"FAIL [{slug}]: `heddle {' '.join(argv)}` exited {code}"
            assert len(calls) == 1, (
                f"FAIL [{slug}]: `heddle {' '.join(argv)}` called "
                f"resolve_snapshot {len(calls)} times — human and JSON must "
                "render ONE snapshot per invocation (read model/AC-12)"
            )
