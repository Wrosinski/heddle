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


def test_unresolved_coverage_leads_final_verification_until_owned(
    tmp_path, monkeypatch, capsys
):
    from tests.proof_continuity_helpers import edit

    root, path = source_host(tmp_path, monkeypatch)
    verify(path, "m1")
    assert execute(ops.MilestoneAdvance(feature=FEATURE)).ok
    (root / "src/unowned.py").write_text("EXTRA = 1\n")

    first, second, *_ = execute(ops.Status(feature=FEATURE)).next_actions
    assert isinstance(first.action, ops.ManualAction)
    assert "src/unowned.py" in first.reason
    assert "source-set-stale" in first.reason
    assert "heddle milestone edit" in first.action.instruction
    assert "heddle feature sources attribute" in first.action.instruction
    assert isinstance(second.action, ops.CommandAction)
    assert second.action.operation == ops.Verify(
        scope="acceptance", feature=FEATURE, expect_revision=read(path)["revision"]
    )

    code, result = edit(
        capsys,
        tmp_path,
        {"owns": ["src/example.py", "src/unowned.py", "tests/check.py"]},
    )
    assert code == 0, result
    # Owning the path changes m1's declaration too, so its proof reruns first.
    routed = execute(ops.Status(feature=FEATURE)).next_actions
    assert not any(isinstance(item.action, ops.ManualAction) for item in routed)
    assert isinstance(routed[0].action, ops.CommandAction)
    assert isinstance(routed[0].action.operation, ops.Verify)


def test_milestone_verification_routes_without_observing_coverage(
    tmp_path, monkeypatch
):
    from heddle.runtime import readiness

    root, _path = source_host(tmp_path, monkeypatch)
    (root / "src/unowned.py").write_text("EXTRA = 1\n")

    def unexpected(*_args):
        raise AssertionError("coverage observed off the final verification route")

    monkeypatch.setattr(readiness, "observe_feature_coverage", unexpected)
    lead = execute(ops.Status(feature=FEATURE)).next_actions[0]
    assert not isinstance(lead.action, ops.ManualAction), lead


def test_a_stale_batch_led_by_milestone_proof_settles_coverage_first(
    tmp_path, monkeypatch
):
    root, path = source_host(tmp_path, monkeypatch)
    verify(path, "m1")
    assert execute(ops.MilestoneAdvance(feature=FEATURE)).ok
    verify(path, "acceptance")
    (root / "src/example.py").write_text(
        (root / "src/example.py").read_text() + "# fix\n"
    )
    (root / "src/unowned.py").write_text("EXTRA = 1\n")

    first, second, *_ = execute(ops.Status(feature=FEATURE)).next_actions
    assert isinstance(first.action, ops.ManualAction), first
    assert "src/unowned.py" in first.reason
    assert isinstance(second.action, ops.CommandAction)
    assert second.action.operation.stale, second


def test_a_verification_refusal_keeps_the_coverage_step_first(tmp_path, monkeypatch):
    root, path = source_host(tmp_path, monkeypatch)
    verify(path, "m1")
    assert execute(ops.MilestoneAdvance(feature=FEATURE)).ok
    (root / "src/unowned.py").write_text("EXTRA = 1\n")

    refused = execute(ops.PhaseExit(feature=FEATURE))
    assert not refused.ok and refused.error.code == "verification-missing"
    first, second, *_ = refused.next_actions
    assert isinstance(first.action, ops.ManualAction), refused.to_envelope()
    assert "src/unowned.py" in first.reason
    assert isinstance(second.action, ops.CommandAction)
    assert second.action.operation.scope == "acceptance"
