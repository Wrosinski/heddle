"""Pytest execution-band selection and permission contracts."""

from __future__ import annotations

import pytest

from tests.guardrails.pytest_policy_helpers import project, require_failure


@pytest.mark.parametrize(
    ("band", "flags", "expected"),
    [
        ("fast", [], {"test_fast", "test_milestone"}),
        ("routine", [], {"test_fast", "test_milestone", "test_slow"}),
        ("toolchain", [], {"test_tool"}),
        ("e2e", ["--allow-e2e"], {"test_local", "test_local_build"}),
        (
            "live",
            ["--allow-live", "--allow-e2e"],
            {"test_external", "test_external_journey"},
        ),
    ],
)
def test_ac1_band_executes_only_its_items(tmp_path, band, flags, expected):
    child = project(tmp_path)
    result = child.run("--test-band", band, *flags, "test_cases.py")
    assert result.returncode == 0, result.stdout
    assert set(child.events("call")) == expected, result.stdout
    assert "deselected" in result.stdout, result.stdout


def test_ac1_default_and_old_environment_cannot_execute_forbidden_items(tmp_path):
    child = project(tmp_path)
    result = child.run(env={"HEDDLE_RUN_LIVE": "1"})
    assert result.returncode == 0, result.stdout
    assert child.events("call") == ["test_fast", "test_milestone"], result.stdout
    assert child.events("setup") == ["test_fast", "test_milestone"], result.stdout


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--test-band", "routine", "-k", "slow"], ["test_slow"]),
        (["--test-band", "routine", "-m", "acceptance"], ["test_fast"]),
        (["-m", "live"], []),
        (["-k", "external"], []),
    ],
)
def test_ac1_filters_intersect_instead_of_replace_band(tmp_path, args, expected):
    child = project(tmp_path)
    result = child.run(*args, "test_cases.py")
    assert child.events("call") == expected, result.stdout
    assert (result.returncode == 0) == bool(expected), result.stdout


def test_ac1_ordinary_marker_is_band_neutral(tmp_path):
    child = project(tmp_path)
    fast = child.run("--test-band", "fast", "-m", "scenario", "test_cases.py")
    assert fast.returncode == 0, fast.stdout
    assert child.events("call") == ["test_milestone"], fast.stdout

    for band in ("e2e", "live"):
        denied = child.run("--test-band", band, "-m", "scenario", "test_cases.py")
        require_failure(denied, "permission")
        assert child.events() == [], denied.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["test_cases.py::test_local"],
        ["test_cases.py::test_external"],
        ["--test-band", "e2e", "test_cases.py"],
        ["--test-band", "live", "test_cases.py"],
        ["--test-band", "live", "--allow-live", "test_cases.py"],
        ["--allow-live", "test_cases.py::test_external"],
    ],
)
def test_ac2_denial_precedes_every_fixture(tmp_path, args):
    child = project(tmp_path)
    result = child.run(*args)
    require_failure(result, "permission")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize("target", [None, "."])
def test_ac2_external_execution_requires_exact_invocation_targets(tmp_path, target):
    child = project(tmp_path)
    args = ["--test-band", "live", "--allow-live", "--allow-e2e"]
    if target:
        args.append(target)
    result = child.run(*args)
    require_failure(result, "exact")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize("origin", ["env", "ini"])
@pytest.mark.parametrize("also_direct", [False, True])
def test_ac2_addopts_permission_is_rejected_even_with_direct_duplicate(
    tmp_path, origin, also_direct
):
    opt = "--allow-e2e"
    child = project(tmp_path, addopts=opt if origin == "ini" else "")
    env = {"PYTEST_ADDOPTS": opt} if origin == "env" else {}
    args = ["--test-band", "e2e", "test_cases.py"]
    if also_direct:
        args.append(opt)
    result = child.run(*args, env=env)
    require_failure(result, "addopts")
    assert child.events() == [], result.stdout


def test_ac2_inherited_exact_target_is_not_a_direct_request(tmp_path):
    child = project(tmp_path)
    result = child.run(
        "--test-band", "e2e", "--allow-e2e", env={"PYTEST_ADDOPTS": "test_cases.py"}
    )
    require_failure(result, "exact")
    assert child.events() == [], result.stdout


def test_ac2_option_value_cannot_masquerade_as_direct_target(tmp_path):
    child = project(tmp_path)
    result = child.run(
        "--test-band",
        "e2e",
        "--allow-e2e",
        "-k",
        "test_cases.py",
        env={"PYTEST_ADDOPTS": "test_cases.py"},
    )
    require_failure(result, "exact")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize("nested", [False, True])
def test_ac2_response_file_cannot_supply_direct_authority(tmp_path, nested):
    child = project(tmp_path)
    (child.root / "request.txt").write_text(
        "--allow-e2e\ntest_cases.py::test_local\n", encoding="utf-8"
    )
    filename = "request.txt"
    if nested:
        (child.root / "outer.txt").write_text("@request.txt\n", encoding="utf-8")
        filename = "outer.txt"
    result = child.run("--test-band", "e2e", "@" + filename)
    require_failure(result, "permission")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize("origin", ["env", "ini"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("also_direct", [False, True])
def test_ac2_inherited_response_file_is_not_permission(
    tmp_path, origin, nested, also_direct
):
    filename = "outer.txt" if nested else "request.txt"
    child = project(tmp_path, addopts="@" + filename if origin == "ini" else "")
    (child.root / "request.txt").write_text("--allow-e2e\n", encoding="utf-8")
    if nested:
        (child.root / "outer.txt").write_text("@request.txt\n", encoding="utf-8")
    args = ["--test-band", "e2e", "test_cases.py"]
    if also_direct:
        args.append("--allow-e2e")
    result = child.run(
        *args, env={"PYTEST_ADDOPTS": "@" + filename} if origin == "env" else {}
    )
    require_failure(result, "addopts")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize("absolute", [False, True])
def test_ac2_symlink_exact_node_refuses_instead_of_partial_success(tmp_path, absolute):
    child = project(tmp_path)
    alias = child.root / "alias.py"
    alias.symlink_to(child.root / "test_cases.py")
    target = str(alias) if absolute else "alias.py"
    result = child.run(target + "::test_local", "test_cases.py::test_fast")
    require_failure(result, "permission")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize("level", ["module", "class", "parameter"])
def test_ac2_inherited_marks_guard_exact_nodes(tmp_path, level):
    header = "import pytest\nfrom pathlib import Path\n"
    effect = "Path('events').write_text('forbidden')"
    if level == "module":
        body = f"pytestmark = pytest.mark.e2e\ndef test_inherited(): {effect}\n"
        target = "test_cases.py::test_inherited"
    elif level == "class":
        body = (
            "@pytest.mark.e2e\nclass TestMarked:\n"
            f"    def test_inherited(self): {effect}\n"
        )
        target = "test_cases.py::TestMarked"
    else:
        body = (
            "@pytest.mark.parametrize('value', "
            "[pytest.param(1, marks=pytest.mark.e2e)])\n"
            f"def test_inherited(value): {effect}\n"
        )
        target = "test_cases.py::test_inherited[1]"
    child = project(tmp_path, header + body)
    result = child.run(target)
    require_failure(result, "permission")
    assert child.events() == [], result.stdout


@pytest.mark.parametrize(
    ("band", "present", "absent"),
    [
        ("fast", "test_fast", "test_external_journey"),
        ("e2e", "test_local_build", "test_external_journey"),
        ("live", "test_external_journey", "test_local_build"),
    ],
)
def test_ac3_collection_does_not_require_permission_or_run_fixtures(
    tmp_path, band, present, absent
):
    child = project(tmp_path)
    result = child.run("--collect-only", "--test-band", band, "test_cases.py")
    assert result.returncode == 0, result.stdout
    assert "::" + present in result.stdout, result.stdout
    assert "::" + absent not in result.stdout, result.stdout
    assert child.events() == [], result.stdout


@pytest.mark.parametrize(
    ("band", "target"),
    [
        ("e2e", "test_cases.py::test_local_build"),
        ("live", "test_cases.py::test_external_journey"),
    ],
)
def test_ac3_exact_collection_is_provider_inert_without_grants(tmp_path, band, target):
    child = project(tmp_path)
    result = child.run("--collect-only", "--test-band", band, target)
    assert result.returncode == 0, result.stdout
    assert "::" + target.partition("::")[2] in result.stdout, result.stdout
    assert child.events("provider") == [], result.stdout
    assert child.events() == [], result.stdout


@pytest.mark.parametrize(
    ("malformed", "diagnostic"),
    [("target", "not found"), ("configuration", "error")],
)
def test_ac3_malformed_inputs_are_nonzero_and_inert(tmp_path, malformed, diagnostic):
    child = project(tmp_path)
    target = "test_cases.py::test_missing"
    if malformed == "configuration":
        (child.root / "pytest.ini").write_text("[pytest\n", encoding="utf-8")
        target = "test_cases.py"

    result = child.run("--collect-only", target)
    require_failure(result, diagnostic)
    assert child.events() == [], result.stdout


def test_ac3_empty_selection_never_passes(tmp_path):
    """Survivor: pytest already refuses an empty ordinary selection."""
    child = project(tmp_path, "def test_only(): pass\n")
    result = child.run("test_cases.py", "-k", "absent")
    assert result.returncode != 0, result.stdout
    assert "deselected" in result.stdout, result.stdout
