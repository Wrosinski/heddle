"""Installed F1 journeys for truthful transition guidance and repair."""

from __future__ import annotations

import json
import os
import shlex
import tarfile
import textwrap
from pathlib import Path

import pytest
import yaml

from tests.content_identity_helpers import git
from tests.runtime.test_completion_installed import (
    FEATURE,
    SPEC,
    Journey,
    start_host,
)
from tests.runtime.wheel_harness import (
    build_installed_wheel,
    parse_envelope,
    snapshot_tree,
    write_claude_shim,
    write_executable,
)
from tests.tiering_helpers import FABLE
from tests.tiering_review_helpers import review_content

pytestmark = [pytest.mark.acceptance, pytest.mark.toolchain, pytest.mark.e2e]


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("f1-installed"))


def _run_emitted(journey: Journey, envelope, *, expected=0):
    assert len(envelope["next_actions"]) == 1, envelope
    rendered = envelope["next_actions"][0]["command"]
    argv = shlex.split(rendered)
    assert argv and argv[0] == "heddle", rendered
    operation = envelope["next_actions"][0]["action"].get("operation", {})
    payload = operation.get("arguments", {}).get("payload")
    if payload is not None:
        input_index = argv.index("--input-json") + 1
        assert argv[input_index] == "-", rendered
        argv[input_index] = journey.payload(
            f"emitted-{len(journey.transcript)}.json", payload
        )
    result = journey.installed.run(
        *argv[1:], "--json", cwd=journey.nested, env=journey.env
    )
    journey.record(result, cwd=journey.nested)
    observed = parse_envelope(result)
    allowed = (expected,) if isinstance(expected, int) else tuple(expected)
    assert result.returncode in allowed, (rendered, observed)
    assert observed["ok"] is (result.returncode in (0, 4)), observed
    return observed


def _payload(journey: Journey, name: str, value) -> str:
    path = journey.calls.parent / name
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return str(path)


def _write_two_milestone_documents(journey: Journey) -> None:
    root = journey.root
    (root / "src").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "src/example.py").write_text("VALUE = 0\n", encoding="utf-8")
    (root / "src/other.py").write_text("OTHER = 0\n", encoding="utf-8")
    (root / "tests/check.py").write_text(
        "import runpy, sys\n"
        "from pathlib import Path\n"
        f"CALLS = Path({str(journey.calls)!r})\n"
        "def check(mode):\n"
        "    value = runpy.run_path('src/example.py')['VALUE']\n"
        "    other = runpy.run_path('src/other.py')['OTHER']\n"
        "    assert value == 7\n"
        "    if mode != 'm1':\n"
        "        assert other == 11\n"
        "if __name__ == '__main__':\n"
        "    mode = sys.argv[1]\n"
        "    with CALLS.open('a', encoding='utf-8') as stream:\n"
        "        stream.write(mode + '\\n')\n"
        "    check(mode)\n",
        encoding="utf-8",
    )
    (root / SPEC).write_text(
        f"---\ntype: feature-spec\narea: runtime\nfeature_name: {FEATURE}\n"
        "lifecycle: active\n---\n\n# Two-part declared value\n\n"
        "## Acceptance Criteria\n\n### AC-1: Both values\n\nPriority: MUST\n"
        "Action: Read both implementation modules.\n"
        "Expected: VALUE equals 7 and OTHER equals 11.\n"
        "Verified-by: tests/check.py::check\n",
        encoding="utf-8",
    )
    (journey.state.parent / "plan.md").write_text(
        "# Plan\n\n## Environment\n\npython3 tests/check.py verify\n\n"
        "### Milestone m1: Implement VALUE\n\nSatisfies: AC-1\n\n"
        "Set VALUE to 7 and verify its owned source.\n\n"
        "### Milestone m2: Implement OTHER\n\nSatisfies: AC-1\n\n"
        "Depends on m1. Set OTHER to 11 and verify the combined behavior.\n",
        encoding="utf-8",
    )
    (journey.state.parent / "brief.md").write_text(
        "# Brief\n\nDeliver VALUE = 7 and OTHER = 11.\n", encoding="utf-8"
    )
    (root / f"journey-plans/{FEATURE}.decision-journal.md").write_text(
        "# Decision Journal\n\nNo policy decisions were made.\n", encoding="utf-8"
    )
    (root / f"journey-plans/{FEATURE}.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        + "\n\n".join(
            f"## {heading}\n\nNone observed in this installed fixture."
            for heading in (
                "Tooling fights",
                "Prompt gaps",
                "Validator false-positives",
                "Escalations that should have been policy-resolvable",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def _native_review(journey: Journey, tmp_path: Path, role: str) -> None:
    payload = review_content(role)
    if role == "plan-review":
        mapping = payload["details"]["ac_mapping"][0]
        assert mapping["milestones"] == ["m1", "m2"]
        assert payload["details"]["dependencies"] == [
            {
                "milestone": "m2",
                "depends_on": ["m1"],
                "reason": "Verification follows the value change.",
            }
        ]
    if role in {"milestone-review", "peer-review-sequential"}:
        payload["details"]["plan_context"] = (
            "Milestone m1 implements VALUE; dependent m2 implements OTHER."
        )
    event = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": payload,
            }
        )
        + "\n"
    )
    binary = write_claude_shim(tmp_path / "review-bin", event, journey.providers)
    journey.env["PATH"] = (
        str(binary.parent) + os.pathsep + journey.installed.env["PATH"]
    )
    reviewed = journey.run(
        "run-gate",
        role,
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=(0, 4),
    )
    assert reviewed["data"]["gate"] == role
    state = journey.read()
    run_id = [
        run["run_id"]
        for row in state["gates"]
        if row["gate"] == role
        for run in row["runs"]
    ][-1]
    disposition = journey.payload(
        f"{role}-{run_id}-disposition.json",
        {
            "schema": "heddle.review-disposition-input/v1",
            "dispositions": [
                {
                    "run_id": run_id,
                    "finding_id": "@coverage",
                    "status": "settled",
                    "evidence_kind": "inspection",
                    "references": ["tests/check.py"],
                    "reason": (
                        "The two declared sources have milestone and final local "
                        "checks; no external integration is part of this fixture."
                    ),
                }
            ],
        },
    )
    closed = journey.run(
        "review",
        "disposition",
        "--input-json",
        disposition,
        "--expect-revision",
        str(state["revision"]),
    )
    assert closed["data"]["closure"]["closed"] is True
    current = journey.run("status")
    if current["next_actions"]:
        operation = current["next_actions"][0]["action"].get("operation", {})
        arguments = operation.get("arguments", {})
        payload = arguments.get("payload", {})
        if operation.get("name") == "review round-open" and payload.get("role") == role:
            stopped = _run_emitted(journey, current)
            assert stopped["data"]["closure"]["closed"] is True


def _prepare_peer_boundary(
    installed,
    tmp_path: Path,
    *,
    flow: str,
    reviewed: bool,
) -> tuple[Journey, dict[str, dict]]:
    journey = start_host(installed, tmp_path, flow=flow, reviewed=reviewed)
    _write_two_milestone_documents(journey)
    journey.run(
        "commands",
        "set",
        "test_command",
        "--command",
        "python3 tests/check.py verify",
    )
    for key, mode in (("smoke_test", "smoke"), ("acceptance_test", "acceptance")):
        journey.run(
            "commands",
            "set",
            key,
            "--command",
            f"python3 tests/check.py {mode}",
        )
    milestones = (
        {
            "title": "Implement VALUE",
            "complexity": "low",
            "satisfies": ["AC-1"],
            "depends_on": [],
            "owns": [SPEC, "src/example.py", "tests/check.py"],
            "verification": {
                "command": "python3 tests/check.py m1",
                "expected": "VALUE is 7",
            },
        },
        {
            "title": "Implement OTHER",
            "complexity": "low",
            "satisfies": ["AC-1"],
            "depends_on": ["m1"],
            "owns": ["src/other.py"],
            "verification": {
                "command": "python3 tests/check.py m2",
                "expected": "both values are correct",
            },
        },
    )
    for index, milestone in enumerate(milestones, 1):
        journey.run(
            "milestone",
            "add",
            "--from-file",
            _payload(journey, f"m{index}.yaml", milestone),
        )
    git(journey.root, "add", ".")
    git(journey.root, "commit", "-qm", "author two-milestone installed feature")

    for stage in ("spec-review", "plan-review", "scaffold", "implement"):
        reached = journey.run("phase-exit", "--through", "complete")
        assert reached["data"]["stage"] == stage
        role = {
            "spec-review": "spec-review",
            "plan-review": "plan-review",
            "scaffold": "review-test-scaffolding",
        }.get(stage)
        if reviewed and role is not None:
            _native_review(journey, tmp_path, role)

    promoted_m1 = journey.run("milestone", "advance")
    assert promoted_m1["data"]["milestone_advance_summary"]["outcome"] == (
        "milestone-promoted"
    )
    journey.run("task", "add", "Implement VALUE = 7")
    m1_task = journey.read()["milestones"][0]["tasks"][-1]["id"]
    (journey.root / "src/example.py").write_text("VALUE = 7\n", encoding="utf-8")
    journey.run("task", "done", m1_task)
    journey.run("verify", "--scope", "m1")
    if reviewed:
        _native_review(journey, tmp_path, "milestone-review")
    completed_m1 = journey.run("milestone", "advance")
    assert completed_m1["data"]["milestone_advance_summary"]["outcome"] == (
        "milestone-completed"
    )
    assert [row["status"] for row in journey.read()["milestones"]] == [
        "done",
        "todo",
    ]
    promoted_m2 = _run_emitted(journey, completed_m1)
    assert promoted_m2["data"]["milestone_advance_summary"]["outcome"] == (
        "milestone-promoted"
    )
    assert [row["status"] for row in journey.read()["milestones"]] == [
        "done",
        "current",
    ]

    journey.run("task", "add", "Implement OTHER = 11")
    m2_task = journey.read()["milestones"][1]["tasks"][-1]["id"]
    (journey.root / "src/other.py").write_text("OTHER = 11\n", encoding="utf-8")
    journey.run("task", "done", m2_task)
    journey.run("verify", "--scope", "m2")
    if reviewed:
        _native_review(journey, tmp_path, "milestone-review")
    completed_m2 = journey.run("milestone", "advance")
    assert completed_m2["data"]["milestone_advance_summary"]["outcome"] == (
        "milestone-completed"
    )
    assert all(row["status"] == "done" for row in journey.read()["milestones"])
    git(journey.root, "add", "src", "tests")
    git(journey.root, "commit", "-qm", "implement both declared values")

    proof: dict[str, dict] = {}
    for scope in ("m1", "m2", "acceptance", "smoke"):
        proof[scope] = journey.run("verify", "--scope", scope)
        fact = journey.read()["verifications"][-1]
        assert fact["scope"] == scope and fact["exit_code"] == 0
        assert (
            fact["evidence"]["before"]["source_sha256"]
            == fact["evidence"]["after"]["source_sha256"]
        )
    peer = journey.run("phase-exit")
    assert peer["data"]["stage"] == "peer-review"
    if reviewed:
        _native_review(journey, tmp_path, "peer-review-sequential")
    return journey, proof


def _assert_installed_identity(journey: Journey) -> None:
    probe = journey.installed.python_probe(
        "import json, heddle, sys; "
        "print(json.dumps({'module': heddle.__file__, 'python': sys.executable}))",
        cwd=journey.nested,
    )
    journey.record(probe, cwd=journey.nested)
    assert probe.returncode == 0, probe.stderr
    identity = json.loads(probe.stdout)
    module = Path(identity["module"]).resolve()
    assert module.is_relative_to(journey.installed.site_packages.resolve())
    assert Path(identity["python"]).resolve() == journey.installed.python.resolve()


def _install_capability_only_claude(journey: Journey, root: Path) -> Path:
    probe_log = root / "capability-probes.jsonl"
    provider_log = journey.providers
    binary = write_executable(
        root / "capability-bin" / "claude",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import sys
            from pathlib import Path

            if '--help' in sys.argv:
                with Path({str(probe_log)!r}).open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(sys.argv[1:]) + '\\n')
                print('usage: claude --print --output-format stream-json --verbose '
                      '--permission-mode --model --effort --append-system-prompt '
                      '--allowedTools --disallowedTools --max-turns --max-budget-usd')
                raise SystemExit(0)
            with Path({str(provider_log)!r}).open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({{'unexpected_session': sys.argv[1:]}}) + '\\n')
            raise SystemExit(22)
            """
        ),
    )
    journey.env["PATH"] = (
        str(binary.parent) + os.pathsep + journey.installed.env["PATH"]
    )
    return probe_log


def _reach_off_robustness(journey: Journey) -> dict:
    before = journey.read()
    result = journey.run("phase-exit", "--through", "complete")
    after = journey.read()
    assert result["data"]["phase_exit_summary"]["outcome"] == "stage-advanced"
    assert result["data"]["phase_exit_summary"]["stage_before"] == "peer-review"
    assert result["data"]["stage"] == "robustness"
    assert result["data"]["authorized_through"] == "complete"
    assert after["revision"] == before["revision"] + 1
    assert after["stage"] == "robustness"
    return result


def test_installed_guidance_progresses_once_and_preserves_handoff(
    installed, tmp_path
) -> None:
    journey, _proof = _prepare_peer_boundary(
        installed, tmp_path, flow="hitl", reviewed=True
    )
    _assert_installed_identity(journey)
    _reach_off_robustness(journey)
    before = journey.read()
    provider_calls = journey.count(journey.providers)
    local_calls = journey.count(journey.calls)

    status = journey.run("status")
    orient = journey.run("orient")
    assert [row["action"] for row in status["next_actions"]] == [
        row["action"] for row in orient["next_actions"]
    ]
    assert status["next_actions"][0]["action"]["operation"]["name"] == "phase-exit"
    assert (
        status["next_actions"][0]["action"]["operation"]["arguments"]["expect_revision"]
        == before["revision"]
    )
    human = installed.run(
        "status", "--feature", FEATURE, cwd=journey.nested, env=journey.env
    )
    journey.record(human, cwd=journey.nested)
    assert human.returncode == 0
    assert status["next_actions"][0]["command"] in human.stdout

    completed = _run_emitted(journey, status)
    assert completed["data"]["stage"] == "complete"
    assert completed["data"]["revision"] == before["revision"] + 1
    assert completed["next_actions"][0]["action"]["operation"]["name"] == "kickoff"
    pending = journey.read()
    assert pending["completion"] is None and pending["sessions"] == before["sessions"]
    assert journey.count(journey.providers) == provider_calls
    assert journey.count(journey.calls) == local_calls
    assert not journey.archive.exists()
    assert "lifecycle: active" in (journey.root / SPEC).read_text()

    bounded = journey.run("drive", "--until", "complete")
    assert bounded["data"]["status"] == "until-reached"
    handoff = journey.run("drive", expected=1)
    assert handoff["error"]["code"] == "awaiting-human-completion"
    assert handoff["next_actions"][0]["action"]["operation"]["name"] == "kickoff"
    assert journey.read()["sessions"] == before["sessions"]
    before_preview = snapshot_tree(journey.root)
    journey.run("feature", "complete", "--dry-run")
    assert snapshot_tree(journey.root) == before_preview
    accepted = journey.run("feature", "complete")
    assert accepted["data"]["accepted"] is True
    assert journey.calls.read_text().splitlines().count("close") == 1
    accepted_state = journey.state.read_bytes()
    with tarfile.open(journey.archive, "r:gz") as archive:
        assert archive.extractfile("state.yaml").read() == accepted_state
    journey.retain()
    retry = journey.run("feature", "complete")
    assert retry["data"]["effects"]["cleanup"]["status"] == "complete"
    assert journey.state.read_bytes() == accepted_state

    auto_root = tmp_path / "auto-leg"
    auto_root.mkdir()
    auto, _auto_proof = _prepare_peer_boundary(
        installed, auto_root, flow="auto", reviewed=False
    )
    _reach_off_robustness(auto)
    auto_before = auto.read()
    probe_log = _install_capability_only_claude(auto, auto_root)
    driven = auto.run("drive", "--until", "complete")
    assert driven["data"]["status"] == "until-reached"
    assert auto.read()["stage"] == "complete"
    assert auto.read()["revision"] == auto_before["revision"] + 1
    assert auto.read()["sessions"] == auto_before["sessions"]
    assert probe_log.read_text().count("--help") == 1
    assert auto.count(auto.providers) == 0
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()


def _follow_current_verification_guidance(journey: Journey) -> dict:
    for _attempt in range(6):
        status = journey.run("status")
        operation = status["next_actions"][0]["action"].get("operation", {})
        if operation.get("name") != "verify":
            return status
        verified = _run_emitted(journey, status)
        assert verified["data"]["verification"]["exit_code"] == 0
    raise AssertionError("verification guidance did not converge within six scopes")


def test_installed_guidance_blocks_then_resumes_after_proof_repair(
    installed, tmp_path
) -> None:
    journey, _proof = _prepare_peer_boundary(
        installed, tmp_path, flow="hitl", reviewed=False
    )
    _reach_off_robustness(journey)
    captured = journey.run("status")
    captured_command = captured["next_actions"][0]["command"]
    before_drift = journey.read()
    (journey.root / "src/other.py").write_text("OTHER = 8\n", encoding="utf-8")

    stale = journey.run("status")
    assert stale["next_actions"]
    assert all(
        row["action"]["operation"]["name"] == "verify" for row in stale["next_actions"]
    )
    refused = _run_emitted(journey, captured, expected=3)
    assert refused["error"]["code"] == "verification-missing"
    assert journey.read() == before_drift
    assert journey.count(journey.providers) == 0
    assert not journey.archive.exists()

    probe_log = _install_capability_only_claude(journey, tmp_path)
    failed_drive = journey.run("drive", expected=1)
    assert failed_drive["error"]["code"] == "attempt-cap"
    failed_state = journey.read()
    failing = [row for row in failed_state["verifications"] if row["exit_code"] != 0]
    assert len(failing) == 3
    assert len({row["scope"] for row in failing}) == 1
    pending = [row for row in failed_state["decisions"] if row["status"] == "pending"]
    assert len(pending) == 1
    assert failed_state["stage"] == "robustness" and failed_state["completion"] is None
    assert probe_log.read_text().count("--help") == 1
    assert journey.count(journey.providers) == 0

    (journey.root / "src/other.py").write_text("OTHER = 11\n", encoding="utf-8")
    journey.run(
        "decisions",
        "resolve",
        pending[0]["id"],
        "--resolution",
        "Retry after restoring the declared OTHER value",
        "--routes-to",
        SPEC,
        "--kind",
        "disposition",
    )
    repaired = _follow_current_verification_guidance(journey)
    assert repaired["next_actions"][0]["action"]["operation"]["name"] == ("phase-exit")
    completed = _run_emitted(journey, repaired)
    assert completed["data"]["stage"] == "complete"
    assert completed["next_actions"][0]["action"]["operation"]["name"] == "kickoff"
    completed_state = journey.state.read_bytes()

    replay_result = journey.installed.run(
        *shlex.split(captured_command)[1:],
        "--json",
        cwd=journey.nested,
        env=journey.env,
    )
    journey.record(replay_result, cwd=journey.nested)
    replay = parse_envelope(replay_result)
    assert replay_result.returncode == 5 and replay["error"]["code"] == "conflict"
    assert journey.state.read_bytes() == completed_state
    assert journey.read()["completion"] is None
    assert journey.read()["sessions"] == []
    assert journey.count(journey.providers) == 0
    assert journey.calls.read_text().splitlines().count("close") == 0
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()
