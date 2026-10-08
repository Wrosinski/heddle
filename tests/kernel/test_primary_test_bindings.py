"""Pure primary-binding parsing and selector policy."""

from __future__ import annotations

from pathlib import Path

from heddle.kernel.knowledge import (
    read_spec_verification_facts,
    read_spec_verified_by_lines,
)
from heddle.kernel.test_bindings import (
    inspect_python_test_source,
    parse_primary_test_bindings,
    resolve_primary_test_binding,
    selector_rebind_pairs,
    unread_verified_by_lines,
    unselected_test_targets,
)


def _resolutions(markdown: str, source: bytes):
    parsed = parse_primary_test_bindings("AC-1", markdown)
    inspection = inspect_python_test_source(source)
    return parsed, tuple(
        resolve_primary_test_binding(binding, inspection, policy="native")
        for binding in parsed.bindings
    )


def test_symbol_resolution_preserves_distinct_selector_kinds() -> None:
    source = b"""
def test_same_name():
    return True

async def test_async_module():
    return True

class TestFeature:
    def test_same_name(self):
        return True

    async def test_async_method(self):
        return True
"""
    parsed, resolved = _resolutions(
        "Verified-by: tests/test_x.py::test_same_name, "
        "tests/test_x.py::test_async_module, "
        "tests/test_x.py::TestFeature::test_async_method",
        source,
    )

    assert not parsed.issues
    assert [row.kind for row in resolved] == [
        "module-function",
        "module-function",
        "qualified-method",
    ]
    assert all(row.issue is None for row in resolved)


def test_native_and_repository_policies_share_inventory_but_differ_on_legacy() -> None:
    source = b"""
class TestFeature:
    def test_behavior(self):
        return True
"""
    parsed = parse_primary_test_bindings(
        "AC-7",
        "Verified-by: tests/test_x.py::TestFeature, tests/test_x.py::test_behavior",
    )
    inspection = inspect_python_test_source(source)

    native = [
        resolve_primary_test_binding(binding, inspection, policy="native")
        for binding in parsed.bindings
    ]
    repository = [
        resolve_primary_test_binding(binding, inspection, policy="repository")
        for binding in parsed.bindings
    ]

    assert [row.issue.reason for row in native if row.issue] == [
        "class-only selector; use a module function or a qualified method",
        "bare method selector; qualify it with its Test class",
    ]
    assert [row.kind for row in repository] == ["class", "bare-method"]


def test_every_declared_target_is_resolved_and_broken_targets_are_not_rescued() -> None:
    parsed, resolved = _resolutions(
        "Verified-by: tests/test_x.py::test_ok, tests/test_x.py::test_missing",
        b"def test_ok():\n    return True\n",
    )

    assert [binding.target for binding in parsed.bindings] == [
        "tests/test_x.py::test_ok",
        "tests/test_x.py::test_missing",
    ]
    assert resolved[0].issue is None
    assert resolved[1].issue is not None
    assert (
        resolved[1].issue.reason == "selector does not resolve in the referenced file"
    )


def test_every_declaration_line_in_an_ac_block_binds() -> None:
    parsed, resolved = _resolutions(
        "Verified-by: tests/test_x.py::test_unit\n"
        "Prose between declarations.\n"
        "Verified-by:\n"
        "Verified-by: tests/test_x.py::test_lane, tests/test_x.py::test_unit\n",
        b"def test_unit():\n    return True\n",
    )

    assert [binding.target for binding in parsed.bindings] == [
        "tests/test_x.py::test_unit",
        "tests/test_x.py::test_lane",
    ]
    assert parsed.issues == ()
    assert resolved[0].issue is None
    assert resolved[1].issue is not None
    assert resolved[1].issue.target == "tests/test_x.py::test_lane"

    later_only = parse_primary_test_bindings(
        "AC-2", "Verified-by:\nVerified-by: tests/test_x.py::test_lane"
    )
    assert [binding.target for binding in later_only.bindings] == [
        "tests/test_x.py::test_lane"
    ]
    assert later_only.issues == ()


def test_fenced_example_declarations_never_bind() -> None:
    example = "```markdown\nVerified-by: tests/test_example.py::test_example\n```\n"

    after = parse_primary_test_bindings(
        "AC-1", f"Verified-by: tests/test_x.py::test_unit\n\n{example}"
    )
    before = parse_primary_test_bindings(
        "AC-2", f"{example}\nVerified-by: tests/test_x.py::test_unit"
    )
    only = parse_primary_test_bindings("AC-3", example)

    assert [binding.target for binding in after.bindings] == [
        "tests/test_x.py::test_unit"
    ]
    assert [binding.target for binding in before.bindings] == [
        "tests/test_x.py::test_unit"
    ]
    assert only.bindings == ()
    assert [issue.reason for issue in only.issues] == ["missing primary binding"]


def test_missing_and_unsupported_declarations_have_stable_issues() -> None:
    missing = parse_primary_test_bindings("AC-2", "No declaration here.")
    assert missing.bindings == ()
    assert missing.issues[0].target == "(missing)"
    assert missing.issues[0].reason == "missing primary binding"

    empty_line = parse_primary_test_bindings(
        "AC-3",
        "Verified-by:\nquality/test_x.py::test_ok",
    )
    assert empty_line.bindings == ()
    assert len(empty_line.issues) == 1
    assert empty_line.issues[0].ac_id == "AC-3"
    assert empty_line.issues[0].target == "(missing)"
    assert empty_line.issues[0].reason == "missing primary binding"

    horizontal_whitespace = parse_primary_test_bindings(
        "AC-4", "Verified-by:\tquality/test_x.py::test_ok"
    )
    assert [binding.target for binding in horizontal_whitespace.bindings] == [
        "quality/test_x.py::test_ok"
    ]
    assert horizontal_whitespace.issues == ()

    parsed, resolved = _resolutions(
        "Verified-by: tests/test_x.py, tests/test_x.py::test_ok[case]",
        b"def test_ok():\n    return True\n",
    )
    assert not parsed.issues
    assert [row.issue.reason for row in resolved if row.issue] == [
        "file-only target; use a module function or a qualified method",
        "parameter-ID selector is unsupported; use the test function or method",
    ]


def test_source_is_inspected_without_execution_and_invalid_bytes_are_reported() -> None:
    parsed = parse_primary_test_bindings(
        "AC-1", "Verified-by: tests/test_x.py::test_behavior"
    )
    source = (
        b"raise AssertionError('must not execute')\n\ndef test_behavior():\n    pass\n"
    )
    valid = resolve_primary_test_binding(
        parsed.bindings[0], inspect_python_test_source(source), policy="native"
    )
    invalid_utf8 = resolve_primary_test_binding(
        parsed.bindings[0], inspect_python_test_source(b"\xff"), policy="native"
    )
    invalid_python = resolve_primary_test_binding(
        parsed.bindings[0],
        inspect_python_test_source(b"def broken(:\n"),
        policy="native",
    )

    assert valid.kind == "module-function" and valid.issue is None
    assert invalid_utf8.issue is not None
    assert invalid_utf8.issue.reason == "source is not valid UTF-8"
    assert invalid_python.issue is not None
    assert invalid_python.issue.reason == "source is not valid Python"


def test_selector_rebind_pairs_only_same_file_selector_changes() -> None:
    current = "runner fast tests/test_a.py tests/test_b.py::test_old 'x y'"

    assert selector_rebind_pairs(
        current, "runner fast tests/test_a.py tests/test_b.py::test_new 'x y'"
    ) == (("tests/test_b.py::test_old", "tests/test_b.py::test_new"),)
    assert selector_rebind_pairs(current, current) is None
    assert (
        selector_rebind_pairs(
            current, "runner fast tests/test_a.py tests/test_c.py::test_old 'x y'"
        )
        is None
    )
    assert (
        selector_rebind_pairs(
            current, "runner slow tests/test_a.py tests/test_b.py::test_new 'x y'"
        )
        is None
    )
    assert (
        selector_rebind_pairs(current, "runner fast tests/test_b.py::test_new 'x y'")
        is None
    )
    assert selector_rebind_pairs(current, "runner 'unterminated") is None


def test_lookalike_declarations_are_unread_by_every_reader_and_reported(
    tmp_path: Path,
) -> None:
    body = (
        "### AC-1: Read\n"
        "\n"
        "Verified-by: tests/test_read.py::test_read\n"
        "- Verified-by: tests/test_list.py::test_list\n"
        "  Verified-by: tests/test_indent.py::test_indent\n"
        "verified-by: tests/test_case.py::test_case\n"
        "Verified by: tests/test_space.py::test_space\n"
        "1. **Verified-by:** tests/test_bold.py::test_bold\n"
        "> Verified-by : tests/test_quote.py::test_quote\n"
        "```text\n"
        "- Verified-by: tests/test_fenced.py::test_fenced\n"
        "```\n"
        "Prose may name the `Verified-by:` line.\n"
        "Verified by running the suite.\n"
    )
    spec = tmp_path / "spec.md"
    spec.write_text("---\nlifecycle: active\n---\n" + body, encoding="utf-8")

    assert unread_verified_by_lines(body) == (4, 5, 6, 7, 8, 9)
    assert read_spec_verified_by_lines(spec).unread_lines == (7, 8, 9, 10, 11, 12)
    assert read_spec_verification_facts(spec).verified_by == ("tests/test_read.py",)
    parsed = parse_primary_test_bindings("AC-1", body)
    assert [binding.target for binding in parsed.bindings] == [
        "tests/test_read.py::test_read"
    ]
    bulleted_only = parse_primary_test_bindings(
        "AC-2", "- Verified-by: tests/test_list.py::test_list"
    )
    assert bulleted_only.bindings == ()
    assert bulleted_only.issues[0].reason == "missing primary binding"


def test_target_selection_is_judged_from_command_tokens_alone() -> None:
    targets = (
        "tests/test_a.py::test_exact",
        "tests/test_b.py::test_file",
        "tests/unit/test_c.py::test_parent",
        "tests/test_d.py::TestD::test_prefix",
        "tests/test_e.py::TestE::test_narrowed",
        "./tests/test_f.py::test_filtered",
        "tests/test_g.py::test_unselected",
    )
    commands = (
        "pytest tests/test_a.py::test_exact ./tests/test_b.py -q",
        "python -m pytest tests/unit/ 'tests/test_d.py::TestD' "
        "&& pytest 'tests/test_e.py::TestE::test_narrowed[case]' "
        "tests/test_f.py -k 'not slow' -m fast --deselect tests/test_f.py::other",
    )

    assert unselected_test_targets(targets, commands, tests_root="tests") == (
        "tests/test_g.py::test_unselected",
    )
    for unjudged in (
        "make acceptance",
        "pytest -k flow",
        "pytest tests/test_a.py && scripts/run-all.sh",
        "pytest 'tests/test_g.py",
        "pytest tests/test_*.py",
        "pytest . tests/test_a.py",
    ):
        assert (
            unselected_test_targets(targets, (*commands, unjudged), tests_root="tests")
            == ()
        )
    excluding = (
        "pytest tests/test_z.py --deselect tests/test_g.py::test_unselected "
        "--ignore=tests/test_g.py --ignore-glob 'tests/test_g*.py'",
    )
    for root in ("tests", "tests/", "./tests"):
        assert unselected_test_targets(targets[-1:], excluding, tests_root=root) == (
            "tests/test_g.py::test_unselected",
        )
