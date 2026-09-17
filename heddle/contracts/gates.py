"""Built-in current review roles shared by policy and execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class GateDeclaration:
    name: str
    produces_verdict: bool = False


def _build_catalog(
    declarations: tuple[GateDeclaration, ...],
) -> Mapping[str, GateDeclaration]:
    catalog = {gate.name: gate for gate in declarations}
    if len(catalog) != len(declarations):
        raise ValueError("duplicate gate name in catalog")
    return MappingProxyType(catalog)


GATE_CATALOG = _build_catalog(
    (
        GateDeclaration("spec-review", produces_verdict=True),
        GateDeclaration("plan-review", produces_verdict=True),
        GateDeclaration("review-test-scaffolding", produces_verdict=True),
        GateDeclaration("milestone-review"),
        GateDeclaration("peer-review-sequential"),
        GateDeclaration("behavior-review"),
        GateDeclaration("complexity-review"),
        GateDeclaration("robustness-analysis"),
    )
)

FINDING_PRODUCING_GATES = frozenset(GATE_CATALOG)
VERDICT_PRODUCING_GATES = frozenset(
    gate.name for gate in GATE_CATALOG.values() if gate.produces_verdict
)

# Closed, product-approved independent groups. Callers select a group as one
# typed operation; role lists and worker counts are intentionally not inputs.
INDEPENDENT_GATE_GROUPS = (("behavior-review", "complexity-review"),)
