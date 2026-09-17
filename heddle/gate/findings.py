from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from heddle.gate.results import (
    ReviewContent,
    ReviewResult,
    VerdictReviewContent,
)
from heddle.gate.types import FindingsInfo, VerdictInfo


@dataclass(frozen=True)
class FindingDetail:
    """An ordered projection of one validated native finding for runtime use."""

    id: str
    severity: str
    classification: str
    title: str
    recommendation: str | None = None
    route: str | None = None


SEVERITIES = ("critical", "important", "minor")
CLASSIFICATIONS = ("implement", "report", "ignore", "unknown")


def structured_findings(content: ReviewContent) -> FindingsInfo:
    """Project operational counts from validated content, without parsing views."""
    severity = dict.fromkeys(SEVERITIES, 0)
    classification = dict.fromkeys(CLASSIFICATIONS, 0)
    combinations = {
        f"{level}/{kind}": 0 for level in SEVERITIES for kind in CLASSIFICATIONS
    }
    for item in content.findings:
        severity[item.severity] += 1
        classification[item.classification] += 1
        combinations[f"{item.severity}/{item.classification}"] += 1
    return FindingsInfo(
        severity,
        classification,
        combinations,
        len(content.findings),
        0,
    )


def structured_details(content: ReviewContent) -> tuple[FindingDetail, ...]:
    return tuple(
        FindingDetail(
            item.id,
            item.severity,
            item.classification,
            item.title,
            item.recommendation if item.classification == "report" else None,
            item.decision.route if item.decision is not None else None,
        )
        for item in content.findings
    )


def structured_verdict(content: ReviewContent) -> VerdictInfo | None:
    if not isinstance(content, VerdictReviewContent):
        return None
    verdict = content.verdict
    return VerdictInfo(
        verdict.status,
        verdict.rerun_recommended,
        verdict.status in {"pass", "pass_with_conditions"}
        and not verdict.rerun_recommended,
    )


def render_review_markdown(result: ReviewResult) -> str:
    """A deterministic, complete human view; the JSON result owns its facts."""
    content = asdict(result.content)
    lines = [
        f"# {result.content.role.replace('-', ' ').title()}: "
        f"{result.invocation.feature} / {result.invocation.scope}",
        "",
    ]
    for label, value in (
        ("Summary", content.pop("summary")),
        ("Findings", content.pop("findings")),
        ("Role Assessments", content.pop("details")),
        *[
            (key.replace("_", " ").title(), value)
            for key, value in content.items()
            if key != "role"
        ],
        ("Invocation", asdict(result.invocation)),
    ):
        lines.extend([f"## {label}", "", *_render_fields(value, 3), ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_fields(value: Any, depth: int) -> list[str]:
    if isinstance(value, dict):
        lines: list[str] = []
        for name, item in value.items():
            label = name.replace("_", " ").title()
            if isinstance(item, (dict, list, tuple)):
                lines.extend(
                    [
                        f"{'#' * min(depth, 6)} {label}",
                        "",
                        *_render_fields(item, depth + 1),
                        "",
                    ]
                )
            else:
                lines.extend(
                    [f"**{label}:** {'Not supplied' if item is None else item}", ""]
                )
        return lines
    if isinstance(value, (list, tuple)):
        if not value:
            return ["None."]
        lines = []
        for index, item in enumerate(value, 1):
            if isinstance(item, dict):
                label = (
                    item.get("id")
                    or item.get("ac_id")
                    or item.get("name")
                    or str(index)
                )
                lines.extend(
                    [
                        f"{'#' * min(depth, 6)} {label}",
                        "",
                        *_render_fields(item, depth + 1),
                        "",
                    ]
                )
            else:
                lines.append(f"- {item}")
        return lines
    return [str(value)]
