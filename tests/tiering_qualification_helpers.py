"""
Validate retained qualification structure; semantic judgments stay inspectable.

This validates author-trusted execution records, not their authenticity. A grade
must point into real decoded outputs; passing this check never replaces reading
the case, rubric and cited evidence during promotion review.
"""

import gzip
import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import PurePosixPath

from heddle.gate.results import decode_review_result, review_output_contract

EXPECTATIONS = {
    "spec-valid": ("adequate-contract", "no-implementation-proxies"),
    "spec-commitment-gap": ("detect-publication-gap", "no-implementation-proxies"),
    "plan-valid": ("adequate-integration", "no-copy-or-unrun-proof-objections"),
    "plan-proof-gap": (
        "retain-original-finding",
        "require-current-proof",
        "no-copy-or-size-proxies",
    ),
}
EXECUTION_KEYS = {
    "cases",
    "rubric",
    "grader",
    "gate",
    "cli",
    "model",
    "reasoning_effort",
    "sandbox",
    "runtime_revision",
    "iteration_cap",
    "configuration",
}
CONFIGURATION_KEYS = {
    "context_builders",
    "tool_permissions",
    "prompt_caps",
    "rerun_state",
}


def digest(value):
    assert isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), value
    return value


def retained(root, reference):
    """Return exact content; compressed refs bind container and content separately."""
    compressed = "encoding" in reference
    assert set(reference) == (
        {"path", "sha256", "encoding", "container_sha256"}
        if compressed
        else {"path", "sha256"}
    )
    path = PurePosixPath(reference["path"])
    assert not path.is_absolute() and ".." not in path.parts
    target = root / path
    assert target.is_file(), f"FAIL qualification: missing retained {path}"
    raw = target.read_bytes()
    if compressed:
        assert reference["encoding"] == "gzip"
        assert hashlib.sha256(raw).hexdigest() == digest(
            reference["container_sha256"]
        ), path
        raw = gzip.decompress(raw)
    assert hashlib.sha256(raw).hexdigest() == digest(reference["sha256"]), path
    return raw


def _resolved(value):
    assert isinstance(value, str) and value.strip()
    assert value not in {"NOT RUN", "default", "unknown"} and "<" not in value


def _execution(value, case_id):
    assert set(value) == EXECUTION_KEYS
    assert value["cases"] == [case_id]
    for key in (
        "gate",
        "cli",
        "model",
        "reasoning_effort",
        "sandbox",
        "runtime_revision",
    ):
        _resolved(value[key])
    assert value["gate"] == (
        "spec-review" if case_id.startswith("spec-") else "plan-review"
    )
    assert value["cli"] in {"codex", "claude"}
    assert type(value["iteration_cap"]) is int and value["iteration_cap"] > 0
    assert re.fullmatch(r"[0-9a-f]{40,64}", value["runtime_revision"])
    assert CONFIGURATION_KEYS <= value["configuration"].keys()
    for key in CONFIGURATION_KEYS:
        assert value["configuration"][key] is not None
    assert isinstance(value["configuration"]["context_builders"], list)
    assert isinstance(value["configuration"]["tool_permissions"], list)
    assert isinstance(value["configuration"]["prompt_caps"], dict)
    assert isinstance(value["configuration"]["rerun_state"], dict)
    assert set(value["grader"]) == {"id", "version"}
    for field in value["grader"].values():
        _resolved(field)


def _pointer(document, pointer):
    assert isinstance(pointer, str) and pointer.startswith("/")
    value = document
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    assert value is not None
    return value


def validate_qualification(evidence, *, case_ids=None, require_promotion=True):
    path = evidence / "qualification.json"
    assert path.is_file(), "FAIL AC-13: bounded prompt comparisons have NOT RUN"
    proof = json.loads(path.read_text())
    assert proof["schema"] == "heddle.tiering-qualification/v1"
    assert proof["promotion"] in (
        {"PROMOTE", "REMOVE"}
        if require_promotion
        else {"PROMOTE", "REMOVE", "WITHHELD"}
    )
    _resolved(proof["authority"])
    assert len(proof["cases"]) == len({row["id"] for row in proof["cases"]})
    cases = {row["id"]: row for row in proof["cases"]}
    selected = tuple(EXPECTATIONS) if case_ids is None else tuple(case_ids)
    assert selected and set(selected) <= EXPECTATIONS.keys()
    assert set(selected) <= cases.keys()
    for case_id in selected:
        expected = EXPECTATIONS[case_id]
        case = cases[case_id]
        execution = case["frozen_execution"]
        _execution(execution, case_id)
        rubric = execution["rubric"]
        assert set(rubric) in ({"path", "sha256"}, {"id", "version", "path", "sha256"})
        if "id" in rubric:
            _resolved(rubric["id"])
            assert rubric["version"] == rubric["sha256"]
        retained(evidence, {key: rubric[key] for key in ("path", "sha256")})
        retained(evidence, case["fixture"])
        assert case["candidate_expectations_passed"] is True
        assert case["misses"] == [] and case["false_positives"] == []
        assert case["attribution"] == "prompt"
        outputs = {}
        output_hashes = {}
        for arm in ("baseline", "candidate"):
            row = case[arm]
            assert row["output_contract"] == "PASS"
            assert row["execution"] == execution
            raw = retained(evidence, row["output"])
            decoded = decode_review_result(raw)
            invocation = decoded.invocation
            contract = review_output_contract(execution["gate"])
            assert invocation.gate == decoded.content.role == execution["gate"]
            assert invocation.output_contract_version == contract.version
            assert invocation.output_contract_sha256 == contract.schema_sha256
            assert asdict(invocation.execution) == {
                key: execution[key]
                for key in ("cli", "model", "reasoning_effort", "sandbox")
            }
            captured = json.loads(retained(evidence, row["execution_record"]))
            assert captured["status"] == "completed" and captured["exit_code"] == 0
            assert captured["execution"] == execution
            assert captured["case_id"] == case_id
            assert captured["fixture_sha256"] == case["fixture"]["sha256"]
            assert captured["output_sha256"] == row["output"]["sha256"]
            assert 0 < captured["iterations"] <= execution["iteration_cap"]
            for field in ("cost", "latency"):
                assert type(captured[field]["value"]) in {int, float}
                assert math.isfinite(captured[field]["value"])
                assert captured[field]["value"] >= 0
            assert captured["latency"]["unit"] == "seconds"
            assert captured["cost"]["unit"] in {"USD", "provider-reported tokens"}
            if captured["cost"]["unit"] == "provider-reported tokens":
                # Subscription usage is observable; its invoice price is not.
                # Do not turn unobserved billing into a fabricated zero USD.
                assert captured["actual_billed_usd"] is None
                _resolved(captured["billing_status"])
            retained(evidence, row["effective_prompt"])
            for field in ("effective_prompt_sha256", "review_basis_hash", "input_hash"):
                assert digest(captured[field]) == digest(getattr(invocation, field))
            assert (
                captured["effective_prompt_sha256"] == row["effective_prompt"]["sha256"]
            )
            outputs[arm] = json.loads(raw)
            output_hashes[arm] = row["output"]["sha256"]
        grade = json.loads(retained(evidence, case["grade_evidence"]))
        assert grade["schema"] == "heddle.case-grade/v1" and grade["case_id"] == case_id
        assert grade["rubric"] == execution["rubric"]
        assert grade["grader"] == execution["grader"]
        assert grade["fixture_sha256"] == case["fixture"]["sha256"]
        assert grade["output_sha256"] == output_hashes
        assert grade["misses"] == [] and grade["false_positives"] == []
        assert {row["id"] for row in grade["expectations"]} == set(expected)
        assert len(grade["expectations"]) == len(expected)
        for expectation in grade["expectations"]:
            assert expectation["candidate_passed"] is True
            assert type(expectation["baseline_passed"]) is bool
            _resolved(expectation["reason"])
            assert {ref["arm"] for ref in expectation["evidence"]} == {
                "baseline",
                "candidate",
            }
            for ref in expectation["evidence"]:
                assert ref["kind"] in {"signal", "absence-check"}
                _pointer(outputs[ref["arm"]], ref["pointer"])
                _resolved(ref["explanation"])
    return proof
