"""Installed scaffold admission: reject, repair, and record one controlled review."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.content_identity_helpers import git
from tests.operational_model_helpers import SPEC, read
from tests.runtime.wheel_harness import (
    build_installed_wheel,
    parse_envelope,
    write_claude_shim,
)
from tests.tiering_helpers import FABLE, entry
from tests.tiering_review_helpers import V7_FEATURE, current_host, review_content


@pytest.mark.acceptance
@pytest.mark.e2e
@pytest.mark.toolchain
def test_installed_scaffold_binding_admission(tmp_path: Path) -> None:
    """AC-2/3/5/8: installed native boundaries enforce the same binding contract."""
    installed = build_installed_wheel(tmp_path / "installed")
    with pytest.MonkeyPatch.context() as patch:
        host, state_path = current_host(
            tmp_path,
            patch,
            stage="scaffold",
            overrides={
                "review-test-scaffolding": entry(
                    "review-test-scaffolding", primary=FABLE, limit=1
                )
            },
        )

    # Outside configured tests/, unchanged, and absent from plan/commands/diff.
    # Only Verified-by can make this source part of the scaffold review basis.
    relative = "contract_checks/test_value.py"
    bound = host / relative
    bound.parent.mkdir()
    test_source = (
        "from pathlib import Path\n\n"
        "def test_value():\n"
        "    assert 'VALUE = 7' in Path('src/example.py').read_text()\n"
    )
    bound.write_text(test_source, encoding="utf-8")
    git(host, "add", relative)
    git(host, "commit", "-qm", "synthetic primary test")
    spec = host / SPEC
    spec_text = (
        "# Value contract\n\n## Acceptance Criteria\n\n"
        "### AC-1: Declared value\n\nPriority: MUST\n"
        "Action: Read VALUE.\nExpected: The value is 7.\n"
        f"Verified-by: {relative}::test_missing\n"
    )
    spec.write_text(spec_text, encoding="utf-8")

    provider_log = tmp_path / "provider-calls.jsonl"
    terminal = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": review_content("review-test-scaffolding"),
            }
        )
        + "\n"
    )
    binary = write_claude_shim(tmp_path / "controlled-bin", terminal, provider_log)
    env = {
        **installed.env,
        "PATH": str(binary.parent) + os.pathsep + installed.env["PATH"],
    }

    def execute(*arguments: str, expected: int = 0):
        result = installed.run(
            *arguments, "--feature", V7_FEATURE, "--json", cwd=host, env=env
        )
        envelope = parse_envelope(result)
        assert result.returncode == expected, (result.stderr, envelope)
        return envelope

    before = state_path.read_bytes()
    before_revision = read(state_path)["revision"]
    for action in ("show-prompt", "run-gate"):
        refused = execute(action, "review-test-scaffolding", expected=3)
        assert not refused["ok"]
        assert refused["error"]["code"] == "workspace-invalid"
        assert "AC-1" in refused["error"]["message"]
        assert f"{relative}::test_missing" in refused["error"]["message"]
        assert state_path.read_bytes() == before
        assert not provider_log.exists()

    spec.write_text(
        spec_text.replace("::test_missing", "::test_value"), encoding="utf-8"
    )
    preview = execute("show-prompt", "review-test-scaffolding")
    assert preview["ok"]
    assert state_path.read_bytes() == before
    assert "structural linkage" in preview["data"]["prompt"]
    kickoff = execute("kickoff")
    assert "before calling the reviewer" in kickoff["data"]["briefing"]

    # A new capture sees an edit to a file outside ordinary test discovery.
    bound.write_text(test_source.replace("VALUE = 7", "VALUE = 8"), encoding="utf-8")
    changed = execute("show-prompt", "review-test-scaffolding")
    assert (
        preview["data"]["prompt_identity"]["review_basis_hash"]
        != changed["data"]["prompt_identity"]["review_basis_hash"]
    )
    bound.write_text(test_source, encoding="utf-8")
    restored = execute("show-prompt", "review-test-scaffolding")
    assert (
        restored["data"]["prompt_identity"]["review_basis_hash"]
        == preview["data"]["prompt_identity"]["review_basis_hash"]
    )
    assert state_path.read_bytes() == before
    assert not provider_log.exists()

    reviewed = execute("run-gate", "review-test-scaffolding")
    assert reviewed["ok"] and reviewed["data"]["accepted"]
    calls = provider_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    assert relative in json.loads(calls[0])["stdin"]
    recorded = read(state_path)
    assert recorded["revision"] > before_revision
    assert len(recorded["review_assignments"]["attempts"]) == 1
    assert (host / reviewed["data"]["artifact"]).is_file()
    status = execute("status")
    assignment = next(
        row
        for row in status["data"]["review_closure"]["assignments"]
        if row["role"] == "review-test-scaffolding"
    )
    assert assignment["calls_completed"] == 1

    assert len(provider_log.read_text(encoding="utf-8").splitlines()) == 1

    probe = installed.python_probe(
        "import heddle.kernel.test_bindings as bindings; print(bindings.__file__)",
        cwd=host,
    )
    assert probe.returncode == 0, probe.stderr
    assert Path(probe.stdout.strip()).is_relative_to(installed.site_packages)
    assert not installed.forbidden_log.exists()
    assert not installed.network_log.exists()
