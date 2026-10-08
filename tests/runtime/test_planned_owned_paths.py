from __future__ import annotations

from pathlib import Path

import pytest

from heddle.contracts import operations as ops
from heddle.contracts.result import Severity
from heddle.io.git import GitError
from heddle.kernel.project_config import KernelError
from heddle.runtime.validate import validate
from tests.runtime.validation_helpers import (
    clean_current_host,
    read_yaml,
    state_path,
    write_yaml,
)

TINY = Path(__file__).resolve().parents[1] / "fixtures/workspaces/tiny"
PLANNED = "src/new_component/new_module.py"


def _host(tmp_path: Path, stage: str, status: str) -> Path:
    host = clean_current_host(tmp_path, TINY)
    path = state_path(host)
    state = read_yaml(path)
    state["stage"] = state["authorized_through"] = stage
    for milestone in state["milestones"]:
        milestone["status"] = "todo"
        milestone.pop("tasks", None)
    state["milestones"][0]["status"] = status
    state["milestones"][0]["owns"].append(PLANNED)
    write_yaml(path, state)
    return host


@pytest.mark.parametrize(
    ("stage", "status"),
    [
        ("specify", "todo"),
        ("scaffold", "todo"),
        ("implement", "current"),
        ("implement", "todo"),
    ],
)
def test_planned_ownership_is_visible_without_creating_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, status: str
) -> None:
    host = _host(tmp_path, stage, status)
    monkeypatch.chdir(host)
    before = state_path(host).read_bytes()

    result = validate(ops.Validate("sample-feature"))

    assert result.ok
    assert result.exit_code == 0
    assert [(d.code, d.severity) for d in result.diagnostics] == [
        ("owned-path-planned", Severity.INFO)
    ]
    assert PLANNED in result.diagnostics[0].message
    assert state_path(host).read_bytes() == before
    assert not (host / PLANNED).exists()


@pytest.mark.parametrize(
    ("stage", "status"),
    [("implement", "done"), ("peer-review", "todo"), ("complete", "done")],
)
def test_missing_delivery_remains_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, status: str
) -> None:
    host = _host(tmp_path, stage, status)
    monkeypatch.chdir(host)

    result = validate(ops.Validate("sample-feature"))

    assert not result.ok
    assert any(
        d.code == "owned-path-missing" and d.severity == Severity.FATAL
        for d in result.diagnostics
    )


def test_creating_planned_path_clears_the_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path, "implement", "current")
    target = host / PLANNED
    target.parent.mkdir(parents=True)
    target.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.chdir(host)

    result = validate(ops.Validate("sample-feature"))

    assert result.ok
    assert not result.diagnostics


def test_planned_ownership_does_not_excuse_missing_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path, "specify", "todo")
    (host / "docs/features/example/sample-feature.md").unlink()
    monkeypatch.chdir(host)

    result = validate(ops.Validate("sample-feature"))

    assert not result.ok
    assert any(d.code == "spec-missing" for d in result.diagnostics)


def test_planned_ownership_does_not_hide_git_faults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path, "specify", "todo")
    monkeypatch.chdir(host)

    def fail_history(root: Path, path: str) -> bool:
        raise GitError("invalid history output")

    monkeypatch.setattr("heddle.runtime.validate.path_has_git_history", fail_history)

    with pytest.raises(KernelError, match="invalid history output") as caught:
        validate(ops.Validate("sample-feature"))
    assert caught.value.code == "workspace-invalid"


@pytest.mark.parametrize("outside", [True, False])
def test_planned_ownership_does_not_excuse_unsafe_or_broken_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outside: bool
) -> None:
    host = _host(tmp_path, "specify", "todo")
    link = host / PLANNED
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "outside.py" if outside else host / "missing.py")
    monkeypatch.chdir(host)

    result = validate(ops.Validate("sample-feature"))

    assert not result.ok
    assert any(d.severity == Severity.FATAL for d in result.diagnostics)


SCAFFOLD_PLANNED = "tests/test_planned_scaffold.py"
SCAFFOLD_COMMANDED = "tests/test_commanded_scaffold.py"


def _declare_scaffold_tests(host: Path, *, enabled: bool, span: str) -> None:
    path = state_path(host)
    state = read_yaml(path)
    for row in state["feature_policy"]["entries"]:
        if enabled and row["role"] == "review-test-scaffolding":
            row.update(mode="upper-limit", limit=2, minimum_rounds=1)
    state["milestones"][1]["owns"].append(SCAFFOLD_PLANNED)
    state["commands"]["acceptance_test"] = f"pytest {SCAFFOLD_COMMANDED} -q"
    write_yaml(path, state)
    plan = host / "plans/sample-feature/plan.md"
    plan.write_text(
        plan.read_text(encoding="utf-8") + f"\nLater tests: `{span}`.\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("stage", "status", "enabled", "advised"),
    [
        ("specify", "todo", True, True),
        ("scaffold", "todo", True, True),
        ("scaffold", "todo", False, False),
        ("implement", "current", True, False),
    ],
)
def test_paths_the_scaffold_gate_refuses_are_advised_through_scaffold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    status: str,
    enabled: bool,
    advised: bool,
) -> None:
    host = _host(tmp_path, stage, status)
    _declare_scaffold_tests(host, enabled=enabled, span=SCAFFOLD_PLANNED)
    monkeypatch.chdir(host)

    result = validate(ops.Validate("sample-feature"))

    assert result.ok
    planned = [d for d in result.diagnostics if d.code == "owned-path-planned"]
    assert [d.severity for d in planned] == [Severity.INFO, Severity.INFO]
    refused = [d for d in result.diagnostics if d.code == "scaffold-test-path-missing"]
    if not advised:
        assert result.exit_code == 0 and refused == []
        return
    assert result.exit_code == 4
    assert [d.severity for d in refused] == [Severity.ADVISORY, Severity.ADVISORY]
    for diagnostic, path in zip(
        refused, (SCAFFOLD_COMMANDED, SCAFFOLD_PLANNED), strict=True
    ):
        assert "review-test-scaffolding will refuse to start" in diagnostic.message
        assert f"declared test path {path} is missing" in diagnostic.message


def test_declarations_the_scaffold_gate_rejects_are_advised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path, "scaffold", "todo")
    _declare_scaffold_tests(host, enabled=True, span="tests/test_*.py")
    monkeypatch.chdir(host)

    result = validate(ops.Validate("sample-feature"))

    refused = [d for d in result.diagnostics if d.code == "scaffold-test-path-missing"]
    assert result.ok and result.exit_code == 4
    assert len(refused) == 1
    assert "unsupported plan test declaration 'tests/test_*.py'" in (refused[0].message)
