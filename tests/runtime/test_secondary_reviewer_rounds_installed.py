"""secondary-reviewer-rounds-v1 installed journey: explicit e2e grant required.

Collection is safe. Execution builds one offline wheel and drives temporary Git
hosts through the installed CLI. Only the claude and codex executables are
doubled, by logging PATH shims; no network, provider or credential is used.
"""

from __future__ import annotations

import json

import pytest
import yaml

from tests.content_identity_helpers import git
from tests.runtime.wheel_harness import (
    build_installed_wheel,
    parse_envelope,
    write_review_shims,
)
from tests.secondary_rounds_helpers import (
    ASTRA_HIGH,
    FABLE_HIGH,
    assert_old_shape,
    assert_pre_change_ledger,
    assignment_rounds,
    delivered_targets,
    install_old_shape,
    slot_names,
    windowed,
)
from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import finding, finding_ref
from tests.tiering_helpers import FABLE, entry, prepare_input, snapshot, wire_policy
from tests.tiering_installed_helpers import (
    FEATURE,
    all_off,
    author_value,
    isolated_env,
    journey,
)
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    gate_command,
    open_round,
    provider_transport,
    review_content,
    runs,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.e2e, pytest.mark.toolchain]

SUGGESTION = {
    "role": "review-test-scaffolding",
    "secondary": {"cli": "codex", "model": "gpt-6-astra", "reasoning_effort": "xhigh"},
    "secondary_rounds": "all",
}


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("secondary-rounds"))


def _isolated(installed):
    assert not installed.forbidden_log.exists(), "FAIL: checkout import or read"
    assert not installed.network_log.exists(), "FAIL: unapproved network attempt"


class Providers:
    def __init__(self, tmp_path):
        self.responses = tmp_path / "responses"
        self.responses.mkdir()
        self.log = tmp_path / "provider-calls.jsonl"
        self.bin = write_review_shims(tmp_path / "bin", self.responses, self.log)

    def respond(self, cli, content):
        (self.responses / f"{cli}.json").write_text(json.dumps(content))

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


def _selection(call):
    """The reviewer identity each doubled CLI actually received."""
    argv = call["argv"]
    if call["cli"] == "claude":
        return {
            "cli": "claude",
            "model": argv[argv.index("--model") + 1],
            "reasoning_effort": call["environment"]["CLAUDE_CODE_EFFORT_LEVEL"],
        }
    effort = next(
        item.split("=", 1)[1].strip('"')
        for item in argv
        if item.startswith("model_reasoning_effort=")
    )
    return {
        "cli": "codex",
        "model": argv[argv.index("-m") + 1],
        "reasoning_effort": effort,
    }


def _run_routed_slot(case, providers, expected):
    actions = case.run("status")["next_actions"]
    routed = [
        row["action"]["operation"]
        for row in actions
        if row["action"]["kind"] == "command"
        and row["action"]["operation"]["name"] == "run-gate"
    ]
    assert len(routed) == 1, f"FAIL AC-3: readiness routes {actions}"
    arguments = routed[0]["arguments"]
    assert {key: arguments[key] for key in expected} == expected, (
        f"FAIL AC-3: readiness selected {arguments}"
    )
    ran = case.run(
        "run-gate", "spec-review", "--cli", arguments["cli"], expected=(0, 4)
    )
    assert ran["data"]["accepted"], ran
    call = providers.calls()[-1]
    assert _selection(call) == expected, f"FAIL AC-3: provider received {call}"
    return yaml.safe_load(case.state.read_text())["gates"][-1]["runs"][-1]


def _dispose(case, rows):
    payload = case.payload(
        "disposition.json",
        {"schema": "heddle.review-disposition-input/v1", "dispositions": rows},
    )
    revision = str(yaml.safe_load(case.state.read_text())["revision"])
    return case.run(
        "review",
        "disposition",
        "--input-json",
        payload,
        "--expect-revision",
        revision,
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


def test_secondary_rounds_installed_host_a_journey(installed, tmp_path):
    case = journey(installed, tmp_path)
    # concurrent-reviews-v1 AC-11 sequential control: this journey pins one
    # routed run-gate per slot, so its host launches reviews sequentially.
    config = case.root / ".heddle.yaml"
    config.write_text(config.read_text() + "reviews:\n  launch: sequential\n")
    git(case.root, "add", ".heddle.yaml")
    git(case.root, "commit", "-qm", "launch reviews sequentially")
    prepared = case.payload("prepare.json", prepare_input())
    recommendation = case.run(
        "feature",
        "prepare",
        FEATURE,
        "--area",
        "runtime",
        "--from-file",
        prepared,
        feature=False,
    )["data"]["recommendation"]
    windows = {
        row["role"]: row.get("secondary_rounds", 1) for row in recommendation["entries"]
    }
    assert windows.pop("spec-review") == windows.pop("plan-review") == "all", (
        "FAIL AC-8: spec and plan review must recommend window all"
    )
    assert set(windows.values()) == {1}
    assert recommendation["suggestions"] == [SUGGESTION]

    chosen = all_off()
    chosen["spec-review"] = windowed(
        entry("spec-review", limit=2, secondary=FABLE), "all"
    )
    policy = case.payload("policy.json", wire_policy(overrides=chosen))
    case.run("feature", "policy", FEATURE, "--from-file", policy, feature=False)
    case.run("feature", "start", FEATURE, feature=False)
    author_value(case)
    case.run("phase-exit", "--through", "complete")
    assert yaml.safe_load(case.state.read_text())["stage"] == "spec-review"

    providers = Providers(tmp_path)
    case.env["PATH"] = f"{providers.bin}:{case.env['PATH']}"
    providers.respond("codex", review_content())
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
    first = [
        _run_routed_slot(case, providers, ASTRA_HIGH),
        _run_routed_slot(case, providers, FABLE_HIGH),
    ]
    origins = {row["reviewer_slot"]: row["run_id"] for row in first}
    _dispose(
        case,
        [
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
            "reason": "Verify the secondary's original concern",
        },
    )
    revision = str(yaml.safe_load(case.state.read_text())["revision"])
    case.run(
        "review",
        "round-open",
        "--input-json",
        round_input,
        "--expect-revision",
        revision,
    )
    rounds = yaml.safe_load(case.state.read_text())["review_assignments"]
    assert [
        [slot["name"] for slot in row["slots"]]
        for row in rounds["assignments"][0]["rounds"]
    ] == [["primary", "secondary"], ["primary", "secondary"]], (
        "FAIL AC-3: round 2 under window all must freeze both slots"
    )

    verification = review_content()
    verification["prior_dispositions"] = [
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
    primary = _run_routed_slot(case, providers, ASTRA_HIGH)
    waiting = case.run("status")["data"]["review_closure"]["assignments"]
    spec = next(row for row in waiting if row["role"] == "spec-review")
    assert not spec["closed"] and spec["missing_slots"] == ["secondary"], (
        "FAIL AC-4: closure must wait for the secondary's round-2 result"
    )
    secondary = _run_routed_slot(case, providers, FABLE_HIGH)
    delivered = providers.calls()[-1]
    assert (origins["secondary"], "SP-I2") in delivered_targets(delivered["stdin"]), (
        "FAIL AC-4: the secondary's round-2 targets omit its own finding"
    )
    assert "SECONDARY_ORIGINAL_CONCERN" in delivered["stdin"]
    closed = _dispose(
        case,
        [
            _row(
                origins["secondary"],
                "SP-I2",
                evidence_kind="review",
                review_run_id=secondary["run_id"],
            ),
            *[
                _row(run["run_id"], "@coverage", status="settled")
                for run in (primary, secondary)
            ],
        ],
    )
    assert closed["data"]["closure"]["closed"], "FAIL AC-4: review did not close"
    assert [call["cli"] for call in providers.calls()] == [
        "codex",
        "claude",
        "codex",
        "claude",
    ]

    # AC-10: installed help and the packaged peer-review briefing.
    help_text = installed.run(
        "feature", "policy", "--help", cwd=case.root, env=case.env
    )
    assert help_text.returncode == 0
    rendered = " ".join(help_text.stdout.split())
    for phrase in ("secondary_rounds", "all", "suggestion"):
        assert phrase in rendered, f"FAIL AC-10: installed help omits {phrase!r}"
    briefing = (
        installed.site_packages / "heddle/resources/peer-review.briefing.md"
    ).read_text()
    assert "secondary_rounds" in briefing, (
        "FAIL AC-10: the packaged peer-review briefing does not name the window"
    )
    assert "joins round 1 only" not in briefing
    _isolated(installed)


def test_secondary_rounds_installed_host_b_pre_change_state(
    installed, tmp_path, monkeypatch, run_cli
):
    """Survivor pin (AC-2 read path): pre-change state through the wheel."""
    root, path = current_host(tmp_path, monkeypatch)
    literal = install_old_shape(path, spec_limit=3, spec_minimum=3)
    provider_transport(monkeypatch, review_content())
    for number in (1, 2):
        if number == 2:
            assert open_round(path, purpose="independent-pass").ok
        for cli in ("codex", "claude") if number == 1 else ("codex",):
            code, result = gate_command(
                run_cli, "run-gate", "spec-review", "--cli", cli
            )
            assert code in (0, 4), result
        latest = [run for run in runs(path) if run["round_number"] == number]
        recorded = dispose(
            path,
            [
                disposition(run["run_id"], "@coverage", status="settled")
                for run in latest
            ],
        )
        assert recorded.ok, recorded.to_envelope()
    value = yaml.safe_load(path.read_text())
    assert_old_shape(value)
    assert_pre_change_ledger(path)
    assert value["feature_policy"] == literal
    assert [slot_names(row) for row in assignment_rounds(path)] == [
        ["primary", "secondary"],
        ["primary"],
    ]

    env = isolated_env(installed, tmp_path)
    before = snapshot(root)
    for command in ("validate", "status", "orient"):
        result = installed.run(
            command, "--feature", V7_FEATURE, "--json", cwd=root, env=env
        )
        assert result.returncode in (0, 4), result.stdout + result.stderr
        envelope = parse_envelope(result)
        assert envelope["ok"], envelope
        if command == "status":
            budget = envelope["data"]["effective_policy"]["budget"]
            assert (budget["feature_minimum"], budget["feature_maximum"]) == (7, 10)
            spec = next(
                row
                for row in envelope["data"]["review_closure"]["assignments"]
                if row["role"] == "spec-review"
            )
            assert (spec["rounds_used"], spec["calls_completed"]) == (2, 3)
            assert spec["missing_slots"] == []
    assert snapshot(root) == before, "FAIL AC-2: reading changed stored bytes"
    _isolated(installed)
