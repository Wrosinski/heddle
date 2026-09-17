"""
Integrated acceptance coverage for the workflow write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.runtime.write_path_helpers import (
    copy_host as _copy_host,
)
from tests.runtime.write_path_helpers import (
    read_yaml as _read_yaml,
)
from tests.runtime.write_path_helpers import (
    write_yaml as _write_yaml,
)
from tests.structured_review_helpers import scripted_review

pytestmark = pytest.mark.acceptance

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"
EDGE = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "edge"
COMPLETED_BOUNDARY_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "workspaces" / "completed-boundary"
)

PASS_REVIEW = scripted_review(summary="The scripted review found no issues.")


def _prepare_alpha_loop_host(tmp_path: Path) -> Path:
    host = _copy_host(tmp_path, GOLDEN)
    state_path = host / "plans" / "nl-screening" / "state.yaml"
    state = _read_yaml(state_path)
    state["revision"] = 100
    state["stage"] = "implement"
    state["authorized_through"] = "implement"
    for milestone in state["milestones"]:
        if milestone["id"] == "m1":
            milestone["status"] = "current"
            milestone["verification"]["command"] = (
                f"{sys.executable} -c \"print('m1 verification ok')\""
            )
        if milestone["id"] == "m2":
            milestone["status"] = "todo"
            milestone["verification"]["command"] = (
                f"{sys.executable} -c \"print('m2 verification ok')\""
            )
            for task in milestone["tasks"]:
                if task["id"] == "t2":
                    task["status"] = "current"
                else:
                    task["status"] = "done"
    state["verifications"] = [
        fact for fact in state["verifications"] if fact["scope"] != "m2"
    ]
    for entry in state["feature_policy"]["entries"]:
        if entry["role"] == "milestone-review":
            entry.update(mode="upper-limit", limit=2, minimum_rounds=1)
    state["gates"] = []
    reviews = state_path.parent / "reviews"
    for artifact in reviews.glob("*"):
        if artifact.is_file():
            artifact.unlink()
    # Materialize the reviewed inputs before recording verification, so the
    # later engine fixture does not change their content after a passing fact.
    from tests.runtime.conftest import _ensure_current_owned_input
    from tests.runtime.write_path_helpers import authored_milestone_verification

    _write_yaml(state_path, state)
    _ensure_current_owned_input(host)
    for milestone in state["milestones"]:
        if milestone["id"] == "m1":
            state["verifications"] = [
                fact
                for fact in state["verifications"]
                if fact["scope"] != milestone["id"]
            ]
            state["verifications"].append(
                authored_milestone_verification(state_path, milestone)
            )
    state["commands"].update(
        smoke_test="python3 -c 'print(1)'", acceptance_test="python3 -c 'print(1)'"
    )
    _write_yaml(state_path, state)
    import subprocess

    for args in (
        ["init", "-q"],
        ["config", "user.email", "test@example.test"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-qm", "fixture birth"],
    ):
        subprocess.run(["git", *args], cwd=host, check=True)
    return host


def _state(host: Path, slug: str = "nl-screening") -> dict[str, Any]:
    return _read_yaml(host / "plans" / slug / "state.yaml")


def _task_statuses(state: dict[str, Any], milestone_id: str) -> dict[str, str]:
    milestone = next(m for m in state["milestones"] if m["id"] == milestone_id)
    return {task["id"]: task["status"] for task in milestone.get("tasks", [])}


@pytest.mark.acceptance
class TestWritePathAcceptance:
    """
    The composed alpha loop plus AC-level checks for the full write.
    """

    def assert_ac15_alpha_loop_end_to_end_no_hand_edited_operational_state(
        self,
        run_cli,
        envelope_tools,
        fake_gate_runner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        host = _prepare_alpha_loop_host(tmp_path)
        monkeypatch.chdir(host)
        fixture_state_path = GOLDEN / "plans" / "nl-screening" / "state.yaml"
        fixture_before = fixture_state_path.read_bytes()

        def close_current_milestone_review(label: str) -> None:
            fake_gate_runner(review=PASS_REVIEW, prepare_owned=False)
            code, out, review_err = run_cli(
                [
                    "run-gate",
                    "milestone-review",
                    "--feature",
                    "nl-screening",
                    "--json",
                ]
            )
            review = envelope_tools.parse(out)
            assert code in {0, 4} and review["ok"] is True, (
                f"FAIL AC-15: {label} needs a native current review, got exit "
                f"{code}: {review!r}; stderr={review_err!r}"
            )
            state = _state(host)
            run = next(
                run
                for fact in reversed(state["gates"])
                if fact["gate"] == "milestone-review"
                for run in reversed(fact["runs"])
            )
            payload = tmp_path / f"{label}-coverage.json"
            payload.write_text(
                json.dumps(
                    {
                        "schema": "heddle.review-disposition-input/v1",
                        "dispositions": [
                            {
                                "run_id": run["run_id"],
                                "finding_id": "@coverage",
                                "status": "settled",
                                "evidence_kind": "inspection",
                                "references": [
                                    "src/tests/screening/"
                                    f"{'parser' if label == 'm1' else 'validate'}"
                                    "/heddle_fake_reviewed_input.py"
                                ],
                                "reason": (
                                    "The milestone verification directly exercises "
                                    "the declared owned behavior."
                                ),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, out, _err = run_cli(
                [
                    "review",
                    "disposition",
                    "--input-json",
                    str(payload),
                    "--expect-revision",
                    str(state["revision"]),
                    "--feature",
                    "nl-screening",
                    "--json",
                ]
            )
            disposition = envelope_tools.parse(out)
            assert code == 0 and disposition["data"]["closure"]["closed"] is True, (
                f"FAIL AC-15: {label} review disposition failed: {disposition!r}"
            )

        close_current_milestone_review("m1")
        code, out, _err = run_cli(
            ["verify", "--scope", "m1", "--feature", "nl-screening", "--json"]
        )
        assert code == 0 and envelope_tools.parse(out)["ok"] is True, out
        for _attempt in range(2):
            if (
                next(
                    milestone
                    for milestone in _state(host)["milestones"]
                    if milestone["id"] == "m2"
                )["status"]
                == "current"
            ):
                break
            code, out, _err = run_cli(
                ["milestone", "advance", "--feature", "nl-screening", "--json"]
            )
            promoted = envelope_tools.parse(out)
            assert code == 0 and promoted["ok"] is True, (
                f"FAIL AC-15: m1 advancement failed: {promoted!r}"
            )

        code, out, _err = run_cli(["orient", "--feature", "nl-screening", "--json"])
        orient = envelope_tools.parse(out)
        assert code == 0 and orient["data"]["current_task"]["id"] == "t2", (
            "FAIL AC-15: orient must see the current implementation task"
        )

        code, out, _err = run_cli(["kickoff", "--feature", "nl-screening", "--json"])
        kickoff = envelope_tools.parse(out)
        assert code == 0 and kickoff["data"]["stage"] == "implement", (
            "FAIL AC-15: kickoff must render the implement briefing"
        )

        for relative in (
            "src/example/screening/validate",
            "src/tests/screening/validate",
        ):
            owned = host / relative
            owned.mkdir(parents=True, exist_ok=True)
            (owned / "heddle_fake_reviewed_input.py").write_text(
                "VALUE = 7\n", encoding="utf-8"
            )

        before_task = _state(host)["revision"]
        code, out, _err = run_cli(
            ["task", "done", "t2", "--feature", "nl-screening", "--json"]
        )
        task_done = envelope_tools.parse(out)
        assert code == 0 and task_done["ok"] is True, (
            "FAIL AC-6/AC-15: task done must close the implementation task"
        )
        state = _state(host)
        assert state["revision"] == before_task + 1, (
            "FAIL AC-1: mutating command must bump revision once"
        )
        assert _task_statuses(state, "m2")["t2"] == "done", (
            "FAIL AC-6: task done must persist t2 as done"
        )

        before_verify = state["revision"]
        code, out, _err = run_cli(
            ["verify", "--scope", "m2", "--feature", "nl-screening", "--json"]
        )
        verify = envelope_tools.parse(out)
        assert code == 0 and verify["ok"] is True, (
            "FAIL AC-7/AC-15: verify must run the stored m2 command"
        )
        state = _state(host)
        facts = [fact for fact in state["verifications"] if fact["scope"] == "m2"]
        assert state["revision"] == before_verify + 1 and facts[-1]["exit_code"] == 0, (
            "FAIL AC-7: verify must append a passing m2 verification fact"
        )
        assert (host / "plans" / "nl-screening" / facts[-1]["log"]).is_file(), (
            "FAIL AC-7: verify must capture output to the recorded log path"
        )

        close_current_milestone_review("m2")

        before_advance = _state(host)["revision"]
        code, out, _err = run_cli(
            ["milestone", "advance", "--feature", "nl-screening", "--json"]
        )
        advance = envelope_tools.parse(out)
        assert code == 0 and advance["ok"] is True, (
            f"FAIL AC-5/AC-15: milestone advance must complete verified m2: {advance}"
        )
        state = _state(host)
        statuses = {m["id"]: m["status"] for m in state["milestones"]}
        assert state["revision"] == before_advance + 1
        assert statuses["m2"] == "done", (
            f"FAIL AC-5: m2 should be done after advance, got {statuses}"
        )

        for scope in ("acceptance", "smoke"):
            code, out, _err = run_cli(
                ["verify", "--scope", scope, "--feature", "nl-screening", "--json"]
            )
            assert code == 0, out
        state = _state(host)
        before_exit = state["revision"]
        code, out, _err = run_cli(["phase-exit", "--feature", "nl-screening", "--json"])
        phase_exit = envelope_tools.parse(out)
        assert code == 0 and phase_exit["ok"] is True, (
            "FAIL AC-10/AC-15: phase-exit must exit implement after the loop"
        )
        envelope_tools.assert_shape(phase_exit)
        state = _state(host)
        assert state["revision"] == before_exit + 1
        assert state["stage"] == "peer-review", (
            f"FAIL AC-11/AC-15: implement should advance to peer-review, got "
            f"{state['stage']!r}"
        )
        assert state["authorized_through"] == "peer-review", (
            "FAIL AC-11: bare phase-exit must self-grant the next stage"
        )
        assert "phase_exit_summary" in phase_exit["data"], (
            "FAIL AC-13: phase-exit must emit its summary skeleton in data"
        )
        assert fixture_state_path.read_bytes() == fixture_before, (
            "FAIL AC-15: acceptance must not mutate committed fixtures"
        )

    def test_ac10_ac18_blocked_phase_exit_reports_block_and_writes_nothing(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        host = _copy_host(tmp_path, EDGE)
        monkeypatch.chdir(host)
        state_path = host / "plans" / "blocked-task-incomplete" / "state.yaml"
        before = state_path.read_bytes()
        code, out, _err = run_cli(
            [
                "phase-exit",
                "--feature",
                "blocked-task-incomplete",
                "--dry-run",
                "--json",
            ]
        )
        envelope = envelope_tools.parse(out)
        assert code == 3 and envelope["error"]["code"] == "task-incomplete", (
            "FAIL AC-10/AC-18: blocked dry-run phase-exit must report the block"
        )
        assert envelope["next_actions"], (
            "FAIL AC-10: blocked phase-exit must include next_actions"
        )
        assert state_path.read_bytes() == before, (
            "FAIL AC-18: blocked phase-exit changed state.yaml"
        )

    def test_ac14_ac16_ac17_manifest_and_guardrail_exit_bars(self, run_cli) -> None:
        from heddle.runtime.contracts import COMMAND_SURFACE

        by_name = {contract.name: contract for contract in COMMAND_SURFACE}
        write_commands = {
            "phase-exit",
            "milestone advance",
            "task add",
            "task done",
            "task current",
            "verify",
            "session log",
        }
        for name in write_commands:
            contract = by_name[name]
            assert contract.output_schema is not None, (
                f"FAIL AC-14/AC-17: {name} must have flipped to a live schema"
            )
            expected = (
                (0, 1, 2, 3, 4, 5)
                if name in {"phase-exit", "milestone advance"}
                else (0, 1, 2, 3, 5)
            )
            assert contract.dry_run and tuple(contract.exit_codes) == expected
        assert by_name["milestone add"].cli_binding is not None

        expected_guardrails = {
            "check-prompt-conventions",
            "check-no-src-print",
            "check-subprocess-handlers",
            "check-enforcement-baseline-doc-language-aware",
            "check-ac-test-coverage",
        }
        # The four workflow-owned guards moved into the packaged hook
        # library (adoption host-tooling increment 1, owner rulings 2026-07-22).
        packaged = {
            "check-test-deletions",
            "check-bypass-justification",
            "check-no-committed-gate-locks",
            "check-skip-only-test-scaffolding",
        }
        scripts = {
            path.name.removesuffix(".py").removesuffix(".sh")
            for path in (REPO_ROOT / "scripts").iterdir()
        }
        missing = expected_guardrails - scripts
        assert not missing, f"FAIL AC-16: missing guardrails {sorted(missing)}"
        hooks_root = REPO_ROOT / "heddle" / "resources" / "hooks"
        missing_packaged = {
            name for name in packaged if not (hooks_root / f"{name}.py").is_file()
        }
        assert not missing_packaged, (
            f"FAIL AC-16: missing packaged guardrails {sorted(missing_packaged)}"
        )

        code, _out, _err = run_cli(["help", "--json"])
        assert code == 0, "FAIL AC-14: help --json manifest must still render"


@pytest.mark.e2e
def test_ac15_alpha_loop_end_to_end_no_hand_edited_operational_state(
    run_cli,
    envelope_tools,
    fake_gate_runner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    TestWritePathAcceptance().assert_ac15_alpha_loop_end_to_end_no_hand_edited_operational_state(
        run_cli,
        envelope_tools,
        fake_gate_runner,
        tmp_path,
        monkeypatch,
    )
