"""Pure parsing and Python symbol resolution for primary test bindings."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Literal

BindingPolicy = Literal["native", "repository"]
SelectorKind = Literal["module-function", "class", "qualified-method", "bare-method"]

_VERIFIED_BY = re.compile(r"^Verified-by:[ \t]*(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class PrimaryTestBinding:
    ac_id: str
    target: str
    path: str
    selector: str


@dataclass(frozen=True)
class TestBindingIssue:
    ac_id: str
    target: str
    reason: str


@dataclass(frozen=True)
class ParsedTestBindings:
    bindings: tuple[PrimaryTestBinding, ...]
    issues: tuple[TestBindingIssue, ...]


@dataclass(frozen=True)
class PythonSymbolInventory:
    module_functions: frozenset[str]
    classes: frozenset[str]
    qualified_methods: frozenset[str]
    bare_methods: frozenset[str]


@dataclass(frozen=True)
class PythonSymbolInspection:
    inventory: PythonSymbolInventory | None
    issue: str | None


@dataclass(frozen=True)
class TestBindingResolution:
    binding: PrimaryTestBinding
    kind: SelectorKind | None
    issue: TestBindingIssue | None


def parse_primary_test_bindings(ac_id: str, ac_block: str) -> ParsedTestBindings:
    """Parse the established comma-separated ``Verified-by`` declaration."""
    match = _VERIFIED_BY.search(ac_block)
    if match is None or not match.group(1).strip():
        return ParsedTestBindings(
            (),
            (
                TestBindingIssue(
                    ac_id,
                    "(missing)",
                    "missing primary binding",
                ),
            ),
        )
    targets = tuple(part.strip() for part in match.group(1).split(",") if part.strip())
    if not targets:
        return ParsedTestBindings(
            (),
            (TestBindingIssue(ac_id, "(missing)", "missing primary binding"),),
        )
    return ParsedTestBindings(
        tuple(parse_primary_test_target(ac_id, target) for target in targets),
        (),
    )


def parse_primary_test_target(ac_id: str, target: str) -> PrimaryTestBinding:
    """Split one target without imposing a caller's selector policy."""
    path, separator, selector = target.partition("::")
    return PrimaryTestBinding(
        ac_id=ac_id,
        target=target,
        path=path.strip(),
        selector=selector.strip() if separator else "",
    )


def inspect_python_test_source(source: bytes) -> PythonSymbolInspection:
    """Discover supported top-level symbols from supplied bytes without execution."""
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        return PythonSymbolInspection(None, "source is not valid UTF-8")
    try:
        module = ast.parse(text)
    except (SyntaxError, ValueError):
        return PythonSymbolInspection(None, "source is not valid Python")

    module_functions: set[str] = set()
    classes: set[str] = set()
    qualified_methods: set[str] = set()
    bare_methods: set[str] = set()
    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            module_functions.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            classes.add(node.name)
            for inner in node.body:
                if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef):
                    qualified_methods.add(f"{node.name}::{inner.name}")
                    bare_methods.add(inner.name)
    return PythonSymbolInspection(
        PythonSymbolInventory(
            frozenset(module_functions),
            frozenset(classes),
            frozenset(qualified_methods),
            frozenset(bare_methods),
        ),
        None,
    )


def resolve_primary_test_binding(
    binding: PrimaryTestBinding,
    inspection: PythonSymbolInspection,
    *,
    policy: BindingPolicy,
) -> TestBindingResolution:
    """Resolve one selector under native-strict or repository-legacy policy."""
    if not binding.path:
        return _failure(binding, "missing referenced file path")
    if not binding.selector:
        return _failure(
            binding,
            "file-only target; use a module function or a qualified method",
        )
    if "[" in binding.selector or "]" in binding.selector:
        return _failure(
            binding,
            "parameter-ID selector is unsupported; use the test function or method",
        )
    if inspection.issue is not None or inspection.inventory is None:
        return _failure(binding, inspection.issue or "source could not be inspected")

    inventory = inspection.inventory
    kind: SelectorKind | None = None
    if binding.selector in inventory.module_functions:
        kind = "module-function"
    elif binding.selector in inventory.classes:
        kind = "class"
    elif binding.selector in inventory.qualified_methods:
        kind = "qualified-method"
    elif "::" not in binding.selector and binding.selector in inventory.bare_methods:
        kind = "bare-method"
    if kind is None:
        return _failure(binding, "selector does not resolve in the referenced file")
    if policy == "native" and kind == "class":
        return _failure(
            binding,
            "class-only selector; use a module function or a qualified method",
        )
    if policy == "native" and kind == "bare-method":
        return _failure(
            binding,
            "bare method selector; qualify it with its Test class",
        )
    return TestBindingResolution(binding, kind, None)


def _failure(binding: PrimaryTestBinding, reason: str) -> TestBindingResolution:
    return TestBindingResolution(
        binding,
        None,
        TestBindingIssue(binding.ac_id, binding.target, reason),
    )
