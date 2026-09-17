"""Escalation taxonomy, attempt limits, decisions, and notification behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.driver.constants import AUTO_TIER2_PLAN
from tests.driver.constants import AUTO_TIER2_SLUG as SLUG
from tests.driver.helpers import (
    append_autopilot_notify_command,
    arm_at_stage,
    make_first_milestone_current_and_taskless,
    resolve_decision,
    set_autopilot_notify_timeout,
    write_executable_script,
)
from tests.operational_model_helpers import (
    decision,
    native_run_fields,
    new_run_id,
    report,
    resolved,
    run,
)

# The decision-raising escalation classes (class 4 is gate-recorded, not added by
# the decision-batch schema) — Feature Spec → Escalation taxonomy. This enum
# backs AC-10's DECISION_BATCH_CLASSES assertion.
DECISION_RAISING_CLASSES = (1, 2, 3, 5, 6, 7, 8)

# The full taxonomy — all 8 classes. AC-14 exercises pause→resolve→resume for
# EACH class, including class 4 (a `report`-classified review recorded by
# gate's run-gate machinery as a pending decision; Feature Spec → Escalation
# taxonomy, row 4 / finding taxonomy).
ALL_ESCALATION_CLASSES = (1, 2, 3, 4, 5, 6, 7, 8)
DERIVED_ESCALATION_BLOCKS = {
    3: "review-cap-reached",
    6: "no-progress",
    7: "attempt-cap",
}
SEED_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "escalation-seeds"


def _seed_fixture_paths() -> tuple[Path, ...]:
    return tuple(sorted(SEED_FIXTURES.glob("class-*.json")))


def _load_seed_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _arm(driver_corpus, host, **overrides):
    return driver_corpus.arm(host, SLUG, **overrides)


def _arm_at_stage(driver_corpus, host, stage: str, **overrides):
    return arm_at_stage(driver_corpus, host, SLUG, stage, **overrides)


def _arm_implement(driver_corpus, host, **overrides):
    return _arm_at_stage(driver_corpus, host, "implement", **overrides)


def _status_blocks(run_cli, envelope_tools) -> list[str]:
    _code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    return envelope_tools.parse(out)["data"]["blocking_conditions"]


def _pending_decision_fact(
    decision_id: str = "E1",
    *,
    escalation_class: int | None = None,
    title: str = "pause me",
    source: str = "session",
    status: str = "pending",
    question: str | None = None,
    options: list[str] | None = None,
    routes_to: str | list[str] = AUTO_TIER2_PLAN,
) -> dict[str, object]:
    fact = decision(
        decision_id,
        kind="question",
        source=source,
        title=title,
        status=status,
        routes_to=[routes_to] if isinstance(routes_to, str) else routes_to,
        created_at="2026-06-16T09:00Z",
    )
    if escalation_class is not None:
        fact["class"] = escalation_class
    if question is not None:
        fact["question"] = question
    if options is not None:
        fact["options"] = options
    return fact


def _session_fact(stage: str, advanced: bool, idx: int) -> dict:
    return {
        "started_at": f"2026-06-16T09:0{idx}Z",
        "ended_at": f"2026-06-16T09:1{idx}Z",
        "completed": ["phase session"],
        "started": [],
        "key_context": "",
        "next_steps": "",
        "blockers": [],
        "stage": stage,
        "revision_before": 3 + idx,
        "revision_after": 3 + idx + (1 if advanced else 0),
        "advanced": advanced,
    }


def _nonconverged_specreview_runs(n: int) -> list[dict]:
    return [
        {
            "gate": "spec-review",
            "scope": "feature",
            "runs": [
                {
                    **native_run_fields("claude"),
                    "run_id": new_run_id(),
                    "report_findings": [],
                    "at": f"2026-06-16T09:{i:02d}Z",
                    "cli": "claude",
                    "artifact": f"reviews/spec-review-{i}.review.json",
                    "input_hash": f"sha256:run{i}",
                    "verdict": {"status": "fail", "rerun_recommended": True},
                    "findings": {
                        "by_severity": {"critical": 1, "important": 0, "minor": 0},
                        "by_classification": {
                            "implement": 1,
                            "report": 0,
                            "ignore": 0,
                            "unknown": 0,
                        },
                        "total": 1,
                        "contradictions": 0,
                    },
                }
                for i in range(n)
            ],
        }
    ]


def _decision_batch_payload(escalation_class: int = 8) -> dict:
    return {
        "schema": "heddle.decision-batch/v1",
        "decisions": [
            {
                "kind": "question",
                "class": escalation_class,
                "source": "session",
                "title": f"class {escalation_class} escalation",
                "question": "May autopilot continue?",
                "options": ["continue", "stop"],
                "routes_to": [AUTO_TIER2_PLAN],
            }
        ],
    }


def _overrides_from_seed(seed_fixture: dict) -> tuple[str, dict]:
    state_seed = seed_fixture["state_seed"]
    kind = state_seed["kind"]
    stage = seed_fixture["stage"]
    if kind == "review-cap":
        return stage, {
            "gates": _nonconverged_specreview_runs(state_seed["nonconverged_runs"])
        }
    if kind == "no-progress":
        return stage, {
            "sessions": [
                _session_fact(stage, advanced=False, idx=i)
                for i in range(state_seed["sessions"])
            ]
        }
    if kind == "attempt-cap":
        return stage, {
            "sessions": [
                _session_fact(stage, advanced=True, idx=i)
                for i in range(state_seed["sessions"])
            ]
        }
    if kind == "pending-decision":
        if seed_fixture["class"] == 4:
            finding_run = run(new_run_id(), reports=[report()])
            return stage, {
                "gates": [
                    {
                        "gate": "spec-review-synthesis",
                        "scope": "feature",
                        "runs": [finding_run],
                    }
                ],
                "decisions": [
                    decision(
                        state_seed["decision_id"],
                        origin_run_id=finding_run["run_id"],
                        title=state_seed["title"],
                    )
                ],
            }
        return stage, {
            "decisions": [
                _pending_decision_fact(
                    decision_id=state_seed["decision_id"],
                    escalation_class=seed_fixture["class"]
                    if seed_fixture["mechanism"] == "decisions-batch"
                    else None,
                    source=state_seed["source"],
                    title=state_seed["title"],
                    question=state_seed.get("question", "May autopilot continue?"),
                    options=state_seed.get("options", ["continue", "stop"]),
                    status=state_seed.get("status", "pending"),
                    routes_to=state_seed["routes_to"],
                )
            ]
        }
    raise AssertionError(f"unknown seed fixture kind {kind!r}")


# --------------------------------------------------------------------------- #
# AC-10 — escalation-taxonomy completeness (structural)
# --------------------------------------------------------------------------- #
def test_ac10_taxonomy_complete_each_class_one_mechanism():
    from heddle.driver import escalations

    registry = escalations.ESCALATION_REGISTRY
    classes = set(registry)
    assert classes == {1, 2, 3, 4, 5, 6, 7, 8}, (
        f"FAIL AC-10: the taxonomy must enumerate all 8 classes, got {sorted(classes)}"
    )
    for cls, mechanism in registry.items():
        assert mechanism in ("blocking-condition", "decisions-batch"), (
            f"FAIL AC-10: class {cls} maps to an unknown mechanism {mechanism!r}"
        )
    enum = set(escalations.DECISION_BATCH_CLASSES)
    assert enum == set(DECISION_RAISING_CLASSES), (
        f"FAIL AC-10: decision-batch class enum must be {DECISION_RAISING_CLASSES}, "
        f"got {sorted(enum)}"
    )
    # The irreversible/contract-touching/precedent-setting open question (class 8,
    # Feature Spec → Escalation taxonomy) must be registered to ESCALATE (a
    # `decisions` batch), never to auto-resolve via a blocking condition.
    assert registry.get(8) == "decisions-batch", (
        "FAIL AC-10: class 8 (irreversible open question, no covering principle) "
        f"must escalate as a decisions-batch, got {registry.get(8)!r}"
    )


def test_ac10_seed_fixture_corpus_contains_each_escalation_class():
    seen: dict[int, dict] = {}
    for path in sorted(SEED_FIXTURES.glob("class-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        seen[payload["class"]] = payload
    assert set(seen) == set(ALL_ESCALATION_CLASSES), (
        "FAIL AC-14: missing committed seeded blocker fixture for escalation "
        f"class(es); got {sorted(seen)}"
    )
    for escalation_class, payload in seen.items():
        assert payload["mechanism"] in ("blocking-condition", "decisions-batch"), (
            f"FAIL AC-14: seeded fixture class {escalation_class} has unknown "
            f"mechanism {payload['mechanism']!r}"
        )
        assert isinstance(payload.get("state_seed"), dict), (
            f"FAIL AC-14: seed fixture class {escalation_class} must carry a "
            "structured state_seed used by the drive test"
        )
    exercised = {_load_seed_fixture(path)["class"] for path in _seed_fixture_paths()}
    assert exercised == set(ALL_ESCALATION_CLASSES), (
        "FAIL AC-14: every committed seed fixture must be selected by the "
        f"drive pause/resume test, got {sorted(exercised)}"
    )
    from heddle.driver import escalations
    from heddle.runtime import decisions

    assert set(decisions.DECISION_BATCH_CLASSES) == set(
        escalations.DECISION_BATCH_CLASSES
    ), "FAIL AC-10: decision-batch class enum drifted from escalation registry"


# --------------------------------------------------------------------------- #
# AC-11 — review-cap-reached is flow-conditional
# --------------------------------------------------------------------------- #
# (flow, non-converged runs, expect review-cap-reached). The (auto, 4, False)
# row is the below-threshold fixture (review): the cap must NOT fire at 4 runs,
# bracketing the boundary from the outside so a "block regardless of count"
# regression is caught.


# --------------------------------------------------------------------------- #
# AC-12 / AC-7 at-cap — attempt cap and must-advance from recorded facts
# --------------------------------------------------------------------------- #
def test_ac12_attempt_cap_blocks_sixth_session(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    _arm_implement(
        driver_corpus,
        host,
        sessions=[_session_fact("implement", advanced=True, idx=i) for i in range(5)],
    )
    monkeypatch.chdir(host)

    blocks = _status_blocks(run_cli, envelope_tools)
    # Assert the attempt-cap derivation specifically (no `pending-decisions`
    # disjunct — the 5-session fixture seeds no decisions, so that disjunct was
    # dead and only widened the accept set; review).
    assert "attempt-cap" in " ".join(blocks), (
        "FAIL AC-12: a sixth-session need (5 recorded sessions) must raise the "
        "attempt-cap block, derived from recorded facts"
    )


def test_ac12_must_advance_escalates_on_two_no_progress_sessions(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    _arm_implement(
        driver_corpus,
        host,
        sessions=[_session_fact("implement", advanced=False, idx=i) for i in range(2)],
    )
    monkeypatch.chdir(host)

    blocks = _status_blocks(run_cli, envelope_tools)
    # review: the fixture seeds two no-progress sessions and NO decisions, so the
    # former `pending-decisions` disjunct was dead — it only widened the accept set
    # (an implementation that surfaced an unrelated `pending-decisions` block and
    # never derived `no-progress` would have passed). Assert the canonical
    # must-advance token directly (matching the attempt-cap sibling and the AC-14
    # class-6 mapping).
    assert "no-progress" in " ".join(blocks), (
        "FAIL AC-12: two consecutive no-advance sessions must escalate (must-advance) "
        f"with a `no-progress` block, got {blocks}"
    )


def test_ac12_no_progress_uses_last_two_session_window(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    _arm_implement(
        driver_corpus,
        host,
        sessions=[
            _session_fact("implement", advanced=True, idx=0),
            _session_fact("implement", advanced=False, idx=1),
            _session_fact("implement", advanced=False, idx=2),
        ],
    )
    monkeypatch.chdir(host)

    blocks = _status_blocks(run_cli, envelope_tools)
    assert "no-progress" in " ".join(blocks), (
        "FAIL AC-12: no-progress must escalate when the last two recorded "
        f"sessions did not advance, got {blocks}"
    )


# Outside-window fixtures (review): the threshold ACs need a fixture INSIDE the
# window (above) AND one outside (here) — otherwise an implementation that blocks
# regardless of count passes. 4 sessions is below the attempt cap (5); a single
# no-progress session is below the must-advance threshold (2).
def test_ac12_four_sessions_below_attempt_cap_does_not_block(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    _arm_implement(
        driver_corpus,
        host,
        sessions=[_session_fact("implement", advanced=True, idx=i) for i in range(4)],
    )
    monkeypatch.chdir(host)

    blocks = _status_blocks(run_cli, envelope_tools)
    assert "attempt-cap" not in " ".join(blocks), (
        "FAIL AC-12: four recorded sessions are below the cap — attempt-cap must "
        f"NOT fire, got blocks={blocks}"
    )


def test_ac12_one_no_progress_session_below_must_advance_does_not_escalate(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    _arm_implement(
        driver_corpus,
        host,
        sessions=[_session_fact("implement", advanced=False, idx=0)],
    )
    monkeypatch.chdir(host)

    blocks = _status_blocks(run_cli, envelope_tools)
    assert "no-progress" not in " ".join(blocks), (
        "FAIL AC-12: a single no-advance session is below the must-advance "
        f"threshold (2) — no-progress must NOT escalate, got blocks={blocks}"
    )


def test_ac07_at_cap_raises_attempt_cap_batch_and_pauses(
    run_json,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
):
    # E1-2: the session-projecting state (current task-less milestone → task
    # add session); all-todo now derives the driver-owned promote advance.
    host = auto_tier2_workspace(
        chdir=False, mutate=make_first_milestone_current_and_taskless
    )
    _arm_implement(
        driver_corpus,
        host,
        sessions=[_session_fact("implement", advanced=True, idx=i) for i in range(4)],
    )
    monkeypatch.chdir(host)

    # The fake claude writes no artifact; the 5th session fails validation at the
    # cap → the driver raises the class-7 attempt-cap decision batch and pauses
    # (it does NOT silently retry past the cap).
    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    state = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    pending = [d for d in state.get("decisions", []) if d.get("status") != "resolved"]
    assert pending, (
        "FAIL AC-7 (at-cap): the 5th-session validation failure must raise an "
        "attempt-cap decision batch (class 7) and pause"
    )
    # The batch is specifically class 7 (attempt-cap), not some other escalation.
    assert any(d.get("class") == 7 for d in pending), (
        "FAIL AC-7 (at-cap): the raised decision batch must be class 7 "
        f"(attempt-cap), got classes {[d.get('class') for d in pending]}"
    )
    # And the driver ran EXACTLY the 5th validating session, then stopped at the
    # cap — it did not raise the cap before attempting the 5th (which `<= 1` would
    # vacuously allow, review), nor spawn a 6th past it (4 recorded + the 5th this
    # run = 5; never a 6th).
    assert len(fake_claude.calls()) == 1, (
        "FAIL AC-7 (at-cap): the driver must run exactly the 5th validating "
        "session and then raise the cap — not raise it before the 5th attempt, and "
        f"not retry a 6th past it; got {len(fake_claude.calls())} spawns this run"
    )


# --------------------------------------------------------------------------- #
# AC-13 — decisions commands wired; resolution is user-sourced
# --------------------------------------------------------------------------- #
def test_ac13_decisions_resolve_is_user_sourced_and_resumes(
    run_cli,
    envelope_tools,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
):
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_implement(
        driver_corpus,
        host,
        decisions=[_pending_decision_fact(title="Irreversible question")],
    )
    monkeypatch.chdir(host)

    code, out, _err = resolve_decision(
        run_cli,
        "E1",
        resolution="Choose option A",
        routes_to=AUTO_TIER2_PLAN,
    )
    assert code == 0, f"FAIL AC-13: decisions resolve must be wired (exit {code})"
    after = driver_corpus.read_yaml(state_path)
    resolved = [d for d in after["decisions"] if d["id"] == "E1"][0]
    assert resolved.get("resolution_source") == "user", (
        "FAIL AC-13: a decisions resolve must record resolution_source=user (INV-4)"
    )

    # Resume (review): re-invoking `drive` after the resolution must get PAST the
    # resolved block — E1 stays resolved and the driver proceeds to spawn the next
    # phase session rather than immediately re-pausing on E1.
    run_cli(["drive", "--feature", SLUG, "--json"])
    resumed = driver_corpus.read_yaml(state_path)
    still = [d for d in resumed["decisions"] if d["id"] == "E1"][0]
    assert still.get("status") == "resolved", (
        "FAIL AC-13: a resolved decision must not be re-opened on resume"
    )
    assert fake_claude.calls(), (
        "FAIL AC-13: re-invoking drive after the resolution must resume the run "
        "(a phase session is spawned past the cleared block)"
    )


def test_ac13_decisions_add_validates_decision_batch_payload(
    run_cli,
    envelope_tools,
    driver_corpus,
    tmp_path,
    monkeypatch,
    auto_tier2_workspace,
):
    host = auto_tier2_workspace(chdir=False)
    state_path = driver_corpus.state(host, SLUG)
    payload_path = tmp_path / "batch.json"
    payload_path.write_text(json.dumps(_decision_batch_payload(8)), encoding="utf-8")
    monkeypatch.chdir(host)

    code, out, _err = run_cli(
        [
            "decisions",
            "add",
            "--feature",
            SLUG,
            "--input-json",
            str(payload_path),
            "--json",
        ]
    )
    envelope_tools.assert_shape(envelope_tools.parse(out))
    assert code == 0, f"FAIL AC-13: valid heddle.decision-batch/v1 must add, got {code}"
    state = driver_corpus.read_yaml(state_path)
    decision = state["decisions"][-1]
    assert decision["id"] == "D1" and decision["status"] == "pending", (
        "FAIL AC-13: decisions add must stamp id/status instead of trusting the "
        f"payload, got {decision!r}"
    )
    assert decision["class"] == 8 and decision["source"] == "session"
    assert decision["question"] and decision["options"] and decision["routes_to"]
    assert "context" not in decision, (
        "FAIL AC-13: decisions add must preserve only state-schema decision fields"
    )


def test_ac13_decisions_add_rejects_invalid_batch_class_without_writing(
    run_cli,
    envelope_tools,
    driver_corpus,
    tmp_path,
    monkeypatch,
    auto_tier2_workspace,
):
    host = auto_tier2_workspace(chdir=False)
    state_path = driver_corpus.state(host, SLUG)
    before = state_path.read_bytes()
    payload = _decision_batch_payload(4)
    payload_path = tmp_path / "bad-batch.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(host)

    code, out, _err = run_cli(
        [
            "decisions",
            "add",
            "--feature",
            SLUG,
            "--input-json",
            str(payload_path),
            "--json",
        ]
    )
    envelope = envelope_tools.parse(out)
    assert code == 2 and envelope["error"]["code"] == "usage", (
        "FAIL AC-10/AC-13: decisions add must reject classes outside "
        f"{{1,2,3,5,6,7,8}}, got exit {code} / {envelope!r}"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-13: invalid heddle.decision-batch/v1 payload must not write state"
    )


def test_ac13_decisions_add_returns_conflict_envelope(
    run_cli,
    envelope_tools,
    tmp_path,
    monkeypatch,
    auto_tier2_workspace,
):
    from heddle.runtime.write_path import Conflict

    host = auto_tier2_workspace(chdir=False)
    payload_path = tmp_path / "batch.json"
    payload_path.write_text(json.dumps(_decision_batch_payload(8)), encoding="utf-8")
    monkeypatch.chdir(host)

    def stale_commit(*_args, **_kwargs):
        raise Conflict("stale revision")

    monkeypatch.setattr("heddle.runtime.decisions.commit_state", stale_commit)
    code, out, _err = run_cli(
        [
            "decisions",
            "add",
            "--feature",
            SLUG,
            "--input-json",
            str(payload_path),
            "--json",
        ]
    )
    envelope = envelope_tools.parse(out)
    assert code == 5 and envelope["error"]["code"] == "conflict", (
        "FAIL AC-13: decisions add advertises CAS exit 5 and must return a "
        f"conflict envelope, got exit {code} / {envelope!r}"
    )


def test_ac13_decisions_resolve_returns_conflict_envelope(
    run_cli,
    envelope_tools,
    driver_corpus,
    monkeypatch,
    auto_tier2_workspace,
):
    from heddle.runtime.write_path import Conflict

    host = auto_tier2_workspace(chdir=False)
    _arm_implement(
        driver_corpus,
        host,
        decisions=[_pending_decision_fact()],
    )
    monkeypatch.chdir(host)

    def stale_commit(*_args, **_kwargs):
        raise Conflict("stale revision")

    monkeypatch.setattr("heddle.runtime.decisions.commit_state", stale_commit)
    code, out, _err = resolve_decision(
        run_cli,
        "E1",
        resolution="continue (user)",
        routes_to=AUTO_TIER2_PLAN,
    )
    envelope = envelope_tools.parse(out)
    assert code == 5 and envelope["error"]["code"] == "conflict", (
        "FAIL AC-13: decisions resolve advertises CAS exit 5 and must return a "
        f"conflict envelope, got exit {code} / {envelope!r}"
    )


def test_ac13_phase_session_cannot_resolve_its_own_escalation(
    run_cli, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    """
    The phase-session argv denies `decisions resolve` through the per-phase deny
        profile (review). Proven REACHABLE (review): the deny rule is not just present
        in ``uniform_deny_rules()`` but actually wired into the ``--disallowedTools``
        of a spawned phase session.
    """
    from heddle.driver import sessions
    from heddle.kernel.project_config import load_project_config

    host = auto_tier2_workspace(chdir=False)
    deny = set(sessions.uniform_deny_rules(load_project_config(host)))
    assert "Bash(heddle decisions resolve*)" in deny, (
        "FAIL AC-13: phase sessions must be denied `decisions resolve` (INV-4)"
    )

    # Reachability: spawn a real (faked) phase session and confirm the deny rule
    # rode through into the session's --disallowedTools (an unreachable deny rule
    # can never fire).
    _arm_implement(driver_corpus, host)
    monkeypatch.chdir(host)
    run_cli(["drive", "--feature", SLUG, "--until", "peer-review", "--json"])
    calls = fake_claude.calls()
    assert calls, "FAIL AC-13: the driver must spawn a phase session to deny"
    argv = calls[0]["argv"]
    deny_tokens: set[str] = set()
    i = 0
    while i < len(argv):
        if argv[i] == "--disallowedTools":
            j = i + 1
            while j < len(argv) and not argv[j].startswith("--"):
                deny_tokens.update(p.strip() for p in argv[j].split(",") if p.strip())
                j += 1
            i = j
        else:
            i += 1
    assert "Bash(heddle decisions resolve*)" in deny_tokens, (
        "FAIL AC-13: the `decisions resolve` deny rule must be reachable — wired "
        "into the spawned phase session's --disallowedTools, not merely declared"
    )


# --------------------------------------------------------------------------- #
# AC-14 — one seeded blocker per class pauses with a well-formed batch
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "seed_fixture_path",
    tuple(
        path
        for path in _seed_fixture_paths()
        if path.stem not in {"class-3", "class-4"}
    ),
    ids=lambda path: path.stem,
)
def test_ac14_seeded_blocker_per_class_pauses_and_resumes(
    run_cli,
    run_json,
    envelope_tools,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
    seed_fixture_path,
):
    """
    Each of the 8 escalation classes pauses `heddle drive` with a well-formed
        decision batch / blocking condition matching its registry mechanism and
        resumes after a user-sourced resolution. Retired class-3/class-4 mechanics
        are covered by the current typed review-assignment and disposition tests.

        preimplementation state: integration commits the final one-per-class blocker
        fixtures. Here each
        class is armed best-effort from the base; the test FAILs until the driver,
        the registry, and `decisions resolve` exist.

    """
    seed_fixture = _load_seed_fixture(seed_fixture_path)
    escalation_class = seed_fixture["class"]
    host = auto_tier2_workspace(chdir=False)
    stage, overrides = _overrides_from_seed(seed_fixture)
    state_path = _arm_at_stage(driver_corpus, host, stage, **overrides)
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 1, (
        f"FAIL AC-14 (class {escalation_class}): drive must pause blocked/escalated "
        f"(exit 1, the drive blocked/escalated code), got exit {code}"
    )
    # 1) The pause is well-formed: a decision batch (decision-raising classes) or a
    # blocking condition (caps 3/6/7) is surfaced.
    state = driver_corpus.read_yaml(state_path)
    pending = [d for d in state.get("decisions", []) if d.get("status") != "resolved"]
    surfaced = " ".join(na.get("reason", "") for na in envelope.get("next_actions", []))
    assert pending or any(
        token in surfaced
        for token in (*DERIVED_ESCALATION_BLOCKS.values(), "pending-decisions")
    ), (
        f"FAIL AC-14 (class {escalation_class}): the pause must surface a well-formed "
        "decision batch or the class's blocking condition"
    )

    # 2) Resolve (user-sourced) — every decision-raising class surfaces a pending
    # decision to resolve (the taxonomy maps caps 3/6/7 to a `decisions` batch).
    assert pending, (
        f"FAIL AC-14 (class {escalation_class}): a pending decision must exist to "
        "resolve (the class's batch)"
    )
    # review: a DRIVER-RAISED batch (classes 3/6/7) must be WELL-FORMED per
    # heddle.decision-batch/v1 (Feature Spec) — not just "some pending decision". Assert
    # the class identity + the required driver-template schema, so a generic
    # one-size pause cannot pass for the derived classes. (Classes 1/2/5/8 are
    # session-raised and 4 is gate-recorded; their batches are pre-seeded stand-ins
    # here, so only the driver-derived ones carry the driver's canned shape.)
    from heddle.driver import escalations

    if escalations.ESCALATION_REGISTRY[escalation_class] == "decisions-batch":
        batch = pending[0]
        assert batch.get("class") == escalation_class, (
            f"FAIL AC-14 (class {escalation_class}): a decision batch must carry "
            f"its class identity, got class={batch.get('class')!r}"
        )
        assert batch.get("source") in {"session", "driver"}, (
            f"FAIL AC-14 (class {escalation_class}): a decision batch's source "
            f"must be session or driver, got {batch.get('source')!r}"
        )
        assert batch.get("title"), (
            f"FAIL AC-14 (class {escalation_class}): a decision batch must carry a "
            "non-empty title"
        )
        assert batch.get("question"), (
            f"FAIL AC-14 (class {escalation_class}): a decision batch must carry a "
            "non-empty question (what needs a user ruling)"
        )
        options = batch.get("options")
        assert isinstance(options, list) and options, (
            f"FAIL AC-14 (class {escalation_class}): a decision batch must carry ≥1 "
            f"option, got {options!r}"
        )
        assert batch.get("routes_to"), (
            f"FAIL AC-14 (class {escalation_class}): a decision batch must carry a "
            "non-empty routes_to destination"
        )
    did = pending[0]["id"]
    rc, _o, _e = resolve_decision(
        run_cli,
        did,
        kind=(
            "continue-stage"
            if escalation_class == 3
            else "continue-session"
            if escalation_class in {6, 7}
            else "disposition"
        ),
        resolution="continue (user)",
        routes_to=AUTO_TIER2_PLAN,
    )
    assert rc == 0, (
        f"FAIL AC-14 (class {escalation_class}): a user-sourced resolution must "
        f"succeed (exit {rc})"
    )
    after_resolve = driver_corpus.read_yaml(state_path)
    resolved = [d for d in after_resolve["decisions"] if d["id"] == did][0]
    assert resolved.get("resolution_source") == "user", (
        f"FAIL AC-14 (class {escalation_class}): the resolution must be "
        "user-sourced (INV-4)"
    )
    if escalation_class in DERIVED_ESCALATION_BLOCKS:
        status_blocks = _status_blocks(run_cli, envelope_tools)
        assert DERIVED_ESCALATION_BLOCKS[escalation_class] not in status_blocks, (
            f"FAIL AC-14 (class {escalation_class}): immediately after the user "
            "resolution + continuation override, the status surface must clear "
            f"the {DERIVED_ESCALATION_BLOCKS[escalation_class]!r} block"
        )

    # 3) Resume — re-invoking drive must get PAST the resolved block (it must not
    # immediately re-trip). For the DERIVED blocks (class 3 review-cap, 6
    # no-progress, 7 attempt-cap) the resolution records a continuation override
    # (CP1-D) so the kernel-derived condition does not re-fire on resume.
    _rc, r_out, _re = run_cli(["drive", "--feature", SLUG, "--json"])
    r_env = envelope_tools.parse(r_out)
    resumed = driver_corpus.read_yaml(state_path)
    assert [d for d in resumed["decisions"] if d["id"] == did][0]["status"] == (
        "resolved"
    ), (
        f"FAIL AC-14 (class {escalation_class}): the resolved block must not "
        "re-open on resume"
    )
    if escalation_class in DERIVED_ESCALATION_BLOCKS:
        overrides_recorded = [
            d["resolution"]
            for d in resumed["decisions"]
            if d["id"] == did and d["status"] == "resolved"
        ]
        assert overrides_recorded[0]["kind"] == (
            "continue-stage" if escalation_class == 3 else "continue-session"
        ), (
            f"FAIL AC-14 (class {escalation_class}): resolving a derived "
            "cap/no-progress block must record a typed continuation resolution "
            "(CP1-D) so the derived block does not immediately re-trip on resume"
        )
        # review: prove the resume makes PROGRESS past the cleared block rather
        # than stalling on it again — the class's OWN derived condition must be
        # absent from the resumed surface (next_actions reasons + blocking
        # conditions). A run that records the override yet immediately re-raises
        # the same block would otherwise pass. (Asserts the block cleared, not a
        # stage advance — the no-op session-double cannot advance a stage, so this
        # stays green-reachable regardless of the review fidelity decision.)
        block_token = DERIVED_ESCALATION_BLOCKS[escalation_class]
        r_surface = (
            " ".join(na.get("reason", "") for na in r_env.get("next_actions", []))
            + " "
            + " ".join((r_env.get("data") or {}).get("blocking_conditions", []))
        )
        if escalation_class != 7:
            assert block_token not in r_surface, (
                f"FAIL AC-14 (class {escalation_class}): after the user resolution + "
                f"continuation override, the resumed run must progress past the "
                f"{block_token!r} block — it must not immediately re-trip the same "
                f"derived condition; got resumed surface {r_surface!r}"
            )


def test_ac14_retripped_driver_batch_uses_unique_decision_id(
    run_cli, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_implement(
        driver_corpus,
        host,
        sessions=[_session_fact("implement", advanced=False, idx=i) for i in range(2)],
    )
    monkeypatch.chdir(host)

    run_cli(["drive", "--feature", SLUG, "--json"])
    state = driver_corpus.read_yaml(state_path)
    first_id = next(
        d["id"] for d in state["decisions"] if d.get("status") != "resolved"
    )
    resolve_decision(
        run_cli,
        first_id,
        kind="continue-session",
        resolution="continue (user)",
        routes_to=AUTO_TIER2_PLAN,
    )

    state = driver_corpus.read_yaml(state_path)
    state["sessions"].extend(
        [_session_fact("implement", advanced=False, idx=i) for i in range(2, 4)]
    )
    driver_corpus.write_yaml(state_path, state)
    run_cli(["drive", "--feature", SLUG, "--json"])
    retripped = driver_corpus.read_yaml(state_path)
    pending = [d for d in retripped["decisions"] if d.get("status") != "resolved"]
    assert pending and pending[0]["id"] != first_id, (
        "FAIL AC-14: driver-raised decision batch IDs must not duplicate "
        f"existing resolved IDs, got first={first_id!r}, pending={pending!r}"
    )
    rc, _out, _err = resolve_decision(
        run_cli,
        pending[0]["id"],
        kind="continue-session",
        resolution="continue again (user)",
        routes_to=AUTO_TIER2_PLAN,
    )
    assert rc == 0, (
        "FAIL AC-14: a re-tripped driver-raised batch with a unique id must be "
        "resolvable by the user"
    )


def test_ac14_driver_batch_conflict_returns_structured_envelope(
    run_json, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    from heddle.runtime import decisions, state_store

    host = auto_tier2_workspace(chdir=False)
    _arm_implement(
        driver_corpus,
        host,
        sessions=[_session_fact("implement", advanced=False, idx=i) for i in range(2)],
    )
    monkeypatch.chdir(host)

    commit = state_store.commit_state

    def stale_commit(path, *, expect_revision, transform):
        commit(
            path,
            expect_revision=expect_revision,
            transform=lambda value: {**value, "commands": {"smoke_test": "true"}},
        )
        return commit(path, expect_revision=expect_revision, transform=transform)

    monkeypatch.setattr(decisions, "commit_state", stale_commit)
    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 5 and envelope["error"]["code"] == "conflict", (
        "FAIL AC-14: driver-raised batch writes must return a structured "
        f"conflict envelope, got exit {code} / {envelope!r}"
    )
    state = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    assert state["commands"]["smoke_test"] == "true" and not state["decisions"]


def test_ac14_attempt_cap_override_resets_validation_retry_budget(
    run_cli, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    # E1-2: the session-projecting state — see test_ac07's arming note.
    host = auto_tier2_workspace(
        chdir=False, mutate=make_first_milestone_current_and_taskless
    )
    state_path = _arm_implement(
        driver_corpus,
        host,
        sessions=[_session_fact("implement", advanced=True, idx=i) for i in range(5)],
        decisions=[
            resolved(
                decision(
                    "attempt-cap-implement",
                    kind="session-cap",
                    target_stage="implement",
                    target_blocker="attempt-cap",
                    **{"class": 7},
                ),
                "continue-session",
                session_count_before=5,
            )
        ],
    )
    monkeypatch.chdir(host)

    run_cli(["drive", "--feature", SLUG, "--json"])
    state = driver_corpus.read_yaml(state_path)
    pending = [d for d in state["decisions"] if d.get("status") != "resolved"]
    assert len(fake_claude.calls()) > 1 and any(d.get("class") == 7 for d in pending), (
        "FAIL AC-14: after an attempt-cap continuation override, validation "
        "retries must use the post-override attempt count instead of halting "
        "after one failed post-override session"
    )


# --------------------------------------------------------------------------- #
# AC-15 — notify command fed the envelope; fire-and-forget
# --------------------------------------------------------------------------- #
def test_ac15_notify_receives_envelope_on_pause(
    run_cli,
    envelope_tools,
    driver_corpus,
    notify_capture,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
):
    host = auto_tier2_workspace(chdir=False)
    # Configure the host notify command + seed a pending decision to force a pause.
    append_autopilot_notify_command(host, notify_capture.command)
    _arm_implement(
        driver_corpus,
        host,
        decisions=[_pending_decision_fact()],
    )
    monkeypatch.chdir(host)

    run_cli(["drive", "--feature", SLUG, "--json"])
    received = notify_capture.received()
    assert received, (
        "FAIL AC-15: the notify command must receive the result envelope on a pause"
    )
    # review: the piped payload must be the STANDARD JSON result envelope, not just
    # some text carrying the token — parse it and assert the five-wire-key shape, so
    # a non-envelope payload containing "[REVIEW]"/"review_count" cannot pass.
    try:
        piped = json.loads(received[-1])
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"FAIL AC-15: the notify payload must be a valid JSON result envelope, "
            f"got {received[-1]!r}"
        ) from exc
    envelope_tools.assert_shape(piped)
    # And the parsed envelope must carry the running review count (a `review_count`
    # field or the rendered `[REVIEW]` tag inside the envelope, not loose text).
    blob = json.dumps(piped)
    assert "[REVIEW]" in blob or "review_count" in blob, (
        "FAIL AC-15: the piped envelope must carry the running [REVIEW] count "
        f"(field or tag) within the envelope, got {piped!r}"
    )


def test_ac15_review_count_is_computed_from_existing_diagnostics():
    from heddle.contracts.result import (
        Diagnostic,
        ExitCode,
        HeddleError,
        HeddleResult,
        Severity,
    )
    from heddle.runtime.drive import _review_count

    result = HeddleResult.failure(
        HeddleError(
            code="pending-decisions",
            message="blocked",
            hint="resolve decisions",
        ),
        exit_code=ExitCode.INTERNAL,
        diagnostics=(
            Diagnostic(Severity.INFO, "review-found", "gate review emitted a note"),
            Diagnostic(Severity.INFO, "other", "[REVIEW] policy choice"),
            Diagnostic(Severity.INFO, "other", "unrelated"),
        ),
    )
    assert _review_count(result) == 2, (
        "FAIL AC-15: notify envelope must include the running [REVIEW] count, "
        "not a hard-coded zero"
    )


def test_ac15_absent_notify_key_is_quiet(
    run_json,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
):
    host = auto_tier2_workspace(chdir=False)  # no notify_command configured
    _arm_implement(
        driver_corpus,
        host,
        decisions=[_pending_decision_fact()],
    )
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    # An absent notify key produces no notification and no error: the run still
    # pauses cleanly on the seeded blocker (exit 1, blocked/escalated) — never an
    # internal error or crash from the missing key.
    assert code == 1, (
        f"FAIL AC-15: an absent notify key must still pause cleanly on the seeded "
        f"blocker (exit 1, no crash), got exit {code}"
    )
    assert (envelope.get("error") or {}).get("code") != "internal", (
        "FAIL AC-15: an absent notify key must never raise an internal error"
    )
    # review: tie the oracle to AC-15 behavior, not to the incidental USAGE≠1 red
    # signal. (a) The run paused for the RIGHT reason — the seeded E1 blocker is
    # still pending — and (b) the absent key is QUIET: no notify side effect /
    # diagnostic was emitted (nothing was attempted to dispatch).
    state = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    pending = [d for d in state.get("decisions", []) if d.get("status") != "resolved"]
    assert any(d.get("id") == "E1" for d in pending), (
        "FAIL AC-15: the run must pause on the seeded E1 blocker (the absent-notify "
        "pause must be for the right reason, not an incidental non-1 exit)"
    )
    diag_blob = json.dumps(envelope.get("diagnostics") or []).lower()
    assert "notify" not in diag_blob, (
        "FAIL AC-15: an absent notify key must be QUIET — no notify diagnostic or "
        f"attempted dispatch, got diagnostics {envelope.get('diagnostics')!r}"
    )


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("nonzero", id="non-zero-exit"),
        pytest.param("slow", id="slow-or-timeout"),
    ],
)
def test_ac15_failing_notify_is_fire_and_forget(
    run_json,
    driver_corpus,
    fake_claude,
    tmp_path,
    monkeypatch,
    auto_tier2_workspace,
    mode,
):
    """
    A failing notify command — non-zero exit, or one slow enough to exceed the
        driver's notify budget (`autopilot.notify_timeout_s`, default 10s) — logs a
        notify-specific diagnostic and does NOT change the run outcome: the run still
        pauses on the seeded blocker (review).

        The slow script sleeps past a reduced notify budget so it genuinely trips
        the driver's timeout path; it still self-terminates after that bounded sleep,
        so the test cannot hang even if the driver fails to enforce its own notify
        timeout (the real timeout bound is the driver's to implement; this asserts the
        outcome is unchanged either way).
    """
    bin_dir = tmp_path / "notifybin-fail"
    bin_dir.mkdir()
    script = bin_dir / "notify-fail"
    host = auto_tier2_workspace(chdir=False)
    if mode == "slow":
        set_autopilot_notify_timeout(host, 1)
    # The slow body sleeps past the configured notify budget to exercise the
    # timeout branch (not merely a slow-but-within-budget call).
    body = "exit 7\n" if mode == "nonzero" else "sleep 2\nexit 0\n"
    write_executable_script(script, "#!/bin/sh\n" + body, mode=0o755)

    append_autopilot_notify_command(host, str(script))
    _arm_implement(
        driver_corpus,
        host,
        decisions=[_pending_decision_fact()],
    )
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 1, (
        f"FAIL AC-15 ({mode}): a failing notify must not change the seeded pause "
        f"outcome (exit 1, the blocked/escalated code), got exit {code}"
    )
    assert (envelope.get("error") or {}).get("code") != "internal", (
        f"FAIL AC-15 ({mode}): a failing notify must never raise an internal error"
    )
    diagnostics = str(envelope.get("diagnostics") or "")
    assert "notify" in diagnostics.lower(), (
        f"FAIL AC-15 ({mode}): a failing notify must log a notify-specific "
        f"diagnostic (naming the notify command), got {envelope.get('diagnostics')!r}"
    )
    if mode == "slow":
        assert any(t in diagnostics.lower() for t in ("timeout", "timed out")), (
            "FAIL AC-15 (slow-or-timeout): a notify that exceeds the "
            "`autopilot.notify_timeout_s` budget must log a timeout diagnostic, "
            f"got {envelope.get('diagnostics')!r}"
        )


# --------------------------------------------------------------------------- #
# review accounting (review, pre-authoring hardening) — gate-cap-reached generalizes the
# class-3
# cap to every required gate at the current stage, counted over the
# error-marked run facts that failed engine runs now record.
# --------------------------------------------------------------------------- #
def _errored_gate_runs(gate: str, n: int, *, scope: str = "m1") -> list[dict]:
    """Build a gate fact with ``n`` error-marked review attempts."""
    return [
        {
            "gate": gate,
            "scope": scope,
            "runs": [
                {
                    "run_id": new_run_id(),
                    "report_findings": [],
                    "at": f"2026-07-03T09:{i:02d}Z",
                    "cli": "claude",
                    "artifact": "",
                    "input_hash": f"sha256:err{i}",
                    "verdict": {"status": "error"},
                    "findings": {
                        "by_severity": {"critical": 0, "important": 0, "minor": 0},
                        "by_classification": {
                            "implement": 0,
                            "report": 0,
                            "ignore": 0,
                            "unknown": 0,
                        },
                        "total": 0,
                        "contradictions": 0,
                    },
                }
                for i in range(n)
            ],
        }
    ]


# (flow, error-marked runs, expect gate-cap-reached). The (auto, 4, False) row
# brackets the cap boundary from the outside, mirroring AC-11's review-cap
# rows: hitl never fires the cap, and 4 runs are below it.
