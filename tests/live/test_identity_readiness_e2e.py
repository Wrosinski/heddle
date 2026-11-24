"""
W4 AC-4/5/6/9/14: two real ordinary calls, source drift and exact free replay.

Run only with exact live E2E authority and a new HEDDLE_W4_LIVE_ROOT. Authentication,
network, both named models and the standing live authorization are prerequisites.
Provider output is never replaced. Each public execution permits one attempt;
failed roots remain intact for diagnosis. This is integration, not quality proof.

The Antigravity reviewer lane adds a third lane (antigravity-reviewer-lane-v1
AC-4 and AC-25). Its case is a separate parameter that runs alone, so the
witness never re-spends Claude and Codex quota. It keeps every check the other
lanes make, adds that ``init.model`` equals the launched model, and replaces the
"no Critical or Important finding" assertion: it records every returned finding
verbatim, resolves each REPORT finding's decision and settles every finding and
the coverage row with lead rows, so closure turns on source identity alone. It
is admitted as a ``stage`` attempt through ``admit_live_attempt`` and writes to
``identity-antigravity/`` under the root; its disposable host lives outside the
root, and what the alignment check needs is copied in before the host goes.
The Claude and Codex case keeps today's behaviour, the root itself.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.content_identity_helpers import git, require_current_state
from tests.operational_model_helpers import (
    FEATURE,
    SPEC,
    application,
    document,
    fresh_host,
    read,
    write,
)
from tests.readiness_helpers import actions
from tests.structured_review_helpers import canonical_result, runs
from tests.tiering_helpers import ROLES, entry, wire_policy
from tests.tiering_review_helpers import disposition

pytestmark = [
    pytest.mark.live,
    pytest.mark.e2e,
]
ROOT = Path(__file__).resolve().parents[2]
CONFIG = {
    "claude": {"model": "claude-opus-5-5", "reasoning_effort": "xhigh"},
    "codex": {"model": "gpt-6-astra", "reasoning_effort": "high"},
    "antigravity": {"model": "gemini-4-neon", "reasoning_effort": "high"},
}
# Lane to the executable its runner launches.
BINARIES = {"claude": "claude", "codex": "codex", "antigravity": "agy"}
LANE_CASES = {
    "claude-codex": ("claude", "codex"),
    "antigravity": ("antigravity",),
}
LIVE_HELPERS = "tests.antigravity_live_helpers"
GATE = "milestone-review"
CHECKED = ["src/example.py", "tests/check.py"]


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")


def live_host(root, lane):
    """The disposable host under ``root`` and its prerequisite record."""
    host, path = fresh_host(root, name=f"ordinary-{lane}", seed=False)
    command = "PYTHONPATH=src python3 tests/check.py"
    state = document(
        schema="heddle.state/v9",
        stage="implement",
        flow="hitl",
        feature_policy=wire_policy(
            overrides={
                role: entry(role, primary={"cli": lane, **CONFIG[lane]})
                if role == GATE
                else entry(role, mode="off", limit=None, minimum_rounds=0)
                for role in ROLES
            }
        ),
        commands={
            "test_command": command,
            "smoke_test": command,
            "acceptance_test": command,
            "live_e2e_test": "",
        },
    )
    state.pop("tier", None)
    state["milestones"] = state["milestones"][:1]
    state["milestones"][0].pop("estimated_hours")
    state["milestones"][0].update(
        title="Expose the declared integer",
        status="current",
        tasks=[{"id": "m1-t1", "text": "Expose VALUE as integer 7", "status": "done"}],
        verification={"command": command, "expected": "imported VALUE equals 7"},
    )
    write(path, state)
    config = read(host / ".heddle.yaml")
    config["gates"] = {"enabled": [GATE]}
    config["autopilot"]["test_command"] = command
    if lane == "antigravity":
        config.setdefault("agents", {})["antigravity"] = True
    write(host / ".heddle.yaml", config)
    (host / SPEC).write_text(
        "---\ntype: feature-spec\narea: runtime\nfeature_name: operational-example\n"
        "lifecycle: active\n---\n\n# Declared integer\n\n## Purpose\n\n"
        "The consumer imports a stable integer constant from example.\n\n"
        "## Conceptual Design\n\nExpose VALUE as an integer with value 7. "
        "There is one module and one import consumer; no service or configuration.\n\n"
        "## Architecture\n\nsrc/example.py owns VALUE; tests/check.py imports and "
        "asserts it. Import has no filesystem or network effects.\n\n"
        "## Acceptance Criteria\n\n### AC-1: Import the declared value\n\n"
        "Priority: MUST\nPrecondition: The source directory is importable.\n"
        "Action: Import VALUE from example.\nExpected: VALUE is the integer 7.\n"
        "Verified-by: tests/check.py::test_declared_value\n\n"
        "## Decision Log\n\nUse a module constant for the single consumer.\n"
    )
    (path.parent / "plan.md").write_text(
        "# Declared integer plan\n\n## Environment\n\n"
        f"Verify with `{command}`. Python 3 is available.\n\n"
        "## Design Context\n\nThe Spec is authoritative; one module owns VALUE.\n\n"
        "### Milestone m1: Expose the declared integer\n\n"
        "Satisfies: AC-1\n\nImplement VALUE = 7 and verify a real import.\n\n"
        "<!-- heddle:begin plan-status -->\nNative initial status.\n"
        "<!-- heddle:end plan-status -->\n"
    )
    (host / "tests/check.py").write_text(
        "from example import VALUE\n\ndef test_declared_value():\n"
        "    assert type(VALUE) is int and VALUE == 7\n\n"
        "if __name__ == '__main__':\n    test_declared_value()\n"
    )
    shutil.copyfile(
        ROOT / "docs/workflow/engineering-principles.md",
        host / "docs/workflow/engineering-principles.md",
    )
    (host / "src/example.py").write_text("VALUE = 0\n")
    git(host, "add", ".")
    git(host, "commit", "-qm", "authored live identity fixture and state birth")
    birth = git(host, "log", "--diff-filter=A", "--format=%H", "--", str(path)).strip()
    assert birth == git(host, "rev-parse", "HEAD").strip()
    (host / "src/example.py").write_text("VALUE = 7\n")
    (host / ".git/heddle/active-feature").write_text(FEATURE + "\n")
    prerequisites = {
        "state_birth_commit": birth.decode(),
        "command": command,
        "execution": CONFIG[lane],
        "gate": GATE,
        "iteration": 1,
        "hard_timeout_seconds": 1200,
        "inactivity_timeout_seconds": 1200,
        "max_attempts": 1,
        "inputs": {
            str(p.relative_to(host)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                host / SPEC,
                path.parent / "plan.md",
                host / "src/example.py",
                host / "tests/check.py",
                host / "docs/workflow/engineering-principles.md",
            )
        },
    }
    return host, path, prerequisites


def lane_summary(path, lane):
    """The run's native gate summary for this lane."""
    found = []
    for candidate in sorted((path.parent / "reviews").glob("*.gate-summary.json")):
        if ".prev." in candidate.name or candidate.name.endswith(
            ".review.gate-summary.json"
        ):
            continue
        value = json.loads(candidate.read_text())
        if value.get("gate") == GATE and value.get("cli") == lane:
            found.append(value)
    assert len(found) == 1, f"FAIL AC-25: expected one {lane} {GATE} summary"
    return found[0]


def stream_events(summary):
    """Events of the kept (redacted) stream; it is read here, never copied."""
    raw = Path(summary["artifacts"]["raw_out"])
    return [
        json.loads(line)
        for line in raw.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("{")
    ]


def settle_every_finding(app, ops, path, event, findings, evidence):
    """The lead's rows for a live review: each REPORT finding's decision is
    resolved, recorded first when the runtime did not, and every finding and
    the coverage row are settled. The rows go into the evidence."""
    from heddle.contracts.decisions import DecisionInput

    decision_rows = []
    rows = []
    for finding in findings:
        if finding["classification"] != "report":
            rows.append(
                disposition(
                    event["run_id"],
                    finding["id"],
                    status="settled",
                    references=CHECKED,
                    reason=(
                        "Live identity witness: the lead inspected the cited export "
                        "and assertion; the finding is kept verbatim as evidence "
                        "and the disposable fixture is not changed"
                    ),
                )
            )
            continue
        owner = next(
            (
                row
                for row in read(path)["decisions"]
                if row.get("origin_finding_id") == finding["id"]
            ),
            None,
        )
        if owner is None:
            added = app.execute(
                ops.DecisionsAdd(
                    decisions=(
                        DecisionInput(
                            kind="finding",
                            escalation_class=4,
                            source=f"{GATE}#{finding['id']}",
                            title=finding["title"],
                            question=f"How does the lead dispose of {finding['id']}?",
                            options=("keep as evidence", "act on it"),
                            routes_to=(SPEC,),
                            recommendation="keep as evidence",
                        ),
                    ),
                    feature=FEATURE,
                    expect_revision=read(path)["revision"],
                )
            )
            decision_rows.append({"added": added.to_envelope()})
            assert added.ok, added.to_envelope()
            owner = read(path)["decisions"][-1]
        resolved = app.execute(
            ops.ResolveDecision(
                owner["id"],
                "disposition",
                "Live identity witness: the finding is kept verbatim as evidence; "
                "the disposable fixture is not changed.",
                (owner.get("routes_to") or [SPEC])[0],
                feature=FEATURE,
            )
        )
        decision_rows.append({"decision": owner, "resolved": resolved.to_envelope()})
        assert resolved.ok, resolved.to_envelope()
        rows.append(
            disposition(
                event["run_id"],
                finding["id"],
                status="settled",
                evidence_kind="decision",
                decision_id=owner["id"],
                references=CHECKED,
                reason="Settled through its resolved owner decision",
            )
        )
    rows.append(
        disposition(
            event["run_id"],
            "@coverage",
            status="settled",
            references=CHECKED,
            reason=(
                "Inspect the actual integer export and independently "
                "executed import assertion"
            ),
        )
    )
    save(evidence / "antigravity-decisions.json", decision_rows)
    save(evidence / "antigravity-dispositions.json", rows)
    return rows


@pytest.mark.parametrize("lanes", list(LANE_CASES.values()), ids=list(LANE_CASES))
def test_ac14_real_review_readiness_change_restore_and_exact_replay(
    monkeypatch, tmp_path, lanes
):
    from heddle.gate import cli, entry

    require_current_state()
    raw_root = os.environ.get("HEDDLE_W4_LIVE_ROOT")
    assert raw_root, (
        "FAIL live prerequisite: set HEDDLE_W4_LIVE_ROOT to a new durable path"
    )
    alone = lanes == ("antigravity",)
    if alone:
        assert importlib.util.find_spec(LIVE_HELPERS) is not None, (
            "FAIL AC-25: tests.antigravity_live_helpers (admit_live_attempt) is "
            "not implemented"
        )
        helpers = importlib.import_module(LIVE_HELPERS)
        hosts = tmp_path / "hosts"
        hosts.mkdir()
        where = {"evidence": None}
    else:
        root = Path(raw_root).resolve()
        root.mkdir(parents=True, exist_ok=False)
        print(f"Identity-readiness live evidence: {root}", flush=True)
        hosts = root
        where = {"evidence": root}
    for lane in lanes:
        assert shutil.which(BINARIES[lane]), (
            f"FAIL live prerequisite: missing {BINARIES[lane]} executable"
        )
    app, ops = application()
    original_args = cli.GateArgs
    original_run = entry.run_gate_for_runtime
    executed = []

    def bounded_args(**kwargs):
        args = original_args(**kwargs)
        args.max_attempts = 1
        args.timeout_seconds = args.inactivity_timeout_seconds = 1200
        args.preserve_success_artifacts = True
        return args

    def observed_run(gate, context, **kwargs):
        prepared = context.prepared_run
        lane = prepared.invocation.exec_config.cli
        save(
            where["evidence"] / f"{lane}-prepared.json",
            {
                "execution": asdict(prepared.invocation.exec_config),
                "transport": asdict(prepared.transport),
                "schema": prepared.output_contract.schema_json,
                "input_hash": prepared.input_hash,
                "review_basis_hash": prepared.review_basis_hash,
            },
        )
        executed.append(lane)
        return original_run(gate, context, **kwargs)

    monkeypatch.setattr(cli, "GateArgs", bounded_args)
    monkeypatch.setattr(entry, "run_gate_for_runtime", observed_run)
    completed = []
    for lane in lanes:
        host, path, prerequisites = live_host(hosts, lane)
        if not alone:
            save(where["evidence"] / f"{lane}-prerequisites.json", prerequisites)
        monkeypatch.chdir(host)
        verified = app.execute(ops.Verify(feature=FEATURE, scope="m1"))
        if alone:
            # Admission comes immediately before the first provider call; the
            # version check inside it may skip the case, claiming nothing.
            evidence = helpers.admit_live_attempt("stage", "identity-antigravity")
            where["evidence"] = evidence
            print(f"Identity-readiness live evidence: {evidence}", flush=True)
            save(evidence / f"{lane}-prerequisites.json", prerequisites)
        evidence = where["evidence"]
        save(evidence / f"{lane}-verification.json", verified.to_envelope())
        assert verified.ok
        fact = read(path)["verifications"][-1]
        assert fact["exit_code"] == 0
        assert (
            fact["evidence"]["before"]["source_sha256"]
            == fact["evidence"]["after"]["source_sha256"]
        )
        operation = ops.RunGate(GATE, feature=FEATURE, cli=lane, **CONFIG[lane])
        first = app.execute(operation)
        save(evidence / f"{lane}-first.json", first.to_envelope())
        assert first.ok and not first.data["cached"], first
        event = runs(path)[-1]
        canonical, value = canonical_result(path, event)
        assert value["schema"] == "heddle.review-result/v3"
        assert value["invocation"]["assignment_id"] == event["assignment_id"]
        assert value["invocation"]["execution"]["cli"] == lane
        findings = value["content"]["findings"]
        if alone:
            recorded = value["invocation"]["execution"]
            assert {key: recorded.get(key) for key in CONFIG[lane]} == CONFIG[lane], (
                f"FAIL AC-25: the review recorded {recorded}"
            )
            summary = lane_summary(path, lane)
            events = stream_events(summary)
            inits = [row["init"] for row in events if row.get("event") == "init"]
            assert inits and {row.get("model") for row in inits} == {
                CONFIG[lane]["model"]
            }, f"FAIL AC-25: init.model {inits} is not the launched model"
            save(evidence / f"{lane}-findings.json", findings)
            rows = settle_every_finding(app, ops, path, event, findings, evidence)
        else:
            # Current reviews need explicit lead coverage settlement. This fixture
            # does not manufacture resolutions for unexpected real model findings.
            assert not any(
                row["severity"] == "critical"
                or (
                    row["severity"] == "important" and row["classification"] != "ignore"
                )
                for row in findings
            ), findings
            rows = [
                disposition(
                    event["run_id"],
                    "@coverage",
                    status="settled",
                    references=CHECKED,
                    reason=(
                        "Inspect the actual integer export and independently "
                        "executed import assertion"
                    ),
                )
            ]
        settled = app.execute(
            ops.RecordReviewDisposition(
                feature=FEATURE,
                payload={
                    "schema": "heddle.review-disposition-input/v1",
                    "dispositions": rows,
                },
                expect_revision=read(path)["revision"],
            )
        )
        save(evidence / f"{lane}-coverage-disposition.json", settled.to_envelope())
        assert settled.ok and settled.data["closure"]["closed"], settled
        baseline = app.execute(ops.Status(feature=FEATURE))
        save(evidence / f"{lane}-baseline-status.json", baseline.to_envelope())
        assert (
            baseline.ok
            and baseline.data["review_status"][0]["applicability"] == "current"
        )
        source = host / "src/example.py"
        original = source.read_bytes()
        source.write_bytes(original + b"\n# changed declared working content\n")
        before = path.read_bytes()
        for name, read_operation in (
            ("status", ops.Status(feature=FEATURE)),
            ("orient", ops.Orient(feature=FEATURE)),
        ):
            stale = app.execute(read_operation)
            save(evidence / f"{lane}-stale-{name}.json", stale.to_envelope())
            assert (
                stale.ok
                and stale.data["review_status"][0]["applicability"] == "unavailable"
            )
            assert stale.data["review_closure"]["open_refs"]
            assert any(
                isinstance(action, (ops.Verify, ops.RunGate))
                for action in actions(stale)
            )
        refused = app.execute(ops.MilestoneAdvance(feature=FEATURE))
        save(evidence / f"{lane}-refused-advance.json", refused.to_envelope())
        assert not refused.ok and path.read_bytes() == before
        source.write_bytes(original)
        restored = app.execute(ops.Status(feature=FEATURE))
        save(evidence / f"{lane}-restored-status.json", restored.to_envelope())
        assert (
            restored.ok
            and restored.data["review_status"][0]["applicability"] == "current"
        )
        frozen = path.read_bytes(), canonical.read_bytes()
        replay = app.execute(operation)
        save(evidence / f"{lane}-replay.json", replay.to_envelope())
        assert replay.ok and replay.data["cached"]
        assert replay.data["run_id"] == first.data["run_id"]
        assert (path.read_bytes(), canonical.read_bytes()) == frozen
        assert len(runs(path)) == 1
        if alone:
            # AC-25 retention: what the alignment check needs, before the host
            # goes. The kept stream is already redacted; it is never copied.
            shutil.copyfile(summary["io"]["log"], evidence / f"{lane}-gate.log")
            save(evidence / f"{lane}-summary.json", summary)
            save(evidence / f"{lane}-ledger-rows.json", runs(path))
            shutil.copyfile(canonical, evidence / f"{lane}-review.json")
            usage = [
                row["result"].get("usage")
                for row in events
                if row.get("event") == "result"
            ]
            save(evidence / f"{lane}-usage.json", usage)
        completed.append(
            {
                "lane": lane,
                "run_id": first.data["run_id"],
                "artifact": str(canonical),
                "artifact_sha256": hashlib.sha256(frozen[1]).hexdigest(),
                "actual_findings": findings,
                "free_replay": True,
            }
        )
    assert executed == list(lanes)
    save(
        where["evidence"] / "completed-live.json",
        {
            "calls": completed,
            "public_executions": executed,
            "cost": "Use retained actual usage; USD may be unavailable.",
        },
    )
