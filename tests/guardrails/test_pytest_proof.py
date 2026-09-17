"""Formal proof-runner execution, accounting, and refusal contracts."""

from __future__ import annotations

import shlex
import shutil
import sys

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.kernel.state import read_state_file
from heddle.runtime import write_path
from heddle.runtime.verification import assess_current_verification
from tests.guardrails.pytest_policy_helpers import (
    POLICY,
    PROOF_RUNNER,
    project,
    require_failure,
)
from tests.runtime.verification_provenance_helpers import write_yaml
from tests.tiering_review_helpers import current_host

PARAMETERIZED = """\
import pytest
from pathlib import Path
@pytest.mark.parametrize('value', [1, 2, 3])
def test_value(value):
    with Path('events').open('a') as stream:
        stream.write('call:' + str(value) + '\\n')
"""

# Synthetic child source, not outer scaffolding. Match the packaged-hook tests'
# explicit string construction so the lexical staged-skip guard sees no marker
# on the outer test. Every child outcome is asserted by an executing outer case.
CHILD_SKIP = "pytest." + "skip"
CHILD_XFAIL = "pytest.mark." + "xfail"


@pytest.mark.parametrize(
    "targets",
    [
        ["test_cases.py", "test_cases.py::test_value[1]"],
        ["test_cases.py::test_value", "test_cases.py::test_value[1]"],
        ["test_cases.py::test_value[1]", "test_cases.py"],
        ["test_cases.py", "test_cases.py"],
    ],
)
def test_ac4_complete_parameter_set_and_overlap_count_once(tmp_path, targets):
    child = project(tmp_path, PARAMETERIZED)
    result = child.prove(*targets)
    assert result.returncode == 0, result.stdout
    assert sorted(child.events("call")) == ["1", "2", "3"], result.stdout
    assert "requested=3" in result.stdout, result.stdout
    assert "passed=3" in result.stdout, result.stdout


def test_ac4_overlap_preserves_the_current_collectors_order(tmp_path):
    child = project(tmp_path, PARAMETERIZED)
    ordinary = child.run("test_cases.py::test_value")
    assert ordinary.returncode == 0, ordinary.stdout
    expected = child.events("call")
    assert sorted(expected) == ["1", "2", "3"], ordinary.stdout
    strict = child.prove(
        "test_cases.py::test_value",
        "test_cases.py::test_value[1]",
    )
    assert strict.returncode == 0, strict.stdout
    assert child.events("call") == expected, strict.stdout


@pytest.mark.parametrize(
    "targets",
    [
        ["test_cases.py", "test_cases.py::missing"],
        ["test_cases.py", "test_cases.py::test_value[missing]"],
        ["test_cases.py::test_value", "test_cases.py::test_value[missing]"],
    ],
)
def test_ac4_parent_target_cannot_hide_an_invalid_explicit_node(tmp_path, targets):
    child = project(tmp_path, PARAMETERIZED)
    result = child.prove(*targets)
    require_failure(result, "exact")
    assert child.events() == [], result.stdout


def test_ac4_parameter_ids_are_not_hierarchical_selectors(tmp_path):
    child = project(
        tmp_path,
        PARAMETERIZED.replace("[1, 2, 3]", "[1, 2], ids=['x', 'x]::other']"),
    )
    result = child.prove(
        "test_cases.py::test_value[x]",
        "test_cases.py::test_value[x]::other]",
    )
    assert result.returncode == 0, result.stdout
    assert child.events("call") == ["1", "2"], result.stdout
    assert "requested=2" in result.stdout, result.stdout


@pytest.mark.parametrize(
    "filters",
    [
        ["-k", "1"],
        ["-m", "missing"],
        ["--deselect", "test_cases.py::test_value[2]"],
        ["--test-band", "toolchain"],
    ],
)
def test_ac4_no_requested_parameter_can_disappear(tmp_path, filters):
    child = project(tmp_path, PARAMETERIZED)
    result = child.prove("test_cases.py", *filters)
    require_failure(result, "strict proof")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize("target", [None, ".", "missing.py"])
def test_ac4_strict_proof_requires_real_exact_targets(tmp_path, target):
    child = project(tmp_path, PARAMETERIZED)
    args = ["--strict-proof"] + ([target] if target else [])
    result = child.prove(*args)
    require_failure(result, "exact")
    assert child.events() == [], result.stdout


def test_ac4_inherited_targets_cannot_author_strict_proof(tmp_path):
    child = project(tmp_path, PARAMETERIZED)
    result = child.prove(env={"PYTEST_ADDOPTS": "test_cases.py"})
    require_failure(result, "exact")
    assert child.events() == [], result.stdout


def test_ac4_explicit_parameter_is_an_authored_subset(tmp_path):
    child = project(tmp_path, PARAMETERIZED)
    result = child.prove("test_cases.py::test_value[2]")
    assert result.returncode == 0, result.stdout
    assert child.events("call") == ["2"], result.stdout
    assert "requested=1" in result.stdout, result.stdout


@pytest.mark.parametrize(
    "option",
    [
        ["--ignore", "other.py"],
        ["--ignore-glob", "other*.py"],
        ["--pyargs"],
        ["-o", "python_functions=check_*"],
        ["-o", "python_files=other.py"],
        ["-o", "python_classes=Other"],
        ["--runxfail"],
        ["--lf"],
        ["--sw"],
        ["--collect-only"],
        ["--doctest-modules"],
        ["--help"],
        ["--version"],
        ["-V"],
        ["--version", "--version"],
        ["--markers"],
        ["--fixtures"],
        ["--fixtures-per-test"],
    ],
)
def test_ac4_unsupported_collection_or_xfail_overrides_refuse(tmp_path, option):
    child = project(tmp_path, PARAMETERIZED)
    result = child.prove("test_cases.py", *option)
    require_failure(result, "strict proof")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize("source", ["direct", "environment", "ini"])
@pytest.mark.parametrize(
    "option", ["--noconftest", "--confcutdir=suite", "--confcutdir suite"]
)
def test_ac4_conftest_suppression_cannot_remove_parameter_witnesses(
    tmp_path, source, option
):
    child = project(
        tmp_path,
        "from pathlib import Path\ndef test_case():\n"
        "    with Path('events').open('a') as stream: stream.write('call:case\\n')\n",
    )
    suite = child.root / "suite"
    suite.mkdir()
    (child.root / "test_cases.py").rename(suite / "test_cases.py")
    (child.root / "conftest.py").write_text(
        "import pytest\nfrom pathlib import Path\n"
        "@pytest.fixture(autouse=True, params=[1, 2])\n"
        "def parameter_witness(request):\n"
        "    with Path('events').open('a') as stream:\n"
        "        stream.write('setup:' + str(request.param) + '\\n')\n"
        "    yield\n"
        "    with Path('events').open('a') as stream:\n"
        "        stream.write('teardown:' + str(request.param) + '\\n')\n",
        encoding="utf-8",
    )
    baseline = child.prove("suite/test_cases.py")
    assert baseline.returncode == 0, baseline.stdout
    assert "requested=2" in baseline.stdout and "passed=2" in baseline.stdout
    assert sorted(child.events("setup")) == ["1", "2"]
    assert child.events("call") == ["case", "case"]
    assert sorted(child.events("teardown")) == ["1", "2"]

    args = shlex.split(option) if source == "direct" else []
    env = {"PYTEST_ADDOPTS": option} if source == "environment" else {}
    if source == "ini":
        config = child.root / "pytest.ini"
        config.write_text(
            config.read_text().replace("addopts =", "addopts = " + option)
        )
    result = child.prove("suite/test_cases.py", *args, env=env)
    require_failure(result, "strict proof")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize(
    ("source", "option"),
    [
        ("direct", "-c suite/isolated.ini"),
        ("environment", "-c suite/isolated.ini"),
        ("ini", "-c suite/isolated.ini"),
        ("direct", "--rootdir=suite"),
    ],
    ids=["direct-config", "environment-config", "ini-config", "root-override"],
)
def test_ac4_configuration_override_cannot_remove_fixture_witnesses(
    tmp_path, source, option
):
    child = project(tmp_path, "def test_case(): pass\n")
    suite = child.root / "suite"
    suite.mkdir()
    (child.root / "test_cases.py").rename(suite / "test_cases.py")
    (suite / "isolated.ini").write_text("[pytest]\n", encoding="utf-8")
    (child.root / "conftest.py").write_text(
        "import pytest\nfrom pathlib import Path\n"
        "@pytest.fixture(autouse=True, params=[1, 2])\n"
        "def witness(request):\n"
        "    with Path('events').open('a') as stream:\n"
        "        stream.write('setup:' + str(request.param) + '\\n')\n"
        "    yield\n",
        encoding="utf-8",
    )
    baseline = child.prove("suite/test_cases.py")
    assert baseline.returncode == 0, baseline.stdout
    assert "requested=2" in baseline.stdout and "passed=2" in baseline.stdout
    assert sorted(child.events("setup")) == ["1", "2"]

    args = shlex.split(option) if source == "direct" else []
    env = {"PYTEST_ADDOPTS": option} if source == "environment" else {}
    if source == "ini":
        config = child.root / "pytest.ini"
        config.write_text(
            config.read_text().replace("addopts =", "addopts = " + option)
        )
    result = child.prove("suite/test_cases.py", *args, env=env)
    require_failure(result, "strict proof")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize(
    "body",
    [
        f"import pytest\ndef test_skip(): {CHILD_SKIP}('fixture unavailable')\n",
        "import pytest\ndef test_ok(): pass\n"
        f"def test_skip(): {CHILD_SKIP}('missing')\n",
        f"import pytest\n@{CHILD_XFAIL}(reason='known')\ndef test_x(): assert False\n",
        f"import pytest\n@{CHILD_XFAIL}(reason='known')\ndef test_x(): pass\n",
        f"import pytest\n@{CHILD_XFAIL}(strict=True, reason='known')\n"
        "def test_x(): pass\n",
        "def test_fail(): assert False\n",
        "import pytest\n@pytest.fixture(autouse=True)\n"
        "def f(): raise ValueError('setup')\ndef test_a(): pass\n",
        "import pytest\n@pytest.fixture(autouse=True)\ndef f():\n"
        "    yield\n    raise ValueError('teardown')\ndef test_a(): pass\n",
        "raise ValueError('collection failed')\n",
        "def test_interrupt(): raise KeyboardInterrupt()\n",
        "# no tests\n",
    ],
    ids=[
        "all-skipped",
        "one-skipped",
        "xfail",
        "xpass",
        "strict-xpass",
        "call-failed",
        "setup-failed",
        "teardown-failed",
        "collection-error",
        "interrupted",
        "empty",
    ],
)
def test_ac5_incomplete_or_nonpassing_outcomes_cannot_prove(tmp_path, body):
    child = project(tmp_path, body)
    result = child.prove("test_cases.py")
    require_failure(result, "strict proof")


def test_ac5_missing_runtime_reports_are_not_success(tmp_path):
    child = project(tmp_path, "def test_unreported(): pass\n")
    (child.root / "conftest.py").write_text(
        "def pytest_runtest_protocol(item, nextitem): return True\n",
        encoding="utf-8",
    )
    result = child.prove("test_cases.py")
    require_failure(result, "strict proof")
    assert "incomplete=1" in result.stdout, result.stdout


def test_ac5_skipped_module_cannot_hide_behind_a_passing_file(tmp_path):
    child = project(
        tmp_path,
        f"import pytest\n{CHILD_SKIP}('unavailable', allow_module_level=True)\n",
    )
    (child.root / "test_pass.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    result = child.prove("test_cases.py", "test_pass.py")
    require_failure(result, "strict proof")


def test_ac5_internal_error_after_reports_is_not_a_passing_proof(tmp_path):
    child = project(tmp_path, "def test_ok(): pass\n")
    (child.root / "conftest.py").write_text(
        "import pytest\n@pytest.hookimpl(wrapper=True)\n"
        "def pytest_runtestloop(session):\n"
        "    yield\n    raise RuntimeError('after passing reports')\n",
        encoding="utf-8",
    )
    result = child.prove("test_cases.py")
    require_failure(result, "strict proof fail")


def test_ac5_ordinary_skip_semantics_remain_a_survivor(tmp_path):
    """Survivor: a developer exploratory skip still reports a skip, not a pass."""
    child = project(
        tmp_path, f"import pytest\ndef test_a(): {CHILD_SKIP}('ordinary')\n"
    )
    result = child.run("test_cases.py")
    assert result.returncode == 0, result.stdout
    assert "1 skipped" in result.stdout, result.stdout


def test_ac5_inactive_conditional_xfail_can_pass(tmp_path):
    child = project(
        tmp_path,
        f"import pytest\n@{CHILD_XFAIL}(False, reason='inactive')\n"
        "def test_a(): pass\n",
    )
    result = child.prove("test_cases.py")
    assert result.returncode == 0, result.stdout
    assert "passed=1" in result.stdout, result.stdout


def test_ac5_authorized_workflow_requires_every_phase(tmp_path):
    child = project(
        tmp_path,
        "import pytest\n@pytest.mark.e2e\ndef test_a(): pass\n",
    )
    result = child.prove("--test-band", "e2e", "--allow-e2e", "test_cases.py")
    assert result.returncode == 0, result.stdout
    assert "requested=1" in result.stdout and "passed=1" in result.stdout


@pytest.mark.parametrize("changed_source", ["pytest_policy.py", "proof_runner.py"])
def test_ac6_native_fact_records_incomplete_proof_and_runner_staleness(
    tmp_path, monkeypatch, changed_source
):
    host, state_path = current_host(tmp_path, monkeypatch, stage="implement")
    from tests.runtime.verification_provenance_helpers import (
        convert_current_fixture_to_v8,
    )

    convert_current_fixture_to_v8(host, state_path)
    (host / "src.py").write_text("value = 1\n", encoding="utf-8")
    package = host / "tests"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("")
    shutil.copyfile(POLICY, package / "pytest_policy.py")
    shutil.copyfile(PROOF_RUNNER, package / "proof_runner.py")
    check = host / "test_proof.py"
    check.write_text(
        f"import pytest\ndef test_proof(): {CHILD_SKIP}('missing proof')\n"
    )
    (host / "pytest.ini").write_text("[pytest]\n")
    command = shlex.join(
        [
            "env",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            f"PYTHONPATH={host}",
            sys.executable,
            "-m",
            "tests.proof_runner",
            "test_proof.py",
        ]
    )
    document = yaml.safe_load(state_path.read_text())
    document["milestones"][0]["owns"] = [
        "src.py",
        "tests/__init__.py",
        "tests/pytest_policy.py",
        "tests/proof_runner.py",
        "pytest.ini",
        "test_proof.py",
    ]
    document["milestones"][0]["verification"]["command"] = command + " --version"
    write_yaml(state_path, document)
    monkeypatch.chdir(host)

    result = write_path.verify(ops.Verify(scope="m1", feature=document["feature"]))
    assert not result.ok and result.error is not None, result
    assert result.error.code == "verification-failed"
    assert result.error.details["recorded"] is True
    assert result.error.details["status"] == "failed"
    state = read_state_file(state_path)
    assert state.verifications[-1].exit_code != 0
    assert assess_current_verification(host, state, "m1").status == "failed"
    log = state_path.parent / state.verifications[-1].log
    assert "strict proof" in log.read_text().lower(), log.read_text()

    document = yaml.safe_load(state_path.read_text())
    document["milestones"][0]["verification"]["command"] = command
    write_yaml(state_path, document)

    result = write_path.verify(ops.Verify(scope="m1", feature=document["feature"]))
    assert not result.ok and result.error is not None, result
    assert result.error.code == "verification-failed"
    assert result.error.details["recorded"] is True
    assert result.error.details["status"] == "failed"
    state = read_state_file(state_path)
    assert state.verifications[-1].exit_code != 0
    assert assess_current_verification(host, state, "m1").status == "failed"
    log = state_path.parent / state.verifications[-1].log
    assert "strict proof" in log.read_text().lower(), log.read_text()

    check.write_text("def test_proof(): pass\n")
    result = write_path.verify(ops.Verify(scope="m1", feature=document["feature"]))
    assert result.ok, result
    state = read_state_file(state_path)
    fact = state.verifications[-1]
    assert fact.exit_code == 0
    assert fact.command == command
    assert fact.evidence.before == fact.evidence.after
    from heddle.runtime.verification import read_source_evidence

    evidence = read_source_evidence(state_path.parent, fact.evidence.before)
    assert set(evidence.definition.paths) == {
        "src.py",
        "tests/__init__.py",
        "tests/pytest_policy.py",
        "tests/proof_runner.py",
        "pytest.ini",
        "test_proof.py",
    }
    assert assess_current_verification(host, state, "m1").status == "fresh"
    source = package / changed_source
    source.write_text(source.read_text() + "\n# relevant runner change\n")
    assert assess_current_verification(host, state, "m1").status == "content-stale"
