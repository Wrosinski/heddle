"""concurrent-reviews-v1 AC-2, AC-3 and AC-11: one stage-agnostic launch rule.

Readiness is read through the typed application boundary over synthetic
hosts; standalone setup calls use the doubled provider transport and batch
runs the fork-safe engine double, so admission, preparation, recording and
readiness stay real. The resolver is also read directly over an in-memory
snapshot for the one AC-2 state no command sequence reaches: an earlier-stage
review with a missing slot beside the current stage's review (a stage
transition seals the earlier review only after it closes).

Red before m2: readiness groups only the behavior/complexity pair at peer
review, ignores the host launch setting and admission, and the resolver
module does not exist. Survivor pins are labelled in their docstrings.
"""

from __future__ import annotations

import ast
import multiprocessing
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from tests.concurrent_review_helpers import (
    PRIMARY,
    SECONDARY,
    batch_command,
    commands,
    dual,
    execute_batch,
    full_peer_host,
    gate_runs,
    install_counting_engine,
    install_slot_engine,
    member_key,
    reach_round_two,
    run_gate_commands,
    run_slot,
    same_gate_host,
    set_gates_enabled,
    set_launch,
    slot_command,
    status_actions,
)
from tests.tiering_review_helpers import V7_FEATURE, current_host

REVIEW_ROLES = (
    "spec-review",
    "plan-review",
    "review-test-scaffolding",
    "milestone-review",
    "peer-review-sequential",
)


def _batch_action(actions):
    rows = [row for row in actions if row.command == batch_command()]
    assert len(rows) == 1, (
        f"FAIL AC-2: expected one run-gates action in {commands(actions)}"
    )
    return rows[0]


def _names(reason: str, members) -> None:
    """The batch action's reason names every member as '<role> <slot>'."""
    for role, slot in members:
        assert f"{role} {slot}" in reason, (
            f"FAIL AC-2: run-gates reason {reason!r} does not name {role} {slot}"
        )


def _resolver():
    from heddle.runtime import review_launch

    return review_launch.launch_set


def _resolve(actions, *, snapshot=None, milestone_scope=None):
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime.review_assignments import projection

    config = load_project_config(Path.cwd())
    snapshot = snapshot or resolve_snapshot(config, V7_FEATURE)
    observed = projection(config, snapshot)
    return _resolver()(
        config, snapshot, observed, actions, milestone_scope=milestone_scope
    )


@pytest.mark.parametrize("role", REVIEW_ROLES)
def test_ac1_ac2_two_slots_of_one_review_project_one_batch_at_every_stage(
    role: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    same_gate_host(tmp_path, monkeypatch, role=role)
    actions = status_actions()
    action = _batch_action(actions)
    assert not run_gate_commands(actions, role), (
        f"FAIL AC-1: {role} still routes a single run-gate: {commands(actions)}"
    )
    _names(action.reason, [(role, PRIMARY), (role, SECONDARY)])


def test_ac2_proof_refresh_and_session_actions_keep_their_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-call actions keep their order; the batch takes the first slot's place."""
    same_gate_host(tmp_path, monkeypatch, role="milestone-review")
    set_launch(Path.cwd(), "sequential")
    sequential = status_actions()
    set_launch(Path.cwd(), "concurrent")
    concurrent = status_actions()
    first_gate = next(
        index
        for index, command in enumerate(commands(sequential))
        if command.startswith("heddle run-gate ")
    )
    expected = [
        *commands(sequential)[:first_gate],
        batch_command(),
        *(
            command
            for command in commands(sequential)[first_gate + 1 :]
            if not command.startswith("heddle run-gate milestone-review ")
        ),
    ]
    assert commands(concurrent) == expected
    assert commands(concurrent)[0].startswith("heddle verify --scope m1 ")


def test_ac2_full_peer_review_round_one_joins_both_gates_in_catalog_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    full_peer_host(tmp_path, monkeypatch)
    actions = status_actions()
    action = _batch_action(actions)
    assert commands(actions).index(batch_command()) == 2
    assert all(c.startswith("heddle verify ") for c in commands(actions)[:2])
    assert not run_gate_commands(actions)
    reason = action.reason
    _names(
        reason,
        [
            ("behavior-review", PRIMARY),
            ("behavior-review", SECONDARY),
            ("complexity-review", PRIMARY),
        ],
    )
    order = [
        reason.index(name)
        for name in (
            "behavior-review primary",
            "behavior-review secondary",
            "complexity-review primary",
        )
    ]
    assert order == sorted(order), f"FAIL AC-2: members out of catalog order: {reason}"


def test_ac2_a_later_round_projects_that_round_missing_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, state_path = same_gate_host(tmp_path, monkeypatch, window="all")
    reach_round_two(state_path, monkeypatch)
    actions = status_actions()
    _names(
        _batch_action(actions).reason,
        [("spec-review", PRIMARY), ("spec-review", SECONDARY)],
    )
    assert not run_gate_commands(actions)


def test_ac2_one_member_set_stays_a_single_run_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin: a primary-only review and a lone missing slot stay single."""
    current_host(tmp_path, monkeypatch)
    actions = status_actions()
    assert batch_command() not in commands(actions)
    assert run_gate_commands(actions, "spec-review") == [
        slot_command("spec-review", PRIMARY)
    ]


def test_ac2_ac3_lone_missing_secondary_after_accepted_primary_stays_single(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin: an accepted slot is never missing, so one member remains."""
    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    from tests.tiering_review_helpers import provider_transport, review_content

    provider_transport(monkeypatch, lambda _cli, _prompt: review_content())
    assert run_slot("spec-review", PRIMARY).ok
    actions = status_actions()
    assert batch_command() not in commands(actions)
    assert run_gate_commands(actions, "spec-review") == [
        slot_command("spec-review", SECONDARY)
    ]
    before = state_path.read_bytes()
    refused = execute_batch()
    assert not refused.ok and state_path.read_bytes() == before


def test_ac2_milestone_review_takes_only_the_scope_readiness_selects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.contracts import operations as ops
    from heddle.contracts.result import NextAction
    from heddle.runtime.application import execute

    same_gate_host(tmp_path, monkeypatch, role="milestone-review")
    status = execute(ops.Status(feature=V7_FEATURE))
    open_scopes = {
        row["scope"]
        for row in status.data["review_closure"]["assignments"]
        if row["role"] == "milestone-review" and not row["closed"]
    }
    assert open_scopes == {"m1", "m2"}, "fixture precondition: two scopes are open"
    action = NextAction(
        ops.CommandAction(ops.RunGate("milestone-review", feature=V7_FEATURE)),
        "complete milestone-review m1 slot primary",
    )
    members = _resolve([action, action], milestone_scope="m1")
    assert [(m.role, m.scope, m.slot) for m in members] == [
        ("milestone-review", "m1", PRIMARY),
        ("milestone-review", "m1", SECONDARY),
    ]
    assert [m.action_index for m in members] == [0, 0]


def test_ac2_resolver_joins_an_earlier_stage_duty_beside_the_current_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.contracts import operations as ops
    from heddle.contracts.result import NextAction
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": dual("spec-review"),
            "plan-review": dual("plan-review"),
        },
    )
    snapshot = resolve_snapshot(load_project_config(Path.cwd()), V7_FEATURE)
    later = replace(
        snapshot,
        stage="plan-review",
        state=replace(snapshot.state, stage="plan-review"),
    )
    current = status_actions()
    assert commands(current)[0].startswith("heddle kickoff ")
    assert commands(current)[1] == slot_command("spec-review", PRIMARY)
    actions = [
        *current[:2],
        NextAction(
            ops.CommandAction(ops.RunGate("plan-review", feature=V7_FEATURE)),
            "complete plan-review feature slot primary",
        ),
    ]
    members = _resolve(actions, snapshot=later)
    assert [(m.role, m.slot, m.action_index) for m in members] == [
        ("spec-review", PRIMARY, 1),
        ("spec-review", SECONDARY, 1),
        ("plan-review", PRIMARY, 2),
        ("plan-review", SECONDARY, 2),
    ]
    assert members[1].reviewer.cli == "claude"
    assert members[2].reviewer.model == "gpt-6-sol"


def test_ac2_no_closed_group_and_no_role_or_stage_list() -> None:
    """Inspection half of AC-2: one data-driven resolver, no declared group."""
    import heddle.contracts.gates as gates
    import heddle.runtime.gate_run as gate_run
    from heddle.contracts.review_assignments import ROLE_STAGES
    from heddle.contracts.schemas import STAGES

    assert not hasattr(gates, "INDEPENDENT_GATE_GROUPS"), (
        "FAIL AC-2: the closed declared group still exists"
    )
    assert not hasattr(gate_run, "_DISTINCT_GATE_GROUP")
    source = Path(gate_run.__file__).with_name("review_launch.py")
    assert source.is_file(), "FAIL AC-2: heddle/runtime/review_launch.py is missing"
    names = set(ROLE_STAGES) | set(STAGES)
    literals = {
        node.value
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not literals & names, (
        f"FAIL AC-2: the resolver names roles or stages: {sorted(literals & names)}"
    )


def test_ac3_off_role_is_never_a_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    full_peer_host(tmp_path, monkeypatch, complexity="off")
    action = _batch_action(status_actions())
    _names(
        action.reason, [("behavior-review", PRIMARY), ("behavior-review", SECONDARY)]
    )
    assert "complexity-review" not in action.reason
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        result = execute_batch()
        started = sorted((row["gate"], row["reviewer_slot"]) for row in starts)
    assert result.ok, result.to_envelope()
    assert started == [("behavior-review", PRIMARY), ("behavior-review", SECONDARY)]


def test_ac3_host_gate_selection_excludes_its_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _state_path = full_peer_host(tmp_path, monkeypatch)
    set_gates_enabled(host, ["behavior-review"])
    action = _batch_action(status_actions())
    assert "complexity-review" not in action.reason
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        result = execute_batch()
        started = sorted((row["gate"], row["reviewer_slot"]) for row in starts)
    assert result.ok, result.to_envelope()
    assert [member_key(row) for row in result.data["members"]] == [
        ("behavior-review", PRIMARY),
        ("behavior-review", SECONDARY),
    ]
    assert started == [("behavior-review", PRIMARY), ("behavior-review", SECONDARY)]


def test_ac3_unauthorized_stage_keeps_the_single_gate_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin: an unauthorized review stays single and run-gates refuses."""
    from tests.concurrent_review_helpers import edit_state

    _host, state_path = same_gate_host(tmp_path, monkeypatch)
    edit_state(
        state_path,
        lambda value: value.__setitem__(
            "authorizations",
            [{"through": "specify", "source": "user", "at": value["updated"]}],
        ),
    )
    actions = status_actions()
    assert batch_command() not in commands(actions)
    assert run_gate_commands(actions, "spec-review") == [
        slot_command("spec-review", PRIMARY)
    ]
    before = state_path.read_bytes()
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        refused = execute_batch()
        assert len(calls) == 0
    assert not refused.ok and state_path.read_bytes() == before


def test_ac3_sealed_review_never_joins_a_later_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        provider_transport,
        review_content,
    )

    _host, state_path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"plan-review": dual("plan-review")},
    )
    provider_transport(monkeypatch, lambda _cli, _prompt: review_content())
    assert run_slot("spec-review", PRIMARY).ok
    origin = gate_runs(state_path)[-1]["run_id"]
    assert dispose(state_path, [disposition(origin, "@coverage", status="settled")]).ok
    advanced = execute(ops.PhaseExit(feature=V7_FEATURE))
    assert advanced.ok, advanced.to_envelope()
    action = _batch_action(status_actions())
    _names(action.reason, [("plan-review", PRIMARY), ("plan-review", SECONDARY)])
    assert "spec-review" not in action.reason


def test_ac3_accepted_slot_with_changed_input_is_never_called_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _state_path = full_peer_host(tmp_path, monkeypatch)
    from tests.tiering_review_helpers import provider_transport, review_content

    provider_transport(
        monkeypatch, lambda _cli, _prompt: review_content("behavior-review")
    )
    assert run_slot("behavior-review", PRIMARY).ok
    (host / "src/example.py").write_text("VALUE = 8\n")
    action = _batch_action(status_actions())
    _names(
        action.reason,
        [("behavior-review", SECONDARY), ("complexity-review", PRIMARY)],
    )
    assert "behavior-review primary" not in action.reason
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        execute_batch()
        started = sorted((row["gate"], row["reviewer_slot"]) for row in starts)
    assert started == [("behavior-review", SECONDARY), ("complexity-review", PRIMARY)]


def test_ac3_changed_source_output_keeps_its_review_out_of_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, state_path = full_peer_host(tmp_path, monkeypatch, complexity="dual")
    from heddle.gate import entry
    from tests.concurrent_review_helpers import canonical_outcome

    def drifting(gate_type, context, *, feature, **_kwargs):
        outcome = canonical_outcome(gate_type, context, feature)
        spec = host / f"docs/features/runtime/{V7_FEATURE}.md"
        spec.write_text(spec.read_text() + "\nmaterial drift\n")
        return outcome

    monkeypatch.setattr(entry, "run_gate_for_runtime", drifting)
    drifted = run_slot("behavior-review", PRIMARY)
    assert not drifted.ok
    pending = [
        attempt
        for attempt in yaml.safe_load(state_path.read_text())["review_assignments"][
            "attempts"
        ]
        if "source" in str(attempt["outcome"].get("reason", ""))
    ]
    assert pending, "fixture precondition: a changed-source attempt is recorded"
    actions = status_actions()
    action = _batch_action(actions)
    _names(
        action.reason,
        [("complexity-review", PRIMARY), ("complexity-review", SECONDARY)],
    )
    assert "behavior-review" not in action.reason
    assert run_gate_commands(actions, "behavior-review"), (
        "FAIL AC-3: the excluded review lost its own single-gate action"
    )
    with multiprocessing.Manager() as manager:
        starts, _finishes, _seen = install_slot_engine(monkeypatch, manager)
        execute_batch()
        started = sorted((row["gate"], row["reviewer_slot"]) for row in starts)
    assert started == [("complexity-review", PRIMARY), ("complexity-review", SECONDARY)]


@pytest.mark.parametrize("shape", ("same-gate", "behavior-and-complexity"))
def test_ac11_sequential_mode_routes_one_run_gate_per_slot(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shape == "same-gate":
        host, state_path = same_gate_host(tmp_path, monkeypatch, launch="sequential")
        first = slot_command("spec-review", PRIMARY)
    else:
        host, state_path = full_peer_host(
            tmp_path, monkeypatch, behavior_secondary=False, launch="sequential"
        )
        first = slot_command("behavior-review", PRIMARY)
    before = state_path.read_bytes()
    actions = status_actions()
    assert batch_command() not in commands(actions), (
        f"FAIL AC-11: sequential mode still offers a batch: {commands(actions)}"
    )
    assert run_gate_commands(actions)[0] == first
    set_launch(host, "concurrent")
    assert batch_command() in commands(status_actions())
    set_launch(host, "sequential")
    assert batch_command() not in commands(status_actions())
    assert state_path.read_bytes() == before, (
        "FAIL AC-11: switching the launch mode changed feature state"
    )


@pytest.mark.parametrize("shape", ("same-gate", "behavior-and-complexity"))
def test_ac11_run_gates_refuses_in_sequential_mode_without_writing(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shape == "same-gate":
        _host, state_path = same_gate_host(tmp_path, monkeypatch, launch="sequential")
    else:
        _host, state_path = full_peer_host(
            tmp_path, monkeypatch, behavior_secondary=False, launch="sequential"
        )
    before = state_path.read_bytes()
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        refused = execute_batch()
        call_count = len(calls)
    assert not refused.ok, "FAIL AC-11: run-gates ran in sequential mode"
    assert call_count == 0
    assert state_path.read_bytes() == before
    assert any(
        action.command.startswith("heddle run-gate ") for action in refused.next_actions
    ), "FAIL AC-11: the refusal does not return the current single-gate action"
