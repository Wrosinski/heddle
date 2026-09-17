"""
scaffold-stage scaffolding for strict-xfail and bounded prompt cleanup (write).
"""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "heddle/resources/hooks/check-skip-only-test-scaffolding.py"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _repo(tmp_path: Path, body: str) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "scaffold@example.invalid")
    _git(tmp_path, "config", "user.name", "Scaffold")
    path = tmp_path / "tests/test_future.py"
    path.parent.mkdir()
    path.write_text(body, encoding="utf-8")
    _git(tmp_path, "add", str(path))
    return path


def _run_hook(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(HOOK)],
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


@pytest.mark.toolchain
def test_ac10_hook_accepts_strict_xfail_with_executable_assertion(
    tmp_path: Path,
) -> None:
    """AC-10 red discriminator: strict XFAIL→XPASS→PASS lifecycle is real."""
    test_path = _repo(
        tmp_path,
        "import pytest\n\n"
        "@pytest.mark." + "xfail(strict=True, reason='future behavior')\n"
        "def test_future_behavior():\n"
        "    import future_behavior\n"
        "    observed = future_behavior.run()\n"
        "    assert observed == 'ready'\n",
    )
    (tmp_path / "future_behavior.py").write_text("def run(): return 'old'\n")
    conftest = tmp_path / "conftest.py"
    conftest.write_text("", encoding="utf-8")

    result = _run_hook(tmp_path)
    assert result.returncode == 0, f"FAIL AC-10: {result.stdout}"

    xfail = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert xfail.returncode == 0 and "xfailed" in xfail.stdout

    conftest.write_text(
        "import future_behavior\n\nfuture_behavior.run = lambda: 'ready'\n",
        encoding="utf-8",
    )
    xpass = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--cache-clear"],
        cwd=tmp_path,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert xpass.returncode == 1 and "XPASS" in xpass.stdout

    test_path.write_text(
        test_path.read_text(encoding="utf-8").replace(
            "@pytest.mark.xfail(strict=True, reason='future behavior')\n", ""
        ),
        encoding="utf-8",
    )
    passed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--cache-clear"],
        cwd=tmp_path,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert passed.returncode == 0 and "1 passed" in passed.stdout


@pytest.mark.parametrize(
    "body",
    (
        (
            "import pytest\n\n"
            "@pytest.mark.xfail(strict=True, reason='future behavior')\n"
            "def test_future_behavior():\n"
            "    pass\n"
        ),
        (
            "import pytest\n\n"
            "def test_future_behavior():\n"
            "    pytest.skip('future behavior')\n"
        ),
        (
            "import pytest\n\n"
            "@pytest.mark.xfail(strict=True, reason='future behavior')\n"
            "def test_future_behavior():\n"
            "    assert False\n"
        ),
        (
            "import pytest\n\n"
            "@pytest.mark.xfail(reason='future behavior')\n"
            "def test_future_behavior():\n"
            "    observed = future_behavior()\n"
            "    assert observed == 'ready'\n"
        ),
    ),
    ids=(
        "marker-only-pass",
        "skip-only",
        "constant-assertion",
        "non-strict-xfail",
    ),
)
@pytest.mark.toolchain
def test_ac10_hook_rejects_pseudo_test_matrix(
    tmp_path: Path,
    body: str,
) -> None:
    """AC-10: every named marker-only/skip-only pseudo-test is rejected."""
    _repo(tmp_path, body)

    result = _run_hook(tmp_path)

    assert result.returncode == 1
    assert "skip-only test scaffolding" in result.stdout


def test_ac10_pytest_exposes_retained_strict_marker_as_xpass(
    tmp_path: Path,
) -> None:
    """AC-10 survivor pin: strict pytest semantics already expose stale marks."""
    path = _repo(
        tmp_path,
        "import pytest\n\n"
        "@pytest.mark." + "xfail(strict=True, reason='future behavior')\n"
        "def test_future_behavior():\n"
        "    observed = 'new'\n"
        "    assert observed == 'new'\n",
    )
    assert path.is_file()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    assert result.returncode == 1
    assert "XPASS" in result.stdout


def test_ac10_standalone_hook_classifies_executable_assertions() -> None:
    """AC-10: the standalone owner rejects empty or constant assertions."""
    namespace = runpy.run_path(str(HOOK), run_name="heddle_xfail_policy")
    hook_policy = namespace["_xfail_scaffold_is_executable"]
    cases = {
        "def test_future():\n    observed = future()\n    assert observed == 1\n": True,
        "def test_future():\n    assert False\n": False,
        "def test_future():\n    pass\n": False,
        ("def test_future():\n    def nested():\n        assert future()\n"): False,
        "def broken(:\n": False,
    }

    for source, expected in cases.items():
        assert hook_policy(source) is expected


@pytest.mark.parametrize(
    "failure",
    (
        OSError("git unavailable"),
        subprocess.TimeoutExpired(cmd="git diff", timeout=30),
    ),
)
def test_ac10_packaged_hook_fails_closed_when_git_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: BaseException,
) -> None:
    namespace = runpy.run_path(str(HOOK), run_name="heddle_xfail_policy")
    hook_subprocess = namespace["subprocess"]

    def fail_run(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(hook_subprocess, "run", fail_run)

    assert namespace["main"]() == 1
    assert "cannot inspect staged test scaffolding" in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure",
    (
        OSError("git show unavailable"),
        subprocess.TimeoutExpired(cmd="git show", timeout=30),
    ),
)
def test_ac10_packaged_hook_second_git_probe_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: BaseException,
) -> None:
    """The staged-source probe emits the same fail-closed diagnostic."""
    namespace = runpy.run_path(str(HOOK), run_name="heddle_xfail_policy")

    def fail_run(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(namespace["subprocess"], "run", fail_run)

    assert namespace["_staged_source"]("tests/test_future.py") is None
    assert "cannot inspect staged test scaffolding" in capsys.readouterr().err


def test_gate_runtime_text_io_is_utf8_explicit_under_ruff_rule() -> None:
    """Identity-bearing gate/runtime text I/O pins its encoding explicitly."""
    ruff = Path(sys.executable).with_name("ruff")
    result = subprocess.run(
        [
            str(ruff),
            "check",
            "--preview",
            "--select",
            "PLW1514",
            str(REPO_ROOT / "heddle/gate"),
            str(REPO_ROOT / "heddle/runtime"),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_ac11_raw_scaffold_briefing_remains_a_packaged_survivor_rail() -> None:
    """AC-11 survivor pin: the production renderer retains its packaged floor."""
    raw = (REPO_ROOT / "heddle/resources/scaffold.briefing.md").read_text(
        encoding="utf-8"
    )

    assert raw.strip()
    assert "scaffold" in raw.lower()
