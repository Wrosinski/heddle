"""Authorization, flow posture, and policy-grant behavior."""

from __future__ import annotations

from importlib.resources import files

import pytest

from heddle.contracts.schemas import STAGES
from tests.driver.constants import AUTO_TIER2_SLUG as SLUG
from tests.driver.helpers import (
    assert_has_grant,
    remove_autopilot_config,
    set_autopilot_config,
)

PRINCIPLES = "docs/workflow/engineering-principles.md"


# --------------------------------------------------------------------------- #
# AC-1 — flow mode is data and is surfaced
# --------------------------------------------------------------------------- #
def test_ac01_flow_auto_surfaced_in_status_and_orient(
    run_cli, envelope_tools, arranged_auto_tier2
):
    arranged_auto_tier2()
    for command in ("status", "orient"):
        code, out, _err = run_cli([command, "--feature", SLUG, "--json"])
        assert code == 0, f"FAIL AC-1: {command} --json must succeed (exit {code})"
        data = envelope_tools.parse(out)["data"]
        assert data.get("flow") == "auto", (
            f"FAIL AC-1: {command} must report flow=auto, got {data.get('flow')!r}"
        )


def _strip_autopilot_block(host) -> None:
    """Remove the `autopilot:` block from the host `.heddle.yaml` so default flow
    resolution has no configured default and must fall back to `hitl`."""
    remove_autopilot_config(host)


@pytest.mark.parametrize("command", ["status", "orient"])
@pytest.mark.parametrize(
    "strip_autopilot, expected_flow",
    [
        pytest.param(False, "auto", id="inherit-heddle-yaml-default"),
        pytest.param(True, "hitl", id="fallback-to-hitl"),
    ],
)
def test_ac01_flow_default_resolution(
    run_cli,
    envelope_tools,
    arranged_auto_tier2,
    command,
    strip_autopilot,
    expected_flow,
):
    """
    AC-1 default flow resolution (review): a `flow`-absent state inherits the
        `.heddle.yaml autopilot.flow` default, and with NO autopilot config it falls
        back to `hitl`. The AC names BOTH `status --json` and `orient --json`, so both
        must resolve the default identically — an `orient` that omits or misresolves the
        default (e.g. only `status` learned the resolution rule) is caught here.
    """
    host, _state_path = arranged_auto_tier2(
        mutate=lambda state: state.pop("flow", None)
    )
    if strip_autopilot:
        _strip_autopilot_block(host)

    code, out, _err = run_cli([command, "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-1: {command} --json must succeed (exit {code})"
    data = envelope_tools.parse(out)["data"]
    assert data.get("flow") == expected_flow, (
        f"FAIL AC-1: a flow-absent state via {command} must resolve flow="
        f"{expected_flow!r} (autopilot block "
        f"{'stripped → hitl fallback' if strip_autopilot else 'present → inherit'}), "
        f"got {data.get('flow')!r}"
    )


# --------------------------------------------------------------------------- #
# AC-2 — auto-flow policy grant when nothing blocks
# --------------------------------------------------------------------------- #
def test_ac02_auto_phase_exit_records_policy_grant(
    run_json, driver_corpus, arranged_auto_tier2
):
    _host, state_path = arranged_auto_tier2()  # stage=specify, a clean boundary

    code, envelope, _out, _err = run_json(["phase-exit", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-2: clean auto boundary must exit 0, got {code}"

    after = driver_corpus.read_yaml(state_path)
    assert after["authorized_through"] == "spec-review", (
        "FAIL AC-2: authorized_through must advance to spec-review"
    )
    grants = after.get("authorizations", [])
    assert_has_grant(
        grants,
        source="policy",
        message="FAIL AC-2: a grant with source=policy must be recorded (INV-3)",
    )
    assert STAGES.index(after["stage"]) <= STAGES.index(after["authorized_through"]), (
        "FAIL AC-2: stage <= authorized_through must hold after phase-exit"
    )


# --------------------------------------------------------------------------- #
# AC-3 — HITL human gate is flow-conditional
# --------------------------------------------------------------------------- #
def test_ac03_hitl_raises_human_gate_auto_does_not(
    run_cli, envelope_tools, arranged_auto_tier2
):
    # auto: no human gate.
    arranged_auto_tier2()
    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    auto_blocks = envelope_tools.parse(out)["data"]["blocking_conditions"]
    assert "awaiting-human-authorization" not in auto_blocks, (
        "FAIL AC-3: awaiting-human-authorization must never arise under flow=auto"
    )

    # hitl: human gate present at the boundary (no user grant recorded).
    arranged_auto_tier2(flow="hitl")
    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    hitl_blocks = envelope_tools.parse(out)["data"]["blocking_conditions"]
    assert "awaiting-human-authorization" in hitl_blocks, (
        "FAIL AC-3: flow=hitl must raise awaiting-human-authorization until a "
        "source=user grant is recorded"
    )


def test_ac03_host_default_hitl_raises_human_gate(
    run_cli, envelope_tools, arranged_auto_tier2
):
    host, _state_path = arranged_auto_tier2(
        mutate=lambda state: state.pop("flow", None)
    )
    set_autopilot_config(host, flow="hitl")

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-3: status --json must succeed (exit {code})"
    data = envelope_tools.parse(out)["data"]
    assert data.get("flow") == "hitl", (
        f"FAIL AC-3: flow-absent state with host default must resolve hitl, "
        f"got {data.get('flow')!r}"
    )
    assert "awaiting-human-authorization" in data["blocking_conditions"], (
        "FAIL AC-3: awaiting-human-authorization must fire for default-resolved "
        "hitl flow"
    )


# --------------------------------------------------------------------------- #
# AC-4 — principles-not-ratified standing precondition
# --------------------------------------------------------------------------- #
def test_ac04_principles_not_ratified_blocks_until_ratified(
    run_cli, envelope_tools, arranged_auto_tier2
):
    host, _state_path = arranged_auto_tier2()
    principles = host / PRINCIPLES
    projected_seed = (
        files("heddle")
        .joinpath("resources", "engineering-principles.seed.md")
        .read_text(encoding="utf-8")
    )
    assert "status: ratified" not in projected_seed
    principles.write_text(projected_seed, encoding="utf-8")

    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    blocks = envelope_tools.parse(out)["data"]["blocking_conditions"]
    assert "principles-not-ratified" in blocks, (
        "FAIL AC-4: flow=auto must raise principles-not-ratified while the "
        "principles file is draft"
    )

    principles.write_text(
        projected_seed.replace(
            "---\n",
            "---\nstatus: ratified\n",
            1,
        ),
        encoding="utf-8",
    )
    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    blocks = envelope_tools.parse(out)["data"]["blocking_conditions"]
    assert "principles-not-ratified" not in blocks, (
        "FAIL AC-4: flipping the principles file to ratified must clear the block"
    )


# --------------------------------------------------------------------------- #
# AC-16 — grant source visible in status/orient
# --------------------------------------------------------------------------- #
def test_ac16_grant_source_exposed_per_grant(
    run_cli, envelope_tools, arranged_auto_tier2
):
    # Two typed grants (one user, one policy) plus a legacy grant that OMITS
    # source. The source-omitting grant guards against a pass-through that
    # fabricates a constant source (e.g. always "policy" in auto flow) rather than
    # surfacing each grant's own field (review): a genuine per-grant surface must
    # report the user/policy grants AND carry the legacy grant through with its
    # source absent (None) — never fabricated — without dropping or crashing on it.
    arranged_auto_tier2(
        authorizations=[
            {"through": "specify", "source": "user", "at": "2026-06-16T08:10Z"},
            {
                "through": "spec-review",
                "source": "policy",
                "at": "2026-06-16T08:20Z",
            },
            {"through": "plan", "at": "2026-06-16T08:30Z"},  # legacy
        ],
    )

    for command in ("status", "orient"):
        code, out, _err = run_cli([command, "--feature", SLUG, "--json"])
        assert code == 0, f"FAIL AC-16: {command} must succeed (exit {code})"
        data = envelope_tools.parse(out)["data"]
        grants = data.get("authorizations", [])
        assert len(grants) == 3, (
            f"FAIL AC-16: {command} must surface every grant per-grant (3), not a "
            f"summarized/dropped set; got {len(grants)}"
        )
        by_through = {g.get("through"): g.get("source") for g in grants}
        assert by_through.get("specify") == "user", (
            f"FAIL AC-16: {command} must surface the user grant's own source, not a "
            f"fabricated constant; got {by_through.get('specify')!r}"
        )
        assert by_through.get("spec-review") == "policy", (
            f"FAIL AC-16: {command} must surface the policy grant's own source; got "
            f"{by_through.get('spec-review')!r}"
        )
        assert by_through.get("plan") in (None, ""), (
            f"FAIL AC-16: {command} must carry a legacy source-absent grant through "
            f"with no fabricated source; got {by_through.get('plan')!r}"
        )


# --------------------------------------------------------------------------- #
# AC-20 — additive to state.yaml v0
# --------------------------------------------------------------------------- #


def test_ac20_verification_missing_remains_stage_independent(
    run_cli, envelope_tools, arranged_auto_tier2
):
    def arm_done_unverified(state: dict) -> None:
        state["stage"] = "spec-review"
        state["authorized_through"] = "spec-review"
        state["verifications"] = []
        first = state["milestones"][0]
        first["status"] = "done"
        first["tasks"] = [
            {"id": task["id"], "text": task["text"], "status": "done"}
            for task in first.get("tasks", [])
        ]

    arranged_auto_tier2(mutate=arm_done_unverified)
    code, out, _err = run_cli(["status", "--feature", SLUG, "--json"])
    assert code == 0, f"FAIL AC-20: status --json must succeed (exit {code})"
    blocks = envelope_tools.parse(out)["data"]["blocking_conditions"]
    assert "verification-missing" in blocks, (
        "FAIL AC-20: verification-missing must remain a legacy derived blocker "
        "outside implement when a done milestone lacks a passing verification fact"
    )


# --------------------------------------------------------------------------- #
# AC-21 — auto phase-exit withholds the grant on any blocker
# --------------------------------------------------------------------------- #
def test_ac21_blocked_task_incomplete_writes_nothing(run_json, arranged_auto_tier2):
    def arm_block(state: dict) -> None:
        state["stage"] = "implement"
        state["authorized_through"] = "implement"
        # core done but carries an open task -> task-incomplete (reader-derived).
        state["milestones"][0]["status"] = "done"
        state["milestones"][0]["tasks"] = [
            {"id": "t1", "text": "left open", "status": "todo"}
        ]
        state["milestones"][1]["status"] = "current"

    _host, state_path = arranged_auto_tier2(mutate=arm_block)

    before = state_path.read_bytes()
    code, envelope, _out, _err = run_json(["phase-exit", "--feature", SLUG, "--json"])
    assert code == 3 and envelope["error"]["code"] == "task-incomplete", (
        f"FAIL AC-21: must block task-incomplete (exit 3), got exit {code} / "
        f"{envelope.get('error', {}).get('code')}"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-21: a blocked auto phase-exit must leave state.yaml byte-for-byte "
        "unchanged (no source: policy written)"
    )


def test_ac21_blocked_implement_exit_block_writes_nothing(
    run_json, arranged_auto_tier2
):
    def arm_block(state: dict) -> None:
        state["stage"] = "implement"
        state["authorized_through"] = "implement"
        # reader's derived set is clear (no open tasks) yet a milestone is not `done`:
        # _implement_exit_block blocks with milestone-out-of-sequence.
        state["milestones"][0]["status"] = "current"
        state["milestones"][0]["tasks"] = [
            {"id": "t1", "text": "finished", "status": "done"}
        ]
        state["milestones"][1]["status"] = "todo"

    _host, state_path = arranged_auto_tier2(mutate=arm_block)

    before = state_path.read_bytes()
    code, envelope, _out, _err = run_json(["phase-exit", "--feature", SLUG, "--json"])
    assert code == 3 and envelope["error"]["code"] == "milestone-out-of-sequence", (
        f"FAIL AC-21: _implement_exit_block must surface milestone-out-of-sequence, "
        f"got exit {code} / {envelope.get('error', {}).get('code')}"
    )
    # review: withholding the grant is not enough — the envelope must also GUIDE the
    # corrective command, a `next_actions` entry naming `heddle milestone advance`
    # (the remedy for an out-of-sequence milestone). A driver that withholds but
    # leaves the operator without the next step is caught here.
    commands = [
        action.get("command", "") for action in envelope.get("next_actions", [])
    ]
    assert any("milestone advance" in cmd for cmd in commands), (
        "FAIL AC-21: the withhold must name `heddle milestone advance` in "
        f"next_actions as the corrective command, got {commands}"
    )
    assert state_path.read_bytes() == before, (
        "FAIL AC-21: a blocked auto phase-exit must leave state.yaml byte-for-byte "
        "unchanged"
    )
