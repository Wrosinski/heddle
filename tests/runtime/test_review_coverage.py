import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from heddle.contracts import operations as ops
from heddle.gate.results import decode_review_content, review_output_contract
from heddle.gate.retained import decode_capture, serialize_capture
from heddle.gate.types import PreparedGateRun
from heddle.gate.validation import validate_review
from heddle.kernel.state import read_state_file
from heddle.runtime.application import execute
from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import evidence, finding, finding_ref
from tests.tiering_helpers import snapshot
from tests.tiering_review_helpers import (
    current_host,
    dispose,
    disposition,
    gate_command,
    open_round,
    provider_transport,
    review_content,
    review_status,
    runs,
)


@pytest.mark.parametrize("retained", [False, True])
def test_originating_coverage_review_closes_without_another_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: Any, retained: bool
) -> None:
    host, path = current_host(tmp_path, monkeypatch)
    content = review_content()
    calls = provider_transport(monkeypatch, content)
    _, first = gate_command(run_cli, "run-gate", "spec-review")
    assert first["data"]["accepted"], first
    origin = first["data"]["run_id"]
    assert dispose(
        path,
        [disposition(origin, "@coverage", status="retained", requires_inspection=True)],
    ).ok
    assert open_round(path).ok
    content["prior_dispositions"] = [
        reviewer_disposition(finding_ref(origin, "@coverage"), action="addressed")
    ]
    if retained:
        content["extra_presentation"] = "retain original bytes"
    _, second = gate_command(run_cli, "run-gate", "spec-review")
    assert second["ok"], second
    data = second["data"]
    if retained:
        assert data["accepted"] is False
        capture_path = host / data["artifact"]
        original_bytes = capture_path.read_bytes()
        capture = decode_capture(original_bytes)
        assert serialize_capture(capture) == original_bytes
        del content["extra_presentation"]
        payload = {
            "schema": "heddle.review-interpretation-input/v1",
            "run_id": data["run_id"],
            "capture_sha256": data["capture_sha256"],
            "author": "lead",
            "reason": "Preserve affirmative coverage; remove presentation field only.",
            "content": content,
        }
        before = snapshot(host)
        preview = execute(ops.InterpretReview(payload=payload, dry_run=True))
        assert preview.ok, preview.to_envelope()
        assert snapshot(host) == before
        accepted = execute(ops.InterpretReview(payload=payload))
        assert accepted.ok, accepted.to_envelope()
        assert capture_path.read_bytes() == original_bytes
    else:
        assert data["accepted"], second
    assert len(calls) == 2 and len(runs(path)) == 2
    assert [origin, "@coverage"] in review_status(path)["open_refs"]
    closed = dispose(
        path,
        [
            disposition(
                origin,
                "@coverage",
                evidence_kind="review",
                review_run_id=data["run_id"],
                requires_inspection=True,
            ),
            disposition(data["run_id"], "@coverage"),
        ],
    )
    assert closed.ok, closed.to_envelope()
    assert review_status(path)["closed"]
    spec = host / read_state_file(path).spec
    assert spec.exists()
    spec.write_text(spec.read_text() + "\nChanged specification contract.\n")
    assert not review_status(path)["closed"]
    before = snapshot(host)
    rejected = dispose(
        path,
        [
            disposition(
                origin,
                "@coverage",
                evidence_kind="review",
                review_run_id=data["run_id"],
                requires_inspection=True,
            )
        ],
    )
    assert not rejected.ok
    assert snapshot(host) == before and len(calls) == 2


def test_coverage_admission_preserves_frozen_capture_and_origin_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: Any
) -> None:
    host, path = current_host(tmp_path, monkeypatch)
    content = review_content()
    calls = provider_transport(monkeypatch, content)
    _, first = gate_command(run_cli, "run-gate", "spec-review")
    origin = first["data"]["run_id"]
    assert open_round(path).ok
    content["prior_dispositions"] = [
        reviewer_disposition(finding_ref(origin, "@coverage"), action="addressed")
    ]
    content["extra_presentation"] = "force retained input boundary"
    _, second = gate_command(run_cli, "run-gate", "spec-review")
    raw = (host / second["data"]["artifact"]).read_bytes()
    capture = decode_capture(raw)
    assert serialize_capture(capture) == raw
    del content["extra_presentation"]
    contract = review_output_contract("spec-review")
    decoded = decode_review_content(json.dumps(content).encode(), contract)
    validate_review(decoded, capture.validation)
    prior = capture.validation.prior_reviews[0]
    for assignment in (None, "another-assignment"):
        other = replace(prior.result.invocation, assignment_id=assignment)
        invalid = replace(
            capture.validation,
            prior_reviews=(
                replace(prior, result=replace(prior.result, invocation=other)),
            ),
        )
        with pytest.raises(ValueError, match="prior finding accounting"):
            validate_review(decoded, invalid)
    with pytest.raises(ValueError, match="prior finding accounting"):
        validate_review(decoded, replace(capture.validation, prior_reviews=()))
    with pytest.raises(ValueError, match="prior finding accounting"):
        historical = SimpleNamespace(**vars(capture.validation))
        historical.assignment_id = None
        validate_review(decoded, cast(PreparedGateRun, historical))
    faults = {
        "unknown-origin": "prior finding accounting",
        "non-affirmative": r"addressed prior finding @coverage \(run [^)]+\) needs "
        "affirmative trace, test or execution evidence, not absence",
        "lead-settled": r"prior coverage target @coverage \(run [^)]+\) takes "
        "retained or addressed, not settled",
        "retained-regression": "retained prior finding cannot also be a "
        "regression: SP-N1 continues a retained prior target",
        "missing-regression": "SP-N1 has no regressions entry and no retained "
        "prior target",
    }
    for fault, message in faults.items():
        invalid_content = deepcopy(content)
        row = invalid_content["prior_dispositions"][0]
        if fault == "unknown-origin":
            row["source"]["run_id"] = "00000000-0000-4000-8000-ffffffffffff"
        elif fault == "non-affirmative":
            row["evidence"]["kind"] = "absence"
        elif fault == "lead-settled":
            row["disposition"] = "settled"
        else:
            invalid_content["findings"] = [finding("SP-N1", classification="implement")]
            if fault == "retained-regression":
                row.update(disposition="retained", output_finding_id="SP-N1")
                invalid_content["regressions"] = [
                    {"finding_id": "SP-N1", "evidence": evidence()}
                ]
        with pytest.raises(ValueError, match=message):
            validate_review(
                decode_review_content(json.dumps(invalid_content).encode(), contract),
                capture.validation,
            )
    assert len(calls) == 2
    rerun_prompt = calls[1][1]
    assert "Prior-disposition encoding" not in calls[0][1]
    for rule in (
        "An IMPLEMENT finding or @coverage target is retained or addressed",
        "every regression cite trace, test or execution evidence",
        "that finding is not a regression",
    ):
        assert rule in rerun_prompt
