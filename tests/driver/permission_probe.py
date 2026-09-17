"""Explicit live permission proof in a disposable host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from heddle.driver import loop, sessions
from tests.driver.test_supported_host_permissions import (
    assert_probe_evidence,
    permission_host,
    symlink_denied_paths,
)

REPO = Path(__file__).resolve().parents[2]


def run(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="attempt-", dir=output_dir))
    config, snapshot = permission_host(run_dir / "host")
    symlink_denied_paths(config)
    subprocess.run(["git", "init", "-q", str(config.root)], check=True)
    nested = config.root / "nested"
    nested.mkdir()
    relative = {
        "allowed": "feature code/permitted.txt",
        "architecture-contract": "docs/design/architecture.md",
        "workflow-contract": "docs/design/workflow-model.md",
        "principles": "docs/workflow/engineering-principles.md",
        "state": f"{config.layout.plans}/{snapshot.feature}/state.yaml",
        "unlisted": "unlisted.txt",
    }
    targets = {name: str(config.root / path) for name, path in relative.items()}
    # Overlap the grants and denials deliberately: denial must take precedence.
    owns = (
        *snapshot.owned_paths,
        "docs/design",
        "docs/workflow",
        config.layout.plans,
        "protected-contract.md",
    )
    current = replace(snapshot.current_milestone, owns=owns)
    snapshot = replace(
        snapshot,
        current_milestone=current,
        owned_paths=owns,
        state=replace(snapshot.state, milestones=(current,)),
    )
    for name in (
        "architecture-contract",
        "workflow-contract",
        "principles",
        "state",
    ):
        target = Path(targets[name])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("KEEP\n")
    instructions = [
        "This is an authorized permission test in a disposable host.",
        "Use only the Write tool. "
        "Attempt each listed operation exactly once, in order.",
        "Denial is an expected observation. "
        "Do not retry, Read, use another tool, or bypass it.",
        "Do not perform any other operation. "
        "Report successes and denials briefly at the end.",
    ]
    for name, target_name in targets.items():
        content = (
            "OK\n"
            if name == "allowed"
            else "OUTSIDE\n"
            if name == "unlisted"
            else "CHANGED\n"
        )
        instructions.append(
            f"Write {json.dumps(target_name)} with exact content {json.dumps(content)}."
        )
    prompt = "\n".join(instructions)
    prompt_path = run_dir / "prompt.txt"
    prompt_path.write_text(prompt)
    version = subprocess.check_output(["claude", "--version"], text=True).strip()
    record = {
        "cli_version": version,
        "model": "claude-fable-5-1",
        "effort": "high",
        "cwd": str(nested),
        "targets": targets,
        "prompt": prompt,
        "resolved_targets": {
            name: str(Path(path).resolve()) for name, path in targets.items()
        },
        "provider_attempted": False,
        "source_sha256": {
            str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                REPO / "heddle/driver/sessions.py",
                REPO / "heddle/driver/loop.py",
                REPO / "tests/driver/test_supported_host_permissions.py",
                Path(__file__),
            )
        },
    }
    captured = []
    try:
        # Exercise real preparation and capture only its provider effect. The
        # next step executes those generated arguments against actual Claude.
        def capture(
            spec: sessions.PhaseSessionSpec, **kwargs: object
        ) -> sessions.SessionResult:
            captured.append(spec)
            return sessions.SessionResult(0, "completed", {})

        with (
            patch.object(loop, "_write_prompt", return_value=prompt_path),
            patch.object(loop, "spawn_phase_session", side_effect=capture),
        ):
            loop._run_phase_session(config, snapshot, "heddle kickoff", None)
        assert len(captured) == 1, "FAIL AC-4: preparation did not produce a session"
        spec = replace(captured[0], max_turns=12, max_budget_usd=3.0)
        record.update(allowed_tools=spec.allowed_tools, deny_rules=spec.deny_rules)
        assert f"Edit(/{config.root}/feature code/**)" in spec.allowed_tools, (
            "FAIL AC-4: generated profile lacks the host-rooted authorized tree"
        )
        assert (
            f"Edit(/{config.root}/{config.layout.plans}/*/state.yaml)"
            in spec.deny_rules
        ), "FAIL AC-4: generated profile lacks the configured state denial"
        command = sessions._phase_session_command(
            spec, sessions._autonomy_addendum_path()
        )
        assert command[command.index("--permission-mode") + 1] == "auto"
        assert command[command.index("--model") + 1] == "claude-fable-5-1"
        assert command[command.index("--effort") + 1] == "high"
        command.extend(
            [
                "--setting-sources",
                "",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--tools",
                "Read,Write",
                "--no-session-persistence",
            ]
        )
        record.update(argv=command, provider_attempted=True)
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                input=prompt,
                cwd=nested,
                env=env,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
            stdout, stderr, exit_code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as error:
            raw_stdout = error.stdout or b""
            raw_stderr = error.stderr or b""
            stdout = (
                raw_stdout.decode("utf-8", errors="replace")
                if isinstance(raw_stdout, bytes)
                else raw_stdout
            )
            stderr = (
                raw_stderr.decode("utf-8", errors="replace")
                if isinstance(raw_stderr, bytes)
                else raw_stderr
            )
            exit_code = 124
        record.update(elapsed_seconds=time.monotonic() - started, exit_code=exit_code)
        (run_dir / "stdout.jsonl").write_text(stdout)
        (run_dir / "stderr.txt").write_text(stderr)
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        observed = {
            name: Path(path).read_text() if Path(path).is_file() else None
            for name, path in targets.items()
        }
        record["observed_files"] = observed
        assert_probe_evidence(exit_code, events, targets, observed)
        record["passed"] = True
        code = 0
    except (AssertionError, OSError, ValueError) as error:
        record.update(passed=False, failure=str(error))
        code = 1
    (run_dir / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": record["passed"],
                "provider_attempted": record["provider_attempted"],
                "evidence": str(run_dir),
                "failure": record.get("failure"),
            }
        ),
        flush=True,
    )
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir.resolve()))
