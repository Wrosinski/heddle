"""`heddle verify --stale`: one step reruns the actionable stale scopes (AC-12).

Hosts are authored in ``tmp_path`` with real Git and real verification facts;
every scope's status is reached through ``heddle verify`` and real edits. A
concurrent writer or an integrity fault is injected by wrapping the real
verification command runner (``write_path.run_verification_command``), the
existing seam for in-run effects.

Activation seams: ``ops.Verify.stale`` and the ``--stale`` CLI flag
(``stale_operation`` and ``run_stale`` in ``tests/change_impact_verify_helpers``),
and the batch rows ``verifications[]``, ``skipped[]`` and ``unrun``
(``batch_rows``).

Selection reading pinned here: the batch takes readiness's leading verify
actions and runs the actionable stale ones among them in readiness order; a
command-stale, missing or failed scope among them is listed with its reason
and does not end the selection.
"""

from __future__ import annotations

import json

import pytest

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.change_impact_verify_helpers import (
    batch_rows,
    implement_exit_host,
    is_stale_batch,
    milestone_command,
    open_behavior_review,
    review_fix,
    run_stale,
    scopes,
    skip_reasons,
    verify_scope,
)
from tests.operational_model_helpers import FEATURE, read, write
from tests.readiness_helpers import verify


def _mixed_host(tmp_path, monkeypatch):
    """Implement exit with one required scope in each status AC-12 names.

    Readiness order: m1 and m2 content-stale, m3 command-stale, m4 missing,
    m5 failed, acceptance and live source-set-stale.
    """
    host = implement_exit_host(
        tmp_path,
        monkeypatch,
        extra_milestones=(
            ("src/third.py", milestone_command("src/third.py")),
            ("src/fourth.py", milestone_command("src/fourth.py")),
            ("src/fifth.py", "python3 -c 'raise SystemExit(1)'"),
        ),
    )
    value = read(host.state)
    # A declared local assertion, not authorization to call a provider.
    value["commands"]["live_e2e_test"] = "python3 tests/check.py"
    write(host.state, value)
    verify(host.state, "m3", "live")
    execute(ops.Verify(feature=FEATURE, scope="m5"))
    assert read(host.state)["verifications"][-1]["exit_code"] != 0
    (host.root / "src/example.py").write_text("VALUE = 7  # m1 content\n")
    (host.root / "src/other.py").write_text("OTHER = 7  # m2 content\n")
    value = read(host.state)
    third = value["milestones"][2]
    third["verification"]["command"] += " # changed"
    third["owns"].append("src/extra.py")
    write(host.state, value)
    (host.root / "src/extra.py").write_text("EXTRA = 7\n")
    return host


def _stale_host(tmp_path, monkeypatch):
    """Implement exit where m1, m2 and acceptance are content-stale."""
    host = implement_exit_host(tmp_path, monkeypatch)
    (host.root / "src/example.py").write_text("VALUE = 7  # m1 content\n")
    (host.root / "src/other.py").write_text("OTHER = 7  # m2 content\n")
    return host


def _on_command(monkeypatch, marker: str, effect) -> None:
    """Run ``effect`` once, right after the real command naming ``marker``."""
    from heddle.runtime import write_path

    original = write_path.run_verification_command
    fired = []

    def wrapped(root, command, log_path):
        exit_code = original(root, command, log_path)
        if marker in command and not fired:
            fired.append(command)
            effect()
        return exit_code

    monkeypatch.setattr(write_path, "run_verification_command", wrapped)


def _new_facts(state, before: int) -> list[dict]:
    return read(state)["verifications"][before:]


def _verification_files(state) -> set:
    return set((state.parent / "verification").iterdir())


def test_ac12_orient_and_dry_run_cover_the_leading_actionable_stale_scopes(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _mixed_host(tmp_path, monkeypatch)
    statuses = {
        row["scope"]: row["freshness"]
        for row in execute(ops.Status(feature=FEATURE)).data["verification_status"]
    }
    assert statuses == {
        "m1": "content-stale",
        "m2": "content-stale",
        "m3": "command-stale",
        "m4": "missing",
        "m5": "failed",
        "acceptance": "source-set-stale",
        "live": "source-set-stale",
    }, statuses

    actions = [
        item.action for item in execute(ops.Orient(feature=FEATURE)).next_actions
    ]
    assert is_stale_batch(actions[0]), (
        "FAIL AC-12: orient does not route one `verify --stale` in place of the "
        f"leading verify actions: {[ops.action_command(a) for a in actions]}"
    )
    rendered = ops.action_command(actions[0])
    assert "--stale" in rendered.split() and "--scope" not in rendered.split()
    remaining = [verify_scope(action) for action in actions[1:]]
    assert [scope for scope in remaining if scope is not None] == [
        "m3",
        "m4",
        "m5",
        "live",
    ], (
        "FAIL AC-12: the batch action must replace exactly the covered scopes: "
        f"{remaining}"
    )

    before = host.state.read_bytes()
    files = _verification_files(host.state)
    code, envelope = run_stale(run_cli, "--dry-run")
    assert code == 0 and envelope["ok"], envelope
    body = batch_rows(envelope)
    assert envelope["data"]["dry_run"] is True and envelope["data"]["wrote"] is False
    commands = {
        milestone["id"]: milestone["verification"]["command"]
        for milestone in read(host.state)["milestones"]
    }
    commands["acceptance"] = read(host.state)["commands"]["acceptance_test"]
    assert [(row["scope"], row["command"]) for row in body["verifications"]] == [
        ("m1", commands["m1"]),
        ("m2", commands["m2"]),
        ("acceptance", commands["acceptance"]),
    ], f"FAIL AC-12: the dry run must list every command it would run: {body}"
    reasons = skip_reasons(body)
    assert set(reasons) >= {"m3", "m4", "m5", "live"}, reasons
    assert "command-stale" in reasons["m3"]
    assert "missing" in reasons["m4"]
    assert "failed" in reasons["m5"]
    assert host.state.read_bytes() == before
    assert _verification_files(host.state) == files, (
        "FAIL AC-12: a dry run published evidence or wrote a log"
    )


def test_ac12_batch_records_each_selected_scope_and_lists_the_rest(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _mixed_host(tmp_path, monkeypatch)
    state = read(host.state)
    revision, count = state["revision"], len(state["verifications"])

    code, envelope = run_stale(run_cli, "--expect-revision", str(revision))

    assert code == 0 and envelope["ok"], (
        f"FAIL AC-12: a batch whose runs all record fresh facts succeeds: {envelope}"
    )
    body = batch_rows(envelope)
    facts = _new_facts(host.state, count)
    assert [fact["scope"] for fact in facts] == ["m1", "m2", "acceptance"], (
        "FAIL AC-12: only the leading actionable stale scopes run, in readiness "
        f"order; recorded {[fact['scope'] for fact in facts]}"
    )
    # One write per scope: the expected revision is rebound after each.
    assert read(host.state)["revision"] == revision + 3
    rows = body["verifications"]
    assert scopes(rows) == ["m1", "m2", "acceptance"], rows
    for offset, row in enumerate(rows):
        assert row["recorded"] is True and row["status"] == "fresh", row
        assert row["fact_index"] == count + offset, row
        assert row["exit_code"] == 0 and row["log"] == facts[offset]["log"], row
    reasons = skip_reasons(body)
    assert set(reasons) >= {"m3", "m4", "m5", "live"}, reasons
    assert not {"m3", "m4", "m5", "live"} & set(scopes(rows))
    after = {
        row["scope"]: row["freshness"]
        for row in execute(ops.Status(feature=FEATURE)).data["verification_status"]
    }
    assert after["m1"] == after["m2"] == after["acceptance"] == "fresh", after
    assert after["m3"] == "command-stale" and after["live"] == "source-set-stale"


def test_ac12_single_stale_scope_keeps_its_own_verify_action(
    tmp_path, monkeypatch
) -> None:
    """Survivor pin: readiness still routes one stale scope as `verify --scope`."""
    from tests.tiering_completion_helpers import final_host

    host = final_host(tmp_path, monkeypatch, stage="implement", verify_now=False)
    verify(host.state, "m1")
    (host.root / "src/example.py").write_text("VALUE = 7  # m1 content\n")
    actions = [
        item.action for item in execute(ops.Orient(feature=FEATURE)).next_actions
    ]
    assert verify_scope(actions[0]) == "m1", [
        ops.action_command(action) for action in actions
    ]
    assert not any(is_stale_batch(action) for action in actions)


def test_ac12_continues_past_a_failing_run_and_reports_every_row(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _stale_host(tmp_path, monkeypatch)
    # m1 and acceptance now fail their checks; m2 still passes.
    (host.root / "src/example.py").write_text("VALUE = 8\n")
    count = len(read(host.state)["verifications"])

    code, envelope = run_stale(run_cli)

    facts = _new_facts(host.state, count)
    assert [fact["scope"] for fact in facts] == ["m1", "m2", "acceptance"], (
        "FAIL AC-12: the batch must continue past an ordinary failing run; "
        f"recorded {[fact['scope'] for fact in facts]}"
    )
    assert [fact["exit_code"] != 0 for fact in facts] == [True, False, True]
    assert code == 3 and not envelope["ok"], envelope
    assert envelope["error"]["code"] == "verification-failed", envelope
    rows = {row["scope"]: row for row in batch_rows(envelope)["verifications"]}
    assert list(rows) == ["m1", "m2", "acceptance"], rows
    assert rows["m2"]["status"] == "fresh" and rows["m2"]["recorded"] is True
    assert rows["m1"]["status"] == rows["acceptance"]["status"] == "failed"
    assert all(row["recorded"] is True for row in rows.values())


def test_ac12_continues_past_a_passing_run_whose_source_changed_during_it(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _stale_host(tmp_path, monkeypatch)
    count = len(read(host.state)["verifications"])

    def edit_m2_source():
        # A passing edit lands while m2's check runs, so m2's fact is unstable.
        (host.root / "src/other.py").write_text("OTHER = 7  # edited mid-run\n")

    _on_command(monkeypatch, "src/other.py", edit_m2_source)
    code, envelope = run_stale(run_cli)

    facts = _new_facts(host.state, count)
    assert [fact["scope"] for fact in facts] == ["m1", "m2", "acceptance"], (
        "FAIL AC-12: the batch must continue past a recorded unstable run; "
        f"recorded {[fact['scope'] for fact in facts]}"
    )
    assert all(fact["exit_code"] == 0 for fact in facts), facts
    assert code == 3 and not envelope["ok"], (
        f"FAIL AC-12: a recorded non-fresh row fails the batch with exit 3: {envelope}"
    )
    assert envelope["error"]["code"] == "verification-failed", envelope
    rows = {row["scope"]: row for row in batch_rows(envelope)["verifications"]}
    assert list(rows) == ["m1", "m2", "acceptance"], rows
    assert rows["m2"]["recorded"] is True and rows["m2"]["exit_code"] == 0, rows
    assert rows["m2"]["status"] == "unstable", (
        f"FAIL AC-12: the m2 row reports its recorded unstable status: {rows['m2']}"
    )
    assert rows["m1"]["status"] == rows["acceptance"]["status"] == "fresh", rows


def test_ac12_reassesses_each_scope_before_running_it(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _stale_host(tmp_path, monkeypatch)
    count = len(read(host.state)["verifications"])

    def restore_m2_source():
        # m2's recorded bytes return while m1 runs, so m2 is fresh again.
        (host.root / "src/other.py").write_text("OTHER = 7\n")

    _on_command(monkeypatch, "src/example.py", restore_m2_source)
    code, envelope = run_stale(run_cli)

    assert code == 0 and envelope["ok"], envelope
    recorded = [fact["scope"] for fact in _new_facts(host.state, count)]
    assert recorded == ["m1", "acceptance"], (
        "FAIL AC-12: each scope is reassessed before it runs; a scope that "
        f"became fresh is skipped, recorded {recorded}"
    )
    assert "m2" in skip_reasons(batch_rows(envelope))


def test_ac12_stops_at_a_concurrent_write_keeping_recorded_rows(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _stale_host(tmp_path, monkeypatch)
    count = len(read(host.state)["verifications"])

    def concurrent_write():
        written = execute(ops.CommandsSet("lint_command", "true", feature=FEATURE))
        assert written.ok, written.to_envelope()

    _on_command(monkeypatch, "src/other.py", concurrent_write)
    code, envelope = run_stale(run_cli)

    recorded = [fact["scope"] for fact in _new_facts(host.state, count)]
    assert recorded == ["m1"], (
        "FAIL AC-12: the batch stops at a conflict and keeps the rows recorded "
        f"before it; recorded {recorded}"
    )
    assert code == 5 and envelope["error"]["code"] == "conflict", envelope
    body = batch_rows(envelope)
    rows = {row["scope"]: row for row in body["verifications"]}
    assert rows["m1"]["recorded"] is True, rows
    assert "unrun" in body and "acceptance" in scopes(body["unrun"]), (
        f"FAIL AC-12: scopes after the stop are listed as unrun: {body}"
    )


def test_ac12_stops_at_an_integrity_failure_listing_unrun_scopes(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _stale_host(tmp_path, monkeypatch)
    state = read(host.state)
    count = len(state["verifications"])
    m2_evidence = next(
        fact["evidence"] for fact in state["verifications"] if fact["scope"] == "m2"
    )

    def damage_m2_evidence():
        for endpoint in ("before", "after"):
            (host.state.parent / m2_evidence[endpoint]["artifact"]).unlink(
                missing_ok=True
            )

    _on_command(monkeypatch, "src/example.py", damage_m2_evidence)
    code, envelope = run_stale(run_cli)

    recorded = [fact["scope"] for fact in _new_facts(host.state, count)]
    assert recorded == ["m1"], (
        "FAIL AC-12: the batch stops at an integrity failure and keeps the rows "
        f"recorded before it; recorded {recorded}"
    )
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid", envelope
    body = batch_rows(envelope)
    assert {"m2", "acceptance"} <= set(scopes(body.get("unrun", []))), (
        f"FAIL AC-12: scopes after the stop are listed as unrun: {body}"
    )


def test_ac12_unauthorized_batch_refuses_as_one_scope_would_before_any_run(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _stale_host(tmp_path, monkeypatch)
    value = read(host.state)
    # Authority ends before implement, so every write is blocked.
    value["authorized_through"] = "scaffold"
    value.pop("authorizations", None)
    write(host.state, value)
    before = host.state.read_bytes()
    files = _verification_files(host.state)
    single_code, out, _err = run_cli(
        ["verify", "--scope", "m1", "--feature", FEATURE, "--json"]
    )
    assert json.loads(out)["error"]["code"] == "not-authorized", out

    code, envelope = run_stale(run_cli)

    assert code == single_code and envelope["error"]["code"] == "not-authorized", (
        "FAIL AC-12: lost authority stops the batch with the cause's existing "
        f"exit code {single_code}: {envelope}"
    )
    assert host.state.read_bytes() == before
    assert _verification_files(host.state) == files


def test_ac12_stale_expected_revision_is_a_conflict_before_any_run(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _stale_host(tmp_path, monkeypatch)
    stale_revision = read(host.state)["revision"] - 1
    before = host.state.read_bytes()

    code, envelope = run_stale(run_cli, "--expect-revision", str(stale_revision))

    assert code == 5 and envelope["error"]["code"] == "conflict", envelope
    assert host.state.read_bytes() == before


def _waived_acceptance_host(tmp_path, monkeypatch):
    """Complete: acceptance stale and waived, live declared and stale."""
    from tests.change_impact_verify_helpers import propose_waiver, resolve_waiver
    from tests.tiering_completion_helpers import final_host

    host = final_host(tmp_path, monkeypatch)
    value = read(host.state)
    value["commands"]["live_e2e_test"] = "python3 tests/check.py"
    write(host.state, value)
    verify(host.state, "live")
    review_fix(host.root, 8)
    verify(host.state, "smoke")
    assert propose_waiver().ok
    decision_id = read(host.state)["decisions"][-1]["id"]
    assert resolve_waiver(decision_id).ok
    return host, {"acceptance": "waive"}


def _degraded_smoke_host(tmp_path, monkeypatch):
    """Complete: a failed smoke fact whose degraded acceptance is resolved."""
    from tests.change_impact_verify_helpers import accept_degraded_smoke
    from tests.tiering_completion_helpers import final_host

    host = final_host(tmp_path, monkeypatch)
    accept_degraded_smoke(host.root, host.state)
    return host, {"smoke": "degraded"}


@pytest.mark.parametrize(
    "build",
    [
        # The owner waived the stale lane's rerun.
        _waived_acceptance_host,
        # The owner accepted the failed smoke run as degraded.
        _degraded_smoke_host,
    ],
    ids=["waived-acceptance", "degraded-smoke"],
)
def test_ac12_nothing_selected_succeeds_and_lists_skipped_scopes(
    tmp_path, monkeypatch, run_cli, build
) -> None:
    host, owner_reasons = build(tmp_path, monkeypatch)
    required = [
        row["scope"]
        for row in execute(ops.Status(feature=FEATURE)).data["verification_status"]
    ]
    assert not any(
        verify_scope(item.action) in owner_reasons
        for item in execute(ops.Orient(feature=FEATURE)).next_actions
    )
    count = len(read(host.state)["verifications"])

    code, envelope = run_stale(run_cli)

    assert code == 0 and envelope["ok"], (
        f"FAIL AC-12: a batch with nothing selected succeeds: {envelope}"
    )
    body = batch_rows(envelope)
    assert body["verifications"] == []
    reasons = skip_reasons(body)
    assert sorted(reasons) == sorted(required), (
        f"FAIL AC-12: every scope is listed once with its reason: {reasons}"
    )
    for scope, word in owner_reasons.items():
        assert word in reasons[scope].lower(), (
            f"FAIL AC-12: the {scope} skip names the owner's decision: {reasons}"
        )
    assert _new_facts(host.state, count) == []


def test_ac12_acceptance_behind_open_review_work_is_not_run(
    tmp_path, monkeypatch, run_cli
) -> None:
    host, _origin = open_behavior_review(tmp_path, monkeypatch, run_cli)
    review_fix(host.root, 8)
    count = len(read(host.state)["verifications"])

    code, envelope = run_stale(run_cli)

    recorded = [fact["scope"] for fact in _new_facts(host.state, count)]
    assert recorded == ["smoke"], (
        "FAIL AC-12: only the leading smoke rerun runs; the acceptance rerun "
        f"waits behind open review work, recorded {recorded}"
    )
    assert code == 0 and envelope["ok"], envelope
    assert "acceptance" in skip_reasons(batch_rows(envelope))


def test_ac12_stale_and_scope_are_mutually_exclusive(
    run_cli, tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    code, out, _err = run_cli(["verify", "--stale", "--scope", "m1", "--json"])

    error = json.loads(out)["error"]
    assert code == 2 and error["code"] == "usage", error
    assert (
        "unrecognized" not in error["message"]
        and "--stale" in error["message"]
        and "--scope" in error["message"]
    ), f"FAIL AC-12: --stale and --scope must be refused as exclusive: {error}"


def test_stale_rows_report_each_run_and_name_a_stopping_limit(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = _stale_host(tmp_path, monkeypatch)
    # m1's and acceptance's checks execute this source, which now outlasts the
    # hard limit; m2's does not.
    (host.root / "src/example.py").write_text(
        "import time\ntime.sleep(30)\nVALUE = 7\n"
    )
    monkeypatch.setenv("HEDDLE_VERIFY_HARD_TIMEOUT_SECONDS", "2")

    code, envelope = run_stale(run_cli)

    assert code == 3 and envelope["error"]["code"] == "verification-failed", envelope
    rows = {row["scope"]: row for row in batch_rows(envelope)["verifications"]}
    assert {scope: row["run"]["termination"] for scope, row in rows.items()} == {
        "m1": "timeout",
        "m2": "completed",
        "acceptance": "timeout",
    }, rows
    assert rows["m1"]["counts"] is None
    assert rows["m2"]["run"]["limits"]["hard"] == {
        "seconds": 2.0,
        "source": "env",
        "override": "HEDDLE_VERIFY_HARD_TIMEOUT_SECONDS",
    }
    hint = envelope["error"]["hint"]
    for scope in ("m1", "acceptance"):
        assert (
            "set HEDDLE_VERIFY_HARD_TIMEOUT_SECONDS above the run's expected "
            f"duration to allow longer, then retry verification for {scope}"
        ) in hint, hint
    assert "repair the check" not in hint and "for m2" not in hint, hint
    assert [item["reason"] for item in envelope["next_actions"]] == [
        f"raise the limit before retrying verification for {scope}"
        for scope in ("m1", "acceptance")
    ], envelope["next_actions"]


def test_stale_dry_run_shows_the_effective_limits(
    tmp_path, monkeypatch, run_cli
) -> None:
    _stale_host(tmp_path, monkeypatch)
    monkeypatch.delenv("HEDDLE_VERIFY_HARD_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("HEDDLE_VERIFY_INACTIVITY_TIMEOUT_SECONDS", "60")

    code, envelope = run_stale(run_cli, "--dry-run")

    assert code == 0 and envelope["ok"], envelope
    limits = envelope["data"]["limits"]
    assert (limits["hard"]["seconds"], limits["hard"]["source"]) == (2700.0, "default")
    assert (limits["inactivity"]["seconds"], limits["inactivity"]["source"]) == (
        60.0,
        "env",
    )


def test_a_skipped_witness_lane_states_its_rerun_route(
    tmp_path, monkeypatch, run_cli
) -> None:
    _mixed_host(tmp_path, monkeypatch)

    code, envelope = run_stale(run_cli, "--dry-run")

    assert code == 0 and envelope["ok"], envelope
    reasons = skip_reasons(batch_rows(envelope))
    assert reasons["live"].startswith("source-set-stale; the live lane reruns only ")
    assert (
        "; rerun-only: at implement exit only formatting-only changes let the "
        "owner accept the earlier witness"
    ) in reasons["live"], reasons["live"]
    assert "rerun-only" not in reasons["m3"], reasons["m3"]


def test_stale_human_output_prints_each_rows_run(
    tmp_path, monkeypatch, run_cli
) -> None:
    _stale_host(tmp_path, monkeypatch)

    code, out, _err = run_cli(["verify", "--stale", "--feature", FEATURE])

    assert code == 0, out
    for scope in ("m1", "m2", "acceptance"):
        line = next(
            (line for line in out.splitlines() if line.startswith(f"  run {scope}: ")),
            None,
        )
        assert line is not None and line.startswith(f"  run {scope}: completed in "), (
            out
        )
