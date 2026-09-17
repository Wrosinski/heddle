"""Kernel contracts for deferred automatic-resolution evidence."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from heddle.io.source import observe_source
from heddle.kernel.model import (
    first_actionable_blocker,
    resolve_snapshot,
)
from heddle.kernel.project_config import KernelError, load_project_config
from heddle.kernel.source_manifest import (
    SourceDefinition,
    normalize_source_paths,
)
from tests.operational_model_helpers import native_run_fields

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"


def _run_fact(*, run_id="00000000-0000-4000-8000-000000000001", reports=1) -> dict:
    return {
        **native_run_fields("claude"),
        "run_id": run_id,
        "report_findings": [
            {
                "finding_id": f"R-{i + 1}",
                "title": "Choose policy",
                "severity": "important",
            }
            for i in range(reports)
        ],
        "at": "2026-07-10T00:00Z",
        "cli": "claude",
        "artifact": "reviews/code-quality.review.json",
        "input_hash": "deferral-fixture",
        "verdict": {},
        "findings": {
            "by_severity": {"critical": 0, "important": reports, "minor": 0},
            "by_classification": {
                "implement": 0,
                "report": reports,
                "ignore": 0,
                "unknown": 0,
            },
            "total": reports,
            "contradictions": 0,
        },
    }


def _host(tmp_path: Path, *, flow: str = "auto") -> tuple[Path, Path, dict]:
    host = tmp_path / f"host-{flow}"
    shutil.copytree(TINY, host)
    principles = host / "docs" / "workflow" / "engineering-principles.md"
    principles.parent.mkdir(parents=True, exist_ok=True)
    principles.write_text(
        "---\nstatus: ratified\n---\n\n# Engineering Principles\n",
        encoding="utf-8",
    )
    state_path = host / "plans" / "sample-feature" / "state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["flow"] = flow
    if flow == "hitl":
        # The positive deferral controls exercise implement-stage behavior,
        # not the pre-existing HITL boundary. A user grant past implement
        # isolates the decision deferral predicate while retaining a real HITL snapshot.
        state["authorized_through"] = "peer-review"
        state["authorizations"] = [
            {
                "through": "peer-review",
                "source": "user",
                "at": "2026-07-10T00:00Z",
            }
        ]
    return host, state_path, state


def _write(state_path: Path, state: dict) -> None:
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")


def _bound_provenance(host, paths):
    manifest = observe_source(
        host,
        SourceDefinition(
            "milestone-owns",
            normalize_source_paths(paths),
            normalize_source_paths(paths),
        ),
    )
    return {
        "schema": manifest.schema,
        "source_set_kind": "milestone-owns",
        "declaration_paths": list(manifest.definition.declaration_paths),
        "paths": list(manifest.definition.paths),
        "before_sha256": manifest.sha256,
        "after_sha256": manifest.sha256,
        "git_head": None,
        "differs_from_head": None,
    }


def _gate_decision(
    source: str = "code-quality#1", *, kind="finding", **overrides
) -> dict:
    decision = {
        "id": source,
        "kind": kind,
        "rationale": None,
        "resolution": None,
        "resolution_source": None,
        "resolved_at": None,
        "source": source,
        "title": "Choose the public cache policy",
        "status": "pending",
        "routes_to": [],
        "created_at": "2026-07-10T00:00Z",
    }
    if kind == "finding":
        decision.update(
            origin_run_id="00000000-0000-4000-8000-000000000001",
            origin_finding_id="R-1",
        )
    else:
        decision["class"] = 1
    decision.update(overrides)
    return decision


@pytest.mark.parametrize(
    "decision,gate_facts",
    [
        pytest.param(_gate_decision(), False, id="decision4-False"),
    ],
)
def test_ac8_explicit_origins_validate_without_source_or_id_inference(
    tmp_path: Path, decision: dict, gate_facts: bool
) -> None:
    """Survivor pin: broadening deferral must never swallow unsupported facts."""
    host, state_path, state = _host(tmp_path, flow="hitl")
    state["decisions"] = [decision]
    state["gates"] = (
        [{"gate": "code-quality", "scope": "m1", "runs": [_run_fact()]}]
        if gate_facts
        else []
    )
    _write(state_path, state)

    if not gate_facts or decision.get("class") == 1:
        with pytest.raises(KernelError, match="origin_run_id|class"):
            resolve_snapshot(load_project_config(host), "sample-feature")
    else:
        # Historical source labels and display IDs cannot override explicit origins.
        snapshot = resolve_snapshot(load_project_config(host), "sample-feature")
        assert first_actionable_blocker(snapshot) is None
