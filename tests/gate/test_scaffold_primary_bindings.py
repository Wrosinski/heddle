"""Native scaffold capture and primary-binding admission."""

from __future__ import annotations

from pathlib import Path

import pytest

from heddle.kernel.project_config import KernelError
from tests.content_identity_helpers import capture_context, git
from tests.operational_model_helpers import SPEC, read, write
from tests.tiering_review_helpers import current_host


def _invocation(context):
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.types import GateInvocationOverrides

    return resolve_gate_execution(
        context.gate_type,
        GateInvocationOverrides(
            cli="codex", model="fixture-model", reasoning_effort="high"
        ),
    )


def _write_spec(host: Path, rows: tuple[tuple[str, str], ...]) -> None:
    sections = ["# Product", "", "## Acceptance Criteria", ""]
    for ac_id, target in rows:
        sections.extend(
            [
                f"### {ac_id}: Required behavior",
                "",
                "Priority: MUST",
                "Action: Exercise the behavior.",
                "Expected: The behavior is preserved.",
                f"Verified-by: {target}",
                "",
            ]
        )
    (host / SPEC).write_text("\n".join(sections), encoding="utf-8")


def _scaffold_context(host: Path):
    return capture_context(host, role="review-test-scaffolding", lane="codex")


def test_required_ac_bindings_are_checked(tmp_path: Path, monkeypatch) -> None:
    from heddle.gate import entry

    host, _state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    test_path = host / "tests/test_bindings.py"
    test_path.write_text("def test_valid():\n    assert True\n", encoding="utf-8")
    _write_spec(
        host,
        (
            ("AC-1", "tests/test_bindings.py::test_valid"),
            ("AC-2", "tests/test_bindings.py::test_missing"),
        ),
    )
    context = _scaffold_context(host)
    invocation = _invocation(context)

    basis = entry.prepare_current_review_basis(context, invocation)
    assert basis.ac_ids == ("AC-1", "AC-2")
    with pytest.raises(KernelError) as raised:
        entry.prepare_gate_run(
            context, gate_type=context.gate_type, invocation=invocation
        )

    assert raised.value.code == "workspace-invalid"
    assert "AC-2: tests/test_bindings.py::test_missing" in raised.value.message
    assert "AC-1" not in raised.value.message


def test_binding_diagnostics_follow_ac_and_target_declaration_order(
    tmp_path: Path, monkeypatch
) -> None:
    from heddle.gate import entry

    host, _state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    (host / "tests/test_bindings.py").write_text("VALUE = 1\n", encoding="utf-8")
    (host / SPEC).write_text(
        "# Product\n\n## Acceptance Criteria\n\n"
        "### AC-1: First\n\n"
        "Verified-by: tests/test_bindings.py::test_first, "
        "tests/test_bindings.py::test_second\n\n"
        "### AC-2: Second\n\nNo primary declaration yet.\n",
        encoding="utf-8",
    )
    context = _scaffold_context(host)

    with pytest.raises(KernelError) as raised:
        entry.prepare_gate_run(
            context,
            gate_type=context.gate_type,
            invocation=_invocation(context),
        )

    message = raised.value.message
    assert message.index("AC-1: tests/test_bindings.py::test_first") < message.index(
        "AC-1: tests/test_bindings.py::test_second"
    )
    assert message.index("AC-1: tests/test_bindings.py::test_second") < message.index(
        "AC-2: (missing)"
    )


def test_binding_only_inputs_share_capture_and_review_basis(
    tmp_path: Path, monkeypatch
) -> None:
    from heddle.gate import entry

    host, _state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    binding_path = host / "quality/binding_witness.py"
    binding_path.parent.mkdir()
    original = (
        "def test_first():\n    assert True\n\n"
        "class TestFeature:\n"
        "    async def test_second(self):\n"
        "        assert True\n"
    )
    binding_path.write_text(original, encoding="utf-8")
    git(host, "add", "quality/binding_witness.py")
    git(host, "commit", "-qm", "unchanged binding-only witness")
    _write_spec(
        host,
        (
            ("AC-1", "quality/binding_witness.py::test_first"),
            ("AC-2", "quality/binding_witness.py::TestFeature::test_second"),
        ),
    )

    context = _scaffold_context(host)
    invocation = _invocation(context)
    prepared = entry.prepare_gate_run(
        context, gate_type=context.gate_type, invocation=invocation
    )
    basis_context = _scaffold_context(host)
    basis = entry.prepare_current_review_basis(
        basis_context, _invocation(basis_context)
    )

    assert prepared.review_basis_hash == basis.review_basis_hash
    content_rows = [
        row
        for row in prepared.reviewed_inputs
        if row.name == "reviewed/test/quality/binding_witness.py"
    ]
    identity_rows = [
        row
        for row in prepared.reviewed_inputs
        if row.name == "reviewed/identity/test/quality/binding_witness.py"
    ]
    assert len(content_rows) == len(identity_rows) == 1
    assert content_rows[0].content == original.encode()
    scaffold_context = next(
        section
        for section in prepared.runtime_sections
        if section.startswith(
            "Resolved authoritative inputs for review-test-scaffolding:"
        )
    )
    assert "quality/binding_witness.py" in scaffold_context

    changed = original.replace("test_first", "test_first_removed")
    binding_path.write_text(changed, encoding="utf-8")
    repeated = entry.prepare_gate_run(
        context, gate_type=context.gate_type, invocation=invocation
    )
    assert repeated.review_basis_hash == prepared.review_basis_hash
    assert (
        next(
            row.content
            for row in repeated.reviewed_inputs
            if row.name == "reviewed/test/quality/binding_witness.py"
        )
        == original.encode()
    )

    fresh_basis_context = _scaffold_context(host)
    fresh_basis = entry.prepare_current_review_basis(
        fresh_basis_context,
        _invocation(fresh_basis_context),
    )
    assert fresh_basis.review_basis_hash != prepared.review_basis_hash
    fresh_context = _scaffold_context(host)
    with pytest.raises(KernelError) as fresh_error:
        entry.prepare_gate_run(
            fresh_context,
            gate_type=fresh_context.gate_type,
            invocation=_invocation(fresh_context),
        )
    assert "quality/binding_witness.py::test_first" in fresh_error.value.message


def test_binding_issues_do_not_turn_basis_observation_into_admission(
    tmp_path: Path, monkeypatch
) -> None:
    from heddle.gate import entry

    host, state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    (host / "quality/binding_directory").mkdir(parents=True)
    (host / "quality/binding_leaf.py").symlink_to("binding_target.py")
    _write_spec(
        host,
        (
            ("AC-1", "tests/not_created.py::test_missing"),
            ("AC-2", "../../outside.py::test_escape"),
            ("AC-3", "quality/binding_directory::test_directory"),
            ("AC-4", "quality/binding_leaf.py::test_symlink"),
            ("AC-5", "/tmp/outside.py::test_absolute"),
        ),
    )
    context = _scaffold_context(host)
    invocation = _invocation(context)

    basis = entry.prepare_current_review_basis(context, invocation)
    assert basis.ac_ids == ("AC-1", "AC-2", "AC-3", "AC-4", "AC-5")
    assert context.source_observations["tests/not_created.py"].kind == "missing"
    assert context.source_observations["quality/binding_directory"].kind == "directory"
    assert context.source_observations["quality/binding_leaf.py"].kind == "symlink"
    assert "../../outside.py" not in context.source_observations
    assert "/tmp/outside.py" not in context.source_observations
    with pytest.raises(KernelError) as raised:
        entry.prepare_gate_run(
            context, gate_type=context.gate_type, invocation=invocation
        )
    assert "AC-1: tests/not_created.py::test_missing" in raised.value.message
    assert "AC-2: ../../outside.py::test_escape" in raised.value.message
    assert "AC-3: quality/binding_directory::test_directory" in raised.value.message
    assert "AC-4: quality/binding_leaf.py::test_symlink" in raised.value.message
    assert "AC-5: /tmp/outside.py::test_absolute" in raised.value.message

    state = read(state_path)
    state["commands"] = {"acceptance_test": "python3 tests/mandatory_missing.py"}
    write(state_path, state)
    mandatory_context = _scaffold_context(host)
    with pytest.raises(KernelError) as mandatory:
        entry.prepare_current_review_basis(
            mandatory_context, _invocation(mandatory_context)
        )
    assert "required reviewed test artifact tests/mandatory_missing.py" in str(
        mandatory.value
    )


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"\xff", "source is not valid UTF-8"),
        (b"def broken(:\n", "source is not valid Python"),
    ],
)
def test_invalid_binding_source_is_captured_before_refusal(
    tmp_path: Path, monkeypatch, content: bytes, reason: str
) -> None:
    from heddle.gate import entry

    host, _state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    invalid = host / "tests/test_invalid_binding.py"
    invalid.write_bytes(content)
    _write_spec(
        host,
        (("AC-1", "tests/test_invalid_binding.py::test_behavior"),),
    )
    context = _scaffold_context(host)
    invocation = _invocation(context)

    entry.prepare_current_review_basis(context, invocation)
    observation = context.source_observations["tests/test_invalid_binding.py"]
    assert observation.content == content
    with pytest.raises(KernelError) as raised:
        entry.prepare_gate_run(
            context, gate_type=context.gate_type, invocation=invocation
        )
    assert reason in raised.value.message
