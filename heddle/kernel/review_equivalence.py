"""Review-equivalence identity: the Python and Markdown rules and the predicate.

A rule maps one path's bytes to a canonical digest under a versioned rule id,
or to nothing when it does not apply. Rules are selected by suffix: ``.py``
takes ``python-tree/1+py<major>.<minor>`` and ``.md`` takes
``markdown-text/1+mdit<major>.<minor>``; every other path has raw identity
only. The identity is recorded beside raw identity and never applied to
content anything reads.

Two observations of one path, kind and executable mode are review-equivalent
when their raw identities match, or when both carry an identity under the same
rule id that this Heddle computes and the digests match. Anything else (an
absent side, a record without an identity, another interpreter or parser
version, an unknown rule) compares raw only.

Each rule hashes a stream of tokens, each framed as ``<length>:<text>``. A
token's first character names its kind, and the kind fixes how many tokens
follow it, so the stream is unambiguous.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import io
import re
import sys
import tokenize
import types
import warnings
from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from heddle.kernel.source_manifest import EvidenceObservation

PYTHON_RULE = "python-tree/1"
MARKDOWN_RULE = "markdown-text/1"

# Observing several scopes hashes one file's bytes repeatedly in one process;
# identities are memoised by rule and content digest.
_IDENTITIES: dict[tuple[str, bytes], tuple[str, str] | None] = {}
_IDENTITY_LIMIT = 4096


class _Unsupported(Exception):
    """A value the canonical serialisation does not define; no identity."""


def python_rule_id() -> str:
    """Name the Python rule under the running interpreter's syntax tree."""
    return f"{PYTHON_RULE}+py{sys.version_info.major}.{sys.version_info.minor}"


def markdown_rule_id() -> str:
    """Name the Markdown rule under the installed CommonMark parser version."""
    from heddle.kernel.knowledge_search import markdown_parser_version

    major, minor = markdown_parser_version()
    return f"{MARKDOWN_RULE}+mdit{major}.{minor}"


def rule_id_for(path: str) -> str | None:
    """Return the rule id this Heddle computes for ``path``, or None."""
    rule = _rule_for(path)
    return None if rule is None else rule[0]()


def equivalence_identity(path: str, content: bytes) -> tuple[str, str] | None:
    """Return ``(rule_id, digest)`` for a regular file's bytes, or None.

    None means raw identity only: the suffix has no rule, or the content is
    not UTF-8 or (for Python) does not parse and tokenize.
    """
    rule = _rule_for(path)
    if rule is None:
        return None
    rule_id, digest_of = rule
    key = (rule_id(), hashlib.sha256(content).digest())
    if key in _IDENTITIES:
        return _IDENTITIES[key]
    digest = digest_of(content)
    identity = None if digest is None else (key[0], digest)
    if len(_IDENTITIES) >= _IDENTITY_LIMIT:
        _IDENTITIES.clear()
    _IDENTITIES[key] = identity
    return identity


def review_equivalent(
    left: EvidenceObservation | None, right: EvidenceObservation | None
) -> bool:
    """Decide review equivalence between two observations; fail closed."""
    if left is None or right is None:
        return False
    if (left.path, left.kind, left.executable) != (
        right.path,
        right.kind,
        right.executable,
    ):
        return False
    if left.content_sha256 == right.content_sha256:
        return True
    if left.kind != "file" or left.equivalence is None or right.equivalence is None:
        return False
    rule = left.equivalence.rule
    return (
        rule == right.equivalence.rule
        and rule == rule_id_for(left.path)
        and left.equivalence.digest == right.equivalence.digest
    )


def _rule_for(
    path: str,
) -> tuple[Callable[[], str], Callable[[bytes], str | None]] | None:
    if path.endswith(".py"):
        return python_rule_id, _python_digest
    if path.endswith(".md"):
        return markdown_rule_id, _markdown_digest
    return None


def _digest(rule: str, tokens: Iterable[str]) -> str:
    framed = "".join(
        f"{len(token)}:{token}"
        for token in ("heddle.review-equivalence/" + rule, *tokens)
    )
    return hashlib.sha256(framed.encode("utf-8", "surrogatepass")).hexdigest()


# python-tree/1 ------------------------------------------------------------
#
# Tokens: ``H<line index><line>`` for a verbatim header line, then the tree in
# pre-order (``N<node type>``, ``L<length>``, ``V<repr of a scalar>``), then
# ``C<comment>`` per comment.

_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_ENCODING_COOKIE = re.compile(rb"[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
_SCALARS = (str, bytes, bool, int, float, complex, types.NoneType, types.EllipsisType)
_NODE_FIELDS: dict[type[ast.AST], tuple[str, ...]] = {}


def _python_digest(content: bytes) -> str | None:
    try:
        content.decode("utf-8")
        with warnings.catch_warnings():
            # Hashing must not report the file's own SyntaxWarnings.
            warnings.simplefilter("ignore")
            tree = ast.parse(content)
        comments = (
            tuple(
                "C" + _normalized_comment(token.string)
                for token in tokenize.tokenize(io.BytesIO(content).readline)
                if token.type == tokenize.COMMENT
            )
            if b"#" in content
            else ()
        )
    except (
        SyntaxError,
        tokenize.TokenError,
        UnicodeDecodeError,
        ValueError,
        RecursionError,
    ):
        return None
    try:
        tree_tokens = _tree_tokens(tree)
    except _Unsupported:
        return None
    return _digest(PYTHON_RULE, (*_header_tokens(content), *tree_tokens, *comments))


def _header_tokens(content: bytes) -> Iterator[str]:
    """Lines 1 and 2 verbatim when they are a shebang or an encoding cookie."""
    for index, line in enumerate(content.splitlines()[:2]):
        if line.startswith(b"#!") or _ENCODING_COOKIE.match(line):
            yield f"H{index}{line.decode('utf-8')}"


def _normalized_comment(text: str) -> str:
    body = text[1:].strip()
    return f"# {body}" if body else "#"


def _clean_docstring(node: ast.AST) -> None:
    """Replace a module, class or function docstring by its cleaned text."""
    if not isinstance(node, _DOCSTRING_OWNERS) or not node.body:
        return
    first = node.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        first.value.value = "\n".join(
            line.rstrip() for line in inspect.cleandoc(first.value.value).split("\n")
        )


def _tree_tokens(tree: ast.AST) -> list[str]:
    """Serialise node types and ``_fields`` values in order, without positions."""
    tokens: list[str] = []
    stack: list[Any] = [tree]
    while stack:
        value = stack.pop()
        if isinstance(value, ast.AST):
            node_type = type(value)
            tokens.append("N" + node_type.__name__)
            _clean_docstring(value)
            stack.extend(getattr(value, name, None) for name in _fields(node_type))
        elif isinstance(value, list):
            tokens.append(f"L{len(value)}")
            stack.extend(reversed(value))
        elif isinstance(value, _SCALARS):
            tokens.append("V" + repr(value))
        else:
            raise _Unsupported(type(value).__name__)
    return tokens


def _fields(node_type: type[ast.AST]) -> tuple[str, ...]:
    """A node type's fields in reverse ``_fields`` order, without positions."""
    names = _NODE_FIELDS.get(node_type)
    if names is None:
        names = tuple(
            name
            for name in reversed(node_type._fields)
            if not (name == "kind" and issubclass(node_type, ast.Constant))
        )
        _NODE_FIELDS[node_type] = names
    return names


# markdown-text/1 ----------------------------------------------------------
#
# Tokens per line: ``v<raw line>`` (verbatim, raw line ending kept), ``b``
# (blank), ``p<prefix>`` then the normalised text and ``1``/``0`` for a hard
# break, or ``t<lead>``/``d<lead>`` (table or delimiter row) then the cell
# count, the cells and ``h`` when the row ends in a hard break.

_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_TERMINATOR = re.compile(r"(?:\r\n|\r|\n)\Z")
_FRONT_MATTER_OPEN = re.compile(r"---[ \t]*")
_FRONT_MATTER_CLOSE = re.compile(r"(?:---|\.\.\.)[ \t]*")
_BLOCKQUOTE = re.compile(r" {0,3}>")
_TABLE_ROW = re.compile(r" {0,3}\|")
_LINK_REFERENCE_DEFINITION = re.compile(r" {0,3}\[(?:\\.|[^\\\[\]])+\]:")
# Leading whitespace plus any list markers and the whitespace after them: the
# marker's spacing sets the item's content column, so it is kept exactly.
_LINE_PREFIX = re.compile(r"[ \t]*(?:(?:[-+*]|[0-9]{1,9}[.)])(?:[ \t]+|\Z))*")
_DELIMITER_CELL = re.compile(r":?-+:?")
_ASCII_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_TAG_START = re.compile(r"[A-Za-z/!?]")
_HTML_TERMINATORS = (("<!--", "-->"), ("<?", "?>"), ("<![CDATA[", "]]>"))


def _markdown_digest(content: bytes) -> str | None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return _digest(MARKDOWN_RULE, _markdown_tokens(text))


def _markdown_tokens(text: str) -> Iterator[str]:
    from heddle.kernel.knowledge_search import markdown_block_ranges

    lines = _LINE.findall(text)
    bodies = [_TERMINATOR.sub("", line) for line in lines]
    front_matter = _front_matter_lines(bodies)
    # The parser reads LF lines with the front matter blanked, so its ranges
    # are file lines and front matter cannot open a block in the body.
    parsed = "".join(
        "\n" if index < front_matter else body + "\n"
        for index, body in enumerate(bodies)
    )
    verbatim = set(range(front_matter))
    for start, end in markdown_block_ranges(parsed):
        verbatim.update(range(start, end))
    # A blockquote line, a link reference definition or a line leaving an
    # inline construct open may be continued by the following lines; those
    # stay verbatim up to the next blank line, which no inline construct
    # crosses.
    continued = False
    for index, (line, body) in enumerate(zip(lines, bodies, strict=True)):
        if index in verbatim:
            yield "v" + line
            continue
        if not body.strip(" \t"):
            continued = False
            yield "b"
            continue
        tokens: tuple[str, ...] | None
        if (
            continued
            or _BLOCKQUOTE.match(body)
            or _LINK_REFERENCE_DEFINITION.match(body)
        ):
            tokens = None
        elif _TABLE_ROW.match(body):
            tokens = _table_tokens(body)
        else:
            tokens = _prose_tokens(body)
        if tokens is None:
            continued = True
            yield "v" + line
        else:
            yield from tokens


def _front_matter_lines(bodies: list[str]) -> int:
    """Return how many leading lines are front matter (0 when there is none)."""
    if not bodies or not _FRONT_MATTER_OPEN.fullmatch(bodies[0]):
        return 0
    for index in range(1, len(bodies)):
        if _FRONT_MATTER_CLOSE.fullmatch(bodies[index]):
            return index + 1
    return 0


def _table_tokens(body: str) -> tuple[str, ...] | None:
    """A table row as its leading whitespace, trimmed cells and hard break.

    Only cell padding and delimiter dashes normalise: a pipe inside an escape,
    code span, angle-bracket span or link tail does not split a cell. None
    when the row leaves an inline construct open.
    """
    content = body.rstrip(" \t")
    trailing = body[len(content) :]
    hard_break = ("h",) if len(trailing) - len(trailing.rstrip(" ")) >= 2 else ()
    cells: list[str] = []
    start = index = 0
    while index < len(content):
        if content[index] == "|":
            cells.append(content[start:index].strip(" \t"))
            start = index = index + 1
            continue
        end = _token_end(content, index)
        if end is None:
            return None
        index = end
    cells.append(content[start:].strip(" \t"))
    lead = body[: len(body) - len(body.lstrip(" "))]
    filled = [cell for cell in cells if cell]
    if filled and all(_DELIMITER_CELL.fullmatch(cell) for cell in filled):
        cells = [re.sub("-+", "-", cell) for cell in cells]
        return ("d" + lead, str(len(cells)), *cells, *hard_break)
    return ("t" + lead, str(len(cells)), *cells, *hard_break)


def _prose_tokens(body: str) -> tuple[str, str, str] | None:
    """Normalise one prose line, or None when it cannot be classified safely."""
    prefix = _LINE_PREFIX.match(body)
    assert prefix is not None
    rest = body[prefix.end() :]
    content = rest.rstrip(" \t")
    trailing = rest[len(content) :]
    hard_break = len(trailing) - len(trailing.rstrip(" ")) >= 2
    text = _inline_text(content)
    if text is None:
        return None
    return "p" + prefix.group(), text, "1" if hard_break else "0"


def _inline_text(content: str) -> str | None:
    """Collapse whitespace runs outside verbatim spans; None if a span stays open."""
    pieces: list[str] = []
    index = 0
    while index < len(content):
        if content[index] in " \t":
            pieces.append(" ")
            index = _skip_spaces(content, index)
            continue
        end = _token_end(content, index)
        if end is None:
            return None
        pieces.append(content[index:end])
        index = end
    return "".join(pieces)


def _token_end(text: str, index: int) -> int | None:
    """End of the escape, verbatim span or single character at ``index``."""
    character = text[index]
    if character == "\\" and text[index + 1 : index + 2] in _ASCII_PUNCTUATION:
        return index + 2
    if character == "`":
        return _code_span_end(text, index)
    if character == "<":
        return _angle_span_end(text, index)
    if text.startswith("](", index):
        return _link_tail_end(text, index)
    return index + 1


def _code_span_end(text: str, start: int) -> int | None:
    """End of the code span opened by the backtick run at ``start``."""
    opener = start
    while opener < len(text) and text[opener] == "`":
        opener += 1
    width = opener - start
    index = opener
    while index < len(text):
        if text[index] != "`":
            index += 1
            continue
        run = index
        while run < len(text) and text[run] == "`":
            run += 1
        if run - index == width:
            return run
        index = run
    return None


def _angle_span_end(text: str, start: int) -> int | None:
    """End of an autolink, inline tag, comment or declaration at ``start``.

    None when the construct stays open past the line. A tag ends at the first
    ``>`` outside a quoted attribute value. A ``<`` opening none of these is
    plain text, or a verbatim span when a later ``>`` on the line closes it.
    """
    for opener, closer in _HTML_TERMINATORS:
        if text.startswith(opener, start):
            close = text.find(closer, start + len(opener))
            return None if close == -1 else close + len(closer)
    if _TAG_START.match(text, start + 1):
        quote = None
        for index in range(start + 1, len(text)):
            character = text[index]
            if quote is not None:
                if character == quote:
                    quote = None
            elif character in "\"'":
                quote = character
            elif character == ">":
                return index + 1
        return None
    close = text.find(">", start + 1)
    return start + 1 if close == -1 else close + 1


def _link_tail_end(text: str, start: int) -> int | None:
    """End of a link or image ``](destination "title")`` tail."""
    index = _skip_spaces(text, start + 2)
    if text.startswith("<", index):
        index += 1
        while index < len(text) and text[index] not in "<>":
            index += 2 if text[index] == "\\" else 1
        if index >= len(text) or text[index] != ">":
            return None
        index += 1
    else:
        depth = 0
        while index < len(text) and text[index] not in " \t":
            character = text[index]
            if character == "\\":
                index += 2
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                if depth == 0:
                    break
                depth -= 1
            index += 1
        index = min(index, len(text))
    index = _skip_spaces(text, index)
    if index < len(text) and text[index] in "\"'(":
        close = ")" if text[index] == "(" else text[index]
        index += 1
        while index < len(text) and text[index] != close:
            index += 2 if text[index] == "\\" else 1
        if index >= len(text):
            return None
        index = _skip_spaces(text, index + 1)
    if index < len(text) and text[index] == ")":
        return index + 1
    return None


def _skip_spaces(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index
