"""
A blocked drive tells the operator where they are and where to look.

commands dogfood (2026-07-06): the first real ``pending-decisions`` pause
printed only ``drive is blocked by pending-decisions`` + a generic hint —
no stage, no pointer to the state document holding the batch, and no
resolution command (text mode silently dropped ``next_actions``). The
operator had to reconstruct all three by hand. Contract: the blocked envelope
names the stage and feature; for ``pending-decisions`` the hint names the
workspace state document and ``next_actions`` carry one truthful action per
pending batch. Complete recommendation-and-route facts render a shell-safe
``decisions resolve`` command; incomplete facts render ``decisions list`` plus
an explicit manual-input instruction. The drive CLI's text renderer prints the
``next:``/``note:`` channels instead of dropping them.
"""

from __future__ import annotations

from types import SimpleNamespace

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.driver.cli import _print
from heddle.driver.loop import _blocked
from heddle.kernel.source_manifest import EvidenceReference
from heddle.kernel.state import (
    DecisionFact,
    Milestone,
    VerificationEvidence,
    VerificationFact,
)


def _decision(**overrides) -> DecisionFact:
    fields = {
        "id": "E1",
        "source": "session",
        "title": "Gate run records carry unresolvable artifact paths",
        "status": "pending",
        "kind": "question",
        "rationale": None,
        "resolution": None,
        "routes_to": ("heddle/gate/entry.py",),
        "created_at": "2026-07-06T08:00Z",
        "resolved_at": None,
        "escalation_class": 1,
        "question": "May the engine be fixed now?",
        "options": ("fix-now", "defer", "stop"),
        "resolution_source": None,
    }
    fields.update(overrides)
    return DecisionFact(**fields)


def _milestone(mid: str, status: str = "done") -> Milestone:
    return Milestone(
        id=mid,
        title=f"Milestone {mid}",
        satisfies=("AC-1",),
        depends_on=(),
        owns=(f"src/{mid}",),
        complexity="low",
        estimated_hours=(1, 2),
        verification={"command": "pytest -q", "expected": "all tests pass"},
        status=status,
        tasks=(),
    )


def _verification(scope: str, at: str) -> VerificationFact:
    reference = EvidenceReference(
        schema="heddle.source-evidence-ref/v1",
        artifact="verification/source-evidence." + "0" * 64 + ".yaml",
        artifact_sha256="0" * 64,
        source_sha256="0" * 64,
    )
    return VerificationFact(
        scope=scope,
        command="pytest -q",
        exit_code=0,
        at=at,
        log=f"verification/{scope}.log",
        evidence=VerificationEvidence(
            schema="heddle.verification-evidence/v1",
            before=reference,
            after=reference,
        ),
    )


def _snap(decisions, milestones=(), verifications=()) -> SimpleNamespace:
    return SimpleNamespace(
        feature="demo",
        stage="plan-review",
        workspace="plans/demo/",  # kernel form carries a trailing slash
        state=SimpleNamespace(
            decisions=tuple(decisions),
            milestones=tuple(milestones),
            verifications=tuple(verifications),
        ),
    )


def test_pending_batch_reason_names_milestones_landed_since_raised():
    """
    Post-completion friction retrospective (review): a deferred batch surfacing
        after later milestones landed must say so — core landed BEFORE the batch
        was raised and stays out of the parenthetical; reader/gate landed after.
    """
    result = _blocked(
        NextAction(
            ops.CommandAction(ops.DecisionsList(feature="demo")),
            "resolve blocking condition: pending-decisions",
        ),
        "pending-decisions",
        snap=_snap(
            [
                _decision(
                    id="code-quality#1",
                    source="code-quality#1",
                    escalation_class=None,
                    title="Extract the seam-probe helper",
                    created_at="2026-07-06T08:00Z",
                ),
            ],
            milestones=[_milestone("m1"), _milestone("m2"), _milestone("m3")],
            verifications=[
                _verification("m1", "2026-07-06T07:00:00Z"),
                _verification("m2", "2026-07-06T09:00:00Z"),
                _verification("m3", "2026-07-06T10:00:00Z"),
            ],
        ),
    )

    reason = result.next_actions[0].reason
    assert "(m2, m3 landed since raised)" in reason, (
        "FAIL: the reason line must name exactly the milestones done after "
        f"the batch was raised, got {reason!r}"
    )
    assert "m1" not in reason, (
        "FAIL: a milestone landed BEFORE the batch was raised must not be "
        f"named as landed-since, got {reason!r}"
    )
    assert "re-verify each recommendation against the current code" in (
        result.error.hint
    ), (
        "FAIL: when any batch has landings the hint must carry the "
        f"re-verification nudge once, got {result.error.hint!r}"
    )


def test_non_decision_blockers_still_gain_stage_context_only():
    result = _blocked(
        NextAction(
            ops.ManualAction("Ratify the engineering principles"),
            "resolve blocking condition: principles-not-ratified",
        ),
        "principles-not-ratified",
        snap=_snap([_decision()]),
    )
    assert "at stage plan-review" in result.error.message
    assert result.error.hint == (
        "resolve the blocking condition, then re-run heddle drive"
    )
    # The generic single next-action stays; no decision enumeration.
    assert len(result.next_actions) == 1
    assert isinstance(result.next_actions[0].action, ops.ManualAction)


def test_blocked_without_snapshot_keeps_the_legacy_envelope():
    """structured blockers compatibility: callers without a snapshot (and the existing
    structural-classification test) get the original shape."""
    result = _blocked(
        NextAction(ops.CommandAction(ops.DecisionsList()), "why"), "pending-decisions"
    )
    assert result.error.message == "drive is blocked by pending-decisions"
    assert result.error.hint == (
        "resolve the blocking condition, then re-run heddle drive"
    )
    assert result.next_actions[0].command == "heddle decisions list"


def test_drive_text_renderer_prints_next_and_note_channels(capsys):
    result = HeddleResult.failure(
        HeddleError(code="pending-decisions", message="blocked", hint="fix"),
        exit_code=ExitCode.INTERNAL,
        next_actions=(
            NextAction(ops.CommandAction(ops.DecisionsList(feature="demo")), "E1"),
        ),
        diagnostics=(Diagnostic(Severity.INFO, "review-found", "a note"),),
    )
    _print(result, json_mode=False)
    out = capsys.readouterr().out
    assert "next: heddle decisions list --feature demo — E1" in out, (
        "FAIL: text mode must print next_actions — that channel carries "
        "the remediation command"
    )
    assert "note: review-found: a note" in out
