"""Exact, user-owned exceptions to red smoke transition policy."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from heddle.kernel.project_config import KernelError
from heddle.kernel.source_manifest import normalize_source_paths

if TYPE_CHECKING:
    from heddle.kernel.state import StateFile

ACCEPT_DEGRADED_SMOKE = "accept-degraded-smoke"
SMOKE_INPUT_KEYS = frozenset(
    {"stage", "attribution", "baseline_evidence", "outside_paths", "owned_scopes"}
)
SMOKE_BINDING_KEYS = SMOKE_INPUT_KEYS | {"bindings", "ownership"}


def validate_smoke_disposition(value: Any, *, bound: bool) -> dict[str, Any]:
    keys = SMOKE_BINDING_KEYS if bound else SMOKE_INPUT_KEYS
    if not isinstance(value, dict) or set(value) != keys:
        raise _invalid("must contain exactly " + ", ".join(sorted(keys)))
    if not isinstance(value["stage"], str) or value["stage"] not in {
        "implement",
        "peer-review",
        "robustness",
        "complete",
    }:
        raise _invalid("stage must name one verification boundary")
    for key in ("attribution", "baseline_evidence"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise _invalid(f"{key} must explain the outside-owned baseline failure")
    for key in ("outside_paths", "owned_scopes"):
        paths = value[key]
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(path, str) and path for path in paths)
            or len(set(paths)) != len(paths)
        ):
            raise _invalid(f"{key} must be a non-empty unique string array")
    if list(normalize_source_paths(value["outside_paths"])) != value["outside_paths"]:
        raise _invalid("outside_paths must be normalized and sorted")
    if any(
        re.fullmatch(r"m[1-9][0-9]*", scope) is None for scope in value["owned_scopes"]
    ):
        raise _invalid("owned_scopes must name milestone verification scopes")
    if bound:
        ownership = value["ownership"]
        if (
            not isinstance(ownership, list)
            or not ownership
            or list(normalize_source_paths(ownership)) != ownership
        ):
            raise _invalid("ownership must retain the normalized full union")
        bindings = value["bindings"]
        if not isinstance(bindings, dict) or set(bindings) != {
            "smoke",
            "acceptance",
            *value["owned_scopes"],
        }:
            raise _invalid("bindings must cover smoke, acceptance and owned_scopes")
        if any(
            not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in bindings.values()
        ):
            raise _invalid("bindings must be exact fact SHA-256 values")
    return dict(value)


def verification_fact_identity(fact: Any) -> str:
    payload = asdict(fact) if not isinstance(fact, Mapping) else dict(fact)
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def validate_smoke_proposal(state: StateFile, value: object) -> dict[str, Any]:
    """Validate the boundary and ownership without observing source files."""
    from heddle.kernel.verification import resolve_source_declaration

    proposal = validate_smoke_disposition(value, bound=False)
    if proposal["stage"] != state.stage:
        raise _invalid("disposition must bind this Tier 2/3 boundary")
    ownership = resolve_source_declaration(state, "smoke").paths
    if any(
        paths_overlap(outside, owned)
        for outside in proposal["outside_paths"]
        for owned in ownership
    ):
        raise _invalid("attributed paths overlap the full ownership union")
    for scope in proposal["owned_scopes"]:
        if not any(milestone.id == scope for milestone in state.milestones):
            raise _invalid(f"owned scope {scope!r} does not name a milestone")
    return proposal


def validate_smoke_bindings(state: StateFile, value: object) -> None:
    """Validate immutable ledger relationships, without requalifying source bytes."""
    from heddle.kernel.verification import (
        resolve_source_declaration,
        verification_command_for_scope,
    )

    bound = validate_smoke_disposition(value, bound=True)
    validate_smoke_proposal(state, {key: bound[key] for key in SMOKE_INPUT_KEYS})
    if tuple(bound["ownership"]) != resolve_source_declaration(state, "smoke").paths:
        raise _invalid("ownership differs from the full ownership union")
    latest = {fact.scope: fact for fact in state.verifications}
    for scope, identity in bound["bindings"].items():
        fact = latest.get(scope)
        if fact is None or verification_fact_identity(fact) != identity:
            raise _invalid(f"{scope} binding must match the latest scoped fact")
        if (
            fact.command != verification_command_for_scope(state, scope)
            or fact.evidence.before.source_sha256 != fact.evidence.after.source_sha256
            or (fact.exit_code == 0) != (scope != "smoke")
        ):
            raise _invalid(
                f"{scope} binding requires stable matching evidence, "
                "failed smoke and passing support"
            )


def paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _invalid(message: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message="smoke_disposition " + message,
        hint="repair the smoke disposition and obtain an explicit user resolution",
    )
