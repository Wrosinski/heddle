"""Public repository, distribution, and publication-boundary contracts."""

from __future__ import annotations

import base64
import csv
import fnmatch
import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tomllib
import warnings
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "release" / "public-repository.toml"
REPOSITORY_MANIFEST = REPO_ROOT / "release" / "manifests" / "repository.txt"
CHECKER = REPO_ROOT / "scripts" / "check-public-repository.py"


def _policy() -> dict[str, object]:
    assert POLICY.is_file(), "FAIL public repository policy is missing"
    return tomllib.loads(POLICY.read_text())


def _manifest(path: Path) -> list[str]:
    assert path.is_file(), f"FAIL manifest is missing: {path.relative_to(REPO_ROOT)}"
    members = [line for line in path.read_text().splitlines() if line]
    assert members == sorted(set(members)), (
        f"FAIL manifest is not sorted and unique: {path}"
    )
    return members


def _run_checker(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    assert CHECKER.is_file(), "FAIL public repository checker is missing"
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )


def _init_repository(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Synthetic Contributor"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "contributor@example.invalid"],
        cwd=root,
        check=True,
    )


def _wheel_members(
    package_bytes: bytes = b'__version__ = "0.0.1"\n',
) -> dict[str, bytes]:
    members = {
        "heddle/__init__.py": package_bytes,
        "heddle-0.0.1.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: heddle\nVersion: 0.0.1\n\n"
        ),
        "heddle-0.0.1.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\n"
            b"Tag: py3-none-any\n\n"
        ),
        "heddle-0.0.1.dist-info/entry_points.txt": (
            b"[console_scripts]\nheddle = heddle.cli:main\n"
        ),
    }
    rows: list[list[str]] = []
    for name, content in sorted(members.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        rows.append([name, f"sha256={digest.decode()}", str(len(content))])
    record = "heddle-0.0.1.dist-info/RECORD"
    rows.append([record, "", ""])
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    members[record] = buffer.getvalue().encode()
    return members


def _write_wheel(
    path: Path,
    members: dict[str, bytes],
    *,
    reverse: bool = False,
    compression: int = zipfile.ZIP_DEFLATED,
    timestamp: tuple[int, int, int, int, int, int] = (2026, 1, 1, 0, 0, 0),
    mode_overrides: dict[str, int] | None = None,
    duplicate: str | None = None,
) -> None:
    names = sorted(members, reverse=reverse)
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.compress_type = compression
            mode = (mode_overrides or {}).get(name, 0o100644)
            info.external_attr = mode << 16
            archive.writestr(info, members[name])
        if duplicate is not None:
            info = zipfile.ZipInfo(duplicate, date_time=timestamp)
            info.compress_type = compression
            info.external_attr = 0o100644 << 16
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(info, members[duplicate])


def _write_artifact_policy(
    root: Path,
    *,
    sdist_members: tuple[str, ...],
    wheel_members: tuple[str, ...],
    directory: str = "release/manifests",
) -> Path:
    manifest_root = root / directory
    manifest_root.mkdir(parents=True, exist_ok=True)
    sdist_relative = f"{directory}/source-members.txt"
    wheel_relative = f"{directory}/wheel-members.txt"
    (root / sdist_relative).write_text("\n".join(sorted(sdist_members)) + "\n")
    (root / wheel_relative).write_text("\n".join(sorted(wheel_members)) + "\n")
    policy = root / "public-repository.toml"
    policy.write_text(
        "schema_version = 1\n\n"
        "[artifacts.sdist]\n"
        f'manifest = "{sdist_relative}"\n'
        'generated = ["PKG-INFO"]\n\n'
        "[artifacts.wheel]\n"
        f'manifest = "{wheel_relative}"\n'
    )
    return policy


# AC-1 survivor/discriminator: the public categories are stable data and the
# proposed repository manifest excludes every private-record category.
def test_policy_covers_repository_and_distribution_categories() -> None:
    policy = _policy()
    assert policy["schema_version"] == 1
    repository = policy["repository"]
    artifacts = policy["artifacts"]
    assert isinstance(repository, dict) and isinstance(artifacts, dict)
    assert set(repository) == {
        "manifest",
        "private_globs",
        "exceptions",
    }
    assert set(artifacts) == {"sdist", "wheel"}
    assert artifacts["sdist"] == {
        "manifest": "release/manifests/sdist.txt",
        "generated": ["PKG-INFO"],
    }
    assert artifacts["wheel"] == {"manifest": "release/manifests/wheel.txt"}

    members = _manifest(REPOSITORY_MANIFEST)
    private_globs = repository["private_globs"]
    exceptions = set(repository["exceptions"])
    assert isinstance(private_globs, list)
    for member in members:
        assert member in exceptions or not any(
            fnmatch.fnmatchcase(member, pattern) for pattern in private_globs
        ), f"FAIL proposed public manifest includes a private-record category: {member}"


# AC-1/AC-6 discriminator: future repository-local workflow records are ignored.
def test_private_workflow_records_are_ignored(tmp_path: Path) -> None:
    ignored = (
        "docs/proposals/local-note.md",
        "docs/features/runtime/local-work.md",
        "docs/features/runtime/local-work.yaml",
        "docs/features/runtime/local-work.json",
        "plans/local-work/state.yaml",
        "docs/gate-trajectories/local-run/result.json",
        "docs/features/_descriptions.yaml",
        "plans/gate-effectiveness.md",
        "plans/friction-log.md",
        ".heddle/intake/local-work.yaml",
    )
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_bytes((REPO_ROOT / ".gitignore").read_bytes())
    for path in ignored:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("private fixture\n")
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--verbose", path],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"FAIL private workflow path is not ignored: {path}\n"
            + result.stdout
            + result.stderr
        )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    staged = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    for path in ignored:
        assert path not in staged, f"FAIL private workflow path was staged: {path}"
    assert staged == [".gitignore"]


# AC-1 discriminator: ignore rules cannot make a force-added private member
# acceptable to the public repository checker.
def test_force_added_private_category_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _init_repository(root)
    private = root / "plans" / "local-work" / "state.yaml"
    private.parent.mkdir(parents=True)
    private.write_text("schema: example.private/v1\n")
    subprocess.run(["git", "add", "-f", "."], cwd=root, check=True)
    result = _run_checker("repository", "--root", str(root), "--policy", str(POLICY))
    assert result.returncode == 1
    assert "private-record category" in result.stdout


# AC-2 discriminator: retained public files cannot link to a category that the
# final repository policy removes.
def test_retained_tree_has_no_private_consumer_links() -> None:
    result = _run_checker("references")
    assert result.returncode == 0, result.stdout + result.stderr


# AC-2 negative fixture: a current public document may not point back into a
# private-record category even when the referenced file is absent.
def test_reference_policy_rejects_private_category_link(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _init_repository(root)
    (root / "README.md").write_text("See docs/proposals/local-note.md.\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    result = _run_checker("references", "--root", str(root), "--policy", str(POLICY))
    assert result.returncode == 1
    assert "private-category reference" in result.stdout


# AC-2 negative fixture: retained source code cannot keep an exact dependency
# on a deleted private record after the public manifest is sealed.
def test_reference_policy_rejects_retained_source_consumer(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _init_repository(root)
    source = root / "heddle" / "consumer.py"
    manifest = root / "release" / "manifests" / "repository.txt"
    source.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    source.write_text("# Contract: docs/proposals/private-contract.md\n")
    manifest.write_text("heddle/consumer.py\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)

    result = _run_checker("references", "--root", str(root), "--policy", str(POLICY))

    assert result.returncode == 1
    assert "heddle/consumer.py:1" in result.stdout


def test_reference_policy_rejects_dangling_bare_markdown_consumer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _init_repository(root)
    source = root / "heddle" / "consumer.py"
    manifest = root / "release" / "manifests" / "repository.txt"
    source.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    source.write_text("# Historical rationale: building-heddle.md\n")
    manifest.write_text("heddle/consumer.py\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)

    result = _run_checker("references", "--root", str(root), "--policy", str(POLICY))

    assert result.returncode == 1
    assert "heddle/consumer.py:1" in result.stdout


# AC-3 survivor pin: cleanup preserves the current product defaults rather than
# deriving expectations from the registry under test.
def test_cleanup_preserves_public_runtime_defaults() -> None:
    from heddle.driver.sessions import DEFAULT_PHASE_SESSION_MODEL, PHASE_SESSION_EFFORT
    from heddle.gate.cli import CLI_FALLBACKS
    from heddle.kernel.project_config import DEFAULT_AUTOPILOT

    assert DEFAULT_PHASE_SESSION_MODEL == "claude-fable-5-1"
    assert PHASE_SESSION_EFFORT == "high"
    assert (
        CLI_FALLBACKS["claude"].model,
        CLI_FALLBACKS["claude"].reasoning_effort,
    ) == (
        "claude-opus-5",
        "xhigh",
    )
    assert (CLI_FALLBACKS["codex"].model, CLI_FALLBACKS["codex"].reasoning_effort) == (
        "gpt-5.6-sol",
        "high",
    )
    assert DEFAULT_AUTOPILOT.flow == "hitl"
    assert DEFAULT_AUTOPILOT.max_turns is None
    assert DEFAULT_AUTOPILOT.max_budget_usd == 150.0


# AC-3 discriminator: retained docs, patterns, resources, source rationale, and
# contributor guidance must satisfy the policy's timeless-surface rules.
def test_retained_public_surfaces_are_self_contained() -> None:
    result = _run_checker("surfaces")
    assert result.returncode == 0, result.stdout + result.stderr


# AC-3 negative fixtures: private provenance is rejected in retained documents,
# source docstrings, and packaged briefings.
@pytest.mark.parametrize(
    ("relative", "body"),
    (
        ("docs/patterns/example.md", "Milestone M3 review finding SR-I2.\n"),
        ("heddle/example.py", '"""Rationale inherited from D42."""\n'),
        ("heddle/resources/example.briefing.md", "Apply the D37 verification rider.\n"),
    ),
    ids=("public-doc", "source-docstring", "packaged-briefing"),
)
def test_public_surface_policy_rejects_opaque_history_fixture(
    tmp_path: Path, relative: str, body: str
) -> None:
    root = tmp_path / "repository"
    _init_repository(root)
    pattern = root / relative
    manifest = root / "release" / "manifests" / "repository.txt"
    pattern.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    pattern.write_text(body)
    manifest.write_text(f"{relative}\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)

    result = _run_checker("surfaces", "--root", str(root), "--policy", str(POLICY))

    assert result.returncode == 1
    assert f"{relative}:1" in result.stdout


# AC-4 discriminator: the public test tree and its encoded node inventory are
# checked by the same neutral policy without embedding migration-specific terms.
def test_public_test_corpus_passes_contextual_policy() -> None:
    result = _run_checker("tests")
    assert result.returncode == 0, result.stdout + result.stderr


# AC-4 negative fixture: generic public test policy rejects source provenance
# and historical test organization without migration-specific deny lists.
@pytest.mark.parametrize(
    ("filename", "body"),
    [
        (
            "test_example.py",
            '"""Regression copied from a private source_feature record."""\n\n'
            "def test_example():\n    assert 1 + 1 == 2\n",
        ),
        (
            "test_m3_example.py",
            "def test_example():\n    assert 1 + 1 == 2\n",
        ),
        (
            "test_example.py",
            "def test_d42_example():\n    assert 1 + 1 == 2\n",
        ),
        (
            "test_example.py",
            "# Phase 9 source-era organization\n"
            "def test_example():\n    assert 1 + 1 == 2\n",
        ),
        (
            "test_example.py",
            "# Commands landed in M2/M3/M4 during private development\n"
            "def test_example():\n    assert 1 + 1 == 2\n",
        ),
        ("fixtures/example/README.md", "Created for milestone M3.\n"),
        ("fixtures/.gitattributes", "# Added for decision D29\n* text\n"),
    ],
    ids=(
        "source-provenance",
        "historical-organization",
        "historical-node",
        "historical-comment",
        "historical-milestone-prose",
        "fixture-readme",
        "fixture-attributes",
    ),
)
def test_public_test_policy_rejects_provenance_fixture(
    tmp_path: Path, filename: str, body: str
) -> None:
    root = tmp_path / "repository"
    _init_repository(root)
    test = root / "tests" / filename
    test.parent.mkdir(parents=True)
    test.write_text(body)
    subprocess.run(["git", "add", "tests"], cwd=root, check=True)
    result = _run_checker("tests", "--root", str(root), "--policy", str(POLICY))
    assert result.returncode == 1
    assert "test provenance" in result.stdout


def test_public_test_policy_checks_decoded_inventory(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _init_repository(root)
    inventory = root / "tests" / "fixtures" / "pytest-inventory-current.txt.gz.b64"
    inventory.parent.mkdir(parents=True)
    nodes = b"tests/test_example.py::test_d42_example\n"
    inventory.write_bytes(base64.b64encode(gzip.compress(nodes, mtime=0)))
    subprocess.run(["git", "add", "tests"], cwd=root, check=True)

    result = _run_checker("tests", "--root", str(root), "--policy", str(POLICY))

    assert result.returncode == 1
    assert "tests/fixtures/pytest-inventory-current.txt.gz.b64" in result.stdout


# AC-5 discriminator: after deletion and public-surface completion, the exact
# Git index must equal the reviewed repository manifest.
def test_repository_manifest_matches_tracked_tree() -> None:
    result = _run_checker("repository")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mismatch", ["extra", "missing"])
def test_repository_manifest_rejects_extra_or_missing_member(
    tmp_path: Path, mismatch: str
) -> None:
    root = tmp_path / "repository"
    _init_repository(root)
    (root / "README.md").write_text("# Synthetic repository\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'synthetic'\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    manifest = tmp_path / "repository.txt"
    members = ["README.md", "pyproject.toml"]
    if mismatch == "extra":
        members.append("missing.txt")
    else:
        members.remove("README.md")
    manifest.write_text("\n".join(sorted(members)) + "\n")
    result = _run_checker(
        "repository",
        "--root",
        str(root),
        "--policy",
        str(POLICY),
        "--manifest",
        str(manifest),
    )
    assert result.returncode == 1
    assert mismatch in result.stdout


# AC-5: deleting completed work must retain a healthy minimum public scaffold.
def test_empty_public_feature_scaffold_remains_searchable(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    (root / "docs" / "features").mkdir(parents=True)
    (root / "docs" / "features" / "_descriptions.yaml").write_text("{}\n")
    (root / "plans").mkdir()
    (root / "plans" / "gate-effectiveness.md").write_text("# Gate effectiveness\n")
    (root / "plans" / "friction-log.md").write_text("# Friction log\n")
    (root / ".heddle.yaml").write_text(
        "layout:\n"
        "  specs: docs/features\n"
        "  plans: plans\n"
        "  prompts: prompts\n"
        "  schemas: schemas\n"
        "  tests: tests\n"
        "  source: src\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "heddle", "search", "anything", "--json"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["data"]["hits"] == []


# AC-6 discriminator: source metadata and the public landing page expose one
# consistent identity and operating posture.
def test_public_identity_and_responsibility_are_consistent() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]
    readme = (REPO_ROOT / "README.md").read_text()
    license_text = (REPO_ROOT / "LICENSE").read_text()
    assert metadata["name"] == "heddle"
    assert metadata["version"] == "0.0.1"
    assert metadata["requires-python"] == ">=3.13"
    assert metadata["authors"] == [
        {"name": "Wojtek Rosinski", "email": "rosinskiws@gmail.com"}
    ]
    assert metadata["license"] == {"text": "MIT"}
    assert metadata["urls"]["Repository"] == "https://github.com/Wrosinski/heddle"
    assert metadata["urls"]["Issues"] == "https://github.com/Wrosinski/heddle/issues"
    for statement in (
        "flexibility with formal verification",
        "Python 3.13",
        "Codex",
        "Claude Code",
        "no support",
        "your own responsibility",
    ):
        assert statement.lower() in readme.lower(), f"FAIL README omits: {statement}"
    assert "MIT License" in license_text and "Wojtek Rosinski" in license_text
    for path in ("CONTRIBUTING.md", "SECURITY.md", ".github/workflows/quality.yml"):
        assert (REPO_ROOT / path).is_file(), f"FAIL public surface is missing: {path}"
    assert (REPO_ROOT / ".pre-commit-config.yaml").is_file()
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text()
    security = (REPO_ROOT / "SECURITY.md").read_text()
    assert "configuration" in readme.lower() and "override" in readme.lower()
    assert "contribut" in contributing.lower() and "python 3.13" in contributing.lower()
    assert "private vulnerability reporting" in security.lower()
    assert "not a support channel" in security.lower()
    admission_route = (
        "heddle orient\n",
        "heddle feature prepare <slug> --area <area> --from-file <intake.yaml>",
        "heddle feature policy <slug> --from-file <policy.yaml> "
        "--expect-revision <revision>",
        "heddle feature start <slug> --area <area> --expect-revision <revision>",
        "heddle orient --feature <slug>",
    )
    positions = [readme.index(step) for step in admission_route]
    assert positions == sorted(positions), (
        "FAIL README documents an illegal admission route"
    )
    assert "Owner approval of the complete policy is required" in readme


# AC-7 positive fixture: container ordering, compression, and timestamps are
# deliberately outside semantic wheel equivalence.
def test_semantic_wheel_equivalence_allows_container_variation(tmp_path: Path) -> None:
    left = tmp_path / "left.whl"
    right = tmp_path / "right.whl"
    members = _wheel_members()
    _write_wheel(left, members)
    _write_wheel(
        right,
        members,
        reverse=True,
        compression=zipfile.ZIP_STORED,
        timestamp=(2027, 2, 2, 2, 2, 2),
    )
    policy = _write_artifact_policy(
        tmp_path,
        sdist_members=(),
        wheel_members=tuple(members),
    )
    result = _run_checker(
        "compare-wheel",
        "--left",
        str(left),
        "--right",
        str(right),
        "--root",
        str(tmp_path),
        "--policy",
        str(policy),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "mismatch", ["member", "duplicate", "mode", "bytes", "metadata", "record"]
)
def test_semantic_wheel_equivalence_rejects_material_mismatch(
    tmp_path: Path, mismatch: str
) -> None:
    left = tmp_path / "left.whl"
    right = tmp_path / "right.whl"
    original = _wheel_members()
    changed = dict(original)
    modes: dict[str, int] = {}
    duplicate: str | None = None
    if mismatch == "member":
        changed = _wheel_members()
        changed["heddle/extra.py"] = b"value = 1\n"
        changed = _wheel_members_with_record(changed)
    elif mismatch == "duplicate":
        duplicate = "heddle/__init__.py"
    elif mismatch == "mode":
        modes["heddle/__init__.py"] = 0o100755
    elif mismatch == "bytes":
        changed = _wheel_members(b'__version__ = "0.0.2"\n')
    elif mismatch == "metadata":
        changed["heddle-0.0.1.dist-info/METADATA"] = (
            b"Metadata-Version: 2.4\nName: heddle\nVersion: 0.0.2\n\n"
        )
        changed = _wheel_members_with_record(changed)
    else:
        changed["heddle-0.0.1.dist-info/RECORD"] = b"invalid,sha256=broken,1\n"
    _write_wheel(left, original)
    _write_wheel(right, changed, mode_overrides=modes, duplicate=duplicate)
    policy = _write_artifact_policy(
        tmp_path,
        sdist_members=(),
        wheel_members=tuple(original),
    )
    result = _run_checker(
        "compare-wheel",
        "--left",
        str(left),
        "--right",
        str(right),
        "--root",
        str(tmp_path),
        "--policy",
        str(policy),
    )
    assert result.returncode == 1
    assert mismatch in result.stdout.lower()


def _wheel_members_with_record(members: dict[str, bytes]) -> dict[str, bytes]:
    without_record = {
        name: content
        for name, content in members.items()
        if not name.endswith("/RECORD")
    }
    rows: list[list[str]] = []
    for name, content in sorted(without_record.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        rows.append([name, f"sha256={digest.decode()}", str(len(content))])
    record = "heddle-0.0.1.dist-info/RECORD"
    rows.append([record, "", ""])
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    without_record[record] = buffer.getvalue().encode()
    return without_record


def _write_sdist(
    path: Path,
    source: Path,
    members: tuple[str, ...],
    *,
    duplicate: str | None = None,
    mode_overrides: dict[str, int] | None = None,
    metadata_overrides: dict[str, str] | None = None,
) -> None:
    pyproject = source / "pyproject.toml"
    if not pyproject.exists():
        pyproject.write_text(
            "[project]\n"
            'name = "heddle"\n'
            'version = "0.0.1"\n'
            'description = "Synthetic Heddle package"\n'
            'requires-python = ">=3.13"\n'
            'authors = [{name = "Wojtek Rosinski", email = '
            '"rosinskiws@gmail.com"}]\n'
            'license = {text = "MIT"}\n'
            "[project.urls]\n"
            'Repository = "https://github.com/Wrosinski/heddle"\n'
            'Issues = "https://github.com/Wrosinski/heddle/issues"\n'
        )
    metadata = {
        "Name": "heddle",
        "Version": "0.0.1",
        "Summary": "Synthetic Heddle package",
        "Requires-Python": ">=3.13",
        "License": "MIT",
        "Author-email": "Wojtek Rosinski <rosinskiws@gmail.com>",
    }
    metadata.update(metadata_overrides or {})
    package_info = (
        "Metadata-Version: 2.5\n"
        + "".join(f"{key}: {value}\n" for key, value in metadata.items())
        + "Project-URL: Repository, https://github.com/Wrosinski/heddle\n"
        + "Project-URL: Issues, https://github.com/Wrosinski/heddle/issues\n\n"
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            override = (mode_overrides or {}).get(member)

            def set_mode(
                info: tarfile.TarInfo, override: int | None = override
            ) -> tarfile.TarInfo:
                if override is not None:
                    info.mode = override
                return info

            archive.add(
                source / member,
                arcname=f"heddle-0.0.1/{member}",
                filter=set_mode,
            )
        info = tarfile.TarInfo("heddle-0.0.1/PKG-INFO")
        info.mode = 0o644
        info.size = len(package_info)
        archive.addfile(info, io.BytesIO(package_info))
        if duplicate is not None:
            archive.add(
                source / duplicate,
                arcname=f"heddle-0.0.1/{duplicate}",
            )


def test_sdist_equivalence_rejects_source_mismatch(tmp_path: Path) -> None:
    for mismatch in ("bytes", "mode", "metadata", "duplicate"):
        root = tmp_path / mismatch
        source = root / "source"
        package = source / "heddle"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text('__version__ = "0.0.1"\n')
        sdist = root / "heddle-0.0.1.tar.gz"
        _write_sdist(
            sdist,
            source,
            ("heddle/__init__.py",),
            duplicate="heddle/__init__.py" if mismatch == "duplicate" else None,
            mode_overrides=(
                {"heddle/__init__.py": 0o755} if mismatch == "mode" else None
            ),
            metadata_overrides=(
                {"Version": "0.0.2"} if mismatch == "metadata" else None
            ),
        )
        policy = _write_artifact_policy(
            source,
            sdist_members=("heddle/__init__.py",),
            wheel_members=(),
        )
        if mismatch == "bytes":
            (package / "__init__.py").write_text('__version__ = "0.0.2"\n')
        result = _run_checker(
            "compare-sdist",
            "--sdist",
            str(sdist),
            "--source",
            str(source),
            "--root",
            str(source),
            "--policy",
            str(policy),
        )
        assert result.returncode == 1
        assert mismatch in result.stdout.lower()


@pytest.mark.parametrize("mismatch", ["missing", "extra"])
def test_sdist_equivalence_rejects_member_mismatch(
    tmp_path: Path, mismatch: str
) -> None:
    source = tmp_path / "source"
    package = source / "heddle"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.0.1"\n')
    (package / "extra.py").write_text("value = 1\n")
    policy = _write_artifact_policy(
        source,
        sdist_members=("heddle/__init__.py",),
        wheel_members=(),
    )
    members = () if mismatch == "missing" else ("heddle/__init__.py", "heddle/extra.py")
    sdist = tmp_path / "heddle-0.0.1.tar.gz"
    _write_sdist(sdist, source, members)
    result = _run_checker(
        "compare-sdist",
        "--sdist",
        str(sdist),
        "--source",
        str(source),
        "--root",
        str(source),
        "--policy",
        str(policy),
    )
    assert result.returncode == 1
    assert mismatch in result.stdout.lower()


def test_artifact_comparators_resolve_relocated_manifests_from_policy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    package = source / "heddle"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.0.1"\n')
    wheel_members = _wheel_members()
    left = tmp_path / "left.whl"
    right = tmp_path / "right.whl"
    _write_wheel(left, wheel_members)
    _write_wheel(right, wheel_members, reverse=True)
    sdist = tmp_path / "heddle-0.0.1.tar.gz"
    _write_sdist(sdist, source, ("heddle/__init__.py",))
    policy = _write_artifact_policy(
        source,
        sdist_members=("heddle/__init__.py",),
        wheel_members=tuple(wheel_members),
        directory="policy/relocated",
    )

    sdist_result = _run_checker(
        "compare-sdist",
        "--sdist",
        str(sdist),
        "--source",
        str(source),
        "--root",
        str(source),
        "--policy",
        str(policy),
    )
    wheel_result = _run_checker(
        "compare-wheel",
        "--left",
        str(left),
        "--right",
        str(right),
        "--root",
        str(source),
        "--policy",
        str(policy),
    )

    assert sdist_result.returncode == 0, sdist_result.stdout + sdist_result.stderr
    assert wheel_result.returncode == 0, wheel_result.stdout + wheel_result.stderr


# AC-8 discriminator: the release qualifier is local, commit-bound, and contains
# no operation that can mutate a remote, release, tag, or package index.
def test_final_qualification_is_commit_bound_and_local() -> None:
    qualifier = REPO_ROOT / "scripts" / "qualify-public-repository.sh"
    assert qualifier.is_file(), "FAIL public repository qualifier is missing"
    text = qualifier.read_text()
    for required in ("git diff --quiet", "git rev-parse HEAD", "git archive", "mktemp"):
        assert required in text, f"FAIL qualifier omits commit-bound step: {required}"
    for prohibited in ("git push", "git tag", "gh ", "twine upload", "uv publish"):
        assert prohibited not in text, (
            f"FAIL qualifier contains external mutation: {prohibited}"
        )
    for required in ("REBUILT_WHEEL", "direct_url.json", "hashlib.sha256"):
        assert required in text, f"FAIL qualifier omits rebuilt-wheel proof: {required}"
    assert "for check in repository references surfaces tests" in text
    assert text.count("check-public-repository.py") == 3
    assert "compare-wheel" in text and "--policy" in text
    assert "import tarfile" not in text and "import zipfile" not in text


# AC-7/AC-8 toolchain contract. The qualifier builds in disposable locations,
# compares all three manifests, rebuilds a wheel from the sdist, and exercises
# the installed CLI without importing from this checkout.
@pytest.mark.toolchain
def test_public_artifacts_are_reproducible_and_installable() -> None:
    qualifier = REPO_ROOT / "scripts" / "qualify-public-repository.sh"
    assert qualifier.is_file(), "FAIL public repository qualifier is missing"
    result = subprocess.run(
        [str(qualifier)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
