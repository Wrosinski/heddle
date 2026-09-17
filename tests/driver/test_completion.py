"""
completion — auto-flow close + flow switching (autopilot — Autopilot).

Covers: AC-8 flow-set half (`flow set` on the surface, EXPECTED_SURFACE
lockstep), AC-17 (completion auto-flow close validation), AC-18 (decision journal
accounts for every resolved question — fact-backed oracle), AC-19 (flow
switching at boundaries only, recorded as a flow_changes[] fact). AC-9 (full
clean run) is in test_acceptance_autopilot.py.
Behavior contract: autopilot

preimplementation state: the auto-flow `complete`-boundary validation, the
`resolution_source: policy` recording path + decision-journal authoring, and the
`heddle flow set` command do not exist until completion lands. The friction
retrospective / decision journal paths follow the Feature Spec's dotted-sibling
form (`plans/<slug>.{friction-retrospective,decision-journal}.md`); completion
reconciles them with the resolved host layout.
"""

from __future__ import annotations

import json

import pytest

from heddle.contracts.schemas import (
    FLOW_CHANGE_APPLIED,
    FLOW_CHANGE_PENDING,
    FLOW_CHANGE_SUPERSEDED,
)
from tests.driver.constants import (
    AUTO_TIER2_DECISION_JOURNAL,
    AUTO_TIER2_FRICTION_RETRO,
    AUTO_TIER2_PLAN,
)
from tests.driver.constants import AUTO_TIER2_SLUG as SLUG
from tests.driver.helpers import (
    append_autopilot_notify_command,
    assert_has_grant,
    assert_no_grant,
    make_first_milestone_current_and_taskless,
    set_autopilot_config,
)
from tests.driver.policy_resolution_fixture import (
    POLICY_RESOLUTION_FIXTURE,
    policy_resolution_payload,
    render_policy_journal,
)


def _policy_decision_fact(
    *, resolution: str = "Chose option A (most reversible)"
) -> dict[str, object]:
    fixture = POLICY_RESOLUTION_FIXTURE
    return {
        "id": str(fixture["id"]),
        "kind": "policy",
        "source": "policy",
        "title": str(fixture["title"]),
        "question": str(fixture["question"]),
        "options": list(fixture["options"]),
        "status": "resolved",
        "resolution": {
            "kind": "policy",
            **{
                key: fixture[key]
                for key in (
                    "choice",
                    "basis",
                    "reversibility",
                    "confidence",
                    "review_flag",
                    "scope",
                )
            },
        },
        "rationale": resolution,
        "resolution_source": "policy",
        "routes_to": [AUTO_TIER2_PLAN],
        "created_at": "2026-06-16T09:00Z",
        "resolved_at": "2026-06-16T09:05Z",
    }


def _arm_complete_boundary(driver_corpus, host):
    """All milestones done AND verified at the robustness->complete boundary
    (flow auto) — a faithful model of a real boundary, where the auto run has
    recorded a verify fact per milestone (R2: verification-missing is enforced
    at auto-complete, so an unverified boundary would withhold the grant)."""

    def mutate(state: dict) -> None:
        state["stage"] = "robustness"
        state["authorized_through"] = "robustness"
        for milestone in state["milestones"]:
            milestone["status"] = "done"
            for task in milestone.get("tasks", []):
                task["status"] = "done"
        state["milestones"][0]["owns"].append(".heddle.yaml")
        state["authorizations"] = [
            {"through": "robustness", "source": "policy", "at": "2026-06-16T09:00Z"}
        ]

    state_path = driver_corpus.mutate(host, SLUG, mutate)
    from tests.runtime.write_path_helpers import authored_boundary_verifications

    authored_boundary_verifications(state_path)
    from tests.driver.helpers import initialize_fixture_git

    initialize_fixture_git(host)
    return state_path


def _seed_policy_fact(
    driver_corpus, host, *, resolution: str = "Chose option A (most reversible)"
):
    return driver_corpus.arm(
        host,
        SLUG,
        decisions=[_policy_decision_fact(resolution=resolution)],
    )


def _seed_milestone_verifications(driver_corpus, host) -> None:
    from tests.runtime.write_path_helpers import authored_milestone_verification

    state_path = driver_corpus.state(host, SLUG)
    state = driver_corpus.read_yaml(state_path)
    state["verifications"] = [
        authored_milestone_verification(state_path, milestone)
        for milestone in state["milestones"]
    ]
    driver_corpus.write_yaml(state_path, state)


def _well_formed_journal(*, choice: str = "A", review_flag: str = "[REVIEW]") -> str:
    return render_policy_journal(choice=choice, review_flag=review_flag)


# completion (kickoff-guidance-integration, reader): close validation moved from the
# robustness -> complete phase-exit to qualifying completion `session log`
# acceptance (review/REQ-11/REQ-14). The historical close tests below drive
# the same validators through the new owner.


def _enter_complete(run_cli, driver_corpus, state_path):
    """REQ-14: the (auto) robustness -> complete advance is artifact-free —
    it only advances and records the policy grant."""
    code, out, _err = run_cli(["phase-exit", "--feature", SLUG, "--json"])
    assert code == 0, (
        "FAIL REQ-14: the artifact-free entry into complete must succeed, "
        f"got exit {code}: {out}"
    )
    after = driver_corpus.read_yaml(state_path)
    assert after["stage"] == "complete", (
        "FAIL REQ-14: phase-exit must advance the stage to complete"
    )
    return after


def _submit_final(run_cli, payload_dir):
    return run_cli(["feature", "complete", "--feature", SLUG, "--json"])


def _terminal_fact_count(driver_corpus, state_path) -> int:
    return int(driver_corpus.read_yaml(state_path)["completion"] is not None)


def _spec_lifecycle(host) -> str | None:
    spec = host / "docs" / "features" / "example" / f"{SLUG}.md"
    for line in spec.read_text(encoding="utf-8").splitlines():
        if line.startswith("lifecycle:"):
            return line.split(":", 1)[1].strip()
    return None


# --------------------------------------------------------------------------- #
# AC-8 (flow set half) — flow set on the command surface
# --------------------------------------------------------------------------- #
def test_ac08_flow_set_on_command_surface():
    """
    AC-8: ``flow set`` joins COMMAND_SURFACE after ``drive`` with its full
    manifest-visible payload (mutating, no dry-run, the <hitl|auto> mode
    positional, and the --feature/--expect-revision/--json flags).
    """
    from heddle.runtime.contracts import build_manifest

    by_name = {entry["name"]: entry for entry in build_manifest()["commands"]}
    names = list(by_name)
    assert "flow set" in names, (
        "FAIL AC-8: 'flow set' must join COMMAND_SURFACE with "
        "EXPECTED_SURFACE updated in lockstep (manifest lockstep)"
    )
    # min-f: drive lands at foundation and flow set at completion, so by the time this
    # test
    # runs both are on the surface — assert the ordering unconditionally rather
    # than skipping it when drive happens to be absent.
    assert "drive" in names, "FAIL AC-8: 'drive' must be on the command surface"
    assert names.index("drive") < names.index("flow set"), (
        "FAIL AC-8: 'drive' must precede 'flow set' on the ordered surface"
    )
    entry = by_name["flow set"]
    assert entry["mutating"] is True and entry["dry_run"] is False, (
        "FAIL AC-8: 'flow set' must be mutating with no --dry-run"
    )
    assert {f["name"] for f in entry["flags"]} == {
        "--expect-revision",
        "--feature",
        "--json",
    }, "FAIL AC-8: 'flow set' must expose the current guarded write flags"
    assert len([a for a in entry["args"] if a.get("required")]) == 1, (
        "FAIL AC-8: 'flow set' must take exactly one required positional (the "
        "<hitl|auto> mode)"
    )


# --------------------------------------------------------------------------- #
# AC-17 — completion auto-flow close validation
# --------------------------------------------------------------------------- #


def test_ac17_retro_missing_heading_withholds_final_close(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    review: the completion-stage friction retrospective must record all four categories;
        a retro that is present and non-empty but MISSING a required heading must
        withhold final close acceptance. An empty-file-only check would wrongly
        accept here (the journal audit is satisfied by a well-formed entry).
    """
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_complete_boundary(driver_corpus, host)
    _seed_policy_fact(
        driver_corpus, host, resolution="Chose option A (most reversible)"
    )
    # Non-empty retro that omits the "Prompt gaps" category heading.
    (host / AUTO_TIER2_FRICTION_RETRO).write_text(
        "# Friction Retrospective\n\n## Tooling fights\n\n"
        "## Validator false-positives\n\n## Escalations that should have been "
        "policy-resolvable\n",
        encoding="utf-8",
    )
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        _well_formed_journal(), encoding="utf-8"
    )
    monkeypatch.chdir(host)
    _enter_complete(run_cli, driver_corpus, state_path)

    code, out, _err = _submit_final(run_cli, host)
    envelope = envelope_tools.parse(out)
    assert code != 0 and not envelope["ok"], (
        "FAIL retrospective: a retro missing a required heading must withhold final "
        "close acceptance"
    )
    assert _terminal_fact_count(driver_corpus, state_path) == 0, (
        "FAIL retrospective: no terminal fact may append while the friction "
        "retrospective is non-conformant"
    )
    assert _spec_lifecycle(host) != "complete", (
        "FAIL retrospective: no lifecycle stamp while the friction retrospective is "
        "non-conformant"
    )


# --------------------------------------------------------------------------- #
# AC-7 (P9 validator) — the complete-boundary close validator rejects a non-green
# clean-venv test suite (the third per-validator example; foundation owns P2/P6)
# --------------------------------------------------------------------------- #
def _set_autopilot_test_command(host, command: str) -> None:
    """Point the host's auto-flow close validator at ``command`` (the clean-venv
    suite the P9 validator runs at the complete boundary)."""
    set_autopilot_config(host, test_command=command)
    # The test changes owned configuration; refresh its constructed evidence.
    from tests.runtime.write_path_helpers import authored_boundary_verifications

    authored_boundary_verifications(host / "plans" / SLUG / "state.yaml")


def test_ac21_auto_close_requires_verification_fact(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """R2 / AC-21: verification-missing is NOT carved out at auto-complete. A
    done-but-unverified milestone withholds the terminal grant even with retro +
    journal present and NO configured test_command — closing the hole where an
    empty test_command let a grant slip past missing verification."""
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_complete_boundary(driver_corpus, host)
    _seed_policy_fact(
        driver_corpus, host, resolution="Chose option A (most reversible)"
    )

    # Strip the verify facts the boundary seeded: done milestones, no verification.
    driver_corpus.mutate(host, SLUG, lambda state: state.update(verifications=[]))

    (host / AUTO_TIER2_FRICTION_RETRO).write_text(
        "# Friction Retrospective\n\n## Tooling fights\n\n## Prompt gaps\n\n"
        "## Validator false-positives\n\n## Escalations that should have been "
        "policy-resolvable\n",
        encoding="utf-8",
    )
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        _well_formed_journal(), encoding="utf-8"
    )
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["phase-exit", "--feature", SLUG, "--json"])
    envelope = envelope_tools.parse(out)
    assert code != 0 and not envelope["ok"], (
        "FAIL R2/AC-21: an unverified done milestone must withhold the terminal "
        "grant at auto-complete (verification-missing enforced, not carved out)"
    )
    after = driver_corpus.read_yaml(state_path)
    assert after["stage"] == "robustness"
    assert_no_grant(
        after.get("authorizations", []),
        through="complete",
        message="FAIL R2: no terminal advance while verification-missing fires",
    )
    assert "verification" in json.dumps(envelope).lower(), (
        f"FAIL R2: the withhold must cite the missing verification, got {envelope!r}"
    )


# --------------------------------------------------------------------------- #
# AC-18 — decision journal accounts for every resolved question (fact-backed)
# --------------------------------------------------------------------------- #
def test_ac18_journal_entry_per_policy_resolution_fact(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    # A completed auto run that resolved one question by policy: a recorded
    # resolution_source: policy decision fact (CP1-A — the dedicated driver path,
    # not `decisions resolve`).
    _seed_policy_fact(driver_corpus, host)
    monkeypatch.chdir(host)

    journal = host / AUTO_TIER2_DECISION_JOURNAL
    journal.write_text(_well_formed_journal(), encoding="utf-8")

    # The audit's completeness oracle: every resolution_source: policy fact has a
    # matching, well-formed journal entry. A `heddle status`/audit surface should
    # report the audit complete (red until the oracle + journal authoring land).
    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-18: status must resolve (exit {code})"
    data = envelope_tools.parse(out)["data"]
    audit = data.get("decision_journal_audit")
    assert audit == "complete", (
        "FAIL AC-18: every resolution_source: policy fact must match a journal "
        f"entry (fact-backed audit), got {audit!r}"
    )


def test_ac15_terminal_complete_fires_notify_with_review_count(
    run_json,
    envelope_tools,
    driver_corpus,
    fake_claude,
    notify_capture,
    monkeypatch,
    auto_tier2_workspace,
):
    """
    review: the terminal ``complete`` event fires the configured
        notify command (not just pauses/hard halts), and the success envelope
        carries the running tally on the canonical ``data.review_count`` path.

        completion (kickoff-guidance-integration core): terminal complete is now
        fact-backed — a drive cannot mint the qualifying completion fact, so the
        fixture arms it directly and the drive resolves the terminal success.
    """
    host = auto_tier2_workspace(chdir=False)
    _completion_arm_complete_stage(driver_corpus, host, terminal=True)
    append_autopilot_notify_command(host, notify_capture.command)
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 0 and envelope["ok"], (
        "FAIL terminal completion: the drive must reach terminal complete, "
        f"got {envelope!r}"
    )
    assert "review_count" in envelope["data"], (
        "FAIL terminal envelope: the terminal success envelope must carry the running "
        f"[REVIEW] tally on data.review_count, got {envelope['data']!r}"
    )
    received = notify_capture.received()
    assert received, (
        "FAIL terminal completion: the terminal complete event must fire the "
        "configured "
        "notify command"
    )
    piped = json.loads(received[-1])
    envelope_tools.assert_shape(piped)
    assert piped["ok"] is True and piped["data"].get("status") == "complete", (
        "FAIL terminal completion: the notify payload for the terminal event must be "
        "the "
        f"complete success envelope, got {piped!r}"
    )


def test_ac12_auto_driver_with_configured_suite_reaches_completion_handoff(
    run_json,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
    tmp_path,
):
    """AC-12 survivor: configured close proof permits, but does not pre-run,
    the automatic robustness-to-complete handoff before final acceptance."""
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_complete_boundary(driver_corpus, host)
    attempted = tmp_path / "close-suite-ran"
    set_autopilot_config(
        host,
        test_command=(
            'python3 -c "from pathlib import Path; '
            f"Path({str(attempted)!r}).write_text('ran')\""
        ),
    )
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(
        ["drive", "--feature", SLUG, "--until", "complete", "--json"]
    )

    assert code == 0 and envelope["ok"], envelope
    after = driver_corpus.read_yaml(state_path)
    assert after["stage"] == "complete"
    assert after["completion"] is None
    assert not attempted.exists(), (
        "FAIL AC-12: driver ran the configured final close suite before acceptance"
    )


def test_ac18_policy_fact_without_journal_entry_is_incomplete(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    The negative half of the completeness oracle (review): a
        resolution_source: policy fact with NO matching journal entry must make the
        audit report NOT complete. Without this, an implementation that returns
        `complete` unconditionally — ignoring journal contents — would pass the
        positive-only test above.
    """
    host = auto_tier2_workspace(chdir=False)
    _seed_policy_fact(
        driver_corpus, host, resolution="Chose option A (most reversible)"
    )
    # Journal present but EMPTY — no entry for P1 (the missing-entry defect).
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        "# Decision Journal\n", encoding="utf-8"
    )
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-18: status must resolve (exit {code})"
    audit = envelope_tools.parse(out)["data"].get("decision_journal_audit")
    assert audit is not None and audit != "complete", (
        "FAIL AC-18: a resolution_source: policy fact with no journal entry must "
        f"make the audit report NOT complete (e.g. 'incomplete'), got {audit!r}"
    )


# A malformed entry (review): MATCHES the decision ID but OMITS the required
# principle / reversibility / confidence fields (AC-18 requires entries "citing
# the principle(s), reversibility, and confidence"). An ID-only match must not
# count as a well-formed entry.
_MALFORMED_JOURNAL = (
    "# Decision Journal\n\n"
    "## P1 — Reversible naming choice\n"
    "- note: chose A\n"  # no principle / reversibility / confidence fields
)


def test_ac18_malformed_journal_entry_is_incomplete(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    review: a journal entry that matches the decision ID but OMITS the required
        principle/reversibility/confidence fields must make the audit NOT complete — an
        ID-only match is not a well-formed entry (AC-18). Without this, an audit that
        matches on ID alone passes with a degenerate entry.
    """
    host = auto_tier2_workspace(chdir=False)
    _seed_policy_fact(driver_corpus, host)
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        _MALFORMED_JOURNAL, encoding="utf-8"
    )
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-18: status must resolve (exit {code})"
    audit = envelope_tools.parse(out)["data"].get("decision_journal_audit")
    assert audit is not None and audit != "complete", (
        "FAIL AC-18: a journal entry missing the required principle/reversibility/"
        f"confidence fields must make the audit NOT complete, got {audit!r}"
    )


@pytest.mark.parametrize(
    "omit",
    [
        "record_kind",
        "title",
        "question",
        "options",
        "choice",
        "rationale",
        "basis",
        "reversibility",
        "confidence",
        "scope",
        "routes_to",
    ],
)
def test_ac18_journal_entry_missing_one_required_field_is_incomplete(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace, omit
):
    """
    review: the audit's completeness oracle requires every journal field
        except the optional review_flag. Dropping any one required field from an
        ID-matching entry must make the audit NOT complete — a stronger probe than
        the all-fields-missing malformed case above.
    """
    host = auto_tier2_workspace(chdir=False)
    _seed_policy_fact(
        driver_corpus, host, resolution="Chose option A (most reversible)"
    )
    journal_path = host / AUTO_TIER2_DECISION_JOURNAL
    journal_path.write_text(_well_formed_journal(), encoding="utf-8")
    monkeypatch.chdir(host)
    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0
    assert envelope_tools.parse(out)["data"]["decision_journal_audit"] == "complete"
    journal = (
        "\n".join(
            line
            for line in _well_formed_journal().splitlines()
            if not line.startswith(f"- {omit}:")
        )
        + "\n"
    )
    journal_path.write_text(journal, encoding="utf-8")

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0
    audit = envelope_tools.parse(out)["data"].get("decision_journal_audit")
    assert audit is not None and audit != "complete", (
        f"FAIL journal: a journal entry missing the required {omit!r} field must "
        f"make the audit NOT complete, got {audit!r}"
    )


def test_ac18_audit_requires_every_policy_fact_to_have_an_entry(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    review: the audit's 'every fact' quantifier — with two policy facts but a
        journal entry for only one, the audit must report NOT complete.
    """
    host = auto_tier2_workspace(chdir=False)
    second = dict(_policy_decision_fact(resolution="Chose option A (most reversible)"))
    second["id"] = "P2"
    second["title"] = "Second reversible choice"
    driver_corpus.arm(
        host,
        SLUG,
        decisions=[
            _policy_decision_fact(resolution="Chose option A (most reversible)"),
            second,
        ],
    )
    # Journal carries an entry for P1 only.
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        _well_formed_journal(), encoding="utf-8"
    )
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0
    audit = envelope_tools.parse(out)["data"].get("decision_journal_audit")
    assert audit is not None and audit != "complete", (
        "FAIL journal: a journal missing an entry for one of several policy facts "
        f"must make the audit NOT complete, got {audit!r}"
    )


def test_ac18_journal_round_trip_tolerates_incidental_whitespace(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """min-b: the fact<->journal comparison is whitespace-normalized, so an entry
    that differs only in incidental spacing (around option commas, collapsed
    runs in the question) still satisfies the audit."""
    host = auto_tier2_workspace(chdir=False)
    _seed_policy_fact(
        driver_corpus, host, resolution="Chose option A (most reversible)"
    )
    spaced_question = POLICY_RESOLUTION_FIXTURE["question"].replace(" ", "   ")
    journal = (
        _well_formed_journal()
        .replace(
            f"- question: {POLICY_RESOLUTION_FIXTURE['question']}",
            f"- question:   {spaced_question}",
        )
        .replace("- options: A, B", "- options:   A ,  B ")
    )
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(journal, encoding="utf-8")
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0
    audit = envelope_tools.parse(out)["data"].get("decision_journal_audit")
    assert audit == "complete", (
        "FAIL min-b: an entry differing only in incidental whitespace must still "
        f"satisfy the fact-backed audit, got {audit!r}"
    )


def test_ac18_journal_entry_must_match_policy_fact_resolution(
    run_cli,
    envelope_tools,
    driver_corpus,
    monkeypatch,
    auto_tier2_workspace,
):
    """The entry must match the fact's recorded resolution: a journal `choice`
    that disagrees with the resolved option is a malformed (non-matching) entry,
    so the audit must report NOT complete."""
    host = auto_tier2_workspace(chdir=False)
    _seed_policy_fact(
        driver_corpus, host, resolution="Chose option A (most reversible)"
    )
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        _well_formed_journal(choice="B"), encoding="utf-8"
    )
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0
    audit = envelope_tools.parse(out)["data"].get("decision_journal_audit")
    assert audit is not None and audit != "complete", (
        "FAIL AC-18: a journal entry whose choice disagrees with the resolved "
        f"option must make the audit report NOT complete, got {audit!r}"
    )


def test_ac18_untagged_journal_entry_is_complete(
    run_cli,
    envelope_tools,
    driver_corpus,
    monkeypatch,
    auto_tier2_workspace,
):
    """
    review: ``[REVIEW]`` is optional. The autonomy addendum tags an entry only
        when principles conflict (step 5); a principle-decided resolution (step 4)
        records no tag. A well-formed, untagged entry must satisfy the audit
        (otherwise a spec-compliant step-4 session would wedge the close).
    """
    host = auto_tier2_workspace(chdir=False)
    fact = _policy_decision_fact()
    fact["resolution"]["review_flag"] = ""
    driver_corpus.arm(host, SLUG, decisions=[fact])
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        _well_formed_journal(review_flag=""), encoding="utf-8"
    )
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0
    audit = envelope_tools.parse(out)["data"].get("decision_journal_audit")
    assert audit == "complete", (
        "FAIL AC-18: a well-formed entry that carries no [REVIEW] tag (autonomy "
        f"addendum step 4) must satisfy the audit, got {audit!r}"
    )


def test_ac17_malformed_journal_entry_withholds_final_close(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    review (AC-17 close side): a retrospective present but a MALFORMED
        journal entry (ID match, missing required fields) must withhold final
        close acceptance — the close validation depends on a complete,
        well-formed audit, not an ID-only match.
    """
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_complete_boundary(driver_corpus, host)
    _seed_policy_fact(driver_corpus, host)
    (host / AUTO_TIER2_FRICTION_RETRO).write_text(
        "# Friction Retrospective\n\n## Tooling fights\n\n## Prompt gaps\n\n"
        "## Validator false-positives\n\n## Escalations that should have been "
        "policy-resolvable\n",
        encoding="utf-8",
    )
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        _MALFORMED_JOURNAL, encoding="utf-8"
    )
    monkeypatch.chdir(host)
    _enter_complete(run_cli, driver_corpus, state_path)

    code, out, _err = _submit_final(run_cli, host)
    envelope = envelope_tools.parse(out)
    assert code != 0 and not envelope["ok"], (
        "FAIL AC-17: a malformed journal entry (missing required fields) must "
        "withhold final close acceptance"
    )
    # Red-DISCRIMINATING (path-independent): the withhold must be FOR the journal
    # audit, not an incidental load/parse failure. Two guards:
    # (1) the error is not a state-load rejection; and
    # (2) the withhold reason names the journal audit — searched over the
    # structured reason fields with the host path stripped first, because the
    # pytest tmp dir embeds this test's name (which contains "journal") and
    # would otherwise satisfy the substring spuriously from the error path.
    err = envelope.get("error") or {}
    assert err.get("code") != "workspace-invalid", (
        "FAIL AC-17: the withhold must come from the journal-audit close validator, "
        f"not a state-load rejection, got {envelope!r}"
    )
    reason = (
        " ".join(
            str(part)
            for part in (
                err.get("code", ""),
                err.get("message", ""),
                err.get("hint", ""),
                json.dumps(envelope.get("next_actions", [])),
                json.dumps(envelope.get("diagnostics", [])),
            )
        )
        .replace(str(host), "<host>")
        .lower()
    )
    assert "journal" in reason or "audit" in reason, (
        "FAIL AC-17: the malformed-journal withhold must name the journal audit as "
        f"the reason (not an unrelated load/parse error), got {envelope!r}"
    )
    assert _terminal_fact_count(driver_corpus, state_path) == 0, (
        "FAIL AC-17: no terminal fact may append while the journal audit is malformed"
    )


# --------------------------------------------------------------------------- #
# AC-19 — flow switching at boundaries only
# --------------------------------------------------------------------------- #
def test_ac19_flow_set_records_flow_changes_fact(
    run_cli, envelope_tools, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    state_path = driver_corpus.arm(host, SLUG, flow="hitl")
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["flow", "set", "auto", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-19: `heddle flow set` must be wired (exit {code})"
    after = driver_corpus.read_yaml(state_path)
    changes = after.get("flow_changes", [])
    assert changes, (
        "FAIL AC-19: `flow set` must record a flow_changes[] fact (flow policy §10.1)"
    )
    assert changes[-1].get("to") == "auto", (
        "FAIL AC-19: the recorded flow change must target the requested mode"
    )
    assert changes[-1].get("status") == FLOW_CHANGE_PENDING, (
        "FAIL AC-19: `flow set` must queue a boundary-effective change"
    )
    assert after.get("flow") == "hitl", (
        "FAIL AC-19: flow set must not change effective flow mid-phase"
    )

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0
    assert envelope_tools.parse(out)["data"]["flow"] == "hitl", (
        "FAIL AC-19: status must report the old flow before the next boundary"
    )

    code, out, _err = run_cli(["phase-exit", "--feature", SLUG, "--json"])
    assert code == 0, (
        "FAIL AC-19: pending auto flow must apply at the next boundary, allowing "
        f"phase-exit without a HITL authorization block (exit {code})"
    )
    applied = driver_corpus.read_yaml(state_path)
    assert applied["flow"] == "auto", (
        "FAIL AC-19: pending flow change must become effective at boundary exit"
    )
    assert applied["flow_changes"][-1].get("status") == FLOW_CHANGE_APPLIED, (
        "FAIL AC-19: applied flow change fact must be marked applied"
    )


def test_ac19_repeated_flow_set_supersedes_stale_pending_change(
    run_cli, driver_corpus, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    state_path = driver_corpus.arm(host, SLUG, flow="hitl")
    monkeypatch.chdir(host)

    code, _out, _err = run_cli(["flow", "set", "auto", "--feature", SLUG, "--json"])
    assert code == 0
    code, _out, _err = run_cli(["flow", "set", "hitl", "--feature", SLUG, "--json"])
    assert code == 0

    queued = driver_corpus.read_yaml(state_path)
    changes = queued.get("flow_changes", [])
    assert changes[-1].get("status") == FLOW_CHANGE_SUPERSEDED, (
        "FAIL AC-19: switching back before a boundary must supersede the stale "
        "pending auto change instead of leaving it eligible for a later boundary"
    )
    assert not any(change.get("status") == FLOW_CHANGE_PENDING for change in changes), (
        "FAIL AC-19: no stale pending flow change may remain after supersession"
    )

    code, _out, _err = run_cli(["phase-exit", "--feature", SLUG, "--json"])
    assert code == 0
    after = driver_corpus.read_yaml(state_path)
    assert after["flow"] == "hitl", (
        "FAIL AC-19: a superseded stale auto change must not flip flow at the "
        "next boundary"
    )


def test_ac19_hitl_to_auto_switch_boundary_records_user_grant(
    run_cli, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    AC-19 / R1 (review, was the deferred review boundary-timing clause): a flow
        switch applies AT the boundary but does NOT retroactively re-flow the exiting
        phase's authorization. The grant source is derived from the flow the phase RAN
        under (snapshot.flow), not the pending target. So crossing a hitl->auto
        boundary records ``source: user`` — a human ran ``phase-exit`` (the driver is
        blocked on awaiting-human-authorization in hitl and never reaches here). The
        model therefore cannot mint a ``source: policy`` crossing INTO auto by parking
        a pending switch; auto preconditions are enforced at the first auto-governed
        boundary. The switch still lands: the NEXT phase runs under auto.

        Regression guard: the prior split (grant source taken from the pending
        boundary flow) recorded ``source: policy`` here — a self-granted crossing into
        auto. This FAILs under that behavior and PASSes under the single
        effective-flow rule.

    """
    host = auto_tier2_workspace(chdir=False)
    state_path = driver_corpus.arm(host, SLUG, flow="hitl")
    monkeypatch.chdir(host)

    code, _out, _err = run_cli(["flow", "set", "auto", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-19: `flow set auto` must be wired (exit {code})"

    code, _out, _err = run_cli(["phase-exit", "--feature", SLUG, "--json"])
    assert code == 0, (
        "FAIL AC-19: a human phase-exit at a hitl boundary IS the authorization "
        f"(awaiting-human-authorization is satisfied, not blocking), got exit {code}"
    )
    after = driver_corpus.read_yaml(state_path)
    assert after["flow"] == "auto", (
        "FAIL AC-19: the pending switch must apply AT the boundary (the next phase "
        "runs under auto)"
    )
    grants = after.get("authorizations", [])
    assert_has_grant(
        grants,
        source="user",
        message=(
            "FAIL R1/AC-19: crossing a hitl boundary must record source=user — the "
            "exiting phase ran under hitl, so a human authorized the crossing"
        ),
    )
    assert_no_grant(
        grants,
        source="policy",
        message=(
            "FAIL R1/AC-19: a parked auto switch must NOT mint a source=policy "
            "crossing into auto — that precondition is enforced at the first "
            "auto-governed boundary, not retroactively at this hitl exit"
        ),
    )


def test_ac19_auto_to_hitl_switch_boundary_records_policy_grant(
    run_cli, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    AC-19 / R1 (review): the mirror direction. An auto phase that queues a
        switch to hitl records its OWN exit honestly as ``source: policy`` (the auto
        driver self-granted under the flow it ran) — NOT a forged ``source: user``
        grant at an exit no human performed — and the switch still lands at the
        boundary, so the NEXT phase is hitl and the driver will stop for the human
        there.

        Regression guard: the prior split (grant source taken from the pending
        boundary flow = hitl) forged a ``source: user`` grant here. This FAILs under
        that behavior and PASSes under the single effective-flow rule.

    """
    host = auto_tier2_workspace(chdir=False)
    state_path = driver_corpus.arm(host, SLUG, flow="auto")
    monkeypatch.chdir(host)

    code, _out, _err = run_cli(["flow", "set", "hitl", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-19: `flow set hitl` must be wired (exit {code})"

    code, _out, _err = run_cli(["phase-exit", "--feature", SLUG, "--json"])
    assert code == 0, (
        f"FAIL AC-19: an auto-boundary phase-exit must self-grant (exit {code})"
    )
    after = driver_corpus.read_yaml(state_path)
    assert after["flow"] == "hitl", (
        "FAIL AC-19: the pending switch must apply AT the boundary (the next phase "
        "runs under hitl)"
    )
    grants = after.get("authorizations", [])
    assert_has_grant(
        grants,
        source="policy",
        message=(
            "FAIL R1/AC-19: an auto phase's own exit must record source=policy — "
            "the driver self-granted it under the flow it ran"
        ),
    )
    assert_no_grant(
        grants,
        source="user",
        message=(
            "FAIL R1/AC-19: a parked hitl switch must NOT forge a source=user grant "
            "at an auto exit no human performed"
        ),
    )


# --------------------------------------------------------------------------- #
# robustness failure regressions
# --------------------------------------------------------------------------- #
def _conformant_retro() -> str:
    return (
        "# Friction Retrospective\n\n## Tooling fights\n\n## Prompt gaps\n\n"
        "## Validator false-positives\n\n## Escalations that should have been "
        "policy-resolvable\n"
    )


def test_malformed_test_command_returns_structured_failure(
    run_cli, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    review: a malformed `autopilot.test_command` (unbalanced quotes) must
        degrade to a structured close-validation failure — NOT crash the final
        close acceptance with a raw traceback and no JSON envelope.
    """
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_complete_boundary(driver_corpus, host)
    # Retro + empty journal present (no policy fact → vacuous journal audit) so the
    # close suite is the only remaining gate; then point it at a command string
    # with an unbalanced quote the shell cannot parse.
    (host / AUTO_TIER2_FRICTION_RETRO).write_text(_conformant_retro(), encoding="utf-8")
    (host / AUTO_TIER2_DECISION_JOURNAL).write_text(
        "# Decision Journal\n", encoding="utf-8"
    )
    monkeypatch.chdir(host)
    _enter_complete(run_cli, driver_corpus, state_path)
    set_autopilot_config(host, test_command='pytest "tests/unterminated')

    code, out, _err = _submit_final(run_cli, host)
    envelope = json.loads(out)
    assert code != 0 and not envelope["ok"], (
        "FAIL malformed command: a malformed test_command must yield a structured "
        "failure "
        "envelope, not a raw ValueError traceback"
    )
    assert _terminal_fact_count(driver_corpus, state_path) == 0, (
        "FAIL malformed command: no terminal fact may append while the close suite "
        "cannot run"
    )
    assert _spec_lifecycle(host) != "complete", (
        "FAIL malformed command: no lifecycle stamp on a malformed suite command"
    )


def test_stamp_lifecycle_write_failure_raises_structured_error(tmp_path, monkeypatch):
    """
    review: a write-side failure in stamp_lifecycle (disk full, read-only spec
        dir) must raise a structured KernelError — which run_phase_exit catches and
        renders as a failure envelope — not crash with a raw OSError traceback. Red
        before the write guard: the OSError escapes stamp_lifecycle unwrapped.
    """
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import write_path

    spec = tmp_path / "feature.md"
    spec.write_text("---\nlifecycle: building\n---\n\nbody\n", encoding="utf-8")

    real_write_text = write_path.Path.write_text

    def boom(self, *args, **kwargs):
        if self.name.endswith(".tmp"):  # the stamp's atomic temp write
            raise OSError("simulated disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(write_path.Path, "write_text", boom)

    with pytest.raises(KernelError) as excinfo:
        write_path.stamp_lifecycle(spec)
    assert excinfo.value.code == "workspace-invalid", (
        "FAIL stamp-write failure: a stamp write failure must surface as a "
        "workspace-invalid "
        "KernelError (the structured-envelope path), not a raw OSError"
    )


def test_unreadable_retro_reported_distinct_from_missing(
    run_cli, driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    review: a friction retrospective that EXISTS but is unreadable (non-UTF-8)
        must be reported as 'unreadable', not 'missing' — the latter sends the
        operator to write a file that already exists.
    """
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_complete_boundary(driver_corpus, host)
    # Present-but-unreadable retro: a real file with invalid UTF-8 content.
    (host / AUTO_TIER2_FRICTION_RETRO).write_bytes(b"\xff\xfe not utf-8\xff")
    monkeypatch.chdir(host)
    _enter_complete(run_cli, driver_corpus, state_path)

    code, out, _err = _submit_final(run_cli, host)
    envelope = json.loads(out)
    assert code != 0 and not envelope["ok"], (
        "FAIL unreadable retrospective: an unreadable retro must withhold final close "
        "acceptance"
    )
    blob = json.dumps(envelope).lower()
    assert "unreadable" in blob, (
        "FAIL unreadable retrospective: a present-but-unreadable retro must be "
        "reported as "
        f"'unreadable', not 'missing' — got {envelope!r}"
    )
    assert _terminal_fact_count(driver_corpus, state_path) == 0, (
        "FAIL unreadable retrospective: no terminal fact may append while the retro is "
        "unreadable"
    )


def _record_test_policy(run_cli, host, payload, *, dry_run=False):
    path = host / "policy-input.json"
    path.write_text(
        json.dumps({"schema": "heddle.policy-batch/v1", "resolutions": [payload]})
    )
    arguments = [
        "decisions",
        "record-policy",
        "--feature",
        SLUG,
        "--from-file",
        str(path),
        "--json",
    ]
    if dry_run:
        arguments.append("--dry-run")
    return run_cli(arguments)


def test_policy_resolution_id_with_whitespace_is_rejected(
    run_cli, driver_corpus, auto_tier2_workspace
):
    """The application rejects an ID that cannot round-trip through a heading."""
    host = auto_tier2_workspace()
    state_path = driver_corpus.state(host, SLUG)
    before = state_path.read_bytes()
    valid = policy_resolution_payload(routes_to=AUTO_TIER2_PLAN)
    code, out, _err = _record_test_policy(run_cli, host, valid, dry_run=True)
    assert code == 0, out
    bad = {**valid, "id": "P1 alt"}
    code, out, _err = _record_test_policy(run_cli, host, bad)
    assert code == 2 and json.loads(out)["error"]["code"] == "usage"
    assert state_path.read_bytes() == before
    assert not (host / AUTO_TIER2_DECISION_JOURNAL).exists()


def test_journal_write_is_atomic_on_failure(tmp_path, monkeypatch):
    """A publication failure leaves existing journal bytes intact and no temp."""
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import decisions

    journal = tmp_path / "feat.decision-journal.md"
    journal.write_text("# Decision Journal\n\n## P1 - prior\n", encoding="utf-8")
    before = journal.read_bytes()

    def boom(_src, _dst, *_args, **_kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(decisions.state_store.os, "replace", boom)
    with pytest.raises(KernelError) as error:
        decisions._publish_policy_journal(journal, "# Decision Journal\nclobbered\n")
    assert error.value.code == "workspace-invalid"
    assert journal.read_bytes() == before
    assert list(tmp_path.iterdir()) == [journal]


def test_drive_leaves_no_orphan_temp_files(
    run_json, driver_corpus, fake_claude, auto_tier2_workspace
):
    """
    review: per-session driver temps (prompt / session-fact / raw JSONL) are
        confined to a per-run scratch dir removed on loop exit, so a drive run leaks no
        temp artifacts into the system temp dir.
    """
    import glob
    import os as _os
    import tempfile as _tempfile

    auto_tier2_workspace()
    patterns = (
        "heddle-prompt-*",
        "heddle-session-*",
        "heddle-phase-*",
        "heddle-drive-*",
    )
    tmp = _tempfile.gettempdir()

    def snapshot() -> set[str]:
        found: set[str] = set()
        for pattern in patterns:
            found |= set(glob.glob(_os.path.join(tmp, pattern)))
        return found

    before = snapshot()
    code, _envelope, _out, _err = run_json(
        ["drive", "--feature", SLUG, "--until", "spec-review", "--json"]
    )
    assert code == 0, f"drive must run a session and exit cleanly, got {code}"
    leaked = snapshot() - before
    assert not leaked, f"FAIL gate cap: drive leaked temp artifacts: {sorted(leaked)}"


def test_comma_bearing_option_round_trips_through_close_audit(
    run_cli, driver_corpus, auto_tier2_workspace
):
    """Comma-bearing options survive the real command's fact/journal write."""
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime.audit import decision_journal_audit

    host = auto_tier2_workspace()
    options = ["keep the current milestone split", "use smaller milestones"]
    payload = policy_resolution_payload(routes_to=AUTO_TIER2_PLAN)
    payload.update(
        options=options, choice=options[0], rationale="Keep the current milestones"
    )
    code, out, _err = _record_test_policy(run_cli, host, payload)
    assert code == 0, out
    config = load_project_config(host)
    state = resolve_snapshot(config, SLUG).state
    assert decision_journal_audit(config, SLUG, state).status == "complete"
    journal = host / AUTO_TIER2_DECISION_JOURNAL
    journal.write_text(journal.read_text().replace(options[0], "different choice"))
    assert decision_journal_audit(config, SLUG, state).status == "incomplete"


def test_policy_resolution_field_with_line_break_is_rejected(
    run_cli, driver_corpus, auto_tier2_workspace
):
    """No input field may inject a line into the canonical journal entry."""
    host = auto_tier2_workspace()
    state_path = driver_corpus.state(host, SLUG)
    before = state_path.read_bytes()
    valid = policy_resolution_payload(routes_to=AUTO_TIER2_PLAN)
    code, out, _err = _record_test_policy(run_cli, host, valid, dry_run=True)
    assert code == 0, out
    for field in ("title", "question", "choice", "rationale", "basis"):
        bad = {**valid, field: "line one\nline two"}
        code, out, _err = _record_test_policy(run_cli, host, bad)
        assert code == 2 and json.loads(out)["error"]["code"] == "usage", field
        assert state_path.read_bytes() == before
    bad = {**valid, "options": ["A", "B\nstill B"]}
    code, out, _err = _record_test_policy(run_cli, host, bad)
    assert code == 2 and json.loads(out)["error"]["code"] == "usage"
    assert state_path.read_bytes() == before
    assert not (host / AUTO_TIER2_DECISION_JOURNAL).exists()


def test_missing_command_phase_session_timeout_accrues_to_attempt_cap(
    run_json, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    """
    review (Important, iter3): a phase session killed by the watchdog (TIMEOUT
        exit 124 / INACTIVITY exit 118) must record a non-advancing session fact and
        let the existing FLOW_AUTO guards escalate to the human decision batch — not
        halt FATAL on the first timeout demanding an unbounded human retry of an
        unattended run. Two consecutive non-advancing sessions trip the no-progress
        guard (the attempt cap at five is the further backstop).
    """
    from heddle.driver import loop
    from heddle.driver.sessions import SessionResult

    # Arm at implement: its boundary needs an advancing session (unlike the cheap
    # specify..scaffold boundaries), so timed-out sessions accumulate at one stage.
    # E1-2: current task-less milestone = the session-projecting state (all-todo
    # now derives the driver-owned promote advance, not a kickoff session).
    host = auto_tier2_workspace(
        chdir=False,
        stage="implement",
        authorized_through="implement",
        mutate=make_first_milestone_current_and_taskless,
    )
    state_path = driver_corpus.state(host, SLUG)
    monkeypatch.chdir(host)

    def _timeout(*_args, **_kwargs):
        return SessionResult(exit_code=124, termination="timeout", output={})

    monkeypatch.setattr(loop, "_run_phase_session", _timeout)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code != 0 and not envelope["ok"], (
        "FAIL timeout: repeated phase-session timeouts must eventually halt, not loop"
    )
    # The key behavioral change: an escalation to the human decision batch, NOT the
    # old FATAL "phase session failed with exit 124" (code workspace-invalid) halt.
    assert envelope["error"]["code"] == "no-progress", (
        "FAIL timeout: a timed-out session must record a non-advancing fact and "
        f"escalate to the human decision batch, not halt FATAL, got {envelope!r}"
    )
    assert (
        envelope["next_actions"][0]["command"]
        == f"heddle decisions list --feature {SLUG}"
    ), f"FAIL timeout: the escalation must route to the decision batch, {envelope!r}"
    state = driver_corpus.read_yaml(state_path)
    sessions = state.get("sessions", [])
    assert len(sessions) == 2 and all(
        session.get("stage") == "implement" and session.get("advanced") is False
        for session in sessions
    ), (
        "FAIL timeout: each timeout must record one non-advancing session fact at the "
        f"stuck stage, and two consecutive trip the no-progress guard, got {sessions!r}"
    )


def test_commit_state_write_failure_raises_structured_error(
    driver_corpus, auto_tier2_workspace, monkeypatch
):
    """The transaction owner maps disk errors and preserves the original state."""
    from heddle.kernel.project_config import KernelError
    from heddle.runtime import state_store

    host = auto_tier2_workspace()
    state_path = driver_corpus.state(host, SLUG)
    before = state_path.read_bytes()
    revision = driver_corpus.read_yaml(state_path)["revision"]

    def boom(*_args, **_kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(state_store.os, "replace", boom)
    with pytest.raises(KernelError) as excinfo:
        state_store.commit_state(
            state_path,
            expect_revision=revision,
            transform=lambda document: {**document, "stage": "spec-review"},
        )
    assert excinfo.value.code == "workspace-invalid"
    assert state_path.read_bytes() == before
    assert not list(state_path.parent.glob("state.yaml.*.tmp"))


def test_driver_maps_state_write_oserror_to_fatal_halt(
    driver_corpus, monkeypatch, auto_tier2_workspace
):
    """
    review: the loop call sites that previously caught only Conflict now map a
        commit_state_write KernelError to a FATAL hard-failure result, so an unwritable
        state file during an escalation routes through the REQ-13 notify set instead of
        escaping as a traceback. Exercises the widened `_raise_decision_batch` site.
    """
    from heddle.contracts.result import ExitCode
    from heddle.driver import loop
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import KernelError, load_project_config
    from heddle.runtime import decisions

    host = auto_tier2_workspace(chdir=False)
    monkeypatch.chdir(host)
    config = load_project_config(host)
    snap = resolve_snapshot(config, SLUG)

    def boom(*_args, **_kwargs):
        raise KernelError(
            code="workspace-invalid",
            message="state file is unwritable: simulated disk full",
            hint="check the workspace filesystem has space and is writable",
        )

    monkeypatch.setattr(decisions, "commit_state", boom)

    result = loop._raise_decision_batch(SLUG, snap, "no-progress")
    assert result.error is not None and result.error.code == "workspace-invalid", (
        f"FAIL state-write failure: an unwritable state file must map to a structured "
        f"workspace-invalid failure, got {result!r}"
    )
    assert result.exit_code == ExitCode.FATAL, (
        "FAIL state-write failure: the halt must be FATAL so it routes through the "
        "REQ-13 notify "
        f"set {{INTERNAL, FATAL}}, got exit {result.exit_code}"
    )


# --------------------------------------------------------------------------- #
# completion (kickoff-guidance-integration) — driver handoff + fail-loud (gate)
# --------------------------------------------------------------------------- #
# Red discriminators for AC-2 (driver delivery half), AC-4, and AC-7 (driver
# half): a resolved `complete` snapshot is handled BEFORE capability probing
# and auto-suite startup checks; kickoff failures propagate typed and halt
# before spawn; the raw briefing body is embedded exactly once. All of these
# fail today (drive probes first, falls back to a minimal header on kickoff
# failure, and strips/succeeds at complete).
# Behavior contract: kickoff-guidance-integration


def _completion_arm_complete_stage(
    driver_corpus,
    host,
    *,
    flow: str | None = None,
    terminal: bool = False,
    pending_decision: bool = False,
):
    """Arm the demo workspace AT `stage: complete` (post-boundary): every
    milestone done+verified, the complete grant recorded, optionally the
    qualifying completion fact (terminal) or a live ordinary blocker."""

    def mutate(state: dict) -> None:
        state["stage"] = "complete"
        state["authorized_through"] = "complete"
        if flow is not None:
            state["flow"] = flow
        for milestone in state["milestones"]:
            milestone["status"] = "done"
            milestone["tasks"] = [
                {"id": t["id"], "text": t.get("text", ""), "status": "done"}
                for t in milestone.get("tasks", [])
            ]
        state["authorizations"] = [
            {"through": "complete", "source": "user", "at": "2026-07-14T09:00Z"}
        ]
        if terminal:
            for entry in state["feature_policy"]["entries"]:
                if entry["role"] == "peer-review-sequential":
                    entry.update(mode="off", limit=None, minimum_rounds=0)
        if pending_decision:
            state.setdefault("decisions", []).append(
                {
                    "id": "completion-open",
                    "kind": "question",
                    "class": 8,
                    "source": "completion-open",
                    "rationale": None,
                    "resolution": None,
                    "resolution_source": None,
                    "resolved_at": None,
                    "title": "an ordinary open question",
                    "status": "pending",
                    "routes_to": [AUTO_TIER2_PLAN],
                    "created_at": "2026-07-14T09:00Z",
                }
            )

    state_path = driver_corpus.mutate(host, SLUG, mutate)
    from tests.runtime.write_path_helpers import authored_boundary_verifications

    authored_boundary_verifications(state_path)
    if terminal:
        import os

        from heddle.contracts.operations import FeatureComplete
        from heddle.runtime.application import execute
        from tests.content_identity_helpers import git
        from tests.driver.helpers import initialize_fixture_git

        (host / AUTO_TIER2_FRICTION_RETRO).write_text(_conformant_retro())
        (host / AUTO_TIER2_DECISION_JOURNAL).write_text("# Decision Journal\n")
        (state_path.parent / "brief.md").write_text(
            "# Brief\n\nExercise terminal driver completion.\n"
        )
        initialize_fixture_git(host)
        previous = os.getcwd()
        try:
            os.chdir(host)
            result = execute(FeatureComplete(feature=SLUG))
            assert result.ok and result.data["accepted"], result
            git(host, "add", "-f", "plans", "docs")
            git(host, "commit", "-qm", "retain accepted fixture")
            result = execute(FeatureComplete(feature=SLUG))
            assert result.ok and int(result.exit_code) == 0, result
        finally:
            os.chdir(previous)
    return state_path


def _completion_no_claude_on_path(monkeypatch, tmp_path) -> None:
    """Guarantee the REQ-21 capability probe would FAIL if attempted — the
    complete-stage branch must resolve truth before any probe (AC-7). A
    poisoned `claude` (empty --help output, `unknown option` at parse — both
    probe-rejecting shapes) shadows any real or fake binary while the rest of
    PATH stays intact: the notify-capture script the same tests assert on
    still needs its interpreter, so emptying PATH would break the very
    notification path AC-7 pins."""
    import os as _os

    from tests.driver.helpers import write_executable_script

    poison_bin = tmp_path / "completion-poison-bin"
    poison_bin.mkdir(exist_ok=True)
    write_executable_script(
        poison_bin / "claude",
        "#!/bin/sh\n"
        'if [ "$1" = "--help" ]; then\n'
        "  exit 1\n"
        "fi\n"
        "echo \"error: unknown option '$1'\" >&2\n"
        "exit 1\n",
        mode=0o755,
    )
    monkeypatch.setenv(
        "PATH", f"{poison_bin}{_os.pathsep}{_os.environ.get('PATH', '')}"
    )


class TestCompletionCompleteHandoff:
    """AC-7 (driver half): pending complete notifies the handoff, bounded
    drive is quiet, terminal complete succeeds — all without capability
    probes, auto-suite config checks, session spawns, or state writes."""

    @pytest.mark.parametrize("until", [None, "complete"])
    def test_completion_terminal_complete_is_success_without_probes(
        self,
        run_json,
        driver_corpus,
        auto_tier2_workspace,
        monkeypatch,
        tmp_path,
        until,
    ):
        host = auto_tier2_workspace(chdir=False)
        state_path = _completion_arm_complete_stage(
            driver_corpus, host, flow="hitl", terminal=True
        )
        before = state_path.read_bytes()
        _completion_no_claude_on_path(monkeypatch, tmp_path)
        monkeypatch.chdir(host)

        argv = ["drive", "--feature", SLUG, "--json"]
        if until is not None:
            argv[3:3] = ["--until", until]
        code, envelope, _out, _err = run_json(argv)
        assert code == 0 and envelope["ok"], (
            f"FAIL AC-7/REQ-9: terminal complete must succeed (until={until}), "
            f"got exit {code}: {envelope.get('error')}"
        )
        assert envelope["data"]["status"] == "complete", (
            "FAIL REQ-9: drive returns `complete` even with --until complete "
            "once terminal evidence exists"
        )
        assert state_path.read_bytes() == before

    def test_completion_ordinary_blocker_precedes_handoff_without_probes(
        self,
        run_json,
        driver_corpus,
        auto_tier2_workspace,
        monkeypatch,
        tmp_path,
    ):
        host = auto_tier2_workspace(chdir=False)
        state_path = _completion_arm_complete_stage(
            driver_corpus, host, flow="hitl", pending_decision=True
        )
        before = state_path.read_bytes()
        _completion_no_claude_on_path(monkeypatch, tmp_path)
        monkeypatch.chdir(host)

        code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
        assert code == 1 and envelope["error"]["code"] == "pending-decisions", (
            "FAIL AC-7/INV-5: an ordinary complete-stage blocker keeps its "
            "existing blocked result — and still no capability probe, got "
            f"exit {code}: {envelope.get('error')}"
        )
        assert state_path.read_bytes() == before


class TestCompletionKickoffFailLoud:
    """AC-4: a failed or degenerate internal kickoff halts drive BEFORE any
    session spawn, preserving the structured failure — never the minimal
    fallback header."""

    def test_completion_unreadable_briefing_halts_with_preserved_failure(
        self,
        run_json,
        driver_corpus,
        auto_tier2_workspace,
        fake_claude,
        notify_capture,
        monkeypatch,
    ):
        from tests.driver.helpers import append_autopilot_notify_command

        host = auto_tier2_workspace(chdir=False)
        state_path = driver_corpus.state(host, SLUG)
        append_autopilot_notify_command(host, notify_capture.command)
        override = host / ".heddle" / "overrides" / "specify.briefing.md"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_bytes(b"\xff\xfe\x00 not utf-8")
        before = state_path.read_bytes()
        monkeypatch.chdir(host)

        code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
        assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
            "FAIL AC-4/REQ-4: the kickoff resource/read failure must halt "
            "drive with its original code and FATAL exit preserved, got "
            f"exit {code}: {envelope.get('error')}"
        )
        assert "specify.briefing.md" in envelope["error"]["hint"], (
            "FAIL AC-4: the original kickoff hint must be preserved"
        )
        assert fake_claude.calls() == [], (
            "FAIL AC-4/INV-3: no session may spawn after a kickoff failure"
        )
        assert state_path.read_bytes() == before, (
            "FAIL AC-4: the failed kickoff path writes no state"
        )
        assert notify_capture.received(), "FAIL AC-4: the FATAL halt must notify"

    def test_completion_blank_stage_is_rejected_before_policy_composition(
        self,
        run_json,
        driver_corpus,
        auto_tier2_workspace,
        fake_claude,
        monkeypatch,
    ):
        host = auto_tier2_workspace(chdir=False)
        state_path = driver_corpus.state(host, SLUG)
        override = host / ".heddle" / "overrides" / "specify.briefing.md"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text("\n   \n", encoding="utf-8")
        before = state_path.read_bytes()
        monkeypatch.chdir(host)

        code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
        assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
            "FAIL AC-4/REQ-4: a blank stage body "
            "must preserve kickoff's typed FATAL delivery failure — never "
            f"the minimal fallback prompt, got exit {code}: "
            f"{envelope.get('error')}"
        )
        assert "specify.briefing.md" in envelope["error"]["hint"]
        assert fake_claude.calls() == [], (
            "FAIL AC-4/INV-3: the malformed-success path must not spawn"
        )
        assert state_path.read_bytes() == before

    def test_completion_typed_kickoff_failure_preserves_diagnostics(self, monkeypatch):
        """The original result crosses the application seam without reconstruction."""
        from heddle.contracts import operations as ops
        from heddle.contracts.result import (
            Diagnostic,
            ExitCode,
            HeddleError,
            HeddleResult,
            NextAction,
            Severity,
        )
        from heddle.driver import loop

        original = HeddleResult.failure(
            HeddleError("workspace-invalid", "m", "h"),
            exit_code=ExitCode.FATAL,
            diagnostics=(
                Diagnostic(
                    Severity.ADVISORY,
                    "pointer-stale",
                    "the recorded pointer names a missing workspace",
                ),
            ),
            next_actions=(
                NextAction(
                    ops.CommandAction(ops.Status(feature=SLUG)),
                    "re-orient before retrying the kickoff",
                ),
            ),
        )
        seen = []

        def execute(operation):
            seen.append(operation)
            return original

        monkeypatch.setattr(loop.application, "execute", execute)
        result = loop._stage_briefing(SLUG)
        assert seen == [ops.Kickoff(feature=SLUG)]
        assert result is original
        assert result.to_envelope()["diagnostics"][0]["code"] == "pointer-stale"
        assert result.next_actions[0].command == f"heddle status --feature {SLUG}"


class TestCompletionBriefingDelivery:
    """
    AC-2 (driver half): the exact unmodified `data.briefing` body — edge
        whitespace included — is one contiguous substring of the phase-session
        prompt, exactly once, BEFORE the driver framing, at every stage from
        specify through robustness (spec-review/plan-review prove the path
        without content edits).

        Survivor pins: the packaged-briefing tests below are green from scaffold
        time BY DESIGN — today's strip-then-reappend bridge happens to reproduce
        the raw bytes for normalized packaged briefings, and that delivery must
        survive gate's typed-seam rewrite. The red discriminator is the
        edge-whitespace override test, where strip-based delivery is observably
        lossy today.
    """

    _THROUGH_ROBUSTNESS = (
        "specify",
        "spec-review",
        "plan-review",
        "scaffold",
        "implement",
        "peer-review",
        "robustness",
    )

    def test_completion_edge_whitespace_briefing_is_delivered_verbatim(
        self, driver_corpus, auto_tier2_workspace, monkeypatch, tmp_path
    ):
        """
        RED discriminator for AC-2/review: a valid briefing whose raw body
                carries edge whitespace must be delivered byte-identically — strip()
                is a validation predicate only, never a transform of the delivered
                string (spec, Single briefing delivery).
        """
        from pathlib import Path as _Path

        from heddle.driver import loop as driver_loop
        from heddle.kernel.model import resolve_snapshot
        from heddle.kernel.project_config import load_project_config

        host = auto_tier2_workspace(chdir=False)
        override = host / ".heddle" / "overrides" / "specify.briefing.md"
        override.parent.mkdir(parents=True, exist_ok=True)
        raw = "\n# Specify (host override)\n\nDo the work.\n\n\n"
        override.write_text(raw, encoding="utf-8")
        monkeypatch.chdir(host)
        snap = resolve_snapshot(load_project_config(host), SLUG)

        prompt = driver_loop._write_prompt(
            snap, "heddle kickoff", None, scratch_dir=tmp_path
        )
        assert isinstance(prompt, _Path), (
            "FAIL AC-2: a valid (nonblank) briefing must produce a prompt path"
        )
        text = prompt.read_text(encoding="utf-8")
        assert text.count(raw) == 1, (
            "FAIL AC-2: the briefing body must be preserved exactly, edge "
            f"whitespace included — found {text.count(raw)} contiguous "
            "occurrences of the raw override body"
        )

    @pytest.mark.parametrize("stage", _THROUGH_ROBUSTNESS)
    def test_completion_prompt_embeds_raw_briefing_exactly_once(
        self, driver_corpus, auto_tier2_workspace, monkeypatch, tmp_path, stage
    ):
        # Survivor pin (see class docstring).
        from pathlib import Path as _Path

        from heddle.driver import loop as driver_loop
        from heddle.kernel.model import resolve_snapshot
        from heddle.kernel.project_config import load_project_config

        host = auto_tier2_workspace(chdir=False)
        driver_corpus.arm(host, SLUG, stage=stage, authorized_through=stage)
        monkeypatch.chdir(host)
        snap = resolve_snapshot(load_project_config(host), SLUG)

        raw = (
            _Path(driver_loop.__file__).resolve().parent.parent
            / "resources"
            / f"{stage}.briefing.md"
        ).read_text(encoding="utf-8")

        prompt = driver_loop._write_prompt(
            snap, "heddle kickoff", None, scratch_dir=tmp_path
        )
        assert isinstance(prompt, _Path), (
            f"FAIL AC-2({stage}): a valid briefing must produce a prompt path"
        )
        text = prompt.read_text(encoding="utf-8")
        assert text.count(raw) == 1, (
            f"FAIL AC-2({stage}): the RAW briefing body (edge whitespace "
            "included) must appear as one contiguous substring exactly once "
            f"in the prompt — found {text.count(raw)} occurrences"
        )
        framing = f"Feature: {SLUG}"
        assert framing in text and text.index(raw) < text.index(framing), (
            f"FAIL AC-2({stage}): the briefing must precede the driver-owned "
            "feature/action framing"
        )

    def test_completion_spawned_session_stdin_carries_raw_briefing(
        self,
        run_json,
        driver_corpus,
        auto_tier2_workspace,
        fake_claude,
        monkeypatch,
    ):
        # Survivor pin (see class docstring).
        from pathlib import Path as _Path

        from heddle.driver import loop as driver_loop

        host = auto_tier2_workspace(chdir=False)
        monkeypatch.chdir(host)
        outcome = run_json(["drive", "--feature", SLUG, "--json"])
        calls = fake_claude.calls()
        assert calls, f"FIXTURE ROT: the specify drive must spawn a session: {outcome}"
        raw = (
            _Path(driver_loop.__file__).resolve().parent.parent
            / "resources"
            / "specify.briefing.md"
        ).read_text(encoding="utf-8")
        stdin = calls[0]["stdin"]
        policy = (
            _Path(driver_loop.__file__).resolve().parent.parent
            / "resources"
            / "decision-routing.md"
        ).read_text(encoding="utf-8")
        assert stdin.count(policy) == 1
        assert stdin.index(policy) < stdin.index(raw)
        assert stdin.count(raw) == 1, (
            "FAIL AC-2: the spawned session's prompt must carry the raw "
            f"briefing exactly once, found {stdin.count(raw)}"
        )


def test_completion_robustness_drive_no_longer_reconciles_the_journal(
    run_json, driver_corpus, auto_tier2_workspace, fake_claude, monkeypatch
):
    """AC-14 (driver semantics) / spec Driver ordering: the robustness-time
    `_reconcile_decision_journal` call is removed — completion owns final journal
    reconciliation AFTER the last auto session can write it. Today drive
    recreates the journal before the until-reached return; under completion the
    absent journal stays absent."""
    host = auto_tier2_workspace(chdir=False)
    _seed_policy_fact(driver_corpus, host)
    driver_corpus.arm(host, SLUG, stage="robustness", authorized_through="robustness")
    journal = host / AUTO_TIER2_DECISION_JOURNAL
    assert not journal.exists(), "FIXTURE ROT: journal must start absent"
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(
        ["drive", "--feature", SLUG, "--until", "robustness", "--json"]
    )
    assert code == 0 and envelope["data"]["status"] == "until-reached", (
        f"FIXTURE ROT: the bounded robustness drive must stop quietly, got "
        f"exit {code}: {envelope!r}"
    )
    assert not journal.exists(), (
        "FAIL completion: drive must not author/reconcile the decision journal at "
        "robustness — final reconciliation moves to completion stage"
    )
