"""concurrent-reviews-v1 fast acceptance for `run-gates` over any launch set.

AC-1 and AC-4 to AC-9 through the public typed application boundary. Provider
work is replaced at the existing gate-engine seam by a fork-safe double keyed
by (gate, reviewer slot); preparation, admission, locks, recording, CAS,
readiness and result folding stay real. Overlap and completion order are
controlled with manager barriers and events, never sleeps. Standalone setup
calls use the doubled provider transport. Synthetic fixtures only.

Red before m2: `run-gates` accepts only the behavior/complexity pair, keys
members by gate, fails the batch on an unconverged verdict, records
recovery-only sets outside its locks and replays an exact set as success.
Survivor pins are labelled in their docstrings.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import signal
import threading
from pathlib import Path

import pytest
import yaml

from tests.concurrent_review_helpers import (
    PRIMARY,
    SECONDARY,
    SLOT_REVIEWERS,
    batch_command,
    commands,
    execute_batch,
    full_peer_host,
    gate_runs,
    install_counting_engine,
    install_slot_engine,
    lock_is_held,
    member_key,
    reach_round_two,
    record_events,
    revision,
    run_gate_commands,
    run_slot,
    same_gate_host,
    show_slot,
    slot_command,
    status_actions,
    trace_gate_locks,
)
from tests.structured_review_helpers import finding
from tests.tiering_review_helpers import V7_FEATURE

SPEC = "spec-review"
P = (SPEC, PRIMARY)
S = (SPEC, SECONDARY)
SLOTS = (P, S)
PREPARED_FIELDS = (
    "input_hash",
    "review_basis_hash",
    "prompt_version",
    "effective_prompt_sha256",
)


def _round_host(tmp_path, monkeypatch, round_number: int):
    host, state_path = same_gate_host(tmp_path, monkeypatch, window="all")
    if round_number == 2:
        reach_round_two(state_path, monkeypatch)
    return host, state_path


def _selected(result) -> dict:
    return result.error.details["selected_member"]


def _remedies(member: dict) -> list[str]:
    return [action["command"] for action in member["next_actions"]]


def _critical(identifier: str):
    return finding(identifier, severity="critical", classification="implement")


def _important(identifier: str):
    return finding(identifier, severity="important", classification="implement")


@pytest.mark.parametrize("round_number", (1, 2))
@pytest.mark.parametrize("first", SLOTS, ids=("primary-first", "secondary-first"))
def test_ac1_ac4_two_slots_overlap_and_record_primary_first(
    first, round_number: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, state_path = _round_host(tmp_path, monkeypatch, round_number)
    standalone = {key: show_slot(*key) for key in SLOTS}
    before = revision(state_path)
    prior_runs = len(gate_runs(state_path))
    with multiprocessing.Manager() as manager:
        starts, finishes, _seen = install_slot_engine(monkeypatch, manager, first=first)
        result = execute_batch()
        started = [dict(row) for row in starts]
        finished = list(finishes)

    assert result.ok, result.to_envelope()
    assert len(started) == 2, "FAIL AC-1: both slots must start before either ends"
    assert finished[0] == first
    members = result.data["members"]
    assert [member_key(row) for row in members] == list(SLOTS)
    assert result.data["gates"] == [SPEC]
    assert all(row["scope"] == "feature" for row in members)
    assert {row["round_number"] for row in members} == {round_number}
    assert all(row["publication"] == "recorded" for row in members)
    new_runs = gate_runs(state_path)[prior_runs:]
    assert [(run["gate"], run["reviewer_slot"]) for run in new_runs] == list(SLOTS)
    assert revision(state_path) == before + 2, "FAIL AC-1: one revision per record"
    for row in started:
        key = (row["gate"], row["reviewer_slot"])
        reviewer = SLOT_REVIEWERS[key[0]][key[1]]
        assert (row["cli"], row["model"], row["reasoning_effort"]) == (
            reviewer["cli"],
            reviewer["model"],
            reviewer["reasoning_effort"],
        )
        for field in PREPARED_FIELDS:
            assert row[field] == standalone[key][field], (
                f"FAIL AC-4: {key} {field} differs from its standalone preparation"
            )


def test_ac1_primary_record_is_in_state_while_the_secondary_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        recorded = record_events(monkeypatch, manager, SLOTS)
        _starts, _finishes, seen = install_slot_engine(
            monkeypatch,
            manager,
            first=P,
            hold_until_recorded={S: P},
            recorded=recorded,
        )
        result = execute_batch()
        observed = dict(seen)
    assert result.ok, result.to_envelope()
    assert observed == {S: True}, (
        "FAIL AC-1: the primary was not recorded while the secondary still ran"
    )


@pytest.mark.parametrize(
    ("outcome", "pairing"),
    [
        (outcome, pairing)
        for pairing in ("same-gate-round-1", "same-gate-round-2", "two-roles")
        for outcome in ("clean", "report", "retained", "failed")
    ],
)
def test_ac4_a_sibling_outcome_never_changes_a_member_input(
    outcome: str, pairing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin: inputs come only from the slot's own assignment history."""
    from tests.tiering_review_helpers import provider_transport, review_content

    if pairing == "two-roles":
        _host, state_path = full_peer_host(
            tmp_path, monkeypatch, behavior_secondary=False
        )
        a, b = ("behavior-review", PRIMARY), ("complexity-review", PRIMARY)
    else:
        _host, state_path = _round_host(
            tmp_path, monkeypatch, 2 if pairing.endswith("2") else 1
        )
        a, b = P, S
    before = show_slot(*b)
    attempts = len(
        yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"]
    )
    payload = {
        "clean": review_content(a[0]),
        "report": review_content(a[0], findings=[finding("SP-I1")]),
        "retained": {"incomplete": "preserve these exact provider bytes"},
        "failed": review_content(a[0]),
    }[outcome]
    provider_transport(
        monkeypatch,
        lambda _cli, _prompt: payload,
        error_attempts=3 if outcome == "failed" else 0,
    )
    run_slot(*a)
    ledger = yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"]
    assert len(ledger) == attempts + 1, f"fixture precondition: {a} {outcome} recorded"
    after = show_slot(*b)
    assert after == before, f"FAIL AC-4: {b} input changed after {a} {outcome}"


def test_ac5_one_lock_per_gate_in_catalog_order_held_through_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.runtime import gate_run

    full_peer_host(tmp_path, monkeypatch)
    acquired = trace_gate_locks(monkeypatch)
    held_at_record: list[bool] = []
    original = gate_run._record

    def checking(*args, **kwargs):
        held_at_record.append(all(lock_is_held(path) for _gate, path in acquired))
        return original(*args, **kwargs)

    monkeypatch.setattr(gate_run, "_record", checking)
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, parties=3)
        result = execute_batch()
    assert result.ok, result.to_envelope()
    assert [gate for gate, _path in acquired] == [
        "behavior-review",
        "complexity-review",
    ], "FAIL AC-5: expected one lock per distinct gate, in catalog order"
    assert held_at_record == [True, True, True]


def test_ac5_two_slots_of_one_gate_take_its_lock_once_and_block_outsiders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    same_gate_host(tmp_path, monkeypatch)
    acquired = trace_gate_locks(monkeypatch)
    with multiprocessing.Manager() as manager:
        probes = manager.list()
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        from heddle.gate import entry

        engine = entry.run_gate_for_runtime

        def probing(gate_type, context, **kwargs):
            # A standalone run-gate opens its own description of the gate lock.
            probes.append(lock_is_held(Path(acquired[0][1])))
            return engine(gate_type, context, **kwargs)

        monkeypatch.setattr(entry, "run_gate_for_runtime", probing)
        result = execute_batch()
        held = list(probes)
    assert result.ok, result.to_envelope()
    assert [gate for gate, _path in acquired] == [SPEC]
    assert held == [True, True], "FAIL AC-5: an outside run-gate could take the lock"


def test_ac5_recovery_only_recording_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.runtime import gate_run

    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    original = gate_run._record
    injected = False

    def conflict_before_first(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            _unrelated_write()
        return original(*args, **kwargs)

    monkeypatch.setattr(gate_run, "_record", conflict_before_first)
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        first = execute_batch()
        assert not first.ok and not gate_runs(state_path)
        calls = len(starts)

        acquired = trace_gate_locks(monkeypatch)
        held: list[bool] = []

        def checking(*args, **kwargs):
            held.append(bool(acquired) and lock_is_held(acquired[0][1]))
            return original(*args, **kwargs)

        monkeypatch.setattr(gate_run, "_record", checking)
        recovered = execute_batch()
        assert len(starts) == calls == 2
    assert recovered.ok, recovered.to_envelope()
    assert [row["reuse"] for row in recovered.data["members"]] == [
        "recovered",
        "recovered",
    ]
    assert held == [True, True], "FAIL AC-5: recovery-only recording ran unlocked"


def test_ac5_a_lock_location_change_while_waiting_refuses_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    from heddle.gate import entry

    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    original_lock = entry.gate_lock_for_runtime
    original_path = entry.gate_lock_path_for_runtime
    locked = False

    @contextmanager
    def lock(context, invocation):
        nonlocal locked
        with original_lock(context, invocation) as path:
            locked = True
            yield path

    def moving_path(context, invocation):
        path = original_path(context, invocation)
        return path.with_name("moved-" + path.name) if locked else path

    monkeypatch.setattr(entry, "gate_lock_for_runtime", lock)
    monkeypatch.setattr(entry, "gate_lock_path_for_runtime", moving_path)
    before = state_path.read_bytes()
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        result = execute_batch()
        call_count = len(calls)
    assert locked, "fixture precondition: run-gates reached its locks"
    assert not result.ok, "FAIL AC-5: a moved lock location did not refuse"
    assert call_count == 0 and state_path.read_bytes() == before
    assert result.error.details == {"cause": "launch-set-changed", "excluded_slots": []}


def _unrelated_write() -> None:
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    changed = execute(
        ops.CommandsSet(
            "lint_command", ".venv/bin/ruff check heddle", feature=V7_FEATURE
        )
    )
    assert changed.ok, changed.to_envelope()


def test_ac6_a_set_that_changes_while_waiting_refuses_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    from heddle.gate import entry

    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    original_lock = entry.gate_lock_for_runtime
    raced = False

    @contextmanager
    def racing_lock(context, invocation):
        nonlocal raced
        if not raced:
            raced = True
            # Another lead records the primary before the batch gets the lock.
            standalone = run_slot(*P)
            assert standalone.ok, standalone.to_envelope()
        with original_lock(context, invocation) as path:
            yield path

    monkeypatch.setattr(entry, "gate_lock_for_runtime", racing_lock)
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        result = execute_batch()
        recorded = list(calls)
    assert raced, "fixture precondition: the race ran before the lock"
    assert recorded == [P], "FAIL AC-6: the batch called a provider after the race"
    assert not result.ok
    assert [(run["gate"], run["reviewer_slot"]) for run in gate_runs(state_path)] == [P]
    assert slot_command(*S) in [action.command for action in result.next_actions]


def test_ac6_a_set_that_shrinks_to_another_batch_routes_back_to_run_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed set of two or more keeps its own batch action in the refusal."""
    from contextlib import contextmanager

    from heddle.gate import entry

    _host, state_path = full_peer_host(tmp_path, monkeypatch)
    behavior = ("behavior-review", PRIMARY)
    original_lock = entry.gate_lock_for_runtime
    raced = False

    @contextmanager
    def racing_lock(context, invocation):
        nonlocal raced
        if not raced:
            raced = True
            standalone = run_slot(*behavior)
            assert standalone.ok, standalone.to_envelope()
        with original_lock(context, invocation) as path:
            yield path

    monkeypatch.setattr(entry, "gate_lock_for_runtime", racing_lock)
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        result = execute_batch()
        recorded = list(calls)
    assert raced, "fixture precondition: the race ran before the lock"
    assert recorded == [behavior]
    assert not result.ok
    assert [(run["gate"], run["reviewer_slot"]) for run in gate_runs(state_path)] == [
        behavior
    ]
    assert batch_command() in [action.command for action in result.next_actions], (
        "FAIL AC-6: the refusal dropped the remaining launch set"
    )


def test_ac6_ac9_rerun_with_no_new_set_is_a_refused_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        first = execute_batch()
        assert first.ok, first.to_envelope()
        before = state_path.read_bytes()
        replay = execute_batch()
        assert len(starts) == 2
    assert not replay.ok, "FAIL AC-9: a replay with no new launch set succeeded"
    assert state_path.read_bytes() == before


# A recorded unconverged verdict is advisory (4) whichever member fails;
# exit 3 is reserved for a batch that fails (`ok: false`).
@pytest.mark.parametrize(
    ("verdicts", "exit_code"),
    (
        (("fail", "pass_with_conditions"), 4),
        (("pass_with_conditions", "pass"), 4),
        (("pass_with_conditions", "pass_with_conditions"), 4),
        (("pass", "fail"), 4),
        (("pass", "pass"), 0),
    ),
)
def test_ac7_unconverged_verdicts_complete_the_batch(
    verdicts, exit_code: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    by_verdict = {
        "pass": (),
        "pass_with_conditions": (finding("SP-I1"),),
        "fail": (_critical("SP-C1"),),
    }
    findings = {
        key: by_verdict[verdict] for key, verdict in zip(SLOTS, verdicts, strict=True)
    }
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, findings=findings)
        result = execute_batch()
    assert result.ok, f"FAIL AC-7: an unconverged verdict failed the batch {result}"
    assert int(result.exit_code) == exit_code
    assert [row["publication"] for row in result.data["members"]] == [
        "recorded",
        "recorded",
    ]
    assert len(gate_runs(state_path)) == 2


def test_ac7_the_driver_folds_the_real_batch_result_naming_both_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import heddle.driver.loop as loop
    from heddle.contracts import operations as ops

    same_gate_host(tmp_path, monkeypatch)
    findings = {P: (_critical("SP-C1"),), S: (finding("SP-I1"),)}
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, findings=findings)
        result = execute_batch()
    action = ops.CommandAction(ops.RunGates(feature=V7_FEATURE))
    assert loop._is_unconverged_gate_verdict(action, result), (
        "FAIL AC-7: the driver would halt on a completed unconverged batch"
    )
    detail = loop._gate_verdict_problem(result).detail
    assert "spec-review primary" in detail and "spec-review secondary" in detail
    # Each member's row carries what a standalone fold would: its verdict,
    # its finding counts and the artifact to read.
    rows = {line.split(":", 1)[0]: line for line in detail.splitlines()}
    for member in result.data["members"]:
        row = rows[f"spec-review {member['reviewer_slot']}"]
        assert member.get("status") in {"fail", "pass_with_conditions"}, member
        assert f"verdict {member['status']}" in row, row
        assert "0 IMPLEMENT, 0 REPORT" not in row, (
            f"FAIL AC-7: the fold drops findings: {row}"
        )
        assert f"{member['artifact']}" in row, row
    assert result.data["members"][0]["status"] == "fail"
    assert "1 IMPLEMENT" in rows["spec-review primary"]


@pytest.mark.parametrize("round_number", (1, 2))
def test_ac8_typed_primary_failure_records_both_and_offers_the_primary_retry(
    round_number: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, state_path = _round_host(tmp_path, monkeypatch, round_number)
    prior = len(gate_runs(state_path))
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, first=S, failures=(P,))
        result = execute_batch()
    assert not result.ok
    members = result.error.details["members"]
    assert [member_key(row) for row in members] == list(SLOTS)
    assert [(row["execution"], row["publication"]) for row in members] == [
        ("failed", "recorded-error"),
        ("completed", "recorded"),
    ]
    assert _selected(result) == {
        "gate": SPEC,
        "scope": "feature",
        "reviewer_slot": PRIMARY,
    }
    assert _remedies(members[0]) == [slot_command(*P)]
    assert _remedies(members[1]) == []
    runs = gate_runs(state_path)[prior:]
    assert [(run["gate"], run["reviewer_slot"]) for run in runs] == list(SLOTS)
    actions = status_actions()
    assert batch_command() not in commands(actions)
    assert run_gate_commands(actions, SPEC) == [slot_command(*P)]


def test_ac8_several_failures_select_the_first_in_publication_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, first=S, failures=SLOTS)
        result = execute_batch()
    assert not result.ok
    assert _selected(result)["reviewer_slot"] == PRIMARY
    members = result.error.details["members"]
    assert [_remedies(row) for row in members] == [
        [slot_command(*P)],
        [slot_command(*S)],
    ]


def test_a_failed_fable_member_reruns_on_its_model_fallback_after_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(
            monkeypatch, manager, failing_models=("claude-fable-5-2",)
        )
        result = execute_batch()
        launched = [
            (row["reviewer_slot"], row["model"], row["reasoning_effort"])
            for row in starts
        ]
    assert result.ok, result.to_envelope()
    assert sorted(launched[:2]) == sorted(
        [
            (PRIMARY, "gpt-6-astra", "high"),
            (SECONDARY, "claude-fable-5-2", "high"),
        ]
    )
    assert launched[2:] == [(SECONDARY, "claude-opus-5-5", "xhigh")]
    secondary = [
        run for run in gate_runs(state_path) if run["reviewer_slot"] == SECONDARY
    ]
    assert [
        (run["execution_config"]["model"], run["verdict"]["status"] == "error")
        for run in secondary
    ] == [("claude-fable-5-2", True), ("claude-opus-5-5", False)]
    member = next(row for row in result.data["members"] if member_key(row) == S)
    assert member["execution"] == "failed"
    assert member["fallback"]["ok"] and member["fallback"]["exit_code"] == 0
    assert member["fallback"]["run_id"] == secondary[1]["run_id"]
    assert "model-fallback" in {row.code for row in result.diagnostics}


def test_a_batch_member_whose_fallback_fails_too_fails_the_batch_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(
            monkeypatch,
            manager,
            failing_models=("claude-fable-5-2", "claude-opus-5-5"),
        )
        result = execute_batch()
        launched = [(row["reviewer_slot"], row["model"]) for row in starts]
    assert not result.ok, result.to_envelope()
    assert launched[2:] == [(SECONDARY, "claude-opus-5-5")]
    assert result.error is not None
    assert "after claude-fable-5-2 at high failed with" in result.error.message
    assert int(result.exit_code) == 3
    member = next(
        row
        for row in result.to_envelope()["error"]["details"]["members"]
        if member_key(row) == S
    )
    assert member["fallback"]["ok"] is False
    assert "model-refusal" in member["fallback"]["error"]["message"]
    retry = member["next_actions"][0]["command"]
    assert "--model claude-opus-5-5 --reasoning-effort xhigh" in retry
    secondary = [
        run["execution_config"]["model"]
        for run in gate_runs(state_path)
        if run["reviewer_slot"] == SECONDARY
    ]
    assert secondary == ["claude-fable-5-2", "claude-opus-5-5"]


def test_a_member_s_retry_names_the_fallback_once_its_failure_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.runtime import gate_run

    _host, _state_path = same_gate_host(tmp_path, monkeypatch)
    # The batch stops before its fallback rerun, as an interrupted one would.
    monkeypatch.setattr(gate_run, "_model_fallback", lambda *_args: None)
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, failing_models=("claude-fable-5-2",))
        result = execute_batch()
    assert not result.ok, result.to_envelope()
    member = next(
        row
        for row in result.to_envelope()["error"]["details"]["members"]
        if member_key(row) == S
    )
    retry = member["next_actions"][0]["command"]
    assert "--model claude-opus-5-5 --reasoning-effort xhigh" in retry


@pytest.mark.parametrize("round_number", (1, 2))
def test_ac8_worker_crash_stops_recording_and_later_output_recovers_without_a_call(
    round_number: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, state_path = _round_host(tmp_path, monkeypatch, round_number)
    prior = len(gate_runs(state_path))
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(
            monkeypatch, manager, first=S, crashes=(P,)
        )
        result = execute_batch()
        assert not result.ok
        members = result.error.details["members"]
        assert [member_key(row) for row in members] == list(SLOTS)
        assert members[0]["execution"] == "failed"
        assert members[1]["publication"] == "recoverable"
        assert _selected(result)["reviewer_slot"] == PRIMARY
        assert _remedies(members[0]) == [slot_command(*P)]
        assert _remedies(members[1]) == [slot_command(*S)]
        assert len(gate_runs(state_path)) == prior

        actions = status_actions()
        assert batch_command() in commands(actions)
        install_slot_engine(monkeypatch, manager, parties=1)
        retried = execute_batch()
    assert retried.ok, retried.to_envelope()
    assert [row["reuse"] for row in retried.data["members"]] == ["none", "recovered"]
    runs = gate_runs(state_path)[prior:]
    assert [(run["gate"], run["reviewer_slot"]) for run in runs] == list(SLOTS)


def test_ac8_a_worker_that_dies_mid_result_fails_alone_and_its_output_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A torn result frame fails its member; run-gates returns and releases."""
    import os
    import signal
    import struct
    from multiprocessing import connection

    from tests.concurrent_review_helpers import canonical_outcome

    _host, state_path = _round_host(tmp_path, monkeypatch, 1)
    prior = len(gate_runs(state_path))
    torn = f"heddle-{S[0]}-{S[1]}"
    send = connection.Connection._send_bytes

    def tear(self, buf):
        # The secondary's worker sends half its result frame, then dies.
        if multiprocessing.current_process().name != torn:
            return send(self, buf)
        body = bytes(buf)
        os.write(self.fileno(), struct.pack("!i", len(body)) + body[: len(body) // 2])
        os._exit(1)

    def hung(_signum, _frame):
        pytest.fail("FAIL AC-8: run-gates still waits for a torn result")

    previous = signal.signal(signal.SIGALRM, hung)
    signal.alarm(30)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                "heddle.gate.entry.run_gate_for_runtime",
                lambda gate_type, context, *, feature, **_kwargs: canonical_outcome(
                    gate_type, context, feature
                ),
            )
            patch.setattr(connection.Connection, "_send_bytes", tear)
            result = execute_batch()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert not result.ok
    members = result.error.details["members"]
    assert [member_key(row) for row in members] == list(SLOTS)
    assert members[0]["publication"] == "recorded"
    assert members[1]["execution"] == "failed"
    assert "did not return a result" in members[1]["error"]["message"]
    assert _selected(result)["reviewer_slot"] == SECONDARY
    assert _remedies(members[1]) == [slot_command(*S)]

    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        recovered = run_slot(*S)
        assert list(calls) == [], "FAIL AC-8: recovery spent a provider call"
    assert recovered.ok, recovered.to_envelope()
    runs = gate_runs(state_path)[prior:]
    assert [(run["gate"], run["reviewer_slot"]) for run in runs] == list(SLOTS)


@pytest.mark.parametrize("conflict_at", ("before-first", "between-members"))
@pytest.mark.parametrize("round_number", (1, 2))
def test_ac8_recording_conflict_recovers_without_a_provider_call(
    conflict_at: str,
    round_number: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.runtime import gate_run

    _host, state_path = _round_host(tmp_path, monkeypatch, round_number)
    prior = len(gate_runs(state_path))
    original = gate_run._record
    publications = 0

    def conflicted(*args, **kwargs):
        nonlocal publications
        publications += 1
        if publications == (1 if conflict_at == "before-first" else 2):
            _unrelated_write()
        return original(*args, **kwargs)

    monkeypatch.setattr(gate_run, "_record", conflicted)
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        result = execute_batch()
        assert not result.ok
        recorded = 0 if conflict_at == "before-first" else 1
        members = result.error.details["members"]
        assert [row["publication"] for row in members] == (
            ["recoverable", "recoverable"]
            if recorded == 0
            else ["recorded", "recoverable"]
        )
        assert _remedies(members[1]) == [slot_command(*S)]
        monkeypatch.setattr(gate_run, "_record", original)
        calls = len(starts)
        if recorded == 0:
            assert batch_command() in commands(status_actions())
            restarted = execute_batch()
            assert restarted.ok, restarted.to_envelope()
            assert [row["reuse"] for row in restarted.data["members"]] == [
                "recovered",
                "recovered",
            ]
        else:
            before = state_path.read_bytes()
            refused = execute_batch()
            assert not refused.ok and state_path.read_bytes() == before
            assert [action.command for action in refused.next_actions].count(
                slot_command(*S)
            ) == 1, "FAIL AC-8: one-member remainder not routed to its run-gate"
            standalone = run_slot(*S)
            assert standalone.ok, standalone.to_envelope()
        assert len(starts) == calls == 2, "FAIL AC-8: recovery called a provider"
    runs = gate_runs(state_path)[prior:]
    assert [(run["gate"], run["reviewer_slot"]) for run in runs] == list(SLOTS)


def test_ac8_interruption_keeps_finished_output_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal

    from heddle.gate import entry
    from tests.concurrent_review_helpers import canonical_outcome

    host, state_path = same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        barrier = manager.Barrier(2)
        secondary_done = manager.Event()

        def interrupted(gate_type, context, *, feature, **_kwargs):
            slot = context.prepared_run.reviewer_slot
            barrier.wait(timeout=10)
            if slot == SECONDARY:
                outcome = canonical_outcome(gate_type, context, feature)
                secondary_done.set()
                return outcome
            assert secondary_done.wait(timeout=10)
            os.kill(os.getppid(), signal.SIGINT)
            # The primary is unfinished: it never reaches its output.
            barrier.wait(timeout=30)  # never released: the parent stops us
            return canonical_outcome(gate_type, context, feature)

        monkeypatch.setattr(entry, "run_gate_for_runtime", interrupted)
        result = execute_batch()
    assert not result.ok
    members = result.error.details["members"]
    assert [member_key(row) for row in members] == list(SLOTS)
    assert members[0]["execution"] == "interrupted"
    # The interrupted primary ran until it was stopped, so it has timing.
    assert members[0]["finished_at"] is not None, members[0]
    assert isinstance(members[0]["duration_s"], int), members[0]
    assert members[1]["publication"] == "recoverable"
    assert not gate_runs(state_path)
    assert list((host / f"plans/{V7_FEATURE}/reviews").glob("*.review.json"))


def test_ac8_launch_failure_reports_the_member_and_leaves_no_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker that fails to start makes no call; its sibling's output stays.

    The second worker start raises once the first member's call has finished,
    so the outcome does not depend on scheduling. Either start order is legal;
    the assertions follow which member actually ran.
    """
    from multiprocessing import process

    from heddle.gate import entry

    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    prior = len(gate_runs(state_path))
    original_start = process.BaseProcess.start
    launched = 0
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager, parties=1)
        engine = entry.run_gate_for_runtime
        ran_done = manager.Event()

        def signalling(gate_type, context, **kwargs):
            try:
                return engine(gate_type, context, **kwargs)
            finally:
                ran_done.set()

        def start(self):
            nonlocal launched
            target = getattr(self._target, "func", self._target)
            if not getattr(target, "__module__", "").startswith("heddle."):
                return original_start(self)
            launched += 1
            if launched == 2:
                assert ran_done.wait(timeout=10), "the first member did not finish"
                raise OSError(11, "Resource temporarily unavailable")
            return original_start(self)

        monkeypatch.setattr(entry, "run_gate_for_runtime", signalling)
        monkeypatch.setattr(process.BaseProcess, "start", start)
        try:
            result = execute_batch()
        finally:
            monkeypatch.setattr(process.BaseProcess, "start", original_start)
        called = [(row["gate"], row["reviewer_slot"]) for row in starts]
    assert launched == 2, "FAIL AC-8: run-gates did not launch both members"
    assert not multiprocessing.active_children(), (
        "FAIL AC-8: a sibling worker was left running"
    )
    assert len(called) == 1, f"FAIL AC-8: the unlaunched member made a call: {called}"
    ran = called[0]
    failed = next(key for key in SLOTS if key != ran)
    assert not result.ok
    members = {member_key(row): row for row in result.error.details["members"]}
    assert members[failed]["execution"] == "failed"
    assert _selected(result) == {
        "gate": SPEC,
        "scope": "feature",
        "reviewer_slot": failed[1],
    }
    assert _remedies(members[failed]) == [slot_command(*failed)]
    runs = gate_runs(state_path)[prior:]
    if ran == P:
        assert members[P]["publication"] == "recorded"
        assert _remedies(members[P]) == []
        assert [(run["gate"], run["reviewer_slot"]) for run in runs] == [P]
    else:
        assert members[S]["publication"] == "recoverable"
        assert _remedies(members[S]) == [slot_command(*S)]
        assert runs == []


def test_ac6_unreadable_shared_inputs_refuse_with_the_sets_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch whose shared inputs cannot be read still lists what it left out."""
    from heddle.runtime import gate_run, verification

    host, state_path = full_peer_host(tmp_path, monkeypatch, complexity="dual")
    config = host / ".heddle.yaml"
    value = yaml.safe_load(config.read_text())
    value["agents"] = {"claude": False}
    config.write_text(yaml.safe_dump(value, sort_keys=False))
    original = gate_run._prepare_batch_members

    def unreadable(*_args, **_kwargs):
        raise OSError("synthetic unreadable verification input")

    def preparing(resolved, launch):
        with pytest.MonkeyPatch.context() as inner:
            inner.setattr(verification, "verification_statuses", unreadable)
            return original(resolved, launch)

    monkeypatch.setattr(gate_run, "_prepare_batch_members", preparing)
    before = state_path.read_bytes()
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        refused = execute_batch()
        call_count = len(calls)
    assert not refused.ok and refused.error.code == "workspace-invalid"
    assert call_count == 0 and state_path.read_bytes() == before
    excluded = refused.error.details["excluded_slots"]
    assert excluded and {row["reason"] for row in excluded} == {"admission-refused"}


@pytest.mark.parametrize(
    "failing", ["lock inputs", "locks", "rederivation", "after rederivation"]
)
def test_ac6_lock_and_admission_failures_list_the_set_last_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    """A refusal lists the exclusions of the set as last derived, if any."""
    from dataclasses import replace

    from heddle.gate import entry
    from heddle.runtime import gate_run

    host, state_path = full_peer_host(tmp_path, monkeypatch, complexity="dual")
    config = host / ".heddle.yaml"
    value = yaml.safe_load(config.read_text())
    value["agents"] = {"claude": False}
    config.write_text(yaml.safe_dump(value, sort_keys=False))

    def unreadable(*_args, **_kwargs):
        raise OSError(f"synthetic unreadable {failing}")

    derive = gate_run.current_launch_set
    lock_inputs = gate_run._batch_lock_inputs
    derivations = []

    def deriving_once(*args, **kwargs):
        derivations.append(args)
        if len(derivations) > 1:
            unreadable()
        return derive(*args, **kwargs)

    def rederiving_one_exclusion(*args, **kwargs):
        derivations.append(args)
        launch, actions = derive(*args, **kwargs)
        if len(derivations) > 1:
            launch = replace(launch, exclusions=launch.exclusions[:1])
        return launch, actions

    def reading_lock_inputs_once(*args, **kwargs):
        if len(derivations) > 1:
            unreadable()
        return lock_inputs(*args, **kwargs)

    if failing == "lock inputs":
        monkeypatch.setattr(gate_run, "_batch_lock_inputs", unreadable)
    elif failing == "locks":
        monkeypatch.setattr(entry, "gate_lock_for_runtime", unreadable)
    elif failing == "rederivation":
        monkeypatch.setattr(gate_run, "current_launch_set", deriving_once)
    else:
        monkeypatch.setattr(gate_run, "current_launch_set", rederiving_one_exclusion)
        monkeypatch.setattr(gate_run, "_batch_lock_inputs", reading_lock_inputs_once)
    before = state_path.read_bytes()
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        refused = execute_batch()
        call_count = len(calls)

    assert not refused.ok and refused.error.code == "workspace-invalid"
    assert f"synthetic unreadable {failing}" in refused.error.message
    assert call_count == 0 and state_path.read_bytes() == before
    if failing == "rederivation":
        assert "excluded_slots" not in (refused.error.details or {})
        return
    excluded = refused.error.details["excluded_slots"]
    assert {row["reason"] for row in excluded} == {"admission-refused"}
    # The set derived again under the locks is the one a later failure lists.
    assert len(excluded) == (1 if failing == "after rederivation" else 2)


def test_ac6_a_failed_final_refresh_still_lists_the_sets_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Members ran; the summary cannot refresh, and still names what it left out."""
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import gate_run
    from heddle.runtime.feature_context import ResolveFeatureFailure

    host, _state_path = full_peer_host(tmp_path, monkeypatch, complexity="dual")
    config = host / ".heddle.yaml"
    value = yaml.safe_load(config.read_text())
    value["agents"] = {"claude": False}
    config.write_text(yaml.safe_dump(value, sort_keys=False))
    resolve = gate_run.resolve_snapshot_from_cwd
    resolutions = []

    def failing_third(*args, **kwargs):
        resolutions.append(args)
        if len(resolutions) == 3:
            return ResolveFeatureFailure(
                KernelError(
                    code="workspace-invalid",
                    message="synthetic unreadable state",
                    hint="repair the state",
                ),
                (),
            )
        return resolve(*args, **kwargs)

    monkeypatch.setattr(gate_run, "resolve_snapshot_from_cwd", failing_third)
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        refused = execute_batch()
        call_count = len(calls)

    assert call_count == 2 and len(resolutions) == 3
    assert not refused.ok and refused.error.message == "synthetic unreadable state"
    excluded = refused.error.details["excluded_slots"]
    assert len(excluded) == 2
    assert {row["reason"] for row in excluded} == {"admission-refused"}


def test_ac6_a_member_preparation_refusal_refuses_the_batch_naming_that_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch returns the refused member's own standalone refusal."""
    from dataclasses import replace

    from heddle.gate import entry

    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    original = entry.prepare_gate_run

    def refusing(*args, **kwargs):
        prepared = original(*args, **kwargs)
        if prepared.reviewer_slot == SECONDARY:
            return replace(prepared, scope="different-boundary")
        return prepared

    monkeypatch.setattr(entry, "prepare_gate_run", refusing)
    before = state_path.read_bytes()
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        standalone = run_slot(*S)
        assert not standalone.ok and not calls and state_path.read_bytes() == before, (
            "fixture precondition: the secondary's preparation refuses on its own"
        )
        refused = execute_batch()
        call_count = len(calls)
    assert not refused.ok
    assert call_count == 0, "FAIL AC-6: a member launched beside a refused one"
    assert state_path.read_bytes() == before
    assert refused.error.details.get("selected_member") == {
        "gate": SPEC,
        "scope": "feature",
        "reviewer_slot": SECONDARY,
    }, f"FAIL AC-6: the refusal does not name its member: {refused.error}"
    assert refused.error.details["excluded_slots"] == []
    assert (
        refused.error.code,
        refused.error.message,
        refused.error.hint,
        refused.exit_code,
    ) == (
        standalone.error.code,
        standalone.error.message,
        standalone.error.hint,
        standalone.exit_code,
    )
    assert [action.command for action in refused.next_actions] == [
        action.command for action in standalone.next_actions
    ]


def test_ac8_source_drift_keeps_outputs_without_review_credit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, state_path = same_gate_host(tmp_path, monkeypatch)
    from heddle.gate import entry

    prior = len(gate_runs(state_path))
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager)
        engine = entry.run_gate_for_runtime

        def drift_after_primary(gate_type, context, **kwargs):
            outcome = engine(gate_type, context, **kwargs)
            if context.prepared_run.reviewer_slot == PRIMARY:
                spec = host / f"docs/features/runtime/{V7_FEATURE}.md"
                spec.write_text(spec.read_text() + "\nmaterial drift\n")
            return outcome

        monkeypatch.setattr(entry, "run_gate_for_runtime", drift_after_primary)
        result = execute_batch()
    assert not result.ok
    assert "members" in result.error.details, (
        "FAIL AC-8: run-gates did not run the two slots"
    )
    members = result.error.details["members"]
    assert [member_key(row) for row in members] == list(SLOTS)
    assert all(
        row["publication"] in {"recorded-error", "recoverable"} for row in members
    ), f"FAIL AC-8: output of changed source was credited: {members}"
    assert all(run.get("failure_reason") for run in gate_runs(state_path)[prior:]), (
        "FAIL AC-8: a review of changed source was recorded as completed"
    )
    artifact_dir = host / f"plans/{V7_FEATURE}/reviews"
    artifacts = {
        path.name: path.read_bytes() for path in artifact_dir.glob("*.review.json")
    }
    assert artifacts

    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        retry = execute_batch()
        call_count = len(calls)
    assert not retry.ok
    assert call_count == 0, "FAIL AC-8: changed source launched a provider"
    assert {
        path.name: path.read_bytes() for path in artifact_dir.glob("*.review.json")
    } == artifacts


def test_ac1_ac3_a_repaired_changed_source_review_rejoins_later_launch_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The changed-source exclusion lasts only until the slots are repaired."""
    from heddle.gate import entry
    from tests.concurrent_review_helpers import canonical_outcome, reach_round_two

    host, state_path = same_gate_host(tmp_path, monkeypatch, window="all")
    spec = host / f"docs/features/runtime/{V7_FEATURE}.md"

    def drifting(gate_type, context, *, feature, **_kwargs):
        outcome = canonical_outcome(gate_type, context, feature)
        if context.prepared_run.reviewer_slot == PRIMARY:
            spec.write_text(spec.read_text() + "\nmaterial drift\n")
        return outcome

    with monkeypatch.context() as patch:
        patch.setattr(entry, "run_gate_for_runtime", drifting)
        drifted = execute_batch()
    assert not drifted.ok
    assert batch_command() not in commands(status_actions()), (
        "fixture precondition: the drifted review is pending repair"
    )
    reach_round_two(state_path, monkeypatch)
    assert batch_command() in commands(status_actions()), (
        "FAIL AC-1: a repaired review stays out of later launch sets"
    )
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        result = execute_batch()
        started = [
            (row["gate"], row["reviewer_slot"], row["round_number"]) for row in starts
        ]
    assert result.ok, result.to_envelope()
    assert sorted(started) == [(SPEC, PRIMARY, 2), (SPEC, SECONDARY, 2)]


def _normalized(state_path: Path) -> dict:
    """State facts that must match across launch modes (AC-9)."""
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    value = yaml.safe_load(state_path.read_text())
    ledger = value["review_assignments"]
    status = execute(ops.Status(feature=V7_FEATURE))
    assert status.ok, status.to_envelope()
    closure = [
        {
            key: row[key]
            for key in (
                "role",
                "scope",
                "closed",
                "next_step",
                "rounds_used",
                "calls_completed",
                "missing_slots",
            )
        }
        for row in status.data["review_closure"]["assignments"]
    ]
    return {
        "revision": value["revision"],
        "assignments": [
            {key: row[key] for key in ("id", "role", "scope")}
            for row in ledger["assignments"]
        ],
        "attempts": [
            (
                row["assignment_id"],
                row["round_number"],
                row["reviewer_slot"],
                row["outcome"]["kind"],
            )
            for row in ledger["attempts"]
        ],
        "decisions": [
            (row["class"], row["status"]) for row in value.get("decisions", [])
        ],
        "closure": closure,
        "actions": commands(status.next_actions),
    }


def test_ac9_concurrent_and_sequential_runs_leave_the_same_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = {P: (_important("SP-I1"),), S: (finding("SP-I2"),)}
    states = {}
    calls = {}
    for mode in ("concurrent", "sequential"):
        root = tmp_path / mode
        root.mkdir()
        _host, state_path = same_gate_host(root, monkeypatch, launch=mode)
        with multiprocessing.Manager() as manager:
            starts, _finishes, _seen = install_slot_engine(
                monkeypatch,
                manager,
                parties=2 if mode == "concurrent" else 1,
                findings=outcomes,
            )
            if mode == "concurrent":
                assert execute_batch().ok
            else:
                for key in SLOTS:
                    assert run_gate_commands(status_actions(), SPEC) == [
                        slot_command(*key)
                    ]
                    run_slot(*key)
            calls[mode] = len(starts)
        states[mode] = _normalized(state_path)
    assert calls == {"concurrent": 2, "sequential": 2}
    assert states["concurrent"] == states["sequential"], (
        "FAIL AC-9: accounting differs between launch modes"
    )


@pytest.mark.parametrize("fails", (False, True), ids=("recorded", "failed"))
def test_launch_advice_is_shown_once_and_returned_in_the_envelope(
    fails: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both slots of one review read the same inputs, so their identical
    launch advice is printed once before launch and returned once."""
    same_gate_host(tmp_path, monkeypatch)
    advice = "Selected review inputs have uncommitted Git changes: `spec.md`."
    monkeypatch.setattr(
        "heddle.gate.preparation._review_source_advice",
        lambda _root, _paths: (advice,),
    )
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, failures=(P,) if fails else ())
        result = execute_batch()
    assert result.ok is not fails, result.to_envelope()
    from heddle.runtime import gate_run

    # The human summary does not repeat advice printed before launch.
    gate_run._emit(result, False)
    assert capsys.readouterr().err.count(advice) == 1
    notes = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "review-input-git-status"
    ]
    assert [diagnostic.message for diagnostic in notes] == [advice]


def test_run_gates_prints_one_progress_line_naming_every_running_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Slots finish only after the parent printed an interval line, so the
    line is observed while both providers are still running."""
    from heddle.runtime import gate_run
    from tests.concurrent_review_helpers import canonical_outcome

    same_gate_host(tmp_path, monkeypatch)
    monkeypatch.setattr(gate_run, "_PROGRESS_INTERVAL_S", 0.01)
    with multiprocessing.Manager() as manager:
        printed = manager.Event()
        line = gate_run._batch_progress_line

        def observed(*args, **kwargs):
            printed.set()
            return line(*args, **kwargs)

        def run(gate_type, context, *, feature, **_kwargs):
            assert printed.wait(timeout=10)
            return canonical_outcome(gate_type, context, feature)

        monkeypatch.setattr(gate_run, "_batch_progress_line", observed)
        monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
        result = execute_batch()
    assert result.ok, result.to_envelope()
    progress = [
        row for row in capsys.readouterr().err.splitlines() if "run-gates:" in row
    ]
    assert progress, "no progress line while both slots ran"
    assert "elapsed; spec-review primary (codex) running; " in progress[0]
    assert "spec-review secondary (claude) running" in progress[0]
    assert not any(row.startswith("heddle run-gate: …") for row in progress)
    for member in result.data["members"]:
        assert member["started_at"] <= member["finished_at"], member
        assert member["duration_s"] >= 0, member


def test_members_report_their_run_times_and_unlaunched_members_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, first=S, crashes=(P,))
        failed = execute_batch()
        install_slot_engine(monkeypatch, manager, parties=1)
        retried = execute_batch()
    assert not failed.ok
    crashed, finished = failed.error.details["members"]
    for member in (crashed, finished):
        assert member["started_at"] and member["finished_at"], member
        assert isinstance(member["duration_s"], int), member
    assert retried.ok, retried.to_envelope()
    rerun, recovered = retried.data["members"]
    assert recovered["reuse"] == "recovered"
    assert (recovered["started_at"], recovered["finished_at"]) == (None, None)
    assert recovered["duration_s"] is None
    assert rerun["reuse"] == "none" and rerun["finished_at"] is not None


def test_standalone_run_gate_ticks_while_its_provider_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import threading

    from heddle.runtime import gate_run
    from tests.concurrent_review_helpers import canonical_outcome

    same_gate_host(tmp_path, monkeypatch)
    monkeypatch.setattr(gate_run, "_PROGRESS_INTERVAL_S", 0.01)
    ticked = threading.Event()
    line = gate_run._gate_progress_line

    def observed(*args, **kwargs):
        ticked.set()
        return line(*args, **kwargs)

    def run(gate_type, context, *, feature, **_kwargs):
        assert ticked.wait(timeout=10)
        return canonical_outcome(gate_type, context, feature)

    monkeypatch.setattr(gate_run, "_gate_progress_line", observed)
    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
    result = run_slot(*P)
    assert result.ok, result.to_envelope()
    assert "heddle run-gate: spec-review primary (codex) running, " in (
        capsys.readouterr().err
    )
    assert not any(
        thread.name == "heddle-run-gate-progress" for thread in threading.enumerate()
    ), "the ticker outlived its provider call"


def test_human_output_names_every_member_of_a_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from heddle.runtime import gate_run

    same_gate_host(tmp_path, monkeypatch)
    findings = {P: (finding("SP-I1"),), S: ()}
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, findings=findings)
        result = execute_batch()
    capsys.readouterr()
    gate_run._render_human(result)
    assert capsys.readouterr().out.splitlines() == [
        f"spec-review primary [{V7_FEATURE}] — pass_with_conditions",
        f"spec-review secondary [{V7_FEATURE}] — pass",
    ]


def test_standalone_failing_verdict_is_recorded_with_the_advisory_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 3 is reserved for a failed run; a recorded `fail` verdict is 4."""
    same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        install_slot_engine(
            monkeypatch, manager, parties=1, findings={P: (_critical("SP-C1"),)}
        )
        result = run_slot(*P)
    assert result.ok, result.to_envelope()
    assert result.data["status"] == "fail"
    assert int(result.exit_code) == 4


def test_a_reused_review_still_shows_its_launch_advice_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that reuses an exact result launches nothing, yet its advice is
    printed before the reuse and not repeated by the human summary."""
    from heddle.runtime import gate_run

    same_gate_host(tmp_path, monkeypatch)
    advice = "Selected review inputs have uncommitted Git changes: `spec.md`."
    monkeypatch.setattr(
        "heddle.gate.preparation._review_source_advice",
        lambda _root, _paths: (advice,),
    )
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, parties=1)
        assert run_slot(*P).ok
        capsys.readouterr()
        reused = run_slot(*P)
        gate_run._emit(reused, False)
    assert reused.ok and reused.data["cached"], reused.to_envelope()
    assert capsys.readouterr().err.count(advice) == 1


def test_a_slot_completion_does_not_restart_the_progress_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secondary finishes first; the primary returns only once the parent
    waits again, and that wait gets only what remains of the interval."""
    import multiprocessing.connection

    from tests.concurrent_review_helpers import canonical_outcome

    same_gate_host(tmp_path, monkeypatch)
    real_wait = multiprocessing.connection.wait
    timeouts: list[float] = []
    with multiprocessing.Manager() as manager:
        waiting_again = manager.Event()

        def wait(objects, timeout=None):
            timeouts.append(timeout)
            if len(timeouts) == 2:
                waiting_again.set()
            return real_wait(objects, timeout)

        def run(gate_type, context, *, feature, **_kwargs):
            if context.prepared_run.reviewer_slot == PRIMARY:
                assert waiting_again.wait(timeout=10)
            return canonical_outcome(gate_type, context, feature)

        monkeypatch.setattr(multiprocessing.connection, "wait", wait)
        monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
        result = execute_batch()
    assert result.ok, result.to_envelope()
    assert len(timeouts) >= 2 and timeouts[1] < timeouts[0], timeouts


# --------------------------------------------------------------------------- #
# antigravity-reviewer-lane-v1: the Antigravity lane as a `run-gates` member.
#
# The doubled `agy` and a doubled Claude sibling are executables on PATH (see
# tests/gate/test_antigravity_runner.py); forked workers inherit the PATH and
# the patched gate arguments. Red until the lane's preparation (AC-8) or its
# runner (AC-9, AC-12) exists.
# --------------------------------------------------------------------------- #
CEILING_REFUSAL = "antigravity-input-too-large"


def _lane():
    import tests.gate.test_antigravity_runner as lane

    return lane


def _ceiling_batch_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str):
    """A batch with a Claude primary and an Antigravity secondary on one gate.

    ``same-gate``: spec review is the only routed review, so nothing is left
    out. ``excluded-slot``: full peer review, where behavior review carries the
    pair and the host disables codex, so admission genuinely leaves out
    complexity review's codex slot.
    """
    from tests.concurrent_review_helpers import off, single
    from tests.tiering_helpers import FABLE, OPUS, entry
    from tests.tiering_review_helpers import current_host

    lane = _lane()
    if layout == "same-gate":
        gate = SPEC
        host, state_path = current_host(
            tmp_path,
            monkeypatch,
            overrides={SPEC: entry(SPEC, primary=FABLE, secondary=lane.NEON)},
        )
        return gate, host, state_path
    gate = "behavior-review"
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={
            "peer-review-sequential": off("peer-review-sequential"),
            gate: entry(gate, primary=OPUS, secondary=lane.NEON),
            "complexity-review": single("complexity-review"),
        },
    )
    config = host / ".heddle.yaml"
    value = yaml.safe_load(config.read_text())
    value["agents"] = {"codex": False}
    config.write_text(yaml.safe_dump(value, sort_keys=False))
    return gate, host, state_path


def _launch_set_now(host: Path) -> tuple[list[tuple[str, str, str]], list[dict]]:
    """The members and exclusions the launch set derives for the host now."""
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime.readiness import current_launch_set

    config = load_project_config(host)
    launch, _actions = current_launch_set(config, resolve_snapshot(config, V7_FEATURE))
    members = [(row.role, row.slot, row.reviewer.cli) for row in launch.members]
    return members, [row.payload() for row in launch.exclusions]


@pytest.mark.parametrize("total", [250_001, 600_000], ids=["one-over", "far"])
@pytest.mark.parametrize("layout", ["same-gate", "excluded-slot"])
def test_ac8_ceiling_refusal_refuses_the_batch_before_any_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str, total: int
) -> None:
    """AC-8 through prepare-before-launch: an Antigravity secondary whose frozen
    capture is exactly 250,001 bytes, or far above, refuses the whole batch
    with its standalone refusal. The refusal names the exact size, the ceiling,
    the policy remedy, the member and the slots the launch set left out (none
    on one gate; complexity review's admission-refused codex slot in the peer
    layout). The Claude primary does not launch either: zero engine calls and
    zero doubled agy or claude processes. State is unchanged."""
    from heddle.contracts.gate_execution import VALID_GATE_CLIS
    from heddle.contracts.result import ERROR_CODES
    from tests.gate.test_antigravity_preparation import (
        assert_ceiling_refusal,
        fit_capture,
    )

    assert CEILING_REFUSAL in ERROR_CODES, (
        f"FAIL AC-8: {CEILING_REFUSAL} is not a public error code"
    )
    assert "antigravity" in VALID_GATE_CLIS, "FAIL AC-8: no antigravity lane"
    lane = _lane()
    agy = lane.install_agy(tmp_path, monkeypatch)
    gate, host, state_path = _ceiling_batch_host(tmp_path, monkeypatch, layout)
    members, exclusions = _launch_set_now(host)
    assert members == [
        (gate, PRIMARY, "claude"),
        (gate, SECONDARY, "antigravity"),
    ], f"fixture: the batch is not a Claude and Antigravity pair: {members}"
    if layout == "excluded-slot":
        assert [(row["role"], row["slot"], row["reason"]) for row in exclusions] == [
            ("complexity-review", PRIMARY, "admission-refused")
        ], f"fixture: the launch set leaves out no codex slot: {exclusions}"
    else:
        assert exclusions == [], f"fixture: the launch set leaves out {exclusions}"
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        fit_capture(host, gate, total, execute_batch)
        before = state_path.read_bytes()
        refused = execute_batch()
        batch_calls = len(calls)
        after_batch = state_path.read_bytes()
        standalone = lane.run_gate(gate, lane.NEON)
        all_calls = len(calls)
    assert not refused.ok, "FAIL AC-8: the batch ran an oversized antigravity member"
    error = refused.error
    assert_ceiling_refusal(error.code, error.message, error.hint, total, "the batch")
    assert batch_calls == 0, "FAIL AC-8: a member launched beside the refused one"
    assert agy.launches() == [] and agy.claude_calls() == [], (
        "FAIL AC-8: a provider process launched"
    )
    assert after_batch == before, "FAIL AC-8: the refused batch changed state"
    assert _selected(refused) == {
        "gate": gate,
        "scope": "feature",
        "reviewer_slot": SECONDARY,
    }, f"FAIL AC-8: the refusal does not name its member: {error}"
    assert error.details["excluded_slots"] == exclusions, (
        "FAIL AC-8: the refusal does not name the slots the launch set left out: "
        f"{error.details.get('excluded_slots')} != {exclusions}"
    )
    assert not standalone.ok and standalone.error.code == CEILING_REFUSAL, (
        f"FAIL AC-8: the slot's standalone run did not refuse: {standalone.error}"
    )
    assert all_calls == 0, "FAIL AC-8: the standalone refusal launched"
    assert (
        error.message,
        error.hint,
        refused.exit_code,
        [action.command for action in refused.next_actions],
    ) == (
        standalone.error.message,
        standalone.error.hint,
        standalone.exit_code,
        [action.command for action in standalone.next_actions],
    ), "FAIL AC-8: the batch refusal differs from the slot's standalone refusal"
    assert int(refused.exit_code) == 3


@pytest.mark.parametrize("target", ("chunk", "index", "launch"))
def test_ac9_altered_member_fails_before_launch_while_a_ready_sibling_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """AC-9 in a batch: the altered Antigravity member fails as
    ``execution-failure`` naming the changed file, with zero provider launches;
    the ready Claude sibling launches once and records."""
    lane = _lane()
    lane.require_runner("AC-9")
    agy = lane.install_agy(tmp_path, monkeypatch)
    _host, state_path = lane.spec_host(
        tmp_path, monkeypatch, secondary=lane.FABLE, prompt=lane.chunked_prompt()
    )
    record = tmp_path / "altered.txt"
    lane.alter_after_write(monkeypatch, state_path, target, record)
    result = execute_batch()
    assert not result.ok
    assert record.exists(), "fixture: the transport write seam was not reached"
    member = lane.batch_member(result, PRIMARY)
    assert (member["execution"], member["publication"]) == ("failed", "recorded-error")
    assert agy.launches() == [], f"FAIL AC-9: agy launched after the {target} changed"
    rows = lane.lane_rows(state_path)
    assert rows and rows[-1].get("failure_reason") == "execution-failure", (
        f"FAIL AC-9: the altered member recorded {rows[-1:]}"
    )
    changed = Path(record.read_text()).name
    assert changed in lane.gate_log(state_path) or changed in json.dumps(
        result.to_envelope()
    ), f"FAIL AC-9: no warning names the changed file {changed}"
    sibling = lane.batch_member(result, SECONDARY)
    assert (sibling["execution"], sibling["publication"]) == ("completed", "recorded")
    assert len(agy.claude_calls()) == 1, "FAIL AC-9: the sibling did not launch once"


def _release_when(agy, count: int, action=None) -> threading.Thread:
    """Once ``count`` launches hold, run ``action`` and release every hold."""

    def watch() -> None:
        try:
            holding = agy.wait_for("holding.jsonl", count)
            if action is not None:
                action(holding)
        finally:
            agy.release()

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def test_ac12_two_concurrent_members_reap_their_tokened_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12, two concurrent `run-gates` members: both hold at once, each run's
    plain and daemonised tokened children are gone afterwards, both record,
    and the unrelated process survives."""
    lane = _lane()
    lane.require_runner("AC-12")
    agy = lane.install_agy(tmp_path, monkeypatch)
    tracker = lane.Processes(agy)
    try:
        _host, state_path = _lane().peer_host(tmp_path, monkeypatch)
        agy.scenario(
            lane.clean_stream({"$children": ["plain", "daemon"]}, {"$hold": "release"})
        )
        watcher = _release_when(agy, 2)
        result = execute_batch()
        watcher.join(timeout=30)
        assert result.ok, result.to_envelope()
        assert len(agy.rows("holding.jsonl")) == 2, "fixture: members did not overlap"
        children = agy.children()
        assert len(children) == 4
        left = [row for row in children if lane.alive(row["pid"], row["start"])]
        assert left == [], f"FAIL AC-12: tokened children survived the batch: {left}"
        assert tracker.unrelated_alive(), "FAIL AC-12: the unrelated process died"
        assert {row["gate"] for row in lane.lane_rows(state_path)} == {
            "behavior-review",
            "complexity-review",
        }
        assert len(agy.launches()) == 2, "FAIL AC-12: reaping caused a provider call"
    finally:
        agy.release()
        tracker.cleanup()


def test_ac12_killed_worker_tokens_are_reaped_by_the_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-12, a killed worker: the coordinator reaps the member's recorded
    tokens after the worker ends. The sibling records; the unrelated process
    survives."""
    lane = _lane()
    lane.require_runner("AC-12")
    agy = lane.install_agy(tmp_path, monkeypatch)
    tracker = lane.Processes(agy)
    killed: list[int] = []
    try:
        _host, state_path = _lane().peer_host(tmp_path, monkeypatch)
        hung = [lane.init_event(), {"$children": ["daemon"]}, {"$hold": "never"}]
        agy.scenario(
            roles={
                "behavior-review": lane.clean_stream(
                    {"$children": ["daemon"]}, {"$hold": "release"}
                ),
                "complexity-review": hung,
            }
        )

        def kill_complexity_worker(_holding) -> None:
            (launch,) = [
                row for row in agy.launches() if row["role"] == "complexity-review"
            ]
            worker = launch["ppid"]
            assert worker != os.getpid(), "fixture: members do not run in workers"
            os.kill(worker, signal.SIGKILL)
            killed.append(worker)

        watcher = _release_when(agy, 2, kill_complexity_worker)
        result = execute_batch()
        watcher.join(timeout=30)
        assert killed, "fixture: the complexity worker was not killed"
        assert not result.ok
        complexity = [
            row
            for row in result.error.details["members"]
            if row["gate"] == "complexity-review"
        ]
        assert complexity and complexity[0]["execution"] in {"failed", "interrupted"}
        left = [row for row in agy.children() if lane.alive(row["pid"], row["start"])]
        assert left == [], f"FAIL AC-12: tokened children outlived the batch: {left}"
        assert tracker.unrelated_alive(), "FAIL AC-12: the unrelated process died"
        recorded = [
            row
            for row in lane.lane_rows(state_path)
            if row["gate"] == "behavior-review"
        ]
        assert recorded and recorded[-1]["verdict"]["status"] != "error", (
            "FAIL AC-12: the sibling did not record"
        )
    finally:
        agy.release()
        tracker.cleanup()


# --------------------------------------------------------------------------- #
# antigravity-reviewer-lane-v1 AC-19: each new failure reason, produced through
# the real runner with the doubled `agy`, reaches every consumer with its
# remedy: standalone `run-gate`, `run-gates` member output and (in
# tests/driver/test_driver_loop.py) the stop of `heddle drive`. A remedy names
# the reason's entry in the installed gate failure guide, which must exist.
# --------------------------------------------------------------------------- #
AC19_REASONS = (
    "antigravity-quota",
    "antigravity-api-error",
    "antigravity-context-compacted",
    "antigravity-unapproved-tool",
    "antigravity-incomplete-coverage",
    "out-of-scope-access",
)
# Reasons decided by the stream audit; the others need only the runner.
AC19_AUDITED = frozenset(AC19_REASONS[2:])
GENERIC_MEMBER_TEXT = "did not complete its review"


def ac19_stream(lane, reason: str) -> tuple[list, bool]:
    """The doubled ``agy`` stream that yields ``reason``, and whether the input
    must be chunked. Built from the double's synthetic primitives; it first
    asserts the lane modules the reason needs, so a missing one fails by name."""
    lane.require_runner("AC-19")
    if reason in AC19_AUDITED:
        lane.require_audit("AC-19")
    streams = {
        "antigravity-quota": [
            lane.init_event(),
            lane.quota_line("RESOURCE_EXHAUSTED", False),
            {"$exit": 3},
        ],
        "antigravity-api-error": [
            lane.init_event(),
            lane.quota_line("UNAVAILABLE", True),
            {"$exit": 3},
        ],
        "antigravity-context-compacted": lane.clean_stream(
            {"$step": {"step_type": "checkpoint"}}
        ),
        "antigravity-unapproved-tool": lane.clean_stream(
            lane.tool_step("search_web", {"query": "review guidance"}, "3 results")
        ),
        "antigravity-incomplete-coverage": lane.clean_stream(
            reads=lane.READ_ALL_BUT_LAST
        ),
        "out-of-scope-access": lane.clean_stream(
            result=lane.result_event(
                denied_actions=[{"action": "read_file", "display_name": "Read File"}]
            )
        ),
    }
    return streams[reason], reason == "antigravity-incomplete-coverage"


def guide_entries() -> set[str]:
    """The reason headings of the packaged gate failure guide."""
    from importlib.resources import files

    guide = files("heddle").joinpath("resources/gate-failure-guide.md")
    return {
        line[4:-1]
        for line in guide.read_text(encoding="utf-8").splitlines()
        if line.startswith("## `") and line.endswith("`")
    }


def names_reason(text: str | None, reason: str) -> bool:
    """``reason`` appears as a whole token, not inside a longer value."""
    pattern = rf"(?<![\w-]){re.escape(reason)}(?![\w-])"
    return re.search(pattern, text or "") is not None


def assert_reason_and_remedy(text: str, remedy: str, reason: str, where: str) -> None:
    """``text`` names exactly this reason; ``remedy`` names its guide entry."""
    assert names_reason(text, reason), (
        f"FAIL AC-19: the {where} does not name the reason {reason}: {text!r}"
    )
    others = [
        other
        for other in (*AC19_REASONS, "execution-failure", "unknown failure")
        if other != reason and names_reason(text, other)
    ]
    assert others == [], f"FAIL AC-19: the {where} names {others} beside {reason}"
    assert names_reason(remedy, reason) and "failure guide" in remedy.casefold(), (
        f"FAIL AC-19: the {where} remedy does not point at the {reason} entry of "
        f"the installed gate failure guide: {remedy!r}"
    )
    assert reason in guide_entries(), (
        f"FAIL AC-19: the packaged gate failure guide has no `{reason}` entry"
    )


def member_reason_text(member: dict) -> tuple[str, str]:
    """A batch member summary's reason text and remedy text, wherever the
    summary carries them: its error, or a reason, remedy or action field."""
    error = member.get("error") or {}
    text = " ".join(
        str(value)
        for value in (
            error.get("message"),
            member.get("failure_reason"),
            member.get("reason"),
        )
        if value
    )
    remedy = " ".join(
        str(value)
        for value in (
            error.get("hint"),
            member.get("remedy"),
            member.get("hint"),
            *(row.get("reason") for row in member.get("next_actions") or ()),
        )
        if value
    )
    return text, remedy


def assert_member_names_reason(result, gate: str, reason: str) -> None:
    member = _lane().gate_member(result, gate)
    assert (member["execution"], member["publication"]) == (
        "failed",
        "recorded-error",
    ), f"fixture: the {gate} member did not fail and record: {member}"
    text, remedy = member_reason_text(member)
    assert GENERIC_MEMBER_TEXT not in text, (
        f"FAIL AC-19: the {gate} member summary is the generic text: {text!r}"
    )
    assert_reason_and_remedy(text, remedy, reason, f"{gate} member summary")


def assert_batch_error_names_selected_reason(result, reasons: dict[str, str]) -> None:
    """The batch error names the selected member's reason and remedy, not the
    generic member text, so a consumer of the top-level result reads it."""
    selected = result.error.details["selected_member"]["gate"]
    assert GENERIC_MEMBER_TEXT not in result.error.message, (
        f"FAIL AC-19: the run-gates error is the generic text: {result.error.message}"
    )
    assert_reason_and_remedy(
        result.error.message, result.error.hint, reasons[selected], "run-gates error"
    )


def assert_produced_by_the_runner(state_path: Path, agy, gate: str, reason: str):
    """The reason was recorded from one real launch of the doubled ``agy``."""
    rows = [row for row in _lane().lane_rows(state_path) if row["gate"] == gate]
    assert rows and rows[-1].get("failure_reason") == reason, (
        f"FAIL AC-19: {gate} recorded {rows[-1:]} through the runner, not {reason}"
    )
    launches = [row for row in agy.launches() if row["role"] == gate]
    assert len(launches) == 1, f"fixture: {gate} launched agy {len(launches)} times"


@pytest.mark.parametrize("reason", AC19_REASONS)
def test_ac19_new_reason_and_remedy_reach_standalone_run_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """AC-19 standalone: the real runner fails on the doubled ``agy`` stream
    with the new reason, and the ``run-gate`` error names exactly that reason
    and its failure-guide remedy."""
    lane = _lane()
    stream, chunked = ac19_stream(lane, reason)
    agy = lane.install_agy(tmp_path, monkeypatch)
    _host, state_path = lane.spec_host(
        tmp_path,
        monkeypatch,
        prompt=lane.chunked_prompt(80_000) if chunked else None,
    )
    agy.scenario(stream)
    result = lane.run_gate()
    assert not result.ok, f"FAIL AC-19: {reason} recorded as a success"
    assert_produced_by_the_runner(state_path, agy, SPEC, reason)
    assert_reason_and_remedy(
        result.error.message, result.error.hint, reason, "run-gate error"
    )


AC19_PAIRS = (
    ("antigravity-quota", "antigravity-context-compacted"),
    ("antigravity-api-error", "antigravity-unapproved-tool"),
    ("antigravity-incomplete-coverage", "out-of-scope-access"),
)


@pytest.mark.parametrize(
    ("first", "second"), AC19_PAIRS, ids=["+".join(pair) for pair in AC19_PAIRS]
)
def test_ac19_new_reasons_and_remedies_reach_run_gates_member_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str, second: str
) -> None:
    """AC-19 batch: two Antigravity members fail through the real runner, one
    reason each. Each member summary names its own reason and remedy, never
    the generic text or its sibling's reason, and the batch error names the
    selected member's reason."""
    lane = _lane()
    reasons = dict(zip(lane.PEER, (first, second), strict=True))
    built = {gate: ac19_stream(lane, reason) for gate, reason in reasons.items()}
    agy = lane.install_agy(tmp_path, monkeypatch)
    _host, state_path = lane.peer_host(
        tmp_path, monkeypatch, chunked=any(chunked for _s, chunked in built.values())
    )
    agy.scenario(roles={gate: stream for gate, (stream, _c) in built.items()})
    result = execute_batch()
    assert not result.ok, f"FAIL AC-19: the failing batch succeeded: {result}"
    for gate, reason in reasons.items():
        assert_produced_by_the_runner(state_path, agy, gate, reason)
        assert_member_names_reason(result, gate, reason)
    assert_batch_error_names_selected_reason(result, reasons)


def test_ac19_run_gates_member_names_its_reason_not_the_generic_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-19, lane-neutral: a member that fails with a typed reason
    (``execution-failure`` from the engine double) is summarized by that reason
    and its failure-guide remedy, and the batch error names it too. Red until
    the batch summary carries reasons: today both hold the generic text."""
    same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, first=S, failures=(P,))
        result = execute_batch()
    assert not result.ok
    member = result.error.details["members"][0]
    assert member_key(member) == P
    assert (member["execution"], member["publication"]) == ("failed", "recorded-error")
    text, remedy = member_reason_text(member)
    assert GENERIC_MEMBER_TEXT not in text, (
        f"FAIL AC-19: the failed member's summary is the generic text: {text!r}"
    )
    assert names_reason(text, "execution-failure"), (
        f"FAIL AC-19: the failed member's summary does not name its reason: {member}"
    )
    assert names_reason(remedy, "execution-failure") and (
        "failure guide" in remedy.casefold()
    ), f"FAIL AC-19: the failed member's summary names no remedy: {member}"
    assert GENERIC_MEMBER_TEXT not in result.error.message, (
        f"FAIL AC-19: the run-gates error is the generic text: {result.error.message}"
    )
    assert names_reason(result.error.message, "execution-failure"), result.error


def test_ac19_survivor_standalone_failure_names_its_reason_and_guide_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin (AC-19): a standalone ``run-gate`` that fails with a typed
    reason already names it and points at its failure-guide entry. m7 edits the
    batch summary beside this path, so it must keep passing."""
    same_gate_host(tmp_path, monkeypatch)
    with multiprocessing.Manager() as manager:
        install_slot_engine(monkeypatch, manager, parties=1, failures=(P,))
        result = run_slot(*P)
    assert not result.ok
    assert names_reason(result.error.message, "execution-failure"), result.error
    assert names_reason(result.error.hint, "execution-failure"), result.error
    assert "failure guide" in result.error.hint.casefold()
    assert "execution-failure" in guide_entries()
