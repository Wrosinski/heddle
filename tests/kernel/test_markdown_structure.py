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


@pytest.mark.parametrize(
    ("tail", "verbatim"),
    [("", []), ("```\ncode\n```\n", [9, 10, 11]), ("<!-- note -->\n", [9])],
    ids=["no-opener", "fence", "html-comment"],
)
def test_ac22_the_parser_loads_only_for_a_possible_fence_or_html_block(
    tail, verbatim
) -> None:
    """In a fresh process, a document with no line that could open a fence
    or an HTML block is read without importing the parser or the search
    module; a fence or a comment loads both."""
    result = subprocess.run(
        [sys.executable, "-c", _LOADED_PROBE, _NO_OPENER + tail],
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
    expected = {"heddle.kernel.knowledge_search", "markdown_it"} if tail else set()
    assert loaded == expected, (
        f"FAIL AC-22: the parser loaded as {sorted(loaded)} for {tail!r}"
    )


# Generated documents for the opener check: container prefixes before block
# openers and closers, headings, declarations, prose with inline HTML and
# indented code, joined by mixed line endings.
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
)


def _generated_documents(seed: int, count: int = 400):
    rng = random.Random(seed)
    for _ in range(count):
        lines = [
            rng.choice(_PREFIXES)
            + (rng.choice(_OPENERS) if rng.random() < 0.08 else rng.choice(_PLAIN))
            for _ in range(rng.randint(1, 14))
        ]
        ending = rng.choice(("\n", "\r\n", "\r"))
        yield ending.join(lines) + rng.choice(("", ending))


@pytest.mark.parametrize("seed", range(3))
def test_ac22_the_opener_check_misses_no_fence_or_html_block(seed, monkeypatch) -> None:
    """Soundness of the parser-free path, against markdown-it itself.

    Every document markdown-it reads a fence or an HTML block in is parsed,
    and every document read without the parser has exactly the flags the
    parser would give, none; so the flags always equal markdown-it's.
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
    for text in _generated_documents(seed):
        before = len(parsed)
        flags = _verbatim(text)
        expected = sorted(
            {
                index
                for token in parser.parse(text)
                if token.type in {"fence", "html_block"} and token.map is not None
                for index in range(token.map[0], token.map[1])
            }
        )
        assert flags == expected, (
            f"FAIL AC-22: verbatim flags differ from markdown-it: {text!r}"
        )
        if len(parsed) == before:
            unparsed += 1
            assert expected == [], (
                f"FAIL AC-22: the opener check missed a block: {text!r}"
            )
    markdown_lines.cache_clear()
    assert unparsed >= 50, f"too few documents took the parser-free path: {unparsed}"
