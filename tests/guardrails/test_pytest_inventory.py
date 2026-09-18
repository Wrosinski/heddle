"""The reviewed current pytest inventory."""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.guardrails.pytest_policy_helpers import REPO_ROOT

CURRENT_INVENTORY: Path = (
    REPO_ROOT / "tests" / "fixtures" / "pytest-inventory-current.txt.gz.b64"
)
CURRENT_INVENTORY_SHA256 = (
    "4369125547636e4efe4a493a34c4b662d29be0ee69ed13c57421d40b9053412b"
)

COMPLETE_BAND_SCRIPT = REPO_ROOT / "scripts" / "run-complete-test-band.py"
_COMPLETE_BAND_SPEC = importlib.util.spec_from_file_location(
    "heddle_complete_band", COMPLETE_BAND_SCRIPT
)
assert _COMPLETE_BAND_SPEC is not None and _COMPLETE_BAND_SPEC.loader is not None
COMPLETE_BAND = importlib.util.module_from_spec(_COMPLETE_BAND_SPEC)
_COMPLETE_BAND_SPEC.loader.exec_module(COMPLETE_BAND)


def _repository_run(*args):
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=REPO_ROOT,
        env={"PATH": os.defpath, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        check=False,
    )


def _node_ids(result: subprocess.CompletedProcess[str]) -> list[str]:
    assert result.returncode == 0, result.stdout
    return sorted(
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    )


def _assert_same_nodes(label: str, actual: set[str], expected: set[str]) -> None:
    removed = sorted(expected - actual)
    added = sorted(actual - expected)
    assert not removed and not added, (
        f"{label} changed\n"
        + "removed:\n"
        + "\n".join(removed)
        + "\nadded:\n"
        + "\n".join(added)
    )


def _inventory(path: Path, expected_sha256: str) -> set[str]:
    raw = gzip.decompress(base64.b64decode(path.read_bytes()))
    assert hashlib.sha256(raw).hexdigest() == expected_sha256
    nodes = [line for line in raw.decode().splitlines() if line]
    assert nodes == sorted(set(nodes)), "inventory must contain unique sorted node IDs"
    return set(nodes)


def _write_current_inventory(
    nodes: list[str], *, destination: Path = CURRENT_INVENTORY
) -> str:
    raw = ("\n".join(sorted(nodes)) + "\n").encode()
    destination.write_bytes(base64.encodebytes(gzip.compress(raw, mtime=0)))
    return hashlib.sha256(raw).hexdigest()


def test_complete_band_targets_cover_every_collected_node() -> None:
    nodes = [
        "tests/example.py::test_one",
        "tests/example.py::test_two[value]",
        "tests/other.py::test_three",
    ]
    targets = COMPLETE_BAND._proof_targets(nodes)
    COMPLETE_BAND._assert_complete_coverage(nodes, targets)
    with pytest.raises(RuntimeError, match="missing=.*test_two"):
        COMPLETE_BAND._assert_complete_coverage(nodes, targets[::2])


def test_complete_band_batches_respect_encoded_byte_boundary() -> None:
    nodes = ["aaa", "bbb", "ccc"]
    assert COMPLETE_BAND._batches(nodes, byte_limit=8) == [
        ["aaa", "bbb"],
        ["ccc"],
    ]


def test_complete_band_oversized_node_falls_back_to_its_exact_file() -> None:
    nodes = [
        "tests/long_case.py::test_value[" + "x" * 40 + "]",
        "tests/long_case.py::test_other[" + "y" * 40 + "]",
        "tests/short.py::test_value",
    ]
    targets = COMPLETE_BAND._proof_targets(nodes, byte_limit=32)
    assert targets == ["tests/long_case.py", "tests/short.py::test_value"]
    COMPLETE_BAND._assert_complete_coverage(nodes, targets)


@pytest.mark.parametrize(
    ("collection_result", "collected", "expected"),
    [
        (pytest.ExitCode.USAGE_ERROR, [], pytest.ExitCode.USAGE_ERROR),
        (pytest.ExitCode.OK, [], pytest.ExitCode.NO_TESTS_COLLECTED),
    ],
)
def test_complete_band_propagates_failed_or_empty_collection(
    monkeypatch, collection_result, collected, expected
) -> None:
    def collect(_arguments, *, plugins):
        plugins[0].nodes = collected
        return collection_result

    monkeypatch.setattr(COMPLETE_BAND.pytest, "main", collect)
    monkeypatch.setattr(sys, "argv", [str(COMPLETE_BAND_SCRIPT), "fast"])
    assert COMPLETE_BAND.main() == expected


def test_complete_band_propagates_later_batch_failure(monkeypatch) -> None:
    def collect(_arguments, *, plugins):
        plugins[0].nodes = ["tests/a.py::test_a", "tests/b.py::test_b"]
        return pytest.ExitCode.OK

    results = iter((0, 7))

    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], next(results))

    monkeypatch.setattr(COMPLETE_BAND.pytest, "main", collect)
    monkeypatch.setattr(
        COMPLETE_BAND,
        "_batches",
        lambda _nodes: [["tests/a.py::test_a"], ["tests/b.py::test_b"]],
    )
    monkeypatch.setattr(COMPLETE_BAND.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", [str(COMPLETE_BAND_SCRIPT), "fast"])
    assert COMPLETE_BAND.main() == 7


def _regenerate_inventory(arguments: list[str]) -> int:
    if arguments not in ([], ["--write"]):
        print(
            "usage: python -m tests.guardrails.test_pytest_inventory [--write]",
            file=sys.stderr,
        )
        return 2
    current = _node_ids(
        _repository_run("--collect-only", "-p", "no:tests.pytest_policy")
    )
    stored = _inventory(CURRENT_INVENTORY, CURRENT_INVENTORY_SHA256)
    actual = set(current)
    removed, added = sorted(stored - actual), sorted(actual - stored)
    print(f"stored={len(stored)} current={len(actual)}")
    for label, nodes in (("removed", removed), ("added", added)):
        print(f"{label}={len(nodes)}")
        for node in nodes:
            print(f"{label}: {node}")
    digest = hashlib.sha256(("\n".join(current) + "\n").encode()).hexdigest()
    print(f"sha256={digest}")
    if arguments == ["--write"]:
        assert _write_current_inventory(current) == digest
        source = Path(__file__).read_text(encoding="utf-8")
        updated = re.sub(
            r'(CURRENT_INVENTORY_SHA256 = \(\n    ")[a-f0-9]{64}("\n\))',
            rf"\g<1>{digest}\g<2>",
            source,
            count=1,
        )
        if updated == source and digest != CURRENT_INVENTORY_SHA256:
            raise RuntimeError("inventory digest constant was not updated")
        Path(__file__).write_text(updated, encoding="utf-8")
        print("wrote current fixture and CURRENT_INVENTORY_SHA256")
        return 0
    return int(bool(stored != actual or digest != CURRENT_INVENTORY_SHA256))


@pytest.mark.parametrize(
    ("band", "target", "present", "absent"),
    [
        (
            "e2e",
            "tests/runtime/test_tiering_installed.py",
            "test_ac14_installed_direct_choice_needs_no_formal_feature",
            "test_ac14_native_birth_and_old_format_refusal_are_separate_hosts",
        ),
        (
            "toolchain",
            "tests/runtime/test_portability_lane.py",
            "test_ac01_red_dry_run_is_exact_ordered_and_write_free",
            "test_ac10_red_installed_ratified_journey_honors_nondefault_plans_layout",
        ),
        (
            "live",
            "tests/driver/test_supported_host_permissions.py",
            "test_ac4_live_generated_permissions",
            "test_ac1_native",
        ),
    ],
)
def test_ac7_real_corpus_is_independently_collectable(band, target, present, absent):
    result = _repository_run("--collect-only", "--test-band", band, target)
    assert result.returncode == 0, result.stdout
    assert "::" + present in result.stdout, result.stdout
    assert "::" + absent not in result.stdout, result.stdout


def test_ac7_local_only_live_directory_case_is_e2e_not_external():
    target = "tests/live/test_write_path_e2e.py"
    local = _repository_run("--collect-only", "--test-band", "e2e", target)
    assert local.returncode == 0, local.stdout
    assert "::test_local_write_path_no_mocks" in local.stdout
    external = _repository_run("--collect-only", "--test-band", "live", target)
    assert external.returncode != 0, external.stdout
    assert "::test_local_write_path_no_mocks" not in external.stdout


@pytest.mark.parametrize(
    ("band", "target", "expected"),
    [
        (
            "e2e",
            "tests/runtime/test_tiering_installed.py",
            {
                "test_ac14_installed_direct_choice_needs_no_formal_feature",
                "test_ac14_installed_all_off_journey_still_verifies_before_acceptance",
                "test_ac14_installed_review_preserves_original_finding_across_disable_and_tamper",
                "test_ac14_installed_v6_conversion_preserves_original_bytes",
            },
        ),
        (
            "toolchain",
            "tests/runtime/test_portability_lane.py",
            {
                "test_mirror_first_adoption_refuses_foreign_claude_md_without_writes",
                "test_mirror_symlink_and_identical_copy_are_accepted",
            },
        ),
    ],
)
def test_ac7_installed_consumers_leave_fast_for_their_explicit_band(
    band, target, expected
):
    fast = _repository_run("--collect-only", "--test-band", "fast", target)
    if target.endswith("test_tiering_installed.py"):
        assert fast.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, fast.stdout
    else:
        assert fast.returncode == pytest.ExitCode.OK, fast.stdout
        assert (
            "::test_mirror_opt_out_and_declared_path_follow_the_host_config"
            in fast.stdout
        ), fast.stdout
    assert all("::" + name not in fast.stdout for name in expected), fast.stdout

    classified = _repository_run("--collect-only", "--test-band", band, target)
    assert classified.returncode == 0, classified.stdout
    assert all("::" + name in classified.stdout for name in expected), classified.stdout


def test_ac7_full_corpus_matches_current_inventory_and_band_partition():
    baseline = _inventory(CURRENT_INVENTORY, CURRENT_INVENTORY_SHA256)

    unfiltered_nodes = _node_ids(
        _repository_run("--collect-only", "-p", "no:tests.pytest_policy")
    )
    assert len(unfiltered_nodes) == len(set(unfiltered_nodes)), (
        "unfiltered collection returned duplicate node IDs"
    )
    unfiltered = set(unfiltered_nodes)
    _assert_same_nodes("unfiltered collection", unfiltered, baseline)

    bands = {
        band: set(_node_ids(_repository_run("--collect-only", "--test-band", band)))
        for band in ("fast", "routine", "toolchain", "e2e", "live")
    }
    assert bands["fast"] <= bands["routine"], (
        "fast must remain a subset of routine collection"
    )
    partition_bands = ("routine", "toolchain", "e2e", "live")
    overlaps = {
        f"{left}/{right}": sorted(bands[left] & bands[right])
        for index, left in enumerate(partition_bands)
        for right in partition_bands[index + 1 :]
        if bands[left] & bands[right]
    }
    assert not overlaps, f"band partition overlaps:\n{overlaps}"
    band_union = set().union(*(bands[band] for band in partition_bands))
    _assert_same_nodes("band union", band_union, unfiltered)


def test_ac8_direct_pytest_loads_root_policy_and_complete_proof():
    result = _repository_run(
        "--strict-proof",
        "tests/guardrails/test_pytest_proof.py::test_ac5_ordinary_skip_semantics_remain_a_survivor",
    )
    assert result.returncode == 0, result.stdout
    assert "requested=1" in result.stdout and "passed=1" in result.stdout


def test_ac8_active_formal_guidance_uses_checked_launcher():
    guidance = tuple(
        (REPO_ROOT / path).read_text(encoding="utf-8")
        for path in (
            "AGENTS.md",
            "README.md",
            "docs/workflow/testing-strategy.md",
        )
    )
    formal_commands = [
        line
        for document in guidance
        for line in document.splitlines()
        if "python -m " in line
        and ("tests.proof_runner" in line or "--strict-proof" in line)
    ]
    assert formal_commands
    assert all("-m tests.proof_runner" in line for line in formal_commands), (
        formal_commands
    )
    advertised = {
        flag
        for document in guidance
        for flag in re.findall(
            r"--(?:test-band|allow-e2e|allow-live|strict-proof)\b", document
        )
    }
    policy = (REPO_ROOT / "tests/pytest_policy.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'group\.addoption\("(--[^\"]+)"', policy))
    assert advertised <= registered
    assert (
        ".venv/bin/python -m tests.guardrails.test_pytest_inventory --write"
        in guidance[-1]
    )


if __name__ == "__main__":
    raise SystemExit(_regenerate_inventory(sys.argv[1:]))
