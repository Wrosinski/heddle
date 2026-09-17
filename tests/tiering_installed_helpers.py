"""Installed-only journey support; call only under the explicit e2e grant."""

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

import yaml

from tests.content_identity_helpers import git
from tests.runtime.wheel_harness import make_git_host, parse_envelope
from tests.tiering_helpers import ROLES, entry, prepare_input, wire_policy

FEATURE = "tiering-installed"
SPEC = f"docs/features/runtime/{FEATURE}.md"


@dataclass
class InstalledJourney:
    installed: object
    root: Path
    evidence: Path
    env: dict
    transcript: list = field(default_factory=list)

    @property
    def state(self):
        return self.root / "plans" / FEATURE / "state.yaml"

    def run(self, *args, feature=True, expected=(0,)):
        argv = [*args, *(("--feature", FEATURE) if feature else ()), "--json"]
        result = self.installed.run(*argv, cwd=self.root, env=self.env)
        self.transcript.append(
            {
                "argv": argv,
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        self.evidence.write_text(
            json.dumps(
                {
                    "command": str(self.installed.command),
                    "wheel_sha256": sha256(
                        next((self.installed.root / "dist").glob("*.whl")).read_bytes()
                    ).hexdigest(),
                    "commands": self.transcript,
                },
                indent=2,
            )
            + "\n"
        )
        envelope = parse_envelope(result)
        assert result.returncode in expected, (argv, envelope)
        return envelope

    def payload(self, name, value):
        path = self.evidence.parent / name
        path.write_text(json.dumps(value))
        return str(path)


def isolated_env(installed, tmp_path):
    env = dict(installed.env)
    # No ambient user home, model credentials, plugins or provider opt-ins.
    home = tmp_path / "isolated-home"
    home.mkdir()
    env.update(HOME=str(home), HEDDLE_RUN_LIVE="0")
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
    ):
        env.pop(key, None)
    return env


def journey(installed, tmp_path):
    root, _nested = make_git_host(tmp_path, "installed-host")
    git(root, "config", "user.name", "Tiering Installed Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    env = isolated_env(installed, tmp_path)
    result = InstalledJourney(installed, root, tmp_path / "journey.json", env)
    result.run("init", feature=False)
    (root / ".heddle.yaml").write_text(
        "autopilot:\n  test_command: python3 tests/check.py\n"
    )
    (root / "docs/workflow/engineering-principles.md").write_text(
        "---\nstatus: ratified\n---\n\n# Engineering Principles\n\n"
        "Preserve user-owned bytes.\n"
    )
    (root / "brief.md").write_text("# Research\nDeliver one observable value.\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "installed host source baseline")
    return result


def admit(journey, *, route="heddle", overrides=None):
    prepared = journey.payload("prepare.json", prepare_input(route=route))
    journey.run(
        "feature",
        "prepare",
        FEATURE,
        "--area",
        "runtime",
        "--from-file",
        prepared,
        feature=False,
    )
    if route == "direct":
        return
    policy = journey.payload("policy.json", wire_policy(overrides=overrides))
    journey.run("feature", "policy", FEATURE, "--from-file", policy, feature=False)
    journey.run("feature", "start", FEATURE, feature=False)


def all_off():
    return {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }


def author_value(journey):
    root = journey.root
    (root / "src").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "src/example.py").write_text("VALUE = 0\n")
    (root / "tests/check.py").write_text(
        "import os, runpy\ndef test_declared_value():\n"
        "    assert os.getenv('HEDDLE_FIXTURE_FAIL') != '1', "
        "'injected runner failure'\n"
        "    assert runpy.run_path('src/example.py')['VALUE'] == 7\n"
        "if __name__ == '__main__':\n    test_declared_value()\n"
    )
    (root / SPEC).write_text(
        f"---\ntype: feature-spec\nfeature_name: {FEATURE}\n"
        "area: runtime\nlifecycle: active\n---\n\n# Declared value\n\n"
        "## Purpose\n\nDeliver VALUE = 7.\n\n## Acceptance Criteria\n\n"
        "### AC-1: Value\n\nPriority: MUST\nAction: Read VALUE.\n"
        "Expected: VALUE equals 7.\n"
        "Verified-by: tests/check.py::test_declared_value\n\n"
        "## Design Commitments\n\nThe module owns one value.\n\n"
        "## Decision Log\n\nNo additional decisions.\n"
    )
    (journey.state.parent / "plan.md").write_text(
        "# Plan\n\n## Technical Architecture\n\nThe module owns the value "
        "and a separate test observes it.\n\n### Milestone m1: Value\n\n"
        "Satisfies: AC-1\n\nChange VALUE to 7.\n"
    )
    (journey.state.parent / "brief.md").write_text("# Brief\nDeliver VALUE = 7.\n")
    for suffix, text in (
        ("decision-journal", "# Decision Journal\n"),
        (
            "friction-retrospective",
            "# Friction Retrospective\n\n"
            + "\n\n".join(
                f"## {heading}\n\nNone observed."
                for heading in (
                    "Tooling fights",
                    "Prompt gaps",
                    "Validator false-positives",
                    "Escalations that should have been policy-resolvable",
                )
            )
            + "\n",
        ),
    ):
        (root / "plans" / f"{FEATURE}.{suffix}.md").write_text(text)
    milestone = journey.payload(
        "milestone.json",
        {
            "title": "Value",
            "complexity": "low",
            "satisfies": ["AC-1"],
            "depends_on": [],
            "owns": [SPEC, "src", "tests/check.py"],
            "verification": {
                "command": "python3 tests/check.py",
                "expected": "VALUE is 7",
            },
        },
    )
    journey.run("milestone", "add", "--from-file", milestone)
    for key in ("test_command", "acceptance_test", "smoke_test"):
        journey.run("commands", "set", key, "--command", "python3 tests/check.py")
    git(root, "add", ".")
    git(root, "commit", "-qm", "authored native installed feature")


def finish_value(journey):
    for stage in ("spec-review", "plan-review", "scaffold", "implement"):
        journey.run("phase-exit", "--through", "complete")
        assert yaml.safe_load(journey.state.read_text())["stage"] == stage
        if stage in {"scaffold", "implement"}:
            rendered = journey.run("kickoff")["data"]
            text = json.dumps(rendered)
            assert "heddle status --json" in rendered["briefing"]
            status = journey.run("status")["data"]
            assert status["stage"] == stage
            assert {
                key: status["verification_commands"][key]
                for key in ("test_command", "acceptance_test", "smoke_test")
            } == dict.fromkeys(
                ("test_command", "acceptance_test", "smoke_test"),
                "python3 tests/check.py",
            )
            assert (
                "Read the plan's Quick Orientation, current Design Context" not in text
            )
            assert "Design Context derived from the spec" not in text
            if stage == "scaffold":
                assert "prerequisite" in text.lower()
    journey.run("milestone", "advance")
    journey.run("task", "add", "Implement VALUE = 7")
    task = yaml.safe_load(journey.state.read_text())["milestones"][0]["tasks"][-1]["id"]
    (journey.root / "src/example.py").write_text("VALUE = 7\n")
    journey.run("task", "done", task)
    journey.run("verify", "--scope", "m1")
    journey.run("milestone", "advance")
    for scope in ("m1", "acceptance", "smoke"):
        journey.run("verify", "--scope", scope)
    for stage in ("peer-review", "robustness", "complete"):
        journey.run("phase-exit")
        assert yaml.safe_load(journey.state.read_text())["stage"] == stage
