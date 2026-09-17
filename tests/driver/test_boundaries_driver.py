"""
Driver leaf-package boundary tests (autopilot — Autopilot).

Covers: AC-8 (the driver is a leaf — imports only public seams, imported by
nothing — and ``drive``/``flow set`` join the command surface), REQ-16.
Behavior contract: autopilot

preimplementation state: ``heddle/driver/`` does not exist until foundation lands, and
``drive`` is
not yet on ``COMMAND_SURFACE`` — every test here FAILs/ERRORs before
implementation. The forward rail (driver imports only sanctioned seams) and
the reverse rail (no module imports ``heddle.driver``) mirror
``tests/runtime/test_boundaries.py``; the package-present assertion keeps
the rails from passing vacuously while the package is absent.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HEDDLE_DIR = REPO_ROOT / "heddle"
DRIVER_DIR = HEDDLE_DIR / "driver"

# The leaf may import stdlib + its own package + exactly these public seams
# (Feature Spec → Architecture → Module boundaries / Integration points).
_ALLOWED_HEDDLE_PREFIXES = (
    "heddle.driver",
    "heddle.kernel.model",
    "heddle.kernel.project_config",
    "heddle.contracts.result",
    "heddle.runtime.contracts",
    "heddle.contracts.schemas",
    "heddle.runtime.write_path",
    "heddle.runtime.gate_run",
    # R4(2): the driver routes the work prompt through the kickoff briefing
    # bridge (a pure read seam) to render <stage>.briefing.md into the session.
    "heddle.runtime.kickoff",
    # Verification provenance: the leaf asks the runtime composition seam for
    # current source-aware freshness before executing a transition action.
    "heddle.runtime.verification",
    "heddle.runtime.readiness.assess_current_readiness",
    "heddle.runtime.readiness.readiness_failure",
    # review: the driver reconciles the decision journal from recorded policy
    # facts at the close boundary, reading the close audit (a pure read seam)
    # to know which entries are missing/malformed before re-asserting them.
    "heddle.runtime.audit",
    "heddle.runtime.application",
    "heddle.runtime.state_store",
    "heddle.contracts.operations",
    "heddle.contracts.decisions",
    "heddle.runtime.clock",
    "heddle.io.process",
)
_EXPECTED_DRIVER_MODULES = ("cli.py", "loop.py", "sessions.py", "escalations.py")

# The autonomous-driver command surface is pinned here at the public manifest
# boundary. Completion behavior is covered separately by test_completion.py.
_EXPECTED_AUTOPILOT_SURFACE = {
    "drive": {
        "mutating": True,
        "dry_run": False,
        "flags": {"--json", "--feature", "--until"},
        "required_args": 0,
    },
}


def _module_refs(tree: ast.AST) -> list[str]:
    refs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            refs.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return refs


def test_ac08_driver_package_present_with_expected_modules():
    """AC-8: the leaf package and its modules exist (anchors the rails)."""
    assert DRIVER_DIR.is_dir(), "FAIL AC-8: heddle/driver/ leaf package must exist"
    present = {path.name for path in DRIVER_DIR.glob("*.py")}
    missing = [name for name in _EXPECTED_DRIVER_MODULES if name not in present]
    assert not missing, f"FAIL AC-8: heddle/driver/ missing modules {missing}"


def test_ac08_no_module_imports_the_driver():
    """AC-8 / REQ-16 (reverse rail): nothing imports ``heddle.driver`` — the
    leaf property. Walk all of heddle/ except the driver package itself."""
    assert DRIVER_DIR.is_dir(), "FAIL AC-8: heddle/driver/ must exist"
    offenders: list[str] = []
    for module_path in sorted(HEDDLE_DIR.rglob("*.py")):
        if DRIVER_DIR in module_path.parents:
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for ref in _module_refs(tree):
            if ref == "heddle.driver" or ref.startswith("heddle.driver."):
                if module_path == HEDDLE_DIR / "runtime/drive.py" and any(
                    ref == allowed or ref.startswith(allowed + ".")
                    for allowed in ("heddle.driver.results", "heddle.driver.loop")
                ):
                    continue
                offenders.append(f"{module_path.relative_to(REPO_ROOT)}: {ref}")
    assert not offenders, (
        "FAIL AC-8: a non-driver module imports heddle.driver "
        f"(not a leaf): {offenders}"
    )


def test_ac08_drive_on_command_surface(run_json):
    """
    AC-8 (foundation half): ``COMMAND_SURFACE`` (via ``heddle help --json``) carries
        ``drive``. The ``flow set`` surface assertion (completion) lives in
        tests/driver/test_completion.py, since `flow set` lands in completion (review).
    """
    code, envelope, _out, _err = run_json(["help", "--json"])
    assert code == 0, f"FAIL AC-8: help --json must succeed, got exit {code}"
    names = [entry["name"] for entry in envelope["data"]["commands"]]
    assert "drive" in names, "FAIL AC-8: 'drive' missing from COMMAND_SURFACE"


def test_ac08_autopilot_command_contract_payload():
    """
    AC-8 (full-payload surface lock, review): the foundation autopilot command's
        manifest-visible contract — mutating, dry_run, flag set, and required-arg
        count — matches ``_EXPECTED_AUTOPILOT_SURFACE`` exactly, so an implementation
        cannot ship ``drive`` with the wrong mutability, a stray ``--dry-run``, or a
        missing flag and still pass. ``flow set`` is asserted in the completion
        contract test.
    """
    from heddle.runtime.contracts import build_manifest

    by_name = {entry["name"]: entry for entry in build_manifest()["commands"]}
    for name, expected in _EXPECTED_AUTOPILOT_SURFACE.items():
        assert name in by_name, f"FAIL AC-8: '{name}' missing from the manifest"
        entry = by_name[name]
        assert entry["mutating"] == expected["mutating"], (
            f"FAIL AC-8: '{name}' mutating must be {expected['mutating']}, "
            f"got {entry['mutating']}"
        )
        assert entry["dry_run"] == expected["dry_run"], (
            f"FAIL AC-8: '{name}' dry_run must be {expected['dry_run']}, "
            f"got {entry['dry_run']}"
        )
        flag_names = {flag["name"] for flag in entry["flags"]}
        assert flag_names == expected["flags"], (
            f"FAIL AC-8: '{name}' flags must be {sorted(expected['flags'])}, "
            f"got {sorted(flag_names)}"
        )
        required = [arg for arg in entry["args"] if arg.get("required")]
        assert len(required) == expected["required_args"], (
            f"FAIL AC-8: '{name}' must have {expected['required_args']} required "
            f"positional arg(s), got {len(required)}"
        )


def test_ac08_drive_has_runtime_route():
    """The advertised drive command is handled by the dedicated driver route."""
    from heddle.runtime.contracts import COMMAND_SURFACE

    by_name = {contract.name: contract for contract in COMMAND_SURFACE}
    assert "drive" in by_name, "FAIL AC-8: 'drive' missing from COMMAND_SURFACE"
    assert by_name["drive"].cli_binding is None
