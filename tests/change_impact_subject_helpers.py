"""Synthetic baseline hosts for the reviewed-test subject and the baseline re-point.

Real Git, adoption, gate preparation and application commands run in disposable
hosts; only provider transports are doubled (``provider_transport`` from
``tests.tiering_review_helpers``). No recorded project evidence is read.

Activation seam: ``baseline_set`` requires the ``feature baseline set`` command
in the public contract manifest before it runs the CLI, so a test reaching it
fails with a named cause until the command exists.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from heddle.kernel.managed_regions import PLAN_STATUS_ID, begin_marker, end_marker
from tests.operational_model_helpers import read, write
from tests.tiering_helpers import ROLES, entry
from tests.tiering_review_helpers import V7_FEATURE, current_host, gate_command

SCAFFOLD_ROLE = "review-test-scaffolding"
BASELINE_COMMAND = "feature baseline set"
BINDING_TEST = "tests/check.py"
CHANGED_TEST = "tests/test_changed.py"
STABLE_TEST = "tests/test_stable.py"
DOOMED_TEST = "tests/test_doomed.py"
BASELINE_TESTS = {
    CHANGED_TEST: "def test_changed():\n    assert 1 == 1\n",
    STABLE_TEST: "def test_stable():\n    assert 'a' == 'a'\n",
    DOOMED_TEST: "def test_doomed():\n    assert 2 == 2\n",
}
UNRELATED = "docs/notes.md"


@dataclass(frozen=True)
class BaselineHost:
    root: Path
    state: Path
    baseline: str

    @property
    def plan(self) -> Path:
        return self.state.parent / "plan.md"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def policy_for(*active_roles: str) -> dict:
    selected = {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }
    selected.update({role: entry(role) for role in active_roles})
    return selected


def record_baseline(state: Path, oid: str) -> None:
    """Author the admission fact a v10 workspace carries before any write."""
    value = read(state)
    value["schema"] = "heddle.state/v10"
    value["source_baseline"] = {"kind": "git-commit", "oid": oid}
    write(state, value)


def baseline_host(
    tmp_path: Path,
    monkeypatch,
    *,
    stage: str = "scaffold",
    roles: tuple[str, ...] = (SCAFFOLD_ROLE,),
    tests: dict[str, str] | None = None,
    record: bool = True,
) -> BaselineHost:
    """A host whose baseline commit holds the binding test and three others.

    ``tests/check.py`` is reached only through the spec's ``Verified-by:``
    binding. The other tests are undeclared. The plan carries plan-status
    markers so ``heddle sync`` renders into it.
    """
    root, state = current_host(
        tmp_path, monkeypatch, stage=stage, overrides=policy_for(*roles)
    )
    plan = state.parent / "plan.md"
    plan.write_text(
        plan.read_text()
        + f"\n{begin_marker(PLAN_STATUS_ID)}\n{end_marker(PLAN_STATUS_ID)}\n",
        encoding="utf-8",
    )
    for relative, text in (BASELINE_TESTS if tests is None else tests).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (root / UNRELATED).parent.mkdir(parents=True, exist_ok=True)
    (root / UNRELATED).write_text("notes\n", encoding="utf-8")
    oid = commit_all(root, "feature baseline")
    if record:
        record_baseline(state, oid)
    return BaselineHost(root, state, oid)


def make_baseline_unavailable(host: BaselineHost, reason: str) -> None:
    """Record a missing object, or rewrite history so the baseline diverges."""
    if reason == "missing":
        value = read(host.state)
        value["source_baseline"] = {"kind": "git-commit", "oid": "f" * 40}
        write(host.state, value)
        return
    assert reason == "diverged", reason
    assert git(host.root, "rev-parse", "HEAD") == host.baseline
    git(host.root, "commit", "--amend", "-qm", "feature baseline (reworded)")
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", host.baseline, "HEAD"],
            cwd=host.root,
            capture_output=True,
        ).returncode
        == 1
    )


def _resolved(root: Path):
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    config = load_project_config(root)
    return config, resolve_snapshot(config, V7_FEATURE)


def _scaffold_invocation():
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.types import GateInvocationOverrides

    return resolve_gate_execution(
        GATES[SCAFFOLD_ROLE],
        GateInvocationOverrides(
            cli="codex", model="gpt-6-astra", reasoning_effort="high"
        ),
    )


def reviewer_basis(root: Path) -> str:
    """The reviewer basis digest a `review` qualification recomputes."""
    from heddle.gate import entry as gate_entry
    from heddle.gate.registry import GATES

    config, snapshot = _resolved(root)
    context = gate_entry.build_gate_context(
        snapshot, config, GATES[SCAFFOLD_ROLE], cli="codex", basis_only=True
    )
    return gate_entry.prepare_current_review_basis(
        context, _scaffold_invocation()
    ).review_basis_hash


def reviewer_subject(
    root: Path, test_roots: tuple[str, ...] = ("tests", "checks")
) -> frozenset[str]:
    """Every test path the prepared scaffolding review names in its inputs.

    A present test is named by its content and identity records; a deleted
    one must still be named by a record, whatever its label.
    """
    from heddle.gate import entry as gate_entry
    from heddle.gate.registry import GATES

    config, snapshot = _resolved(root)
    gate_type = GATES[SCAFFOLD_ROLE]
    context = gate_entry.build_gate_context(snapshot, config, gate_type, cli="codex")
    prepared = gate_entry.prepare_gate_run(
        context, gate_type=gate_type, invocation=_scaffold_invocation()
    )
    return frozenset(
        f"{test_root}/{item.name.split(f'/{test_root}/', 1)[1]}"
        for item in prepared.reviewed_inputs
        for test_root in test_roots
        if f"/{test_root}/" in item.name
    )


def assignment_basis(root: Path) -> str:
    """The scaffolding assignment basis dispositions are recorded against."""
    from heddle.kernel.review_assignments import assignment_for
    from heddle.runtime.review_assignments import current_basis

    config, snapshot = _resolved(root)
    assignment = assignment_for(snapshot.state, SCAFFOLD_ROLE, "feature")
    return current_basis(config, snapshot, assignment)


def expected_assignment_basis(root: Path, subject: set[str] | frozenset[str]) -> str:
    """Independent oracle: the feature documents plus exactly ``subject``.

    A path absent from the working tree is observed as missing, which is how a
    deleted test must enter the basis.
    """
    from heddle.io.source import observe_source, resolve_source_definition

    _config, snapshot = _resolved(root)
    paths = {snapshot.spec_path, snapshot.plan_path, *subject}
    definition = resolve_source_definition(root, "feature-inputs", tuple(paths))
    return observe_source(root, definition, plan_path=snapshot.plan_path).source_sha256


def baseline_set_contract() -> dict:
    from heddle.runtime.contracts import build_manifest

    commands = {row["name"]: row for row in build_manifest()["commands"]}
    assert BASELINE_COMMAND in commands, (
        f"FAIL AC-8: `heddle {BASELINE_COMMAND}` is not a registered command"
    )
    return commands[BASELINE_COMMAND]


def baseline_set(run_cli, *args: str) -> tuple[int, dict]:
    baseline_set_contract()
    return gate_command(run_cli, *BASELINE_COMMAND.split(), *args)


def plan_status(plan: Path) -> dict[str, str]:
    """The rendered plan-status fields as ``{name: value}``."""
    text = plan.read_text(encoding="utf-8")
    body = text.split(begin_marker(PLAN_STATUS_ID), 1)[1].split(
        end_marker(PLAN_STATUS_ID), 1
    )[0]
    fields = {}
    for line in body.splitlines():
        if line.startswith("- ") and ": " in line:
            name, value = line[2:].split(": ", 1)
            fields[name] = value
    return fields
