"""
AC-3, AC-4 and AC-9: review evidence stays current across formatting-only changes.

Disposable hosts use real Git, gate preparation, recording, closure, readiness,
sync, phase exit and source attribution. Only the provider transports are
doubled (`tests.tiering_review_helpers.provider_transport`). Formatter output is
written as fixed literal bytes: each `*_FORMATTED` Python literal is the pinned
ruff output of its `*_AUTHORED` source, and `_PLAN_FORMATTED` and
`_ORIGIN_FORMATTED` are the Prettier shapes of their `*_AUTHORED` Markdown (table
re-padding, delimiter dashes, an internal space run and trailing whitespace).
The AC-15 integrity case sits here because it needs this module's post-feature
disposition rows.

Activation seams, all reached inside tests and never at collection; the
implement session completing review-evidence currency owns their removal:

- disposition rows gain the optional `basis_evidence`, `reference_equivalence`
  and `rebound_by` fields (the plan's working names);
- `basis_evidence` is an evidence reference whose `artifact` resolves under the
  feature directory, as verification evidence does;
- a re-binding row carries a non-empty `rebound_by` marker and a
  `basis_evidence` reference for the current basis;
- the phase-exit dry run reports the rows it would add under a payload key
  containing "rebind" or "rebound" (the marker's spelling is delegated).

Survivor pins protect today's sealing transaction shape, attempt artifact
admission, raw delivered content and the post-run rejection of behaviour edits.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from heddle.contracts import operations as ops
from heddle.kernel.project_config import KernelError, load_project_config
from heddle.kernel.state import read_state_file
from heddle.runtime.application import execute
from heddle.runtime.verification import reconcile_current_source, workflow_control_paths
from tests.change_impact_explanation_helpers import basis_manifest
from tests.content_identity_helpers import git
from tests.operational_model_helpers import SPEC
from tests.runtime.verification_provenance_helpers import make_current_host
from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import finding, finding_ref
from tests.tiering_helpers import ROLES, entry, snapshot
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    gate_command,
    open_round,
    provider_transport,
    review_content,
    review_status,
    runs,
)

SCAFFOLDING = "review-test-scaffolding"
PLAN = f"plans/{V7_FEATURE}/plan.md"
CHECK = "tests/check.py"
LAUNCHES = ("run-gate", "review round-open", "unconverged review gate")
STAMP = "2026-10-10T00:00Z"

_CHECK_AUTHORED = (
    "from pathlib import Path\n\n"
    "def test_declared_value():\n"
    "    assert 'VALUE = 7' in Path('src/example.py').read_text()\n\n"
    "if __name__ == '__main__':\n"
    "    test_declared_value()\n"
)
_CHECK_FORMATTED = (
    "from pathlib import Path\n\n\n"
    "def test_declared_value():\n"
    '    assert "VALUE = 7" in Path("src/example.py").read_text()\n\n\n'
    'if __name__ == "__main__":\n'
    "    test_declared_value()\n"
)
_CHECK_BEHAVIOUR = _CHECK_FORMATTED.replace("VALUE = 7", "VALUE = 8")
_CITED_AUTHORED = (
    "from pathlib import Path\n"
    "assert 'VALUE = 7' in Path('src/example.py').read_text()\n"
)
_CITED_FORMATTED = (
    "from pathlib import Path\n\n"
    'assert "VALUE = 7" in Path("src/example.py").read_text()\n'
)
_CITED_BEHAVIOUR = _CITED_FORMATTED.replace("VALUE = 7", "VALUE = 8")

_PLAN_HEAD = (
    "# Plan\n\n<!-- heddle:begin plan-status -->\n<!-- heddle:end plan-status -->\n\n"
)
_PLAN_MILESTONES = (
    "### Milestone m1: Declared value\n\n"
    "Satisfies: AC-1\n\nImplement the declared value.\n\n"
    "### Milestone m2: Verify the value\n\n"
    "Satisfies: AC-1\n\nVerify the declared value.\n"
)
_PLAN_AUTHORED = (
    _PLAN_HEAD + "Scaffolding declares `tests/check.py`  for AC-1. \n\n"
    "| Path | Role |\n"
    "|---|---|\n"
    "| tests/check.py | scaffold |\n\n" + _PLAN_MILESTONES
)
_PLAN_FORMATTED = (
    _PLAN_HEAD + "Scaffolding declares `tests/check.py` for AC-1.\n\n"
    "| Path           | Role     |\n"
    "| -------------- | -------- |\n"
    "| tests/check.py | scaffold |\n\n" + _PLAN_MILESTONES
)
_PLAN_BEHAVIOUR = _PLAN_FORMATTED.replace(
    "Implement the declared value.", "Implement the declared value twice."
)
_ORIGIN_AUTHORED = (
    "# Origin\n\nOther feature's  accepted scope. \n\n"
    "| Path | Owner |\n|---|---|\n| outside.py | other |\n"
)
_ORIGIN_FORMATTED = (
    "# Origin\n\nOther feature's accepted scope.\n\n"
    "| Path       | Owner |\n| ---------- | ----- |\n| outside.py | other |\n"
)
_ORIGIN_BEHAVIOUR = _ORIGIN_FORMATTED.replace("accepted scope", "rejected scope")


def _policy_for(*active_roles):
    selected = {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }
    selected.update({role: entry(role) for role in active_roles})
    return selected


def _write(host, relative, text):
    (host / relative).write_text(text, encoding="utf-8")


def _commit(host, message, *paths):
    git(host, "add", *(paths or ("-A",)))
    git(host, "commit", "-qm", message)


def _authored_host(tmp_path, monkeypatch, *, stage, role):
    host, path = current_host(
        tmp_path, monkeypatch, stage=stage, overrides=_policy_for(role)
    )
    _write(host, PLAN, _PLAN_AUTHORED)
    _write(host, CHECK, _CHECK_AUTHORED if stage == "scaffold" else _CITED_AUTHORED)
    _commit(host, "authored inputs")
    return host, path


def _run(run_cli, role):
    code, result = gate_command(run_cli, "run-gate", role)
    assert code in (0, 4) and result["ok"] and result["data"]["accepted"], result
    return result["data"]["run_id"]


def _recorded(path, rows):
    result = dispose(path, rows)
    assert result.ok, result.to_envelope()
    return result


def _first_round(monkeypatch, run_cli, path):
    calls = provider_transport(
        monkeypatch,
        review_content(
            SCAFFOLDING, findings=[finding("TS-I1", classification="implement")]
        ),
    )
    origin = _run(run_cli, SCAFFOLDING)
    _recorded(
        path,
        [
            disposition(origin, "TS-I1", status="retained", references=[CHECK]),
            disposition(origin, "@coverage", status="settled", references=[PLAN]),
        ],
    )
    return calls, origin


def _verification_round(monkeypatch, run_cli, path, origin):
    """A later round whose result affirmatively disposes both original targets."""
    assert open_round(path, role=SCAFFOLDING).ok
    content = review_content(SCAFFOLDING)
    content["prior_dispositions"] = [
        reviewer_disposition(finding_ref(origin, "TS-I1"), action="addressed"),
        reviewer_disposition(finding_ref(origin, "@coverage"), action="addressed"),
    ]
    calls = provider_transport(monkeypatch, content)
    return calls, _run(run_cli, SCAFFOLDING)


def _review_backed_rows(origin, verifier):
    return [
        disposition(
            origin,
            "TS-I1",
            evidence_kind="review",
            review_run_id=verifier,
            references=[CHECK],
        ),
        disposition(verifier, "@coverage", status="settled", references=[PLAN]),
    ]


def _closed_scaffolding(tmp_path, monkeypatch, run_cli):
    """Two rounds, every row at one basis: rows cite the test, the plan and a run."""
    host, path = _authored_host(
        tmp_path, monkeypatch, stage="scaffold", role=SCAFFOLDING
    )
    first, origin = _first_round(monkeypatch, run_cli, path)
    second, verifier = _verification_round(monkeypatch, run_cli, path, origin)
    _recorded(path, _review_backed_rows(origin, verifier))
    row = review_status(path, role=SCAFFOLDING)
    assert row["closed"] and row["evidence_state"] == "current", row
    return host, path, (first, second), (origin, verifier)


def _mixed_basis_scaffolding(tmp_path, monkeypatch, run_cli):
    """Rows this build records on both sides of a formatting-only commit.

    Dispositions, in ledger order:

    0. round-one TS-I1 retained (superseded by 2);
    1. round-one coverage, recorded before the reflow: current only by
       equivalence;
    2. TS-I1 backed by the round-two review, after the reflow: raw-current;
    3. round-two coverage, after the reflow: raw-current.
    """
    host, path = _authored_host(
        tmp_path, monkeypatch, stage="scaffold", role=SCAFFOLDING
    )
    first, origin = _first_round(monkeypatch, run_cli, path)
    _write(host, CHECK, _CHECK_FORMATTED)
    _commit(host, "format tests", CHECK)
    second, verifier = _verification_round(monkeypatch, run_cli, path, origin)
    _recorded(path, _review_backed_rows(origin, verifier))
    return host, path, (first, second), (origin, verifier)


def _assignment(result, role=SCAFFOLDING):
    assert result.ok, result.to_envelope()
    return next(
        row
        for row in result.data["review_closure"]["assignments"]
        if row["role"] == role and row["scope"] == "feature"
    )


def _launches(result) -> list[str]:
    commands = [row["command"] for row in result.to_envelope()["next_actions"]]
    return [text for text in commands if any(mark in text for mark in LAUNCHES)]


def _plan_status(host) -> dict[str, str]:
    synced = execute(ops.Sync(feature=V7_FEATURE))
    assert synced.ok, synced.to_envelope()
    text = (host / PLAN).read_text()
    block = text.split("<!-- heddle:begin plan-status -->")[1].split(
        "<!-- heddle:end plan-status -->"
    )[0]
    return {
        key: value
        for line in block.splitlines()
        if line.startswith("- ") and ": " in line
        for key, value in [line[2:].split(": ", 1)]
    }


def _assert_closed_everywhere(host):
    """Status, orient and the rendered plan-status agree, and none launches."""
    status = execute(ops.Status(feature=V7_FEATURE))
    orient = execute(ops.Orient(feature=V7_FEATURE))
    observed = _assignment(status)
    assert observed == _assignment(orient), (
        "FAIL AC-3: status and orient disagree about the review assignment"
    )
    assert observed["closed"] and observed["open_refs"] == [], (
        "FAIL AC-3: a formatting-only change reopened the review assignment: "
        f"{observed['open_refs']} ({observed['evidence_state']})"
    )
    assert observed["evidence_state"] == "current", (
        f"FAIL AC-3: review evidence reads {observed['evidence_state']}"
    )
    assert observed["evidence_explanations"] == [], (
        "FAIL AC-3: a disposition reads stale after a formatting-only change: "
        f"{observed['evidence_explanations']}"
    )
    for result in (status, orient):
        assert not _launches(result), (
            f"FAIL AC-3: a closed review still offers a launch: {_launches(result)}"
        )
        assert "gate-not-converged" not in result.data["blocking_conditions"], (
            "FAIL AC-3: a closed review still blocks as gate-not-converged"
        )
    rendered = _plan_status(host)
    assert "gate-not-converged" not in rendered["conditions"], (
        f"FAIL AC-9: plan-status renders {rendered['conditions']}"
    )
    assert not any(mark in rendered["next"] for mark in LAUNCHES), (
        f"FAIL AC-9: plan-status offers a review launch: {rendered['next']}"
    )
    first = orient.to_envelope()["next_actions"]
    assert rendered["next"] == (first[0]["command"] if first else "none"), (
        "FAIL AC-3: plan-status next differs from orient's first action"
    )
    return observed


def _provider_calls(calls) -> int:
    """Count every provider call made through the fixture's live transports."""
    return sum(len(transport) for transport in calls)


def _show_identity(run_cli):
    code, shown = gate_command(run_cli, "show-prompt", SCAFFOLDING)
    assert code == 0 and shown["ok"], shown
    return shown["data"]["prompt_identity"]


def _rebinding_preview(value: Any) -> list[Any] | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if ("rebind" in key or "rebound" in key) and isinstance(item, list):
                return item
            found = _rebinding_preview(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _rebinding_preview(item)
            if found is not None:
                return found
    return None


def _attribution_host(tmp_path, monkeypatch):
    """An outside Python source attributed with a cited Markdown origin."""
    root, state_path = make_current_host(tmp_path)
    _write(root, "origin.md", _ORIGIN_AUTHORED)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    _commit(root, "baseline")
    _write(root, "outside.py", _CITED_AUTHORED)
    monkeypatch.chdir(root)
    payload = {
        "schema": "heddle.source-attribution-input/v1",
        "attributions": [
            {
                "paths": ["outside.py"],
                "references": ["origin.md"],
                "reason": "Belongs to the independently accepted other feature.",
            }
        ],
    }
    attributed = execute(ops.AttributeSources(payload, feature="sample-feature"))
    assert attributed.ok, attributed.to_envelope()
    return root, state_path


def _coverage(root, state_path) -> str:
    """Final source coverage status, or the attribution refusal's message."""
    state = read_state_file(state_path)
    controls = workflow_control_paths(load_project_config(root), state)
    try:
        return reconcile_current_source(
            root,
            state,
            "acceptance",
            baseline_probe=state_path.relative_to(root).as_posix(),
            runtime_owned_roots=controls.roots,
            plan_path=state_path.with_name("plan.md").relative_to(root).as_posix(),
            excluded_paths=controls.exact,
        ).status
    except KernelError as error:
        return error.message


def test_ac3_ac9_formatting_only_rewrite_and_commit_keep_review_evidence_current(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _refs = _closed_scaffolding(tmp_path, monkeypatch, run_cli)
    before = yaml.safe_load(path.read_text())["review_assignments"]

    _write(host, CHECK, _CHECK_FORMATTED)
    _write(host, PLAN, _PLAN_FORMATTED)
    _commit(host, "format the test and the plan", CHECK, PLAN)

    _assert_closed_everywhere(host)
    after = yaml.safe_load(path.read_text())["review_assignments"]
    assert after == before, "reads and sync record nothing in the review ledger"
    assert _provider_calls(calls) == 2


def test_ac3_ac9_rows_current_by_raw_and_by_equivalence_close_for_every_consumer(
    tmp_path, monkeypatch, run_cli
):
    """Rows sit at two raw bases, so the latest row's basis is not every row's."""
    host, path, calls, _refs = _mixed_basis_scaffolding(tmp_path, monkeypatch, run_cli)
    rows = yaml.safe_load(path.read_text())["review_assignments"]["dispositions"]
    assert len({row["evidence_basis"] for row in rows}) == 2

    observed = _assert_closed_everywhere(host)

    assert observed["next_step"] == "closed"
    assert _provider_calls(calls) == 2


def test_ac3_a_non_equivalent_edit_stales_exactly_the_rows_that_depend_on_it(
    tmp_path, monkeypatch, run_cli
):
    role = "plan-review"
    host, path = _authored_host(tmp_path, monkeypatch, stage=role, role=role)
    calls = provider_transport(
        monkeypatch,
        review_content(role, findings=[finding("PR-I1", classification="implement")]),
    )
    origin = _run(run_cli, role)
    _recorded(
        path,
        [
            disposition(origin, "PR-I1", references=[CHECK]),
            disposition(origin, "@coverage", status="settled", references=[PLAN]),
        ],
    )
    _write(host, CHECK, _CITED_FORMATTED)
    _write(host, PLAN, _PLAN_FORMATTED)
    _commit(host, "format the cited test and the plan", CHECK, PLAN)
    current = review_status(path, role=role)
    assert current["closed"] and current["evidence_explanations"] == [], (
        "FAIL AC-3: formatting-only rewrites of cited files staled the review: "
        f"{current['open_refs']}"
    )

    _write(host, CHECK, _CITED_BEHAVIOUR)
    test_edit = review_status(path, role=role)
    assert test_edit["open_refs"] == [[origin, "PR-I1"]], (
        "FAIL AC-3: a behaviour edit to the cited test must stale only the row "
        f"citing it: {test_edit['open_refs']}"
    )
    [explanation] = test_edit["evidence_explanations"]
    assert explanation["subject"] == f"{origin}#PR-I1"
    assert explanation["changed_dependencies"] == [CHECK]

    _write(host, PLAN, _PLAN_BEHAVIOUR)
    plan_edit = review_status(path, role=role)
    assert sorted(plan_edit["open_refs"]) == sorted(
        [[origin, "PR-I1"], [origin, "@coverage"]]
    ), "a plan behaviour edit stales every row bound to the plan-review basis"
    assert plan_edit["evidence_state"] == "stale"
    assert len(calls) == 1


def test_ac3_equivalent_change_during_the_provider_call_records_the_launch_basis(
    tmp_path, monkeypatch, run_cli
):
    host, path = _authored_host(
        tmp_path, monkeypatch, stage="scaffold", role=SCAFFOLDING
    )
    launch = _show_identity(run_cli)

    def reflow_while_reviewing(_cli, _prompt):
        _write(host, CHECK, _CHECK_FORMATTED)
        _write(host, PLAN, _PLAN_FORMATTED)
        return review_content(SCAFFOLDING)

    calls = provider_transport(monkeypatch, reflow_while_reviewing)
    code, result = gate_command(run_cli, "run-gate", SCAFFOLDING)

    recorded = runs(path)
    assert len(calls) == 1 and len(recorded) == 1
    assert recorded[0].get("failure_reason") != "reviewed-source-changed", (
        "FAIL AC-3: a review-equivalent source change during the provider call "
        "rejected the completed review"
    )
    assert code in (0, 4) and result["ok"] and result["data"]["accepted"], result
    assert recorded[0]["review_basis_hash"] == launch["review_basis_hash"]
    assert recorded[0]["input_hash"] == launch["input_hash"]
    attempts = yaml.safe_load(path.read_text())["review_assignments"]["attempts"]
    assert len(attempts) == 1 and attempts[0]["source"] is not None
    after = _show_identity(run_cli)
    assert after["review_basis_hash"] != launch["review_basis_hash"], (
        "the recorded basis stays raw; equivalence never replaces it"
    )


def test_ac3_survivor_behaviour_edit_during_the_provider_call_still_rejects(
    tmp_path, monkeypatch, run_cli
):
    """Survivor pin: only review-equivalent changes may pass the post-run check."""
    host, path = _authored_host(
        tmp_path, monkeypatch, stage="scaffold", role=SCAFFOLDING
    )

    def edit_while_reviewing(_cli, _prompt):
        _write(host, CHECK, _CHECK_BEHAVIOUR)
        return review_content(SCAFFOLDING)

    calls = provider_transport(monkeypatch, edit_while_reviewing)
    code, refused = gate_command(run_cli, "run-gate", SCAFFOLDING)

    assert code != 0 and not refused["ok"], refused
    recorded = runs(path)
    assert len(calls) == 1 and len(recorded) == 1
    assert recorded[0]["failure_reason"] == "reviewed-source-changed"
    attempts = yaml.safe_load(path.read_text())["review_assignments"]["attempts"]
    assert len(attempts) == 1 and attempts[0]["source"] is None


def test_ac3_survivor_delivered_prompt_and_input_identity_stay_raw(
    tmp_path, monkeypatch, run_cli
):
    """Survivor pin: reviewers and identities see raw bytes, never canonical ones."""
    host, path = _authored_host(
        tmp_path, monkeypatch, stage="scaffold", role=SCAFFOLDING
    )
    authored = _show_identity(run_cli)
    spec = (host / SPEC).read_text()
    assert "Action: Read VALUE." in spec
    _write(host, SPEC, spec.replace("Action: Read VALUE.", "Action:  Read VALUE."))
    spec_reflowed = _show_identity(run_cli)
    _write(host, PLAN, _PLAN_FORMATTED)
    after = _show_identity(run_cli)
    for earlier, later in ((authored, spec_reflowed), (spec_reflowed, after)):
        assert later["review_basis_hash"] != earlier["review_basis_hash"]
        assert later["input_hash"] != earlier["input_hash"]

    calls = provider_transport(monkeypatch, review_content(SCAFFOLDING))
    _run(run_cli, SCAFFOLDING)

    [(_cli, prompt, _command)] = calls
    assert "Action:  Read VALUE." in prompt
    assert "Action: Read VALUE." not in prompt
    assert runs(path)[0]["review_basis_hash"] == after["review_basis_hash"]


def test_ac4_survivor_rows_appended_in_the_sealing_write_are_recorded_by_receipt(
    tmp_path, monkeypatch, run_cli
):
    """Survivor pin: the sealing write may append rows that its receipt records.

    `validate_retained_transition` takes sealed assignments from receipts already
    in the pre-transition document, and the receipt is built from the locked
    document, so rows appended to it first are sealed in the same revision.
    """
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from heddle.kernel.write_path import apply_phase_exit
    from heddle.runtime.review_assignments import build_boundary_acceptance
    from heddle.runtime.state_store import commit_state

    host, path, calls, (_origin, verifier) = _closed_scaffolding(
        tmp_path, monkeypatch, run_cli
    )
    config = load_project_config(host)
    current = resolve_snapshot(config, V7_FEATURE)
    original = yaml.safe_load(path.read_text())
    appended = len(original["review_assignments"]["dispositions"])

    def seal(document):
        rows = document["review_assignments"]["dispositions"]
        rows.append({**rows[-1], "at": STAMP})
        receipt = build_boundary_acceptance(
            config,
            current,
            document,
            boundary="scaffold",
            scope=None,
            stage_after="implement",
            accepted_at=STAMP,
        )
        sealed = apply_phase_exit(document)
        sealed["review_assignments"]["acceptances"].append(receipt)
        return sealed

    committed = commit_state(path, expect_revision=original["revision"], transform=seal)

    assert committed.revision == original["revision"] + 1
    value = yaml.safe_load(path.read_text())
    assert value["stage"] == "implement"
    [receipt] = value["review_assignments"]["acceptances"]
    records = {
        row["index"]
        for row in receipt["assignments"][0]["records"]
        if row["family"] == "dispositions"
    }
    assert appended in records and appended - 1 not in records
    assert value["review_assignments"]["dispositions"][appended]["run_id"] == verifier
    assert _assignment(execute(ops.Status(feature=V7_FEATURE)))["evidence_state"] == (
        "accepted"
    )
    assert _provider_calls(calls) == 2


def test_ac4_survivor_attempt_evidence_artifact_is_recorded_reloaded_and_retained(
    tmp_path, monkeypatch, run_cli
):
    """Survivor pin: an attempt admits an extra `evidence` artifact in verification/.

    The reviewer-basis manifest rides on the attempt this way; recording, reads,
    sealing and completion retention must accept and keep it unchanged.
    """
    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.io.source import observe_source, resolve_source_definition
    from heddle.kernel.state import parse_state_document
    from heddle.runtime import recording
    from heddle.runtime.completion import _retained_paths
    from heddle.runtime.verification import publish_source_evidence

    host, path = _authored_host(
        tmp_path, monkeypatch, stage="scaffold", role=SCAFFOLDING
    )
    evidence = observe_source(
        host, resolve_source_definition(host, "feature-inputs", (CHECK, PLAN))
    )
    reference, _reused = publish_source_evidence(path.parent, evidence)
    assert reference.artifact.startswith("verification/")
    extra = ArtifactRef(reference.artifact, reference.artifact_sha256, "evidence")
    record = recording.record_gate_run

    def with_manifest(*args, artifacts=(), **kwargs):
        return record(*args, artifacts=(*artifacts, extra), **kwargs)

    monkeypatch.setattr(recording, "record_gate_run", with_manifest)
    calls = provider_transport(monkeypatch, review_content(SCAFFOLDING))
    origin = _run(run_cli, SCAFFOLDING)
    monkeypatch.setattr(recording, "record_gate_run", record)
    _recorded(
        path, [disposition(origin, "@coverage", status="settled", references=[PLAN])]
    )
    assert execute(ops.PhaseExit(feature=V7_FEATURE)).ok

    raw = yaml.safe_load(path.read_text())
    [attempt] = raw["review_assignments"]["attempts"]
    assert {
        "path": reference.artifact,
        "sha256": reference.artifact_sha256,
        "role": "evidence",
        "kind": "file",
        "mode": None,
    } in attempt["artifacts"]
    state = parse_state_document(raw, source=path)
    assert extra in state.review_assignments.attempts[0].artifacts
    assert reference.artifact in _retained_paths(state)
    assert _assignment(execute(ops.Status(feature=V7_FEATURE)))["closed"]
    assert len(calls) == 1


def test_ac4_sealing_rebinds_exactly_the_equivalence_current_rows(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, (origin, verifier) = _mixed_basis_scaffolding(
        tmp_path, monkeypatch, run_cli
    )
    row = review_status(path, role=SCAFFOLDING)
    assert row["closed"], (
        "FAIL AC-4: the mixed assignment is not closed before sealing; rows "
        f"current only by equivalence read stale: {row['open_refs']}"
    )
    current_basis = row["evidence_basis"]
    before = yaml.safe_load(path.read_text())
    rows_before = before["review_assignments"]["dispositions"]
    rebinding_source = rows_before[1]
    assert (rebinding_source["run_id"], rebinding_source["finding_id"]) == (
        origin,
        "@coverage",
    )
    assert rebinding_source["evidence_basis"] != current_basis
    raw_current = (rows_before[2], rows_before[3])
    assert [item["evidence_basis"] for item in raw_current] == [current_basis] * 2

    unchanged = snapshot(host)
    preview = execute(
        ops.PhaseExit(
            feature=V7_FEATURE, expect_revision=before["revision"], dry_run=True
        )
    )
    assert preview.ok, preview.to_envelope()
    assert snapshot(host) == unchanged, "a dry run writes and publishes nothing"
    reported = _rebinding_preview(preview.data)
    assert reported is not None, (
        "FAIL AC-4: the phase-exit dry run does not report the re-binding rows "
        "it would add"
    )
    [would_add] = reported
    assert origin in json.dumps(would_add) and "@coverage" in json.dumps(would_add)

    sealed = execute(ops.PhaseExit(feature=V7_FEATURE))

    assert sealed.ok, sealed.to_envelope()
    value = yaml.safe_load(path.read_text())
    assert value["revision"] == before["revision"] + 1, "one sealing write"
    rows = value["review_assignments"]["dispositions"]
    assert rows[: len(rows_before)] == rows_before
    added = rows[len(rows_before) :]
    assert len(added) == 1, (
        "FAIL AC-4: sealing must add exactly one re-binding row per "
        f"equivalence-current row and none for raw-current rows, added {added}"
    )
    [rebound] = added
    assert rebound.get("rebound_by") and not rebinding_source.get("rebound_by"), (
        "FAIL AC-4: the re-binding row carries no marker naming the equivalence"
    )
    excluded = {"evidence_basis", "basis_evidence", "rebound_by", "at"}
    assert {k: v for k, v in rebound.items() if k not in excluded} == {
        k: v for k, v in rebinding_source.items() if k not in excluded
    }, "same resolution, evidence and citations"
    assert rebound["evidence_basis"] == current_basis
    basis_manifest(path, rebound, ac="AC-4")
    [receipt] = value["review_assignments"]["acceptances"]
    accepted = receipt["assignments"][0]
    assert accepted["source_identity"] == current_basis
    assert {
        item["index"]
        for item in accepted["records"]
        if item["family"] == "dispositions"
    } == {2, 3, len(rows_before)}, "the receipt seals current rows and the re-binding"

    validated = execute(ops.Validate(feature=V7_FEATURE))
    assert validated.ok, validated.to_envelope()
    observed = _assignment(execute(ops.Status(feature=V7_FEATURE)))
    assert observed["closed"] and observed["evidence_state"] == "accepted"
    advanced = execute(ops.MilestoneAdvance(feature=V7_FEATURE))
    assert advanced.ok, advanced.to_envelope()
    sealed_bytes = snapshot(host)
    refused = dispose(
        path,
        [
            disposition(
                verifier,
                "@coverage",
                status="settled",
                references=[PLAN],
                reason="A later lead judgment recorded after sealing",
            )
        ],
    )
    assert not refused.ok and snapshot(host) == sealed_bytes
    assert _provider_calls(calls) == 2


def test_ac3_attribution_references_use_equivalence_while_the_source_stays_exact(
    tmp_path, monkeypatch
):
    root, state_path = _attribution_host(tmp_path, monkeypatch)
    assert _coverage(root, state_path) == "complete"

    _write(root, "origin.md", _ORIGIN_FORMATTED)
    observed = _coverage(root, state_path)
    assert observed == "complete", (
        "FAIL AC-3: a formatting-only change to an attribution's cited evidence "
        f"stales the attribution: {observed}"
    )

    _write(root, "origin.md", _ORIGIN_BEHAVIOUR)
    assert "evidence changed: origin.md" in _coverage(root, state_path)
    _write(root, "origin.md", _ORIGIN_FORMATTED)
    assert _coverage(root, state_path) == "complete"
    _write(root, "outside.py", _CITED_FORMATTED)
    assert _coverage(root, state_path).endswith(
        "1 of 1 attributed paths changed: outside.py"
    ), "the attributed source itself still compares raw"


def test_ac15_a_missing_or_altered_disposition_manifest_is_an_integrity_error(
    tmp_path, monkeypatch, run_cli
):
    """The reflow makes currency read the manifest instead of the raw basis."""
    host, path, _calls, _refs = _closed_scaffolding(tmp_path, monkeypatch, run_cli)
    rows = yaml.safe_load(path.read_text())["review_assignments"]["dispositions"]
    reference = rows[-1].get("basis_evidence")
    assert reference, (
        "FAIL AC-15: a disposition recorded by this build names no basis manifest"
    )
    artifact = path.parent / reference["artifact"]
    assert artifact.is_file(), f"the manifest is published at {artifact}"
    content = artifact.read_bytes()
    _write(host, CHECK, _CHECK_FORMATTED)
    _write(host, PLAN, _PLAN_FORMATTED)
    _commit(host, "format the test and the plan", CHECK, PLAN)
    assert _assignment(execute(ops.Status(feature=V7_FEATURE)))["closed"]

    findings = {
        row["finding_id"] for row in rows if row.get("basis_evidence") == reference
    }
    for damaged in (None, content + b"\n"):
        artifact.unlink()
        if damaged is not None:
            artifact.write_bytes(damaged)
        result = execute(ops.Status(feature=V7_FEATURE))
        assert not result.ok and result.error.code == "workspace-invalid", (
            "FAIL AC-15: a missing or altered manifest must be an integrity "
            f"failure, not a fallback: {result.to_envelope()}"
        )
        text = json.dumps(result.to_envelope()["error"])
        assert reference["artifact"] in text and any(f in text for f in findings), (
            f"FAIL AC-15: the integrity failure names the finding and artifact: {text}"
        )
        artifact.unlink(missing_ok=True)
        artifact.write_bytes(content)
    assert _assignment(execute(ops.Status(feature=V7_FEATURE)))["closed"]
