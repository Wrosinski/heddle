"""
Integrated feature acceptance test (autopilot — Autopilot).

Command: `.venv/bin/python -m pytest -m acceptance tests/ -q`
Behavior contract: autopilot

This is the integrated, end-to-end acceptance: a clean Tier-2 `flow: auto`
fixture driven by `heddle drive` from brief -> completion-stage close through the
**session-double** (a scripted fake `claude`) and the deterministic gate runner.
Cross-module interfaces (kernel / runtime / driver) are REAL; the only mocks are
the two external CLIs (the phase-session `claude` and the gate CLI).

Success criteria (approved by the user at the test scaffolding checkpoint):
1. The fixture reaches completion-stage `complete` with zero human interventions.
2. AC-9 oracle: the shared artifact-path set + per-gate iteration counts equal
   the checked-in golden HITL baseline; auto-only artifacts (decision journal,
   friction retrospective, source: policy grants) are allowed additions;
   volatile fields (timestamps, session ids, durations, temp paths) excluded.
3. The session-raised escalation classes (1/2/5/8) pause with a well-formed
   batch and resume after a user-sourced resolution. The kernel-/driver-derived
   classes 3/6/7 (which need a recorded-fact precondition and the CP1-D
   continuation override) are owned by
   tests/driver/test_escalations.py::test_ac14_seeded_blocker_per_class_pauses_and_resumes,
   which arms each correctly — the acceptance test does not re-arm them.
4. AC-level granularity: every AC has >=1 assertion across the autopilot suite. The
   acceptance test owns the integrated/cross-cutting ACs (AC-9, plus AC-1/AC-16
   observed in the clean run and AC-13/AC-14 in the escalation cycle; AC-18 is
   presence-only here — the fact-backed audit oracle is owned by
   test_completion.py). Edge-case ACs are delegated to the milestone tests
   and recorded in the plan's AC Coverage Matrix:
     AC-2/AC-5/AC-6/AC-21/AC-22/AC-23/AC-24 -> test_driver_loop.py /
       test_authorization_seam.py
     AC-3/AC-4/AC-20 -> test_authorization_seam.py
     AC-8 -> test_boundaries_driver.py
     AC-10/AC-11/AC-12/AC-15 -> test_escalations.py
     AC-17/AC-19 -> test_completion.py

preimplementation state: `heddle drive` does not exist and the golden baseline is a
placeholder
until completion (T4) — the whole acceptance suite is red until completion lands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.driver.constants import (
    AUTO_TIER2_DECISION_JOURNAL,
    AUTO_TIER2_FRICTION_RETRO,
    AUTO_TIER2_PLAN,
)
from tests.driver.constants import AUTO_TIER2_SLUG as SLUG
from tests.driver.helpers import (
    assert_has_grant,
    assert_no_grant,
    initialize_fixture_git,
    resolve_decision,
)
from tests.runtime.write_path_helpers import authored_boundary_verifications

pytestmark = pytest.mark.acceptance

# The session-raised escalation classes (Feature Spec → taxonomy: 1/2/5/8 are
# raised by a phase session via `decisions add`). The acceptance test arms and
# exercises exactly these; the kernel-/driver-derived classes 3/6/7 — which need
# a recorded-fact precondition (non-converged runs / no-progress / session count)
# and the CP1-D continuation override — are owned by the integration per-class test,
# which arms each correctly (review).
SESSION_RAISED_CLASSES = (1, 2, 5, 8)
GOLDEN = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "golden"
    / "autopilot-demo.hitl-baseline.json"
)


def _shared_artifact_paths(host: Path) -> set[str]:
    """The repo-relative shared artifact-path set, volatile paths excluded:
    specs, plans, and gate-review artifacts a HITL run also produces."""
    paths: set[str] = set()
    for pattern in ("docs/features/**/*.md", "plans/**/*.md"):
        for path in host.glob(pattern):
            paths.add(str(path.relative_to(host)))
    return paths


def _gate_iteration_counts(state: dict) -> list[dict[str, int | str]]:
    return sorted(
        (
            {
                "gate": gate["gate"],
                "scope": gate["scope"],
                "run_count": len(gate.get("runs", [])),
            }
            for gate in state.get("gates", [])
        ),
        key=lambda item: (str(item["gate"]), str(item["scope"])),
    )


def _disable_reviews_for_driver_oracle(state_path, driver_corpus) -> None:
    """Keep this driver oracle on the explicitly all-off policy path."""
    state = driver_corpus.read_yaml(state_path)
    for entry in state["feature_policy"]["entries"]:
        entry.update(mode="off", limit=None, minimum_rounds=0)
    driver_corpus.write_yaml(state_path, state)
    (state_path.parent / "brief.md").write_text(
        "# Brief\n\nExercise the unattended driver path.\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# AC-9 — clean auto run end-to-end vs the checked-in golden HITL baseline
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_ac09_clean_auto_run_matches_golden_hitl_baseline(
    run_cli,
    run_json,
    envelope_tools,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
):
    """AC-9 uses the content-aware session-double mode: it advances the demo
    implement milestone state and seeds converged gate facts while the real
    kernel/runtime/driver paths perform the loop, writes, and close validation."""
    host = auto_tier2_workspace(chdir=False)  # principles ratified, no blockers
    state_path = driver_corpus.state(host, SLUG)
    # Reviews are explicitly Off in this driver-loop oracle. Native review
    # journeys are covered by the installed and concurrency acceptance lanes.
    _disable_reviews_for_driver_oracle(state_path, driver_corpus)
    authored_boundary_verifications(state_path)
    initialize_fixture_git(host)
    fake_claude.enable_autopilot_completion()
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    # completion (kickoff-guidance-integration core): `complete` is the all-tier HITL
    # completion — the clean unattended run ends AT the pending handoff.
    assert code == 1 and envelope["error"]["code"] == "awaiting-human-completion", (
        f"FAIL AC-9: a clean auto run must drive to the completion stage handoff "
        f"(completion: complete is HITL), got exit {code} with envelope {envelope!r}"
    )
    state = driver_corpus.read_yaml(state_path)
    assert state["stage"] == "complete", (
        "FAIL AC-9: the clean run must reach `complete` with zero interventions"
    )

    # review: "clean auto run" is defined by the ABSENCE of human touchpoints, not
    # only a positive policy-grant signal. No grant may be user-sourced, no
    # decision user-resolved, none left unresolved (the status/blocking check below
    # also asserts no awaiting-human-authorization was surfaced).
    grants = state.get("authorizations", [])
    assert_no_grant(
        grants,
        source="user",
        message="FAIL AC-9: a clean auto run must record NO source: user grants",
    )
    decisions = state.get("decisions", [])
    assert not any(d.get("resolution_source") == "user" for d in decisions), (
        "FAIL AC-9: a clean auto run must have NO user-sourced decision resolutions"
    )
    assert not any(d.get("status") != "resolved" for d in decisions), (
        "FAIL AC-9: a clean auto run must leave NO unresolved decisions"
    )

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    # review/R5: the golden baseline must carry first-class, asserted provenance
    # (not just a free-text note). It records how the baseline was produced and
    # how to regenerate it, so the "produced once from a real HITL run"
    # requirement is explicit and checkable rather than implicit.
    provenance = golden.get("_provenance")
    assert isinstance(provenance, dict), (
        "FAIL AC-9: the golden HITL baseline must carry a first-class "
        "_provenance block (SR-I1/R5)"
    )
    for field in ("method", "captured_at", "source", "attestation", "regeneration"):
        assert provenance.get(field), (
            f"FAIL AC-9: the golden baseline _provenance must record {field!r}"
        )
    # And it must never be the scaffold placeholder.
    assert "PLACEHOLDER" not in json.dumps(golden), (
        "FAIL AC-9: the golden HITL baseline must not be the scaffold placeholder"
    )
    actual_paths = _shared_artifact_paths(host)
    golden_paths = set(golden["artifact_paths"])
    missing = golden_paths - actual_paths
    assert not missing, (
        f"FAIL AC-9: the auto run must reproduce the golden HITL artifact-path set "
        f"(auto-only additions allowed); missing {sorted(missing)}"
    )
    # review: the artifact oracle is two-directional — beyond the golden HITL set,
    # the ONLY allowed extra shared artifact is the driver-authored decision
    # journal. A driver that emits stray spec/plan/gate artifacts above the
    # HITL baseline must fail here, not slip past a subset-only check.
    auto_only = {AUTO_TIER2_DECISION_JOURNAL}
    unexpected = actual_paths - golden_paths - auto_only
    assert not unexpected, (
        "FAIL AC-9: the auto run must not produce shared artifacts beyond the golden "
        f"HITL set + the auto-only allowlist; unexpected {sorted(unexpected)}"
    )
    assert _gate_iteration_counts(state) == golden["gate_iteration_counts"], (
        "FAIL AC-9: per-gate iteration counts must equal the golden HITL baseline"
    )

    # With every review role explicitly Off, the driver has no policy decision
    # to journal before the human completion handoff.
    # completion: the friction retrospective is a completion artifact the HITL close
    # actor authors — the unattended run must NOT have produced it (robustness
    # sessions carry no completion writer).
    assert not (host / AUTO_TIER2_DECISION_JOURNAL).exists(), (
        "FAIL AC-9/AC-18: an all-off review path must not fabricate a decision journal"
    )
    assert not (host / AUTO_TIER2_FRICTION_RETRO).exists(), (
        "FAIL AC-9/completion: the friction retrospective is HITL completion stage "
        "work — an "
        "unattended run ending at the handoff must not have authored it"
    )
    assert_has_grant(
        state.get("authorizations", []),
        source="policy",
        message="FAIL AC-9/AC-16: the auto run must record source: policy grants",
    )
    # AC-1 integrated: flow is surfaced throughout. review: and the clean run must
    # never have surfaced a human-authorization block.
    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    status_data = envelope_tools.parse(out)["data"]
    assert status_data.get("flow") == "auto", (
        "FAIL AC-9/AC-1: flow=auto must be surfaced for the driven feature"
    )
    assert "awaiting-human-authorization" not in status_data.get(
        "blocking_conditions", []
    ), "FAIL AC-9: a clean auto run must never surface awaiting-human-authorization"


# --------------------------------------------------------------------------- #
# AC-14 (integrated) — each escalation class pauses, resolves user-sourced, resumes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("escalation_class", SESSION_RAISED_CLASSES)
def test_ac14_escalation_class_pauses_resolves_resumes(
    run_cli,
    envelope_tools,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
    escalation_class,
):
    host = auto_tier2_workspace(chdir=False)
    did = f"E{escalation_class}"

    def mutate(state: dict) -> None:
        state["stage"] = "implement"
        state["authorized_through"] = "implement"
        # Every parametrized class is session-raised, so it is armed unconditionally
        # (no silent pass-through for an un-armed class — review).
        state["decisions"] = [
            {
                "id": did,
                "kind": "question",
                "class": escalation_class,
                "source": "session",
                "resolution": None,
                "rationale": None,
                "resolution_source": None,
                "resolved_at": None,
                "title": f"class {escalation_class} escalation",
                "status": "pending",
                "routes_to": [AUTO_TIER2_PLAN],
                "created_at": "2026-06-16T09:00Z",
            }
        ]

    state_path = driver_corpus.mutate(host, SLUG, mutate)
    monkeypatch.chdir(host)

    # 1) pause
    code, out, _err = run_cli(["drive", "--feature", SLUG, "--json"])
    assert code == 1, (
        f"FAIL AC-14 (class {escalation_class}): drive must pause blocked/escalated "
        f"(exit 1), got {code}; stdout={out!r}"
    )

    # 2) resolve (user-sourced) — find the pending decision id from state.
    after = driver_corpus.read_yaml(state_path)
    pending = [d for d in after.get("decisions", []) if d.get("status") != "resolved"]
    assert pending, (
        f"FAIL AC-14 (class {escalation_class}): a pending decision batch must exist "
        "to resolve"
    )
    code, _out, _err = resolve_decision(
        run_cli,
        pending[0]["id"],
        resolution="continue (user)",
        routes_to=AUTO_TIER2_PLAN,
    )
    assert code == 0, (
        f"FAIL AC-14 (class {escalation_class}): a user-sourced resolution must succeed"
    )

    # 3) resume — re-invoking drive must get PAST the resolved block: the decision
    # stays resolved and the driver proceeds to spawn the next phase session
    # rather than immediately re-pausing on the same block (rejects the
    # `code in (0, 1)` false-green — review).
    spawns_before = len(fake_claude.calls())
    run_cli(["drive", "--feature", SLUG, "--json"])
    resumed = driver_corpus.read_yaml(state_path)
    assert [d for d in resumed["decisions"] if d["id"] == did][0]["status"] == (
        "resolved"
    ), (
        f"FAIL AC-14 (class {escalation_class}): the resolved decision must not "
        "re-open on resume"
    )
    assert len(fake_claude.calls()) > spawns_before, (
        f"FAIL AC-14 (class {escalation_class}): re-invoking drive after the "
        "resolution must resume the run (a phase session is spawned past the "
        "cleared block), not re-pause on the same decision"
    )


# --------------------------------------------------------------------------- #
# completion (kickoff-guidance-integration) — auto acceptance stops at the handoff
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_completion_auto_run_stops_at_handoff_and_hitl_fact_makes_terminal(
    run_cli,
    run_json,
    envelope_tools,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
    tmp_path,
):
    """RED discriminator for AC-7/AC-15 (acceptance half): under completion a clean
    auto run ends at the notifying `awaiting-human-completion` handoff —
    reaching `complete` is no longer zero-human success (configuration). Terminality
    requires the qualifying HITL `session log` fact (which runs the close
    audits + configured suite and stamps the lifecycle); only then does a
    re-driven `drive` return the terminal `complete` success.
    Behavior contract: kickoff-guidance-integration."""

    host = auto_tier2_workspace(chdir=False)
    state_path = driver_corpus.state(host, SLUG)
    # Reviews are explicitly Off in this driver-loop oracle. Native review
    # journeys are covered by the installed and concurrency acceptance lanes.
    _disable_reviews_for_driver_oracle(state_path, driver_corpus)
    authored_boundary_verifications(state_path)
    initialize_fixture_git(host)
    fake_claude.enable_autopilot_completion()
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 1 and envelope["error"]["code"] == "awaiting-human-completion", (
        "FAIL AC-7: the clean auto run must stop at the completion stage handoff "
        f"(exit 1, awaiting-human-completion), got exit {code} with "
        f"{envelope.get('error') or envelope.get('data')!r}"
    )
    state = driver_corpus.read_yaml(state_path)
    assert state["stage"] == "complete", (
        "FAIL AC-7: auto still drives the boundary INTO complete before handing off"
    )
    spec_path = host / state["spec"]
    assert "lifecycle: active" in spec_path.read_text(encoding="utf-8"), (
        "FAIL AC-7/AC-10: entering complete must leave the lifecycle active "
        "until the qualifying fact is accepted"
    )

    # The HITL completion close: audits present, then the qualifying fact.
    retro = host / "plans" / f"{SLUG}.friction-retrospective.md"
    journal = host / "plans" / f"{SLUG}.decision-journal.md"
    retro.write_text(
        "# Friction Retrospective\n\n## Tooling fights\n\n## Prompt gaps\n\n"
        "## Validator false-positives\n\n## Escalations that should have "
        "been policy-resolvable\n",
        encoding="utf-8",
    )
    if not journal.exists():
        journal.write_text("# Decision Journal\n", encoding="utf-8")
    code, out, _err = run_cli(["feature", "complete", "--feature", SLUG, "--json"])
    close_envelope = envelope_tools.parse(out)
    assert code == 0 and close_envelope["data"]["accepted"], (
        "FAIL AC-8/AC-9: the qualifying HITL fact must be accepted (policy "
        "close grant + the fixture's configured suite), got exit "
        f"{code}: {close_envelope.get('error')}"
    )
    assert "lifecycle: complete" in spec_path.read_text(encoding="utf-8"), (
        "FAIL AC-10: acceptance stamps the permanent Feature Spec"
    )

    from tests.content_identity_helpers import git

    git(host, "add", "-f", "plans", "docs")
    git(host, "commit", "-qm", "retain accepted auto-flow fixture")
    code, out, _err = run_cli(["feature", "complete", "--feature", SLUG, "--json"])
    assert code == 0 and envelope_tools.parse(out)["data"]["accepted"], out
    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 0 and envelope["data"]["status"] == "complete", (
        "FAIL AC-7/REQ-9: with terminal evidence drive must return the "
        f"terminal complete success, got exit {code}"
    )
