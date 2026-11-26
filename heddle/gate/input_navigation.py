"""Lossless navigation and routing of frozen gate input.

Pure planning over an assembled capture: the Codex byte-range index and the
Antigravity delivery route. No source reads or publication.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

CODEX_INLINE_LIMIT = 1_048_576
# agy delivers at most 192,000 bytes of its wrapped message (a 15-byte header,
# the content and a trailer of about 400 bytes). Content up to this many UTF-8
# bytes is sent inline with margin to spare.
ANTIGRAVITY_INLINE_LIMIT = 160_000
# Above this many UTF-8 bytes the lane refuses before any launch.
ANTIGRAVITY_CEILING = 250_000
# One agy read_file call returns at most 800 lines or 46,080 rendered bytes,
# each line rendered behind an "N: " prefix. A chunk of at most 36,864 raw
# bytes and 600 lines renders to at most 36,864 + 600 * len("999999: ") =
# 41,664 bytes, so it is returned whole even with six-digit line numbers.
_CHUNK_BYTES = 36_864
_CHUNK_LINES = 600
# agy strips code points in Unicode categories Cf, Co and Cn, and every Cc
# control except these, before the model sees inline content.
_STRIPPED_CATEGORIES = frozenset({"Cf", "Co", "Cn"})
_KEPT_CONTROLS = frozenset("\t\n\v\f\r\x85")
# agy wraps inline content in these tags; inline text that opens or closes
# one could be read as agy's own framing.
_WRAPPER_TAG = re.compile(r"</?(?:USER_REQUEST|CONTEXT_SUMMARY)", re.IGNORECASE)
_RANGE_BYTES = 32_768
_BEGIN_DIFF = b"\n===== BEGIN AUTHORITATIVE DIFF =====\n"
_END_DIFF = b"\n===== END AUTHORITATIVE DIFF ====="
_HEADERS = re.compile(rb"^diff --git [^\n]*", re.MULTILINE)
_READ_ORDER = (
    "context",
    "implementation",
    "tests",
    "guidance",
    "unclassified",
    "evidence",
)
NAVIGATION_POLICY = (
    "Codex captured-input navigation/v1 (operator-generated): read the complete "
    "frozen input using its byte-range index. Read instructions/context first, "
    "then implementation/configuration changes, tests and delivered guidance, "
    "before documentary evidence. The index's read_order is a permutation of "
    "all ranges, not a scope filter. Read every range completely in bounded "
    "chunks; retain the full scope and account for historical evidence too. "
    "Hashing or listing bytes is not model-visible reading. A truncated tool "
    "response is not complete coverage: continue the unread range. Unknown "
    "framing uses complete source-order navigation without claiming file "
    "classification. Do not substitute a fresh Git diff, modify captured input "
    "or treat unread evidence as assessed. Report any remaining coverage gap."
)


def navigation_index(text: str, diff_text: str) -> bytes:
    """Derive a complete UTF-8 partition and a presentation-only permutation.

    The milestone renderer currently puts the authoritative diff last. Select
    only that exact terminal framing with the frozen diff's header sequence;
    earlier embedded examples cannot become a substitute authoritative region.
    Unknown framing remains wholly readable through source-order ranges.
    """
    captured = text.encode("utf-8")
    sections = _sections(captured, diff_text.encode("utf-8"))
    ranges = []
    for start, stop, kind, path in sections:
        while start < stop:
            end = min(start + _RANGE_BYTES, stop)
            # Each range can be decoded independently, including long lines.
            while end < stop and captured[end] & 0xC0 == 0x80:
                end -= 1
            ranges.append({"start": start, "end": end, "kind": kind, "path": path})
            start = end
    order = sorted(
        range(len(ranges)),
        key=lambda index: _READ_ORDER.index(str(ranges[index]["kind"])),
    )
    return (
        json.dumps(
            {
                "schema": "heddle.codex-input-navigation/v1",
                "bytes": len(captured),
                "sha256": hashlib.sha256(captured).hexdigest(),
                "classification": "terminal-authoritative-diff"
                if len(sections) > 1
                else "source-order-fallback",
                "ranges": ranges,
                "read_order": order,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _sections(captured: bytes, diff: bytes) -> list[tuple[int, int, str, str | None]]:
    fallback: list[tuple[int, int, str, str | None]] = [
        (0, len(captured), "context", None)
    ]
    marker = captured.rfind(_BEGIN_DIFF)
    if marker < 0 or not captured.endswith(_END_DIFF):
        return fallback
    start, end = marker + len(_BEGIN_DIFF), len(captured) - len(_END_DIFF)
    body = captured[start:end]
    headers = list(_HEADERS.finditer(body))
    expected = [match.group() for match in _HEADERS.finditer(diff)]
    if (
        not headers
        or headers[0].start() != 0
        or [match.group() for match in headers] != expected
    ):
        return fallback
    sections: list[tuple[int, int, str, str | None]] = [(0, start, "context", None)]
    for index, header in enumerate(headers):
        stop = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        path = _header_path(header.group().decode("utf-8"))
        sections.append((start + header.start(), start + stop, _kind(path), path))
    # The closing delimiter is context, not an unaccounted trailing byte slice.
    sections.append((end, len(captured), "context", None))
    return sections


def _header_path(header: str) -> str | None:
    """Label unambiguous Git paths; exotic C/octal quoting stays unclassified."""
    body = header.removeprefix("diff --git ").rstrip("\r")
    if body.startswith('"'):
        try:
            decoder = json.JSONDecoder()
            left, consumed = decoder.raw_decode(body)
            right, consumed_right = decoder.raw_decode(body[consumed:].lstrip())
            if body[consumed:].lstrip()[consumed_right:].strip():
                return None
        except ValueError:
            return None
    elif body.startswith("a/") and body.count(" b/") == 1:
        left, right = body.split(" b/", 1)
        right = "b/" + right
    else:
        return None
    if (
        not isinstance(left, str)
        or not isinstance(right, str)
        or not left.startswith("a/")
        or not right.startswith("b/")
    ):
        return None
    return right[2:]


def _kind(path: str | None) -> str:
    if path is None:
        return "unclassified"
    parts = PurePosixPath(path).parts
    if parts and parts[0] == "docs" and "evidence" in parts:
        return "evidence"
    if any(part in {"test", "tests", "__tests__"} for part in parts):
        return "tests"
    if (parts and parts[0] == "docs") or PurePosixPath(path).suffix in {
        ".md",
        ".rst",
        ".txt",
    }:
        return "guidance"
    return "implementation"


@dataclass(frozen=True)
class CaptureChunk:
    """One chunk of a chunked capture: a half-open UTF-8 byte range and the
    SHA-256 of exactly those bytes."""

    start: int
    end: int
    sha256: str


@dataclass(frozen=True)
class CaptureRoute:
    """How an assembled Antigravity capture is delivered, decided once.

    ``size`` is the capture's UTF-8 byte count and ``ceiling`` the lane's
    refusal threshold. Only a ``chunked`` route has chunks; they partition the
    capture in order, each starting and ending on a character boundary.
    """

    kind: Literal["inline", "chunked", "refused"]
    size: int
    ceiling: int
    chunks: tuple[CaptureChunk, ...] = ()


def route_antigravity_capture(capture: str) -> CaptureRoute:
    """Route one assembled capture: inline, whole-readable chunks or refused.

    Size is checked first, so content never rescues an oversized capture.
    Content agy would alter inline (a stripped code point or a wrapper tag)
    is chunked at any size, because ``read_file`` returns file bytes
    unaltered.
    """
    raw = capture.encode("utf-8")
    size = len(raw)
    if size > ANTIGRAVITY_CEILING:
        return CaptureRoute("refused", size, ANTIGRAVITY_CEILING)
    if size <= ANTIGRAVITY_INLINE_LIMIT and not _altered_inline(capture):
        return CaptureRoute("inline", size, ANTIGRAVITY_CEILING)
    return CaptureRoute("chunked", size, ANTIGRAVITY_CEILING, _chunk_plan(raw))


def _altered_inline(capture: str) -> bool:
    if _WRAPPER_TAG.search(capture):
        return True
    for char in set(capture):
        category = unicodedata.category(char)
        if category in _STRIPPED_CATEGORIES or (
            category == "Cc" and char not in _KEPT_CONTROLS
        ):
            return True
    return False


def _chunk_plan(raw: bytes) -> tuple[CaptureChunk, ...]:
    chunks = []
    start = 0
    while start < len(raw):
        end = _chunk_end(raw, start)
        chunks.append(
            CaptureChunk(start, end, hashlib.sha256(raw[start:end]).hexdigest())
        )
        start = end
    return tuple(chunks)


def _chunk_end(raw: bytes, start: int) -> int:
    """The end of the chunk starting at ``start``, within both read limits."""
    limit = min(start + _CHUNK_BYTES, len(raw))
    newline = start - 1
    for _ in range(_CHUNK_LINES):
        newline = raw.find(b"\n", newline + 1, limit)
        if newline < 0:
            break
    else:
        # The line limit comes first: end after the last line it allows.
        return newline + 1
    if limit == len(raw):
        return limit
    # End after the last whole line in the window. A line longer than a
    # chunk is cut at a character boundary instead.
    last = raw.rfind(b"\n", start, limit)
    if last >= 0:
        return last + 1
    end = limit
    while raw[end] & 0xC0 == 0x80:
        end -= 1
    return end
