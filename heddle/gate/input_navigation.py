"""Lossless navigation of frozen Codex input; no source reads or publication."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

CODEX_INLINE_LIMIT = 1_048_576
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
