"""Workflow-usability declaration, resume, and verification guidance."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.gate.extraction import extract_explicit_test_paths
from heddle.gate.preparation import declared_scaffold_paths
from heddle.gate.registry import GATES
from heddle.gate.types import GateContext
from heddle.kernel.project_config import KernelError
from heddle.runtime import application
from tests.tiering_helpers import entry, snapshot
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    gate_command,
    provider_transport,
    review_content,
)


def _context(root: Path, plan: str, commands: dict[str, str]) -> GateContext:
    return GateContext(
        gate_type=GATES["review-test-scaffolding"],
        feature="declaration-probe",
        repo_root=root,
        plan_path=root / "plan.md",
        spec_path=root / "spec.md",
        milestone=None,
        plan_content=plan,
        verification_commands=commands,
    )


@pytest.mark.parametrize(
    ("source", "token"),
    [
        ("plan", "tests/runtime/test_{alpha,beta}.py"),
        ("command", "tests/runtime/test_workflow_*.py"),
    ],
)
def test_ac7_unsupported_declaration_reports_full_token_and_context(
    tmp_path: Path, source: str, token: str
) -> None:
    """AC-7 red: brace/glob shorthand never degrades to a phantom path."""
    plan = f"Planned: `{token}`.\n" if source == "plan" else "# Plan\n"
    commands = (
        {"acceptance_test": f"python -m pytest {token}"} if source == "command" else {}
    )
    with pytest.raises(KernelError) as caught:
        declared_scaffold_paths(
            _context(tmp_path, plan, commands), require_existing=False
        )
    rendered = f"{caught.value.message} {caught.value.hint}"
    assert token in rendered, "FAIL AC-7: diagnostic truncated the authored token"
    assert source in rendered.casefold()
    assert "exact" in rendered.casefold() and "file" in rendered.casefold()


def test_ac7_survivor_exact_selector_still_binds_its_file() -> None:
    """AC-7 survivor: supported pytest selectors keep their file identity."""
    text = "Run `tests/runtime/test_contracts.py::TestAC2ManifestShape`.\n"
    assert extract_explicit_test_paths(text, tests_root="tests") == (
        "tests/runtime/test_contracts.py",
    )


@pytest.mark.parametrize("source", ["plan", "command"])
def test_ac7_parameterized_selector_is_not_glob_shorthand(
    tmp_path: Path, source: str
) -> None:
    """AC-7: pytest parameter IDs remain selector syntax, not path globbing."""
    token = "tests/runtime/test_example.py::test_value[case]"
    plan = f"Run `{token}`.\n" if source == "plan" else "# Plan\n"
    commands = (
        {"acceptance_test": f"python -m pytest {token}"} if source == "command" else {}
    )
    assert declared_scaffold_paths(
        _context(tmp_path, plan, commands), require_existing=False
    ) == ("tests/runtime/test_example.py",)


def test_ac7_survivor_code_spans_fences_and_directories_bind_exact_files(
    tmp_path: Path,
) -> None:
    """AC-7 survivor: every documented supported declaration form stays usable."""
    tests = tmp_path / "tests/runtime"
    tests.mkdir(parents=True)
    (tests / "test_alpha.py").write_text("def test_alpha(): pass\n")
    (tests / "test_beta.py").write_text("def test_beta(): pass\n")
    plan = (
        "Inline `tests/runtime/test_alpha.py::test_alpha`.\n\n"
        "```sh\npython -m pytest tests/runtime/test_beta.py\n```\n\n"
        "Directory `tests/runtime`.\n"
    )
    extracted = extract_explicit_test_paths(plan, tests_root="tests")
    assert extracted == (
        "tests/runtime/test_alpha.py",
        "tests/runtime/test_beta.py",
        "tests/runtime",
    )
    declared = declared_scaffold_paths(
        _context(tmp_path, plan, {}), require_existing=True
    )
    assert declared == (
        "tests/runtime/test_alpha.py",
        "tests/runtime/test_beta.py",
    )


def test_ac7_ac9_actual_scaffold_gate_refuses_unsupported_token_before_provider(
    tmp_path: Path, monkeypatch, run_cli
) -> None:
    """AC-7/AC-9 red: launch reports the authored token without a provider call."""
    _host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="scaffold",
        overrides={
            "review-test-scaffolding": entry(
                "review-test-scaffolding", minimum_rounds=1, limit=2
            )
        },
    )
    token = "tests/runtime/test_{alpha,beta}.py"
    plan = state_path.with_name("plan.md")
    plan.write_text(plan.read_text() + f"\nPlanned: `{token}`.\n")
    calls = provider_transport(monkeypatch, review_content("review-test-scaffolding"))
    code, result = gate_command(run_cli, "run-gate", "review-test-scaffolding")
    assert code != 0 and not result["ok"]
    rendered = str(result)
    assert token in rendered and "plan" in rendered.casefold()
    assert "exact" in rendered.casefold() and "file" in rendered.casefold()
    assert not calls


@pytest.mark.parametrize("stage", ["specify", "scaffold"])
def test_ac8_authoring_guidance_is_read_only_and_separate_from_actions(
    tmp_path, monkeypatch, run_cli, stage: str
) -> None:
    """AC-8 red: fresh authoring stages expose guidance without rescheduling."""
    host, _state_path = current_host(tmp_path, monkeypatch, stage=stage)
    before = snapshot(host)
    status = application.execute(ops.Status(feature=V7_FEATURE))
    orient = application.execute(ops.Orient(feature=V7_FEATURE))
    kickoff = application.execute(ops.Kickoff(feature=V7_FEATURE))

    assert status.ok and orient.ok and kickoff.ok
    assert status.next_actions == orient.next_actions
    for result in (status, orient, kickoff):
        guidance = result.data["authoring_guidance"]
        assert guidance == {
            "stage": stage,
            "work": "stage-work",
            "briefing_command": f"heddle kickoff --feature {V7_FEATURE}",
            "read_only": True,
        }
    assert status.next_actions[0].action != status.data["authoring_guidance"]
    code, output, error = run_cli(["orient", "--feature", V7_FEATURE])
    assert code == 0, error
    assert "guidance (read-only): stage-work" in output
    briefing = f"heddle kickoff --feature {V7_FEATURE}"
    assert f"briefing: {briefing}" in output
    assert f"next: {briefing}" in output
    assert snapshot(host) == before


def test_ac8_resumed_phase_exit_keeps_authoring_guidance_and_front_action(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-8 red: a resumed authoring session keeps phase exit as executable front."""
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="scaffold",
        overrides={
            "review-test-scaffolding": entry(
                "review-test-scaffolding", mode="off", limit=None, minimum_rounds=0
            )
        },
    )
    revision = yaml.safe_load(state_path.read_text())["revision"]
    recorded = application.execute(
        ops.RecordSession(
            ops.SessionInput(
                started_at="2026-09-17T12:00Z",
                ended_at="2026-09-17T12:01Z",
                completed=("scaffold authored",),
                started=(),
                key_context="ready for the legal boundary",
                next_steps="exit scaffold",
                blockers=(),
                stage="scaffold",
                revision_before=revision,
                revision_after=revision,
                advanced=True,
            ),
            feature=V7_FEATURE,
            expect_revision=revision,
        )
    )
    assert recorded.ok, recorded.to_envelope()
    before = snapshot(host)
    status = application.execute(ops.Status(feature=V7_FEATURE))
    assert isinstance(status.next_actions[0].action, ops.CommandAction)
    assert isinstance(status.next_actions[0].action.operation, ops.PhaseExit)
    assert status.data["authoring_guidance"] == {
        "stage": "scaffold",
        "work": "stage-work",
        "briefing_command": f"heddle kickoff --feature {V7_FEATURE}",
        "read_only": True,
    }
    code, output, error = run_cli(["orient", "--feature", V7_FEATURE])
    assert code == 0, error
    briefing = f"heddle kickoff --feature {V7_FEATURE}"
    assert "guidance (read-only): stage-work" in output
    assert f"briefing: {briefing}" in output
    assert "next: heddle phase-exit" in output
    assert f"next: {briefing}" not in output
    assert snapshot(host) == before


def test_ac10_verify_help_names_only_supported_native_scopes(run_cli) -> None:
    """AC-10 red: help separates native proof from progressive commands."""
    code, output, error = run_cli(["verify", "--help"])
    folded = " ".join((output + error).split()).casefold()
    assert code == 0
    assert "feature|" not in folded, "FAIL AC-10: unsupported feature scope advertised"
    assert "m<n>" in folded and "smoke" in folded and "acceptance" in folded
    assert "commands set test_command" in folded or "progressive" in folded


def test_ac9_ac10_survivor_unsupported_feature_scope_executes_nothing(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-9/AC-10 survivor: unsupported proof fails without writing state."""
    host, _state_path = current_host(tmp_path, monkeypatch, stage="implement")
    before = snapshot(host)
    code, output, error = run_cli(
        ["verify", "--scope", "feature", "--feature", V7_FEATURE, "--json"]
    )
    rendered = " ".join((output + error).split()).casefold()
    assert code != 0 and "feature" in rendered
    assert "supported native scopes: m<n>, smoke, acceptance, or live" in rendered
    assert "commands set test_command" in rendered
    assert "m<n>, feature" not in rendered
    assert snapshot(host) == before


def test_ac10_survivor_session_command_evidence_cannot_satisfy_native_proof(
    tmp_path, monkeypatch
) -> None:
    """AC-10 survivor: prose session evidence never becomes VerificationFact."""
    host, state_path = current_host(tmp_path, monkeypatch, stage="implement")
    before = yaml.safe_load(state_path.read_text())
    revision = before["revision"]
    result = application.execute(
        ops.RecordSession(
            ops.SessionInput(
                started_at="2026-09-17T12:00Z",
                ended_at="2026-09-17T12:02Z",
                completed=(
                    "heddle verify --scope m1 passed",
                    "heddle verify --scope smoke passed",
                    "heddle verify --scope acceptance passed",
                ),
                started=(),
                key_context="progressive command output only",
                next_steps="record native proof",
                blockers=(),
                stage="implement",
                revision_before=revision,
                revision_after=revision,
                advanced=True,
            ),
            feature=V7_FEATURE,
            expect_revision=revision,
        )
    )
    assert result.ok, result.to_envelope()
    after = yaml.safe_load(state_path.read_text())
    assert after["verifications"] == before["verifications"]
    status = application.execute(ops.Status(feature=V7_FEATURE))
    rows = {
        row["scope"]: row["freshness"] for row in status.data["verification_status"]
    }
    assert all(rows.get(scope) != "passed" for scope in ("m1", "smoke", "acceptance"))
    assert not any(path.name.endswith(".log") for path in host.rglob("*.log"))
