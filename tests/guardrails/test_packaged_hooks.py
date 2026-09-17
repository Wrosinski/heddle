"""
Packaged workflow-hook library tests (adoption host-tooling increment 1).

Covers heddle/resources/hooks/: manifest conformance, the stdlib-only
contract, the `{{name|default}}` placeholder mechanic (unrendered files
must run with defaults — the heddle repo dogfoods them that way; `heddle
init` bakes host values in at projection time), and per-hook pass/reject
behavior against throwaway git repos. Nothing here touches the real
repository's index or worktree.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

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
    """Simulate the init-time projection: replace whole placeholder tokens."""
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

    result = _run_hook(_hook_path("check-test-deletions"), cwd=tmp_path)

    assert result.returncode == 1
    assert "Test deletions detected" in result.stdout


@pytest.mark.toolchain
def test_test_deletions_rejects_deleted_file_and_bypass_passes(
    tmp_path: Path,
) -> None:
    test_file = _repo_with_test_file(tmp_path, "tests")
    subprocess.run(["git", "rm", "-q", str(test_file)], cwd=tmp_path, check=True)
    hook = _hook_path("check-test-deletions")

    result = _run_hook(hook, cwd=tmp_path)
    assert result.returncode == 1
    assert "Deleted test files" in result.stdout

    bypassed = _run_hook(hook, cwd=tmp_path, env={"ALLOW_TEST_DELETION": "1"})
    assert bypassed.returncode == 0


@pytest.mark.toolchain
def test_test_deletions_rendered_tests_root(tmp_path: Path) -> None:
    test_file = _repo_with_test_file(tmp_path, "src/tests")
    rendered = _render("check-test-deletions", tmp_path, {"layout_tests": "src/tests"})
    test_file.write_text("# removed\n", encoding="utf-8")
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True)

    # The unrendered default (tests/) does not see src/tests; the rendered
    # hook does — the placeholder is the projection seam.
    assert _run_hook(_hook_path("check-test-deletions"), cwd=tmp_path).returncode == 0
    assert _run_hook(rendered, cwd=tmp_path).returncode == 1


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
