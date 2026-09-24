"""Native review content and invocation-bound canonical results.

The model supplies content. Only the runtime binds invocation identity. JSON
Schema checks representation here; validation.py owns review meaning.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from heddle.contracts.gates import GATE_CATALOG
from heddle.gate.types import GateExecutionConfig, PreparedGateRun

MAX_REVIEW_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 32 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024
REVIEW_CONTENT_VERSION = "heddle.review-content/v3"
REVIEW_RESULT_VERSION = "heddle.review-result/v3"
EVIDENCE_KINDS = ("trace", "speculation", "test", "execution", "absence", "unavailable")
NON_AFFIRMATIVE_EVIDENCE_KINDS = frozenset({"absence", "unavailable", "speculation"})
AFFIRMATIVE_EVIDENCE_KINDS = tuple(
    kind for kind in EVIDENCE_KINDS if kind not in NON_AFFIRMATIVE_EVIDENCE_KINDS
)


class TruncatedReviewError(ValueError):
    """The JSON decoder reached an explicitly unfinished document."""


@dataclass(frozen=True)
class Evidence:
    kind: str
    references: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class EvidenceNote:
    text: str
    evidence: Evidence


@dataclass(frozen=True)
class DecisionOption:
    name: str
    tradeoffs: str


@dataclass(frozen=True)
class FindingDecision:
    context: str
    options: tuple[DecisionOption, ...]
    recommended: str
    rationale: str
    deferred_impact: str
    route: str | None


@dataclass(frozen=True)
class ReviewFinding:
    id: str
    title: str
    severity: str
    classification: str
    confidence: str
    location: str
    evidence: Evidence
    problem: str
    impact: str
    recommendation: str
    decision: FindingDecision | None


@dataclass(frozen=True)
class FindingRef:
    run_id: str
    finding_id: str


@dataclass(frozen=True)
class FindingDisposition:
    source: FindingRef
    disposition: str
    output_finding_id: str | None
    decision_id: str | None
    decision_origin: FindingRef | None
    evidence: Evidence
    reason: str


@dataclass(frozen=True)
class Regression:
    finding_id: str
    evidence: Evidence


@dataclass(frozen=True)
class EnforcementSuggestion:
    finding_id: str | None
    rule: str
    mechanism: str
    location: str | None
    rationale: str
    evidence: Evidence


@dataclass(frozen=True)
class ACStatus:
    ac_id: str
    status: str
    evidence: Evidence
    caveat: str


@dataclass(frozen=True)
class RuleCompliance:
    rule: str
    assessment: str
    evidence: Evidence


@dataclass(frozen=True)
class SelfReviewDetails:
    assessment: str
    ac_status: tuple[ACStatus, ...]
    rule_compliance: tuple[RuleCompliance, ...]


@dataclass(frozen=True)
class IdentifiedAssessment:
    id: str
    assessment: str
    evidence: Evidence


@dataclass(frozen=True)
class Improvement:
    finding_id: str
    dimension: str
    current: str
    proposed: str
    behavior_preserved: str


@dataclass(frozen=True)
class CodeQualityDetails:
    assessment: str
    dimensions: tuple[IdentifiedAssessment, ...]
    improvements: tuple[Improvement, ...]


@dataclass(frozen=True)
class ACSpecificity:
    ac_id: str
    assessment: str
    reason: str


@dataclass(frozen=True)
class ScopeDelta:
    item: str
    classification: str
    authorization: str
    reason: str


@dataclass(frozen=True)
class Increment:
    name: str
    category: str
    delivers: str
    additive_seam: str


@dataclass(frozen=True)
class ScopeAssessment:
    approved_mvp_present: bool
    delta: tuple[ScopeDelta, ...]
    sizing: str
    challenge: str
    integrated_acceptance: str
    increment_ladder: tuple[Increment, ...]
    confirmation: str


@dataclass(frozen=True)
class Refinement:
    finding_id: str | None
    section: str
    current: str
    proposed: str


@dataclass(frozen=True)
class SpecReviewDetails:
    dimensions: tuple[IdentifiedAssessment, ...]
    ac_specificity: tuple[ACSpecificity, ...]
    scope: ScopeAssessment
    refinements: tuple[Refinement, ...]


@dataclass(frozen=True)
class ACMapping:
    ac_id: str
    milestones: tuple[str, ...]
    assessment: str
    evidence: Evidence


@dataclass(frozen=True)
class DependencyAssessment:
    milestone: str
    depends_on: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class PlanReviewDetails:
    dimensions: tuple[IdentifiedAssessment, ...]
    ac_mapping: tuple[ACMapping, ...]
    dependencies: tuple[DependencyAssessment, ...]
    verification: str


@dataclass(frozen=True)
class ACTests:
    ac_id: str
    assessment: str
    milestone_tests: tuple[str, ...]
    acceptance_tests: tuple[str, ...]
    live_tests: tuple[str, ...]
    evidence: Evidence


@dataclass(frozen=True)
class ScaffoldingDetails:
    dimensions: tuple[IdentifiedAssessment, ...]
    ac_tests: tuple[ACTests, ...]
    infrastructure: tuple[IdentifiedAssessment, ...]


@dataclass(frozen=True)
class ACCoverage:
    ac_id: str
    coverage: str
    tests: tuple[str, ...]
    evidence: Evidence
    consequence: str


@dataclass(frozen=True)
class Risk:
    finding_id: str
    likelihood: str
    impact_level: str
    current_handling: str
    dependency: str
    mitigation: str


@dataclass(frozen=True)
class RobustnessAnalysisDetails:
    assessment: str
    categories: tuple[IdentifiedAssessment, ...]
    risks: tuple[Risk, ...]


@dataclass(frozen=True)
class PeerReviewDetails:
    assessment: str
    ac_status: tuple[ACStatus, ...]
    ac_coverage: tuple[ACCoverage, ...]
    dimensions: tuple[IdentifiedAssessment, ...]
    plan_context: str


type ReviewDetails = (
    SelfReviewDetails
    | CodeQualityDetails
    | SpecReviewDetails
    | PlanReviewDetails
    | ScaffoldingDetails
    | RobustnessAnalysisDetails
    | PeerReviewDetails
)


@dataclass(frozen=True)
class ReviewVerdict:
    status: str
    rerun_recommended: bool
    reason: str


@dataclass(frozen=True)
class ReviewContent:
    role: str
    summary: str
    findings: tuple[ReviewFinding, ...]
    observations: tuple[EvidenceNote, ...]
    limitations: tuple[EvidenceNote, ...]
    enforcement_suggestions: tuple[EnforcementSuggestion, ...]
    prior_dispositions: tuple[FindingDisposition, ...]
    regressions: tuple[Regression, ...]
    details: ReviewDetails


@dataclass(frozen=True)
class VerdictReviewContent(ReviewContent):
    verdict: ReviewVerdict


@dataclass(frozen=True)
class ReviewOutputContract:
    version: str
    role: str
    schema_json: str
    schema_sha256: str


@dataclass(frozen=True)
class ReviewInvocation:
    feature: str
    gate: str
    scope: str
    execution: GateExecutionConfig
    review_policy_id: str | None
    prompt_version: str
    effective_prompt_sha256: str
    review_basis_hash: str
    input_hash: str
    output_contract_version: str
    output_contract_sha256: str
    assignment_id: str | None = None
    round_number: int | None = None
    reviewer_slot: str | None = None


@dataclass(frozen=True)
class ReviewResult:
    schema: str
    invocation: ReviewInvocation
    content: ReviewContent


def canonical_json(value: Any, *, newline: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + ("\n" if newline else "")
    ).encode("utf-8")


def read_bounded(path: Path, maximum: int = MAX_REVIEW_BYTES) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError(
            f"review input exceeds the {maximum}-byte size limit: {path.name}"
        )
    return raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError(f"nonfinite JSON number is not permitted: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _nonfinite(value)
    return parsed


def strict_json(raw: bytes, *, maximum: int = MAX_REVIEW_BYTES) -> dict[str, Any]:
    if len(raw) > maximum:
        raise ValueError(f"review input exceeds the {maximum}-byte size limit")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_nonfinite,
            parse_float=_finite_float,
        )
    except json.JSONDecodeError as error:
        if text.lstrip().startswith("{") and (
            error.pos >= len(text.rstrip())
            or error.msg.startswith("Unterminated string")
        ):
            raise TruncatedReviewError(f"unfinished review JSON: {error}") from error
        raise ValueError(f"invalid review JSON representation: {error}") from error
    except (UnicodeError, RecursionError) as error:
        raise ValueError(f"invalid review JSON representation: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("review JSON root must be one object")
    return value


def _object(**properties: Any) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def _enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _nullable(value: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [value, {"type": "null"}]}


_TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
_CAVEAT = {"type": "string"}

ROLE_DIMENSIONS = {
    "spec-review": (
        "purpose-and-scope",
        "conceptual-coherence",
        "acceptance-criteria-quality",
        "assumptions-and-failure-classes",
        "design-commitments",
        "related-context-and-reusability",
        "approach-necessity",
    ),
    "plan-review": (
        "approach-soundness",
        "architecture-fit",
        "acceptance-criteria-coverage",
        "dependency-ordering",
        "independent-verifiability",
        "verification-feasibility",
        "related-context-and-reusability",
        "approach-necessity",
    ),
    "review-test-scaffolding": (
        "ac-coverage-completeness",
        "tautological-test-detection",
        "red-phase-readiness",
        "acceptance-test-design",
        "live-e2e-alignment",
        "smoke-and-infrastructure-validity",
    ),
    "peer-review-sequential": (
        "correctness",
        "architecture",
        "robustness",
        "test-quality",
        "code-quality",
    ),
    "milestone-review": ("correctness", "code-quality", "material-test-changes"),
    "behavior-review": ("correctness", "failure-behavior", "final-proof-adequacy"),
    "complexity-review": (
        "ownership",
        "interfaces",
        "dependency-direction",
        "necessity",
        "simplification",
    ),
}
FAILURE_CATEGORIES = (
    "external-dependency-failure",
    "input-handling",
    "concurrency",
    "resource-exhaustion",
    "error-messages",
    "failure-recoverability",
    "security",
)


def _common_definitions() -> dict[str, Any]:
    return {
        "Evidence": _object(
            kind=_enum(*EVIDENCE_KINDS),
            references=_array(_TEXT),
            explanation=_TEXT,
        ),
        "EvidenceNote": _object(text=_TEXT, evidence=_ref("Evidence")),
        "DecisionOption": _object(name=_TEXT, tradeoffs=_TEXT),
        "FindingDecision": _object(
            context=_TEXT,
            options=_array(_ref("DecisionOption")),
            recommended=_TEXT,
            rationale=_TEXT,
            deferred_impact=_TEXT,
            route=_nullable(_TEXT),
        ),
        "ReviewFinding": _object(
            id=_TEXT,
            title=_TEXT,
            severity=_enum("critical", "important", "minor"),
            classification=_enum("implement", "report", "ignore"),
            confidence=_enum("high", "medium", "low"),
            location=_TEXT,
            evidence=_ref("Evidence"),
            problem=_TEXT,
            impact=_TEXT,
            recommendation=_TEXT,
            decision=_nullable(_ref("FindingDecision")),
        ),
        "FindingRef": _object(run_id=_TEXT, finding_id=_TEXT),
        "FindingDisposition": _object(
            source=_ref("FindingRef"),
            disposition=_enum("retained", "addressed", "awaiting_decision", "settled"),
            output_finding_id=_nullable(_TEXT),
            decision_id=_nullable(_TEXT),
            decision_origin=_nullable(_ref("FindingRef")),
            evidence=_ref("Evidence"),
            reason=_TEXT,
        ),
        "Regression": _object(finding_id=_TEXT, evidence=_ref("Evidence")),
        "EnforcementSuggestion": _object(
            finding_id=_nullable(_TEXT),
            rule=_TEXT,
            mechanism=_TEXT,
            location=_nullable(_TEXT),
            rationale=_TEXT,
            evidence=_ref("Evidence"),
        ),
        "ACStatus": _object(
            ac_id=_TEXT,
            status=_enum("pass", "partial", "fail"),
            evidence=_ref("Evidence"),
            caveat=_CAVEAT,
        ),
        "RuleCompliance": _object(
            rule=_TEXT,
            assessment=_enum("compliant", "violated", "unverifiable"),
            evidence=_ref("Evidence"),
        ),
    }


def _detail_schema(gate: str, definitions: dict[str, Any]) -> dict[str, Any]:
    role = gate
    role_dimensions = ROLE_DIMENSIONS
    definitions["IdentifiedAssessment"] = _object(
        id=_TEXT, assessment=_TEXT, evidence=_ref("Evidence")
    )
    dimensions = (
        _array(
            _object(
                id=_enum(*role_dimensions[role]),
                assessment=_TEXT,
                evidence=_ref("Evidence"),
            )
        )
        if role in role_dimensions
        else None
    )
    if role == "spec-review":
        return _object(
            dimensions=dimensions,
            ac_specificity=_array(
                _object(
                    ac_id=_TEXT,
                    assessment=_enum(
                        "concrete", "partial", "vague", "conflict", "unverifiable"
                    ),
                    reason=_TEXT,
                )
            ),
            scope=_object(
                approved_mvp_present={"type": "boolean"},
                delta=_array(
                    _object(
                        item=_TEXT,
                        classification=_enum(
                            "approved",
                            "authorized-part",
                            "deferred",
                            "unmapped",
                            "conflict",
                        ),
                        authorization=_TEXT,
                        reason=_TEXT,
                    )
                ),
                sizing=_enum(
                    "right-sized", "too-broad", "too-small", "conflict", "unverifiable"
                ),
                challenge=_TEXT,
                integrated_acceptance=_TEXT,
                increment_ladder=_array(
                    _object(
                        name=_TEXT,
                        category=_enum("mvp", "important", "optional"),
                        delivers=_TEXT,
                        additive_seam=_TEXT,
                    )
                ),
                confirmation=_TEXT,
            ),
            refinements=_array(
                _object(
                    finding_id=_nullable(_TEXT),
                    section=_TEXT,
                    current=_TEXT,
                    proposed=_TEXT,
                )
            ),
        )
    if role == "plan-review":
        return _object(
            dimensions=dimensions,
            ac_mapping=_array(
                _object(
                    ac_id=_TEXT,
                    milestones=_array(_TEXT),
                    assessment=_enum(
                        "covered", "partial", "gap", "conflict", "unverifiable"
                    ),
                    evidence=_ref("Evidence"),
                )
            ),
            dependencies=_array(
                _object(milestone=_TEXT, depends_on=_array(_TEXT), reason=_TEXT)
            ),
            verification=_TEXT,
        )
    if role == "review-test-scaffolding":
        return _object(
            dimensions=dimensions,
            ac_tests=_array(
                _object(
                    ac_id=_TEXT,
                    assessment=_enum(
                        "covered", "partial", "gap", "conflict", "unverifiable"
                    ),
                    milestone_tests=_array(_TEXT),
                    acceptance_tests=_array(_TEXT),
                    live_tests=_array(_TEXT),
                    evidence=_ref("Evidence"),
                )
            ),
            infrastructure=_array(_ref("IdentifiedAssessment")),
        )
    if role == "robustness-analysis":
        risks = _array(
            _object(
                finding_id=_TEXT,
                likelihood=_enum("high", "medium", "low"),
                impact_level=_enum("high", "medium", "low"),
                current_handling=_TEXT,
                dependency=_TEXT,
                mitigation=_TEXT,
            )
        )
        return _object(
            assessment=_enum("concerns-identified", "adequate", "robust"),
            categories=_array(
                _object(
                    id=_enum(*FAILURE_CATEGORIES),
                    assessment=_TEXT,
                    evidence=_ref("Evidence"),
                )
            ),
            risks=risks,
        )
    definitions["ACCoverage"] = _object(
        ac_id=_TEXT,
        coverage=_enum("full", "partial", "missing"),
        tests=_array(_TEXT),
        evidence=_ref("Evidence"),
        consequence=_TEXT,
    )
    if role in {
        "peer-review-sequential",
        "milestone-review",
        "behavior-review",
        "complexity-review",
    }:
        definitions["ACStatus"] = _object(
            ac_id=_TEXT,
            status=_enum("pass", "partial", "fail", "untestable"),
            evidence=_ref("Evidence"),
            caveat=_CAVEAT,
        )
        fields: dict[str, Any] = dict(
            assessment=_TEXT,
            ac_status=_array(_ref("ACStatus")),
            ac_coverage=_array(_ref("ACCoverage")),
        )
        fields.update(dimensions=dimensions, plan_context=_TEXT)
        return _object(**fields)
    raise ValueError(f"unknown review role: {gate}")


@lru_cache(maxsize=15)
def review_output_contract(gate: str) -> ReviewOutputContract:
    """The current launch contract; retired roles have no current contract."""
    if gate not in GATE_CATALOG:
        raise ValueError(f"unknown or retired review role: {gate}")
    return _output_contract(gate, version=REVIEW_CONTENT_VERSION)


def _output_contract(gate: str, *, version: str) -> ReviewOutputContract:
    definitions = _common_definitions()
    if version == REVIEW_CONTENT_VERSION:
        definitions["FindingDecision"]["properties"]["recommended"] = {
            **_TEXT,
            "description": (
                "The preferred resolution in ordinary recommendation prose. "
                "Explain the choice as needed; this is not an option-name selector "
                "and need not exactly repeat an options[].name value."
            ),
        }
    details = _detail_schema(gate, definitions)
    schema = _object(
        role=_enum(gate),
        summary=_TEXT,
        findings=_array(_ref("ReviewFinding")),
        observations=_array(_ref("EvidenceNote")),
        limitations=_array(_ref("EvidenceNote")),
        enforcement_suggestions=_array(_ref("EnforcementSuggestion")),
        prior_dispositions=_array(_ref("FindingDisposition")),
        regressions=_array(_ref("Regression")),
        details=details,
    )
    if GATE_CATALOG[gate].produces_verdict:
        schema["properties"]["verdict"] = _object(
            status=_enum("pass", "pass_with_conditions", "fail"),
            rerun_recommended={"type": "boolean"},
            reason=_TEXT,
        )
        schema["required"].append("verdict")
    schema["$defs"] = definitions
    Draft202012Validator.check_schema(schema)
    raw = canonical_json(schema)
    contract = ReviewOutputContract(
        version,
        gate,
        raw.decode(),
        hashlib.sha256(raw).hexdigest(),
    )
    _validator(contract)
    return contract


@lru_cache(maxsize=15)
def _validator(contract: ReviewOutputContract) -> Draft202012Validator:
    return Draft202012Validator(json.loads(contract.schema_json))


def _evidence(value: dict[str, Any]) -> Evidence:
    return Evidence(value["kind"], tuple(value["references"]), value["explanation"])


def _with_evidence(cls: Any, value: dict[str, Any]) -> Any:
    return cls(**{**value, "evidence": _evidence(value["evidence"])})


def _finding(value: dict[str, Any]) -> ReviewFinding:
    decision = value["decision"]
    if decision is not None:
        decision = FindingDecision(
            **{
                **decision,
                "options": tuple(
                    DecisionOption(**option) for option in decision["options"]
                ),
            }
        )
    return ReviewFinding(
        **{**value, "decision": decision, "evidence": _evidence(value["evidence"])}
    )


def _disposition(value: dict[str, Any]) -> FindingDisposition:
    return FindingDisposition(
        **{
            **value,
            "source": FindingRef(**value["source"]),
            "decision_origin": FindingRef(**value["decision_origin"])
            if value["decision_origin"] is not None
            else None,
            "evidence": _evidence(value["evidence"]),
        }
    )


def decode_review_content(raw: bytes, contract: ReviewOutputContract) -> ReviewContent:
    value = strict_json(raw)
    try:
        _validator(contract).validate(value)
    except ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise ValueError(f"review schema at {location}: {error.message}") from error
    return _content_from_validated(value)


def _evidence_rows(cls: Any, rows: list[dict[str, Any]]) -> tuple[Any, ...]:
    return tuple(_with_evidence(cls, row) for row in rows)


def _coverage_rows(rows: list[dict[str, Any]]) -> tuple[ACCoverage, ...]:
    return tuple(
        _with_evidence(ACCoverage, {**row, "tests": tuple(row["tests"])})
        for row in rows
    )


def _details_from_validated(gate: str, value: dict[str, Any]) -> ReviewDetails:
    role = gate
    if role == "spec-review":
        scope = value["scope"]
        return SpecReviewDetails(
            _evidence_rows(IdentifiedAssessment, value["dimensions"]),
            tuple(ACSpecificity(**row) for row in value["ac_specificity"]),
            ScopeAssessment(
                **{
                    **scope,
                    "delta": tuple(ScopeDelta(**row) for row in scope["delta"]),
                    "increment_ladder": tuple(
                        Increment(**row) for row in scope["increment_ladder"]
                    ),
                }
            ),
            tuple(Refinement(**row) for row in value["refinements"]),
        )
    if role == "plan-review":
        return PlanReviewDetails(
            _evidence_rows(IdentifiedAssessment, value["dimensions"]),
            tuple(
                _with_evidence(
                    ACMapping, {**row, "milestones": tuple(row["milestones"])}
                )
                for row in value["ac_mapping"]
            ),
            tuple(
                DependencyAssessment(**{**row, "depends_on": tuple(row["depends_on"])})
                for row in value["dependencies"]
            ),
            value["verification"],
        )
    if role == "review-test-scaffolding":
        return ScaffoldingDetails(
            _evidence_rows(IdentifiedAssessment, value["dimensions"]),
            tuple(
                _with_evidence(
                    ACTests,
                    {
                        **row,
                        **{
                            key: tuple(row[key])
                            for key in (
                                "milestone_tests",
                                "acceptance_tests",
                                "live_tests",
                            )
                        },
                    },
                )
                for row in value["ac_tests"]
            ),
            _evidence_rows(IdentifiedAssessment, value["infrastructure"]),
        )
    if role == "robustness-analysis":
        return RobustnessAnalysisDetails(
            value["assessment"],
            _evidence_rows(IdentifiedAssessment, value["categories"]),
            tuple(Risk(**row) for row in value["risks"]),
        )
    if role in {
        "peer-review-sequential",
        "milestone-review",
        "behavior-review",
        "complexity-review",
    }:
        return PeerReviewDetails(
            value["assessment"],
            _evidence_rows(ACStatus, value["ac_status"]),
            _coverage_rows(value["ac_coverage"]),
            _evidence_rows(IdentifiedAssessment, value["dimensions"]),
            value["plan_context"],
        )
    raise ValueError(f"unknown review role: {gate}")


def _content_from_validated(value: dict[str, Any]) -> ReviewContent:
    fields = dict(
        role=value["role"],
        summary=value["summary"],
        findings=tuple(_finding(item) for item in value["findings"]),
        observations=tuple(
            _with_evidence(EvidenceNote, item) for item in value["observations"]
        ),
        limitations=tuple(
            _with_evidence(EvidenceNote, item) for item in value["limitations"]
        ),
        enforcement_suggestions=tuple(
            _with_evidence(EnforcementSuggestion, item)
            for item in value["enforcement_suggestions"]
        ),
        prior_dispositions=tuple(
            _disposition(item) for item in value["prior_dispositions"]
        ),
        regressions=tuple(
            _with_evidence(Regression, item) for item in value["regressions"]
        ),
        details=_details_from_validated(value["role"], value["details"]),
    )
    if "verdict" in value:
        return VerdictReviewContent(**fields, verdict=ReviewVerdict(**value["verdict"]))
    return ReviewContent(**fields)


def bind_review_result(
    content: ReviewContent, prepared: PreparedGateRun, feature: str
) -> ReviewResult:
    if content.role != prepared.gate:
        raise ValueError("review role does not match the prepared invocation")
    return ReviewResult(
        REVIEW_RESULT_VERSION, bind_review_invocation(prepared, feature), content
    )


def bind_review_invocation(prepared: PreparedGateRun, feature: str) -> ReviewInvocation:
    """Capture the same invocation for accepted or retained-only review output."""
    contract = prepared.output_contract
    if not isinstance(contract, ReviewOutputContract):
        raise ValueError("binding a native review requires a frozen native contract")
    if contract.version != REVIEW_CONTENT_VERSION:
        raise ValueError("historical contracts cannot bind a new review result")
    if (
        not prepared.assignment_id
        or type(prepared.round_number) is not int
        or prepared.round_number < 1
        or prepared.reviewer_slot not in {"primary", "secondary"}
    ):
        raise ValueError("new review result requires assignment, round and slot")
    return ReviewInvocation(
        feature=feature,
        gate=prepared.gate,
        scope=prepared.scope,
        execution=prepared.invocation.exec_config,
        review_policy_id=None,
        prompt_version=prepared.prompt_version,
        effective_prompt_sha256=prepared.effective_prompt_sha256,
        review_basis_hash=prepared.review_basis_hash,
        input_hash=prepared.input_hash,
        output_contract_version=contract.version,
        output_contract_sha256=contract.schema_sha256,
        assignment_id=prepared.assignment_id,
        round_number=prepared.round_number,
        reviewer_slot=prepared.reviewer_slot,
    )


def _retained_contract(
    role: str, result_version: str, content_version: str
) -> ReviewOutputContract:
    if result_version != REVIEW_RESULT_VERSION:
        raise ValueError(f"unknown canonical review result version: {result_version}")
    if content_version != REVIEW_CONTENT_VERSION:
        raise ValueError(f"unsupported review content version: {content_version}")
    return review_output_contract(role)


@lru_cache(maxsize=32)
def _result_validator(
    role: str, version: str, content_version: str
) -> Draft202012Validator:
    contract = _retained_contract(role, version, content_version)
    content_schema = json.loads(contract.schema_json)
    definitions = content_schema.pop("$defs")
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    schema = _object(
        schema=_enum(version),
        invocation=_object(
            feature=_TEXT,
            gate=_enum(role),
            scope=_TEXT,
            execution=_object(
                cli=_enum("claude", "codex"),
                model=_TEXT,
                reasoning_effort=_TEXT,
                sandbox=_TEXT,
            ),
            review_policy_id=_nullable(_TEXT),
            prompt_version=_TEXT,
            effective_prompt_sha256=digest,
            review_basis_hash=digest,
            input_hash=digest,
            output_contract_version=_enum(contract.version),
            output_contract_sha256=digest,
        ),
        content=content_schema,
    )
    invocation = schema["properties"]["invocation"]
    invocation["properties"].update(
        assignment_id=_TEXT,
        round_number={"type": "integer", "minimum": 1},
        reviewer_slot=_enum("primary", "secondary"),
    )
    invocation["required"].extend(("assignment_id", "round_number", "reviewer_slot"))
    schema["$defs"] = definitions
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def decode_review_result(raw: bytes) -> ReviewResult:
    value = strict_json(raw)
    content = value.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("role"), str):
        raise ValueError("canonical review result must contain a concrete content role")
    role = content["role"]
    version = value.get("schema")
    if not isinstance(version, str):
        raise ValueError("canonical review result needs a versioned schema")
    invocation = value.get("invocation")
    if not isinstance(invocation, dict) or not isinstance(
        invocation.get("output_contract_version"), str
    ):
        raise ValueError("canonical review result needs a versioned content contract")
    content_version = invocation["output_contract_version"]
    try:
        _result_validator(role, version, content_version).validate(value)
    except ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise ValueError(
            f"canonical result schema at {location}: {error.message}"
        ) from error
    return ReviewResult(
        value["schema"],
        ReviewInvocation(
            **{
                **invocation,
                "execution": GateExecutionConfig(**invocation["execution"]),
            }
        ),
        _content_from_validated(content),
    )


def decode_review_invocation(value: dict[str, Any]) -> ReviewInvocation:
    """Validate a captured invocation with the canonical result's own schema."""
    role, version = value.get("gate"), value.get("output_contract_version")
    if not isinstance(role, str) or not isinstance(version, str):
        raise ValueError("captured invocation needs its gate and content version")
    result_schema = _result_validator(role, REVIEW_RESULT_VERSION, version).schema
    if not isinstance(result_schema, dict):
        raise ValueError("canonical result schema must describe its invocation")
    schema = result_schema["properties"]["invocation"]
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as error:
        raise ValueError(f"captured invocation schema: {error.message}") from error
    return ReviewInvocation(
        **{**value, "execution": GateExecutionConfig(**value["execution"])}
    )


def serialize_review_result(result: ReviewResult) -> bytes:
    value = asdict(result)
    raw = canonical_json(value, newline=True)
    if len(raw) > MAX_REVIEW_BYTES:
        raise ValueError(
            f"canonical review result exceeds the {MAX_REVIEW_BYTES}-byte size limit"
        )
    return raw
