"""The Markdown line model Heddle reads document structure through."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import NamedTuple

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
# A line that could open a fenced code block or an HTML block: after any
# container prefixes (indentation, blockquote markers, list markers with
# their spacing) it begins with a run of three backticks or tildes, or `<`.
# Every CommonMark fence and HTML block opens on such a line, so a document
# with none holds neither, and needs no parse.
_POSSIBLE_OPENER = re.compile(
    r"(?:[ \t]*(?:>|[-+*](?=[ \t]|$)|[0-9]{1,9}[.)](?=[ \t]|$)))*[ \t]*(?:```|~~~|<)"
)


class MarkdownLine(NamedTuple):
    """One line of a Markdown document as the CommonMark parser counts it."""

    text: str  # the line without its line ending
    raw: str  # the line with its own line ending, if it has one
    verbatim: bool  # a fenced code block or an HTML block covers it


@lru_cache(maxsize=16)
def markdown_lines(text: str) -> tuple[MarkdownLine, ...]:
    """Return the document's lines, each flagged when a verbatim block covers it.

    This is the one rule for reading Markdown structure: the gate's section
    extractors and the kernel's ``Verified-by`` readers both take it from
    here. A line is verbatim when a CommonMark fenced code block or HTML block
    (an HTML comment included) covers it, at any container depth (list items
    and blockquotes included), the fence delimiters included. A block left
    open ends where CommonMark ends it: a fence at the end of its container
    or of the document, an HTML block at its own end condition.

    Indented code is not flagged. Every reader matches structure at column 0
    (headings, ``Verified-by:``), and an indented code line never starts
    there, so flagging it would change no reading; leaving it out keeps the
    opener check below exact.

    Lines end at ``\\r\\n``, ``\\r`` and ``\\n``, as the parser counts them,
    and ``raw`` keeps each line's own ending. Any other boundary
    ``str.splitlines`` knows (a vertical tab, a form feed, the file, group and
    record separators, NEL, U+2028 or U+2029) stays inside its line. A final
    line ending opens no further line.

    The CommonMark parser loads only when some line could open a fence or an
    HTML block, so a document with no such line is read without it. The
    result is cached, because callers read one document many times, once per
    section or acceptance criterion.
    """
    spans: list[tuple[str, str]] = []
    start = 0
    for match in _LINE_BREAK.finditer(text):
        spans.append((text[start : match.start()], text[start : match.end()]))
        start = match.end()
    if start < len(text):
        spans.append((text[start:], text[start:]))
    verbatim: frozenset[int] = frozenset()
    if any(_POSSIBLE_OPENER.match(line) for line, _raw in spans):
        # Imported on use: the parser lives in the one module allowed to
        # import it, and loads only for a document that may need it.
        from heddle.kernel.knowledge_search import markdown_verbatim_lines

        verbatim = markdown_verbatim_lines(text)
    return tuple(
        MarkdownLine(line, raw, index in verbatim)
        for index, (line, raw) in enumerate(spans)
    )
