"""Derive the review slots that may launch together from one snapshot.

A launch set is every slot readiness would route next whose review does not
read another member's findings: the missing slots of each routed review's
current round, or every slot of an implicit first round, that admission
accepts. The rule reads only state and config facts, so it names no role or
stage; catalog order and slot order fix publication order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.feature_policy import Reviewer
from heddle.contracts.gates import GATE_CATALOG
from heddle.contracts.result import NextAction
from heddle.gate.registry import GATES
from heddle.gate.types import GateInvocationOverrides
from heddle.kernel import review_assignments as core
from heddle.kernel.model import FeatureSnapshot
from heddle.kernel.project_config import KernelError, ProjectConfig
from heddle.runtime.review_assignments import (
    changed_source_attempt_is_pending,
    resolve_invocation,
)

_CATALOG_ORDER = {name: index for index, name in enumerate(GATE_CATALOG)}


@dataclass(frozen=True)
class LaunchMember:
    """One review slot of a launch set, in publication order."""

    role: str
    scope: str
    slot: str
    reviewer: Reviewer
    action_index: int

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.role, self.scope, self.slot)

    @property
    def label(self) -> str:
        return f"{self.role} {self.slot}"


def launch_set(
    config: ProjectConfig,
    snapshot: FeatureSnapshot,
    observed: dict[str, Any],
    actions: list[NextAction] | tuple[NextAction, ...],
    *,
    milestone_scope: str | None = None,
) -> tuple[LaunchMember, ...]:
    """Return the members readiness's routed reviews would launch now.

    ``observed`` is the review-assignment projection for ``snapshot``;
    ``milestone_scope`` is the milestone the caller selected for a gate that
    reviews one milestone. Sequential hosts launch nothing together.
    """
    if config.review_launch != "concurrent" or not observed:
        return ()
    rows = {
        (row["role"], row["scope"]): row
        for row in observed["review_closure"]["assignments"]
    }
    state = snapshot.state
    seen: set[str] = set()
    ranked: list[tuple[tuple[int, int], LaunchMember]] = []
    for index, action in enumerate(actions):
        operation = (
            action.action.operation
            if isinstance(action.action, ops.CommandAction)
            else None
        )
        if not isinstance(operation, ops.RunGate) or operation.gate in seen:
            continue
        seen.add(operation.gate)
        gate_type = GATES.get(operation.gate)
        if gate_type is None:
            continue
        scope = milestone_scope if gate_type.requires_milestone else "feature"
        row = rows.get((operation.gate, scope))
        if scope is None or row is None:
            continue
        assignment = core.assignment_for(state, operation.gate, scope)
        if core.assignment_sealed(
            state, assignment
        ) or changed_source_attempt_is_pending(state, assignment):
            continue
        current = core.launch_round(state, assignment)
        missing = (
            set(row["missing_slots"])
            if assignment.rounds
            else {slot.name for slot in current.slots}
        )
        for position, slot in enumerate(current.slots):
            if slot.name not in missing:
                continue
            try:
                resolve_invocation(
                    snapshot,
                    config,
                    gate_type,
                    GateInvocationOverrides(**asdict(slot.reviewer)),
                    admit=True,
                )
            except KernelError:
                continue
            ranked.append(
                (
                    (_CATALOG_ORDER[operation.gate], position),
                    LaunchMember(
                        operation.gate, scope, slot.name, slot.reviewer, index
                    ),
                )
            )
    return tuple(member for _rank, member in sorted(ranked, key=lambda r: r[0]))
