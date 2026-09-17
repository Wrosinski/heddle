"""The owner-approved review dependency rule, through native disposition reads."""

import subprocess

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.contracts.review_assignments import ROLE_STAGES
from heddle.runtime.application import execute
from tests.structured_review_helpers import finding
from tests.tiering_helpers import entry
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    runs,
)


def test_changed_citation_reopens_only_its_original_finding(
    tmp_path, monkeypatch, run_cli
):
    """AC-6 survivor: changed source reopens only the citing obligation."""
    host, path = current_host(tmp_path, monkeypatch)
    (host / "docs/first.md").write_text("First concern proof.\n")
    (host / "docs/second.md").write_text("Second concern proof.\n")
    calls = provider_transport(
        monkeypatch,
        review_content(
            findings=[
                finding("SP-I1", classification="implement"),
                finding("SP-I2", classification="implement"),
            ]
        ),
    )
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 4
    origin = runs(path)[0]["run_id"]
    assert dispose(
        path,
        [
            disposition(origin, "SP-I1", references=["docs/first.md"]),
            disposition(origin, "SP-I2", references=["docs/second.md"]),
            disposition(
                origin,
                "@coverage",
                status="settled",
                references=["docs/features/runtime/operational-example.md"],
            ),
        ],
    ).ok
    (host / "docs/first.md").write_text("Changed first concern proof.\n")
    result = execute(ops.Status(feature=V7_FEATURE))
    assert result.ok, result.to_envelope()
    row = next(
        r
        for r in result.data["review_closure"]["assignments"]
        if r["role"] == "spec-review"
    )
    assert row["open_refs"] == [[origin, "SP-I1"]]
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["spec-review", "plan-review", "milestone-review"])
@pytest.mark.parametrize(
    "edited", ["spec", "plan", "own", "other", "shared", "cited", "uncited"]
)
def test_only_assignment_dependencies_and_cited_evidence_reopen_dispositions(
    tmp_path, monkeypatch, run_cli, role, edited
):
    """AC-6 survivor: unrelated edits do not erase qualified dispositions."""
    host, path = current_host(
        tmp_path, monkeypatch, stage=ROLE_STAGES[role], overrides={role: entry(role)}
    )
    value = yaml.safe_load(path.read_text())
    value["milestones"][0]["owns"] = ["src/example.py", "tests/check.py"]
    value["milestones"][1]["owns"] = ["src/other.py"]
    value["feature_inputs"] = ["docs/shared.txt"]
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    subjects = {
        "spec": host / "docs/features/runtime/operational-example.md",
        "plan": path.parent / "plan.md",
        "own": host / "src/example.py",
        "other": host / "src/other.py",
        "shared": host / "docs/shared.txt",
        "cited": host / "docs/cited.md",
        "uncited": host / "docs/uncited.md",
    }
    for key in ("other", "shared", "cited", "uncited"):
        subjects[key].write_text("Original supporting material.\n")
    calls = provider_transport(monkeypatch, review_content(role))
    assert gate_command(run_cli, "run-gate", role)[0] == 0
    accepted = runs(path)
    row = disposition(
        accepted[-1]["run_id"],
        "@coverage",
        status="settled",
        references=["docs/cited.md"],
    )
    assert dispose(path, [row]).ok

    def status():
        result = execute(ops.Status(feature=V7_FEATURE))
        assert result.ok, result.to_envelope()
        return next(
            r
            for r in result.data["review_closure"]["assignments"]
            if r["role"] == role
            and r["scope"] == ("m1" if role == "milestone-review" else "feature")
        )

    assert status()["closed"]
    before = path.read_bytes()
    subject = subjects[edited]
    subject.write_text(subject.read_text() + "\nChanged supporting material.\n")
    relevant = {
        "spec-review": {"spec", "cited"},
        "plan-review": {"plan", "spec", "cited"},
        "milestone-review": {"own", "shared", "cited"},
    }
    assert status()["closed"] is (edited not in relevant[role])
    assert path.read_bytes() == before and runs(path) == accepted and len(calls) == 1
    if edited in relevant[role]:
        assert status()["open_refs"] == [[accepted[-1]["run_id"], "@coverage"]]
        assert dispose(path, [row]).ok
        assert status()["closed"] and len(calls) == 1


@pytest.mark.parametrize("edited", ["spec", "plan", "cited"])
def test_ignored_workflow_inputs_change_only_applicable_review_identity(
    tmp_path, monkeypatch, run_cli, edited
):
    """Survivor pin for AC-4/AC-7: exact ignored inputs stay content-bound."""
    role = "plan-review"
    host, path = current_host(
        tmp_path, monkeypatch, stage=ROLE_STAGES[role], overrides={role: entry(role)}
    )
    spec = host / "docs/features/runtime/operational-example.md"
    plan = path.parent / "plan.md"
    cited = host / "docs/cited-local.md"
    relative_paths = [
        spec.relative_to(host).as_posix(),
        plan.relative_to(host).as_posix(),
        cited.relative_to(host).as_posix(),
    ]
    (host / ".gitignore").write_text("\n".join(relative_paths) + "\n")
    subprocess.run(
        ["git", "rm", "--cached", relative_paths[0], relative_paths[1]],
        cwd=host,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=host, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "keep workflow inputs local"], cwd=host, check=True
    )
    cited.write_text("Ignored cited evidence.\n")
    for relative in relative_paths:
        assert (
            subprocess.run(
                ["git", "check-ignore", "-q", relative], cwd=host, check=False
            ).returncode
            == 0
        )

    calls = provider_transport(monkeypatch, review_content(role))
    assert gate_command(run_cli, "run-gate", role)[0] == 0
    accepted = runs(path)
    row = disposition(
        accepted[-1]["run_id"],
        "@coverage",
        status="settled",
        references=[relative_paths[2]],
    )
    assert dispose(path, [row]).ok

    def status():
        result = execute(ops.Status(feature=V7_FEATURE))
        assert result.ok, result.to_envelope()
        return next(
            item
            for item in result.data["review_closure"]["assignments"]
            if item["role"] == role
        )

    assert status()["closed"]
    subject = {"spec": spec, "plan": plan, "cited": cited}[edited]
    subject.write_text(subject.read_text() + "Changed ignored content.\n")
    assert not status()["closed"]
    assert status()["open_refs"] == [[accepted[-1]["run_id"], "@coverage"]]
    assert len(calls) == 1
