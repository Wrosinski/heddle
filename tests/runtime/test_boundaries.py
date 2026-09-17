"""
reader runtime structural rails — milestone completion (AC-14, runtime half).

Covers: the runtime import boundary as extended by reader (heddle/runtime/
modules may import stdlib, ``heddle.runtime``, and ``heddle.kernel`` — the
spec-declared exception — plus the existing ``heddle.gate.entry`` lazy seam
inside ``dispatch.run()``; still no third-party imports) and the runtime
write-sink ban with the ONE spec-declared allowlist entry: write sinks are
permitted only inside ``heddle/runtime/pointer.py``'s
``write_active_feature_pointer`` (the feature resolution session-state exemption).
Feature Spec §Architecture, structural-test extensions 1 and 3; the kernel
half lives in tests/kernel/test_boundaries.py.

Behavior contract: reader-kernel-read-model
preimplementation state (retired): importorskip-gated on heddle.runtime.pointer until
completion landed the write seam; guard retired to a hard import at feature
completion (pattern red-phase-always-green-scaffolding, step 5). The core
rails in tests/runtime/test_schemas.py were amended in the same completion change
(the contract-amendment sweep — scaffolding review review) and now assert
the same single seam.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from heddle.kernel.managed_regions import PLAN_STATUS_ID
from tests.runtime.policy_helpers import (
    assert_runtime_import_edges,
    assert_runtime_write_seams,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_DIR = REPO_ROOT / "heddle" / "runtime"
KERNEL_DIR = REPO_ROOT / "heddle" / "kernel"


def _iter_runtime_modules() -> list[Path]:
    assert RUNTIME_DIR.is_dir(), "FAIL: heddle/runtime/ package missing"
    # rglob, not glob: the rail must keep covering subpackages as the
    # package grows (review path).
    return sorted(RUNTIME_DIR.rglob("*.py"))


class TestRuntimeImportBoundary:
    """
    AC-14 (runtime import rail, reader form plus write write-path exceptions):
        heddle/runtime/ modules import stdlib, heddle.runtime, heddle.kernel,
        and heddle.contracts (the stdlib-only contract layer the leaves moved
        to — pre-authoring hardening dependency boundary) only, except `write_path.py`
        may import
        PyYAML for authored payloads. The exact shared-I/O crossings allow
        verification execution and Git-backed source/ownership reads.
    """

    def test_runtime_modules_import_only_declared_roots(self):
        assert_runtime_import_edges(RUNTIME_DIR)

    def test_shared_consumers_do_not_import_private_write_helpers(self):
        offenders = []
        for path in _iter_runtime_modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "heddle.runtime.write_path"
                ):
                    offenders.extend(
                        f"{path.name}:{node.lineno}: {alias.name}"
                        for alias in node.names
                        if alias.name.startswith("_")
                    )
        assert not offenders, "shared runtime helpers need public owners: " + str(
            offenders
        )


def _read_text_calls_outside_decode_guard(tree: ast.AST) -> list[int]:
    """Line numbers of ``.read_text(...)`` calls not enclosed in a ``try``
    whose handlers name both ``OSError`` and ``UnicodeDecodeError``.
    (Mirror of the kernel rail in tests/kernel/test_boundaries.py.)"""
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
            names = handler_names(node)
            body_guarded = guarded or (
                "OSError" in names
                and bool({"UnicodeDecodeError", "UnicodeError", "ValueError"} & names)
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


class TestRuntimeDecodeGuard:
    """
    completion enforcement (robustness review rail, runtime half): vacuous
        today (heddle/runtime/ performs no read_text — reads live in the
        kernel) and bites the moment write's writer/reader growth adds one
        without the (OSError, UnicodeDecodeError) guard.
    """

    def test_every_runtime_read_text_guards_decode_failures(self):
        offenders: list[str] = []
        for module_path in _iter_runtime_modules():
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            offenders.extend(
                f"{module_path.name}:{line}"
                for line in _read_text_calls_outside_decode_guard(tree)
            )
        assert not offenders, (
            "FAIL: every read_text on a control-plane file must catch "
            "UnicodeDecodeError alongside OSError so a byte-corrupt file "
            "stays on the typed error channel (A6; robustness timeout). "
            "Wrap the call in `try: ... except (OSError, "
            f"UnicodeDecodeError):` — unguarded calls: {offenders}"
        )

    @pytest.mark.parametrize(
        ("caught", "guarded"),
        [
            ("OSError, UnicodeDecodeError", True),
            ("OSError, UnicodeError", True),
            ("OSError, ValueError", True),
            ("OSError", False),
            ("ValueError", False),
            ("Exception", False),
        ],
    )
    def test_decode_guard_recognizes_actual_exception_ancestry(self, caught, guarded):
        # UnicodeDecodeError is a ValueError. Recognize that real handler while
        # retaining the separate filesystem-error and explicit-handler rails.
        tree = ast.parse(
            "try:\n    path.read_text(encoding='utf-8')\n"
            f"except ({caught}):\n    pass\n"
        )
        assert (not _read_text_calls_outside_decode_guard(tree)) is guarded


class TestPointerWriteAtomicity:
    """
    completion enforcement (8→9 gate configuration, robustness review/review): the
        sanctioned pointer write must stay atomic — one ``.write_text`` to a
        sibling temp file renamed over the pointer via ``os.replace`` — so a
        failed or concurrent write can never truncate the previously recorded
        pointer. write's state writer inherits this pattern.
    """

    def test_seam_writes_temp_then_os_replace(self):
        seam_path = RUNTIME_DIR / "pointer.py"
        tree = ast.parse(seam_path.read_text(encoding="utf-8"))
        seam = next(
            (
                statement
                for statement in tree.body
                if isinstance(statement, ast.FunctionDef)
                and statement.name == "write_active_feature_pointer"
            ),
            None,
        )
        assert seam is not None, (
            "FAIL: pointer.py::write_active_feature_pointer missing — the "
            "feature resolution seam function is the pinned pointer-write home (REQ-17)"
        )
        write_text_lines = [
            node.lineno
            for node in ast.walk(seam)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
        ]
        replace_calls = [
            node
            for node in ast.walk(seam)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        ]
        assert len(write_text_lines) == 1 and replace_calls, (
            "FAIL: the pointer write must be atomic — exactly one "
            "write_text (to a sibling temp file) followed by os.replace "
            "onto the pointer; a truncating in-place write_text loses the "
            "prior pointer on mid-write failure (8→9 gate configuration, robustness "
            "partial-write and atomic recovery). Found write_text at "
            f"{write_text_lines}, "
            f"os.replace calls: {len(replace_calls)}"
        )


class TestRuntimeWriteSinkBan:
    """
    AC-14 (runtime write rail, reader form) as amended by gate: the core ban set
        with explicit allowlisted seams — including write sinks inside
        heddle/runtime/pointer.py::write_active_feature_pointer (the feature resolution
        session-state exemption; REQ-17) and heddle/runtime/recording.py::
        _atomic_write (the gate append-only gate-run ledger sink; gate-gates-runtime
        AC-10/REQ-10). Gate-policy R-16's workspace-directory advisory lock creates
        no file and adds no write seam. Everything else in heddle/runtime/ stays
        read-and-render; subprocess imports stay banned everywhere.
    """

    def test_write_sinks_only_inside_the_pointer_seam(self):
        assert_runtime_write_seams(RUNTIME_DIR)


def test_kernel_write_transforms_do_not_import_runtime() -> None:
    """
    write AC-2: once the pure write transforms land, that module must stay
        runtime-free. Since pre-authoring hardening dependency boundary moved the two
        stdlib-only
        contract leaves to heddle/contracts/, reader's former per-module exemption
        is retired: NO kernel module may import heddle.runtime at all.
    """

    # review: the pure write transforms have landed, so retire the red-phase
    # importorskip to a hard import — a missing/renamed module must now ERROR
    # loudly rather than silently skip this boundary check.
    import heddle.kernel.write_path  # noqa: F401

    offenders: list[str] = []
    for module_path in sorted(KERNEL_DIR.rglob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module_refs: list[str] = []
            if isinstance(node, ast.Import):
                module_refs = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module is not None:
                    module_refs = [node.module]
            for module_ref in module_refs:
                if module_ref == "heddle.runtime" or module_ref.startswith(
                    "heddle.runtime."
                ):
                    offenders.append(f"{module_path.name}:{node.lineno}: {module_ref}")
    assert not offenders, (
        "FAIL AC-2: kernel modules must not import heddle.runtime — the "
        "shared contract leaves live in heddle/contracts/ (M2 exemption "
        "retired by pre-M6 hardening dependency boundary). "
        f"Offenders: {offenders}"
    )


@pytest.mark.parametrize("shape", ["non-utf8", "directory"])
def test_sync_final_reread_shape_faults_stay_workspace_invalid(
    shape: str,
    tmp_path: Path,
) -> None:
    """
    review: a post-preflight shape change is provable divergence.

        The historical test identifier is retained for the deletion guard; the
        robustness user ruling supersedes its earlier REQ-9 expectation.

    """
    from heddle.runtime import sync

    target = tmp_path / "plan.md"
    if shape == "non-utf8":
        target.write_bytes(b"\xff\xfe")
    else:
        target.mkdir()
    projection = sync._Projection(
        block=PLAN_STATUS_ID,
        display_path="plans/demo/plan.md",
        path=target,
        before="preflight bytes\n",
        text="projected bytes\n",
    )

    assert sync._target_matches_preflight(projection) is False


def test_gate_identity_seam_has_no_superseded_cache_key_passthroughs() -> None:
    """PreparedGateRun is the only live gate/cache identity handoff."""
    from heddle.gate import entry

    for name in ("synthesis_inputs_digest", "compute_input_hash"):
        assert not hasattr(entry, name)
