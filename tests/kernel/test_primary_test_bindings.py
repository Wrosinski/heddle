"""Pure primary-binding parsing and selector policy."""

from __future__ import annotations

from heddle.kernel.test_bindings import (
    inspect_python_test_source,
    parse_primary_test_bindings,
    resolve_primary_test_binding,
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
