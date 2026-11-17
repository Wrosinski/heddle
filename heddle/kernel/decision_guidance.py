"""Capture the shared decision policy for session and reviewer delivery."""

from dataclasses import dataclass
from pathlib import Path

from heddle.kernel.project_config import ProjectConfig
from heddle.kernel.resources import read_resource_text


@dataclass(frozen=True)
class DecisionGuidance:
    path: Path
    source: str
    text: str


def load_decision_guidance(config: ProjectConfig) -> DecisionGuidance:
    path, source, text = read_resource_text(
        "decision-routing.md", config, contents="the shared decision policy"
    )
    return DecisionGuidance(path, source, text)


def compose_decision_guidance(policy: DecisionGuidance, body: str) -> str:
    """Preserve captured policy bytes, including meaningful identity-only edits."""
    return policy.text + "\n\n---\n\n" + body


# Stages whose sessions act on review rounds receive the shared round rule.
REVIEW_ROUND_STAGES = frozenset(
    {"spec-review", "plan-review", "scaffold", "implement", "peer-review", "robustness"}
)


def load_review_rounds(config: ProjectConfig) -> DecisionGuidance:
    """The lead's rule for further review rounds, shared by review stages.

    It reaches sessions only, never reviewers, so it is no gate's review basis.
    """
    path, source, text = read_resource_text(
        "review-rounds.md", config, contents="the shared review-round rule"
    )
    return DecisionGuidance(path, source, text)
