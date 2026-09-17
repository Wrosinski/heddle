"""Shared helpers for authoring-surface tests."""

from __future__ import annotations

import os
import re
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SCAFFOLD_ASSETS = (
    "feature-spec.scaffold.md",
    "plan.scaffold.md",
    "brief.scaffold.md",
)

TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")

# AC-8 retier payloads carry exactly the R-P3-2 keys.
RETIER_PAYLOAD_KEYS = {
    "feature",
    "tier",
    "authorized_through",
    "tier_change",
    "authorization",
    "revision",
}

# AC-11/AC-12 milestone payloads carry exactly the R-P3-2 keys; the
# milestone object itself carries exactly the nine skeleton keys.
MILESTONE_PAYLOAD_KEYS = {"feature", "milestone", "revision"}
SKELETON_KEYS = {
    "id",
    "title",
    "satisfies",
    "depends_on",
    "owns",
    "complexity",
    "estimated_hours",
    "verification",
    "status",
}


def tree_snapshot(root: Path) -> list[str]:
    """Sorted relative listing of every file/dir — the untouched-filesystem
    oracle for usage-exit and dry-run assertions."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def ratify_principles(host: Path) -> None:
    """AC-9 precondition: the auto-flow principles blocker clears only when
    docs/workflow/engineering-principles.md carries status: ratified."""
    principles = host / "docs" / "workflow" / "engineering-principles.md"
    principles.parent.mkdir(parents=True, exist_ok=True)
    principles.write_text(
        "---\nstatus: ratified\n---\n\n# Engineering Principles\n",
        encoding="utf-8",
    )


def install_fake_claude(tmp_path: Path, monkeypatch) -> Path:
    """The autopilot fake-session harness: a scripted `claude` on PATH backed by
    tests/driver/fake_claude_runtime.py (the AC-24 acceptance pattern) — no
    paid session is ever spawned."""
    from tests.driver.helpers import write_executable_script

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "cli-invocations.jsonl"
    write_executable_script(
        bin_dir / "claude",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tests.driver.fake_claude_runtime import main

            raise SystemExit(main("claude", {str(log)!r}))
            """
        ),
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return bin_dir


def prepare_auto_tier1_drive(host, feature, run_cli):
    """Configure the real verification prerequisite on a natively born feature."""
    import json

    import yaml

    config_path = host / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["autopilot"] = {"test_command": "python3 -c 'print(1)'"}
    config_path.write_text(yaml.safe_dump(config))
    source = host / f"{feature}.py"
    source.write_text("VALUE = 7\n")
    inputs = host / "inputs.json"
    inputs.write_text(json.dumps([source.name]))
    for args in (
        [
            "feature",
            "inputs",
            "set",
            "--from-file",
            str(inputs),
            "--feature",
            feature,
            "--json",
        ],
        [
            "commands",
            "set",
            "test_command",
            "--command",
            "python3 -c 'print(1)'",
            "--feature",
            feature,
            "--json",
        ],
    ):
        code, out, _err = run_cli(args)
        assert code == 0, out
