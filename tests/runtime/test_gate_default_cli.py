"""
R4/review gate->CLI config: per-gate default lanes (owner ruling 2026-07-22).

The implementation-stage milestone gates (code-quality, self-review) default to the
Codex
lane, matching the legacy harness's reviewer vendor for the adoption comparative
runs. Their Codex lane runs gpt-6-sol at xhigh effort (owner rulings
2026-09-09 and 2026-09-22); every other Codex lane stays on gpt-6-astra at high (owner
ruling 2026-09-05). Every other gate keeps the Claude default. An explicit ``--cli``
always wins; a registry default must be a member of the gate's
``supported_clis`` (fail-loud membership check covers both paths).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from heddle.gate.registry import GATES
from tests.runtime.git_helpers import initialize_git_repo

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"

_CODEX_DEFAULT_GATES = {"code-quality", "self-review"}
_OPUS_SYNTHESIS_GATES = {
    "review-synthesis",
    "plan-review-synthesis",
    "review-test-scaffolding-synthesis",
}
_FABLE_SYNTHESIS_GATES = {
    "spec-review-synthesis",
}


def test_registry_default_cli_is_always_supported() -> None:
    # The run-gate membership check fails loud on a default outside
    # supported_clis; the registry must never ship that state.
    for name, gate_type in GATES.items():
        assert gate_type.default_cli in gate_type.supported_clis, (
            f"FAIL: {name} default_cli {gate_type.default_cli!r} is not in "
            f"supported_clis {gate_type.supported_clis}"
        )


def _read_state(host: Path) -> dict:
    state_path = host / "plans" / "nl-screening" / "state.yaml"
    return yaml.safe_load(state_path.read_text(encoding="utf-8"))


def _run_fact_clis(host: Path, gate: str) -> list[str]:
    return [
        run["cli"]
        for fact in _read_state(host).get("gates", [])
        if fact["gate"] == gate
        for run in fact["runs"]
    ]


def _prepare_milestone_diff(host: Path) -> None:
    def run_git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=host,
            check=True,
            capture_output=True,
        )

    initialize_git_repo(
        run_git,
        email="gate-tests@example.invalid",
        name="Gate Tests",
    )
    reviewed = host / "src/example/screening/validate/rule.py"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("RULE = 'changed'\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", reviewed.relative_to(host).as_posix()],
        cwd=host,
        check=True,
        capture_output=True,
    )
