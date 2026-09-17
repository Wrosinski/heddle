"""Pure Feature Spec lifecycle rendering, shared by close preparation and writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from heddle.kernel.project_config import KernelError


def parse_spec_frontmatter(text: str, spec_path: Path) -> tuple[dict[str, Any], str]:
    start, end, body_start = _frontmatter_span(text, spec_path)
    try:
        frontmatter = yaml.safe_load(text[start:end])
    except yaml.YAMLError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"{spec_path} frontmatter is malformed YAML: {error}",
            hint="fix feature spec frontmatter before completing the feature",
        ) from error
    if not isinstance(frontmatter, dict):
        raise KernelError(
            code="workspace-invalid",
            message=f"{spec_path} frontmatter must be a mapping",
            hint="shape the feature spec frontmatter as YAML key/value pairs",
        )
    return frontmatter, text[body_start:]


def _frontmatter_span(text: str, spec_path: Path) -> tuple[int, int, int]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise KernelError(
            code="workspace-invalid",
            message=f"{spec_path} has no leading frontmatter fence",
            hint="add YAML frontmatter before completing the feature",
        )
    offset = len(lines[0])
    for line in lines[1:]:
        if line.rstrip("\r\n") == "---":
            return len(lines[0]), offset, offset + len(line)
        offset += len(line)
    raise KernelError(
        code="workspace-invalid",
        message=f"{spec_path} has no closing frontmatter fence",
        hint="add a closing YAML frontmatter fence before completing the feature",
    )


def render_lifecycle(text: str, spec_path: Path, value: str = "complete") -> str:
    frontmatter, _body = parse_spec_frontmatter(text, spec_path)
    start, end, _body_start = _frontmatter_span(text, spec_path)
    node = yaml.compose(text[start:end])
    assert isinstance(node, yaml.MappingNode)
    lifecycle_nodes = [
        (key, value) for key, value in node.value if key.value == "lifecycle"
    ]
    if len(lifecycle_nodes) > 1:
        raise KernelError(
            "workspace-invalid",
            f"{spec_path} repeats lifecycle",
            "keep one unambiguous lifecycle field",
        )
    if frontmatter.get("lifecycle") == value:
        return text
    if not lifecycle_nodes:
        if node.flow_style:
            position = start + node.end_mark.index - 1
            separator = ", " if node.value else ""
            return text[:position] + f"{separator}lifecycle: {value}" + text[position:]
        newline = "\r\n" if text.startswith("---\r\n") else "\n"
        return text[:end] + f"lifecycle: {value}{newline}" + text[end:]
    key, target = lifecycle_nodes[0]
    token = text[start + target.start_mark.index : start + target.end_mark.index]
    if (
        not isinstance(target, yaml.ScalarNode)
        or target.style in {"|", ">"}
        or target.start_mark.index < key.end_mark.index
        or token.lstrip().startswith(("&", "!"))
    ):
        raise KernelError(
            "workspace-invalid",
            f"{spec_path} lifecycle is not a single-line scalar",
            "use a single-line lifecycle value before accepting completion",
        )
    replacement = (
        f"{target.style}{value}{target.style}" if target.style in {"'", '"'} else value
    )
    return (
        text[: start + target.start_mark.index]
        + replacement
        + text[start + target.end_mark.index :]
    )
