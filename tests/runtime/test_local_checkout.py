"""Local-checkout mode: Git ignores `.heddle.yaml`, and Heddle delivers the
rules that keep it out of the shared repository."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import heddle
from heddle.runtime.local_checkout import is_local_checkout
from tests.runtime.git_helpers import initialize_git_repo

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"
SLUG = "tier1-quickfix"
RULES_PATH = Path(heddle.__file__).parent / "resources" / "local-checkout.md"


@pytest.fixture(autouse=True)
def _only_fixture_ignore_rules(tmp_path, monkeypatch) -> None:
    """Only each fixture's own exclude file may decide the mode."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout


def _committed_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(
        lambda *args: _git(path, *args),
        email="fixture@example.invalid",
        name="Local checkout fixture",
    )
    return path


def _hide_config(checkout: Path) -> None:
    path = _git(checkout, "rev-parse", "--git-path", "info/exclude").strip()
    exclude = checkout / path
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as file:
        file.write("/.heddle.yaml\n")


def _golden_host(parent: Path, *, local: bool) -> Path:
    host = parent / "host"
    shutil.copytree(GOLDEN, host)
    _git(host, "init", "-q")
    if local:
        _hide_config(host)
    return host


@pytest.mark.parametrize(
    ("case", "expected"),
    [("ignored", True), ("unignored", False), ("tracked", False), ("no-git", False)],
)
def test_only_an_ignored_untracked_config_marks_a_local_checkout(
    tmp_path, case, expected
) -> None:
    host = tmp_path / "host"
    host.mkdir()
    (host / ".heddle.yaml").write_text("{}\n", encoding="utf-8")
    if case != "no-git":
        _git(host, "init", "-q")
    if case in ("ignored", "tracked"):
        _hide_config(host)
    if case == "tracked":
        _git(host, "add", "-f", ".heddle.yaml")
    assert is_local_checkout(host) is expected


def test_kickoff_appends_the_rules_only_in_a_local_checkout(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    briefings = {}
    for local in (False, True):
        monkeypatch.chdir(_golden_host(tmp_path / str(local), local=local))
        code, out, _err = run_cli(["kickoff", "--json", "--feature", SLUG])
        assert code == 0, out
        briefings[local] = envelope_tools.parse(out)["data"]["briefing"]
    rules = RULES_PATH.read_text(encoding="utf-8")
    assert briefings[True] == briefings[False] + "\n\n---\n\n" + rules


@pytest.mark.parametrize("feature", [SLUG, "no-such-feature"])
def test_orient_names_the_rules_only_in_a_local_checkout(
    run_cli, envelope_tools, tmp_path, monkeypatch, feature
) -> None:
    for local in (False, True):
        monkeypatch.chdir(_golden_host(tmp_path / str(local), local=local))
        _code, out, _err = run_cli(["orient", "--json", "--feature", feature])
        notes = [
            row
            for row in envelope_tools.parse(out)["diagnostics"]
            if row["code"] == "local-checkout"
        ]
        if local:
            assert len(notes) == 1 and notes[0]["severity"] == "info", notes
            assert str(RULES_PATH) in notes[0]["message"]
        else:
            assert notes == []


def test_a_git_failure_fails_kickoff_and_warns_in_orient(
    run_cli, envelope_tools, tmp_path, monkeypatch
) -> None:
    host = _golden_host(tmp_path, local=True)
    (host / ".git" / "index").write_bytes(b"not an index")
    monkeypatch.chdir(host)
    code, out, _err = run_cli(["kickoff", "--json", "--feature", SLUG])
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
    assert "keeps Heddle local" in envelope["error"]["message"]
    _code, out, _err = run_cli(["orient", "--json", "--feature", SLUG])
    notes = [
        row
        for row in envelope_tools.parse(out)["diagnostics"]
        if row["code"] == "local-checkout"
    ]
    assert [row["severity"] for row in notes] == ["advisory"], notes


@pytest.mark.parametrize("command", ["doctor", "orient"])
@pytest.mark.parametrize(
    ("local", "checkout", "expected"),
    [
        pytest.param(True, "worktree", "manual", id="local-worktree"),
        pytest.param(
            True, "main", "heddle init --no-session-entry --dry-run", id="local-main"
        ),
        pytest.param(
            True,
            "submodule",
            "heddle init --no-session-entry --dry-run",
            id="local-submodule",
        ),
        pytest.param(False, "worktree", "heddle init --dry-run", id="shared-worktree"),
    ],
)
def test_a_checkout_without_heddle_files_gets_its_clones_first_move(
    run_cli, envelope_tools, tmp_path, monkeypatch, command, local, checkout, expected
) -> None:
    host = _committed_repo(tmp_path / "host")
    target = host
    if checkout == "worktree":
        target = tmp_path / "worktree"
        _git(host, "worktree", "add", "-q", str(target))
    elif checkout == "submodule":
        library = _committed_repo(tmp_path / "library")
        _git(
            host,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(library),
            "lib",
        )
        target = host / "lib"
    if local:
        _hide_config(target)
    monkeypatch.chdir(target)
    code, out, _err = run_cli([command, "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
    assert envelope["error"]["details"] == {"git_root": str(target.resolve())}
    [action] = envelope["next_actions"]
    if expected == "manual":
        assert action["action"]["kind"] == "manual"
        assert ".heddle.lock" in action["action"]["instruction"]
    else:
        assert action["command"] == expected
