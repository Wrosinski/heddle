"""Write-path command manifest and repository guardrails."""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

WRITE_PATH_LEAVES = {
    "phase-exit",
    "milestone advance",
    "task add",
    "task done",
    "task current",
    "verify",
    "session log",
}

MILESTONE_AUTHORING = {"milestone add", "milestone edit"}


def _expected_exit_codes(name: str) -> list[int]:
    return (
        [0, 1, 2, 3, 4, 5]
        if name in {"phase-exit", "milestone advance"}
        else [0, 1, 2, 3, 5]
    )


def test_ac14_manifest_flips_only_write_path_commands(run_cli, envelope_tools) -> None:
    # AC-14: the write leaves flip to live output schemas; out-of-scope
    # milestone authoring retains its separate schema identifiers.
    from heddle.runtime.contracts import COMMAND_SURFACE

    code, out, _err = run_cli(["help", "--json"])
    assert code == 0
    commands = {
        entry["name"]: entry for entry in envelope_tools.parse(out)["data"]["commands"]
    }
    contracts = {contract.name: contract for contract in COMMAND_SURFACE}

    for name in WRITE_PATH_LEAVES:
        entry = commands[name]
        assert entry["mutating"] is True
        assert entry["dry_run"] is True
        assert entry["exit_codes"] == _expected_exit_codes(name), (
            f"FAIL AC-14: {name} must expose its current write-result exit set"
        )
        assert entry["output_schema"], (
            f"FAIL AC-14: {name} must flip from not-implemented to a pinned "
            "output_schema id"
        )

    for name in MILESTONE_AUTHORING:
        entry = commands[name]
        assert contracts[name].cli_binding is not None
        assert entry["output_schema"] == f"heddle.{name.replace(' ', '-')}/v0", (
            f"FAIL AC-14: {name} landed with its authoring command group, "
            f"pinned schema id, got {entry!r}"
        )


@pytest.mark.parametrize("name", sorted(WRITE_PATH_LEAVES))
def test_ac14_each_write_command_json_retired_not_implemented_stub(
    run_cli, envelope_tools, name: str, monkeypatch, tmp_path
) -> None:
    # AC-14: command execution is live, not the command availability stub. Arguments are
    # minimal;
    # usage/blocking failures are acceptable for underspecified invocations, but
    # not `not-implemented`. commands B0 micro-edit: anchored outside any project —
    # a stub still fails not-implemented at dispatch from any cwd, but at the
    # repo root a "minimal" `phase-exit --json` resolves the host's unique
    # flow: auto workspace and RECORDS a policy grant (a production
    # state.yaml write) once no standing blocker fires.
    monkeypatch.chdir(tmp_path)
    code, out, _err = run_cli([*name.split(), "--json"])
    envelope = envelope_tools.parse(out)
    envelope_tools.assert_shape(envelope)
    assert envelope.get("error", {}).get("code") != "not-implemented", (
        f"FAIL AC-14: heddle {name} still returns the not-implemented stub"
    )
    assert code in {0, 1, 2, 3, 5}, (
        f"FAIL AC-14: heddle {name} returned exit {code}, outside M4's exit set"
    )


def test_ac16_nine_a2_guardrails_are_ported_with_local_precommit_hooks() -> None:
    # AC-16: increment 11 ports the genuine A2 guardrails and wires them into
    # local pre-commit. This test pins names and hook wiring rather than relying
    # on prose in the plan.
    expected_scripts = {
        "check-prompt-conventions",
        "check-no-src-print",
        "check-subprocess-handlers",
        "check-enforcement-baseline-doc-language-aware",
        "check-ac-test-coverage",
    }
    # The four workflow-owned guards moved from scripts/ into the packaged
    # hook library (adoption host-tooling increment 1, owner rulings 2026-07-22)
    # — same guards, single-source packaged files.
    packaged_guardrails = {
        "check-test-deletions",
        "check-bypass-justification",
        "check-no-committed-gate-locks",
        "check-skip-only-test-scaffolding",
    }
    scripts = {
        path.name.removesuffix(".py").removesuffix(".sh")
        for path in (REPO_ROOT / "scripts").iterdir()
    }
    missing = expected_scripts - scripts
    assert not missing, (
        f"FAIL AC-16: missing ported guardrail scripts {sorted(missing)}"
    )
    hooks_root = REPO_ROOT / "heddle" / "resources" / "hooks"
    missing_packaged = {
        name
        for name in packaged_guardrails
        if not (hooks_root / f"{name}.py").is_file()
    }
    assert not missing_packaged, (
        f"FAIL AC-16: missing packaged guardrails {sorted(missing_packaged)}"
    )

    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text("utf-8"))
    local_hook_entries = {
        hook["id"]: hook
        for repo in config["repos"]
        if repo.get("repo") == "local"
        for hook in repo.get("hooks", [])
    }
    missing_hooks = (expected_scripts | packaged_guardrails).difference(
        local_hook_entries
    )
    assert not missing_hooks, (
        f"FAIL AC-16: local pre-commit hooks missing {sorted(missing_hooks)}"
    )
    assert local_hook_entries["check-bypass-justification"].get("stages") == [
        "commit-msg"
    ], "FAIL AC-16: bypass justification must be wired as a commit-msg hook"


def test_ac17_write_path_and_phase_exit_bars_are_observable_in_manifest() -> None:
    # AC-17: the final milestone exit bar is mechanically visible before the
    # full acceptance run: every operational write is CAS-capable/dry-run-able,
    # and phase-exit is one of those writes.
    from heddle.runtime.contracts import COMMAND_SURFACE

    by_name = {contract.name: contract for contract in COMMAND_SURFACE}
    for name in WRITE_PATH_LEAVES:
        contract = by_name[name]
        assert contract.dry_run is True, (
            f"FAIL AC-17: {name} must be dry-run-able at the write-path bar"
        )
        assert list(contract.exit_codes) == _expected_exit_codes(name), (
            f"FAIL AC-17: {name} must be CAS-capable with exit 5"
        )
        assert contract.output_schema is not None, (
            f"FAIL AC-17: {name} must be live, not a M4 stub"
        )
    assert "phase-exit" in by_name and by_name["phase-exit"].dry_run is True
