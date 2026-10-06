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

import multiprocessing
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


@pytest.mark.parametrize(
    ("verdicts", "exit_code"),
    (
        (("fail", "pass_with_conditions"), 3),
        (("pass_with_conditions", "pass"), 4),
        (("pass_with_conditions", "pass_with_conditions"), 4),
        (("pass", "fail"), 3),
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
