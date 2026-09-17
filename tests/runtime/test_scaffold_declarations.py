"""Narrow gate interfaces and explicit scaffold declarations."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from heddle.gate.types import GateContext


def test_public_contract_smoke() -> None:
    """Survivor: one manifest still discovers the typed allowance command."""
    from heddle.runtime.contracts import build_manifest

    commands = {row["name"]: row for row in build_manifest()["commands"]}
    allowance = commands["review allowance"]
    assert allowance["mutating"] is True
    assert {flag["name"] for flag in allowance["flags"]} >= {
        "--role",
        "--limit",
        "--approval",
        "--expect-revision",
    }


def test_ac1_ac6_scaffold_paths_ignore_incidental_prose_and_bind_explicit_code(
    tmp_path: Path, monkeypatch
) -> None:
    """Red: one extractor call serves the real scaffold-path consumer."""
    extraction = importlib.import_module("heddle.gate.extraction")
    resolver = getattr(extraction, "extract_explicit_test_paths", None)
    assert callable(resolver), (
        "FAIL AC-1/AC-6: no explicit Markdown test-path extraction boundary"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "real_test.py").write_text("def test_real(): assert True\n")
    text = (
        "Rename tests/markers only when useful.\n\n"
        "Use `tests/real_test.py::test_real` as the exact witness, and run "
        "`python -m pytest tests/real_test.py::test_real` when verifying it.\n"
    )
    assert resolver(text, tests_root="tests") == ("tests/real_test.py",)

    from heddle.gate import preparation
    from heddle.gate import prompt as gate_prompt
    from heddle.gate.registry import GATES

    calls = []

    def traced(content: str, *, tests_root: str):
        calls.append((content, tests_root))
        return resolver(content, tests_root=tests_root)

    monkeypatch.setattr(preparation, "extract_explicit_test_paths", traced)

    context = GateContext(
        gate_type=GATES["review-test-scaffolding"],
        feature="identity-readiness",
        repo_root=tmp_path,
        plan_path=tmp_path / "plan.md",
        spec_path=tmp_path / "spec.md",
        milestone=None,
        plan_content=text,
        verification_commands={
            "acceptance_test": "python -m pytest tests/real_test.py::test_real"
        },
    )
    assert preparation.declared_scaffold_paths(context) == ("tests/real_test.py",)
    assert calls == [(text, "tests")]
    assert "re.finditer" not in inspect.getsource(preparation.declared_scaffold_paths)
    assert "def _extract_test_scaffolding_paths" not in inspect.getsource(gate_prompt)


def test_ac1_gate_entry_retains_orchestration_and_evidence_operations() -> None:
    """Survivor: the narrow facade still owns cross-module gate operations."""
    from heddle.gate import entry

    retained = {
        "build_gate_context",
        "prepare_gate_run",
        "run_gate_for_runtime",
        "read_contained_utf8_artifact",
        "machine_projection_from_result",
        "validate_review_replay",
    }
    assert all(callable(getattr(entry, name, None)) for name in retained)


def test_ac1_gate_entry_exposes_operations_without_catalog_prompt_forwarders() -> None:
    """Red: callers use the modules that own catalog, prompt, and preparation."""
    from heddle.gate import entry

    forwarded = {
        "declared_test_scaffolding_paths",
        "exec_config_for",
        "expand_prompt_partials",
        "gate_invocation_overrides",
        "gate_prompt_templates",
        "gate_scope",
        "gate_type_for",
        "input_contract_for",
        "is_verdict_gate",
        "prepared_validation_error",
        "resolve_gate_execution",
        "resolve_gate_prompt",
    }
    remaining = sorted(name for name in forwarded if hasattr(entry, name))
    assert remaining == [], f"FAIL AC-1: forwarding gate surface remains: {remaining}"


def test_ac2_runtime_consumers_use_contract_owners_without_entry_translation() -> None:
    """Red: command consumers import registry, prompt and preparation owners."""
    from heddle.runtime import (
        doctor,
        gate_run,
        guardrails,
        review_assignments,
        show_prompt,
        trajectory,
    )

    forbidden = {
        gate_run: (
            "entry.gate_type_for",
            "entry.gate_invocation_overrides",
            "entry.gate_scope",
            "entry.prepared_validation_error",
            "entry.is_verdict_gate",
        ),
        review_assignments: (
            "entry.gate_type_for",
            "entry.input_contract_for",
            "entry.declared_test_scaffolding_paths",
            "entry.resolve_gate_execution",
            "entry.gate_invocation_overrides",
        ),
        show_prompt: (
            "entry.gate_type_for",
            "entry.input_contract_for",
            "entry.resolve_gate_execution",
            "entry.gate_invocation_overrides",
            "entry.resolve_gate_prompt",
        ),
        doctor: (
            "gate_entry.gate_type_for",
            "gate_entry.gate_prompt_templates",
            "gate_entry.resolve_gate_prompt",
        ),
        guardrails: ("from heddle.gate.entry import expand_prompt_partials",),
        trajectory: ("entry.is_verdict_gate",),
    }
    leftovers = {
        module.__name__: [
            token for token in tokens if token in inspect.getsource(module)
        ]
        for module, tokens in forbidden.items()
    }
    leftovers = {name: tokens for name, tokens in leftovers.items() if tokens}
    assert not leftovers, f"FAIL AC-2: runtime translation remains: {leftovers}"
