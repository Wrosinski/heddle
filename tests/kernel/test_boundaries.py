"""
Structural rails for heddle/kernel/ — milestone foundation (AC-14, kernel half).

Covers: the kernel import boundary (modules under heddle/kernel/ import only
stdlib, ``yaml``, ``heddle.kernel``, and the stdlib-only contract layer
``heddle.contracts`` — the two named leaves relocated there from
``heddle/runtime/`` by pre-authoring hardening dependency boundary, retiring REQ-18's
runtime
exemption outright; feature-search adds one file-specific ``markdown_it``
exception in ``knowledge_search.py``; any ``heddle.runtime`` or
``heddle.gate`` reference fails) and the kernel write-sink ban with an
**empty** allowlist (the kernel is read-only — ``Path.read_text`` is
permitted; ``open``/``exec``/``eval``, write/process sinks, and
``subprocess`` imports are not). Feature Spec §Architecture, structural-test
extensions 2 and 4. The runtime half of AC-14 lives in
tests/runtime/test_boundaries.py (completion-gated reader rails) plus the amended
core import boundary in tests/runtime/test_schemas.py.

Scope note (scaffolding review review): these AST rails catch banned
imports and sinks — a knowledge-plane ``.md`` READ via the permitted
``Path.read_text`` is NOT caught here; that protection is AC-15's runtime
read trace (tests/runtime/test_performance.py, ``assert not md_reads``).
The completion static ``.md``-literal ban below is belt-and-braces beside
that trace (it catches literal paths only, not dynamically built ones).

Behavior contract: reader-kernel-read-model
preimplementation state: heddle/kernel/ does not exist until foundation — the
package-missing
assertion is the expected pre-implementation failure. These AST tests cover
the whole package from day one (plan Milestone 1 Work).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KERNEL_DIR = REPO_ROOT / "heddle" / "kernel"


def _iter_kernel_modules() -> list[Path]:
    assert KERNEL_DIR.is_dir(), (
        "FAIL: heddle/kernel/ package missing — it lands in plan milestone 1 "
        "(red-phase signal until then)"
    )
    # rglob, not glob: the rail must keep covering subpackages as the
    # package grows (gate adds compiler.py next — review path).
    return sorted(KERNEL_DIR.rglob("*.py"))


class TestKernelImportBoundary:
    """Spec §Architecture import discipline (REQ-18, as amended by pre-authoring
    hardening dependency boundary and feature-search): kernel modules import only
    stdlib, yaml,
    heddle.kernel, and the heddle.contracts layer. The one knowledge-plane
    search module may additionally import markdown_it. The kernel never
    imports heddle.runtime (the former two-module exemption moved to
    heddle/contracts/) or heddle.gate."""

    def test_kernel_modules_import_only_declared_roots(self):
        offenders: list[str] = []
        for module_path in _iter_kernel_modules():
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module_refs: list[str] = []
                if isinstance(node, ast.Import):
                    module_refs = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    if node.module is not None:
                        module_refs = [node.module]
                for module_ref in module_refs:
                    root = module_ref.split(".")[0]
                    if root == "yaml":
                        continue  # the ONLY third-party home (Decision Log)
                    if (
                        root == "markdown_it"
                        and module_path.name == "knowledge_search.py"
                    ):
                        continue  # feature-search's one CommonMark exception
                    if root == "heddle":
                        if module_ref == "heddle.kernel" or module_ref.startswith(
                            "heddle.kernel."
                        ):
                            continue
                        if module_ref == "heddle.contracts" or module_ref.startswith(
                            "heddle.contracts."
                        ):
                            continue  # the stdlib-only contract layer
                        # heddle.gate (never), heddle.runtime (never, since
                        # dependency boundary), bare `heddle` — all out of bounds.
                        offenders.append(f"{module_path.name}: {module_ref}")
                    elif root not in sys.stdlib_module_names:
                        offenders.append(f"{module_path.name}: {root}")
        assert not offenders, (
            "FAIL: heddle/kernel modules may import only stdlib, yaml, "
            "heddle.kernel, and heddle.contracts, except markdown_it in "
            "knowledge_search.py only (Feature Spec REQ-13); offending "
            f"imports: {offenders}"
        )

    def test_no_gate_reference_anywhere(self):
        # Belt-and-braces on the spec's "any heddle.gate reference fails":
        # catches string-based/lazy references the import walk cannot see.
        offenders: list[str] = []
        for module_path in _iter_kernel_modules():
            if "heddle.gate" in module_path.read_text(encoding="utf-8"):
                offenders.append(module_path.name)
        assert not offenders, (
            f"FAIL: heddle.gate referenced from kernel modules: {offenders} — "
            "the kernel never imports gate code (gate code imports the kernel)"
        )


def _read_text_calls_outside_decode_guard(tree: ast.AST) -> list[int]:
    """Line numbers of ``.read_text(...)`` calls not enclosed in a ``try``
    whose handlers name both ``OSError`` and ``UnicodeDecodeError``."""
    offenders: list[int] = []

    def handler_names(try_node: ast.Try) -> set[str]:
        names: set[str] = set()
        for handler in try_node.handlers:
            if isinstance(handler.type, ast.Name):
                names.add(handler.type.id)
            elif isinstance(handler.type, ast.Tuple):
                names.update(
                    element.id
                    for element in handler.type.elts
                    if isinstance(element, ast.Name)
                )
        return names

    def visit(node: ast.AST, guarded: bool) -> None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_text"
            and not guarded
        ):
            offenders.append(node.lineno)
        if isinstance(node, ast.Try):
            body_guarded = guarded or {"OSError", "UnicodeDecodeError"} <= (
                handler_names(node)
            )
            for child in node.body:
                visit(child, body_guarded)
            for other in (*node.handlers, *node.orelse, *node.finalbody):
                visit(other, guarded)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return offenders


class TestKernelDecodeGuard:
    """
    completion enforcement (robustness review): every ``.read_text`` on a
        control-plane file must sit in a ``try`` catching both ``OSError`` and
        ``UnicodeDecodeError``, so a byte-corrupt file maps to the typed
        KernelError channel (A6), never the dispatcher's generic ``internal``.
        review was exactly the miss this rail prevents: project_config.py
        complied while state.py caught OSError only.
    """

    def test_every_kernel_read_text_guards_decode_failures(self):
        offenders: list[str] = []
        for module_path in _iter_kernel_modules():
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            offenders.extend(
                f"{module_path.name}:{line}"
                for line in _read_text_calls_outside_decode_guard(tree)
            )
        assert not offenders, (
            "FAIL: every read_text on a control-plane file must catch "
            "UnicodeDecodeError alongside OSError so a byte-corrupt file "
            "maps to workspace-invalid / the feature resolution fallthrough (A6; "
            "robustness "
            "timeout). Wrap the call in `try: ... except (OSError, "
            f"UnicodeDecodeError):` — unguarded calls: {offenders}"
        )


class TestKernelWriteSinkBan:
    """
    Read-only kernel invariant (REQ-17): the core write-sink ban applied to
        heddle/kernel/ with an EMPTY allowlist. File reads via Path.read_text are
        permitted; open()/exec()/eval(), write and process sinks, and subprocess
        imports are not. The one sanctioned runtime write seam
        (heddle/runtime/pointer.py::write_active_feature_pointer) is completion's and
        lives outside the kernel by design (Decision Log).
    """

    _BANNED_CALL_NAMES = frozenset({"open", "exec", "eval"})
    _BANNED_ATTR_CALLS = frozenset(
        {"write", "write_text", "write_bytes", "Popen", "system", "popen"}
    )

    def test_kernel_modules_have_no_write_or_exec_sinks(self):
        offenders: list[str] = []
        for module_path in _iter_kernel_modules():
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [node.module]
                        if isinstance(node, ast.ImportFrom) and node.module
                        else [alias.name for alias in node.names]
                    )
                    for name in names:
                        if name.split(".")[0] == "subprocess":
                            offenders.append(
                                f"{module_path.name}:{node.lineno}: import {name}"
                            )
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Name) and func.id in self._BANNED_CALL_NAMES:
                    offenders.append(f"{module_path.name}:{node.lineno}: {func.id}()")
                elif (
                    isinstance(func, ast.Attribute)
                    and func.attr in self._BANNED_ATTR_CALLS
                ):
                    offenders.append(
                        f"{module_path.name}:{node.lineno}: .{func.attr}()"
                    )
        assert not offenders, (
            "FAIL: heddle/kernel modules must not open files for writing, "
            "spawn processes, or call write/exec/eval sinks — the kernel is "
            "read-only with an empty allowlist (Feature Spec §Invariants; "
            f"REQ-17). Offending sinks: {offenders}"
        )


class TestKnowledgeSearchSQLiteBoundary:
    """Feature-search AC-5: the sole SQLite target is disposable."""

    def test_search_connects_only_to_memory_and_closes_in_finally(self):
        module_path = KERNEL_DIR / "knowledge_search.py"
        assert module_path.is_file(), (
            "RED feature-search: knowledge_search.py must land before its "
            "SQLite boundary can be enforced"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        connect_targets: list[object] = []
        finally_closes = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sqlite3"
                and node.func.attr == "connect"
            ):
                connect_targets.append(
                    node.args[0].value
                    if node.args and isinstance(node.args[0], ast.Constant)
                    else None
                )
            if isinstance(node, ast.Try):
                finally_closes = finally_closes or any(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "close"
                    for statement in node.finalbody
                    for child in ast.walk(statement)
                )
        assert connect_targets == [":memory:"], (
            "FAIL AC-5: knowledge search must have exactly one literal "
            f"sqlite3.connect(':memory:') target, got {connect_targets}"
        )
        assert finally_closes, (
            "FAIL AC-5: the in-memory SQLite connection must close in finally"
        )
