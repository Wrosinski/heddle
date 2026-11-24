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
import os
import shutil
import subprocess
import sys
import textwrap
from importlib import import_module
from pathlib import Path

import pytest

import heddle.runtime.contracts as contracts_module
import heddle.runtime.dispatch as dispatch_module
from heddle.contracts.result import ERROR_CODES
from heddle.contracts.review_assignments import EVIDENCE_KINDS
from heddle.runtime.contracts import COMMAND_SURFACE

REPO_ROOT = Path(__file__).resolve().parents[2]
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

    def test_review_round_open_help_renders_payload_contract(self, run_cli):
        code, out, _err = run_cli(["review", "round-open", "--help"])
        assert code == 0
        assert "input payload (heddle.review-round-input/v1" in out
        for purpose in ("discovery", "verification", "independent-pass"):
            assert f"\n      {purpose}  " in out
        assert "scope_change  object or null, optional" in out
        assert "sealed acceptance" in out and "stop decision" in out
        assert '"purpose": "verification"' in out

    def test_review_disposition_help_explains_evidence_kinds(self, run_cli):
        code, out, _err = run_cli(["review", "disposition", "--help"])
        assert code == 0
        lines = [line.strip() for line in out.splitlines()]
        for kind in EVIDENCE_KINDS:
            option = next(line for line in lines if line.startswith(f"{kind}  "))
            assert option != f"{kind}  {kind}", option
        assert "heddle verify" in out and "self-mutating-reference" in out
        assert "decision-routed" in out

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
            assert "heddle — LLM coding workflow harness" in out

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


# antigravity-reviewer-lane-v1, milestone m1: the gate-session guard.
#
# Gate reviewer children carry HEDDLE_AGENT_SESSION=gate
# (heddle/gate/runners/environment.py). A marked invocation of a command whose
# contract is mutating exits 3 with `gate-session-refused` before the command
# parses its own flags; help, unknown commands, read-only contracts and
# unmarked or in-process paths keep their outcomes.
GATE_SESSION_ENV = "HEDDLE_AGENT_SESSION"
GATE_SESSION_REFUSED = "gate-session-refused"
MUTATING = tuple(contract for contract in COMMAND_SURFACE if contract.mutating)
READ_ONLY = tuple(contract for contract in COMMAND_SURFACE if not contract.mutating)
# Minimal valid arguments for read-only contracts on the operational host;
# every other read-only contract runs with none.
READ_ONLY_ARGUMENTS = {
    "search": ["value"],
    "show-prompt": ["spec-review", "--feature", "operational-example"],
}
COMMANDS_SET = ["commands", "set", "smoke_test", "--command", "true"]


def _mark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GATE_SESSION_ENV, "gate")


def _operational_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A disposable adopted git host with an active feature, as cwd."""
    from tests.operational_model_helpers import fresh_host

    host, _state = fresh_host(tmp_path)
    monkeypatch.chdir(host)
    return host


def _host_bytes(host: Path) -> dict[str, object]:
    """Host files plus Heddle's local records under `.git/heddle`.

    The shared snapshot excludes `.git`; git's own index is a stat cache that
    read commands such as `git status` refresh, so it is not host state.
    """
    from tests.tiering_helpers import snapshot

    local = host / ".git" / "heddle"
    return {
        **snapshot(host),
        **{
            path.relative_to(host).as_posix(): path.read_bytes()
            for path in sorted(local.rglob("*"))
            if path.is_file()
        },
    }


def _forbid_handlers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every mutating command's handler with a recorder.

    Keeps the run-gate and drive diversions from reaching a provider while the
    guard is absent, and shows whether a refused command reached its handler.
    """
    reached: list[str] = []

    def recorder(name: str):
        def handler(*_args: object, **_kwargs: object) -> int:
            reached.append(name)
            return 0

        return handler

    for contract in MUTATING:
        if contract.cli_binding is not None:
            module, function = contract.cli_binding
            monkeypatch.setattr(
                import_module(f"heddle.runtime.{module}"),
                function,
                recorder(f"{module}.{function}"),
            )
    monkeypatch.setattr(
        import_module("heddle.runtime.gate_run"), "run_gate", recorder("run_gate")
    )
    monkeypatch.setattr(
        import_module("heddle.driver.cli"), "run_drive", recorder("run_drive")
    )
    return reached


def _minimal_argv(contract) -> list[str]:
    return [*contract.name.split(), *("x" for arg in contract.args if arg.required)]


def _assert_refused(envelope_tools, code: int, out: str, argv: list[str]) -> None:
    assert code == 3, (
        f"FAIL AC-1: marked `heddle {' '.join(argv)}` must exit 3 "
        f"({GATE_SESSION_REFUSED}), got {code}: {out.strip()[:300]}"
    )
    envelope = envelope_tools.parse(out)
    envelope_tools.assert_shape(envelope)
    error = envelope["error"]
    assert error["code"] == GATE_SESSION_REFUSED, (
        f"FAIL AC-1: refusal code must be {GATE_SESSION_REFUSED}, got {error!r}"
    )
    assert GATE_SESSION_ENV in f"{error['message']} {error['hint']}", (
        f"FAIL AC-1: the refusal must name the {GATE_SESSION_ENV} marker: {error!r}"
    )


class TestAC1GateSessionGuard:
    """AC-1 (m1, red): a marked invocation of a mutating contract is refused
    with exit 3 and `gate-session-refused`, names the marker, and leaves the
    host's feature state and files byte-identical."""

    def test_refusal_code_joins_the_error_vocabulary(self):
        assert GATE_SESSION_REFUSED in ERROR_CODES, (
            f"FAIL AC-1: {GATE_SESSION_REFUSED} is not in the pinned error-code "
            "vocabulary (heddle/contracts/result.py ERROR_CODES)"
        )

    @pytest.mark.parametrize("json_position", ["leading", "trailing"])
    @pytest.mark.parametrize("contract", MUTATING, ids=lambda c: c.name)
    def test_every_mutating_contract_is_refused_before_its_handler(
        self, run_cli, envelope_tools, monkeypatch, tmp_path, contract, json_position
    ):
        """Registry-wide: no mutating contract reaches its handler when marked,
        with `--json` before or after the command."""
        monkeypatch.chdir(tmp_path)
        reached = _forbid_handlers(monkeypatch)
        _mark(monkeypatch)
        argv = _minimal_argv(contract)
        argv = ["--json", *argv] if json_position == "leading" else [*argv, "--json"]
        code, out, _err = run_cli(argv)
        assert reached == [], (
            f"FAIL AC-1: marked `heddle {' '.join(argv)}` reached its handler "
            f"{reached}; the dispatcher guard must refuse first"
        )
        _assert_refused(envelope_tools, code, out, argv)

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param([*COMMANDS_SET, "--json"], id="write-trailing-json"),
            pytest.param(["--json", *COMMANDS_SET], id="write-leading-json"),
            pytest.param([*COMMANDS_SET, "--dry-run", "--json"], id="dry-run"),
            pytest.param(["--json", "phase-exit", "--dry-run"], id="phase-exit-dry"),
            pytest.param(["phase-exit", "--nope", "--json"], id="malformed-flag"),
            pytest.param(["run-gate", "spec-review", "--json"], id="run-gate"),
            pytest.param(["--json", "run-gate", "spec-review"], id="run-gate-lead"),
            pytest.param(["drive", "--json"], id="drive"),
            pytest.param(["--json", "drive"], id="drive-leading-json"),
        ],
    )
    def test_marked_mutation_on_an_active_feature_changes_nothing(
        self, run_cli, envelope_tools, monkeypatch, tmp_path, argv
    ):
        """Real handlers stay live except the provider-launching diversions
        (run-gate, run-gates, drive), which a recorder replaces. A malformed
        flag is refused, not reported as a usage error."""
        host = _operational_host(tmp_path, monkeypatch)
        reached: list[str] = []
        for module, function in (
            ("heddle.runtime.gate_run", "run_gate"),
            ("heddle.runtime.gate_run", "run_gates"),
            ("heddle.driver.cli", "run_drive"),
        ):
            monkeypatch.setattr(
                import_module(module),
                function,
                lambda *_a, _name=function, **_k: reached.append(_name) or 0,
            )
        before = _host_bytes(host)
        _mark(monkeypatch)
        code, out, _err = run_cli(argv)
        assert reached == [], (
            f"FAIL AC-1: marked `heddle {' '.join(argv)}` reached {reached}"
        )
        _assert_refused(envelope_tools, code, out, argv)
        assert _host_bytes(host) == before, (
            f"FAIL AC-1: marked `heddle {' '.join(argv)}` changed host bytes"
        )

    def test_human_mode_refusal_names_the_code(self, run_cli, monkeypatch, tmp_path):
        host = _operational_host(tmp_path, monkeypatch)
        before = _host_bytes(host)
        _mark(monkeypatch)
        code, out, err = run_cli(COMMANDS_SET)
        assert code == 3, (
            f"FAIL AC-1: marked human-mode `commands set` must exit 3, got {code}"
        )
        assert GATE_SESSION_REFUSED in out + err
        assert_not_json(out)
        assert _host_bytes(host) == before

    def test_real_entry_point_refuses_a_marked_mutation(self, tmp_path):
        """One subprocess through `python -m heddle` (heddle.cli:main, the
        console script's target), with the marker in the process environment."""
        from tests.operational_model_helpers import fresh_host

        host, _state = fresh_host(tmp_path)
        before = _host_bytes(host)
        environment = {
            **os.environ,
            GATE_SESSION_ENV: "gate",
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(
            [sys.executable, "-m", "heddle", *COMMANDS_SET, "--json"],
            cwd=host,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 3, (
            "FAIL AC-1: the marked entry point must exit 3, got "
            f"{completed.returncode}: {completed.stdout[:300]}{completed.stderr[:300]}"
        )
        envelope = json.loads(completed.stdout)
        assert envelope["error"]["code"] == GATE_SESSION_REFUSED
        assert "Traceback" not in completed.stderr
        assert _host_bytes(host) == before, (
            "FAIL AC-1: the refused subprocess changed host bytes"
        )


class TestAC2GuardLeavesOtherPathsUnchanged:
    """AC-2 (m1, survivors, dispatch side): help, unknown commands, every
    read-only contract, unmarked commands and in-process services keep their
    current outcomes. Each passes before the guard exists and pins what the
    guard must not change; the Claude and Codex runner cases live in their
    runner tests."""

    @pytest.mark.parametrize("contract", READ_ONLY, ids=lambda c: c.name)
    def test_every_read_only_contract_runs_marked_and_changes_nothing(
        self, run_cli, envelope_tools, monkeypatch, tmp_path, contract
    ):
        """Survivor, registry-wide: each read-only contract, marked, with
        minimal valid arguments, succeeds and leaves host bytes identical, so
        every command that changes state declares mutating=True."""
        host = _operational_host(tmp_path, monkeypatch)
        before = _host_bytes(host)
        _mark(monkeypatch)
        argv = [
            *contract.name.split(),
            *READ_ONLY_ARGUMENTS.get(contract.name, []),
            "--json",
        ]
        code, out, err = run_cli(argv)
        envelope = envelope_tools.parse(out)
        assert envelope.get("error", {}).get("code") != GATE_SESSION_REFUSED, (
            f"FAIL AC-2: read-only `heddle {contract.name}` was refused when marked"
        )
        assert code == 0 and envelope["ok"] is True, (
            f"FAIL AC-2: marked `heddle {' '.join(argv)}` exited {code}; give "
            f"new read-only contracts minimal valid arguments in "
            f"READ_ONLY_ARGUMENTS: {out[:300]}{err[:300]}"
        )
        assert _host_bytes(host) == before, (
            f"FAIL AC-2: read-only `heddle {contract.name}` changed host bytes; "
            "its contract must declare mutating=True"
        )

    def test_bare_decisions_alias_runs_marked(
        self, run_cli, envelope_tools, monkeypatch, tmp_path
    ):
        """Survivor: the bare-noun alias resolves to read-only `decisions list`."""
        host = _operational_host(tmp_path, monkeypatch)
        before = _host_bytes(host)
        _mark(monkeypatch)
        code, out, _err = run_cli(["decisions", "--json"])
        assert code == 0 and envelope_tools.parse(out)["ok"] is True
        assert _host_bytes(host) == before

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["run-gate", "--help"], "usage: heddle run-gate"),
            (["decisions", "add", "--help"], "usage: heddle decisions add"),
            (["drive", "--help"], "usage: heddle drive"),
            (["phase-exit", "--dry-run", "--help"], "usage: heddle phase-exit"),
            (["--help"], "heddle — LLM coding workflow harness"),
        ],
        ids=["run-gate", "decisions-add", "drive", "phase-exit-dry-run", "bare"],
    )
    def test_marked_help_is_answered_before_the_guard(
        self, run_cli, monkeypatch, tmp_path, argv, expected
    ):
        """Survivor: per-command help is read-only and exits 0 when marked."""
        monkeypatch.chdir(tmp_path)
        _mark(monkeypatch)
        code, out, _err = run_cli(argv)
        assert code == 0, f"FAIL AC-2: marked `heddle {' '.join(argv)}` exited {code}"
        assert expected in out

    def test_marked_help_json_is_the_manifest_entry(
        self, run_cli, envelope_tools, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        _mark(monkeypatch)
        code, out, _err = run_cli(["run-gate", "--help", "--json"])
        assert code == 0
        assert envelope_tools.parse(out)["data"]["name"] == "run-gate"

    @pytest.mark.parametrize(
        ("argv", "hint"),
        [
            (["frobnicate", "--json"], "heddle help"),
            (["milestone", "--json"], "advance"),
            (["bogus", "--help", "--json"], "heddle help"),
        ],
        ids=["unknown-command", "bare-noun", "unknown-with-help"],
    )
    def test_marked_unknown_commands_keep_their_usage_outcome(
        self, run_cli, envelope_tools, monkeypatch, tmp_path, argv, hint
    ):
        """Survivor: no contract resolves, so the usage error (exit 2) stands."""
        monkeypatch.chdir(tmp_path)
        _mark(monkeypatch)
        code, out, _err = run_cli(argv)
        envelope = envelope_tools.parse(out)
        assert code == 2 and envelope["error"]["code"] == "usage", envelope
        assert hint in envelope["error"]["hint"]

    def test_unmarked_mutation_writes_as_today(
        self, run_cli, envelope_tools, monkeypatch, tmp_path
    ):
        """Survivor: without the marker a mutating command writes."""
        host = _operational_host(tmp_path, monkeypatch)
        monkeypatch.delenv(GATE_SESSION_ENV, raising=False)
        state = host / "plans" / "operational-example" / "state.yaml"
        before = state.read_bytes()
        code, out, _err = run_cli([*COMMANDS_SET, "--json"])
        envelope = envelope_tools.parse(out)
        assert code == 0 and envelope["data"]["wrote"] is True, envelope
        assert state.read_bytes() != before

    def test_in_process_service_writes_while_marked(self, monkeypatch, tmp_path):
        """Survivor: the guard sits at the CLI dispatcher. Heddle's in-process
        application services, which the gate runtime records through, still
        write when the process environment carries the marker."""
        from tests.tiering_helpers import invoke

        host = _operational_host(tmp_path, monkeypatch)
        state = host / "plans" / "operational-example" / "state.yaml"
        before = state.read_bytes()
        _mark(monkeypatch)
        result = invoke("CommandsSet", key="smoke_test", command="true")
        assert result.ok, result.to_envelope()
        assert state.read_bytes() != before


# The synthetic first module of the nested session. It records the marker at
# import, before any test runs, then spawns a mutating command against a
# disposable copy of the tiny host with the session's environment.
_SYNTHETIC_FIRST_MODULE = textwrap.dedent(
    '''
    """Synthetic first module: records the gate-session marker at import."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    Path(@RECORD@).write_text(
        json.dumps({"marker": os.environ.get("HEDDLE_AGENT_SESSION")}),
        encoding="utf-8",
    )


    def test_mutating_command_succeeds_against_a_disposable_host(tmp_path):
        from tests.runtime.write_path_helpers import copy_host

        host = copy_host(tmp_path, Path(@TINY@))
        state = host / "plans" / "sample-feature" / "state.yaml"
        before = state.read_bytes()
        completed = subprocess.run(
            [
                sys.executable, "-m", "heddle", "commands", "set", "smoke_test",
                "--command", "true", "--feature", "sample-feature", "--json",
            ],
            cwd=host,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert state.read_bytes() != before
    '''
)


class TestAC3BootstrapClearsTheMarker:
    """AC-3 (m1, red): the repository bootstrap (tests/conftest.py, loaded
    exactly as the suite loads its plugins before collection) removes the
    marker before the first module is imported."""

    def test_nested_marked_session_records_no_marker_and_mutates(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        record = tmp_path / "import-record.json"
        (session / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (session / "test_first_module.py").write_text(
            _SYNTHETIC_FIRST_MODULE.replace("@RECORD@", repr(str(record))).replace(
                "@TINY@", repr(str(TINY))
            ),
            encoding="utf-8",
        )
        environment = {
            **os.environ,
            GATE_SESSION_ENV: "gate",
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        # Inherited options could deselect the one synthetic test.
        environment.pop("PYTEST_ADDOPTS", None)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-p",
                "tests.conftest",
                "test_first_module.py",
            ],
            cwd=session,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        output = result.stdout[-2000:] + result.stderr[-2000:]
        assert record.is_file(), (
            f"FAIL AC-3: the nested session never imported its first module\n{output}"
        )
        recorded = json.loads(record.read_text(encoding="utf-8"))
        assert recorded == {"marker": None}, (
            f"FAIL AC-3: the first module saw {GATE_SESSION_ENV}="
            f"{recorded['marker']!r} at import; tests/conftest.py pytest_configure "
            "must remove the marker before collection"
        )
        assert result.returncode == 0 and "1 passed" in result.stdout, (
            "FAIL AC-3: the nested fixture's mutating command must succeed against "
            f"its disposable host\n{output}"
        )
