"""
Performance budget and hot-path trace tests — milestone completion.

Covers: AC-15 (warm in-process runs within PERF_CI_CEILING_S; the traced
run reads no knowledge-plane .md and no state.yaml other than the active
feature's — the structural half of the read performance/identity promise).
Behavior contract: reader-kernel-read-model

preimplementation state (retired): importorskip-gated on heddle.runtime.status until
completion
landed it; guard retired at feature completion (pattern step 5).

The read trace uses a sys.addaudithook recorder: audit hooks cannot be
removed, so the hook is registered once at module load and gated by a
toggle — inert outside the capture window. CI wiring on the scaled fixture
stays M9 (the AC pins the 2.0 s ceiling as the algorithmic-regression
tripwire; the 300 ms warm target is reported, never asserted).
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from heddle.runtime.contracts import (
    PERF_CI_CEILING_S,
    PERF_WARM_TARGET_MS,
)
from tests.runtime.search_helpers import snapshot_tree

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"

# Audit-hook recorder: hooks are permanent for the interpreter, so gate the
# recording with a toggle instead of trying to unregister.
_TRACE: dict = {"active": False, "opens": []}


def _record_opens(event: str, args: tuple) -> None:  # pragma: no cover - hook
    if _TRACE["active"] and event == "open":
        target = args[0]
        try:
            _TRACE["opens"].append(os.fspath(target))
        except TypeError:
            pass  # file descriptors and exotic targets are not paths


sys.addaudithook(_record_opens)


def make_host(tmp_path: Path) -> Path:
    host = tmp_path / "host"
    shutil.copytree(GOLDEN, host)
    pointer_dir = host / ".git" / "heddle"
    pointer_dir.mkdir(parents=True)
    (pointer_dir / "active-feature").write_text("nl-screening\n", encoding="utf-8")
    return host


class TestAC15PerformanceAndHotPath:
    """AC-15: the budget holds warm and the hot path is structurally clean."""

    def test_warm_runs_within_ceiling(self, run_cli, tmp_path, monkeypatch):
        host = make_host(tmp_path)
        monkeypatch.chdir(host)
        # Warm: one throwaway resolve first (imports, caches).
        code, _out, _err = run_cli(["status", "--json"])
        assert code == 0
        for argv in (["status"], ["status", "--json"], ["orient"]):
            start = time.perf_counter()
            code, _out, _err = run_cli(argv)
            elapsed = time.perf_counter() - start
            assert code == 0
            assert elapsed < PERF_CI_CEILING_S, (
                f"FAIL: `heddle {' '.join(argv)}` took {elapsed:.3f}s — the "
                f"{PERF_CI_CEILING_S}s ceiling is the algorithmic-regression "
                "tripwire (PERF_CI_CEILING_S, identity)"
            )
            # The 300 ms design target is reported, never asserted (AC-15).
            print(
                f"note: heddle {' '.join(argv)} warm elapsed "
                f"{elapsed * 1000:.1f}ms (target {PERF_WARM_TARGET_MS}ms)"
            )

    def test_hot_path_reads_only_the_control_plane(
        self, run_cli, tmp_path, monkeypatch
    ):
        host = make_host(tmp_path)
        monkeypatch.chdir(host)
        run_cli(["status", "--json"])  # warm before tracing
        _TRACE["opens"].clear()
        _TRACE["active"] = True
        try:
            code, _out, _err = run_cli(["status", "--json"])
        finally:
            _TRACE["active"] = False
        assert code == 0
        host_resolved = host.resolve()
        host_reads = sorted(
            {
                str(Path(raw).resolve())
                for raw in _TRACE["opens"]
                if str(Path(raw).resolve()).startswith(str(host_resolved))
            }
        )
        assert host_reads, "FAIL: the traced run read nothing under the host"
        # No knowledge-plane read, ever (read performance/identity; AC-15).
        md_reads = [path for path in host_reads if path.endswith(".md")]
        assert not md_reads, (
            f"FAIL: the hot path read knowledge-plane documents: {md_reads}"
        )
        # Only the active feature's state file.
        state_reads = {path for path in host_reads if path.endswith("state.yaml")}
        expected_state = str(host_resolved / "plans" / "nl-screening" / "state.yaml")
        assert state_reads == {expected_state}, (
            f"FAIL: hot-path state reads must be exactly the active "
            f"feature's; got {sorted(state_reads)}"
        )
        # The full read set is the pinned hot path: .heddle.yaml + pointer +
        # one state.yaml (directory listings do not open files).
        allowed = {
            str(host_resolved / ".heddle.yaml"),
            str(host_resolved / ".git" / "heddle" / "active-feature"),
            expected_state,
            # Source observation opens these declared ancestors as directory
            # descriptors. It does not read their knowledge documents.
            str(host_resolved),
            str(host_resolved / "src"),
        }
        unexpected = [path for path in host_reads if path not in allowed]
        assert not unexpected, (
            "FAIL: hot-path opens beyond control files and declared source "
            f"ancestors: {unexpected}"
        )


def test_ac11_non_search_hot_paths_never_load_or_scan_search(tmp_path: Path) -> None:
    """AC-11 survivor: fresh non-search processes never enter search code."""
    host = make_host(tmp_path)
    probe = r"""
import builtins
import contextlib
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

host = Path(sys.argv[1]).resolve()
imports = []
opens = []
connections = []
real_import = builtins.__import__
real_connect = sqlite3.connect

def traced_import(name, *args, **kwargs):
    imports.append(name)
    return real_import(name, *args, **kwargs)

def traced_connect(*args, **kwargs):
    connections.append(repr(args[0]) if args else "<missing>")
    return real_connect(*args, **kwargs)

def audit(event, args):
    if event != "open":
        return
    try:
        path = str(Path(os.fspath(args[0])).resolve())
    except (TypeError, OSError):
        return
    if path.startswith(str(host)):
        opens.append(path)

builtins.__import__ = traced_import
sqlite3.connect = traced_connect
sys.addaudithook(audit)
os.chdir(host)
from heddle.cli import main

codes = []
with (
    contextlib.redirect_stdout(io.StringIO()),
    contextlib.redirect_stderr(io.StringIO()),
):
    for argv in (["status"], ["status", "--json"], ["orient"], ["help"]):
        codes.append(main(argv))

print(json.dumps({
    "codes": codes,
    "imports": imports,
    "opens": opens,
    "connections": connections,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(host)],
        cwd=host,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    assert observation["codes"] == [0, 0, 0, 0]
    forbidden_imports = [
        name
        for name in observation["imports"]
        if name == "heddle.kernel.knowledge_search" or name.startswith("markdown_it")
    ]
    assert not forbidden_imports, (
        f"FAIL AC-11: non-search hot paths imported search code: {forbidden_imports}"
    )
    md_reads = [path for path in observation["opens"] if path.endswith(".md")]
    assert not md_reads, (
        f"FAIL AC-11: non-search hot paths read knowledge Markdown: {md_reads}"
    )
    assert observation["connections"] == [], (
        "FAIL AC-11: non-search hot paths connected to SQLite: "
        f"{observation['connections']}"
    )


def test_ac13_representative_cold_search_budget(tmp_path: Path) -> None:
    """AC-13: one uncached 182-file/~6,681-document search stays below 8 s."""
    try:
        module = importlib.import_module("heddle.kernel.knowledge_search")
    except ModuleNotFoundError as error:
        pytest.fail(
            f"RED AC-13: knowledge-search core is not implemented ({error})",
            pytrace=False,
        )
    from heddle.kernel.project_config import load_project_config

    host = tmp_path / "scaled-host"
    specs = host / "docs" / "features"
    specs.mkdir(parents=True)
    (host / ".heddle.yaml").write_text(
        "layout:\n  specs: docs/features\n",
        encoding="utf-8",
    )

    document_count = 0
    for file_index in range(180):
        sections = [
            (
                f"## Budget section {section_index}\n\n"
                f"budget marker file {file_index} section {section_index}\n"
            )
            for section_index in range(37)
        ]
        document_count += len(sections)
        (specs / f"{file_index:03d}.md").write_text(
            "# Generated fixture\n\n" + "\n".join(sections),
            encoding="utf-8",
        )
    final_sections = [
        (
            f"## Budget tail {section_index}\n\n"
            f"budget marker tail section {section_index}\n"
        )
        for section_index in range(21)
    ]
    document_count += len(final_sections)
    (specs / "180.md").write_text(
        "# Generated fixture\n\n" + "\n".join(final_sections),
        encoding="utf-8",
    )
    (specs / "181.md").write_text(
        "# No searchable sections\n\nThe file-count counterweight.\n",
        encoding="utf-8",
    )
    assert len(list(specs.glob("*.md"))) == 182
    assert document_count == 6681

    before = snapshot_tree(host)
    request = module.SearchRequest(query="budget marker", limit=5)
    config = load_project_config(host)
    start = time.perf_counter()
    hits = module.search_knowledge(config, request)
    elapsed = time.perf_counter() - start
    after = snapshot_tree(host)

    print(
        f"note: cold feature search over 182 files / {document_count} documents "
        f"took {elapsed:.3f}s (target approximately 4s, ceiling 8s)"
    )
    assert len(hits) == 5
    assert elapsed < 8.0, (
        f"FAIL AC-13: cold search took {elapsed:.3f}s; the 8s ceiling blocks "
        "the no-cache MVP and reopens the deferred cache decision"
    )
    assert after == before, "FAIL AC-13: cold search created persistent state"
