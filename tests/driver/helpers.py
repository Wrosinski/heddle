"""Shared helpers for autopilot driver tests."""

from __future__ import annotations

import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from heddle.kernel.model import FeatureSnapshot, resolve_snapshot
from heddle.kernel.project_config import ProjectConfig, load_project_config
from tests.runtime.write_path_helpers import read_yaml, write_yaml


def arm_at_stage(driver_corpus, host: Path, slug: str, stage: str, **overrides) -> Path:
    values = {"stage": stage, "authorized_through": stage, **overrides}
    return driver_corpus.arm(host, slug, **values)


def make_first_milestone_current_and_taskless(state: dict) -> None:
    """Arm the session-projecting implement state: a CURRENT milestone with
    no tasks derives `heddle task add` → the fresh per-milestone phase
    session. Since E1-2 (2026-07-07) an all-todo milestone list derives the
    driver-owned promote `milestone advance` instead of a kickoff session,
    so tests that need the driver to SPAWN a session at implement must arm
    this state, not the fixture's all-todo default."""
    state["milestones"][0].update(status="current", tasks=[])


def has_grant(
    grants: list[dict],
    *,
    source: str | None = None,
    through: str | None = None,
) -> bool:
    return any(
        (source is None or grant.get("source") == source)
        and (through is None or grant.get("through") == through)
        for grant in grants
    )


def assert_has_grant(
    grants: list[dict],
    *,
    message: str,
    source: str | None = None,
    through: str | None = None,
) -> None:
    assert has_grant(grants, source=source, through=through), message


def assert_no_grant(
    grants: list[dict],
    *,
    message: str,
    source: str | None = None,
    through: str | None = None,
) -> None:
    assert not has_grant(grants, source=source, through=through), message


def append_autopilot_notify_command(host: Path, command: str) -> None:
    set_autopilot_config(host, notify_command=command)


def set_autopilot_notify_timeout(host: Path, timeout_s: int) -> None:
    set_autopilot_config(host, notify_timeout_s=timeout_s)


def set_autopilot_config(host: Path, **values) -> None:
    def mutate(config: dict) -> None:
        config.setdefault("autopilot", {}).update(values)

    mutate_heddle_config(host, mutate)


def remove_autopilot_config(host: Path) -> None:
    mutate_heddle_config(host, lambda config: config.pop("autopilot", None))


def mutate_heddle_config(host: Path, mutator: Callable[[dict], None]) -> None:
    config_path = host / ".heddle.yaml"
    config = read_yaml(config_path)
    mutator(config)
    write_yaml(config_path, config)


def initialize_fixture_git(host: Path) -> None:
    """Give a fresh current fixture a real birth for completion provenance."""
    import subprocess

    if (host / ".git").exists():
        return
    for arguments in (
        ("init", "-q"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Heddle Test"),
        ("add", "."),
        ("commit", "-qm", "current fixture birth"),
    ):
        subprocess.run(["git", *arguments], cwd=host, check=True)


def resolve_decision(
    run_cli: Callable[[list[str]], tuple[int, str, str]],
    decision_id: str,
    *,
    resolution: str,
    routes_to: str,
    kind: str = "disposition",
) -> tuple[int, str, str]:
    return run_cli(
        [
            "decisions",
            "resolve",
            decision_id,
            "--kind",
            kind,
            "--resolution",
            resolution,
            "--routes-to",
            routes_to,
            "--json",
        ]
    )


def write_executable_script(path: Path, text: str, *, mode: int | None = None) -> Path:
    path.write_text(text, encoding="utf-8")
    if mode is None:
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
    else:
        path.chmod(mode)
    return path


def phase_permission_inputs(
    host: Path, slug: str, stage: str
) -> tuple[ProjectConfig, FeatureSnapshot]:
    """Resolve fixture facts, including the completion unknown-stage test case."""
    config = load_project_config(host)
    return config, replace(resolve_snapshot(config, slug), stage=stage)
