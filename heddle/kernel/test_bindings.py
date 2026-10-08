"""Pure parsing and Python symbol resolution for primary test bindings."""

from __future__ import annotations

import ast
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

BindingPolicy = Literal["native", "repository"]
SelectorKind = Literal["module-function", "class", "qualified-method", "bare-method"]

# The one canonical declaration every Verified-by reader uses: an unindented
# line with this exact spelling and comma-separated targets.
VERIFIED_BY = re.compile(r"^Verified-by:[ \t]*(.*)$", re.MULTILINE)
# Line-start text that reads as a declaration in another casing, spacing,
# indentation, quote or list form. VERIFIED_BY silently skips these lines.
_VERIFIED_BY_LOOKALIKE = re.compile(
    r"^[ \t]*(?:>[ \t]*)*(?:(?:[-*+]|\d+[.)])[ \t]+)?[*_]*"
    r"verified[ \t_-]*by[*_]*[ \t]*:",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_CONTROL_OPERATORS = frozenset({"&&", "||", ";", ";;", "|", "|&", "&", "(", ")"})
# Options whose value names tests to leave out; that value never selects.
_EXCLUDING_OPTIONS = ("--deselect", "--ignore", "--ignore-glob")
_GLOB_CHARACTERS = frozenset("*?[")


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
    """Parse every ``Verified-by`` line's comma-separated targets, in order.

    Each line adds its targets to the AC's binding; a repeated target binds
    once, at its first occurrence. Fenced code is example text and never binds.
    """
    targets = tuple(
        dict.fromkeys(
            target
            for _number, line in _unfenced_lines(ac_block)
            if (match := VERIFIED_BY.match(line)) is not None
            for target in _declared_targets(match.group(1))
        )
    )
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


def _declared_targets(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def verified_by_targets(markdown: str) -> tuple[tuple[int, str], ...]:
    """Return every target VERIFIED_BY reads, paired with its 1-based line."""
    return tuple(
        (markdown.count("\n", 0, match.start()) + 1, target)
        for match in VERIFIED_BY.finditer(markdown)
        for target in _declared_targets(match.group(1))
    )


def unread_verified_by_lines(markdown: str) -> tuple[int, ...]:
    """Return 1-based lines that look like declarations VERIFIED_BY skips.

    Fenced code is example text, so its lines are never reported.
    """
    return tuple(
        number
        for number, line in _unfenced_lines(markdown)
        if _VERIFIED_BY_LOOKALIKE.match(line) and not VERIFIED_BY.match(line)
    )


def _unfenced_lines(markdown: str) -> Iterator[tuple[int, str]]:
    """Yield 1-based numbered lines outside fenced code and its delimiters."""
    fence_marker: str | None = None
    fence_size = 0
    for number, line in enumerate(markdown.splitlines(), start=1):
        delimiter = _FENCE.match(line)
        if fence_marker is not None:
            if (
                delimiter is not None
                and delimiter.group(1)[0] == fence_marker
                and len(delimiter.group(1)) >= fence_size
            ):
                fence_marker = None
            continue
        if delimiter is not None:
            fence_marker = delimiter.group(1)[0]
            fence_size = len(delimiter.group(1))
            continue
        yield number, line


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


def selector_rebind_pairs(
    current: str, supplied: str
) -> tuple[tuple[str, str], ...] | None:
    """Pair the test targets a rewrite re-points within their own files.

    Returns ``None`` unless the two shell lines have the same tokens except for
    at least one ``path::selector`` target whose path is unchanged.
    """
    try:
        before = shlex.split(current)
        after = shlex.split(supplied)
    except ValueError:
        return None
    if len(before) != len(after):
        return None
    pairs: list[tuple[str, str]] = []
    for old, new in zip(before, after, strict=True):
        if old == new:
            continue
        old_path, old_separator, old_selector = old.partition("::")
        new_path, new_separator, new_selector = new.partition("::")
        if not (
            old_separator
            and new_separator
            and old_path
            and old_path == new_path
            and old_selector
            and new_selector
        ):
            return None
        pairs.append((old, new))
    return tuple(pairs) or None


def unselected_test_targets(
    targets: tuple[str, ...], commands: tuple[str, ...], *, tests_root: str
) -> tuple[str, ...]:
    """Return the targets no command can select, judged from shell tokens only.

    A token selects a target it equals, the target's file, a parent directory
    of that file, a node prefix of it, or a narrower node inside it; ``.``
    selects every target. Filters such as ``-k`` and ``-m`` only narrow a
    selection, so a covered target stays possibly selected, and the values of
    ``--deselect``, ``--ignore`` and ``--ignore-glob`` never select. A command
    segment with no token under ``tests_root`` (a make target, a script) could
    select anything, and an unparseable command or a path the shell would
    expand as a glob cannot be judged; either way nothing is returned.
    """
    root = _shell_path(tests_root)
    tokens: list[str] = []
    for command in commands:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            words = list(lexer)
        except ValueError:
            return ()
        segment: list[str] = []
        excluded_value = False
        for word in [*words, ";"]:
            if word not in _CONTROL_OPERATORS:
                if excluded_value or word.startswith(
                    tuple(f"{option}=" for option in _EXCLUDING_OPTIONS)
                ):
                    excluded_value = False
                    continue
                excluded_value = word in _EXCLUDING_OPTIONS
                if excluded_value:
                    continue
                path = word.partition("::")[0]
                if ("/" in path or path.endswith(".py")) and (
                    _GLOB_CHARACTERS & set(path)
                ):
                    return ()
                segment.append(_shell_path(word))
                continue
            excluded_value = False
            if segment and not any(
                token == root or token.startswith(f"{root}/") for token in segment
            ):
                return ()
            tokens.extend(segment)
            segment = []
    return tuple(
        target
        for target in targets
        if not any(_token_selects(token, target.removeprefix("./")) for token in tokens)
    )


def _shell_path(word: str) -> str:
    stripped = word.removeprefix("./").rstrip("/")
    if not stripped and word.startswith("."):
        return "."
    return stripped


def _token_selects(token: str, target: str) -> bool:
    path = target.partition("::")[0]
    return bool(token) and (
        token in {".", target, path}
        or path.startswith(f"{token}/")
        or target.startswith(f"{token}::")
        or token.startswith((f"{target}::", f"{target}["))
    )
