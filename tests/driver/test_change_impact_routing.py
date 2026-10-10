"""change-impact-rebinding-v1 driver routing: AC-8 (driver half) and AC-11.

The real driver loop, readiness, application, gate engine and recording run in
process on synthetic hosts. Only the provider transport is doubled; the
phase-session spawn is replaced by a recorder that ends the drive, and every
operation the driver executes is recorded.

Activation seams, each failing as an assertion until implemented:

- the ``baseline-unavailable`` blocking condition paired with the manual
  remedy ``heddle feature baseline set`` (today the driver launches the
  scaffolding review against the ``HEAD`` cascade);
- ``review-reaffirm`` authoring work (today the driver's stage session
  carries ``review-disposition`` work for stale rows).
"""

from __future__ import annotations

import json

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.driver import loop
from heddle.driver.sessions import SessionResult
from heddle.kernel.project_config import load_project_config
from tests.change_impact_routing_helpers import (
    PEER_CITATIONS,
    SPEC_CITATIONS,
    baseline_unavailable_host,
    change_citation,
    disposed_peer_reviews,
    disposed_spec_review,
    stale_basis,
)
from tests.tiering_review_helpers import V7_FEATURE, provider_transport, review_content


def _drive(monkeypatch, host, *, until):
    """One bounded drive that records sessions and executed operations."""
    from heddle.runtime import application

    prompts: list[str] = []
    executed: list[object] = []
    original = application.execute

    def session(spec, **_kwargs):
        prompts.append(spec.prompt_path.read_text())
        return SessionResult(exit_code=17, termination="completed", output={})

    def recording(operation):
        executed.append(operation)
        return original(operation)

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    monkeypatch.setattr(loop, "spawn_phase_session", session)
    monkeypatch.setattr(application, "execute", recording)
    result = loop.run_loop(load_project_config(host), V7_FEATURE, until=until)
    return result, prompts, executed


def _ledger(path):
    return yaml.safe_load(path.read_text())["review_assignments"]


@pytest.mark.parametrize("reason", ["missing", "diverged"])
def test_ac8_driver_blocks_on_baseline_unavailable_instead_of_routing_a_review(
    tmp_path, monkeypatch, reason
):
    host, path, _oid = baseline_unavailable_host(tmp_path, monkeypatch, reason)
    calls = provider_transport(monkeypatch, review_content("review-test-scaffolding"))
    before = path.read_bytes()
    result, prompts, executed = _drive(monkeypatch, host, until="implement")
    assert not calls, (
        f"FAIL AC-8: the driver launched a review while the baseline is {reason}"
    )
    assert not any(isinstance(op, (ops.RunGate, ops.RunGates)) for op in executed), (
        "FAIL AC-8: the driver routed a review while the baseline is unavailable"
    )
    assert not prompts, "FAIL AC-8: the driver opened a session instead of blocking"
    envelope = result.to_envelope()
    assert not result.ok and result.error.code == "baseline-unavailable", (
        f"FAIL AC-8: the driver must block on baseline-unavailable: {envelope}"
    )
    action = result.next_actions[0].action
    assert isinstance(action, ops.ManualAction), envelope
    assert "heddle feature baseline set" in action.instruction, (
        f"FAIL AC-8: the block must name the re-point remedy: {envelope}"
    )
    assert reason in json.dumps(envelope), (
        f"FAIL AC-8: the block must name the reason ({reason}): {envelope}"
    )
    assert path.read_bytes() == before


def test_ac11_driver_opens_a_stage_session_for_review_reaffirm_and_never_runs_it(
    tmp_path, monkeypatch
):
    host, path, origin, calls = disposed_spec_review(tmp_path, monkeypatch)
    stale_basis(host)
    change_citation(host, "docs/second.md")
    before = path.read_bytes()
    result, prompts, executed = _drive(monkeypatch, host, until="plan-review")
    assert len(prompts) == 1, f"FAIL AC-11: expected one stage session: {prompts}"
    assert "Authoring work: review-reaffirm" in prompts[0], (
        "FAIL AC-11: the driver's stage session must carry review-reaffirm work"
    )
    for finding_id in SPEC_CITATIONS:
        assert f"{origin}#{finding_id}" in prompts[0], finding_id
    assert not any(isinstance(op, ops.ReaffirmReview) for op in executed), (
        "FAIL AC-11: the driver executed review reaffirm itself"
    )
    assert not result.ok and "17" in result.error.message, result.to_envelope()
    assert path.read_bytes() == before and len(calls) == 1


def test_ac11_driver_session_names_every_row_of_the_one_review_reaffirm(
    tmp_path, monkeypatch
):
    """Across two assignments the session carries one review-reaffirm work item
    naming every row the role-free command writes or refuses."""
    from heddle.runtime.application import execute

    host, path, origins, calls = disposed_peer_reviews(tmp_path, monkeypatch)
    # Peer review needs every done milestone verified, and refreshes acceptance
    # and smoke verification before its review work; each run passes.
    for name in ("test_command", "acceptance_test", "smoke_test"):
        declared = execute(
            ops.CommandsSet(name, "python3 tests/check.py", feature=V7_FEATURE)
        )
        assert declared.ok, declared.to_envelope()
    for milestone in yaml.safe_load(path.read_text())["milestones"]:
        verified = execute(ops.Verify(scope=milestone["id"], feature=V7_FEATURE))
        assert verified.ok, verified.to_envelope()
    stale_basis(host)
    change_citation(host, "docs/second.md")
    before = _ledger(path)
    result, prompts, executed = _drive(monkeypatch, host, until="robustness")
    assert len(prompts) == 1, f"FAIL AC-11: expected one stage session: {prompts}"
    lines = prompts[0].splitlines()
    assert lines.count("Authoring work: review-reaffirm") == 1, prompts[0]
    # The work's own references, not the session's list of open obligations.
    references = lines[lines.index("Authoring work: review-reaffirm") + 1]
    assert references.startswith("References: "), prompts[0]
    for role, citations in PEER_CITATIONS.items():
        for finding_id in citations:
            assert f"{origins[role]}#{finding_id}" in references, (
                f"FAIL AC-11: the review-reaffirm work must name {role} "
                f"{finding_id}: {references}"
            )
    assert not any(isinstance(op, ops.ReaffirmReview) for op in executed), (
        "FAIL AC-11: the driver executed review reaffirm itself"
    )
    assert not result.ok and "17" in result.error.message, result.to_envelope()
    assert _ledger(path) == before and len(calls) == 2
