"""Kickoff briefing resolution and read-only behavior."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"
FRESH_SLUG = "tier1-quickfix"


def _kickoff_live() -> bool:
    """
    True once kickoff stops returning the command availability not-implemented stub.
    """
    from heddle.cli import main

    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(["kickoff", "--json"])
    except SystemExit:
        pass
    except Exception:
        return True  # raised past the stub → no longer the stub
    try:
        envelope, _ = json.JSONDecoder().raw_decode(out.getvalue().strip())
    except (ValueError, json.JSONDecodeError):
        return True  # not the clean stub envelope → treat as landed
    return (envelope.get("error") or {}).get("code") != "not-implemented"


# Feature landed: a regression that reverted kickoff to the command availability stub
# must FAIL
# here, not silently skip the module (review integration; plan write T5 — retire
# red-phase skip sentinels to hard asserts at completion).
assert _kickoff_live(), (
    "kickoff must render instead of returning the command-availability stub — a "
    "regression "
    "here must fail loudly, not skip this module"
)


def _fresh_host(tmp_path: Path) -> Path:
    host = tmp_path / "host"
    shutil.copytree(GOLDEN, host)
    return host


def test_ac12_kickoff_renders_current_stage_briefing(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    # AC-12 (all three Expected clauses, owned by this Verified-by test): the
    # current stage's briefing renders (resolved via the resource precedence chain),
    # state.yaml
    # is byte-identical before/after, and a second invocation is identical.
    host = _fresh_host(tmp_path)
    monkeypatch.chdir(host)
    state_path = host / "plans" / FRESH_SLUG / "state.yaml"
    before = state_path.read_bytes()

    code, out, _err = run_cli(["kickoff", "--json", "--feature", FRESH_SLUG])
    assert code == 0, f"FAIL: kickoff exited {code} (must be a successful read)"
    envelope = envelope_tools.parse(out)
    envelope_tools.assert_shape(envelope)
    assert envelope["ok"] is True, "FAIL: kickoff must succeed as a pure read"
    data = envelope["data"]
    # The render payload: the current stage, the resource precedence-resolved source,
    # and a
    # non-empty briefing for that stage (REQ-14). Asserts the stage name appears
    # rather than exact text — canonical content lands at authoring (A3).
    assert data["feature"] == FRESH_SLUG
    assert data["stage"] == "implement", (
        f"FAIL: the fresh fixture is at the implement stage, got {data['stage']!r}"
    )
    assert data["source"] in ("override", "generated", "packaged"), (
        f"FAIL: kickoff must name the resolved resource source, got {data['source']!r}"
    )
    assert "implement" in data["briefing"].lower(), (
        "FAIL: kickoff must render the current stage's briefing — the implement "
        f"briefing, got {data['briefing'][:60]!r} (REQ-14/resource precedence)"
    )

    # Pure read: state.yaml byte-identical, and a second invocation identical.
    assert state_path.read_bytes() == before, (
        "FAIL: kickoff mutated state.yaml — it must store nothing (INV-4/session entry)"
    )
    code2, out2, _err = run_cli(["kickoff", "--json", "--feature", FRESH_SLUG])
    assert code2 == 0 and out2 == out, (
        "FAIL: kickoff is not idempotent — identical state must render "
        "identical output (INV-4)"
    )


def test_ac12_kickoff_is_a_pure_read_no_state_mutation(
    run_cli, tmp_path, monkeypatch
) -> None:
    # AC-12 / review: state.yaml is byte-identical before/after, and a second
    # invocation yields identical output (idempotent, stores nothing).
    host = _fresh_host(tmp_path)
    monkeypatch.chdir(host)
    state_path = host / "plans" / FRESH_SLUG / "state.yaml"

    before = state_path.read_bytes()
    code, first, _e = run_cli(["kickoff", "--feature", FRESH_SLUG])
    assert code == 0, f"FAIL: kickoff exited {code}"
    after = state_path.read_bytes()
    assert before == after, (
        "FAIL: kickoff mutated state.yaml — it must store nothing (INV-4/session entry)"
    )

    code, second, _e = run_cli(["kickoff", "--feature", FRESH_SLUG])
    assert code == 0
    assert first == second, (
        "FAIL: kickoff is not idempotent — identical state must render "
        "identical output (INV-4)"
    )


def test_ac13_orient_routes_fresh_stage_to_kickoff_which_renders(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    # AC-13: orient's next_actions names `heddle kickoff` for a fresh stage
    # (already true since reader) AND kickoff now renders (no longer the command
    # availability stub).
    host = _fresh_host(tmp_path)
    monkeypatch.chdir(host)

    code, out, _err = run_cli(["orient", "--json", "--feature", FRESH_SLUG])
    assert code == 0, f"FAIL: orient exited {code}"
    # next_actions is a top-level wire key (AC-4's five-key envelope), not part
    # of orient's pinned AC-10 data payload.
    envelope = envelope_tools.parse(out)
    commands = [action["command"] for action in envelope["next_actions"]]
    assert "heddle kickoff --feature tier1-quickfix" in commands, (
        f"FAIL: orient next_actions {commands} must name `heddle kickoff` for "
        "a fresh stage (REQ-15)"
    )

    code, out, _err = run_cli(["kickoff", "--json", "--feature", FRESH_SLUG])
    assert code == 0, (
        "FAIL: the route orient points at must render — kickoff is no longer "
        "a not-implemented stub (REQ-15/AC-13)"
    )
    rendered = envelope_tools.parse(out)
    assert rendered["ok"] is True and rendered["data"]["briefing"].strip(), (
        "FAIL: the kickoff route must render a non-empty briefing, not merely "
        "exit 0 (REQ-15/AC-13)"
    )


# --------------------------------------------------------------------------- #
# completion (kickoff-guidance-integration) — the public kickoff contract is frozen
# --------------------------------------------------------------------------- #
