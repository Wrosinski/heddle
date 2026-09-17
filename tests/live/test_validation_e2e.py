"""Local E2E coverage for real validation commands on synthetic hosts."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [
    pytest.mark.e2e,
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"


def _run_heddle(host: Path, *argv: str) -> tuple[int, dict[str, Any], str]:
    proc = subprocess.run(
        [sys.executable, "-m", "heddle", *argv, "--json"],
        cwd=host,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        envelope = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "FAIL live M5: Heddle did not emit a clean JSON envelope on stdout; "
            f"exit={proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}"
        ) from exc
    return proc.returncode, envelope, proc.stderr


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _state_path(host: Path, slug: str = "sample-feature") -> Path:
    return host / "plans" / slug / "state.yaml"


def _workspace(host: Path, slug: str = "sample-feature") -> Path:
    return host / "plans" / slug


def _clone_workspace(host: Path, slug: str, *, schema: str) -> None:
    shutil.copytree(_workspace(host), _workspace(host, slug))
    state_path = _state_path(host, slug)
    state = _read_yaml(state_path)
    state["feature"] = slug
    state["schema"] = schema
    _write_yaml(state_path, state)


def _normalize_v1(host: Path) -> None:
    state_path = _state_path(host)
    state = _read_yaml(state_path)
    state["schema"] = "heddle.state/v1"
    state.setdefault("flow", "hitl")
    state.setdefault(
        "authorizations",
        [
            {
                "through": state["authorized_through"],
                "source": "user",
                "at": state["updated"],
            }
        ],
    )
    _write_yaml(state_path, state)
    for milestone in state["milestones"]:
        for owned in milestone.get("owns", []):
            target = host / owned
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{owned}\n", encoding="utf-8")


def _seed_guardrail_inputs(host: Path) -> None:
    tests_dir = host / "tests" / "verified"
    tests_dir.mkdir(parents=True, exist_ok=True)
    current_text = "def test_verified():\n    assert True\n"
    (tests_dir / "test_drifted.py").write_text(current_text, encoding="utf-8")
    (host / "docs" / "features" / "example" / "drifted.md").write_text(
        "---\n"
        "type: feature-spec\n"
        "area: example\n"
        "feature_name: Drifted\n"
        "status: complete\n"
        "---\n\n"
        "# Drifted\n\n"
        "## Acceptance Criteria\n\n"
        "### AC-1: verified behavior\n\n"
        "Priority: MUST\nAction: run.\nExpected: pass.\n"
        "Verified-by: tests/verified/test_drifted.py::test_verified\n",
        encoding="utf-8",
    )
    baseline = host / ".heddle" / "spec-drift-baseline.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        baseline,
        {
            "example/drifted": {
                "tests/verified/test_drifted.py": _sha256_text("old content\n")
            }
        },
    )

    patterns = host / "docs" / "patterns"
    patterns.mkdir(parents=True, exist_ok=True)
    (patterns / "_descriptions.yaml").write_text(
        "broken-ref: Broken reference case\n", encoding="utf-8"
    )
    (patterns / "broken-ref.md").write_text(
        "---\n"
        "name: broken-ref\n"
        "area: testing\n"
        "status: active\n"
        "created: 2026-06-20\n"
        "superseded_by: null\n"
        "---\n\n"
        "# Broken Ref\n\n"
        "**Intent:** live guardrail.\n\n"
        "**When to use:** validation.\n\n"
        "**Recipe:** run validate.\n\n"
        "**Anti-patterns / caveats:** none.\n\n"
        "**Concrete future-feature scenario:** a future feature uses it.\n\n"
        "See [[pattern:no-such-slug]].\n\n"
        "**Source:** synthetic validation fixture.\n",
        encoding="utf-8",
    )

    design = host / "docs" / "design"
    design.mkdir(parents=True, exist_ok=True)
    (design / "covered.md").write_text("# Covered\n\n## Existing\n", encoding="utf-8")
    orientation = host / "docs" / "workflow" / "orientation.md"
    orientation.parent.mkdir(parents=True, exist_ok=True)
    orientation.write_text(
        "---\n"
        "covers:\n"
        "  - docs/design/*.md\n"
        "---\n\n"
        "# Orientation\n\n"
        "- [covered](../design/covered.md#existing)\n"
        "- [missing](../design/missing.md)\n",
        encoding="utf-8",
    )


def _seed_single_pattern_violation(host: Path) -> None:
    patterns = host / "docs" / "patterns"
    patterns.mkdir(parents=True, exist_ok=True)
    (patterns / "_descriptions.yaml").write_text(
        "global-once: Repo-global once-only pattern case\n", encoding="utf-8"
    )
    (patterns / "global-once.md").write_text(
        "---\n"
        "name: global-once\n"
        "area: testing\n"
        "status: active\n"
        "created: 2026-06-21\n"
        "superseded_by: null\n"
        "---\n\n"
        "# Global Once\n\n"
        "**Intent:** live repo-global guardrail.\n\n"
        "**When to use:** validation.\n\n"
        "**Recipe:** run validate.\n\n"
        "**Anti-patterns / caveats:** none.\n\n"
        "**Concrete future-feature scenario:** a future feature uses it.\n\n"
        "See [[pattern:no-such-slug]].\n\n"
        "**Source:** synthetic validation fixture.\n",
        encoding="utf-8",
    )


def test_local_migrate_and_validate_no_mocks(tmp_path: Path) -> None:
    """AC-1..AC-13 live-local coverage over real CLI process boundaries."""
    host = shutil.copytree(TINY, tmp_path / "m5-live")
    state_path = _state_path(host)
    before = _read_yaml(state_path)
    before_bytes = state_path.read_bytes()

    code, envelope, _stderr = _run_heddle(host, "migrate", "--dry-run")
    assert code == 0 and envelope["data"]["dry_run"] is True, (
        f"FAIL live M5 AC-4: migrate --dry-run failed: exit={code}, "
        f"envelope={envelope!r}"
    )
    assert state_path.read_bytes() == before_bytes, (
        "FAIL live M5 AC-4: migrate --dry-run changed state.yaml"
    )

    code, envelope, _stderr = _run_heddle(host, "migrate")
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live M5 AC-3: migrate failed: exit={code}, envelope={envelope!r}"
    )
    migrated = _read_yaml(state_path)
    assert migrated["schema"] == "heddle.state/v1", (
        "FAIL live M5 AC-3: migrate must stamp schema v1"
    )
    assert migrated["flow"] == "hitl"
    assert migrated["authorizations"] == [
        {
            "through": before["authorized_through"],
            "source": "user",
            "at": before["updated"],
        }
    ]
    assert migrated["revision"] == before["revision"] + 1

    after_first = state_path.read_bytes()
    code, envelope, _stderr = _run_heddle(host, "migrate")
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL live M5 AC-5: repeated migrate failed: exit={code}, "
        f"envelope={envelope!r}"
    )
    assert state_path.read_bytes() == after_first, (
        "FAIL live M5 AC-4: repeated migrate must be a no-op"
    )

    code, envelope, _stderr = _run_heddle(host, "migrate", "--from-legacy")
    assert code == 3 and envelope["error"]["code"] == "not-implemented", (
        f"FAIL live M5 AC-5: --from-legacy failed incorrectly: exit={code}, "
        f"envelope={envelope!r}"
    )
    assert "M8" in envelope["error"]["hint"], (
        "FAIL live M5 AC-5: --from-legacy deferral must name M8"
    )

    _normalize_v1(host)
    code, envelope, _stderr = _run_heddle(host, "validate")
    assert code == 0 and envelope["ok"] is True and envelope["diagnostics"] == [], (
        f"FAIL live M5 AC-6: clean validate failed: exit={code}, envelope={envelope!r}"
    )

    spec_path = host / "docs" / "features" / "example" / "sample-feature.md"
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8")
        + "\n### AC-3: live advisory\n\n"
        + "Priority: MUST\nAction: validate.\nExpected: advisory.\n",
        encoding="utf-8",
    )
    code, envelope, _stderr = _run_heddle(host, "validate")
    assert code == 4 and envelope["ok"] is True and "error" not in envelope, (
        f"FAIL live M5 AC-6: advisory-only validate failed: exit={code}, "
        f"envelope={envelope!r}"
    )
    assert any(d["code"] == "ac-unassigned" for d in envelope["diagnostics"]), (
        "FAIL live M5 AC-6/AC-7: AC-3 must be reported as ac-unassigned"
    )

    state = _read_yaml(state_path)
    state["milestones"][0]["owns"].append("src/live_missing.py")
    _write_yaml(state_path, state)
    _seed_guardrail_inputs(host)
    code, envelope, _stderr = _run_heddle(host, "validate")
    assert code == 3 and envelope["error"]["code"] == "ledger-drift", (
        f"FAIL live M5 AC-7/AC-9: fatal validate failed: exit={code}, "
        f"envelope={envelope!r}"
    )
    codes = {diagnostic["code"] for diagnostic in envelope["diagnostics"]}
    for expected_code in ("spec-drift", "pattern-integrity", "orientation-drift"):
        assert expected_code in codes, (
            f"FAIL live M5 AC-10/11/12: missing {expected_code} diagnostic"
        )
    messages = "\n".join(d["message"] for d in envelope["diagnostics"])
    assert "src/live_missing.py" in messages and "AC-3" in messages, (
        "FAIL live M5 AC-9: validate must accumulate fatal and advisory findings"
    )
    for token in ("drifted", "no-such-slug", "missing.md"):
        assert token in messages, (
            f"FAIL live M5 AC-10/11/12: missing guardrail token {token!r}"
        )

    validate_global_host = shutil.copytree(TINY, tmp_path / "m5-global-validate")
    _normalize_v1(validate_global_host)
    _clone_workspace(validate_global_host, "future-lab", schema="heddle.state/v2")
    _clone_workspace(validate_global_host, "drift-lab", schema="heddle.state/v1")
    _seed_single_pattern_violation(validate_global_host)
    drift_state_path = _state_path(validate_global_host, "drift-lab")
    drift_state = _read_yaml(drift_state_path)
    drift_state["milestones"][0]["owns"].append("src/live_global_missing.py")
    _write_yaml(drift_state_path, drift_state)
    code, envelope, _stderr = _run_heddle(validate_global_host, "validate")
    assert code == 3 and envelope["error"]["code"] == "ledger-drift", (
        f"FAIL live M5 AC-13: repo-global validate failed: exit={code}, "
        f"envelope={envelope!r}"
    )
    pattern_findings = [
        d for d in envelope["diagnostics"] if d["code"] == "pattern-integrity"
    ]
    assert (
        len(pattern_findings) == 1
        and "no-such-slug" in (pattern_findings[0]["message"])
    ), (
        "FAIL live M5 AC-13: repo-global validate must run guardrails once, "
        f"got {pattern_findings!r}"
    )

    global_host = shutil.copytree(TINY, tmp_path / "m5-global")
    _clone_workspace(global_host, "future-lab", schema="heddle.state/v2")
    code, envelope, _stderr = _run_heddle(global_host, "migrate")
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
        f"FAIL live M5 AC-13: repo-global migrate split failed: exit={code}, "
        f"envelope={envelope!r}"
    )
    assert _read_yaml(_state_path(global_host))["schema"] == "heddle.state/v1"
    assert _read_yaml(_state_path(global_host, "future-lab"))["schema"] == (
        "heddle.state/v2"
    )
    assert "data" not in envelope, (
        "FAIL live M5 AC-13: failure envelopes must preserve data-xor-error; "
        "migrate split belongs in diagnostics"
    )
    migrated_messages = "\n".join(
        d["message"]
        for d in envelope["diagnostics"]
        if d["severity"] == "info" and d["code"] == "workspace-migrated"
    )
    skipped_messages = "\n".join(
        d["message"]
        for d in envelope["diagnostics"]
        if d["severity"] == "fatal" and "workspace-invalid" in d["code"]
    )
    assert "sample-feature" in migrated_messages and "future-lab" not in (
        migrated_messages
    ), (
        "FAIL live M5 AC-13: migrated behind workspace must be reported via "
        f"diagnostics, got {migrated_messages!r}"
    )
    assert "future-lab" in skipped_messages and "sample-feature" not in (
        skipped_messages
    ), (
        "FAIL live M5 AC-13: skipped ahead workspace must be reported via "
        f"workspace-invalid diagnostics, got {skipped_messages!r}"
    )

    code, envelope, _stderr = _run_heddle(host, "help")
    by_name = {entry["name"]: entry for entry in envelope["data"]["commands"]}
    assert by_name["validate"]["output_schema"] == "heddle.validate/v0"
    assert by_name["migrate"]["output_schema"] == "heddle.migrate/v0"
