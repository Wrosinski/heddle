"""
Tests for markdown section extraction (replaces 6 awk scripts).

Covers extraction functions: extract_milestone_section,
extract_acceptance_criteria_blocks, extract_feature_spec_ac_ids,
extract_markdown_section and extract_active_rule_blocks, including their shared
fence and line-terminator rules.
"""

from __future__ import annotations

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


class TestMilestoneExtraction:
    @REQUIRES_IMPL
    def test_extract_milestone_section_by_number(
        self, sample_plan_content: str
    ) -> None:
        from heddle.gate.extraction import extract_milestone_section

        section = extract_milestone_section(sample_plan_content, "2")
        assert "### Milestone 2:" in section
        assert "Satisfies: AC-3, AC-4" in section
        assert "### Milestone 1:" not in section

    @REQUIRES_IMPL
    def test_extract_milestone_section_missing(self, sample_plan_content: str) -> None:
        from heddle.gate.extraction import extract_milestone_section

        section = extract_milestone_section(sample_plan_content, "99")
        assert section == ""

    @REQUIRES_IMPL
    def test_lookalike_heading_does_not_shadow_exact_milestone(self) -> None:
        from textwrap import dedent

        from heddle.gate.extraction import (
            extract_milestone_section,
        )

        content = dedent("""\
            ### Milestone m1 context

            Narrative context, not the milestone contract.

            ### Milestone m1: Exact contract

            Satisfies: AC-1

            ### Milestone m2: Next contract
            """)

        section = extract_milestone_section(content, "m1")
        assert section.startswith("### Milestone m1: Exact contract")
        assert "Narrative context" not in section


class TestSpecExtraction:
    @REQUIRES_IMPL
    def test_extract_feature_spec_ac_ids(self, sample_spec_content: str) -> None:
        from heddle.gate.extraction import extract_feature_spec_ac_ids

        ids = extract_feature_spec_ac_ids(sample_spec_content)
        assert ids == ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]

    @REQUIRES_IMPL
    def test_extract_acceptance_criteria_blocks(self, sample_spec_content: str) -> None:
        from heddle.gate.extraction import extract_acceptance_criteria_blocks

        blocks = extract_acceptance_criteria_blocks(
            sample_spec_content, ["AC-1", "AC-3"]
        )
        assert "### AC-1:" in blocks
        assert "### AC-3:" in blocks
        assert "### AC-2:" not in blocks


class TestMarkdownSection:
    @REQUIRES_IMPL
    def test_extract_h2_section(self, sample_spec_content: str) -> None:
        from heddle.gate.extraction import extract_markdown_section

        section = extract_markdown_section(sample_spec_content, "## Purpose")
        assert "sample feature for testing" in section.lower()
        assert "## Acceptance Criteria" not in section

    @REQUIRES_IMPL
    def test_extract_nested_section_respects_level(
        self, sample_plan_content: str
    ) -> None:
        from heddle.gate.extraction import extract_markdown_section

        section = extract_markdown_section(sample_plan_content, "## Milestones")
        assert "### Milestone 1:" in section
        assert "### Milestone 2:" in section

    def test_fenced_heading_is_content_not_boundary(self) -> None:
        # review-write finding 1: free-form section bodies (Decision Log
        # prose) legitimately carry fenced markdown samples; a fenced
        # `## ` line is content, not a section boundary.
        from heddle.gate.extraction import extract_markdown_section

        content = (
            "## Decision Log\n\n"
            "### D-1: Layout\n\n"
            "Rejected sample:\n\n"
            "```markdown\n"
            "## Fake Heading\n"
            "body\n"
            "```\n\n"
            "### D-2: Second decision\n\nKept.\n\n"
            "## Outcomes\n\nShipped.\n"
        )
        section = extract_markdown_section(content, "## Decision Log")
        assert "## Fake Heading" in section, "fenced heading truncated the slice"
        assert "### D-2: Second decision" in section
        assert section.count("```") == 2, "unbalanced fence in the slice"
        assert "## Outcomes" not in section

    def test_fenced_lookalike_heading_does_not_start_a_section(self) -> None:
        from heddle.gate.extraction import extract_markdown_section

        content = (
            "## Intro\n\n"
            "```\n"
            "## Decision Log\n"
            "fenced lookalike\n"
            "```\n\n"
            "## Decision Log\n\nReal body.\n\n"
            "## Tail\n\nx.\n"
        )
        section = extract_markdown_section(content, "## Decision Log")
        assert "Real body." in section
        assert "fenced lookalike" not in section


class TestExtractionDriftSensitivity:
    """Non-tautological format-drift guards for the parsers whose silent
    under-extraction would mislead preflight (the failure shape behind the
    finding-extractor telemetry bug). Each fixture spans a realistic format
    variation and asserts the exact set, so a regression that drops one entry
    fails the test."""

    @REQUIRES_IMPL
    def test_milestone_section_resolves_m_prefix_and_bounds(self) -> None:
        from textwrap import dedent

        from heddle.gate.extraction import extract_milestone_section

        content = dedent("""\
            ### M2: Integration

            Satisfies: AC-3

            ### M3: Polish

            Satisfies: AC-5
            """)
        section = extract_milestone_section(content, "2")
        assert "### M2: Integration" in section
        assert "AC-3" in section
        # Boundary: the next milestone's content must be excluded.
        assert "M3" not in section
        assert "AC-5" not in section


# --------------------------------------------------------------------------- #
# antigravity-reviewer-lane-v1, AC-22: embedded plan and spec sections equal
# their source bytes. The section extractors treat the lines a CommonMark
# fenced code block covers as content and keep every selected line's own line
# ending; for plain-LF input they still return exactly what the previous rule
# returned (survivor, AC-23).
# --------------------------------------------------------------------------- #

# The line endings CommonMark counts, the multi-character CRLF first.
TERMINATOR_NAMES = {"\r\n": "crlf", "\r": "cr", "\n": "lf"}
TERMINATORS = tuple(TERMINATOR_NAMES)
# Every other boundary `str.splitlines` recognizes: CommonMark ends no line at
# these, so each stays inside its line.
INLINE_SEPARATOR_NAMES = {
    "\v": "vt",
    "\f": "ff",
    "\x1c": "fs",
    "\x1d": "gs",
    "\x1e": "rs",
    "\x85": "nel",
    "\u2028": "line-separator",
    "\u2029": "paragraph-separator",
}
FENCED_EXAMPLE = [
    "```markdown",
    "## Example heading inside a fence",
    "### Milestone m9: Fenced lookalike",
    "### Milestone m1: Fenced lookalike of the selected heading",
    "### AC-9: Fenced lookalike",
    "### AC-1: Fenced lookalike of the selected heading",
    "```",
]


def _document(lines: list[str], terminator: str = "\n") -> str:
    return terminator.join(lines) + terminator


def test_splitlines_boundaries_are_the_terminators_covered_here() -> None:
    """Fixture check: the line endings and the inline separators together are
    exactly what ``str.splitlines`` splits on."""
    boundaries = {
        chr(code) for code in range(0x110000) if len(f"a{chr(code)}b".splitlines()) == 2
    }
    assert boundaries | {"\r\n"} == set(TERMINATORS) | set(INLINE_SEPARATOR_NAMES)
    assert len("a\r\nb".splitlines()) == 2


@pytest.mark.parametrize("terminator", TERMINATORS, ids=TERMINATOR_NAMES.get)
def test_ac22_markdown_section_keeps_its_own_line_terminators(terminator) -> None:
    from heddle.gate.extraction import (
        extract_markdown_h2_section,
        extract_markdown_section,
    )

    span = ["## Purpose", "first line", "  indented second line", "", "last line"]
    content = _document(["# Doc", "", *span, "", "## Next", "tail"], terminator)
    expected = terminator.join(span)
    assert extract_markdown_section(content, "## Purpose") == expected, (
        f"FAIL AC-22: the section does not keep its {terminator!r} terminators"
    )
    assert extract_markdown_h2_section(content, "Purpose") == expected


@pytest.mark.parametrize("terminator", TERMINATORS, ids=TERMINATOR_NAMES.get)
def test_ac22_milestone_section_keeps_its_own_line_terminators(terminator) -> None:
    from heddle.gate.extraction import extract_milestone_section

    span = ["### Milestone m1: First", "", "Satisfies: AC-1", "body line"]
    content = _document(
        ["# Plan", "", *span, "", "### Milestone m2: Second", "tail"], terminator
    )
    assert extract_milestone_section(content, "m1") == terminator.join(span), (
        f"FAIL AC-22: the milestone section does not keep its {terminator!r} "
        "terminators"
    )


@pytest.mark.parametrize("terminator", TERMINATORS, ids=TERMINATOR_NAMES.get)
def test_ac22_acceptance_blocks_keep_their_own_line_terminators(terminator) -> None:
    from heddle.gate.extraction import (
        extract_acceptance_criteria_blocks,
        extract_feature_spec_ac_ids,
    )

    first = ["### AC-1: One", "", "Expected: one."]
    second = ["### AC-2: Two", "", "Expected: two.", "  continued"]
    content = _document(
        [
            "# Spec",
            "",
            "## Acceptance Criteria",
            "",
            *first,
            "",
            *second,
            "",
            "## Next",
            "tail",
        ],
        terminator,
    )
    blocks = extract_acceptance_criteria_blocks(content, ["AC-2"])
    assert blocks == terminator.join(["## Acceptance Criteria", *second]), (
        f"FAIL AC-22: the AC block does not keep its {terminator!r} terminators"
    )
    both = extract_acceptance_criteria_blocks(content, ["AC-1", "AC-2"])
    assert both == terminator.join(["## Acceptance Criteria", *first, "", *second])
    assert extract_feature_spec_ac_ids(content) == ["AC-1", "AC-2"]


@pytest.mark.parametrize("terminator", TERMINATORS, ids=TERMINATOR_NAMES.get)
def test_ac22_active_rule_blocks_keep_their_own_line_terminators(terminator) -> None:
    from heddle.gate.extraction import extract_active_rule_blocks

    item = ["- first rule", "  continued"]
    paragraph = ["A paragraph rule", "over two lines"]
    content = _document(
        ["## Active Enforcement Rules", "", *item, "", *paragraph, "## Next"],
        terminator,
    )
    assert extract_active_rule_blocks(content) == (
        terminator.join(item),
        terminator.join(paragraph),
    ), f"FAIL AC-22: rule blocks do not keep their {terminator!r} terminators"


@pytest.mark.parametrize(
    "separator", INLINE_SEPARATOR_NAMES, ids=INLINE_SEPARATOR_NAMES.get
)
def test_ac22_a_separator_commonmark_does_not_count_stays_inside_its_line(
    separator,
) -> None:
    """A heading after such a separator shares its line, so it bounds nothing,
    and the separator stays in the span as its source byte."""
    from heddle.gate.extraction import (
        extract_acceptance_criteria_blocks,
        extract_feature_spec_ac_ids,
        extract_markdown_section,
        extract_milestone_section,
    )

    purpose = f"## Purpose\nbody{separator}## Next on the same line\nmore"
    doc = f"# Doc\n\n{purpose}\n\n## Next\ntail\n"
    assert extract_markdown_section(doc, "## Purpose") == purpose, (
        f"FAIL AC-22: {separator!r} ended a line inside the section"
    )
    milestone = f"### Milestone m1: First\nbody{separator}### Milestone m2: Same line"
    plan = f"# Plan\n\n{milestone}\n\n### Milestone m2: Second\ntail\n"
    assert extract_milestone_section(plan, "m1") == milestone
    block = f"### AC-1: One\nbody{separator}### AC-2: Same line\nmore"
    spec = f"## Acceptance Criteria\n{block}\n\n### AC-3: Three\n## Next\n"
    assert extract_acceptance_criteria_blocks(spec, ["AC-1"]) == (
        f"## Acceptance Criteria\n{block}"
    )
    assert extract_feature_spec_ac_ids(spec) == ["AC-1", "AC-3"]


def test_ac22_mixed_terminators_inside_a_span_are_its_source_bytes() -> None:
    """CRLF lines with a line separator and a next-line mark inside the span."""
    from heddle.gate.extraction import (
        extract_acceptance_criteria_blocks,
        extract_milestone_section,
    )

    milestone = (
        "### Milestone m1: First\r\n\r\nSatisfies: AC-1\u2028still m1\x85"
        "and this\r\nlast  \t"
    )
    plan = f"# Plan\n\n{milestone}\r\n\r\n### Milestone m2: Next\n"
    assert extract_milestone_section(plan, "m1") == milestone.rstrip(), (
        "FAIL AC-22: mixed terminators inside the milestone span were rewritten"
    )
    block = "### AC-1: One\r\nExpected:\u2029one.\r\n\r\n  indented"
    spec = f"## Acceptance Criteria\r\n{block}\r\n\r\n## Next\r\n"
    assert extract_acceptance_criteria_blocks(spec, ["AC-1"]) == (
        f"## Acceptance Criteria\r\n{block}"
    ), "FAIL AC-22: mixed terminators inside the AC block were rewritten"


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_ac22_fenced_headings_never_bound_a_milestone_section(fence) -> None:
    """A fenced `## ` line or milestone lookalike neither ends nor starts it."""
    from heddle.gate.extraction import extract_milestone_section

    example = [line.replace("```", fence) for line in FENCED_EXAMPLE]
    section = [
        "### Milestone m1: First",
        "",
        *example,
        "",
        "Text after the fence stays in m1.",
    ]
    content = _document(
        [
            "# Plan",
            "",
            fence,
            "### Milestone m1: Lookalike before the real heading",
            "text inside the earlier fence",
            fence,
            "",
            *section,
            "",
            "### Milestone m2: Second",
            "m2 body",
        ]
    )
    assert extract_milestone_section(content, "m1") == "\n".join(section), (
        "FAIL AC-22: a fenced heading bounded the milestone section"
    )


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_ac22_fenced_headings_never_bound_or_switch_acceptance_blocks(fence) -> None:
    """A fenced `## ` line ends no section; a fenced AC lookalike opens no block."""
    from heddle.gate.extraction import (
        extract_acceptance_criteria_blocks,
        extract_feature_spec_ac_ids,
    )

    example = [line.replace("```", fence) for line in FENCED_EXAMPLE]
    first = ["### AC-1: One", "", *example, "", "Text after the fence stays in AC-1."]
    second = ["### AC-2: Two", "", "Expected: two."]
    content = _document(
        [
            "# Spec",
            "",
            fence,
            "## Acceptance Criteria",
            "### AC-1: Lookalike before the section",
            fence,
            "",
            "## Acceptance Criteria",
            "",
            "### AC-3: Three",
            "",
            fence,
            "### AC-1: Lookalike inside another block",
            fence,
            "AC-3 text after its fence.",
            "",
            *first,
            "",
            *second,
            "",
            "## Next",
            "### AC-4: Outside the section",
        ]
    )
    assert extract_acceptance_criteria_blocks(content, ["AC-1"]) == "\n".join(
        ["## Acceptance Criteria", *first]
    ), "FAIL AC-22: a fenced heading bounded or switched the AC-1 block"
    assert extract_acceptance_criteria_blocks(content, ["AC-2"]) == "\n".join(
        ["## Acceptance Criteria", *second]
    ), "FAIL AC-22: a fenced `## ` line ended the section before AC-2"
    assert extract_feature_spec_ac_ids(content) == ["AC-3", "AC-1", "AC-2"], (
        "FAIL AC-22: the AC id list counts a fenced lookalike or stops at a "
        "fenced `## ` line"
    )


def test_ac22_ac_ids_name_exactly_the_blocks_the_extractor_selects() -> None:
    """The id list and the blocks come from one boundary rule.

    Every listed id selects a block headed by that id, and selecting every
    listed id yields the whole section, so no block lies outside the list.
    """
    from heddle.gate.extraction import (
        extract_acceptance_criteria_blocks,
        extract_feature_spec_ac_ids,
    )

    section = [
        "## Acceptance Criteria",
        "### AC-1: One",
        *FENCED_EXAMPLE,
        "### AC-2b: Two",
        "~~~",
        "## Acceptance Criteria",
        "### AC-7: Lookalike",
        "~~~",
        "### AC-10: Ten",
        "## Acceptance Criteria",
        "### AC-11: After a repeated section heading",
    ]
    content = _document([*section, "## Next", "### AC-12: Outside"])
    ids = extract_feature_spec_ac_ids(content)
    assert ids == ["AC-1", "AC-2b", "AC-10", "AC-11"], (
        f"FAIL AC-22: the AC id list disagrees with the blocks: {ids}"
    )
    for ac_id in ids:
        block = extract_acceptance_criteria_blocks(content, [ac_id])
        headings = [line for line in block.split("\n") if line.startswith("### ")]
        assert headings[0].startswith(f"### {ac_id}:"), (
            f"FAIL AC-22: listed {ac_id} selects no block of its own"
        )
    assert extract_acceptance_criteria_blocks(content, ids) == "\n".join(section), (
        "FAIL AC-22: selecting every listed id misses part of the section"
    )


# A fence closes only at a run of its own character, at least as long as the
# run that opened it, indented at most three spaces, with nothing but
# whitespace after it, as CommonMark specifies. Each case hides a `## ` line
# that a looser closing rule would treat as a boundary; the section's real end
# follows the fence, so the fence must close.
FENCE_CASES = {
    "nested-block": [
        "````markdown",
        "```python",
        "## Heading inside the inner block",
        "```",
        "## Heading after the inner block",
        "````",
    ],
    "info-string-line": [
        "```",
        "plain fenced text",
        "```python",
        "## Heading after an info-string line",
        "```",
    ],
    "longer-closing-run": [
        "~~~~",
        "~~~",
        "## Heading after a shorter run",
        "~~~~~~",
    ],
    "other-character": [
        "```",
        "~~~",
        "## Heading after the other character",
        "```",
    ],
    "text-after-a-run": [
        "  ```",
        "``` not a closing run",
        "## Heading after a run with text",
        "```   ",
    ],
    "over-indented-run": [
        "```",
        "    ```",
        "## Heading after a run indented four spaces",
        "   ```",
    ],
}
SECTION_SHAPES = {
    "markdown-section": (
        ["# Doc", "", "## Purpose"],
        ["", "## Next", "tail"],
    ),
    "milestone-section": (
        ["# Plan", "", "### Milestone m1: First"],
        ["", "### Milestone m2: Second", "tail"],
    ),
    "acceptance-block": (
        ["## Acceptance Criteria", "### AC-1: One"],
        ["", "### AC-2: Two", "two", "## Next", "### AC-3: Outside"],
    ),
}


def _extract(shape: str, content: str) -> str:
    from heddle.gate.extraction import (
        extract_acceptance_criteria_blocks,
        extract_markdown_section,
        extract_milestone_section,
    )

    if shape == "markdown-section":
        return extract_markdown_section(content, "## Purpose")
    if shape == "milestone-section":
        return extract_milestone_section(content, "m1")
    return extract_acceptance_criteria_blocks(content, ["AC-1"])


@pytest.mark.parametrize("shape", SECTION_SHAPES)
@pytest.mark.parametrize("case", FENCE_CASES)
def test_ac22_a_fence_closes_only_at_its_own_marker(case, shape) -> None:
    """Inside an open fence, a line closes it only when its run is the same
    character, at least as long, indented at most three spaces, with nothing
    but whitespace after it."""
    from heddle.gate.extraction import extract_feature_spec_ac_ids

    head, tail = SECTION_SHAPES[shape]
    body = ["intro", *FENCE_CASES[case], "Text after the fence stays in the section."]
    content = _document([*head, *body, *tail])
    expected = [*head, *body]
    if shape != "acceptance-block":
        expected = expected[2:]
    assert _extract(shape, content) == "\n".join(expected), (
        f"FAIL AC-22: the {case} fence bounded the {shape} or never closed"
    )
    if shape == "acceptance-block":
        assert extract_feature_spec_ac_ids(content) == ["AC-1", "AC-2"]


@pytest.mark.parametrize("shape", SECTION_SHAPES)
def test_ac22_a_fence_left_open_runs_to_the_end_of_the_input(shape) -> None:
    """A top-level fence left open keeps every later line as content, so the
    section runs to the end of the input, as CommonMark and the toggle rule
    both end it."""
    head, tail = SECTION_SHAPES[shape]
    body = ["intro", "```text", "## Heading inside", *tail]
    expected = [*head, *body]
    if shape != "acceptance-block":
        expected = expected[2:]
    assert _extract(shape, _document([*head, *body])) == "\n".join(expected), (
        f"FAIL AC-22: an unclosed fence did not run the {shape} to the end"
    )


# A fence left open inside a list item or a blockquote ends with its
# container, which the next unindented, unquoted line closes.
OPEN_CONTAINER_FENCES = {
    "list-item": ["- item", "  ```text", "  ## Heading inside the item's fence"],
    "blockquote": ["> ```text", "> ## Heading inside the quoted fence"],
}


@pytest.mark.parametrize("shape", SECTION_SHAPES)
@pytest.mark.parametrize("container", OPEN_CONTAINER_FENCES)
def test_ac22_a_fence_left_open_ends_with_its_container(container, shape) -> None:
    """The line that closes the container is structure again, so the section
    ends at its real boundary instead of running to the end of the input."""
    head, tail = SECTION_SHAPES[shape]
    body = ["intro", "", *OPEN_CONTAINER_FENCES[container]]
    expected = [*head, *body]
    if shape != "acceptance-block":
        expected = expected[2:]
    assert _extract(shape, _document([*head, *body, *tail])) == "\n".join(expected), (
        f"FAIL AC-22: a fence left open in a {container} hid the {shape} boundary"
    )


@pytest.mark.parametrize("shape", SECTION_SHAPES)
@pytest.mark.parametrize("marker", ["    ```", "    ~~~"], ids=["backtick", "tilde"])
def test_ac22_a_marker_in_indented_code_opens_no_fence(marker, shape) -> None:
    """A run indented four spaces is indented code, not a fence opening, so
    the boundary after it still ends the section."""
    from heddle.gate.extraction import extract_feature_spec_ac_ids

    head, tail = SECTION_SHAPES[shape]
    body = ["intro", "", marker, "    literal text in indented code"]
    expected = [*head, *body]
    if shape != "acceptance-block":
        expected = expected[2:]
    content = _document([*head, *body, *tail])
    assert _extract(shape, content) == "\n".join(expected), (
        f"FAIL AC-22: a literal {marker.strip()} marker in indented code hid the "
        f"{shape} boundary"
    )
    if shape == "acceptance-block":
        assert extract_feature_spec_ac_ids(content) == ["AC-1", "AC-2"]


# --------------------------------------------------------------------------- #
# Survivor (AC-23): plain-LF output is byte-identical to the previous rule.
# The functions below copy the extractors as they stood before the fence and
# terminator change; they are the oracle, so they are never edited.
# --------------------------------------------------------------------------- #
def _previous_is_fence_delimiter(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _previous_is_heading(line: str) -> bool:
    import re

    return bool(re.match(r"^#+ ", line))


def _previous_heading_level(line: str) -> int:
    import re

    match = re.match(r"^(#+)", line)
    return len(match.group(1)) if match else 0


def _previous_markdown_section(content: str, heading_line: str) -> str:
    lines = content.splitlines()
    collected: list[str] = []
    level = 0
    in_section = False
    in_fence = False
    for line in lines:
        is_fence_delimiter = _previous_is_fence_delimiter(line)
        if is_fence_delimiter and not (in_section or in_fence):
            in_fence = True
            continue
        if is_fence_delimiter:
            in_fence = not in_fence
        if not in_section:
            if not in_fence and line == heading_line:
                in_section = True
                level = _previous_heading_level(heading_line)
                collected.append(line)
            continue
        if (
            collected
            and not in_fence
            and not is_fence_delimiter
            and _previous_is_heading(line)
            and _previous_heading_level(line) <= level
            and line != heading_line
        ):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _previous_active_rule_blocks(content: str) -> tuple[str, ...] | None:
    import re

    headings = []
    in_fence = False
    for line in content.splitlines():
        if _previous_is_fence_delimiter(line):
            in_fence = not in_fence
        elif not in_fence and re.fullmatch(r"#{2,6} Active Enforcement Rules", line):
            headings.append(line)
    if not headings:
        return None
    if len(headings) != 1:
        raise ValueError("the plan has multiple Active Enforcement Rules sections")
    body = _previous_markdown_section(content, headings[0]).splitlines()[1:]
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in body:
        starts_item = bool(re.match(r"(?:[-*+] |\d+[.)] )", line))
        if not in_fence and (not line.strip() or starts_item):
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            if not line.strip():
                continue
        current.append(line)
        if _previous_is_fence_delimiter(line):
            in_fence = not in_fence
    if current:
        blocks.append("\n".join(current).strip())
    return tuple(blocks)


def _previous_milestone_section(content: str, milestone: str) -> str:
    import re

    escaped = re.escape(milestone)
    pattern = re.compile(rf"^### (?:Milestone\s+{escaped}|M{escaped})(?::(?:\s.*)?|$)")
    lines = content.splitlines()
    collected: list[str] = []
    in_section = False
    for line in lines:
        if not in_section and pattern.search(line):
            in_section = True
        if not in_section:
            continue
        if collected and (
            line.startswith("## ")
            or (line.startswith("### ") and not pattern.search(line))
        ):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _previous_acceptance_criteria_blocks(content: str, ac_ids: list[str]) -> str:
    import re

    wanted = set(ac_ids)
    lines = content.splitlines()
    in_acceptance = False
    print_block = False
    collected: list[str] = []
    for line in lines:
        if line == "## Acceptance Criteria":
            in_acceptance = True
            collected.append(line)
            continue
        if (
            in_acceptance
            and line.startswith("## ")
            and line != "## Acceptance Criteria"
        ):
            break
        if in_acceptance and re.match(r"^### AC-[0-9]+[a-z]?:", line):
            match = re.search(r"AC-[0-9]+[a-z]?", line)
            print_block = bool(match and match.group(0) in wanted)
        if in_acceptance and print_block:
            collected.append(line)
    return "\n".join(collected).strip()


def _previous_feature_spec_ac_ids(content: str) -> list[str]:
    import re

    lines = content.splitlines()
    in_acceptance = False
    ids: list[str] = []
    for line in lines:
        if line == "## Acceptance Criteria":
            in_acceptance = True
            continue
        if in_acceptance and line.startswith("## "):
            break
        if in_acceptance and re.match(r"^### AC-[0-9]+[a-z]?:", line):
            match = re.search(r"AC-[0-9]+[a-z]?", line)
            if match:
                ids.append(match.group(0))
    return ids


HEADING_LINES = (
    "# Title",
    "## Acceptance Criteria",
    "## Purpose",
    "## Decision Log",
    "## Active Enforcement Rules",
    "### Active Enforcement Rules",
    "## ",
    "##NoSpace",
    "### AC-1: One",
    "### AC-2: Two",
    "### AC-10a: Ten",
    "### AC-3:",
    "### AC-x: not an id",
    "### Milestone m1: First",
    "### Milestone m1",
    "### Milestone m1 context",
    "### Milestone m2: Second",
    "### M1: Short form",
    "### Notes",
    "#### Detail",
)
TEXT_LINES = (
    "",
    "",
    " ",
    "\t",
    "text",
    "  indented text",
    "trailing spaces   ",
    "- item",
    "* item",
    "1. step",
    "2) step",
    "Verified-by: tests/check.py::test_value",
    "a ## not a heading",
    "AC-1 named in prose",
    "é€🚀 multibyte",
)
# Simple fences: each opening line paired with the bare run that closes it.
# Placed at the top level, the previous toggle rule and CommonMark agree.
SIMPLE_FENCES = (
    ("```", "```"),
    ("```python", "```"),
    ("~~~", "~~~"),
    ("  ```", "  ```"),
    ("~~~~", "~~~~"),
    ("````text", "````"),
)
SECTION_HEADINGS = (
    "## Acceptance Criteria",
    "## Purpose",
    "## Decision Log",
    "### Notes",
    "#### Detail",
    "# Title",
)
MILESTONE_IDS = ("m1", "m2", "1", "m9")
AC_SELECTIONS = ([], ["AC-1"], ["AC-2", "AC-10a"], ["AC-3", "AC-1", "AC-9"])


def _plain_lf_documents(seed: int, *, fences: bool, count: int = 400):
    """Generated plain-LF documents over the lines the extractors react to."""
    import random

    rng = random.Random(seed)
    # Block headings and prose outweigh section headings, so most documents
    # hold an Acceptance Criteria section or a milestone with a real body.
    blocks = tuple(line for line in HEADING_LINES if line.startswith("### "))
    vocabulary = HEADING_LINES + blocks * 2 + TEXT_LINES * 2

    def fence(closed: bool) -> list[str]:
        opening, closing = rng.choice(SIMPLE_FENCES)
        body = [rng.choice(vocabulary) for _ in range(rng.randint(0, 4))]
        lines = [opening, *body, closing] if closed else [opening, *body]
        # An indented opening right after a list item would open inside the
        # item; a blank line and an unindented paragraph close any list first.
        return ["", "text", *lines] if opening.startswith(" ") else lines

    for _ in range(count):
        lines: list[str] = []
        for _ in range(rng.randint(0, 40)):
            if fences and rng.random() < 0.15:
                lines.extend(fence(closed=True))
            else:
                lines.append(rng.choice(vocabulary))
        if rng.random() < 0.6:
            lines.insert(rng.randint(0, len(lines)), "## Acceptance Criteria")
        if fences and rng.random() < 0.1:
            lines.extend(fence(closed=False))  # left open to the end of input
        content = "\n".join(lines) + rng.choice(("", "\n", "\n\n"))
        _assert_top_level_fences(content)
        yield content


def _assert_top_level_fences(content: str) -> None:
    """Generator check: CommonMark reads every fence at the top level."""
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark").parse(content)
    assert all(token.level == 0 for token in tokens if token.type == "fence"), content


def _outcome(function, *args):
    try:
        return ("value", function(*args))
    except ValueError as error:
        return ("ValueError", str(error))


@pytest.mark.parametrize("seed", range(5))
def test_ac23_survivor_plain_lf_extraction_equals_the_previous_rule(seed) -> None:
    """Survivor: plain-LF input extracts exactly as before.

    Generated documents use LF only, with no other line boundary. The
    extractors that were fence-aware already see simple CommonMark fences
    too, each at the top level and closed by its own bare run or left open
    to the end of the input, where the previous toggle rule and CommonMark
    agree. The milestone and AC extractors, which change on fenced input,
    see fence-free input. The fence cases where the rules differ have their
    own tests.
    """
    from heddle.gate import extraction

    for content in _plain_lf_documents(seed, fences=True):
        for heading in SECTION_HEADINGS:
            assert extraction.extract_markdown_section(
                content, heading
            ) == _previous_markdown_section(content, heading), (content, heading)
        assert _outcome(extraction.extract_active_rule_blocks, content) == _outcome(
            _previous_active_rule_blocks, content
        ), content
    for content in _plain_lf_documents(seed + 100, fences=False):
        for milestone in MILESTONE_IDS:
            assert extraction.extract_milestone_section(
                content, milestone
            ) == _previous_milestone_section(content, milestone), (content, milestone)
        for selection in AC_SELECTIONS:
            assert extraction.extract_acceptance_criteria_blocks(
                content, selection
            ) == _previous_acceptance_criteria_blocks(content, selection), (
                content,
                selection,
            )
        assert extraction.extract_feature_spec_ac_ids(
            content
        ) == _previous_feature_spec_ac_ids(content), content
