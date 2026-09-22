"""
Dispatcher behavior tests — Milestone 2.

Covers: AC-1 (`help --json` manifest envelope), AC-3 (not-implemented stubs),
AC-4 (envelope invariants across the dispatcher), AC-6 (run-gate routing — the
core verbatim passthrough is retired; run-gate now routes through the runtime
handler, Checkpoint 2 lifecycle boundary / gate envelope), AC-12 (human output paths
JSON-free).
Behavior contract: core-contract-rails

Red-phase note (historical): until Milestone 2 this module was gated by a
pytest.importorskip on heddle.runtime.dispatch so the plan-level
test_command stayed green at the Milestone 1→2 boundary. The guard was
retired to a hard import at feature completion (Checkpoint 2 configuration / peer
review review) so an import-time break fails loudly instead of silently
skipping the suite.
"""

from __future__ import annotations

import importlib.metadata
import json
import shutil
from pathlib import Path

import pytest

import heddle.runtime.contracts as contracts_module
import heddle.runtime.dispatch as dispatch_module
from heddle.contracts.result import ERROR_CODES
from heddle.runtime.contracts import COMMAND_SURFACE

TINY = Path(__file__).resolve().parent.parent / "fixtures" / "workspaces" / "tiny"

# REQ-18: known nouns whose missing/unrecognized verb is a usage error
# (`decisions` excepted — it is the single bare-noun alias, command alias).
NOUN_VERBS = {
    "feature": ["start", "switch"],
    "milestone": ["add", "edit", "advance"],
    "task": ["add", "done", "current"],
    "session": ["log"],
}


def assert_not_json(text: str) -> None:
    """REQ-8: human mode emits no JSON envelope."""
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


@pytest.mark.parametrize(
    ("argv", "module", "function", "expected"),
    [
        (
            ["status", "--feature", "sample"],
            "status",
            "run_status",
            (["--feature", "sample"], True),
        ),
        (
            ["task", "done", "m1-t1"],
            "write_path",
            "run_task",
            ("done", ["m1-t1"], True),
        ),
        (
            ["decisions", "record-policy", "--from-file", "-"],
            "decisions",
            "run_decisions",
            (["record-policy", "--from-file", "-"], True),
        ),
        (
            ["feature", "inputs", "set", "--from-file", "-"],
            "feature_inputs",
            "run_feature_inputs_set",
            (["--from-file", "-"], True),
        ),
    ],
)
def test_declared_cli_adapters_preserve_argument_shapes(
    monkeypatch, argv, module, function, expected
):
    from importlib import import_module

    calls = []

    def handler(*arguments):
        calls.append(arguments)
        return 5

    monkeypatch.setattr(import_module(f"heddle.runtime.{module}"), function, handler)
    assert dispatch_module.run(["--json", *argv]) == 5
    assert calls == [expected]


class TestAC1HelpJson:
    """AC-1: `help --json` returns the runtime interface manifest in the envelope."""

    def test_help_json_returns_manifest_envelope(self, run_cli, envelope_tools):
        code, out, _err = run_cli(["help", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        assert envelope["ok"] is True
        assert envelope["schema_version"] == "heddle.envelope/v2"
        data = envelope["data"]
        assert set(data.keys()) == {"schema_version", "runtime_version", "commands"}
        assert data["schema_version"] == "heddle.manifest/v0"
        assert data["runtime_version"] == importlib.metadata.version("heddle")
        assert len(data["commands"]) == len(COMMAND_SURFACE)

    def test_cli_manifest_matches_contract_builder(self, run_cli, envelope_tools):
        # The CLI serves exactly the contract module's manifest — the deep
        # per-command payload lock lives in test_contracts.py (AC-2).
        _code, out, _err = run_cli(["help", "--json"])
        envelope = envelope_tools.parse(out)
        assert envelope["data"] == contracts_module.build_manifest()


class TestAC3CommandRouting:
    """Every advertised command routes through its current implementation."""

    def test_init_preview_human_mode_no_json(self, run_cli):
        code, out, _err = run_cli(["init", "--dry-run"])
        assert code == 0
        assert out.strip()
        assert_not_json(out)

    def test_decisions_bare_noun_alias_resolves_before_stub_check(
        self, run_cli, envelope_tools, monkeypatch, tmp_path
    ):
        # command alias: `decisions` ≡ `decisions list`. autopilot forward-ports the
        # decisions
        # handlers, so the alias must now route to the live list command.
        from tests.tiering_review_helpers import current_host

        current_host(tmp_path, monkeypatch)
        code, out, _err = run_cli(["decisions", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        assert envelope["ok"] is True
        assert envelope["data"]["feature"] == "operational-example"
        assert "decisions" in envelope["data"]


class TestAC4EnvelopeInvariants:
    """AC-4: envelope invariants hold across the dispatcher's behavior
    classes (success, structured failure, usage paths, internal error)."""

    def test_unknown_command_json(self, run_cli, envelope_tools):
        code, out, _err = run_cli(["frobnicate", "--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        assert envelope["error"]["code"] == "usage"
        assert "heddle help" in envelope["error"]["hint"]

    def test_unknown_command_human(self, run_cli):
        code, out, _err = run_cli(["frobnicate"])
        assert code == 2
        assert_not_json(out)

    def test_bare_invocation_usage_on_stderr(self, run_cli):
        code, out, err = run_cli([])
        assert code == 2
        assert err.strip(), "FAIL: bare `heddle` puts usage text on stderr"
        assert not out.strip(), (
            "FAIL: bare `heddle` keeps stdout empty — usage text belongs on "
            "stderr only (peer review SY-M2)"
        )

    def test_bare_invocation_with_json_gets_usage_envelope(
        self, run_cli, envelope_tools
    ):
        # Boundary condition: JSON mode wins over shim parity.
        code, out, _err = run_cli(["--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"

    @pytest.mark.parametrize("noun", sorted(NOUN_VERBS))
    def test_bare_noun_usage_lists_verbs(self, run_cli, envelope_tools, noun):
        code, out, _err = run_cli([noun, "--json"])
        assert code == 2, f"FAIL [{noun}]: bare noun is a usage error (REQ-18)"
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"
        for verb in NOUN_VERBS[noun]:
            assert verb in envelope["error"]["hint"], (
                f"FAIL [{noun}]: hint must list the noun's verbs"
            )

    def test_noun_with_unrecognized_verb(self, run_cli, envelope_tools):
        code, out, _err = run_cli(["feature", "bogus", "--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"
        assert "start" in envelope["error"]["hint"]
        assert "switch" in envelope["error"]["hint"]

    def test_json_flag_position_independence(self, run_cli, envelope_tools):
        _code, out_after, _err = run_cli(["help", "--json"])
        _code, out_before, _err = run_cli(["--json", "help"])
        assert envelope_tools.parse(out_before) == envelope_tools.parse(out_after)

    def test_json_flag_leading_position_on_live_init(self, run_cli, envelope_tools):
        # Boundary conditions: --json is recognized in any argv position,
        # exercised beyond `help` on the live write-free init preview.
        code, out, _err = run_cli(["--json", "init", "--dry-run"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        assert envelope["ok"] is True
        assert envelope["data"]["dry_run"] is True

    def test_injected_internal_error(self, run_cli, envelope_tools, monkeypatch):
        # AC-4: a forced handler exception → `internal`, exit 1, stdout
        # envelope-clean. The dispatcher's import style for build_manifest is
        # its own choice (spec: internals unpinned) — patch both seams.
        def boom(*_args, **_kwargs):
            raise RuntimeError("injected handler exception")

        monkeypatch.setattr(contracts_module, "build_manifest", boom)
        if hasattr(dispatch_module, "build_manifest"):
            monkeypatch.setattr(dispatch_module, "build_manifest", boom)
        code, out, err = run_cli(["help", "--json"])
        assert code == 1
        envelope = envelope_tools.parse(out)  # envelope-clean stdout
        envelope_tools.assert_shape(envelope)
        assert envelope["error"]["code"] == "internal"
        assert "injected handler exception" in err, (
            "FAIL: REQ-9 routes the diagnostic detail to stderr (peer review SY-M3)"
        )

    def test_error_codes_drawn_from_req7_vocabulary(
        self, run_cli, envelope_tools, monkeypatch, tmp_path
    ):
        # commands B0 micro-edit: anchored outside any project (no .heddle.yaml) so
        # `status` fails deterministically — at the repo root it would resolve
        # the host's own native workspace and succeed with no error envelope.
        monkeypatch.chdir(tmp_path)
        for argv in (["status", "--json"], ["frobnicate", "--json"], ["--json"]):
            _code, out, _err = run_cli(argv)
            envelope = envelope_tools.parse(out)
            assert envelope["error"]["code"] in ERROR_CODES


class TestAC6RunGateRuntimeEnvelope:
    """The dispatcher routes run-gate through the shared runtime envelope."""

    def test_run_gate_json_emits_an_envelope(self, run_cli, monkeypatch, tmp_path):
        # Drive a clean workspace-resolution failure so no gate runner is
        # needed; the envelope path is the behavior under test.
        # no gate runner is needed — the envelope path is what is under test.
        import json as _json

        monkeypatch.chdir(tmp_path)  # no .heddle.yaml → workspace-invalid
        code, out, _err = run_cli(["run-gate", "--json", "spec-review"])
        envelope = _json.loads(out)
        assert envelope["ok"] is False and "error" in envelope, (
            "FAIL: run-gate --json must emit a well-formed failure envelope "
            "on stdout (gate envelope/M3) — the M1 no-envelope behavior is retired"
        )
        assert code == 3, "FAIL: outside a Heddle project is workspace-invalid/3"


class TestBrokenPipePolicy:
    """
    Checkpoint 2 lifecycle boundary (review): the CLI entry boundary
        suppresses BrokenPipeError — a consumer closing stdout early gets a
        quiet exit 1, never a traceback (covers the recovery path's re-write
        to the broken stream).
    """

    def test_broken_pipe_exits_quietly(self, monkeypatch, capsys):
        from heddle.cli import main

        def broken(_result):
            raise BrokenPipeError

        # Every JSON write raises — the REQ-9 wrapper's recovery re-write
        # raises a second time, which must end at cli.main, not escape.
        monkeypatch.setattr(dispatch_module, "_print_envelope", broken)
        code = main(["help", "--json"])
        assert code == 1
        assert "internal error" in capsys.readouterr().err


class TestInstallHealthHint:
    """
    Checkpoint 2 distribution errors (review): a missing heddle distribution
        surfaces an actionable install hint, not the generic internal advice
        (Assumption A5's violation path).
    """

    def test_missing_distribution_gets_actionable_hint(
        self, run_cli, envelope_tools, monkeypatch
    ):
        def missing(name):
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(importlib.metadata, "version", missing)
        code, out, err = run_cli(["help", "--json"])
        assert code == 1
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        assert envelope["error"]["code"] == "internal"
        assert "pip install" in envelope["error"]["hint"], (
            "FAIL: the hint must name the install command (Checkpoint 2 distribution "
            "errors)"
        )
        assert "missing distribution metadata" in err


class TestAC12HumanOutput:
    """AC-12 (SHOULD): human output paths are JSON-free and informative."""

    def test_help_lists_every_command_with_summary(self, run_cli):
        code, out, _err = run_cli(["help"])
        assert code == 0
        assert_not_json(out)
        # Whitespace-normalized containment: the renderer may wrap lines.
        normalized = " ".join(out.split())
        for contract in COMMAND_SURFACE:
            assert contract.name in out, (
                f"FAIL: `heddle help` must list every leaf command "
                f"({contract.name} missing)"
            )
            assert " ".join(contract.summary.split()) in normalized, (
                f"FAIL: `heddle help` must show {contract.name}'s summary "
                "(AC-12: every leaf command with its summary)"
            )

    def test_init_refusal_text_carries_hint_and_next_actions(
        self, run_cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # A synthetic occupied host: the checkout's own adoption state is
        # local and must not decide whether init refuses.
        host = tmp_path / "host"
        shutil.copytree(TINY, host)
        (host / ".git").mkdir()
        monkeypatch.chdir(host)
        code, out, _err = run_cli(["init"])
        assert code == 3
        assert_not_json(out)
        assert "workspace-invalid" in out, (
            "FAIL: human failure text names the error code (AC-12)"
        )
        assert "review every refused row" in out, (
            "FAIL: human failure text renders the remediation hint"
        )
        assert "heddle init --dry-run" in out, (
            "FAIL: human failure text renders the safe preview next action"
        )


class TestPerCommandHelp:
    """Per-command `--help`/`-h` (post-completion friction retrospective: usage was
    discoverable only through the error path). Served from manifest contract
    metadata by a single dispatch intercept — never by command execution —
    so help needs no workspace and can never mutate one."""

    def test_phase_exit_help_human(self, run_cli):
        code, out, _err = run_cli(["phase-exit", "--help"])
        assert code == 0
        assert_not_json(out)
        assert "usage: heddle phase-exit" in out
        assert "advance one legal stage and report the resulting guidance" in out
        assert "--through" in out and "stage travel remains one legal step" in out

    def test_phase_exit_help_json_is_manifest_entry(self, run_cli, envelope_tools):
        code, out, _err = run_cli(["phase-exit", "--help", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        assert envelope["ok"] is True
        expected = next(
            entry
            for entry in contracts_module.build_manifest()["commands"]
            if entry["name"] == "phase-exit"
        )
        assert envelope["data"] == expected, (
            "FAIL: per-command help --json must be exactly one "
            "heddle.manifest/v0 commands[] entry — no new schema is minted"
        )

    def test_decisions_add_help_json_is_manifest_entry_with_input_schema(
        self, run_cli, envelope_tools
    ):
        # input schema: the body flows through serialize_command on BOTH surfaces —
        # per-command help stays byte-identical to the manifest entry.
        code, out, _err = run_cli(["decisions", "add", "--help", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        assert envelope["ok"] is True
        expected = next(
            entry
            for entry in contracts_module.build_manifest()["commands"]
            if entry["name"] == "decisions add"
        )
        assert envelope["data"] == expected
        assert envelope["data"]["input_schema"]["id"] == "heddle.decision-batch/v1"

    def test_decisions_add_help_human_renders_payload_contract(self, run_cli):
        # input schema: the payload section carries the fields and the class
        # taxonomy one-liners — the discoverability surface itself.
        code, out, _err = run_cli(["decisions", "add", "--help"])
        assert code == 0
        assert "input payload (heddle.decision-batch/v1" in out
        assert "routes_to" in out
        assert "ratified-contract conflict" in out
        assert "class 4 is recorded by run-gate" in out

    def test_help_without_input_schema_omits_payload_section(self, run_cli):
        code, out, _err = run_cli(["status", "--help"])
        assert code == 0
        assert "input payload" not in out

    def test_short_alias_dash_h(self, run_cli):
        code, out, _err = run_cli(["status", "-h"])
        assert code == 0
        assert "--all" in out

    def test_help_flag_position_independent(self, run_cli):
        # Mirrors the pinned --json position-independence convention.
        code, out, _err = run_cli(["phase-exit", "--dry-run", "--help"])
        assert code == 0
        assert "usage: heddle phase-exit" in out

    def test_run_gate_help_bypasses_handler(self, run_cli, tmp_path, monkeypatch):
        # From a non-workspace cwd: help must not resolve a feature or touch
        # the gate seam — a false stub-fallthrough would exit 3 instead.
        monkeypatch.chdir(tmp_path)
        code, out, _err = run_cli(["run-gate", "--help"])
        assert code == 0
        assert "<gate>" in out

    def test_run_gate_help_json(self, run_cli, envelope_tools):
        code, out, _err = run_cli(["run-gate", "--help", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        assert envelope["data"]["name"] == "run-gate"

    def test_drive_help(self, run_cli):
        code, out, _err = run_cli(["drive", "--help"])
        assert code == 0
        assert "usage: heddle drive" in out

    def test_multiword_write_command_help(self, run_cli):
        code, out, _err = run_cli(["milestone", "add", "--help"])
        assert code == 0
        assert "usage: heddle milestone add" in out

    def test_stub_command_help_precedes_stub(self, run_cli):
        # Contract metadata is advertised even when the adapter is absent —
        # the same policy the manifest already follows.
        code, out, _err = run_cli(["init", "--help"])
        assert code == 0
        assert out.startswith("heddle init — scaffold or upgrade host integration\n")
        assert "roadmap milestone" not in out

    def test_bare_help_flag_lists_surface(self, run_cli):
        for flag in ("-h", "--help"):
            code, out, _err = run_cli([flag])
            assert code == 0
            assert "heddle — agent-workflow protocol runtime" in out

    def test_unknown_flag_still_usage_exit_2(self, run_cli, envelope_tools):
        code, out, _err = run_cli(["phase-exit", "--nope", "--json"])
        assert code == 2
        assert envelope_tools.parse(out)["error"]["code"] == "usage"

    def test_unknown_command_with_help_still_usage_exit_2(
        self, run_cli, envelope_tools
    ):
        # Interception requires a resolved contract.
        code, out, _err = run_cli(["bogus", "--help", "--json"])
        assert code == 2
        assert envelope_tools.parse(out)["error"]["code"] == "usage"

    def test_noun_without_verb_with_help_still_usage_exit_2(
        self, run_cli, envelope_tools
    ):
        # The command alias bare-noun alias requires an exact ["<noun>"] match, so a
        # noun + --help stays the verbs-hint usage error — deliberate pin.
        code, out, _err = run_cli(["milestone", "--help", "--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"
        assert "advance" in envelope["error"]["hint"]

    def test_decisions_noun_with_help_still_usage_exit_2(self, run_cli, envelope_tools):
        # Sharpest edge of the pin above: bare `decisions` ALIASES to
        # `decisions list` (command alias) yet `decisions --help` is still the
        # verbs-hint usage error — help tokens never join name resolution.
        code, out, _err = run_cli(["decisions", "--help", "--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"
        assert "resolve" in envelope["error"]["hint"]
