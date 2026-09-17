"""Finding taxonomy shape and compatibility behavior."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACES = REPO_ROOT / "tests" / "fixtures" / "workspaces"
CURRENT_KEYS = {"by_severity", "by_classification", "total", "contradictions"}


def _taxonomy_migrated() -> bool:
    from heddle.kernel.state import FINDINGS_KEYS

    return tuple(FINDINGS_KEYS) != ("category_a", "category_b")


# Feature landed: a regression that reverted FINDINGS_KEYS to the legacy
# category_a/category_b pair must FAIL here, not silently skip the module (peer
# review review; plan write T5 — retire red-phase skip sentinels to hard asserts at
# completion).
assert _taxonomy_migrated(), (
    "the current finding taxonomy must be active — a "
    "regression "
    "here must fail loudly, not skip this module"
)


def test_ac11_findings_keys_carry_the_current_shape() -> None:
    # AC-11: FINDINGS_KEYS is the finding taxonomy sub-schema, not the legacy pair.
    from heddle.kernel.state import FINDINGS_KEYS

    assert set(FINDINGS_KEYS) == CURRENT_KEYS, (
        f"FAIL: FINDINGS_KEYS {tuple(FINDINGS_KEYS)} has the wrong shape "
        f"{sorted(CURRENT_KEYS)} (REQ-11)"
    )


def test_ac11_no_legacy_taxonomy_residue_in_source_or_fixtures() -> None:
    # AC-11: no category_a / category_b remains in heddle/ or tests/fixtures/.
    offenders: list[str] = []
    for root in (REPO_ROOT / "heddle", WORKSPACES):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".yaml", ".yml"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "category_a" in text or "category_b" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "FAIL: legacy {category_a, category_b} taxonomy still present in "
        f"{offenders} — finding taxonomy replaces it everywhere in heddle/ and "
        "tests/fixtures/ (AC-11)"
    )


def test_ac11_kernel_types_migrated_fixtures_without_workspace_invalid() -> None:
    # AC-11: the kernel reads the migrated fixtures cleanly — the finding taxonomy shape
    # is
    # what GateRun validation now accepts (no workspace-invalid).
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    for host in ("tiny", "golden"):
        config = load_project_config(WORKSPACES / host)
        plans = WORKSPACES / host / "plans"
        for workspace in sorted(plans.iterdir()):
            if not (workspace / "state.yaml").is_file():
                continue
            # Must not raise KernelError(workspace-invalid).
            snapshot = resolve_snapshot(config, workspace.name)
            assert snapshot.feature == workspace.name
