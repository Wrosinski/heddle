"""Replaceable resource resolution precedence and failure behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

# A representative authoritative packaged asset. adoption retired the historical
# self-review tier witness; the autonomy addendum remains part of the doctor
# corpus and exercises the same three-tier resolver contract.
ASSET = "autonomy-addendum.md"


def _config_for(root: Path):
    from heddle.kernel.project_config import load_project_config

    (root / ".heddle.yaml").write_text("layout:\n  prompts: .heddle/prompts\n", "utf-8")
    return load_project_config(root)


def _place(root: Path, tier_dir: str, body: str) -> Path:
    target = root / ".heddle" / tier_dir / ASSET
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def test_ac04_override_beats_generated_beats_packaged(tmp_path: Path) -> None:
    # AC-4 case (c): asset present in all three tiers → override wins, the
    # source tag is "override".
    from heddle.kernel.resources import resolve_resource

    config = _config_for(tmp_path)
    override = _place(tmp_path, "overrides", "OVERRIDE")
    _place(tmp_path, "generated", "GENERATED")

    path, source = resolve_resource(ASSET, config)
    assert source == "override", (
        f"FAIL: with all three tiers present, source must be 'override', got "
        f"{source!r} (AC-4 / resource precedence precedence)"
    )
    assert path == override, f"FAIL: resolved path {path} != override {override}"


def test_ac04_generated_beats_packaged(tmp_path: Path) -> None:
    # AC-4 case (b): packaged + generated, no override → generated wins, the
    # source tag is "generated".
    from heddle.kernel.resources import resolve_resource

    config = _config_for(tmp_path)
    generated = _place(tmp_path, "generated", "GENERATED")

    path, source = resolve_resource(ASSET, config)
    assert source == "generated", (
        f"FAIL: with no override, source must be 'generated', got {source!r} (AC-4)"
    )
    assert path == generated, f"FAIL: resolved path {path} != generated {generated}"


def test_ac04_packaged_default_is_the_floor(tmp_path: Path) -> None:
    # AC-4 case (a): asset absent from both host tiers → the packaged default
    # resolves, source tag "packaged".
    from heddle.kernel.resources import resolve_resource

    config = _config_for(tmp_path)
    # No override/generated placed: only the packaged default can answer.
    # The no-packaged-default boundary — a fatal resource diagnostic naming the
    # searched precedence — is covered by
    # test_ac04_missing_asset_in_all_tiers_is_reported below.
    path, source = resolve_resource(ASSET, config)
    assert source == "packaged", (
        f"FAIL: with no host-tier asset, source must be 'packaged', got "
        f"{source!r} (AC-4) — and the path must point inside the installed "
        "package, not the host tree"
    )
    assert tmp_path not in path.parents, (
        "FAIL: a 'packaged' resolution must not come from the host tree"
    )


def test_ac04_missing_asset_in_all_tiers_is_reported(tmp_path: Path) -> None:
    # AC-4 boundary: not found in any tier → a clear, surfaced failure naming
    # the searched precedence (consumed by doctor as a fatal resource
    # diagnostic, AC-14). The exact exception/return contract is the
    # implementing agent's; this asserts the searched tiers are named.
    from heddle.kernel.resources import resolve_resource

    config = _config_for(tmp_path)
    with pytest.raises(Exception) as excinfo:  # noqa: B017 - exact type set at foundation
        resolve_resource("definitely-absent-asset-xyz.md", config)
    # review: the broad catch narrows once foundation fixes the resolver's error
    # contract; until then, reject signature/coding errors so a wrong call or a
    # missing module cannot masquerade as the not-found path.
    assert not isinstance(
        excinfo.value, (TypeError, AttributeError, ImportError, NameError)
    ), (
        f"FAIL: a not-found lookup must raise a resource error, not a "
        f"signature/coding error — got {type(excinfo.value).__name__} "
        f"({excinfo.value})"
    )
    message = str(excinfo.value)
    assert "packaged" in message.lower(), (
        "FAIL: a not-found resource error must name the searched precedence "
        "(override/generated/packaged) so doctor can report it (AC-4/AC-14)"
    )
