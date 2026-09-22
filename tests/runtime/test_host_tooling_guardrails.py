"""
adoption host-tooling increment 2: validate guardrail additions.

Covers the repo-level findings added to ``run_guardrails``
(managed-block-integrity, prompt-conventions). All fixtures are throwaway
tmp roots — no repo fixture is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heddle.contracts.result import Severity
from heddle.kernel.managed_regions import begin_marker, end_marker
from heddle.kernel.project_config import load_project_config
from heddle.runtime.guardrails import (
    MANAGED_BLOCK_INTEGRITY,
    PROMPT_CONVENTIONS,
    run_guardrails,
)
from heddle.runtime.sync import SESSION_ENTRY_ID

WELL_FORMED_BLOCK = (
    f"{begin_marker(SESSION_ENTRY_ID)}\nbody\n{end_marker(SESSION_ENTRY_ID)}\n"
)


def _host(tmp_path: Path, config_text: str = "") -> Path:
    (tmp_path / ".heddle.yaml").write_text(config_text, encoding="utf-8")
    return tmp_path


def _findings(root: Path, code: str) -> list:
    config = load_project_config(root)
    return [
        diagnostic
        for diagnostic in run_guardrails(root, config)
        if diagnostic.code == code
    ]


# ---------------------------------------------------------------------------
# managed-block-integrity
# ---------------------------------------------------------------------------


def test_managed_block_absent_file_is_silent(tmp_path: Path) -> None:
    # A host that has not adopted `heddle sync` has nothing to check.
    root = _host(tmp_path)
    assert _findings(root, MANAGED_BLOCK_INTEGRITY) == []


def test_managed_block_well_formed_pair_is_silent(tmp_path: Path) -> None:
    root = _host(tmp_path)
    (root / "AGENTS.md").write_text(
        f"# Agents\n\n{WELL_FORMED_BLOCK}", encoding="utf-8"
    )
    assert _findings(root, MANAGED_BLOCK_INTEGRITY) == []


def test_managed_block_crlf_pair_is_silent(tmp_path: Path) -> None:
    # Marker matching is line-content based (CR stripped), mirroring
    # sync.replace_managed_region — a CRLF-authored AGENTS.md is well-formed.
    root = _host(tmp_path)
    crlf_block = WELL_FORMED_BLOCK.replace("\n", "\r\n")
    (root / "AGENTS.md").write_bytes(f"# Agents\r\n\r\n{crlf_block}".encode())

    assert _findings(root, MANAGED_BLOCK_INTEGRITY) == []


def test_managed_block_absent_block_is_advisory(tmp_path: Path) -> None:
    # OD-4: sync creates the block on demand — advisory, not fatal. And no
    # freshness check exists at all (OD-2 explicit-only sync).
    root = _host(tmp_path)
    (root / "AGENTS.md").write_text("# Agents, no block\n", encoding="utf-8")

    findings = _findings(root, MANAGED_BLOCK_INTEGRITY)

    assert len(findings) == 1
    assert findings[0].severity is Severity.ADVISORY


@pytest.mark.parametrize(
    "text, fault",
    [
        (f"{begin_marker(SESSION_ENTRY_ID)}\nbody\n", "begin-only"),
        (f"body\n{end_marker(SESSION_ENTRY_ID)}\n", "end-only"),
        (
            f"{begin_marker(SESSION_ENTRY_ID)}\n"
            f"{begin_marker(SESSION_ENTRY_ID)}\n"
            f"{end_marker(SESSION_ENTRY_ID)}\n",
            "duplicate-begin",
        ),
        (
            f"{begin_marker(SESSION_ENTRY_ID)}\n"
            f"{end_marker(SESSION_ENTRY_ID)}\n"
            f"{end_marker(SESSION_ENTRY_ID)}\n",
            "duplicate-end",
        ),
        (
            f"{end_marker(SESSION_ENTRY_ID)}\n{begin_marker(SESSION_ENTRY_ID)}\n",
            "reversed",
        ),
    ],
)
def test_managed_block_malformed_markers_are_fatal(
    tmp_path: Path, text: str, fault: str
) -> None:
    root = _host(tmp_path)
    (root / "AGENTS.md").write_text(text, encoding="utf-8")

    findings = _findings(root, MANAGED_BLOCK_INTEGRITY)

    assert len(findings) == 1
    assert findings[0].severity is Severity.FATAL
    assert fault in findings[0].message


# ---------------------------------------------------------------------------
# retired `sync` section
# ---------------------------------------------------------------------------


def test_retired_sync_section_is_an_ignored_unknown_section(tmp_path: Path) -> None:
    # Hosts that still declare the retired `sync.mirror` key load with one
    # advisory unknown-section diagnostic and no mirror behavior.
    root = _host(tmp_path, "sync:\n  mirror: null\n")

    config = load_project_config(root)

    unknown = [d for d in config.diagnostics if d.code == "config-unknown-key"]
    assert len(unknown) == 1 and "sync" in unknown[0].message
    assert not hasattr(config, "sync_mirror")


# ---------------------------------------------------------------------------
# prompt-conventions
# ---------------------------------------------------------------------------

CONVENTIONS = (
    "conventions:\n"
    "  finding_structure:\n"
    "    keywords: ['IMPLEMENT', 'REPORT', 'IGNORE']\n"
    "prompts:\n"
    "  sample.md: ['finding_structure']\n"
)


def _prompts_host(tmp_path: Path, prompt_text: str) -> Path:
    root = _host(tmp_path, "layout:\n  prompts: prompts\n")
    prompts = root / "prompts"
    prompts.mkdir()
    (prompts / "conventions.yaml").write_text(CONVENTIONS, encoding="utf-8")
    (prompts / "sample.md").write_text(prompt_text, encoding="utf-8")
    return root


def test_prompt_conventions_absent_manifest_is_silent(tmp_path: Path) -> None:
    root = _host(tmp_path, "layout:\n  prompts: prompts\n")
    (root / "prompts").mkdir()

    assert _findings(root, PROMPT_CONVENTIONS) == []


def test_prompt_conventions_satisfied_is_silent(tmp_path: Path) -> None:
    root = _prompts_host(
        tmp_path, "Classify each finding as IMPLEMENT, REPORT, or IGNORE.\n"
    )
    assert _findings(root, PROMPT_CONVENTIONS) == []


def test_prompt_conventions_missing_marker_is_fatal(tmp_path: Path) -> None:
    root = _prompts_host(tmp_path, "This prompt omits the required markers.\n")

    findings = _findings(root, PROMPT_CONVENTIONS)

    assert len(findings) == 1
    assert findings[0].severity is Severity.FATAL
    assert "missing marker" in findings[0].message


def test_prompt_conventions_missing_prompt_file_is_fatal(tmp_path: Path) -> None:
    root = _prompts_host(tmp_path, "irrelevant\n")
    (root / "prompts" / "sample.md").unlink()

    findings = _findings(root, PROMPT_CONVENTIONS)

    assert len(findings) == 1
    assert "missing" in findings[0].message


def test_prompt_conventions_missing_partial_is_attributed_fatal(
    tmp_path: Path,
) -> None:
    root = _prompts_host(tmp_path, "[partial-missing]\n")

    findings = _findings(root, PROMPT_CONVENTIONS)

    assert len(findings) == 1
    assert findings[0].severity is Severity.FATAL
    assert "sample.md" in findings[0].message
    assert "missing.md" in findings[0].message


def test_prompt_conventions_malformed_comment_names_prompt(tmp_path: Path) -> None:
    root = _prompts_host(tmp_path, "<!-- never closed\n")

    findings = _findings(root, PROMPT_CONVENTIONS)

    assert len(findings) == 1
    assert findings[0].severity is Severity.FATAL
    assert "sample.md" in findings[0].message
    assert "malformed author comment" in findings[0].message


def test_prompt_conventions_malformed_manifest_is_isolated_fatal(
    tmp_path: Path,
) -> None:
    # run_guardrails' per-guardrail isolation: a malformed conventions.yaml
    # yields one fatal finding rather than aborting the sweep.
    root = _host(tmp_path, "layout:\n  prompts: prompts\n")
    prompts = root / "prompts"
    prompts.mkdir()
    (prompts / "conventions.yaml").write_text("{unbalanced\n", encoding="utf-8")

    findings = _findings(root, PROMPT_CONVENTIONS)

    assert len(findings) == 1
    assert findings[0].severity is Severity.FATAL
