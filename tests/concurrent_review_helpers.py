"""Synthetic hosts and a slot-keyed engine double for concurrent-reviews-v1.

Shared by the launch-set (readiness) and batch acceptance modules. Hosts are
built from literal fixture policies; provider work is replaced only at the
existing gate-engine seam (or the provider transport for standalone setup
calls), so preparation, admission, recording, CAS and readiness stay real.
No local run evidence is used.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

import yaml

from tests.tiering_helpers import ASTRA, FABLE, OPUS, SOL, entry
from tests.tiering_review_helpers import V7_FEATURE, current_host

PRIMARY = "primary"
SECONDARY = "secondary"
# Literal slot reviewers; a secondary always uses the other CLI (feature policy).
SLOT_REVIEWERS = {
    "spec-review": {PRIMARY: ASTRA, SECONDARY: FABLE},
    "plan-review": {PRIMARY: SOL, SECONDARY: FABLE},
    "review-test-scaffolding": {PRIMARY: SOL, SECONDARY: OPUS},
    "milestone-review": {PRIMARY: SOL, SECONDARY: OPUS},
    "peer-review-sequential": {PRIMARY: OPUS, SECONDARY: SOL},
    "behavior-review": {PRIMARY: OPUS, SECONDARY: SOL},
    "complexity-review": {PRIMARY: SOL, SECONDARY: OPUS},
}
ROLE_STAGE = {
    "spec-review": "spec-review",
    "plan-review": "plan-review",
    "review-test-scaffolding": "scaffold",
    "milestone-review": "implement",
    "peer-review-sequential": "peer-review",
    "behavior-review": "peer-review",
    "complexity-review": "peer-review",
}


def dual(role: str, *, window: int | str = 1):
    """A confirmed dual entry for one role with the given secondary window."""
    from tests.secondary_rounds_helpers import windowed

    row = entry(
        role,
        primary=SLOT_REVIEWERS[role][PRIMARY],
        secondary=SLOT_REVIEWERS[role][SECONDARY],
    )
    return row if window == 1 else windowed(row, window)


def single(role: str):
    return entry(role, primary=SLOT_REVIEWERS[role][PRIMARY])


def off(role: str):
    return entry(
        role,
        mode="off",
        limit=None,
        minimum_rounds=0,
        primary=SLOT_REVIEWERS[role][PRIMARY],
    )


def set_launch(host: Path, mode: str) -> None:
    """Write the host launch setting; config is read on every command."""
    path = host / ".heddle.yaml"
    value = yaml.safe_load(path.read_text())
    value["reviews"] = {"launch": mode}
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def set_gates_enabled(host: Path, gates: list[str]) -> None:
    path = host / ".heddle.yaml"
    value = yaml.safe_load(path.read_text())
    value["gates"] = {"enabled": list(gates)}
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def edit_state(path: Path, change) -> None:
    """Author a fixture state before any command has written it."""
    value = yaml.safe_load(path.read_text())
    change(value)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def same_gate_host(
    tmp_path: Path,
    monkeypatch,
    *,
    role: str = "spec-review",
    window: int | str = 1,
    launch: str | None = None,
):
    """A host at the role's stage whose only active review is one dual role."""
    stage = ROLE_STAGE[role]
    overrides = {role: dual(role, window=window)}
    if stage == "peer-review" and role != "peer-review-sequential":
        overrides["peer-review-sequential"] = off("peer-review-sequential")
    host, state_path = current_host(
        tmp_path, monkeypatch, stage=stage, overrides=overrides
    )
    if stage == "implement":
        # m1 implementation is finished, so its review is exit evidence.
        edit_state(
            state_path,
            lambda value: value["milestones"][0].__setitem__(
                "tasks", [{"id": "t1", "text": "Declared value", "status": "done"}]
            ),
        )
    if launch is not None:
        set_launch(host, launch)
    return host, state_path


def full_peer_host(
    tmp_path: Path,
    monkeypatch,
    *,
    behavior_secondary: bool = True,
    complexity: str = "single",
    launch: str | None = None,
):
    """Full peer review: behavior (optionally dual) and complexity, no sequential."""
    overrides = {
        "peer-review-sequential": off("peer-review-sequential"),
        "behavior-review": dual("behavior-review")
        if behavior_secondary
        else single("behavior-review"),
        "complexity-review": {
            "single": single,
            "dual": dual,
            "off": off,
        }[complexity]("complexity-review"),
    }
    host, state_path = current_host(
        tmp_path, monkeypatch, stage="peer-review", overrides=overrides
    )
    if launch is not None:
        set_launch(host, launch)
    return host, state_path


def status_actions(feature: str = V7_FEATURE):
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    status = execute(ops.Status(feature=feature))
    assert status.ok, status.to_envelope()
    return list(status.next_actions)


def commands(actions) -> list[str]:
    return [action.command for action in actions]


def batch_command(feature: str = V7_FEATURE) -> str:
    return f"heddle run-gates --feature {feature}"


def slot_command(role: str, slot: str, feature: str = V7_FEATURE) -> str:
    reviewer = SLOT_REVIEWERS[role][slot]
    return (
        f"heddle run-gate {role} --cli {reviewer['cli']} --model {reviewer['model']} "
        f"--reasoning-effort {reviewer['reasoning_effort']} --feature {feature}"
    )


def run_gate_commands(actions, role: str | None = None) -> list[str]:
    prefix = "heddle run-gate " + (f"{role} " if role else "")
    return [command for command in commands(actions) if command.startswith(prefix)]


def execute_batch(feature: str = V7_FEATURE):
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    return execute(ops.RunGates(feature=feature))


def run_slot(role: str, slot: str, feature: str = V7_FEATURE):
    """One standalone run-gate for exactly this slot's reviewer."""
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    reviewer = SLOT_REVIEWERS[role][slot]
    return execute(ops.RunGate(role, feature=feature, **reviewer))


def show_slot(role: str, slot: str, feature: str = V7_FEATURE) -> dict:
    """The standalone preparation of one slot, without a call or a write."""
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    reviewer = SLOT_REVIEWERS[role][slot]
    shown = execute(ops.ShowPrompt(role, feature=feature, **reviewer))
    assert shown.ok, shown.to_envelope()
    identity = shown.data["prompt_identity"]
    return {
        "input_hash": identity["input_hash"],
        "review_basis_hash": identity["review_basis_hash"],
        "prompt_version": identity["prompt_version"],
        "effective_prompt_sha256": identity["effective_prompt_sha256"],
        "prompt": shown.data["prompt"],
        "execution": shown.data["execution"],
    }


def member_key(member: dict) -> tuple[str, str]:
    return (member["gate"], member["reviewer_slot"])


def gate_runs(state_path: Path) -> list[dict]:
    value = yaml.safe_load(state_path.read_text())
    return [
        {"gate": gate["gate"], **run} for gate in value["gates"] for run in gate["runs"]
    ]


def revision(state_path: Path) -> int:
    return yaml.safe_load(state_path.read_text())["revision"]


def canonical_outcome(gate_type, context, feature: str, *, findings=()):
    """Bind a valid canonical review for the prepared run and store it."""
    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.gate.cli import GateArgs
    from heddle.gate.entry import machine_projection_from_result
    from heddle.gate.io import gate_artifact_location
    from heddle.gate.results import (
        bind_review_result,
        decode_review_content,
        serialize_review_result,
    )
    from heddle.gate.types import GateCanonicalReview
    from tests.structured_review_helpers import (
        complete_fixture_coverage,
        complete_fixture_rerun,
        complete_fixture_verdict,
    )
    from tests.tiering_review_helpers import review_content

    prepared = context.prepared_run
    assert prepared is not None
    payload = review_content(gate_type.name, findings=findings)
    complete_fixture_coverage(payload, prepared.ac_ids, prepared.active_rules)
    complete_fixture_rerun(payload, prepared.prior_reviews, prepared.review_decisions)
    _cover_verification_targets(payload, prepared)
    complete_fixture_verdict(payload, bool(prepared.prior_reviews))
    result = bind_review_result(
        decode_review_content(json.dumps(payload).encode(), prepared.output_contract),
        prepared,
        feature,
    )
    raw = serialize_review_result(result)
    digest = hashlib.sha256(raw).hexdigest()
    workspace = context.workspace_dir or context.plan_path.parent
    args = GateArgs(
        gate_name=gate_type.name,
        cli=prepared.invocation.exec_config.cli,
        feature=feature,
    )
    directory, name = gate_artifact_location(context, args)
    directory.mkdir(parents=True, exist_ok=True)
    artifact = directory / f"{name}.{digest}.review.json"
    artifact.write_bytes(raw)
    projection = machine_projection_from_result(result)
    return GateCanonicalReview(
        result=result,
        artifact=ArtifactRef(
            artifact.relative_to(workspace).as_posix(), digest, "canonical"
        ),
        structure_warnings=tuple(projection["structure_warnings"]),
    )


def _cover_verification_targets(payload, prepared) -> None:
    """Account for every target a verification round opened with.

    The real engine validates this before it writes a canonical review, and
    recovery replays that validation, so the double must satisfy it too.
    """
    from tests.structured_review_helpers import evidence

    covered = {
        (row["source"]["run_id"], row["source"]["finding_id"])
        for row in payload["prior_dispositions"]
    }
    classes = {
        (prior.run_id, item.id): item.classification
        for prior in prepared.prior_reviews
        if prior.result is not None
        for item in prior.result.content.findings
    }
    for run_id, finding_id in prepared.required_prior_references or ():
        if (run_id, finding_id) in covered:
            continue
        classification = classes.get((run_id, finding_id), "implement")
        payload["prior_dispositions"].append(
            {
                "source": {"run_id": run_id, "finding_id": finding_id},
                "output_finding_id": None,
                "decision_id": None,
                "decision_origin": None,
                "evidence": evidence(),
                "reason": "Scripted verification of the round's target.",
                "disposition": (
                    "addressed" if classification == "implement" else "settled"
                ),
            }
        )


def typed_failure(context, key: tuple[str, str]):
    """A typed provider failure with its diagnostic artifact."""
    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.gate.types import GateEngineFailure

    workspace = context.workspace_dir or context.plan_path.parent
    diagnostic = workspace / "reviews" / f"{key[0]}.{key[1]}.controlled-failure.json"
    diagnostic.parent.mkdir(parents=True, exist_ok=True)
    diagnostic.write_text(json.dumps({"gate": key[0], "slot": key[1]}) + "\n")
    raw = diagnostic.read_bytes()
    return GateEngineFailure(
        gate_exit=1,
        reason="execution-failure",
        rerun_recommended=None,
        structure_warnings=("controlled provider failure",),
        artifacts=(
            ArtifactRef(
                diagnostic.relative_to(workspace).as_posix(),
                hashlib.sha256(raw).hexdigest(),
                "temporary",
            ),
        ),
    )


def install_slot_engine(
    monkeypatch,
    manager,
    *,
    parties: int = 2,
    first: tuple[str, str] | None = None,
    failures: tuple[tuple[str, str], ...] = (),
    crashes: tuple[tuple[str, str], ...] = (),
    findings: dict[tuple[str, str], tuple] | None = None,
    hold_until_recorded: dict[tuple[str, str], tuple[str, str]] | None = None,
    recorded=None,
):
    """A fork-safe engine double keyed by (gate, reviewer slot).

    Every started call waits at a ``parties``-wide barrier, so no call can
    finish before all have started. ``first`` finishes before every other call.
    ``failures`` return a typed provider failure; ``crashes`` exit the worker
    without a result. ``hold_until_recorded`` maps a held key to the key whose
    state record it waits for (``recorded`` is the shared event map that the
    parent's recording wrapper sets).
    """
    starts = manager.list()
    finishes = manager.list()
    barrier = manager.Barrier(parties) if parties > 1 else None
    first_finished = manager.Event()
    observations = manager.dict()

    def run(gate_type, context, *, feature, **_kwargs):
        prepared = context.prepared_run
        assert prepared is not None
        key = (gate_type.name, prepared.reviewer_slot)
        starts.append(
            {
                "gate": key[0],
                "reviewer_slot": key[1],
                "scope": prepared.scope,
                "input_hash": prepared.input_hash,
                "review_basis_hash": prepared.review_basis_hash,
                "prompt_version": prepared.prompt_version,
                "effective_prompt_sha256": prepared.effective_prompt_sha256,
                "assignment_id": prepared.assignment_id,
                "round_number": prepared.round_number,
                "cli": prepared.invocation.exec_config.cli,
                "model": prepared.invocation.exec_config.model,
                "reasoning_effort": prepared.invocation.exec_config.reasoning_effort,
                "pid": os.getpid(),
            }
        )
        if barrier is not None:
            barrier.wait(timeout=10)
        if first is not None and key != first:
            assert first_finished.wait(timeout=10)
        if hold_until_recorded and key in hold_until_recorded:
            awaited = hold_until_recorded[key]
            observations[key] = bool(recorded[awaited].wait(timeout=10))
        try:
            if key in crashes:
                os._exit(17)
            if key in failures:
                return typed_failure(context, key)
            return canonical_outcome(
                gate_type, context, feature, findings=(findings or {}).get(key, ())
            )
        finally:
            finishes.append(key)
            if key == first:
                first_finished.set()

    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
    return starts, finishes, observations


def install_counting_engine(monkeypatch, manager):
    """A barrier-free engine double that counts calls across processes."""
    calls = manager.list()

    def run(gate_type, context, *, feature, **_kwargs):
        prepared = context.prepared_run
        assert prepared is not None
        calls.append((gate_type.name, prepared.reviewer_slot))
        return canonical_outcome(gate_type, context, feature)

    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
    return calls


def record_events(monkeypatch, manager, keys):
    """Set one shared event per (gate, slot) once the parent has recorded it."""
    from heddle.runtime import gate_run

    events = {key: manager.Event() for key in keys}
    original = gate_run._record

    def recording(*args, **kwargs):
        accepted = original(*args, **kwargs)
        key = (args[1], kwargs.get("reviewer_slot"))
        if key in events:
            events[key].set()
        return accepted

    monkeypatch.setattr(gate_run, "_record", recording)
    return events


def lock_is_held(path: Path) -> bool:
    """True when another open file description holds the gate lock."""
    with path.open("a+") as probe:
        try:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        return False


def trace_gate_locks(monkeypatch):
    """Record each gate lock the runtime acquires, in acquisition order."""
    from heddle.gate import entry

    original = entry.gate_lock_for_runtime
    acquired: list[tuple[str, Path]] = []

    @contextmanager
    def traced(context, invocation):
        with original(context, invocation) as path:
            acquired.append((context.gate_type.name, path))
            yield path

    monkeypatch.setattr(entry, "gate_lock_for_runtime", traced)
    return acquired


def reach_round_two(state_path: Path, monkeypatch, *, role: str = "spec-review"):
    """Record round 1 with standalone runs, settle it, and open round 2."""
    from tests.tiering_review_helpers import (
        dispose,
        disposition,
        open_round,
        provider_transport,
        review_content,
    )

    provider_transport(monkeypatch, lambda _cli, _prompt: review_content(role))
    origins = []
    for slot in (PRIMARY, SECONDARY):
        result = run_slot(role, slot)
        assert result.ok, result.to_envelope()
        origins.append(gate_runs(state_path)[-1]["run_id"])
    settled = dispose(
        state_path,
        [disposition(run_id, "@coverage", status="settled") for run_id in origins],
    )
    assert settled.ok, settled.to_envelope()
    opened = open_round(state_path, role=role)
    assert opened.ok, opened.to_envelope()
    return origins
