from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build-pinned-supervisor.py"


def _run(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=script.parent.parent,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path / "source"
    script = root / "scripts" / SCRIPT.name
    script.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, script)
    (root / "input.txt").write_text("Synthetic source.\n")

    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout

    git("init", "-q")
    git("add", "input.txt")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "Create synthetic source",
    )
    revision = git("rev-parse", "HEAD").decode().strip()
    version = ".".join(str(part) for part in sys.version_info[:3])
    manifest = {
        "schema": "heddle.pinned-supervisor/v1",
        "name": "synthetic-supervisor",
        "source": {
            "git_revision": revision,
            "archive_sha256": hashlib.sha256(git("archive", revision)).hexdigest(),
            "source_date_epoch": 1,
        },
        "build": {
            "tool": sys.executable,
            "version": version,
            "arguments": ["--version"],
        },
        "artifact": {"filename": "fixture.whl", "sha256": "a" * 64},
        "runtime": {"python_version": version, "distributions": {"fixture": "1.0"}},
        "qualification": {
            "help_arguments": ["help", "--json"],
            "require_external_cwd": True,
        },
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return script, path, manifest


def test_manifest_checks_self_contained_source_and_rejects_hash_drift(
    source: tuple[Path, Path, dict[str, Any]],
) -> None:
    script, path, manifest = source
    result = _run(script, "--manifest", str(path), "--check")
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "checked"
    assert receipt["manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt["source_revision"] == manifest["source"]["git_revision"]

    manifest["source"]["archive_sha256"] = "b" * 64
    path.write_text(yaml.safe_dump(manifest))
    result = _run(script, "--manifest", str(path), "--check")
    assert result.returncode == 2
    assert "source archive hash mismatch" in result.stderr


def test_manifest_rejects_unknown_fields_without_creating_destination(
    source: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    script, path, manifest = source
    manifest["artifact"]["unexpected"] = True
    path.write_text(yaml.safe_dump(manifest))
    destination = tmp_path / "supervisor"
    result = _run(script, "--manifest", str(path), "--destination", str(destination))
    assert result.returncode == 2
    assert "artifact keys differ" in result.stderr
    assert not destination.exists()


def test_manifest_must_be_explicit() -> None:
    result = _run(SCRIPT, "--check")
    assert result.returncode == 2
    assert "--manifest" in result.stderr
