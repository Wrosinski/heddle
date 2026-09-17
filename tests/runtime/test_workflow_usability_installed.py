"""Installed delivery witnesses for workflow-usability public surfaces."""

from __future__ import annotations

import json

import pytest

from tests.runtime.wheel_harness import build_installed_wheel

pytestmark = pytest.mark.toolchain


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """Build one isolated candidate wheel shared by independent contract probes."""
    return build_installed_wheel(tmp_path_factory.mktemp("workflow-usability-wheel"))


def _manifest(installed, cwd) -> dict[str, dict]:
    result = installed.run("help", "--json", cwd=cwd)
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    return {row["name"]: row for row in envelope["data"]["commands"]}


def _resource(installed, name: str, cwd) -> str:
    probe = installed.python_probe(
        "from importlib.resources import files; "
        f"print((files('heddle.resources')/'{name}').read_text())",
        cwd=cwd,
    )
    assert probe.returncode == 0, probe.stderr
    return probe.stdout


def test_ac1_candidate_wheel_delivers_nested_authoring_contracts(
    installed, tmp_path
) -> None:
    """Installed AC-1 red: all three decoder inputs are public and packaged."""
    commands = _manifest(installed, tmp_path)
    for name, schema_id in (
        ("feature prepare", "heddle.intake-input/v1"),
        ("feature policy", "heddle.feature-policy/v1"),
        ("review disposition", "heddle.review-disposition-input/v1"),
    ):
        schema = commands[name]["input_schema"]
        assert schema is not None, f"FAIL installed AC-1: {name} has no input schema"
        assert schema["id"] == schema_id and schema["fields"] and schema["example"]


def test_ac3_candidate_wheel_delivers_approval_and_qualification_guidance(
    installed, tmp_path
) -> None:
    """Installed AC-3 red: packaged guidance explains evidence authority."""
    text = " ".join(
        (
            _resource(installed, "specify.briefing.md", tmp_path)
            + _resource(installed, "spec-review.briefing.md", tmp_path)
        ).split()
    ).casefold()
    for phrase in (
        "does not grant approval",
        "owner revision",
        "review_run_id",
        "verification_scope",
        "clean later report",
    ):
        assert phrase in text, f"FAIL installed AC-3: missing {phrase!r}"


def test_ac10_candidate_wheel_delivers_truthful_verify_help(
    installed, tmp_path
) -> None:
    """Installed AC-10 red: candidate CLI advertises only native proof scopes."""
    result = installed.run("verify", "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    folded = " ".join((result.stdout + result.stderr).split()).casefold()
    assert "feature|" not in folded
    assert "m<n>" in folded and "smoke" in folded and "acceptance" in folded
    assert "progressive" in folded


def test_ac11_candidate_wheel_delivers_close_obligation_guidance(
    installed, tmp_path
) -> None:
    """Installed AC-11 red: packaged briefings identify the host-owned command."""
    help_result = installed.run("feature", "complete", "--help", cwd=tmp_path)
    assert help_result.returncode == 0, help_result.stderr
    surfaces = {
        "feature complete --help": help_result.stdout + help_result.stderr,
        **{
            name: _resource(installed, name, tmp_path)
            for name in (
                "specify.briefing.md",
                "scaffold.briefing.md",
                "implement.briefing.md",
                "complete.briefing.md",
                "heddle-yaml.scaffold.yaml",
            )
        },
    }
    required = (
        "autopilot.test_command",
        "host tooling",
        "feature proof",
        "additional close",
        "does not create a clean environment",
    )
    for surface, text in surfaces.items():
        folded = " ".join(text.split()).casefold()
        for phrase in required:
            assert phrase in folded, f"FAIL installed AC-11: {surface} omits {phrase!r}"


def test_ac12_candidate_wheel_delivers_preview_reporting_contract(
    installed, tmp_path
) -> None:
    """Installed AC-12 red: completion guidance preserves preview execution truth."""
    text = " ".join(
        _resource(installed, "complete.briefing.md", tmp_path).split()
    ).casefold()
    assert "close_obligation" in text
    assert "dry-run" in text and "does not run" in text


def test_ac12_candidate_wheel_uses_only_installed_resources_and_no_network(
    installed, tmp_path
) -> None:
    """Installed AC-12 survivor: candidate delivery is checkout and network isolated."""
    assert "Heddle" in _resource(installed, "complete.briefing.md", tmp_path)
    assert (
        not installed.forbidden_log.exists() or not installed.forbidden_log.read_text()
    )
    assert not installed.network_log.exists() or not installed.network_log.read_text()
