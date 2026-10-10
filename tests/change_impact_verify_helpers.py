"""Hosts and accessors for verification refresh and witness acceptance tests.

Every host is authored in ``tmp_path`` with real Git, real ``heddle verify``
facts and real decisions; only the provider transport is doubled.

Activation seams (each asserts its presence at call time):

- ``ops.Verify.stale`` and ``heddle verify --stale`` (``stale_operation``,
  ``run_stale``);
- the typed ``changes`` field on a status explanation row, read only through
  ``tests.change_impact_explanation_helpers.typed_changes``;
- the optional manifest reference on a witness-waiver binding
  (``waiver_manifest_reference``).
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import fields
from pathlib import Path

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.operational_model_helpers import FEATURE, read, write
from tests.readiness_helpers import verify
from tests.tiering_completion_helpers import final_host

JOURNAL_ROUTE = f"plans/{FEATURE}.decision-journal.md"
# The four keys a witness-waiver binding carried before review equivalence.
LEGACY_WAIVER_KEYS = frozenset({"scope", "fact", "ownership", "source_sha256"})

# Two owned Python files, and the pinned ruff format of ``src.py``: requoted
# strings and a space after the comma; the syntax tree and comments are
# unchanged.
PYTHON_SOURCES = {"helper.py": b"LIMIT = 1\n", "src.py": b"values = ['a','b']\n"}
FORMATTED_SRC = b'values = ["a", "b"]\n'
# The upgrade fixture snapshot recorded by the build before review equivalence
# at robustness: m1 and acceptance hold v1 facts, smoke failed with a resolved
# degraded acceptance, and live is waived by a resolved four-key binding.
LEGACY_SNAPSHOT = "robustness-open"


def stale_operation(**kwargs) -> ops.Verify:
    """Build ``Verify(stale=True)`` once the flag exists."""
    assert "stale" in {field.name for field in fields(ops.Verify)}, (
        "FAIL AC-12: ops.Verify has no stale flag; `heddle verify --stale` is "
        "not implemented"
    )
    return ops.Verify(stale=True, feature=FEATURE, **kwargs)  # type: ignore[call-arg]


def is_stale_batch(action) -> bool:
    return (
        isinstance(action, ops.CommandAction)
        and isinstance(action.operation, ops.Verify)
        and getattr(action.operation, "stale", False) is True
    )


def verify_scope(action) -> str | None:
    if (
        isinstance(action, ops.CommandAction)
        and isinstance(action.operation, ops.Verify)
        and not getattr(action.operation, "stale", False)
    ):
        return action.operation.scope
    return None


def run_stale(run_cli, *arguments: str) -> tuple[int, dict]:
    """Run ``heddle verify --stale`` through the real CLI boundary."""
    code, out, _err = run_cli(
        ["verify", "--stale", "--feature", FEATURE, *arguments, "--json"]
    )
    envelope = json.loads(out)
    error = envelope.get("error") or {}
    assert not (
        code == 2
        and error.get("code") == "usage"
        and "--stale" in error.get("message", "")
    ), f"FAIL AC-12: `heddle verify --stale` is not a recognized flag: {error}"
    return code, envelope


def batch_rows(envelope: dict) -> dict:
    """Return the batch rows from data on success or error.details otherwise."""
    body = envelope["data"] if envelope["ok"] else envelope["error"]["details"]
    assert "verifications" in body and "skipped" in body, (
        "FAIL AC-12: the verify --stale result lists no verifications[] and "
        f"skipped[] rows: {body}"
    )
    return body


def scopes(items) -> list[str]:
    """Scope names from rows or bare scope strings, in listed order."""
    return [item if isinstance(item, str) else item["scope"] for item in items]


def skip_reasons(body: dict) -> dict[str, str]:
    reasons = {}
    for row in body["skipped"]:
        assert isinstance(row.get("reason"), str) and row["reason"].strip(), (
            f"FAIL AC-12: skipped scope {row.get('scope')} carries no reason: {row}"
        )
        reasons[row["scope"]] = row["reason"]
    return reasons


def status_rows(*, orient: bool = False, feature: str = FEATURE) -> dict[str, dict]:
    operation = ops.Orient(feature=feature) if orient else ops.Status(feature=feature)
    result = execute(operation)
    assert result.ok, result.to_envelope()
    return {row["scope"]: row for row in result.data["verification_status"]}


def waiver_manifest_reference(binding: dict) -> dict:
    """The optional manifest reference a post-feature waiver binds."""
    extra = set(binding) - LEGACY_WAIVER_KEYS
    assert len(extra) == 1, (
        "FAIL AC-14: the witness-waiver binding names no current manifest "
        f"beside {sorted(LEGACY_WAIVER_KEYS)}: {sorted(binding)}"
    )
    return binding[extra.pop()]


def propose_waiver(scope: str = "acceptance"):
    from heddle.contracts.decisions import DecisionInput

    return execute(
        ops.DecisionsAdd(
            decisions=(
                DecisionInput(
                    kind="witness-waiver",
                    escalation_class=5,
                    source="session",
                    title=f"Accept the earlier {scope} witness",
                    question=f"Rerun {scope} or accept the earlier witness?",
                    options=("rerun", "waive"),
                    routes_to=(JOURNAL_ROUTE,),
                    recommendation="waive",
                    witness_waiver={"scope": scope},
                ),
            ),
            feature=FEATURE,
        )
    )


def resolve_waiver(decision_id: str):
    return execute(
        ops.ResolveDecision(
            decision_id=decision_id,
            kind="accept-prior-witness",
            rationale="Owner accepted the earlier witness; only formatting changed.",
            routes_to=JOURNAL_ROUTE,
            feature=FEATURE,
        )
    )


def journal_waiver(root: Path, state: Path, decision_id: str) -> None:
    waiver = next(
        decision["witness_waiver"]
        for decision in read(state)["decisions"]
        if decision["id"] == decision_id
    )
    journal = root / JOURNAL_ROUTE
    journal.write_text(
        journal.read_text() + f"\n## {decision_id}\n\n- choice: accept-prior-witness\n"
        f"- scope: {waiver['scope']}\n- fact_sha256: {waiver['fact']}\n"
    )


def legacy_host(tmp_path, monkeypatch):
    """Rebuild ``LEGACY_SNAPSHOT`` exactly as the pre-change build left it."""
    from tests.change_impact_upgrade_helpers import rebuild

    host = rebuild(LEGACY_SNAPSHOT, tmp_path)
    monkeypatch.chdir(host.root)
    return host


def reflow_legacy_source(root: Path) -> None:
    """A review-equivalent edit of the recorded source: redundant parentheses."""
    source = root / "src/example.py"
    content = source.read_bytes()
    assert b"VALUE = 7" in content, content
    source.write_bytes(content.replace(b"VALUE = 7", b"VALUE = (7)"))


def accept_degraded_smoke(root: Path, state: Path) -> str:
    """Fail smoke on an outside-owned path and resolve its degraded acceptance.

    Needs fresh smoke, acceptance and m1 facts, as the disposition binds them.
    """
    from heddle.contracts.decisions import DecisionInput

    (root / "docs/other-feature.md").write_text("# Other feature\n")
    value = read(state)
    value["commands"]["smoke_test"] = (
        'python3 -c "import pathlib, sys; '
        "sys.exit(1 if pathlib.Path('docs/other-feature.md').exists() else 0)\""
    )
    write(state, value)
    execute(ops.Verify(feature=FEATURE, scope="smoke"))
    assert read(state)["verifications"][-1]["exit_code"] != 0
    added = execute(
        ops.DecisionsAdd(
            decisions=(
                DecisionInput(
                    kind="smoke-disposition",
                    escalation_class=2,
                    source="session",
                    title="Accept the outside-owned smoke failure",
                    question="Close with the known outside-owned smoke failure?",
                    options=("accept-degraded-smoke", "repair"),
                    routes_to=(JOURNAL_ROUTE,),
                    recommendation="accept-degraded-smoke",
                    smoke_disposition={
                        "stage": read(state)["stage"],
                        "attribution": "The other feature's note trips smoke.",
                        "baseline_evidence": "Smoke fails on that note alone.",
                        "outside_paths": ["docs/other-feature.md"],
                        "owned_scopes": ["m1"],
                    },
                ),
            ),
            feature=FEATURE,
        )
    )
    assert added.ok, added.to_envelope()
    decision_id = read(state)["decisions"][-1]["id"]
    resolved = execute(
        ops.ResolveDecision(
            decision_id=decision_id,
            kind="accept-degraded-smoke",
            rationale="Owner accepts the outside-owned smoke failure.",
            routes_to=JOURNAL_ROUTE,
            feature=FEATURE,
        )
    )
    assert resolved.ok, resolved.to_envelope()
    return decision_id


def implement_exit_host(tmp_path, monkeypatch, *, extra_milestones=()):
    """Every milestone done at implement, each scope and acceptance fresh.

    ``extra_milestones`` adds ``(source, command)`` milestones after m1 and m2;
    a declared command that fails records a failed fact.
    """
    host = final_host(tmp_path, monkeypatch, stage="implement", verify_now=False)
    value = read(host.state)
    for milestone in value["milestones"]:
        milestone["status"] = "done"
    for number, (source, command) in enumerate(extra_milestones, start=3):
        template = deepcopy(value["milestones"][-1])
        template.update(
            id=f"m{number}",
            title=f"Part {number}",
            depends_on=[f"m{number - 1}"],
            owns=[source],
            verification={"command": command, "expected": "pass"},
            status="done",
            tasks=[
                {
                    "id": f"m{number}-t1",
                    "text": "Implement the declared value",
                    "status": "done",
                }
            ],
        )
        value["milestones"].append(template)
        (host.root / source).write_text("VALUE = 7\n")
    write(host.state, value)
    plan = host.state.parent / "plan.md"
    plan.write_text(
        plan.read_text()
        + "".join(
            f"\n### Milestone m{number}: Part {number}\n\nSatisfies: AC-1\n"
            for number in range(3, 3 + len(extra_milestones))
        )
    )
    verify(host.state, "m1", "m2", "acceptance")
    return host


def milestone_command(source: str) -> str:
    return (
        f'python3 -c \'import runpy; assert runpy.run_path("{source}")["VALUE"] == 7\''
    )


def open_behavior_review(tmp_path, monkeypatch, run_cli):
    """A peer-review boundary whose behavior review has one open finding."""
    from tests.structured_review_helpers import finding
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import (
        gate_command,
        provider_transport,
        review_content,
        runs,
    )

    host = final_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={"behavior-review": entry("behavior-review")},
    )
    provider_transport(
        monkeypatch,
        review_content(
            "behavior-review", findings=[finding("BE-I1", classification="implement")]
        ),
    )
    code, result = gate_command(run_cli, "run-gate", "behavior-review")
    assert code in (0, 4), result
    return host, runs(host.state)[0]["run_id"]


def review_fix(root: Path, round_number: int) -> None:
    # A passing review-driven edit: owned content changes, behaviour holds.
    (root / "src/example.py").write_text(f"VALUE = 7  # review fix {round_number}\n")


def reflow_review_fix(root: Path, round_number: int) -> None:
    # The formatter-shaped form of review_fix: redundant parentheses only.
    (root / "src/example.py").write_text(f"VALUE = (7)  # review fix {round_number}\n")
