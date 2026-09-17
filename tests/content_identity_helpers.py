"""
identity readiness authored observations and real preparation seams; no identity oracle.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
from dataclasses import fields

from tests.operational_model_helpers import FEATURE


def source_api():
    name = "heddle.io.source"
    assert importlib.util.find_spec(name) is not None, (
        "FAIL W4 AC-1/AC-4: the direct source observer is not implemented"
    )
    module = importlib.import_module(name)
    assert callable(getattr(module, "capture_path", None))
    assert callable(getattr(module, "observe_source", None))
    return module


def definition(declarations, paths=None, *, kind="milestone-owns"):
    from heddle.kernel.source_manifest import SourceDefinition

    assert "declaration_paths" in {field.name for field in fields(SourceDefinition)}, (
        "FAIL W4 AC-2: source identity does not bind its raw declarations"
    )
    return SourceDefinition(
        kind=kind,
        declaration_paths=tuple(declarations),
        paths=tuple(declarations if paths is None else paths),
    )


def require_current_state():
    from heddle.contracts.schemas import CURRENT_STATE_SCHEMA, STATE_SCHEMA_VERSIONS

    assert STATE_SCHEMA_VERSIONS == ("heddle.state/v9", "heddle.state/v10")
    assert CURRENT_STATE_SCHEMA == "heddle.state/v10"


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True
    ).stdout


def git_host(root):
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Content Test")
    git(root, "config", "user.email", "content@example.invalid")
    git(root, "commit", "--allow-empty", "-qm", "baseline")
    return root


def current_source(root, paths, *, kind="milestone-owns"):
    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime.verification import observe_current_source

    return observe_current_source(
        root,
        SourceDeclaration(kind, tuple(paths)),
        with_diagnostics=False,
        scope="m1",
    )[:2]


def capture_context(host, *, role="self-review", lane="claude"):
    from heddle.gate import entry
    from heddle.gate.registry import GATES
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    config = load_project_config(host)
    snapshot = resolve_snapshot(config, FEATURE)
    gate = GATES.get(role)
    return entry.build_gate_context(snapshot, config, gate, cli=lane)


def prepare_context(context, *, lane="claude"):
    from heddle.gate import entry
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.types import GateInvocationOverrides

    invocation = resolve_gate_execution(
        context.gate_type,
        GateInvocationOverrides(
            cli=lane, model="fixture-model", reasoning_effort="high"
        ),
    )
    return entry.prepare_gate_run(
        context, gate_type=context.gate_type, invocation=invocation
    )
