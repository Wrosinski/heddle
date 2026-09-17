from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-public-repository.py"


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=check
    )


def host(tmp_path: Path) -> Path:
    root = tmp_path / "host"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    (root / "README.md").write_text("Synthetic host\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    return root


def policy(root: Path, baseline: str | None = None) -> Path:
    text = (ROOT / "release/public-repository.toml").read_text()
    original = tomllib.loads(text)["history"]["published_baseline"]
    if baseline is None:
        text = text.replace(f'published_baseline = "{original}"', "")
    else:
        text = text.replace(original, baseline)
    path = root / ".git/policy.toml"
    path.write_text(text)
    return path


def check(
    root: Path, command: str, *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            command,
            "--root",
            str(root),
            "--policy",
            str(root / ".git/policy.toml"),
            *args,
        ],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def commit_file(root: Path, path: str, content: str = "fixture\n") -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    git(root, "add", "-f", "--", path)
    git(root, "commit", "-qm", "fixture change")
    return git(root, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.parametrize(
    "path",
    [
        "plans/gate-effectiveness.md",
        "plans/friction-log.md",
        "docs/features/_descriptions.yaml",
        ".heddle/intake/example.yaml",
        "docs/proposals/example.md",
        "docs/gate-trajectories/example.json",
        "plans/space and\nnewline/review.json",
    ],
)
def test_forced_staging_is_rejected_and_working_bytes_survive(
    tmp_path: Path, path: str
) -> None:
    root = host(tmp_path)
    policy(root)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private fixture\n")
    git(root, "add", "-f", "--", path)
    result = check(root, "staged")
    assert result.returncode == 1 and "must stay local" in result.stdout
    assert target.read_text() == "private fixture\n"
    git(root, "rm", "--cached", "--", path)
    assert check(root, "staged").returncode == 0
    assert target.is_file()


def test_templates_fixtures_and_implementation_remain_publishable(
    tmp_path: Path,
) -> None:
    root = host(tmp_path)
    policy(root)
    for path in [
        "heddle/resources/example.scaffold.md",
        "tests/fixtures/plans/example/state.yaml",
        "heddle/example.py",
    ]:
        commit_file(root, path)
    tip = git(root, "rev-parse", "HEAD").stdout.strip()
    assert check(root, "staged").returncode == 0
    assert check(root, "history", "--head", tip).returncode == 0


def test_deleted_record_is_rejected_from_intermediate_history(tmp_path: Path) -> None:
    root = host(tmp_path)
    policy(root)
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    leaked = commit_file(root, "plans/example/review.json")
    git(root, "rm", "plans/example/review.json")
    git(root, "commit", "-qm", "remove record")
    tip = git(root, "rev-parse", "HEAD").stdout.strip()
    assert check(root, "staged").returncode == 0
    result = check(root, "history", "--base", base, "--head", tip)
    assert result.returncode == 1 and leaked in result.stdout


def test_generated_record_names_are_allowed_but_public_links_are_not(
    tmp_path: Path,
) -> None:
    root = host(tmp_path)
    policy(root)
    readme = root / "README.md"
    readme.write_text("Record feedback in `plans/friction-log.md`.\n")
    assert check(root, "references").returncode == 0
    readme.write_text("Read [feedback](plans/friction-log.md).\n")
    assert check(root, "references").returncode == 1


def test_published_baseline_allows_cleanup_but_not_new_record_changes(
    tmp_path: Path,
) -> None:
    root = host(tmp_path)
    baseline = commit_file(root, "plans/friction-log.md")
    policy(root, baseline)
    git(root, "rm", "--cached", "plans/friction-log.md")
    git(root, "commit", "-qm", "stop tracking seed")
    tip = git(root, "rev-parse", "HEAD").stdout.strip()
    assert check(root, "history", "--head", tip).returncode == 0
    tip = commit_file(root, "plans/friction-log.md", "new private history\n")
    assert check(root, "history", "--head", tip).returncode == 1


def test_merge_and_new_branch_history_are_checked(tmp_path: Path) -> None:
    root = host(tmp_path)
    policy(root)
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    main = git(root, "branch", "--show-current").stdout.strip()
    git(root, "checkout", "-qb", "side")
    leaked = commit_file(root, "docs/proposals/review.md")
    git(root, "rm", "docs/proposals/review.md")
    git(root, "commit", "-qm", "remove")
    git(root, "checkout", main)
    commit_file(root, "code.py")
    git(root, "merge", "--no-ff", "-qm", "merge side", "side")
    tip = git(root, "rev-parse", "HEAD").stdout.strip()
    for previous in [base, "0" * 40]:
        result = check(
            root,
            "outgoing",
            "origin",
            "unused",
            input_text=f"refs/heads/main {tip} refs/heads/main {previous}\n",
        )
        assert result.returncode == 1 and leaked in result.stdout


def test_every_outgoing_ref_and_annotated_tag_is_checked(tmp_path: Path) -> None:
    root = host(tmp_path)
    policy(root)
    clean = git(root, "rev-parse", "HEAD").stdout.strip()
    leaked = commit_file(root, "plans/example/state.yaml")
    git(root, "tag", "-am", "fixture", "leaked")
    tag = git(root, "rev-parse", "refs/tags/leaked").stdout.strip()
    result = check(
        root,
        "outgoing",
        "origin",
        "unused",
        input_text=(
            f"refs/heads/clean {clean} refs/heads/clean {'0' * 40}\n"
            f"refs/tags/leaked {tag} refs/tags/leaked {'0' * 40}\n"
        ),
    )
    assert result.returncode == 1 and leaked in result.stdout
    assert (
        check(
            root,
            "outgoing",
            "origin",
            "unused",
            input_text=(f"(delete) {'0' * 40} refs/heads/leaked {leaked}\n"),
        ).returncode
        == 0
    )


def test_missing_remote_object_scans_local_history_and_bad_input_fails(
    tmp_path: Path,
) -> None:
    root = host(tmp_path)
    policy(root)
    tip = commit_file(root, "plans/example/state.yaml")
    result = check(
        root,
        "outgoing",
        "origin",
        "unused",
        input_text=(f"refs/heads/main {tip} refs/heads/main {'f' * 40}\n"),
    )
    assert result.returncode == 1 and tip in result.stdout
    assert (
        check(root, "outgoing", "origin", "unused", input_text="bad\n").returncode == 1
    )
    assert check(root, "history", "--head", "HEAD").returncode == 1


def test_shallow_history_is_not_reported_clean(tmp_path: Path) -> None:
    root = host(tmp_path)
    policy(root)
    tip = commit_file(root, "code.py")
    clone = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth=1", root.as_uri(), str(clone))
    policy(clone)
    result = check(clone, "history", "--head", tip)
    assert result.returncode == 1 and "shallow" in result.stdout


@pytest.mark.toolchain
def test_real_pre_push_blocks_two_refs_without_touching_remote(tmp_path: Path) -> None:
    root = host(tmp_path)
    clean = git(root, "rev-parse", "HEAD").stdout.strip()
    main = git(root, "branch", "--show-current").stdout.strip()
    git(root, "checkout", "-qb", "private")
    commit_file(root, "plans/example/state.yaml")
    git(root, "checkout", main)
    (root / "scripts").mkdir()
    shutil.copy2(CHECKER, root / "scripts/check-public-repository.py")
    shutil.copy2(ROOT / "scripts/pre-push", root / "scripts/pre-push")
    (root / "release").mkdir()
    shutil.copy2(policy(root), root / "release/public-repository.toml")
    (root / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
    (root / ".git/hooks/pre-push").symlink_to(root / "scripts/pre-push")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    result = git(
        root,
        "push",
        str(remote),
        f"{main}:refs/heads/public",
        "private:refs/heads/private",
        check=False,
    )
    assert (
        result.returncode != 0
        and "push contains local workflow records" in result.stdout
    )
    assert not git(remote, "for-each-ref").stdout
    result = git(root, "push", str(remote), f"{main}:refs/heads/public", check=False)
    assert result.returncode == 0, result.stderr
    assert git(remote, "rev-parse", "refs/heads/public").stdout.strip() == clean


@pytest.mark.toolchain
def test_real_commit_hook_rejects_force_added_record(tmp_path: Path) -> None:
    root = host(tmp_path)
    policy(root)
    config = root / ".pre-commit-config.yaml"
    config.write_text(
        "repos:\n- repo: local\n  hooks:\n  - id: local-records\n"
        "    name: local records\n    language: system\n"
        f"    entry: {sys.executable} {CHECKER} staged --root {root} "
        f"--policy {root / '.git/policy.toml'}\n"
        "    pass_filenames: false\n    always_run: true\n"
    )
    subprocess.run(
        [sys.executable, "-m", "pre_commit", "install"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    target = root / "plans/example/state.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("private\n")
    git(root, "add", ".pre-commit-config.yaml")
    git(root, "add", "-f", "plans/example/state.yaml")
    before = git(root, "rev-parse", "HEAD").stdout
    result = git(root, "commit", "-qm", "must refuse", check=False)
    assert result.returncode != 0 and "must stay local" in result.stdout + result.stderr
    assert git(root, "rev-parse", "HEAD").stdout == before
    assert target.read_text() == "private\n"


@pytest.mark.toolchain
def test_setup_preserves_records_and_refuses_unfamiliar_push_hook(
    tmp_path: Path,
) -> None:
    root = host(tmp_path)
    for relative in [
        "scripts/install-repository-hooks.py",
        "scripts/pre-push",
        "release/public-repository.toml",
    ]:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    parsed = tomllib.loads((root / "release/public-repository.toml").read_text())
    for template in parsed["local_records"]["scaffolds"].values():
        target = root / template
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / template, target)
    (root / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
    (root / ".pre-commit-config.yaml").write_text(
        "default_install_hook_types: [pre-commit, commit-msg]\nrepos: []\n"
    )
    hook = root / ".git/hooks/pre-push"
    hook.write_text("existing user hook\n")
    command = [sys.executable, str(root / "scripts/install-repository-hooks.py")]
    refused = subprocess.run(command, capture_output=True, text=True, check=False)
    assert refused.returncode != 0 and hook.read_text() == "existing user hook\n"
    hook.unlink()
    existing = root / "plans/friction-log.md"
    existing.parent.mkdir()
    existing.write_text("preserve local observations\n")
    for _ in range(2):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    assert existing.read_text() == "preserve local observations\n"
    assert hook.resolve() == root / "scripts/pre-push"
    for relative in parsed["local_records"]["scaffolds"]:
        assert (root / relative).is_file()
        assert git(root, "check-ignore", relative).returncode == 0
    assert os.access(root / ".git/hooks/pre-commit", os.X_OK)
    assert os.access(root / ".git/hooks/commit-msg", os.X_OK)


@pytest.mark.toolchain
def test_fresh_public_snapshot_builds_and_tests_without_workflow_history(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public-source"
    members = (ROOT / "release/manifests/repository.txt").read_text().splitlines()
    for relative in members:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    for relative in [
        "plans",
        "docs/features",
        "docs/proposals",
        "docs/gate-trajectories",
    ]:
        assert not (root / relative).exists()
    git(root, "init", "-q")
    git(root, "add", "--force", "--", *members)
    clean_env = os.environ.copy()
    clean_env.pop("PYTHONPATH", None)
    clean_env["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pathlib, heddle; "
            "assert pathlib.Path(heddle.__file__).resolve()"
            ".is_relative_to(pathlib.Path.cwd())",
        ],
        cwd=root,
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for command in [
        ["scripts/check-public-repository.py", "repository"],
        ["scripts/check-public-repository.py", "references"],
        ["scripts/check-public-repository.py", "surfaces"],
        ["scripts/check-public-repository.py", "tests"],
        [
            "-m",
            "tests.proof_runner",
            "tests/kernel/test_project_config.py",
            "tests/runtime/test_schemas.py",
        ],
        [
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(tmp_path / "dist"),
        ],
    ]:
        result = subprocess.run(
            [sys.executable, *command],
            cwd=root,
            env=clean_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    wheel = next((tmp_path / "dist").glob("*.whl"))
    sdist = next((tmp_path / "dist").glob("*.tar.gz"))
    for arguments in [
        ["compare-wheel", "--left", str(wheel), "--right", str(wheel)],
        ["compare-sdist", "--sdist", str(sdist), "--source", str(root)],
    ]:
        result = subprocess.run(
            [sys.executable, "scripts/check-public-repository.py", *arguments],
            cwd=root,
            env=clean_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(wheel) as archive:
        assert not any(
            name.startswith(("plans/", "docs/", ".heddle/"))
            for name in archive.namelist()
        )
        for name in [
            "feature-index.scaffold.yaml",
            "friction-log.scaffold.md",
            "gate-effectiveness.scaffold.md",
        ]:
            assert archive.read(f"heddle/resources/{name}")
