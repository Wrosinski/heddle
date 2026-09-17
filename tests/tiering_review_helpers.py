"""
Versioned authored review fixtures and the actual provider-transport double.

No application, gate engine, canonical decoder, recording or policy is mocked.
"""

import json
from pathlib import Path

import yaml

from tests.operational_model_helpers import document, fresh_host
from tests.structured_review_helpers import content, evidence
from tests.tiering_helpers import invoke, wire_policy

V7_FEATURE = "operational-example"
ROLE_DIMENSIONS = {
    "milestone-review": ("correctness", "code-quality", "material-test-changes"),
    "behavior-review": ("correctness", "failure-behavior", "final-proof-adequacy"),
    "complexity-review": (
        "ownership",
        "interfaces",
        "dependency-direction",
        "necessity",
        "simplification",
    ),
}


def runs(path):
    return [
        run
        for gate in yaml.safe_load(path.read_text())["gates"]
        for run in gate["runs"]
    ]


def dispose(path, rows, *, expect_revision=None, dry_run=False):
    revision = yaml.safe_load(path.read_text())["revision"]
    return invoke(
        "RecordReviewDisposition",
        feature=V7_FEATURE,
        payload={"schema": "heddle.review-disposition-input/v1", "dispositions": rows},
        expect_revision=revision if expect_revision is None else expect_revision,
        dry_run=dry_run,
    )


def disposition(run_id, finding_id="SP-I1", **kw):
    row = {
        "run_id": run_id,
        "finding_id": finding_id,
        "status": "addressed",
        "evidence_kind": "inspection",
        "references": ["src/example.py"],
        "reason": "Inspected the exact VALUE counterexample against its owner",
    }
    row.update(kw)
    return row


def open_round(
    path,
    *,
    role="spec-review",
    purpose="verification",
    reason="Inspect the remaining original concern",
):
    return invoke(
        "ReviewRoundOpen",
        feature=V7_FEATURE,
        payload={
            "schema": "heddle.review-round-input/v1",
            "role": role,
            "scope": "feature",
            "purpose": purpose,
            "reason": reason,
        },
        expect_revision=yaml.safe_load(path.read_text())["revision"],
    )


def review_status(path, *, role="spec-review"):
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    result = execute(ops.Status(feature=V7_FEATURE))
    assert result.ok, result.to_envelope()
    return next(
        row
        for row in result.data["review_closure"]["assignments"]
        if row["role"] == role and row["scope"] == "feature"
    )


def amend(path, selection, *, revision):
    return invoke(
        "FeaturePolicy",
        slug=V7_FEATURE,
        payload=wire_policy(revision=revision, overrides={"spec-review": selection}),
        expect_revision=yaml.safe_load(path.read_text())["revision"],
    )


ALTITUDE_DIMENSIONS = {
    "spec-review": (
        "purpose-and-scope",
        "conceptual-coherence",
        "acceptance-criteria-quality",
        "assumptions-and-failure-classes",
        "design-commitments",
        "related-context-and-reusability",
        "approach-necessity",
    ),
    "plan-review": (
        "approach-soundness",
        "architecture-fit",
        "acceptance-criteria-coverage",
        "dependency-ordering",
        "independent-verifiability",
        "verification-feasibility",
        "related-context-and-reusability",
        "approach-necessity",
    ),
}


def current_host(tmp_path, monkeypatch, *, stage="spec-review", **policy_kwargs):
    from heddle.contracts.review_assignments import ROLE_STAGES
    from heddle.contracts.schemas import STAGES
    from tests.tiering_helpers import ROLES, entry

    overrides = dict(policy_kwargs.get("overrides", {}))
    for role in ROLES:
        if STAGES.index(ROLE_STAGES[role]) < STAGES.index(stage):
            overrides.setdefault(
                role,
                entry(role, mode="off", limit=None, minimum_rounds=0),
            )
    policy_kwargs["overrides"] = overrides
    value = document(
        schema="heddle.state/v9",
        stage=stage,
        feature_policy=wire_policy(**policy_kwargs),
    )
    value["review_assignments"] = {
        "schema": "heddle.review-assignments/v3",
        "assignments": [],
        "attempts": [],
        "dispositions": [],
        "acceptances": [],
    }
    value.pop("tier", None)
    value["authorizations"] = [
        {"through": "complete", "source": "user", "at": value["updated"]}
    ]
    for milestone in value["milestones"]:
        milestone.pop("estimated_hours")
        if stage == "implement" and milestone["id"] == "m1":
            milestone["status"] = "current"
        elif stage in {"peer-review", "robustness", "complete"}:
            milestone["status"] = "done"
    host, state_path = fresh_host(tmp_path, state=value)
    monkeypatch.chdir(host)
    return host, state_path


def review_content(role="spec-review", *, findings=(), altitude=True):
    findings = tuple(findings)
    # A document's displayed verdict describes reported severity. Native
    # assignment closure separately decides whether each finding is required.
    result = content(
        "peer-review-sequential" if role in ROLE_DIMENSIONS else role,
        findings=findings,
        status="fail"
        if any(row["severity"] == "critical" for row in findings)
        else "pass_with_conditions"
        if any(row["severity"] == "important" for row in findings)
        else "pass",
        rerun=any(
            row["severity"] == "critical"
            or (row["severity"] == "important" and row["classification"] == "implement")
            for row in findings
        ),
    )
    result["role"] = role
    dimensions = ROLE_DIMENSIONS.get(role)
    if altitude:
        dimensions = ALTITUDE_DIMENSIONS.get(role, dimensions)
    if dimensions is not None:
        result["details"]["dimensions"] = [
            {"id": name, "assessment": "adequate", "evidence": evidence()}
            for name in dimensions
        ]
    return result


def provider_transport(monkeypatch, artifact, *, error_attempts=0):
    from heddle.gate.runners import claude, codex
    from heddle.io.process import MonitorResult, Termination

    calls = []

    def transport(cli, command, raw_out, *, stdin_source):
        prompt = stdin_source.read_text()
        calls.append((cli, prompt, tuple(command)))
        if len(calls) <= error_attempts:
            raw_out.write_text("injected provider failure\n")
            return MonitorResult(
                exit_code=1,
                termination=Termination.COMPLETED,
                saw_stream_activity=True,
                inactivity_seconds=0,
                inactivity_marker="",
                checkpoints=[],
            )
        payload = artifact(cli, prompt) if callable(artifact) else artifact
        text = json.dumps(payload) if isinstance(payload, dict) else payload
        if cli == "claude":
            raw_out.write_text(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "structured_output": json.loads(text),
                    }
                )
                + "\n"
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
            raw_out.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        return MonitorResult(
            exit_code=0,
            termination=Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )

    def run_claude(command, raw_out, _config, *, stdin_source=None, **_kwargs):
        return transport("claude", command, raw_out, stdin_source=stdin_source)

    def run_codex(command, raw_out, _config, *, stdin_source=None, **_kwargs):
        return transport("codex", command, raw_out, stdin_source=stdin_source)

    monkeypatch.setattr(claude, "run_monitored", run_claude)
    monkeypatch.setattr(codex, "run_monitored", run_codex)
    monkeypatch.setattr("heddle.gate.runner._binary_available", lambda _name: True)
    return calls


def gate_command(run_cli, *args):
    code, out, err = run_cli([*args, "--feature", V7_FEATURE, "--json"])
    result = json.loads(out)
    assert isinstance(result, dict), (code, out, err)
    return code, result
