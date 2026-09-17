"""Pure state-document transforms for the write path.

The runtime owns all file I/O and serialization. Functions here operate on the
parsed ``state.yaml`` mapping and return either a changed document or the
unchanged document for convergent no-ops.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from heddle.contracts.result import Conflict
from heddle.contracts.review_assignments import SCHEMA as REVIEW_ASSIGNMENTS_SCHEMA
from heddle.contracts.schemas import (
    COMMAND_KEYS,
    CURRENT_STATE_SCHEMA,
)
from heddle.kernel.project_config import KernelError
from heddle.kernel.source_manifest import (
    normalize_milestone_source_paths,
    normalize_source_paths,
)
from heddle.kernel.state import (
    CompletionFact,
    SourceBaseline,
    StateFile,
    parse_state_document,
)
from heddle.kernel.verification import latest_verification_is_bound_to_declaration

_STAGES = (
    "specify",
    "spec-review",
    "plan-review",
    "scaffold",
    "implement",
    "peer-review",
    "robustness",
    "complete",
)


def seed_state(
    *,
    slug: str,
    spec: str,
    flow: str,
    authorized_through: str,
    at: str,
    feature_policy: dict[str, Any],
    source_baseline: SourceBaseline,
) -> dict[str, Any]:
    """Build the current state mapping from confirmed policy and intake."""
    return {
        "schema": CURRENT_STATE_SCHEMA,
        "feature": slug,
        "spec": spec,
        "revision": 1,
        "created": at,
        "updated": at,
        "stage": "specify",
        "authorized_through": authorized_through,
        "source_baseline": asdict(source_baseline),
        "flow": flow,
        "authorizations": [{"through": authorized_through, "source": "user", "at": at}],
        "feature_inputs": [],
        "commands": {key: "" for key in COMMAND_KEYS},
        "milestones": [],
        "gates": [],
        "verifications": [],
        "decisions": [],
        "sessions": [],
        "completion": None,
        "feature_policy": deepcopy(feature_policy),
        "policy_history": [deepcopy(feature_policy)],
        "review_assignments": {
            "schema": REVIEW_ASSIGNMENTS_SCHEMA,
            "assignments": [],
            "attempts": [],
            "dispositions": [],
            "acceptances": [],
        },
    }


def accept_completion(
    document: dict[str, Any], *, expected: StateFile, completion: CompletionFact
) -> dict[str, Any]:
    """Compare readiness inputs with locked latest facts and preserve new narrative."""
    latest = parse_state_document(document, source=Path("state.yaml"))
    if replace(latest, sessions=()) != replace(expected, sessions=()):
        raise Conflict("completion inputs changed before acceptance; reread and retry")
    # YAML serialization must keep the declared list wire shape.
    fact = asdict(completion)
    fact["verification_indexes"] = list(completion.verification_indexes)
    fact["review_run_ids"] = list(completion.review_run_ids)
    document["completion"] = fact
    return document


def _require_command_key(key: str) -> None:
    if key not in COMMAND_KEYS:
        raise KernelError(
            code="usage",
            message=f"unknown verification command key {key!r}",
            hint=f"choose one of {', '.join(COMMAND_KEYS)}",
        )


def set_command(
    document: dict[str, Any], *, key: str, shell_command: str
) -> dict[str, Any]:
    """Set one verification command through the runtime-owned write path."""
    _require_command_key(key)
    if not shell_command.strip():
        raise KernelError(
            code="usage",
            message="verification command must be non-blank",
            hint=(
                "pass the exact executable shell command; "
                "use heddle commands unset to remove it"
            ),
        )
    if document.get("commands", {}).get(key) == shell_command:
        return document
    new = deepcopy(document)
    new.setdefault("commands", {})[key] = shell_command
    return new


def unset_command(document: dict[str, Any], *, key: str) -> dict[str, Any]:
    """Remove one declaration, preserving evidence and convergent no-op identity."""
    _require_command_key(key)
    if key not in document.get("commands", {}):
        return document
    new = deepcopy(document)
    del new["commands"][key]
    return new


_MILESTONE_ID_PATTERN = re.compile(r"m(\d+)")


def add_milestone(
    document: dict[str, Any], payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Append a milestone skeleton: the id is assigned
    ``m<max+1>`` (never reused, never gap-filling; the empty skeleton —
    the native birth state — assigns ``m1``), status is born ``todo``,
    and the entry carries exactly the nine skeleton keys with the
    optional arrays defaulting to ``[]``. ``tasks`` is not a
    skeleton field; expansion owns it. Payload *shape* is the runtime
    gauntlet's concern; this transform enforces the WM §4.2 invariants
    and raises ``KernelError`` on violations."""
    normalize_milestone_source_paths(
        payload.get("owns", ()), feature=str(document.get("feature", ""))
    )
    new = deepcopy(document)
    milestones = new.setdefault("milestones", [])
    if not isinstance(milestones, list):
        raise KernelError(
            code="workspace-invalid",
            message="milestones must be a list",
            hint="shape milestones as a YAML sequence",
        )
    entry: dict[str, Any] = {
        "id": _next_milestone_id(milestones),
        "title": payload["title"],
        "satisfies": deepcopy(payload.get("satisfies", [])),
        "depends_on": deepcopy(payload.get("depends_on", [])),
        "owns": deepcopy(payload.get("owns", [])),
        "complexity": payload["complexity"],
        "verification": deepcopy(payload["verification"]),
        "status": "todo",
    }
    if payload.get("estimated_hours") is not None:
        entry["estimated_hours"] = deepcopy(payload["estimated_hours"])
    _require_known_dependencies(entry, milestones)
    milestones.append(entry)
    return new


def edit_milestone(
    document: dict[str, Any], ref: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Shallow per-field merge into one milestone skeleton:
    named fields replace wholly — arrays as whole arrays, never an
    element merge — and absent fields are untouched. ``done`` milestones
    are history and immutable except for append-only declaration repair;
    ``depends_on`` must keep referencing existing ids (WM §4.2). An
    identity payload converges to the unchanged document (equal, so
    ``commit_state_write`` skips the bump)."""
    new = deepcopy(document)
    milestones = _milestones(new)
    target = next(
        (milestone for milestone in milestones if milestone.get("id") == ref),
        None,
    )
    if target is None:
        ids = ", ".join(
            str(milestone.get("id")) for milestone in milestones if milestone.get("id")
        )
        raise KernelError(
            code="usage",
            message=f"unknown milestone ref {ref!r}",
            hint=f"existing milestone ids: {ids or '(none)'}",
        )
    if target.get("status") == "done":
        return _repair_done_milestone(new, target, ref, payload)
    if "owns" in payload:
        normalize_milestone_source_paths(
            payload["owns"], feature=str(document.get("feature", ""))
        )
    for field, value in payload.items():
        target[field] = deepcopy(value)
    _require_known_dependencies(target, milestones)
    return new


def _repair_done_milestone(
    new: dict[str, Any],
    target: dict[str, Any],
    ref: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Repair only explicitly authored missing declarations on a done milestone.

    A nonblank verification command may change only alongside a strict ownership
    expansion. This supports later source consolidation without allowing a
    command-only rewrite of completed history. The expected result stays fixed.
    """
    allowed = {"owns", "verification"}
    if not payload or not (set(payload) <= allowed):
        raise _done_repair_error(ref)

    repaired: dict[str, Any] = {}
    if "owns" in payload:
        feature = str(new.get("feature", ""))
        current = normalize_milestone_source_paths(
            target.get("owns", []), feature=feature
        )
        supplied = normalize_milestone_source_paths(payload["owns"], feature=feature)
        if not set(current) < set(supplied):
            raise _done_repair_error(ref)
        repaired["owns"] = list(supplied)

    if "verification" in payload:
        current_verification = target.get("verification")
        supplied_verification = payload["verification"]
        if not isinstance(current_verification, dict) or not isinstance(
            supplied_verification, dict
        ):
            raise _done_repair_error(ref)
        current_command = current_verification.get("command")
        current_expected = current_verification.get("expected")
        supplied_command = supplied_verification.get("command")
        supplied_expected = supplied_verification.get("expected")
        command_missing = (
            isinstance(current_command, str) and not current_command.strip()
        )
        command_relocated = (
            isinstance(current_command, str)
            and bool(current_command.strip())
            and "owns" in repaired
        )
        repairable = command_missing or command_relocated
        well_formed = (
            isinstance(current_expected, str)
            and isinstance(supplied_command, str)
            and bool(supplied_command.strip())
            and isinstance(supplied_expected, str)
            and bool(supplied_expected.strip())
        )
        if not repairable or not well_formed:
            raise _done_repair_error(ref)
        assert isinstance(current_expected, str)
        assert isinstance(supplied_expected, str)
        if current_expected.strip() and supplied_expected != current_expected:
            raise _done_repair_error(ref)
        repaired["verification"] = {
            "command": supplied_command,
            "expected": supplied_expected,
        }

    target.update(repaired)
    return new


def _done_repair_error(ref: str) -> KernelError:
    return KernelError(
        code="milestone-out-of-sequence",
        message=(
            f"milestone {ref} is done; only append-only repair of owns "
            "and/or constrained repair of its verification command is allowed"
        ),
        hint=(
            f"use `heddle milestone edit {ref} --from-file -` with an owns list "
            "that strictly contains the stored paths and verification: "
            "{command: <cmd>, expected: <unchanged text>}; a blank command may "
            "still be filled without expanding owns"
        ),
    )


def set_feature_inputs(
    document: dict[str, Any], paths: tuple[str, ...]
) -> dict[str, Any]:
    """Replace the normalized milestone-less source declaration."""
    if document.get("milestones"):
        raise KernelError(
            code="usage",
            message="feature_inputs can be authored only before milestones exist",
            hint="milestone-bearing workspaces derive feature scope from owns",
        )
    normalized = normalize_source_paths(paths)
    if not normalized:
        raise KernelError(
            code="usage",
            message="feature_inputs must not be empty",
            hint="provide at least one implementation or test source path",
        )
    if tuple(document.get("feature_inputs", [])) == normalized:
        return document
    new = deepcopy(document)
    new["feature_inputs"] = list(normalized)
    return new


def _next_milestone_id(milestones: list[Any]) -> str:
    numbers = [
        int(match.group(1))
        for milestone in milestones
        if isinstance(milestone, dict)
        for match in [_MILESTONE_ID_PATTERN.fullmatch(str(milestone.get("id")))]
        if match is not None
    ]
    return f"m{max(numbers, default=0) + 1}"


def _require_known_dependencies(entry: dict[str, Any], milestones: list[Any]) -> None:
    known = {
        milestone.get("id") for milestone in milestones if isinstance(milestone, dict)
    } | {entry.get("id")}
    missing = [
        dependency
        for dependency in entry.get("depends_on", [])
        if dependency not in known
    ]
    if missing:
        raise KernelError(
            code="milestone-out-of-sequence",
            message=(
                f"milestone {entry.get('id')} depends on missing "
                f"milestone(s): {', '.join(str(m) for m in missing)}"
            ),
            hint="depends_on must reference existing milestone ids (WM §4.2)",
        )


def advance_milestone(
    document: dict[str, Any], *, allow_complete_reconciliation: bool = False
) -> dict[str, Any]:
    """Perform one ``milestone advance`` transition.

    A pre-terminal close may add a reconciliation milestone after the final
    ownership audit. The runtime opts into that narrow repair explicitly;
    ordinary callers retain the implementation-stage contract.
    """
    stage = document.get("stage")
    if stage != "implement" and not (
        stage == "complete" and allow_complete_reconciliation
    ):
        raise KernelError(
            code="usage",
            message=(
                "milestone advance is valid only during implementation or "
                "pre-terminal close reconciliation"
            ),
            hint="run milestone advance during implement or before Phase 9 closes",
        )

    milestones = _milestones(document)
    current = [
        milestone for milestone in milestones if milestone.get("status") == "current"
    ]
    if len(current) > 1 or _has_unmet_done_or_current_dependency(milestones):
        raise KernelError(
            code="milestone-out-of-sequence",
            message="milestone ordering is inconsistent",
            hint="repair milestone ordering before advancing",
        )
    incomplete_done = _first_done_milestone_with_open_task(milestones)
    if incomplete_done is not None:
        raise KernelError(
            code="task-incomplete",
            message=f"milestone {incomplete_done.get('id')} has open tasks",
            hint="complete open tasks in done milestones before advancing",
        )
    parsed_state: StateFile | None = None
    done_milestones = tuple(
        milestone for milestone in milestones if milestone.get("status") == "done"
    )
    if done_milestones:
        parsed_state = parse_state_document(document, source=Path("state.yaml"))
    unverified_done = _first_unverified_done_milestone(parsed_state, done_milestones)
    if unverified_done is not None:
        milestone_id = _str_value(unverified_done.get("id"), "milestone id")
        raise KernelError(
            code="verification-missing",
            message=f"milestone {milestone_id} has no fresh passing verification fact",
            hint=f"record verification: heddle verify --scope {milestone_id}",
        )

    if current:
        milestone = current[0]
        if any(task.get("status") != "done" for task in milestone.get("tasks", [])):
            raise KernelError(
                code="task-incomplete",
                message=f"milestone {milestone.get('id')} has open tasks",
                hint="complete the open tasks: heddle task done <task-ref>",
            )
        milestone_id = _str_value(milestone.get("id"), "milestone id")
        if parsed_state is None:
            parsed_state = parse_state_document(document, source=Path("state.yaml"))
        if not latest_verification_is_bound_to_declaration(parsed_state, milestone_id):
            raise KernelError(
                code="verification-missing",
                message=(
                    f"milestone {milestone_id} has no fresh passing verification fact"
                ),
                hint=f"record verification: heddle verify --scope {milestone_id}",
            )
        new = deepcopy(document)
        _matching_milestone(new, milestone_id)["status"] = "done"
        return new

    if not milestones or all(
        milestone.get("status") == "done" for milestone in milestones
    ):
        return document

    done_ids = {
        _str_value(milestone.get("id"), "milestone id")
        for milestone in milestones
        if milestone.get("status") == "done"
    }
    eligible = [
        milestone
        for milestone in milestones
        if milestone.get("status") == "todo"
        and all(dep in done_ids for dep in milestone.get("depends_on", []))
    ]
    if eligible:
        # AC-5: promote the lowest-numbered dependency-satisfied todo, not
        # merely the first in file order (they coincide only for in-order plans).
        target = min(eligible, key=lambda m: _milestone_order_key(m.get("id")))
        new = deepcopy(document)
        _matching_milestone(new, _str_value(target.get("id"), "milestone id"))[
            "status"
        ] = "current"
        return new

    raise KernelError(
        code="milestone-out-of-sequence",
        message="no todo milestone has all dependencies done",
        hint="repair milestone dependencies before advancing",
    )


def mutate_task(
    document: dict[str, Any],
    verb: str,
    *,
    text: str | None = None,
    task_ref: str | None = None,
) -> dict[str, Any]:
    """Mutate the current milestone's task list."""
    milestone = _single_current_milestone(document)

    if verb == "add":
        if text is None:
            raise KernelError(
                code="usage",
                message="task add requires task text",
                hint="usage: heddle task add <text>",
            )
        new = deepcopy(document)
        current_milestone = _matching_milestone(
            new, _str_value(milestone.get("id"), "milestone id")
        )
        tasks = current_milestone.setdefault("tasks", [])
        tasks.append({"id": _next_task_id(tasks), "text": text, "status": "todo"})
        return new

    if verb not in {"done", "current"}:
        raise KernelError(
            code="usage",
            message=f"unknown task verb {verb!r}",
            hint="supported task verbs: add, done, current",
        )
    if task_ref is None:
        raise KernelError(
            code="usage",
            message=f"task {verb} requires a task reference",
            hint=f"usage: heddle task {verb} <task-ref>",
        )

    tasks = milestone.get("tasks", [])
    target = next((task for task in tasks if task.get("id") == task_ref), None)
    if target is None:
        raise KernelError(
            code="usage",
            message=f"unknown task reference {task_ref!r}",
            hint="choose a task id from the current milestone",
        )
    if verb == "done":
        if target.get("status") == "done":
            return document
        new = deepcopy(document)
        _matching_task(new, _str_value(milestone.get("id"), "milestone id"), task_ref)[
            "status"
        ] = "done"
        return new

    current_task: dict[str, Any] | None = next(
        (task for task in tasks if task.get("status") == "current"), None
    )
    if current_task is not None:
        if current_task.get("id") == task_ref:
            return document
        raise KernelError(
            code="usage",
            message="a different task is already current",
            hint=(
                f"complete the current task first: heddle task done "
                f"{current_task.get('id')}"
            ),
        )
    new = deepcopy(document)
    _matching_task(new, _str_value(milestone.get("id"), "milestone id"), task_ref)[
        "status"
    ] = "current"
    return new


def append_verification(
    document: dict[str, Any], fact: dict[str, Any]
) -> dict[str, Any]:
    new = deepcopy(document)
    new.setdefault("verifications", []).append(dict(fact))
    return new


def append_session(document: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    new = deepcopy(document)
    new.setdefault("sessions", []).append(dict(entry))
    return new


def apply_phase_exit(
    document: dict[str, Any], *, through: str | None = None
) -> dict[str, Any]:
    """Apply the stage/grant part of phase-exit without checking eligibility."""
    stage = _str_value(document.get("stage"), "stage")
    authorized = _str_value(document.get("authorized_through"), "authorized_through")
    if stage not in _STAGES:
        raise KernelError(
            code="workspace-invalid",
            message=f"stage {stage!r} is not in the canonical stage sequence",
            hint="repair state.yaml stage before exiting the phase",
        )
    if authorized not in _STAGES:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"authorized_through {authorized!r} is not in the canonical "
                "stage sequence"
            ),
            hint="repair state.yaml authorized_through before exiting the phase",
        )
    if through is not None and through not in _STAGES:
        raise KernelError(
            code="usage",
            message=f"unknown phase-exit target {through!r}",
            hint=f"--through must be one of {', '.join(_STAGES)}",
        )

    stage_index = _STAGES.index(stage)
    if stage == "complete" and through is None:
        return document
    target = through or _STAGES[min(stage_index + 1, len(_STAGES) - 1)]
    target_index = _STAGES.index(target)
    if target_index <= stage_index:
        # An upgrade can intentionally reset the grant to the
        # new tier's birth posture while leaving lifecycle stage unchanged.
        # `phase-exit --through <current-stage>` is the documented explicit
        # user re-grant. It repairs authorization only; it is not a boundary
        # transition and never rewinds or advances stage.
        authorized_index = _STAGES.index(authorized)
        if target_index == stage_index and authorized_index < stage_index:
            new = deepcopy(document)
            new["authorized_through"] = stage
            return new
        return document

    new = deepcopy(document)
    new["stage"] = _STAGES[stage_index + 1]
    authorized_index = max(_STAGES.index(authorized), target_index)
    new["authorized_through"] = _STAGES[authorized_index]
    return new


def apply_authorization_grant(
    document: dict[str, Any], *, through: str
) -> dict[str, Any]:
    """Expand authorization without advancing the lifecycle stage."""
    stage = _str_value(document.get("stage"), "stage")
    authorized = _str_value(document.get("authorized_through"), "authorized_through")
    if stage not in _STAGES:
        raise KernelError(
            code="workspace-invalid",
            message=f"stage {stage!r} is not in the canonical stage sequence",
            hint="repair state.yaml stage before recording authorization",
        )
    if authorized not in _STAGES:
        raise KernelError(
            code="workspace-invalid",
            message=(
                f"authorized_through {authorized!r} is not in the canonical "
                "stage sequence"
            ),
            hint="repair state.yaml authorized_through before recording authorization",
        )
    if through not in _STAGES:
        raise KernelError(
            code="usage",
            message=f"unknown phase-exit target {through!r}",
            hint=f"--through must be one of {', '.join(_STAGES)}",
        )
    if _STAGES.index(through) <= _STAGES.index(authorized):
        return document
    new = deepcopy(document)
    new["authorized_through"] = through
    return new


def _milestones(document: dict[str, Any]) -> list[dict[str, Any]]:
    milestones = document.get("milestones", [])
    if not isinstance(milestones, list):
        raise KernelError(
            code="workspace-invalid",
            message="milestones must be a list",
            hint="shape milestones as a YAML sequence",
        )
    return milestones


def _single_current_milestone(document: dict[str, Any]) -> dict[str, Any]:
    current = [
        milestone
        for milestone in _milestones(document)
        if milestone.get("status") == "current"
    ]
    if len(current) != 1:
        raise KernelError(
            code="usage",
            message="task commands require exactly one current milestone",
            hint="advance to a current milestone before mutating tasks",
        )
    return current[0]


def _has_unmet_done_or_current_dependency(milestones: list[dict[str, Any]]) -> bool:
    by_id = {
        milestone.get("id"): milestone
        for milestone in milestones
        if isinstance(milestone.get("id"), str)
    }
    for milestone in milestones:
        if milestone.get("status") not in {"current", "done"}:
            continue
        for dep in milestone.get("depends_on", []):
            dep_milestone = by_id.get(dep)
            if dep_milestone is None or dep_milestone.get("status") != "done":
                return True
    return False


def _first_done_milestone_with_open_task(
    milestones: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for milestone in milestones:
        if milestone.get("status") != "done":
            continue
        if any(task.get("status") != "done" for task in milestone.get("tasks", [])):
            return milestone
    return None


def _first_unverified_done_milestone(
    state: StateFile | None, milestones: tuple[dict[str, Any], ...]
) -> dict[str, Any] | None:
    if state is None:
        return None
    for milestone in milestones:
        milestone_id = _str_value(milestone.get("id"), "milestone id")
        if not latest_verification_is_bound_to_declaration(state, milestone_id):
            return milestone
    return None


def _matching_milestone(document: dict[str, Any], milestone_id: str) -> dict[str, Any]:
    return next(
        milestone
        for milestone in _milestones(document)
        if milestone.get("id") == milestone_id
    )


def _matching_task(
    document: dict[str, Any], milestone_id: str, task_ref: str
) -> dict[str, Any]:
    milestone = _matching_milestone(document, milestone_id)
    return next(
        task for task in milestone.get("tasks", []) if task.get("id") == task_ref
    )


def _next_task_id(tasks: list[dict[str, Any]]) -> str:
    highest = 0
    for task in tasks:
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.startswith("t"):
            continue
        suffix = task_id[1:]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"t{highest + 1}"


def _milestone_order_key(milestone_id: Any) -> tuple[int, str]:
    """Sort key promoting the lowest-numbered ``m<N>`` milestone;
    non-numeric ids sort last by id string, keeping promotion deterministic."""
    if isinstance(milestone_id, str) and milestone_id.startswith("m"):
        suffix = milestone_id[1:]
        if suffix.isdigit():
            return (int(suffix), milestone_id)
    return (10**9, str(milestone_id))


def _str_value(value: Any, label: str) -> str:
    if isinstance(value, str):
        return value
    raise KernelError(
        code="workspace-invalid",
        message=f"{label} must be a string",
        hint=f"repair {label} in state.yaml",
    )
