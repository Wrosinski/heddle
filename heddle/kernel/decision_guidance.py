"""Capture the shared decision policy for session and reviewer delivery."""

from dataclasses import dataclass
from pathlib import Path

from heddle.kernel.project_config import KernelError, ProjectConfig
from heddle.kernel.resources import resolve_resource


@dataclass(frozen=True)
class DecisionGuidance:
    path: Path
    source: str
    text: str


def load_decision_guidance(config: ProjectConfig) -> DecisionGuidance:
    path, source = resolve_resource("decision-routing.md", config)
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"decision-routing.md is unreadable at {path}: {error}",
            hint="repair the selected decision-routing.md resource as readable UTF-8",
        ) from error
    if not text.strip():
        raise KernelError(
            code="workspace-invalid",
            message=f"decision-routing.md is blank at {path}",
            hint="restore the shared decision policy in the selected resource",
        )
    return DecisionGuidance(path, source, text)


def compose_decision_guidance(policy: DecisionGuidance, body: str) -> str:
    """Preserve captured policy bytes, including meaningful identity-only edits."""
    return policy.text + "\n\n---\n\n" + body
