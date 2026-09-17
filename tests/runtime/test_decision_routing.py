"""AC-2/4: real kickoff/driver and native policy publication boundaries."""

from dataclasses import replace

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.contracts.decisions import PolicyResolutionInput
from heddle.runtime.application import execute
from tests.tiering_review_helpers import V7_FEATURE, current_host


def policy(**changes):
    item = PolicyResolutionInput(
        id="P1",
        title="Choice",
        question="Which?",
        options=("keep", "change"),
        choice="keep",
        rationale="Keep the reversible choice",
        basis="none",
        reversibility="reversible",
        confidence="high",
        scope="feature",
        routes_to=("docs/features/example.md",),
        review_flag="[REVIEW]",
    )
    return replace(item, **changes)


def test_kickoff_composes_once_and_driver_preserves_exact_result(tmp_path, monkeypatch):
    from heddle.driver.loop import _write_prompt
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    host, state = current_host(tmp_path, monkeypatch, stage="implement")
    override = host / ".heddle/overrides"
    override.mkdir(parents=True)
    (override / "decision-routing.md").write_text("Unique common policy  \n")
    (override / "implement.briefing.md").write_text("Unique stage body\n")
    before = state.read_bytes()
    result = execute(ops.Kickoff(V7_FEATURE))
    assert result.ok, result
    data = result.data
    assert data["briefing"].count("Unique common policy") == 1
    assert data["briefing"].count("Unique stage body") == 1
    assert data["source"] == "override"
    assert data["decision_policy_source"] == "override"
    snapshot = resolve_snapshot(load_project_config(host), V7_FEATURE)
    prompt = _write_prompt(snapshot, "continue", None, scratch_dir=tmp_path)
    assert prompt.read_text().count(data["briefing"]) == 1
    assert state.read_bytes() == before


@pytest.mark.parametrize(
    "asset,body",
    [
        ("decision-routing.md", b" \n"),
        ("decision-routing.md", b"\xff"),
        ("implement.briefing.md", b" \n"),
    ],
)
def test_invalid_delivery_stops_driver_before_prompt_creation(
    tmp_path, monkeypatch, asset, body
):
    from heddle.driver.loop import _write_prompt
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    host, _ = current_host(tmp_path, monkeypatch, stage="implement")
    target = host / ".heddle/overrides" / asset
    target.parent.mkdir(parents=True)
    target.write_bytes(body)
    snapshot = resolve_snapshot(load_project_config(host), V7_FEATURE)
    result = _write_prompt(snapshot, "continue", None, scratch_dir=tmp_path)
    assert not result.ok
    assert result.error.code == "workspace-invalid"
    assert asset in result.error.message or asset in result.error.hint
    assert not list(tmp_path.glob("heddle-prompt-*"))


@pytest.mark.parametrize("basis", ["conflict", "ConFLict"])
def test_new_conflict_and_mixed_batch_refuse_without_publication(
    tmp_path, monkeypatch, basis
):
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime.audit import decision_journal_path

    host, state = current_host(tmp_path, monkeypatch, stage="implement")
    before = state.read_bytes()
    result = execute(
        ops.RecordPolicy((policy(), policy(id="P2", basis=basis)), feature=V7_FEATURE)
    )
    assert not result.ok
    assert "owner" in result.error.hint.casefold()
    assert state.read_bytes() == before
    assert not decision_journal_path(load_project_config(host), V7_FEATURE).exists()


def test_complete_conflict_fact_retry_is_exact_and_readable(tmp_path, monkeypatch):
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime import decisions
    from heddle.runtime.audit import decision_journal_path

    host, state = current_host(tmp_path, monkeypatch, stage="implement")
    item = policy(basis="conflict")
    document = yaml.safe_load(state.read_text())
    document["decisions"].append(decisions._policy_fact(item))
    state.write_text(yaml.safe_dump(document, sort_keys=False))
    journal = decision_journal_path(load_project_config(host), V7_FEATURE)
    journal.write_text("# Journal\n\n## " + decisions._policy_section(item) + "\n")
    before = (state.read_bytes(), journal.read_bytes())
    result = execute(ops.RecordPolicy((item,), feature=V7_FEATURE))
    assert result.ok, result
    assert (state.read_bytes(), journal.read_bytes()) == before
    changed = execute(
        ops.RecordPolicy((replace(item, rationale="different"),), feature=V7_FEATURE)
    )
    assert not changed.ok
    assert (state.read_bytes(), journal.read_bytes()) == before


def test_journal_only_conflict_cannot_prove_option_identity(tmp_path, monkeypatch):
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime import decisions
    from heddle.runtime.audit import decision_journal_path

    host, state = current_host(tmp_path, monkeypatch, stage="implement")
    old = policy(basis="conflict", options=("keep", "a, b", "c"))
    collision = replace(old, options=("keep", "a", "b, c"))
    assert decisions._policy_section(old) == decisions._policy_section(collision)
    journal = decision_journal_path(load_project_config(host), V7_FEATURE)
    journal.write_text("# Journal\n\n## " + decisions._policy_section(old) + "\n")
    before = (state.read_bytes(), journal.read_bytes())
    for operation in (ops.Status(V7_FEATURE), ops.Orient(V7_FEATURE)):
        observed = execute(operation)
        assert observed.ok, observed
        diagnostic = next(
            d for d in observed.diagnostics if d.code == "policy-journal-unbacked"
        )
        assert "for nonconflict entries" in diagnostic.message
        assert "journal-only conflict requires an owner ruling" in diagnostic.message
        assert (
            f"heddle decisions add --feature {V7_FEATURE} --input-json <path>"
            in diagnostic.message
        )
    for item in (old, collision):
        result = execute(ops.RecordPolicy((item,), feature=V7_FEATURE))
        assert not result.ok
        assert "owner" in result.error.hint.casefold()
        assert (state.read_bytes(), journal.read_bytes()) == before

    from heddle.runtime.contracts import POLICY_BATCH_INPUT_SCHEMA

    help_notes = " ".join(POLICY_BATCH_INPUT_SCHEMA["notes"])
    assert "none requires [REVIEW]" in help_notes
    assert "new conflict choices require an owner ruling" in help_notes
    assert "complete-fact retries remain supported, including conflict" in help_notes
    assert "nonconflict journal-only entries allow identical retry" in help_notes
    assert "journal-only conflict requires an owner ruling" in help_notes


def test_nonconflict_journal_first_failure_recovers(tmp_path, monkeypatch):
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime import decisions
    from heddle.runtime.audit import decision_journal_path

    host, state = current_host(tmp_path, monkeypatch, stage="implement")
    original = decisions.state_store.os.replace

    def fail_state(src, dst, *args, **kwargs):
        if str(dst) == str(state):
            raise OSError("synthetic state publication failure")
        return original(src, dst, *args, **kwargs)

    before = state.read_bytes()
    monkeypatch.setattr(decisions.state_store.os, "replace", fail_state)
    failed = execute(ops.RecordPolicy((policy(),), feature=V7_FEATURE))
    assert not failed.ok
    assert state.read_bytes() == before
    journal = decision_journal_path(load_project_config(host), V7_FEATURE)
    published = journal.read_bytes()
    monkeypatch.setattr(decisions.state_store.os, "replace", original)
    result = execute(ops.RecordPolicy((policy(),), feature=V7_FEATURE))
    assert result.ok, result
    assert journal.read_bytes() == published
    assert len(yaml.safe_load(state.read_text())["decisions"]) == 1


def test_public_preview_and_doctor_share_policy_identity(tmp_path, monkeypatch):
    host, _ = current_host(tmp_path, monkeypatch)
    path = host / ".heddle/overrides/decision-routing.md"
    path.parent.mkdir(parents=True)
    path.write_text("Public policy\n")
    first = execute(ops.ShowPrompt(gate="spec-review", feature=V7_FEATURE))
    assert first.ok, first
    assert first.data["decision_policy_source"] == "override"
    assert first.data["prompt"].count("Public policy") == 1
    assert execute(ops.Kickoff(V7_FEATURE)).data["decision_policy_source"] == "override"
    path.write_text("Public policy\n ")
    second = execute(ops.ShowPrompt(gate="spec-review", feature=V7_FEATURE))
    assert second.ok, second
    for key in (
        "prompt_version",
        "effective_prompt_sha256",
        "review_basis_hash",
        "input_hash",
    ):
        assert first.data["prompt_identity"][key] != second.data["prompt_identity"][key]
    diagnosis = execute(ops.Doctor(feature=V7_FEATURE))
    assert any(
        d.code == "resource-decision-routing.md" and "override" in d.message
        for d in diagnosis.diagnostics
    )
    path.write_text(" ")
    invalid = execute(ops.ShowPrompt(gate="spec-review", feature=V7_FEATURE))
    assert not invalid.ok and "decision-routing.md" in invalid.error.message
    diagnosis = execute(ops.Doctor(feature=V7_FEATURE))
    assert any(
        d.code == "resource-missing"
        and "decision-routing.md" in d.message
        and "restore" in d.message
        for d in diagnosis.diagnostics
    )


def test_driver_still_rejects_malformed_success(monkeypatch):
    from heddle.contracts.result import HeddleResult
    from heddle.driver import loop

    monkeypatch.setattr(
        loop.application,
        "execute",
        lambda op: HeddleResult.success({"briefing": " \n"}),
    )
    result = loop._stage_briefing(V7_FEATURE)
    assert not result.ok and result.error.code == "internal"
    assert result.next_actions[0].command == f"heddle kickoff --feature {V7_FEATURE}"
