"""The Markdown line model Heddle reads document structure through."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import NamedTuple

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_FRONTMATTER_DELIMITER = "---"
# CommonMark's HTML block tag names (start condition 6), as markdown-it lists
# them in `markdown_it.common.html_blocks`; a test pins this copy to that list.
_HTML_BLOCK_NAMES = tuple(
    (
        "address article aside base basefont blockquote body caption center col"
        " colgroup dd details dialog dir div dl dt fieldset figcaption figure"
        " footer form frame frameset h1 h2 h3 h4 h5 h6 head header hr html"
        " iframe legend li link main menu menuitem nav noframes ol optgroup"
        " option p param search section summary table tbody td tfoot th thead"
        " title tr track ul"
    ).split()
)
# The tag names of start condition 1, whose blocks end at their closing tag.
_RAW_TEXT_NAMES = ("script", "pre", "style", "textarea")
# Container prefixes: indentation, blockquote markers, and list markers with
# their spacing, in any nesting.
_CONTAINER_PREFIX = r"(?:[ \t]*(?:>|[-+*](?=[ \t]|$)|[0-9]{1,9}[.)](?=[ \t]|$)))*[ \t]*"
# A line that could open a fenced code block or an HTML block of conditions 1
# to 6: after its container prefixes and any indentation it starts with a
# fence run, or with one of CommonMark's HTML block start conditions. Each
# condition is read at least as widely as the parser reads it (any case, a
# closing slash on every tag name, `/>` after every tag name), so the check
# can only over-match.
_POSSIBLE_OPENER = re.compile(
    _CONTAINER_PREFIX
    + r"(?:```|~~~|<(?:!--|\?|![a-z]|!\[CDATA\[|/?(?:"
    + "|".join(_RAW_TEXT_NAMES + _HTML_BLOCK_NAMES)
    + r")(?=\s|/?>|$)))",
    re.IGNORECASE,
)
# Start condition 7: a complete open or closing tag followed only by
# whitespace. Such a line starts with `<` or `</` and a letter, and its last
# non-whitespace character is `>`; checking the line end, not the tag, keeps
# a quoted `>` inside an attribute value from hiding one.
_POSSIBLE_TAG_LINE = re.compile(_CONTAINER_PREFIX + r"</?[a-z]", re.IGNORECASE)


class MarkdownLine(NamedTuple):
    """One line of a Markdown document as the CommonMark parser counts it."""

    text: str  # the line without its line ending
    raw: str  # the line with its own line ending, if it has one
    verbatim: bool  # frontmatter, a fenced code block or an HTML block covers it


@lru_cache(maxsize=16)
def markdown_lines(text: str) -> tuple[MarkdownLine, ...]:
    """Return the document's lines, each flagged when a verbatim block covers it.

    This is the one rule for reading Markdown structure: the gate's section
    extractors and the kernel's ``Verified-by`` readers both take it from
    here. A line is verbatim when frontmatter, a CommonMark fenced code block
    or an HTML block (an HTML comment included) covers it.

    Frontmatter: when the first line is exactly ``---``, the frontmatter runs
    through the next line that is exactly ``---``, both delimiters included;
    with no such line there is no frontmatter. Its lines are metadata, never
    Markdown structure, so they are verbatim and the parser never sees them:
    only the body after the closing delimiter is parsed, and the block lines
    it finds are counted in whole-document lines.

    Fenced code blocks and HTML blocks count at any container depth (list
    items and blockquotes included), the fence delimiters included. A block
    left open ends where CommonMark ends it: a fence at the end of its
    container or of the document, an HTML block at its own end condition.

    Indented code is not flagged. Every reader matches structure at column 0
    (headings, ``Verified-by:``), and an indented code line never starts
    there, so flagging it would change no reading; leaving it out keeps the
    opener check below exact.

    Lines end at ``\\r\\n``, ``\\r`` and ``\\n``, as the parser counts them,
    and ``raw`` keeps each line's own ending. Any other boundary
    ``str.splitlines`` knows (a vertical tab, a form feed, the file, group and
    record separators, NEL, U+2028 or U+2029) stays inside its line. A final
    line ending opens no further line.

    The CommonMark parser loads only when some body line could open a fence
    or an HTML block under CommonMark's start conditions, so a document with
    no such line is read without it. The result is cached, because callers
    read one document many times, once per section or acceptance criterion.
    """
    spans: list[tuple[str, str]] = []
    start = 0
    for match in _LINE_BREAK.finditer(text):
        spans.append((text[start : match.start()], text[start : match.end()]))
        start = match.end()
    if start < len(text):
        spans.append((text[start:], text[start:]))
    frontmatter = _frontmatter_line_count(spans)
    verbatim = set(range(frontmatter))
    body = spans[frontmatter:]
    if any(_could_open_block(line) for line, _raw in body):
        # Imported on use: the parser lives in the one module allowed to
        # import it, and loads only for a document that may need it.
        from heddle.kernel.knowledge_search import markdown_verbatim_lines

        offset = sum(len(raw) for _line, raw in spans[:frontmatter])
        verbatim.update(
            frontmatter + index for index in markdown_verbatim_lines(text[offset:])
        )
    return tuple(
        MarkdownLine(line, raw, index in verbatim)
        for index, (line, raw) in enumerate(spans)
    )


def _frontmatter_line_count(spans: list[tuple[str, str]]) -> int:
    if not spans or spans[0][0] != _FRONTMATTER_DELIMITER:
        return 0
    for index in range(1, len(spans)):
        if spans[index][0] == _FRONTMATTER_DELIMITER:
            return index + 1
    return 0


def _could_open_block(line: str) -> bool:
    if _POSSIBLE_OPENER.match(line):
        return True
    return _POSSIBLE_TAG_LINE.match(line) is not None and line.rstrip().endswith(">")
