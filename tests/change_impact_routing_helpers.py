"""Synthetic hosts for change-impact-rebinding-v1 reaffirm and driver routing.

Hosts come from the shared fixture builders: real Git, adoption, application,
gate engine, recording, closure and readiness. Only the provider transport is
doubled. No local run evidence, plan records or archives are read.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from heddle.contracts import operations as ops
from tests.concurrent_review_helpers import (
    PRIMARY,
    SECONDARY,
    dual,
    full_peer_host,
    gate_runs,
    off,
    run_slot,
    set_launch,
    single,
)
from tests.operational_model_helpers import SPEC, read, session, write
from tests.structured_review_helpers import finding
from tests.tiering_helpers import ASTRA
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    open_round,
    provider_transport,
    review_content,
    review_status,
)

# Each spec-review row cites one stable file of its own.
SPEC_CITATIONS = {
    "PC-I1": "docs/first.md",
    "PC-I2": "docs/second.md",
    "@coverage": "tests/check.py",
}
# Two unsealed peer-review assignments at one boundary; both bases include the
# milestones' owned spec, so one contract change stales every row.
PEER_CITATIONS = {
    "behavior-review": {"BR-I1": "docs/first.md", "@coverage": "tests/check.py"},
    "complexity-review": {
        "CR-I1": "docs/first.md",
        "CR-I2": "docs/second.md",
        "@coverage": "tests/check.py",
    },
}
PEER_CONTENT_CLI = {"behavior-review": "claude", "complexity-review": "codex"}
MISSING_BASELINE_OID = "deadbeef" * 5


def _rows(origin: str, citations: dict[str, str]) -> list[dict[str, Any]]:
    return [
        disposition(
            origin,
            finding_id,
            status="settled" if finding_id == "@coverage" else "addressed",
            references=[reference],
        )
        for finding_id, reference in citations.items()
    ]


def _cited_files(root: Path) -> None:
    for name in ("first", "second"):
        (root / f"docs/{name}.md").write_text(f"Stable {name} proof.\n")


def run_review(role: str, reviewer: dict[str, str]) -> str:
    """One standalone review call through the public application boundary."""
    from heddle.runtime.application import execute

    result = execute(ops.RunGate(role, feature=V7_FEATURE, **reviewer))
    assert result.ok, result.to_envelope()
    return str(result.data["run_id"])


def disposed_spec_review(tmp_path, monkeypatch, *, limit=None):
    """A closed spec review: two findings and coverage, one citation each.

    With ``limit`` the review's round limit is that many rounds.
    """
    from tests.tiering_helpers import entry

    overrides = (
        {} if limit is None else {"spec-review": entry("spec-review", limit=limit)}
    )
    root, path = current_host(tmp_path, monkeypatch, overrides=overrides)
    _cited_files(root)
    calls = provider_transport(
        monkeypatch,
        review_content(
            findings=[
                finding(finding_id, classification="implement")
                for finding_id in SPEC_CITATIONS
                if finding_id != "@coverage"
            ]
        ),
    )
    origin = run_review("spec-review", ASTRA)
    recorded = dispose(path, _rows(origin, SPEC_CITATIONS))
    assert recorded.ok, recorded.to_envelope()
    assert review_status(path)["closed"]
    return root, path, origin, calls


def disposed_peer_reviews(tmp_path, monkeypatch, *, behavior_secondary=False):
    """Two unsealed peer-review assignments with closed rows at peer-review.

    With ``behavior_secondary`` the behavior review is dual and only its
    primary slot runs, so that assignment is missing a slot and holds no rows.
    """
    root, path = full_peer_host(
        tmp_path,
        monkeypatch,
        behavior_secondary=behavior_secondary,
        complexity="single",
        launch="sequential",
    )
    _cited_files(root)
    contents = {
        cli: review_content(
            role,
            findings=[
                finding(finding_id, classification="implement")
                for finding_id in PEER_CITATIONS[role]
                if finding_id != "@coverage"
            ],
        )
        for role, cli in PEER_CONTENT_CLI.items()
    }
    calls = provider_transport(monkeypatch, lambda cli, _prompt: contents[cli])
    origins = {}
    for role in PEER_CITATIONS:
        result = run_slot(role, PRIMARY)
        assert result.ok, result.to_envelope()
        origins[role] = gate_runs(path)[-1]["run_id"]
    disposed = [
        role
        for role in PEER_CITATIONS
        if not (behavior_secondary and role == "behavior-review")
    ]
    recorded = dispose(
        path,
        [
            row
            for role in disposed
            for row in _rows(origins[role], PEER_CITATIONS[role])
        ],
    )
    assert recorded.ok, recorded.to_envelope()
    for role in disposed:
        assert review_status(path, role=role)["closed"], role
    if behavior_secondary:
        waiting = review_status(path, role="behavior-review")
        assert waiting["missing_slots"] == [SECONDARY], waiting
    return root, path, origins, calls


def install_engine(monkeypatch) -> list[tuple[str, str]]:
    """An in-process double at the gate-engine seam.

    Each call binds a valid canonical review for its prepared run, accounting
    for a verification round's targets, exactly as the concurrent-review
    double does; preparation, recording and closure stay real.
    """
    from tests.concurrent_review_helpers import canonical_outcome

    calls: list[tuple[str, str]] = []

    def run(gate_type, context, *, feature, **_kwargs):
        calls.append((gate_type.name, context.prepared_run.reviewer_slot))
        return canonical_outcome(gate_type, context, feature)

    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
    return calls


def missing_slot_with_recorded_rows(tmp_path, monkeypatch):
    """A dual behavior review whose closed round 1 holds disposition rows and
    whose round 2 is missing its secondary, beside a closed complexity review.

    The secondary serves every round, so round 2 freezes both slots.
    """
    root, path = current_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={
            "peer-review-sequential": off("peer-review-sequential"),
            "behavior-review": dual("behavior-review", window="all"),
            "complexity-review": single("complexity-review"),
        },
    )
    set_launch(root, "sequential")
    calls = install_engine(monkeypatch)
    origins = {}
    for role, slot in (
        ("behavior-review", PRIMARY),
        ("behavior-review", SECONDARY),
        ("complexity-review", PRIMARY),
    ):
        result = run_slot(role, slot)
        assert result.ok, result.to_envelope()
        origins[(role, slot)] = gate_runs(path)[-1]["run_id"]
    recorded = dispose(
        path,
        [
            disposition(
                run_id, "@coverage", status="settled", references=["tests/check.py"]
            )
            for run_id in origins.values()
        ],
    )
    assert recorded.ok, recorded.to_envelope()
    for role in PEER_CITATIONS:
        assert review_status(path, role=role)["closed"], role
    opened = open_round(path, role="behavior-review")
    assert opened.ok, opened.to_envelope()
    later = run_slot("behavior-review", PRIMARY)
    assert later.ok, later.to_envelope()
    waiting = review_status(path, role="behavior-review")
    assert waiting["missing_slots"] == [SECONDARY], waiting
    return root, path, origins, calls


def stale_basis(root: Path) -> None:
    """An authored contract change: every row whose basis holds the spec stales."""
    spec = root / SPEC
    spec.write_text(spec.read_text() + "\nClarify the declared contract.\n")


def change_citation(root: Path, *references: str) -> None:
    for reference in references:
        (root / reference).write_text(f"Changed {reference} after the disposition.\n")


def appended_rows(before: dict, after: dict) -> list[dict[str, Any]]:
    prior = before["review_assignments"]["dispositions"]
    rows = after["review_assignments"]["dispositions"]
    assert rows[: len(prior)] == prior, "recorded rows must stay append-only"
    return rows[len(prior) :]


def mentions(value: Any, token: str) -> list[dict[str, Any]]:
    """Result entries that name ``token`` in one of their own string fields.

    Closure projections are excluded: they restate open references today, so
    they cannot witness what a reaffirm result itself lists.
    """
    found: list[dict[str, Any]] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if any(isinstance(v, str) and token in v for v in item.values()):
                found.append(item)
            for key, child in item.items():
                if key not in {"closure", "review_closure"}:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def refused_changes(
    value: Any, run_id: str, finding_id: str, *, ac: str = "AC-10"
) -> set[tuple[str, str]]:
    """The typed changes a reaffirm result lists for one refused row.

    The row is the entry naming ``run_id`` and ``finding_id`` (as fields or as
    a ``run#finding`` reference); its ``changes`` are read through the shared
    ``typed_changes``.
    """
    from tests.change_impact_explanation_helpers import typed_changes

    reference = f"{run_id}#{finding_id}"
    entries = [
        entry
        for entry in mentions(value, finding_id)
        if (entry.get("run_id"), entry.get("finding_id")) == (run_id, finding_id)
        or any(isinstance(v, str) and reference in v for v in entry.values())
    ]
    assert entries, f"FAIL {ac}: the result does not list refused row {reference}"
    typed = [entry for entry in entries if "changes" in entry] or entries[:1]
    return set().union(*(typed_changes(entry, ac=ac) for entry in typed))


def listed_with(value: Any, token: str, detail: str) -> bool:
    return any(detail in json.dumps(entry) for entry in mentions(value, token))


def baseline_unavailable_host(tmp_path, monkeypatch, reason: str):
    """A resumed scaffold stage whose recorded baseline is unreachable.

    ``missing`` names a well-formed commit absent from the object store;
    ``diverged`` names a real commit that is not an ancestor of ``HEAD``.
    """
    root, path = current_host(tmp_path, monkeypatch, stage="scaffold")
    if reason == "missing":
        oid = MISSING_BASELINE_OID
    else:
        oid = subprocess.run(
            ["git", "commit-tree", "HEAD^{tree}", "-m", "unrelated history"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    value = read(path)
    value["schema"] = "heddle.state/v10"
    value["source_baseline"] = {"kind": "git-commit", "oid": oid}
    # A recorded scaffold session makes the stage resume, so readiness would
    # otherwise route the scaffolding review rather than authoring work.
    value["sessions"] = [session(stage="scaffold")]
    write(path, value)
    return root, path, oid
