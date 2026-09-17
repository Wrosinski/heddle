"""Runtime for the driver test fake ``claude`` executable."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import yaml

from tests.driver.policy_resolution_fixture import policy_resolution_payload


def main(name: str, log_path: str) -> int:
    argv = sys.argv[1:]
    if name == "claude" and argv == ["--help"]:
        sys.stdout.write(
            "usage: claude --print --output-format stream-json "
            "--verbose --permission-mode auto --model --effort "
            "--append-system-prompt "
            "--allowedTools --disallowedTools --max-turns "
            "--max-budget-usd\n"
        )
        return 0

    try:
        stdin = "" if sys.stdin.isatty() else sys.stdin.read()
    except Exception:
        stdin = ""
    _append_invocation_log(Path(log_path), name=name, argv=argv, stdin=stdin)
    result: str | dict[str, Any] = "ok"
    if os.environ.get("HEDDLE_FAKE_CLAUDE_AUTOPILOT_COMPLETE") == "1":
        payload = _run_content_aware_side_effects(stdin)
        if payload:
            result = json.dumps(payload)
    sys.stdout.write(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": result,
            }
        )
        + "\n"
    )
    return 0


def _append_invocation_log(
    log_path: Path, *, name: str, argv: list[str], stdin: str
) -> None:
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"bin": name, "argv": argv, "stdin": stdin}) + "\n")


def _run_content_aware_side_effects(stdin: str) -> dict[str, Any]:
    fields = _prompt_fields(stdin)
    stage = fields.get("stage")
    feature = fields.get("feature")
    if stage == "implement" and feature:
        _complete_implement_stage(feature)
    if stage == "robustness" and feature:
        from heddle.cli import main as heddle_main

        with tempfile.TemporaryDirectory(prefix="fake-policy-") as directory:
            path = Path(directory) / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "heddle.policy-batch/v1",
                        "resolutions": [
                            policy_resolution_payload(
                                routes_to=f"plans/{feature}/plan.md"
                            )
                        ],
                    }
                )
            )
            with redirect_stdout(io.StringIO()) as output:
                code = heddle_main(
                    [
                        "decisions",
                        "record-policy",
                        "--feature",
                        feature,
                        "--from-file",
                        str(path),
                        "--json",
                    ]
                )
            assert code == 0, output.getvalue()
    return {}


def _prompt_fields(stdin: str) -> dict[str, str]:
    fields = {}
    for line in stdin.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def _complete_implement_stage(feature: str) -> None:
    state_path = Path("plans") / feature / "state.yaml"
    if not state_path.is_file():
        return
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    now = "2026-06-16T09:00Z"
    milestones = state.get("milestones", [])
    for milestone in milestones:
        milestone["status"] = "done"
        for task in milestone.get("tasks", []):
            task["status"] = "done"

    _append_missing_verifications(state_path, state, milestones, now)
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated"] = now
    state_path.write_text(
        yaml.safe_dump(
            state,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )


def _append_missing_verifications(
    state_path: Path, state: dict[str, Any], milestones: list[dict[str, Any]], now: str
) -> None:
    from tests.runtime.write_path_helpers import authored_milestone_verification

    verified = {
        fact.get("scope")
        for fact in state.setdefault("verifications", [])
        if fact.get("exit_code") == 0
    }
    for milestone in milestones:
        mid = milestone.get("id")
        if mid and mid not in verified:
            state["verifications"].append(
                authored_milestone_verification(state_path.resolve(), milestone, at=now)
            )
