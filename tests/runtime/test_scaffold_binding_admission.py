"""Runtime admission for scaffold primary bindings."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.content_identity_helpers import git
from tests.operational_model_helpers import SPEC
from tests.tiering_helpers import snapshot
from tests.tiering_review_helpers import (
    current_host,
    gate_command,
    provider_transport,
    review_content,
    runs,
)


def _scaffold_host(tmp_path: Path, monkeypatch, *, valid: bool) -> tuple[Path, Path]:
    host, state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    (host / SPEC).write_text(
        "# Product\n\n## Acceptance Criteria\n\n"
        "### AC-1: Declared value\n\n"
        "Priority: MUST\nAction: Read VALUE.\nExpected: The value is 7.\n"
        "Verified-by: tests/check.py::test_declared_value\n",
        encoding="utf-8",
    )
    name = "test_declared_value" if valid else "test_other_value"
    (host / "tests/check.py").write_text(
        "from pathlib import Path\n\n"
        f"def {name}():\n"
        "    assert 'VALUE = 7' in Path('src/example.py').read_text()\n",
        encoding="utf-8",
    )
    return host, state_path


def _interpretation(data: dict, content: dict) -> dict:
    return {
        "schema": "heddle.review-interpretation-input/v1",
        "run_id": data["run_id"],
        "capture_sha256": data["capture_sha256"],
        "author": "lead",
        "reason": (
            "Preserved the complete scaffold review and removed extra presentation."
        ),
        "content": content,
    }


def test_invalid_binding_refuses_without_consuming_review(
    tmp_path: Path, monkeypatch, run_cli
) -> None:
    host, state_path = _scaffold_host(tmp_path, monkeypatch, valid=False)
    content = review_content("review-test-scaffolding")
    calls = provider_transport(monkeypatch, content)
    state_before = state_path.read_bytes()

    preview_code, preview = gate_command(
        run_cli, "show-prompt", "review-test-scaffolding"
    )
    run_code, refused = gate_command(run_cli, "run-gate", "review-test-scaffolding")

    assert preview_code != 0 and run_code != 0
    for result in (preview, refused):
        assert not result["ok"]
        assert "AC-1" in str(result)
        assert "tests/check.py::test_declared_value" in str(result)
    assert not calls
    assert state_path.read_bytes() == state_before
    assert (
        yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"] == []
    )

    (host / "tests/check.py").write_text(
        "from pathlib import Path\n\n"
        "def test_declared_value():\n"
        "    assert 'VALUE = 7' in Path('src/example.py').read_text()\n",
        encoding="utf-8",
    )
    repaired_preview_code, repaired_preview = gate_command(
        run_cli, "show-prompt", "review-test-scaffolding"
    )
    accepted_code, accepted = gate_command(
        run_cli, "run-gate", "review-test-scaffolding"
    )

    assert repaired_preview_code == 0, repaired_preview
    assert accepted_code == 0 and accepted["ok"], accepted
    assert len(calls) == 1
    state = yaml.safe_load(state_path.read_text())
    assert len(state["review_assignments"]["attempts"]) == 1
    assert len(runs(state_path)) == 1


def test_retained_scaffold_review_uses_original_capture_after_source_change(
    tmp_path: Path, monkeypatch, run_cli
) -> None:
    host, state_path = _scaffold_host(tmp_path, monkeypatch, valid=True)
    content = review_content("review-test-scaffolding")
    calls = provider_transport(monkeypatch, {**content, "extra": "retained detail"})

    code, warning = gate_command(run_cli, "run-gate", "review-test-scaffolding")
    assert code == 4 and warning["ok"], warning
    data = warning["data"]
    assert not data["accepted"] and len(calls) == 1 and not runs(state_path)
    artifact = host / data["artifact"]
    original_capture = artifact.read_bytes()
    captured = json.loads(original_capture)
    captured_basis = captured["invocation"]["review_basis_hash"]

    (host / "tests/check.py").write_text(
        "def test_binding_removed():\n    assert True\n", encoding="utf-8"
    )
    before = snapshot(host)
    replay_code, replay = gate_command(run_cli, "run-gate", "review-test-scaffolding")

    assert replay_code == 4 and replay["data"]["cached"], replay
    assert replay["data"]["run_id"] == data["run_id"]
    assert len(calls) == 1 and snapshot(host) == before
    interpreted = execute(ops.InterpretReview(payload=_interpretation(data, content)))
    assert interpreted.ok, interpreted.to_envelope()
    recorded = runs(state_path)[0]
    assert recorded["review_basis_hash"] == captured_basis
    assert artifact.read_bytes() == original_capture
    assert len(calls) == 1


def test_binding_only_source_change_during_provider_refuses_stale_review(
    tmp_path: Path, monkeypatch, run_cli
) -> None:
    host, state_path = _scaffold_host(tmp_path, monkeypatch, valid=True)
    binding = host / "quality/binding_only.py"
    binding.parent.mkdir()
    binding.write_text(
        "def test_declared_value():\n    assert True\n", encoding="utf-8"
    )
    git(host, "add", "quality/binding_only.py")
    git(host, "commit", "-qm", "binding-only source")
    (host / SPEC).write_text(
        "# Product\n\n## Acceptance Criteria\n\n"
        "### AC-1: Declared value\n\n"
        "Priority: MUST\nAction: Read VALUE.\nExpected: The value is 7.\n"
        "Verified-by: quality/binding_only.py::test_declared_value\n",
        encoding="utf-8",
    )

    def response(_cli, _prompt):
        binding.write_text(
            "def test_declared_value_removed():\n    assert True\n",
            encoding="utf-8",
        )
        return review_content("review-test-scaffolding")

    calls = provider_transport(monkeypatch, response)
    code, refused = gate_command(run_cli, "run-gate", "review-test-scaffolding")

    assert code != 0 and not refused["ok"], refused
    assert len(calls) == 1
    recorded = runs(state_path)
    assert len(recorded) == 1
    assert recorded[0]["verdict"]["status"] == "error"
    assert recorded[0]["failure_reason"] == "reviewed-source-changed"
    attempts = yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"]
    assert len(attempts) == 1 and attempts[0]["source"] is None


def test_binding_admission_is_limited_to_feature_bound_scaffold_preparation(
    tmp_path: Path, monkeypatch
) -> None:
    from heddle.gate import entry
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.prompt import resolve_gate_prompt
    from heddle.gate.registry import GATES
    from heddle.gate.types import GateInvocationOverrides
    from tests.content_identity_helpers import capture_context

    host, _state_path = _scaffold_host(tmp_path, monkeypatch, valid=False)
    other = capture_context(host, role="spec-review", lane="codex")
    invocation = resolve_gate_execution(
        other.gate_type, GateInvocationOverrides(cli="codex")
    )
    prepared = entry.prepare_gate_run(
        other, gate_type=other.gate_type, invocation=invocation
    )
    assert prepared.gate == "spec-review"

    template_context = entry.featureless_gate_context(
        GATES["review-test-scaffolding"], host
    )
    prompt = resolve_gate_prompt(
        template_context, template_context.gate_type, cli="codex"
    )
    assert prompt.effective_instructions
    assert template_context.feature == "[feature]"
