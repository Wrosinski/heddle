from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum, StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

if TYPE_CHECKING:
    from heddle.gate.results import ReviewOutputContract, ReviewResult
    from heddle.gate.retained import CapturedReview
    from heddle.kernel.state import SourceBaseline

from heddle.contracts.review_assignments import (
    ArtifactRef,
    AssignmentRound,
    ReviewAssignment,
)
from heddle.io.process import MonitorResult
from heddle.kernel.decision_guidance import DecisionGuidance
from heddle.kernel.source_manifest import ObservedPath


class FailureReason(StrEnum):
    MISSING_PLAN_PATH = "missing-plan-path"
    MISSING_MILESTONE_SECTION = "missing-milestone-section"
    MISSING_SATISFIES_AC_IDS = "missing-satisfies-ac-ids"
    EMPTY_DIFF = "empty-authoritative-diff"
    CHANGED_FILES_EMPTY = "changed-files-empty"
    MISSING_OWNED_PATHS = "missing-owned-paths"
    ZERO_OVERLAP = "zero-diff-owned-path-overlap"
    UNTRACKED_OWNED_FILES = "untracked-owned-files-hidden-from-diff"
    OFF_SCOPE_CHANGED_FILES = "off-scope-changed-files"
    TIMEOUT = "timeout"
    INACTIVITY_TIMEOUT = "inactivity-timeout"
    OVERLOADED = "claude-overloaded"
    BUDGET_EXHAUSTED = "budget-exhausted"
    MAX_TURNS = "max-turns"
    EXECUTION_FAILURE = "execution-failure"
    UNSUPPORTED_MODEL = "unsupported-model"
    TRANSPORT_ERROR = "transport-error"
    MISSING_FINAL_MESSAGE = "missing-final-message"
    MISSING_MESSAGE_EVENT = "missing-message-event"
    MISSING_TOOL_EVENTS = "missing-tool-events"
    MISSING_TURN_COMPLETED = "missing-turn-completed"
    INACTIVITY_NO_FINAL = "inactivity-no-final-message"
    OUTPUT_LAST_MSG_EMPTY = "output-last-message-empty"
    NONZERO_EXIT = "exit"
    EMPTY_OUTPUT = "empty-output"
    INVALID_OUTPUT_STRUCTURE = "invalid-output-structure"
    TRUNCATED_OUTPUT = "truncated-output"
    CAPTURE_STORAGE = "capture-storage-failure"
    REVIEWED_SOURCE_CHANGED = "reviewed-source-changed"
    REVIEWED_SOURCE_UNAVAILABLE = "reviewed-source-unavailable"


@dataclass(frozen=True)
class ResolvedResource:
    logical_name: str
    path: Path
    source: Literal["override", "generated", "packaged"]
    effective_text: str
    authoring_text: str | None = None


@dataclass(frozen=True)
class PromptTransport:
    system: str | None
    stdin: str


@dataclass(frozen=True)
class ResolvedGatePrompt:
    gate: str
    template: ResolvedResource
    partials: tuple[ResolvedResource, ...]
    decision_policy: DecisionGuidance
    logical_instructions: str
    effective_instructions: str
    prompt_version: str
    effective_prompt_sha256: str
    output_constraint: str
    transport: PromptTransport


@dataclass(frozen=True)
class GateExecutionConfig:
    cli: str
    model: str
    reasoning_effort: str
    sandbox: str


@dataclass(frozen=True)
class GateLanePolicy:
    cli: Literal["claude", "codex"]
    model: str | None = None
    reasoning_effort: str | None = None
    sandbox: str | None = None


@dataclass(frozen=True)
class GateInvocationOverrides:
    cli: Literal["claude", "codex"] | None = None
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ResolvedGateInvocation:
    exec_config: GateExecutionConfig
    explicit_axes: frozenset[str]


@dataclass(frozen=True)
class GateReportFinding:
    finding_id: str
    title: str
    severity: str
    recommendation: str | None = None
    route: str | None = None


@dataclass(frozen=True)
class GateEngineFailure:
    """Execution failed; no capture or canonical review can coexist with it."""

    gate_exit: int
    reason: str
    rerun_recommended: bool | None
    structure_warnings: tuple[str, ...]
    artifacts: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        if self.gate_exit == 0:
            raise ValueError("engine failure requires a nonzero gate exit")

    status: ClassVar[None] = None
    report_findings: ClassVar[tuple[GateReportFinding, ...]] = ()

    @property
    def findings(self) -> Mapping[str, Any]:
        return {
            "by_severity": {"critical": 0, "important": 0, "minor": 0},
            "by_classification": {
                "implement": 0,
                "report": 0,
                "ignore": 0,
                "unknown": 0,
            },
            "total": 0,
            "contradictions": 0,
        }

    @property
    def severity_classification(self) -> Mapping[str, int]:
        return {
            f"{severity}/{classification}": 0
            for severity in ("critical", "important", "minor")
            for classification in ("implement", "report", "ignore", "unknown")
        }

    @property
    def verdict(self) -> Mapping[str, Any]:
        return {"status": "error", "rerun_recommended": self.rerun_recommended}

    @property
    def artifact_relpath(self) -> str:
        return self.artifacts[0].path if self.artifacts else ""

    @property
    def artifact_sha256(self) -> str | None:
        return self.artifacts[0].sha256 if self.artifacts else None

    @property
    def review_result(self) -> None:
        return None

    @property
    def retained_review(self) -> None:
        return None


@dataclass(frozen=True)
class GateUsableReviewCapture:
    """Complete provider output retained for lead interpretation."""

    capture: CapturedReview
    artifact: ArtifactRef
    structure_warnings: tuple[str, ...]
    artifacts: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        if self.artifact.role != "capture":
            raise ValueError("usable review capture requires a capture artifact")
        if self.artifact not in self.artifacts:
            object.__setattr__(self, "artifacts", (self.artifact, *self.artifacts))

    gate_exit = 0
    reason = None
    status = "retained"
    verdict: ClassVar[Mapping[str, Any]] = MappingProxyType(
        {"status": "retained", "accepted": False}
    )
    findings: ClassVar[Mapping[str, Any]] = MappingProxyType({})
    severity_classification: ClassVar[Mapping[str, int]] = MappingProxyType({})
    report_findings: ClassVar[tuple[GateReportFinding, ...]] = ()

    @property
    def artifact_relpath(self) -> str:
        return self.artifact.path

    @property
    def artifact_sha256(self) -> str:
        return self.artifact.sha256

    @property
    def review_result(self) -> None:
        return None

    @property
    def retained_review(self) -> CapturedReview:
        return self.capture


@dataclass(frozen=True)
class GateCanonicalReview:
    """Validated canonical review; projections always derive from its content."""

    result: ReviewResult
    artifact: ArtifactRef
    structure_warnings: tuple[str, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        if self.artifact.role != "canonical":
            raise ValueError("canonical review requires a canonical artifact")
        if self.artifact not in self.artifacts:
            object.__setattr__(self, "artifacts", (self.artifact, *self.artifacts))

    def _projection(self) -> dict[str, Any]:
        from heddle.gate.entry import machine_projection_from_result

        return machine_projection_from_result(self.result)

    gate_exit = 0
    reason = None

    @property
    def status(self) -> str | None:
        return cast(str | None, self._projection()["status"])

    @property
    def verdict(self) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], self._projection()["verdict"])

    @property
    def findings(self) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], self._projection()["findings"])

    @property
    def severity_classification(self) -> Mapping[str, int]:
        return cast(Mapping[str, int], self._projection()["severity_classification"])

    @property
    def report_findings(self) -> tuple[GateReportFinding, ...]:
        return tuple(
            GateReportFinding(
                item["id"],
                item["title"],
                item["severity"],
                item.get("recommendation"),
                item.get("route"),
            )
            for item in self._projection()["report_findings"]
        )

    @property
    def artifact_relpath(self) -> str:
        return self.artifact.path

    @property
    def artifact_sha256(self) -> str:
        return self.artifact.sha256

    @property
    def review_result(self) -> ReviewResult:
        return self.result

    @property
    def retained_review(self) -> None:
        return None


type GateOutcome = GateEngineFailure | GateUsableReviewCapture | GateCanonicalReview


@dataclass(frozen=True)
class ReviewedInput:
    name: str
    kind: Literal["fact", "content", "producer"]
    identity: str
    content: str | bytes


@dataclass(frozen=True)
class PriorReviewArtifact:
    """Captured output and exact event identity for replay preparation."""

    path: str
    content: str
    run_id: str
    input_hash: str
    result: ReviewResult


@dataclass(frozen=True)
class ReviewDecision:
    id: str
    status: str
    origin_run_id: str
    origin_finding_id: str


@dataclass(frozen=True)
class ActiveRule:
    id: str
    text: str


@dataclass(frozen=True)
class ReviewBasis:
    """Stable source identity and the role fields needed to validate a producer."""

    gate: str
    review_basis_hash: str
    ac_ids: tuple[str, ...]
    active_rules: tuple[ActiveRule, ...] | None
    output_contract_version: str | None
    output_contract_sha256: str | None


@dataclass(frozen=True)
class PreparedDiff:
    """Captured diff facts whose filesystem bytes live on the prepared run."""

    source: str
    source_detail: str
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "changed_files", tuple(self.changed_files))
        object.__setattr__(self, "untracked_files", tuple(self.untracked_files))
        object.__setattr__(self, "warnings", tuple(self.warnings))


@dataclass(frozen=True)
class ContextProjection:
    text: str
    reviewed_inputs: tuple[ReviewedInput, ...]


@dataclass(frozen=True)
class GateInputContract:
    requires_spec: bool
    requires_plan: bool
    requires_milestone: bool
    requires_diff: bool
    context_builders: tuple[str, ...]
    source_selector: Literal["none", "milestone-owns", "feature-owned-union"] = "none"


@dataclass(frozen=True)
class PreparedGateRun:
    feature: str
    gate: str
    scope: str
    invocation: ResolvedGateInvocation
    prompt: ResolvedGatePrompt
    prompt_version: str
    effective_prompt_sha256: str
    transport: PromptTransport
    runtime_sections: tuple[str, ...]
    reviewed_inputs: tuple[ReviewedInput, ...]
    review_basis_hash: str
    input_hash: str
    diff: PreparedDiff
    diff_text: str
    overlap: OverlapResult
    preflight: PreflightResult
    diagnostics: tuple[str, ...] = ()
    output_contract: ReviewOutputContract | None = None
    output_contract_version: str | None = None
    output_contract_sha256: str | None = None
    ac_ids: tuple[str, ...] = ()
    prior_reviews: tuple[PriorReviewArtifact, ...] = ()
    review_decisions: tuple[ReviewDecision, ...] = ()
    active_rules: tuple[ActiveRule, ...] | None = None
    assignment_id: str | None = None
    round_number: int | None = None
    reviewer_slot: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_sections", tuple(self.runtime_sections))
        object.__setattr__(self, "reviewed_inputs", tuple(self.reviewed_inputs))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


@dataclass(frozen=True)
class ReviewValidationInputs:
    """Only the frozen operands used to validate an interpreted current review."""

    gate: str
    ac_ids: tuple[str, ...]
    active_rules: tuple[ActiveRule, ...] | None
    output_contract_version: str
    assignment_id: str
    prior_reviews: tuple[PriorReviewArtifact, ...]
    review_decisions: tuple[ReviewDecision, ...]


@dataclass(frozen=True)
class GateType:
    name: str
    requires_plan: bool
    requires_milestone: bool
    requires_diff: bool
    supported_clis: tuple[str, ...]
    tool_permissions: tuple[str, ...]
    output_contract: ReviewOutputContract | None
    prompt_template: str
    milestone_output_naming: bool = False
    context_builders: tuple[str, ...] = ()
    preflight_applicable: bool = False
    # The gate-to-CLI configuration: the lane a run uses when the invoker
    # passes no --cli. Must be a member of supported_clis; run-gate resolves
    # it after registry lookup so an explicit --cli always wins.
    default_cli: str = "claude"
    # spec reliance is declared independently from plan reliance. The
    # default preserves hand-built/third-party GateType compatibility; every
    # built-in gate declares its actual contract in the registry.
    requires_spec: bool = False
    source_selector: Literal["none", "milestone-owns", "feature-owned-union"] = "none"
    # The complete execution defaults for each supported provider lane. Empty
    # preserves compatibility for hand-built GateType values, which resolve
    # through the provider fallback.
    lane_policies: tuple[GateLanePolicy, ...] = ()


@dataclass
class GateContext:
    gate_type: GateType
    feature: str
    repo_root: Path
    plan_path: Path | None
    spec_path: Path | None
    milestone: str | None
    milestone_title: str = ""
    milestone_section: str = ""
    satisfies_ac_ids: list[str] = field(default_factory=list)
    owned_paths: list[str] = field(default_factory=list)
    spec_ac_ids: list[str] = field(default_factory=list)
    plan_content: str = ""
    spec_content: str = ""
    source_observations: dict[str, ObservedPath] = field(default_factory=dict)
    operational_plan_facts: tuple[dict, ...] = ()
    feature_owned_paths: tuple[str, ...] = ()
    source_baseline: SourceBaseline | None = None
    # seam: the kernel FeatureSnapshot's verification commands (AC-1) — a
    # runtime-seam fact; empty on hand-built contexts.
    verification_commands: dict[str, str] = field(default_factory=dict)
    # Native workspace root (plans/<slug>/) from the kernel snapshot; None on
    # legacy-format features (no workspace directory). When set, gate artifacts
    # land in <workspace_dir>/reviews/ (E1 rehome) so the recorded run-fact
    # artifact is genuinely workspace-relative even for gates that run before
    # plan.md exists (plan_path is None at spec-review).
    workspace_dir: Path | None = None
    # Captured same-lane completed history and the pipeline's decision context.
    # Provider errors do not become retained review inputs.
    latest_lane_run_id: str | None = None
    prior_completed_runs: int = 0
    document_review_context: str = ""
    document_review_round: int = 0
    prior_review_artifacts: tuple[PriorReviewArtifact, ...] = ()
    prior_decisions: list[dict] = field(default_factory=list)
    reviewed_inputs: tuple[ReviewedInput, ...] = ()
    preparation_diagnostics: list[str] = field(default_factory=list)
    # Runtime-only handoff: a cache miss launches the exact prompt prepared
    # before cache lookup instead of resolving mutable resources a second time.
    prepared_run: PreparedGateRun | None = None
    review_assignment: ReviewAssignment | None = None
    assignment_round: AssignmentRound | None = None
    reviewer_slot: str | None = None


@dataclass(frozen=True)
class DiffResult:
    source: str
    source_detail: str
    diff_path: Path
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "changed_files", tuple(self.changed_files))
        object.__setattr__(self, "untracked_files", tuple(self.untracked_files))
        object.__setattr__(self, "warnings", tuple(self.warnings))


@dataclass(frozen=True)
class OverlapResult:
    status: str
    files: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))


@dataclass(frozen=True)
class PreflightResult:
    status: str
    fatal_reason: str | None
    anomalies: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "anomalies", tuple(self.anomalies))


@dataclass
class JsonlParseResult:
    events: list[dict[str, Any]]
    total_lines: int
    json_lines_attempted: int
    json_lines_parsed: int
    parse_errors: int
    non_json_lines: int
    error_samples: list[str]
    diagnostic_lines: list[str]
    event_offsets: list[int] = field(default_factory=list)


@dataclass
class CodexEventTimeline:
    first_event_offset_s: float | None
    last_event_offset_s: float | None
    last_tool_event_offset_s: float | None
    last_message_event_offset_s: float | None
    turn_completed_offset_s: float | None
    gap_before_termination_s: float | None
    last_event_type: str | None = None


@dataclass
class MessageExtraction:
    source_used: str
    output_file_bytes: int
    jsonl_message_count: int
    jsonl_last_message_bytes: int
    jsonl_all_messages_bytes: int
    sources_agree: bool
    warnings: list[str]


@dataclass
class CodexDiagnosis:
    category: str
    description: str
    inspect_artifacts: list[str]
    likely_cause: str
    recommended_action: str


@dataclass
class CodexEventInfo:
    tool_event_count: int
    message_event_count: int
    turn_completed_count: int
    transport_error_count: int
    parse_errors: int = 0
    non_json_lines: int = 0
    timeline: CodexEventTimeline | None = None
    message_extraction: MessageExtraction | None = None


@dataclass
class CodexDiagnosticBundle:
    parse_result: JsonlParseResult
    timeline: CodexEventTimeline
    extraction: MessageExtraction
    diagnosis: CodexDiagnosis
    event_info: CodexEventInfo


@dataclass
class ValidationResult:
    passed: bool
    structure_errors: list[str]
    structure_warnings: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    monitor: MonitorResult
    cli_exit: int
    failure_reason: FailureReason | None
    raw_out_path: Path
    msg_out_path: Path | None
    filtered_jsonl_path: Path | None
    codex_diagnostics: CodexDiagnosticBundle | None
    json_message_path: Path | None = None
    # Only set after strict process and terminal-success validation.
    response_warning: str | None = None
    response_channel: str | None = None


@dataclass
class MilestoneInfo:
    number: str | None
    title: str
    satisfies_ac_ids: list[str]


@dataclass
class RuntimeInfo:
    model: str
    reasoning_effort: str
    sandbox: str
    workdir: str
    timeout_seconds: int
    inactivity_seconds: int
    inactivity_observed_seconds: int
    inactivity_marker: str
    iteration: int = 1
    max_iterations: int | None = 5
    # 16-char content hash of the gate's prompt wording (template + spliced
    # partials + the shared standards doc) — buckets trajectory metrics by
    # prompt version so a wording edit is measurable. None when the template
    # could not be resolved (the failure surfaces loudly at build time).
    prompt_version: str | None = None
    effective_prompt_sha256: str | None = None
    review_basis_hash: str | None = None
    input_hash: str | None = None
    review_policy_id: str | None = None
    output_contract_version: str | None = None
    output_contract_sha256: str | None = None


@dataclass
class DiffSummary:
    source: str
    source_detail: str
    bytes: int
    changed_files: list[str]
    untracked_files: list[str] = field(default_factory=list)


@dataclass
class FindingsInfo:
    by_severity: dict[str, int]
    by_classification: dict[str, int]
    by_severity_classification: dict[str, int]
    total: int
    contradictions: int = 0


@dataclass
class VerdictInfo:
    status: str | None
    rerun_recommended: bool | None
    converged: bool | None


@dataclass
class OwnershipInfo:
    milestone_owned_paths: list[str]
    overlap_status: str
    overlap_files: list[str]


@dataclass
class PreflightInfo:
    status: str
    fatal_reason: str | None
    anomalies: list[str]


@dataclass
class OutputContractInfo:
    required_sections: list[str]
    structure_errors: list[str]
    structure_warnings: list[str] = field(default_factory=list)


@dataclass
class IOInfo:
    output: str
    log: str
    summary: str
    raw_bytes: int
    filtered_bytes: int
    last_message_bytes: int
    json_message_bytes: int
    output_bytes: int
    output_lines: int


@dataclass
class ArtifactInfo:
    output: str
    log: str
    summary: str
    prompt: str | None
    raw_out: str | None
    last_message: str | None
    json_message: str | None
    command_output: str | None
    events_jsonl: str | None
    published_views: tuple[str, ...] = ()


@dataclass
class GateSummary:
    feature: str
    gate: str
    cli: str
    plan: str
    spec: str
    milestone: MilestoneInfo
    status: str
    reason: str
    gate_exit: int
    cli_exit: int
    runtime: RuntimeInfo
    diff: DiffSummary
    ownership: OwnershipInfo
    preflight: PreflightInfo
    output_contract: OutputContractInfo
    io: IOInfo
    codex_events: CodexEventInfo
    artifacts: ArtifactInfo
    codex_diagnosis: CodexDiagnosis | None = None
    findings: FindingsInfo | None = None
    findings_note: str | None = None
    verdict: VerdictInfo | None = None
    verdict_note: str | None = None
    review_result: ReviewResult | None = None
    retained_review: CapturedReview | None = None

    def to_dict(self) -> dict[str, Any]:
        data = cast(
            dict[str, Any],
            serialize(replace(self, review_result=None, retained_review=None)),
        )
        data.pop("review_result", None)
        data.pop("retained_review", None)
        for name in (
            "review_policy_id",
            "output_contract_version",
            "output_contract_sha256",
        ):
            if data["runtime"].get(name) is None:
                data["runtime"].pop(name, None)
        if self.findings is None and self.findings_note is None:
            data.pop("findings", None)
        if self.findings_note is None:
            data.pop("findings_note", None)
        if self.verdict is None and self.verdict_note is None:
            data.pop("verdict", None)
        if self.verdict_note is None:
            data.pop("verdict_note", None)
        if not data["artifacts"]["published_views"]:
            data["artifacts"].pop("published_views")
        return data


def serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {
            item.name: serialize(getattr(value, item.name))
            for item in fields(value)
            if getattr(value, item.name) is not None or item.name != "codex_diagnosis"
        }
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    return value
