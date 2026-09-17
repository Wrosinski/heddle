"""Controlled child pytest projects for the repository execution-policy tests."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "tests" / "pytest_policy.py"
PROOF_RUNNER = REPO_ROOT / "tests" / "proof_runner.py"

MARKERS = """\
    acceptance: acceptance witness
    live: external sentinel
    e2e: workflow sentinel
    toolchain: infrastructure sentinel
    slow: cost sentinel
    scenario: ordinary unbanded scenario
"""

MIXED = """\
import pytest
from pathlib import Path

def record(kind, name):
    with Path('events').open('a') as stream:
        stream.write(kind + ':' + name + '\\n')

@pytest.fixture(autouse=True)
def effect(request):
    record('setup', request.node.name)
    yield
    record('teardown', request.node.name)

@pytest.fixture
def provider(request):
    record('provider', request.node.name)

@pytest.mark.acceptance
def test_fast(): record('call', 'test_fast')
@pytest.mark.scenario
def test_milestone(): record('call', 'test_milestone')
@pytest.mark.slow
def test_slow(): record('call', 'test_slow')
@pytest.mark.toolchain
def test_tool(): record('call', 'test_tool')
@pytest.mark.e2e
@pytest.mark.scenario
def test_local(provider): record('call', 'test_local')
@pytest.mark.e2e
@pytest.mark.toolchain
@pytest.mark.slow
def test_local_build(provider): record('call', 'test_local_build')
@pytest.mark.live
@pytest.mark.scenario
def test_external(provider): record('call', 'test_external')
@pytest.mark.live
@pytest.mark.e2e
def test_external_journey(provider): record('call', 'test_external_journey')
"""


@dataclass
class Project:
    root: Path

    def run(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return self._run("pytest", "-p", "tests.pytest_policy", *args, env=env)

    def prove(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return self._run("tests.proof_runner", *args, env=env)

    def _run(
        self, module: str, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        (self.root / "events").unlink(missing_ok=True)
        environment = {
            "PATH": os.defpath,
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        environment.update(env or {})
        return subprocess.run(
            [sys.executable, "-m", module, "-q", *args],
            cwd=self.root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            check=False,
        )

    def events(self, kind: str | None = None) -> list[str]:
        path = self.root / "events"
        if not path.exists():
            return []
        lines = path.read_text().splitlines()
        if kind is None:
            return lines
        return [line.partition(":")[2] for line in lines if line.startswith(kind + ":")]


def project(tmp_path: Path, source: str = MIXED, *, addopts: str = "") -> Project:
    root = tmp_path / "child"
    root.mkdir()
    (root / "pytest.ini").write_text(
        "[pytest]\naddopts = --strict-markers --strict-config "
        + addopts
        + "\nmarkers =\n"
        + MARKERS,
        encoding="utf-8",
    )
    (root / "test_cases.py").write_text(source, encoding="utf-8")
    return Project(root)


def require_failure(result: subprocess.CompletedProcess[str], reason: str) -> None:
    assert result.returncode != 0, (
        f"FAIL: incomplete/forbidden execution passed\n{result.stdout}"
    )
    assert reason in result.stdout.lower(), result.stdout
    assert "importerror" not in result.stdout.lower(), result.stdout
    assert "unrecognized arguments" not in result.stdout.lower(), result.stdout
