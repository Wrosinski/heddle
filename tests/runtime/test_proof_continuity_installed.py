"""AC-1..10 installed composition; execution needs its own exact grant.

Synthetic native birth/tasks/proof and actual installed CLI throughout. Only the
external reviewer executable is controlled. No provider credentials or network.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict

import pytest

from tests.content_identity_helpers import git
from tests.runtime.test_completion_feedback_installed import _publish_evidence
from tests.runtime.test_completion_installed import (
    FEATURE,
    SPEC,
    fixture_policy,
    start_host,
    write_declared_documents,
)
from tests.runtime.wheel_harness import (
    REPO_ROOT,
    build_installed_wheel,
    snapshot_tree,
    write_claude_shim,
)
from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import finding, finding_ref
from tests.tiering_helpers import FABLE, entry
from tests.tiering_review_helpers import review_content

pytestmark = [pytest.mark.acceptance, pytest.mark.e2e, pytest.mark.toolchain]


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("proof-continuity-wheel"))


def _closure(journey):
    return next(
        row
        for row in journey.run("status")["data"]["review_closure"]["assignments"]
        if row["role"] == "milestone-review" and row["scope"] == "m1"
    )


def _freshness(journey):
    return next(
        row
        for row in journey.run("status")["data"]["verification_status"]
        if row["scope"] == "m1"
    )["freshness"]


def _close_calls(journey):
    return (
        journey.calls.read_text().splitlines().count("close")
        if journey.calls.exists()
        else 0
    )


def _review(journey, tmp_path, content, *, expected):
    event = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": content,
            }
        )
        + "\n"
    )
    binary = write_claude_shim(tmp_path / "controlled-bin", event, journey.providers)
    journey.env["PATH"] = (
        str(binary.parent) + os.pathsep + journey.installed.env["PATH"]
    )
    before = journey.count(journey.providers)
    result = journey.run(
        "run-gate",
        "milestone-review",
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=expected,
    )
    assert result["data"]["accepted"]
    assert journey.count(journey.providers) == before + 1
    return result


def _dispose(journey, references):
    payload = journey.payload(
        "dispositions.json",
        {
            "schema": "heddle.review-disposition-input/v1",
            "dispositions": [
                {
                    "run_id": run_id,
                    "finding_id": finding_id,
                    "status": "addressed",
                    "evidence_kind": "verification",
                    "verification_scope": "m1",
                    "references": ["tests/check.py"],
                    "reason": (
                        "Current executable proof and lead inspection "
                        "resolve this original."
                    ),
                }
                for run_id, finding_id in references
            ],
        },
    )
    return journey.run("review", "disposition", "--input-json", payload)


def _append(journey, path):
    payload = journey.payload("append.json", {"owns_append": [path]})
    before = snapshot_tree(journey.root)
    journey.run("milestone", "edit", "m1", "--from-file", payload, "--dry-run")
    assert snapshot_tree(journey.root) == before
    journey.run("milestone", "edit", "m1", "--from-file", payload)
    before = snapshot_tree(journey.root)
    journey.run("milestone", "edit", "m1", "--from-file", payload)
    assert snapshot_tree(journey.root) == before


def test_installed_proof_continuity_journey(installed, tmp_path):
    journey = start_host(installed, tmp_path)
    root = journey.root
    policy = fixture_policy(reviewed=False)
    policy["revision"] = 2
    policy["entries"] = [
        asdict(entry("milestone-review", primary=FABLE, limit=2))
        if row["role"] == "milestone-review"
        else row
        for row in policy["entries"]
    ]
    journey.run(
        "feature",
        "policy",
        FEATURE,
        "--from-file",
        journey.payload("policy.json", policy),
        feature=False,
    )
    (root / "tests").mkdir(exist_ok=True)
    (root / "src/example.py").write_text("VALUE = 0\n")
    (root / "tests/check.py").write_text(
        "import runpy, sys\nfrom pathlib import Path\n"
        "def test_declared_value():\n"
        "    assert runpy.run_path('src/example.py')['VALUE'] == 7\n"
        "if __name__ == '__main__':\n"
        "    test_declared_value()\n"
        f"    with Path({str(journey.calls)!r}).open('a') as stream:\n"
        "        stream.write(sys.argv[1] + '\\n')\n"
    )
    write_declared_documents(journey)
    (root / f"journey-plans/{FEATURE}.decision-journal.md").write_text(
        "# Decision Journal\n\nNo unresolved choices.\n"
    )
    (root / f"journey-plans/{FEATURE}.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        + "\n\n".join(
            f"## {heading}\n\nNone observed in this synthetic host."
            for heading in (
                "Tooling fights",
                "Prompt gaps",
                "Validator false-positives",
                "Escalations that should have been policy-resolvable",
            )
        )
        + "\n"
    )
    for key, label in (
        ("test_command", "verify"),
        ("smoke_test", "smoke"),
        ("acceptance_test", "acceptance"),
    ):
        journey.run(
            "commands", "set", key, "--command", f"python3 tests/check.py {label}"
        )
    milestone = journey.payload(
        "milestone.json",
        {
            "title": "Implement the declared value",
            "complexity": "low",
            "satisfies": ["AC-1"],
            "depends_on": [],
            "owns": ["src/example.py", "tests/check.py"],
            "verification": {
                "command": "python3 tests/check.py milestone",
                "expected": "VALUE is 7",
            },
        },
    )
    journey.run("milestone", "add", "--from-file", milestone)
    git(root, "add", "src", "tests")
    git(root, "commit", "-qm", "synthetic source baseline")
    for stage in ("spec-review", "plan-review", "scaffold", "implement"):
        journey.run("phase-exit", "--through", "complete")
        assert journey.read()["stage"] == stage
    journey.run("milestone", "advance")
    journey.run("task", "add", "Implement and review VALUE = 7")
    task = journey.read()["milestones"][0]["tasks"][-1]["id"]
    (root / "src/example.py").write_text("VALUE = 7\n")
    journey.run("task", "done", task)
    journey.run("verify", "--scope", "m1")

    # AC-4: bookkeeping is accounted but not accidentally included in proof.
    (root / SPEC).write_text(
        (root / SPEC).read_text() + "\n## Outcomes\n\nSynthetic bookkeeping.\n"
    )
    assert _freshness(journey) == "fresh"
    (root / "src/example.py").write_text("VALUE = 7\n# Relevant source edit.\n")
    assert _freshness(journey) == "content-stale"

    # AC-5: exact controls and neighbors share the installed admission contract.
    # Track these synthetic files explicitly so ignore rules cannot make the
    # coverage assertion vacuous; no real feature records are committed.
    control = "journey-plans/friction-log.md"
    neighbor = "journey-plans/product-analysis.md"
    for relative in (control, neighbor):
        (root / relative).write_text("Initial synthetic record.\n")
    git(root, "add", "-f", "--", control, neighbor)
    git(root, "commit", "-qm", "synthetic control and neighboring product file")
    for relative in (control, neighbor):
        (root / relative).write_text("Changed synthetic record.\n")
    advice = json.dumps(journey.run("status")["diagnostics"])
    assert neighbor in advice and control not in advice
    for relative, expected in ((control, 3), (neighbor, 0)):
        attribution = journey.payload(
            "attribution.json",
            {
                "schema": "heddle.source-attribution-input/v1",
                "attributions": [
                    {
                        "paths": [relative],
                        "references": ["src/bootstrap.py"],
                        "reason": "Synthetic independent product analysis.",
                    }
                ],
            },
        )
        before = snapshot_tree(root)
        journey.run(
            "feature",
            "sources",
            "attribute",
            "--from-file",
            attribution,
            expected=expected,
        )
        if relative == control:
            assert snapshot_tree(root) == before
    advice = json.dumps(journey.run("status")["diagnostics"])
    assert neighbor not in advice and control not in advice

    # AC-7/8: the nondefault host observes and repairs actual product ownership.
    (root / "src/extra.py").write_text("EXTRA = 1\n")
    for args in (("validate",), ("status",), ("milestone", "advance", "--dry-run")):
        observation = journey.run(*args, expected=(0, 3, 4))
        advice = json.dumps(observation["diagnostics"]).lower()
        assert "src/extra.py" in advice and "own" in advice and "attribut" in advice
    _append(journey, "src/extra.py")
    journey.run("verify", "--scope", "m1")
    assert _freshness(journey) == "fresh"

    # AC-1/2/3: close originals, then spend one allowed verification round.
    first = _review(
        journey,
        tmp_path,
        review_content(
            "milestone-review",
            findings=[
                finding("PC-I1", classification="implement"),
                finding("PC-I2", classification="implement"),
            ],
        ),
        expected=4,
    )
    origin = first["data"]["run_id"]
    (root / "src/example.py").write_text("VALUE = 7\nOWNER = 'module'\n")
    (root / "src/extra.py").write_text("EXTRA = 2\n")
    test = root / "tests/check.py"
    test.write_text(
        test.read_text().replace(
            "def test_declared_value():\n",
            "def test_declared_value():\n"
            "    assert runpy.run_path('src/example.py')['OWNER'] == 'module'\n"
            "    assert runpy.run_path('src/extra.py')['EXTRA'] == 2\n",
        )
    )
    journey.run("verify", "--scope", "m1")
    originals = [(origin, f) for f in ("PC-I1", "PC-I2", "@coverage")]
    _dispose(journey, originals)
    assert _closure(journey)["closed"]
    refused = journey.run(
        "run-gate",
        "milestone-review",
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=2,
    )
    assert "--input-json" in refused["error"]["hint"]
    assert journey.count(journey.providers) == 1
    round_input = journey.payload(
        "round.json",
        {
            "schema": "heddle.review-round-input/v1",
            "role": "milestone-review",
            "scope": "m1",
            "purpose": "verification",
            "reason": "Verify the two exact fixes and originating coverage.",
        },
    )
    journey.run("review", "round-open", "--input-json", round_input)
    content = review_content("milestone-review")
    content["prior_dispositions"] = [
        reviewer_disposition(finding_ref(*ref), action="addressed") for ref in originals
    ]
    second = _review(journey, tmp_path, content, expected=0)
    assert "src/example.py" in json.dumps(second["diagnostics"])
    new_origin = second["data"]["run_id"]
    assert _closure(journey)["open_refs"] == [[new_origin, "@coverage"]]
    _dispose(journey, [(new_origin, "@coverage")])
    assert _closure(journey)["closed"]

    # AC-6: explicit attestation, not inferred equivalence or recaptured citations.
    old_rows = journey.read()["review_assignments"]["dispositions"]
    source = root / "src/example.py"
    source.write_text(source.read_text() + "# Lead-inspected harmless clarification.\n")
    journey.run("verify", "--scope", "m1")
    reaffirm = ("review", "reaffirm", "--role", "milestone-review", "--scope", "m1")
    before = snapshot_tree(root)
    journey.run(*reaffirm, "--dry-run")
    assert snapshot_tree(root) == before
    journey.run(*reaffirm)
    rows = journey.read()["review_assignments"]["dispositions"]
    assert rows[: len(old_rows)] == old_rows and len(rows) == len(old_rows) * 2
    originals_by_ref = {(r["run_id"], r["finding_id"]): r for r in old_rows}
    copies_by_ref = {(r["run_id"], r["finding_id"]): r for r in rows[len(old_rows) :]}
    assert originals_by_ref.keys() == copies_by_ref.keys()
    for ref, original in originals_by_ref.items():
        copied = copies_by_ref[ref]
        assert original["references"] == copied["references"]
        assert (
            set(original) == set(copied)
            and original["evidence_basis"] != copied["evidence_basis"]
        )
    assert _closure(journey)["closed"]
    original_test = test.read_bytes()
    test.write_bytes(original_test + b"\n# Changed evidence bytes.\n")
    before = snapshot_tree(root)
    rejected = journey.run(*reaffirm, expected=(2, 3))
    assert origin in json.dumps(rejected) and "PC-I1" in json.dumps(rejected)
    assert snapshot_tree(root) == before
    test.write_bytes(original_test)
    before = snapshot_tree(root)
    journey.run(*reaffirm)
    assert snapshot_tree(root) == before and journey.count(journey.providers) == 2

    journey.run("milestone", "advance")
    for scope in ("m1", "acceptance", "smoke"):
        journey.run("verify", "--scope", scope)
    for stage in ("peer-review", "robustness", "complete"):
        journey.run("phase-exit")
        assert journey.read()["stage"] == stage

    # AC-7/8/9: advisory earlier, fatal now; done-ownership repair refreshes proof.
    (root / "src/final-extra.py").write_text("FINAL = 1\n")
    for flags in (("--dry-run",), ()):
        before = snapshot_tree(root)
        failed = journey.run("feature", "complete", *flags, expected=3)
        assert "coverage" in json.dumps(failed).lower()
        assert snapshot_tree(root) == before and _close_calls(journey) == 0
    _append(journey, "src/final-extra.py")
    for scope in ("m1", "acceptance", "smoke"):
        journey.run("verify", "--scope", scope)
    before = snapshot_tree(root)
    preview = journey.run("feature", "complete", "--dry-run")["data"]
    assert preview["close_suite_command"] == "python3 tests/check.py close"
    assert preview["close_suite"] is None and preview["close_obligation"]["configured"]
    assert snapshot_tree(root) == before and _close_calls(journey) == 0
    accepted = journey.run("feature", "complete", expected=(0, 4))["data"]
    assert (
        accepted["accepted"]
        and accepted["close_suite"] == journey.read()["completion"]["close_suite"]
    )
    assert accepted["close_suite_command"] == preview["close_suite_command"]
    for args in (("feature", "complete"), ("status",), ("kickoff",)):
        terminal = journey.run(*args, expected=(0, 4))["data"]
        for key in ("close_suite_command", "close_suite", "close_obligation"):
            assert terminal[key] == accepted[key]
    assert _close_calls(journey) == 1
    assert accepted["retained_evidence"]["status"] == "archive-bound"

    # AC-10: package floor/help, outside the checkout, and retained candidate proof.
    help_data = journey.run("help", feature=False)["data"]
    assert "review reaffirm" in {row["name"] for row in help_data["commands"]}
    package = installed.python_probe(
        "from importlib.resources import files; import heddle; "
        "print(heddle.__file__); "
        "print(files('heddle.resources').joinpath('implement.briefing.md').read_text())",
        cwd=journey.nested,
    )
    assert package.returncode == 0, package.stderr
    assert (
        str(installed.site_packages) in package.stdout
        and "review reaffirm" in package.stdout
    )
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()
    wheel = next((installed.root / "dist").glob("*.whl"))
    _publish_evidence(
        journey,
        wheel,
        accepted["retained_evidence"],
        base=(
            REPO_ROOT
            / "plans/proof-continuity-v1/archive/acceptance/installed-proof-continuity"
        ),
    )
