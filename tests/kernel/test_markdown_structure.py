"""The Markdown line model Heddle reads document structure through.

antigravity-reviewer-lane-v1, AC-22: the gate's section extractors and the
kernel's Verified-by readers take their lines and verbatim flags from
``heddle.kernel.markdown_structure.markdown_lines``. A line is verbatim when a
CommonMark fenced code block or HTML block covers it; the parser loads only
for a document holding a line that could open one.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from heddle.kernel.markdown_structure import MarkdownLine, markdown_lines

REPO_ROOT = Path(__file__).resolve().parents[2]


def _verbatim(text: str) -> list[int]:
    return [index for index, line in enumerate(markdown_lines(text)) if line.verbatim]


_FENCE_SHAPES_DOCUMENT = (
    "\n".join(
        [
            "intro",  # 0
            "```python",  # 1: a top-level fence with an info string
            "## Inside the fence",
            "``` not a closing run",  # 3: a run followed by text closes nothing
            "```",  # 4
            "",
            "- item",
            "  ~~~",  # 7: a fence inside a list item
            "  ## Inside the item's fence",
            "  ~~~",  # 9
            "> ```",  # 10: a fence inside a blockquote
            "> ## Inside the quoted fence",
            "> ```",  # 12
            "",
            "    ```",  # 14: indented code, not a fence
            "    ## Inside indented code",
            "",
            "- open item",
            "  ```",  # 18: left open inside the item
            "  code",
            "## After the item",  # 20: closes the item, and its fence with it
            "````",  # 21: left open at the top level, so it runs to the end
            "## Runs to the end",
            "tail",  # 23
        ]
    )
    + "\n"
)


def test_ac22_markdown_lines_flag_every_line_a_commonmark_fence_covers() -> None:
    """Fence lines, delimiters included, at any container depth."""
    lines = markdown_lines(_FENCE_SHAPES_DOCUMENT)
    assert [line.text for line in lines] == _FENCE_SHAPES_DOCUMENT.split("\n")[:-1]
    verbatim = _verbatim(_FENCE_SHAPES_DOCUMENT)
    assert verbatim == [1, 2, 3, 4, 7, 8, 9, 10, 11, 12, 18, 19, 21, 22, 23], (
        f"FAIL AC-22: fenced lines do not follow CommonMark: {verbatim}"
    )


_HTML_SHAPES_DOCUMENT = (
    "\n".join(
        [
            "intro",  # 0
            "<!--",  # 1: a comment holding a fenced Markdown example
            "```markdown",
            "## Inside the commented example",
            "### AC-9: Commented lookalike",
            "```",
            "-->",  # 6
            "## After the comment",  # 7: real
            "<details>",  # 8: a block that ends at the next blank line
            "## Inside the details block",
            "",  # 10: ends it
            "## After the blank line",  # 11: real
            "Prose names a <slug> inline.",  # 12: inline HTML is no block
            "<slug> continues the paragraph.",  # 13: a tag with text opens none
            "",
            "- item",
            "  <!-- a comment in the item -->",  # 16: inside a list item
            "> <div>",  # 17: inside a blockquote
            "> ## Inside the quoted block",
            "",
            "    <!-- indented code, not HTML -->",  # 20: indented code
            "tail",  # 21
        ]
    )
    + "\n"
)


def test_ac22_markdown_lines_flag_every_line_an_html_block_covers() -> None:
    """An HTML comment holding a fenced example, a block that ends at a blank
    line and blocks inside containers are verbatim; inline HTML in prose and
    indented code are not."""
    verbatim = _verbatim(_HTML_SHAPES_DOCUMENT)
    assert verbatim == [1, 2, 3, 4, 5, 6, 8, 9, 16, 17, 18], (
        f"FAIL AC-22: HTML block lines do not follow CommonMark: {verbatim}"
    )


def test_ac22_markdown_lines_end_lines_at_crlf_cr_and_lf_only() -> None:
    """Each line keeps its own ending, so the lines join back to the source;
    a vertical tab or a line separator stays inside its line."""
    line = MarkdownLine
    text = "a\r\n```\rb\u2028c\n```\r\n\vtail"
    lines = markdown_lines(text)
    assert lines == (
        line("a", "a\r\n", False),
        line("```", "```\r", True),
        line("b\u2028c", "b\u2028c\n", True),
        line("```", "```\r\n", True),
        line("\vtail", "\vtail", False),
    ), f"FAIL AC-22: lines are not counted at CRLF, CR and LF only: {lines}"
    assert "".join(item.raw for item in lines) == text
    assert markdown_lines("a\n") == (line("a", "a\n", False),)
    assert markdown_lines("") == ()


# Each case: the document and its verbatim lines. A YAML block scalar holding
# a literal comment opener must not hide the body after the frontmatter.
FRONTMATTER_CASES = {
    "block-scalar-comment-opener": (
        "---\nsummary: |\n  <!-- a literal opener, never closed\n---\n"
        "# Title\n\n## Acceptance Criteria\n\n### AC-1: One\n",
        [0, 1, 2, 3],
    ),
    "crlf-delimiters": (
        "---\r\nsummary: |\r\n  <!-- never closed\r\n---\r\n## Body\r\n",
        [0, 1, 2, 3],
    ),
    "cr-delimiters": (
        "---\rsummary: |\r  <!-- never closed\r---\r## Body\r",
        [0, 1, 2, 3],
    ),
    "structure-lookalikes": (
        "---\n## a YAML comment\n### AC-9: lookalike\n"
        "Verified-by: tests/test_a.py::test_one\n---\n## Body heading\n",
        [0, 1, 2, 3, 4],
    ),
    "empty": ("---\n---\nbody\n", [0, 1]),
    "closing-delimiter-ends-the-input": ("---\nkey: value\n---", [0, 1, 2]),
    # Body lines keep whole-document numbers.
    "body-blocks-after-frontmatter": (
        "---\nkey: <div>\n---\n<!-- c -->\ntext\n```\ncode\n```\n",
        [0, 1, 2, 3, 5, 6, 7],
    ),
    # No closing delimiter: no frontmatter, so the parser reads every line.
    "unterminated": (
        "---\nsummary: |\n  <!-- never closed\n## Heading\n",
        [2, 3],
    ),
    "not-on-the-first-line": ("intro\n---\n## Not frontmatter\n---\n", []),
    "indented-delimiter": (" ---\n## Not frontmatter\n---\n", []),
    "delimiter-with-trailing-space": ("--- \n## Not frontmatter\n---\n", []),
}


@pytest.mark.parametrize("case", FRONTMATTER_CASES)
def test_ac22_frontmatter_is_verbatim_and_never_parsed(case) -> None:
    """Frontmatter runs from a first line that is exactly `---` through the
    next such line, delimiters included, at any line ending. Its lines are
    verbatim and only the body is parsed, so a comment opener in a block
    scalar hides nothing after it; a `---` that opens no frontmatter is
    Markdown."""
    text, expected = FRONTMATTER_CASES[case]
    lines = markdown_lines(text)
    assert "".join(line.raw for line in lines) == text
    verbatim = _verbatim(text)
    assert verbatim == expected, (
        f"FAIL AC-22: the {case} frontmatter reads as {verbatim}"
    )


def test_ac22_the_html_block_names_are_markdown_its() -> None:
    """The opener check's copy of CommonMark's HTML block tag names is the
    parser's own list."""
    from markdown_it.common.html_blocks import block_names

    from heddle.kernel.markdown_structure import _HTML_BLOCK_NAMES

    assert sorted(_HTML_BLOCK_NAMES) == sorted(block_names)
    assert len(set(_HTML_BLOCK_NAMES)) == len(_HTML_BLOCK_NAMES)


# Frontmatter content for the agreement test, in chunks of lines; no line is
# exactly `---`. The delimiter lookalikes at column 0 make the YAML malformed,
# which ``read_markdown`` refuses, so only ``_frontmatter_span`` reads those.
_FRONTMATTER_CHUNKS = (
    ("type: feature-spec",),
    ("summary: |", "  <!-- a literal opener", "  ## not a heading"),
    ("## a YAML comment",),
    ("Verified-by: tests/test_a.py::test_one",),
    ("html: <div>",),
    ("code: |", "  ```"),
    ("",),
    ("----",),
    (" ---",),
    ("--- ",),
    ("---x",),
)
_BODY = "Body starts here."


def _frontmatter_documents(seed: int, count: int = 150):
    """LF and CRLF documents: closed frontmatter, unterminated frontmatter, or
    a first line that only looks like a delimiter. The body's first line is
    always ``_BODY``."""
    rng = random.Random(seed)
    for _ in range(count):
        content = [
            line
            for _ in range(rng.randint(0, 4))
            for line in rng.choice(_FRONTMATTER_CHUNKS)
        ]
        shape = rng.choice(("closed", "closed", "unterminated", "lookalike"))
        opening = (
            rng.choice((" ---", "--- ", "----")) if shape == "lookalike" else "---"
        )
        closing = [] if shape == "unterminated" else ["---"]
        ending = rng.choice(("\n", "\r\n"))
        lines = [opening, *content, *closing, _BODY, "## Body heading", "prose"]
        yield shape, ending.join(lines) + rng.choice(("", ending))


@pytest.mark.parametrize("seed", range(2))
def test_ac22_the_frontmatter_boundary_agrees_with_the_repository_readers(
    seed, tmp_path
) -> None:
    """On LF and CRLF documents the body starts at the offset
    ``_frontmatter_span`` reports and, wherever the YAML is well formed, at
    the line ``read_markdown`` reports as ``body_line``; every line before it
    is verbatim. Where both readers find no frontmatter (unterminated, or a
    first line that only looks like a delimiter) the first line is Markdown.
    """
    from heddle.kernel.knowledge import read_markdown
    from heddle.kernel.project_config import KernelError
    from heddle.kernel.spec_lifecycle import _frontmatter_span

    path = tmp_path / "document.md"
    compared_with_read_markdown = 0
    for shape, text in _frontmatter_documents(seed):
        path.write_bytes(text.encode())
        lines = markdown_lines(text)
        try:
            body_line = read_markdown(path).body_line
            refusal = ""
        except KernelError as error:
            body_line, refusal = None, error.message
        try:
            body_start = _frontmatter_span(text, path)[2]
        except KernelError:
            body_start = None
        if shape == "closed":
            assert body_start is not None, text
            boundary = len(markdown_lines(text[:body_start]))
            assert all(line.verbatim for line in lines[:boundary]), text
            assert (lines[boundary].text, lines[boundary].verbatim) == (_BODY, False)
            if body_line is None:
                assert "malformed YAML" in refusal, refusal
            else:
                assert body_line - 1 == boundary, text
                compared_with_read_markdown += 1
        else:
            assert body_start is None, text
            if shape == "unterminated":
                assert "unterminated" in refusal, refusal
            else:
                assert body_line == 1, text
            assert not lines[0].verbatim, text
    assert compared_with_read_markdown >= 20, compared_with_read_markdown


_LOADED_PROBE = """
import json
import sys

from heddle.kernel.markdown_structure import markdown_lines

lines = markdown_lines(sys.argv[1])
print(json.dumps({
    "verbatim": [index for index, line in enumerate(lines) if line.verbatim],
    "loaded": sorted(
        name for name in sys.modules
        if name == "heddle.kernel.knowledge_search"
        or name.split(".")[0] == "markdown_it"
    ),
}))
"""
_NO_OPENER = (
    "# Title\n\nProse names a <slug> inline, and a `span`.\n\n"
    "    indented code\n\n- item\n> quote\n1. step\n"
)


_LEADING_PLACEHOLDER = "Prose continues on the next line,\n<slug> is the feature.\n"
_FRONTMATTER_OPENER = "---\nsummary: |\n  <!-- a literal opener\n---\n"


@pytest.mark.parametrize(
    ("text", "verbatim", "loads"),
    [
        (_NO_OPENER, [], False),
        (_NO_OPENER + "```\ncode\n```\n", [9, 10, 11], True),
        (_NO_OPENER + "<!-- note -->\n", [9], True),
        (_NO_OPENER + _LEADING_PLACEHOLDER, [], False),
        (_FRONTMATTER_OPENER + _NO_OPENER, [0, 1, 2, 3], False),
    ],
    ids=[
        "no-opener",
        "fence",
        "html-comment",
        "leading-placeholder",
        "frontmatter-comment-opener",
    ],
)
def test_ac22_the_parser_loads_only_for_a_possible_fence_or_html_block(
    text, verbatim, loads
) -> None:
    """In a fresh process, a document with no body line that could open a
    fence or an HTML block is read without importing the parser or the
    search module, even with a placeholder tag starting a prose line or a
    comment opener in its frontmatter; a fence or a comment loads both."""
    result = subprocess.run(
        [sys.executable, "-c", _LOADED_PROBE, text],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    assert observation["verbatim"] == verbatim
    loaded = {
        name
        for name in observation["loaded"]
        if name in {"heddle.kernel.knowledge_search", "markdown_it"}
    }
    expected = {"heddle.kernel.knowledge_search", "markdown_it"} if loads else set()
    assert loaded == expected, (
        f"FAIL AC-22: the parser loaded as {sorted(loaded)} for {text!r}"
    )


# Generated documents for the opener check: container prefixes before block
# openers and closers (every HTML block start condition, complete tags and
# near misses among them), headings, declarations, prose with inline HTML or
# a leading placeholder tag, and indented code, joined by mixed line endings;
# some open with frontmatter.
_PREFIXES = (
    ("",) * 8
    + (" ", "   ", "    ", "\t", "> ", ">", "> > ", "- ", "-   ", "* ", "+\t")
    + ("1. ", "10) ", "  - ", "> - ", "- > ", "1.  > ", "-", "1.", "    > ")
)
_OPENERS = (
    "```",
    "~~~",
    "````text",
    "``` a`b",
    "<!--",
    "-->",
    "<!-- note -->",
    "<details>",
    "</details>",
    "<div>",
    "<slug>",
    "<a href='x'>",
    "<?php",
    "<![CDATA[",
    "<!DOCTYPE html>",
    "<script>",
    "</script>",
    "<",
    "< not a tag",
    # Every start condition again, in other cases and spellings.
    "<pre",
    "<Style>",
    "<textarea rows=2>",
    "<\u017fcript>",
    "<?x ?>",
    "<!doctype html>",
    "<DIV>",
    "</div x",
    "<details/>",
    "<section",
    "<h1 class=x>",
    "<div\u00a0class=x>",
    "<td\u2028",
    "<DIV class=x",
    "<Section",
    "<\u017fcript",
    # Complete tags alone on a line, a quoted `>` in an attribute among them.
    "<feature-id>",
    "</slug>",
    '<a title="x>y">',
    "<a b='>' c>",
    "<slug/>  ",
    # Near misses: no block starts on these.
    "<!1",
    "<!-",
    "<scripts",
    "<divx text",
    "< div>",
    "<1>",
    "<slug> text",
    '<a title="x>y"> text',
)
_PLAIN = (
    "",
    "",
    "## Heading",
    "### AC-1: One",
    "Verified-by: tests/test_a.py::test_one",
    "plain prose",
    "Prose names a <placeholder> inline.",
    "a `span` and <b>bold</b> inline",
    "    indented code",
    "- item",
    "> quote",
    "<slug> is the feature it names.",
    "<feature-id> names the feature, and <b>bold</b> too.",
)


def _generated_documents(seed: int, count: int = 400):
    """Yield (frontmatter lines, body offset, body uses an opener, text)."""
    rng = random.Random(seed)
    for _ in range(count):
        frontmatter: list[str] = []
        if rng.random() < 0.2:
            content = [rng.choice(_OPENERS + _PLAIN) for _ in range(rng.randint(0, 3))]
            frontmatter = ["---", *content, "---"]
        body: list[str] = []
        opener = False
        for _ in range(rng.randint(1, 14)):
            if rng.random() < 0.08:
                opener = True
                body.append(rng.choice(_PREFIXES) + rng.choice(_OPENERS))
            else:
                body.append(rng.choice(_PREFIXES) + rng.choice(_PLAIN))
        ending = rng.choice(("\n", "\r\n", "\r"))
        offset = sum(len(line) + len(ending) for line in frontmatter)
        text = ending.join(frontmatter + body) + rng.choice(("", ending))
        yield len(frontmatter), offset, opener, text


@pytest.mark.parametrize("seed", range(3))
def test_ac22_the_opener_check_misses_no_fence_or_html_block(seed, monkeypatch) -> None:
    """Soundness of the parser-free path, against markdown-it itself.

    The flags always equal frontmatter plus markdown-it's fence and HTML
    block lines in the body: every body markdown-it reads such a block in is
    parsed, and every document read without the parser has none. A body
    with no opener line, placeholder prose included, is always read without
    the parser, whatever its frontmatter holds.
    """
    from markdown_it import MarkdownIt

    import heddle.kernel.knowledge_search as knowledge_search

    parser = MarkdownIt("commonmark")
    parsed: list[str] = []
    real = knowledge_search.markdown_verbatim_lines

    def counted(text: str) -> frozenset[int]:
        parsed.append(text)
        return real(text)

    monkeypatch.setattr(knowledge_search, "markdown_verbatim_lines", counted)
    markdown_lines.cache_clear()
    unparsed = 0
    for frontmatter, offset, opener, text in _generated_documents(seed):
        before = len(parsed)
        flags = _verbatim(text)
        body = sorted(
            {
                frontmatter + index
                for token in parser.parse(text[offset:])
                if token.type in {"fence", "html_block"} and token.map is not None
                for index in range(token.map[0], token.map[1])
            }
        )
        assert flags == [*range(frontmatter), *body], (
            f"FAIL AC-22: verbatim flags differ from markdown-it: {text!r}"
        )
        if len(parsed) == before:
            unparsed += 1
            assert body == [], f"FAIL AC-22: the opener check missed a block: {text!r}"
        else:
            assert opener, (
                f"FAIL AC-22: an opener-free body loaded the parser: {text!r}"
            )
    markdown_lines.cache_clear()
    assert unparsed >= 50, f"too few documents took the parser-free path: {unparsed}"
