"""
Packaged workflow-hook library tests (adoption host-tooling increment 1).

Covers heddle/resources/hooks/: manifest conformance, the stdlib-only
contract, the `{{name|default}}` placeholder mechanic (unrendered files
must run with defaults — the heddle repo dogfoods them that way; an adopting
host replaces each token with its own value), and per-hook pass/reject
behavior against throwaway git repos. Nothing here touches the real
repository's index or worktree.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_ROOT = REPO_ROOT / "heddle" / "resources" / "hooks"
MANIFEST = HOOKS_ROOT / "manifest.yaml"

PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\|([^}]*)\}\}")


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _hook_path(hook_id: str) -> Path:
    return HOOKS_ROOT / f"{hook_id}.py"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def _commit_all(path: Path, message: str = "baseline") -> None:
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _run_hook(
    hook: Path,
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ALLOW_") and key != "BYPASS_JUSTIFICATION"
    }
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(hook), *args],
        cwd=cwd,
        env=merged,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _render(hook_id: str, target_dir: Path, values: dict[str, str]) -> Path:
    """Simulate a host's adoption: replace whole placeholder tokens."""
    text = _hook_path(hook_id).read_text(encoding="utf-8")

    def _substitute(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))

    rendered = target_dir / f"{hook_id}.py"
    rendered.write_text(PLACEHOLDER.sub(_substitute, text), encoding="utf-8")
    return rendered


# ---------------------------------------------------------------------------
# Manifest conformance + packaging contract
# ---------------------------------------------------------------------------


def test_manifest_and_files_agree() -> None:
    manifest = _manifest()
    hook_ids = [hook["id"] for hook in manifest["hooks"]]
    assert len(hook_ids) == len(set(hook_ids)), "duplicate hook ids in manifest"

    for hook in manifest["hooks"]:
        assert hook["stage"] in {"pre-commit", "commit-msg"}, hook["id"]
        assert _hook_path(hook["id"]).is_file(), f"missing file for {hook['id']}"
        snippet = hook["snippet"]
        assert f".heddle/hooks/{hook['id']}.py" in snippet["entry"], hook["id"]
        for param in hook["params"]:
            assert set(param) == {"name", "source", "default"}, hook["id"]

    utility_ids = {item["id"] for item in manifest["utilities"]}
    assert utility_ids == {"git-commit-retry.sh"}
    assert (HOOKS_ROOT / "git-commit-retry.sh").is_file()

    on_disk = {path.name for path in HOOKS_ROOT.iterdir() if path.is_file()}
    declared = {f"{hook_id}.py" for hook_id in hook_ids} | utility_ids
    assert on_disk == declared | {"manifest.yaml"}, (
        "every packaged file must be declared in the manifest and vice versa"
    )


def test_manifest_params_match_file_placeholders() -> None:
    for hook in _manifest()["hooks"]:
        text = _hook_path(hook["id"]).read_text(encoding="utf-8")
        placeholders = {(name, default) for name, default in PLACEHOLDER.findall(text)}
        declared = {(param["name"], param["default"]) for param in hook["params"]}
        assert placeholders == declared, (
            f"{hook['id']}: manifest params {declared} != file placeholders "
            f"{placeholders}"
        )


def test_hooks_are_stdlib_only() -> None:
    allowed = set(sys.stdlib_module_names)
    for path in sorted(HOOKS_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.partition(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{path.name}: relative import"
                assert node.module is not None
                roots = [node.module.partition(".")[0]]
            for root in roots:
                assert root in allowed, (
                    f"{path.name}: non-stdlib import {root!r} — packaged hooks "
                    "must run under the host's python3 with no dependencies"
                )


def test_git_commit_retry_ships_verbatim() -> None:
    packaged = (HOOKS_ROOT / "git-commit-retry.sh").read_bytes()
    repo_copy = (REPO_ROOT / "scripts" / "git-commit-retry.sh").read_bytes()
    assert packaged == repo_copy, (
        "git-commit-retry.sh must stay byte-identical to the repo utility "
        "(owner ruling 3: shipped verbatim, single source)"
    )


# ---------------------------------------------------------------------------
# check-no-committed-gate-locks (union semantics, ruling 2)
# ---------------------------------------------------------------------------


@pytest.mark.toolchain
def test_gate_locks_staged_scan_rejects(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    lock = tmp_path / "plans" / ".gate-locks" / "spec-review.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    subprocess.run(["git", "add", "-f", str(lock)], cwd=tmp_path, check=True)

    result = _run_hook(_hook_path("check-no-committed-gate-locks"), cwd=tmp_path)

    assert result.returncode == 1
    assert "Gate lock files" in result.stdout
    assert "spec-review.lock" in result.stdout


@pytest.mark.toolchain
def test_gate_locks_tracked_scan_rejects_already_committed(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    lock = tmp_path / "plans" / ".gate-locks" / "old.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    _commit_all(tmp_path)

    # Nothing staged: only the tracked-file half of the union catches this.
    result = _run_hook(_hook_path("check-no-committed-gate-locks"), cwd=tmp_path)

    assert result.returncode == 1
    assert "old.lock" in result.stdout


@pytest.mark.toolchain
def test_gate_locks_clean_repo_and_bypass_pass(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("clean\n", encoding="utf-8")
    _commit_all(tmp_path)
    hook = _hook_path("check-no-committed-gate-locks")

    assert _run_hook(hook, cwd=tmp_path).returncode == 0

    lock = tmp_path / "plans" / ".gate-locks" / "spec-review.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    subprocess.run(["git", "add", "-f", str(lock)], cwd=tmp_path, check=True)
    bypassed = _run_hook(hook, cwd=tmp_path, env={"ALLOW_COMMITTED_GATE_LOCKS": "1"})
    assert bypassed.returncode == 0


@pytest.mark.toolchain
def test_gate_locks_ignores_tracked_non_gate_lockfiles(tmp_path: Path) -> None:
    # Review finding (2026-07-22): the tracked scan is scoped to the locks
    # dir — a host's legitimate tracked lockfile (poetry.lock) must never
    # trip the guard, even when a plans/ file is part of the commit.
    _init_git_repo(tmp_path)
    (tmp_path / "poetry.lock").write_text("[[package]]\n", encoding="utf-8")
    plan = tmp_path / "plans" / "notes.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("plan\n", encoding="utf-8")
    _commit_all(tmp_path)
    plan.write_text("plan v2\n", encoding="utf-8")
    subprocess.run(["git", "add", str(plan)], cwd=tmp_path, check=True)

    result = _run_hook(_hook_path("check-no-committed-gate-locks"), cwd=tmp_path)

    assert result.returncode == 0, result.stdout


@pytest.mark.toolchain
def test_gate_locks_rendered_plans_root(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    rendered = _render(
        "check-no-committed-gate-locks", tmp_path, {"layout_plans": "workplans"}
    )
    lock = tmp_path / "workplans" / ".gate-locks" / "gate.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    subprocess.run(["git", "add", "-f", str(lock)], cwd=tmp_path, check=True)

    result = _run_hook(rendered, cwd=tmp_path)

    assert result.returncode == 1
    assert "workplans" in result.stdout


# ---------------------------------------------------------------------------
# check-bypass-justification
# ---------------------------------------------------------------------------


@pytest.mark.toolchain
def test_bypass_justification_rejects_and_accepts(tmp_path: Path) -> None:
    hook = _hook_path("check-bypass-justification")
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("sample\n", encoding="utf-8")

    fail = _run_hook(hook, str(message), cwd=tmp_path, env={"ALLOW_TEST_DELETION": "1"})
    assert fail.returncode == 1
    assert "Bypass-Justification" in fail.stdout

    message.write_text(
        "sample\n\nBypass-Justification: exercising the packaged hook\n",
        encoding="utf-8",
    )
    with_trailer = _run_hook(
        hook, str(message), cwd=tmp_path, env={"ALLOW_TEST_DELETION": "1"}
    )
    assert with_trailer.returncode == 0

    no_bypass = _run_hook(hook, str(message), cwd=tmp_path)
    assert no_bypass.returncode == 0

    env_justified = _run_hook(
        hook,
        cwd=tmp_path,
        env={
            "ALLOW_TEST_DELETION": "1",
            "BYPASS_JUSTIFICATION": "temporary fixture",
        },
    )
    assert env_justified.returncode == 0


# ---------------------------------------------------------------------------
# check-test-deletions
# ---------------------------------------------------------------------------


def _repo_with_test_file(tmp_path: Path, tests_root: str) -> Path:
    _init_git_repo(tmp_path)
    test_file = tmp_path / tests_root / "test_sample.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "def test_will_be_removed():\n    assert True\n", encoding="utf-8"
    )
    _commit_all(tmp_path)
    return test_file


@pytest.mark.toolchain
def test_test_deletions_rejects_removed_function(tmp_path: Path) -> None:
    test_file = _repo_with_test_file(tmp_path, "tests")
    test_file.write_text("# removed\n", encoding="utf-8")
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True)

    result = _deletions(tmp_path)

    assert result.returncode == 1
    assert "Test deletions detected" in result.stdout


@pytest.mark.toolchain
def test_test_deletions_rejects_deleted_file_and_bypass_passes(
    tmp_path: Path,
) -> None:
    test_file = _repo_with_test_file(tmp_path, "tests")
    subprocess.run(["git", "rm", "-q", str(test_file)], cwd=tmp_path, check=True)
    hook = _hook_path("check-test-deletions")

    result = _run_hook(hook, _message(tmp_path), cwd=tmp_path)
    assert result.returncode == 1
    assert "Deleted test files" in result.stdout

    bypassed = _run_hook(
        hook, _message(tmp_path), cwd=tmp_path, env={"ALLOW_TEST_DELETION": "1"}
    )
    assert bypassed.returncode == 0


@pytest.mark.toolchain
def test_test_deletions_rendered_tests_root(tmp_path: Path) -> None:
    test_file = _repo_with_test_file(tmp_path, "src/tests")
    rendered = _render("check-test-deletions", tmp_path, {"layout_tests": "src/tests"})
    test_file.write_text("# removed\n", encoding="utf-8")
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True)

    # The unrendered default (tests/) does not see src/tests; the rendered
    # hook does — the placeholder is the adoption seam.
    assert _deletions(tmp_path).returncode == 0
    assert _run_hook(rendered, _message(tmp_path), cwd=tmp_path).returncode == 1


def _load_hook(hook_id: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        hook_id.replace("-", "_"), _hook_path(hook_id)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _message(root: Path, text: str = "change\n") -> str:
    path = root / "COMMIT_EDITMSG"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _stage(root: Path, files: dict[str, str | None]) -> None:
    """Write (or delete, for None) each file, then stage every change."""
    for rel, text in files.items():
        path = root / rel
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def _deletions(
    root: Path, message: str = "change\n"
) -> subprocess.CompletedProcess[str]:
    return _run_hook(
        _hook_path("check-test-deletions"), _message(root, message), cwd=root
    )


TWO_TESTS = "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n"
CLASS_TEST = "class TestOld:\n    def test_m(self):\n        assert True\n"


def test_test_deletion_ids_follow_lexical_class_nesting() -> None:
    hook = _load_hook("check-test-deletions")
    source = (
        b"import sys\n\n"
        b"def test_top():\n    def test_inner():\n        pass\n\n"
        b"async def test_async():\n    pass\n\n"
        b"def helper():\n    pass\n\n"
        b"class TestOuter:\n    def test_method(self):\n        pass\n\n"
        b"    class TestNested:\n        def test_deep(self):\n            pass\n\n"
        b"class Helpers:\n    def test_on_plain_class(self):\n        pass\n\n"
        b"if sys.platform:\n    def test_conditional():\n        pass\n"
    )

    assert hook._test_ids("tests/test_a.py", source) == {
        "tests/test_a.py::test_top",
        "tests/test_a.py::test_async",
        "tests/test_a.py::TestOuter",
        "tests/test_a.py::TestOuter::test_method",
        "tests/test_a.py::TestOuter::TestNested",
        "tests/test_a.py::TestOuter::TestNested::test_deep",
        "tests/test_a.py::Helpers::test_on_plain_class",
        "tests/test_a.py::test_conditional",
    }
    assert hook._test_ids("tests/test_a.py", b"def test_broken(:\n") is None


_A = "tests/test_a.py"
_B = "tests/test_b.py"
_C = "tests/test_c.py"


@pytest.mark.parametrize(
    ("removed", "added", "renames", "unaccounted", "unused"),
    [
        pytest.param(
            {f"{_A}::test_old"},
            {f"{_A}::test_new"},
            [],
            {f"{_A}::test_old"},
            [],
            id="rename-without-trailer",
        ),
        pytest.param(
            {f"{_A}::test_old"},
            {f"{_A}::test_new"},
            [(f"{_A}::test_old", f"{_A}::test_new")],
            set(),
            [],
            id="trailed-rename",
        ),
        pytest.param(
            {f"{_A}::test_gone"},
            set(),
            [(f"{_A}::test_old", f"{_A}::test_new")],
            {f"{_A}::test_gone"},
            [(f"{_A}::test_old", f"{_A}::test_new")],
            id="inherited-trailer-exempts-nothing",
        ),
        pytest.param(
            {f"{_A}::test_old"},
            set(),
            [(f"{_A}::test_old", f"{_A}::test_kept")],
            {f"{_A}::test_old"},
            [(f"{_A}::test_old", f"{_A}::test_kept")],
            id="new-id-already-in-head",
        ),
        pytest.param(
            {f"{_A}::test_one", f"{_A}::test_two"},
            {f"{_A}::test_new"},
            [
                (f"{_A}::test_one", f"{_A}::test_new"),
                (f"{_A}::test_two", f"{_A}::test_new"),
            ],
            {f"{_A}::test_one", f"{_A}::test_two"},
            [
                (f"{_A}::test_one", f"{_A}::test_new"),
                (f"{_A}::test_two", f"{_A}::test_new"),
            ],
            id="new-id-named-twice",
        ),
        pytest.param(
            {f"{_A}::TestOld", f"{_A}::TestOld::test_kept", f"{_A}::TestOld::test_was"},
            {f"{_A}::TestNew", f"{_A}::TestNew::test_kept", f"{_A}::TestNew::test_is"},
            [(f"{_A}::TestOld", f"{_A}::TestNew")],
            {f"{_A}::TestOld::test_was"},
            [],
            id="class-rename-covers-kept-members",
        ),
        pytest.param(
            {f"{_A}::test_moved"}, {f"{_B}::test_moved"}, [], set(), [], id="move"
        ),
        pytest.param(
            {f"{_A}::test_same", f"{_B}::test_same"},
            {f"{_C}::test_same"},
            [],
            {f"{_B}::test_same"},
            [],
            id="one-move-per-added-test",
        ),
        pytest.param(
            {f"{_A}::TestX::test_m"},
            {f"{_B}::TestY::test_m"},
            [],
            {f"{_A}::TestX::test_m"},
            [],
            id="move-keeps-its-class",
        ),
    ],
)
def test_test_deletion_accounting_pairs_renames_and_moves(
    removed: set[str],
    added: set[str],
    renames: list[tuple[str, str]],
    unaccounted: set[str],
    unused: list[tuple[str, str]],
) -> None:
    left, _unpaired, unused_entries = _load_hook("check-test-deletions")._account(
        removed, added, renames
    )

    assert (left, unused_entries) == (unaccounted, unused)


@pytest.mark.toolchain
def test_test_deletions_census_passes_kept_moved_and_renamed_tests(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _stage(
        tmp_path,
        {
            "tests/test_a.py": TWO_TESTS,
            "tests/test_c.py": CLASS_TEST,
        },
    )
    _commit_all(tmp_path)
    _stage(
        tmp_path,
        {
            # A signature change and a re-wrapped definition keep both names.
            "tests/test_a.py": "def test_a(tmp_path):\n    assert True\n\n\n"
            "def test_b(\n    tmp_path,\n):\n    assert True\n",
            "tests/test_c.py": CLASS_TEST.replace("TestOld", "TestNew"),
        },
    )
    assert _deletions(tmp_path).returncode == 1  # the class rename is untrailed

    trailer = (
        "change\n\nTest-Renamed: tests/test_c.py::TestOld -> tests/test_c.py::TestNew\n"
    )
    assert _deletions(tmp_path, trailer).returncode == 0

    # test_b moves to another file under its own name.
    _stage(
        tmp_path,
        {
            "tests/test_a.py": "def test_a(tmp_path):\n    assert True\n",
            "tests/test_moved.py": "def test_b():\n    assert True\n",
        },
    )
    result = _deletions(tmp_path, trailer)
    assert result.returncode == 0, result.stdout


@pytest.mark.toolchain
def test_test_deletions_rename_without_trailer_names_both_ids(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _stage(tmp_path, {"tests/test_a.py": TWO_TESTS})
    _commit_all(tmp_path)
    _stage(tmp_path, {"tests/test_a.py": TWO_TESTS.replace("test_a", "test_renamed")})

    result = _deletions(tmp_path)

    assert result.returncode == 1
    assert "Removed tests:\n  tests/test_a.py::test_a\n" in result.stdout
    assert "Added tests:\n  tests/test_a.py::test_renamed\n" in result.stdout
    assert "Test-Renamed: <old id> -> <new id>" in result.stdout
    trailed = _deletions(
        tmp_path,
        "rename\n\nTest-Renamed: tests/test_a.py::test_a -> "
        "tests/test_a.py::test_renamed\n",
    )
    assert trailed.returncode == 0, trailed.stdout


@pytest.mark.toolchain
def test_test_deletions_flags_tests_removed_inside_a_renamed_file(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _stage(tmp_path, {"tests/test_a.py": TWO_TESTS})
    _commit_all(tmp_path)
    subprocess.run(
        ["git", "mv", "tests/test_a.py", "tests/test_z.py"], cwd=tmp_path, check=True
    )
    assert _deletions(tmp_path).returncode == 0

    _stage(tmp_path, {"tests/test_z.py": "def test_b():\n    assert True\n"})
    result = _deletions(tmp_path)

    assert result.returncode == 1
    assert "tests/test_a.py::test_a" in result.stdout


@pytest.mark.toolchain
def test_test_deletions_flags_a_deleted_file_unless_its_tests_moved(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _stage(
        tmp_path,
        {"tests/conftest.py": "import pytest\n", "tests/test_a.py": TWO_TESTS},
    )
    _commit_all(tmp_path)

    _stage(tmp_path, {"tests/conftest.py": None})
    support = _deletions(tmp_path)
    assert support.returncode == 1
    assert "Deleted test files:\n  tests/conftest.py\n" in support.stdout

    subprocess.run(["git", "reset", "-q", "--hard"], cwd=tmp_path, check=True)
    _stage(tmp_path, {"tests/test_a.py": None, "tests/test_split.py": TWO_TESTS})
    moved = _deletions(tmp_path)
    assert moved.returncode == 0, moved.stdout


@pytest.mark.toolchain
def test_test_deletions_unparsable_file_falls_back_to_removed_lines(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _stage(tmp_path, {"tests/test_a.py": TWO_TESTS})
    _commit_all(tmp_path)
    _stage(tmp_path, {"tests/test_a.py": "def test_b(:\n    assert True\n"})

    result = _deletions(tmp_path)

    assert result.returncode == 1
    assert "note: tests/test_a.py does not parse" in result.stdout
    assert "tests/test_a.py: -def test_a():" in result.stdout


@pytest.mark.toolchain
def test_test_deletions_skips_a_merge_and_an_unborn_branch(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _stage(tmp_path, {"tests/test_a.py": TWO_TESTS})
    assert _deletions(tmp_path).returncode == 0  # nothing to delete yet

    _commit_all(tmp_path)
    _stage(tmp_path, {"tests/test_a.py": None})
    assert _deletions(tmp_path).returncode == 1
    git_dir = tmp_path / ".git"
    (git_dir / "MERGE_HEAD").write_text(
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout,
        encoding="utf-8",
    )
    assert _deletions(tmp_path).returncode == 0


@pytest.mark.toolchain
def test_test_deletions_amend_ignores_an_inherited_trailer(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _stage(tmp_path, {"tests/test_a.py": TWO_TESTS})
    _commit_all(tmp_path)
    trailer = (
        "rename\n\nTest-Renamed: tests/test_a.py::test_a -> "
        "tests/test_a.py::test_renamed\n"
    )
    _stage(tmp_path, {"tests/test_a.py": TWO_TESTS.replace("test_a", "test_renamed")})
    assert _deletions(tmp_path, trailer).returncode == 0
    _commit_all(tmp_path, trailer)

    # Amending with the same message: the inherited entry matches nothing.
    _stage(tmp_path, {"README.md": "notes\n"})
    assert _deletions(tmp_path, trailer).returncode == 0
    _stage(tmp_path, {"tests/test_a.py": "def test_renamed():\n    assert True\n"})
    result = _deletions(tmp_path, trailer)
    assert result.returncode == 1
    assert "tests/test_a.py::test_b" in result.stdout


@pytest.mark.toolchain
def test_test_deletions_fails_when_git_cannot_read_the_change(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()

    result = _run_hook(
        _hook_path("check-test-deletions"),
        _message(outside),
        cwd=outside,
        env={"GIT_CEILING_DIRECTORIES": str(tmp_path)},
    )

    assert result.returncode == 1
    assert "could not read the staged change" in result.stdout
    usage = _run_hook(_hook_path("check-test-deletions"), cwd=outside)
    assert usage.returncode == 1 and "Usage" in usage.stdout


def test_test_deletion_guard_runs_at_commit_msg_where_it_is_wired() -> None:
    manifest = _manifest()
    hook = next(h for h in manifest["hooks"] if h["id"] == "check-test-deletions")
    assert hook["stage"] == "commit-msg"
    assert hook["snippet"]["stages"] == ["commit-msg"]
    assert "pass_filenames" not in hook["snippet"]
    assert manifest["default_install_hook_types"] == ["pre-commit", "commit-msg"]
    assert {h["stage"] for h in manifest["hooks"]} == set(
        manifest["default_install_hook_types"]
    )

    config = yaml.safe_load(
        (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    wired = next(
        h
        for repo in config["repos"]
        for h in repo["hooks"]
        if h["id"] == "check-test-deletions"
    )
    assert wired["stages"] == ["commit-msg"]
    assert "pass_filenames" not in wired


# ---------------------------------------------------------------------------
# check-skip-only-test-scaffolding
# ---------------------------------------------------------------------------


@pytest.mark.toolchain
def test_skip_only_rejects_new_skip_and_allows_optin_reason(
    tmp_path: Path,
) -> None:
    test_file = _repo_with_test_file(tmp_path, "tests")
    hook = _hook_path("check-skip-only-test-scaffolding")

    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark." + "skip(reason='later')\n"
        "def test_new_skip():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True)
    result = _run_hook(hook, cwd=tmp_path)
    assert result.returncode == 1
    assert "skip-only test scaffolding" in result.stdout

    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark." + "skip(reason='live: requires explicit opt-in')\n"
        "def test_live_only():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True)
    assert _run_hook(hook, cwd=tmp_path).returncode == 0


@pytest.mark.toolchain
def test_skip_only_rendered_tests_root(tmp_path: Path) -> None:
    test_file = _repo_with_test_file(tmp_path, "src/tests")
    rendered = _render(
        "check-skip-only-test-scaffolding", tmp_path, {"layout_tests": "src/tests"}
    )
    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark." + "skip(reason='later')\n"
        "def test_new_skip():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True)

    result = _run_hook(rendered, cwd=tmp_path)

    assert result.returncode == 1


# ---------------------------------------------------------------------------
# check-principles-amendment
# ---------------------------------------------------------------------------


def _repo_with_principles_doc(tmp_path: Path, doc_rel: str) -> Path:
    _init_git_repo(tmp_path)
    doc = tmp_path / doc_rel
    doc.parent.mkdir(parents=True)
    doc.write_text("# Engineering Principles\n", encoding="utf-8")
    _commit_all(tmp_path)
    return doc


@pytest.mark.toolchain
def test_principles_amendment_requires_trailer(tmp_path: Path) -> None:
    doc = _repo_with_principles_doc(tmp_path, "docs/workflow/engineering-principles.md")
    doc.write_text("# Engineering Principles\n\nAmended.\n", encoding="utf-8")
    subprocess.run(["git", "add", str(doc)], cwd=tmp_path, check=True)
    hook = _hook_path("check-principles-amendment")

    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("amend principles\n", encoding="utf-8")
    fail = _run_hook(hook, str(message), cwd=tmp_path)
    assert fail.returncode == 1
    assert "Principles-Amendment" in fail.stdout

    message.write_text(
        "amend principles\n\nPrinciples-Amendment: ruled by the owner\n",
        encoding="utf-8",
    )
    assert _run_hook(hook, str(message), cwd=tmp_path).returncode == 0


@pytest.mark.toolchain
def test_principles_amendment_ignores_unrelated_commits(tmp_path: Path) -> None:
    _repo_with_principles_doc(tmp_path, "docs/workflow/engineering-principles.md")
    other = tmp_path / "README.md"
    other.write_text("unrelated\n", encoding="utf-8")
    subprocess.run(["git", "add", str(other)], cwd=tmp_path, check=True)
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("unrelated change\n", encoding="utf-8")

    result = _run_hook(
        _hook_path("check-principles-amendment"), str(message), cwd=tmp_path
    )

    assert result.returncode == 0


@pytest.mark.toolchain
def test_principles_amendment_rendered_doc_path(tmp_path: Path) -> None:
    doc = _repo_with_principles_doc(tmp_path, "docs/principles.md")
    rendered = _render(
        "check-principles-amendment", tmp_path, {"principles_doc": "docs/principles.md"}
    )
    doc.write_text("# Principles\n\nAmended.\n", encoding="utf-8")
    subprocess.run(["git", "add", str(doc)], cwd=tmp_path, check=True)
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("amend\n", encoding="utf-8")

    result = _run_hook(rendered, str(message), cwd=tmp_path)

    assert result.returncode == 1
    assert "docs/principles.md" in result.stdout


@pytest.mark.toolchain
def test_principles_amendment_usage_error_without_msg_file(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    result = _run_hook(_hook_path("check-principles-amendment"), cwd=tmp_path)

    assert result.returncode == 1
    assert "Usage" in result.stdout
