"""Public sync command, projection, filesystem, and envelope behavior."""

from __future__ import annotations

import builtins
import errno
import importlib
import json
import os
import re
import shutil
import stat
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from heddle.kernel.knowledge import AC_HEADING, MILESTONE_HEADING, VERIFIED_BY
from heddle.kernel.model import derive_next_actions, resolve_snapshot
from heddle.kernel.project_config import KernelError, load_project_config
from tests.runtime.adoption_helpers import adopt_fixture_host
from tests.runtime.authoring_helpers import tree_snapshot
from tests.runtime.policy_helpers import WRITE_SEAMS, assert_runtime_write_seams
from tests.runtime.write_path_helpers import copy_host
from tests.tiering_helpers import prepare_input, wire_policy

pytestmark = pytest.mark.acceptance

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"

PLAN_ID = "plan-status"
SESSION_ID = "session-entry"
MIRROR_ID = "mirror"
PLAN_BEGIN = "<!-- heddle:begin plan-status -->"
PLAN_END = "<!-- heddle:end plan-status -->"
SESSION_BEGIN = "<!-- heddle:begin session-entry -->"
SESSION_END = "<!-- heddle:end session-entry -->"

SESSION_BODY = (
    "\nThis repository's feature workflow runs through the `heddle` CLI.\n\n"
    "To enter a feature-work session:\n\n"
    "1. Run `heddle orient`.\n"
    "2. Follow the `next_actions` it returns.\n"
    "3. Run `heddle kickoff` only when `orient` routes there.\n\n"
)
CREATED_AGENTS = SESSION_BEGIN + "\n" + SESSION_BODY + SESSION_END + "\n"


def _sync_module() -> ModuleType:
    try:
        return importlib.import_module("heddle.runtime.sync")
    except ModuleNotFoundError as error:
        if error.name != "heddle.runtime.sync":
            raise
        pytest.fail(
            "FAIL M7 red discriminator: heddle.runtime.sync does not exist; "
            "implement the m1 module before driving this m2 command suite"
        )


def _json_run(run_cli, envelope_tools, argv: list[str]) -> tuple[int, dict[str, Any]]:
    code, stdout, stderr = run_cli(argv)
    assert stderr == "", (
        f"FAIL M7 JSON envelope: expected clean stderr for {argv!r}, got {stderr!r}"
    )
    envelope = envelope_tools.parse(stdout)
    envelope_tools.assert_shape(envelope)
    return code, envelope


def _admit_feature(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    *,
    slug: str,
) -> tuple[int, dict[str, Any]]:
    root = Path.cwd()
    if not (root / ".git").exists():
        from tests.content_identity_helpers import git

        git(root, "init", "-q")
        git(root, "config", "user.name", "Sync fixture")
        git(root, "config", "user.email", "sync@example.invalid")
        git(root, "add", ".")
        git(root, "commit", "-qm", "sync fixture baseline")
    (Path.cwd() / "brief.md").write_text("# Research\nOne declared behavior.\n")
    prepared = tmp_path / f"{slug}-prepare.json"
    prepared.write_text(json.dumps(prepare_input()))
    policy = tmp_path / f"{slug}-policy.json"
    policy.write_text(json.dumps(wire_policy()))
    for argv in (
        [
            "feature",
            "prepare",
            slug,
            "--area",
            "runtime",
            "--from-file",
            str(prepared),
            "--json",
        ],
        [
            "feature",
            "policy",
            slug,
            "--from-file",
            str(policy),
            "--json",
        ],
    ):
        code, envelope = _json_run(run_cli, envelope_tools, argv)
        assert code == 0, f"test setup: intake command failed: {envelope!r}"
    return _json_run(
        run_cli,
        envelope_tools,
        ["feature", "start", slug, "--json"],
    )


def _created_demo_host(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    slug: str = "demo",
) -> Path:
    host = copy_host(tmp_path, TINY, name=name)
    shutil.rmtree(host / "plans" / "sample-feature")
    shutil.rmtree(host / "docs" / "features" / "example")
    monkeypatch.chdir(host)
    code, envelope = _admit_feature(
        run_cli,
        envelope_tools,
        tmp_path,
        slug=slug,
    )
    assert code == 0, (
        f"test setup: feature start {slug} must succeed, got exit {code}: "
        f"{envelope.get('error')!r}"
    )
    return host


def _replace_quick_orientation(plan_path: Path, managed_text: str) -> None:
    original = plan_path.read_text(encoding="utf-8")
    replacement = "## Quick Orientation\n\n" + managed_text.rstrip("\n") + "\n\n"
    rendered, count = re.subn(
        r"(?ms)^## Quick Orientation\n.*?(?=^## |\Z)",
        replacement,
        original,
        count=1,
    )
    assert count == 1, "test setup: plan scaffold must have Quick Orientation"
    plan_path.write_text(rendered, encoding="utf-8")


def _force_marker_ready(plan_path: Path, body: str = "stale status\n") -> None:
    _replace_quick_orientation(
        plan_path,
        PLAN_BEGIN + "\n" + body.rstrip("\n") + "\n" + PLAN_END,
    )


def _marker_ready_demo_host(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
) -> Path:
    host = _created_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name=name,
    )
    _force_marker_ready(host / "plans" / "demo" / "plan.md")
    return host


def _managed_outside(text: str, begin: str, end: str) -> tuple[bytes, bytes]:
    lines = text.splitlines(keepends=True)
    begin_rows = [
        index for index, line in enumerate(lines) if _line_content(line) == begin
    ]
    end_rows = [index for index, line in enumerate(lines) if _line_content(line) == end]
    assert len(begin_rows) == 1 and len(end_rows) == 1, (
        "test setup: outside-byte oracle needs one exact marker pair"
    )
    begin_row, end_row = begin_rows[0], end_rows[0]
    assert begin_row < end_row, "test setup: marker pair must be ordered"
    return (
        "".join(lines[: begin_row + 1]).encode("utf-8"),
        "".join(lines[end_row:]).encode("utf-8"),
    )


def _managed_body(text: str, begin: str, end: str) -> str:
    lines = text.splitlines(keepends=True)
    begin_row = next(
        index for index, line in enumerate(lines) if _line_content(line) == begin
    )
    end_row = next(
        index for index, line in enumerate(lines) if _line_content(line) == end
    )
    return "".join(lines[begin_row + 1 : end_row])


def _line_content(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def _root_snapshot(root: Path) -> tuple[tuple[Any, ...], ...]:
    """Byte, link, mode, inode, and mtime oracle for a throwaway root."""
    entries: list[tuple[Any, ...]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            entries.append((relative, "symlink", os.readlink(path), info.st_mode))
        elif stat.S_ISREG(info.st_mode):
            try:
                content: bytes | tuple[str, str] = path.read_bytes()
            except OSError as error:
                content = ("unreadable", type(error).__name__)
            entries.append(
                (
                    relative,
                    "file",
                    content,
                    info.st_mode,
                    info.st_ino,
                    info.st_mtime_ns,
                )
            )
        elif stat.S_ISDIR(info.st_mode):
            entries.append((relative, "dir", info.st_mode, info.st_mtime_ns))
        else:
            entries.append((relative, "other", info.st_mode, info.st_mtime_ns))
    return tuple(entries)


def _path_snapshot(path: Path) -> tuple[Any, ...]:
    if not os.path.lexists(path):
        return ("absent",)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return ("symlink", os.readlink(path), info.st_mode)
    if stat.S_ISREG(info.st_mode):
        return (
            "file",
            path.read_bytes(),
            info.st_mode,
            info.st_ino,
            info.st_mtime_ns,
        )
    if stat.S_ISDIR(info.st_mode):
        return ("dir", sorted(child.name for child in path.iterdir()), info.st_mode)
    return ("other", info.st_mode)


def _target_rows(*actions: str) -> list[dict[str, str]]:
    rows = [
        {
            "block": PLAN_ID,
            "path": "plans/demo/plan.md",
            "action": actions[0],
        },
        {
            "block": SESSION_ID,
            "path": "AGENTS.md",
            "action": actions[1],
        },
    ]
    if len(actions) > 2:
        # The default `sync.mirror` (CLAUDE.md) follows AGENTS.md.
        rows.append({"block": MIRROR_ID, "path": "CLAUDE.md", "action": actions[2]})
    return rows


def _error_blob(envelope: dict[str, Any]) -> str:
    return json.dumps(
        {
            "error": envelope.get("error"),
            "diagnostics": envelope.get("diagnostics"),
            "next_actions": envelope.get("next_actions"),
        },
        sort_keys=True,
    ).lower()


def _has_term(text: str, term: str) -> bool:
    """Match a remediation term as a token, never inside another word."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def test_ac01_red_cli_preserves_crlf_outside_and_writes_lf_body(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1 red discriminator: command I/O never normalizes host CRLF."""
    host = _created_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="ac01-cli-crlf",
    )
    plan = host / "plans" / "demo" / "plan.md"
    prefix = (
        b"# Host plan\r\n"
        b"\r\n"
        b"## Quick Orientation\r\n"
        b"\r\n" + PLAN_BEGIN.encode("utf-8") + b"\r\n"
    )
    suffix = (
        PLAN_END.encode("utf-8")
        + b"\r\n"
        + b"Host trailing whitespace \t\r\n"
        + b"Host EOF has no newline"
    )
    plan.write_bytes(prefix + b"stale body\r\n" + suffix)

    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )
    assert code == 0, (
        f"FAIL AC-1 [CLI CRLF]: sync must succeed, got {code}: "
        f"{envelope.get('error')!r}"
    )
    projected = plan.read_bytes()
    assert projected.startswith(prefix) and projected.endswith(suffix), (
        "FAIL AC-1 [CLI CRLF]: command I/O changed a host/marker byte outside "
        "the managed region"
    )
    managed = projected[len(prefix) : len(projected) - len(suffix)]
    assert managed.endswith(b"\n") and b"\r" not in managed, (
        "FAIL AC-1 [CLI CRLF]: generated body lines must be bare-LF while "
        "outside bytes retain CRLF and missing final newline"
    )


@pytest.mark.parametrize(
    ("fault", "managed_text", "hint_terms"),
    [
        ("absent", "host prose only", ("begin", "end")),
        ("begin-only", PLAN_BEGIN + "\nstale", ("end",)),
        ("end-only", "stale\n" + PLAN_END, ("begin",)),
        (
            "duplicate-begin",
            PLAN_BEGIN + "\n" + PLAN_BEGIN + "\nstale\n" + PLAN_END,
            ("duplicate", "begin"),
        ),
        (
            "duplicate-end",
            PLAN_BEGIN + "\nstale\n" + PLAN_END + "\n" + PLAN_END,
            ("duplicate", "end"),
        ),
        ("reversed", PLAN_END + "\nstale\n" + PLAN_BEGIN, ("order",)),
        ("leading-space", " " + PLAN_BEGIN + "\nstale\n" + PLAN_END, ("begin",)),
        ("trailing-space", PLAN_BEGIN + " \nstale\n" + PLAN_END, ("begin",)),
        (
            "case-variant",
            "<!-- heddle:BEGIN plan-status -->\nstale\n" + PLAN_END,
            ("begin",),
        ),
        (
            "attribute-syntax",
            '<!-- heddle:begin id="plan-status" -->\nstale\n' + PLAN_END,
            ("begin",),
        ),
        (
            "wrong-block-id",
            "<!-- heddle:begin session-entry -->\nstale\n" + PLAN_END,
            ("begin",),
        ),
    ],
    ids=[
        "absent",
        "begin-only",
        "end-only",
        "duplicate-begin",
        "duplicate-end",
        "reversed",
        "leading-space",
        "trailing-space",
        "case-variant",
        "attribute-syntax",
        "wrong-block-id",
    ],
)
def test_ac02_red_cli_marker_fault_taxonomy_refuses_without_writes(
    fault: str,
    managed_text: str,
    hint_terms: tuple[str, ...],
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2 red discriminator: CLI half covers six faults + five near misses."""
    host = _created_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name=f"ac02-{fault}",
    )
    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    _replace_quick_orientation(plan, managed_text)
    before = plan.read_bytes()

    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        f"FAIL AC-2 [{fault}]: marker refusal must be workspace-invalid/3, "
        f"got exit {code}: {envelope.get('error')!r}"
    )
    message = envelope["error"]["message"].lower()
    hint = envelope["error"]["hint"].lower()
    detail = message + "\n" + hint
    assert "plans/demo/plan.md" in message, (
        f"FAIL AC-2 [{fault}]: error.message must name the plan target"
    )
    assert all(_has_term(detail, term) for term in hint_terms), (
        f"FAIL AC-2 [{fault}]: subtype/remediation missing {hint_terms!r}: {detail}"
    )
    expected_markers = (
        (PLAN_END,)
        if fault == "begin-only"
        else (PLAN_BEGIN,)
        if fault == "end-only"
        or fault
        in {
            "leading-space",
            "trailing-space",
            "case-variant",
            "attribute-syntax",
            "wrong-block-id",
        }
        else (PLAN_BEGIN, PLAN_END)
        if fault == "absent"
        else ()
    )
    assert all(marker.lower() in hint for marker in expected_markers), (
        f"FAIL AC-2 [{fault}]: error.hint must show the exact relevant marker "
        f"line(s) {expected_markers!r}, got {hint!r}"
    )
    assert plan.read_bytes() == before and not agents.exists(), (
        f"FAIL AC-2 [{fault}]: preflight refusal must leave both targets untouched"
    )


@pytest.mark.parametrize(
    ("line_break", "label"),
    [
        pytest.param("\n", "lf", id="lf"),
        pytest.param("\r", "cr", id="cr"),
    ],
)
def test_ac04_red_cli_unsafe_snapshot_value_refuses_before_both_writes(
    line_break: str,
    label: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4 red discriminator: CLI maps doctored CR/LF to typed exit 3."""
    sync = _sync_module()
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name=f"ac04-{label}",
    )
    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    before = plan.read_bytes()
    real_render = sync.render_plan_status

    def unsafe_render(snapshot, next_actions):
        return real_render(
            replace(snapshot, feature="demo" + line_break + "injected"),
            next_actions,
        )

    monkeypatch.setattr(sync, "render_plan_status", unsafe_render)
    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        f"FAIL AC-4 [{label}]: unsafe inline value must exit 3, got {code}: "
        f"{envelope.get('error')!r}"
    )
    message = envelope["error"]["message"].lower()
    hint = envelope["error"]["hint"].lower()
    assert "plans/demo/plan.md" in message and "feature" in message, (
        f"FAIL AC-4 [{label}]: error.message must name plan target and feature "
        f"field, got {message!r}"
    )
    assert (
        "cr" in hint
        and "lf" in hint
        and (_has_term(hint, "repair") or _has_term(hint, "remove"))
    ), f"FAIL AC-4 [{label}]: error.hint must name CR/LF repair, got {hint!r}"
    assert plan.read_bytes() == before and not agents.exists(), (
        f"FAIL AC-4 [{label}]: unsafe render must happen before either write"
    )


def test_review_synthesis_non_utf8_inline_value_refuses_before_both_writes(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    review: invalid Unicode has dry-run/write parity at renderer preflight.
    """
    sync = _sync_module()
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="sy-i1-invalid-unicode",
    )
    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    before = plan.read_bytes()
    real_render = sync.render_plan_status

    def invalid_unicode_render(snapshot, next_actions):
        return real_render(
            replace(snapshot, feature="demo\ud800"),
            next_actions,
        )

    monkeypatch.setattr(sync, "render_plan_status", invalid_unicode_render)
    observables: list[dict[str, Any]] = []
    for extra_args in ([], ["--dry-run"]):
        code, envelope = _json_run(
            run_cli,
            envelope_tools,
            ["sync", "--feature", "demo", *extra_args, "--json"],
        )
        assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
        assert "plans/demo/plan.md" in envelope["error"]["message"]
        assert "feature" in envelope["error"]["message"]
        assert "UTF-8" in envelope["error"]["message"]
        assert "invalid Unicode" in envelope["error"]["hint"]
        observables.append(
            {
                "error": envelope["error"],
                "diagnostics": envelope["diagnostics"],
                "next_actions": envelope["next_actions"],
            }
        )
        assert plan.read_bytes() == before and not agents.exists(), (
            "FAIL SY-I1: invalid Unicode must refuse before either target in "
            "both write and dry-run modes"
        )
    assert observables[0] == observables[1], (
        "FAIL SY-I1: invalid Unicode must produce an identical error, "
        "diagnostics, and next-actions envelope in write and dry-run modes"
    )


def test_ac07_ac08_ac15_red_populate_then_zero_diff_dogfood_equivalent(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3/5/7/8/15 red discriminator: populate + zero-diff rerun."""
    sync = _sync_module()
    host = _created_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="ac07-ac08-acceptance",
    )
    plan = host / "plans" / "demo" / "plan.md"
    state = host / "plans" / "demo" / "state.yaml"
    agents = host / "AGENTS.md"
    state_before = state.read_bytes()
    outside_before = _managed_outside(
        plan.read_text(encoding="utf-8"), PLAN_BEGIN, PLAN_END
    )
    snapshot = resolve_snapshot(load_project_config(host), "demo")
    next_actions = derive_next_actions(snapshot)
    expected_body = sync.render_plan_status(snapshot, next_actions)

    first_code, first = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )
    assert first_code == 0, (
        f"FAIL AC-7: first sync must exit 0, got {first_code}: {first.get('error')!r}"
    )
    assert first["data"] == {
        "feature": "demo",
        "targets": _target_rows("updated", "created", "created"),
    }, f"FAIL AC-7: exact ordered first-run payload required, got {first['data']!r}"
    assert state.read_bytes() == state_before, (
        "FAIL AC-7/REQ-22: sync must not record a fact or alter state.yaml"
    )
    assert (
        _managed_outside(plan.read_text(encoding="utf-8"), PLAN_BEGIN, PLAN_END)
        == outside_before
    ), "FAIL AC-7: plan host bytes outside markers changed"
    plan_body = _managed_body(plan.read_text(encoding="utf-8"), PLAN_BEGIN, PLAN_END)
    assert plan_body == expected_body, (
        "FAIL AC-3/AC-7: command must install exactly the kernel-derived "
        "render_plan_status body"
    )
    assert "Generated status snapshot — not live." in plan_body, (
        "FAIL AC-3/AC-7: populated notice must disclaim liveness"
    )
    assert "`state.yaml`, which is authoritative" in plan_body, (
        "FAIL AC-3/AC-7: populated notice must name the authoritative state"
    )
    assert "`heddle sync --feature demo`" in plan_body, (
        "FAIL AC-3/AC-7: populated notice must name the exact refresh command"
    )
    field_lines = [line for line in plan_body.splitlines() if line.startswith("- ")]
    assert [line.split(":", 1)[0] for line in field_lines] == [
        "- feature",
        "- stage",
        "- milestone",
        "- conditions",
        "- next",
    ], (
        "FAIL AC-3/AC-7: populated plan body must carry exactly the five pinned "
        f"fields in order, got {field_lines!r}"
    )
    assert "- feature: demo" in field_lines, (
        f"FAIL AC-3/AC-7: populated body has wrong snapshot values: {field_lines!r}"
    )
    for grammar_name, grammar in (
        ("MILESTONE_HEADING", MILESTONE_HEADING),
        ("AC_HEADING", AC_HEADING),
        ("VERIFIED_BY", VERIFIED_BY),
    ):
        assert grammar.search(plan_body) is None, (
            f"FAIL AC-3/AC-7: integrated body matched {grammar_name}"
        )
    assert not any(line.startswith("#") for line in plan_body.splitlines()), (
        "FAIL AC-3/AC-7: integrated generated body must contain no headings"
    )
    assert agents.read_text(encoding="utf-8") == CREATED_AGENTS, (
        "FAIL AC-7/AC-9: first sync must create the exact block-only AGENTS.md"
    )
    mirror = host / "CLAUDE.md"
    assert mirror.is_file() and not mirror.is_symlink(), (
        "FAIL mirror: first sync must create the default CLAUDE.md as a regular file"
    )
    assert mirror.read_bytes() == agents.read_bytes(), (
        "FAIL mirror: CLAUDE.md must be byte-identical to AGENTS.md"
    )

    before_second = {
        "plan": _path_snapshot(plan),
        "agents": _path_snapshot(agents),
        "mirror": _path_snapshot(mirror),
        "state": _path_snapshot(state),
    }
    second_code, second = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )
    after_second = {
        "plan": _path_snapshot(plan),
        "agents": _path_snapshot(agents),
        "mirror": _path_snapshot(mirror),
        "state": _path_snapshot(state),
    }
    assert second_code == 0, (
        f"FAIL AC-8: second sync must exit 0, got {second_code}: "
        f"{second.get('error')!r}"
    )
    assert second["data"] == {
        "feature": "demo",
        "targets": _target_rows("unchanged", "unchanged", "unchanged"),
    }, f"FAIL AC-8: exact zero-diff payload required, got {second['data']!r}"
    assert after_second == before_second, (
        "FAIL AC-5/AC-8/AC-15: second sync rewrote a current target; bytes, "
        "inode, mtime, mode, and state must remain identical"
    )


def test_ac09_red_created_agents_file_is_exact_block_only_constant(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-9 red discriminator: creation authors no byte outside its block."""
    sync = _sync_module()
    assert sync.render_session_entry() == SESSION_BODY, (
        "FAIL AC-9/REQ-13: the prescribed constant renderer must return the "
        "pinned session-entry body"
    )
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="ac09-block-only",
    )
    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )
    assert code == 0, (
        f"FAIL AC-9: sync must create AGENTS.md, got exit {code}: "
        f"{envelope.get('error')!r}"
    )
    agents = host / "AGENTS.md"
    assert agents.read_bytes() == CREATED_AGENTS.encode("utf-8"), (
        "FAIL AC-9: AGENTS.md must be exactly BEGIN + pinned constant + END + "
        "one final LF, with no unmanaged pointer or feature/status prose"
    )
    winner = agents.read_bytes()
    children_before = {path.name for path in host.iterdir()}
    with pytest.raises((KernelError, FileExistsError, OSError)):
        sync.install_projection(agents, "replacement must not win\n", create=True)
    assert agents.read_bytes() == winner, (
        "FAIL AC-9/REQ-14: exclusive create must preserve a concurrently "
        "landed winner byte-for-byte"
    )
    assert {path.name for path in host.iterdir()} == children_before, (
        "FAIL AC-9/REQ-14: refused exclusive create must clean every temporary "
        "or staging path"
    )


def test_ac09_red_exclusive_create_lost_race_preserves_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-9 red discriminator: either approved exclusive claim loses safely."""
    sync = _sync_module()
    target = tmp_path / "AGENTS.md"
    winner = b"concurrent host winner\n"
    children_before = {path.name for path in tmp_path.iterdir()}
    real_os_open = os.open
    real_os_link = os.link

    def land_winner() -> None:
        if not target.exists():
            target.write_bytes(winner)

    def racing_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        candidate = Path(path) if not isinstance(path, bytes) else None
        if (
            candidate is not None
            and candidate.absolute() == target.absolute()
            and flags & os.O_EXCL
        ):
            land_winner()
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    def racing_link(
        source: Any,
        destination: Any,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        candidate = Path(destination) if not isinstance(destination, bytes) else None
        if candidate is not None and candidate.absolute() == target.absolute():
            land_winner()
        real_os_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", racing_open)
        patch.setattr(os, "link", racing_link)
        with pytest.raises(KernelError) as caught:
            sync.install_projection(target, CREATED_AGENTS, create=True)

    assert "appeared during install" in caught.value.message, (
        "FAIL SY-M2: a lost exclusive-create race needs concurrent-writer "
        f"remediation, got {caught.value!r}"
    )
    assert "re-run heddle sync" in caught.value.hint
    assert target.read_bytes() == winner, (
        "FAIL AC-9/REQ-14: an exclusive-create lost race overwrote the "
        "concurrent winner"
    )
    assert {path.name for path in tmp_path.iterdir()} == children_before | {
        target.name
    }, "FAIL AC-9/REQ-14: lost race left a staging or temporary path"


def test_review_synthesis_create_cleanup_failure_is_loud_and_not_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    review: a post-link cleanup failure is loud and its stage is never reused.
    """
    sync = _sync_module()
    target = tmp_path / "AGENTS.md"
    real_unlink = Path.unlink
    residual: list[Path] = []

    def fail_post_link_cleanup(
        self: Path,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if (
            self.parent == tmp_path
            and self.name.startswith(".AGENTS.md.")
            and target.exists()
        ):
            residual.append(self)
            raise OSError("injected post-link cleanup failure")
        real_unlink(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_post_link_cleanup)
        with pytest.raises(KernelError) as caught:
            sync.install_projection(target, CREATED_AGENTS, create=True)

    error = caught.value
    assert error.code == "workspace-invalid"
    assert "was created but staging cleanup failed" in error.message
    assert target.read_bytes() == CREATED_AGENTS.encode("utf-8")
    assert residual
    residual_path = residual[0]
    assert residual_path.exists()
    before = (target.read_bytes(), residual_path.read_bytes())

    with pytest.raises(KernelError, match="appeared during install"):
        sync.install_projection(target, "replacement must not win\n", create=True)

    assert (target.read_bytes(), residual_path.read_bytes()) == before, (
        "FAIL SY-C5: a later create attempt reopened or truncated the residual "
        "hard-link alias"
    )
    real_unlink(residual_path)


def test_ac09_unsupported_hard_links_are_a_specific_supported_fs_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    review: exclusive creation names its supported-filesystem prerequisite.
    """
    sync = _sync_module()
    target = tmp_path / "AGENTS.md"
    children_before = {path.name for path in tmp_path.iterdir()}

    def unsupported_link(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(errno.EOPNOTSUPP, "hard links disabled by filesystem")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", unsupported_link)
        with pytest.raises(KernelError) as caught:
            sync.install_projection(target, CREATED_AGENTS, create=True)

    error = caught.value
    assert error.code == "workspace-invalid"
    assert "does not support" in error.message
    assert "atomic exclusive hard-link" in error.message
    assert "filesystem supporting" in error.hint
    assert not target.exists()
    assert {path.name for path in tmp_path.iterdir()} == children_before


def test_ac18_primary_install_and_cleanup_failures_preserve_both_contexts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    review: cleanup failure cannot hide or erase the primary install fault.
    """
    sync = _sync_module()
    target = tmp_path / "AGENTS.md"
    real_unlink = Path.unlink
    residual: list[Path] = []

    def fail_link(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(errno.EIO, "injected primary install failure")

    def fail_stage_cleanup(
        self: Path,
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        if self.parent == tmp_path and self.name.startswith(".AGENTS.md."):
            residual.append(self)
            raise OSError("injected cleanup failure")
        real_unlink(self, *_args, **_kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", fail_link)
        patch.setattr(Path, "unlink", fail_stage_cleanup)
        with pytest.raises(KernelError) as caught:
            sync.install_projection(target, CREATED_AGENTS, create=True)

    error = caught.value
    assert "injected primary install failure" in error.message
    assert "staging cleanup also failed" in error.message
    assert "injected cleanup failure" in error.message
    assert "residual staging path" in error.message
    assert not target.exists() and residual and residual[0].exists()
    real_unlink(residual[0])


def test_lstat_refusal_preserves_os_error_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    review: inspection failures retain the exception type and message.
    """
    sync = _sync_module()
    target = tmp_path / "AGENTS.md"

    def denied_lstat(self: Path) -> os.stat_result:
        assert self == target
        raise PermissionError("injected lstat denial")

    monkeypatch.setattr(Path, "lstat", denied_lstat)
    with pytest.raises(KernelError) as caught:
        sync._target_lstat(target, "AGENTS.md")

    assert "PermissionError" in caught.value.message
    assert "injected lstat denial" in caught.value.message


@pytest.mark.parametrize(
    "shape",
    ["unmarked", "empty", "symlink", "directory"],
)
def test_ac10_red_existing_agents_is_never_adopted(
    shape: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-10 red discriminator: four existing AGENTS.md shapes refuse."""
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name=f"ac10-{shape}",
    )
    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    if shape == "unmarked":
        agents.write_text("host-owned instructions\n", encoding="utf-8")
    elif shape == "empty":
        agents.write_bytes(b"")
    elif shape == "symlink":
        backing = host / "marked-agents.md"
        backing.write_text(CREATED_AGENTS, encoding="utf-8")
        agents.symlink_to(backing.name)
    else:
        agents.mkdir()
    before_root = _root_snapshot(host)

    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        f"FAIL AC-10 [{shape}]: never-adopt refusal must exit 3, got {code}: "
        f"{envelope.get('error')!r}"
    )
    message = envelope["error"]["message"].lower()
    hint = envelope["error"]["hint"].lower()
    assert "agents.md" in message, (
        f"FAIL AC-10 [{shape}]: error.message must name AGENTS.md"
    )
    if shape in {"unmarked", "empty"}:
        assert (
            SESSION_BEGIN.lower() in hint and SESSION_END.lower() in hint
        ) or _has_term(hint, "move"), (
            f"FAIL AC-10 [{shape}]: hint must show exact markers or move-aside "
            f"remediation, got {hint!r}"
        )
    else:
        assert _has_term(hint, "move") or _has_term(hint, "regular"), (
            f"FAIL AC-10 [{shape}]: hint must repair the non-regular target, "
            f"got {hint!r}"
        )
    assert _root_snapshot(host) == before_root, (
        f"FAIL AC-10 [{shape}]: preflight refusal changed AGENTS.md or sibling plan"
    )
    assert plan.exists(), "FAIL AC-10: the sibling plan must remain present"


def test_ac10_red_human_refusal_is_informative_and_keeps_exit_taxonomy(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-10 red discriminator: human mode is actionable, not JSON-only."""
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="ac10-human",
    )
    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    agents.write_text("host-owned unmarked instructions\n", encoding="utf-8")
    plan_before = plan.read_bytes()
    agents_before = agents.read_bytes()

    code, stdout, stderr = run_cli(["sync", "--feature", "demo"])

    rendered = (stdout + "\n" + stderr).lower()
    assert code == 3, (
        f"FAIL AC-10 [human]: typed refusal must remain exit 3, got exit "
        f"{code}, stdout={stdout!r}, stderr={stderr!r}"
    )
    parsed_streams: list[object] = []
    for stream in (stdout, stderr):
        try:
            parsed_streams.append(json.loads(stream))
        except (json.JSONDecodeError, TypeError):
            pass
    assert not any(
        isinstance(value, dict) and "schema_version" in value
        for value in parsed_streams
    ), "FAIL AC-10 [human]: bare sync must not emit the JSON envelope"
    assert "agents.md" in rendered, (
        f"FAIL AC-10 [human]: output must name the target, got {rendered!r}"
    )
    assert SESSION_BEGIN.lower() in rendered or _has_term(rendered, "move"), (
        "FAIL AC-10 [human]: output must carry marker or move-aside remediation, "
        f"got stdout={stdout!r}, stderr={stderr!r}"
    )
    assert plan.read_bytes() == plan_before and agents.read_bytes() == agents_before, (
        "FAIL AC-10 [human]: human-mode refusal changed a target"
    )


@pytest.mark.parametrize(
    "divergence",
    ["target-before-plan", "state-before-plan", "state-before-agents"],
)
def test_ac11_red_final_check_divergence_is_conflict_five_and_retryable(
    divergence: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-11 red discriminator: all three final-check races conflict/5."""
    sync = _sync_module()
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name=f"ac11-{divergence}",
    )
    plan = host / "plans" / "demo" / "plan.md"
    state = host / "plans" / "demo" / "state.yaml"
    agents = host / "AGENTS.md"
    plan_before = plan.read_bytes()
    armed = {"value": True}
    injected = {"value": False}
    from heddle.kernel import managed_regions

    real_replace = managed_regions.replace_managed_region
    real_install = sync.install_projection

    def mutate_after_plan_preflight(text: str, block_id: str, body: str) -> str:
        projected = real_replace(text, block_id, body)
        if (
            armed["value"]
            and not injected["value"]
            and block_id == PLAN_ID
            and divergence != "state-before-agents"
        ):
            injected["value"] = True
            if divergence == "target-before-plan":
                plan.write_bytes(plan.read_bytes() + b"concurrent host edit\n")
            else:
                state_text = state.read_text(encoding="utf-8")
                changed = state_text.replace("\nflow: hitl\n", "\nflow: auto\n", 1)
                assert changed != state_text, (
                    "test setup: injected current state field must change"
                )
                state.write_text(changed, encoding="utf-8")
        return projected

    def mutate_after_plan_install(path: Path, text: str, *, create: bool) -> None:
        real_install(path, text, create=create)
        if (
            armed["value"]
            and not injected["value"]
            and divergence == "state-before-agents"
            and path == plan
        ):
            injected["value"] = True
            state_text = state.read_text(encoding="utf-8")
            changed = state_text.replace("\nflow: hitl\n", "\nflow: auto\n", 1)
            assert changed != state_text, (
                "test setup: injected current state field must change"
            )
            state.write_text(changed, encoding="utf-8")

    monkeypatch.setattr(
        managed_regions, "replace_managed_region", mutate_after_plan_preflight
    )
    monkeypatch.setattr(sync, "install_projection", mutate_after_plan_install)
    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )
    assert injected["value"], (
        f"FAIL AC-11 [{divergence}]: the prescribed preflight/install barrier "
        "was not reached"
    )
    expected_path = (
        "AGENTS.md" if divergence == "state-before-agents" else ("plans/demo/plan.md")
    )
    assert code == 5 and envelope["error"]["code"] == "conflict", (
        f"FAIL AC-11 [{divergence}]: final-check divergence must be "
        f"conflict/5, got exit {code}: {envelope.get('error')!r}"
    )
    assert expected_path.lower() in _error_blob(envelope), (
        f"FAIL AC-11 [{divergence}]: conflict must name pending {expected_path}"
    )
    assert [item.get("command") for item in envelope["next_actions"]] == [
        "heddle sync --feature demo"
    ], (
        f"FAIL AC-11 [{divergence}]: retry action must preserve the explicit "
        f"feature, got {envelope['next_actions']!r}"
    )
    assert not agents.exists(), (
        f"FAIL AC-11 [{divergence}]: pending AGENTS.md must remain absent"
    )
    if divergence == "target-before-plan":
        assert plan.read_bytes() == plan_before + b"concurrent host edit\n", (
            "FAIL AC-11 [target-before-plan]: command overwrote the concurrent edit"
        )
        assert envelope["diagnostics"] == [], (
            "FAIL AC-11 [target-before-plan]: no command target completed"
        )
    elif divergence == "state-before-plan":
        assert plan.read_bytes() == plan_before and envelope["diagnostics"] == [], (
            "FAIL AC-11 [state-before-plan]: neither target may be written"
        )
    else:
        assert envelope["diagnostics"] == [
            {
                "severity": "info",
                "code": "sync-partial",
                "message": "plans/demo/plan.md: updated",
            }
        ], (
            "FAIL AC-11 [state-before-agents]: completed plan action must ride "
            f"the exact sync-partial diagnostic, got {envelope['diagnostics']!r}"
        )
        assert plan.read_bytes() != plan_before, (
            "FAIL AC-11 [state-before-agents]: plan should have completed first"
        )

    armed["value"] = False
    state_before_retry = state.read_bytes()
    retry_code, retry = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )
    assert retry_code == 0 and retry["ok"] is True, (
        f"FAIL AC-11 [{divergence}]: immediate retry must converge, got "
        f"exit {retry_code}: {retry.get('error')!r}"
    )
    assert state.read_bytes() == state_before_retry, (
        f"FAIL AC-11 [{divergence}]: retry must remain a pure state read"
    )


@pytest.mark.parametrize(
    "divergence",
    ["deleted", "directory", "symlink", "invalid-utf8"],
)
def test_ac11_final_check_target_shape_divergence_is_conflict(
    divergence: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    review: every provable post-preflight target change maps to conflict/5.
    """
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name=f"ac11-target-shape-{divergence}",
    )
    plan = host / "plans" / "demo" / "plan.md"
    state = host / "plans" / "demo" / "state.yaml"
    agents = host / "AGENTS.md"
    from heddle.kernel import managed_regions

    real_replace = managed_regions.replace_managed_region
    injected = {"value": False}

    def mutate_after_preflight(text: str, block_id: str, body: str) -> str:
        projected = real_replace(text, block_id, body)
        if block_id == PLAN_ID and not injected["value"]:
            injected["value"] = True
            plan.unlink()
            if divergence == "directory":
                plan.mkdir()
            elif divergence == "symlink":
                plan.symlink_to(state)
            elif divergence == "invalid-utf8":
                plan.write_bytes(b"\xff\xfe")
        return projected

    monkeypatch.setattr(
        managed_regions, "replace_managed_region", mutate_after_preflight
    )
    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )

    assert injected["value"]
    assert code == 5 and envelope["error"]["code"] == "conflict"
    assert "plans/demo/plan.md" in envelope["error"]["message"]
    assert envelope["diagnostics"] == []
    assert not agents.exists()


def test_ac12_red_dry_run_reports_would_actions_and_changes_nothing(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-12 red discriminator: dry-run follows preflight with zero writes."""
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="ac12-dry-run",
    )
    before_paths = tree_snapshot(host)
    before = _root_snapshot(host)

    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--dry-run", "--json"],
    )

    assert code == 0, (
        f"FAIL AC-12: dry-run must exit 0, got {code}: {envelope.get('error')!r}"
    )
    assert envelope["data"] == {
        "feature": "demo",
        "targets": _target_rows("would-update", "would-create", "would-create"),
        "dry_run": True,
    }, f"FAIL AC-12: exact dry-run payload required, got {envelope['data']!r}"
    assert tree_snapshot(host) == before_paths and _root_snapshot(host) == before, (
        "FAIL AC-12: dry-run changed path topology, bytes, mode, inode, or mtime"
    )


# ---------------------------------------------------------------------------
# search root-mirror increment (owner-ruled 2026-09-11): the default CLAUDE.md
# mirror rides sync as a third, full-file projection.
# ---------------------------------------------------------------------------


def test_mirror_symlink_to_agents_is_satisfied_without_writes(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _marker_ready_demo_host(
        run_cli, envelope_tools, tmp_path, monkeypatch, name="mirror-symlink"
    )
    mirror = host / "CLAUDE.md"
    mirror.symlink_to("AGENTS.md")

    code, envelope = _json_run(
        run_cli, envelope_tools, ["sync", "--feature", "demo", "--json"]
    )

    assert code == 0, f"FAIL mirror: symlink mirror must sync, got {envelope!r}"
    assert envelope["data"]["targets"] == _target_rows(
        "updated", "created", "unchanged"
    )
    assert mirror.is_symlink() and os.readlink(mirror) == "AGENTS.md", (
        "FAIL mirror: a symlink resolving to AGENTS.md must be left in place"
    )
    assert mirror.read_bytes() == (host / "AGENTS.md").read_bytes()


def test_mirror_stale_regular_file_is_rewritten(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _marker_ready_demo_host(
        run_cli, envelope_tools, tmp_path, monkeypatch, name="mirror-stale"
    )
    mirror = host / "CLAUDE.md"
    mirror.write_bytes(b"stale manual copy\r\n")

    dry_code, dry = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--dry-run", "--json"],
    )
    assert dry_code == 0 and dry["data"]["targets"][2] == {
        "block": MIRROR_ID,
        "path": "CLAUDE.md",
        "action": "would-update",
    }
    assert mirror.read_bytes() == b"stale manual copy\r\n"

    code, envelope = _json_run(
        run_cli, envelope_tools, ["sync", "--feature", "demo", "--json"]
    )

    assert code == 0 and envelope["data"]["targets"][2]["action"] == "updated"
    assert (
        mirror.read_bytes()
        == (host / "AGENTS.md").read_bytes()
        == (CREATED_AGENTS.encode("utf-8"))
    )


@pytest.mark.parametrize("shape", ["foreign-symlink", "directory"])
def test_mirror_foreign_shapes_refuse_with_zero_writes(
    shape: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _marker_ready_demo_host(
        run_cli, envelope_tools, tmp_path, monkeypatch, name=f"mirror-{shape}"
    )
    mirror = host / "CLAUDE.md"
    if shape == "foreign-symlink":
        (host / "elsewhere.md").write_text("not the source\n", encoding="utf-8")
        mirror.symlink_to("elsewhere.md")
    else:
        mirror.mkdir()
    before = _root_snapshot(host)

    code, envelope = _json_run(
        run_cli, envelope_tools, ["sync", "--feature", "demo", "--json"]
    )

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        f"FAIL mirror [{shape}]: foreign mirror shape must exit 3, got {envelope!r}"
    )
    blob = _error_blob(envelope)
    assert "claude.md" in blob and _has_term(blob, "move"), (
        f"FAIL mirror [{shape}]: refusal must name CLAUDE.md with a move-aside hint"
    )
    assert _root_snapshot(host) == before, (
        f"FAIL mirror [{shape}]: a mirror refusal must write no sibling target"
    )


def test_mirror_opt_out_projects_only_the_two_managed_blocks(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _marker_ready_demo_host(
        run_cli, envelope_tools, tmp_path, monkeypatch, name="mirror-opt-out"
    )
    config = host / ".heddle.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\nsync:\n  mirror: null\n",
        encoding="utf-8",
    )

    code, envelope = _json_run(
        run_cli, envelope_tools, ["sync", "--feature", "demo", "--json"]
    )

    assert code == 0 and envelope["data"]["targets"] == _target_rows(
        "updated", "created"
    )
    assert not (host / "CLAUDE.md").exists()


def test_mirror_declared_nested_path_is_created_with_parents(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _marker_ready_demo_host(
        run_cli, envelope_tools, tmp_path, monkeypatch, name="mirror-nested"
    )
    config = host / ".heddle.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\nsync:\n  mirror: .claude/CLAUDE.md\n",
        encoding="utf-8",
    )
    (host / ".claude").mkdir()

    code, envelope = _json_run(
        run_cli, envelope_tools, ["sync", "--feature", "demo", "--json"]
    )

    assert code == 0 and envelope["data"]["targets"][2] == {
        "block": MIRROR_ID,
        "path": ".claude/CLAUDE.md",
        "action": "created",
    }
    assert (host / ".claude" / "CLAUDE.md").read_bytes() == (
        host / "AGENTS.md"
    ).read_bytes()


def test_ac13_red_standard_envelope_and_full_active_feature_resolution_chain(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-13 red discriminator: pointer/explicit/ambiguous/usage/no-root."""
    host = _created_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="ac13-d24",
    )
    _force_marker_ready(host / "plans" / "demo" / "plan.md")
    code, _envelope = _admit_feature(
        run_cli,
        envelope_tools,
        tmp_path,
        slug="other",
    )
    assert code == 0, "test setup: second workspace must be created"
    _force_marker_ready(host / "plans" / "other" / "plan.md")

    switch_code, _switch = _json_run(
        run_cli, envelope_tools, ["feature", "switch", "demo", "--json"]
    )
    assert switch_code == 0, "test setup: recorded pointer must be writable"
    pointer_code, pointer = _json_run(run_cli, envelope_tools, ["sync", "--json"])
    assert pointer_code == 0 and pointer["data"]["feature"] == "demo", (
        f"FAIL AC-13 [pointer]: expected demo, got exit {pointer_code}: {pointer!r}"
    )
    assert pointer["data"]["targets"][0]["path"] == "plans/demo/plan.md", (
        "FAIL AC-13 [pointer]: target path must follow the resolved feature"
    )

    explicit_code, explicit = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "other", "--json"],
    )
    assert explicit_code == 0 and explicit["data"]["feature"] == "other", (
        f"FAIL AC-13 [explicit]: flag must override pointer, got "
        f"exit {explicit_code}: {explicit!r}"
    )
    assert explicit["data"]["targets"][0]["path"] == "plans/other/plan.md", (
        "FAIL AC-13 [explicit]: target path must follow the explicit override"
    )

    (host / ".git" / "heddle" / "active-feature").unlink()
    before_ambiguous = _root_snapshot(host)
    ambiguous_code, ambiguous = _json_run(run_cli, envelope_tools, ["sync", "--json"])
    assert ambiguous_code == 2 and ambiguous["error"]["code"] == (
        "feature-ambiguous"
    ), (
        f"FAIL AC-13 [ambiguous]: expected feature-ambiguous/2, got "
        f"exit {ambiguous_code}: {ambiguous.get('error')!r}"
    )
    ambiguous_message = ambiguous["error"]["message"].lower()
    assert "demo" in ambiguous_message and "other" in ambiguous_message, (
        "FAIL AC-13 [ambiguous]: error.message must list both candidates, "
        f"got {ambiguous_message!r}"
    )
    assert [item.get("command") for item in ambiguous["next_actions"]] == [
        "Select the active feature with heddle feature switch"
    ], (
        "FAIL AC-13 [ambiguous]: feature resolution ambiguity must route through the "
        "exact "
        f"feature-switch action, got {ambiguous['next_actions']!r}"
    )
    assert _root_snapshot(host) == before_ambiguous, (
        "FAIL AC-13 [ambiguous]: resolution failure must write no target"
    )

    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    plan.write_bytes(b"\xffinvalid plan: usage must win")
    agents.write_bytes(b"\xffinvalid agents: usage must win")
    before_usage = _root_snapshot(host)
    usage_code, usage = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--unknown", "--json"],
    )
    assert usage_code == 2 and usage["error"]["code"] == "usage", (
        f"FAIL AC-13 [unknown flag]: parse before target reads and exit 2, got "
        f"{usage_code}: {usage.get('error')!r}"
    )
    assert _root_snapshot(host) == before_usage, (
        "FAIL AC-13 [unknown flag]: usage failure changed the filesystem"
    )

    empty = tmp_path / "ac13-empty"
    empty.mkdir()
    shutil.copy2(TINY / ".heddle.yaml", empty / ".heddle.yaml")
    monkeypatch.chdir(empty)
    empty_before = _root_snapshot(empty)
    projection_reads: list[str] = []
    real_path_open = Path.open
    real_builtin_open = builtins.open
    real_os_open = os.open

    def guarded_path_open(self: Path, *args, **kwargs):
        if self.name in {"plan.md", "AGENTS.md"}:
            projection_reads.append(str(self))
            raise AssertionError(f"workspace-free sync read projection target {self}")
        return real_path_open(self, *args, **kwargs)

    def guarded_builtin_open(file, *args, **kwargs):
        if isinstance(file, str | os.PathLike):
            candidate = Path(file)
            if candidate.name in {"plan.md", "AGENTS.md"}:
                projection_reads.append(str(candidate))
                raise AssertionError(
                    f"workspace-free sync read projection target {candidate}"
                )
        return real_builtin_open(file, *args, **kwargs)

    def guarded_os_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        candidate = Path(path) if not isinstance(path, bytes) else None
        if candidate is not None and candidate.name in {"plan.md", "AGENTS.md"}:
            projection_reads.append(str(candidate))
            raise AssertionError(
                f"workspace-free sync read projection target {candidate}"
            )
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_path_open)
        patch.setattr(builtins, "open", guarded_builtin_open)
        patch.setattr(os, "open", guarded_os_open)
        empty_code, no_workspace = _json_run(
            run_cli, envelope_tools, ["sync", "--json"]
        )
    assert empty_code == 3 and no_workspace["error"]["code"] == ("workspace-invalid"), (
        f"FAIL AC-13 [workspace-free]: expected workspace-invalid/3, got "
        f"{empty_code}: {no_workspace.get('error')!r}"
    )
    empty_message = no_workspace["error"]["message"].lower()
    empty_hint = no_workspace["error"]["hint"].lower()
    assert "no active feature workspaces" in empty_message and (
        "heddle feature prepare <slug> --area <area>" in empty_hint
        and "heddle feature start --help" in empty_hint
    ), (
        "FAIL AC-13 [workspace-free]: preserve the kernel message and "
        f"creation hint, got message={empty_message!r}, hint={empty_hint!r}"
    )
    assert projection_reads == [], (
        "FAIL AC-13 [workspace-free]: resolution attempted projection-target "
        f"I/O before reporting no workspaces: {projection_reads!r}"
    )
    assert _root_snapshot(empty) == empty_before, (
        "FAIL AC-13 [workspace-free]: no projection target may be read or written"
    )


@pytest.mark.parametrize("second_slug", ["demo", "other"])
def test_review_synthesis_repeated_feature_flag_is_usage_before_target_io(
    second_slug: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    review: every repeated --feature is usage/2 before target access.
    """
    host = tmp_path / f"sy-i2-duplicate-{second_slug}"
    host.mkdir()
    shutil.copy2(TINY / ".heddle.yaml", host / ".heddle.yaml")
    monkeypatch.chdir(host)
    before = _root_snapshot(host)

    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        [
            "sync",
            "--feature",
            "demo",
            "--feature",
            second_slug,
            "--json",
        ],
    )

    assert code == 2 and envelope["error"]["code"] == "usage"
    assert "only once" in envelope["error"]["message"]
    assert _root_snapshot(host) == before, (
        "FAIL SY-I2: repeated --feature attempted workspace resolution or "
        "changed the workspace-free root"
    )


def test_review_synthesis_workspace_and_state_feature_must_match(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    review: a resolved workspace cannot project another state identity.
    """
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="sy-i3-identity-mismatch",
    )
    state = host / "plans" / "demo" / "state.yaml"
    state_text = state.read_text(encoding="utf-8")
    mismatched = state_text.replace(
        "\nfeature: demo\n", "\nfeature: impostor\n", 1
    ).replace(
        "\n  path: .heddle/intake/demo.yaml\n",
        "\n  path: .heddle/intake/impostor.yaml\n",
        1,
    )
    assert mismatched != state_text, "test setup: state feature line must change"
    state.write_text(mismatched, encoding="utf-8")
    before = _root_snapshot(host)
    projection_reads: list[str] = []
    real_path_open = Path.open
    real_builtin_open = builtins.open
    real_os_open = os.open

    def guarded_path_open(self: Path, *args, **kwargs):
        if self.name in {"plan.md", "AGENTS.md"}:
            projection_reads.append(str(self))
            raise AssertionError(f"identity mismatch read projection target {self}")
        return real_path_open(self, *args, **kwargs)

    def guarded_builtin_open(file, *args, **kwargs):
        if isinstance(file, str | os.PathLike):
            candidate = Path(file)
            if candidate.name in {"plan.md", "AGENTS.md"}:
                projection_reads.append(str(candidate))
                raise AssertionError(
                    f"identity mismatch read projection target {candidate}"
                )
        return real_builtin_open(file, *args, **kwargs)

    def guarded_os_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        candidate = Path(path) if not isinstance(path, bytes) else None
        if candidate is not None and candidate.name in {"plan.md", "AGENTS.md"}:
            projection_reads.append(str(candidate))
            raise AssertionError(
                f"identity mismatch read projection target {candidate}"
            )
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_path_open)
        patch.setattr(builtins, "open", guarded_builtin_open)
        patch.setattr(os, "open", guarded_os_open)
        code, envelope = _json_run(
            run_cli,
            envelope_tools,
            ["sync", "--feature", "demo", "--json"],
        )

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
    detail = _error_blob(envelope)
    assert "identity mismatch" in detail
    assert "impostor" in detail and "demo" in detail
    assert projection_reads == [], (
        "FAIL SY-I3: identity mismatch reached projection-target reads: "
        f"{projection_reads!r}"
    )
    assert _root_snapshot(host) == before, (
        "FAIL SY-I3: identity mismatch must refuse before either target changes"
    )


def test_ac14_red_manifest_landing_and_write_seam_locks_flip_together(
    run_cli,
    envelope_tools,
) -> None:
    """AC-14 discriminator: exact sync row, current count, and one write seam."""
    from heddle.runtime.contracts import COMMAND_SURFACE, build_manifest

    rows = {contract.name: contract for contract in COMMAND_SURFACE}
    assert "sync" in rows, (
        "FAIL AC-14: COMMAND_SURFACE must gain the sync-ratified sync row"
    )
    row = rows["sync"]
    surface_names = [contract.name for contract in COMMAND_SURFACE]
    sync_position = surface_names.index("sync")
    assert surface_names[sync_position - 1 : sync_position + 2] == [
        "session log",
        "sync",
        "decisions list",
    ], (
        "FAIL AC-14: sync must land in the sync command-table position between "
        f"session log and decisions list, got {surface_names!r}"
    )
    assert row.mutating is True and row.dry_run is True and row.args == (), (
        "FAIL AC-14: sync is mutating, advertises dry-run, and has no positional"
    )
    assert [flag.name for flag in row.flags] == [
        "--json",
        "--feature",
        "--dry-run",
    ], f"FAIL AC-14: exact sync flag order drifted: {row.flags!r}"
    assert row.exit_codes == (0, 1, 2, 3, 5), (
        f"FAIL AC-14: sync exit set must be (0,1,2,3,5), got {row.exit_codes!r}"
    )
    assert row.output_schema == "heddle.sync/v0"
    assert WRITE_SEAMS.get("sync.py") == {"install_projection"}, (
        "FAIL AC-14: sync.py must add exactly one sanctioned write seam named "
        "install_projection; update policy_helpers and its literal policy lock"
    )
    assert_runtime_write_seams(REPO_ROOT / "heddle" / "runtime")

    code, served = _json_run(run_cli, envelope_tools, ["help", "--json"])
    assert code == 0, "FAIL AC-14: help --json must remain successful"
    manifest = build_manifest()
    assert served["data"] == manifest, (
        "FAIL AC-14: help --json must serve the central contract manifest"
    )
    manifest_names = [entry["name"] for entry in manifest["commands"]]
    manifest_position = manifest_names.index("sync")
    assert manifest_names[manifest_position - 1 : manifest_position + 2] == [
        "session log",
        "sync",
        "decisions list",
    ], (
        "FAIL AC-14: serialized command order must match the sync table, got "
        f"{manifest_names!r}"
    )
    entries = {entry["name"]: entry for entry in manifest["commands"]}
    assert entries["init"]["output_schema"] == "heddle.init/v0"
    assert entries["sync"] == {
        "name": "sync",
        "summary": "refresh the generated managed blocks",
        "mutating": True,
        "dry_run": True,
        "args": [],
        "flags": [
            {
                "name": "--json",
                "summary": "emit the result envelope as JSON on stdout",
            },
            {
                "name": "--feature",
                "summary": "target feature slug (overrides feature resolution)",
            },
            {
                "name": "--dry-run",
                "summary": "preview the mutation without writing",
            },
        ],
        "exit_codes": [0, 1, 2, 3, 5],
        "output_schema": "heddle.sync/v0",
        # input schema: the additive input_schema field — null until a payload body
        # is pinned for sync (it takes no structured input today).
        "input_schema": None,
    }, f"FAIL AC-14: served sync row drifted: {entries['sync']!r}"


def test_ac16_red_markerless_override_is_faithful_until_sync_refuses(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    AC-16 red discriminator: resource precedence remains faithful; only sync polices.
    """
    host = copy_host(tmp_path, TINY, name="ac16-override")
    adopt_fixture_host(host)
    agents_before = (host / "AGENTS.md").read_bytes()
    shutil.rmtree(host / "plans" / "sample-feature")
    shutil.rmtree(host / "docs" / "features" / "example")
    override = host / ".heddle" / "overrides" / "plan.scaffold.md"
    override.parent.mkdir(parents=True)
    template = (
        "# Custom plan: {{slug}}\n\n"
        "## Quick Orientation\n\n"
        "Host-owned markerless text for {{slug}}.\n"
    )
    override.write_text(template, encoding="utf-8")
    monkeypatch.chdir(host)

    start_code, start = _admit_feature(
        run_cli,
        envelope_tools,
        tmp_path,
        slug="custom",
    )
    assert start_code == 0, (
        f"FAIL AC-16: feature start must honor markerless override, got "
        f"{start_code}: {start.get('error')!r}"
    )
    assert start["diagnostics"] == [], (
        "FAIL AC-16: clean markerless feature start must add no marker-policing "
        f"diagnostic, got {start['diagnostics']!r}"
    )
    plan = host / "plans" / "custom" / "plan.md"
    expected = template.replace("{{slug}}", "custom")
    assert plan.read_bytes() == expected.encode("utf-8"), (
        "FAIL AC-16: feature start must render the override byte-faithfully"
    )

    validate_code, validate = _json_run(run_cli, envelope_tools, ["validate", "--json"])
    doctor_code, doctor = _json_run(
        run_cli,
        envelope_tools,
        ["doctor", "--feature", "custom", "--json"],
    )
    markerless_plan = plan.read_bytes()
    _force_marker_ready(plan)
    control_validate_code, control_validate = _json_run(
        run_cli, envelope_tools, ["validate", "--json"]
    )
    control_doctor_code, control_doctor = _json_run(
        run_cli,
        envelope_tools,
        ["doctor", "--feature", "custom", "--json"],
    )
    plan.write_bytes(markerless_plan)
    assert validate_code in {0, 4} and doctor_code in {0, 4}, (
        "FAIL AC-16: validate/doctor must remain ordinary diagnostic reads"
    )
    assert (validate_code, doctor_code) == (
        control_validate_code,
        control_doctor_code,
    ), (
        "FAIL AC-16: markerlessness alone changed validate/doctor exit status; "
        f"markerless={(validate_code, doctor_code)!r}, "
        f"control={(control_validate_code, control_doctor_code)!r}"
    )
    assert (
        validate["diagnostics"],
        doctor["diagnostics"],
    ) == (
        control_validate["diagnostics"],
        control_doctor["diagnostics"],
    ), (
        "FAIL AC-16: markerless and marker-ready controls must have identical "
        "validate/doctor diagnostics; only sync may police the pair"
    )
    before = plan.read_bytes()

    sync_code, refusal = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "custom", "--json"],
    )
    assert sync_code == 3 and refusal["error"]["code"] == "workspace-invalid", (
        f"FAIL AC-16: only sync should refuse markerless plan at exit 3, got "
        f"{sync_code}: {refusal.get('error')!r}"
    )
    refusal_message = refusal["error"]["message"].lower()
    refusal_hint = refusal["error"]["hint"].lower()
    assert "plans/custom/plan.md" in refusal_message, (
        "FAIL AC-16: error.message must name the markerless plan target, got "
        f"{refusal_message!r}"
    )
    assert PLAN_BEGIN.lower() in refusal_hint and PLAN_END.lower() in refusal_hint, (
        f"FAIL AC-16: error.hint must name exact marker lines, got {refusal_hint!r}"
    )
    assert (
        plan.read_bytes() == before
        and (host / "AGENTS.md").read_bytes() == agents_before
    ), "FAIL AC-16: markerless override refusal must write neither target"


def test_self_review_markerless_override_emits_no_managed_marker_diagnostics(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-16: validate and doctor never police managed plan markers."""
    host = copy_host(tmp_path, TINY, name="ac16-marker-diagnostic-absence")
    adopt_fixture_host(host)
    shutil.rmtree(host / "plans" / "sample-feature")
    shutil.rmtree(host / "docs" / "features" / "example")
    override = host / ".heddle" / "overrides" / "plan.scaffold.md"
    override.parent.mkdir(parents=True)
    override.write_text(
        "# Custom plan: {{slug}}\n\n"
        "## Quick Orientation\n\n"
        "Host-owned markerless text for {{slug}}.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(host)

    start_code, start = _admit_feature(
        run_cli,
        envelope_tools,
        tmp_path,
        slug="custom",
    )
    assert start_code == 0, (
        "test setup: markerless override must create a workspace, got "
        f"{start_code}: {start.get('error')!r}"
    )
    plan_text = (host / "plans" / "custom" / "plan.md").read_text(encoding="utf-8")
    assert PLAN_BEGIN not in plan_text and PLAN_END not in plan_text, (
        "test setup: the custom override must remain markerless"
    )

    validate_code, validate = _json_run(run_cli, envelope_tools, ["validate", "--json"])
    doctor_code, doctor = _json_run(
        run_cli,
        envelope_tools,
        ["doctor", "--feature", "custom", "--json"],
    )

    def is_managed_marker_diagnostic(diagnostic: dict[str, Any]) -> bool:
        code = str(diagnostic.get("code", "")).casefold().replace("_", "-")
        payload = json.dumps(diagnostic, sort_keys=True).casefold()
        marker_code_terms = (
            "marker",
            "managed-block",
            "managed-region",
            PLAN_ID,
        )
        if any(term in code for term in marker_code_terms):
            return True

        exact_markers = (PLAN_BEGIN.casefold(), PLAN_END.casefold())
        if any(marker in payload for marker in exact_markers):
            return True

        plan_subjects = (
            "plans/custom/plan.md",
            "plan.md",
            "plan scaffold",
            "plan.scaffold",
            "plan projection",
            PLAN_ID,
            "plan status",
            "status block",
        )
        marker_language = re.search(
            r"\b(?:managed|marker|markers|delimiter|delimiters|projection)\b",
            payload,
        )
        boundary_fault = re.search(
            r"\b(?:missing|required|duplicate|reversed)\b", payload
        ) and re.search(r"\b(?:begin|end|boundary|boundaries)\b", payload)
        return any(subject in payload for subject in plan_subjects) and bool(
            marker_language or boundary_fault
        )

    assert is_managed_marker_diagnostic(
        {
            "code": "projection-invalid",
            "message": (
                "plans/custom/plan.md is missing required begin and end markers"
            ),
        }
    ), "test oracle: plan-target boundary wording must count as marker policing"

    for command, code, envelope in (
        ("validate", validate_code, validate),
        ("doctor", doctor_code, doctor),
    ):
        assert code in {0, 4}, (
            f"FAIL AC-16 [{command}]: markerlessness must remain an ordinary "
            f"diagnostic read, got exit {code}: {envelope!r}"
        )
        marker_diagnostics = [
            diagnostic
            for diagnostic in envelope["diagnostics"]
            if is_managed_marker_diagnostic(diagnostic)
        ]
        assert marker_diagnostics == [], (
            f"FAIL AC-16 [{command}]: markerless plan produced managed-marker "
            f"diagnostics: {marker_diagnostics!r}"
        )


@pytest.mark.parametrize(
    ("shape", "fault_terms"),
    [
        pytest.param("plan-absent", ("restore", "repair"), id="plan-absent"),
        pytest.param("plan-symlink", ("symlink",), id="plan-symlink"),
        pytest.param("plan-directory", ("regular",), id="plan-directory"),
        pytest.param("plan-invalid-utf8", ("utf-8",), id="plan-invalid-utf8"),
        pytest.param(
            "plan-unreadable",
            ("read", "readable", "unreadable"),
            id="plan-unreadable",
        ),
        pytest.param(
            "agents-invalid-utf8",
            ("utf-8",),
            id="agents-invalid-utf8",
        ),
    ],
)
def test_ac17_red_target_shape_family_refuses_with_zero_sibling_writes(
    shape: str,
    fault_terms: tuple[str, ...],
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """AC-17 red discriminator: the complete REQ-9 target family is loud."""
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name=f"ac17-{shape}",
    )
    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    expected_path = (
        "AGENTS.md" if shape == "agents-invalid-utf8" else ("plans/demo/plan.md")
    )
    if shape == "plan-absent":
        plan.unlink()
    elif shape == "plan-symlink":
        backing = plan.with_name("host-plan.md")
        plan.rename(backing)
        plan.symlink_to(backing.name)
    elif shape == "plan-directory":
        plan.unlink()
        plan.mkdir()
    elif shape == "plan-invalid-utf8":
        plan.write_bytes(b"\xff\xfeinvalid plan")
    elif shape == "plan-unreadable":
        plan.chmod(0)

        def restore_plan_mode() -> None:
            if plan.exists() and not plan.is_symlink():
                plan.chmod(0o644)

        request.addfinalizer(restore_plan_mode)
    else:
        agents.write_bytes(b"\xff\xfeinvalid agents")
    before = _root_snapshot(host)

    if shape == "plan-unreadable":
        real_path_open = Path.open
        real_builtin_open = builtins.open
        real_os_open = os.open

        def deny_path_open(self, *args, **kwargs):
            if Path(self).absolute() == plan.absolute():
                raise PermissionError("injected unreadable plan target")
            return real_path_open(self, *args, **kwargs)

        def deny_builtin_open(file, *args, **kwargs):
            if isinstance(file, str | os.PathLike) and Path(file).absolute() == (
                plan.absolute()
            ):
                raise PermissionError("injected unreadable plan target")
            return real_builtin_open(file, *args, **kwargs)

        def deny_os_open(
            path: Any,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            candidate = Path(path) if not isinstance(path, bytes) else None
            if candidate is not None and candidate.absolute() == plan.absolute():
                raise PermissionError("injected unreadable plan target")
            return real_os_open(path, flags, mode, dir_fd=dir_fd)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "open", deny_path_open)
            patch.setattr(builtins, "open", deny_builtin_open)
            patch.setattr(os, "open", deny_os_open)
            code, envelope = _json_run(
                run_cli,
                envelope_tools,
                ["sync", "--feature", "demo", "--json"],
            )
    else:
        code, envelope = _json_run(
            run_cli,
            envelope_tools,
            ["sync", "--feature", "demo", "--json"],
        )

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        f"FAIL AC-17 [{shape}]: target-shape fault must exit 3, got {code}: "
        f"{envelope.get('error')!r}"
    )
    message = envelope["error"]["message"].lower()
    hint = envelope["error"]["hint"].lower()
    assert expected_path.lower() in message, (
        f"FAIL AC-17 [{shape}]: error.message must name {expected_path}, "
        f"got {message!r}"
    )
    assert hint and any(
        _has_term(message + " " + hint, term) for term in fault_terms
    ), (
        f"FAIL AC-17 [{shape}]: refusal needs shape-specific remediation "
        f"containing one of {fault_terms!r}, got message={message!r}, hint={hint!r}"
    )
    assert _root_snapshot(host) == before, (
        f"FAIL AC-17 [{shape}]: target or sibling changed despite preflight refusal"
    )


@pytest.mark.parametrize("failed_target", ["plan", "agents"])
def test_ac18_red_positioned_install_failures_are_loud_and_retryable(
    failed_target: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-18 red discriminator: first/second install faults retain position."""
    sync = _sync_module()
    host = _marker_ready_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name=f"ac18-{failed_target}",
    )
    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    root_before = _root_snapshot(host)
    plan_before = plan.read_bytes()
    real_install = sync.install_projection

    def injected_failure(path: Path, text: str, *, create: bool) -> None:
        is_failed = (failed_target == "plan" and path == plan) or (
            failed_target == "agents" and path == agents
        )
        if is_failed:
            raise KernelError(
                code="workspace-invalid",
                message=f"projection target is unwritable: {path}",
                hint="repair the target filesystem and retry heddle sync",
            )
        real_install(path, text, create=create)

    monkeypatch.setattr(sync, "install_projection", injected_failure)
    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )
    expected_path = "plans/demo/plan.md" if failed_target == "plan" else ("AGENTS.md")
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        f"FAIL AC-18 [{failed_target}]: positioned I/O failure must exit 3, "
        f"got {code}: {envelope.get('error')!r}"
    )
    assert expected_path.lower() in _error_blob(envelope), (
        f"FAIL AC-18 [{failed_target}]: error must name failed target"
    )
    assert not agents.exists(), (
        f"FAIL AC-18 [{failed_target}]: failed create must leave AGENTS.md absent"
    )
    if failed_target == "plan":
        assert _root_snapshot(host) == root_before, (
            "FAIL AC-18 [plan]: first-target failure must leave every file exact"
        )
        assert envelope["diagnostics"] == [], (
            "FAIL AC-18 [plan]: no completed target may be reported"
        )
    else:
        assert plan.read_bytes() != plan_before, (
            "FAIL AC-18 [agents]: plan must have completed before create failure"
        )
        assert envelope["diagnostics"] == [
            {
                "severity": "info",
                "code": "sync-partial",
                "message": "plans/demo/plan.md: updated",
            }
        ], (
            "FAIL AC-18 [agents]: completed plan action must ride the exact "
            f"sync-partial diagnostic, got {envelope['diagnostics']!r}"
        )

    monkeypatch.setattr(sync, "install_projection", real_install)
    retry_code, retry = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )
    expected_actions = (
        ("updated", "created", "created")
        if failed_target == "plan"
        else ("unchanged", "created", "created")
    )
    assert retry_code == 0 and retry["data"]["targets"] == _target_rows(
        *expected_actions
    ), (
        f"FAIL AC-18 [{failed_target}]: cleared retry must converge with "
        f"{expected_actions!r}, got exit {retry_code}: {retry!r}"
    )


def test_ac18_red_atomic_update_failure_preserves_winner_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-18 red discriminator: the update seam maps raw replace failure."""
    sync = _sync_module()
    target = tmp_path / "plan.md"
    target.write_bytes(b"host winner\n")
    before = _path_snapshot(target)
    children_before = {path.name for path in tmp_path.iterdir()}

    def fail_replace(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> None:
        assert Path(source).parent == target.parent, (
            "FAIL AC-18/REQ-16: update temp must be in the target directory"
        )
        assert Path(destination) == target, (
            "test setup: atomic update must replace the requested plan target"
        )
        raise OSError("injected os.replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(KernelError) as caught:
            sync.install_projection(target, "new managed projection\n", create=False)

    error = caught.value
    assert error.code == "workspace-invalid" and "plan.md" in error.message.lower(), (
        "FAIL AC-18/REQ-16: raw os.replace failure must become a typed "
        f"workspace-invalid naming the target, got {error!r}"
    )
    assert error.hint, "FAIL AC-18/REQ-16: write failure needs retry remediation"
    assert _path_snapshot(target) == before, (
        "FAIL AC-18/REQ-16: failed atomic update changed winner bytes or metadata"
    )
    assert {path.name for path in tmp_path.iterdir()} == children_before, (
        "FAIL AC-18/REQ-16: failed atomic update left temporary-file debris"
    )


def test_ac18_red_create_write_failure_removes_partial_and_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-18 red discriminator: raw create-write failure is typed and clean."""
    sync = _sync_module()
    target = tmp_path / "AGENTS.md"
    children_before = {path.name for path in tmp_path.iterdir()}
    real_write_text = Path.write_text
    real_link = os.link

    def fail_create_write(
        self: Path,
        content: str,
        *args: Any,
        **kwargs: Any,
    ) -> int:
        if self == target:
            raise OSError("injected create-path content write failure")
        return real_write_text(self, content, *args, **kwargs)

    def fail_exclusive_link(
        source: Any,
        destination: Any,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        candidate = Path(destination) if not isinstance(destination, bytes) else None
        if candidate is not None and candidate.absolute() == target.absolute():
            raise OSError("injected exclusive-create install failure")
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", fail_create_write)
        patch.setattr(os, "link", fail_exclusive_link)
        with pytest.raises(KernelError) as caught:
            sync.install_projection(target, CREATED_AGENTS, create=True)

    error = caught.value
    assert error.code == "workspace-invalid" and "agents.md" in (
        error.message.lower()
    ), (
        "FAIL AC-18/REQ-16: raw create write failure must become a typed "
        f"workspace-invalid naming AGENTS.md, got {error!r}"
    )
    assert error.hint, "FAIL AC-18/REQ-16: create failure needs remediation"
    assert not target.exists(), (
        "FAIL AC-18/REQ-16: create failure left an empty or partial AGENTS.md"
    )
    assert {path.name for path in tmp_path.iterdir()} == children_before, (
        "FAIL AC-18/REQ-16: create failure left temporary-file debris"
    )


def test_ac19_red_dual_refusals_aggregate_in_target_order_with_zero_writes(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-19 red discriminator: plan is primary, AGENTS fatal, no writes."""
    host = _created_demo_host(
        run_cli,
        envelope_tools,
        tmp_path,
        monkeypatch,
        name="ac19-dual-refusal",
    )
    plan = host / "plans" / "demo" / "plan.md"
    agents = host / "AGENTS.md"
    _replace_quick_orientation(plan, "unmarked plan host prose")
    agents.write_text("unmarked AGENTS host prose\n", encoding="utf-8")
    plan_before = plan.read_bytes()
    agents_before = agents.read_bytes()

    code, envelope = _json_run(
        run_cli,
        envelope_tools,
        ["sync", "--feature", "demo", "--json"],
    )

    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        f"FAIL AC-19: dual refusal must exit 3, got {code}: {envelope.get('error')!r}"
    )
    primary = json.dumps(envelope["error"], sort_keys=True).lower()
    assert "plans/demo/plan.md" in primary and PLAN_BEGIN.lower() in primary, (
        f"FAIL AC-19: plan-first envelope error needs exact remediation: {primary}"
    )
    fatal_agents = [
        item
        for item in envelope["diagnostics"]
        if item.get("severity") == "fatal"
        and "agents.md" in item.get("message", "").lower()
    ]
    assert len(fatal_agents) == 1, (
        "FAIL AC-19: exactly one additional fatal refusal must name AGENTS.md, "
        f"got {envelope['diagnostics']!r}"
    )
    agents_refusal = fatal_agents[0]
    agents_detail = (
        agents_refusal.get("message", "") + " " + str(agents_refusal.get("hint", ""))
    ).lower()
    assert agents_refusal.get("code") == "workspace-invalid" and (
        SESSION_BEGIN.lower() in agents_detail or _has_term(agents_detail, "move")
    ), (
        "FAIL AC-19: AGENTS fatal diagnostic must carry its marker/move-aside "
        f"refusal, got {agents_refusal!r}"
    )
    assert plan.read_bytes() == plan_before and agents.read_bytes() == agents_before, (
        "FAIL AC-19: aggregate preflight must write neither faulted target"
    )
