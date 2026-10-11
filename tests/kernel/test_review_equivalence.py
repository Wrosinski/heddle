"""
Review-equivalence identity rules: the Python and Markdown rules and the
review-equivalence predicate (AC-1, AC-2).

Pure kernel tests. Each pair is hashed by the production identity function and
the two identities are compared; no rule is re-implemented as an oracle. Python
pairs come from the pinned ruff formatter, run once over every fixture input.
Markdown pairs are Prettier-shaped by hand because the host has no Node
toolchain.

Activation seams (asserted at call time through
``tests/change_impact_identity_helpers.py``):

- ``heddle.kernel.review_equivalence.equivalence_identity(path, content)`` and
  ``review_equivalence.review_equivalent(left, right)``;
- ``EvidenceObservation.equivalence``, computed by ``build_source_evidence``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.change_impact_identity_helpers import (
    identity,
    identity_api,
    markdown_rule_id,
    observation,
    python_rule_id,
    recorded_equivalence,
    with_rule,
    without_equivalence,
)

PYTHON_PATH = "src/quote.py"
MARKDOWN_PATH = "docs/plan.md"
FOREIGN_PYTHON_RULE = "python-tree/1+py2.7"
FOREIGN_MARKDOWN_RULE = "markdown-text/1+mdit3.0"

# Inputs the pinned ruff rewrites; each output must keep the input's identity.
FORMATTER_INPUTS = {
    "quotes": "x = 'a'\ny = 'it\\'s'\n",
    "trailing-commas-and-splits": (
        "result = compute_the_total(first_argument_value, second_argument_value, "
        "third_argument_value, fourth_argument_value)\n"
    ),
    "magic-trailing-comma": "values = [1, 2,]\n",
    "parenthesisation": "if (ready):\n    pass\nvalue = (1)\n",
    "line-joins": "total = (1 +\n         2)\nitems = [\n    1,\n    2\n]\n",
    "blank-lines": "import os\ndef f():\n    pass\n\n\n\n\nclass A:\n    pass\n",
    "u-prefixes": "s = u'text'\nt = U\"more\"\n",
    "docstring-indentation": (
        'def f():\n    """Summary line.   \n\n      Indented detail.  \n'
        '        Deeper.\n    """\n    return 1\n'
    ),
    "tab-indented-docstring": (
        'def f():\n\t"""Summary.\n\n\tDetail line.\n\t"""\n\treturn 1\n'
    ),
    "class-docstring-blank-lines": 'class K:\n\n\n    """Doc."""\n    x = 1\n',
    "module-docstring-trailing-whitespace": (
        '"""Module summary.   \n\nDetail.  \n"""\nx = 1\n'
    ),
    "comment-spacing": "x = 1 #c\n#comment   \ny = 2\n",
    "type-ignore-comment-spacing": "x = f()  #type: ignore[attr-defined]\n",
    "trailing-whitespace": "x = 1   \ny = 2\t\n",
    "shebang-kept": "#!/usr/bin/env python3\nx = 'a'\n",
}

# Second sides that change meaning; each names the defect it would expose.
PYTHON_DIFFERENT = {
    # The tree must keep operators.
    "operator": ("total = a + b\n", "total = a - b\n"),
    # The tree must keep literal values.
    "literal-value": ("limit = 10\n", "limit = 11\n"),
    # Whitespace inside a string literal is data, not layout.
    "string-literal-whitespace": ('label = "a b"\n', 'label = "a  b"\n'),
    # Only docstrings are cleaned; another multi-line literal keeps its indent.
    "multi-line-literal-indentation": (
        'x = 1\ntext = """\n    indented\n"""\n',
        'x = 1\ntext = """\nindented\n"""\n',
    ),
    # Comment text is part of the identity.
    "comment-word": ("x = 1  # keep cache\n", "x = 1  # drop cache\n"),
    # Docstring text is part of the identity.
    "docstring-word": (
        'def f():\n    """Return the value."""\n',
        'def f():\n    """Return the values."""\n',
    ),
    # A shebang is verbatim; comment spacing rules must not apply to it.
    "shebang": ("#!/usr/bin/env python3\nx = 1\n", "# !/usr/bin/env python3\nx = 1\n"),
    # An encoding cookie on line 2 is verbatim too.
    "encoding-cookie": (
        "#!/usr/bin/env python3\n#coding:utf-8\nx = 1\n",
        "#!/usr/bin/env python3\n# coding:utf-8\nx = 1\n",
    ),
    # Statement order, including imports, is part of the tree.
    "import-order": ("import os\nimport sys\n", "import sys\nimport os\n"),
    # A type-ignore pragma's text is kept with the comments.
    "type-ignore-text": (
        "value = load()  # type: ignore[attr-defined]\n",
        "value = load()  # type: ignore[arg-type]\n",
    ),
}

NO_IDENTITY = {
    "syntax-error": (PYTHON_PATH, b"def broken(:\n    pass\n"),
    "not-utf8": (PYTHON_PATH, b"name = '\xe9'\n"),
    "text-suffix": ("notes.txt", b"x = 1\n"),
    "suffix-after-py": ("src/quote.py.orig", b"x = 1\n"),
    "suffix-after-md": ("docs/plan.md.bak", b"one  two\n"),
    "no-suffix": ("README", b"# Title\n"),
}

# Prettier-shaped rewrites (table padding, dashes, spaces, trailing space, CRLF).
MARKDOWN_EQUAL = {
    "table-padding-and-delimiter-dashes": (
        "| Name | Value |\n|---|:---:|\n| alpha | 1 |\n| b | 22 |\n",
        "| Name  | Value |\n| ----- | :---: |\n| alpha |   1   |\n| b     |  22   |\n",
    ),
    "internal-spaces-and-tabs": ("one  two\tthree   four\n", "one two three four\n"),
    "trailing-space-and-tab": ("text \nmore\t\n", "text\nmore\n"),
    "crlf-line-endings": (
        "Intro  text\r\n\r\n| a | b |\r\n|---|---|\r\n",
        "Intro text\n\n| a | b |\n|---|---|\n",
    ),
    "inline-html-paragraph": ("<span>x</span> a  b\n", "<span>x</span> a b\n"),
    # Spacing outside a verbatim angle-bracket span still normalises.
    "spacing-outside-an-angle-bracket-span": (
        "See <https://example.com/a b> today.\n",
        "See  <https://example.com/a b>  today.\n",
    ),
    "spacing-around-an-autolink": (
        "Visit <https://example.com/x> now.\n",
        "Visit  <https://example.com/x>   now.\n",
    ),
    "prose-after-front-matter": (
        "---\ntitle: x\n---\n\nprose  here \n",
        "---\ntitle: x\n---\n\nprose here\n",
    ),
    "prose-after-a-closed-fence": (
        "```\na  b\n```\n\nafter  text\n",
        "```\na  b\n```\n\nafter text\n",
    ),
    "prose-after-a-fence-with-an-indented-backtick-run": (
        "  ```\naaa\n     ```\nx = 1\n```\nafter  text\n",
        "  ```\naaa\n     ```\nx = 1\n```\nafter text\n",
    ),
    "prettier-plan-document": (
        "# Plan \n\n"
        "Intro with  two   spaces and a [link](docs/a.md) and `code  span`.\n\n"
        "## Milestones\n\n"
        "| Id | Scope | Status |\n|--|:--|--:|\n"
        "| m1 | identity rules | done |\n| m2 |  explanations  | open |\n\n"
        "- first item  with  gap\n- second item\n\n"
        "```python\nx  =  1\n```\n",
        "# Plan\n\n"
        "Intro with two spaces and a [link](docs/a.md) and `code  span`.\n\n"
        "## Milestones\n\n"
        "| Id  | Scope          | Status |\n| --- | :------------- | -----: |\n"
        "| m1  | identity rules |   done |\n| m2  | explanations   |   open |\n\n"
        "- first item with gap\n- second item\n\n"
        "```python\nx  =  1\n```\n",
    ),
}

# AC-2's Different list outside code and HTML blocks.
MARKDOWN_DIFFERENT = {
    "leading-whitespace": ("  text\n", "text\n"),
    "blank-line": ("first\n\nsecond\n", "first\nsecond\n"),
    "list-marker": ("- item\n", "* item\n"),
    "emphasis-marker": ("an *important* word\n", "an _important_ word\n"),
    "escape": ("a \\* b\n", "a * b\n"),
    "hard-break": ("line  \nnext\n", "line \nnext\n"),
    "alignment-colons": ("| a | b |\n| :-- | --- |\n", "| a | b |\n| --- | --- |\n"),
    "fenced-code": ("```\na  b\n```\n", "```\na b\n```\n"),
    "indented-code": ("text\n\n    a  b\n", "text\n\n    a b\n"),
    "code-span": ("use `a  b` here\n", "use `a b` here\n"),
    "code-span-across-lines": ("a `b  c\nd` e\n", "a `b c\nd` e\n"),
    "front-matter": ("---\ntitle:  x\n---\nbody\n", "---\ntitle: x\n---\nbody\n"),
    "link-destination": ("[guide](<docs/a b.md>)\n", "[guide](<docs/a  b.md>)\n"),
    # An inline angle-bracket span is kept verbatim, spacing included.
    "angle-bracket-span-spacing": (
        "See <https://example.com/a b> today.\n",
        "See <https://example.com/a  b> today.\n",
    ),
    "image-title": ('![logo](img.png "A  title")\n', '![logo](img.png "A title")\n'),
    "link-reference-definition": ('[ref]: /url "a  b"\n', '[ref]: /url "a b"\n'),
    "rewrap": ("one two three\n", "one two\nthree\n"),
    # A link reference definition stays exact wherever CommonMark finds one.
    "link-reference-definition-on-a-list-marker-line": (
        '- [ref]: /url "a  b"\n',
        '- [ref]: /url "a b"\n',
    ),
    "link-reference-definition-at-an-ordered-item-content-indent": (
        '1.  item\n\n    [ref]: /url "a  b"\n',
        '1.  item\n\n    [ref]: /url "a b"\n',
    ),
    "link-reference-definition-in-a-nested-list": (
        '- a\n\n  - [ref]: /url "a  b"\n',
        '- a\n\n  - [ref]: /url "a b"\n',
    ),
    "link-reference-definition-with-a-two-line-label": (
        '[ref\nlabel]: /url "t  t"\n',
        '[ref\nlabel]: /url "t t"\n',
    ),
    "duplicate-link-reference-definition-in-a-list-item": (
        '[ref]: /a\n\n- [ref]: /b "x  y"\n',
        '[ref]: /a\n\n- [ref]: /b "x y"\n',
    ),
    # A table row keeps code spans, link titles and hard breaks exact.
    "pipe-spacing-inside-a-code-span-in-a-table-row": (
        "| `a | b` |\n",
        "| `a|b` |\n",
    ),
    "pipe-spacing-inside-a-link-title-in-a-table-row": (
        '| [x](url "a | b") |\n',
        '| [x](url "a|b") |\n',
    ),
    "hard-break-on-a-table-row": (
        "| a | b |  \n| c | d |\n",
        "| a | b |\n| c | d |\n",
    ),
}

# Whitespace inside CommonMark blocks whose boundaries a hand-written scanner
# gets wrong; each side differs only by doubled spaces inside the block.
MARKDOWN_FAIL_CLOSED = {
    "multi-line-html-comment": (
        "<!--\nnote\n\nkept  apart\n-->\n",
        "<!--\nnote\n\nkept apart\n-->\n",
    ),
    "div-body": ("<div>\nalpha  beta\n</div>\n", "<div>\nalpha beta\n</div>\n"),
    "pre-after-list-marker": (
        "- <pre>\n  alpha  beta\n  </pre>\n",
        "- <pre>\n  alpha beta\n  </pre>\n",
    ),
    "four-space-backtick-run-after-unindented-fence": (
        "```\naaa\n    ```\nx  =  1\n```\n",
        "```\naaa\n    ```\nx = 1\n```\n",
    ),
    "four-space-run-after-three-space-fence": (
        "   ```\nalpha\n    ```\nbeta  gamma\n```\n",
        "   ```\nalpha\n    ```\nbeta gamma\n```\n",
    ),
    "five-space-run-after-two-space-fence": (
        "  ```\naaa\n     ```\nx  =  1\n```\nafter\n",
        "  ```\naaa\n     ```\nx = 1\n```\nafter\n",
    ),
    "fence-three-spaces-after-list-marker": (
        "-   ```\n    y  =  2\n    ```\n",
        "-   ```\n    y = 2\n    ```\n",
    ),
    "fence-in-blockquote": ("> ```\n> a  b\n> ```\n", "> ```\n> a b\n> ```\n"),
    "fence-below-front-matter": (
        "---\ntitle: x\nowner: y\n---\n\n```\nx  y\n```\n",
        "---\ntitle: x\nowner: y\n---\n\n```\nx y\n```\n",
    ),
    "table-row-indentation": (" | a | b |\n", "| a | b |\n"),
    "list-item-indented-code": ("-     code  x\n", "-     code x\n"),
    "blockquote-line": ("> a  b\n", "> a b\n"),
    # Verbatim lines keep their raw line endings; only other lines take LF.
    "crlf-inside-fenced-code": (
        "Intro\r\n\r\n```\r\ncode\r\n```\r\n",
        "Intro\n\n```\ncode\n```\n",
    ),
    # A line continuing an inline construct opened on an earlier paragraph line
    # stays exact, though the line alone looks like prose.
    "middle-line-of-a-code-span": ("a `b\nc  d\ne` f\n", "a `b\nc d\ne` f\n"),
    "link-title-on-a-continuation-line": (
        '[text](url\n"title  two")\n',
        '[text](url\n"title two")\n',
    ),
    "link-reference-definition-title-on-the-next-line": (
        '[ref]: /url\n"a  b"\n',
        '[ref]: /url\n"a b"\n',
    ),
    "inline-html-attribute-on-a-continuation-line": (
        'See <a\nhref="x  y">t</a>\n',
        'See <a\nhref="x y">t</a>\n',
    ),
    "quoted-angle-bracket-in-a-multi-line-attribute": (
        'See <a title="x > y\nz  w">t</a>\n',
        'See <a title="x > y\nz w">t</a>\n',
    ),
    "inline-html-comment-across-lines": (
        "a <!-- x > y\nz  w -->\n",
        "a <!-- x > y\nz w -->\n",
    ),
    "code-span-continued-from-a-blockquote": (
        "> a `b\nc  d\ne` f\n",
        "> a `b\nc d\ne` f\n",
    ),
}


def _identities(path: str, left: str | bytes, right: str | bytes):
    left_bytes = left.encode() if isinstance(left, str) else left
    right_bytes = right.encode() if isinstance(right, str) else right
    assert left_bytes != right_bytes, "fixture pair must differ in raw bytes"
    return identity(path, left_bytes), identity(path, right_bytes)


def test_ac1_ruff_formatted_python_keeps_its_identity(tmp_path) -> None:
    """AC-1 Equal: the pinned ruff's output has the input's identity."""
    identity_api()
    ruff = Path(sys.executable).with_name("ruff")
    assert ruff.is_file(), (
        f"the pinned ruff formatter is missing at {ruff}; install the dev extra"
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name, source in FORMATTER_INPUTS.items():
        (inputs / f"{name}.py").write_bytes(source.encode())
    completed = subprocess.run(
        [str(ruff), "format", "--isolated", "--no-cache", str(inputs)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    unchanged = []
    changed_identity = []
    for name, source in FORMATTER_INPUTS.items():
        formatted = (inputs / f"{name}.py").read_bytes()
        if formatted == source.encode():
            unchanged.append(name)
            continue
        before = identity(PYTHON_PATH, source.encode())
        after = identity(PYTHON_PATH, formatted)
        if before is None or after is None or before != after:
            changed_identity.append((name, formatted.decode()))
    assert not unchanged, f"ruff left these fixture inputs unchanged: {unchanged}"
    assert not changed_identity, (
        "FAIL AC-1: ruff-formatted Python no longer has its source's identity: "
        f"{changed_identity}"
    )


@pytest.mark.parametrize(
    ("left", "right"), PYTHON_DIFFERENT.values(), ids=list(PYTHON_DIFFERENT)
)
def test_ac1_meaningful_python_changes_change_identity(left, right) -> None:
    """AC-1 Different: operators, literals, comments, docstrings, header, order."""
    before, after = _identities(PYTHON_PATH, left, right)
    assert before is not None and after is not None, (
        "FAIL AC-1: a parsing Python file has no identity"
    )
    assert before != after, (
        f"FAIL AC-1: a meaningful Python change kept its identity: {right!r}"
    )


@pytest.mark.parametrize(
    ("path", "content"), NO_IDENTITY.values(), ids=list(NO_IDENTITY)
)
def test_ac1_unparseable_python_and_other_suffixes_have_no_identity(
    path, content
) -> None:
    """AC-1: no identity for a non-parsing file; only .py and .md have rules."""
    assert identity(path, content) is None, (
        f"FAIL AC-1: {path} must have raw identity only, got an equivalence identity"
    )


def test_ac1_python_rule_id_names_the_running_interpreter() -> None:
    """AC-1: the rule id carries the interpreter's major and minor version."""
    value = identity(PYTHON_PATH, b"x = 1\n")
    assert value is not None, "FAIL AC-1: a parsing Python file has no identity"
    rule, digest = value
    assert rule == python_rule_id(), (
        f"FAIL AC-1: Python rule id {rule!r} does not name this interpreter"
    )
    assert isinstance(digest, str) and digest


def test_ac1_identity_recorded_under_another_interpreter_gives_no_equivalence() -> None:
    """AC-1: an identity recorded under another interpreter compares raw."""
    api = identity_api()
    recorded = observation(PYTHON_PATH, b"x = 'a'\n")
    current = observation(PYTHON_PATH, b'x = "a"\n')
    assert recorded_equivalence(recorded) == identity(PYTHON_PATH, b"x = 'a'\n")
    assert api.review_equivalent(recorded, current), (
        "FAIL AC-1: a ruff reflow is not review-equivalent under one interpreter"
    )
    assert not api.review_equivalent(
        with_rule(recorded, FOREIGN_PYTHON_RULE), current
    ), "FAIL AC-1: an identity recorded under another interpreter was trusted"


def test_ac1_review_equivalence_holds_by_raw_identity_or_one_rule_digest() -> None:
    """AC-1/AC-2: equal raw identity, or one rule id with equal digests."""
    api = identity_api()
    assert api.review_equivalent(
        observation("notes.txt", b"plain  text\n"),
        observation("notes.txt", b"plain  text\n"),
    ), "FAIL AC-1: equal raw identity is not review-equivalent"
    assert api.review_equivalent(
        observation(PYTHON_PATH, b"x = 'a'\n"), observation(PYTHON_PATH, b'x = "a"\n')
    ), "FAIL AC-1: a Python reflow is not review-equivalent"
    assert api.review_equivalent(
        observation(MARKDOWN_PATH, b"one  two\n"),
        observation(MARKDOWN_PATH, b"one two\n"),
    ), "FAIL AC-2: a Markdown re-spacing is not review-equivalent"


PREDICATE_FAILS_CLOSED = {
    # Different digests under one rule.
    "content-change": lambda: (
        observation(PYTHON_PATH, b"x = 1\n"),
        observation(PYTHON_PATH, b"x = 2\n"),
    ),
    # A suffix without a rule compares raw only.
    "rule-less-suffix": lambda: (
        observation("notes.txt", b"a  b\n"),
        observation("notes.txt", b"a b\n"),
    ),
    # Equivalence never crosses paths.
    "other-path": lambda: (
        observation("src/a.py", b"x = 'a'\n"),
        observation("src/b.py", b'x = "a"\n'),
    ),
    # The executable bit is part of raw identity and is never bridged.
    "executable-bit": lambda: (
        observation(PYTHON_PATH, b"x = 'a'\n"),
        observation(PYTHON_PATH, b'x = "a"\n', executable=True),
    ),
    # An absent path matches only by raw identity, even against empty bytes.
    "path-absent": lambda: (
        observation(PYTHON_PATH, b""),
        observation(PYTHON_PATH, b"", kind="missing"),
    ),
    # A record without an equivalence identity compares raw.
    "record-without-equivalence": lambda: (
        without_equivalence(observation(PYTHON_PATH, b"x = 'a'\n")),
        observation(PYTHON_PATH, b'x = "a"\n'),
    ),
    # A rule this build cannot compute gives no equivalence on either side.
    "unknown-rule-on-both-sides": lambda: (
        with_rule(observation(PYTHON_PATH, b"x = 'a'\n"), FOREIGN_PYTHON_RULE),
        with_rule(observation(PYTHON_PATH, b'x = "a"\n'), FOREIGN_PYTHON_RULE),
    ),
}


@pytest.mark.parametrize(
    "pair", PREDICATE_FAILS_CLOSED.values(), ids=list(PREDICATE_FAILS_CLOSED)
)
def test_ac1_review_equivalence_fails_closed(pair) -> None:
    """AC-1/AC-2: same path, kind, mode and computable rule, or raw only."""
    api = identity_api()
    left, right = pair()
    assert not api.review_equivalent(left, right), (
        f"FAIL AC-1: review equivalence held where it must not: {left} / {right}"
    )


@pytest.mark.parametrize(
    ("left", "right"), MARKDOWN_EQUAL.values(), ids=list(MARKDOWN_EQUAL)
)
def test_ac2_prettier_shaped_markdown_keeps_its_identity(left, right) -> None:
    """AC-2 Equal: table padding, dashes, inner spaces, trailing space, CRLF."""
    before, after = _identities(MARKDOWN_PATH, left, right)
    assert before is not None, "FAIL AC-2: a Markdown file has no identity"
    assert before == after, (
        f"FAIL AC-2: a formatting-only Markdown change altered identity: {right!r}"
    )


@pytest.mark.parametrize(
    ("left", "right"), MARKDOWN_DIFFERENT.values(), ids=list(MARKDOWN_DIFFERENT)
)
def test_ac2_markdown_changes_outside_the_equal_list_change_identity(
    left, right
) -> None:
    """AC-2 Different: structure, markers, hard breaks, code, links, rewraps."""
    before, after = _identities(MARKDOWN_PATH, left, right)
    assert before is not None and after is not None, (
        "FAIL AC-2: a Markdown file has no identity"
    )
    assert before != after, (
        f"FAIL AC-2: a meaningful Markdown change kept its identity: {right!r}"
    )


@pytest.mark.parametrize(
    ("left", "right"), MARKDOWN_FAIL_CLOSED.values(), ids=list(MARKDOWN_FAIL_CLOSED)
)
def test_ac2_whitespace_inside_commonmark_blocks_changes_identity(left, right) -> None:
    """AC-2: code, HTML, front-matter-shifted, table and blockquote lines stay exact."""
    before, after = _identities(MARKDOWN_PATH, left, right)
    assert before is not None and after is not None, (
        "FAIL AC-2: a Markdown file has no identity"
    )
    assert before != after, (
        f"FAIL AC-2: whitespace inside a verbatim region was normalised: {right!r}"
    )


def test_ac2_markdown_rule_id_names_the_installed_parser_version() -> None:
    """AC-2: the rule id carries markdown-it-py's major and minor version."""
    value = identity(MARKDOWN_PATH, b"# Title\n\ntext\n")
    assert value is not None, "FAIL AC-2: a Markdown file has no identity"
    assert value[0] == markdown_rule_id(), (
        f"FAIL AC-2: Markdown rule id {value[0]!r} does not name the parser version"
    )


def test_ac2_identity_recorded_under_another_parser_version_gives_no_equivalence() -> (
    None
):
    """AC-2: an identity recorded under another parser version compares raw."""
    api = identity_api()
    recorded = observation(MARKDOWN_PATH, b"one  two\n")
    current = observation(MARKDOWN_PATH, b"one two\n")
    assert api.review_equivalent(recorded, current), (
        "FAIL AC-2: a Markdown re-spacing is not review-equivalent"
    )
    assert not api.review_equivalent(
        with_rule(recorded, FOREIGN_MARKDOWN_RULE), current
    ), "FAIL AC-2: an identity recorded under another parser version was trusted"
