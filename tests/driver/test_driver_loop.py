"""Driver loop, session spawning, and stop-condition behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.driver.constants import AUTO_TIER2_SLUG as SLUG
from tests.driver.constants import AUTO_TIER2_SPEC
from tests.driver.helpers import (
    append_autopilot_notify_command,
    arm_at_stage,
    assert_has_grant,
    assert_no_grant,
    make_first_milestone_current_and_taskless,
    mutate_heddle_config,
    phase_permission_inputs,
    write_executable_script,
)

# --------------------------------------------------------------------------- #
# AC-6 — the scaffold-stage pinning of the per-phase permission profile (the source of
# truth the spawn argv must match, set-equal on allow / superset on deny).
# --------------------------------------------------------------------------- #
READ_BASELINE = (
    "Read",
    "Grep",
    "Glob",
    "Bash(heddle status:*)",
    "Bash(heddle orient:*)",
    "Bash(heddle kickoff:*)",
    "Bash(heddle session log:*)",
    "Bash(heddle decisions add:*)",
    "Bash(heddle decisions record-policy:*)",
)
MILESTONE_TOOLS = (
    "Bash(heddle milestone add:*)",
    "Bash(heddle milestone edit:*)",
)
# Literal command oracle, separate from the file rules that W1-B migrates.
# Both complete-profile tests and W1-B's focused survivor use this one policy.
EXPECTED_COMMAND_SUFFIXES = {
    "specify": MILESTONE_TOOLS + ("Bash(heddle search:*)", "Bash(heddle validate:*)"),
    "spec-review": ("Bash(heddle run-gate:*)", "Bash(heddle review interpret:*)")
    + MILESTONE_TOOLS
    + (
        "Bash(heddle validate:*)",
        "Bash(heddle review disposition:*)",
        "Bash(heddle review allowance:*)",
    ),
    "plan-review": ("Bash(heddle run-gate:*)", "Bash(heddle review interpret:*)")
    + MILESTONE_TOOLS
    + (
        "Bash(heddle validate:*)",
        "Bash(heddle review disposition:*)",
        "Bash(heddle review allowance:*)",
    ),
    "scaffold": (
        "Bash(heddle run-gate:*)",
        "Bash(heddle review interpret:*)",
        "Bash(heddle commands set:*)",
        "Bash(heddle milestone edit:*)",
        "Bash(heddle review disposition:*)",
        "Bash(heddle review allowance:*)",
    ),
    "implement": (
        "Bash(heddle task:*)",
        "Bash(heddle verify:*)",
        "Bash(heddle run-gate:*)",
        "Bash(heddle review interpret:*)",
        "Bash(heddle review disposition:*)",
        "Bash(heddle review allowance:*)",
    ),
    "peer-review": (
        "Bash(heddle run-gate:*)",
        "Bash(heddle review interpret:*)",
        "Bash(heddle verify:*)",
        "Bash(heddle validate:*)",
        "Bash(heddle review disposition:*)",
        "Bash(heddle review allowance:*)",
    ),
    "robustness": (
        "Bash(heddle run-gate:*)",
        "Bash(heddle review interpret:*)",
        "Bash(heddle verify:*)",
        "Bash(heddle validate:*)",
        "Bash(heddle review disposition:*)",
        "Bash(heddle review allowance:*)",
    ),
}


def expected_allow_by_stage(host: Path) -> dict[str, frozenset[str]]:
    """Literal fixture expectations; never derived from the permission resolver."""
    root = f"/{host.resolve()}"
    spec = (f"Edit({root}/{AUTO_TIER2_SPEC})",)
    plan = (f"Edit({root}/plans/{SLUG}/plan.md)",)
    documents = (f"Edit({root}/plans/{SLUG}/brief.md)",) + plan
    # Both declared implementation paths are missing files in this fixture.
    current = (f"Edit({root}/src/example/demo.py)",)
    all_owned = current + (f"Edit({root}/src/example/cli.py)",)
    files_by_stage = {
        "specify": spec
        + documents
        + (f"Edit({root}/docs/features/_descriptions.yaml)",),
        "spec-review": spec + documents,
        "plan-review": documents,
        "scaffold": spec + documents + (f"Edit({root}/tests/**)",),
        "implement": spec + documents + current,
        "peer-review": spec + plan + all_owned,
        "robustness": spec + plan + all_owned,
    }
    return {
        stage: frozenset(READ_BASELINE + EXPECTED_COMMAND_SUFFIXES[stage] + files)
        for stage, files in files_by_stage.items()
    }


def expected_uniform_deny(host: Path) -> frozenset[str]:
    root = f"/{host.resolve()}"
    return frozenset(
        {
            f"Edit({root}/docs/design/architecture.md)",
            f"Edit({root}/docs/design/workflow-model.md)",
            f"Edit({root}/docs/workflow/engineering-principles.md)",
            f"Edit({root}/plans/*/state.yaml)",
            "Bash(heddle phase-exit*)",
            "Bash(heddle milestone advance*)",
            "Bash(heddle decisions resolve*)",
            "Bash(heddle flow set*)",
        }
    )


def _flag_present(argv: list[str], flag: str) -> bool:
    return flag in argv


def _flag_value(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        idx = argv.index(flag)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def _collect_flag_tokens(argv: list[str], flag: str) -> set[str]:
    """
    Collect every token after each occurrence of ``flag`` up to the next
        ``--option``, tolerating both comma-joined and space-separated encodings
        (foundation picks one; this test is the contract it satisfies).
    """
    tools: set[str] = set()
    i = 0
    while i < len(argv):
        if argv[i] == flag:
            j = i + 1
            while j < len(argv) and not argv[j].startswith("--"):
                tools.update(
                    part.strip() for part in argv[j].split(",") if part.strip()
                )
                j += 1
            i = j
        else:
            i += 1
    return tools


def _allowed_tools(argv: list[str]) -> set[str]:
    return _collect_flag_tokens(argv, "--allowedTools")


def _disallowed_tools(argv: list[str]) -> set[str]:
    """
    The effective deny set, parsed the same way as the allow set (over
        ``--disallowedTools``) rather than substring-matched over the joined argv —
        a deny rule that only appears inside the ``--append-system-prompt`` addendum
        text must NOT satisfy the superset check (review).
    """
    return _collect_flag_tokens(argv, "--disallowedTools")


def _assert_spawn_profile(
    argv: list[str], expected_allow: frozenset[str], label: str, host: Path
) -> None:
    assert _flag_present(argv, "--print"), f"FAIL AC-6: {label} argv must carry --print"
    assert _flag_value(argv, "--output-format") == "stream-json", (
        f"FAIL AC-6: {label} argv must carry --output-format stream-json"
    )
    assert _flag_present(argv, "--verbose"), (
        f"FAIL AC-6: {label} argv must carry --verbose — the real CLI "
        "hard-errors at startup on --print + stream-json without it "
        "(live-smoke finding 2026-07-05)"
    )
    assert _flag_value(argv, "--permission-mode") == "auto", (
        f"FAIL AC-6: {label} argv must carry --permission-mode auto"
    )
    from heddle.driver.sessions import PHASE_SESSION_MODEL

    assert _flag_value(argv, "--model") == PHASE_SESSION_MODEL, (
        f"FAIL AC-6: {label} argv must pin --model {PHASE_SESSION_MODEL} — the "
        "configured phase-session model (pre-m6b driver-config batch D-1/D-2: "
        "default claude-fable-5-1, overridable via HEDDLE_PHASE_SESSION_MODEL) — "
        "an unpinned session inherits the operator's ambient ~/.claude settings"
    )
    assert _flag_value(argv, "--effort") == "high", (
        f"FAIL AC-6: {label} argv must pin --effort high for Fable 5"
    )
    assert _flag_present(argv, "--append-system-prompt"), (
        f"FAIL AC-6: {label} argv must carry --append-system-prompt"
    )

    allow = _allowed_tools(argv)
    assert allow == expected_allow, (
        f"FAIL AC-6: {label} --allowedTools must set-equal the pinned table row.\n"
        f"  missing: {sorted(expected_allow - allow)}\n  "
        f"extra: {sorted(allow - expected_allow)}"
    )
    deny = _disallowed_tools(argv)
    expected_deny = expected_uniform_deny(host)
    assert expected_deny <= deny, (
        f"FAIL AC-6: {label} --disallowedTools missing {sorted(expected_deny - deny)}"
    )


def _arm(driver_corpus, host, **overrides):
    return driver_corpus.arm(host, SLUG, **overrides)


def _arm_at_stage(driver_corpus, host, stage: str, **overrides):
    return arm_at_stage(driver_corpus, host, SLUG, stage, **overrides)


# --------------------------------------------------------------------------- #
# AC-5 — the driver loop advances and terminates
# --------------------------------------------------------------------------- #
def test_ac05_drive_advances_one_clean_boundary_and_stops_at_until(
    run_json, driver_corpus, fake_claude, auto_tier2_workspace
):
    host = auto_tier2_workspace()  # stage=specify, clean boundary
    state_path = driver_corpus.state(host, SLUG)

    code, _envelope, _out, _err = run_json(
        ["drive", "--feature", SLUG, "--until", "spec-review", "--json"]
    )
    assert code == 0, f"FAIL AC-5: bounded drive must exit 0 cleanly, got {code}"
    after = driver_corpus.read_yaml(state_path)
    # The bounded-stop contract (review): `--until spec-review` advances exactly
    # one boundary (specify -> spec-review) and STOPS there. A driver that ignores
    # --until and runs past spec-review must fail this assertion.
    assert after["authorized_through"] == "spec-review", (
        "FAIL AC-5: a `--until spec-review` drive must stop exactly at the "
        "spec-review boundary (no advance past it), got "
        f"{after['authorized_through']!r}"
    )
    assert after["stage"] != "specify", (
        "FAIL AC-5: the driver must execute the front next_actions entry and "
        "advance past specify"
    )
    grants = after.get("authorizations", [])
    assert_has_grant(
        grants,
        source="policy",
        message=(
            "FAIL AC-5: each auto boundary the driver crosses records a "
            "source=policy grant"
        ),
    )


def test_ac05_until_behind_current_stage_is_rejected(
    run_json, driver_corpus, fake_claude, auto_tier2_workspace
):
    """
    review: an `--until` stage behind the current stage can never be reached,
        so the loop would run unbounded. The drive must reject it (exit 2, usage)
        and do no work rather than spin.
    """
    host = auto_tier2_workspace()
    state_path = driver_corpus.state(host, SLUG)
    # Advance one real boundary so the current stage (spec-review) is ahead of
    # specify.
    run_json(["drive", "--feature", SLUG, "--until", "spec-review", "--json"])
    before = driver_corpus.read_yaml(state_path)

    code, _envelope, _out, _err = run_json(
        ["drive", "--feature", SLUG, "--until", "specify", "--json"]
    )
    assert code == 2, (
        "FAIL bounded stage: an `--until` stage behind the current stage must exit 2 "
        f"(usage), got {code}"
    )
    after = driver_corpus.read_yaml(state_path)
    assert after == before, (
        "FAIL bounded stage: a rejected `--until` must do no work (state unchanged)"
    )


def test_ac15_until_reached_stop_is_quiet(
    run_json,
    driver_corpus,
    fake_claude,
    notify_capture,
    monkeypatch,
    auto_tier2_workspace,
):
    """
    review / REQ-13: a bounded `--until` stop is not a terminal event, so it
        must NOT fire the configured notify command (notify fires on pauses and on
        the terminal `complete` only).
    """
    host = auto_tier2_workspace(chdir=False)
    append_autopilot_notify_command(host, notify_capture.command)
    monkeypatch.chdir(host)

    code, _envelope, _out, _err = run_json(
        ["drive", "--feature", SLUG, "--until", "spec-review", "--json"]
    )
    assert code == 0, f"FAIL terminal completion: bounded drive must exit 0, got {code}"
    assert not notify_capture.received(), (
        "FAIL terminal completion: a bounded `--until` stop must not fire the notify "
        "command"
    )


def test_missing_command_drive_auto_without_test_command_halts_at_startup(
    run_json, driver_corpus, fake_claude, auto_tier2_workspace
):
    """
    review: a flow: auto feature with no autopilot.test_command must halt at
        drive startup with a structured config error — otherwise the completion-stage
        additional close gate (_auto_close_block) is a silent no-op and close trusts
        possibly-stale verify facts. Red before the startup check: drive proceeds and
        the gap is invisible.
    """
    host = auto_tier2_workspace()
    # Model an auto host that forgot to configure the close suite.
    mutate_heddle_config(
        host, lambda config: config["autopilot"].pop("test_command", None)
    )
    state_path = driver_corpus.state(host, SLUG)
    before = driver_corpus.read_yaml(state_path)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code != 0 and not envelope["ok"], (
        "FAIL timeout: drive must halt when a flow: auto feature has no "
        "autopilot.test_command"
    )
    rendered = json.dumps(envelope).lower()
    assert "test_command" in rendered, (
        f"FAIL timeout: the halt must name autopilot.test_command, got {envelope!r}"
    )
    assert "additional close suite" in rendered
    assert "clean-venv" not in rendered
    assert "clean environment" not in rendered
    after = driver_corpus.read_yaml(state_path)
    assert after == before, (
        "FAIL timeout: a startup config halt must do no work (state unchanged)"
    )


def test_ac05_driver_routes_kernel_next_action_prefixes(monkeypatch):
    """The driver retains typed kernel actions without classifying display text."""
    from types import SimpleNamespace

    from heddle.contracts import operations as ops
    from heddle.contracts.result import NextAction
    from heddle.driver import loop

    snapshot = SimpleNamespace(feature=SLUG, stage="implement")
    monkeypatch.setattr(loop, "first_actionable_blocker", lambda _snap: None)
    actions = (
        ops.SessionAction(SLUG, "stage-work"),
        ops.SessionAction(SLUG, "task-expansion"),
        ops.CommandAction(ops.RunGate("code-quality", feature=SLUG)),
        ops.CommandAction(ops.PhaseExit(feature=SLUG)),
        ops.CommandAction(ops.MilestoneAdvance(feature=SLUG)),
        ops.CommandAction(ops.TaskAdd("Expand tasks", feature=SLUG)),
        ops.CommandAction(ops.TaskDone("m1", "t1", feature=SLUG)),
        ops.CommandAction(ops.Verify("m1", feature=SLUG)),
        ops.ManualAction("Edit the ratified document"),
    )
    for action in actions:
        projected = NextAction(action, "arbitrary presentation text")
        monkeypatch.setattr(
            loop,
            "assess_current_readiness",
            lambda _config, _snap, *, purpose, value=projected: SimpleNamespace(
                next_actions=(value,), blockers=(), original_obligations=()
            ),
        )
        selected, blocker = loop._next_command(None, snapshot, None)
        assert selected is projected and blocker is None


def test_structural_driver_classifies_blockers_structurally_not_from_prose():
    """A typed blocker controls the error code independently of reason wording."""
    from heddle.contracts import operations as ops
    from heddle.contracts.result import NextAction
    from heddle.driver.loop import _blocked

    reworded = "please unblock: a fully reworded reason with no known prefix"
    action = NextAction(ops.CommandAction(ops.DecisionsList(feature=SLUG)), reworded)
    result = _blocked(action, "pending-decisions")
    assert result.error is not None
    assert result.error.code == "pending-decisions"
    assert result.next_actions[0] is action


def test_structural_no_prose_blocker_parsing_in_driver():
    """No driver module parses the kernel's blocked-row prose into a code."""
    import heddle.driver as driver_pkg

    driver_dir = Path(driver_pkg.__file__).resolve().parent
    offenders = [
        module.name
        for module in sorted(driver_dir.glob("*.py"))
        if 'startswith("resolve blocking condition' in module.read_text()
        or 'removeprefix("resolve blocking condition' in module.read_text()
    ]
    assert not offenders, (
        "FAIL structured blockers: reason prose must be display-only in the driver; "
        f"prose-parsing found in: {offenders}"
    )


# --------------------------------------------------------------------------- #
# AC-6 — phase-session spawn profile (set-equal allow, superset deny)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "stage",
    tuple(EXPECTED_COMMAND_SUFFIXES),
)
def test_ac06_stage_permission_profile_table_is_locked(stage, auto_tier2_workspace):
    """Every spawnable stage receives its exact orchestration capabilities."""
    from heddle.driver.sessions import allowed_tools_for_stage

    host = auto_tier2_workspace(mutate=make_first_milestone_current_and_taskless)
    inputs = phase_permission_inputs(host, SLUG, stage)
    assert set(allowed_tools_for_stage(*inputs)) == expected_allow_by_stage(host)[stage]


def test_ac06_phase_session_spawn_profile(
    run_cli, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(
        chdir=False, mutate=make_first_milestone_current_and_taskless
    )
    # Arm an implement-stage session: the next action is within-milestone work,
    # so the driver spawns an implement-category phase session.
    _arm_at_stage(driver_corpus, host, "implement")
    monkeypatch.chdir(host)

    run_cli(["drive", "--feature", SLUG, "--until", "peer-review", "--json"])
    calls = fake_claude.calls()
    assert calls, "FAIL AC-6: the driver must spawn a headless claude phase session"
    argv = calls[0]["argv"]

    _assert_spawn_profile(
        argv, expected_allow_by_stage(host)["implement"], "implement-phase", host
    )


def test_ac06_non_implement_session_spawn_profile_is_set_equal(
    run_cli, driver_corpus, fake_claude, auto_tier2_workspace
):
    """
    AC-6 (per-category set-equality, review): the spawn profile is built PER
        phase category, so a non-implement (specify / Doc-spec) phase session's
        allowlist must SET-EQUAL its table row — not merely include the read baseline
        and exclude implement tools. The stage=specify fixture spawns a specify-phase
        session; its `--allowedTools` must equal the specify row exactly (so an
        extra non-implement tool is also caught), and its `--disallowedTools` must be a
        superset of the uniform deny. Without exactness, an implementation that applied
        one over-permissive profile to every category would still pass.
    """
    host = auto_tier2_workspace()  # stage=specify

    run_cli(["drive", "--feature", SLUG, "--until", "spec-review", "--json"])
    calls = fake_claude.calls()
    assert calls, "FAIL AC-6: the driver must spawn a specify-phase session"
    argv = calls[0]["argv"]
    _assert_spawn_profile(
        argv, expected_allow_by_stage(host)["specify"], "specify-phase", host
    )
    allow = _allowed_tools(argv)
    # Belt-and-suspenders against the specific over-permissive regression: none of
    # the implement-only write tools may leak into a Doc/spec session.
    expected = expected_allow_by_stage(host)
    leaked = allow & (expected["implement"] - expected["specify"])
    assert not leaked, (
        "FAIL AC-6: a non-implement (specify) phase session must NOT carry the "
        f"implement category's write tools; leaked: {sorted(leaked)}"
    )


def test_r4_session_prompt_includes_stage_briefing(
    run_cli, run_json, fake_claude, auto_tier2_workspace
):
    """R4(2): the work prompt is routed through the kickoff briefing bridge — the
    spawned session's stdin must carry the rendered <stage>.briefing.md, not just
    the minimal Feature/Stage/Next-action header."""
    auto_tier2_workspace()  # stage=specify

    # The briefing the kickoff bridge renders for the current (specify) stage.
    code, envelope, _out, _err = run_json(["kickoff", "--feature", SLUG, "--json"])
    assert code == 0, f"precondition: kickoff must render the briefing, got {code}"
    briefing = envelope["data"]["briefing"].strip()
    assert briefing, "precondition: the specify briefing must be non-empty"

    run_cli(["drive", "--feature", SLUG, "--until", "spec-review", "--json"])
    calls = fake_claude.calls()
    assert calls, "FAIL R4(2): the driver must spawn a phase session"
    stdin = calls[0].get("stdin", "")
    assert briefing in stdin, (
        "FAIL R4(2): the session prompt must include the rendered stage briefing "
        "(kickoff bridge), but the briefing text was absent from the session stdin"
    )


# Representative non-authoring stage spawns. The table-level test above covers
# every stage; these cases prove the selected profile reaches the real argv.
_AC06_REMAINING_STAGES = [
    pytest.param(
        "spec-review",
        "plan-review",
        id="spec-review",
    ),
    pytest.param(
        "scaffold",
        "implement",
        id="scaffold",
    ),
    pytest.param(
        "peer-review",
        "complete",
        id="peer-review",
    ),
]


@pytest.mark.parametrize("stage, until", _AC06_REMAINING_STAGES)
def test_ac06_gate_and_close_categories_spawn_profile_set_equal(
    run_cli,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
    stage,
    until,
):
    """Selected real spawns carry the exact stage-specific allowlist."""
    host = auto_tier2_workspace(chdir=False)
    _arm_at_stage(driver_corpus, host, stage)
    monkeypatch.chdir(host)

    run_cli(["drive", "--feature", SLUG, "--until", until, "--json"])
    calls = fake_claude.calls()
    assert calls, "FAIL AC-6: the driver must spawn a headless claude phase session"
    argv = calls[0]["argv"]

    _assert_spawn_profile(argv, expected_allow_by_stage(host)[stage], stage, host)


# --------------------------------------------------------------------------- #
# AC-7 (below-cap branch) — deterministic exit validation re-attempts, never blind
# --------------------------------------------------------------------------- #
def test_ac07_below_cap_revalidates_and_reattempts_without_grant(
    run_cli, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_at_stage(driver_corpus, host, "implement")
    monkeypatch.chdir(host)

    # The fake claude writes no artifact, so deterministic exit validation fails.
    # With the spec cap at five recorded sessions, this run reaches the fifth
    # validating session and then halts; sessions two through five are the
    # below-cap reattempts with the validation problem folded into kickoff.
    run_cli(["drive", "--feature", SLUG, "--until", "peer-review", "--json"])
    calls = fake_claude.calls()
    assert len(calls) == 5, (
        "FAIL AC-7: validation must re-attempt until the recorded-session "
        f"attempt cap of five, got {len(calls)}"
    )
    # review: "attempts are never blind" (AC-7) — the reattempt kickoff must FOLD
    # the prior validation failure into its prompt, so the second session's stdin
    # (the kickoff prompt) must differ from the first's. A driver that replays the
    # identical kickoff (blind retry) would have stdin[1] == stdin[0] and fail here.
    first_stdin = calls[0].get("stdin", "")
    second_stdin = calls[1].get("stdin", "")
    assert second_stdin and second_stdin != first_stdin, (
        "FAIL AC-7 (below-cap): the reattempt must not be blind — the second "
        "kickoff must fold in the prior validation failure (its prompt must differ "
        "from the first), but the two kickoffs were identical"
    )
    after = driver_corpus.read_yaml(state_path)
    grants = after.get("authorizations", [])
    assert_no_grant(
        grants,
        source="policy",
        message="FAIL AC-7 (below-cap): a failed exit validation must record NO grant",
    )


def _state_mutating_claude(bin_dir: Path) -> Path:
    fake = bin_dir / "claude"
    return write_executable_script(
        fake,
        "#!/bin/sh\n"
        'if [ "$1" = "--help" ]; then\n'
        "  echo 'usage: claude --print --output-format stream-json "
        "--verbose --permission-mode auto --model --effort "
        "--append-system-prompt --allowedTools "
        "--disallowedTools --max-turns --max-budget-usd'\n"
        "  exit 0\n"
        "fi\n"
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "import re\n"
        "path = Path('plans/autopilot-demo/state.yaml')\n"
        "text = path.read_text(encoding='utf-8')\n"
        "def repl(match):\n"
        "    return f'revision: {int(match.group(1)) + 1}'\n"
        "path.write_text(\n"
        "    re.sub(r'^revision: (\\d+)$', repl, text, count=1, flags=re.M),\n"
        "    encoding='utf-8',\n"
        ")\n"
        "PY\n"
        'printf \'%s\\n\' \'{"type":"result","subtype":"success",'
        '"is_error":false,"result":"ok"}\'\n',
        mode=0o755,
    )


def test_ac07_session_progress_markers_are_computed_from_pre_post_state(
    run_cli, driver_corpus, tmp_path, monkeypatch, auto_tier2_workspace, fake_claude
):
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_at_stage(driver_corpus, host, "implement")
    before_revision = driver_corpus.read_yaml(state_path)["revision"]
    bin_dir = tmp_path / "mutating-bin"
    bin_dir.mkdir()
    _state_mutating_claude(bin_dir)
    # The mutating claude shadows the fixture's fake; the fixture still supplies
    # the codex presence double the implement-stage capability probe requires.
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.chdir(host)

    run_cli(["drive", "--feature", SLUG, "--until", "peer-review", "--json"])

    after = driver_corpus.read_yaml(state_path)
    session = after["sessions"][0]
    assert session["revision_before"] == before_revision, (
        "FAIL AC-7: session progress markers must record the pre-session revision"
    )
    assert session["revision_after"] == before_revision + 1, (
        "FAIL AC-7: session progress markers must record the post-session revision"
    )
    assert session["advanced"] is True, (
        "FAIL AC-7: session progress markers must compute advanced=true when "
        "phase work changes state"
    )


# Per-validator rejection examples (Feature Spec AC-7): each distinct validator,
# armed at its stage, must reject and record NO grant. One generic case would not
# prove the per-stage validators each gate. P6 (implement, Session-Log presence)
# spawns a phase session whose output the validator then rejects. P2 (specify,
# AC-ID uniqueness) has its own dedicated test below (it must seed a real
# duplicate-AC spec to exercise the uniqueness validator specifically, review).
# P9 (complete, non-green clean-venv suite) is the complete-boundary close
# validator (completion test_ac07_p9_… + AC-17).
_VALIDATOR_REJECTIONS = [
    pytest.param("implement", id="P6-implement-missing-session-log"),
]


@pytest.mark.parametrize("stage", _VALIDATOR_REJECTIONS)
def test_ac07_validator_rejection_records_no_grant(
    run_cli, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace, stage
):
    """AC-7 (per-validator): a phase session that leaves the stage's required
    artifact invalid is rejected by that stage's validator — the session IS
    spawned (so the validator runs) but NO grant is recorded. Asserting the spawn
    keeps this non-tautological: a "no grant" check alone would pass vacuously
    before the driver exists."""
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_at_stage(driver_corpus, host, stage)
    monkeypatch.chdir(host)

    run_cli(["drive", "--feature", SLUG, "--json"])
    assert fake_claude.calls(), (
        f"FAIL AC-7 ({stage}): the driver must spawn a phase session for the "
        f"{stage} validator to reject"
    )
    after = driver_corpus.read_yaml(state_path)
    grants = after.get("authorizations", [])
    assert_no_grant(
        grants,
        source="policy",
        message=(
            f"FAIL AC-7 ({stage}): a failed {stage} validator must record NO "
            "source=policy grant"
        ),
    )


# The fixture's feature spec the specify-stage validator reads (the no-op session
# double writes nothing, so a pre-seeded spec is exactly what the validator sees).
_FIXTURE_SPEC_RELPATH = AUTO_TIER2_SPEC

# A spec carrying DUPLICATE AC IDs (two `### AC-1:` blocks) — the P2 validator's
# specific trigger (AC-ID uniqueness), distinct from a missing/empty artifact.
_DUPLICATE_AC_SPEC = """\
# Autopilot Demo (P2 duplicate-AC fixture)

## Acceptance Criteria

### AC-1: First criterion
Priority: MUST
Expected: something observable.

### AC-1: Duplicate id (must be rejected)
Priority: MUST
Expected: a second block reusing AC-1 — violates AC-ID uniqueness.
"""


def test_ac07_p2_specify_duplicate_ac_ids_rejected(
    run_cli,
    envelope_tools,
    driver_corpus,
    fake_claude,
    monkeypatch,
    auto_tier2_workspace,
):
    """
    AC-7 (P2, review): the specify-stage validator must reject a spec with
        DUPLICATE AC IDs specifically (not merely a missing artifact). Seed a spec with
        two `### AC-1:` blocks; the no-op session double leaves it intact, so the P2
        validator sees the duplicates, withholds the grant, and the surfaced rejection
        reason names AC-ID uniqueness. A driver whose only specify validator is
        "artifact present" would surface no uniqueness reason and fail this test.
    """
    host = auto_tier2_workspace(chdir=False)
    (host / _FIXTURE_SPEC_RELPATH).write_text(_DUPLICATE_AC_SPEC, encoding="utf-8")
    state_path = _arm_at_stage(driver_corpus, host, "specify")
    monkeypatch.chdir(host)

    _code, out, _err = run_cli(["drive", "--feature", SLUG, "--json"])
    envelope = envelope_tools.parse(out)
    assert fake_claude.calls(), (
        "FAIL AC-7 (P2): the driver must spawn a specify session for the AC-ID "
        "uniqueness validator to reject"
    )
    after = driver_corpus.read_yaml(state_path)
    grants = after.get("authorizations", [])
    assert_no_grant(
        grants,
        source="policy",
        message=(
            "FAIL AC-7 (P2): a duplicate-AC-ID spec must withhold the grant (no "
            "source=policy)"
        ),
    )
    # The rejection reason must identify AC-ID uniqueness specifically — searched
    # across the whole envelope so wherever foundation surfaces it (next_actions reason,
    # diagnostics, decision context) is accepted, but a generic missing-artifact
    # rejection (no uniqueness wording) is not.
    blob = json.dumps(envelope).lower()
    assert any(token in blob for token in ("duplicate", "unique", "ac-id", "ac id")), (
        "FAIL AC-7 (P2): the rejection must name AC-ID uniqueness (duplicate / "
        f"unique / AC-ID), got envelope {envelope!r}"
    )


# --------------------------------------------------------------------------- #
# AC-22 — hard failure halts with a structured error and notifies
# --------------------------------------------------------------------------- #
def _marker_notify_command(script_dir: Path, marker: Path) -> str:
    """A PATH-INDEPENDENT notify command: an absolute `#!/bin/sh` script using only
    shell builtins, so it fires even when PATH is narrowed to exclude the real
    toolchain (the missing-binary fixture — where the python3-based
    ``notify_capture`` could not run). It records its invocation by creating
    ``marker`` and drains stdin with a builtin loop so the driver's fire-and-forget
    write never blocks. Returns the notify_command string (the script's abs path)."""
    script = script_dir / "notify-marker"
    write_executable_script(
        script,
        f'#!/bin/sh\n: > "{marker}"\nwhile IFS= read -r _; do :; done\n',
        mode=0o755,
    )
    return str(script)


def test_ac22_missing_claude_binary_halts_structured(
    run_json, driver_corpus, tmp_path, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    # AC-22 notify must fire on THIS hard-failure class too (review); a
    # PATH-independent marker is required since PATH carries no python3 here.
    marker = tmp_path / "notify-missingbin.fired"
    append_autopilot_notify_command(host, _marker_notify_command(tmp_path, marker))
    monkeypatch.setenv("PATH", str(empty_bin))  # no `claude` on PATH
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 3 and not envelope["ok"], (
        f"FAIL AC-22: a missing claude binary must halt with a structured error "
        f"(exit 3), got {code}"
    )
    assert marker.exists(), (
        "FAIL AC-22: a missing-binary hard failure must fire the configured notify"
    )
    after = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    assert_no_grant(
        after.get("authorizations", []),
        source="policy",
        message="FAIL AC-22: a hard failure must record no source=policy grant",
    )


def _degraded_claude(bin_dir: Path, spawn_log: Path) -> Path:
    """
    A `claude` with NO --permission-mode support: its --help omits the flag
        AND its parser rejects unadvertised flags with `unknown option` (mirroring
        commander; since the 2026-07-05 probe fix a help-text miss alone is not a
        rejection — the parse-acceptance fallback must also fail). It is a POSIX-sh
        script (PATH-independent via the absolute /bin/sh) so it runs even when
        PATH is narrowed, and it TOUCHES ``spawn_log`` whenever it is invoked AS A
        PHASE SESSION — i.e. with the ``--append-system-prompt`` autonomy addendum
        (AC-6's unambiguous phase-session signature). The probe checks capability
        without that flag, so the sentinel materializes only if the driver wrongly
        spawned a session — making ``assert not spawn_log.exists()`` a reachable,
        discriminating check (review).
    """
    fake = bin_dir / "claude"
    return write_executable_script(
        fake,
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        f'  if [ "$a" = "--append-system-prompt" ]; then : > "{spawn_log}"; fi\n'
        "done\n"
        'if [ "$1" = "--help" ]; then\n'
        "  echo 'usage: claude --print --output-format stream-json'\n"
        "  exit 0\n"
        "fi\n"
        "echo \"error: unknown option '$1'\" >&2\n"
        "exit 1\n",
        mode=0o755,
    )


_CLAUDE_HELP_TOKENS = (
    "--print",
    "--output-format",
    "stream-json",
    "--verbose",
    "--permission-mode",
    "--model",
    "--effort",
    "--append-system-prompt",
    "--allowedTools",
    "--disallowedTools",
    "--max-turns",
    "--max-budget-usd",
)


def _help_only_claude(
    bin_dir: Path, help_text: str, *, parse_accepts_hidden: bool = False
) -> Path:
    """A fake `claude` that serves ``help_text`` for --help. For any other
    invocation (the probe's parse-acceptance fallback) it either rejects at
    parse like commander does for an unregistered flag (`unknown option`,
    the default) or accepts and fails at the local no-input gate like a
    registered-but-hidden flag (``parse_accepts_hidden=True`` — the CLI
    2.1.201 `--max-turns` shape the 2026-07-05 live smoke caught)."""
    fake = bin_dir / "claude"
    if parse_accepts_hidden:
        non_help = (
            "  echo 'Error: Input must be provided either through stdin or"
            " as a prompt argument when using --print' >&2\n  exit 1\n"
        )
    else:
        non_help = "  echo \"error: unknown option '$1'\" >&2\n  exit 1\n"
    return write_executable_script(
        fake,
        "#!/bin/sh\n"
        'if [ "$1" = "--help" ]; then\n'
        f"  printf '%s\\n' {help_text!r}\n"
        "  exit 0\n"
        "fi\n"
        f"{non_help}",
        mode=0o755,
    )


@pytest.mark.parametrize(
    "missing_token, expected_fragment",
    [
        ("--print", "print"),
        ("--output-format", "output-format"),
        ("stream-json", "stream-json"),
        ("--verbose", "verbose"),
        ("--permission-mode", "permission-mode"),
        ("--model", "model"),
        ("--effort", "effort"),
        ("--append-system-prompt", "append-system-prompt"),
        ("--allowedTools", "allowedTools"),
        ("--disallowedTools", "disallowedTools"),
        ("--max-turns", "max-turns"),
        ("--max-budget-usd", "max-budget-usd"),
    ],
)
def test_ac22_capability_probe_names_each_required_claude_capability(
    tmp_path, monkeypatch, missing_token, expected_fragment
):
    """AC-22 / enforcement: the startup probe covers the full Claude flag surface."""
    from heddle.driver.sessions import probe_claude_capabilities

    bin_dir = tmp_path / "probe-bin"
    bin_dir.mkdir()
    help_text = "usage: claude " + " ".join(
        token for token in _CLAUDE_HELP_TOKENS if token != missing_token
    )
    _help_only_claude(bin_dir, help_text)
    monkeypatch.setenv("PATH", str(bin_dir))

    missing = probe_claude_capabilities()
    assert missing is not None
    assert expected_fragment in missing, (
        "FAIL AC-22: missing capability message must name "
        f"{expected_fragment!r}, got {missing!r}"
    )


def test_ac22_probe_accepts_flag_hidden_from_help_but_parsed(tmp_path, monkeypatch):
    """Live-smoke regression (2026-07-05): CLI 2.1.201 hides `--max-turns`
    from `--help` while still accepting it at parse. A help-text-only probe
    false-negatives and halts a working configuration; the probe must fall
    back to parse-acceptance and treat the hidden-but-registered flag as
    supported."""
    from heddle.driver.sessions import probe_claude_capabilities

    bin_dir = tmp_path / "probe-bin"
    bin_dir.mkdir()
    help_text = "usage: claude " + " ".join(
        token for token in _CLAUDE_HELP_TOKENS if token != "--max-turns"
    )
    _help_only_claude(bin_dir, help_text, parse_accepts_hidden=True)
    monkeypatch.setenv("PATH", str(bin_dir))

    missing = probe_claude_capabilities()
    assert missing is None, (
        "FAIL AC-22 regression: a flag hidden from --help but accepted by "
        "the parser is a supported capability; the probe must not report "
        f"it missing, got {missing!r}"
    )


def test_ac22_capability_probe_halts_before_first_spawn_naming_capability(
    run_cli, envelope_tools, driver_corpus, tmp_path, monkeypatch, auto_tier2_workspace
):
    """Fixture 3: a `claude` present but missing a required capability halts
    BEFORE the first phase session, the envelope naming the missing capability."""
    host = auto_tier2_workspace(chdir=False)
    bin_dir = tmp_path / "degraded-bin"
    bin_dir.mkdir()
    spawn_log = tmp_path / "spawned.flag"
    _degraded_claude(bin_dir, spawn_log)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["drive", "--feature", SLUG, "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 3 and not envelope["ok"], (
        f"FAIL AC-22: a missing capability must halt the run (exit 3), got {code}"
    )
    message = (envelope.get("error") or {}).get("message", "") + (
        envelope.get("error") or {}
    ).get("hint", "")
    assert "permission-mode" in message or "capability" in message.lower(), (
        "FAIL AC-22: the hard-failure envelope must name the missing capability"
    )
    # Reachable now: the degraded claude touches spawn_log on any --append-system-
    # prompt (phase-session) invocation, so this fails if the driver spawned.
    assert not spawn_log.exists(), (
        "FAIL AC-22: the probe must halt BEFORE the first phase session is spawned"
    )
    after = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    assert_no_grant(
        after.get("authorizations", []),
        source="policy",
        message=(
            "FAIL AC-22: a capability-probe halt must record no source=policy grant"
        ),
    )


def test_ac22_capability_probe_halts_before_first_write_action(
    run_cli, envelope_tools, driver_corpus, tmp_path, monkeypatch, auto_tier2_workspace
):
    """AC-22 enforcement: capability probing precedes driver-executed writes, not
    only phase-session spawns."""
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm_at_stage(
        driver_corpus,
        host,
        "spec-review",
        sessions=[
            {
                "started_at": "2026-06-16T09:00Z",
                "ended_at": "2026-06-16T09:01Z",
                "completed": "phase session",
                "started": "",
                "key_context": "",
                "next_steps": "",
                "blockers": "",
                "stage": "spec-review",
                "revision_before": 3,
                "revision_after": 3,
                "advanced": False,
            }
        ],
    )
    before = driver_corpus.read_yaml(state_path)
    bin_dir = tmp_path / "degraded-bin"
    bin_dir.mkdir()
    _degraded_claude(bin_dir, tmp_path / "spawned.flag")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["drive", "--feature", SLUG, "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 3 and not envelope["ok"], (
        "FAIL AC-22: a missing capability must halt before driver writes"
    )
    after = driver_corpus.read_yaml(state_path)
    assert after == before, (
        "FAIL AC-22: capability probing must happen before the ready phase-exit "
        "write mutates state"
    )


def test_ac22_workspace_invalid_halts_structured(
    run_json, driver_corpus, fake_claude, tmp_path, monkeypatch, auto_tier2_workspace
):
    """
    Fixture 2: a workspace-invalid step (an unparseable state.yaml) halts with
        a structured error envelope and records no grant — it does not silently
        retry. The fake claude is on PATH; the halt must precede any spawn. AC-22's
        notify clause applies to this hard-failure class too (review).
    """
    host = auto_tier2_workspace(chdir=False)
    marker = tmp_path / "notify-wsinvalid.fired"
    append_autopilot_notify_command(host, _marker_notify_command(tmp_path, marker))
    state_path = driver_corpus.state(host, SLUG)
    state_path.write_text("stage: [unterminated\n  bad: : yaml\n", encoding="utf-8")
    corrupt_before = state_path.read_bytes()
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 3 and not envelope["ok"], (
        "FAIL AC-22: a workspace-invalid step must halt structured "
        f"(exit 3), got {code}"
    )
    assert fake_claude.calls() == [], (
        "FAIL AC-22: a workspace-invalid halt must precede any phase-session spawn"
    )
    assert marker.exists(), (
        "FAIL AC-22: a workspace-invalid hard failure must fire the configured notify"
    )
    assert state_path.read_bytes() == corrupt_before, (
        "FAIL AC-22: a hard failure must record no grant — the invalid state.yaml "
        "must be left byte-for-byte unchanged"
    )


def test_ac22_hard_failure_fires_notify(
    run_cli,
    envelope_tools,
    driver_corpus,
    notify_capture,
    tmp_path,
    monkeypatch,
    auto_tier2_workspace,
):
    """AC-22: a hard failure fires the configured notify command. PATH prepends
    the degraded-claude bin (so the probe rejects it) while preserving the real
    PATH (the notify capture script needs its interpreter)."""
    host = auto_tier2_workspace(chdir=False)
    bin_dir = tmp_path / "degraded-bin"
    bin_dir.mkdir()
    _degraded_claude(bin_dir, tmp_path / "spawned.flag")
    append_autopilot_notify_command(host, notify_capture.command)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["drive", "--feature", SLUG, "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 3 and not envelope["ok"], (
        f"FAIL AC-22: a missing-capability hard failure must halt (exit 3), got {code}"
    )
    assert notify_capture.received(), (
        "FAIL AC-22: a hard failure must fire the configured notify command"
    )
    after = driver_corpus.read_yaml(driver_corpus.state(host, SLUG))
    assert_no_grant(
        after.get("authorizations", []),
        source="policy",
        message="FAIL AC-22: a hard failure must record no source=policy grant",
    )


# --------------------------------------------------------------------------- #
# AC-23 — CAS conflict is retried, not escalated
# --------------------------------------------------------------------------- #
def test_ac23_cas_conflict_retried_not_escalated(
    run_json, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    auto_tier2_workspace()

    # Force the first state write to raise a CAS Conflict (exit 5), then succeed
    # — the write forced-conflict idiom. The driver must re-resolve and retry, never
    # surface the conflict as an escalation/decision batch.
    from heddle.runtime import write_path as _wp

    real_commit = _wp.commit_state
    state = {"calls": 0}

    def conflict_once(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            from heddle.runtime.write_path import Conflict

            # Positional message — `Conflict` is a bare Exception subclass with no
            # custom __init__, so kwargs (expected=/found=) would raise TypeError,
            # which the driver's `except Conflict:` could not catch (review).
            raise Conflict("forced AC-23 conflict: on-disk revision moved")
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(_wp, "commit_state", conflict_once)

    code, envelope, _out, _err = run_json(
        ["drive", "--feature", SLUG, "--until", "spec-review", "--json"]
    )
    assert code == 0, (
        f"FAIL AC-23: a single exit-5 conflict must be retried and the run "
        f"proceed (exit 0), got {code}"
    )
    # The retry actually happened: commit_state was re-entered after the
    # forced Conflict (CON-6 — proves the retry path executed, not just exit 0).
    assert state["calls"] >= 2, (
        "FAIL AC-23: the driver must re-resolve and re-attempt the write after a "
        f"CAS conflict (>=2 commit_state calls), got {state['calls']}"
    )
    next_actions = envelope.get("next_actions", [])
    assert not any(
        "decision" in (na.get("reason", "") + na.get("command", "")).lower()
        for na in next_actions
    ), "FAIL AC-23: a CAS conflict must not be surfaced as an escalation"


# --------------------------------------------------------------------------- #
# AC-24 — drive respects flow posture (HITL gate; Tier 1 pre-grant)
# --------------------------------------------------------------------------- #
def test_ac24_hitl_drive_stops_at_first_boundary_no_policy_grant(
    run_json, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    host = auto_tier2_workspace(chdir=False)
    state_path = _arm(driver_corpus, host, flow="hitl")
    before = driver_corpus.read_yaml(state_path)
    monkeypatch.chdir(host)

    code, envelope, out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 1, (
        "FAIL AC-24: a flow=hitl drive must stop blocked at the first boundary "
        f"(exit 1, blocked/escalated), got exit {code}"
    )
    after = driver_corpus.read_yaml(state_path)
    grants = after.get("authorizations", [])
    assert_no_grant(
        grants,
        source="policy",
        message="FAIL AC-24: a flow=hitl drive must record no source=policy grant",
    )
    assert after["stage"] == before["stage"] and (
        after["authorized_through"] == before["authorized_through"]
    ), (
        "FAIL AC-24: a flow=hitl drive blocked at the boundary must leave "
        "stage/authorized_through unchanged (it does not cross the boundary)"
    )
    blob = out.lower()
    assert "awaiting-human-authorization" in blob, (
        "FAIL AC-24: a flow=hitl drive must stop blocked on "
        "awaiting-human-authorization (the supervised posture)"
    )


def test_ac24_hitl_blocked_action_carries_the_work_projection(
    run_json, driver_corpus, fake_claude, monkeypatch, auto_tier2_workspace
):
    """
    adoption steering fix (2026-07-22): the supervised pause is unchanged
        (AC-24 above), but the blocked envelope's next action now carries the
        kernel WORK projection — at a fresh stage that is the kickoff briefing,
        never the bare `heddle phase-exit` row that shadowed all stage
        steering in HITL flow.
    """
    host = auto_tier2_workspace(chdir=False)
    _arm(driver_corpus, host, flow="hitl")
    monkeypatch.chdir(host)

    code, envelope, _out, _err = run_json(["drive", "--feature", SLUG, "--json"])
    assert code == 1, f"FAIL: the supervised pause must hold, got exit {code}"
    actions = envelope.get("next_actions", [])
    assert actions and actions[0]["command"] == f"heddle kickoff --feature {SLUG}", (
        "FAIL M8A steering: the blocked action must project the stage's "
        f"work (kickoff at a fresh stage), got {actions!r}"
    )


@pytest.mark.e2e
def test_ac24_tier1_pregrant_drives_to_complete(
    run_cli, envelope_tools, driver_corpus, fake_claude, monkeypatch
):
    """
    GREEN-PATH PREREQUISITE (review / CON-2, resolved 2026-06-16 = defer +
        docstring note): like the AC-9 acceptance test, this drives to `complete`
        with the content-aware session double that advances milestone state and
        converges enabled gates. A canned "ok" cannot reach `complete`.

        completion (kickoff-guidance-integration core): `complete` is the all-tier HITL
        completion — the unattended run now ends AT the pending handoff, not at a
        terminal success.
    """
    host = driver_corpus.copy("tier1-pregrant")
    state_path = host / "plans" / "tier1-quickfix" / "state.yaml"
    from tests.runtime.write_path_helpers import authored_boundary_verifications

    authored_boundary_verifications(state_path)
    state = driver_corpus.read_yaml(state_path)
    state["commands"]["test_command"] = "python3 -c 'print(1)'"
    driver_corpus.write_yaml(state_path, state)

    fake_claude.enable_autopilot_completion()
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["drive", "--feature", "tier1-quickfix", "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 1 and envelope["error"]["code"] == "awaiting-human-completion", (
        f"FAIL AC-24: a Tier 1 pre-granted feature must drive unattended to "
        "the completion stage handoff (complete is HITL for every tier), "
        f"exit {code}: {envelope.get('error')}"
    )
    after = driver_corpus.read_yaml(state_path)
    assert after["stage"] == "complete", (
        "FAIL AC-24: the driver must run the pre-granted Tier 1 feature to complete "
        "without the auto-flow preconditions"
    )


# --------------------------------------------------------------------------- #
# completion (kickoff-guidance-integration) — complete has no executor (gate)
# --------------------------------------------------------------------------- #
# Red discriminators for AC-6 (profile half) + review: the `close` category
# and the complete -> close mapping disappear; complete-stage profile
# construction hard-refuses; robustness loses the completion artifact writers.
class TestCompletionCompleteProfileRefusal:
    def test_completion_complete_profile_construction_raises_value_error(
        self, auto_tier2_workspace
    ):
        """
        AC-6/review: `allowed_tools_for_stage("complete", ...)` must raise
                ValueError — a returned profile (todays `close` category, or the
                implement fallback after a bare mapping deletion) would make a
                complete-stage session representable.
        """
        from heddle.driver.sessions import allowed_tools_for_stage

        host = auto_tier2_workspace()
        with pytest.raises(ValueError):
            allowed_tools_for_stage(*phase_permission_inputs(host, SLUG, "complete"))

    def test_completion_robustness_profile_drops_close_writers(
        self, auto_tier2_workspace
    ):
        """AC-6: robustness is robustness only — run-gate on top of the read
        baseline, with NO friction-retrospective/decision-journal writers
        (completion owns its own artifacts under completion)."""
        from heddle.driver.sessions import allowed_tools_for_stage

        host = auto_tier2_workspace()
        assert (
            set(
                allowed_tools_for_stage(
                    *phase_permission_inputs(host, SLUG, "robustness")
                )
            )
            == expected_allow_by_stage(host)["robustness"]
        )

    def test_completion_survivor_unknown_stage_fallback_policy_unchanged(
        self, auto_tier2_workspace
    ):
        """
        Survivor pin: the complete refusal must not alter the existing
                fallback policy for any OTHER unknown stage value (spec, Driver
                ordering and permissions). Green from scaffold time BY DESIGN; at
                risk because gate rewrites the category mapping.
        """
        from heddle.driver.sessions import allowed_tools_for_stage

        host = auto_tier2_workspace(mutate=make_first_milestone_current_and_taskless)
        assert (
            set(
                allowed_tools_for_stage(
                    *phase_permission_inputs(host, SLUG, "not-a-stage")
                )
            )
            == expected_allow_by_stage(host)["implement"]
        )
