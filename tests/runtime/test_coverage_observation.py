"""AC-7: advisory observation on early reads and successful/blocked writes."""

import pytest

from tests.proof_continuity_helpers import (
    FEATURE,
    diagnostics,
    execute,
    ops,
    read,
    source_host,
    verify,
    write,
)
from tests.tiering_helpers import snapshot


@pytest.mark.parametrize(
    "surface", ["validate", "status", "advance", "phase", "blocked-phase", "noop-phase"]
)
def test_early_coverage_advice_survives_each_result_path(
    tmp_path, monkeypatch, surface
):
    stage = (
        "scaffold"
        if surface in {"phase", "blocked-phase", "noop-phase"}
        else "implement"
    )
    root, path = source_host(tmp_path, monkeypatch, stage=stage)
    if surface == "advance":
        verify(path, "m1")
    if surface == "blocked-phase":
        from tests.operational_model_helpers import decision

        state = read(path)
        state["decisions"] = [decision("D1", kind="question")]
        write(path, state)
    (root / "src/unowned.py").write_text("EXTRA = 1\n")
    operation = {
        "validate": ops.Validate(feature=FEATURE),
        "status": ops.Status(feature=FEATURE),
        "advance": ops.MilestoneAdvance(feature=FEATURE),
        "phase": ops.PhaseExit(feature=FEATURE, through="implement"),
        "blocked-phase": ops.PhaseExit(
            feature=FEATURE, through="implement", dry_run=True
        ),
        "noop-phase": ops.PhaseExit(feature=FEATURE, through="scaffold"),
    }[surface]
    before = snapshot(root)
    result = execute(operation)
    assert result.ok is (surface != "blocked-phase"), result.to_envelope()
    advice = diagnostics(result)
    assert "src/unowned.py" in advice, result.to_envelope()
    assert "own" in advice.lower() and "attribut" in advice.lower(), advice
    if surface in {"validate", "status", "blocked-phase", "noop-phase"}:
        assert snapshot(root) == before
    elif surface == "advance":
        assert read(path)["milestones"][0]["status"] == "done"
    else:
        assert read(path)["stage"] == "implement"


def test_empty_early_ownership_observes_changes_without_final_proof_refusal(
    tmp_path, monkeypatch
):
    root, _path = source_host(tmp_path, monkeypatch, stage="spec-review", owns=[])
    before = snapshot(root)
    result = execute(ops.Status(feature=FEATURE))
    assert result.ok, result.to_envelope()
    assert "src/example.py" in diagnostics(result)
    assert snapshot(root) == before


def test_unavailable_baseline_is_unknown_not_clean_or_a_fatal_early_check(
    tmp_path, monkeypatch
):
    from heddle.io import git

    root, _path = source_host(tmp_path, monkeypatch)

    def unavailable(*_args, **_kwargs):
        raise git.FeatureBaselineError("baseline-missing", "No synthetic baseline")

    monkeypatch.setattr(git, "resolve_feature_baseline", unavailable)
    before = snapshot(root)
    result = execute(ops.Status(feature=FEATURE))
    assert result.ok, result.to_envelope()
    advice = diagnostics(result).lower()
    assert "baseline" in advice and any(
        word in advice for word in ("unknown", "unavailable", "missing")
    ), advice
    assert snapshot(root) == before


@pytest.mark.parametrize("stale", [False, True])
def test_empty_ownership_still_qualifies_existing_attribution(
    tmp_path, monkeypatch, stale
):
    root, path = source_host(tmp_path, monkeypatch, stage="spec-review")
    (root / "outside.txt").write_text("Independent change.\n")
    (root / "origin.md").write_text("Independent owner and scope.\n")
    result = execute(
        ops.AttributeSources(
            {
                "schema": "heddle.source-attribution-input/v1",
                "attributions": [
                    {
                        "paths": ["outside.txt", "origin.md"],
                        "references": ["origin.md"],
                        "reason": "Other feature's work.",
                    }
                ],
            },
            feature=FEATURE,
        )
    )
    assert result.ok, result.to_envelope()
    state = read(path)
    state["milestones"][0]["owns"] = []
    write(path, state)
    if stale:
        (root / "origin.md").write_text("Changed attribution evidence.\n")
    before = snapshot(root)
    result = execute(ops.Status(feature=FEATURE))
    if stale:
        assert not result.ok, "Empty ownership must not bypass attribution integrity."
        assert "attribut" in (result.error.message + result.error.hint).lower()
    else:
        assert result.ok, result.to_envelope()
        advice = diagnostics(result)
        assert "src/example.py" in advice and "outside.txt" not in advice, advice
    assert snapshot(root) == before
