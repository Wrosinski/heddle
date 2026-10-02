"""secondary-reviewer-rounds-v1 fast acceptance: AC-2 to AC-6 and AC-9.

The real application, gate engine, recording, closure and state validation run
in process; only the provider transport is doubled. AC-2 fixtures are literal
pre-change documents that never pass through the candidate's serializer.
"""

from __future__ import annotations

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.kernel.project_config import KernelError
from heddle.kernel.state import parse_state_document
from heddle.runtime.application import execute
from tests.secondary_rounds_helpers import (
    ASTRA_HIGH,
    FABLE_HIGH,
    assert_old_shape,
    assert_pre_change_ledger,
    assignment_rounds,
    delivered_targets,
    install_old_shape,
    old_shape_policy,
    slot_names,
    windowed,
)
from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import finding, finding_ref
from tests.tiering_helpers import (
    FABLE,
    OPUS,
    ROLES,
    SOL,
    entry,
    invoke,
    snapshot,
    wire_policy,
)
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    gate_command,
    open_round,
    provider_transport,
    review_content,
    review_status,
    runs,
)

BOTH = ["primary", "secondary"]
PRIMARY = ["primary"]


def _state(path):
    return yaml.safe_load(path.read_text())


def _next_run():
    actions = invoke("Status", feature=V7_FEATURE).next_actions
    routed = [
        row.action.operation
        for row in actions
        if isinstance(row.action, ops.CommandAction)
        and isinstance(row.action.operation, ops.RunGate)
    ]
    assert len(routed) == 1, f"FAIL AC-3: readiness routes {actions!r}"
    return routed[0]


def _run_routed_slot(run_cli, path, expected):
    """AC-3: readiness names the missing slot with that slot's reviewer."""
    operation = _next_run()
    assert {
        "cli": operation.cli,
        "model": operation.model,
        "reasoning_effort": operation.reasoning_effort,
    } == expected, f"FAIL AC-3: readiness selected {operation!r}"
    code, result = gate_command(
        run_cli, "run-gate", operation.gate, "--cli", operation.cli
    )
    assert code in (0, 4) and result["ok"] and result["data"]["accepted"], result
    return runs(path)[-1]


def _settle(path, run_rows, *extra):
    recorded = dispose(
        path,
        [
            *extra,
            *[
                disposition(row["run_id"], "@coverage", status="settled")
                for row in run_rows
            ],
        ],
    )
    assert recorded.ok, recorded.to_envelope()
    return recorded


def _amend(path, *, revision, **overrides):
    return invoke(
        "FeaturePolicy",
        slug=V7_FEATURE,
        payload=wire_policy(revision=revision, overrides=overrides),
        expect_revision=_state(path)["revision"],
    )


def test_ac3_ac4_all_window_freezes_both_slots_and_secondary_closes_its_own_duty(
    tmp_path, monkeypatch, run_cli
):
    _host_root, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": windowed(
                entry("spec-review", limit=3, secondary=FABLE), "all"
            )
        },
    )
    phase = {"value": 1}
    origins: dict[str, str] = {}

    def report(cli, _prompt):
        if phase["value"] == 1:
            items = (
                [
                    finding(
                        "SP-I2",
                        classification="implement",
                        title="SECONDARY_ORIGINAL_CONCERN",
                    )
                ]
                if cli == "claude"
                else []
            )
            return review_content(findings=items)
        result = review_content()
        result["prior_dispositions"] = [
            reviewer_disposition(
                finding_ref(origins["secondary"], "SP-I2"), action="addressed"
            ),
            *(
                reviewer_disposition(
                    finding_ref(run_id, "@coverage"), action="addressed"
                )
                for run_id in origins.values()
            ),
        ]
        return result

    calls = provider_transport(monkeypatch, report)
    first = [
        _run_routed_slot(run_cli, path, ASTRA_HIGH),
        _run_routed_slot(run_cli, path, FABLE_HIGH),
    ]
    origins.update({row["reviewer_slot"]: row["run_id"] for row in first})
    recorded = _settle(
        path,
        first,
        disposition(
            origins["secondary"], "SP-I2", status="retained", requires_inspection=True
        ),
    )
    assert [origins["secondary"], "SP-I2"] in recorded.data["closure"]["open_refs"]

    phase["value"] = 2
    opened = open_round(path, reason="Verify the secondary's original concern")
    assert opened.ok, opened.to_envelope()
    second = assignment_rounds(path)[1]
    assert slot_names(second) == BOTH, (
        "FAIL AC-3: round 2 under window all must freeze the primary and secondary"
    )
    primary_run = _run_routed_slot(run_cli, path, ASTRA_HIGH)
    waiting = review_status(path)
    assert not waiting["closed"] and waiting["missing_slots"] == ["secondary"], (
        "FAIL AC-4: the review must stay open while the secondary slot has no result"
    )
    before = path.read_bytes()
    early = open_round(path, purpose="independent-pass")
    assert not early.ok and path.read_bytes() == before and len(calls) == 3, (
        "FAIL AC-3: a round opened while the secondary slot had no result"
    )
    secondary_run = _run_routed_slot(run_cli, path, FABLE_HIGH)
    assert (secondary_run["round_number"], secondary_run["reviewer_slot"]) == (
        2,
        "secondary",
    )
    cli, prompt, _command = calls[-1]
    assert cli == "claude"
    assert (origins["secondary"], "SP-I2") in delivered_targets(prompt), (
        "FAIL AC-4: the secondary's round-2 targets omit its own finding"
    )
    assert "SECONDARY_ORIGINAL_CONCERN" in prompt

    # Survivor: evidence from another reviewer never qualifies an inspection duty.
    before = path.read_bytes()
    wrong = dispose(
        path,
        [
            disposition(
                origins["secondary"],
                "SP-I2",
                evidence_kind="review",
                review_run_id=primary_run["run_id"],
            )
        ],
    )
    assert not wrong.ok and path.read_bytes() == before
    assert [row["predicate"] for row in wrong.error.details["rows"]] == [
        "review-not-qualifying"
    ], wrong.to_envelope()
    closed = _settle(
        path,
        [primary_run, secondary_run],
        disposition(
            origins["secondary"],
            "SP-I2",
            evidence_kind="review",
            review_run_id=secondary_run["run_id"],
        ),
    )
    assert closed.data["closure"]["closed"], closed.to_envelope()
    assert len(calls) == 4


def test_ac3_ac9_window_n_ends_and_the_cli_refusal_names_it(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": windowed(
                entry("spec-review", limit=4, minimum_rounds=3, secondary=FABLE), 2
            )
        },
    )
    calls = provider_transport(monkeypatch, review_content())
    for number in (1, 2):
        if number > 1:
            opened = open_round(path, purpose="independent-pass")
            assert opened.ok, opened.to_envelope()
        accepted = [
            _run_routed_slot(run_cli, path, ASTRA_HIGH),
            _run_routed_slot(run_cli, path, FABLE_HIGH),
        ]
        _settle(path, accepted)
    opened = open_round(path, purpose="independent-pass")
    assert opened.ok, opened.to_envelope()
    rounds = assignment_rounds(path)
    assert [slot_names(row) for row in rounds] == [BOTH, BOTH, PRIMARY], (
        "FAIL AC-3: window 2 must freeze both slots in rounds 1-2 and the primary after"
    )

    before = snapshot(host)
    code, refused = gate_command(run_cli, "run-gate", "spec-review", "--cli", "claude")
    assert code != 0 and not refused["ok"], refused
    message = refused["error"]["message"]
    for fact in ("spec-review round 3", "secondary_rounds: 2"):
        assert fact in message, f"FAIL AC-9: refusal omits {fact!r}: {message}"
    assert "omit --cli" in refused["error"]["hint"]
    assert len(calls) == 4 and snapshot(host) == before

    value = _state(path)
    for index, slots in ((1, rounds[1]["slots"][:1]), (2, rounds[1]["slots"])):
        tampered = yaml.safe_load(path.read_text())
        tampered["review_assignments"]["assignments"][0]["rounds"][index]["slots"] = (
            slots
        )
        with pytest.raises((KernelError, ValueError), match="frozen reviewer slots"):
            parse_state_document(tampered, source=path)
    parse_state_document(value, source=path)


def test_ac5_amendment_applies_from_the_next_round(tmp_path, monkeypatch, run_cli):
    _root, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": entry(
                "spec-review", limit=3, minimum_rounds=2, secondary=FABLE
            )
        },
    )
    provider_transport(monkeypatch, review_content())
    first = [
        _run_routed_slot(run_cli, path, ASTRA_HIGH),
        _run_routed_slot(run_cli, path, FABLE_HIGH),
    ]
    _settle(path, first)
    round_one = assignment_rounds(path)[0]
    amended = _amend(
        path,
        revision=2,
        **{
            "spec-review": windowed(
                entry("spec-review", limit=3, minimum_rounds=2, secondary=FABLE),
                "all",
            )
        },
    )
    assert amended.ok, amended.to_envelope()
    assert open_round(path, purpose="independent-pass").ok
    rounds = assignment_rounds(path)
    assert rounds[0] == round_one
    assert slot_names(rounds[1]) == BOTH and rounds[1]["policy_revision"] == 2, (
        "FAIL AC-5: the amended window applies from the next round"
    )
    status = execute(ops.Status(feature=V7_FEATURE))
    assert status.ok, status.to_envelope()
    history = _state(path)["policy_history"]
    assert all("secondary_rounds" not in row for row in history[0]["entries"])


@pytest.mark.parametrize("change", ["narrow", "remove"])
def test_ac5_ac6_narrowing_or_removal_applies_from_the_next_round(
    change, tmp_path, monkeypatch, run_cli
):
    dual = entry("spec-review", limit=3, minimum_rounds=3, secondary=FABLE)
    _root, path = current_host(
        tmp_path, monkeypatch, overrides={"spec-review": windowed(dual, "all")}
    )
    provider_transport(monkeypatch, review_content())
    for number in (1, 2):
        if number == 2:
            assert open_round(path, purpose="independent-pass").ok
        _settle(
            path,
            [
                _run_routed_slot(run_cli, path, ASTRA_HIGH),
                _run_routed_slot(run_cli, path, FABLE_HIGH),
            ],
        )
    earlier = assignment_rounds(path)
    after = (
        dual if change == "narrow" else entry("spec-review", limit=3, minimum_rounds=3)
    )
    amended = _amend(path, revision=2, **{"spec-review": after})
    assert amended.ok, amended.to_envelope()
    assert open_round(path, purpose="independent-pass").ok
    rounds = assignment_rounds(path)
    assert rounds[:2] == earlier
    assert [slot_names(row) for row in rounds] == [BOTH, BOTH, PRIMARY], (
        f"FAIL AC-6: {change} must apply from the next round only"
    )
    assert [row["policy_revision"] for row in rounds] == [1, 1, 2]
    # The earlier dual rounds still validate against their own revision.
    parse_state_document(_state(path), source=path)
    assert execute(ops.Status(feature=V7_FEATURE)).ok


def _refusal(path, overrides, *, revision):
    before = path.read_bytes()
    refused = _amend(path, revision=revision, **overrides)
    assert not refused.ok, "FAIL AC-6: a secondary that can serve no round was accepted"
    assert path.read_bytes() == before
    return refused.error.message


def test_ac6_amendment_refuses_only_a_secondary_that_can_serve_no_open_round(
    tmp_path, monkeypatch, run_cli
):
    _root, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=1)},
    )
    provider_transport(monkeypatch, review_content())
    _settle(path, [_run_routed_slot(run_cli, path, ASTRA_HIGH)])

    def spec(window, limit, secondary=FABLE):
        row = entry("spec-review", limit=limit, secondary=secondary)
        return {"spec-review": row if window is None else windowed(row, window)}

    # Refused: added at the limit, or with a window that ends before round 2.
    for window, limit in ((None, 1), ("all", 1), (1, 2)):
        message = _refusal(path, spec(window, limit), revision=2)
        for fact in (
            "spec-review",
            f"secondary_rounds {window or 1}",
            "feature review at round 1",
        ):
            assert fact in message, f"FAIL AC-6: refusal omits {fact!r}: {message}"
    revision = 2
    for overrides in (
        spec(2, 2),  # The smallest integer window that reaches round 2.
        spec("all", 2),  # The same `all` with the limit raised in one revision.
        spec(1, 2),  # Narrowing is never refused.
    ):
        accepted = _amend(path, revision=revision, **overrides)
        assert accepted.ok, accepted.to_envelope()
        revision += 1
    # Refused: replacing the secondary reviewer with no round left to serve.
    _refusal(path, spec(1, 2, secondary=OPUS), revision=revision)
    for overrides in (
        # An unchanged secondary and window is never refused by this rule.
        {**spec(1, 2), "plan-review": entry("plan-review", primary=SOL, limit=3)},
        # A role with no review yet, and milestones whose review has not started.
        {
            **spec(1, 2),
            "plan-review": entry("plan-review", primary=SOL, secondary=FABLE),
            "milestone-review": entry("milestone-review", primary=SOL, secondary=FABLE),
        },
        # Convergence always allows the next round, so `all` serves it.
        {
            "spec-review": windowed(
                entry("spec-review", mode="convergence", limit=None, secondary=FABLE),
                "all",
            )
        },
        # Removal is never refused.
        {"spec-review": entry("spec-review", limit=2)},
    ):
        accepted = _amend(path, revision=revision, **overrides)
        assert accepted.ok, accepted.to_envelope()
        revision += 1
    assert execute(ops.Status(feature=V7_FEATURE)).ok


def _scaffold_only():
    chosen = {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }
    chosen["review-test-scaffolding"] = entry("review-test-scaffolding")
    return chosen


def test_ac6_a_role_whose_reviews_are_all_sealed_refuses_a_new_secondary(
    tmp_path, monkeypatch, run_cli
):
    _root, path = current_host(
        tmp_path, monkeypatch, stage="scaffold", overrides=_scaffold_only()
    )
    provider_transport(monkeypatch, review_content("review-test-scaffolding"))
    code, result = gate_command(run_cli, "run-gate", "review-test-scaffolding")
    assert code in (0, 4), result
    _settle(path, runs(path))
    assert execute(ops.PhaseExit(feature=V7_FEATURE)).ok
    assert _state(path)["review_assignments"]["acceptances"]
    chosen = _scaffold_only()
    chosen["review-test-scaffolding"] = windowed(
        entry("review-test-scaffolding", secondary=FABLE), "all"
    )
    message = _refusal(path, chosen, revision=2)
    assert "review-test-scaffolding" in message and "all" in message


def test_ac6_milestone_review_before_any_milestone_accepts_a_secondary(
    tmp_path, monkeypatch
):
    _root, path = current_host(tmp_path, monkeypatch)
    value = _state(path)
    value["milestones"] = []
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    assert execute(ops.Status(feature=V7_FEATURE)).ok
    accepted = _amend(
        path,
        revision=2,
        **{
            "milestone-review": windowed(
                entry("milestone-review", primary=SOL, secondary=FABLE), "all"
            )
        },
    )
    assert accepted.ok, accepted.to_envelope()


def test_ac2_old_shape_state_reads_reconfirms_and_raises_allowance_unchanged(
    tmp_path, monkeypatch, run_cli
):
    _root, path = current_host(tmp_path, monkeypatch)
    literal = install_old_shape(path, spec_limit=3, spec_minimum=3)
    provider_transport(monkeypatch, review_content())
    _settle(
        path,
        [
            _run_routed_slot(run_cli, path, ASTRA_HIGH),
            _run_routed_slot(run_cli, path, FABLE_HIGH),
        ],
    )
    assert open_round(path, purpose="independent-pass").ok
    _settle(path, [_run_routed_slot(run_cli, path, ASTRA_HIGH)])
    value = _state(path)
    assert_old_shape(value)
    assert_pre_change_ledger(path)
    assert value["feature_policy"] == literal
    rounds = assignment_rounds(path)
    assert [slot_names(row) for row in rounds] == [BOTH, PRIMARY]

    # Read path: status, readiness and budget equal the pre-change values.
    before = path.read_bytes()
    status = execute(ops.Status(feature=V7_FEATURE))
    assert status.ok, status.to_envelope()
    budget = status.data["effective_policy"]["budget"]
    assert (budget["feature_minimum"], budget["feature_maximum"]) == (7, 10)
    counted = review_status(path)
    assert (counted["rounds_used"], counted["calls_completed"]) == (2, 3)
    assert counted["missing_slots"] == []
    assert not any(
        isinstance(action.action, ops.CommandAction)
        and isinstance(action.action.operation, ops.RunGate)
        for action in status.next_actions
    )
    assert path.read_bytes() == before

    # Write path: re-confirming the stored policy, with or without an explicit 1.
    explicit = old_shape_policy(spec_limit=3, spec_minimum=3)
    for row in explicit["entries"]:
        row["secondary_rounds"] = 1
    for payload in (literal, explicit):
        replay = invoke(
            "FeaturePolicy",
            slug=V7_FEATURE,
            payload=payload,
            expect_revision=_state(path)["revision"],
        )
        assert replay.ok and not replay.data["wrote"], (
            f"FAIL AC-2: an unchanged re-confirmation must write nothing: {replay}"
        )
        assert path.read_bytes() == before

    # Write path: an allowance raise keeps every earlier form and round.
    raised = invoke(
        "ReviewAllowance",
        role="spec-review",
        limit=5,
        approval="Owner authorizes an absolute five-round ceiling for this role",
        feature=V7_FEATURE,
        expect_revision=_state(path)["revision"],
    )
    assert raised.ok, raised.to_envelope()
    after = _state(path)
    assert_old_shape(after)
    assert after["policy_history"][0] == literal
    assert after["feature_policy"]["revision"] == 2
    assert assignment_rounds(path) == rounds
    assert open_round(path, purpose="independent-pass").ok
    assert slot_names(assignment_rounds(path)[2]) == PRIMARY


def test_ac6_survivor_intake_reconfirmation_before_admission_is_not_checked(
    tmp_path, monkeypatch
):
    """Survivor pin: without a review ledger there is nothing to refuse."""
    from tests.tiering_helpers import FEATURE, blank_host, confirmed

    host = blank_host(tmp_path, monkeypatch)
    confirmed(host)
    intake = host / ".heddle" / "intake" / f"{FEATURE}.yaml"
    added = invoke(
        "FeaturePolicy",
        slug=FEATURE,
        payload=wire_policy(
            revision=2, overrides={"spec-review": entry("spec-review", secondary=FABLE)}
        ),
        expect_revision=yaml.safe_load(intake.read_text())["revision"],
    )
    assert added.ok, added.to_envelope()
