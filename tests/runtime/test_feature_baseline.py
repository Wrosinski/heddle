"""AC-8: an unavailable baseline refuses, and a recorded re-point restores it.

Hosts record a v10 feature baseline, then lose it: a ``missing`` baseline names
an object the repository does not hold, and a ``diverged`` one is reworded by
``git commit --amend`` so it is no longer an ancestor of ``HEAD``. Only the
provider transports are doubled, and every refusal is checked against the
number of provider launches.

Activation seam: ``baseline_set`` (``tests/change_impact_subject_helpers.py``)
requires the ``feature baseline set`` command in the public contract manifest
before it runs the CLI. The driver's block on ``baseline-unavailable`` is
proven in the driver routing tests.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from tests.change_impact_explanation_helpers import typed_changes
from tests.change_impact_subject_helpers import (
    BINDING_TEST,
    SCAFFOLD_ROLE,
    STABLE_TEST,
    UNRELATED,
    BaselineHost,
    assignment_basis,
    baseline_host,
    baseline_set,
    baseline_set_contract,
    commit_all,
    expected_assignment_basis,
    git,
    make_baseline_unavailable,
    plan_status,
    record_baseline,
    reviewer_subject,
)
from tests.operational_model_helpers import read, write
from tests.tiering_review_helpers import (
    dispose,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    review_status,
    runs,
)

EXTRA_TEST = "tests/test_extra.py"
REASON_WORDS = {"missing": ("missing",), "diverged": ("diverged", "not an ancestor")}
REPOINT_COMMAND = "heddle feature baseline set"
# Any wording that the feature holds no baseline; "unavailable" does not match.
ABSENCE = re.compile(r"\b(?:no|none|without|lacks|missing|absent|not recorded)\b")


def _names_reason(text: str, reason: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in REASON_WORDS[reason])


def _orient(run_cli) -> dict:
    code, result = gate_command(run_cli, "orient")
    assert code == 0 and result["ok"], result
    return result


def _revision(host: BaselineHost) -> int:
    return read(host.state)["revision"]


def _with_done_task(host: BaselineHost) -> None:
    value = read(host.state)
    value["milestones"][0]["tasks"] = [
        {"id": "m1-t1", "text": "Declare VALUE", "status": "done"}
    ]
    write(host.state, value)


def _repoint_host(tmp_path, monkeypatch) -> tuple[BaselineHost, str]:
    """History parent -> baseline (adds a test) -> commit changing that test.

    Relative to the parent or the baseline the added test is in the subject;
    relative to a squash of both commits it is not.
    """
    parent = baseline_host(tmp_path, monkeypatch, record=False)
    (parent.root / EXTRA_TEST).write_text("def test_extra():\n    assert 1 == 1\n")
    baseline = commit_all(parent.root, "add a test at the feature baseline")
    record_baseline(parent.state, baseline)
    (parent.root / EXTRA_TEST).write_text("def test_extra():\n    assert 1 == 2\n")
    commit_all(parent.root, "change the added test")
    return BaselineHost(parent.root, parent.state, baseline), parent.baseline


def _squash_onto(host: BaselineHost, parent: str) -> str:
    git(host.root, "reset", "--soft", parent)
    git(host.root, "commit", "-qm", "squash the feature commits")
    return git(host.root, "rev-parse", "HEAD")


def _repoint(run_cli, host: BaselineHost, commit: str, reason: str) -> dict:
    code, result = baseline_set(
        run_cli,
        "--commit",
        commit,
        "--reason",
        reason,
        "--expect-revision",
        str(_revision(host)),
    )
    assert code == 0 and result["ok"], (
        f"FAIL AC-8: the re-point to {commit} was refused: {result}"
    )
    return result


def _oid(value) -> str:
    return value["oid"] if isinstance(value, dict) else value


@pytest.mark.parametrize(
    ("stage", "reason"),
    [("scaffold", "missing"), ("implement", "diverged")],
)
def test_ac8_unavailable_baseline_blocks_orient_and_plan_status_with_its_remedy(
    tmp_path, monkeypatch, run_cli, stage, reason
):
    role = SCAFFOLD_ROLE if stage == "scaffold" else "milestone-review"
    host = baseline_host(tmp_path, monkeypatch, stage=stage, roles=(role,))
    if stage == "implement":
        _with_done_task(host)
    make_baseline_unavailable(host, reason)
    code, synced = gate_command(run_cli, "sync")
    assert code == 0 and synced["ok"], synced

    orient = _orient(run_cli)
    conditions = orient["data"]["blocking_conditions"]
    assert "baseline-unavailable" in conditions, (
        f"FAIL AC-8: orient does not report baseline-unavailable for a {reason} "
        f"baseline, got {conditions}"
    )
    first = orient["next_actions"][0]
    assert first["action"]["kind"] == "manual"
    assert REPOINT_COMMAND in first["command"], first
    assert _names_reason(f"{first['command']} {first['reason']}", reason), first
    commands = [row["command"] for row in orient["next_actions"]]
    assert not any(
        command.startswith(("heddle run-gate", "heddle run-gates"))
        for command in commands
    ), f"FAIL AC-8: orient still routes a review: {commands}"

    rendered = plan_status(host.plan)
    assert rendered["conditions"] == ", ".join(conditions), (
        "FAIL AC-8: plan-status conditions disagree with orient: "
        f"{rendered['conditions']!r} != {conditions}"
    )
    assert rendered["next"] == first["command"], (
        f"FAIL AC-8: plan-status next disagrees with orient: {rendered['next']!r}"
    )


@pytest.mark.parametrize(
    ("stage", "role", "reason"),
    [
        ("scaffold", SCAFFOLD_ROLE, "diverged"),
        ("implement", "milestone-review", "missing"),
    ],
)
def test_ac8_scaffolding_and_milestone_gates_refuse_before_any_provider_call(
    tmp_path, monkeypatch, run_cli, stage, role, reason
):
    host = baseline_host(tmp_path, monkeypatch, stage=stage, roles=(role,))
    if stage == "implement":
        _with_done_task(host)
    make_baseline_unavailable(host, reason)
    calls = provider_transport(monkeypatch, review_content(role))

    code, result = gate_command(run_cli, "run-gate", role)

    assert len(calls) == 0, (
        f"FAIL AC-8: {role} launched a provider with a {reason} baseline"
    )
    assert code != 0 and not result["ok"], result
    error = result["error"]
    text = f"{error['message']} {error['hint']}"
    assert REPOINT_COMMAND in text, (
        f"FAIL AC-8: the {role} refusal does not name the re-point remedy: {text}"
    )
    assert _names_reason(text, reason), f"FAIL AC-8: refusal omits the reason: {text}"


def test_ac8_feature_without_a_recorded_baseline_is_not_blocked(
    tmp_path, monkeypatch, run_cli
):
    """Survivor pin: a v9 feature keeps running scaffolding review."""
    host = baseline_host(tmp_path, monkeypatch, record=False)
    assert "source_baseline" not in read(host.state)
    calls = provider_transport(monkeypatch, review_content(SCAFFOLD_ROLE))

    assert "baseline-unavailable" not in _orient(run_cli)["data"]["blocking_conditions"]
    code, result = gate_command(run_cli, "run-gate", SCAFFOLD_ROLE)

    assert code in (0, 4) and result["ok"], result
    assert len(calls) == 1


@pytest.mark.parametrize(
    "case", ["available", "not-ancestor", "stale-revision", "no-recorded-baseline"]
)
def test_ac8_baseline_set_refuses_without_writing(tmp_path, monkeypatch, run_cli, case):
    host = baseline_host(tmp_path, monkeypatch, record=case != "no-recorded-baseline")
    revision = _revision(host)
    commit = git(host.root, "rev-parse", "HEAD")
    if case in {"not-ancestor", "stale-revision"}:
        make_baseline_unavailable(host, "diverged")
        commit = (
            host.baseline
            if case == "not-ancestor"
            else git(host.root, "rev-parse", "HEAD")
        )
    if case == "stale-revision":
        # The fixture starts at revision 0; a future revision is equally stale.
        revision += 1
    before = host.state.read_bytes()

    code, result = baseline_set(
        run_cli,
        "--commit",
        commit,
        "--reason",
        "history was rewritten",
        "--expect-revision",
        str(revision),
    )

    assert not result["ok"], f"FAIL AC-8: the re-point accepted case {case}: {result}"
    assert host.state.read_bytes() == before, (
        f"FAIL AC-8: a refused re-point ({case}) wrote state"
    )
    if case == "stale-revision":
        assert code == 5 and result["error"]["code"] == "conflict", result
        return
    assert code not in (0, 2, 5), result
    # The message names the reason; the hint may name the remedy command.
    message = result["error"]["message"].lower()
    if case == "available":
        assert re.search(r"\bavailable\b", message), (
            f"FAIL AC-8: the refusal does not say the baseline is still available: "
            f"{message}"
        )
    elif case == "not-ancestor":
        assert "not an ancestor" in message, (
            f"FAIL AC-8: the refusal does not say the commit is not an ancestor: "
            f"{message}"
        )
    else:
        assert "baseline" in message and ABSENCE.search(message), (
            f"FAIL AC-8: the refusal does not name the missing recorded baseline: "
            f"{message}"
        )


def test_ac8_baseline_set_dry_run_writes_nothing(tmp_path, monkeypatch, run_cli):
    host = baseline_host(tmp_path, monkeypatch)
    make_baseline_unavailable(host, "diverged")
    before = host.state.read_bytes()

    code, result = baseline_set(
        run_cli,
        "--commit",
        git(host.root, "rev-parse", "HEAD"),
        "--reason",
        "reworded the baseline commit",
        "--expect-revision",
        str(_revision(host)),
        "--dry-run",
    )

    assert code == 0 and result["ok"], result
    assert host.state.read_bytes() == before, "FAIL AC-8: a dry-run re-point wrote"


def test_ac8_baseline_set_records_the_new_baseline_with_append_only_history(
    tmp_path, monkeypatch, run_cli
):
    host = baseline_host(tmp_path, monkeypatch)
    make_baseline_unavailable(host, "diverged")
    first_target = git(host.root, "rev-parse", "HEAD")

    _repoint(run_cli, host, first_target, "reworded the baseline commit")

    value = read(host.state)
    assert value["source_baseline"] == {"kind": "git-commit", "oid": first_target}
    history = value.get("source_baseline_history")
    assert isinstance(history, list) and len(history) == 1, (
        f"FAIL AC-8: the re-point kept no history entry: {history!r}"
    )
    [entry] = history
    assert set(entry) == {"from", "to", "reason", "at"}, entry
    assert (_oid(entry["from"]), _oid(entry["to"])) == (host.baseline, first_target)
    assert entry["reason"] == "reworded the baseline commit"
    assert isinstance(entry["at"], str) and entry["at"]
    assert "baseline-unavailable" not in _orient(run_cli)["data"]["blocking_conditions"]

    git(host.root, "commit", "--amend", "-qm", "feature baseline (reworded again)")
    second_target = git(host.root, "rev-parse", "HEAD")
    _repoint(run_cli, host, second_target, "reworded it again")

    history = read(host.state)["source_baseline_history"]
    assert history[0] == entry, "FAIL AC-8: source_baseline_history is not append-only"
    assert [(_oid(row["from"]), _oid(row["to"])) for row in history] == [
        (host.baseline, first_target),
        (first_target, second_target),
    ]


def test_ac8_gates_and_coverage_use_the_repointed_baseline(
    tmp_path, monkeypatch, run_cli
):
    host, parent = _repoint_host(tmp_path, monkeypatch)
    squashed = _squash_onto(host, parent)
    code, status = gate_command(run_cli, "status")
    assert "source-coverage-baseline-unavailable" in {
        row["code"] for row in status["diagnostics"]
    }

    _repoint(run_cli, host, squashed, "squashed the feature commits")
    (host.root / STABLE_TEST).write_text("def test_stable():\n    assert 'a' == 'b'\n")
    commit_all(host.root, "change a test after the new baseline")
    (host.root / UNRELATED).write_text("notes, revised\n")
    commit_all(host.root, "unrelated tip commit")

    code, status = gate_command(run_cli, "status")
    assert code == 0 and status["ok"], status
    coverage = {
        row["code"]: row["message"]
        for row in status["diagnostics"]
        if row["code"].startswith("source-coverage-")
    }
    assert "source-coverage-baseline-unavailable" not in coverage, (
        "FAIL AC-8: coverage still reads the replaced baseline"
    )
    # Unowned paths changed since the new baseline are unresolved; the added
    # test changed only before it.
    unresolved = coverage.get("source-coverage-unresolved", "")
    assert STABLE_TEST in unresolved and EXTRA_TEST not in unresolved, coverage
    # Against the parent or the replaced baseline the added test would join;
    # the HEAD cascade would miss the test changed before the tip commit.
    subject = frozenset({BINDING_TEST, STABLE_TEST})
    assert reviewer_subject(host.root) == subject, (
        "FAIL AC-8: the scaffolding reviewer subject is not relative to the new "
        f"baseline: {sorted(reviewer_subject(host.root))}"
    )
    assert assignment_basis(host.root) == expected_assignment_basis(
        host.root, subject
    ), "FAIL AC-8: the scaffolding assignment basis ignores the new baseline"
    calls = provider_transport(monkeypatch, review_content(SCAFFOLD_ROLE))
    code, result = gate_command(run_cli, "run-gate", SCAFFOLD_ROLE)
    assert code in (0, 4) and result["ok"], result
    assert len(calls) == 1


@pytest.mark.parametrize("target", ["same-subject", "membership-change"])
def test_ac8_existing_evidence_compares_against_the_recomputed_subject(
    tmp_path, monkeypatch, run_cli, target
):
    host, parent = _repoint_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content(SCAFFOLD_ROLE))
    code, result = gate_command(run_cli, "run-gate", SCAFFOLD_ROLE)
    assert code in (0, 4) and result["ok"], result
    origin = runs(host.state)[-1]["run_id"]
    accepted = dispose(
        host.state,
        [disposition(origin, "@coverage", status="settled", references=[BINDING_TEST])],
    )
    assert accepted.ok and accepted.data["closure"]["closed"], accepted.to_envelope()
    squashed = _squash_onto(host, parent)

    # The parent selects the same subject as the replaced baseline (the added
    # test differs from both); the squash no longer differs in that test.
    _repoint(
        run_cli,
        host,
        parent if target == "same-subject" else squashed,
        "squashed the feature commits",
    )

    row = review_status(host.state, role=SCAFFOLD_ROLE)
    assert len(calls) == 1
    if target == "same-subject":
        assert row["closed"] and row["evidence_state"] == "current", (
            f"FAIL AC-8: an unchanged subject did not stay current: {row}"
        )
        return
    assert row["evidence_state"] == "stale", row
    assert row["evidence_explanations"], row
    changes = set().union(
        *(
            typed_changes(explanation, ac="AC-8")
            for explanation in row["evidence_explanations"]
        )
    )
    assert changes == {("membership", EXTRA_TEST)}, (
        f"FAIL AC-8: the subject difference is not named as membership: {changes}"
    )


def test_ac8_baseline_set_is_on_no_agent_session_allowlist(tmp_path, monkeypatch):
    from heddle.contracts.schemas import STAGES
    from heddle.driver.sessions import allowed_tools_for_stage
    from heddle.gate.registry import GATES
    from heddle.gate.runners.claude import CLAUDE_PREAPPROVED_TOOLS
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    contract = baseline_set_contract()
    assert contract["mutating"] is True
    assert {flag["name"] for flag in contract["flags"]} >= {
        "--commit",
        "--reason",
        "--expect-revision",
        "--dry-run",
    }
    host = baseline_host(tmp_path, monkeypatch)
    config = load_project_config(host.root)
    snapshot = resolve_snapshot(config, read(host.state)["feature"])
    allowlists = {
        f"{stage} session": allowed_tools_for_stage(
            config, replace(snapshot, stage=stage)
        )
        for stage in STAGES
        if stage != "complete"
    }
    allowlists.update(
        {
            f"{name} reviewer": tuple(
                tool
                for tool in gate.tool_permissions
                if tool in CLAUDE_PREAPPROVED_TOOLS
            )
            for name, gate in GATES.items()
        }
    )
    command = f"{REPOINT_COMMAND} --commit {host.baseline} --reason moved"

    admitted = {
        owner: rule
        for owner, rules in allowlists.items()
        for rule in rules
        if _admits(rule, command)
    }

    assert admitted == {}, (
        f"FAIL AC-8: an agent allowlist admits the re-point: {admitted}"
    )


def _admits(rule: str, command: str) -> bool:
    if rule == "Bash":
        return True
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    pattern = rule[len("Bash(") : -1]
    if pattern.endswith(":*"):
        prefix = pattern[: -len(":*")]
        return command == prefix or command.startswith(prefix + " ")
    if pattern.endswith("*"):
        return command.startswith(pattern[:-1])
    return command == pattern
