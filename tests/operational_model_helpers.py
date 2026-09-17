"""Fresh current-state fixtures; no operational ledger conversion."""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
from copy import deepcopy
from itertools import count
from pathlib import Path
from uuid import UUID

import yaml

from tests.runtime.adoption_helpers import adopt_fixture_host

FEATURE = "operational-example"
STAMP = "2026-09-06T10:00Z"
RUN_A = "00000000-0000-4000-8000-000000000001"
RUN_B = "00000000-0000-4000-8000-000000000002"
SPEC = f"docs/features/runtime/{FEATURE}.md"

_RUN_IDS = count(1_000_000)


def new_run_id():
    """Allocate distinct opaque identities when an authored fixture adds events."""
    return str(UUID(int=next(_RUN_IDS)))


def authored_evidence(
    host, paths, *, kind="milestone-owns", workspace: Path | None = None
):
    """Publish authored evidence for a fixture fact; never convert a ledger."""
    from dataclasses import asdict

    from heddle.kernel.verification import SourceDeclaration
    from heddle.runtime.verification import (
        observe_current_source,
        publish_source_evidence,
    )

    _definition, evidence, _git = observe_current_source(
        host, SourceDeclaration(kind, tuple(paths)), with_diagnostics=False
    )
    if workspace is None:
        workspace = host / "plans" / FEATURE
    reference, _reused = publish_source_evidence(workspace, evidence)
    return {
        "schema": "heddle.verification-evidence/v1",
        "before": asdict(reference),
        "after": asdict(reference),
    }


def required_module(name):
    try:
        spec = importlib.util.find_spec(name)
    except ModuleNotFoundError as error:
        if error.name is None or not name.startswith(error.name + "."):
            raise
        spec = None
    assert spec is not None, f"W2 contract missing: {name}"
    return importlib.import_module(name)


def application():
    return required_module("heddle.runtime.application"), required_module(
        "heddle.contracts.operations"
    )


def report(finding_id="R-1", severity="important"):
    return {"finding_id": finding_id, "title": "Choose the owner", "severity": severity}


def native_run_fields(cli="claude"):
    """Literal completed-event binding fields for state-only fixture tests."""
    return {
        "review_basis_hash": "c" * 64,
        "execution_config": {
            "cli": cli,
            "model": "fixture-review-model",
            "reasoning_effort": "high",
            "sandbox": "read-only",
        },
        "overridden_axes": [],
        "artifact_sha256": "b" * 64,
        "prompt_version": "authored-fixture-v1",
        "effective_prompt_sha256": "e" * 64,
    }


def run(run_id=RUN_A, *, reports=(), status="pass", rerun=False, cli="claude", **kw):
    reports = deepcopy(list(reports))
    severity = {
        key: sum(r["severity"] == key for r in reports)
        for key in ("critical", "important", "minor")
    }
    native = native_run_fields(cli) if status != "error" else {}
    return {
        "run_id": run_id,
        "at": STAMP,
        "cli": cli,
        "artifact": f"reviews/{run_id}.review.json",
        **native,
        "input_hash": "a" * 64,
        "verdict": {"status": status, "rerun_recommended": rerun},
        "findings": {
            "by_severity": severity,
            "by_classification": {
                "implement": 0,
                "report": len(reports),
                "ignore": 0,
                "unknown": 0,
            },
            "total": len(reports),
            "contradictions": 0,
        },
        "report_findings": reports,
        **kw,
    }


def fact(*runs, gate="review-synthesis", scope="feature"):
    return {"gate": gate, "scope": scope, "runs": deepcopy(list(runs))}


def decision(id="decision-1", *, kind="finding", **kw):
    result = {
        "id": id,
        "kind": kind,
        "source": "gate" if kind == "finding" else "user",
        "title": "Choose the owner",
        "status": "pending",
        "routes_to": [],
        "created_at": STAMP,
        "resolved_at": None,
        "resolution": None,
        "rationale": None,
        "resolution_source": None,
    }
    if kind == "finding":
        result.update(origin_run_id=RUN_A, origin_finding_id="R-1")
    elif kind == "question":
        result["class"] = 1
    result.update(kw)
    return result


def resolved(value, kind="disposition", **payload):
    return {
        **deepcopy(value),
        "status": "resolved",
        "resolved_at": STAMP,
        "resolution_source": "user",
        "rationale": "Keep one declared owner",
        "routes_to": [SPEC],
        "resolution": {"kind": kind, **payload},
    }


def session(*, stage="implement", advanced=False):
    return {
        "started_at": STAMP,
        "ended_at": STAMP,
        "completed": [],
        "started": [],
        "key_context": "fixture",
        "next_steps": "continue",
        "blockers": [],
        "stage": stage,
        "revision_before": 0,
        "revision_after": 0,
        "advanced": advanced,
    }


def document(**kw):
    from dataclasses import asdict

    from tests.tiering_helpers import ROLES, entry, policy

    kw.pop("tier", None)
    current_policy = policy(
        overrides={
            role: entry(role, mode="off", limit=None, minimum_rounds=0)
            for role in ROLES
        }
    )
    result = {
        "schema": "heddle.state/v9",
        "completion": None,
        "feature": FEATURE,
        "spec": SPEC,
        "revision": 0,
        "created": STAMP,
        "updated": STAMP,
        "stage": "spec-review",
        "authorized_through": "complete",
        "flow": "auto",
        "commands": {},
        "feature_inputs": ["src/example.py"],
        "gates": [],
        "decisions": [],
        "verifications": [],
        "sessions": [],
        "milestones": [],
        "feature_policy": {
            "schema": "heddle.feature-policy/v1",
            **asdict(current_policy),
        },
        "review_assignments": {
            "schema": "heddle.review-assignments/v3",
            "assignments": [],
            "attempts": [],
            "dispositions": [],
            "acceptances": [],
        },
    }
    for number in (1, 2):
        result["milestones"].append(
            {
                "id": f"m{number}",
                "title": f"Part {number}",
                "satisfies": ["AC-1"],
                "depends_on": [] if number == 1 else ["m1"],
                "owns": [SPEC, "src/example.py", "tests/check.py"],
                "complexity": "low",
                "estimated_hours": [1, 2],
                "verification": {
                    "command": "python3 tests/check.py",
                    "expected": "pass",
                },
                "status": "todo",
                "tasks": [],
            }
        )
    result.update(deepcopy(kw))
    return result


def read(path):
    return yaml.safe_load(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def fresh_host(tmp_path, *, state=None, name="host", seed=True):
    """Real Git/adoption, authored inputs, and optional literal current ledger."""
    host = tmp_path / name
    host.mkdir()
    write(
        host / ".heddle.yaml",
        {
            "layout": {
                "specs": "docs/features",
                "plans": "plans",
                "source": "src",
                "tests": "tests",
                "prompts": ".heddle/prompts",
                "schemas": ".heddle/schemas",
            },
            "agents": {"claude": True, "codex": True},
            "autopilot": {"test_command": "python3 tests/check.py"},
        },
    )
    for relative, text in {
        SPEC: (
            "# Operational example\n\n## Acceptance Criteria\n\n"
            "### AC-1: Declared value\n\nPriority: MUST\n"
            "Action: Read VALUE.\nExpected: The value is 7.\n"
        ),
        f"plans/{FEATURE}/plan.md": (
            "# Plan\n\n### Milestone m1: Declared value\n\n"
            "Satisfies: AC-1\n\nImplement the declared value.\n\n"
            "### Milestone m2: Verify the value\n\nSatisfies: AC-1\n\n"
            "Verify the declared value.\n"
        ),
        "src/example.py": "VALUE = 7\n",
        "tests/check.py": (
            "from pathlib import Path\n"
            "assert 'VALUE = 7' in Path('src/example.py').read_text()\n"
        ),
    }.items():
        target = host / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    adopt_fixture_host(host)
    state_path = host / "plans" / FEATURE / "state.yaml"
    if seed:
        write(state_path, state if state is not None else document())
    # A real tracked implementation diff satisfies milestone gate preflight.
    (host / "src/example.py").write_text("VALUE = 0\n")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "fixture@example.invalid"),
        ("config", "user.name", "Fixture"),
        ("add", "."),
        ("commit", "-qm", "fixture inputs"),
    ):
        subprocess.run(["git", *args], cwd=host, check=True, capture_output=True)
    (host / "src/example.py").write_text("VALUE = 7\n")
    pointer = host / ".git/heddle/active-feature"
    pointer.parent.mkdir(parents=True)
    if seed:
        pointer.write_text(FEATURE + "\n")
    return host, state_path


def cli(run_cli, *args):
    code, out, err = run_cli([*args, "--json"])
    value = json.loads(out)
    assert isinstance(value, dict), (code, out, err)
    return code, value


def review(*, gate="spec-review", report_id=None, status="PASS", rerun="No"):
    """Literal gate-specific artifacts, independent of the production renderer."""
    from tests.structured_review_helpers import content, finding

    items = [finding(report_id)] if report_id is not None else []
    if not items and status != "PASS":
        items = [
            finding(
                "SP-C1" if status == "FAIL" else "SP-I1",
                severity="critical" if status == "FAIL" else "important",
                classification="implement",
            )
        ]
    effective = (
        "fail"
        if any(f["severity"] == "critical" for f in items)
        else "pass_with_conditions"
        if items
        else "pass"
    )
    return json.dumps(
        content(gate, findings=items, status=effective, rerun=rerun == "Yes")
    )


def providers(
    monkeypatch, artifact, *, before_return=None, exit_code=0, termination=None
):
    """Only external transport is replaced; real preparation/validation/recording."""
    from heddle.gate.runners import claude, codex
    from heddle.io.process import MonitorResult, Termination

    calls = []

    def invoke(lane, command, raw_out, *, stdin_source):
        prompt = stdin_source.read_text()
        calls.append((lane, prompt, tuple(command)))
        text = artifact(prompt) if callable(artifact) else artifact
        # Scripted event fixtures account for the exact inputs sent to the
        # provider. Malformed-output tests retain their malformed bytes.
        from tests.structured_review_helpers import complete_scripted_prompt

        text = complete_scripted_prompt(text, prompt)
        if before_return:
            before_return(lane, prompt)
        if lane == "claude":
            raw_out.write_text(
                '{"type":"result","subtype":"success","is_error":false,'
                '"result":"fixture transport","structured_output":' + text + "}\n"
            )
        else:
            Path(command[command.index("-o") + 1]).write_text(text)
            events = [
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": text},
                },
                {"type": "turn.completed", "usage": {}},
            ]
            raw_out.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        return MonitorResult(
            exit_code=exit_code,
            termination=termination or Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )

    def claude_call(command, raw_out, _config, *, stdin_source=None, **_kw):
        return invoke("claude", command, raw_out, stdin_source=stdin_source)

    def codex_call(command, raw_out, _config, *, stdin_source=None, **_kw):
        return invoke("codex", command, raw_out, stdin_source=stdin_source)

    monkeypatch.setattr(claude, "run_monitored", claude_call)
    monkeypatch.setattr(codex, "run_monitored", codex_call)
    monkeypatch.setattr("heddle.gate.runner._binary_available", lambda _binary: True)
    return calls
