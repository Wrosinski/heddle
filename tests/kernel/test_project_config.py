"""
Config-loader contract tests — milestone foundation.

Covers: AC-1 (loader parses both fixture halves), AC-2 (defaults,
diagnostics, discovery, wrong-type failures), AC-3 (TESTS_ROOT seam retired;
this repo's .heddle.yaml carries the §A2 host policy values).
Behavior contract: reader-kernel-read-model

preimplementation state: imports heddle.kernel.project_config, which does not exist
until
foundation — collection errors are the expected pre-implementation signal
(pattern red-phase-always-green-scaffolding: the current milestone's tests
bite directly).

Expected values are transcribed literals from the Feature Spec (pinned
defaults table, the fixture halves, the repo .heddle.yaml literal in the
Decision Log) — never derived from the loader under test
(pattern contract-value-citation-lock).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from heddle.kernel import KernelError
from heddle.kernel.project_config import (
    LayoutConfig,
    find_project_root,
    load_project_config,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures"
TINY = FIXTURES_ROOT / "workspaces" / "tiny"
GOLDEN = FIXTURES_ROOT / "workspaces" / "golden"
CONFIGS = FIXTURES_ROOT / "configs"

# Pinned defaults table (Feature Spec §Conceptual Design, Decision Log
# "Config loader defaults") — transcribed, not imported.
DEFAULT_LAYOUT = LayoutConfig(
    specs="docs/features",
    plans="plans",
    prompts=".heddle/prompts",
    schemas=".heddle/schemas",
    tests="tests",
    source="src",
)


class TestAC1ConfigParsesBothHalves:
    """AC-1: tiny (minimal + defaults) and golden (every configuration section, typed
    exactly, file order preserved). Zero diagnostics on both."""

    def test_tiny_minimal_half(self):
        config = load_project_config(TINY)
        assert config.root == TINY
        # tiny declares all six layout keys explicitly (= the defaults shape).
        assert config.layout == DEFAULT_LAYOUT
        assert dict(config.commands) == {"test": "pytest -q"}
        # Absent sections per the pinned defaults table.
        assert dict(config.agents) == {}
        assert config.gates_enabled is None
        assert config.diagnostics == ()

    def test_golden_full_half(self):
        config = load_project_config(GOLDEN)
        assert config.root == GOLDEN
        assert config.layout == LayoutConfig(
            specs="docs/features",
            plans="plans",
            prompts=".heddle/prompts",
            schemas=".heddle/schemas",
            tests="src/tests",
            source="src/example",
        )
        assert dict(config.commands) == {
            "test": "poetry run pytest -q -n auto",
            "lint": "poetry run flake8",
            "typecheck": "poetry run mypy --config-file=pyproject.toml",
        }
        assert dict(config.agents) == {"claude": True, "codex": True}
        # gates.enabled in file order — direct tuple equality, never sorted
        # (assertion-tightness rule).
        assert config.gates_enabled == (
            "spec-review",
            "plan-review",
            "review-test-scaffolding",
            "milestone-review",
            "peer-review-sequential",
            "behavior-review",
            "complexity-review",
            "robustness-analysis",
        )
        assert config.diagnostics == ()


class TestAC2DefaultsDiagnosticsDiscovery:
    """AC-2: the synthetic config corpus (tests/fixtures/configs/ — see its
    README coverage map) plus root discovery."""

    def test_empty_config_applies_full_defaults(self):
        # AC-2 (a): empty file ⇒ the full pinned defaults table.
        config = load_project_config(CONFIGS / "empty")
        assert config.layout == DEFAULT_LAYOUT
        assert dict(config.commands) == {}
        assert dict(config.agents) == {}
        assert config.gates_enabled is None
        assert config.diagnostics == ()

    def test_unknown_keys_are_advisory_diagnostics(self):
        # AC-2 (b): unknown section `ci` + unknown layout key `docs` — loads
        # successfully, one advisory config-unknown-key diagnostic per key.
        config = load_project_config(CONFIGS / "unknown-keys")
        assert config.layout.tests == "tests"  # the declared key still lands
        assert len(config.diagnostics) == 2, (
            "FAIL: expected exactly one config-unknown-key diagnostic per "
            "unknown key (one section, one layout key)"
        )
        for diagnostic in config.diagnostics:
            assert diagnostic.code == "config-unknown-key"
            assert diagnostic.severity == "advisory"
        messages = " ".join(d.message for d in config.diagnostics)
        assert "ci" in messages, "FAIL: diagnostic must name the unknown section"
        assert "docs" in messages, "FAIL: diagnostic must name the unknown layout key"

    def test_malformed_yaml_fails_workspace_invalid(self):
        # AC-2 (c): malformed YAML is fatal with an actionable hint — a typed
        # KernelError, never a raw yaml traceback.
        with pytest.raises(KernelError) as excinfo:
            load_project_config(CONFIGS / "malformed")
        assert excinfo.value.code == "workspace-invalid"
        assert excinfo.value.hint, "FAIL: workspace-invalid must carry a hint"

    def test_discovery_nested_inside_tiny(self):
        # AC-2 (d): walk-up discovery from a nested directory finds the
        # nearest .heddle.yaml.
        assert find_project_root(TINY / "plans" / "sample-feature") == TINY

    def test_discovery_outside_any_project(self, tmp_path):
        # AC-2 (d): no .heddle.yaml anywhere up-tree ⇒ None. tmp_path lives
        # outside this repository (which gains its own .heddle.yaml at foundation).
        outside = tmp_path / "no-project" / "deep"
        outside.mkdir(parents=True)
        assert find_project_root(outside) is None

    @pytest.mark.parametrize(
        ("variant", "offending_key"),
        [
            ("wrong-type-layout", "tests"),
            ("wrong-type-command", "test"),
            ("wrong-type-agent", "codex"),
            ("wrong-type-gates", "enabled"),
        ],
    )
    def test_wrong_typed_values_fail_naming_the_key(self, variant, offending_key):
        # AC-2 (e): each wrong-typed pinned value fails workspace-invalid
        # with a hint naming the offending key.
        with pytest.raises(KernelError) as excinfo:
            load_project_config(CONFIGS / variant)
        assert excinfo.value.code == "workspace-invalid"
        blob = f"{excinfo.value.message} {excinfo.value.hint}"
        assert offending_key in blob, (
            f"FAIL [{variant}]: the error must name the offending key "
            f"{offending_key!r} — got: {blob}"
        )


class TestConfigReadFailures:
    """
    Peer review review: config read failures stay on the typed error
        channel — KernelError(workspace-invalid), never a bare OSError or
        UnicodeDecodeError reaching the dispatcher's generic wrapper (the
        mirror of the state reader's guard).
    """

    def test_non_utf8_config_fails_workspace_invalid(self, tmp_path):
        root = tmp_path / "host"
        root.mkdir()
        (root / ".heddle.yaml").write_bytes(b"\xff\xfe\x00bad")
        with pytest.raises(KernelError) as excinfo:
            load_project_config(root)
        assert excinfo.value.code == "workspace-invalid"
        assert ".heddle.yaml" in (f"{excinfo.value.message} {excinfo.value.hint}"), (
            "FAIL: the error must name the unreadable config file"
        )

    def test_unreadable_config_fails_workspace_invalid(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("permission bits do not bind when running as root")
        root = tmp_path / "host"
        root.mkdir()
        config_path = root / ".heddle.yaml"
        config_path.write_text("layout:\n  plans: plans\n", encoding="utf-8")
        config_path.chmod(0)
        try:
            with pytest.raises(KernelError) as excinfo:
                load_project_config(root)
            assert excinfo.value.code == "workspace-invalid"
        finally:
            config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


class TestLayoutRootContainment:
    """
    Checkpoint 2 R3 (review): configuration's layout values are
        project-root-relative directory paths — absolute or parent-traversing
        values are fatal (workspace-invalid), naming the offending key.
    """

    @pytest.mark.parametrize(
        "value",
        ["/tmp/outside", "../outside", "a/../../outside"],
        ids=["absolute", "parent-traversal", "embedded-traversal"],
    )
    def test_escaping_layout_paths_fail_workspace_invalid(self, tmp_path, value):
        root = tmp_path / "host"
        root.mkdir()
        (root / ".heddle.yaml").write_text(
            f"layout:\n  plans: {value}\n", encoding="utf-8"
        )
        with pytest.raises(KernelError) as excinfo:
            load_project_config(root)
        assert excinfo.value.code == "workspace-invalid"
        assert "plans" in f"{excinfo.value.message} {excinfo.value.hint}", (
            "FAIL: the error must name the offending layout key"
        )

    def test_nested_relative_layout_paths_stay_legal(self):
        # The counter-case: golden declares `tests: src/tests` — nested
        # relative paths under the root are the normal adopter shape.
        config = load_project_config(GOLDEN)
        assert config.layout.tests == "src/tests"
        assert config.layout.source == "src/example"


class TestAC3SeamRetired:
    """AC-3: TESTS_ROOT is gone from heddle/gate/prompt.py and this
    repository carries its own .heddle.yaml with the §A2 host policy values
    (the exact literal pinned in the Feature Spec Decision Log). The
    behavioral half — prompt context building still works — is the existing
    tests/gate suite staying green (plan Milestone 1 Verification)."""

    def test_tests_root_constant_retired(self):
        source = (REPO_ROOT / "heddle" / "gate" / "prompt.py").read_text(
            encoding="utf-8"
        )
        assert "TESTS_ROOT" not in source, (
            "FAIL: heddle/gate/prompt.py still references TESTS_ROOT — the "
            "tests root must resolve from project config "
            "(load_project_config(ctx.repo_root).layout.tests, REQ-5)"
        )
        # The replacement must be config-sourced, not an inlined "tests"
        # literal — the behavioral discriminator on a non-default
        # layout.tests host lives in tests/gate/test_prompt_config_seam.py
        # (scaffolding review review, iteration 2).
        assert "load_project_config" in source and "layout.tests" in source, (
            "FAIL: heddle/gate/prompt.py must resolve the tests root via "
            "load_project_config(...).layout.tests (REQ-5) — retiring the "
            "constant by inlining a hardcoded 'tests' is not the seam"
        )

    def test_repo_heddle_yaml_carries_a2_host_policy(self):
        config = load_project_config(REPO_ROOT)
        assert config.root == REPO_ROOT
        assert config.layout == LayoutConfig(
            specs="docs/features",
            plans="plans",
            # A1: heddle's own corpus IS the packaged floor it ships.
            prompts="heddle/resources/prompts",
            schemas=".heddle/schemas",
            tests="tests",
            source="heddle",
        )
        assert dict(config.commands) == {
            "test": (
                ".venv/bin/python -m tests.proof_runner "
                "tests/kernel/test_project_config.py tests/runtime/test_schemas.py "
                "tests/runtime/test_boundaries.py::TestRuntimeImportBoundary::"
                "test_runtime_modules_import_only_declared_roots"
            ),
            "lint": ".venv/bin/ruff check heddle/ tests/",
            "typecheck": ".venv/bin/mypy heddle",
        }
        assert dict(config.agents) == {"claude": True, "codex": True}
        # gates deliberately omitted — no host narrowing; the tier matrix
        # alone governs (Decision Log).
        assert config.gates_enabled is None
        assert config.diagnostics == ()
