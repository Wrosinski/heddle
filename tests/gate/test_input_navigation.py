"""Pure byte-navigation contracts; no source discovery, provider or workflow.

The Codex index cases are survivor pins. The Antigravity routing cases below
them are red discriminators for antigravity-reviewer-lane-v1 (AC-6, AC-7 and
AC-8's preparation part), against the pure routing seam named in
``antigravity_route``.
"""

import hashlib
import json
import unicodedata

import pytest

from heddle.gate.input_navigation import navigation_index


def assert_partition(text, index):
    raw = text.encode("utf-8")
    assert index["bytes"] == len(raw)
    assert index["sha256"] == hashlib.sha256(raw).hexdigest()
    cursor, chunks = 0, []
    for row in index["ranges"]:
        assert row["start"] == cursor < row["end"] <= len(raw)
        assert row["end"] - row["start"] <= 32_768
        chunk = raw[row["start"] : row["end"]]
        assert chunk.decode("utf-8").encode("utf-8") == chunk
        chunks.append(chunk)
        cursor = row["end"]
    assert cursor == len(raw) and b"".join(chunks) == raw
    assert sorted(index["read_order"]) == list(range(len(chunks)))
    # Reading out of source order does not lose, duplicate or mutate a slice.
    read = {i: chunks[i] for i in index["read_order"]}
    assert b"".join(read[i] for i in sorted(read)) == raw


def framed(diff, *, prefix="Instructions and required context.\n"):
    return (
        prefix
        + "\n===== BEGIN AUTHORITATIVE DIFF =====\n"
        + diff
        + "\n===== END AUTHORITATIVE DIFF ====="
    )


@pytest.mark.parametrize("text", ["", "one line", "é🚀\r\n" * 20_000, "x" * 100_001])
def test_fallback_partitions_every_utf8_byte_including_huge_lines(text):
    encoded = navigation_index(text, "")
    assert navigation_index(text, "") == encoded
    index = json.loads(encoded)
    assert index["classification"] == "source-order-fallback"
    assert index["read_order"] == list(range(len(index["ranges"])))
    assert_partition(text, index)


def test_terminal_diff_navigation_reads_implementation_before_retained_evidence():
    diff = (
        "diff --git a/docs/proposals/evidence/history.json "
        "b/docs/proposals/evidence/history.json\n"
        "--- a/docs/proposals/evidence/history.json\n"
        "+++ b/docs/proposals/evidence/history.json\n"
        "@@ -1 +1 @@\n"
        "+" + "é🚀" * 20_000 + "\n"
        "+===== BEGIN AUTHORITATIVE DIFF =====\n"
        "+diff --git a/fake.py b/fake.py\n"
        "diff --git a/docs/workflow/rules.md b/docs/workflow/rules.md\n"
        "+Required delivered guidance.\n"
        "diff --git a/heddle/core.py b/heddle/core.py\n"
        "+answer = 42\n"
        "diff --git a/tests/test_core.py b/tests/test_core.py\n"
        "+assert answer == 42"
    )
    # An earlier embedded example has matching framing, but is not the terminal
    # native diff. The prefix itself is still completely accounted for.
    text = framed(diff, prefix=framed("diff --git a/old.py b/old.py\n+old") + "\n")
    index = json.loads(navigation_index(text, diff))
    assert index["classification"] == "terminal-authoritative-diff"
    assert_partition(text, index)
    ordered = [index["ranges"][i] for i in index["read_order"]]
    kinds = list(dict.fromkeys(row["kind"] for row in ordered))
    assert kinds == ["context", "implementation", "tests", "guidance", "evidence"]
    assert {row["path"] for row in ordered if row["path"]} == {
        "docs/proposals/evidence/history.json",
        "docs/workflow/rules.md",
        "heddle/core.py",
        "tests/test_core.py",
    }


@pytest.mark.parametrize("fault", ["suffix", "headers", "nested-marker", "no-header"])
def test_ambiguous_or_mismatched_diff_never_selects_an_arbitrary_region(fault):
    diff = "diff --git a/core.py b/core.py\n+changed"
    text = framed(diff)
    if fault == "suffix":
        text += "\nAnother runtime section"
    elif fault == "headers":
        diff = "diff --git a/another.py b/another.py\n+other"
    elif fault == "nested-marker":
        diff += "\n===== BEGIN AUTHORITATIVE DIFF =====\nnot a diff"
        text = framed(diff)
    else:
        diff = "No diff headers"
        text = framed(diff)
    index = json.loads(navigation_index(text, diff))
    assert index["classification"] == "source-order-fallback"
    assert_partition(text, index)


@pytest.mark.parametrize(
    ("header", "path"),
    [
        ("diff --git a/src/with spaces.py b/src/with spaces.py", "src/with spaces.py"),
        ('diff --git "a/src/café.py" "b/src/café.py"', "src/café.py"),
        ('diff --git "a/src/a\\tname.py" "b/src/a\\tname.py"', "src/a\tname.py"),
        (r'diff --git "a/src/\303\251.py" "b/src/\303\251.py"', None),
        ("diff --git a/old.py b/new.py", "new.py"),
        ("diff --git unknown header", None),
        ("diff --git a/src/a b/ambiguous.py b/src/a b/ambiguous.py", None),
    ],
)
def test_path_labels_do_not_change_byte_ownership(header, path):
    diff = header + "\r\nBinary files differ"
    text = framed(diff)
    index = json.loads(navigation_index(text, diff))
    assert_partition(text, index)
    changes = [row for row in index["ranges"] if row["kind"] != "context"]
    assert len(changes) == 1 and changes[0]["path"] == path
    assert changes[0]["kind"] == ("unclassified" if path is None else "implementation")


# --------------------------------------------------------------------------- #
# Antigravity routing (AC-6, AC-7, AC-8): one pure decision over the assembled
# capture's UTF-8 bytes. Every case below is a red discriminator until the
# routing seam exists; none reads files, launches a provider or prepares a gate.
# --------------------------------------------------------------------------- #
ROUTER = "route_antigravity_capture"
INLINE_LIMIT = 160_000
CEILING = 250_000
# agy's measured per-call read limit: one read_file call returns at most 800
# lines or 46,080 rendered bytes. The plan's margin renders every line with a
# six-digit "N: " prefix, so a chunk must fit even then.
READ_LINES = 800
READ_BYTES = 46_080
WORST_PREFIX = len(b"999999: ")
# 1-, 2-, 3- and 4-byte characters in one repeating unit.
MULTIBYTE = "aé€🚀"
# Characters whose JSON encoding is much longer than their UTF-8 bytes; none of
# them is in a class agy strips.
ESCAPE_HEAVY = '"\\\t\n\r\f\v<>&\x85\u2028\u2029'


def antigravity_route(capture: str):
    """Route one assembled capture through the pure Antigravity seam.

    Seam named by this scaffold, since the plan names none: the function
    returns a route with ``kind`` ("inline", "chunked" or "refused"), ``size``
    (the capture's UTF-8 bytes), ``ceiling`` and ``chunks``. Each chunk is a
    byte range into the capture, with ``start``, ``end`` and the ``sha256`` of
    its slice. Index format and line accounting stay delegated.

    The function's name and module and the route's field names and kind values
    are delegated: this helper is their only reader, and the cases below see
    the route only through the view it returns. The 160,000-byte inline limit
    and the 250,000-byte ceiling are the spec's and stay fixed.
    """
    from types import SimpleNamespace

    from heddle.gate import input_navigation

    router = getattr(input_navigation, ROUTER, None)
    assert callable(router), (
        f"FAIL AC-6/AC-7/AC-8: heddle.gate.input_navigation.{ROUTER} "
        "(Antigravity input routing) is not implemented"
    )
    route = router(capture)
    return SimpleNamespace(
        kind=route.kind,
        size=route.size,
        ceiling=route.ceiling,
        chunks=tuple(
            SimpleNamespace(start=chunk.start, end=chunk.end, sha256=chunk.sha256)
            for chunk in route.chunks
        ),
    )


def sized(total: int, unit: str = MULTIBYTE, *, around: str = "") -> str:
    """Exactly ``total`` UTF-8 bytes: ``around`` between runs of ``unit``."""
    unit_bytes = len(unit.encode("utf-8"))
    fill = total - len(around.encode("utf-8"))
    head = unit * (fill // 2 // unit_bytes)
    tail = unit * ((fill - len(head.encode("utf-8"))) // unit_bytes)
    text = head + around + tail
    text += "x" * (total - len(text.encode("utf-8")))
    assert len(text.encode("utf-8")) == total, "fixture precondition: exact size"
    return text


def route_plan(route) -> tuple:
    return (
        route.kind,
        route.size,
        tuple((chunk.start, chunk.end, chunk.sha256) for chunk in route.chunks),
    )


def assert_whole_readable_chunks(capture: str, route) -> None:
    """AC-7: the chunks partition the capture and each fits one read_file call."""
    raw = capture.encode("utf-8")
    assert route.kind == "chunked", f"FAIL AC-7: expected chunked, got {route.kind}"
    assert route.size == len(raw), (
        f"FAIL AC-7: route measured {route.size} bytes, capture has {len(raw)}"
    )
    assert route.chunks, "FAIL AC-7: a chunked route plans no chunks"
    cursor = 0
    for index, chunk in enumerate(route.chunks):
        assert chunk.start == cursor < chunk.end <= len(raw), (
            f"FAIL AC-7: chunk {index} [{chunk.start}, {chunk.end}) does not "
            f"continue the partition at byte {cursor}"
        )
        piece = raw[chunk.start : chunk.end]
        try:
            piece.decode("utf-8")
        except UnicodeDecodeError as error:
            pytest.fail(f"FAIL AC-7: chunk {index} splits a UTF-8 character: {error}")
        assert chunk.sha256 == hashlib.sha256(piece).hexdigest(), (
            f"FAIL AC-7: chunk {index}'s digest does not bind its byte range"
        )
        lines = piece.count(b"\n") + (0 if piece.endswith(b"\n") else 1)
        assert lines <= READ_LINES, f"FAIL AC-7: chunk {index} has {lines} lines"
        rendered = len(piece) + lines * WORST_PREFIX
        assert rendered <= READ_BYTES, (
            f"FAIL AC-7: chunk {index} renders to {rendered} bytes, above one "
            f"read_file call's {READ_BYTES}"
        )
        cursor = chunk.end
    assert cursor == len(raw), f"FAIL AC-7: bytes {cursor}..{len(raw)} are unplanned"
    assert b"".join(raw[chunk.start : chunk.end] for chunk in route.chunks) == raw


@pytest.mark.parametrize(
    "unit", [MULTIBYTE, ESCAPE_HEAVY], ids=["1-to-4-byte", "escape-heavy"]
)
def test_ac6_inline_limit_is_160000_utf8_bytes_of_the_capture(unit):
    """AC-6: a capture of exactly 160,000 UTF-8 bytes routes inline.

    Red discriminator. The fixtures fix the unit of measure: the 1-to-4-byte
    capture has far fewer characters than bytes, and the escape-heavy one's
    JSON-encoded line is far above agy's 192,000-byte cap. Measuring either
    characters or the NDJSON line moves one of the two boundaries.
    """
    capture = sized(INLINE_LIMIT, unit)
    if unit == MULTIBYTE:
        assert len(capture) < INLINE_LIMIT // 2, "fixture precondition: multi-byte"
    else:
        assert len(json.dumps(capture, ensure_ascii=False).encode()) > 192_000, (
            "fixture precondition: the NDJSON form exceeds the wrapped-message cap"
        )
    route = antigravity_route(capture)
    assert route.kind == "inline", (
        f"FAIL AC-6: a {INLINE_LIMIT}-byte capture routed {route.kind}"
    )
    assert route.size == INLINE_LIMIT, f"FAIL AC-6: measured {route.size} bytes"
    assert not route.chunks, "FAIL AC-6: an inline route plans chunks"


@pytest.mark.parametrize(
    "unit", [MULTIBYTE, ESCAPE_HEAVY], ids=["1-to-4-byte", "escape-heavy"]
)
def test_ac6_ac7_one_byte_over_the_inline_limit_routes_to_chunks(unit):
    """AC-6/AC-7: 160,001 bytes is chunked, and the chunks rebuild the capture.

    Red discriminator: counting characters would keep the multi-byte capture
    inline.
    """
    capture = sized(INLINE_LIMIT + 1, unit)
    route = antigravity_route(capture)
    assert route.kind == "chunked", (
        f"FAIL AC-6: a {INLINE_LIMIT + 1}-byte capture routed {route.kind}"
    )
    assert_whole_readable_chunks(capture, route)


def test_ac7_capture_of_exactly_250000_bytes_routes_to_chunks():
    """AC-7: the ceiling itself is still delivered, as whole-readable chunks."""
    capture = sized(CEILING, MULTIBYTE)
    route = antigravity_route(capture)
    assert route.kind == "chunked", (
        f"FAIL AC-7: a {CEILING}-byte capture routed {route.kind}"
    )
    assert_whole_readable_chunks(capture, route)


@pytest.mark.parametrize("total", [CEILING + 1, 1_000_000], ids=["one-over", "far"])
def test_ac8_capture_above_250000_bytes_is_refused_with_size_and_ceiling(total):
    """AC-8: above the ceiling the route is a refusal that plans nothing."""
    route = antigravity_route(sized(total, MULTIBYTE))
    assert route.kind == "refused", (
        f"FAIL AC-8: a {total}-byte capture routed {route.kind}"
    )
    assert (route.size, route.ceiling) == (total, CEILING), (
        f"FAIL AC-8: refusal names {route.size} of {route.ceiling}"
    )
    assert not route.chunks, "FAIL AC-8: a refused route plans chunks"


def test_ac8_ceiling_refusal_outranks_unsafe_content():
    """AC-8: size is checked first; a tag or stripped code point never rescues it."""
    capture = sized(CEILING + 1, MULTIBYTE, around="<USER_REQUEST>\u200b")
    route = antigravity_route(capture)
    assert route.kind == "refused", (
        f"FAIL AC-8: an oversized unsafe capture routed {route.kind}"
    )


@pytest.mark.parametrize(
    ("code_point", "category"),
    [
        ("\x00", "Cc"),
        ("\x07", "Cc"),
        ("\x1b", "Cc"),
        ("\x7f", "Cc"),
        ("\x80", "Cc"),
        ("\x9f", "Cc"),
        ("\u00ad", "Cf"),
        ("\u200b", "Cf"),
        ("\u200d", "Cf"),
        ("\u2060", "Cf"),
        ("\ufeff", "Cf"),
        ("\U000e0041", "Cf"),
        ("\ue000", "Co"),
        ("\U000f0000", "Co"),
        ("\U0010fffd", "Co"),
        ("\ufdd0", "Cn"),
        ("\uffff", "Cn"),
        ("\U0010ffff", "Cn"),
    ],
    ids=lambda value: f"U+{ord(value):04X}" if len(value) == 1 else value,
)
def test_ac7_stripped_class_code_point_routes_a_small_capture_to_chunks(
    code_point, category
):
    """AC-7: one code point agy strips sends even a tiny capture to chunks."""
    assert unicodedata.category(code_point) == category, "fixture precondition"
    capture = f"Review the captured input.\nBefore {code_point} after.\n"
    route = antigravity_route(capture)
    assert route.kind == "chunked", (
        f"FAIL AC-7: U+{ord(code_point):04X} ({category}) routed {route.kind}"
    )
    assert_whole_readable_chunks(capture, route)


def test_ac7_allowed_controls_and_separators_stay_inline():
    """AC-7 control: the Cc code points agy keeps, and line separators, stay inline."""
    capture = "tab\tnewline\nvtab\vform\ffeed\rnext\x85line\u2028para\u2029end"
    for code_point in "\t\n\v\f\r\x85":
        assert unicodedata.category(code_point) == "Cc", "fixture precondition"
    route = antigravity_route(capture)
    assert route.kind == "inline", f"FAIL AC-7: controls agy keeps routed {route.kind}"


@pytest.mark.parametrize(
    "tag",
    [
        "<USER_REQUEST>",
        "</USER_REQUEST>",
        "<user_request>",
        "<uSeR_rEqUeSt attr='1'>",
        "<USER_REQUESTED>",
        "<CONTEXT_SUMMARY",
        "</Context_Summary>",
    ],
    ids=[
        "open",
        "close",
        "lower-case",
        "mixed-case-attribute",
        "longer-name",
        "context-unclosed",
        "context-close-title-case",
    ],
)
def test_ac7_wrapper_tag_routes_a_small_capture_to_chunks(tag):
    """AC-7: ``<`` or ``</`` then a wrapper name, in any case, means chunks."""
    capture = f"Diff line before.\n+    text = '{tag}'\nDiff line after.\n"
    route = antigravity_route(capture)
    assert route.kind == "chunked", f"FAIL AC-7: {tag!r} routed {route.kind}"
    assert_whole_readable_chunks(capture, route)


@pytest.mark.parametrize(
    "near_miss",
    [
        "USER_REQUEST",
        "CONTEXT_SUMMARY>",
        "/USER_REQUEST>",
        "<USER_REQ",
        "<CONTEXT_SUMMAR",
        "< USER_REQUEST>",
        "<USER-REQUEST>",
        "&lt;USER_REQUEST&gt;",
    ],
    ids=[
        "bare-name",
        "context-without-open",
        "slash-without-open",
        "truncated-name",
        "truncated-context",
        "space-after-bracket",
        "hyphenated",
        "html-escaped",
    ],
)
def test_ac7_wrapper_tag_near_misses_stay_inline(near_miss):
    """AC-7 controls: the name without ``<`` and a truncated tag stay inline."""
    capture = f"Diff line before.\n+    text = '{near_miss}'\nDiff line after.\n"
    route = antigravity_route(capture)
    assert route.kind == "inline", f"FAIL AC-7: {near_miss!r} routed {route.kind}"


@pytest.mark.parametrize(
    "capture",
    [
        # Cuts at any multiple of 4 land inside a character of this line.
        "a" + "🚀" * 49_999 + "\n",
        "\n" * 200_000,
        "".join("é" * (index % 97) + "€\n" for index in range(2_400)),
        # Below the inline limit, but unsafe, and longer than one read.
        "\u200b" + "€" * 40_000,
    ],
    ids=["long-4-byte-line", "dense-newlines", "mixed-lines", "unsafe-long-line"],
)
def test_ac7_chunks_fit_one_read_for_long_and_dense_lines(capture):
    """AC-7: chunks honor both read limits and never split a character."""
    route = antigravity_route(capture)
    assert_whole_readable_chunks(capture, route)
    assert len(route.chunks) > 1, "FAIL AC-7: one chunk cannot fit one read here"


def test_ac7_routing_is_a_pure_function_of_the_capture():
    """AC-7: the same capture always yields the same frozen plan."""
    capture = sized(INLINE_LIMIT + 4_096, MULTIBYTE, around="</CONTEXT_SUMMARY>")
    first = antigravity_route(capture)
    assert route_plan(first) == route_plan(antigravity_route(capture)), (
        "FAIL AC-7: routing the same capture twice planned different chunks"
    )
    assert first.kind == "chunked"
