"""Pending flow-change resolution and phase-exit grant helpers.

Extracted move-only from ``write_path.py``: the
flow-change state machine — resolve the latest pending entry, apply
it at a boundary, supersede stale pendings — plus the authorization-grant
append and the grant-blocker derivation ``run_phase_exit`` judges an exit
against. Pure document/snapshot transforms; no I/O, no envelope.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from heddle.contracts.schemas import (
    FLOW_CHANGE_APPLIED,
    FLOW_CHANGE_PENDING,
    FLOW_CHANGE_SUPERSEDED,
    FLOW_MODES,
)
from heddle.kernel.model import FeatureSnapshot


def pending_flow_change(document: Mapping[str, Any]) -> dict[str, Any] | None:
    changes = document.get("flow_changes", [])
    if not isinstance(changes, list):
        return None
    return pending_flow_change_from_entries(changes)


def pending_flow_change_from_entries(
    changes: tuple[Mapping[str, Any], ...] | list[Any],
) -> dict[str, Any] | None:
    for change in reversed(changes):
        if isinstance(change, dict) and change.get("status") == FLOW_CHANGE_PENDING:
            return change
    return None


def apply_pending_flow_change(document: dict[str, Any], *, at: str) -> None:
    pending = pending_flow_change(document)
    if pending is None:
        return
    mode = pending.get("to")
    if mode not in FLOW_MODES:
        return
    document["flow"] = mode
    pending["status"] = FLOW_CHANGE_APPLIED
    pending["applied_at"] = at


def supersede_pending_flow_changes(document: dict[str, Any], *, at: str) -> None:
    changes = document.get("flow_changes", [])
    if not isinstance(changes, list):
        return
    for change in changes:
        if not isinstance(change, dict):
            continue
        if change.get("status") != FLOW_CHANGE_PENDING:
            continue
        change["status"] = FLOW_CHANGE_SUPERSEDED
        change["superseded_at"] = at


def append_authorization_grant(
    document: dict[str, Any], *, through: str, source: str, at: str
) -> None:
    document.setdefault("authorizations", []).append(
        {"through": through, "source": source, "at": at}
    )


def phase_exit_grant_blockers(snapshot: FeatureSnapshot) -> tuple[str, ...]:
    # A policy terminal grant requires the full derived blocking set
    # to be empty, including verification-missing — a recorded
    # verify fact for every done milestone. verification-missing is NOT carved
    # out at auto-complete; the auto-close clean-venv suite (test_command) is an
    # additional close gate, never a substitute for the verification fact.
    ignored = {"awaiting-human-authorization"}
    return tuple(code for code in snapshot.blocking_conditions if code not in ignored)
