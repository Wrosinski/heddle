"""
Installed-artifact and workflow-adoption acceptance contracts.

The built-wheel test deliberately uses a temporary output directory, a fresh
venv, and a host outside the checkout.  Runtime dependencies are copied from
the already-resolved development environment so the acceptance lane stays
offline while still proving that the wheel declares and imports them.

The cases use synthetic feature documents and the maintained public surfaces.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from tests.runtime.search_helpers import snapshot_tree
from tests.runtime.wheel_harness import (
    REPO_ROOT,
    build_installed_wheel,
)
from tests.runtime.wheel_harness import (
    run as _run,
)

P0_SELECTED_SKILLS = (
    "new-feature",
    "pre-implementation-analysis",
    "root-cause-analysis",
)
P0_FORBIDDEN_MECHANISMS = {
    "retired search script": ("scripts/feature_search.py",),
    "retired validation script": ("scripts/validate_docs.py",),
    "flat brief path": ("plans/.briefs",),
    "flat plan path": ("plans/[feature-name].plan.md",),
    "manual state mutation": ("phase_authorized_through",),
    "source package setup": ("poetry install", "import private_app"),
    "source checkout path": ("/srv/private_app",),
    "source root prompt": ("prompts/phase12-kickoff.md",),
    "stop-hook bypass": ("PRIVATE_APP_SKIP",),
    "vendor model policy": ("claude-fable", "claude-opus"),
    "fixed concurrency policy": ("max 10 concurrent", "10 concurrent agents"),
}
P0_FORBIDDEN_TOKENS = tuple(
    token for tokens in P0_FORBIDDEN_MECHANISMS.values() for token in tokens
)
P0_PUBLIC_COMMAND_CONTRACTS = {
    "orient": {
        "required_args": (),
        "flags": ("--feature", "--json"),
        "mutating": False,
    },
    "feature start": {
        "required_args": ("slug",),
        "flags": ("--area", "--dry-run", "--flow", "--json", "--retier", "--tier"),
        "mutating": True,
    },
    "search": {
        "required_args": ("query",),
        "flags": ("--json", "--limit", "--titles-only"),
        "mutating": False,
    },
    "kickoff": {
        "required_args": (),
        "flags": ("--feature", "--json"),
        "mutating": False,
    },
    "validate": {
        "required_args": (),
        "flags": ("--json",),
        "mutating": False,
    },
    "phase-exit": {
        "required_args": (),
        "flags": ("--dry-run", "--feature", "--json", "--through"),
        "mutating": True,
    },
}
P0_ADVERTISED_COMMAND_FORMS = (
    ("heddle orient --json", "orient", (), ("--json",)),
    (
        "heddle feature start <slug> --area <area> --tier <1|2|3> "
        "[--flow hitl|auto] --json",
        "feature start",
        ("slug",),
        ("--area", "--flow", "--json", "--tier"),
    ),
    (
        "heddle orient --feature <slug> --json",
        "orient",
        (),
        ("--feature", "--json"),
    ),
    (
        "heddle kickoff --feature <slug>",
        "kickoff",
        (),
        ("--feature",),
    ),
    (
        'heddle search "<intent>" --titles-only',
        "search",
        ("query",),
        ("--titles-only",),
    ),
    ("heddle validate", "validate", (), ()),
    ("heddle phase-exit", "phase-exit", (), ()),
)


@pytest.fixture(scope="module")
def p0_public_command_entries() -> dict[str, dict[str, object]]:
    """Resolve launcher contracts once through the public module entrypoint."""
    result = _run(
        [sys.executable, "-m", "heddle", "help", "--json"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    return {entry["name"]: entry for entry in manifest["data"]["commands"]}


def _advertised_manifest_contract_errors(
    text: str, entries: dict[str, dict[str, object]]
) -> list[str]:
    errors: list[str] = []
    for name, expected in P0_PUBLIC_COMMAND_CONTRACTS.items():
        entry = entries.get(name)
        if entry is None:
            errors.append(f"missing public command: {name}")
            continue
        projection = {
            "required_args": tuple(
                argument["name"] for argument in entry["args"] if argument["required"]
            ),
            "flags": tuple(sorted(flag["name"] for flag in entry["flags"])),
            "mutating": entry["mutating"],
        }
        if projection != expected:
            errors.append(f"public command projection: {name}")

    for form, name, required_args, advertised_flags in P0_ADVERTISED_COMMAND_FORMS:
        if f"`{form}`" not in text:
            errors.append(f"advertised command form: {form}")
            continue
        contract = P0_PUBLIC_COMMAND_CONTRACTS[name]
        if required_args != contract["required_args"]:
            errors.append(f"advertised required arguments: {form}")
        if not set(advertised_flags) <= set(contract["flags"]):
            errors.append(f"advertised flags: {form}")
    return errors


def _new_feature_contract_errors(text: str) -> list[str]:
    normalized = " ".join(text.split())
    required = {
        "native search": ('`heddle search "<intent>" --titles-only`',),
        "emitted routing": ("`next_actions`",),
        "native validation": ("`heddle validate`",),
        "runtime phase exit": ("`heddle phase-exit`", "or the Heddle driver"),
        "state ownership": ("Never hand-edit `state.yaml`",),
        "conditional feature start": (
            "Only for genuinely new work, determine a slug, area, initial tier, "
            "and flow, then run `heddle feature start <slug> --area <area> "
            "--tier <1|2|3> [--flow hitl|auto] --json`.",
        ),
        "routed kickoff": (
            "Run `heddle kickoff --feature <slug>` only when orientation routes there.",
        ),
    }
    errors = [
        label
        for label, fragments in required.items()
        if not all(fragment in normalized for fragment in fragments)
    ]

    ordered = (
        "`heddle orient --json`",
        "`heddle feature start <slug> --area <area> --tier <1|2|3> "
        "[--flow hitl|auto] --json`",
        "`next_actions`",
        "`heddle orient --feature <slug> --json`",
        "`heddle kickoff --feature <slug>`",
    )
    cursor = 0
    for fragment in ordered:
        position = text.find(fragment, cursor)
        if position < 0:
            errors.append(f"ordered native entry: {fragment}")
            break
        cursor = position + len(fragment)

    for fragment in P0_FORBIDDEN_TOKENS:
        if fragment in text:
            errors.append(f"forbidden legacy mechanism: {fragment}")
    return errors


@pytest.mark.acceptance
@pytest.mark.toolchain
def test_ac10_built_wheel_nested_cwd_without_active_feature(tmp_path: Path) -> None:
    """AC-10: the declared wheel works in a clean, nested, docs-only host."""
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = pyproject["project"]["dependencies"]
    assert any(
        dependency.lower().startswith("markdown-it-py") for dependency in dependencies
    ), "RED AC-10: markdown-it-py must be a direct wheel dependency"

    installed = build_installed_wheel(tmp_path)
    venv_python = installed.python
    target_site = installed.site_packages

    host = tmp_path / "host"
    nested = host / "nested" / "deeper"
    (host / "docs" / "features").mkdir(parents=True)
    (host / "docs" / "patterns").mkdir(parents=True)
    nested.mkdir(parents=True)
    (host / ".heddle.yaml").write_text(
        "layout:\n  specs: docs/features\n",
        encoding="utf-8",
    )
    (host / "docs" / "features" / "other.md").write_text(
        "# Other\n\n## Unrelated\n\nNo matching terms.\n",
        encoding="utf-8",
    )
    (host / "docs" / "patterns" / "wheel.md").write_text(
        "# Wheel Pattern\n\nInstalled dependency closure.\n",
        encoding="utf-8",
    )
    before = snapshot_tree(host)

    clean_env = installed.env
    command = installed.command
    result = _run(
        [
            str(command),
            "search",
            "wheel pattern",
            "--json",
            "--titles-only",
        ],
        cwd=nested,
        env=clean_env,
    )
    assert result.returncode == 0, (
        f"FAIL AC-10: installed search failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"] == {
        "query": "wheel pattern",
        "hits": [
            {
                "kind": "pattern",
                "path": "docs/patterns/wheel.md",
                "anchor": "wheel-pattern",
                "title": "Wheel Pattern",
            }
        ],
    }
    assert not (host / "plans").exists()
    assert not (host / ".git" / "heddle" / "active-feature").exists()
    assert snapshot_tree(host) == before

    provenance = _run(
        [
            str(venv_python),
            "-c",
            (
                "import heddle, importlib.metadata as m; "
                "print(heddle.__file__); "
                "print('\\n'.join(m.requires('heddle') or ()))"
            ),
        ],
        cwd=nested,
        env=clean_env,
    )
    assert provenance.returncode == 0
    assert str(target_site) in provenance.stdout
    assert str(REPO_ROOT) not in provenance.stdout
    assert "markdown-it-py" in provenance.stdout.lower()


@pytest.mark.acceptance
def test_ac12_workflow_adopts_native_search_without_mirror_drift(
    tmp_path, corpus
) -> None:
    """AC-12: every live Phase-1 surface uses the native command."""
    for checker in ("check-skill-sync.py", "check-prompt-conventions.py"):
        result = _run(
            [sys.executable, str(REPO_ROOT / "scripts" / checker)],
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, (
            f"FAIL AC-12: {checker} failed\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    native = 'heddle search "<intent>" --titles-only'
    live_surfaces = [
        "heddle/resources/specify.briefing.md",
        "docs/workflow/brainstorming-guidelines.md",
        "docs/workflow/document-structure.md",
        "docs/workflow/subagent-exploration-patterns.md",
        ".codex/skills/new-feature/SKILL.md",
        ".codex/skills/pre-implementation-analysis/SKILL.md",
        ".claude/skills/new-feature/SKILL.md",
        ".claude/skills/pre-implementation-analysis/SKILL.md",
    ]
    for relative in live_surfaces:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "scripts/feature_search.py" not in text, (
            f"RED AC-12: stale executable search guidance remains in {relative}"
        )
        assert native in text, (
            f"RED AC-12: native Phase-1 search guidance missing from {relative}"
        )

    document_structure = (
        REPO_ROOT / "docs" / "workflow" / "document-structure.md"
    ).read_text(encoding="utf-8")
    assert "heddle.search/v0" in document_structure
    assert "_descriptions.yaml" in document_structure
    assert "fallback" in document_structure.lower()

    for skill in ("new-feature", "pre-implementation-analysis"):
        codex = REPO_ROOT / ".codex" / "skills" / skill / "SKILL.md"
        claude = REPO_ROOT / ".claude" / "skills" / skill / "SKILL.md"
        assert codex.read_bytes() == claude.read_bytes(), (
            f"FAIL AC-12: {skill} Claude/Codex mirrors drifted"
        )

    from dataclasses import replace

    from heddle.driver.sessions import allowed_tools_for_stage
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config
    from tests.runtime.write_path_helpers import copy_host

    host = copy_host(tmp_path, corpus.golden)
    config = load_project_config(host)
    snapshot = resolve_snapshot(config, "nl-screening")
    search_tool = "Bash(heddle search:*)"
    assert search_tool in allowed_tools_for_stage(
        config, replace(snapshot, stage="specify")
    )
    for stage in (
        "spec-review",
        "plan-review",
        "scaffold",
        "implement",
        "peer-review",
        "robustness",
    ):
        assert search_tool not in allowed_tools_for_stage(
            config, replace(snapshot, stage=stage)
        ), (
            "FAIL AC-12: native search permission escaped the specify "
            f"profile to {stage}"
        )


def test_review_allowance_description_is_searchable_with_round_vocabulary(
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "host"
    feature = host / "docs/features/workflow/example-round-policy.md"
    feature.parent.mkdir(parents=True)
    (host / ".heddle.yaml").write_text("layout:\n  specs: docs/features\n")
    feature.write_text(
        "# Review allowance\n\n"
        "## Review round allowance\n\n"
        "Raise a confirmed role's provider quality round limit.\n"
    )
    monkeypatch.chdir(host)
    code, out, err = run_cli(["search", "review round allowance", "--json"])
    assert code == 0, err
    envelope = json.loads(out)
    assert envelope["ok"] is True
    results = envelope["data"]["hits"]
    matches = [
        row
        for row in results
        if row["path"] == "docs/features/workflow/example-round-policy.md"
    ]
    assert matches
    searchable = json.dumps(matches).lower()
    assert "quality round" in searchable
    assert "provider" in searchable


def test_p0_ac4_canonical_forbidden_set_covers_the_selected_corpus() -> None:
    """AC-4: one categorized fixture set scans every selected mirror."""
    for agent in ("codex", "claude"):
        for skill in P0_SELECTED_SKILLS:
            path = REPO_ROOT / f".{agent}" / "skills" / skill / "SKILL.md"
            text = path.read_text(encoding="utf-8")
            found = {
                category: [token for token in tokens if token in text]
                for category, tokens in P0_FORBIDDEN_MECHANISMS.items()
            }
            found = {category: tokens for category, tokens in found.items() if tokens}
            assert not found, f"FAIL AC-4: {path} retains source-only policy: {found}"
