"""Hash-keyed gate cache and idempotent resume behavior."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.runtime.git_helpers import initialize_git_repo
from tests.structured_review_helpers import scripted_review

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"


def _run_gate_landed() -> bool:
    from heddle.runtime.contracts import COMMAND_SURFACE

    run_gate = next((c for c in COMMAND_SURFACE if c.name == "run-gate"), None)
    return run_gate is not None and any(f.name == "--json" for f in run_gate.flags)


# Feature landed: a regression that removed the integration surface must FAIL here, not
# silently skip the module (review integration; plan write T5 — retire red-phase
# skip sentinels to hard asserts at completion).
assert _run_gate_landed(), (
    "run-gate must return its JSON envelope — a regression here must "
    "fail loudly, not skip this module"
)


_CLEAN_SELF_REVIEW = scripted_review(summary="The scripted review found no issues.")


def test_ac07_cached_artifact_reader_rejects_escape_and_symlink(
    tmp_path: Path,
) -> None:
    from heddle.gate.entry import read_contained_utf8_artifact
    from heddle.kernel.project_config import KernelError

    workspace = tmp_path / "workspace"
    (workspace / "reviews").mkdir(parents=True)
    artifact = workspace / "reviews" / "review.md"
    artifact.write_text("review", encoding="utf-8")
    assert read_contained_utf8_artifact(
        workspace, relative_path="reviews/review.md"
    ) == ("reviews/review.md", "review")

    (workspace / "linked").symlink_to(workspace / "reviews", target_is_directory=True)
    for unsafe in ("../outside.md", "/tmp/outside.md", "linked/review.md"):
        with pytest.raises(KernelError):
            read_contained_utf8_artifact(workspace, relative_path=unsafe)


def test_ac08_run_gate_exit_codes_exclude_cas() -> None:
    # AC-8/A4: idempotent re-invocation needs no CAS — the exit-code set has no
    # exit-5 (kept in sync with the manifest contract).
    from heddle.runtime.contracts import COMMAND_SURFACE

    run_gate = next(c for c in COMMAND_SURFACE if c.name == "run-gate")
    assert 5 not in run_gate.exit_codes, (
        "FAIL: run-gate must not advertise exit 5 — appends keyed by "
        "input_hash cannot conflict (A4)"
    )


def _host(tmp_path: Path) -> Path:
    host = tmp_path / "host"
    shutil.copytree(GOLDEN, host)
    for relative in (
        "src/example/screening/validate/rules.py",
        "src/tests/screening/validate/test_rules.py",
    ):
        path = host / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# current milestone input\n", encoding="utf-8")

    def run_git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=host,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    initialize_git_repo(
        run_git,
        email="cache@example.invalid",
        name="Cache Fixture",
        add_args=(".",),
        message="baseline",
    )
    changed = host / "src/example/screening/validate/rules.py"
    changed.write_text("# current milestone input changed\n", encoding="utf-8")
    return host


def _last_self_review_run(state_path: Path) -> dict:
    import yaml

    doc = yaml.safe_load(state_path.read_text("utf-8"))
    runs = [
        run
        for gate in doc["gates"]
        if gate["gate"] == "self-review"
        for run in gate["runs"]
    ]
    assert runs, "FAIL: a self-review run fact must be recorded (AC-7)"
    return runs[-1]
