"""Synthetic component hosts and public commands for proof-continuity contracts.

No recorded project evidence, production mutation doubles, or provider launches.
Review cases use tiering_review_helpers' external-transport-only double.
"""

from __future__ import annotations

import json

from heddle.cli import main
from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.operational_model_helpers import (
    FEATURE,
    SPEC,
    document,
    fresh_host,
    read,
    write,
)
from tests.readiness_helpers import verify
from tests.tiering_review_helpers import dispose, disposition


def source_host(tmp_path, monkeypatch, *, stage="implement", owns=None):
    value = document(stage=stage)
    value["milestones"] = value["milestones"][:1]
    milestone = value["milestones"][0]
    milestone["status"] = "current" if stage == "implement" else "todo"
    milestone["owns"] = ["src/example.py", "tests/check.py"] if owns is None else owns
    milestone["tasks"] = [{"id": "m1-t1", "text": "Declare VALUE", "status": "done"}]
    value["commands"] = {
        "test_command": "python3 tests/check.py",
        "acceptance_test": "python3 tests/check.py",
        "smoke_test": "python3 tests/check.py",
        "live_e2e_test": "",
    }
    root, path = fresh_host(tmp_path, state=value)
    monkeypatch.chdir(root)
    return root, path


def command(capsys, *args):
    code = main([*args, "--feature", FEATURE, "--json"])
    captured = capsys.readouterr()
    assert captured.out.strip(), (code, captured.err)
    return code, json.loads(captured.out)


def edit(capsys, tmp_path, patch, *, milestone="m1", flags=()):
    payload = tmp_path / "milestone-patch.yaml"
    write(payload, patch)
    return command(
        capsys, "milestone", "edit", milestone, "--from-file", str(payload), *flags
    )


def affirm_all(path, origins, *, references=("tests/check.py",)):
    rows = [
        disposition(origin, finding, references=list(references))
        for origin, finding in origins
    ]
    result = dispose(path, rows)
    assert result.ok, result.to_envelope()


def launch(capsys, *, role="spec-review", cli="codex", model="gpt-6-astra"):
    return command(
        capsys,
        "run-gate",
        role,
        "--cli",
        cli,
        "--model",
        model,
        "--reasoning-effort",
        "high",
    )


def diagnostics(result):
    envelope = result if isinstance(result, dict) else result.to_envelope()
    return json.dumps(envelope["diagnostics"], sort_keys=True)


__all__ = [
    "FEATURE",
    "SPEC",
    "affirm_all",
    "command",
    "diagnostics",
    "edit",
    "execute",
    "launch",
    "ops",
    "read",
    "source_host",
    "verify",
    "write",
]
