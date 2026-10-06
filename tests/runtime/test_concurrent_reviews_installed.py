"""concurrent-reviews-v1 installed journey: explicit e2e grant required (D1).

Collection is safe. Execution builds one offline wheel and drives a temporary
Git host through the installed CLI. Only the claude and codex executables are
doubled, by logging PATH shims in rendezvous mode: in a concurrent round each
shim waits for the other CLI's start marker, so the round passes only if both
calls were in flight together. No network, provider or credential is used.

Covers AC-1 (overlap, primary-first records, one accepted review per slot),
AC-6 (replay refusal), AC-7 (exit code and routing), AC-9 (one record per
slot) and AC-11 (sequential routing and the run-gates refusal).
"""

from __future__ import annotations

import json

import pytest
import yaml

from tests.runtime.wheel_harness import build_installed_wheel, write_review_shims
from tests.secondary_rounds_helpers import windowed
from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import finding, finding_ref
from tests.tiering_helpers import ASTRA, FABLE, SOL, entry, prepare_input, wire_policy
from tests.tiering_installed_helpers import (
    FEATURE,
    all_off,
    author_value,
    git,
    journey,
)
from tests.tiering_review_helpers import review_content

pytestmark = [pytest.mark.acceptance, pytest.mark.e2e, pytest.mark.toolchain]


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("concurrent-reviews"))


class Providers:
    def __init__(self, tmp_path):
        self.responses = tmp_path / "responses"
        self.responses.mkdir()
        self.log = tmp_path / "provider-calls.jsonl"
        self.markers = tmp_path / "markers"
        self.markers.mkdir()
        self.control = self.markers / "control.json"
        self.bin = write_review_shims(
            tmp_path / "bin", self.responses, self.log, control=self.control
        )

    def step(self, label, mode):
        self.control.write_text(json.dumps({"label": label, "mode": mode}))

    def respond(self, cli, content):
        (self.responses / f"{cli}.json").write_text(json.dumps(content))

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def started(self, label):
        return {
            cli
            for cli in ("codex", "claude")
            if (self.markers / f"started-{label}-{cli}").exists()
        }


def _selection(call):
    argv = call["argv"]
    if call["cli"] == "claude":
        return (
            "claude",
            argv[argv.index("--model") + 1],
            call["environment"]["CLAUDE_CODE_EFFORT_LEVEL"],
        )
    effort = next(
        item.split("=", 1)[1].strip('"')
        for item in argv
        if item.startswith("model_reasoning_effort=")
    )
    return ("codex", argv[argv.index("-m") + 1], effort)


def _commands(envelope):
    return [row["command"] for row in envelope["next_actions"]]


def _state(case):
    return yaml.safe_load(case.state.read_text())


def _round_attempts(case, role, number):
    value = _state(case)
    return [
        attempt
        for attempt in value["review_assignments"]["attempts"]
        if attempt["assignment_id"].startswith(f"{FEATURE}:{role}:")
        and attempt["round_number"] == number
    ]


def _dispose(case, rows):
    payload = case.payload(
        "disposition.json",
        {"schema": "heddle.review-disposition-input/v1", "dispositions": rows},
    )
    revision = str(_state(case)["revision"])
    return case.run(
        "review", "disposition", "--input-json", payload, "--expect-revision", revision
    )


def _row(run_id, finding_id, **kw):
    row = {
        "run_id": run_id,
        "finding_id": finding_id,
        "status": "addressed",
        "evidence_kind": "inspection",
        "references": ["src/example.py"],
        "reason": "Inspected the exact VALUE counterexample against its owner",
    }
    row.update(kw)
    return row


def _concurrent_round(case, providers, label):
    """AC-1: one run-gates action; both calls overlap; primary records first."""
    orient = case.run("orient")
    assert f"heddle run-gates --feature {FEATURE}" in _commands(orient), (
        f"FAIL AC-1: orient did not emit run-gates: {_commands(orient)}"
    )
    assert not any(
        command.startswith("heddle run-gate spec-review ")
        for command in _commands(orient)
    )
    providers.step(label, "rendezvous")
    revision = _state(case)["revision"]
    calls = len(providers.calls())
    return orient, revision, calls


def test_concurrent_reviews_installed_journey(installed, tmp_path):
    case = journey(installed, tmp_path)
    (case.root / ".heddle.yaml").write_text(
        "autopilot:\n  test_command: python3 tests/check.py\n"
        "checkpoints:\n  specification: false\n  review_changes: false\n"
    )
    chosen = all_off()
    chosen["spec-review"] = windowed(
        entry("spec-review", limit=2, primary=ASTRA, secondary=FABLE), "all"
    )
    chosen["plan-review"] = entry("plan-review", limit=1, primary=SOL, secondary=FABLE)
    prepared = case.payload("prepare.json", prepare_input())
    case.run(
        "feature",
        "prepare",
        FEATURE,
        "--area",
        "runtime",
        "--from-file",
        prepared,
        feature=False,
    )
    policy = case.payload("policy.json", wire_policy(overrides=chosen))
    case.run("feature", "policy", FEATURE, "--from-file", policy, feature=False)
    case.run("feature", "start", FEATURE, feature=False)
    author_value(case)
    case.run("phase-exit", "--through", "complete")
    assert _state(case)["stage"] == "spec-review"

    providers = Providers(tmp_path)
    case.env["PATH"] = f"{providers.bin}:{case.env['PATH']}"

    # Round 1: primary `fail`, secondary `pass_with_conditions`.
    providers.respond(
        "codex",
        review_content(
            findings=[
                finding(
                    "SP-C1",
                    severity="critical",
                    classification="implement",
                    title="PRIMARY_ORIGINAL_CONCERN",
                )
            ]
        ),
    )
    providers.respond(
        "claude",
        review_content(
            findings=[
                finding(
                    "SP-I2",
                    classification="implement",
                    title="SECONDARY_ORIGINAL_CONCERN",
                )
            ]
        ),
    )
    _orient, revision, calls = _concurrent_round(case, providers, "r1")
    ran = case.run("run-gates", expected=(3,))
    assert ran["ok"], f"FAIL AC-7: unconverged verdicts failed the batch: {ran}"
    assert ran["data"]["gates"] == ["spec-review"]
    members = ran["data"]["members"]
    assert [(row["gate"], row["reviewer_slot"]) for row in members] == [
        ("spec-review", "primary"),
        ("spec-review", "secondary"),
    ]
    assert providers.started("r1") == {"codex", "claude"}, (
        "FAIL AC-1: the two slots were not in flight together"
    )
    attempts = _round_attempts(case, "spec-review", 1)
    assert [row["reviewer_slot"] for row in attempts] == ["primary", "secondary"]
    assert _state(case)["revision"] == revision + 2, "FAIL AC-1: one write per record"
    assert len(providers.calls()) == calls + 2
    origins = {row["reviewer_slot"]: row["run_id"] for row in members}

    # AC-6/AC-9: a replay with no missing slot is refused without any effect.
    before = case.state.read_bytes()
    replay = case.run("run-gates", expected=(1, 2, 3, 4, 5))
    assert not replay["ok"], "FAIL AC-9: replay with no new launch set succeeded"
    assert case.state.read_bytes() == before
    assert len(providers.calls()) == calls + 2

    _dispose(
        case,
        [
            _row(
                origins["primary"], "SP-C1", status="retained", requires_inspection=True
            ),
            _row(
                origins["secondary"],
                "SP-I2",
                status="retained",
                requires_inspection=True,
            ),
            *[
                _row(run_id, "@coverage", status="settled")
                for run_id in origins.values()
            ],
        ],
    )
    round_input = case.payload(
        "round.json",
        {
            "schema": "heddle.review-round-input/v1",
            "role": "spec-review",
            "scope": "feature",
            "purpose": "verification",
            "reason": "Verify both original concerns",
        },
    )
    case.run(
        "review",
        "round-open",
        "--input-json",
        round_input,
        "--expect-revision",
        str(_state(case)["revision"]),
    )

    # Round 2: both slots again, together; both pass.
    verification = review_content()
    verification["prior_dispositions"] = [
        reviewer_disposition(
            finding_ref(origins["primary"], "SP-C1"), action="addressed"
        ),
        reviewer_disposition(
            finding_ref(origins["secondary"], "SP-I2"), action="addressed"
        ),
        *(
            reviewer_disposition(finding_ref(run_id, "@coverage"), action="addressed")
            for run_id in origins.values()
        ),
    ]
    providers.respond("codex", verification)
    providers.respond("claude", verification)
    _orient, revision, calls = _concurrent_round(case, providers, "r2")
    second = case.run("run-gates")
    assert second["ok"] and providers.started("r2") == {"codex", "claude"}
    assert [row["round_number"] for row in second["data"]["members"]] == [2, 2]
    assert [
        row["reviewer_slot"] for row in _round_attempts(case, "spec-review", 2)
    ] == [
        "primary",
        "secondary",
    ]
    assert _state(case)["revision"] == revision + 2
    verified = {
        row["reviewer_slot"]: row["run_id"] for row in second["data"]["members"]
    }
    closed = _dispose(
        case,
        [
            _row(
                origins["primary"],
                "SP-C1",
                evidence_kind="review",
                review_run_id=verified["primary"],
            ),
            _row(
                origins["secondary"],
                "SP-I2",
                evidence_kind="review",
                review_run_id=verified["secondary"],
            ),
            *[
                _row(run_id, "@coverage", status="settled")
                for run_id in verified.values()
            ],
        ],
    )
    assert closed["data"]["closure"]["closed"], "FAIL AC-1: spec review did not close"

    # AC-11: switch to sequential before plan review; nothing in state changes.
    case.run("phase-exit")
    assert _state(case)["stage"] == "plan-review"
    before = case.state.read_bytes()
    (case.root / ".heddle.yaml").write_text(
        "autopilot:\n  test_command: python3 tests/check.py\n"
        "checkpoints:\n  specification: false\n  review_changes: false\n"
        "reviews:\n  launch: sequential\n"
    )
    git(case.root, "add", ".heddle.yaml")
    git(case.root, "commit", "-qm", "launch reviews sequentially")
    orient = case.run("orient")
    assert case.state.read_bytes() == before, "FAIL AC-11: the switch changed state"
    assert f"heddle run-gates --feature {FEATURE}" not in _commands(orient)
    providers.step("solo", "solo")
    calls = len(providers.calls())
    refused = case.run("run-gates", expected=(1, 2, 3, 4, 5))
    assert not refused["ok"], "FAIL AC-11: run-gates ran in sequential mode"
    assert case.state.read_bytes() == before and len(providers.calls()) == calls
    assert any(
        command.startswith("heddle run-gate plan-review ")
        for command in _commands(refused)
    ), "FAIL AC-11: the refusal does not return the current single-gate action"

    providers.respond("codex", review_content("plan-review"))
    providers.respond("claude", review_content("plan-review"))
    plan_runs = []
    for cli in ("codex", "claude"):
        routed = [
            command
            for command in _commands(case.run("orient"))
            if command.startswith("heddle run-gate plan-review ")
        ]
        assert len(routed) == 1 and f"--cli {cli} " in routed[0], (
            f"FAIL AC-11: sequential routing offered {routed}"
        )
        ran = case.run("run-gate", "plan-review", "--cli", cli, expected=(0, 4))
        assert ran["data"]["accepted"], ran
        plan_runs.append(_state(case)["gates"][-1]["runs"][-1]["run_id"])
    closed = _dispose(
        case, [_row(run_id, "@coverage", status="settled") for run_id in plan_runs]
    )
    assert closed["data"]["closure"]["closed"], "FAIL AC-11: plan review did not close"
    case.run("validate")

    selections = [_selection(call) for call in providers.calls()]
    assert len(selections) == 6, f"FAIL AC-1: expected six provider calls: {selections}"
    astra = ("codex", ASTRA["model"], ASTRA["reasoning_effort"])
    fable = ("claude", FABLE["model"], FABLE["reasoning_effort"])
    # Concurrent rounds log in start order, which either slot may win.
    assert sorted(selections[0:2]) == sorted(selections[2:4]) == sorted([astra, fable])
    assert selections[4:] == [("codex", SOL["model"], SOL["reasoning_effort"]), fable]
    assert not installed.forbidden_log.exists(), "FAIL: checkout import or read"
    assert not installed.network_log.exists(), "FAIL: unapproved network attempt"
