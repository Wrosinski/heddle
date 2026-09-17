from __future__ import annotations

import re


def extract_markdown_h2_section(content: str, heading: str) -> str:
    return extract_markdown_section(content, f"## {heading}")


def extract_markdown_section(content: str, heading_line: str) -> str:
    # Fence-aware: a `## `-prefixed line inside a ```/~~~ code fence (a
    # rejected-layout example, a diff, sample markdown in a free-form
    # section body) is fenced content, not a section boundary — without
    # tracking, it both truncates the slice early and can start a section
    # at a fenced lookalike heading.
    lines = content.splitlines()
    collected: list[str] = []
    level = 0
    in_section = False
    in_fence = False
    for line in lines:
        is_fence_delimiter = _is_fence_delimiter(line)
        if is_fence_delimiter and not (in_section or in_fence):
            in_fence = True
            continue
        if is_fence_delimiter:
            in_fence = not in_fence
        if not in_section:
            if not in_fence and line == heading_line:
                in_section = True
                level = _heading_level(heading_line)
                collected.append(line)
            continue
        if (
            collected
            and not in_fence
            and not is_fence_delimiter
            and _is_heading(line)
            and _heading_level(line) <= level
            and line != heading_line
        ):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def extract_explicit_test_paths(content: str, *, tests_root: str) -> tuple[str, ...]:
    """Return test paths authored in Markdown code spans or fences."""
    return _extract_test_paths(_markdown_code(content), tests_root=tests_root)


def extract_command_test_paths(
    commands: tuple[str, ...], *, tests_root: str
) -> tuple[str, ...]:
    """Return test paths from explicit runtime command facts."""
    return _extract_test_paths(commands, tests_root=tests_root)


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


def _is_fence_delimiter(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def extract_active_rule_blocks(content: str) -> tuple[str, ...] | None:
    """Capture authored rule text without requiring a rule-ID authoring format.

    A paragraph or top-level list item is one source block. Continuations and
    fenced examples stay with their block; their rules still need assessment.
    None means no section, while an empty tuple means an empty section.
    """
    headings = []
    in_fence = False
    for line in content.splitlines():
        if _is_fence_delimiter(line):
            in_fence = not in_fence
        elif not in_fence and re.fullmatch(r"#{2,6} Active Enforcement Rules", line):
            headings.append(line)
    if not headings:
        return None
    if len(headings) != 1:
        raise ValueError("the plan has multiple Active Enforcement Rules sections")
    body = extract_markdown_section(content, headings[0]).splitlines()[1:]
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
        if _is_fence_delimiter(line):
            in_fence = not in_fence
    if current:
        blocks.append("\n".join(current).strip())
    return tuple(blocks)


def extract_milestone_section(content: str, milestone: str) -> str:
    pattern = _milestone_heading_pattern(milestone)
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


def _milestone_heading_pattern(milestone: str) -> re.Pattern[str]:
    escaped = re.escape(milestone)
    return re.compile(rf"^### (?:Milestone\s+{escaped}|M{escaped})(?::(?:\s.*)?|$)")


def extract_acceptance_criteria_blocks(content: str, ac_ids: list[str]) -> str:
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


def extract_feature_spec_ac_ids(content: str) -> list[str]:
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


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^#+ ", line))


def _heading_level(line: str) -> int:
    match = re.match(r"^(#+)", line)
    return len(match.group(1)) if match else 0
