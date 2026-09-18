"""Completed imperfect responses remain usable without another provider call."""

import base64
import hashlib
import io
import json
import stat
import sys
import tarfile
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.structured_review_helpers import finding, native_providers
from tests.tiering_helpers import ASTRA, OPUS, entry, snapshot
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    dispose,
    disposition,
    gate_command,
    provider_transport,
    review_content,
    review_status,
    runs,
)


@pytest.mark.parametrize("selection", [ASTRA, OPUS], ids=["codex", "claude"])
def test_retained_response_interpretation_preserves_one_call_and_original_owner(
    tmp_path, monkeypatch, run_cli, selection
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={"behavior-review": entry("behavior-review", primary=selection)},
    )
    content = review_content(
        "behavior-review", findings=[finding("BR-I1", classification="report")]
    )
    imperfect = {
        **content,
        "extra_explanation": "Full original response — keep this too.",
    }
    calls = provider_transport(monkeypatch, imperfect)
    code, warning = gate_command(run_cli, "run-gate", "behavior-review")
    assert code == 4 and warning["ok"], warning
    data = warning["data"]
    assert data["accepted"] is False and data["findings"] is None
    assert len(calls) == 1 and not runs(path)
    original = host / data["artifact"]
    original_bytes = original.read_bytes()
    captured = json.loads(original_bytes)
    raw = base64.b64decode(captured["response"]["raw_stream"]["base64"])
    assert b"extra_explanation" in raw
    assert not yaml.safe_load(path.read_text())["decisions"]
    status = review_status(path, role="behavior-review")
    assert status["next_step"] == "interpret" and not status["closed"]
    assert status["calls_completed"] == 1
    before = snapshot(host)
    code, replay = gate_command(run_cli, "run-gate", "behavior-review")
    assert code == 4 and replay["data"]["cached"]
    assert replay["data"]["run_id"] == data["run_id"]
    assert len(calls) == 1 and snapshot(host) == before
    payload = {
        "schema": "heddle.review-interpretation-input/v1",
        "run_id": data["run_id"],
        "capture_sha256": data["capture_sha256"],
        "author": "lead",
        "reason": "Preserved every finding; removed only the extra presentation field.",
        "content": content,
    }
    preview = execute(ops.InterpretReview(payload=payload, dry_run=True))
    assert preview.ok, preview.to_envelope()
    assert snapshot(host) == before
    accepted = execute(ops.InterpretReview(payload=payload))
    assert accepted.ok, accepted.to_envelope()
    assert len(calls) == 1 and len(runs(path)) == 1
    assert runs(path)[0]["run_id"] == data["run_id"]
    decisions = yaml.safe_load(path.read_text())["decisions"]
    assert len(decisions) == 1 and decisions[0]["status"] == "pending"
    assert decisions[0]["origin_run_id"] == data["run_id"]
    assert original.read_bytes() == original_bytes
    status = review_status(path, role="behavior-review")
    assert not status["closed"] and not status["missing_slots"]
    assert status["calls_completed"] == 1
    before = snapshot(host)
    again = execute(ops.InterpretReview(payload=payload))
    assert again.ok and again.data["cached"]
    assert snapshot(host) == before and len(calls) == 1
    code, replay = gate_command(run_cli, "run-gate", "behavior-review")
    assert code in (0, 4) and replay["data"]["cached"], replay
    assert replay["data"]["run_id"] == data["run_id"]
    assert replay["diagnostics"] and snapshot(host) == before and len(calls) == 1


def interpretation(data, content):
    return {
        "schema": "heddle.review-interpretation-input/v1",
        "run_id": data["run_id"],
        "capture_sha256": data["capture_sha256"],
        "author": "lead",
        "reason": "Read the complete response; normalized presentation only.",
        "content": content,
    }


@pytest.mark.parametrize(
    "role,stage",
    [
        ("spec-review", "spec-review"),
        ("plan-review", "plan-review"),
        ("review-test-scaffolding", "scaffold"),
        ("milestone-review", "implement"),
        ("peer-review-sequential", "peer-review"),
        ("behavior-review", "peer-review"),
        ("complexity-review", "peer-review"),
        ("robustness-analysis", "robustness"),
    ],
)
def test_all_current_roles_share_the_retained_interpretation_boundary(
    tmp_path, monkeypatch, run_cli, role, stage
):
    _host, path = current_host(
        tmp_path,
        monkeypatch,
        stage=stage,
        overrides={
            role: entry(
                role,
                primary=ASTRA,
                trigger={
                    "gate": role,
                    "gap": "Inspect an unexecuted failure boundary",
                    "references": ["src/example.py"],
                }
                if role == "robustness-analysis"
                else None,
            )
        },
    )
    content = review_content(role)
    calls = provider_transport(monkeypatch, {**content, "extra": "keep every byte"})
    code, result = gate_command(run_cli, "run-gate", role)
    assert code == 4 and result["ok"], result
    accepted = execute(
        ops.InterpretReview(payload=interpretation(result["data"], content))
    )
    assert accepted.ok, accepted.to_envelope()
    assert len(calls) == 1 and len(runs(path)) == 1
    assert runs(path)[0]["run_id"] == result["data"]["run_id"]


def test_retained_secondary_records_after_canonical_primary_without_another_call(
    tmp_path, monkeypatch, run_cli
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", secondary=OPUS)},
    )
    content = review_content()
    calls = provider_transport(
        monkeypatch,
        lambda cli, _prompt: (
            content if cli == "codex" else {**content, "extra": "retain exact bytes"}
        ),
    )

    code, primary = gate_command(run_cli, "run-gate", "spec-review", "--cli", "codex")
    assert code == 0 and primary["ok"], primary
    code, secondary = gate_command(
        run_cli, "run-gate", "spec-review", "--cli", "claude"
    )
    assert code == 4 and secondary["ok"], secondary
    assert secondary["data"]["accepted"] is False
    assert len(calls) == 2 and len(runs(path)) == 1
    assert (host / secondary["data"]["artifact"]).is_file()
    attempts = yaml.safe_load(path.read_text())["review_assignments"]["attempts"]
    assert [row["outcome"]["kind"] for row in attempts] == [
        "canonical-review",
        "usable-capture",
    ]

    before = snapshot(host)
    code, replay = gate_command(run_cli, "run-gate", "spec-review", "--cli", "claude")
    assert code == 4 and replay["data"]["cached"], replay
    assert replay["data"]["run_id"] == secondary["data"]["run_id"]
    assert len(calls) == 2 and snapshot(host) == before


def test_interpretation_is_discoverable_on_the_native_manifest():
    from heddle.runtime.contracts import build_manifest

    row = next(
        c for c in build_manifest()["commands"] if c["name"] == "review interpret"
    )
    assert row["mutating"] and row["dry_run"]
    assert {f["name"] for f in row["flags"]} == {
        "--json",
        "--feature",
        "--dry-run",
        "--input-json",
        "--expect-revision",
    }
    assert row["input_schema"]["id"] == "heddle.review-interpretation-input/v1"
    assert set(row["input_schema"]["fields"]) == {
        "schema",
        "run_id",
        "capture_sha256",
        "author",
        "reason",
        "content",
    }


def retained_host(tmp_path, monkeypatch, run_cli, **host_kwargs):
    host, path = current_host(tmp_path, monkeypatch, **host_kwargs)
    content = review_content()
    calls = provider_transport(monkeypatch, {**content, "extra": "retained detail"})
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["data"]["accepted"] is False, result
    return host, path, calls, result["data"], interpretation(result["data"], content)


@pytest.mark.parametrize(
    "selection,mutation,payload",
    [
        (ASTRA, None, b'{"not": "finished"'),
        (ASTRA, None, b"\xff invalid UTF-8 response"),
        (ASTRA, "missing-final", review_content()),
        (ASTRA, "empty-final", review_content()),
        (OPUS, "missing-structured", review_content()),
        (OPUS, "wrong-structured-type", review_content()),
    ],
    ids=[
        "truncated-content",
        "invalid-utf8",
        "missing-final",
        "empty-final",
        "missing-structured",
        "wrong-structured",
    ],
)
def test_complete_imperfect_channels_retain_exact_bytes_without_format_retry(
    tmp_path, monkeypatch, run_cli, selection, mutation, payload
):
    host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=selection)},
    )
    observed = {}

    def observe(_lane, call):
        observed["raw"] = call["raw_out"].read_bytes()
        command = call["command"]
        if "-o" in command:
            final = Path(command[command.index("-o") + 1])
            observed["selected"] = final.read_bytes() if final.exists() else None

    calls = native_providers(
        monkeypatch, tmp_path, payload, mutation=mutation, before_return=observe
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    data = result["data"]
    raw = json.loads((host / data["artifact"]).read_bytes())
    assert base64.b64decode(raw["response"]["raw_stream"]["base64"]) == observed["raw"]
    if selection == ASTRA:
        selected = raw["response"]["selected_response"]
        assert (base64.b64decode(selected["base64"]) if selected else None) == observed[
            "selected"
        ]
    else:
        assert raw["response"]["selected_response"] is None
    assert raw["prompt"]["stdin"].encode() == calls[0]["stdin"]
    assert raw["schema_json"].encode() == calls[0]["schema"]
    assert len(calls) == 1 and not runs(path)
    before = snapshot(host)
    code, replay = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and replay["data"]["cached"]
    assert len(calls) == 1 and snapshot(host) == before


@pytest.mark.parametrize("selection", [ASTRA, OPUS], ids=["codex", "claude"])
@pytest.mark.parametrize(
    "failure",
    [
        "missing-terminal",
        "duplicate-terminal",
        "conflicting-terminal",
        "exit",
        "timeout",
        "inactivity",
    ],
)
def test_actual_provider_failures_do_not_acquire_retained_success(
    tmp_path, monkeypatch, run_cli, selection, failure
):
    from heddle.io.process import Termination

    _host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=selection)},
    )
    calls = native_providers(
        monkeypatch,
        tmp_path,
        review_content(),
        mutation=failure if "terminal" in failure else None,
        exit_code=7 if failure == "exit" else 0,
        termination={
            "timeout": Termination.TIMEOUT,
            "inactivity": Termination.INACTIVITY,
        }.get(failure),
    )
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not result["ok"], result
    assert len(calls) == 1
    assert all(row["verdict"]["status"] == "error" for row in runs(path))
    assert (
        not yaml.safe_load(path.read_text())
        .get("review_assignments", {})
        .get("retained", [])
    )


def test_native_cli_interpretation_and_cas_preview_are_nonmutating(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, _data, payload = retained_host(tmp_path, monkeypatch, run_cli)
    revision = yaml.safe_load(path.read_text())["revision"]
    before = snapshot(host)
    typed = execute(
        ops.InterpretReview(payload=payload, dry_run=True, expect_revision=revision)
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, out, _err = run_cli(
        [
            "review",
            "interpret",
            "--feature",
            V7_FEATURE,
            "--input-json",
            "-",
            "--expect-revision",
            str(revision),
            "--dry-run",
            "--json",
        ]
    )
    assert code == 0 and json.loads(out) == typed.to_envelope()
    assert snapshot(host) == before
    conflict = execute(
        ops.InterpretReview(payload=payload, expect_revision=revision + 1)
    )
    assert not conflict.ok and conflict.exit_code == 5 and snapshot(host) == before
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code, accepted = gate_command(run_cli, "review", "interpret", "--input-json", "-")
    assert code == 0 and accepted["data"]["accepted"], accepted
    assert len(calls) == 1 and len(runs(path)) == 1


@pytest.mark.parametrize(
    "mutation", ["run", "hash", "author", "reason", "content", "extra"]
)
def test_invalid_interpretation_cannot_publish_any_authority_or_artifact(
    tmp_path, monkeypatch, run_cli, mutation
):
    host, path, calls, _data, payload = retained_host(tmp_path, monkeypatch, run_cli)
    if mutation == "run":
        payload["run_id"] = "unknown"
    elif mutation == "hash":
        payload["capture_sha256"] = "0" * 64
    elif mutation in {"author", "reason"}:
        payload[mutation] = " "
    elif mutation == "content":
        payload["content"]["details"]["dimensions"].pop()
    else:
        payload["invocation"] = {"model": "different-reviewer"}
    before = snapshot(host)
    result = execute(ops.InterpretReview(payload=payload))
    assert not result.ok, result.to_envelope()
    assert snapshot(host) == before and not runs(path) and len(calls) == 1


def test_interpretation_keeps_original_inputs_after_changes_and_dual_slot_open(
    tmp_path, monkeypatch, run_cli
):
    host, path, calls, data, payload = retained_host(
        tmp_path,
        monkeypatch,
        run_cli,
        overrides={"spec-review": entry("spec-review", secondary=OPUS)},
    )
    original = (host / data["artifact"]).read_bytes()
    spec = host / "docs/features/runtime" / f"{V7_FEATURE}.md"
    spec.write_text(spec.read_text().replace("AC-1", "AC-9"))
    before = snapshot(host)
    code, replay = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and replay["data"]["cached"] and snapshot(host) == before
    result = execute(ops.InterpretReview(payload=payload))
    assert result.ok, result.to_envelope()
    run = runs(path)[0]
    capture = json.loads(original)
    assert run["input_hash"] == capture["invocation"]["input_hash"]
    assert run["review_basis_hash"] == capture["invocation"]["review_basis_hash"]
    assert (host / data["artifact"]).read_bytes() == original and len(calls) == 1
    status = review_status(path)
    assert not status["closed"] and status["missing_slots"]
    assert all("secondary" in row for row in status["missing_slots"])
    assert status["calls_completed"] == 1
    early = dispose(path, [disposition(run["run_id"], "@coverage", status="settled")])
    assert not early.ok


@pytest.mark.parametrize(
    "fault", ["capture-publication", "capture-state", "interpretation-state"]
)
def test_storage_failure_never_accepts_and_durable_capture_recovers_without_new_call(
    tmp_path, monkeypatch, run_cli, fault
):
    from heddle.gate import io as gate_io
    from heddle.runtime import state_store

    host, path = current_host(tmp_path, monkeypatch)
    content = review_content()
    calls = provider_transport(monkeypatch, {**content, "extra": "full raw"})

    def fail(*_args, **_kwargs):
        raise OSError("injected durable publication failure")

    before = path.read_bytes()
    if fault != "interpretation-state":
        with monkeypatch.context() as injected:
            injected.setattr(
                gate_io if fault == "capture-publication" else state_store,
                "publish_review_result"
                if fault == "capture-publication"
                else "_publish",
                fail,
            )
            if fault == "capture-publication":
                injected.setattr("heddle.gate.runner.publish_review_result", fail)
            code, result = gate_command(run_cli, "run-gate", "spec-review")
            assert code != 0 and not result["ok"], result
        if fault == "capture-publication":
            assert all(row["verdict"]["status"] == "error" for row in runs(path))
            assert not yaml.safe_load(path.read_text())["decisions"]
            assert len(calls) == 1
            assert not list(path.parent.glob("reviews/*.capture.json"))
            return
        assert not runs(path) and path.read_bytes() == before and len(calls) == 1
        assert len(list(path.parent.glob("reviews/*.capture.json"))) == 1
    code, warning = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and warning["ok"], warning
    assert len(calls) == 1
    data = warning["data"]
    payload = interpretation(data, content)
    if fault == "interpretation-state":
        before = path.read_bytes()
        with monkeypatch.context() as injected:
            injected.setattr(state_store, "_publish", fail)
            result = execute(ops.InterpretReview(payload=payload))
        assert not result.ok and path.read_bytes() == before and not runs(path)
    result = execute(ops.InterpretReview(payload=payload))
    assert result.ok and len(runs(path)) == 1 and len(calls) == 1


@pytest.mark.parametrize("fault", ["publication", "revision-conflict"])
def test_valid_canonical_success_recovers_after_state_recording_failure(
    tmp_path, monkeypatch, run_cli, fault
):
    from heddle.runtime import recording, state_store

    host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content())

    if fault == "publication":
        original_publish = state_store._publish
        armed = True

        def fail_once(state_path, text, *, create):
            nonlocal armed
            if state_path == path and not create and armed:
                armed = False
                raise OSError("injected canonical state publication failure")
            return original_publish(state_path, text, create=create)

        target, name, replacement = state_store, "_publish", fail_once
    else:
        original_record = recording.record_gate_run
        armed = True

        def conflict_once(*args, **kwargs):
            nonlocal armed
            if armed:
                armed = False

                def append_session(document):
                    document["sessions"].append(
                        {
                            "started_at": "2026-09-13T00:00Z",
                            "ended_at": "2026-09-13T00:01Z",
                            "completed": ["concurrent fact"],
                            "started": [],
                            "key_context": "force stale canonical recording",
                            "next_steps": "retry recording",
                            "blockers": [],
                            "stage": document["stage"],
                        }
                    )
                    return document

                state_store.append_state(path, transform=append_session)
            return original_record(*args, **kwargs)

        target, name, replacement = recording, "record_gate_run", conflict_once

    with monkeypatch.context() as injected:
        injected.setattr(target, name, replacement)
        code, failed = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 3 and not failed["ok"], failed
    assert not runs(path) and len(calls) == 1
    orphan = list(path.parent.glob("reviews/*.review.json"))
    assert len(orphan) == 1
    orphan_bytes = orphan[0].read_bytes()

    code, recovered = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and recovered["ok"], recovered
    assert recovered["data"]["cached"] is True
    assert len(runs(path)) == 1 and len(calls) == 1
    assert orphan[0].read_bytes() == orphan_bytes
    assert yaml.safe_load(path.read_text())["review_assignments"]["assignments"]


@pytest.mark.parametrize("fault", ["read", "serialize", "publish"])
def test_capture_storage_errors_preserve_real_provider_completion_and_raw_artifacts(
    tmp_path, monkeypatch, run_cli, fault
):
    from heddle.gate import retained, runner

    _host, path = current_host(tmp_path, monkeypatch)
    content = {**review_content(), "extra": "complete original imperfect response"}
    calls = provider_transport(monkeypatch, content)

    def fail(*_args, **_kwargs):
        if fault == "publish":
            raise OSError("injected capture publication failure")
        raise ValueError("injected bounded capture representation failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            runner if fault == "publish" else retained,
            {
                "read": "read_bounded",
                "serialize": "serialize_capture",
                "publish": "publish_review_result",
            }[fault],
            fail,
        )
        code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not result["ok"], result
    assert "capture-storage-failure" in result["error"]["message"]
    recorded = runs(path)
    assert len(recorded) == len(calls) == 1
    assert recorded[0]["verdict"]["status"] == "error"
    assert recorded[0]["failure_reason"] == "capture-storage-failure"
    summary = json.loads(
        (path.parent / "reviews/spec-review.codex.gate-summary.json").read_text()
    )
    assert summary["cli_exit"] == 0 and summary["gate_exit"] == 1
    assert summary["reason"] == "capture-storage-failure"
    assert (
        "complete original imperfect response"
        in Path(summary["artifacts"]["raw_out"]).read_text()
    )
    log = Path(summary["io"]["log"]).read_text()
    assert "CaptureStorageError" in log and "InternalError" not in log
    assert not list(path.parent.glob("reviews/*.capture.json"))
    state = yaml.safe_load(path.read_text())
    ledger = state["review_assignments"]
    assert state["decisions"] == [] and set(ledger) == {
        "schema",
        "assignments",
        "attempts",
        "dispositions",
        "acceptances",
    }
    assert len(ledger["attempts"]) == 1
    attempt = ledger["attempts"][0]
    assert attempt["outcome"]["kind"] == "engine-failure"
    assert attempt["source"] is None and attempt["interpretation"] is None


@pytest.mark.parametrize("imperfect", [False, True])
@pytest.mark.parametrize("source_fault", ["changed", "unavailable"])
def test_midrun_source_rejection_survives_restore_and_capture_rename(
    tmp_path, monkeypatch, run_cli, imperfect, source_fault
):
    import hashlib

    host, path = current_host(tmp_path, monkeypatch)
    spec = host / f"docs/features/runtime/{V7_FEATURE}.md"
    original_spec = spec.read_bytes()
    generation = {"value": 1}

    def response(_cli, _prompt):
        content = review_content()
        if generation["value"] == 1:
            if source_fault == "changed":
                spec.write_bytes(original_spec + b"\nNew consequential commitment.\n")
            else:
                spec.unlink()
            if imperfect:
                content["extra"] = "preserve the rejected original response"
        return content

    calls = provider_transport(monkeypatch, response)
    code, refused = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not refused["ok"], refused
    assert runs(path), refused
    error = runs(path)[0]
    assert error["verdict"]["status"] == "error"
    assert error["failure_reason"] == f"reviewed-source-{source_fault}"
    assert all("was accepted" not in row["message"] for row in refused["diagnostics"])
    artifact = path.parent / error["artifact"]
    raw = artifact.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == error["artifact_sha256"]
    state = yaml.safe_load(path.read_text())
    assert set(state["review_assignments"]) == {
        "schema",
        "assignments",
        "attempts",
        "dispositions",
        "acceptances",
    }
    assert all(
        attempt.get("source") is None
        for attempt in state["review_assignments"]["attempts"]
    )
    assert not state["decisions"]
    spec.write_bytes(original_spec)
    if imperfect:
        copied = artifact.with_name(f"renamed.{error['artifact_sha256']}.capture.json")
        copied.write_bytes(raw)
    generation["value"] = 2
    code, fresh = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 0 and fresh["ok"] and not fresh["data"]["cached"], fresh
    assert len(calls) == 2 and len(runs(path)) == 2
    assert runs(path)[0] == error and artifact.read_bytes() == raw
    state = yaml.safe_load(path.read_text())
    accepted_sources = [
        row["source"]
        for row in state["review_assignments"]["attempts"]
        if row.get("source") is not None
    ]
    assert [row["run_id"] for row in accepted_sources] == [fresh["data"]["run_id"]]


def test_unindexed_capture_with_changed_basis_refuses_recovery_before_provider(
    tmp_path, monkeypatch, run_cli
):
    from heddle.runtime import recording

    host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(
        monkeypatch, {**review_content(), "extra": "full original"}
    )

    def interrupted(*_args, **_kwargs):
        raise OSError("injected ledger recording interruption after capture")

    with monkeypatch.context() as patch:
        patch.setattr(recording, "record_retained_review", interrupted)
        code, failure = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not failure["ok"] and len(calls) == 1
    assert len(list(path.parent.glob("reviews/*.capture.json"))) == 1
    spec = host / f"docs/features/runtime/{V7_FEATURE}.md"
    spec.write_text(spec.read_text() + "\nNew source contract after interruption.\n")
    before = snapshot(host)
    code, refused = gate_command(run_cli, "run-gate", "spec-review")
    assert code != 0 and not refused["ok"], refused
    assert "unrecorded completed response needs recovery" in refused["error"]["message"]
    assert "source changed" in refused["error"]["message"]
    assert "do not rerun the provider" in refused["error"]["hint"]
    assert len(calls) == 1 and snapshot(host) == before and not runs(path)


def test_capture_and_interpretation_history_are_append_only_and_receipts_are_validated(
    tmp_path, monkeypatch, run_cli
):
    from heddle.kernel.project_config import KernelError
    from heddle.runtime.state_store import append_state

    host, path, calls, data, payload = retained_host(tmp_path, monkeypatch, run_cli)
    assert execute(ops.InterpretReview(payload=payload)).ok
    before = snapshot(host)
    for target in ("attempt", "interpretation"):

        def erase(document, target=target):
            attempts = document["review_assignments"]["attempts"]
            if target == "attempt":
                attempts.clear()
            else:
                attempts[0].pop("interpretation")
            return document

        with pytest.raises(KernelError, match="append-only"):
            append_state(path, transform=erase)
        assert snapshot(host) == before
    changed = deepcopy(payload)
    changed["reason"] = "Different interpretation authority"
    assert not execute(ops.InterpretReview(payload=changed)).ok
    assert snapshot(host) == before
    capture = host / data["artifact"]
    capture.write_bytes(capture.read_bytes() + b" ")
    for op in (ops.Status(), ops.Orient(), ops.InterpretReview(payload=payload)):
        result = execute(op)
        assert not result.ok, result.to_envelope()
    assert len(calls) == 1


def test_reporting_and_cleanup_preserve_capture_and_count_interpretation_once(
    tmp_path, monkeypatch, run_cli
):
    from heddle.kernel.state import read_state_file
    from heddle.runtime.completion import _retained_paths
    from heddle.runtime.trajectory import archive_trajectory

    host, path, calls, data, payload = retained_host(tmp_path, monkeypatch, run_cli)
    before = snapshot(host)
    status = execute(ops.Status())
    orient = execute(ops.Orient())
    assert status.data["review_closure"] == orient.data["review_closure"]
    assert snapshot(host) == before
    for interpreted in (False, True):
        if interpreted:
            assert execute(ops.InterpretReview(payload=payload)).ok
        saved = path.read_bytes()
        exported = archive_trajectory(host, V7_FEATURE, now=date(2026, 9, 11))
        report = json.loads(exported.read_bytes())
        assert report["budget"]["calls_count"] == 1 and len(report["runs"]) == 1
        row = report["runs"][0]
        assert row["run_id"] == data["run_id"]
        assert row["capture_sha256"] == data["capture_sha256"]
        if interpreted:
            assert row["interpretation"]["author"] == "lead"
        else:
            assert row["findings"] is None and row["status"] == "retained"
            assert (
                "not zero-findings reviews"
                in exported.with_name("_aggregate.md").read_text()
            )
        assert path.read_bytes() == saved and len(calls) == 1
        state = read_state_file(path)
        terminal = replace(state, completion=SimpleNamespace(close_suite=None))
        assert state.review_assignments.retained[0].artifact in _retained_paths(
            terminal
        )


def test_restarted_driver_routes_capture_to_lead_session_without_reviewer_retry(
    tmp_path, monkeypatch, run_cli
):
    from heddle.driver import loop
    from heddle.driver.sessions import SessionResult
    from heddle.kernel.project_config import load_project_config

    host, _path, calls, data, _payload = retained_host(tmp_path, monkeypatch, run_cli)
    sessions = []

    def session(spec, **_kwargs):
        sessions.append(spec.prompt_path.read_text())
        assert "Bash(heddle review interpret:*)" in spec.allowed_tools
        # Stop at the actual lead session transport boundary, not a full journey.
        return SessionResult(exit_code=17, termination="completed", output={})

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    monkeypatch.setattr(loop, "spawn_phase_session", session)
    before = snapshot(host)
    result = loop.run_loop(load_project_config(host), V7_FEATURE, until=None)
    assert not result.ok and "17" in result.error.message, result.to_envelope()
    assert len(sessions) == 1
    assert data["artifact"] in sessions[0] and data["capture_sha256"] in sessions[0]
    assert "Lead interpretation earns original reviewer credit" in sessions[0]
    assert snapshot(host) == before and len(calls) == 1


def test_restarted_driver_routes_coverage_to_lead_session_without_empty_write(
    tmp_path, monkeypatch, run_cli
):
    from heddle.driver import loop
    from heddle.driver.sessions import SessionResult
    from heddle.kernel.project_config import load_project_config

    host, path = current_host(tmp_path, monkeypatch)
    calls = provider_transport(monkeypatch, review_content())
    assert gate_command(run_cli, "run-gate", "spec-review")[0] == 0
    origin = runs(path)[0]["run_id"]
    sessions = []

    def session(spec, **_kwargs):
        sessions.append(spec.prompt_path.read_text())
        assert "Bash(heddle review disposition:*)" in spec.allowed_tools
        return SessionResult(exit_code=17, termination="completed", output={})

    monkeypatch.setattr(loop, "_capability_probe_result", lambda *_args: None)
    monkeypatch.setattr(loop, "_auto_close_suite_result", lambda *_args: None)
    monkeypatch.setattr(loop, "spawn_phase_session", session)
    before = snapshot(host)
    result = loop.run_loop(load_project_config(host), V7_FEATURE, until="plan-review")
    assert not result.ok and "17" in result.error.message, result.to_envelope()
    assert len(sessions) == 1
    assert origin in sessions[0] and "@coverage" in sessions[0]
    assert snapshot(host) == before and len(calls) == 1


def test_prior_accepted_findings_and_report_owners_survive_a_later_retained_response(
    tmp_path, monkeypatch, run_cli
):
    from tests.tiering_review_helpers import open_round

    host, path = current_host(tmp_path, monkeypatch)
    first = review_content(findings=[finding("SP-I1", classification="report")])
    provider_transport(monkeypatch, first)
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code in (0, 4), result
    original = deepcopy(runs(path)[0])
    decisions = deepcopy(yaml.safe_load(path.read_text())["decisions"])
    assert open_round(path).ok
    # Empty, structurally wrong content must not clear previous obligations.
    calls = provider_transport(monkeypatch, {"schema": "wrong", "text": "Review notes"})
    code, result = gate_command(run_cli, "run-gate", "spec-review")
    assert code == 4 and result["ok"], result
    assert runs(path) == [original]
    assert yaml.safe_load(path.read_text())["decisions"] == decisions
    status = review_status(path)
    assert not status["closed"] and status["calls_completed"] == 2
    interpreted = execute(
        ops.InterpretReview(payload=interpretation(result["data"], review_content()))
    )
    assert interpreted.ok, interpreted.to_envelope()
    # Current assignments retain original obligations independently of a later
    # review's optional prior-disposition prose; omission is never settlement.
    assert runs(path)[0] == original and len(runs(path)) == 2
    assert yaml.safe_load(path.read_text())["decisions"] == decisions
    assert not review_status(path)["closed"] and len(calls) == 1
    capture = json.loads((host / result["data"]["artifact"]).read_bytes())
    assert capture["validation"]["prior_reviews"][0]["run_id"] == original["run_id"]


@pytest.mark.parametrize(
    "mutation", ["raw-byte-count", "completion", "contract", "extra"]
)
def test_capture_codec_rejects_tampered_shape_and_byte_identity(
    tmp_path, monkeypatch, run_cli, mutation
):
    from heddle.gate.retained import decode_capture

    host, _path, _calls, data, _payload = retained_host(tmp_path, monkeypatch, run_cli)
    capture = json.loads((host / data["artifact"]).read_bytes())
    if mutation == "raw-byte-count":
        capture["response"]["raw_stream"]["bytes"] += 1
    elif mutation == "completion":
        capture["completion"]["exit_code"] = False
    elif mutation == "contract":
        capture["schema_json"] = "{}"
    else:
        capture["inferred_pass"] = True
    with pytest.raises(ValueError):
        decode_capture(json.dumps(capture).encode())


def test_final_boundary_requires_interpretation_and_archives_original_capture(
    tmp_path, monkeypatch, run_cli
):
    from tests.tiering_completion_helpers import final_host

    host = final_host(
        tmp_path,
        monkeypatch,
        overrides={"behavior-review": entry("behavior-review")},
    )
    content = review_content("behavior-review")
    calls = provider_transport(monkeypatch, {**content, "extra": "complete original"})
    code, result = gate_command(run_cli, "run-gate", "behavior-review")
    assert code == 4 and result["ok"], result
    data = result["data"]
    capture_path = host.root / data["artifact"]
    captured = capture_path.read_bytes()
    before = snapshot(host.root)
    blocked = host.complete()
    assert not blocked.ok and snapshot(host.root) == before
    assert any(
        isinstance(action.action, ops.AuthoringAction)
        and action.action.work == "review-interpretation"
        for action in blocked.next_actions
    )
    accepted = execute(ops.InterpretReview(payload=interpretation(data, content)))
    assert accepted.ok, accepted.to_envelope()
    assert dispose(
        host.state, [disposition(data["run_id"], "@coverage", status="settled")]
    ).ok
    from heddle.contracts.review_assignments import (
        ArtifactRef,
        AttemptInvocation,
        EngineFailure,
        ReviewAttempt,
        review_attempt_document,
    )
    from tests.operational_model_helpers import read, write
    from tests.readiness_helpers import verify

    evidence_path = host.state.parent / "reviews/supporting-evidence.json"
    evidence_path.write_text('{"fixture":"retained evidence"}\n')
    evidence_relative = evidence_path.relative_to(host.state.parent).as_posix()
    evidence_reference = ArtifactRef(
        evidence_relative,
        hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "evidence",
        mode=stat.S_IMODE(evidence_path.stat().st_mode),
    )
    value = read(host.state)
    value["review_assignments"]["attempts"].append(
        review_attempt_document(
            ReviewAttempt(
                attempt_id="00000000-0000-4000-8000-000000000920",
                assignment_id="retained-evidence:fixture:p1",
                round_number=1,
                reviewer_slot="primary",
                invocation=AttemptInvocation(
                    cli="codex",
                    model="fixture",
                    reasoning_effort="high",
                    sandbox="read-only",
                    input_hash="1" * 64,
                    review_basis_hash="2" * 64,
                    prompt_version="fixture",
                    effective_prompt_sha256="3" * 64,
                ),
                outcome=EngineFailure("fixture", "retained supporting evidence"),
                created_at="2026-09-18T00:00Z",
                artifacts=(evidence_reference,),
            )
        )
    )
    write(host.state, value)
    verify(host.state, "m1", "m2", "acceptance", "smoke")
    completed = host.complete()
    assert completed.ok and completed.data["accepted"], completed.to_envelope()
    report = completed.data["retained_evidence"]
    supported_roles = {role for row in report["artifacts"] for role in row["roles"]}
    assert {
        "accepted-ledger",
        "canonical",
        "capture",
        "evidence",
        "log",
        "review-record",
        "verification-evidence",
        "verification-log",
        "close-suite-log",
    } <= supported_roles
    assert any(
        {"canonical", "review-record"} <= set(row["roles"])
        for row in report["artifacts"]
    )
    captured_row = next(
        row
        for row in report["artifacts"]
        if row["path"] == capture_path.relative_to(host.state.parent).as_posix()
    )
    assert captured_row["roles"] == ["capture"]
    assert len(captured_row["roles"]) == len(set(captured_row["roles"]))
    assert captured_row["kind"] == "file"
    assert captured_row["sha256"] == hashlib.sha256(captured).hexdigest()
    assert captured_row["mode"] == stat.S_IMODE(capture_path.stat().st_mode)
    assert captured_row["archive_member"] == captured_row["path"]
    assert capture_path.read_bytes() == captured and len(calls) == 1
    relative = capture_path.relative_to(host.state.parent).as_posix()
    with tarfile.open(host.archive, "r:gz") as archive:
        assert archive.extractfile(relative).read() == captured
    host.commit_retention()
    accepted_state = host.state.read_bytes()
    before_reads = snapshot(host.root)
    close_calls = tuple(host.suite_calls())
    cleaned = host.complete()
    assert cleaned.ok and cleaned.data["retained_evidence"] == report

    for operation in (
        ops.Status(feature=V7_FEATURE),
        ops.Orient(feature=V7_FEATURE),
        ops.Kickoff(feature=V7_FEATURE),
    ):
        observed = execute(operation)
        assert observed.ok, observed.to_envelope()
        assert observed.data["retained_evidence"] == report

    for command in (
        ("feature", "complete"),
        ("status",),
        ("orient",),
        ("kickoff",),
    ):
        code, output, error = run_cli([*command, "--feature", V7_FEATURE, "--json"])
        assert code == 0, error
        assert json.loads(output)["data"]["retained_evidence"] == report

        code, output, error = run_cli([*command, "--feature", V7_FEATURE])
        assert code == 0
        rendered = output + error
        assert f"retained local evidence: {relative}" in rendered
        assert "roles: capture" in rendered
        assert f"verified archive member: {report['archive']}::{relative}" in rendered

    assert capture_path.read_bytes() == captured
    assert host.state.read_bytes() == accepted_state
    assert snapshot(host.root) == before_reads
    assert tuple(host.suite_calls()) == close_calls
    assert len(calls) == 1


def test_completion_preview_reports_no_attempt_gate_record_once(
    tmp_path, monkeypatch, run_cli
) -> None:
    """AC-3 red: compatible gate evidence derives only review-record."""
    from heddle.kernel.state import GateFact, GateRun, read_state_file
    from heddle.runtime.completion import (
        _retained_evidence_report,
        _retained_evidence_snapshot,
    )
    from tests.tiering_completion_helpers import final_host

    del run_cli  # The compatibility fact is intentionally constructed without a call.
    host = final_host(tmp_path, monkeypatch)
    artifact = "reviews/no-attempt.review.json"
    path = host.state.parent / artifact
    path.parent.mkdir(exist_ok=True)
    path.write_text('{"verdict":"pass"}\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    run = GateRun(
        run_id="00000000-0000-4000-8000-000000000919",
        report_findings=(),
        at="2026-09-18T00:00Z",
        cli="codex",
        artifact=artifact,
        input_hash="1" * 64,
        verdict={"status": "pass", "rerun_recommended": False},
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
        artifact_sha256=digest,
    )
    state = replace(
        read_state_file(host.state),
        gates=(GateFact("behavior-review", "feature", (run,)),),
    )
    observed = _retained_evidence_snapshot(host.root, host.state.parent, state)

    report = _retained_evidence_report(
        state,
        observed,
        workspace=f"plans/{V7_FEATURE}/",
        archive=f"docs/gate-trajectories/.raw/{V7_FEATURE}/completion.tar.gz",
        status="pending",
    )

    row = next(item for item in report["artifacts"] if item["path"] == artifact)
    assert row == {
        "path": artifact,
        "roles": ["review-record"],
        "kind": "file",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mode": stat.S_IMODE(path.stat().st_mode),
    }
