"""AC-1..10 installed composition; execution needs its own exact grant.

Synthetic native birth/tasks/proof and actual installed CLI throughout. Only the
external reviewer executable is controlled. No provider credentials or network.

``test_installed_change_impact_rebinding_journey`` is the change-impact
rebinding witness for AC-3, AC-4, AC-5, AC-7, AC-10, AC-11 and AC-12. A
scripted claude double answers exactly two reviews and fails any further
launch; the formatter is a fixed rewrite to known reflowed bytes. It reaches
the change-impact behaviour only through the installed CLI, so collection
never depends on it. The surfaces it pins, each asserted where the journey
reaches it:

- review evidence current across a review-equivalent rewrite (closure rows and
  orient actions);
- the ``rebound_by`` marker on re-binding rows appended by phase exit, and a
  phase-exit dry run that names the rows it would add;
- typed ``changes`` on a ``verification_status`` explanation, with a
  ``formatting-only`` kind;
- ``heddle verify --stale`` and its ``data.verifications`` rows;
- the ``review-reaffirm`` authoring work in orient;
- ``heddle review reaffirm`` without ``--role``/``--scope``, with exit 4 while
  rows remain and exit 0 once none do.
"""

from __future__ import annotations

import contextlib
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
    write_scripted_claude_shim,
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
    rejected = journey.run(*reaffirm, expected=1)
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


QUOTE_PLAN = (
    "# Plan\n\n## Environment\n\npython3 tests/test_quote.py verify\n\n"
    "| Milestone | Proof |\n|---|---|\n| m1 | QUOTE reads ready |\n\n"
    "### Milestone m1: Quote readiness\n\nSatisfies: AC-1\n\n"
    "Set QUOTE to ready and exercise the actual check.\n"
)
# Prettier's table re-padding: cell padding and delimiter dashes only.
REFLOWED_QUOTE_PLAN = QUOTE_PLAN.replace(
    "| Milestone | Proof |\n|---|---|\n| m1 | QUOTE reads ready |\n",
    "| Milestone | Proof             |\n"
    "| --------- | ----------------- |\n"
    "| m1        | QUOTE reads ready |\n",
)
REVIEW_LAUNCHES = frozenset({"run-gate", "run-gates", "review round-open"})


def _quote_test(calls, *, reflowed):
    """The host test as authored, or as ``ruff format`` would rewrite it."""
    if not reflowed:
        return (
            "import runpy, sys\nfrom pathlib import Path\n"
            "def test_quote():\n"
            "    #the declared quote\n"
            "    assert runpy.run_path('src/quote.py')['QUOTE'] == 'ready'\n"
            "if __name__ == '__main__':\n"
            "    test_quote()\n"
            f"    with Path({str(calls)!r}).open('a') as stream:\n"
            "        stream.write(sys.argv[1] + '\\n')\n"
        )
    # Quotes, blank lines and the comment's space change; the tree does not.
    return (
        "import runpy, sys\nfrom pathlib import Path\n\n\n"
        "def test_quote():\n"
        "    # the declared quote\n"
        '    assert runpy.run_path("src/quote.py")["QUOTE"] == "ready"\n\n\n'
        'if __name__ == "__main__":\n'
        "    test_quote()\n"
        f'    with Path({json.dumps(str(calls))}).open("a") as stream:\n'
        '        stream.write(sys.argv[1] + "\\n")\n'
    )


def _review_event(content):
    return (
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


def _assignment(envelope, role, scope="feature"):
    return next(
        row
        for row in envelope["data"]["review_closure"]["assignments"]
        if row["role"] == role and row["scope"] == scope
    )


def _verification(envelope, scope):
    return next(
        row for row in envelope["data"]["verification_status"] if row["scope"] == scope
    )


def _review_launches(envelope):
    """Actions that would call a provider or open another review round."""
    return [
        action["command"]
        for action in envelope["next_actions"]
        if action["action"]["kind"] == "command"
        and action["action"]["operation"]["name"] in REVIEW_LAUNCHES
    ]


def _typed_changes(explanation, ac):
    """``{(kind, path)}`` from an explanation's typed ``changes``."""
    subject = explanation.get("scope") or explanation.get("subject")
    assert "changes" in explanation, (
        f"FAIL {ac}: the explanation for {subject} carries no typed changes: "
        f"{sorted(explanation)}"
    )
    return {
        (change.get("kind"), change.get("path")) for change in explanation["changes"]
    }


def _gate(journey, role):
    before = journey.count(journey.providers)
    result = journey.run(
        "run-gate",
        role,
        "--cli",
        "claude",
        "--model",
        FABLE["model"],
        "--reasoning-effort",
        "high",
        expected=4,
    )
    assert result["data"]["accepted"]
    assert journey.count(journey.providers) == before + 1
    return result["data"]["run_id"]


def _inspected(run_id, finding_id, references, *, status="addressed"):
    return {
        "run_id": run_id,
        "finding_id": finding_id,
        "status": status,
        "evidence_kind": "inspection",
        "references": references,
        "reason": "Lead inspection of the cited quote work resolves this original.",
    }


def _dispose_rows(journey, name, rows):
    payload = journey.payload(
        name, {"schema": "heddle.review-disposition-input/v1", "dispositions": rows}
    )
    return journey.run("review", "disposition", "--input-json", payload)


def _step(journey, ac, *args, expected=0):
    """Run one command whose outcome this feature defines, labelled by its AC."""
    try:
        return journey.run(*args, expected=expected)
    except AssertionError as error:
        raise AssertionError(f"FAIL {ac}: {error}") from error


@contextlib.contextmanager
def _report_on_failure(journey):
    """Print the shim log and the latest envelopes when the journey fails."""
    try:
        yield
    except BaseException:
        if journey.providers.exists():
            print("provider calls:", journey.count(journey.providers))
            for line in journey.providers.read_text().splitlines():
                print(json.loads(line)["argv"])
        for entry in journey.transcript[-6:]:
            print(entry["argv"][1:], "exit", entry["exit"])
            print(entry["stdout"][-4000:], entry["stderr"][-2000:])
        raise


def test_installed_change_impact_rebinding_journey(installed, tmp_path):
    journey = start_host(installed, tmp_path)
    root = journey.root
    plan = journey.state.parent / "plan.md"
    plan_reference = plan.relative_to(root).as_posix()
    source = root / "src/quote.py"
    test = root / "tests/test_quote.py"
    with _report_on_failure(journey):
        # Scaffolding review (limit 2, primary only) and milestone review on;
        # every other role and both owner checkpoints off.
        policy = fixture_policy(reviewed=False)
        policy["revision"] = 2
        policy["entries"] = [
            asdict(entry(row["role"], primary=FABLE, limit=2))
            if row["role"] in ("review-test-scaffolding", "milestone-review")
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
        config = root / ".heddle.yaml"
        config.write_text(
            config.read_text()
            + "checkpoints:\n  specification: false\n  review_changes: false\n"
        )

        # A spec, a plan with a table, and m1 owning the quote source and test.
        (root / "tests").mkdir(exist_ok=True)
        source.write_text("QUOTE = 'draft'\n")
        test.write_text(_quote_test(journey.calls, reflowed=False))
        (root / SPEC).write_text(
            f"---\ntype: feature-spec\narea: runtime\nfeature_name: {FEATURE}\n"
            "lifecycle: active\n---\n\n# Quote readiness\n\n"
            "## Purpose\n\nDeliver QUOTE = ready.\n\n## Acceptance Criteria\n\n"
            "### AC-1: Quote readiness\n\nPriority: MUST\n"
            "Action: Read QUOTE.\nExpected: QUOTE equals ready.\n"
            "Verified-by: tests/test_quote.py::test_quote\n"
        )
        plan.write_text(QUOTE_PLAN)
        (journey.state.parent / "brief.md").write_text(
            "# Brief\n\nDeliver QUOTE = ready.\n"
        )
        for key, label in (
            ("test_command", "verify"),
            ("smoke_test", "smoke"),
            ("acceptance_test", "acceptance"),
        ):
            journey.run(
                "commands",
                "set",
                key,
                "--command",
                f"python3 tests/test_quote.py {label}",
            )
        milestone = {
            "title": "Quote readiness",
            "complexity": "low",
            "satisfies": ["AC-1"],
            "depends_on": [],
            "owns": ["src/quote.py", "tests/test_quote.py"],
            "verification": {
                "command": "python3 tests/test_quote.py milestone",
                "expected": "QUOTE is ready",
            },
        }
        journey.run(
            "milestone",
            "add",
            "--from-file",
            journey.payload("milestone.json", milestone),
        )
        attribution = {
            "schema": "heddle.source-attribution-input/v1",
            "attributions": [
                {
                    "paths": [".heddle.yaml"],
                    "references": ["src/bootstrap.py"],
                    "reason": "Host checkpoint configuration, not feature work.",
                }
            ],
        }
        journey.run(
            "feature",
            "sources",
            "attribute",
            "--from-file",
            journey.payload("attribution.json", attribution),
        )
        git(root, "add", "src", "tests", ".heddle.yaml")
        git(root, "commit", "-qm", "synthetic quote work")
        for stage in ("spec-review", "plan-review", "scaffold"):
            journey.run("phase-exit", "--through", "complete")
            assert journey.read()["stage"] == stage
        binary = write_scripted_claude_shim(
            tmp_path / "controlled-bin",
            [
                _review_event(
                    review_content(
                        "review-test-scaffolding",
                        findings=[finding("SC-I1", classification="implement")],
                    )
                ),
                _review_event(
                    review_content(
                        "milestone-review",
                        findings=[finding("MR-I1", classification="implement")],
                    )
                ),
            ],
            journey.providers,
        )
        journey.env["PATH"] = (
            str(binary.parent) + os.pathsep + journey.installed.env["PATH"]
        )

        # Scaffolding review: one important finding; rows cite the test and plan.
        scaffolding = _gate(journey, "review-test-scaffolding")
        _dispose_rows(
            journey,
            "scaffolding-dispositions.json",
            [
                _inspected(
                    scaffolding, "SC-I1", ["tests/test_quote.py", plan_reference]
                ),
                _inspected(
                    scaffolding, "@coverage", ["tests/test_quote.py"], status="settled"
                ),
            ],
        )
        assert _assignment(journey.run("status"), "review-test-scaffolding")["closed"]
        recorded = journey.read()["review_assignments"]["dispositions"]
        assert [(row["run_id"], row["finding_id"]) for row in recorded] == [
            (scaffolding, "SC-I1"),
            (scaffolding, "@coverage"),
        ]

        # AC-3/AC-7: a committed formatter rewrite of both cited files, then a
        # commit and its revert, leave the review closed without a provider call.
        test.write_text(_quote_test(journey.calls, reflowed=True))
        plan.write_text(REFLOWED_QUOTE_PLAN)
        git(root, "add", "tests/test_quote.py")
        git(root, "commit", "-qm", "format the quote test")
        reflowed = _assignment(journey.run("status"), "review-test-scaffolding")
        assert reflowed["closed"] and reflowed["evidence_state"] == "current", (
            "FAIL AC-3: a review-equivalent rewrite of the cited test and plan "
            f"reopened scaffolding review: {reflowed}"
        )
        scratch = root / "tests/test_scratch.py"
        scratch.write_text("def test_scratch():\n    pass\n")
        git(root, "add", "tests/test_scratch.py")
        git(root, "commit", "-qm", "unrelated scratch test")
        git(root, "revert", "--no-edit", "HEAD")
        for command in ("orient", "status"):
            envelope = journey.run(command)
            row = _assignment(envelope, "review-test-scaffolding")
            assert row["closed"] and row["evidence_state"] == "current", (
                f"FAIL AC-3: {command} reports scaffolding review open: {row}"
            )
            assert row["evidence_basis"] == reflowed["evidence_basis"], (
                "FAIL AC-7: a commit and its revert changed the scaffolding "
                f"basis: {reflowed['evidence_basis']} -> {row['evidence_basis']}"
            )
            assert not _review_launches(envelope), (
                f"FAIL AC-3: {command} offers a review launch or round-open: "
                f"{_review_launches(envelope)}"
            )
            assert "gate-not-converged" not in envelope["data"]["blocking_conditions"]
        assert envelope["next_actions"][0]["command"].startswith("heddle phase-exit")
        assert journey.count(journey.providers) == 1

        # AC-4: phase exit seals with one re-binding row per equivalence-current
        # row, and its dry run names those rows without writing.
        before = snapshot_tree(root)
        preview = _step(journey, "AC-4", "phase-exit", "--dry-run")
        assert snapshot_tree(root) == before, "FAIL AC-4: the dry run wrote"
        previewed = json.dumps(preview["data"])
        assert all(
            token in previewed for token in (scaffolding, "SC-I1", "@coverage")
        ), f"FAIL AC-4: the dry run names no re-binding rows: {preview['data']}"
        _step(journey, "AC-4", "phase-exit")
        state = journey.read()
        assert state["stage"] == "implement"
        ledger = state["review_assignments"]
        rebound = ledger["dispositions"][len(recorded) :]
        originals = {(row["run_id"], row["finding_id"]): row for row in recorded}
        assert sorted((row["run_id"], row["finding_id"]) for row in rebound) == sorted(
            originals
        ), f"FAIL AC-4: sealing appended {rebound}, expected one row per original"
        for row in rebound:
            original = originals[(row["run_id"], row["finding_id"])]
            assert row.get("rebound_by"), (
                f"FAIL AC-4: re-binding row names no equivalence marker: {row}"
            )
            for field in ("status", "evidence_kind", "references", "reason"):
                assert row[field] == original[field], (
                    f"FAIL AC-4: the re-binding row changed {field}: {row}"
                )
            assert row["evidence_basis"] != original["evidence_basis"]
        sealed = next(
            item["id"]
            for item in ledger["assignments"]
            if item["role"] == "review-test-scaffolding"
        )
        receipt = next(
            item
            for acceptance in ledger["acceptances"]
            for item in acceptance["assignments"]
            if item["assignment_id"] == sealed
        )
        assert {row["evidence_basis"] for row in rebound} == {
            receipt["source_identity"]
        }, "FAIL AC-4: the receipt does not seal the re-bound raw basis"
        assert {
            record["index"]
            for record in receipt["records"]
            if record["family"] == "dispositions"
        } == set(range(len(recorded), len(recorded) + len(rebound)))
        journey.run("validate")

        # At implement: a passing m1 fact, then the m1 milestone review with one
        # finding; row A cites the plan, row B the host test file.
        journey.run("milestone", "advance")
        journey.run("task", "add", "Make the quote ready")
        task = journey.read()["milestones"][0]["tasks"][-1]["id"]
        source.write_text("QUOTE = 'ready'\n")
        journey.run("task", "done", task)
        journey.run("verify", "--scope", "m1")
        assert _freshness(journey) == "fresh"
        review = _gate(journey, "milestone-review")
        _dispose_rows(
            journey,
            "milestone-dispositions.json",
            [
                _inspected(review, "MR-I1", [plan_reference]),
                _inspected(
                    review, "@coverage", ["tests/test_quote.py"], status="settled"
                ),
            ],
        )
        assert _closure(journey)["closed"]

        # AC-5/AC-12: a formatter rewrite of the source stales m1 as
        # formatting-only, review rows stay current, and verify --stale refreshes.
        source.write_text('QUOTE = "ready"\n')
        status = journey.run("status")
        fact = _verification(status, "m1")
        assert fact["freshness"] == "content-stale", (
            f"FAIL AC-5: verification must stay raw: {fact}"
        )
        assert _typed_changes(fact, "AC-5") == {("formatting-only", "src/quote.py")}, (
            f"FAIL AC-5: m1 does not name src/quote.py as formatting-only: {fact}"
        )
        assert "formatting" in str(fact["cause"]).lower(), (
            f"FAIL AC-5: the explanation does not say formatting-only: {fact}"
        )
        milestone_review = _assignment(status, "milestone-review", "m1")
        assert milestone_review["closed"], (
            "FAIL AC-3: a review-equivalent source rewrite reopened milestone "
            f"review: {milestone_review}"
        )
        orient = journey.run("orient")
        assert any(
            action["action"]["kind"] == "command"
            and action["action"]["operation"]["name"] == "verify"
            and (
                action["action"]["operation"]["arguments"].get("scope") == "m1"
                or action["action"]["operation"]["arguments"].get("stale")
            )
            for action in orient["next_actions"]
        ), f"FAIL AC-12: orient routes no m1 verification: {orient['next_actions']}"
        assert not _review_launches(orient)
        facts = len(journey.read()["verifications"])
        refreshed = _step(journey, "AC-12", "verify", "--stale")
        rows = refreshed["data"].get("verifications")
        assert isinstance(rows, list) and [
            (row["scope"], row["status"]) for row in rows
        ] == [("m1", "fresh")], (
            f"FAIL AC-12: verify --stale did not rerun exactly m1: {refreshed['data']}"
        )
        assert len(journey.read()["verifications"]) == facts + 1
        assert _freshness(journey) == "fresh"
        assert _closure(journey)["closed"]

        # AC-11: a behaviour edit to the cited test routes reaffirm as authoring
        # work: row A writable, row B refused naming the changed path.
        test.write_text(
            test.read_text().replace(
                '["QUOTE"] == "ready"', '["QUOTE"].startswith("ready")'
            )
        )
        orient = journey.run("orient")
        stale = _assignment(orient, "milestone-review", "m1")
        assert not stale["closed"] and stale["evidence_explanations"]
        # Since the rows were recorded, src/quote.py changed only in formatting.
        for explanation in stale["evidence_explanations"]:
            assert {
                path
                for kind, path in _typed_changes(explanation, "AC-6")
                if kind != "formatting-only"
            } == {"tests/test_quote.py"}, (
                f"FAIL AC-6: the stale row does not name exactly the edited test: "
                f"{explanation}"
            )
        work = [
            action
            for action in orient["next_actions"]
            if action["action"]["kind"] == "authoring"
            and action["action"]["work"] == "review-reaffirm"
        ]
        assert len(work) == 1, (
            "FAIL AC-11: orient offers no review-reaffirm work: "
            f"{orient['next_actions']}"
        )
        offered = json.dumps(work[0])
        assert all(
            token in offered
            for token in (review, "MR-I1", "@coverage", "tests/test_quote.py")
        ), f"FAIL AC-11: the work does not list both rows and the path: {work[0]}"
        assert work[0]["command"].startswith("heddle review reaffirm"), work[0]
        assert not _review_launches(orient)

        # AC-10: reaffirm without a role writes row A, lists row B with its
        # changed path and leaves the sealed scaffolding assignment alone.
        earlier = journey.read()["review_assignments"]
        reaffirmed = _step(journey, "AC-10", "review", "reaffirm", expected=4)
        ledger = journey.read()["review_assignments"]
        added = ledger["dispositions"][len(earlier["dispositions"]) :]
        assert [(row["run_id"], row["finding_id"]) for row in added] == [
            (review, "MR-I1")
        ], f"FAIL AC-10: reaffirm wrote {added}, expected row A only"
        row_a = next(
            row
            for row in reversed(earlier["dispositions"])
            if (row["run_id"], row["finding_id"]) == (review, "MR-I1")
        )
        assert added[0]["references"] == row_a["references"]
        assert added[0]["evidence_basis"] != row_a["evidence_basis"]
        assert ledger["acceptances"] == earlier["acceptances"]
        listed = json.dumps(reaffirmed["data"])
        assert "@coverage" in listed and "tests/test_quote.py" in listed, (
            f"FAIL AC-10: reaffirm does not list row B and its path: {listed}"
        )

        # AC-10: once row B is re-disposed nothing remains and reaffirm exits 0.
        _dispose_rows(
            journey,
            "redisposed.json",
            [
                _inspected(
                    review, "@coverage", ["tests/test_quote.py"], status="settled"
                )
            ],
        )
        settled = len(journey.read()["review_assignments"]["dispositions"])
        _step(journey, "AC-10", "review", "reaffirm")
        assert len(journey.read()["review_assignments"]["dispositions"]) == settled
        assert _closure(journey)["closed"]

        # Deterministic pass conditions over the whole journey.
        assert journey.count(journey.providers) == 2, (
            "FAIL AC-3: an unexpected review launched; provider calls "
            f"{journey.count(journey.providers)}"
        )
        assert [
            (fact["scope"], fact["exit_code"])
            for fact in journey.read()["verifications"]
        ] == [("m1", 0), ("m1", 0)], "FAIL AC-12: unexpected verification facts"
        assert journey.calls.read_text().splitlines() == ["milestone", "milestone"]
        journey.run("validate")
        assert not installed.forbidden_log.exists()
        assert not installed.network_log.exists()
