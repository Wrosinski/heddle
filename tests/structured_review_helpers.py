"""
Independent native-content fixtures for action routing; only provider I/O is faked.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
from copy import deepcopy
from pathlib import Path

from tests.operational_model_helpers import FEATURE, SPEC, document, fresh_host

ORDINARY_ROLES = (
    "self-review",
    "code-quality",
    "spec-review",
    "plan-review",
    "review-test-scaffolding",
    "review-correctness",
    "review-tests",
    "review-architecture",
    "review-robustness",
    "robustness-analysis",
    "peer-review-sequential",
)
SYNTHESIS_ROLES = (
    "spec-review-synthesis",
    "plan-review-synthesis",
    "review-test-scaffolding-synthesis",
    "review-synthesis",
)
VERDICT_ROLES = (
    "spec-review",
    "plan-review",
    "review-test-scaffolding",
    *SYNTHESIS_ROLES[:3],
)
DIMENSIONS = {
    "code-quality": (
        "simplification",
        "performance",
        "language-idioms",
        "dead-code",
        "abstraction-quality",
        "naming-and-clarity",
    ),
    "spec-review": (
        "self-containment",
        "acceptance-criteria-quality",
        "architectural-completeness",
        "assumption-clarity",
        "enforcement-awareness",
        "conceptual-coherence",
        "ambiguity-detection",
        "milestone-readiness",
        "related-context-and-reusability",
        "mvp-and-increment-scoping",
        "approach-necessity",
    ),
    "plan-review": (
        "independent-verifiability",
        "acceptance-criteria-coverage",
        "dependency-ordering",
        "milestone-sizing",
        "self-containment",
        "environment-completeness",
        "prototyping-milestones",
        "document-size",
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


def result_api():
    """Intentional missing-contract discriminator, not a collection failure."""
    name = "heddle.gate.results"
    assert importlib.util.find_spec(name) is not None, (
        "FAIL W3: native structured review result contract is not implemented"
    )
    return importlib.import_module(name)


def evidence(*, kind="trace", explanation="src/example.py:1 declares VALUE = 7."):
    return {
        "kind": kind,
        "references": ["src/example.py:1"] if kind == "trace" else [],
        "explanation": explanation,
    }


def captured_review(prepared, *, payload=None):
    """Author one canonical result and its accepted event for capture/reuse tests."""
    import hashlib
    from dataclasses import asdict

    from heddle.gate.entry import machine_projection_from_result
    from heddle.gate.results import (
        bind_review_result,
        decode_review_content,
        serialize_review_result,
    )
    from heddle.gate.types import PriorReviewArtifact
    from heddle.kernel.state import GateRun
    from tests.operational_model_helpers import new_run_id

    payload = deepcopy(payload) if payload is not None else content(prepared.gate)
    complete_fixture_coverage(payload, prepared.ac_ids, prepared.active_rules)
    result = bind_review_result(
        decode_review_content(json.dumps(payload).encode(), prepared.output_contract),
        prepared,
        prepared.feature,
    )
    raw = serialize_review_result(result)
    projection = machine_projection_from_result(result)
    assert not projection["report_findings"], "author REPORT ownership explicitly"
    run_id = new_run_id()
    path = f"reviews/{run_id}.review.json"
    run = GateRun(
        run_id=run_id,
        report_findings=(),
        at="2026-09-08T00:00Z",
        cli=prepared.invocation.exec_config.cli,
        artifact=path,
        input_hash=prepared.input_hash,
        review_basis_hash=prepared.review_basis_hash,
        verdict=projection["verdict"],
        findings=projection["findings"],
        execution_config=asdict(prepared.invocation.exec_config),
        overridden_axes=tuple(sorted(prepared.invocation.explicit_axes)),
        review_policy_id=result.invocation.review_policy_id,
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        prompt_version=prepared.prompt_version,
        effective_prompt_sha256=prepared.effective_prompt_sha256,
    )
    prior = PriorReviewArtifact(path, raw.decode(), run_id, prepared.input_hash, result)
    return run, prior


def scripted_review(
    findings=(), *, summary="The scripted review found no issues.", conflicts=0
):
    """Literal content for event tests whose gate role is selected at invocation."""
    return {
        "findings": deepcopy(list(findings)),
        "summary": summary,
        "conflicts": conflicts,
    }


def scripted_result(review, prepared, feature):
    """Bind authored native fields with explicit event-fixture coverage/history.

    Event tests replace the engine; provider acceptance tests use native_providers.
    No review prose is parsed or used as evidence for these authored fields.
    """
    from heddle.gate.results import bind_review_result, decode_review_content
    from heddle.gate.validation import validate_review

    items = deepcopy(review["findings"])
    status = (
        "fail"
        if any(x["severity"] == "critical" for x in items)
        else "pass_with_conditions"
        if any(x["severity"] == "important" for x in items)
        else "pass"
    )
    if prepared.gate in {
        "milestone-review",
        "behavior-review",
        "complexity-review",
    }:
        from tests.tiering_review_helpers import review_content

        payload = review_content(prepared.gate, findings=items)
    else:
        payload = content(prepared.gate, findings=items, status=status)
    payload["summary"] = review["summary"]
    complete_fixture_coverage(payload, prepared.ac_ids, prepared.active_rules)
    if prepared.gate in SYNTHESIS_ROLES:
        from dataclasses import asdict

        complete_fixture_synthesis(
            payload,
            [
                ({"run_id": p.run_id}, asdict(p.result.content))
                for p in prepared.producers
            ],
        )
        if review["conflicts"]:
            rows = payload["synthesis"]["assessment_coverage"]
            per_run = {}
            for row in rows:
                per_run.setdefault(row["source"]["run_id"], row["source"])
            references = list(per_run.values())[:2]
            owner = next(f["id"] for f in items if f["classification"] == "report")
            payload["synthesis"]["conflicts"] = [
                {
                    "id": f"CON-{index + 1}",
                    "kind": "interpretation",
                    "owner_finding_id": owner,
                    "perspectives": [
                        {
                            "finding_ref": None,
                            "assessment_ref": ref,
                            "position": f"Fixture interpretation {i + 1}",
                        }
                        for i, ref in enumerate(references)
                    ],
                    "explanation": "The fixture requires an interpretation decision.",
                }
                for index in range(review["conflicts"])
            ]
    complete_fixture_rerun(payload, prepared.prior_reviews, prepared.review_decisions)
    complete_fixture_verdict(payload, bool(prepared.prior_reviews))
    decoded = decode_review_content(
        json.dumps(payload).encode(), prepared.output_contract
    )
    validate_review(decoded, prepared)
    return bind_review_result(decoded, prepared, feature)


def complete_fixture_coverage(payload, ac_ids, active_rules=()):
    """Expand literal assessments to this event fixture's declared inputs."""
    details = payload["details"]
    for field in (
        "ac_status",
        "ac_coverage",
        "ac_specificity",
        "ac_mapping",
        "ac_tests",
    ):
        if field in details:
            if not ac_ids:
                details[field] = []
                continue
            template = details[field][0]
            details[field] = [{**deepcopy(template), "ac_id": key} for key in ac_ids]
    if "rule_compliance" in details:
        details["rule_compliance"] = [
            {
                "rule": rule.id,
                "assessment": "unverifiable",
                "evidence": evidence(
                    kind="unavailable",
                    explanation="The event fixture does not evaluate authored rules.",
                ),
            }
            for rule in active_rules or ()
        ]
        if active_rules:
            payload["limitations"] = [
                note
                for note in payload["limitations"]
                if note["text"] != "The fixture plan supplies no active-rule section."
            ]


def complete_fixture_verdict(payload, has_prior):
    if "verdict" not in payload:
        return
    new = {r["finding_id"] for r in payload["regressions"]}
    payload["verdict"]["rerun_recommended"] = any(
        (
            f["severity"] == "critical"
            or (f["severity"] == "important" and f["classification"] == "implement")
        )
        and (not has_prior or f["id"] in new)
        for f in payload["findings"]
    )


def complete_fixture_synthesis(payload, sources):
    """Author exact source references for event tests, including derived findings."""
    result = synthesized(
        sources
        if payload["findings"]
        else [(run, {**body, "findings": []}) for run, body in sources],
        role=payload["role"],
        findings=payload["findings"],
    )
    summary = result["synthesis"]
    if not payload["findings"]:
        for run, body in sources:
            for item in body["findings"]:
                assert item["classification"] in {"implement", "ignore"}
                summary["finding_coverage"].append(
                    disposition(
                        finding_ref(run["run_id"], item["id"]),
                        action="addressed"
                        if item["classification"] == "implement"
                        else "settled",
                        reason="The fixture rechecks and settles this source item.",
                    )
                )
    covered = {r["output_finding_id"] for r in summary["finding_coverage"]}
    references = [r["source"] for r in summary["assessment_coverage"]]
    for item in payload["findings"]:
        if item["id"] not in covered:
            assert references, "A derived fixture finding needs supplied assessments"
            summary["derived_findings"].append(
                {
                    "finding_id": item["id"],
                    "assessment_refs": [references[0]],
                }
            )
    payload["synthesis"] = summary


def complete_scripted_prompt(text, prompt):
    """Attach invocation references to scripted W2 provider content only."""
    import re
    from types import SimpleNamespace

    from heddle.gate.results import decode_review_result

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("malformed fixture duplicates a JSON field")
            result[key] = value
        return result

    try:
        payload = json.loads(text, object_pairs_hook=unique)
    except ValueError:
        return text
    if not isinstance(payload, dict) or not all(
        isinstance(f, dict) and "id" in f for f in payload.get("findings", [])
    ):
        return text
    sources = []
    for run_id, block in re.findall(
        r"<producer-input [^>]*run_id=(\S+) [^>]*>\n(.*?)\n</producer-input>",
        prompt,
        re.S,
    ):
        result, _ = json.JSONDecoder().raw_decode(block)
        sources.append(({"run_id": run_id}, result["content"]))
    if "synthesis" in payload:
        complete_fixture_synthesis(payload, sources)
    priors = [
        SimpleNamespace(run_id=run_id, result=decode_review_result(block.encode()))
        for run_id, block in re.findall(
            r'<prior-review-artifact [^>]*run_id="([^"]+)">\n'
            r"(.*?)\n</prior-review-artifact>",
            prompt,
            re.S,
        )
    ]
    # The scripted host's literal owners identify how the next fixture output
    # accounts for previous REPORTs; production still validates these links.
    from tests.operational_model_helpers import read

    decisions = []
    feature = priors[-1].result.invocation.feature if priors else FEATURE
    path = Path.cwd() / f"plans/{feature}/state.yaml"
    if path.is_file():
        decisions = [
            SimpleNamespace(**d)
            for d in read(path)["decisions"]
            if d["kind"] == "finding"
        ]
    complete_fixture_rerun(payload, priors, decisions)
    complete_fixture_verdict(payload, bool(priors))
    return json.dumps(payload)


def authored_producer(context, lane, *, basis, artifact=None, historical=False):
    """Literal accepted producer for preparation/selection unit tests."""
    import hashlib
    from dataclasses import asdict

    from heddle.gate.results import (
        ReviewInvocation,
        ReviewResult,
        decode_review_content,
        historical_review_output_contract,
        review_output_contract,
        serialize_review_result,
    )
    from heddle.gate.types import ProducerCandidate
    from heddle.kernel.state import GateRun
    from tests.operational_model_helpers import new_run_id

    contract = (
        historical_review_output_contract(lane.gate)
        if historical
        else review_output_contract(lane.gate)
    )
    payload = content(lane.gate)
    complete_fixture_coverage(payload, context.spec_ac_ids)
    decoded = decode_review_content(json.dumps(payload).encode(), contract)
    result = ReviewResult(
        "heddle.review-result/v2",
        ReviewInvocation(
            feature=context.feature,
            gate=lane.gate,
            scope=lane.scope,
            execution=lane.exec_config,
            review_policy_id=None,
            prompt_version="authored-fixture-v1",
            effective_prompt_sha256="e" * 64,
            review_basis_hash=basis,
            input_hash="f" * 64,
            output_contract_version=contract.version,
            output_contract_sha256=contract.schema_sha256,
        ),
        decoded,
    )
    raw = serialize_review_result(result)
    digest = hashlib.sha256(raw).hexdigest()
    artifact = artifact or f"reviews/{lane.gate}.{lane.exec_config.cli}.review.json"
    workspace = context.workspace_dir or context.plan_path.parent
    path = workspace / artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    verdict = payload.get("verdict", {})
    run = GateRun(
        run_id=new_run_id(),
        report_findings=(),
        at="2026-09-07T10:00Z",
        cli=lane.exec_config.cli,
        artifact=artifact,
        input_hash="f" * 64,
        verdict={
            "status": verdict.get("status"),
            "rerun_recommended": verdict.get("rerun_recommended"),
        },
        findings={
            "by_severity": {"critical": 0, "important": 0, "minor": 0},
            "by_classification": {
                "implement": 0,
                "report": 0,
                "ignore": 0,
                "unknown": 0,
            },
            "total": 0,
            "contradictions": 0,
        },
        review_basis_hash=basis,
        execution_config=asdict(lane.exec_config),
        overridden_axes=(),
        artifact_sha256=digest,
        prompt_version=result.invocation.prompt_version,
        effective_prompt_sha256=result.invocation.effective_prompt_sha256,
    )
    return ProducerCandidate(
        gate=lane.gate,
        scope=lane.scope,
        exec_config=lane.exec_config,
        review_basis_hash=basis,
        artifact=artifact,
        artifact_identity=digest,
        status=verdict.get("status"),
        overridden_axes=frozenset(),
        accepted_run=run,
        content=raw.decode(),
        result=result,
    )


def prepare_render_producers(context):
    """Author the required accepted native producers for pure prompt tests."""
    from heddle.gate.registry import resolve_synthesis_declaration
    from heddle.gate.summary import resolve_exec_config
    from heddle.gate.types import GateExecutionConfig, ProducerLane

    context.producer_candidates = tuple(
        authored_producer(
            context,
            ProducerLane(
                role.name,
                "feature",
                GateExecutionConfig(**resolve_exec_config(cli, gate=role.name)),
            ),
            basis="b" * 64,
        )
        for declaration in context.gate_type.synthesis_inputs
        for role, cli, optional in (resolve_synthesis_declaration(declaration),)
        if not optional
    )


def complete_fixture_rerun(payload, priors, decisions):
    """Supply explicit history for scripted outputs in event/locking tests."""
    previous = [item for item in priors if item.result is not None]
    if not previous:
        return
    latest = previous[-1]
    owners = {(d.origin_run_id, d.origin_finding_id): d for d in decisions}
    outputs = {item["id"] for item in payload["findings"]}
    retained = set()
    for item in latest.result.content.findings:
        reference = {"run_id": latest.run_id, "finding_id": item.id}
        owner = owners.get((latest.run_id, item.id))
        disposition = {
            "source": reference,
            "output_finding_id": None,
            "decision_id": None,
            "decision_origin": None,
            "evidence": evidence(),
            "reason": "Scripted fixture history for the next event.",
        }
        if owner:
            disposition.update(
                disposition="settled"
                if owner.status == "resolved"
                else "awaiting_decision",
                decision_id=owner.id,
                decision_origin=reference,
            )
        elif item.id in outputs:
            disposition.update(disposition="retained", output_finding_id=item.id)
            retained.add(item.id)
        else:
            assert item.classification in {"implement", "ignore"}
            disposition["disposition"] = (
                "addressed" if item.classification == "implement" else "settled"
            )
        payload["prior_dispositions"].append(disposition)
    payload["regressions"] = [
        {"finding_id": item["id"], "evidence": evidence()}
        for item in payload["findings"]
        if item["id"] not in retained
    ]


def finding(
    identifier="SR-I1",
    *,
    classification="report",
    severity="important",
    title=None,
    recommendation=None,
    route=SPEC,
):
    item = {
        "id": identifier,
        "title": "Choose the owner of the declared value",
        "severity": severity,
        "classification": classification,
        "confidence": "high",
        "location": "src/example.py:1",
        "evidence": evidence(),
        "problem": "Two proposed owners would maintain the same declared value.",
        "impact": "Future edits could update one owner and leave the other stale.",
        "recommendation": "Keep src/example.py as the single declared owner.",
        "decision": {
            "context": "The approved behavior needs one declared value owner.",
            "options": [
                {
                    "name": "module",
                    "tradeoffs": "One local owner; changes require a code edit.",
                },
                {
                    "name": "configuration",
                    "tradeoffs": "User-editable, but needs a second loading boundary.",
                },
            ],
            "recommended": "module",
            "rationale": "The module already owns the value; no loader is required.",
            "deferred_impact": "Parallel owners can drift until the owner is selected.",
            "route": route,
        }
        if classification == "report"
        else None,
    }

    if title is not None:
        item["title"] = title
    if recommendation is not None:
        item["recommendation"] = recommendation
        if item["decision"] is not None:
            item["decision"]["options"][0]["name"] = recommendation
            item["decision"]["recommended"] = recommendation
    return item


def ac_status(status="pass"):
    return [
        {
            "ac_id": "AC-1",
            "status": status,
            "evidence": evidence(),
            "caveat": "Static value inspection does not prove unexecuted integration.",
        }
    ]


def dimensions(role):
    return [
        {"id": item, "assessment": "adequate", "evidence": evidence()}
        for item in DIMENSIONS[role]
    ]


def coverage(status="full"):
    return [
        {
            "ac_id": "AC-1",
            "coverage": status,
            "tests": ["tests/check.py:2"],
            "evidence": evidence(),
            "consequence": "The declared-value assertion detects a changed value.",
        }
    ]


def content(role="self-review", *, findings=(), status="pass", rerun=False):
    """Literal role meaning, authored independently of the production schema."""
    items = deepcopy(list(findings))
    base_role = role.removesuffix("-synthesis") if role != "review-synthesis" else role
    result = {
        "role": role,
        "summary": (
            "The declared value and its explicit acceptance route were reviewed."
        ),
        "findings": items,
        "observations": [
            {"text": "The implementation is one constant.", "evidence": evidence()}
        ],
        "limitations": [
            {
                "text": "No external integration was executed.",
                "evidence": evidence(
                    kind="unavailable", explanation="No live execution is supplied."
                ),
            },
            {
                "text": "The fixture plan supplies no active-rule section.",
                "evidence": evidence(
                    kind="absence", explanation="The captured plan has no active rules."
                ),
            },
        ],
        "enforcement_suggestions": [],
        "prior_dispositions": [],
        "regressions": [],
    }
    if base_role == "self-review":
        details = {
            "assessment": "issues-found"
            if any(x["severity"] != "minor" for x in items)
            else "clean",
            "ac_status": ac_status(),
            "rule_compliance": [],
        }
    elif base_role == "code-quality":
        details = {
            "assessment": "improvements-found"
            if any(x["severity"] != "minor" for x in items)
            else "clean",
            "dimensions": dimensions(base_role),
            "improvements": [
                {
                    "finding_id": x["id"],
                    "dimension": "abstraction-quality",
                    "current": "VALUE is declared in the module.",
                    "proposed": x["recommendation"],
                    "behavior_preserved": "The declared value remains 7.",
                }
                for x in items
            ],
        }
    elif base_role == "spec-review":
        details = {
            "dimensions": dimensions(base_role),
            "ac_specificity": [
                {
                    "ac_id": "AC-1",
                    "assessment": "concrete",
                    "reason": "The AC names a value and its observation.",
                }
            ],
            "scope": {
                "approved_mvp_present": True,
                "delta": [
                    {
                        "item": "Declared value",
                        "classification": "approved",
                        "authorization": "Fixture approved scope",
                        "reason": "AC-1 requires the value.",
                    }
                ],
                "sizing": "right-sized",
                "challenge": "One independently observable value is sufficient.",
                "integrated_acceptance": "Read the module and execute tests/check.py.",
                "increment_ladder": [],
                "confirmation": "The fixture scope matches its approved increment.",
            },
            "refinements": [],
        }
    elif base_role == "plan-review":
        details = {
            "dimensions": dimensions(base_role),
            "ac_mapping": [
                {
                    "ac_id": "AC-1",
                    "milestones": ["m1", "m2"],
                    "assessment": "covered",
                    "evidence": evidence(),
                }
            ],
            "dependencies": [
                {
                    "milestone": "m2",
                    "depends_on": ["m1"],
                    "reason": "Verification follows the value change.",
                }
            ],
            "verification": "Both milestones execute tests/check.py.",
        }
    elif base_role == "review-test-scaffolding":
        details = {
            "dimensions": dimensions(base_role),
            "ac_tests": [
                {
                    "ac_id": "AC-1",
                    "assessment": "covered",
                    "milestone_tests": ["tests/check.py:2"],
                    "acceptance_tests": ["tests/check.py:2"],
                    "live_tests": [],
                    "evidence": evidence(),
                }
            ],
            "infrastructure": [
                {"id": key, "assessment": "adequate", "evidence": evidence()}
                for key in ("commands", "prerequisites", "milestone-verification")
            ],
        }
    elif base_role == "review-correctness":
        details = {
            "ac_status": ac_status(),
            "invariants": [
                {"id": "value-owner", "assessment": "satisfied", "evidence": evidence()}
            ],
            "assumptions": [
                {
                    "id": "module-import",
                    "assessment": "supported",
                    "evidence": evidence(),
                }
            ],
        }
    elif base_role == "review-tests":
        details = {"ac_coverage": coverage()}
    elif base_role == "review-architecture":
        details = {
            "elements": [
                {"id": "value-owner", "assessment": "compliant", "evidence": evidence()}
            ],
            "structural_assessment": (
                "A module constant matches the declared ownership boundary."
            ),
        }
    elif base_role in {"review-robustness", "robustness-analysis"}:
        risks = [
            {
                "finding_id": x["id"],
                "likelihood": "low",
                "impact_level": "high",
                "current_handling": "The value has one module owner.",
                "dependency": "Python import",
                "mitigation": x["recommendation"],
            }
            for x in items
        ]
        if base_role == "review-robustness":
            details = {
                "assumptions": [
                    {
                        "id": "module-import",
                        "assessment": "supported",
                        "evidence": evidence(),
                        "impact": "An unavailable module prevents value use.",
                    }
                ],
                "edge_cases": [
                    {
                        "id": "changed-value",
                        "handling": "The assertion detects drift.",
                        "location": "tests/check.py:2",
                        "evidence": evidence(),
                    }
                ],
                "risks": risks,
            }
        else:
            details = {
                "assessment": (
                    "concerns-identified"
                    if any(item["severity"] == "critical" for item in items)
                    else "adequate"
                    if any(item["severity"] == "important" for item in items)
                    else "robust"
                ),
                "categories": [
                    {"id": item, "assessment": "adequate", "evidence": evidence()}
                    for item in FAILURE_CATEGORIES
                ],
                "risks": risks,
            }
    elif base_role == "peer-review-sequential":
        details = {
            "assessment": (
                "The declared value is implemented with visible verification limits."
            ),
            "ac_status": ac_status(),
            "ac_coverage": coverage(),
            "dimensions": dimensions(base_role),
            "plan_context": "Milestone m1 implements the value; m2 verifies it.",
        }
    elif base_role == "review-synthesis":
        details = {
            "assessment": "The specialist assessments agree on the inspected value.",
            "ac_status": ac_status(),
            "ac_coverage": coverage(),
        }
    else:
        raise AssertionError(f"Fixture role must be explicit: {role}")
    result["details"] = details
    if role in VERDICT_ROLES:
        result["verdict"] = {
            "status": status,
            "rerun_recommended": rerun,
            "reason": "No unresolved material defect remains."
            if not rerun
            else "The declared material finding needs a disposition pass.",
        }
    if role in SYNTHESIS_ROLES:
        result["synthesis"] = {
            "producer_runs": [],
            "finding_coverage": [],
            "assessment_coverage": [],
            "derived_findings": [],
            "conflicts": [],
            "severity_adjustments": [],
        }
    return result


def json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def host_for_review(tmp_path, *, stage="implement", tier=2, name="host"):
    value = document(stage=stage, tier=tier)
    if stage == "implement":
        value["milestones"][0]["status"] = "current"
    return fresh_host(tmp_path, state=value, name=name)


def prepare_historical(host, *, role="self-review", lane="claude"):
    """Explicit v1 semantic witness; retired roles never enter the live registry."""
    from dataclasses import replace

    from heddle.gate import entry
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.results import historical_review_output_contract
    from heddle.gate.types import GateInvocationOverrides
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    equivalent = {
        "self-review": "milestone-review",
        "code-quality": "milestone-review",
        "review-correctness": "peer-review-sequential",
        "review-tests": "peer-review-sequential",
    }.get(role, role)
    live = GATES.get(equivalent)
    assert live is not None
    gate = replace(
        live,
        name=role,
        output_contract=historical_review_output_contract(
            role, version="heddle.review-content/v1"
        ),
    )
    config = load_project_config(host)
    snapshot = resolve_snapshot(config, FEATURE)
    ctx = entry.build_gate_context(snapshot, config, gate, cli=lane)
    invocation = resolve_gate_execution(
        gate,
        GateInvocationOverrides(
            cli=lane, model="fixture-model", reasoning_effort="high"
        ),
    )
    return ctx, entry.prepare_gate_run(ctx, gate_type=gate, invocation=invocation)


def native_providers(
    monkeypatch,
    tmp_path,
    payload,
    *,
    mutation=None,
    exit_code=0,
    termination=None,
    before_return=None,
):
    """Replace only the true external monitored process; capture actual channels."""
    from heddle.gate.runners import claude, codex
    from heddle.io.process import MonitorResult, Termination

    binaries = tmp_path / "native-provider-path"
    binaries.mkdir(exist_ok=True)
    for lane in ("claude", "codex"):
        executable = binaries / lane
        executable.write_text("#!/bin/sh\nexit 97\n")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    calls = []

    def invoke(lane, command, raw_out, config, *, stdin_source):
        stdin = stdin_source.read_bytes()
        value = (
            payload(lane, stdin.decode(), tuple(command))
            if callable(payload)
            else deepcopy(payload)
        )
        raw = value if isinstance(value, bytes) else json_bytes(value)
        system = (
            command[command.index("--append-system-prompt") + 1]
            if lane == "claude"
            else None
        )
        schema = None
        schema_path = None
        if lane == "claude" and "--json-schema" in command:
            schema = command[command.index("--json-schema") + 1].encode()
        elif lane == "codex" and "--output-schema" in command:
            schema_path = Path(command[command.index("--output-schema") + 1])
            schema = schema_path.read_bytes()
        calls.append(
            {
                "lane": lane,
                "stdin": stdin,
                "system": system,
                "command": tuple(command),
                "schema": schema,
                "schema_path": schema_path,
                "config": config,
                "raw_out": raw_out,
            }
        )
        if lane == "claude":
            # Build bytes directly: duplicate keys inside content must survive.
            terminal = (
                b'{"type":"result","subtype":"success","is_error":false,'
                b'"result":"DECOY: this prose must never become review authority",'
                b'"structured_output":' + raw.strip() + b"}"
            )
            events = [terminal]
        else:
            output = Path(command[command.index("-o") + 1])
            if mutation != "missing-final":
                output.write_bytes(b"" if mutation == "empty-final" else raw)
            terminal = b'{"type":"turn.completed","usage":{}}'
            events = [
                json_bytes(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": raw.decode("utf-8", errors="replace"),
                        },
                    }
                ).strip(),
                terminal,
            ]
        if mutation == "missing-terminal":
            events = events[:-1]
        elif mutation == "duplicate-terminal":
            events.append(terminal)
        elif mutation == "conflicting-terminal":
            events.append(
                b'{"type":"result","subtype":"error_during_execution","is_error":true}'
                if lane == "claude"
                else b'{"type":"turn.failed","error":{"message":"failed"}}'
            )
        elif mutation == "truncated-terminal":
            events[-1] = terminal[:-2]
        elif lane == "claude" and mutation == "missing-structured":
            events[-1] = (
                b'{"type":"result","subtype":"success","is_error":false,'
                b'"result":"plausible decoy"}'
            )
        elif lane == "claude" and mutation == "wrong-structured-type":
            events[-1] = (
                terminal[: terminal.index(b'"structured_output":')]
                + b'"structured_output":[]}'
            )
        elif lane == "claude" and mutation == "is-error":
            events[-1] = terminal.replace(b'"is_error":false', b'"is_error":true')
        elif lane == "claude" and mutation == "duplicate-envelope-key":
            events[-1] = terminal.replace(
                b'"is_error":false', b'"is_error":true,"is_error":false'
            )
        raw_out.write_bytes(b"\n".join(events) + b"\n")
        if before_return:
            before_return(lane, calls[-1])
        return MonitorResult(
            exit_code=exit_code,
            termination=termination or Termination.COMPLETED,
            saw_stream_activity=True,
            inactivity_seconds=0,
            inactivity_marker="",
            checkpoints=[],
        )

    def claude_call(command, raw_out, config, *, stdin_source=None, **_kwargs):
        return invoke("claude", command, raw_out, config, stdin_source=stdin_source)

    def codex_call(command, raw_out, config, *, stdin_source=None, **_kwargs):
        return invoke("codex", command, raw_out, config, stdin_source=stdin_source)

    monkeypatch.setattr(claude, "run_monitored", claude_call)
    monkeypatch.setattr(codex, "run_monitored", codex_call)
    return calls


def runs(path):
    from tests.operational_model_helpers import read

    return [r for gate in read(path)["gates"] for r in gate["runs"]]


def canonical_result(path, run):
    output = path.parent / run["artifact"]
    assert output.name.endswith(".review.json"), (
        f"FAIL W3 AC-4: accepted artifact must be structured JSON, got {output.name}"
    )
    return output, json.loads(output.read_bytes())


def assessment_keys(payload):
    """Literal fixture assessment inventory, independent of production readers."""
    details = payload["details"]
    keys = []
    for field, kind, identity in (
        ("dimensions", "dimension", "id"),
        ("ac_specificity", "ac-specificity", "ac_id"),
        ("ac_mapping", "ac-mapping", "ac_id"),
        ("ac_tests", "ac-tests", "ac_id"),
        ("ac_status", "ac-status", "ac_id"),
        ("ac_coverage", "ac-coverage", "ac_id"),
        ("infrastructure", "infrastructure", "id"),
        ("elements", "architecture-element", "id"),
        ("assumptions", "assumption", "id"),
        ("invariants", "invariant", "id"),
        ("edge_cases", "edge-case", "id"),
        ("categories", "failure-category", "id"),
        ("dependencies", "dependency-order", "milestone"),
        ("risks", "risk", "finding_id"),
        ("rule_compliance", "rule-compliance", "rule"),
    ):
        keys.extend((kind, row[identity]) for row in details.get(field, []))
    if "scope" in details:
        keys.append(("scope", "approved-mvp"))
    if "verification" in details:
        keys.append(("verification", "declared-commands"))
    return keys


def finding_ref(run_id, finding_id):
    return {"run_id": run_id, "finding_id": finding_id}


def assessment_ref(run_id, kind, identifier):
    return {"run_id": run_id, "kind": kind, "assessment_id": identifier}


def disposition(
    source,
    *,
    action="retained",
    target=None,
    decision_id=None,
    decision_origin=None,
    reason="The source finding is explicitly retained.",
):
    return {
        "source": deepcopy(source),
        "disposition": action,
        "output_finding_id": target,
        "decision_id": decision_id,
        "decision_origin": deepcopy(decision_origin),
        "evidence": evidence(),
        "reason": reason,
    }


def synthesized(
    sources, *, role="spec-review-synthesis", findings=(), status="pass", rerun=False
):
    """Sources are (accepted run dict, independent content dict) pairs."""
    result = content(role, findings=findings, status=status, rerun=rerun)
    summary = result["synthesis"]
    summary["producer_runs"] = [run["run_id"] for run, _ in sources]
    for run, payload in sources:
        for item in payload["findings"]:
            assert findings, "Fixture source findings need an explicit output target"
            summary["finding_coverage"].append(
                disposition(
                    finding_ref(run["run_id"], item["id"]), target=findings[0]["id"]
                )
            )
        targets = set(assessment_keys(result))
        for kind, identifier in assessment_keys(payload):
            summary["assessment_coverage"].append(
                {
                    "source": assessment_ref(run["run_id"], kind, identifier),
                    "target": f"{kind}/{identifier}"
                    if (kind, identifier) in targets
                    else "summary",
                    "reason": (
                        "The source assessment is retained "
                        "with its stated evidence limits."
                    ),
                }
            )
    return result
