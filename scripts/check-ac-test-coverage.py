#!/usr/bin/env python3
"""Check that every AC ID in a Feature Spec has at least one test reference.

Standalone usage:
    poetry run python scripts/check-ac-test-coverage.py <spec-path> [test-dir ...]

Pre-commit mode (no arguments):
    Checks active native workspace bindings affected by staged tests/specs,
    plus explicitly changed historical Verified-by lines.

Exit codes:
    0 - all selected AC bindings are valid (or no relevant staged files)
    1 - one or more ACs missing test coverage

Bypass: ALLOW_MISSING_AC_TEST_COVERAGE=1
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

from heddle.kernel.test_bindings import (
    PythonSymbolInspection,
    inspect_python_test_source,
    parse_primary_test_bindings,
    parse_primary_test_target,
    resolve_primary_test_binding,
)


class CoverageMatch:
    __slots__ = ("source", "references")

    def __init__(self, source: str, references: list[str]):
        self.source = source
        self.references = references


class CoverageResult:
    __slots__ = ("covered", "uncovered", "issues", "stale_verified_by")

    def __init__(
        self,
        covered: dict[str, CoverageMatch],
        uncovered: list[str],
        issues: dict[str, str],
        stale_verified_by: list[str],
    ):
        self.covered = covered
        self.uncovered = uncovered
        self.issues = issues
        self.stale_verified_by = stale_verified_by


def _git(*args: str) -> str:
    # subprocess-handler: allow git helper; pre-commit surfaces git failures
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def _extract_ac_blocks(spec_path: str) -> list[tuple[str, str]]:
    content = Path(spec_path).read_text(encoding="utf-8")
    lines = content.splitlines()
    headings: list[tuple[str, int]] = []
    for idx, line in enumerate(lines):
        match = re.match(r"^###\s+(AC-\d+[a-z]?):\s*(.*)$", line)
        if not match:
            continue
        if match.group(2).strip().lower().startswith("[removed"):
            continue
        headings.append((match.group(1), idx))

    blocks: list[tuple[str, str]] = []
    for idx, (ac_id, start_idx) in enumerate(headings):
        end_idx = headings[idx + 1][1] if idx + 1 < len(headings) else len(lines)
        blocks.append((ac_id, "\n".join(lines[start_idx + 1 : end_idx]).strip()))
    return blocks


def _extract_ac_ids(spec_path: str) -> list[str]:
    return [ac_id for ac_id, _body in _extract_ac_blocks(spec_path)]


def _extract_verified_by(ac_body: str) -> list[str]:
    parsed = parse_primary_test_bindings("", ac_body)
    return [binding.target for binding in parsed.bindings]


def _find_test_files(test_dir: str) -> list[str]:
    test_path = Path(test_dir)
    if test_path.is_file():
        return [str(test_path)] if test_path.suffix == ".py" else []
    if not test_path.is_dir():
        return []
    files: list[str] = []
    for pattern in ("test_*.py", "*_test.py"):
        files.extend(str(path) for path in test_path.rglob(pattern))
    return sorted(set(files))


def _search_tests_for_ac(test_files: list[str], ac_id: str) -> list[str]:
    ac_num = ac_id.split("-", 1)[1] if "-" in ac_id else ac_id
    patterns = [
        re.compile(
            rf"(?<![0-9A-Za-z]){re.escape(ac_id)}(?![0-9A-Za-z])",
            re.IGNORECASE,
        ),
        re.compile(rf"(?<![a-z0-9])ac[_-]?{re.escape(ac_num)}(?![0-9a-z])"),
        re.compile(rf"(?<![A-Za-z0-9])AC[_-]?{re.escape(ac_num)}(?![0-9A-Za-z])"),
        re.compile(rf"TestAC{re.escape(ac_num)}(?![0-9a-z])"),
    ]
    matches: list[str] = []
    for path_str in test_files:
        try:
            content = Path(path_str).read_text(encoding="utf-8")
        except OSError:
            continue
        for pattern in patterns:
            if pattern.search(content):
                matches.append(path_str)
                break
    return matches


def _resolve_verified_by_targets(
    targets: list[str],
) -> tuple[list[str], list[str], list[str]]:
    valid_targets: list[str] = []
    broken_targets: list[str] = []
    referenced_files: list[str] = []
    symbol_cache: dict[Path, PythonSymbolInspection] = {}

    for target in targets:
        binding = parse_primary_test_target("", target)
        target_path = Path(binding.path)
        referenced_files.append(str(target_path))
        if not binding.path or not target_path.is_file():
            broken_targets.append(target)
            continue
        if target_path not in symbol_cache:
            try:
                symbol_cache[target_path] = inspect_python_test_source(
                    target_path.read_bytes()
                )
            except OSError:
                broken_targets.append(target)
                symbol_cache[target_path] = PythonSymbolInspection(
                    None, "source could not be read"
                )
                continue
        resolved = resolve_primary_test_binding(
            binding,
            symbol_cache[target_path],
            policy="repository",
        )
        if resolved.issue is not None:
            broken_targets.append(target)
            continue
        valid_targets.append(target)

    return valid_targets, broken_targets, referenced_files


def _staged_paths() -> list[str]:
    # Treat renames as deletion + addition so both binding paths are checked.
    output = _git(
        "diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ADM", "-z"
    )
    return [path for path in output.split("\0") if path]


def _staged_test_files() -> list[str]:
    try:
        return [
            path
            for path in _staged_paths()
            if path.endswith(".py") and path.startswith(("tests/", "src/tests/"))
        ]
    except subprocess.SubprocessError:
        # Standalone AST checks also work outside Git. Pre-commit calls the
        # strict staged-path reader first and reports Git failures.
        return []


def _collect_stale_verified_by_notes(
    staged_files: list[str],
    covered: dict[str, CoverageMatch],
) -> list[str]:
    staged_set = {Path(path).as_posix() for path in staged_files}
    notes: list[str] = []
    for ac_id, match in sorted(covered.items()):
        if match.source != "verified_by":
            continue
        for target in match.references:
            file_part, _separator, _function_name = target.partition("::")
            if Path(file_part).as_posix() in staged_set:
                notes.append(
                    f"{ac_id}: staged test file matches Verified-by target {target}"
                )
    return notes


def check_coverage(spec_path: str, test_dirs: list[str]) -> CoverageResult:
    ac_blocks = _extract_ac_blocks(spec_path)
    if not ac_blocks:
        return CoverageResult({}, [], {}, [])

    test_files: list[str] = []
    for test_dir in test_dirs:
        test_files.extend(_find_test_files(test_dir))
    test_files = sorted(set(test_files))

    covered: dict[str, CoverageMatch] = {}
    uncovered: list[str] = []
    issues: dict[str, str] = {}

    for ac_id, ac_body in ac_blocks:
        verified_targets = _extract_verified_by(ac_body)
        if verified_targets:
            valid_targets, broken_targets, _referenced_files = (
                _resolve_verified_by_targets(verified_targets)
            )
            if broken_targets or not valid_targets:
                issues[ac_id] = "broken Verified-by target(s): " + ", ".join(
                    broken_targets or verified_targets
                )
                uncovered.append(ac_id)
                continue
            covered[ac_id] = CoverageMatch("verified_by", valid_targets)
            continue

        regex_matches = _search_tests_for_ac(test_files, ac_id)
        if regex_matches:
            covered[ac_id] = CoverageMatch("regex", regex_matches)
        else:
            issues[ac_id] = "no test references found"
            uncovered.append(ac_id)

    stale_notes = _collect_stale_verified_by_notes(_staged_test_files(), covered)
    return CoverageResult(covered, uncovered, issues, stale_notes)


def _native_workspace_metadata() -> list[tuple[Path, dict[str, str]]]:
    """Read repository binding metadata without decoding operational authority."""
    workspaces = []
    for path in sorted(Path("plans").glob("*/state.yaml")):
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path}: native workspace metadata must be a mapping")
        stage, spec = value.get("stage"), value.get("spec")
        if not isinstance(stage, str) or not isinstance(spec, str) or not spec:
            raise ValueError(f"{path}: native metadata needs stage and spec strings")
        commands = value.get("commands", {})
        if not isinstance(commands, dict) or any(
            not isinstance(command, str) for command in commands.values()
        ):
            raise ValueError(f"{path}: commands metadata must map names to strings")
        # Accepted v6 workspaces are history. This is metadata selection, not
        # an alternate terminal decoder or a legacy session-marker interpreter.
        workspaces.append(
            (
                path,
                {
                    **commands,
                    "feature_spec": spec,
                    "stage": stage,
                    "accepted": "yes" if value.get("completion") is not None else "no",
                },
            )
        )
    return workspaces


def _changed_verified_by_lines(path: str) -> set[str]:
    diff = _git("diff", "--cached", "--unified=0", "--no-ext-diff", "--", path)
    return {line[1:] for line in diff.splitlines() if line.startswith("+Verified-by:")}


def _infer_test_dirs(metadata: dict[str, str]) -> list[str]:
    dirs: set[str] = set()
    for key in ("test_command", "acceptance_test", "live_e2e_test"):
        command = metadata.get(key, "")
        for token in shlex.split(command):
            part = token.partition("::")[0]
            if part in {"src/tests", "tests"} or part.startswith(
                ("src/tests/", "tests/")
            ):
                path = Path(part)
                dirs.add(str(path.parent if path.suffix == ".py" else path))
    return sorted(dirs) if dirs else ["src/tests", "tests"]


def _display_path(path_str: str) -> str:
    return os.path.relpath(path_str, start=Path.cwd())


def _display_verified_target(target: str) -> str:
    file_part, separator, function_name = target.partition("::")
    if not separator:
        return target
    return f"{_display_path(file_part)}::{function_name}"


def _check_standalone(spec_path: str, test_dirs: list[str]) -> int:
    if not Path(spec_path).is_file():
        print(f"ERROR: Feature Spec not found at {spec_path}")
        return 1

    result = check_coverage(spec_path, test_dirs)

    if not result.covered and not result.uncovered:
        print(f"No AC IDs found in {spec_path}")
        return 0

    total = len(result.covered) + len(result.uncovered)
    print(f"AC Coverage: {len(result.covered)}/{total} ACs have test references")
    for ac_id, match in sorted(result.covered.items()):
        if match.source == "verified_by":
            refs = ", ".join(_display_verified_target(ref) for ref in match.references)
            print(f"  + {ac_id}: linked via Verified-by -> {refs}")
        else:
            refs = ", ".join(_display_path(ref) for ref in match.references)
            print(f"  + {ac_id}: found via regex -> {refs}")

    for note in result.stale_verified_by:
        print(f"  ~ NOTE: {note}")

    if result.uncovered:
        print()
        print("ERROR: The following ACs have no test references:")
        for ac_id in result.uncovered:
            print(
                f"  - {ac_id}: {result.issues.get(ac_id, 'no test references found')}"
            )
        print()
        print(
            "Fix: Add a valid Verified-by target or reference these AC IDs "
            "in test names, docstrings, comments, or pytest markers."
        )
        print()
        print("To bypass: ALLOW_MISSING_AC_TEST_COVERAGE=1")
        return 1

    print("\nAll ACs have test coverage.")
    return 0


def _check_precommit() -> int:
    try:
        staged = _staged_paths()
        if not staged:
            return 0
        staged_tests = [
            path
            for path in staged
            if path.endswith(".py") and path.startswith(("tests/", "src/tests/"))
        ]
        metadata = _native_workspace_metadata()
        early_specs = {
            item["feature_spec"]
            for _path, item in metadata
            if item["stage"] in {"specify", "spec-review", "plan-review"}
        }
        checked = set()
        violations = []
        for workspace, item in metadata:
            if (
                item["stage"]
                not in {
                    "scaffold",
                    "implement",
                    "peer-review",
                    "robustness",
                    "complete",
                }
                or item["accepted"] == "yes"
            ):
                continue
            spec = item["feature_spec"]
            test_dirs = _infer_test_dirs(item)
            explicit_files = (
                {
                    _display_path(target.partition("::")[0])
                    for _ac, body in _extract_ac_blocks(spec)
                    for target in _extract_verified_by(body)
                }
                if Path(spec).is_file()
                else set()
            )
            relevant = (
                spec in staged
                or bool(explicit_files.intersection(staged))
                or any(
                    Path(path).is_relative_to(directory)
                    for path in staged_tests
                    for directory in test_dirs
                )
            )
            if not relevant:
                continue
            checked.add(spec)
            if not Path(spec).is_file():
                violations.append(f"{workspace}: Feature Spec not found: {spec}")
                continue
            # Missing/deleted test directories must remain in the check.
            result = check_coverage(spec, test_dirs)
            violations.extend(
                f"{workspace}: {spec}: {ac_id} {result.issues[ac_id]}"
                for ac_id in result.uncovered
            )
        for spec in staged:
            if (
                not spec.endswith(".md")
                or spec in checked
                or spec in early_specs
                or not Path(spec).is_file()
            ):
                continue
            changed = _changed_verified_by_lines(spec)
            if not changed:
                continue
            for ac_id, body in _extract_ac_blocks(spec):
                if not changed.intersection(body.splitlines()):
                    continue
                targets = _extract_verified_by(body)
                valid, broken, _files = _resolve_verified_by_targets(targets)
                if broken or not valid:
                    violations.append(
                        f"{spec}: {ac_id} broken Verified-by target(s): "
                        + ", ".join(broken or targets)
                    )
    except (
        OSError,
        ValueError,
        yaml.YAMLError,
        subprocess.SubprocessError,
    ) as error:
        print(f"ERROR: AC binding discovery failed: {error}")
        return 1

    if not violations:
        return 0
    print("ERROR: Staged changes leave ACs without valid test bindings.")
    print("Rule: active scaffold and later bindings must resolve; specification")
    print("placeholders remain permitted before scaffolding. Explicitly changed")
    print("historical Verified-by lines must also resolve through the AST checker.")
    for violation in violations:
        print(f"  - {violation}")
    print("Fix: repair the named test binding. ALLOW_MISSING_AC_TEST_COVERAGE=1")
    print("requires a documented bypass justification.")
    return 1


def main() -> int:
    if os.environ.get("ALLOW_MISSING_AC_TEST_COVERAGE") == "1":
        return 0

    if len(sys.argv) >= 2:
        spec_path = sys.argv[1]
        test_dirs = sys.argv[2:] if len(sys.argv) > 2 else ["tests"]
        return _check_standalone(spec_path, test_dirs)

    return _check_precommit()


if __name__ == "__main__":
    raise SystemExit(main())
