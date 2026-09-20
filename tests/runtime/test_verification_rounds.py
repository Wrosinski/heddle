"""AC-1/2: verification accounts for originals without erasing ordinary coverage."""

import json

import pytest

from heddle.gate.retained import decode_capture, serialize_capture
from tests.proof_continuity_helpers import (
    FEATURE,
    SPEC,
    affirm_all,
    command,
    execute,
    launch,
    ops,
    read,
)
from tests.structured_review_helpers import disposition as reviewer_disposition
from tests.structured_review_helpers import finding, finding_ref
from tests.tiering_helpers import FABLE, entry, snapshot
from tests.tiering_review_helpers import (
    current_host,
    open_round,
    provider_transport,
    review_content,
    review_status,
)


def reviewed(tmp_path, monkeypatch, capsys, *, dual=False):
    selection = entry("spec-review", secondary=FABLE if dual else None)
    root, path = current_host(
        tmp_path, monkeypatch, overrides={"spec-review": selection}
    )
    content = review_content(
        findings=[
            finding("PC-I1", classification="implement"),
            finding("PC-I2", classification="implement"),
        ]
    )
    calls = provider_transport(monkeypatch, content)
    code, first = launch(capsys)
    assert code == 4 and first["data"]["accepted"], first
    origins = [first["data"]["run_id"]]
    if dual:
        code, second = launch(capsys, cli=FABLE["cli"], model=FABLE["model"])
        assert code == 4 and second["data"]["accepted"], second
        origins.append(second["data"]["run_id"])
    return root, path, content, calls, origins


def accounting(origins, *, omit=None):
    return [
        reviewer_disposition(finding_ref(origin, finding_id), action="addressed")
        for origin in origins
        for finding_id in ("PC-I1", "PC-I2", "@coverage")
        if (origin, finding_id) != omit
    ]


def test_closed_unsealed_verification_preserves_before_open_and_new_coverage(
    tmp_path, monkeypatch, capsys
):
    _root, path, content, calls, origins = reviewed(tmp_path, monkeypatch, capsys)
    affirm_all(path, [(origins[0], f) for f in ("PC-I1", "PC-I2", "@coverage")])
    assert review_status(path)["closed"]
    result = open_round(path)
    assert result.ok, result.to_envelope()
    assignment = read(path)["review_assignments"]["assignments"][0]
    assert assignment["rounds"][-1]["before_open"] == []
    content.clear()
    content.update(review_content())
    content["prior_dispositions"] = accounting(origins)
    code, second = launch(capsys)
    assert code == 0 and second["data"]["accepted"], second
    new_origin = second["data"]["run_id"]
    assert review_status(path)["open_refs"] == [[new_origin, "@coverage"]]
    affirm_all(path, [(new_origin, "@coverage")])
    assert review_status(path)["closed"]
    assert len(calls) == 2
    prompt = calls[-1][1]
    assert all(f in prompt for f in (origins[0], "PC-I1", "PC-I2", "@coverage"))


@pytest.mark.parametrize("omitted", ["PC-I2", "@coverage"])
@pytest.mark.parametrize("retained", [False, True])
def test_each_target_is_required_in_direct_and_retained_admission(
    tmp_path, monkeypatch, capsys, omitted, retained
):
    root, path, content, calls, origins = reviewed(tmp_path, monkeypatch, capsys)
    # Leave the assignment open so this validator witness reaches today's launch.
    assert open_round(path).ok
    content.clear()
    content.update(review_content())
    content["prior_dispositions"] = accounting(origins, omit=(origins[0], omitted))
    if retained:
        content["presentation_only"] = "force immutable retained capture"
    _code, response = launch(capsys)
    assert response["ok"], response
    data = response["data"]
    if not retained:
        assert not data["accepted"], "A clean verdict cannot omit a required original."
    else:
        assert not data["accepted"]
        capture_path = root / data["artifact"]
        frozen = capture_path.read_bytes()
        assert serialize_capture(decode_capture(frozen)) == frozen
        del content["presentation_only"]
        payload = {
            "schema": "heddle.review-interpretation-input/v1",
            "run_id": data["run_id"],
            "capture_sha256": data["capture_sha256"],
            "author": "lead",
            "reason": "Remove presentation field only.",
            "content": content,
        }
        before = snapshot(root)
        refused = execute(ops.InterpretReview(payload=payload, feature=FEATURE))
        assert not refused.ok, (
            "Interpretation must use the frozen complete target operand."
        )
        assert snapshot(root) == before
        assert capture_path.read_bytes() == frozen
    assert len(calls) == 2


def test_verification_targets_include_all_preceding_slots(
    tmp_path, monkeypatch, capsys
):
    _root, path, content, calls, origins = reviewed(
        tmp_path, monkeypatch, capsys, dual=True
    )
    assert open_round(path).ok
    content.clear()
    content.update(review_content())
    # Accounting for primary originals alone must not silently drop secondary duties.
    content["prior_dispositions"] = accounting(origins[:1])
    _code, response = launch(capsys)
    assert response["ok"] and not response["data"]["accepted"], response
    assert len(calls) == 3


def test_changed_slot_hint_names_a_publicly_executable_round_sequence(
    tmp_path, monkeypatch, capsys
):
    root, path, content, calls, origins = reviewed(tmp_path, monkeypatch, capsys)
    (root / SPEC).write_text((root / SPEC).read_text() + "\nContract clarification.\n")
    _code, refused = launch(capsys)
    assert not refused["ok"], refused
    hint = refused["error"]["hint"]
    assert "review round-open" in hint and "--input-json" in hint, hint
    assert "--purpose" not in hint
    payload = tmp_path / "round.json"
    payload.write_text(
        json.dumps(
            {
                "schema": "heddle.review-round-input/v1",
                "role": "spec-review",
                "scope": "feature",
                "purpose": "verification",
                "reason": "Verify the exact original concerns.",
            }
        )
    )
    code, opened = command(capsys, "review", "round-open", "--input-json", str(payload))
    assert code == 0 and opened["ok"], opened
    content.clear()
    content.update(review_content())
    content["prior_dispositions"] = accounting(origins)
    code, second = launch(capsys)
    assert code == 0 and second["data"]["accepted"], second
    affirm_all(
        path,
        [(origins[0], f) for f in ("PC-I1", "PC-I2", "@coverage")]
        + [(second["data"]["run_id"], "@coverage")],
    )
    assert review_status(path)["closed"] and len(calls) == 2


def test_new_capture_freezes_targets_while_legacy_absence_roundtrips_unchanged(
    tmp_path, monkeypatch, capsys
):
    from heddle.gate.results import canonical_json
    from heddle.gate.retained import interpret_capture

    root, path, content, calls, origins = reviewed(tmp_path, monkeypatch, capsys)
    assert open_round(path).ok
    content.clear()
    content.update(review_content())
    content["prior_dispositions"] = accounting(origins, omit=(origins[0], "PC-I2"))
    content["presentation_only"] = "capture, not a reviewer schema extension"
    _code, response = launch(capsys)
    assert response["ok"] and not response["data"]["accepted"], response
    frozen = (root / response["data"]["artifact"]).read_bytes()
    current = decode_capture(frozen)
    assert serialize_capture(current) == frozen
    del content["presentation_only"]

    # Explicit historic shape, authored from the existing capture contract. No
    # golden bytes from a local run and no synthesis from today's assignment.
    old = json.loads(frozen)
    old_keys = {"ac_ids", "active_rules", "prior_reviews", "review_decisions"}
    old["validation"] = {k: v for k, v in old["validation"].items() if k in old_keys}
    old_bytes = canonical_json(old, newline=True)
    legacy = decode_capture(old_bytes)
    assert serialize_capture(legacy) == old_bytes
    interpret_capture(legacy, content)  # Existing subset-validation semantics.
    with pytest.raises(ValueError, match="prior|target|account"):
        interpret_capture(current, content)
    value = json.loads(frozen)
    added = set(value["validation"]) - old_keys
    assert len(added) == 1, "One optional frozen target operand, no state migration."
    value["validation"][added.pop()] = "invalid operand shape"
    with pytest.raises(ValueError):
        decode_capture(canonical_json(value, newline=True))
    content["prior_dispositions"] = accounting(origins)
    interpret_capture(current, content)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "condition",
    ["sealed", "stopped", "capped", "off", "missing-slot", "unavailable-target"],
)
def test_verification_denials_and_changed_slot_remedies_do_not_launch_or_mutate(
    tmp_path, monkeypatch, capsys, condition
):
    from tests.tiering_review_helpers import amend, dispose, disposition, open_round

    selection = entry(
        "spec-review",
        limit=1 if condition in {"capped", "stopped"} else 2,
        secondary=FABLE if condition == "missing-slot" else None,
    )
    root, path = current_host(
        tmp_path, monkeypatch, overrides={"spec-review": selection}
    )
    content = review_content(findings=[finding("PC-I1", classification="implement")])
    calls = provider_transport(monkeypatch, content)
    code, first = launch(capsys)
    assert code == 4 and first["data"]["accepted"], first
    origin = first["data"]["run_id"]
    if condition == "stopped":
        assert dispose(
            path,
            [
                disposition(origin, "PC-I1", status="retained"),
                disposition(origin, "@coverage"),
            ],
        ).ok
        assert not open_round(path).ok  # Materialize the ordinary pending stop owner.
        stop = review_status(path)["decision_id"]
        assert stop is not None
        raised = execute(
            ops.ReviewAllowance(
                "spec-review",
                2,
                "Synthetic owner approves a bounded increase.",
                FEATURE,
                read(path)["revision"],
            )
        )
        assert raised.ok, raised.to_envelope()
    if condition != "missing-slot":
        affirm_all(path, [(origin, "PC-I1"), (origin, "@coverage")])
        assert review_status(path)["closed"]
    if condition == "sealed":
        result = execute(ops.PhaseExit(feature=FEATURE, through="plan-review"))
        assert result.ok, result.to_envelope()
    elif condition == "off":
        assert amend(
            path,
            entry("spec-review", mode="off", limit=None, minimum_rounds=0),
            revision=2,
        ).ok
    elif condition == "unavailable-target":
        (root / first["data"]["artifact"]).unlink()
    payload = tmp_path / "denied-round.json"
    payload.write_text(
        json.dumps(
            {
                "schema": "heddle.review-round-input/v1",
                "role": "spec-review",
                "scope": "feature",
                "purpose": "verification",
                "reason": "Attempt protected follow-up.",
            }
        )
    )
    before = snapshot(root)
    for args in (
        ("review", "round-open", "--input-json", str(payload)),
        (
            "run-gate",
            "spec-review",
            "--cli",
            "codex",
            "--model",
            "gpt-6-astra",
            "--reasoning-effort",
            "high",
        ),
    ):
        if args[0] == "run-gate" and condition != "unavailable-target":
            (root / SPEC).write_text(
                (root / SPEC).read_text() + "\nChanged review input.\n"
            )
            before = snapshot(root)
        code, refused = command(capsys, *args)
        assert code != 0 and not refused["ok"], refused
        assert snapshot(root) == before and len(calls) == 1
        explanation = json.dumps(refused).lower()
        terms = {
            "sealed": ("seal", "accept"),
            "stopped": (stop.lower(),) if condition == "stopped" else (),
            "capped": ("allowance", "limit", "cap"),
            "off": ("off",),
            "missing-slot": ("missing", "secondary"),
            "unavailable-target": ("artifact", "restore", "missing"),
        }[condition]
        assert any(term in explanation for term in terms), refused
        assert refused["error"]["hint"].strip()
        if condition == "capped":
            assert "review allowance" in explanation or "feature policy" in explanation
        elif condition == "stopped":
            assert "decisions resolve" in explanation
        elif condition == "off":
            assert "feature policy" in explanation


@pytest.mark.parametrize("purpose", ["discovery", "independent-pass"])
def test_closed_exception_is_verification_only(tmp_path, monkeypatch, capsys, purpose):
    root, path, _content, calls, origins = reviewed(tmp_path, monkeypatch, capsys)
    affirm_all(path, [(origins[0], f) for f in ("PC-I1", "PC-I2", "@coverage")])
    payload = tmp_path / "wrong-purpose.json"
    payload.write_text(
        json.dumps(
            {
                "schema": "heddle.review-round-input/v1",
                "role": "spec-review",
                "scope": "feature",
                "purpose": purpose,
                "reason": "Must not widen the closed-assignment exception.",
            }
        )
    )
    before = snapshot(root)
    code, refused = command(
        capsys, "review", "round-open", "--input-json", str(payload)
    )
    assert code != 0 and not refused["ok"], refused
    assert snapshot(root) == before and len(calls) == 1
