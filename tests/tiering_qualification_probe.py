"""
Hermetic structural counterexamples for the scaffold's evidence checker.

Run with python -m tests.tiering_qualification_probe. Every record here is
synthetic and lives in a disposable directory. This probes the checker, never
claims model execution or writes the feature's real qualification.json.
"""

import gzip
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from heddle.gate.results import review_output_contract
from tests.tiering_qualification_helpers import EXPECTATIONS, validate_qualification
from tests.tiering_review_helpers import review_content


def fixture(root, *, reported_tokens=False, compressed_fixtures=False):
    def save(name, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        (root / name).write_bytes(raw)
        return {"path": name, "sha256": sha256(raw).hexdigest()}

    rubric = save("rubric.md", b"Synthetic structural-probe rubric, not a model grade.")
    if reported_tokens:
        rubric.update(id="synthetic-structure-probe", version=rubric["sha256"])
    proof = {
        "schema": "heddle.tiering-qualification/v1",
        "promotion": "PROMOTE",
        "authority": "Synthetic checker fixture only",
        "cases": [],
    }
    for case_id, expected in EXPECTATIONS.items():
        role = "spec-review" if case_id.startswith("spec-") else "plan-review"
        contract = review_output_contract(role)
        execution = {
            "cases": [case_id],
            "rubric": rubric,
            "grader": {"id": "synthetic-structure-probe", "version": "1"},
            "gate": role,
            "cli": "codex",
            "model": "gpt-6-astra",
            "reasoning_effort": "high",
            "sandbox": "danger-full-access",
            "runtime_revision": "a" * 40,
            "iteration_cap": 1,
            "configuration": {
                "context_builders": [],
                "tool_permissions": [],
                "prompt_caps": {},
                "rerun_state": {},
            },
        }
        case = {
            "id": case_id,
            "frozen_execution": execution,
            "fixture": save(case_id + ".md", b"Synthetic input, not the release case."),
            "candidate_expectations_passed": True,
            "misses": [],
            "false_positives": [],
            "attribution": "prompt",
        }
        if compressed_fixtures:
            reference = case["fixture"]
            compressed = save(
                case_id + ".md.gz",
                gzip.compress((root / reference["path"]).read_bytes(), mtime=0),
            )
            reference.update(
                path=compressed["path"],
                encoding="gzip",
                container_sha256=compressed["sha256"],
            )
        for arm in ("baseline", "candidate"):
            stem = case_id + "." + arm
            prompt = save(stem + ".prompt.md", b"Synthetic prompt.")
            invocation = {
                "feature": "synthetic-probe",
                "gate": role,
                "scope": "feature",
                "execution": {
                    k: execution[k]
                    for k in ("cli", "model", "reasoning_effort", "sandbox")
                },
                "review_policy_id": None,
                "prompt_version": "synthetic-probe-v1",
                "effective_prompt_sha256": prompt["sha256"],
                "review_basis_hash": "b" * 64,
                "input_hash": "c" * 64,
                "output_contract_version": contract.version,
                "output_contract_sha256": contract.schema_sha256,
                "assignment_id": "synthetic-document-review",
                "round_number": 1,
                "reviewer_slot": "primary",
            }
            output = save(
                stem + ".review.json",
                {
                    "schema": "heddle.review-result/v3",
                    "invocation": invocation,
                    "content": review_content(role),
                },
            )
            captured = {
                "status": "completed",
                "exit_code": 0,
                "execution": execution,
                "case_id": case_id,
                "fixture_sha256": case["fixture"]["sha256"],
                "output_sha256": output["sha256"],
                "iterations": 1,
                "cost": {"value": 0, "unit": "USD"},
                "latency": {"value": 0, "unit": "seconds"},
                **{
                    k: invocation[k]
                    for k in (
                        "effective_prompt_sha256",
                        "review_basis_hash",
                        "input_hash",
                    )
                },
            }
            if reported_tokens:
                captured.update(
                    cost={"value": 123, "unit": "provider-reported tokens"},
                    actual_billed_usd=None,
                    billing_status="Synthetic subscription usage; billing not observed",
                )
            case[arm] = {
                "output_contract": "PASS",
                "execution": execution,
                "output": output,
                "effective_prompt": prompt,
                "execution_record": save(stem + ".execution.json", captured),
            }
        grade = {
            "schema": "heddle.case-grade/v1",
            "case_id": case_id,
            "rubric": rubric,
            "grader": execution["grader"],
            "fixture_sha256": case["fixture"]["sha256"],
            "output_sha256": {
                arm: case[arm]["output"]["sha256"] for arm in ("baseline", "candidate")
            },
            "misses": [],
            "false_positives": [],
            "expectations": [
                {
                    "id": name,
                    "candidate_passed": True,
                    "baseline_passed": True,
                    "reason": "Synthetic structural fixture, no semantic judgment.",
                    "evidence": [
                        {
                            "arm": arm,
                            "kind": "absence-check",
                            "pointer": "/content/summary",
                            "explanation": "Synthetic pointer resolution check.",
                        }
                        for arm in ("baseline", "candidate")
                    ],
                }
                for name in expected
            ],
        }
        case["grade_evidence"] = save(case_id + ".grade.json", grade)
        proof["cases"].append(case)
    return proof


def main():
    rejected = []
    names = (
        "empty-tuple",
        "missing-grader",
        "unresolved-runtime",
        "tuple-drift",
        "arbitrary-output",
        "failed-execution",
        "missing-capture",
        "hash-drift",
        "missing-grade",
        "bad-pointer",
        "missing-expectation",
        "missing-identity",
        "nonfinite-cost",
        "nonfinite-latency",
        "unlabelled-usage",
        "invented-billing",
        "rubric-version-drift",
        "gzip-container-drift",
        "gzip-content-drift",
        "gzip-missing-container",
        "gzip-wrong-encoding",
        "gzip-not-compressed",
    )
    valid = {None, "valid-reported-tokens", "valid-gzip-fixture"}
    for damage in (None, "valid-reported-tokens", "valid-gzip-fixture", *names):
        with TemporaryDirectory(prefix="tiering-qualification-probe-") as temporary:
            root = Path(temporary)
            proof = deepcopy(
                fixture(
                    root,
                    reported_tokens=damage is not None,
                    compressed_fixtures=damage == "valid-gzip-fixture"
                    or (isinstance(damage, str) and damage.startswith("gzip-")),
                )
            )
            case = proof["cases"][0]

            def edit(reference, mutate, root=root):
                target = root / reference["path"]
                value = json.loads(target.read_text())
                mutate(value)
                raw = json.dumps(value).encode()
                target.write_bytes(raw)
                reference["sha256"] = sha256(raw).hexdigest()

            if damage == "empty-tuple":
                case["frozen_execution"] = case["baseline"]["execution"] = case[
                    "candidate"
                ]["execution"] = {}
            elif damage == "missing-grader":
                case["frozen_execution"].pop("grader")
            elif damage == "unresolved-runtime":
                case["frozen_execution"]["runtime_revision"] = "NOT RUN"
            elif damage == "tuple-drift":
                case["candidate"]["execution"] = {
                    **case["frozen_execution"],
                    "model": "different-model",
                }
            elif damage == "arbitrary-output":
                ref = case["candidate"]["output"]
                (root / ref["path"]).write_bytes(b"arbitrary non-review output")
                ref["sha256"] = sha256(b"arbitrary non-review output").hexdigest()
            elif damage == "failed-execution":
                edit(
                    case["candidate"]["execution_record"],
                    lambda v: v.update(exit_code=1),
                )
            elif damage == "missing-capture":
                case["candidate"].pop("execution_record")
            elif damage == "hash-drift":
                case["fixture"]["sha256"] = "f" * 64
            elif damage == "missing-grade":
                (root / case["grade_evidence"]["path"]).unlink()
            elif damage == "bad-pointer":
                edit(
                    case["grade_evidence"],
                    lambda v: v["expectations"][0]["evidence"][0].update(
                        pointer="/content/not-a-field"
                    ),
                )
            elif damage == "missing-expectation":
                edit(case["grade_evidence"], lambda v: v["expectations"].pop())
            elif damage == "missing-identity":
                edit(
                    case["candidate"]["execution_record"], lambda v: v.pop("input_hash")
                )
            elif damage in {"nonfinite-cost", "nonfinite-latency"}:
                field = damage.removeprefix("nonfinite-")
                edit(
                    case["candidate"]["execution_record"],
                    lambda v, field=field: v[field].update(value=float("inf")),
                )
            elif damage == "unlabelled-usage":
                edit(
                    case["candidate"]["execution_record"],
                    lambda v: v.pop("billing_status"),
                )
            elif damage == "invented-billing":
                edit(
                    case["candidate"]["execution_record"],
                    lambda v: v.update(actual_billed_usd=0),
                )
            elif damage == "rubric-version-drift":
                case["frozen_execution"]["rubric"]["version"] = "f" * 64
            elif damage == "gzip-container-drift":
                case["fixture"]["container_sha256"] = "f" * 64
            elif damage == "gzip-content-drift":
                # Valid container and hash, but one newline changes the fixed case.
                reference = case["fixture"]
                target = root / reference["path"]
                changed = gzip.compress(gzip.decompress(target.read_bytes()) + b"\n")
                target.write_bytes(changed)
                reference["container_sha256"] = sha256(changed).hexdigest()
            elif damage == "gzip-missing-container":
                case["fixture"].pop("container_sha256")
            elif damage == "gzip-wrong-encoding":
                case["fixture"]["encoding"] = "plain"
            elif damage == "gzip-not-compressed":
                reference = case["fixture"]
                raw = b"Not compressed, despite the claimed encoding."
                (root / reference["path"]).write_bytes(raw)
                reference["container_sha256"] = sha256(raw).hexdigest()
            (root / "qualification.json").write_text(json.dumps(proof))
            try:
                validate_qualification(root)
            except (AssertionError, KeyError, ValueError, OSError):
                if damage in valid:
                    raise
                rejected.append(damage)
            else:
                assert damage in valid, f"FAIL checker admitted {damage}"
    assert tuple(rejected) == names
    print(
        json.dumps(
            {
                "synthetic_valid_structure": "PASS",
                "rejected": rejected,
                "provider_calls": 0,
                "release_qualification_claim": False,
            }
        )
    )


if __name__ == "__main__":
    main()
