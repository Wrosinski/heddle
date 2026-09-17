"""Current command manifest and implemented-command boundaries."""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"

# Every command with a structured JSON response declares its stable output schema.
EXPECTED_OUTPUT_SCHEMAS = {
    "init": "heddle.init/v0",
    "run-gate": "heddle.run-gate/v1",
    "decisions list": "heddle.decisions-list/v1",
    "decisions add": "heddle.decisions-add/v1",
    "decisions resolve": "heddle.decisions-resolve/v1",
    "decisions record-policy": "heddle.decisions-record-policy/v1",
    "status": "heddle.status/v1",
    "search": "heddle.search/v0",
    "orient": "heddle.orient/v0",
    "feature switch": "heddle.feature-switch/v0",
    "feature complete": "heddle.feature-complete/v0",
    "help": "heddle.manifest/v0",
    "kickoff": "heddle.kickoff/v0",
    "doctor": "heddle.doctor/v0",
    "phase-exit": "heddle.phase-exit/v0",
    "milestone advance": "heddle.milestone-advance/v0",
    "task add": "heddle.task-add/v0",
    "task done": "heddle.task-done/v0",
    "task current": "heddle.task-current/v0",
    "verify": "heddle.verify/v0",
    "session log": "heddle.session-log/v0",
    "sync": "heddle.sync/v0",
    "validate": "heddle.validate/v0",
    "migrate": "heddle.migrate/v0",
    "feature start": "heddle.feature-start/v0",
    "milestone add": "heddle.milestone-add/v0",
    "milestone edit": "heddle.milestone-edit/v0",
}


class TestCommandManifestActivation:
    """Schema-bearing commands are declared and implemented commands are live."""

    def test_manifest_declares_current_output_schemas(self, run_cli, envelope_tools):
        code, out, _err = run_cli(["help", "--json"])
        assert code == 0
        manifest = envelope_tools.parse(out)["data"]
        for entry in manifest["commands"]:
            expected = EXPECTED_OUTPUT_SCHEMAS.get(entry["name"])
            assert entry["output_schema"] == expected, (
                f"FAIL [{entry['name']}]: output_schema must be {expected!r} — "
                "schema-bearing commands must match their stable identifiers and "
                "commands without a schema must stay null"
            )

    def test_milestone_add_requires_its_payload(self, run_cli, envelope_tools):
        # A bare invocation reaches the live handler and reports the missing payload.
        code, out, _err = run_cli(["milestone", "add", "--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"
        assert "from-file" in envelope["error"]["message"], (
            "FAIL: the live handler must ask for the --from-file payload"
        )

    def test_status_all_returns_each_workspace(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        # The portfolio view answers with one row per workspace.
        host = tmp_path / "host"
        shutil.copytree(GOLDEN, host)
        monkeypatch.chdir(host)
        code, out, _err = run_cli(["status", "--all", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        slugs = [row["feature"] for row in envelope["data"]["features"]]
        assert slugs == ["nl-screening", "tier1-quickfix"], (
            "FAIL: the live portfolio must list the golden pair in slug order"
        )

    def test_implemented_commands_no_longer_stub(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        # Outside a project, each command reaches its real resolution or usage path.
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        monkeypatch.chdir(outside)
        for argv, expected_code in (
            (["status", "--json"], "workspace-invalid"),
            (["orient", "--json"], "workspace-invalid"),
            (["feature", "switch", "x", "--json"], "usage"),
        ):
            _code, out, _err = run_cli(argv)
            envelope = envelope_tools.parse(out)
            assert envelope["error"]["code"] == expected_code, (
                f"FAIL {argv}: expected {expected_code}, got "
                f"{envelope['error']['code']} — implemented commands must not "
                "return the unavailable-command stub"
            )
