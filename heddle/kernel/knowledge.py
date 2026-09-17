"""Knowledge-plane readers for validation.

Markdown fact readers (spec/plan bodies + frontmatter) plus the YAML-manifest
readers the guardrails depend on (the patterns ``_descriptions.yaml`` registry
and the spec-drift baseline). The kernel owns structured-format parsing so the
runtime stays a read/render + envelope surface; the
runtime guardrails consume these typed readers rather than importing ``yaml``
directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

from heddle.kernel.project_config import KernelError

AC_HEADING = re.compile(r"^###\s+(AC-\d+)\b", re.MULTILINE)
MILESTONE_HEADING = re.compile(r"^###\s+Milestone\s+([^:\s]+):", re.MULTILINE)
VERIFIED_BY = re.compile(r"^Verified-by:\s*(.+?)\s*$", re.MULTILINE)
MARKDOWN_SUFFIX = "." + "md"
PATTERNS_ROOT = Path("docs") / "patterns"


@dataclass(frozen=True)
class MarkdownDocument:
    path: str
    frontmatter: dict[str, Any]
    body: str


@dataclass(frozen=True)
class SpecFacts:
    path: str
    ac_ids: tuple[str, ...]


@dataclass(frozen=True)
class SpecVerificationFacts:
    path: str
    status: str | None
    verified_by: tuple[str, ...]


@dataclass(frozen=True)
class PlanFacts:
    path: str
    milestone_section_ids: tuple[str, ...]


def read_markdown(
    path: Path,
    *,
    display_path: str | None = None,
) -> MarkdownDocument:
    label = display_path or str(path)
    if path.suffix != MARKDOWN_SUFFIX:
        raise KernelError(
            code="workspace-invalid",
            message=f"knowledge document must be Markdown (.md): {label}",
            hint="point knowledge-plane references at Markdown documents",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"knowledge document is unreadable: {label}",
            hint="repair the referenced Markdown document",
        ) from error
    # Normalize Windows line endings so a doc
    # saved with CRLF parses identically to an LF one. Otherwise the literal
    # `---\n` frontmatter delimiter below silently misses `---\r\n` frontmatter,
    # making spec-drift skip a complete spec and orientation-drift drop its
    # covers set. Body parsing here is line-based, so normalizing is safe; the
    # Verified-by hash reads its target file separately and is unaffected.
    text = text.replace("\r\n", "\n")
    frontmatter: dict[str, Any] = {}
    body = text
    lines = text.splitlines(keepends=True)
    if lines and lines[0] == "---\n":
        closing_index = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line in {"---\n", "---"}
            ),
            None,
        )
        if closing_index is None:
            raise KernelError(
                code="workspace-invalid",
                message=f"frontmatter is unterminated: {label}",
                hint="close the opening Markdown frontmatter with `---`",
            )
        frontmatter_text = "".join(lines[1:closing_index])
        try:
            loaded = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as error:
            raise KernelError(
                code="workspace-invalid",
                message=f"frontmatter is malformed YAML: {label}: {error}",
                hint="repair the referenced Markdown frontmatter",
            ) from error
        if isinstance(loaded, dict):
            frontmatter = loaded
        body = "".join(lines[closing_index + 1 :])
    return MarkdownDocument(str(path), frontmatter, body)


def heading_anchor(heading: str) -> str:
    """Return Heddle's deterministic Markdown heading anchor."""
    lowered = heading.strip().lower()
    lowered = re.sub(r"[^\w\s-]", "", lowered)
    return re.sub(r"[\s_]+", "-", lowered).strip("-")


def read_spec_facts(spec_path: Path) -> SpecFacts:
    document = read_markdown(spec_path)
    ac_ids = tuple(dict.fromkeys(AC_HEADING.findall(document.body)))
    return SpecFacts(path=document.path, ac_ids=ac_ids)


def read_spec_verification_facts(spec_path: Path) -> SpecVerificationFacts:
    return _spec_verification_facts(read_markdown(spec_path))


def read_plan_facts(plan_path: Path) -> PlanFacts:
    document = read_markdown(plan_path)
    section_ids = tuple(dict.fromkeys(MILESTONE_HEADING.findall(document.body)))
    return PlanFacts(path=document.path, milestone_section_ids=section_ids)


def _spec_verification_facts(document: MarkdownDocument) -> SpecVerificationFacts:
    verified_by = tuple(
        dict.fromkeys(
            reference.strip().strip("`").split("::", 1)[0].strip()
            for line in VERIFIED_BY.findall(document.body)
            for reference in line.split(",")
            if reference.strip().strip("`")
        )
    )
    # Native lifecycle is authoritative even when explicitly invalid/empty.
    # Status-only historical complete specs retain their existing guard.
    lifecycle = (
        document.frontmatter["lifecycle"]
        if "lifecycle" in document.frontmatter
        else document.frontmatter.get("status")
    )
    return SpecVerificationFacts(
        path=document.path,
        status=_string_or_none(lifecycle),
        verified_by=verified_by,
    )


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _read_yaml_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"YAML manifest is unreadable: {path}: {error}",
            hint="repair or restore the referenced YAML manifest",
        ) from error


def _load_yaml(path: Path, text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"YAML manifest is malformed: {path}: {error}",
            hint="fix the YAML syntax in the referenced manifest",
        ) from error


def read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read a YAML manifest as a mapping.

    An absent file or a non-mapping document yields an empty mapping; a
    malformed or unreadable file raises ``KernelError`` (workspace-invalid).
    """
    if not path.is_file():
        return {}
    data = _load_yaml(path, _read_yaml_text(path))
    return data if isinstance(data, dict) else {}


def read_yaml_mapping_with_duplicates(
    path: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Read a YAML manifest plus the dotted paths of any duplicate keys.

    Duplicate keys are silently last-wins under ``safe_load``; the second
    return value surfaces them so a caller can treat a duplicated manifest key
    as corruption rather than trust the last value.
    """
    if not path.is_file():
        return {}, []
    text = _read_yaml_text(path)
    data = _load_yaml(path, text)
    mapping = data if isinstance(data, dict) else {}
    return mapping, _duplicate_yaml_keys(text)


def _duplicate_yaml_keys(text: str) -> list[str]:
    duplicates: list[str] = []

    def visit(node: Node, path: str) -> None:
        if isinstance(node, MappingNode):
            seen: set[str] = set()
            for key_node, value_node in node.value:
                key = str(key_node.value)
                current = f"{path}/{key}" if path else key
                if key in seen:
                    duplicates.append(current)
                seen.add(key)
                visit(value_node, current)
        elif isinstance(node, SequenceNode):
            for index, item in enumerate(node.value):
                visit(item, f"{path}[{index}]")

    node = yaml.compose(text)
    if node is not None:
        visit(node, "")
    return duplicates
