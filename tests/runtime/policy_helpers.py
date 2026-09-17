"""
Shared test-policy constants (authoring code-quality reader review, option 3).

The runtime write-seam allowlist and the landed-milestones stub-sweep
exception each lived as three independent literals, so a partial landing
required remembering every lockstep copy. These are the single derived
inputs the scanning tests consume; the LITERAL re-pin lives in
tests/runtime/test_policy_locks.py — the independent contract tripwire —
so growing either policy stays a deliberate two-file act: extend here,
re-pin there.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# module filename -> function names whose atomic write_text is sanctioned.
# The spec-declared write seams: the feature resolution pointer write (reader), the gate
# append-only gate-run ledger sink, the write write-path pair, the two authoring
# authoring seams, sync's shared projection installer, and the adoption
# host-tooling trajectory-archival pair (record + aggregate, owner
# rulings 6/7 2026-07-22), and the operational core evidence publisher and exact
# artifact cleanup seam.
WRITE_SEAMS: dict[str, frozenset[str]] = {
    "pointer.py": frozenset({"write_active_feature_pointer"}),
    "state_store.py": frozenset({"_write_text"}),
    "write_path.py": frozenset({"stamp_lifecycle"}),
    "completion.py": frozenset(
        {"_stamp", "_publish_archive", "_cleanup", "cleanup_artifacts"}
    ),
    "verification.py": frozenset({"replace_file_bytes"}),
    "sync.py": frozenset({"install_projection"}),
    "trajectory.py": frozenset({"_atomic_write_json", "write_aggregate"}),
}


def write_seam_pairs() -> frozenset[tuple[str, str]]:
    """(module filename, function name) pairs derived from WRITE_SEAMS."""
    return frozenset(
        (module_name, function_name)
        for module_name, function_names in WRITE_SEAMS.items()
        for function_name in function_names
    )


def assert_runtime_write_seams(runtime_dir: Path) -> None:
    """Assert the allowlist matches every runtime write sink in real source.

    ``WRITE_SEAMS`` is only the declaration.  This shared structural oracle
    proves both directions of the policy: every banned sink is inside a named
    seam, and every named seam contains a sanctioned file write.  The
    independent literal value-lock remains in ``test_policy_locks.py``.
    """
    assert runtime_dir.is_dir(), "FAIL: heddle/runtime/ package missing"
    banned_call_names = frozenset({"open", "exec", "eval"})
    banned_attr_calls = frozenset(
        {"write", "write_text", "write_bytes", "Popen", "system", "popen"}
    )
    offenders: list[str] = []
    seams_seen: set[str] = set()

    for module_path in sorted(runtime_dir.rglob("*.py")):
        relative = module_path.relative_to(runtime_dir).as_posix()
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        seam_subtree: set[ast.AST] = set()
        seam_functions = WRITE_SEAMS.get(relative, set())
        if seam_functions:
            for statement in tree.body:
                if (
                    isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and statement.name in seam_functions
                ):
                    seam_subtree.update(ast.walk(statement))

        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom) and node.module
                    else [alias.name for alias in node.names]
                )
                for name in names:
                    if name.split(".")[0] == "subprocess" and relative != "drive.py":
                        offenders.append(f"{relative}:{node.lineno}: import {name}")
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_banned_name = isinstance(func, ast.Name) and func.id in banned_call_names
            sanctioned_writes = {"write", "write_text", "write_bytes"}
            if relative == "completion.py":
                sanctioned_writes.update({"addfile", "unlink"})
            is_banned_attr = (
                isinstance(func, ast.Attribute)
                and func.attr in banned_attr_calls | sanctioned_writes
            )
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            ):
                owner = node
                while owner in parents and not isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    owner = parents[owner]
                if not (
                    relative == "drive.py"
                    and isinstance(owner, ast.FunctionDef)
                    and owner.name == "_notify"
                    and func.attr == "run"
                ):
                    offenders.append(
                        f"{relative}:{node.lineno}: subprocess.{func.attr}()"
                    )
            if not (is_banned_name or is_banned_attr):
                continue
            if (
                node in seam_subtree
                and isinstance(func, ast.Attribute)
                and func.attr in sanctioned_writes
            ):
                owner: ast.AST = node
                while owner in parents and not isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    owner = parents[owner]
                owner_name = (
                    owner.name
                    if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else ""
                )
                seams_seen.add(f"{relative}::{owner_name}")
                continue
            label = func.id if isinstance(func, ast.Name) else f".{func.attr}"
            offenders.append(f"{relative}:{node.lineno}: {label}()")

    assert not offenders, (
        "FAIL: runtime write sinks must live only in the sanctioned named "
        f"seams; offending sinks: {offenders}"
    )
    expected_seams = {
        f"{module_name}::{function_name}"
        for module_name, function_names in WRITE_SEAMS.items()
        for function_name in function_names
    }
    assert seams_seen == expected_seams, (
        "FAIL: each sanctioned runtime write seam must contain its atomic "
        f"write_text; saw {sorted(seams_seen)}, expected {sorted(expected_seams)}"
    )


# W2 declares application crossings by their exact importing file and API.
RUNTIME_IMPORT_EDGES = {
    "decisions.py": {"heddle.gate.entry"},
    "doctor.py": {
        "heddle.gate.entry",
        "heddle.gate.prompt",
        "heddle.gate.registry",
    },
    "show_prompt.py": {
        "heddle.gate.cli",
        "heddle.gate.entry",
        "heddle.gate.preparation",
        "heddle.gate.prompt",
        "heddle.gate.registry",
        "heddle.gate.types",
    },
    "trajectory.py": {"heddle.gate.entry", "heddle.gate.registry"},
    "gate_run.py": {
        "heddle.gate.entry",
        "heddle.gate.preparation",
        "heddle.gate.registry",
        "heddle.gate.types",
    },
    "recording.py": {"heddle.gate.entry"},
    "document_reviews.py": {"heddle.gate.entry", "heddle.io.source"},
    "review_assignments.py": {
        "heddle.gate.cli",
        "heddle.gate.entry",
        "heddle.gate.preparation",
        "heddle.gate.registry",
        "heddle.gate.types",
        "heddle.io.source",
    },
    "migration_history.py": {"heddle.gate.entry"},
    "intake.py": {"yaml", "heddle.io.source"},
    "feature_start.py": {"heddle.io.git"},
    "readiness.py": {"heddle.gate.entry"},
    "peer_approval.py": {"heddle.gate.entry", "heddle.io.source"},
    "robustness_approval.py": {"heddle.gate.entry", "heddle.io.source"},
    "guardrails.py": {"heddle.gate.entry", "heddle.gate.prompt"},
    "validate.py": {"heddle.io.git"},
    "verification.py": {"heddle.io.git", "heddle.io.source"},
    "completion.py": {"heddle.io.git"},
    "verify_exec.py": {"heddle.io.process"},
    "drive.py": {"heddle.driver.results", "heddle.driver.loop"},
    "state_store.py": {"yaml"},
    "write_path.py": {"yaml"},  # Authoring payload decoding, never state serialization.
}


def import_references(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        if node.module in {
            "heddle.gate",
            "heddle.driver",
            "heddle.contracts",
            "heddle.io",
        }:
            return [f"{node.module}.{alias.name}" for alias in node.names]
        return [node.module]
    return []


def assert_runtime_import_edges(runtime_dir):
    offenders = []
    for path in sorted(runtime_dir.rglob("*.py")):
        relative = path.relative_to(runtime_dir).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for module in import_references(node):
                root = module.split(".")[0]
                if root in sys.stdlib_module_names:
                    continue
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in (
                        "heddle.runtime",
                        "heddle.kernel",
                        "heddle.contracts",
                    )
                ):
                    continue
                if module in RUNTIME_IMPORT_EDGES.get(relative, set()):
                    continue
                offenders.append(f"{relative}:{node.lineno}: {module}")
    assert not offenders, f"Undeclared runtime import crossings: {offenders}"
