"""Pinned formatter and managed-region compatibility witness."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from heddle.contracts import operations as ops
from heddle.kernel.managed_regions import replace_managed_region
from heddle.kernel.model import derive_next_actions, resolve_snapshot
from heddle.kernel.project_config import KernelError, load_project_config
from heddle.runtime import application
from heddle.runtime.sync import render_plan_status, render_session_entry
from tests.tiering_review_helpers import V7_FEATURE, current_host

REPO = Path(__file__).resolve().parents[2]
PRETTIER_FIXTURE = REPO / "tests/fixtures/toolchains/prettier-3.9.6"


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_ac5_managed_bodies_have_formatter_stable_marker_boundaries(
    tmp_path, monkeypatch
) -> None:
    """AC-5 red: producer bodies retain the blank line Prettier requires."""
    host, _state_path = current_host(tmp_path, monkeypatch, stage="scaffold")
    snapshot = resolve_snapshot(load_project_config(host), V7_FEATURE)
    status = render_plan_status(snapshot, derive_next_actions(snapshot))
    session = render_session_entry()
    assert status.startswith("\n") and session.startswith("\n"), (
        "FAIL AC-5: managed bodies will churn at their opening markers"
    )
    assert status.endswith("\n\n"), (
        "FAIL AC-5: plan-status body will churn at its closing marker"
    )
    assert session.endswith("\n\n"), (
        "FAIL AC-5: session-entry body will churn at its closing marker"
    )


@pytest.mark.toolchain
def test_ac5_sync_is_a_fixed_point_after_offline_prettier(
    tmp_path, monkeypatch
) -> None:
    """AC-5 red: sync -> real formatter -> sync preserves formatted bytes."""
    toolchain = tmp_path / "toolchain"
    shutil.copytree(PRETTIER_FIXTURE, toolchain)
    cache = Path.home() / ".npm"
    env = os.environ.copy()
    env.update({"npm_config_cache": str(cache), "npm_config_offline": "true"})
    install = _run(
        ["npm", "ci", "--offline", "--ignore-scripts"], cwd=toolchain, env=env
    )
    assert install.returncode == 0, (
        "FAIL AC-5 prerequisite: locked Prettier is absent from the provisioned "
        f"offline cache\n{install.stderr}"
    )
    prettier = toolchain / "node_modules/prettier/bin/prettier.cjs"
    version = _run(["node", str(prettier), "--version"], cwd=toolchain, env=env)
    assert version.returncode == 0 and version.stdout.strip() == "3.9.6", (
        "FAIL AC-5 prerequisite: fixture did not resolve exact Prettier 3.9.6"
    )

    host_root = tmp_path / "host-root"
    host_root.mkdir()
    host, state_path = current_host(host_root, monkeypatch, stage="scaffold")
    plan = state_path.with_name("plan.md")
    plan.write_text(
        "# Synthetic plan\n\n"
        "<!-- heddle:begin plan-status -->\n"
        "old status\n"
        "<!-- heddle:end plan-status -->\n",
        encoding="utf-8",
    )
    first = application.execute(ops.Sync(feature=V7_FEATURE))
    assert first.ok, first.to_envelope()
    config = host / ".prettierrc.yaml"
    config.write_text(
        "printWidth: 88\n"
        "semi: false\n"
        "singleQuote: false\n"
        "tabWidth: 2\n"
        "bracketSpacing: false\n",
        encoding="utf-8",
    )
    (host / ".editorconfig").write_text(
        "root = true\n\n[*]\nend_of_line = lf\ninsert_final_newline = true\n",
        encoding="utf-8",
    )
    (host / ".prettierignore").write_text("", encoding="utf-8")
    targets = [plan, host / "AGENTS.md", host / "CLAUDE.md"]
    formatted = _run(
        [
            "node",
            str(prettier),
            "--write",
            "--ignore-unknown",
            *(str(path) for path in targets),
        ],
        cwd=host,
        env=env,
    )
    assert formatted.returncode == 0, f"FAIL AC-5 formatter: {formatted.stderr}"
    before_second_sync = {path: path.read_bytes() for path in targets}

    second = application.execute(ops.Sync(feature=V7_FEATURE))

    assert second.ok, second.to_envelope()
    assert {path: path.read_bytes() for path in targets} == before_second_sync, (
        "FAIL AC-5: second sync changed the pinned formatter's output"
    )


def test_ac5_survivor_replacement_preserves_crlf_bytes_outside_markers() -> None:
    """AC-5 survivor: managed replacement changes only the bounded body."""
    before = (
        "prefix  \r\n"
        "<!-- heddle:begin sample -->\r\n"
        "old\r\n"
        "<!-- heddle:end sample -->\r\n"
        "suffix  \r\n"
    )
    after = replace_managed_region(before, "sample", "new\n")
    assert after.startswith("prefix  \r\n<!-- heddle:begin sample -->\r\n")
    assert after.endswith("<!-- heddle:end sample -->\r\nsuffix  \r\n")


def test_ac5_survivor_malformed_markers_still_fail_loudly() -> None:
    """AC-5 survivor: formatter compatibility does not weaken marker refusal."""
    with pytest.raises(KernelError, match="BEGIN marker but no END"):
        replace_managed_region(
            "<!-- heddle:begin sample -->\nunterminated\n", "sample", "new\n"
        )
