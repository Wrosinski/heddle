"""Literal retained-v6/v2 evidence, independent of the current launch catalog."""

import hashlib
import json
from collections import Counter

import yaml

from tests.operational_model_helpers import (
    SPEC,
    STAMP,
    decision,
    document,
    fresh_host,
    new_run_id,
    run,
)
from tests.structured_review_helpers import content

LEGACY_HASHES = {
    "self-review": "0400bc7447f59b0626695899306ba2a0c9db725277a5c5ffc884a61104808690",
    "spec-review": "2306dc151aaeeb1f86b7033cdcd389ad58b16f4ba3c9579b182da477bd72832f",
    "review-correctness": (
        "cccf9e856eea8bd0aa76ef59a9351af75b576db097a905857fb493915a9ab741"
    ),
    "spec-review-synthesis": (
        "22c63ef746c205c9366976494f01c0ab6c2e3d0c775cbb51341d38726ea3c988"
    ),
}


def legacy_host(tmp_path, monkeypatch, *, stage="spec-review", accepted=False):
    value = document(stage=stage)
    if accepted:
        value.update(
            stage="complete",
            authorizations=[{"through": "complete", "source": "user", "at": STAMP}],
        )
        for milestone in value["milestones"]:
            milestone["status"] = "done"
        paths = sorted([SPEC, "src/example.py", "tests/check.py"])
        value["commands"] = {
            "acceptance_test": "python3 tests/check.py",
            "smoke_test": "python3 tests/check.py",
        }
        value["verifications"] = [
            {
                "scope": scope,
                "command": "python3 tests/check.py",
                "exit_code": 0,
                "at": STAMP,
                "log": f"verification/{scope}.log",
                "provenance": {
                    "schema": "heddle.verification-source/v2",
                    "source_set_kind": "feature-owned-union",
                    "declaration_paths": paths,
                    "paths": paths,
                    "before_sha256": "c" * 64,
                    "after_sha256": "c" * 64,
                    "git_head": None,
                    "differs_from_head": None,
                },
            }
            for scope in ("acceptance", "smoke")
        ]
        value["completion"] = {
            "accepted_at": STAMP,
            "authorization_index": 0,
            "verification_indexes": [0, 1],
            "review_run_ids": [],
            "accepted_smoke_decision_id": None,
            "spec_stamp": {
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
                "executable": False,
            },
            "close_suite": None,
        }
    host, path = fresh_host(tmp_path, state=value)
    monkeypatch.chdir(host)
    return host, path


def legacy_report(
    path,
    *,
    role="spec-review",
    findings=(),
    cli="codex",
    payload=None,
    policy_id=None,
    scope="feature",
    model=None,
):
    """Create exact old-format evidence, never relabel a current report as old."""
    identifier = new_run_id()
    findings = tuple(findings)
    body = (
        payload
        if payload is not None
        else content(
            role,
            findings=findings,
            status="pass_with_conditions" if findings else "pass",
        )
    )
    execution = {
        "cli": cli,
        "model": "gpt-6-astra" if cli == "codex" else "claude-opus-5",
        "reasoning_effort": "high",
        "sandbox": "danger-full-access" if cli == "codex" else "read-only-tools",
    }
    if policy_id and cli == "claude":
        execution["model"] = "claude-fable-5-1"
    if model is not None:
        execution["model"] = model
    invocation = {
        "feature": yaml.safe_load(path.read_text())["feature"],
        "gate": role,
        "scope": scope,
        "execution": execution,
        "review_policy_id": policy_id,
        "prompt_version": "retained-original-v1",
        "effective_prompt_sha256": "e" * 64,
        "review_basis_hash": "c" * 64,
        "input_hash": hashlib.sha256(identifier.encode()).hexdigest(),
        "output_contract_version": "heddle.review-content/v1",
        "output_contract_sha256": LEGACY_HASHES[role],
    }
    raw = (
        json.dumps(
            {
                "schema": "heddle.review-result/v2",
                "invocation": invocation,
                "content": body,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    artifact = path.parent / "reviews" / f"{identifier}.review.json"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_bytes(raw)
    reports = [
        {"finding_id": row["id"], "title": row["title"], "severity": row["severity"]}
        for row in findings
        if row["classification"] == "report"
    ]
    event = run(
        identifier,
        cli=cli,
        reports=reports,
        status="pass_with_conditions" if findings else "pass",
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        input_hash=invocation["input_hash"],
        execution_config=execution,
        **({"review_policy_id": policy_id} if policy_id is not None else {}),
        review_basis_hash=invocation["review_basis_hash"],
        prompt_version=invocation["prompt_version"],
        findings={
            "by_severity": {
                key: Counter(row["severity"] for row in findings)[key]
                for key in ("critical", "important", "minor")
            },
            "by_classification": {
                key: Counter(row["classification"] for row in findings)[key]
                for key in ("implement", "report", "ignore", "unknown")
            },
            "total": len(findings),
            "contradictions": 0,
        },
    )
    value = yaml.safe_load(path.read_text())
    if role in {"review-correctness", "self-review"}:
        event["verdict"] = {"status": None, "rerun_recommended": None}
    existing = next(
        (
            gate
            for gate in value["gates"]
            if (gate["gate"], gate["scope"]) == (role, scope)
        ),
        None,
    )
    if existing is None:
        existing = {"gate": role, "scope": scope, "runs": []}
        value["gates"].append(existing)
    existing["runs"].append(event)
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    return identifier, artifact


def paired_legacy_round(path, *, findings=(), coverage_gap=True):
    from tests.structured_review_helpers import (
        complete_fixture_synthesis,
        disposition,
        finding_ref,
    )

    sources = []
    identifiers = []
    reports = []
    retained = []
    for cli in ("claude", "codex"):
        lane_findings = tuple(findings) if cli == "claude" else ()
        producer = content(
            "spec-review",
            findings=lane_findings,
            status="pass_with_conditions" if lane_findings else "pass",
        )
        if not coverage_gap:
            producer["limitations"] = []
        identifier, artifact = legacy_report(
            path, cli=cli, findings=lane_findings, payload=producer
        )
        for row in lane_findings:
            if row["classification"] == "report":
                reports.append(row)
                retained.append(
                    disposition(
                        finding_ref(identifier, row["id"]),
                        target=row["id"],
                        reason="The historical synthesis retains the source REPORT.",
                    )
                )
        identifiers.append(identifier)
        sources.append(
            ({"run_id": identifier}, json.loads(artifact.read_text())["content"])
        )
    payload = content("spec-review-synthesis")
    if not coverage_gap:
        payload["limitations"] = []
    # In v6, independent producers defer REPORT ownership to the synthesis.
    # Preserve their bytes and author explicit retained-source coverage below.
    resolved_sources = [
        (
            run,
            {
                **body,
                "findings": [
                    row for row in body["findings"] if row["classification"] != "report"
                ],
            },
        )
        for run, body in sources
    ]
    complete_fixture_synthesis(payload, resolved_sources)
    payload["findings"] = reports
    payload["synthesis"]["finding_coverage"].extend(retained)
    if reports:
        payload["verdict"]["status"] = "pass_with_conditions"
    synthesis_id, _artifact = legacy_report(
        path,
        role="spec-review-synthesis",
        cli="claude",
        findings=reports,
        payload=payload,
    )
    value = yaml.safe_load(path.read_text())
    for row in reports:
        value["decisions"].append(
            decision(
                id=f"D{len(value['decisions']) + 1}",
                title=row["title"],
                origin_run_id=synthesis_id,
                origin_finding_id=row["id"],
            )
        )
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    return tuple(identifiers), synthesis_id
