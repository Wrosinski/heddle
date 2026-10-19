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
