from __future__ import annotations

import re
from collections.abc import Iterator

from heddle.kernel.markdown_structure import MarkdownLine, markdown_lines

# The section extractors share two rules, both owned by
# `heddle.kernel.markdown_structure.markdown_lines`:
# - Verbatim blocks: a line a CommonMark fenced code block or HTML block (an
#   HTML comment included) covers, at any container depth, is content, never
#   structure. A `## ` line in such a block (a rejected-layout example, a
#   diff, sample markdown, a commented-out draft) neither ends a section nor
#   starts one, and a `### Milestone mN:` or `### AC-N:` lookalike in one
#   neither starts nor switches one. Indented code is not verbatim here, and
#   a literal fence marker in it hides nothing.
# - Lines: lines end at CRLF, CR and LF, as CommonMark counts them, and a
#   selected span keeps each line's own ending, so an embedded section is its
#   source bytes. Any other `str.splitlines` boundary stays inside its line.
#   Only whitespace at the span's two ends is stripped.
# - Frontmatter: each extractor takes a whole document (`content` is a spec
#   or plan file as written), so its frontmatter is metadata, never a section;
#   a section re-read after extraction is a cut block with no frontmatter.

_ACCEPTANCE_CRITERIA = "## Acceptance Criteria"
_AC_HEADING = re.compile(r"^### (AC-[0-9]+[a-z]?):")


def _span(raws: list[str]) -> str:
    return "".join(raws).strip()


def extract_markdown_h2_section(content: str, heading: str) -> str:
    return extract_markdown_section(content, f"## {heading}")


def extract_markdown_section(content: str, heading_line: str) -> str:
    collected: list[str] = []
    level = _heading_level(heading_line)
    for line in markdown_lines(content, whole_document=True):
        if not collected:
            if not line.verbatim and line.text == heading_line:
                collected.append(line.raw)
            continue
        if (
            not line.verbatim
            and _is_heading(line.text)
            and _heading_level(line.text) <= level
            and line.text != heading_line
        ):
            break
        collected.append(line.raw)
    return _span(collected)


def extract_explicit_test_paths(content: str, *, tests_root: str) -> tuple[str, ...]:
    """Return test paths authored in Markdown code spans or fences."""
    return _extract_test_paths(_markdown_code(content), tests_root=tests_root)


def extract_command_test_paths(
    commands: tuple[str, ...], *, tests_root: str
) -> tuple[str, ...]:
    """Return test paths from explicit runtime command facts."""
    return _extract_test_paths(commands, tests_root=tests_root)


def unsupported_explicit_test_tokens(
    content: str, *, tests_root: str
) -> tuple[str, ...]:
    """Return whole unsupported test-path tokens from authored Markdown code."""
    return _unsupported_test_tokens(_markdown_code(content), tests_root=tests_root)


def unsupported_command_test_tokens(
    commands: tuple[str, ...], *, tests_root: str
) -> tuple[str, ...]:
    """Return whole unsupported test-path tokens from runtime commands."""
    return _unsupported_test_tokens(commands, tests_root=tests_root)


def _unsupported_test_tokens(
    explicit_text: tuple[str, ...], *, tests_root: str
) -> tuple[str, ...]:
    token_pattern = re.compile(
        rf"(?<![A-Za-z0-9_./-]){re.escape(tests_root)}/[^\s`\"']+"
    )
    unsupported: list[str] = []
    seen: set[str] = set()
    for code in explicit_text:
        for match in token_pattern.finditer(code):
            token = match.group(0).rstrip(".,);")
            path, _separator, _selector = token.partition("::")
            if any(marker in path for marker in ("*", "?", "[", "]", "{", "}")):
                if token not in seen:
                    seen.add(token)
                    unsupported.append(token)
    return tuple(unsupported)


def _extract_test_paths(
    explicit_text: tuple[str, ...], *, tests_root: str
) -> tuple[str, ...]:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_./-]){re.escape(tests_root)}/[A-Za-z0-9_./-]+"
    )
    paths: list[str] = []
    seen: set[str] = set()
    for code in explicit_text:
        for match in pattern.finditer(code):
            path = match.group(0).rstrip(".,)")
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return tuple(paths)


def _markdown_code(content: str) -> tuple[str, ...]:
    segments: list[str] = []
    fenced: list[str] = []
    fence_marker: str | None = None
    fence_size = 0
    for line in content.splitlines():
        delimiter = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", line)
        if fence_marker is not None:
            if (
                delimiter is not None
                and delimiter.group(1)[0] == fence_marker
                and len(delimiter.group(1)) >= fence_size
            ):
                segments.append("\n".join(fenced))
                fenced = []
                fence_marker = None
                fence_size = 0
            else:
                fenced.append(line)
            continue
        if delimiter is not None:
            fence_marker = delimiter.group(1)[0]
            fence_size = len(delimiter.group(1))
            continue
        segments.extend(_inline_code(line))
    if fence_marker is not None:
        segments.append("\n".join(fenced))
    return tuple(segments)


def _inline_code(line: str) -> tuple[str, ...]:
    segments: list[str] = []
    offset = 0
    while match := re.search(r"`+", line[offset:]):
        start = offset + match.start()
        marker = match.group(0)
        end = line.find(marker, start + len(marker))
        if end < 0:
            break
        segments.append(line[start + len(marker) : end])
        offset = end + len(marker)
    return tuple(segments)


def extract_active_rule_blocks(content: str) -> tuple[str, ...] | None:
    """Capture authored rule text without requiring a rule-ID authoring format.

    A paragraph or top-level list item is one source block. Continuations and
    verbatim examples stay with their block; their rules still need assessment.
    None means no section, while an empty tuple means an empty section.
    """
    headings = [
        line.text
        for line in markdown_lines(content, whole_document=True)
        if not line.verbatim
        and re.fullmatch(r"#{2,6} Active Enforcement Rules", line.text)
    ]
    if not headings:
        return None
    if len(headings) != 1:
        raise ValueError("the plan has multiple Active Enforcement Rules sections")
    section = extract_markdown_section(content, headings[0])
    body = markdown_lines(section, whole_document=False)[1:]
    blocks: list[str] = []
    current: list[str] = []
    for line in body:
        starts_item = bool(re.match(r"(?:[-*+] |\d+[.)] )", line.text))
        if not line.verbatim and (not line.text.strip() or starts_item):
            if current:
                blocks.append(_span(current))
                current = []
            if not line.text.strip():
                continue
        current.append(line.raw)
    if current:
        blocks.append(_span(current))
    return tuple(blocks)


def extract_milestone_section(content: str, milestone: str) -> str:
    pattern = _milestone_heading_pattern(milestone)
    collected: list[str] = []
    for line in markdown_lines(content, whole_document=True):
        if not collected:
            if not line.verbatim and pattern.search(line.text):
                collected.append(line.raw)
            continue
        if not line.verbatim and (
            line.text.startswith("## ")
            or (line.text.startswith("### ") and not pattern.search(line.text))
        ):
            break
        collected.append(line.raw)
    return _span(collected)


def _milestone_heading_pattern(milestone: str) -> re.Pattern[str]:
    escaped = re.escape(milestone)
    return re.compile(rf"^### (?:Milestone\s+{escaped}|M{escaped})(?::(?:\s.*)?|$)")


def extract_acceptance_criteria_blocks(content: str, ac_ids: list[str]) -> str:
    """The `## Acceptance Criteria` heading and the blocks of ``ac_ids``."""
    wanted = set(ac_ids)
    selected = False
    collected: list[str] = []
    for line, heading, opened in _acceptance_criteria(content):
        if opened is not None:
            selected = opened in wanted
        if heading or selected:
            collected.append(line.raw)
    return _span(collected)


def extract_feature_spec_ac_ids(content: str) -> list[str]:
    """The AC ids in spec order, read from the same blocks as the extractor."""
    return [
        opened
        for _line, _heading, opened in _acceptance_criteria(content)
        if opened is not None
    ]


def _acceptance_criteria(
    content: str,
) -> Iterator[tuple[MarkdownLine, bool, str | None]]:
    """The Acceptance Criteria section's lines, the one owner of AC blocks.

    Each line comes with whether it is the section heading and the AC id it
    opens, if any. The section starts at a `## Acceptance Criteria` line
    outside every verbatim block and ends before the next such `## ` line; a
    repeated section heading continues it. A `### AC-N:` line outside every
    verbatim block opens block AC-N.
    """
    in_acceptance = False
    for line in markdown_lines(content, whole_document=True):
        if not line.verbatim and line.text == _ACCEPTANCE_CRITERIA:
            in_acceptance = True
            yield line, True, None
            continue
        if not in_acceptance:
            continue
        if not line.verbatim and line.text.startswith("## "):
            return
        match = None if line.verbatim else _AC_HEADING.match(line.text)
        yield line, False, match.group(1) if match else None


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^#+ ", line))


def _heading_level(line: str) -> int:
    match = re.match(r"^(#+)", line)
    return len(match.group(1)) if match else 0
