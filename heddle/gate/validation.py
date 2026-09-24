from __future__ import annotations

from collections.abc import Hashable, Iterable

from heddle.gate.results import (
    AFFIRMATIVE_EVIDENCE_KINDS,
    FAILURE_CATEGORIES,
    NON_AFFIRMATIVE_EVIDENCE_KINDS,
    REVIEW_CONTENT_VERSION,
    ROLE_DIMENSIONS,
    FindingRef,
    PeerReviewDetails,
    PlanReviewDetails,
    ReviewContent,
    RobustnessAnalysisDetails,
    ScaffoldingDetails,
    SpecReviewDetails,
    VerdictReviewContent,
)
from heddle.gate.types import (
    PreparedGateRun,
    ReviewBasis,
    ReviewValidationInputs,
)

# Reviewer-native prior dispositions by the original finding's classification;
# every @coverage target counts as IMPLEMENT.
PRIOR_DISPOSITIONS = {
    "implement": ("retained", "addressed"),
    "ignore": ("retained", "settled"),
    "report": ("retained", "awaiting_decision", "settled"),
}


def _choices(values: Iterable[str]) -> str:
    items = list(values)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


_AFFIRMATIVE = _choices(AFFIRMATIVE_EVIDENCE_KINDS)


def _prior_label(source: FindingRef) -> str:
    return f"{source.finding_id} (run {source.run_id})"


def _unique[HashableT: Hashable](
    values: Iterable[HashableT], label: str
) -> set[HashableT]:
    values = tuple(values)
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {label}")
    return set(values)


def validate_review(
    content: ReviewContent, prepared: PreparedGateRun | ReviewValidationInputs
) -> None:
    """Check typed review meaning against the frozen invocation inputs."""
    version = prepared.output_contract_version
    if version is None:
        raise ValueError("review requires a versioned native output contract")
    validate_review_content(content, version=version)
    validate_review_fields(content, prepared)
    _validate_prior_dispositions(content, prepared)
    if isinstance(content, VerdictReviewContent):
        _validate_document_verdict(content, prepared)


def validate_review_content(
    content: ReviewContent, *, version: str = REVIEW_CONTENT_VERSION
) -> None:
    """Validate meaning that depends only on the typed result's own content."""
    if version != REVIEW_CONTENT_VERSION:
        raise ValueError(f"unknown review content version: {version}")
    identifiers = _unique((item.id for item in content.findings), "finding ID")
    for item in content.findings:
        if item.classification == "report":
            decision = item.decision
            if decision is None:
                raise ValueError(f"REPORT finding {item.id} needs decision content")
            options = _unique(
                (option.name for option in decision.options), "REPORT option"
            )
            if len(options) < 2 or not decision.recommended.strip():
                raise ValueError(
                    f"REPORT finding {item.id} needs distinct options "
                    "and a nonempty recommendation"
                )
        elif item.decision is not None:
            raise ValueError(
                f"non-REPORT finding {item.id} cannot own decision content"
            )
    for suggestion in content.enforcement_suggestions:
        if (
            suggestion.finding_id is not None
            and suggestion.finding_id not in identifiers
        ):
            raise ValueError("enforcement suggestion references an unknown finding")
    _validate_prior_references(content, identifiers)
    _validate_role_details(content)
    if isinstance(content, VerdictReviewContent):
        _validate_document_status(content)


def _validate_prior_references(content: ReviewContent, findings: set[str]) -> None:
    """Check local references without reinterpreting historical decision state."""
    _unique(
        (
            (row.source.run_id, row.source.finding_id)
            for row in content.prior_dispositions
        ),
        "prior finding disposition",
    )
    retained = set()
    for row in content.prior_dispositions:
        if row.disposition == "retained":
            if (
                row.output_finding_id not in findings
                or row.decision_id is not None
                or row.decision_origin is not None
            ):
                raise ValueError(
                    f"retained prior finding {_prior_label(row.source)} must name "
                    "an output finding, without a decision disposition"
                )
            retained.add(row.output_finding_id)
        else:
            if row.output_finding_id is not None:
                raise ValueError(
                    "disposed prior finding cannot also name an output finding"
                )
            if row.disposition == "addressed":
                if row.decision_id is not None or row.decision_origin is not None:
                    raise ValueError("addressed prior finding has no decision owner")
                if row.evidence.kind in NON_AFFIRMATIVE_EVIDENCE_KINDS:
                    raise ValueError(
                        f"addressed prior finding {_prior_label(row.source)} needs "
                        f"affirmative {_AFFIRMATIVE} evidence, "
                        f"not {row.evidence.kind}"
                    )
            elif (
                row.disposition == "awaiting_decision"
                or row.decision_id is not None
                or row.decision_origin is not None
            ) and (
                row.decision_id is None
                or row.decision_origin is None
                or row.decision_origin != row.source
            ):
                raise ValueError(
                    "prior REPORT disposition must bind the exact decision origin"
                )
    regressions = _unique(
        (row.finding_id for row in content.regressions), "regression finding"
    )
    if not regressions <= findings:
        raise ValueError("regression references an unknown finding")
    if overlap := regressions & retained:
        raise ValueError(
            "retained prior finding cannot also be a regression: "
            f"{', '.join(sorted(overlap))} continues a retained prior target, "
            "so leave it out of regressions"
        )
    if content.prior_dispositions and regressions != findings - retained:
        missing = ", ".join(sorted(findings - retained - regressions))
        raise ValueError(
            "every new finding on a rerun needs explicit regression evidence: "
            f"{missing} has no regressions entry and no retained prior target"
        )
    for regression in content.regressions:
        if regression.evidence.kind in NON_AFFIRMATIVE_EVIDENCE_KINDS:
            raise ValueError(
                "regression evidence must identify the observed change: "
                f"{regression.finding_id} needs {_AFFIRMATIVE} evidence, "
                f"not {regression.evidence.kind}"
            )


def validate_review_fields(
    content: ReviewContent,
    prepared: PreparedGateRun | ReviewBasis | ReviewValidationInputs,
) -> None:
    """Check role and coverage against the captured stable reviewed sources."""
    if content.role != prepared.gate:
        raise ValueError("review role does not match the prepared gate")
    details = content.details
    if isinstance(details, SpecReviewDetails):
        _exact_coverage(
            (row.ac_id for row in details.ac_specificity),
            prepared.ac_ids,
            "AC specificity assessment",
        )
    elif isinstance(details, PlanReviewDetails):
        _exact_coverage(
            (row.ac_id for row in details.ac_mapping),
            prepared.ac_ids,
            "AC mapping assessment",
        )
    elif isinstance(details, ScaffoldingDetails):
        _exact_coverage(
            (row.ac_id for row in details.ac_tests),
            prepared.ac_ids,
            "AC test assessment",
        )
    if isinstance(details, PeerReviewDetails):
        _exact_coverage(
            (row.ac_id for row in details.ac_status),
            prepared.ac_ids,
            "AC status assessment",
        )
    if isinstance(details, PeerReviewDetails):
        _exact_coverage(
            (row.ac_id for row in details.ac_coverage),
            prepared.ac_ids,
            "AC test coverage",
        )


def _exact_coverage(values: Iterable[str], expected: Iterable[str], label: str) -> None:
    actual = _unique(values, label)
    expected = set(expected)
    if actual != expected:
        raise ValueError(f"{label} coverage must be exactly {sorted(expected)}")


def _validate_role_details(content: ReviewContent) -> None:
    details = content.details
    role = content.role
    dimensions = ROLE_DIMENSIONS
    if isinstance(
        details,
        (
            SpecReviewDetails,
            PlanReviewDetails,
            ScaffoldingDetails,
            PeerReviewDetails,
        ),
    ):
        _exact_coverage(
            (row.id for row in details.dimensions), dimensions[role], "dimension"
        )
    if isinstance(details, SpecReviewDetails):
        _unique(
            (row.ac_id for row in details.ac_specificity),
            "AC specificity assessment",
        )
        _unique((row.item for row in details.scope.delta), "scope delta item")
        if details.scope.approved_mvp_present and not details.scope.delta:
            raise ValueError("present approved MVP requires scope delta coverage")
        if not details.scope.approved_mvp_present and details.scope.delta:
            raise ValueError("missing approved MVP blocks the scope delta assessment")
        _unique((row.name for row in details.scope.increment_ladder), "scope increment")
        findings = {item.id for item in content.findings}
        if any(
            row.finding_id is not None and row.finding_id not in findings
            for row in details.refinements
        ):
            raise ValueError("refinement references an unknown finding")
    elif isinstance(details, PlanReviewDetails):
        _unique(
            (row.ac_id for row in details.ac_mapping),
            "AC mapping assessment",
        )
        for mapping in details.ac_mapping:
            _unique(mapping.milestones, "AC milestone")
            if mapping.assessment == "covered" and not mapping.milestones:
                raise ValueError("covered AC mapping requires an actual milestone")
        _unique(
            (row.milestone for row in details.dependencies), "dependency assessment"
        )
        for dependency in details.dependencies:
            _unique(dependency.depends_on, "dependency source")
            if dependency.milestone in dependency.depends_on:
                raise ValueError("milestone dependency cannot reference itself")
    elif isinstance(details, ScaffoldingDetails):
        _unique(
            (row.ac_id for row in details.ac_tests),
            "AC test assessment",
        )
        _exact_coverage(
            (row.id for row in details.infrastructure),
            ("commands", "prerequisites", "milestone-verification"),
            "infrastructure assessment",
        )
        for row in details.ac_tests:
            if row.assessment == "covered" and not row.milestone_tests:
                raise ValueError("covered AC test assessment requires test references")
    elif isinstance(details, RobustnessAnalysisDetails):
        _exact_coverage(
            (row.finding_id for row in details.risks),
            (item.id for item in content.findings),
            "risk finding",
        )
        _exact_coverage(
            (row.id for row in details.categories),
            FAILURE_CATEGORIES,
            "failure category assessment",
        )
        severities = {item.severity for item in content.findings}
        expected = (
            "concerns-identified"
            if "critical" in severities
            else "adequate"
            if "important" in severities
            else "robust"
        )
        if details.assessment != expected:
            raise ValueError(
                f"robustness assessment must be {expected} for its findings"
            )
    if isinstance(details, PeerReviewDetails):
        _unique(
            (row.ac_id for row in details.ac_status),
            "AC status assessment",
        )
        for ac in details.ac_status:
            if (
                ac.status == "pass"
                and ac.evidence.kind in NON_AFFIRMATIVE_EVIDENCE_KINDS
            ):
                raise ValueError(
                    f"AC {ac.ac_id} cannot PASS without affirmative evidence"
                )
    if isinstance(details, PeerReviewDetails):
        _unique(
            (row.ac_id for row in details.ac_coverage),
            "AC test coverage",
        )
        for coverage in details.ac_coverage:
            if coverage.coverage == "full" and (
                not coverage.tests
                or coverage.evidence.kind in NON_AFFIRMATIVE_EVIDENCE_KINDS
            ):
                raise ValueError("full AC coverage requires affirmative test evidence")


def _document_assessment_severity(content: ReviewContent) -> str | None:
    details = content.details
    if isinstance(details, SpecReviewDetails):
        if not details.scope.approved_mvp_present or any(
            row.assessment == "vague" for row in details.ac_specificity
        ):
            return "critical"
        if any(row.assessment != "concrete" for row in details.ac_specificity):
            return "important"
    if isinstance(details, (PlanReviewDetails, ScaffoldingDetails)):
        assessments = (
            details.ac_mapping
            if isinstance(details, PlanReviewDetails)
            else details.ac_tests
        )
        if any(row.assessment == "gap" for row in assessments):
            return "critical"
        if any(row.assessment != "covered" for row in assessments):
            return "important"
    return None


def _validate_document_status(
    content: VerdictReviewContent,
) -> None:
    levels = {item.severity for item in content.findings}
    assessment_severity = _document_assessment_severity(content)
    if assessment_severity is not None:
        levels.add(assessment_severity)
    expected = (
        "fail"
        if "critical" in levels
        else "pass_with_conditions"
        if "important" in levels
        else "pass"
    )
    if content.verdict.status != expected:
        raise ValueError(
            f"document verdict must be {expected} for its findings and assessments"
        )


def _validate_document_verdict(
    content: VerdictReviewContent,
    prepared: PreparedGateRun | ReviewValidationInputs,
) -> None:
    prior = prepared.assignment_id is None and any(
        item.result is not None for item in prepared.prior_reviews
    )
    new_ids = {row.finding_id for row in content.regressions}
    candidates = [item for item in content.findings if not prior or item.id in new_ids]
    rerun = any(
        item.severity == "critical"
        or (item.severity == "important" and item.classification == "implement")
        for item in candidates
    )
    if not prior and _document_assessment_severity(content) == "critical":
        rerun = True
    if content.verdict.rerun_recommended != rerun:
        raise ValueError(
            "document rerun verdict must match new material actionable findings"
        )


def _validate_prior_dispositions(
    content: ReviewContent, prepared: PreparedGateRun | ReviewValidationInputs
) -> None:
    accepted = [prior for prior in prepared.prior_reviews if prior.result is not None]
    latest = accepted[-1] if accepted else None
    prior = (
        {
            (latest.run_id, item.id): item.classification
            for item in latest.result.content.findings
        }
        if latest is not None and latest.result is not None
        else {}
    )
    actual = {
        (row.source.run_id, row.source.finding_id) for row in content.prior_dispositions
    }
    if prepared.assignment_id is not None:
        prior = {
            (item.run_id, finding.id): finding.classification
            for item in accepted
            for finding in item.result.content.findings
        }
        prior.update(
            ((item.run_id, "@coverage"), "implement")
            for item in accepted
            if item.result.invocation.assignment_id == prepared.assignment_id
        )
    if (not actual <= set(prior)) or (
        prepared.assignment_id is None and actual != set(prior)
    ):
        raise ValueError(
            "prior finding accounting must cover exactly the latest accepted findings"
        )
    if (
        prepared.required_prior_references is not None
        and not set(prepared.required_prior_references) <= actual
    ):
        raise ValueError(
            "prior finding accounting must cover every verification target"
        )
    findings = {item.id for item in content.findings}
    retained = set()
    decisions = {item.id: item for item in prepared.review_decisions}
    for row in content.prior_dispositions:
        source = prior[(row.source.run_id, row.source.finding_id)]
        allowed = PRIOR_DISPOSITIONS[source]
        if row.disposition not in allowed:
            target = (
                "coverage target"
                if row.source.finding_id == "@coverage"
                else f"{source.upper()} finding"
            )
            raise ValueError(
                f"prior {target} {_prior_label(row.source)} takes "
                f"{_choices(allowed)}, not {row.disposition}"
            )
        if row.disposition == "retained":
            if any(
                (item.origin_run_id, item.origin_finding_id)
                == (row.source.run_id, row.source.finding_id)
                for item in prepared.review_decisions
            ):
                raise ValueError(
                    "an owned prior REPORT needs its exact pending or settled "
                    "disposition, not a new finding"
                )
            retained.add(row.output_finding_id)
        else:
            if source == "ignore":
                if row.decision_id is not None or row.decision_origin is not None:
                    raise ValueError("accepted awareness has no decision owner")
            elif source == "report":
                decision = (
                    decisions.get(row.decision_id)
                    if row.decision_id is not None
                    else None
                )
                expected = (
                    "pending" if row.disposition == "awaiting_decision" else "resolved"
                )
                origin = row.decision_origin
                if (
                    decision is None
                    or decision.status != expected
                    or origin is None
                    or (origin.run_id, origin.finding_id)
                    != (decision.origin_run_id, decision.origin_finding_id)
                ):
                    raise ValueError(
                        "prior REPORT disposition must bind the exact "
                        "decision origin and status"
                    )
    regressions = {row.finding_id for row in content.regressions}
    if latest is None:
        if regressions:
            raise ValueError("first review cannot claim regression from a prior result")
    elif prepared.assignment_id is None and regressions != findings - retained:
        missing = ", ".join(sorted(findings - retained - regressions))
        raise ValueError(
            "every new finding on a rerun needs explicit regression evidence: "
            f"{missing} has no regressions entry and no retained prior target"
        )
