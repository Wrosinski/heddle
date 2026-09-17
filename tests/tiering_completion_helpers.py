"""Authored current terminal boundaries with real, narrow local verification."""

from tests.completion_helpers import CompletionHost
from tests.operational_model_helpers import FEATURE, SPEC, read, write
from tests.readiness_helpers import boundary_host, verify
from tests.tiering_helpers import ROLES, axes, entry, wire_policy


def final_host(
    tmp_path,
    monkeypatch,
    *,
    stage="complete",
    base="light",
    overlay=False,
    verify_now=True,
    overrides=None,
):
    root, path = boundary_host(tmp_path, stage=stage, tier=2)
    rows = {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }
    if overlay:
        rows["robustness-analysis"] = entry(
            "robustness-analysis",
            trigger={
                "gate": "robustness-analysis",
                "gap": "Failure between final proof and publication",
                "references": ["src/example.py"],
            },
        )
    rows.update(overrides or {})
    value = read(path)
    value.update(
        schema="heddle.state/v9",
        feature_policy=wire_policy(
            overrides=rows, assessment=axes("large" if base == "full" else "small")
        ),
    )
    value.pop("tier", None)
    value["review_assignments"] = {
        "schema": "heddle.review-assignments/v3",
        "assignments": [],
        "attempts": [],
        "dispositions": [],
        "acceptances": [],
    }
    value["authorizations"] = [
        {"through": "complete", "source": "user", "at": value["updated"]}
    ]
    for milestone in value["milestones"]:
        milestone.pop("estimated_hours")
    write(path, value)
    spec = root / SPEC
    spec.write_text(
        f"---\ntype: feature-spec\nfeature_name: {FEATURE}\n"
        "area: runtime\nlifecycle: active\n---\n" + spec.read_text()
    )
    (path.parent / "brief.md").write_text("# Brief\nDeliver the declared value.\n")
    (root / f"plans/{FEATURE}.decision-journal.md").write_text("# Decision Journal\n")
    (root / f"plans/{FEATURE}.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        + "\n\n".join(
            f"## {heading}\n\nNone observed."
            for heading in (
                "Tooling fights",
                "Prompt gaps",
                "Validator false-positives",
                "Escalations that should have been policy-resolvable",
            )
        )
        + "\n"
    )
    monkeypatch.chdir(root)
    if verify_now:
        verify(path, "m1", "m2", "acceptance", "smoke")
    return CompletionHost(
        root, path, path.parent / "verification" / "close-suite.calls"
    )
