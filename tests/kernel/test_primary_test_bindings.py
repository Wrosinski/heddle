"""Pure primary-binding parsing and selector policy."""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from heddle.kernel.knowledge import (
    read_spec_verification_facts,
    read_spec_verified_by_lines,
)
from heddle.kernel.test_bindings import (
    PARSER_PYTHON,
    VERIFIED_BY,
    dropped_command_tokens,
    inspect_python_test_source,
    is_python_source_issue,
    parse_primary_test_bindings,
    resolve_primary_test_binding,
    selector_rebind_pairs,
    unread_verified_by_lines,
    unselected_test_targets,
    verified_by_targets,
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
    assert invalid_python.issue.reason.startswith(
        f"source is not valid Python {PARSER_PYTHON} syntax (line 1: "
    )
    assert is_python_source_issue(invalid_python.issue.reason)
    assert not is_python_source_issue(invalid_utf8.issue.reason)


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


def test_dropped_command_tokens_only_removals_that_keep_the_rest_in_order() -> None:
    current = "runner fast tests/test_a.py tests/test_gone.py 'x y'"

    assert dropped_command_tokens(current, "runner fast tests/test_a.py 'x y'") == (
        "tests/test_gone.py",
    )
    assert dropped_command_tokens(current, current) is None
    assert (
        dropped_command_tokens(
            current, "runner fast tests/test_gone.py tests/test_a.py"
        )
        is None
    )
    assert (
        dropped_command_tokens(current, "runner fast tests/test_a.py 'x y' extra")
        is None
    )
    assert dropped_command_tokens(current, "runner 'unterminated") is None


# antigravity-reviewer-lane-v1, AC-22: every Verified-by reader takes its lines
# and fences from the one CommonMark rule the gate's section extractors use, so
# a declaration binds exactly when a reader of the rendered spec sees it
# outside a code block.

REAL = "tests/test_a.py::test_real"
EXAMPLE = "tests/test_a.py::test_example"
# Each shape: the AC text, then the 1-based lines of REAL's declarations and of
# the lookalike lines the readers report as unread.
FENCE_SHAPES = {
    "top-level-info-string": (
        f"Verified-by: {REAL}\n```python\nVerified-by: {EXAMPLE}\n```\n",
        (1,),
        (),
    ),
    "run-followed-by-text": (
        "````\n## Heading\n```` not a closing run\n"
        f"Verified-by: {EXAMPLE}\n````\nVerified-by: {REAL}\n",
        (6,),
        (),
    ),
    "list-item": (
        f"- item\n  ```\n  Verified-by: {EXAMPLE}\n  ```\nVerified-by: {REAL}\n",
        (5,),
        (),
    ),
    "blockquote": (
        f"> ```\n> Verified-by: {EXAMPLE}\n> ```\n\nVerified-by: {REAL}\n",
        (5,),
        (),
    ),
    "indented-code": (
        f"intro\n\n    ```\n    Verified-by: {EXAMPLE}\n\nVerified-by: {REAL}\n",
        (6,),
        (4,),
    ),
    "left-open-at-the-top-level": (
        f"Verified-by: {REAL}\n~~~\nVerified-by: {EXAMPLE}\n",
        (1,),
        (),
    ),
    "left-open-in-a-list-item": (
        f"- item\n  ```\n  code\nVerified-by: {REAL}\n",
        (4,),
        (),
    ),
    "crlf": (
        f"Verified-by: {REAL}\r\n```\r\nVerified-by: {EXAMPLE}\r\n```\r\n",
        (1,),
        (),
    ),
    "cr": (
        f"```\rVerified-by: {EXAMPLE}\r```\rVerified-by: {REAL}\r",
        (4,),
        (),
    ),
}


@pytest.mark.parametrize("shape", FENCE_SHAPES)
def test_ac22_verified_by_readers_follow_the_commonmark_fence_rule(shape) -> None:
    """A fenced declaration never binds and a fenced lookalike is never
    reported, at any container depth; a fence left open in a list item ends
    with it; lines count at CRLF, CR and LF."""
    _assert_only_the_real_declaration_reads(FENCE_SHAPES[shape], shape)


def _assert_only_the_real_declaration_reads(case, shape) -> None:
    text, real_lines, unread = case
    parsed = parse_primary_test_bindings("AC-1", text)
    assert [binding.target for binding in parsed.bindings] == [REAL], (
        f"FAIL AC-22: the {shape} binding does not follow the CommonMark rule"
    )
    assert verified_by_targets(text) == tuple((line, REAL) for line in real_lines)
    assert unread_verified_by_lines(text) == unread


# HTML blocks, comments included, hold their lines as content too: a commented
# sample declaration never binds, and a commented lookalike is never reported.
HTML_SHAPES = {
    "html-comment": (
        f"<!--\nVerified-by: {EXAMPLE}\n-->\nVerified-by: {REAL}\n",
        (4,),
        (),
    ),
    "commented-fenced-example": (
        "<!--\n```markdown\n### AC-9: Lookalike\n"
        f"Verified-by: {EXAMPLE}\n  Verified-by: {EXAMPLE}\n```\n-->\n\n"
        f"Verified-by: {REAL}\n",
        (9,),
        (),
    ),
    "details-block-ends-at-a-blank-line": (
        f"<details>\nVerified-by: {EXAMPLE}\n\nVerified-by: {REAL}\n",
        (4,),
        (),
    ),
    "inline-html-in-prose": (
        "intro names a <slug> inline\n<slug> continues the paragraph\n"
        f"Verified-by: {REAL}\n",
        (3,),
        (),
    ),
    "comment-in-a-list-item": (
        f"- item\n\n  <!--\n  - Verified-by: {EXAMPLE}\n  -->\nVerified-by: {REAL}\n",
        (6,),
        (),
    ),
    "comment-in-a-blockquote": (
        f"> <!--\n> Verified-by: {EXAMPLE}\n> -->\n\nVerified-by: {REAL}\n",
        (5,),
        (),
    ),
    "crlf-comment": (
        f"<!--\r\nVerified-by: {EXAMPLE}\r\n-->\r\nVerified-by: {REAL}\r\n",
        (4,),
        (),
    ),
}


@pytest.mark.parametrize("shape", HTML_SHAPES)
def test_ac22_verified_by_readers_skip_html_blocks(shape) -> None:
    """A declaration or lookalike inside an HTML block, a comment included,
    never binds and is never reported, at any container depth; a block that
    ends at a blank line exposes the declaration after it, and inline HTML in
    prose hides nothing."""
    _assert_only_the_real_declaration_reads(HTML_SHAPES[shape], shape)


# Frontmatter is metadata: a `Verified-by:` key or a lookalike in it never binds
# and is never reported, and a comment opener in it hides nothing after it.
FRONTMATTER_SHAPES = {
    "verified-by-key": (
        f"---\nVerified-by: {EXAMPLE}\n  Verified-by: {EXAMPLE}\n---\n"
        f"Verified-by: {REAL}\n",
        (5,),
        (),
    ),
    "block-scalar-comment-opener": (
        f"---\nsummary: |\n  <!-- never closed\n---\nVerified-by: {REAL}\n",
        (5,),
        (),
    ),
    "crlf": (
        f"---\r\nVerified-by: {EXAMPLE}\r\n---\r\nVerified-by: {REAL}\r\n",
        (4,),
        (),
    ),
    # No closing delimiter, so no frontmatter: the declaration binds.
    "unterminated": (
        f"---\nsummary: plain\nVerified-by: {REAL}\n",
        (3,),
        (),
    ),
}


@pytest.mark.parametrize("shape", FRONTMATTER_SHAPES)
def test_ac22_verified_by_readers_skip_frontmatter(shape) -> None:
    """Declarations count in whole-document lines after frontmatter, and
    none is read inside it."""
    _assert_only_the_real_declaration_reads(FRONTMATTER_SHAPES[shape], shape)


# Survivor: plain-LF text reads exactly as under the previous kernel fence rule
# (at most three spaces of indent, closed by a longer or equal run of the same
# character). The functions below copy that rule.
_PREVIOUS_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_PREVIOUS_LOOKALIKE = re.compile(
    r"^[ \t]*(?:>[ \t]*)*(?:(?:[-*+]|\d+[.)])[ \t]+)?[*_]*"
    r"verified[ \t_-]*by[*_]*[ \t]*:",
    re.IGNORECASE,
)


def _previous_unfenced_lines(markdown: str):
    fence_marker = None
    fence_size = 0
    for number, line in enumerate(markdown.splitlines(), start=1):
        delimiter = _PREVIOUS_FENCE.match(line)
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


def _previous_declarations(markdown: str) -> list[tuple[int, str]]:
    return [
        (number, part.strip())
        for number, line in _previous_unfenced_lines(markdown)
        if (match := VERIFIED_BY.match(line)) is not None
        for part in match.group(1).split(",")
        if part.strip()
    ]


def _previous_whole_text_targets(markdown: str) -> list[tuple[int, str]]:
    return [
        (markdown.count("\n", 0, match.start()) + 1, part.strip())
        for match in VERIFIED_BY.finditer(markdown)
        for part in match.group(1).split(",")
        if part.strip()
    ]


def _previous_unread_lines(markdown: str) -> list[int]:
    return [
        number
        for number, line in _previous_unfenced_lines(markdown)
        if _PREVIOUS_LOOKALIKE.match(line) and not VERIFIED_BY.match(line)
    ]


_DECLARATION_LINES = (
    "Verified-by: tests/test_a.py::test_one",
    "Verified-by: tests/test_a.py::test_one, tests/test_b.py::test_two",
    "Verified-by: tests/test_c.py",
    "Verified-by:",
    "- Verified-by: tests/test_list.py",
    "  Verified-by: tests/test_indent.py",
    "verified-by: tests/test_case.py",
    "Verified by: tests/test_space.py",
    "1. **Verified-by:** tests/test_bold.py",
    "> Verified-by : tests/test_quote.py",
)
_PROSE_LINES = (
    "",
    "text",
    "## Heading",
    "### AC-1: One",
    "- item",
    "  indented text",
    "Prose names the `Verified-by:` line.",
)
_SIMPLE_FENCES = (
    ("```", "```"),
    ("```python", "```"),
    ("~~~", "~~~"),
    ("  ```", "  ```"),
    ("~~~~", "~~~~"),
    ("````text", "````"),
)


def _plain_lf_texts(seed: int, *, fences: bool, count: int = 300):
    """Generated plain-LF texts holding no HTML block; any fence is a
    CommonMark fence at the top level, closed by its own bare run or left
    open to the end of the text."""
    from markdown_it import MarkdownIt

    rng = random.Random(seed)
    vocabulary = _DECLARATION_LINES * 2 + _PROSE_LINES

    def fence(closed: bool) -> list[str]:
        opening, closing = rng.choice(_SIMPLE_FENCES)
        body = [rng.choice(vocabulary) for _ in range(rng.randint(0, 4))]
        lines = [opening, *body, closing] if closed else [opening, *body]
        # An indented opening right after a list item would open inside the
        # item; a blank line and an unindented paragraph close any list first.
        return ["", "text", *lines] if opening.startswith(" ") else lines

    for _ in range(count):
        lines: list[str] = []
        for _ in range(rng.randint(0, 30)):
            if fences and rng.random() < 0.15:
                lines.extend(fence(closed=True))
            else:
                lines.append(rng.choice(vocabulary))
        if fences and rng.random() < 0.1:
            lines.extend(fence(closed=False))
        text = "\n".join(lines) + rng.choice(("", "\n"))
        tokens = MarkdownIt("commonmark").parse(text)
        assert all(token.level == 0 for token in tokens if token.type == "fence")
        assert not any(token.type == "html_block" for token in tokens)
        yield text


@pytest.mark.parametrize("seed", range(3))
def test_ac23_survivor_plain_lf_verified_by_reading_equals_the_previous_rule(
    seed,
) -> None:
    """On HTML-free plain-LF text with top-level fences the previous rule
    closed correctly, every reader returns what it returned before. The whole-text
    target reader, which read fenced lines before, equals its previous output
    on fence-free text and the previous unfenced declarations otherwise."""
    for text in _plain_lf_texts(seed, fences=True):
        previous = _previous_declarations(text)
        parsed = parse_primary_test_bindings("AC-1", text)
        assert [binding.target for binding in parsed.bindings] == list(
            dict.fromkeys(target for _line, target in previous)
        ), text
        assert verified_by_targets(text) == tuple(previous), text
        assert list(unread_verified_by_lines(text)) == _previous_unread_lines(text)
    for text in _plain_lf_texts(seed + 100, fences=False):
        assert verified_by_targets(text) == tuple(_previous_whole_text_targets(text))
