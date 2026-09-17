"""Runtime envelope and exit semantics for run-gate."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"


def _run_gate_landed() -> bool:
    from heddle.runtime.contracts import COMMAND_SURFACE

    run_gate = next((c for c in COMMAND_SURFACE if c.name == "run-gate"), None)
    return run_gate is not None and any(f.name == "--json" for f in run_gate.flags)


# Feature landed: a regression that removed the integration surface must FAIL here, not
# silently skip the module (review integration; plan write T5 — retire red-phase
# skip sentinels to hard asserts at completion).
assert _run_gate_landed(), (
    "run-gate must return its JSON envelope — a regression here must "
    "fail loudly, not skip this module"
)


def test_ac06_manifest_adds_json_and_feature_additively() -> None:
    # AC-6: the run-gate manifest entry now lists --json and --feature; its
    # exit-code set stays {0,1,2,3,4} (no exit-5 — A4).
    from heddle.runtime.contracts import COMMAND_SURFACE

    run_gate = next(c for c in COMMAND_SURFACE if c.name == "run-gate")
    flag_names = {f.name for f in run_gate.flags}
    assert {"--json", "--feature"} <= flag_names, (
        f"FAIL: run-gate flags {sorted(flag_names)} must include --json and "
        "--feature (AC-6/gate envelope)"
    )
    assert "--cli" in flag_names, (
        "FAIL: the original --cli flag must remain (additive growth only)"
    )
    assert tuple(run_gate.exit_codes) == (0, 1, 2, 3, 4), (
        f"FAIL: run-gate exit codes {run_gate.exit_codes} must stay "
        "{0,1,2,3,4} — append-only writer, no CAS/exit-5 (A4)"
    )


def test_ac06_dispatch_keeps_gate_import_lazy() -> None:
    # AC-6/REQ-13 (lazy-import half): the engine import stays the lazy
    # function-level `heddle.gate.entry` seam (review) — no module-level
    # heddle.gate import in the dispatch path. Structural assertion over
    # dispatch source. (The "gate args reach the engine through the seam" half
    # of REQ-13 is exercised behaviorally by the run-gate tests above, which pass
    # `self-review` + flags through dispatch to the engine — review: this test's
    # name now matches the property it actually pins. Checkpoint 2 lifecycle boundary:
    # the core
    # "verbatim forward to entry.main" wording is retired from REQ-13.)
    import ast

    src = (REPO_ROOT / "heddle" / "runtime" / "dispatch.py").read_text("utf-8")
    tree = ast.parse(src)
    module_level_gate_imports = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and "heddle.gate" in ast.dump(node)
    ]
    assert not module_level_gate_imports, (
        "FAIL: heddle.gate must stay a function-level import inside the "
        "dispatch path (INV-5) — no module-level gate import in dispatch.py"
    )
