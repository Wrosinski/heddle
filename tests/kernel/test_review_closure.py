"""
AC-3/4 pure closure discriminators, with literal originating identities.

The runtime must qualify canonical artifacts before constructing these facts;
runtime disposition tests separately exercise that trust boundary.
"""

from dataclasses import replace

import pytest

from tests.tiering_helpers import ASTRA, FABLE, api, entry, policy

BASIS = "a" * 64
OLD = "b" * 64
REF = ("initial-primary", "SP-I1")
SECONDARY = ("initial-secondary", "SP-I1")


def obligation(ref=REF, *, severity="important", classification="implement", **kw):
    return api().ReviewObligation(
        run_id=ref[0],
        finding_id=ref[1],
        severity=severity,
        classification=classification,
        reviewer="primary",
        **kw,
    )


def resolution(ref=REF, *, status="addressed", **kw):
    values = dict(
        run_id=ref[0],
        finding_id=ref[1],
        status=status,
        references=(("tests/proof.py", BASIS),),
        evidence_basis=BASIS,
        reason="The bound proof corrects the original counterexample",
        reviewer=None,
        decision_id=None,
    )
    values.update(kw)
    return api().ReviewResolution(**values)


def round_fact(number=1, **kw):
    values = dict(
        number=number,
        purpose="discovery",
        scope_identity="contract-A",
        required_slots=("primary",),
        accepted_slots=("primary",),
        new_important=0,
        before_open=(),
        material_progress=(),
    )
    values.update(kw)
    return api().ReviewRound(**values)


def assess(
    *, selection=None, rounds=None, obligations=(), resolutions=(), decisions=()
):
    facts = api().ReviewClosureFacts(
        policy=selection or entry("spec-review"),
        rounds=(round_fact(),) if rounds is None else rounds,
        obligations=obligations,
        resolutions=resolutions,
        decisions=decisions,
        current_basis=BASIS,
    )
    return api("heddle.kernel.review_closure").assess_review_closure(facts)


def test_confirmed_override_to_originating_disposition_closes_without_extra_call():
    chosen = policy(
        overrides={"spec-review": entry("spec-review", primary=FABLE, limit=1)}
    )
    effective = api("heddle.kernel.feature_policy").effective_policy(chosen)
    selected = next(row for row in effective.entries if row.role == "spec-review")
    assert selected.primary.cli == "claude"
    assert selected.limit == 1
    source = obligation(requires_inspection=True)
    open_result = assess(selection=selected, obligations=(source,))
    assert (open_result.open_refs, open_result.stop_reason) == ((REF,), "round-limit")
    closed_result = assess(
        selection=selected,
        obligations=(source,),
        resolutions=(resolution(reviewer="primary"),),
    )
    assert closed_result.closed
    assert closed_result.stop_reason is None
    assert closed_result.rounds_used == open_result.rounds_used == 1
    assert closed_result.calls_completed == open_result.calls_completed == 1


@pytest.mark.parametrize(
    "severity,classification,required",
    [
        ("critical", "implement", True),
        ("critical", "report", True),
        ("critical", "ignore", True),
        ("important", "implement", True),
        ("important", "report", True),
        ("important", "ignore", False),
        ("minor", "implement", False),
        ("minor", "report", False),
        ("minor", "ignore", False),
    ],
)
def test_ac3_severity_and_classification_define_obligations(
    severity, classification, required
):
    result = assess(
        obligations=(obligation(severity=severity, classification=classification),)
    )
    assert result.open_refs == ((REF,) if required else ())
    assert result.closed is not required


def test_ac3_duplicate_correction_never_coalesces_original_refs():
    sources = (obligation(), obligation(SECONDARY))
    partial = assess(obligations=sources, resolutions=(resolution(),))
    assert partial.open_refs == (SECONDARY,)
    assert not partial.closed
    complete = assess(
        obligations=sources, resolutions=(resolution(), resolution(SECONDARY))
    )
    assert complete.closed
    assert complete.open_refs == ()


@pytest.mark.parametrize(
    "change",
    [
        {"status": "retained"},
        {"status": "awaiting-decision"},
        {"references": ()},
        {"evidence_basis": OLD},
    ],
)
def test_ac3_open_or_unqualified_disposition_cannot_close(change):
    result = assess(obligations=(obligation(),), resolutions=(resolution(**change),))
    assert result.open_refs == (REF,)
    assert not result.closed


def test_ac3_supported_settlement_and_executable_proof_need_no_automatic_call():
    for status in ("addressed", "settled"):
        result = assess(
            obligations=(obligation(),), resolutions=(resolution(status=status),)
        )
        assert result.closed
        assert result.next_step == "closed"


def test_ac3_material_coverage_gap_is_an_originating_obligation():
    coverage_ref = (REF[0], "@coverage")
    result = assess(obligations=(obligation(coverage_ref),))
    assert result.open_refs == (coverage_ref,)
    assert not result.closed
    assert assess(
        obligations=(obligation(coverage_ref),), resolutions=(resolution(coverage_ref),)
    ).closed


def test_ac3_report_requires_exact_resolved_decision_and_settlement():
    source = obligation(classification="report", decision_id="decision-7")
    settled = resolution(status="settled", decision_id="decision-7")
    c = api()
    pending = c.ReviewDecision("decision-7", REF[0], REF[1], "pending")
    wrong_origin = c.ReviewDecision(
        "decision-7", SECONDARY[0], SECONDARY[1], "resolved"
    )
    for decisions in ((), (pending,), (wrong_origin,)):
        assert not assess(
            obligations=(source,), resolutions=(settled,), decisions=decisions
        ).closed
    resolved = replace(pending, status="resolved")
    assert not assess(obligations=(source,), decisions=(resolved,)).closed
    assert assess(
        obligations=(source,), resolutions=(settled,), decisions=(resolved,)
    ).closed


def test_ac3_secondary_inspection_duty_survives_a_primary_only_verification():
    source = replace(
        obligation(SECONDARY, requires_inspection=True), reviewer="secondary"
    )
    primary = resolution(SECONDARY, reviewer="primary")
    assert not assess(obligations=(source,), resolutions=(primary,)).closed
    assert not assess(
        obligations=(source,), resolutions=(resolution(SECONDARY),)
    ).closed
    secondary = replace(primary, reviewer="secondary")
    assert assess(obligations=(source,), resolutions=(secondary,)).closed


def test_ac3_no_new_findings_model_switch_or_gate_off_does_not_erase_old_finding():
    rounds = (
        round_fact(new_important=1),
        round_fact(2, purpose="verification", before_open=(REF,)),
    )
    selections = (
        entry("spec-review"),
        entry("spec-review", primary=FABLE),
        entry("spec-review", mode="off", limit=None, minimum_rounds=0),
    )
    for selected in selections:
        result = assess(selection=selected, rounds=rounds, obligations=(obligation(),))
        assert result.open_refs == (REF,)
        assert not result.closed
        assert result.rounds_used == 2


def test_ac4_dual_partial_round_retries_missing_slot_without_new_quality_credit():
    selected = entry("spec-review", secondary=FABLE)
    partial = round_fact(required_slots=("primary", "secondary"))
    result = assess(selection=selected, rounds=(partial,))
    assert (result.rounds_used, result.calls_completed) == (1, 1)
    assert result.missing_slots == ("secondary",)
    assert result.next_step == "run"
    assert not result.closed
    # Provider errors produce no accepted slot. Retry/replay retain this round.
    retried = replace(partial, accepted_slots=("primary", "secondary"))
    done = assess(selection=selected, rounds=(retried,))
    assert (done.rounds_used, done.calls_completed) == (1, 2)
    assert done.closed


def test_ac4_closure_precedes_exhaustion_and_no_decrease():
    rounds = (round_fact(new_important=1), round_fact(2, new_important=1))
    result = assess(
        rounds=rounds, obligations=(obligation(),), resolutions=(resolution(),)
    )
    assert result.closed
    assert result.stop_reason is None
    assert result.next_step == "closed"


def test_ac4_upper_limit_does_not_mean_fixed_passes():
    assert assess(selection=entry("spec-review", limit=9)).closed
    fixed = entry("spec-review", limit=9, minimum_rounds=3)
    assert not assess(selection=fixed).closed
    assert assess(selection=fixed).next_step == "run"
    rounds = tuple(round_fact(n, purpose="independent-pass") for n in (1, 2, 3))
    assert assess(selection=fixed, rounds=rounds).closed


def test_ac4_arbitrary_cap_and_convergence_have_no_hidden_quality_ceiling():
    rounds = tuple(round_fact(n, scope_identity=f"contract-{n}") for n in range(1, 8))
    for selected in (
        entry("spec-review", limit=9),
        entry("spec-review", mode="convergence", limit=None),
    ):
        result = assess(
            selection=selected,
            rounds=rounds,
            obligations=(obligation(),),
            resolutions=(resolution(status="retained"),),
        )
        assert result.rounds_used == 7
        assert result.stop_reason is None
        assert not result.closed
    capped = assess(
        selection=entry("spec-review", limit=7),
        rounds=rounds,
        obligations=(obligation(),),
    )
    assert capped.stop_reason == "round-limit"
    assert capped.open_refs == (REF,)


@pytest.mark.parametrize(
    "previous,current,expected",
    [(2, 2, True), (2, 3, True), (2, 1, False), (0, 0, False), (0, 1, False)],
)
@pytest.mark.parametrize(
    "role", ["spec-review", "plan-review", "review-test-scaffolding"]
)
def test_ac4_positive_comparable_important_counts_stop_only_without_decrease(
    previous, current, expected, role
):
    rounds = (round_fact(new_important=previous), round_fact(2, new_important=current))
    result = assess(
        selection=entry(role, limit=9),
        rounds=rounds,
        obligations=(obligation(),),
        resolutions=(resolution(status="retained"),),
    )
    assert (result.stop_reason == "no-decrease") is expected
    assert not result.closed


def test_ac4_large_high_default_scaffolding_convergence_honors_no_decrease():
    from tests.tiering_helpers import axes

    recommendation = api("heddle.kernel.feature_policy").recommend_policy(
        axes("large", "high", "partial")
    )
    selected = next(
        row for row in recommendation.entries if row.role == "review-test-scaffolding"
    )
    assert selected.mode == "convergence" and selected.secondary is None
    result = assess(
        selection=selected,
        rounds=(round_fact(new_important=1), round_fact(2, new_important=1)),
        obligations=(obligation(),),
    )
    assert result.stop_reason == "no-decrease" and result.next_step == "decision"


@pytest.mark.parametrize(
    "changed",
    [{"purpose": "verification"}, {"scope_identity": "material-contract-change"}],
)
@pytest.mark.parametrize(
    "role", ["spec-review", "plan-review", "review-test-scaffolding"]
)
def test_ac4_unlike_purpose_or_scope_is_not_a_comparable_count(changed, role):
    rounds = (round_fact(new_important=1), round_fact(2, new_important=2, **changed))
    result = assess(
        selection=entry(role, limit=9),
        rounds=rounds,
        obligations=(obligation(),),
        resolutions=(resolution(status="retained"),),
    )
    assert result.stop_reason is None


def test_ac4_same_required_findings_without_material_progress_stop():
    first = round_fact(new_important=1)
    second = round_fact(2, purpose="verification", before_open=(REF,))
    result = assess(
        rounds=(first, second),
        obligations=(obligation(),),
        resolutions=(resolution(status="retained"),),
        selection=entry("spec-review", limit=9),
    )
    assert result.stop_reason == "no-progress"
    assert result.open_refs == (REF,)
    narrowed = replace(second, material_progress=(REF,))
    progressing = assess(
        rounds=(first, narrowed),
        obligations=(obligation(),),
        resolutions=(resolution(status="retained"),),
        selection=entry("spec-review", limit=9),
    )
    assert progressing.stop_reason is None
    assert not progressing.closed


def test_ac4_amendment_does_not_reset_usage_but_explicit_increase_adds_allowance():
    rounds = (round_fact(), round_fact(2, scope_identity="contract-B"))
    source = (obligation(),)
    for selected in (
        entry("spec-review"),
        entry("spec-review", primary=ASTRA),
        entry("spec-review", primary=FABLE),
    ):
        result = assess(selection=selected, rounds=rounds, obligations=source)
        assert result.rounds_used == 2
        assert result.stop_reason == "round-limit"
    increased = assess(
        selection=entry("spec-review", limit=3), rounds=rounds, obligations=source
    )
    assert increased.rounds_used == 2
    assert increased.stop_reason is None


@pytest.mark.parametrize(
    "bad",
    ["duplicate-slot", "unknown-slot", "missing-round", "unknown-origin"],
)
def test_ac3_malformed_pure_fact_relationships_refuse(bad):
    rounds = (round_fact(),)
    resolutions = ()
    if bad == "duplicate-slot":
        rounds = (round_fact(accepted_slots=("primary", "primary")),)
    elif bad == "unknown-slot":
        rounds = (round_fact(accepted_slots=("secondary",)),)
    elif bad == "missing-round":
        rounds = (round_fact(2),)
    else:
        resolutions = (resolution(("unknown-run", "SP-I1")),)
    with pytest.raises(ValueError, match="slot|round|origin|reference"):
        assess(rounds=rounds, obligations=(obligation(),), resolutions=resolutions)
