"""Shared synthetic-host helpers for validation tests."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import yaml


def copy_host(tmp_path: Path, src: Path, name: str = "host") -> Path:
    host = tmp_path / name
    shutil.copytree(src, host)
    return host


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_files(host: Path, paths: list[str]) -> None:
    for relpath in paths:
        target = host / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(f"{relpath}\n", encoding="utf-8")


def state_path(host: Path, slug: str = "sample-feature") -> Path:
    return host / "plans" / slug / "state.yaml"


def workspace_path(host: Path, slug: str = "sample-feature") -> Path:
    return host / "plans" / slug


def clean_current_host(tmp_path: Path, src: Path, name: str = "host") -> Path:
    host = copy_host(tmp_path, src, name)
    state = read_yaml(state_path(host))
    assert state["schema"] == "heddle.state/v9"
    ensure_files(
        host,
        [owned for milestone in state["milestones"] for owned in milestone["owns"]],
    )
    return host


def clone_workspace(host: Path, slug: str, *, schema: str) -> None:
    shutil.copytree(workspace_path(host), workspace_path(host, slug))
    path = state_path(host, slug)
    state = read_yaml(path)
    state["feature"] = slug
    state["schema"] = schema
    write_yaml(path, state)


def write_feature_spec(
    path: Path,
    *,
    status: str,
    verified_by: str,
    extra_frontmatter: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "type: feature-spec\n"
        "area: example\n"
        f"feature_name: {path.stem.title()}\n"
        f"status: {status}\n"
        f"{extra_frontmatter}"
        "---\n\n"
        f"# {path.stem.title()}\n\n"
        "## Acceptance Criteria\n\n"
        "### AC-1: verified behavior\n\n"
        "Priority: MUST\n"
        "Action: run the verified test.\n"
        "Expected: pass.\n"
        f"Verified-by: {verified_by}\n",
        encoding="utf-8",
    )


def write_pattern(
    path: Path, *, name: str, body: str, source_path: str | None = None
) -> None:
    source_line = f"source_path: {source_path}\n" if source_path is not None else ""
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "area: testing\n"
        "status: active\n"
        "created: 2026-06-19\n"
        f"{source_line}"
        "superseded_by: null\n"
        "---\n\n"
        f"# {name}\n\n"
        "**Intent:** test fixture.\n\n"
        "**When to use:** validation.\n\n"
        "**Recipe:** run validate.\n\n"
        "**Anti-patterns / caveats:** none.\n\n"
        f"{body}\n\n"
        "**Source:** synthetic validation fixture.\n",
        encoding="utf-8",
    )


def seed_single_pattern_violation(host: Path) -> None:
    patterns = host / "docs" / "patterns"
    patterns.mkdir(parents=True, exist_ok=True)
    (patterns / "_descriptions.yaml").write_text(
        "global-once: Repo-global once-only pattern case\n", encoding="utf-8"
    )
    write_pattern(
        patterns / "global-once.md",
        name="global-once",
        body=(
            "**Concrete future-feature scenario:** a future feature uses it.\n\n"
            "See [[pattern:no-such-slug]]."
        ),
    )


def seed_guardrails(host: Path) -> None:
    tests_dir = host / "tests" / "verified"
    tests_dir.mkdir(parents=True, exist_ok=True)
    current_text = "def test_verified():\n    assert True\n"
    for name in ("drifted", "matching", "missing_baseline", "grandfathered"):
        (tests_dir / f"test_{name}.py").write_text(current_text, encoding="utf-8")
    write_feature_spec(
        host / "docs" / "features" / "example" / "drifted.md",
        status="complete",
        verified_by="tests/verified/test_drifted.py::test_verified",
    )
    write_feature_spec(
        host / "docs" / "features" / "example" / "matching.md",
        status="complete",
        verified_by="tests/verified/test_matching.py::test_verified",
    )
    write_feature_spec(
        host / "docs" / "features" / "example" / "active.md",
        status="draft",
        verified_by="tests/verified/test_drifted.py::test_verified",
    )
    write_feature_spec(
        host / "docs" / "features" / "example" / "missing-baseline.md",
        status="complete",
        verified_by="tests/verified/test_missing_baseline.py::test_verified",
    )
    write_feature_spec(
        host / "docs" / "features" / "example" / "missing-file.md",
        status="complete",
        verified_by="tests/verified/test_missing_file.py::test_verified",
    )
    write_feature_spec(
        host / "docs" / "features" / "example" / "grandfathered.md",
        status="complete",
        verified_by="tests/verified/test_grandfathered.py::test_verified",
        extra_frontmatter="created: 1999-01-01\nverified_count: 999\n",
    )
    baseline = host / ".heddle" / "spec-drift-baseline.yaml"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(
        baseline,
        {
            "example/drifted": {
                "tests/verified/test_drifted.py": sha256_text("old content\n")
            },
            "example/matching": {
                "tests/verified/test_matching.py": sha256_text(current_text)
            },
            "example/missing-file": {
                "tests/verified/test_missing_file.py": sha256_text("missing\n")
            },
            "example/grandfathered": {
                "tests/verified/test_grandfathered.py": sha256_text("old\n")
            },
        },
    )

    patterns = host / "docs" / "patterns"
    patterns.mkdir(parents=True, exist_ok=True)
    (patterns / "_descriptions.yaml").write_text(
        "broken-ref: Broken reference case\n"
        "duplicate-slug: Duplicate slug case\n"
        "dangling-source: Dangling source case\n"
        "missing-scenario: Missing scenario case\n",
        encoding="utf-8",
    )
    scenario = "**Concrete future-feature scenario:** a future feature uses it."
    write_pattern(
        patterns / "broken-ref.md",
        name="broken-ref",
        body=f"{scenario}\n\nSee [[pattern:no-such-slug]].",
    )
    write_pattern(
        patterns / "duplicate-a.md",
        name="duplicate-slug",
        body=scenario,
    )
    write_pattern(
        patterns / "duplicate-b.md",
        name="duplicate-slug",
        body=scenario,
    )
    write_pattern(
        patterns / "dangling-source.md",
        name="dangling-source",
        body=scenario,
        source_path="docs/features/example/missing-feature.md",
    )
    write_pattern(
        patterns / "missing-scenario.md",
        name="missing-scenario",
        body="No scenario section here.",
    )
    write_pattern(
        patterns / "no-description.md",
        name="no-description",
        body=scenario,
    )

    design = host / "docs" / "design"
    design.mkdir(parents=True, exist_ok=True)
    (design / "covered.md").write_text(
        "# Covered Doc\n\n## Existing Anchor\n", encoding="utf-8"
    )
    (design / "unlinked.md").write_text("# Unlinked Doc\n", encoding="utf-8")
    orientation = host / "docs" / "workflow" / "orientation.md"
    orientation.parent.mkdir(parents=True, exist_ok=True)
    orientation.write_text(
        "---\n"
        "covers:\n"
        "  - docs/design/*.md\n"
        "---\n\n"
        "# Orientation\n\n"
        "- [covered](../design/covered.md#existing-anchor)\n"
        "- [missing](../design/missing.md)\n"
        "- [bad-anchor](../design/covered.md#missing-anchor)\n",
        encoding="utf-8",
    )
