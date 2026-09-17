"""Shared native trajectory fixtures."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path


def native_trajectory_workspace(root: Path, feature: str, *, tier: str = "3") -> Path:
    from heddle.gate.registry import GATES
    from heddle.gate.types import GateContext, GateExecutionConfig, ProducerLane
    from tests.operational_model_helpers import document, write
    from tests.structured_review_helpers import authored_producer

    workspace = root / "plans" / feature
    workspace.mkdir(parents=True)
    context = GateContext(
        gate_type=GATES["spec-review"],
        feature=feature,
        repo_root=root,
        plan_path=workspace / "plan.md",
        workspace_dir=workspace,
        spec_path=None,
        milestone=None,
        spec_ac_ids=["AC-1"],
        tier=tier,
    )
    accepted = []
    for lane in ("claude", "codex"):
        candidate = authored_producer(
            context,
            ProducerLane(
                "spec-review",
                "feature",
                GateExecutionConfig(
                    lane, "trajectory-fixture-model", "high", "read-only"
                ),
            ),
            basis="b" * 64,
            historical=True,
        )
        accepted.append(
            {
                key: value
                for key, value in asdict(candidate.accepted_run).items()
                if value is not None
            }
        )
    state = document(
        feature=feature,
        spec="docs/features/workflow/sample.md",
        tier=int(tier),
        gates=[{"gate": "spec-review", "scope": "feature", "runs": accepted}],
    )
    write(workspace / "state.yaml", state)
    (workspace / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-07-22T10:00:00Z",
                "kind": "milestone-retry",
                "milestone": "m1",
                "note": f"retry under {root}/src",
            }
        )
        + "\nnot json\n",
        encoding="utf-8",
    )
    return workspace
