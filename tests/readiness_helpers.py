"""Authored boundary hosts; every accepted review and verification is real."""

from __future__ import annotations

import importlib
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path

from tests.content_identity_helpers import git
from tests.operational_model_helpers import (
    FEATURE,
    SPEC,
    application,
    document,
    fresh_host,
    read,
    write,
)
from tests.structured_review_helpers import (
    complete_scripted_prompt,
    native_providers,
)

STAGE_REVIEWS = {
    ("spec-review", 3): (
        ("spec-review", "claude"),
        ("spec-review", "codex"),
        ("spec-review-synthesis", "claude"),
    ),
    ("plan-review", 3): (
        ("plan-review", "claude"),
        ("plan-review", "codex"),
        ("plan-review-synthesis", "claude"),
    ),
    ("scaffold", 3): (
        ("review-test-scaffolding", "claude"),
        ("review-test-scaffolding", "codex"),
        ("review-test-scaffolding-synthesis", "claude"),
    ),
    ("peer-review", 2): (("peer-review-sequential", "claude"),),
    ("peer-review", 3): (
        ("review-correctness", "claude"),
        ("review-architecture", "claude"),
        ("review-robustness", "claude"),
        ("review-tests", "claude"),
        ("review-synthesis", "claude"),
    ),
    ("robustness", 3): (("robustness-analysis", "claude"),),
}


def readiness_api(layer="runtime"):
    name = f"heddle.{layer}.readiness"
    assert importlib.util.find_spec(name) is not None, (
        f"FAIL W4 AC-6: the shared {layer} readiness assessment is missing"
    )
    return importlib.import_module(name)


def boundary_host(tmp_path, *, stage="implement", tier=2, enabled=None):
    state = document(stage=stage, tier=tier, flow="hitl")
    for index, milestone in enumerate(state["milestones"]):
        source = "src/example.py" if index == 0 else "src/other.py"
        symbol = "VALUE" if index == 0 else "OTHER"
        milestone["owns"] = [source]
        if index == 0:
            milestone["owns"].extend([SPEC, "tests/check.py"])
        milestone["verification"]["command"] = (
            "python3 -c 'import runpy; "
            f'assert runpy.run_path("{source}")["{symbol}"] == 7\''
        )
        milestone["tasks"] = [
            {
                "id": f"m{index + 1}-t1",
                "text": "Implement the declared value",
                "status": "done",
            }
        ]
        milestone["status"] = (
            "current"
            if stage == "implement" and index == 0
            else "todo"
            if stage in {"spec-review", "plan-review", "scaffold", "implement"}
            else "done"
        )
    state["commands"] = {
        "smoke_test": "python3 tests/check.py",
        "test_command": "python3 tests/check.py",
        "acceptance_test": "python3 tests/check.py",
        "live_e2e_test": "",
    }
    host, path = fresh_host(tmp_path, state=state)
    (host / "src/other.py").write_text("OTHER = 7\n")
    (host / "tests/check.py").write_text(
        "import runpy\n\ndef test_declared_value():\n"
        "    assert runpy.run_path('src/example.py')['VALUE'] == 7\n"
        "    assert runpy.run_path('src/other.py')['OTHER'] == 7\n\n"
        "if __name__ == '__main__':\n    test_declared_value()\n"
    )
    spec = host / SPEC
    spec.write_text(
        spec.read_text() + "Verified-by: tests/check.py::test_declared_value\n"
    )
    plan = path.parent / "plan.md"
    plan.write_text(
        plan.read_text()
        + (
            "\n## Verification Commands\n\npython3 tests/check.py\n"
            "\n<!-- heddle:begin plan-status -->\nNative initial status.\n"
            "<!-- heddle:end plan-status -->\n"
        )
    )
    config = read(host / ".heddle.yaml")
    if enabled is not None:
        config["gates"] = {"enabled": list(enabled)}
    write(host / ".heddle.yaml", config)
    # Freeze authored input changes, then retain a real owned implementation diff.
    (host / "src/example.py").write_text("VALUE = 0\n")
    (host / "src/other.py").write_text("OTHER = 0\n")
    git(host, "add", ".")
    git(host, "commit", "-qm", "authored readiness boundary")
    (host / "src/example.py").write_text("VALUE = 7\n")
    (host / "src/other.py").write_text("OTHER = 7\n")
    return host, path


def runtime_manifest(stdin):
    marker = "===== BEGIN GATE RUNTIME MANIFEST =====\n"
    tail = stdin.split(marker, 1)[1]
    return json.JSONDecoder().raw_decode(tail)[0]


def scripted_providers(monkeypatch, tmp_path, *, overrides=None, before_return=None):
    overrides = {} if overrides is None else overrides

    def payload(_lane, stdin, _argv):
        role = runtime_manifest(stdin)["gate"]
        from tests.tiering_review_helpers import review_content

        value = overrides.get(role, review_content(role))
        return complete_scripted_prompt(json.dumps(value), stdin).encode()

    return native_providers(monkeypatch, tmp_path, payload, before_return=before_return)


def review(gate="self-review", lane="claude"):
    app, ops = application()
    result = app.execute(
        ops.RunGate(
            gate,
            feature=FEATURE,
            cli=lane,
            model="fixture-model",
            reasoning_effort="high",
        )
    )
    assert result.ok, f"fixture review must be valid before its boundary test: {result}"
    return result


def verify(path, *scopes):
    app, ops = application()
    for scope in scopes:
        result = app.execute(ops.Verify(feature=FEATURE, scope=scope))
        assert result.ok, result
        fact = read(path)["verifications"][-1]
        assert fact["exit_code"] == 0
        assert (
            fact["evidence"]["before"]["source_sha256"]
            == (fact["evidence"]["after"]["source_sha256"])
        )


def complete_stage_reviews(path, stage, tier):
    for gate, lane in STAGE_REVIEWS[(stage, tier)]:
        review(gate, lane)
    if stage in {"peer-review", "robustness"}:
        verify(path, "m1", "m2", "acceptance", "smoke")


def actions(result):
    return tuple(
        action.action.operation
        for action in result.next_actions
        if hasattr(action.action, "operation")
    )


def current_readiness(host, *, boundary=None):
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    api = readiness_api()
    config = load_project_config(host)
    snapshot = resolve_snapshot(config, FEATURE)
    return api.assess_current_readiness(config, snapshot, boundary=boundary)


@contextmanager
def readiness_reads(monkeypatch, host):
    """Count actual source captures and canonical reads at public entrypoints."""
    from heddle.gate import context, entry, preparation

    observed, canonical_reads = [], []
    original = Path.read_bytes
    read_canonical = entry.read_contained_utf8_artifact
    selected = {
        host / "src/example.py",
        host / "tests/check.py",
        host / SPEC,
        host / "plans" / FEATURE / "plan.md",
    }

    def observe(source):
        if source in selected:
            observed.append(source)
        return original(source)

    def canonical(*args, **kwargs):
        result = read_canonical(*args, **kwargs)
        canonical_reads.append(result[0])
        return result

    def forbidden(*_args, **_kwargs):
        raise AssertionError("FAIL AC-10: readiness loaded full prior-review history")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", observe)
        patch.setattr(entry, "read_contained_utf8_artifact", canonical)
        patch.setattr(context, "_capture_prior_reviews", forbidden)
        patch.setattr(preparation, "build_rerun_ledger", forbidden)
        yield observed, canonical_reads, selected


def authored_current_event(
    state_path,
    gate_name,
    scope,
    *,
    cli="claude",
    payload=None,
    run_id=None,
    at="2026-09-06T10:00Z",
    artifact=None,
):
    """Author current canonical test evidence, without claiming a provider run.

    Transition fixtures supply their review content; the actual observer binds
    that content to their declared tree. This never operates on a live ledger.
    """
    from copy import deepcopy
    from dataclasses import asdict
    from hashlib import sha256

    from heddle.gate import entry
    from heddle.gate.registry import GATES
    from heddle.gate.results import (
        REVIEW_RESULT_VERSION,
        ReviewInvocation,
        ReviewResult,
        decode_review_content,
        serialize_review_result,
    )
    from heddle.gate.types import GateInvocationOverrides
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.runtime.review_assignments import resolve_invocation
    from tests.operational_model_helpers import new_run_id
    from tests.structured_review_helpers import complete_fixture_coverage

    state_path = state_path.resolve()
    host = state_path.parents[2]
    config = load_project_config(host)
    snapshot = resolve_snapshot(config, read(state_path)["feature"])
    gate = GATES.get(gate_name)
    assert gate is not None, f"fixture review role must be current: {gate_name}"
    invocation = resolve_invocation(
        snapshot,
        config,
        gate,
        GateInvocationOverrides(cli=cli),
    )
    context = entry.build_gate_context(
        snapshot,
        config,
        gate,
        cli=cli,
        basis_only=True,
        milestone_id=scope if gate.requires_milestone else None,
    )
    basis = entry.prepare_current_review_basis(context, invocation)
    from tests.tiering_review_helpers import review_content

    payload = deepcopy(review_content(gate_name) if payload is None else payload)
    complete_fixture_coverage(payload, basis.ac_ids, basis.active_rules)
    contract = gate.output_contract
    assert contract is not None
    result = ReviewResult(
        REVIEW_RESULT_VERSION,
        ReviewInvocation(
            snapshot.feature,
            gate_name,
            scope,
            invocation.exec_config,
            None,
            "authored-current-v1",
            "e" * 64,
            basis.review_basis_hash,
            sha256(("authored:" + basis.review_basis_hash).encode()).hexdigest(),
            contract.version,
            contract.schema_sha256,
            context.review_assignment.id if context.review_assignment else None,
            context.assignment_round.number if context.assignment_round else None,
            context.reviewer_slot,
        ),
        decode_review_content(json.dumps(payload).encode(), contract),
    )
    entry.validate_current_review_fields(result, basis)
    raw = serialize_review_result(result)
    digest = sha256(raw).hexdigest()
    artifact = artifact or f"reviews/{gate_name}-{scope}.{cli}.{digest}.review.json"
    target = state_path.parent / artifact
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    projection = entry.machine_projection_from_result(result)
    event = {
        "run_id": run_id or new_run_id(),
        "at": at,
        "cli": cli,
        "artifact": artifact,
        "artifact_sha256": digest,
        "input_hash": result.invocation.input_hash,
        "review_basis_hash": basis.review_basis_hash,
        "execution_config": asdict(invocation.exec_config),
        "overridden_axes": ["cli"],
        "prompt_version": result.invocation.prompt_version,
        "effective_prompt_sha256": result.invocation.effective_prompt_sha256,
        "assignment_id": result.invocation.assignment_id,
        "round_number": result.invocation.round_number,
        "reviewer_slot": result.invocation.reviewer_slot,
        "verdict": projection["verdict"],
        "findings": projection["findings"],
        "report_findings": [
            {"finding_id": f.id, "title": f.title, "severity": f.severity}
            for f in result.content.findings
            if f.classification == "report"
        ],
    }
    return event
