"""Frozen application operations and structured next actions; no workflow effects."""

from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

from heddle.contracts.decisions import (
    DecisionInput,
    PolicyResolutionInput,
    ResolutionKind,
)

VERIFICATION_SCOPE_GUIDANCE = (
    "supported native scopes: m<N>, smoke, acceptance, or live; configure "
    "progressive feedback with "
    "`heddle commands set test_command --command <shell-line>`"
)


@dataclass(frozen=True)
class Status:
    feature: str | None = None
    all_features: bool = False


@dataclass(frozen=True)
class Orient:
    feature: str | None = None


@dataclass(frozen=True)
class Kickoff:
    feature: str | None = None


@dataclass(frozen=True)
class Doctor:
    feature: str | None = None


@dataclass(frozen=True)
class RunGate:
    gate: str
    feature: str | None = None
    cli: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class RunGates:
    feature: str | None = None


@dataclass(frozen=True)
class ShowPrompt:
    gate: str
    feature: str | None = None
    cli: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class RecordReviewDisposition:
    feature: str | None = None
    payload: dict[str, Any] | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class ReviewRoundOpen:
    feature: str | None = None
    payload: dict[str, Any] | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class InterpretReview:
    feature: str | None = None
    payload: dict[str, Any] | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class ReviewAllowance:
    role: str
    limit: int
    approval: str
    feature: str
    expect_revision: int
    dry_run: bool = False


@dataclass(frozen=True)
class Search:
    query: str
    limit: int = 5
    titles_only: bool = False


@dataclass(frozen=True)
class FeatureStart:
    slug: str
    area: str | None = None
    flow: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class FeaturePrepare:
    slug: str
    area: str
    payload: dict[str, Any]
    dry_run: bool = False


@dataclass(frozen=True)
class FeaturePolicy:
    slug: str
    payload: dict[str, Any]
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class FeatureReassess:
    slug: str
    payload: dict[str, Any]
    expect_revision: int | None = None


@dataclass(frozen=True)
class FeatureSwitch:
    feature: str


@dataclass(frozen=True)
class FeatureComplete:
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class FeatureInputsSet:
    paths: tuple[str, ...]
    feature: str | None = None
    expect_revision: int | None = None


@dataclass(frozen=True)
class AttributeSources:
    payload: dict[str, Any]
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class CommandsSet:
    key: str
    command: str
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class CommandsUnset:
    key: str
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class VerificationInput:
    command: str
    expected: str


@dataclass(frozen=True)
class MilestoneInput:
    title: str
    complexity: str
    verification: VerificationInput
    estimated_hours: tuple[int, int] | None = None
    satisfies: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    owns: tuple[str, ...] = ()


@dataclass(frozen=True)
class MilestonePatch:
    title: str | None = None
    complexity: str | None = None
    estimated_hours: tuple[int, int] | None = None
    verification: VerificationInput | None = None
    satisfies: tuple[str, ...] | None = None
    depends_on: tuple[str, ...] | None = None
    owns: tuple[str, ...] | None = None
    owns_append: tuple[str, ...] | None = None


@dataclass(frozen=True)
class MilestoneAdd:
    milestone: MilestoneInput
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class MilestoneEdit:
    milestone_id: str
    patch: MilestonePatch
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class MilestoneAdvance:
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class TaskAdd:
    text: str
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class TaskCurrent:
    task_id: str
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class TaskDone:
    milestone_id: str
    task_id: str
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class Verify:
    scope: str | None = None
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class SessionInput:
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
class RecordSession:
    session: SessionInput
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class PhaseExit:
    through: str | None = None
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class FlowSet:
    flow: str
    feature: str | None = None
    expect_revision: int | None = None


@dataclass(frozen=True)
class DecisionsList:
    feature: str | None = None


@dataclass(frozen=True)
class DecisionsAdd:
    decisions: tuple[DecisionInput, ...]
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class ResolveDecision:
    decision_id: str
    kind: ResolutionKind
    rationale: str
    routes_to: str
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class RecordPolicy:
    resolutions: tuple[PolicyResolutionInput, ...]
    feature: str | None = None
    expect_revision: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class Init:
    dry_run: bool = False
    adopt_existing: bool = False


@dataclass(frozen=True)
class Sync:
    feature: str | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class Validate:
    feature: str | None = None


@dataclass(frozen=True)
class Drive:
    feature: str | None = None
    until: str | None = None


type Operation = (
    Status
    | Orient
    | Kickoff
    | Doctor
    | RunGate
    | RunGates
    | ShowPrompt
    | RecordReviewDisposition
    | ReviewRoundOpen
    | InterpretReview
    | ReviewAllowance
    | Search
    | FeatureStart
    | FeaturePrepare
    | FeaturePolicy
    | FeatureReassess
    | FeatureSwitch
    | FeatureComplete
    | FeatureInputsSet
    | AttributeSources
    | CommandsSet
    | CommandsUnset
    | MilestoneAdd
    | MilestoneEdit
    | MilestoneAdvance
    | TaskAdd
    | TaskCurrent
    | TaskDone
    | Verify
    | RecordSession
    | PhaseExit
    | FlowSet
    | DecisionsList
    | DecisionsAdd
    | ResolveDecision
    | RecordPolicy
    | Init
    | Sync
    | Validate
    | Drive
)

_OPERATION_NAMES: dict[type, str] = {
    Status: "status",
    Orient: "orient",
    Kickoff: "kickoff",
    Doctor: "doctor",
    RunGate: "run-gate",
    RunGates: "run-gates",
    ShowPrompt: "show-prompt",
    RecordReviewDisposition: "review disposition",
    ReviewRoundOpen: "review round-open",
    InterpretReview: "review interpret",
    ReviewAllowance: "review allowance",
    Search: "search",
    FeatureStart: "feature start",
    FeaturePrepare: "feature prepare",
    FeaturePolicy: "feature policy",
    FeatureReassess: "feature reassess",
    FeatureSwitch: "feature switch",
    FeatureComplete: "feature complete",
    FeatureInputsSet: "feature inputs set",
    AttributeSources: "feature sources attribute",
    CommandsSet: "commands set",
    CommandsUnset: "commands unset",
    MilestoneAdd: "milestone add",
    MilestoneEdit: "milestone edit",
    MilestoneAdvance: "milestone advance",
    TaskAdd: "task add",
    TaskCurrent: "task current",
    TaskDone: "task done",
    Verify: "verify",
    RecordSession: "session log",
    PhaseExit: "phase-exit",
    FlowSet: "flow set",
    DecisionsList: "decisions list",
    DecisionsAdd: "decisions add",
    ResolveDecision: "decisions resolve",
    RecordPolicy: "decisions record-policy",
    Init: "init",
    Sync: "sync",
    Validate: "validate",
    Drive: "drive",
}


def operation_type_name(operation_type: type[Operation]) -> str:
    """Canonical command spelling for a typed operation, without an instance."""
    return _OPERATION_NAMES[operation_type]


def operation_name(operation: Operation) -> str:
    return operation_type_name(type(operation))


def operation_payload(operation: Operation) -> dict[str, object]:
    return {"name": operation_name(operation), "arguments": decoded_payload(operation)}


def decoded_payload(value: Any) -> dict[str, Any]:
    """Project typed decoded inputs to the declared collection wire shapes."""

    def wire(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: wire(entry) for key, entry in item.items()}
        if isinstance(item, (tuple, list)):
            return [wire(entry) for entry in item]
        return item

    return cast(dict[str, Any], wire(asdict(value)))


def operation_command(operation: Operation) -> str:
    """Render external argv once; display text is never an execution input."""
    arguments: list[str] = ["heddle", *operation_name(operation).split()]
    match operation:
        case Init():
            if operation.adopt_existing:
                arguments.append("--adopt-existing")
        case RecordReviewDisposition() | ReviewRoundOpen() | InterpretReview():
            if operation.payload is not None:
                arguments.extend(("--input-json", "-"))
        case ReviewAllowance():
            arguments.extend(
                (
                    "--role",
                    operation.role,
                    "--limit",
                    str(operation.limit),
                    "--approval",
                    operation.approval,
                )
            )
        case RunGate() | ShowPrompt():
            arguments.append(operation.gate)
            for flag, value in (
                ("--cli", operation.cli),
                ("--model", operation.model),
                ("--reasoning-effort", operation.reasoning_effort),
            ):
                if value is not None:
                    arguments.extend((flag, value))
        case CommandsSet():
            arguments.extend((operation.key, "--command", operation.command))
        case CommandsUnset():
            arguments.append(operation.key)
        case FeatureStart():
            arguments.append(operation.slug)
            for flag, value in (
                ("--area", operation.area),
                ("--flow", operation.flow),
            ):
                if value is not None:
                    arguments.extend((flag, value))
        case FeaturePrepare() | FeaturePolicy() | FeatureReassess():
            arguments.extend((operation.slug, "--from-file", "-"))
            if isinstance(operation, FeaturePrepare):
                arguments.extend(("--area", operation.area))
        case FeatureSwitch():
            arguments.append(operation.feature)
        case FeatureComplete():
            pass
        case MilestoneEdit():
            arguments.extend((operation.milestone_id, "--from-file", "-"))
        case (
            MilestoneAdd()
            | FeatureInputsSet()
            | AttributeSources()
            | RecordSession()
            | RecordPolicy()
        ):
            arguments.extend(("--from-file", "-"))
        case DecisionsAdd():
            arguments.extend(("--input-json", "-"))
        case TaskAdd():
            arguments.append(operation.text)
        case TaskCurrent() | TaskDone():
            arguments.append(operation.task_id)
        case Verify():
            if operation.scope is not None:
                arguments.extend(("--scope", operation.scope))
        case PhaseExit():
            if operation.through is not None:
                arguments.extend(("--through", operation.through))
        case FlowSet():
            arguments.append(operation.flow)
        case ResolveDecision():
            arguments.extend(
                (
                    operation.decision_id,
                    "--kind",
                    operation.kind,
                    "--resolution",
                    operation.rationale,
                    "--routes-to",
                    operation.routes_to,
                )
            )
        case Search():
            arguments.extend((operation.query, "--limit", str(operation.limit)))
            if operation.titles_only:
                arguments.append("--titles-only")
        case Status():
            if operation.all_features:
                arguments.append("--all")
        case Drive():
            if operation.until is not None:
                arguments.extend(("--until", operation.until))
    expect_revision = getattr(operation, "expect_revision", None)
    if expect_revision is not None:
        arguments.extend(("--expect-revision", str(expect_revision)))
    feature = getattr(operation, "feature", None)
    if feature is not None and not isinstance(operation, FeatureSwitch):
        arguments.extend(("--feature", feature))
    if getattr(operation, "dry_run", False):
        arguments.append("--dry-run")
    return shlex.join(arguments)


@dataclass(frozen=True)
class CommandAction:
    operation: Operation


@dataclass(frozen=True)
class SessionAction:
    feature: str
    work: Literal["stage-work", "task-expansion"]


@dataclass(frozen=True)
class AuthoringAction:
    feature: str
    work: str
    references: tuple[str, ...]
    input_schema: str
    expected_revision: int


@dataclass(frozen=True)
class DecisionAction:
    feature: str
    decision_id: str
    choices: tuple[str, ...]
    routes_to: tuple[str, ...]


@dataclass(frozen=True)
class ManualAction:
    instruction: str


type Action = (
    CommandAction | SessionAction | AuthoringAction | DecisionAction | ManualAction
)


def action_payload(action: Action) -> dict[str, object]:
    match action:
        case CommandAction(operation):
            return {"kind": "command", "operation": operation_payload(operation)}
        case SessionAction(feature, work):
            return {"kind": "session", "feature": feature, "work": work}
        case AuthoringAction(feature, work, references, input_schema, revision):
            return {
                "kind": "authoring",
                "feature": feature,
                "work": work,
                "references": list(references),
                "input_schema": input_schema,
                "expected_revision": revision,
            }
        case DecisionAction(feature, decision_id, choices, routes_to):
            return {
                "kind": "decision",
                "feature": feature,
                "decision_id": decision_id,
                "choices": list(choices),
                "routes_to": list(routes_to),
            }
        case ManualAction(instruction):
            return {"kind": "manual", "instruction": instruction}
    raise TypeError(f"unknown action {type(action).__name__}")


def action_command(action: Action) -> str:
    match action:
        case CommandAction(operation):
            return operation_command(operation)
        case SessionAction(feature, _):
            return operation_command(Kickoff(feature))
        case AuthoringAction(feature, work, _, _, revision):
            if work == "feature-policy":
                authored_operation: Operation = FeaturePolicy(
                    slug=feature,
                    payload={},
                    expect_revision=revision,
                )
            elif work == "review-disposition":
                authored_operation = RecordReviewDisposition(
                    feature=feature,
                    payload={},
                    expect_revision=revision,
                )
            elif work == "review-interpretation":
                authored_operation = InterpretReview(
                    feature=feature,
                    payload={},
                    expect_revision=revision,
                )
            else:
                raise ValueError(f"unknown authoring work {work!r}")
            return operation_command(authored_operation)
        case DecisionAction(feature, "pending-intake-selection", _, _):
            return "heddle orient --feature <choice>"
        case DecisionAction(feature, _, _, _):
            return operation_command(DecisionsList(feature=feature))
        case ManualAction(instruction):
            return instruction
    raise TypeError(f"unknown action {type(action).__name__}")


def action_from_payload(payload: dict[str, object]) -> Action:
    """Decode the public non-command action variants without inferring inputs."""
    kind = payload.get("kind")
    if kind == "authoring":
        expected = {
            "kind",
            "feature",
            "work",
            "references",
            "input_schema",
            "expected_revision",
        }
        if set(payload) != expected:
            raise ValueError("authoring action has an invalid field set")
        feature = payload["feature"]
        work = payload["work"]
        references = payload["references"]
        input_schema = payload["input_schema"]
        revision = payload["expected_revision"]
        if (
            not isinstance(feature, str)
            or not isinstance(work, str)
            or not isinstance(references, list)
            or not all(isinstance(item, str) for item in references)
            or not isinstance(input_schema, str)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
        ):
            raise ValueError("authoring action has invalid field values")
        return AuthoringAction(feature, work, tuple(references), input_schema, revision)
    if kind == "decision":
        expected = {"kind", "feature", "decision_id", "choices", "routes_to"}
        if set(payload) != expected:
            raise ValueError("decision action has an invalid field set")
        feature = payload["feature"]
        decision_id = payload["decision_id"]
        choices = payload["choices"]
        routes_to = payload["routes_to"]
        if (
            not isinstance(feature, str)
            or not isinstance(decision_id, str)
            or not isinstance(choices, list)
            or not all(isinstance(item, str) for item in choices)
            or not isinstance(routes_to, list)
            or not all(isinstance(item, str) for item in routes_to)
        ):
            raise ValueError("decision action has invalid field values")
        return DecisionAction(feature, decision_id, tuple(choices), tuple(routes_to))
    raise ValueError(f"unsupported action kind {kind!r}")
