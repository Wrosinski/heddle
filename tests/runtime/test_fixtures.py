"""Fixture-corpus layout, vocabulary, and synthetic-domain contracts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from heddle.contracts import schemas  # red-phase import — Milestone 1 module

# Paths are defined locally: with --import-mode=importlib and no __init__.py,
# test modules cannot import conftest by name (helpers arrive via fixtures).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACES_ROOT = REPO_ROOT / "tests" / "fixtures" / "workspaces"
TINY = WORKSPACES_ROOT / "tiny"
GOLDEN = WORKSPACES_ROOT / "golden"

STATE_FILES = sorted(WORKSPACES_ROOT.rglob("state.yaml"))
STATE_IDS = [str(path.relative_to(WORKSPACES_ROOT)) for path in STATE_FILES]

NL_WORKSPACE = GOLDEN / "plans" / "nl-screening"
T1_WORKSPACE = GOLDEN / "plans" / "tier1-quickfix"


class TestCorpusLayout:
    """Both fixture hosts are committed and exercised."""

    def test_corpus_root_layout(self):
        assert TINY.is_dir(), "FAIL: tests/fixtures/workspaces/tiny/ missing"
        assert GOLDEN.is_dir(), "FAIL: tests/fixtures/workspaces/golden/ missing"
        assert (WORKSPACES_ROOT / "README.md").is_file(), (
            "FAIL: the edge-case coverage map README is part of the corpus"
        )


@pytest.mark.parametrize("state_path", STATE_FILES, ids=STATE_IDS)
class TestStateVocabularyConformance:
    """Every fixture state file conforms to the current vocabulary."""

    def test_artifact_paths_resolve(self, corpus, state_path):
        corpus.assert_artifact_paths_resolve(state_path)

    def test_spec_path_resolves_in_host(self, corpus, state_path):
        host_root = state_path.parent.parent.parent  # plans/<feature>/state.yaml
        state = corpus.load_yaml(state_path)
        assert (host_root / state["spec"]).is_file(), (
            f"FAIL: spec path {state['spec']} does not resolve inside the host"
        )

    def test_plan_prose_id_join(self, corpus, state_path):
        # §4.1: every state milestone has a `### Milestone <id>:` prose
        # section in the workspace's plan.md.
        state = corpus.load_yaml(state_path)
        if not state["milestones"]:
            return  # Tier 1 empty skeleton has no plan prose to join
        plan = state_path.parent / schemas.WORKSPACE_PLAN
        assert plan.is_file(), f"FAIL: {plan} missing beside state.yaml"
        prose = plan.read_text(encoding="utf-8")
        for milestone in state["milestones"]:
            assert f"### Milestone {milestone['id']}:" in prose, (
                f"FAIL: plan.md lacks the prose section for {milestone['id']}"
            )


class TestAC7TinyFixture:
    """AC-7: the tiny fixture workspace is committed and exercised."""

    def test_tiny_layout(self):
        assert (TINY / ".heddle.yaml").is_file()
        assert (TINY / "docs" / "features" / "example" / "sample-feature.md").is_file()
        workspace = TINY / "plans" / "sample-feature"
        assert (workspace / schemas.WORKSPACE_PLAN).is_file()
        assert (workspace / schemas.WORKSPACE_STATE).is_file()

    def test_tiny_single_workspace_uniqueness_inference(self):
        # feature resolution case 3: exactly one workspace — uniqueness inference
        # resolves it.
        workspaces = [p for p in (TINY / "plans").iterdir() if p.is_dir()]
        assert len(workspaces) == 1


class TestAC8GoldenEdgeCases:
    """AC-8: the golden fixture carries the six enumerated edge cases."""

    def test_ac_unassigned_validate_case(self, corpus):
        # AC-4 of nl-screening is deliberately in no milestone's satisfies —
        # the `ac-unassigned` cross-plane case validation's validate tests against.
        spec_text = (
            GOLDEN / "docs" / "features" / "analysis" / "nl-screening.md"
        ).read_text(encoding="utf-8")
        spec_acs = set(re.findall(r"^### (AC-\d+)", spec_text, re.MULTILINE))
        assert spec_acs == {"AC-1", "AC-2", "AC-3", "AC-4"}
        state = corpus.load_yaml(NL_WORKSPACE / "state.yaml")
        assigned = {
            ac for milestone in state["milestones"] for ac in milestone["satisfies"]
        }
        assert "AC-4" in spec_acs - assigned, (
            "FAIL: AC-4 must remain unassigned in every milestone's satisfies"
        )

    def test_legacy_migration_input_pair(self):
        legacy_plan = GOLDEN / "plans" / "legacy-feature.plan.md"
        legacy_review = GOLDEN / "plans" / "legacy-feature.self-review-m1.md"
        assert legacy_plan.is_file(), "FAIL: legacy one-document plan missing"
        assert legacy_review.is_file(), "FAIL: dotted-suffix artifact missing"
        plan_text = legacy_plan.read_text(encoding="utf-8")
        # Legacy facts the §13 migration map extracts.
        assert "current_milestone:" in plan_text
        assert "total_milestones:" in plan_text
        assert "← current" in plan_text

    def test_multi_workspace_ambiguity(self):
        # feature resolution case 4: two workspace directories, no recorded pointer.
        workspaces = sorted(
            p.name
            for p in (GOLDEN / "plans").iterdir()
            if p.is_dir() and (p / "state.yaml").is_file()
        )
        assert workspaces == ["nl-screening", "tier1-quickfix"]


class TestAC10FixtureConfigs:
    """AC-10 (fixture half): both hosts carry conforming .heddle.yaml files."""

    def test_tiny_minimal_config_conforms(self, corpus):
        corpus.assert_heddle_yaml_conforms(TINY / ".heddle.yaml")

    def test_golden_full_config_conforms(self, corpus):
        config_path = GOLDEN / ".heddle.yaml"
        corpus.assert_heddle_yaml_conforms(config_path)
        config = corpus.load_yaml(config_path)
        # Golden carries every pre-autopilot configuration section, incl. gates.enabled;
        # the
        # additive autopilot autopilot and adoption sync sections remain optional for
        # legacy hosts.
        assert set(config.keys()) == set(schemas.HEDDLE_YAML_SECTIONS) - {
            "autopilot",
            "sync",
        }
        assert set(config["layout"].keys()) == set(schemas.LAYOUT_KEYS)
        assert set(config["commands"].keys()) == set(schemas.HOST_COMMAND_KEYS)
        assert config["gates"]["enabled"], "FAIL: gates.enabled must be non-empty"


class TestAC13NoPrivateSourceStrings:
    """The corpus is sanitized to the example domain."""

    BANNED_TOKENS = ("private-source-host", "sourceapp-")

    def test_synthetic_hosts_use_example_domain(self, corpus):
        golden_config = corpus.load_yaml(GOLDEN / ".heddle.yaml")
        assert "example" in golden_config["layout"]["source"], (
            "FAIL: the golden host's source root must live in the example domain"
        )
        assert (TINY / "docs" / "features" / "example").is_dir(), (
            "FAIL: the tiny host's spec area must be the example domain"
        )

    def test_no_banned_tokens_anywhere(self):
        offenders: list[str] = []
        for path in sorted(WORKSPACES_ROOT.rglob("*")):
            if not path.is_file():
                continue
            haystacks = [path.name.lower(), path.read_text(encoding="utf-8").lower()]
            for token in self.BANNED_TOKENS:
                if any(token in haystack for haystack in haystacks):
                    offenders.append(str(path.relative_to(WORKSPACES_ROOT)))
        assert not offenders, (
            f"FAIL: host-identifying strings found in fixture corpus: {offenders} "
            "— synthetic hosts use the example domain"
        )
