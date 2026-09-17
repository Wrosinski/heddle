"""
Native feature-search command scaffold for milestone reader.

The tests intentionally fail against the pre-search dispatcher while keeping
collection and unrelated smoke tests green.  They pin the public CLI/envelope
contract and leave CommonMark/FTS behavior with the kernel suite.

Behavior contract: feature-search
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _base_host(tmp_path: Path, name: str = "host") -> Path:
    host = tmp_path / name
    (host / "docs" / "features").mkdir(parents=True)
    (host / ".heddle.yaml").write_text(
        "layout:\n  specs: docs/features\n",
        encoding="utf-8",
    )
    return host


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _two_hit_host(tmp_path: Path, name: str = "host") -> Path:
    host = _base_host(tmp_path, name)
    _write(
        host / "docs" / "features" / "a.md",
        "# Fixture\n\n## Alpha Signal\n\nshared marker body a\nA boundary sentinel\n",
    )
    _write(
        host / "docs" / "patterns" / "b.md",
        "# Beta Signal\n\nshared marker body b\nB boundary sentinel\n",
    )
    return host


def _search_module() -> ModuleType:
    try:
        return importlib.import_module("heddle.kernel.knowledge_search")
    except ModuleNotFoundError as error:
        pytest.fail(
            "RED feature-search: the m1 knowledge-search core is unavailable "
            f"to the native m2 command ({error})",
            pytrace=False,
        )


def test_ac03_omitted_limit_defaults_to_five(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-3 CLI half: absence of --limit supplies five stable full hits."""
    host = _base_host(tmp_path)
    for index in range(7):
        _write(
            host / "docs" / "features" / f"{index}.md",
            f"# Fixture\n\n## Result {index}\n\ndefaultmarker\n",
        )
    monkeypatch.chdir(host)

    expected_hits = [
        {
            "kind": "feature-spec-section",
            "path": f"docs/features/{index}.md",
            "anchor": f"result-{index}",
            "title": f"Result {index}",
            "body": "\ndefaultmarker\n",
        }
        for index in range(5)
    ]
    observed_hits = []
    for _ in range(2):
        code, out, err = run_cli(["search", "defaultmarker", "--json"])
        assert code == 0, f"FAIL AC-3: omitted limit search failed: {out} {err}"
        observed_hits.append(envelope_tools.parse(out)["data"]["hits"])
    assert observed_hits == [expected_hits, expected_hits]


def test_local_history_ignored_spec_remains_searchable(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin for AC-7: search reads local specs even when Git ignores them."""
    host = _base_host(tmp_path)
    relative = "docs/features/local-only.md"
    _write(
        host / relative,
        "# Local only\n\n## Retained History\n\nignoredhistorymarker\n",
    )
    (host / ".gitignore").write_text("docs/features/\n")
    subprocess.run(["git", "init", "-q"], cwd=host, check=True)
    assert (
        subprocess.run(
            ["git", "check-ignore", "-q", relative], cwd=host, check=False
        ).returncode
        == 0
    )
    monkeypatch.chdir(host)

    code, out, err = run_cli(["search", "ignoredhistorymarker", "--json"])

    assert code == 0, (out, err)
    hits = envelope_tools.parse(out)["data"]["hits"]
    assert [hit["path"] for hit in hits] == [relative]


def test_ac04_corpus_faults_fail_closed_without_partial_hits(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-4: the optional-absent countercase passes; every present fault fails."""

    def invoke(host: Path) -> tuple[int, dict[str, Any], str]:
        monkeypatch.chdir(host)
        code, out, err = run_cli(["search", "faultmarker", "--json"])
        return code, envelope_tools.parse(out), err

    def assert_invalid(
        host: Path,
        path_hint: str,
        *,
        reject_absolute: bool = False,
    ) -> None:
        code, envelope, _err = invoke(host)
        assert code == 3
        assert envelope["ok"] is False and "data" not in envelope
        assert envelope["error"]["code"] == "workspace-invalid"
        error_text = f"{envelope['error']['message']} {envelope['error']['hint']}"
        assert path_hint in error_text, (
            f"FAIL AC-4: corpus fault must identify {path_hint!r}: {error_text}"
        )
        if reject_absolute:
            assert str(host.resolve()) not in error_text, (
                f"FAIL AC-4: document fault exposed absolute host path: {error_text}"
            )

    # The one non-fault: an absent optional patterns shelf is a complete,
    # successful Feature-Spec-only corpus.
    optional_absent = _base_host(tmp_path, "optional-absent")
    _write(
        optional_absent / "docs" / "features" / "valid.md",
        "# Fixture\n\n## Valid\n\nfaultmarker\n",
    )
    code, envelope, _err = invoke(optional_absent)
    assert code == 0 and envelope["data"]["hits"]

    missing_specs = tmp_path / "missing-specs"
    missing_specs.mkdir()
    (missing_specs / ".heddle.yaml").write_text("", encoding="utf-8")
    assert_invalid(missing_specs, "docs/features")

    required_file = tmp_path / "required-file"
    (required_file / "docs").mkdir(parents=True)
    (required_file / ".heddle.yaml").write_text("", encoding="utf-8")
    (required_file / "docs" / "features").write_text("not a dir", encoding="utf-8")
    assert_invalid(required_file, "docs/features")

    optional_file = _base_host(tmp_path, "optional-file")
    _write(
        optional_file / "docs" / "features" / "valid.md",
        "# Fixture\n\n## Valid\n\nfaultmarker\n",
    )
    (optional_file / "docs" / "patterns").write_text("not a dir", encoding="utf-8")
    assert_invalid(optional_file, "docs/patterns")

    root_symlink = tmp_path / "root-symlink"
    (root_symlink / "docs").mkdir(parents=True)
    (root_symlink / ".heddle.yaml").write_text("", encoding="utf-8")
    real_specs = root_symlink / "real-specs"
    real_specs.mkdir()
    (root_symlink / "docs" / "features").symlink_to(
        real_specs, target_is_directory=True
    )
    assert_invalid(root_symlink, "docs/features")

    entry_symlink = _base_host(tmp_path, "entry-symlink")
    target = entry_symlink / "target.md"
    _write(target, "# Fixture\n\n## Target\n\nfaultmarker\n")
    (entry_symlink / "docs" / "features" / "alias.md").symlink_to(target)
    assert_invalid(entry_symlink, "docs/features/alias.md")

    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (escaped / ".heddle.yaml").write_text(
        "layout:\n  specs: ../outside\n",
        encoding="utf-8",
    )
    assert_invalid(escaped, "specs")

    malformed = _base_host(tmp_path, "malformed")
    bad = malformed / "docs" / "features" / "bad.md"
    _write(bad, "---\nkey: [\n---\n# Bad\n\n## Bad\n\nfaultmarker\n")
    assert_invalid(malformed, "docs/features/bad.md", reject_absolute=True)

    unterminated = _base_host(tmp_path, "unterminated")
    _write(
        unterminated / "docs" / "features" / "bad.md",
        "---\ntype: feature-spec\n# missing delimiter\n\n## Bad\n\nfaultmarker\n",
    )
    assert_invalid(unterminated, "docs/features/bad.md", reject_absolute=True)

    non_utf8 = _base_host(tmp_path, "non-utf8")
    (non_utf8 / "docs" / "features" / "bad.md").write_bytes(b"\xff\xfe")
    assert_invalid(non_utf8, "docs/features/bad.md", reject_absolute=True)

    unreadable = _base_host(tmp_path, "unreadable")
    unreadable_file = unreadable / "docs" / "features" / "bad.md"
    _write(unreadable_file, "# Fixture\n\n## Bad\n\nfaultmarker\n")
    unreadable_file.chmod(0)
    try:
        assert_invalid(
            unreadable,
            "docs/features/bad.md",
            reject_absolute=True,
        )
    finally:
        unreadable_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    unreadable_root = _base_host(tmp_path, "unreadable-root")
    specs_root = unreadable_root / "docs" / "features"
    specs_root.chmod(0)
    try:
        assert_invalid(unreadable_root, "docs/features")
    finally:
        specs_root.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    pattern_faults = {
        "pre-h1": "prose first\n\n# Pattern\n\nfaultmarker\n",
        "missing-h1": "## Pattern\n\nfaultmarker\n",
        "multiple-h1": "# One\n\nfaultmarker\n\n# Two\n",
    }
    for name, text in pattern_faults.items():
        host = _base_host(tmp_path, name)
        _write(
            host / "docs" / "features" / "valid.md",
            "# Fixture\n\n## Valid\n\nfaultmarker\n",
        )
        _write(host / "docs" / "patterns" / "bad.md", text)
        assert_invalid(host, "docs/patterns/bad.md", reject_absolute=True)

    # Fail-whole-query is recoverable: repairing the named document exposes
    # the previously suppressed valid hit on the next complete rebuild.
    _write(bad, "# Fixture\n\n## Repaired\n\nfaultmarker\n")
    code, envelope, _err = invoke(malformed)
    assert code == 0
    assert [hit["title"] for hit in envelope["data"]["hits"]] == ["Repaired"]


def test_ac06_manifest_help_and_no_workspace_discovery(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-6: one exact live manifest row; metadata help needs no project."""
    outside = tmp_path / "outside" / "nested"
    outside.mkdir(parents=True)
    monkeypatch.chdir(outside)

    code, human_help, _err = run_cli(["help"])
    assert code == 0
    assert human_help.count("\n  search") == 1

    code, manifest_out, _err = run_cli(["help", "--json"])
    assert code == 0
    manifest = envelope_tools.parse(manifest_out)["data"]
    rows = [row for row in manifest["commands"] if row["name"] == "search"]
    assert len(rows) == 1
    row = rows[0]
    assert row == {
        "name": "search",
        "summary": "search Feature Specs and Heddle patterns by intent",
        "mutating": False,
        "dry_run": False,
        "args": [
            {
                "name": "query",
                "summary": "non-empty natural AND-term query",
                "required": True,
            }
        ],
        "flags": [
            {
                "name": "--json",
                "summary": "emit the result envelope as JSON on stdout",
            },
            {
                "name": "--limit",
                "summary": "result count from 1 through 25 (default 5)",
            },
            {"name": "--titles-only", "summary": "omit result bodies"},
        ],
        "exit_codes": [0, 1, 2, 3],
        "output_schema": "heddle.search/v0",
        "input_schema": None,
    }

    code, command_help, _err = run_cli(["search", "--help"])
    assert code == 0
    assert "usage: heddle search <query> [flags]" in command_help
    assert "output schema: heddle.search/v0" in command_help

    code, command_json, _err = run_cli(["search", "--help", "--json"])
    assert code == 0
    assert envelope_tools.parse(command_json)["data"] == row

    probe_script = """
import contextlib
import io
import json
import sys
from heddle.cli import main

with (
    contextlib.redirect_stdout(io.StringIO()),
    contextlib.redirect_stderr(io.StringIO()),
):
    code = main(["search", "--help"])
print(json.dumps({
    "code": code,
    "core_loaded": "heddle.kernel.knowledge_search" in sys.modules,
}))
"""
    probe = subprocess.run(
        [sys.executable, "-c", probe_script],
        cwd=outside,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout) == {"code": 0, "core_loaded": False}, (
        "FAIL AC-6: metadata help must not import or execute the search core"
    )


def test_ac07_human_rendering_contract(
    run_cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-7: stable headers/bodies/separators and the exact no-hit marker."""
    host = _two_hit_host(tmp_path)
    monkeypatch.chdir(host)
    code, full, _err = run_cli(["search", "shared marker"])
    assert code == 0
    assert full == (
        "docs/features/a.md#alpha-signal — Alpha Signal\n"
        "\nshared marker body a\nA boundary sentinel\n"
        "---\n"
        "docs/patterns/b.md#beta-signal — Beta Signal\n"
        "\nshared marker body b\nB boundary sentinel\n"
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(full)

    code, titles, _err = run_cli(["search", "shared marker", "--titles-only"])
    assert code == 0
    assert titles == (
        "docs/features/a.md#alpha-signal — Alpha Signal\n"
        "docs/patterns/b.md#beta-signal — Beta Signal\n"
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(titles)

    code, empty, _err = run_cli(["search", "absentterm"])
    assert code == 0
    assert empty == "(no hits)\n"
    with pytest.raises(json.JSONDecodeError):
        json.loads(empty)


def test_ac08_json_payload_and_flag_position_contract(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-8: exact minimal projection across both global-JSON positions."""
    host = _two_hit_host(tmp_path)
    monkeypatch.chdir(host)

    expected_full = {
        "ok": True,
        "schema_version": "heddle.envelope/v2",
        "data": {
            "query": "shared marker",
            "hits": [
                {
                    "kind": "feature-spec-section",
                    "path": "docs/features/a.md",
                    "anchor": "alpha-signal",
                    "title": "Alpha Signal",
                    "body": "\nshared marker body a\nA boundary sentinel\n",
                },
                {
                    "kind": "pattern",
                    "path": "docs/patterns/b.md",
                    "anchor": "beta-signal",
                    "title": "Beta Signal",
                    "body": "\nshared marker body b\nB boundary sentinel\n",
                },
            ],
        },
        "diagnostics": [],
        "next_actions": [],
    }
    expected_titles = {
        **expected_full,
        "data": {
            "query": "shared marker",
            "hits": [
                {key: value for key, value in hit.items() if key != "body"}
                for hit in expected_full["data"]["hits"]
            ],
        },
    }
    variants = [
        (["--json", "search", "shared marker"], expected_full),
        (["search", "shared marker", "--json"], expected_full),
        (
            ["--json", "search", "shared marker", "--titles-only"],
            expected_titles,
        ),
        (
            ["search", "shared marker", "--titles-only", "--json"],
            expected_titles,
        ),
        (
            ["--json", "search", "shared marker", "--json", "--titles-only"],
            expected_titles,
        ),
    ]
    for argv, expected in variants:
        code, out, _err = run_cli(argv)
        assert code == 0
        assert envelope_tools.parse(out) == expected


def test_ac09_usage_gauntlet_and_fts5_failure_mapping(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-9: syntax is pre-I/O; FTS5 and unexpected defects stay structured."""
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    invalid_argv = [
        ["search", "--json"],
        ["search", "   ", "--json"],
        ["search", "one", "two", "--json"],
        ["search", "one", "--titles-only", "--titles-only", "--json"],
        ["search", "one", "--limit", "2", "--limit", "3", "--json"],
        ["search", "one", "--limit", "--json"],
        ["search", "one", "--unknown", "--json"],
        ["search", "one", "--limit", "0", "--json"],
        ["search", "one", "--limit", "26", "--json"],
        ["search", "one", "--limit", "many", "--json"],
    ]
    for argv in invalid_argv:
        code, out, _err = run_cli(argv)
        assert code == 2, f"FAIL AC-9: {argv!r} must fail as usage before I/O"
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"

    host = _base_host(tmp_path, "valid")
    _write(
        host / "docs" / "features" / "literal.md",
        """# Fixture

## Literal

OR foo"bar error* are ordinary authored terms.
""",
    )
    monkeypatch.chdir(host)
    for query in ("OR", 'foo"bar', "error*"):
        code, out, _err = run_cli(["search", query, "--json"])
        assert code == 0 and envelope_tools.parse(out)["data"]["hits"]
    code, out, _err = run_cli(["search", "***", "--json"])
    assert code == 0 and envelope_tools.parse(out)["data"]["hits"] == []
    code, out, _err = run_cli(["--json", "search", "OR", "--json", "--titles-only"])
    assert code == 0 and envelope_tools.parse(out)["ok"] is True

    search_module = _search_module()
    real_connect = sqlite3.connect

    class RejectFtsCursor:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def execute(self, sql, parameters=()):
            if "CREATE VIRTUAL TABLE" in sql.upper():
                raise sqlite3.OperationalError("no such module: fts5")
            return self._wrapped.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    class RejectFtsConnection:
        def __init__(self):
            self._wrapped = real_connect(":memory:")

        def execute(self, sql, parameters=()):
            return RejectFtsCursor(self._wrapped.cursor()).execute(sql, parameters)

        def cursor(self):
            return RejectFtsCursor(self._wrapped.cursor())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._wrapped.close()
            return False

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    monkeypatch.setattr(
        search_module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: RejectFtsConnection(),
    )
    code, out, _err = run_cli(["search", "OR", "--json"])
    assert code == 1
    fts_failure = envelope_tools.parse(out)
    assert fts_failure["error"]["code"] == "internal"
    fts_text = (
        f"{fts_failure['error']['message']} {fts_failure['error']['hint']}".lower()
    )
    assert "fts5" in fts_text and ("install" in fts_text or "python" in fts_text)

    runtime_search = importlib.import_module("heddle.runtime.search")

    def explode(*_args, **_kwargs):
        raise RuntimeError("private injected detail")

    monkeypatch.setattr(runtime_search, "search_knowledge", explode)
    code, out, err = run_cli(["search", "OR", "--json"])
    assert code == 1
    unexpected = envelope_tools.parse(out)
    assert unexpected["error"]["code"] == "internal"
    assert "private injected detail" not in out
    assert "private injected detail" in err
