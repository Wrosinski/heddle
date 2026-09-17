"""Current operational state and its pure decoded-document validator."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from uuid import UUID

import yaml

from heddle.contracts import decisions as decision_types
from heddle.contracts.feature_policy import COMPLEXITIES, ConfirmedPolicy
from heddle.contracts.gate_execution import VALID_OVERRIDE_AXES
from heddle.contracts.review_assignments import ReviewAssignments
from heddle.contracts.schemas import (
    AUTHORIZATION_KEYS,
    AUTHORIZATION_OPTIONAL_KEYS,
    CLOSE_SUITE_KEYS,
    COMMAND_KEYS,
    COMPLETION_KEYS,
    CURRENT_STATE_SCHEMA,
    DECISION_KEYS,
    DECISION_OPTIONAL_KEYS,
    DECISION_RESOLUTION_KINDS,
    FLOW_MODES,
    GATE_KEYS,
    GATE_RUN_ASSIGNMENT_KEYS,
    GATE_RUN_COMPLETED_KEYS,
    GATE_RUN_KEYS,
    GATE_RUN_OPTIONAL_KEYS,
    MILESTONE_KEYS,
    MILESTONE_STATUSES,
    REPORT_FINDING_KEYS,
    RESOLUTION_PAYLOAD_KEYS,
    SESSION_KEYS,
    SESSION_OPTIONAL_KEYS,
    SESSION_PROGRESS_FIELD_TYPES,
    SPEC_STAMP_KEYS,
    STAGES,
    STATE_REQUIRED_KEYS,
    STATE_SCHEMA_V10,
    STATE_SCHEMA_VERSIONS,
    STATE_V9_OPTIONAL_TOP_LEVEL_KEYS,
    STATE_V9_TOP_LEVEL_KEYS,
    STATE_V10_TOP_LEVEL_KEYS,
    TASK_KEYS,
    VERIFICATION_EVIDENCE_KEYS,
    VERIFICATION_KEYS,
)
from heddle.kernel.feature_policy import parse_axes, parse_policy
from heddle.kernel.project_config import KernelError
from heddle.kernel.review_assignments import (
    parse_assignments,
    validate_assignments,
    validate_boundary_acceptances,
)
from heddle.kernel.smoke_disposition import (
    validate_smoke_bindings,
    validate_smoke_disposition,
)
from heddle.kernel.source_manifest import (
    EvidenceReference,
    decode_evidence_reference,
)

INCOMPATIBLE_STATE_SCHEMA = "incompatible-state-schema"

STATE_LOADER_ACCELERATED = hasattr(yaml, "CSafeLoader")
STATE_YAML_LOADER = yaml.CSafeLoader if STATE_LOADER_ACCELERATED else yaml.SafeLoader


def decode_state_yaml(
    text: str, *, loader: type[yaml.SafeLoader] | None = None
) -> object:
    """Decode state with one explicitly safe loader owner."""
    selected = STATE_YAML_LOADER if loader is None else loader
    safe_loaders = {yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)}
    if selected not in safe_loaders:
        raise ValueError("state decoding requires SafeLoader or CSafeLoader")
    return yaml.load(text, Loader=selected)


def state_loader_capability() -> dict[str, bool | str]:
    """Describe the active safe decoder without reading or rewriting state."""
    safe_loaders = {yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)}
    return {
        "safe": STATE_YAML_LOADER in safe_loaders,
        "loader": STATE_YAML_LOADER.__name__,
        "accelerated": STATE_LOADER_ACCELERATED,
    }


# workflow-model §6.2 — the unified finding taxonomy (severity C/I/M ×
# classification IMPLEMENT/REPORT/IGNORE, `unknown` when unclassified), which
# replaced the legacy two-category counter pair.
FINDINGS_KEYS = ("by_severity", "by_classification", "total", "contradictions")

# workflow-model §4.1/§5 — verification declarations carry exactly these.
MILESTONE_VERIFICATION_KEYS = ("command", "expected")


def _strict_int(value: Any) -> bool:
    """YAML booleans satisfy ``isinstance(value, int)`` (``True == 1``);
    the strict §10.1 posture rejects them for
    every integer field."""
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_d33_findings(findings: Any) -> bool:
    """The §6.2 finding sub-schema: exactly ``FINDINGS_KEYS``, with
    ``by_severity``/``by_classification`` integer-valued mappings and integer
    ``total``/``contradictions``."""
    if not isinstance(findings, dict) or sorted(findings) != sorted(FINDINGS_KEYS):
        return False
    by_severity = findings["by_severity"]
    by_classification = findings["by_classification"]
    if not isinstance(by_severity, dict) or not isinstance(by_classification, dict):
        return False
    counts = (*by_severity.values(), *by_classification.values())
    return (
        all(_strict_int(count) for count in counts)
        and _strict_int(findings["total"])
        and _strict_int(findings["contradictions"])
    )


@dataclass(frozen=True)
class Task:  # §4.1; TASK_KEYS
    id: str  # "t<N>"
    text: str
    status: str  # ∈ MILESTONE_STATUSES


@dataclass(frozen=True)
class Milestone:  # §4.1/§10.1; MILESTONE_KEYS
    id: str  # "m<N>"
    title: str
    satisfies: tuple[str, ...]  # AC ids
    depends_on: tuple[str, ...]  # milestone ids
    owns: tuple[str, ...]  # repo-relative paths
    complexity: str  # ∈ ("low", "medium", "high")
    estimated_hours: tuple[int, int] | None  # [lo, hi]; optional v7 metadata
    verification: Mapping[str, str]  # {command, expected}
    status: str  # ∈ MILESTONE_STATUSES
    tasks: tuple[Task, ...]  # empty until expanded (§4.1 "present
    #                          once expanded" → absent key = ())


@dataclass(frozen=True)
class ReportFinding:
    finding_id: str
    title: str
    severity: str


@dataclass(frozen=True)
class GateRun:  # §6.2; GATE_RUN_KEYS
    run_id: str
    report_findings: tuple[ReportFinding, ...]
    at: str  # ISO-8601, informational (never sort key)
    cli: str
    artifact: str  # workspace-relative path
    input_hash: str
    verdict: Mapping[str, Any]  # shape depends on gate class (§6.2):
    #                             verdict-producing gates record
    #                             {status, rerun_recommended}; others
    #                             record as reported (e.g. {raw: CLEAN})
    findings: Mapping[str, Any]  # {by_severity, by_classification,
    #                              total, contradictions} (AC-11)
    # V4 completed events require these native bindings. Errors may omit them
    # when preparation or canonical publication did not complete.
    review_basis_hash: str | None = None
    execution_config: Mapping[str, str] | None = None
    overridden_axes: tuple[str, ...] | None = None
    artifact_sha256: str | None = None
    prompt_version: str | None = None
    effective_prompt_sha256: str | None = None
    failure_reason: str | None = None
    review_policy_id: str | None = None
    assignment_id: str | None = None
    round_number: int | None = None
    reviewer_slot: str | None = None


@dataclass(frozen=True)
class GateFact:  # §6.2; GATE_KEYS
    gate: str
    scope: str  # "feature" | milestone id
    runs: tuple[GateRun, ...]  # append order; latest = last element


@dataclass(frozen=True)
class VerificationEvidence:
    schema: str
    before: EvidenceReference
    after: EvidenceReference


def verification_evidence_payload(evidence: VerificationEvidence) -> dict[str, Any]:
    """Serialize the compact v8 verification evidence wire shape."""
    return asdict(evidence)


@dataclass(frozen=True)
class VerificationFact:  # §5; VERIFICATION_KEYS
    scope: str  # "m<N>" | smoke|acceptance|live
    command: str
    exit_code: int  # 0 = passing
    at: str
    log: str  # workspace-relative path
    evidence: VerificationEvidence


@dataclass(frozen=True)
class DecisionFact:  # §7; DECISION_KEYS
    id: str
    kind: decision_types.DecisionKind
    source: str
    title: str
    status: str  # "resolved" ⇒ no pending-decisions block
    resolution: decision_types.Resolution | None
    rationale: str | None
    routes_to: tuple[str, ...]
    created_at: str
    resolved_at: str | None
    escalation_class: int | None = None
    question: str | None = None
    options: tuple[str, ...] = ()
    recommendation: str | None = None
    resolution_source: str | None = None
    smoke_disposition: Mapping[str, Any] | None = None
    origin_run_id: str | None = None
    origin_finding_id: str | None = None
    target_stage: str | None = None
    target_blocker: str | None = None
    supersedes: str | None = None


@dataclass(frozen=True)
class SessionFact:  # §8; SESSION_KEYS
    started_at: str
    ended_at: str
    completed: tuple[str, ...]
    started: tuple[str, ...]
    key_context: str
    next_steps: str
    blockers: tuple[str, ...]
    stage: str | None = None
    revision_before: int | None = None
    revision_after: int | None = None
    advanced: bool | None = None


@dataclass(frozen=True)
class AuthorizationFact:  # §2.2 authorization fact
    through: str
    source: str | None
    at: str | None


@dataclass(frozen=True)
class SpecStampIdentity:
    before_sha256: str
    after_sha256: str
    executable: bool


@dataclass(frozen=True)
class CloseSuiteFact:
    command: str
    log: str
    exit_code: int


@dataclass(frozen=True)
class SourceBaseline:
    """Immutable commit boundary captured at feature admission."""

    kind: Literal["git-commit"]
    oid: str


def parse_source_baseline(value: Any) -> SourceBaseline:
    """Decode the closed baseline shape without consulting Git."""
    if not isinstance(value, dict) or set(value) != {"kind", "oid"}:
        raise ValueError("source_baseline needs exactly kind and oid")
    if value["kind"] != "git-commit":
        raise ValueError("source_baseline.kind must be git-commit")
    oid = value["oid"]
    if (
        not isinstance(oid, str)
        or re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", oid) is None
    ):
        raise ValueError(
            "source_baseline.oid must be a full 40- or 64-character lowercase "
            "hexadecimal ID"
        )
    return SourceBaseline(kind="git-commit", oid=oid)


@dataclass(frozen=True)
class CompletionFact:
    accepted_at: str
    authorization_index: int
    verification_indexes: tuple[int, ...]
    review_run_ids: tuple[str, ...]
    accepted_smoke_decision_id: str | None
    spec_stamp: SpecStampIdentity
    close_suite: CloseSuiteFact | None


@dataclass(frozen=True)
class StateFile:  # §10.1 required keys plus additive optional keys
    schema: str  # required; supported version determines the wire contract
    feature: str  # required; slug = workspace dir name
    spec: str  # required; repo-relative spec path
    revision: int  # required; CAS token (§11)
    created: str  # required; ISO-8601
    updated: str  # required; ISO-8601
    stage: str  # required; ∈ STAGES (8 slugs, §2.1)
    authorized_through: str  # required; ∈ STAGES
    commands: Mapping[str, str]  # keys ⊆ COMMAND_KEYS; may be empty
    milestones: tuple[Milestone, ...]  # ordered as in file
    gates: tuple[GateFact, ...]
    verifications: tuple[VerificationFact, ...]
    decisions: tuple[DecisionFact, ...]
    sessions: tuple[SessionFact, ...]  # append order = time order
    completion: CompletionFact | None
    source_baseline: SourceBaseline | None = None
    feature_inputs: tuple[str, ...] = ()
    flow: str | None = None
    authorizations: tuple[AuthorizationFact, ...] = ()
    flow_changes: tuple[Mapping[str, Any], ...] = ()
    feature_policy: ConfirmedPolicy | None = None
    policy_history: tuple[ConfirmedPolicy, ...] = ()
    review_assignments: ReviewAssignments = ReviewAssignments()
    assessments: tuple[Mapping[str, Any], ...] = ()
    intake: Mapping[str, str] | None = None


def read_state_file(path: Path) -> StateFile:
    """Parse and validate one ``state.yaml`` into the typed §10.1 model;
    every violation raises ``KernelError`` with code ``workspace-invalid``.
    Handlers own the code→exit mapping."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        # UnicodeDecodeError is a ValueError, not an
        # OSError — a byte-corrupt state file must map to workspace-invalid
        # like every other unreadable state (A6; the config loader's guard
        # is the mirror).
        raise _invalid(
            path,
            f"state file is unreadable: {error}",
            "check that the file exists and is readable",
        ) from error
    try:
        document = decode_state_yaml(text)
    except yaml.YAMLError as error:
        raise _invalid(
            path,
            f"malformed YAML: {error}",
            "fix the YAML syntax",
        ) from error
    return parse_state_document(document, source=path)


def parse_state_document(document: object, *, source: Path) -> StateFile:
    """Validate an already-decoded state document against the full §10.1
    contract and return its typed model.

    ``read_state_file`` owns I/O and YAML decoding, while recording and other
    read-transform seams can reuse this pure validator without a second disk
    read or temporary serialization round-trip. ``source`` is diagnostic
    identity only; this function never reads or mutates it.
    """
    path = source
    if not isinstance(document, dict):
        raise _invalid(
            path,
            f"state document must be a mapping, got {type(document).__name__}",
            "shape state.yaml per workflow-model §10.1",
        )

    schema = document.get("schema")
    if schema not in STATE_SCHEMA_VERSIONS:
        encountered = repr(schema) if "schema" in document else "missing"
        raise _invalid(
            path,
            f"schema {encountered} is unsupported; current version is "
            f"{CURRENT_STATE_SCHEMA!r}",
            "preserve incompatible evidence under its compatible runtime and "
            "start a fresh workspace with the current runtime",
            reason=INCOMPATIBLE_STATE_SCHEMA,
        )
    if "migration" in document:
        raise _invalid(
            path,
            "migration attribution is unsupported by the current runtime",
            "preserve the workspace under its compatible runtime or start fresh",
            reason=INCOMPATIBLE_STATE_SCHEMA,
        )
    required_keys = (
        STATE_V10_TOP_LEVEL_KEYS
        if schema == STATE_SCHEMA_V10
        else STATE_V9_TOP_LEVEL_KEYS
    )
    missing = [key for key in required_keys if key not in document]
    if missing:
        raise _invalid(
            path,
            f"missing required top-level keys: {missing}",
            "every §10.1 key must be present (collections may be empty)",
        )
    optional_keys = STATE_V9_OPTIONAL_TOP_LEVEL_KEYS
    allowed_top_level = required_keys + optional_keys
    unknown = [key for key in document if key not in allowed_top_level]
    if unknown:
        raise _invalid(
            path,
            f"unknown top-level keys: {unknown}",
            f"current state allows required keys {list(required_keys)} "
            f"and optional keys {list(optional_keys)}",
        )

    scalars = {key: document[key] for key in STATE_REQUIRED_KEYS}
    for key in ("feature", "spec", "created", "updated"):
        if not isinstance(scalars[key], str):
            raise _invalid(
                path,
                f"{key} must be a string, got {type(scalars[key]).__name__}",
                f"fix the {key} value",
            )
    if not _strict_int(scalars["revision"]):
        raise _invalid(
            path,
            f"revision must be an integer, got {type(scalars['revision']).__name__}",
            "fix the revision value (the §11 CAS token)",
        )
    for key in ("stage", "authorized_through"):
        if scalars[key] not in STAGES:
            raise _invalid(
                path,
                f"{key} is {scalars[key]!r}, not one of the eight §2.1 stages "
                f"{list(STAGES)}",
                f"fix the {key} value",
            )

    flow = document.get("flow")
    if flow is not None and flow not in FLOW_MODES:
        raise _invalid(
            path,
            f"flow is {flow!r}, not one of {list(FLOW_MODES)}",
            "fix flow to `hitl` or `auto`",
        )

    source_baseline = None
    if schema == STATE_SCHEMA_V10:
        try:
            source_baseline = parse_source_baseline(document["source_baseline"])
        except ValueError as error:
            raise _invalid(
                path,
                str(error),
                "restore the immutable source baseline recorded at admission",
            ) from error

    confirmed_policy = None
    history: tuple[ConfirmedPolicy, ...] = ()
    assessments: tuple[Mapping[str, Any], ...] = ()
    intake_binding = document.get("intake")
    try:
        confirmed_policy = parse_policy(document["feature_policy"])
        history = tuple(
            parse_policy(item)
            for item in _entries(
                path,
                "policy_history",
                document.get("policy_history", [document["feature_policy"]]),
            )
        )
        if (
            not history
            or history[-1] != confirmed_policy
            or any(
                earlier.revision >= later.revision
                for earlier, later in zip(history, history[1:], strict=False)
            )
        ):
            raise ValueError("policy history must increase and end at current policy")
        assessments = tuple(
            dict(item)
            for item in _entries(path, "assessments", document.get("assessments", []))
        )
        for assessment in assessments:
            _require_keys(path, "assessment", assessment, ("axes", "at"))
            parse_axes(assessment["axes"])
            _str_value(path, "assessment.at", assessment["at"])
        if intake_binding is not None:
            if not isinstance(intake_binding, dict) or set(intake_binding) != {
                "path",
                "sha256",
            }:
                raise ValueError("intake binding needs path and sha256")
            _str_value(path, "intake.path", intake_binding["path"])
            if intake_binding["path"] != f".heddle/intake/{scalars['feature']}.yaml":
                raise ValueError("intake binding must name this feature's intake")
            if (
                not isinstance(intake_binding["sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", intake_binding["sha256"]) is None
            ):
                raise ValueError("intake binding needs SHA-256 identity")
    except ValueError as error:
        raise _invalid(
            path,
            str(error),
            "use a valid confirmed current policy",
        ) from error
    try:
        review_assignments = parse_assignments(document.get("review_assignments"))
    except ValueError as error:
        raise _invalid(
            path,
            str(error),
            "use a valid current review-assignment ledger",
        ) from error
    state = StateFile(
        schema=schema,
        feature=scalars["feature"],
        spec=scalars["spec"],
        revision=scalars["revision"],
        created=scalars["created"],
        updated=scalars["updated"],
        stage=scalars["stage"],
        authorized_through=scalars["authorized_through"],
        commands=_commands(path, document["commands"]),
        milestones=tuple(
            _milestone(path, entry, current=True)
            for entry in _entries(path, "milestones", document["milestones"])
        ),
        gates=tuple(
            parse_gate_fact(path, entry)
            for entry in _entries(path, "gates", document["gates"])
        ),
        verifications=tuple(
            _verification(path, entry)
            for entry in _entries(path, "verifications", document["verifications"])
        ),
        decisions=tuple(
            _decision(path, entry)
            for entry in _entries(path, "decisions", document["decisions"])
        ),
        sessions=tuple(
            _session(path, entry)
            for entry in _entries(path, "sessions", document["sessions"])
        ),
        completion=None,
        source_baseline=source_baseline,
        feature_inputs=_str_tuple(path, "feature_inputs", document["feature_inputs"]),
        feature_policy=confirmed_policy,
        policy_history=history,
        review_assignments=review_assignments,
        assessments=assessments,
        intake=intake_binding,
        flow=flow,
        authorizations=tuple(
            _authorization(path, entry)
            for entry in _entries(
                path, "authorizations", document.get("authorizations", [])
            )
        ),
        flow_changes=tuple(
            dict(entry)
            for entry in _entries(
                path, "flow_changes", document.get("flow_changes", [])
            )
        ),
    )
    from heddle.kernel.reviews import validate_relationships

    validate_assignments(state)
    validate_boundary_acceptances(state)
    validate_relationships(state, source=path)
    if document["completion"] is not None:
        state = replace(
            state, completion=_completion(path, document["completion"], state)
        )
    return state


def validate_state_transition(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Keep the wire version and admission baseline immutable across writes."""
    if after.get("schema") != before.get("schema"):
        raise _invalid(
            Path("state.yaml"),
            "state schema is immutable across ordinary writes",
            "preserve the existing state schema",
        )
    if before.get("source_baseline") != after.get("source_baseline"):
        raise _invalid(
            Path("state.yaml"),
            "source_baseline is immutable after admission",
            "restore the baseline recorded at admission",
        )


def _completion(path: Path, value: Any, state: StateFile) -> CompletionFact:
    """Decode references against this ledger; acceptance never copies fact bodies."""

    def reject(problem: str) -> KernelError:
        return _invalid(
            path,
            f"completion: {problem}",
            "restore the accepted ledger or start a fresh workspace",
        )

    if not isinstance(value, dict):
        raise reject("must be a mapping or null")
    _require_keys(path, "completion", value, COMPLETION_KEYS)
    if state.stage != "complete" or any(
        milestone.status != "done"
        or any(task.status != "done" for task in milestone.tasks)
        for milestone in state.milestones
    ):
        raise reject("requires complete stage and finished milestones/tasks")
    at = _str_value(path, "completion.accepted_at", value["accepted_at"])
    try:
        parsed_at = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError as error:
        raise reject("accepted_at must be an ISO-8601 timestamp") from error
    if parsed_at.tzinfo is None:
        raise reject("accepted_at must include a timezone")

    def index(label: str, item: Any, size: int) -> int:
        if not _strict_int(item) or not 0 <= item < size:
            raise reject(f"{label} must reference an existing fact by zero-based index")
        return cast(int, item)

    authorization_index = index(
        "authorization_index", value["authorization_index"], len(state.authorizations)
    )
    if state.authorizations[authorization_index].through != "complete":
        raise reject("authorization_index must reference a complete grant")
    if any(
        grant.through == "complete"
        for grant in state.authorizations[authorization_index + 1 :]
    ):
        raise reject(
            "authorization_index must reference the last appended complete grant"
        )
    indexes = value["verification_indexes"]
    if not isinstance(indexes, list) or not indexes:
        raise reject("verification_indexes must be a nonempty list")
    verification_indexes = tuple(
        index("verification_indexes", item, len(state.verifications))
        for item in indexes
    )
    if len(set(verification_indexes)) != len(verification_indexes):
        raise reject("verification_indexes must be unique")
    from heddle.kernel.verification import (
        required_verification_scopes,
        verification_command_for_scope,
    )

    scopes = required_verification_scopes(state)
    selected = tuple(state.verifications[i] for i in verification_indexes)
    if tuple(fact.scope for fact in selected) != scopes:
        raise reject(
            "verification_indexes must reference the required completion scopes"
        )
    for fact_index, fact in zip(verification_indexes, selected, strict=True):
        if any(
            later.scope == fact.scope for later in state.verifications[fact_index + 1 :]
        ):
            raise reject("verification_indexes must reference the latest scoped facts")
        if (
            fact.command != verification_command_for_scope(state, fact.scope)
            or fact.evidence.before.source_sha256 != fact.evidence.after.source_sha256
        ):
            raise reject("verification_indexes must reference stable matching commands")
    review_run_ids = _str_tuple(
        path, "completion.review_run_ids", value["review_run_ids"]
    )
    available_runs = {
        run.run_id
        for gate in state.gates
        for run in gate.runs
        if run.verdict.get("status") != "error"
    }
    if (
        len(set(review_run_ids)) != len(review_run_ids)
        or not set(review_run_ids) <= available_runs
    ):
        raise reject("review_run_ids must reference unique completed runs")
    smoke_id = _optional_str(
        path,
        "completion.accepted_smoke_decision_id",
        value["accepted_smoke_decision_id"],
    )
    if smoke_id is not None:
        decision = next(
            (decision for decision in state.decisions if decision.id == smoke_id), None
        )
        if (
            decision is None
            or decision.status != "resolved"
            or decision.resolution_source != "user"
            or decision.escalation_class != 2
            or not (decision.resolved_at or "").strip()
            or not isinstance(decision.resolution, decision_types.AcceptDegradedSmoke)
        ):
            raise reject(
                "accepted_smoke_decision_id must reference a resolved "
                "user smoke acceptance"
            )
        validate_smoke_bindings(state, decision.smoke_disposition)
    for fact_index in verification_indexes:
        verification = state.verifications[fact_index]
        if verification.exit_code != 0 and not (
            verification.scope == "smoke" and smoke_id is not None
        ):
            raise reject("failed verification requires accepted degraded smoke")
    stamp = value["spec_stamp"]
    if not isinstance(stamp, dict):
        raise reject("spec_stamp must be a mapping")
    _require_keys(path, "completion.spec_stamp", stamp, SPEC_STAMP_KEYS)
    for key in ("before_sha256", "after_sha256"):
        if (
            not isinstance(stamp[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", stamp[key]) is None
        ):
            raise reject(f"spec_stamp.{key} must be a lowercase SHA-256 digest")
    if type(stamp["executable"]) is not bool:
        raise reject("spec_stamp.executable must be a boolean")
    suite = value["close_suite"]
    close_suite = None
    if suite is not None:
        if not isinstance(suite, dict):
            raise reject("close_suite must be a mapping or null")
        _require_keys(path, "completion.close_suite", suite, CLOSE_SUITE_KEYS)
        command = _str_value(path, "completion.close_suite.command", suite["command"])
        log = _str_value(path, "completion.close_suite.log", suite["log"])
        if (
            not command.strip()
            or not log
            or PurePosixPath(log).is_absolute()
            or ".." in PurePosixPath(log).parts
            or PurePosixPath(log).as_posix() != log
        ):
            raise reject(
                "close_suite needs a nonempty command and canonical "
                "workspace-relative log"
            )
        if not _strict_int(suite["exit_code"]) or suite["exit_code"] != 0:
            raise reject("close_suite must record an actual zero exit")
        close_suite = CloseSuiteFact(command, log, 0)
    elif state.authorizations[authorization_index].source != "user":
        raise reject("missing close_suite requires a user-sourced complete grant")
    return CompletionFact(
        at,
        authorization_index,
        verification_indexes,
        review_run_ids,
        smoke_id,
        SpecStampIdentity(**stamp),
        close_suite,
    )


def _invalid(
    path: Path, problem: str, hint: str, *, reason: str | None = None
) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"{path}: {problem}",
        hint=hint,
        reason=reason,
    )


def _entries(path: Path, key: str, value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise _invalid(
            path,
            f"{key} must be a list, got {type(value).__name__}",
            f"shape {key} as a YAML sequence (it may be empty)",
        )
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise _invalid(
                path,
                f"{key}[{index}] must be a mapping, got {type(entry).__name__}",
                f"shape each {key} entry per workflow-model §10.1",
            )
    return tuple(value)


def _require_keys(
    path: Path,
    label: str,
    entry: Mapping[str, Any],
    allowed: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> None:
    unknown = [key for key in entry if key not in allowed]
    if unknown:
        raise _invalid(
            path,
            f"{label} carries unknown keys: {unknown}",
            f"{label} allows exactly {list(allowed)}",
        )
    missing = [key for key in allowed if key not in optional and key not in entry]
    if missing:
        raise _invalid(
            path,
            f"{label} misses required keys: {missing}",
            f"declare every required {label} key",
        )


def _str_value(path: Path, label: str, value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid(
            path,
            f"{label} must be a string, got {type(value).__name__}: {value!r}",
            f"fix the {label} value",
        )
    return value


def _str_tuple(path: Path, label: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _invalid(
            path,
            f"{label} must be a list of strings, got {value!r}",
            f"fix the {label} value",
        )
    return tuple(value)


def _optional_str(path: Path, label: str, value: Any) -> str | None:
    if value is None:
        return None
    return _str_value(path, label, value)


def _status_value(path: Path, label: str, value: Any) -> str:
    if value not in MILESTONE_STATUSES:
        raise _invalid(
            path,
            f"{label} is {value!r}, not one of {list(MILESTONE_STATUSES)}",
            f"fix the {label} value",
        )
    return cast(str, value)


def _commands(path: Path, value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise _invalid(
            path,
            f"commands must be a mapping, got {type(value).__name__}",
            "shape commands as `{command_key: shell line}` (it may be empty)",
        )
    commands: dict[str, str] = {}
    for key, command in value.items():
        if key not in COMMAND_KEYS:
            raise _invalid(
                path,
                f"commands carries unknown key {key!r}",
                f"commands keys are drawn from {list(COMMAND_KEYS)}",
            )
        commands[key] = _str_value(path, f"commands.{key}", command)
    return commands


def _milestone(
    path: Path, entry: Mapping[str, Any], *, current: bool = False
) -> Milestone:
    # `tasks` is the one sanctioned omission (§4.1 "present once expanded").
    _require_keys(
        path,
        "milestone",
        entry,
        MILESTONE_KEYS,
        optional=("tasks", "estimated_hours") if current else ("tasks",),
    )
    milestone_id = _str_value(path, "milestone.id", entry["id"])
    hours = entry.get("estimated_hours")
    if ("estimated_hours" in entry or not current) and (
        not isinstance(hours, list)
        or len(hours) != 2
        or not all(_strict_int(bound) for bound in hours)
    ):
        raise _invalid(
            path,
            f"milestone {milestone_id}: estimated_hours must be [lo, hi] "
            f"integers, got {hours!r}",
            "fix the estimated_hours value",
        )
    complexity = _str_value(path, "milestone.complexity", entry["complexity"])
    if current and complexity not in COMPLEXITIES:
        raise _invalid(
            path,
            "current milestone complexity must be low or high",
            "reassess complexity",
        )
    verification = entry["verification"]
    if not isinstance(verification, dict):
        raise _invalid(
            path,
            f"milestone {milestone_id}: verification must be a mapping, "
            f"got {type(verification).__name__}",
            "shape verification as {command, expected}",
        )
    _require_keys(
        path,
        f"milestone {milestone_id} verification",
        verification,
        MILESTONE_VERIFICATION_KEYS,
    )
    tasks = tuple(
        _task(path, milestone_id, task_entry)
        for task_entry in _entries(path, "tasks", entry.get("tasks", []))
    )
    current_tasks = [task.id for task in tasks if task.status == "current"]
    if len(current_tasks) > 1:
        # §12.1 gives the milestone-level two-`current` case a reporting
        # code, but defines no task-level code: "the current task" has no
        # answer with two, so this is unresolvable state, not a block.
        raise _invalid(
            path,
            f"milestone {milestone_id}: more than one task is `current` "
            f"({current_tasks})",
            "exactly one task may be current within a milestone (§4.2)",
        )
    return Milestone(
        id=milestone_id,
        title=_str_value(path, "milestone.title", entry["title"]),
        satisfies=_str_tuple(path, "milestone.satisfies", entry["satisfies"]),
        depends_on=_str_tuple(path, "milestone.depends_on", entry["depends_on"]),
        owns=_str_tuple(path, "milestone.owns", entry["owns"]),
        complexity=complexity,
        estimated_hours=(hours[0], hours[1]) if hours is not None else None,
        verification={
            key: _str_value(path, f"verification.{key}", verification[key])
            for key in MILESTONE_VERIFICATION_KEYS
        },
        status=_status_value(path, "milestone.status", entry["status"]),
        tasks=tasks,
    )


def _task(path: Path, milestone_id: str, entry: Mapping[str, Any]) -> Task:
    _require_keys(path, f"milestone {milestone_id} task", entry, TASK_KEYS)
    return Task(
        id=_str_value(path, "task.id", entry["id"]),
        text=_str_value(path, "task.text", entry["text"]),
        status=_status_value(path, "task.status", entry["status"]),
    )


def parse_gate_fact(
    path: Path,
    entry: Mapping[str, Any],
) -> GateFact:
    _require_keys(path, "gate fact", entry, GATE_KEYS)
    gate = _str_value(path, "gate.gate", entry["gate"])
    fact = GateFact(
        gate=gate,
        scope=_str_value(path, "gate.scope", entry["scope"]),
        runs=tuple(
            _gate_run(path, gate, run_entry, current=True)
            for run_entry in _entries(path, "runs", entry["runs"])
        ),
    )
    return fact


def _gate_run(
    path: Path, gate: str, entry: Mapping[str, Any], *, current: bool = False
) -> GateRun:
    required = (*GATE_RUN_KEYS, *(GATE_RUN_ASSIGNMENT_KEYS if current else ()))
    _require_keys(
        path,
        f"gate {gate} run",
        entry,
        (*required, *GATE_RUN_OPTIONAL_KEYS),
        optional=GATE_RUN_OPTIONAL_KEYS,
    )
    verdict = entry["verdict"]
    if not isinstance(verdict, dict):
        raise _invalid(
            path,
            f"gate {gate} run: verdict must be a mapping, got {type(verdict).__name__}",
            "record the verdict as reported (§6.2)",
        )
    completed = verdict.get("status") != "error"
    if completed:
        _require_keys(
            path,
            f"completed gate {gate} run",
            entry,
            (*required, *GATE_RUN_OPTIONAL_KEYS),
            optional=tuple(
                key
                for key in GATE_RUN_OPTIONAL_KEYS
                if key not in GATE_RUN_COMPLETED_KEYS
            ),
        )
    findings = entry["findings"]
    if not _valid_d33_findings(findings):
        raise _invalid(
            path,
            f"gate {gate} run: findings must use the canonical shape "
            f"{list(FINDINGS_KEYS)} — by_severity/by_classification mappings of "
            f"integer counts, integer total + contradictions, got {findings!r}",
            "fix the findings block",
        )
    execution_config = _execution_config(path, entry.get("execution_config"))
    cli = _str_value(path, "run.cli", entry["cli"])
    if execution_config is not None and execution_config["cli"] != cli:
        raise _invalid(
            path,
            "run.execution_config.cli must equal the top-level cli",
            "record both CLI fields from the same prepared invocation",
        )
    overridden_axes = (
        _str_tuple(path, "run.overridden_axes", entry["overridden_axes"])
        if "overridden_axes" in entry
        else None
    )
    if overridden_axes is not None and (
        tuple(sorted(set(overridden_axes))) != overridden_axes
        or not set(overridden_axes) <= VALID_OVERRIDE_AXES
    ):
        raise _invalid(
            path,
            "run.overridden_axes must be a sorted unique list of cli, model, "
            "and reasoning_effort",
            "record the exact sorted public override axes",
        )
    artifact_sha256 = _optional_sha256(path, entry, "artifact_sha256")
    artifact = _str_value(path, "run.artifact", entry["artifact"])
    prompt_version = (
        _str_value(path, "run.prompt_version", entry["prompt_version"])
        if "prompt_version" in entry
        else None
    )
    if completed:
        if not artifact.endswith(".review.json"):
            raise _invalid(
                path,
                "completed run.artifact must name a canonical .review.json result",
                "preserve old evidence and start a fresh current workspace",
            )
        if execution_config is None or not all(execution_config.values()):
            raise _invalid(
                path,
                "completed run.execution_config must contain every prepared axis",
                "record the complete prepared execution configuration",
            )
        if not prompt_version or not prompt_version.strip():
            raise _invalid(
                path,
                "completed run.prompt_version must name the captured instructions",
                "record the authored prompt identity from the prepared invocation",
            )
    if current and (
        type(entry["round_number"]) is not int
        or entry["round_number"] < 1
        or entry["reviewer_slot"] not in {"primary", "secondary"}
    ):
        raise _invalid(
            path, "invalid round or reviewer slot", "preserve invocation binding"
        )
    return GateRun(
        assignment_id=_str_value(path, "run.assignment_id", entry["assignment_id"])
        if current
        else None,
        round_number=entry["round_number"] if current else None,
        reviewer_slot=entry["reviewer_slot"] if current else None,
        run_id=_run_id(path, "run.run_id", entry["run_id"]),
        report_findings=tuple(
            _report_finding(path, finding)
            for finding in _entries(
                path, "run.report_findings", entry["report_findings"]
            )
        ),
        at=_str_value(path, "run.at", entry["at"]),
        cli=cli,
        artifact=artifact,
        input_hash=_str_value(path, "run.input_hash", entry["input_hash"]),
        verdict=dict(verdict),
        findings=dict(findings),
        review_basis_hash=_optional_sha256(path, entry, "review_basis_hash"),
        execution_config=execution_config,
        overridden_axes=overridden_axes,
        artifact_sha256=artifact_sha256,
        prompt_version=prompt_version,
        effective_prompt_sha256=_optional_sha256(
            path, entry, "effective_prompt_sha256"
        ),
        review_policy_id=(
            _str_value(path, "run.review_policy_id", entry["review_policy_id"])
            if "review_policy_id" in entry
            else None
        ),
        failure_reason=(
            _str_value(path, "run.failure_reason", entry["failure_reason"])
            if "failure_reason" in entry
            else None
        ),
    )


def _execution_config(path: Path, value: Any) -> Mapping[str, str] | None:
    if value is None:
        return None
    expected = {"cli", "model", "reasoning_effort", "sandbox"}
    if not isinstance(value, dict) or set(value) != expected:
        raise _invalid(
            path,
            f"run.execution_config must contain exactly {sorted(expected)}",
            "record the complete prepared execution configuration",
        )
    return {
        key: _str_value(path, f"run.execution_config.{key}", value[key])
        for key in ("cli", "model", "reasoning_effort", "sandbox")
    }


def _optional_sha256(path: Path, entry: Mapping[str, Any], key: str) -> str | None:
    if key not in entry:
        return None
    value = _str_value(path, f"run.{key}", entry[key])
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise _invalid(
            path,
            f"run.{key} must be 64 lowercase hexadecimal characters",
            "record the SHA-256 digest of the exact captured bytes",
        )
    return value


def _run_id(path: Path, label: str, value: Any) -> str:
    identifier = _str_value(path, label, value)
    try:
        UUID(identifier)
    except ValueError as error:
        raise _invalid(
            path,
            f"{label} must be a UUID, got {identifier!r}",
            "record the opaque ID allocated for this accepted run",
        ) from error
    return identifier


def _report_finding(path: Path, entry: Mapping[str, Any]) -> ReportFinding:
    _require_keys(path, "REPORT finding", entry, REPORT_FINDING_KEYS)
    finding_id = _str_value(path, "report.finding_id", entry["finding_id"])
    title = _str_value(path, "report.title", entry["title"])
    severity = _str_value(path, "report.severity", entry["severity"])
    if (
        not finding_id.strip()
        or not title.strip()
        or severity
        not in {
            "critical",
            "important",
            "minor",
        }
    ):
        raise _invalid(
            path,
            "REPORT finding requires a nonempty finding_id/title and known severity",
            "record canonical REPORT metadata from the validated artifact",
        )
    return ReportFinding(finding_id, title, severity)


def _resolution(path: Path, value: Any) -> decision_types.Resolution | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid(
            path,
            "decision.resolution must be a mapping or null",
            "record a typed resolution",
        )
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in RESOLUTION_PAYLOAD_KEYS:
        raise _invalid(
            path,
            f"unknown resolution.kind {kind!r}",
            "record a known resolution variant",
        )
    _require_keys(path, f"resolution {kind}", value, RESOLUTION_PAYLOAD_KEYS[kind])

    def count(key: str) -> int:
        result = value[key]
        if type(result) is not int or result < 0:
            raise _invalid(
                path,
                f"resolution.{key} must be a nonnegative integer",
                "record the actual native count",
            )
        return result

    match kind:
        case "disposition":
            return decision_types.Disposition()
        case "continue-review":
            return decision_types.ContinueReview(
                _str_value(path, "resolution.assignment_id", value["assignment_id"]),
                count("stopped_round"),
            )
        case "continue-stage":
            return decision_types.ContinueStage(
                count("review_run_count_before"), count("gate_run_count_before")
            )
        case "continue-session":
            return decision_types.ContinueSession(count("session_count_before"))
        case "accept-degraded-smoke":
            return decision_types.AcceptDegradedSmoke()
        case "policy":
            fields = {
                key: _str_value(path, f"resolution.{key}", value[key])
                for key in RESOLUTION_PAYLOAD_KEYS[kind]
                if key != "kind"
            }
            return decision_types.PolicyResolution(**fields)
    raise AssertionError(f"unhandled resolution kind: {kind}")


def _verification(path: Path, entry: Mapping[str, Any]) -> VerificationFact:
    _require_keys(path, "verification fact", entry, VERIFICATION_KEYS)
    exit_code = entry["exit_code"]
    if not _strict_int(exit_code):
        raise _invalid(
            path,
            f"verification exit_code must be an integer, got {exit_code!r}",
            "fix the exit_code value",
        )
    return VerificationFact(
        scope=_str_value(path, "verification.scope", entry["scope"]),
        command=_str_value(path, "verification.command", entry["command"]),
        exit_code=exit_code,
        at=_str_value(path, "verification.at", entry["at"]),
        log=_str_value(path, "verification.log", entry["log"]),
        evidence=_verification_evidence(path, entry["evidence"]),
    )


def _verification_evidence(path: Path, value: Any) -> VerificationEvidence:
    if not isinstance(value, dict):
        raise _invalid(
            path,
            f"verification.evidence must bind current source, got {value!r}",
            "start a fresh workspace and run current verification",
        )
    _require_keys(
        path,
        "verification evidence",
        value,
        VERIFICATION_EVIDENCE_KEYS,
    )
    schema = _str_value(path, "verification.evidence.schema", value["schema"])
    if schema != "heddle.verification-evidence/v1":
        raise _invalid(
            path,
            f"verification.evidence.schema is {schema!r}",
            "start a fresh workspace with current evidence references",
        )
    try:
        before = decode_evidence_reference(value["before"])
        after = decode_evidence_reference(value["after"])
    except KernelError as error:
        raise _invalid(
            path,
            f"verification evidence reference is invalid: {error.message}",
            error.hint,
        ) from error
    return VerificationEvidence(schema=schema, before=before, after=after)


def _decision(path: Path, entry: Mapping[str, Any]) -> DecisionFact:
    _require_keys(
        path,
        "decision fact",
        entry,
        DECISION_KEYS,
        optional=DECISION_OPTIONAL_KEYS,
    )
    escalation_class = entry.get("class")
    if escalation_class is not None and not _strict_int(escalation_class):
        raise _invalid(
            path,
            f"decision.class must be an integer, got {escalation_class!r}",
            "fix the decision class value",
        )
    question = _optional_str(path, "decision.question", entry.get("question"))
    options_value = entry.get("options", [])
    options = _str_tuple(path, "decision.options", options_value)
    recommendation = _optional_str(
        path, "decision.recommendation", entry.get("recommendation")
    )
    kind = entry["kind"]
    if not isinstance(kind, str) or kind not in DECISION_RESOLUTION_KINDS:
        raise _invalid(
            path, f"decision.kind is {kind!r}", "record a known decision kind"
        )
    resolution = _resolution(path, entry["resolution"])
    resolution_source = _optional_str(
        path, "decision.resolution_source", entry.get("resolution_source")
    )
    resolved_at = _optional_str(path, "decision.resolved_at", entry.get("resolved_at"))
    for key in (
        "origin_run_id",
        "origin_finding_id",
        "target_stage",
        "target_blocker",
        "supersedes",
    ):
        if key in entry and (not isinstance(entry[key], str) or not entry[key].strip()):
            raise _invalid(
                path,
                f"decision.{key} must be nonempty when present",
                "omit inapplicable targets; record the exact applicable reference",
            )
    return DecisionFact(
        id=_str_value(path, "decision.id", entry["id"]),
        kind=cast(decision_types.DecisionKind, kind),
        source=_str_value(path, "decision.source", entry["source"]),
        title=_str_value(path, "decision.title", entry["title"]),
        status=_str_value(path, "decision.status", entry["status"]),
        resolution=resolution,
        rationale=_optional_str(path, "decision.rationale", entry["rationale"]),
        routes_to=_str_tuple(path, "decision.routes_to", entry["routes_to"]),
        created_at=_str_value(path, "decision.created_at", entry["created_at"]),
        resolved_at=resolved_at,
        escalation_class=escalation_class,
        question=question,
        options=options,
        recommendation=recommendation,
        resolution_source=resolution_source,
        smoke_disposition=(
            validate_smoke_disposition(entry["smoke_disposition"], bound=True)
            if "smoke_disposition" in entry
            else None
        ),
        origin_run_id=_optional_str(
            path, "decision.origin_run_id", entry.get("origin_run_id")
        ),
        origin_finding_id=_optional_str(
            path, "decision.origin_finding_id", entry.get("origin_finding_id")
        ),
        target_stage=_optional_str(
            path, "decision.target_stage", entry.get("target_stage")
        ),
        target_blocker=_optional_str(
            path, "decision.target_blocker", entry.get("target_blocker")
        ),
        supersedes=_optional_str(path, "decision.supersedes", entry.get("supersedes")),
    )


def _session(path: Path, entry: Mapping[str, Any]) -> SessionFact:
    _require_keys(
        path,
        "session fact",
        entry,
        SESSION_KEYS,
        optional=SESSION_OPTIONAL_KEYS,
    )
    for field, expected_type in SESSION_PROGRESS_FIELD_TYPES.items():
        value = entry.get(field)
        if value is None:
            continue
        if expected_type is int:
            valid = _strict_int(value)
            expected = "integer"
        else:
            valid = isinstance(value, expected_type)
            expected = expected_type.__name__
        if not valid:
            raise _invalid(
                path,
                f"session.{field} must be a {expected}, got {value!r}",
                f"fix the session.{field} value",
            )
    return SessionFact(
        started_at=_str_value(path, "session.started_at", entry["started_at"]),
        ended_at=_str_value(path, "session.ended_at", entry["ended_at"]),
        completed=_str_tuple(path, "session.completed", entry["completed"]),
        started=_str_tuple(path, "session.started", entry["started"]),
        key_context=_str_value(path, "session.key_context", entry["key_context"]),
        next_steps=_str_value(path, "session.next_steps", entry["next_steps"]),
        blockers=_str_tuple(path, "session.blockers", entry["blockers"]),
        stage=_optional_str(path, "session.stage", entry.get("stage")),
        revision_before=entry.get("revision_before"),
        revision_after=entry.get("revision_after"),
        advanced=entry.get("advanced"),
    )


def _authorization(path: Path, entry: Mapping[str, Any]) -> AuthorizationFact:
    _require_keys(
        path,
        "authorization fact",
        entry,
        AUTHORIZATION_KEYS,
        optional=AUTHORIZATION_OPTIONAL_KEYS,
    )
    source = _optional_str(path, "authorization.source", entry.get("source"))
    at = _optional_str(path, "authorization.at", entry.get("at"))
    return AuthorizationFact(
        through=_str_value(path, "authorization.through", entry["through"]),
        source=source,
        at=at,
    )
