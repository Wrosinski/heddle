"""
Focused behavior checks for the write ported guardrails.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
# The four workflow guards moved into the packaged hook library (adoption
# host-tooling increment 1); their behavior checks below drive the
# packaged files the pre-commit config now wires in.
PACKAGED_HOOKS = REPO_ROOT / "heddle" / "resources" / "hooks"
PRECOMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
QUALITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "quality.yml"
README = REPO_ROOT / "README.md"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
ENFORCEMENT_BASELINE = REPO_ROOT / "docs" / "workflow" / "enforcement-baseline.md"


def _run(
    script: str,
    *args: str,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    root = PACKAGED_HOOKS if (PACKAGED_HOOKS / script).is_file() else SCRIPTS
    command = [sys.executable, str(root / script), *args]
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        command,
        cwd=cwd,
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("stage", ["scaffold", "implement"])
@pytest.mark.parametrize(
    "test_command",
    ["python -m pytest tests", "python -m pytest tests/test_example.py::test_original"],
)
@pytest.mark.parametrize(
    "change", ["rename-symbol", "delete-file", "rename-file", "bound-spec"]
)
@pytest.mark.toolchain
def test_w5_ac9_native_bindings_follow_staged_changes(
    tmp_path, stage, test_command, change
):
    """A surviving AC comment cannot excuse a dangling explicit target."""
    from tests.content_identity_helpers import git

    (tmp_path / "tests").mkdir()
    test = tmp_path / "tests/test_example.py"
    test.write_text("# AC-1\ndef test_original():\n    assert 1 + 1 == 2\n")
    spec = tmp_path / "docs/features/example.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "# Feature\n\n## Acceptance Criteria\n\n### AC-1: Works\n\n"
        "Verified-by: tests/test_example.py::test_original\n"
    )
    state = tmp_path / "plans/example/state.yaml"
    state.parent.mkdir(parents=True)
    state.write_text(
        yaml.safe_dump(
            {
                "schema": "heddle.state/v5",
                "stage": stage,
                "spec": "docs/features/example.md",
                "commands": {"test_command": test_command},
            }
        )
    )
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Binding Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "valid authored binding")
    test.write_text(test.read_text() + "\n# staged valid control\n")
    git(tmp_path, "add", "tests")
    assert (
        _run(
            "check-ac-test-coverage.py",
            cwd=tmp_path,
            env={"ALLOW_MISSING_AC_TEST_COVERAGE": ""},
        ).returncode
        == 0
    )
    git(tmp_path, "commit", "-qm", "staged valid binding control")
    if change == "rename-symbol":
        test.write_text(test.read_text().replace("test_original", "test_renamed"))
        git(tmp_path, "add", "tests")
    elif change == "delete-file":
        git(tmp_path, "rm", "tests/test_example.py")
    elif change == "rename-file":
        git(tmp_path, "mv", "tests/test_example.py", "tests/test_renamed.py")
    else:
        spec.write_text(spec.read_text().replace("test_original", "test_missing"))
        git(tmp_path, "add", "docs/features/example.md")
    result = _run(
        "check-ac-test-coverage.py",
        cwd=tmp_path,
        env={"ALLOW_MISSING_AC_TEST_COVERAGE": ""},
    )
    assert result.returncode == 1, (
        f"FAIL W5 AC-9: {change} escaped native binding discovery: {result.stdout}"
    )
    assert "tests/test_example.py" in result.stdout and "AC-1" in result.stdout
    # Restore the real binding and prove the checker accepts an active workspace.
    test.parent.mkdir(exist_ok=True)
    test.write_text("# AC-1\ndef test_original():\n    assert 1 + 1 == 2\n")
    spec.write_text(spec.read_text().replace("test_missing", "test_original"))
    git(tmp_path, "add", "tests", "docs/features/example.md")
    assert (
        _run(
            "check-ac-test-coverage.py",
            cwd=tmp_path,
            env={"ALLOW_MISSING_AC_TEST_COVERAGE": ""},
        ).returncode
        == 0
    )


@pytest.mark.parametrize("change", ["delete-file", "rename-file"])
@pytest.mark.toolchain
def test_w5_ac9_precommit_rejects_staged_dangling_binding(tmp_path, change):
    from tests.content_identity_helpers import git, git_host

    root = git_host(tmp_path / "host")
    # Keep the production configuration, hook arguments and entry path intact.
    (root / ".pre-commit-config.yaml").write_bytes(PRECOMMIT_CONFIG.read_bytes())
    (root / ".venv").symlink_to(REPO_ROOT / ".venv", target_is_directory=True)
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "check-ac-test-coverage.py").write_bytes(
        (SCRIPTS / "check-ac-test-coverage.py").read_bytes()
    )
    test = root / "tests/test_example.py"
    test.parent.mkdir()
    original = "def test_original():\n    assert 1 + 1 == 2\n"
    test.write_text(original)
    spec = root / "docs/features/example.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "### AC-1: Works\nVerified-by: tests/test_example.py::test_original\n"
    )
    state = root / "plans/example/state.yaml"
    state.parent.mkdir(parents=True)
    state.write_text(
        yaml.safe_dump(
            {
                "stage": "implement",
                "spec": "docs/features/example.md",
                "commands": {"test_command": "python -m pytest tests"},
            }
        )
    )
    git(root, "add", ".pre-commit-config.yaml", "scripts", "tests", "docs", "plans")
    env = {**os.environ, "ALLOW_MISSING_AC_TEST_COVERAGE": "", "SKIP": ""}

    def run_hook():
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pre_commit",
                "run",
                "--config",
                ".pre-commit-config.yaml",
                "check-ac-test-coverage",
                "--verbose",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    valid = run_hook()
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert "Passed" in valid.stdout and "Skipped" not in valid.stdout
    git(root, "commit", "-qm", "valid production hook binding")
    if change == "delete-file":
        git(root, "rm", "tests/test_example.py")
    else:
        git(root, "mv", "tests/test_example.py", "tests/test_renamed.py")
    rejected = run_hook()
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    assert "AC-1" in rejected.stdout and "tests/test_example.py" in rejected.stdout
    test.parent.mkdir(exist_ok=True)
    test.write_text(original)
    git(root, "add", "tests")
    repaired = run_hook()
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert "Passed" in repaired.stdout and "Skipped" not in repaired.stdout


@pytest.mark.parametrize("stage", ["scaffold", "implement"])
@pytest.mark.parametrize("directory", ["tests/integration", "contract-checks"])
@pytest.mark.parametrize("change", ["delete", "rename-file", "rename-symbol"])
@pytest.mark.toolchain
def test_w5_ac9_explicit_target_selects_coverage_outside_command_directories(
    tmp_path, stage, directory, change
):
    from tests.content_identity_helpers import git, git_host

    root = git_host(tmp_path / "host")
    relative = f"{directory}/test_example.py"
    target = root / relative
    target.parent.mkdir(parents=True)
    original = "def test_original():\n    assert 1 + 1 == 2\n"
    target.write_text(original)
    spec = root / "docs/features/example.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(f"### AC-1: Works\nVerified-by: {relative}::test_original\n")
    state = root / "plans/example/state.yaml"
    state.parent.mkdir(parents=True)
    state.write_text(
        yaml.safe_dump(
            {
                "stage": stage,
                "spec": "docs/features/example.md",
                "commands": {"test_command": "python -m pytest tests/unit"},
            }
        )
    )
    git(root, "add", ".")
    env = {"ALLOW_MISSING_AC_TEST_COVERAGE": ""}
    assert _run("check-ac-test-coverage.py", cwd=root, env=env).returncode == 0
    git(root, "commit", "-qm", "valid explicit binding outside command directory")
    if change == "delete":
        git(root, "rm", relative)
    elif change == "rename-file":
        git(root, "mv", relative, f"{directory}/test_renamed.py")
    else:
        target.write_text(original.replace("test_original", "test_renamed"))
        git(root, "add", relative)
    rejected = _run("check-ac-test-coverage.py", cwd=root, env=env)
    assert rejected.returncode == 1, "Staged explicit AC target escaped discovery"
    assert "AC-1" in rejected.stdout and relative in rejected.stdout
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(original)
    git(root, "add", relative)
    repaired = _run("check-ac-test-coverage.py", cwd=root, env=env)
    assert repaired.returncode == 0, repaired.stdout


@pytest.mark.parametrize("stage", ["specify", "spec-review", "plan-review", "scaffold"])
@pytest.mark.toolchain
def test_w5_ac9_placeholder_has_a_real_scaffold_activation_boundary(tmp_path, stage):
    from tests.content_identity_helpers import git, git_host

    root = git_host(tmp_path / "host")
    spec = root / "docs/features/example.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "# Feature\n\n## Acceptance Criteria\n\n### AC-1: Works\n\n"
        "Verified-by: (scaffold stage binding pending)\n"
    )
    state = root / "plans/example/state.yaml"
    state.parent.mkdir(parents=True)
    state.write_text(
        yaml.safe_dump(
            {
                "stage": stage,
                "spec": "docs/features/example.md",
                "commands": {},
            }
        )
    )
    git(root, "add", ".")
    result = _run(
        "check-ac-test-coverage.py",
        cwd=root,
        env={"ALLOW_MISSING_AC_TEST_COVERAGE": ""},
    )
    assert result.returncode == (1 if stage == "scaffold" else 0), result.stdout


@pytest.mark.toolchain
def test_w5_ac9_only_explicitly_changed_historical_binding_is_checked(tmp_path):
    from tests.content_identity_helpers import git, git_host

    root = git_host(tmp_path / "host")
    specs = root / "docs/features"
    specs.mkdir(parents=True)
    text = (
        "---\ntype: feature-spec\nlifecycle: complete\n---\n\n# History\n\n"
        "## Acceptance Criteria\n\n### AC-1: Historical result\n\n"
        "Verified-by: tests/test_missing.py::test_old\n"
        "\n### AC-2: Untouched binding in the same file\n\n"
        "Verified-by: tests/test_missing.py::test_unchanged\n"
    )
    (specs / "untouched.md").write_text(text)
    selected = specs / "selected.md"
    selected.write_text(text)
    git(root, "add", ".")
    git(root, "commit", "-qm", "historical fixture evidence")
    clean = _run(
        "check-ac-test-coverage.py",
        cwd=root,
        env={"ALLOW_MISSING_AC_TEST_COVERAGE": ""},
    )
    assert clean.returncode == 0, clean.stdout
    selected.write_text(text.replace("test_old", "test_now_missing"))
    git(root, "add", "docs/features/selected.md")
    changed = _run(
        "check-ac-test-coverage.py",
        cwd=root,
        env={"ALLOW_MISSING_AC_TEST_COVERAGE": ""},
    )
    assert changed.returncode == 1, (
        "FAIL W5 AC-9: changed bound historical spec escaped"
    )
    assert "selected.md" in changed.stdout and "untouched.md" not in changed.stdout
    assert "AC-2" not in changed.stdout
    valid = root / "tests/test_valid.py"
    valid.parent.mkdir(exist_ok=True)
    valid.write_text("def test_valid():\n    assert 1 + 1 == 2\n")
    selected.write_text(
        text.replace(
            "tests/test_missing.py::test_old", "tests/test_valid.py::test_valid"
        )
    )
    git(root, "add", "docs/features/selected.md", "tests/test_valid.py")
    repaired = _run(
        "check-ac-test-coverage.py",
        cwd=root,
        env={"ALLOW_MISSING_AC_TEST_COVERAGE": ""},
    )
    assert repaired.returncode == 0, repaired.stdout


def test_w5_ac9_obsolete_plan_proof_is_removed_without_losing_principles_guard():
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text())
    hooks = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}
    assert "check-plan-verification-evidence" not in hooks, (
        "FAIL W5 AC-9: retired flat-plan proof still runs"
    )
    assert not (SCRIPTS / "check-plan-verification-evidence.py").exists()
    assert "check-principles-amendment" in hooks


@pytest.mark.toolchain
def test_prompt_conventions_and_enforcement_baseline_pass() -> None:
    assert _run("check-prompt-conventions.py").returncode == 0
    assert _run("check-enforcement-baseline-doc-language-aware.py").returncode == 0


@pytest.mark.toolchain
def test_prompt_conventions_rejects_missing_marker(tmp_path: Path) -> None:
    # review: a guard regressed to always-exit(0) would slip through without a
    # rejection test — feed a prompt missing a required convention marker.
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "conventions.yaml").write_text(
        "conventions:\n"
        "  finding_structure:\n"
        "    keywords: ['IMPLEMENT', 'REPORT', 'IGNORE']\n"
        "prompts:\n"
        "  sample.md: ['finding_structure']\n",
        encoding="utf-8",
    )
    (prompts / "sample.md").write_text(
        "This prompt omits the required finding markers.\n", encoding="utf-8"
    )

    result = _run("check-prompt-conventions.py", str(prompts), cwd=tmp_path)

    assert result.returncode == 1
    assert "missing marker" in result.stdout


@pytest.mark.toolchain
def test_enforcement_baseline_rejects_template_markers(tmp_path: Path) -> None:
    # review: rejection case for the baseline guard — a left-in template marker
    # must fail rather than pass silently.
    doc = tmp_path / "baseline.md"
    doc.write_text(
        "# Enforcement Baseline\n\nLast updated: YYYY-MM-DD\n", encoding="utf-8"
    )

    result = _run("check-enforcement-baseline-doc-language-aware.py", str(doc))

    assert result.returncode == 1
    assert "template marker still present" in result.stdout


@pytest.mark.toolchain
def test_ac_test_coverage_rejects_unbacked_ac(tmp_path: Path) -> None:
    # review: rejection case for the AC-coverage guard — an AC with no resolvable
    # test reference must fail (standalone spec-arg mode).
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Feature Spec\n\n## Acceptance Criteria\n\n"
        "### AC-1: Criterion with no backing test\n\n"
        "A description, but no Verified-by line and no test references.\n",
        encoding="utf-8",
    )
    empty_tests = tmp_path / "emptytests"
    empty_tests.mkdir()

    result = _run("check-ac-test-coverage.py", str(spec), str(empty_tests))

    assert result.returncode == 1
    assert "no test references" in result.stdout


@pytest.mark.toolchain
def test_no_src_print_rejects_non_cli_source_print(tmp_path: Path) -> None:
    source = tmp_path / "heddle" / "kernel"
    source.mkdir(parents=True)
    bad = source / "bad.py"
    bad.write_text("def f():\n    print('debug')\n", encoding="utf-8")

    result = _run("check-no-src-print.py", "heddle/kernel/bad.py", cwd=tmp_path)

    assert result.returncode == 1
    assert "print() is not allowed" in result.stdout


@pytest.mark.parametrize(
    "source_text",
    [
        "import subprocess\n\ndef f():\n    return subprocess.run(['git', 'status'])\n",
        "from subprocess import run\n\ndef f():\n    return run(['git', 'status'])\n",
    ],
)
@pytest.mark.parametrize("package", ["gate", "io"])
@pytest.mark.toolchain
def test_subprocess_handler_guard_rejects_unsanctioned_gate_calls(
    tmp_path: Path, source_text: str, package: str
) -> None:
    source = tmp_path / "heddle" / package
    source.mkdir(parents=True)
    bad = source / "bad.py"
    bad.write_text(source_text, encoding="utf-8")

    result = _run(
        "check-subprocess-handlers.py",
        f"heddle/{package}/bad.py",
        cwd=tmp_path,
    )

    assert result.returncode == 1
    assert "is not sanctioned" in result.stdout


@pytest.mark.toolchain
def test_precommit_top_level_and_commit_msg_paths_run(tmp_path: Path) -> None:
    all_files = subprocess.run(
        [
            ".venv/bin/pre-commit",
            "run",
            "--config",
            ".pre-commit-config.yaml",
            "--all-files",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert all_files.returncode == 0, all_files.stdout + all_files.stderr

    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(
        "sample\n\nBypass-Justification: exercising commit-msg hook\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "ALLOW_TEST_DELETION": "1",
        "BYPASS_JUSTIFICATION": "",
    }
    commit_msg = subprocess.run(
        [
            ".venv/bin/pre-commit",
            "run",
            "--config",
            ".pre-commit-config.yaml",
            "check-bypass-justification",
            "--hook-stage",
            "commit-msg",
            "--commit-msg-filename",
            str(message),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert commit_msg.returncode == 0, commit_msg.stdout + commit_msg.stderr

    no_trailer = tmp_path / "COMMIT_EDITMSG.no-trailer"
    no_trailer.write_text("sample\n", encoding="utf-8")
    missing = subprocess.run(
        [
            ".venv/bin/pre-commit",
            "run",
            "--config",
            ".pre-commit-config.yaml",
            "check-bypass-justification",
            "--hook-stage",
            "commit-msg",
            "--commit-msg-filename",
            str(no_trailer),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert missing.returncode == 1
    assert "Bypass-Justification" in missing.stdout


def test_raw_evidence_exclusions_are_limited_to_mutating_hooks() -> None:
    """Raw evidence stays byte-faithful without bypassing semantic hooks."""
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    evidence_paths = {
        "plans/example/verification/m1.log",
        "plans/example/reviews/self-review-m1.md",
    }
    global_exclude = config.get("exclude", r"(?!)")
    assert not {path for path in evidence_paths if re.search(global_exclude, path)}, (
        "verification and review evidence must not be excluded repository-wide"
    )

    hooks = {
        hook["id"]: hook
        for repo in config.get("repos", [])
        for hook in repo.get("hooks", [])
    }
    mutating_hooks = {"trailing-whitespace", "end-of-file-fixer"}
    for hook_id, hook in hooks.items():
        local_exclude = hook.get("exclude", r"(?!)")
        excluded_evidence = {
            path for path in evidence_paths if re.search(local_exclude, path)
        }
        if hook_id in mutating_hooks:
            assert excluded_evidence == evidence_paths
        else:
            assert not excluded_evidence, (
                f"non-mutating hook {hook_id!r} excludes raw evidence paths"
            )

    large_files = hooks["check-added-large-files"]
    large_files_pattern = large_files.get("files", config.get("files", ""))
    assert all(re.search(large_files_pattern, path) for path in evidence_paths)


@pytest.mark.toolchain
def test_bypass_justification_requires_reason(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("sample\n", encoding="utf-8")
    fail = _run(
        "check-bypass-justification.py",
        str(message),
        env={"ALLOW_TEST_DELETION": "1", "BYPASS_JUSTIFICATION": ""},
    )
    assert fail.returncode == 1
    assert "Bypass-Justification" in fail.stdout

    passed = _run(
        "check-bypass-justification.py",
        env={
            "ALLOW_TEST_DELETION": "1",
            "BYPASS_JUSTIFICATION": "temporary test fixture",
        },
    )
    assert passed.returncode == 0


@pytest.mark.toolchain
def test_gate_lock_guard_rejects_lock_paths() -> None:
    result = _run(
        "check-no-committed-gate-locks.py",
        "plans/.gate-locks/example.lock",
    )

    assert result.returncode == 1
    assert "Gate lock files" in result.stdout


@pytest.mark.toolchain
def test_ac_coverage_standalone_validates_verified_by_targets(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_feature.py"
    test_file.write_text(
        "def test_ac1_behavior():\n    assert True\n",
        encoding="utf-8",
    )
    spec = tmp_path / "spec.md"
    spec.write_text(
        "### AC-1: Behavior\nVerified-by: tests/test_feature.py::test_ac1_behavior\n",
        encoding="utf-8",
    )

    result = _run(
        "check-ac-test-coverage.py",
        "spec.md",
        "tests",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "selector",
    [
        "test_module_behavior",
        "TestFeature::test_class_behavior",
        # Preserve the legacy bare-method spelling while specs migrate to the
        # pytest-compatible class-qualified form.
        "test_class_behavior",
    ],
)
@pytest.mark.toolchain
def test_ac_coverage_accepts_supported_function_selectors(
    tmp_path: Path, selector: str
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_feature.py").write_text(
        "def test_module_behavior():\n    assert True\n\n"
        "class TestFeature:\n"
        "    def test_class_behavior(self):\n"
        "        assert True\n",
        encoding="utf-8",
    )
    (tmp_path / "spec.md").write_text(
        f"### AC-1: Behavior\nVerified-by: tests/test_feature.py::{selector}\n",
        encoding="utf-8",
    )

    result = _run(
        "check-ac-test-coverage.py",
        "spec.md",
        "tests",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "selector",
    [
        "TestMissing::test_class_behavior",
        "TestFeature::test_missing",
        # The AST checker cannot validate generated parameter IDs and therefore
        # rejects them rather than accepting an unverifiable exact target.
        "TestFeature::test_class_behavior[case]",
    ],
)
@pytest.mark.toolchain
def test_ac_coverage_rejects_unsupported_class_selectors(
    tmp_path: Path, selector: str
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_feature.py").write_text(
        "class TestFeature:\n    def test_class_behavior(self):\n        assert True\n",
        encoding="utf-8",
    )
    (tmp_path / "spec.md").write_text(
        f"### AC-1: Behavior\nVerified-by: tests/test_feature.py::{selector}\n",
        encoding="utf-8",
    )

    result = _run(
        "check-ac-test-coverage.py",
        "spec.md",
        "tests",
        cwd=tmp_path,
    )

    assert result.returncode == 1
    assert selector in result.stdout


@pytest.mark.toolchain
def test_test_deletion_guard_uses_tests_layout(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_delete.py"
    test_file.write_text(
        "def test_will_be_deleted():\n    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True)

    test_file.write_text("# removed\n", encoding="utf-8")
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True)

    result = subprocess.run(
        [sys.executable, str(PACKAGED_HOOKS / "check-test-deletions.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "Test deletions detected" in result.stdout


@pytest.mark.toolchain
def test_skip_only_guard_rejects_new_unqualified_skip(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_skip.py"
    test_file.write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True)

    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark."
        "skip(reason='later')\n"
        "def test_new_skip():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(test_file)], cwd=tmp_path, check=True)

    result = _run("check-skip-only-test-scaffolding.py", cwd=tmp_path)

    assert result.returncode == 1
    assert "skip-only test scaffolding" in result.stdout


@pytest.mark.toolchain
def test_skill_sync_rejects_drifted_and_missing_mirrors(tmp_path: Path) -> None:
    # review rejection: the skill-sync guard resolves the repo root from its own
    # file location (not cwd), so run a copy from a temp scripts/ dir against a
    # classified skills tree with one drifted mirror and one missing mirror.
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    guard = scripts_dir / "check-skill-sync.py"
    guard.write_text(
        (SCRIPTS / "check-skill-sync.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    shutil.copytree(REPO_ROOT / ".claude/skills", tmp_path / ".claude/skills")
    shutil.copytree(REPO_ROOT / ".codex/skills", tmp_path / ".codex/skills")
    shutil.copytree(REPO_ROOT / ".agents/skills", tmp_path / ".agents/skills")
    mirror = tmp_path / ".codex/skills"
    (mirror / "root-cause-analysis/SKILL.md").write_text("drifted\n", encoding="utf-8")
    (mirror / "worktree-workflow/SKILL.md").unlink()

    result = subprocess.run(
        [sys.executable, str(guard)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "content drift" in result.stdout
    assert "missing mirror" in result.stdout


def test_ruff_versions_match_across_dev_and_pre_commit() -> None:
    """Keep formatter behavior identical in the venv and git hook."""
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]
    dev_pin = next(
        dependency.removeprefix("ruff==")
        for dependency in dev_dependencies
        if dependency.startswith("ruff==")
    )

    precommit = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    hook_pin = next(
        repo["rev"].removeprefix("v")
        for repo in precommit["repos"]
        if repo["repo"] == "https://github.com/astral-sh/ruff-pre-commit"
    )

    assert hook_pin == dev_pin, (
        f"Ruff version mismatch: dev dependency is {dev_pin}, pre-commit hook is "
        f"{hook_pin}. Keep both pins equal so formatting does not change during "
        "a commit."
    )


def _configured_hooks() -> tuple[dict[str, dict], dict[str, dict]]:
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    repos = {repo["repo"]: repo for repo in config["repos"]}
    hooks = {
        hook["id"]: hook for repo in config["repos"] for hook in repo.get("hooks", [])
    }
    return repos, hooks


def test_pre_commit_baseline_ac1_documents_complete_hook_installation() -> None:
    """AC-1: fresh setup installs both declared Git-hook stages."""
    contributing = CONTRIBUTING.read_text(encoding="utf-8")
    installer_command = ".venv/bin/python scripts/install-repository-hooks.py"
    assert installer_command in contributing, (
        "FAIL AC-1: contributing guidance must route development setup through the "
        "repository hook installer"
    )
    installer = (SCRIPTS / "install-repository-hooks.py").read_text(encoding="utf-8")
    assert 'root / ".venv/bin/pre-commit"' in installer
    assert '"install", "--install-hooks"' in installer

    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    assert set(config["default_install_hook_types"]) == {"pre-commit", "commit-msg"}


def test_pre_commit_baseline_ac2_ci_replays_the_local_quality_contract() -> None:
    """AC-2: read-only push and pull-request CI runs hooks, then tests."""
    assert QUALITY_WORKFLOW.is_file(), (
        "FAIL AC-2: .github/workflows/quality.yml does not exist"
    )
    workflow = yaml.safe_load(QUALITY_WORKFLOW.read_text(encoding="utf-8"))
    assert set(workflow["on"]) == {"push", "pull_request"}
    assert workflow["permissions"] == {"contents": "read"}

    steps = workflow["jobs"]["quality"]["steps"]
    uses = [step["uses"] for step in steps if "uses" in step]
    assert "actions/checkout@v7" in uses
    assert "actions/setup-python@v7" in uses
    setup_python = next(
        step for step in steps if step.get("uses") == "actions/setup-python@v7"
    )
    assert str(setup_python["with"]["python-version"]) == "3.13"

    commands = [step["run"] for step in steps if "run" in step]
    joined = "\n".join(commands)
    assert "python -m venv .venv" in joined
    assert ".venv/bin/python -m pip install -e '.[dev]'" in joined
    hook_index = next(
        index
        for index, command in enumerate(commands)
        if ".venv/bin/pre-commit run --all-files" in command
    )
    test_commands = [
        command for command in commands if ".venv/bin/python -m pytest" in command
    ]
    assert test_commands == [
        ".venv/bin/python -m pytest --test-band=fast",
        ".venv/bin/python -m pytest --test-band=toolchain",
    ]
    fast_index = commands.index(test_commands[0])
    toolchain_index = commands.index(test_commands[1])
    assert hook_index < fast_index < toolchain_index
    assert "--allow-e2e" not in joined
    assert "--allow-live" not in joined


def test_pre_commit_baseline_ac3_ruff_covers_all_maintained_python() -> None:
    """AC-3: Ruff lint and format share the complete maintained scope."""
    _, hooks = _configured_hooks()
    expected_scope = r"^(heddle|tests|scripts)/.*\.py$"
    assert "ruff-check" in hooks, "FAIL AC-3: the current Ruff lint hook is absent"
    assert hooks["ruff-check"]["files"] == expected_scope
    assert hooks["ruff-format"]["files"] == expected_scope


def test_pre_commit_baseline_ac4_mypy_is_reproducible_and_package_wide() -> None:
    """AC-4: the hook delegates package scope and policy to pinned config."""
    _, hooks = _configured_hooks()
    mypy_hook = hooks["mypy"]
    assert "args" not in mypy_hook, (
        "FAIL AC-4: the local hook must not override pyproject's package scope"
    )
    assert mypy_hook["pass_filenames"] is False

    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencies = set(pyproject["project"]["optional-dependencies"]["dev"])
    assert {
        "mypy==2.3.1",
        "types-pyyaml==6.0.12.20260906",
        "types-jsonschema==4.26.0.20260518",
    } <= dependencies
    policy = pyproject["tool"]["mypy"]
    assert policy["files"] == ["heddle"]
    for option in (
        "check_untyped_defs",
        "warn_redundant_casts",
        "warn_unused_configs",
        "strict_equality",
        "warn_unreachable",
    ):
        assert policy[option] is True, f"FAIL AC-4: mypy {option} is not enabled"


def test_pre_commit_baseline_ac5_generic_hygiene_hooks_are_active() -> None:
    """AC-5: the approved upstream accident checks are wired."""
    _, hooks = _configured_hooks()
    required = {
        "check-json",
        "check-merge-conflict",
        "check-case-conflict",
        "debug-statements",
        "detect-private-key",
        "check-symlinks",
        "check-executables-have-shebangs",
        "check-shebang-scripts-are-executable",
    }
    missing = required - hooks.keys()
    assert not missing, f"FAIL AC-5: missing generic hygiene hooks: {sorted(missing)}"


def test_pre_commit_baseline_ac6_enables_final_strict_typing() -> None:
    """AC-6: the final policy rejects untyped defs and Any returns."""
    policy = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["mypy"]
    assert policy.get("warn_return_any") is True, (
        "FAIL AC-6: mypy warn_return_any is not enabled"
    )
    assert policy.get("disallow_untyped_defs") is True, (
        "FAIL AC-6: mypy disallow_untyped_defs is not enabled"
    )


@pytest.mark.acceptance
def test_pre_commit_baseline_ac7_documents_the_integrated_green_commands() -> None:
    """AC-7: the baseline documents the focused local acceptance commands."""
    baseline = ENFORCEMENT_BASELINE.read_text(encoding="utf-8")
    for command in (
        ".venv/bin/pre-commit run --all-files",
        ".venv/bin/ruff check heddle tests scripts",
        ".venv/bin/ruff format --check heddle tests scripts",
        ".venv/bin/mypy",
        ".venv/bin/python -m tests.proof_runner <exact-file.py-or-node-id>",
        ".venv/bin/heddle validate",
    ):
        assert command in baseline, f"FAIL AC-7: baseline omits {command!r}"


# --- completion enforcement rails (review, review) ---------------------------------
#
# These two structural tests lock in the two "Enforce now" findings from the write
# peer-review cross-synthesis so the exact false-greens it caught cannot recur:
# * review — a hook stage declared but absent from default_install_hook_types
# (the commit-msg bypass guard shipped dormant this way).
# * review — a ported guard with no rejection test (a guard regressed to
# always-exit(0) ships green).


def _local_check_guards() -> set[str]:
    """The `check-*` guards wired as repo:local pre-commit hooks — both the
    remaining `scripts/` ports and the packaged `heddle/resources/hooks/`
    library entries.

    Derived from the live config so the rejection-coverage rail tracks the wired
    set without a hand-maintained inventory that could drift.
    """
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    guards: set[str] = set()
    for repo in config.get("repos", []):
        if repo.get("repo") != "local":
            continue
        for hook in repo.get("hooks", []):
            match = re.search(
                r"(?:scripts|heddle/resources/hooks)/(check-[\w-]+)\.(?:py|sh)",
                hook.get("entry", ""),
            )
            if match:
                guards.add(match.group(1))
    return guards


def test_every_declared_hook_stage_is_installed_by_default() -> None:
    """
    review: every hook `stages:` value must appear in
        `default_install_hook_types`.

        Why: `pre-commit install` only wires the git hooks named in
        `default_install_hook_types`; a hook declaring `stages: [commit-msg]` without
        that stage installed is silently dormant on real commits (the
        `check-bypass-justification` guard shipped this way until review). How to fix:
        add the missing stage to `default_install_hook_types` in
        `.pre-commit-config.yaml`.

    """
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    installed = set(config.get("default_install_hook_types", ["pre-commit"]))
    declared: set[str] = set()
    for repo in config.get("repos", []):
        for hook in repo.get("hooks", []):
            declared.update(hook.get("stages", []))

    uncovered = declared - installed
    assert not uncovered, (
        f"pre-commit hook stage(s) {sorted(uncovered)} are declared but absent "
        f"from default_install_hook_types {sorted(installed)} — a plain "
        f"`pre-commit install` will not wire them, leaving those guards dormant. "
        f"Add the stage(s) to default_install_hook_types in .pre-commit-config.yaml."
    )


def test_check_public_repository_rejects_unsorted_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "repository.txt"
    manifest.write_text("z-last\na-first\n", encoding="utf-8")
    result = _run(
        "check-public-repository.py",
        "repository",
        "--manifest",
        str(manifest),
    )
    assert result.returncode != 0
    assert "sorted and unique" in result.stdout


def test_every_wired_guard_has_a_rejection_test() -> None:
    """
    review: every `check-*` guard wired as a repo:local hook must have at
        least one rejection test in the guardrails test modules (this file or
        the packaged-hook tests).

        Why: a guard with only pass-direction tests would still ship green if it
        regressed to always-`sys.exit(0)` — the precise failure these guards exist
        to prevent. A rejection test feeds a violating input and asserts a non-zero
        exit. How to fix: add a rejection test in one of the scanned files that
        runs the named guard against a violating fixture and asserts a non-zero
        returncode, mirroring the existing per-guard reject tests.

    """
    nonzero_returncode = re.compile(r"returncode\s*(?:==\s*[1-9]\d*|!=\s*0)")
    scanned = (
        Path(__file__),
        Path(__file__).with_name("test_packaged_hooks.py"),
    )
    # Each block is one test function body (plus the leading module section).
    blocks = [
        block
        for path in scanned
        for block in re.split(
            r"(?=^def test_)",
            path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
    ]

    missing = sorted(
        guard
        for guard in _local_check_guards()
        if not any(
            guard in block and nonzero_returncode.search(block) for block in blocks
        )
    )
    assert not missing, (
        f"ported guard(s) {missing} have no rejection test in the scanned "
        f"guardrails test modules: a guard regressed to always-exit(0) would "
        f"ship green. Add a test that feeds a violating input to each named "
        f"guard and asserts a non-zero returncode."
    )
