"""Milestone add and edit command behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.runtime.authoring_helpers import MILESTONE_PAYLOAD_KEYS
from tests.runtime.write_path_helpers import (
    copy_host as _copy_host,
)
from tests.runtime.write_path_helpers import (
    read_yaml as _read_yaml,
)
from tests.runtime.write_path_helpers import (
    write_yaml as _write_yaml,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"

ADD_PAYLOAD = """\
title: Wire the portfolio scan
complexity: medium
estimated_hours: [2, 4]
verification:
  command: "pytest tests/runtime/test_portfolio_status.py -q"
  expected: "all tests pass"
"""


def _tiny_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "host"
) -> Path:
    host = _copy_host(tmp_path, TINY, name=name)
    monkeypatch.chdir(host)
    return host


def _state_path(host: Path) -> Path:
    return host / "plans" / "sample-feature" / "state.yaml"


def _payload_file(tmp_path: Path, text: str, name: str = "payload.yaml") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _edit_ready_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "host"
) -> Path:
    """
    AC-12 precondition: core done (tasks closed), reader current.
    """
    host = _tiny_host(tmp_path, monkeypatch, name=name)
    state = _read_yaml(_state_path(host))
    for milestone in state["milestones"]:
        if milestone["id"] == "m1":
            milestone["status"] = "done"
            for task in milestone.get("tasks", []):
                task["status"] = "done"
        if milestone["id"] == "m2":
            milestone["status"] = "current"
    _write_yaml(_state_path(host), state)
    return host


def _run(run_cli, envelope_tools, argv: list[str]) -> tuple[int, dict]:
    code, out, _err = run_cli(argv)
    envelope = envelope_tools.parse(out)
    envelope_tools.assert_shape(envelope)
    return code, envelope


class TestAC11MilestoneAdd:
    def test_add_with_absent_depends_on_is_out_of_sequence(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        host = _tiny_host(tmp_path, monkeypatch)
        payload = _payload_file(tmp_path, ADD_PAYLOAD + "depends_on: [m9]\n")
        before = _state_path(host).read_bytes()

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "add", "--from-file", payload, "--json"],
        )
        assert code == 3 and envelope["error"]["code"] == "milestone-out-of-sequence", (
            f"FAIL AC-11: a depends_on reference to an absent milestone is a "
            f"§4.2 violation (exit 3), got {code} {envelope.get('error')!r}"
        )
        assert _state_path(host).read_bytes() == before, (
            "FAIL AC-11: nothing may be appended on a refusal"
        )

    @pytest.mark.parametrize(
        ("label", "extra"),
        [
            ("explicit-id", "id: m9\n"),
            ("explicit-status", "status: current\n"),
            ("explicit-tasks", "tasks: []\n"),
            ("unknown-field", "sprint: 3\n"),
            # review: YAML admits non-string mapping keys — the gauntlet
            # must reject them as usage, never crash formatting them.
            ("non-string-key", "1: numeric-key\n"),
        ],
    )
    def test_add_rejects_forbidden_and_unknown_fields_as_usage(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch, label, extra
    ) -> None:
        host = _tiny_host(tmp_path, monkeypatch, name=f"host-{label}")
        payload = _payload_file(tmp_path, ADD_PAYLOAD + extra, f"{label}.yaml")
        before = _state_path(host).read_bytes()

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "add", "--from-file", payload, "--json"],
        )
        assert code == 2, (
            f"FAIL AC-11 [{label}]: payload contract violations are "
            f"usage exit 2, got {code} {envelope.get('error')!r}"
        )
        assert _state_path(host).read_bytes() == before

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("wrong-type", ADD_PAYLOAD.replace("[2, 4]", '"soon"')),
            (
                "missing-required",
                "complexity: medium\nestimated_hours: [2, 4]\n"
                "verification: {command: x, expected: y}\n",
            ),
            # review: the state reader enforces exactly {command, expected};
            # an extra key admitted here would corrupt the workspace on
            # the next read.
            ("verification-extra-key", ADD_PAYLOAD + '  note: "extra"\n'),
            # review: malformed-but-typed values — the highest-probability
            # real-world payload defects beyond the AC's literal cases.
            ("wrong-arity", ADD_PAYLOAD.replace("[2, 4]", "[1, 2, 3]")),
            ("non-int-hours", ADD_PAYLOAD.replace("[2, 4]", '["a", "b"]')),
            ("bad-complexity", ADD_PAYLOAD.replace("medium", "extreme")),
            (
                "verification-missing-command",
                "title: t\ncomplexity: medium\nestimated_hours: [2, 4]\n"
                "verification: {expected: y}\n",
            ),
        ],
    )
    def test_add_rejects_type_and_required_field_faults_as_usage(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch, label, text
    ) -> None:
        host = _tiny_host(tmp_path, monkeypatch, name=f"host-{label}")
        payload = _payload_file(tmp_path, text, f"{label}.yaml")
        before = _state_path(host).read_bytes()

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "add", "--from-file", payload, "--json"],
        )
        assert code == 2, (
            f"FAIL AC-11 [{label}]: exit 2 expected, got {code} "
            f"{envelope.get('error')!r}"
        )
        assert _state_path(host).read_bytes() == before

    @pytest.mark.parametrize(
        ("label", "argv_payload"),
        [
            ("nonexistent-path", "ghost-payload.yaml"),
            ("malformed-yaml", None),  # written below
            ("non-mapping", None),
        ],
    )
    def test_add_reader_faults_are_usage_with_state_unchanged(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch, label, argv_payload
    ) -> None:
        host = _tiny_host(tmp_path, monkeypatch, name=f"host-{label}")
        if label == "malformed-yaml":
            argv_payload = _payload_file(tmp_path, "title: [unclosed\n", "bad.yaml")
        elif label == "non-mapping":
            argv_payload = _payload_file(tmp_path, "- just\n- a list\n", "list.yaml")
        before = _state_path(host).read_bytes()

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "add", "--from-file", argv_payload, "--json"],
        )
        assert code == 2, (
            f"FAIL AC-11/SP-I5 [{label}]: reader faults (unreadable file / "
            f"bad YAML / non-mapping) are usage exit 2, got {code} "
            f"{envelope.get('error')!r}"
        )
        assert _state_path(host).read_bytes() == before


class TestAC12MilestoneEdit:
    EDIT_PAYLOAD = """\
title: Wire and harden the CLI
estimated_hours: [3, 5]
depends_on: [m1]
"""

    def test_a_named_fields_replace_wholly_others_untouched(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        host = _edit_ready_host(tmp_path, monkeypatch)
        before_state = _read_yaml(_state_path(host))
        m2_before = next(m for m in before_state["milestones"] if m["id"] == "m2")
        payload = _payload_file(tmp_path, self.EDIT_PAYLOAD)

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m2", "--from-file", payload, "--json"],
        )
        assert code == 0, (
            f"FAIL AC-12(a): milestone edit must exit 0, got {code} "
            f"{envelope.get('error')!r}"
        )
        data = envelope["data"]
        assert set(data.keys()) == MILESTONE_PAYLOAD_KEYS, (
            f"FAIL AC-12/R-P3-2: data carries exactly "
            f"{sorted(MILESTONE_PAYLOAD_KEYS)}, got {sorted(data.keys())}"
        )
        milestone = data["milestone"]
        assert milestone["title"] == "Wire and harden the CLI"
        assert milestone["estimated_hours"] == [3, 5]
        assert milestone["depends_on"] == ["m1"], (
            "FAIL AC-12(a)/array replacement: a named array replaces as a whole array"
        )
        assert milestone["satisfies"] == m2_before["satisfies"], (
            "FAIL AC-12(a): unlisted fields are untouched (shallow merge)"
        )
        assert milestone["owns"] == m2_before["owns"]
        # review (peer review): every unlisted field, not just the arrays.
        assert milestone["complexity"] == m2_before["complexity"]
        assert milestone["verification"] == m2_before["verification"]
        assert milestone["status"] == "current", (
            "FAIL AC-12: edit never touches status (INV-4)"
        )
        assert data["revision"] == before_state["revision"] + 1

        state = _read_yaml(_state_path(host))
        m2_after = next(m for m in state["milestones"] if m["id"] == "m2")
        assert m2_after["title"] == "Wire and harden the CLI"

    @pytest.mark.parametrize(
        ("label", "payload_text", "owner_hint"),
        [
            ("status", "status: done\n", "milestone advance"),
            ("id", "id: m7\n", None),
            ("tasks", "tasks: []\n", None),
            # review: non-string YAML keys are usage, never a crash.
            ("non-string-key", "1: numeric-key\n", None),
        ],
    )
    def test_b_forbidden_payload_fields_are_usage(
        self,
        run_cli,
        envelope_tools,
        tmp_path: Path,
        monkeypatch,
        label,
        payload_text,
        owner_hint,
    ) -> None:
        host = _edit_ready_host(tmp_path, monkeypatch, name=f"host-{label}")
        payload = _payload_file(tmp_path, payload_text, f"{label}.yaml")
        before = _state_path(host).read_bytes()

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m2", "--from-file", payload, "--json"],
        )
        # status: milestone advance owns it; id: immutable (document authority); tasks:
        # not
        # a skeleton field — all usage-class refusals (CP1 document authority/dependency
        # references).
        assert code == 2, (
            f"FAIL AC-12(b/b′) [{label}]: {label} in an edit payload is usage "
            f"exit 2, got {code} {envelope.get('error')!r}"
        )
        if owner_hint is not None:
            blob = (
                envelope["error"]["message"] + " " + envelope["error"]["hint"]
            ).lower()
            assert owner_hint in blob, (
                f"FAIL AC-12(b): the refusal must name `{owner_hint}` as the owner"
            )
        assert _state_path(host).read_bytes() == before

    def test_b2_verification_extra_key_is_usage(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        # review (peer review): the reader's exact-key contract holds on
        # the edit surface too — extra verification keys are usage, never
        # a written-then-unreadable state.yaml.
        host = _edit_ready_host(tmp_path, monkeypatch)
        before = _state_path(host).read_bytes()
        payload = _payload_file(
            tmp_path,
            "verification: {command: x, expected: y, note: extra}\n",
        )

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m2", "--from-file", payload, "--json"],
        )
        assert code == 2, (
            f"FAIL XS-C1: extra verification keys are usage exit 2, "
            f"got {code} {envelope.get('error')!r}"
        )
        assert _state_path(host).read_bytes() == before, (
            "FAIL XS-C1: a rejected payload never touches state.yaml"
        )

    def test_c_editing_a_done_milestone_is_out_of_sequence(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        host = _edit_ready_host(tmp_path, monkeypatch)
        before = _state_path(host).read_bytes()
        payload = _payload_file(tmp_path, "title: rewrite history\n")

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m1", "--from-file", payload, "--json"],
        )
        assert code == 3 and envelope["error"]["code"] == "milestone-out-of-sequence", (
            f"FAIL AC-12(c): done milestones are not editable (WM §4.2), "
            f"got {code} {envelope.get('error')!r}"
        )
        # review (peer review): a refused edit never touches state.
        assert _state_path(host).read_bytes() == before, (
            "FAIL AC-12(c)/XS-M6: a refused edit leaves state.yaml byte-identical"
        )

    def test_c2_done_milestone_repairs_relocated_verification_with_ownership(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        host = _edit_ready_host(tmp_path, monkeypatch)
        payload = _payload_file(
            tmp_path,
            """\
owns:
  - src/example_app/parser.py
  - tests/test_parser.py
  - tests/test_parser_current.py
verification:
  command: pytest tests/test_parser_current.py -q
  expected: all tests pass
""",
        )

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m1", "--from-file", payload, "--json"],
        )
        assert code == 0, envelope
        milestone = next(
            row
            for row in _read_yaml(_state_path(host))["milestones"]
            if row["id"] == "m1"
        )
        assert milestone["status"] == "done"
        assert milestone["verification"] == {
            "command": "pytest tests/test_parser_current.py -q",
            "expected": "all tests pass",
        }
        assert milestone["owns"][-1] == "tests/test_parser_current.py"

    def test_d_depends_on_missing_milestone_is_out_of_sequence(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        host = _edit_ready_host(tmp_path, monkeypatch)
        before = _state_path(host).read_bytes()
        payload = _payload_file(tmp_path, "depends_on: [m9]\n")

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m2", "--from-file", payload, "--json"],
        )
        assert code == 3 and envelope["error"]["code"] == "milestone-out-of-sequence", (
            f"FAIL AC-12(d): got {code} {envelope.get('error')!r}"
        )
        assert _state_path(host).read_bytes() == before, (
            "FAIL AC-12(d)/XS-M6: a refused edit leaves state.yaml byte-identical"
        )

    def test_e_missing_ref_is_usage_with_existing_ids_hint(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        _edit_ready_host(tmp_path, monkeypatch)
        payload = _payload_file(tmp_path, "title: anything\n")

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m9", "--from-file", payload, "--json"],
        )
        assert code == 2, (
            f"FAIL AC-12(e)/REQ-17: a missing ref is usage exit 2 (not a "
            f"sequencing fault), got {code} {envelope.get('error')!r}"
        )
        blob = envelope["error"]["message"] + " " + envelope["error"]["hint"]
        assert "m1" in blob and "m2" in blob, (
            "FAIL AC-12(e): the hint must list the existing milestone ids"
        )

    def test_f_dry_run_previews_merge_and_leaves_state_byte_identical(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        host = _edit_ready_host(tmp_path, monkeypatch)
        payload = _payload_file(tmp_path, self.EDIT_PAYLOAD)
        before = _state_path(host).read_bytes()

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m2", "--from-file", payload, "--dry-run", "--json"],
        )
        assert code == 0, f"FAIL AC-12(f): dry-run edit must exit 0, got {code}"
        data = envelope["data"]
        assert data.get("dry_run") is True
        assert data["milestone"]["title"] == "Wire and harden the CLI", (
            "FAIL AC-12(f)/R-P3-2: the previewed post-merge object rides the "
            "dry-run payload"
        )
        assert _state_path(host).read_bytes() == before, (
            "FAIL AC-12(f): dry-run leaves state.yaml byte-identical"
        )

    @pytest.mark.parametrize(
        ("label", "make_payload"),
        [
            ("nonexistent-path", lambda tmp: "ghost-payload.yaml"),
            (
                "malformed-yaml",
                lambda tmp: _payload_file(tmp, "title: [unclosed\n", "bad.yaml"),
            ),
            (
                "non-mapping",
                lambda tmp: _payload_file(tmp, "- a\n- list\n", "list.yaml"),
            ),
        ],
    )
    def test_g_reader_faults_are_usage_with_state_unchanged(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch, label, make_payload
    ) -> None:
        host = _edit_ready_host(tmp_path, monkeypatch, name=f"host-{label}")
        payload = make_payload(tmp_path)
        before = _state_path(host).read_bytes()

        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m2", "--from-file", payload, "--json"],
        )
        assert code == 2, (
            f"FAIL AC-12(g)/SP-I5 [{label}]: reader faults are usage exit 2, "
            f"got {code} {envelope.get('error')!r}"
        )
        assert _state_path(host).read_bytes() == before

    def test_cas_conflict_routes_to_exit_five(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        from heddle.runtime import write_path

        host = _edit_ready_host(tmp_path, monkeypatch)
        payload = _payload_file(tmp_path, self.EDIT_PAYLOAD)
        before = _state_path(host).read_bytes()

        def forced_conflict(*_args: Any, **_kwargs: Any):
            raise write_path.Conflict("forced stale revision for AC-12")

        monkeypatch.setattr(write_path, "commit_state", forced_conflict)
        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["milestone", "edit", "m2", "--from-file", payload, "--json"],
        )
        assert code == 5 and envelope["error"]["code"] == "conflict", (
            f"FAIL AC-12/REQ-18: a CAS conflict exits 5/conflict, got {code} "
            f"{envelope.get('error')!r}"
        )
        assert _state_path(host).read_bytes() == before
