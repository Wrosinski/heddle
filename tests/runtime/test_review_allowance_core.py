"""AC-1 through AC-4: public review-round allowance policy facade."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
import yaml

from tests.tiering_helpers import entry, invoke, snapshot
from tests.tiering_review_helpers import V7_FEATURE, current_host

APPROVAL = "Owner authorizes an absolute five-round ceiling for this review role"


def _state(path):
    return yaml.safe_load(path.read_text())


def _allowance(path, *, role="spec-review", limit=5, approval=APPROVAL, **kwargs):
    return invoke(
        "ReviewAllowance",
        role=role,
        limit=limit,
        approval=approval,
        feature=V7_FEATURE,
        expect_revision=_state(path)["revision"],
        **kwargs,
    )


def _gate(policy, role):
    return next(row for row in policy["entries"] if row["role"] == role)


def _cli_args(path):
    return [
        "review",
        "allowance",
        "--role",
        "spec-review",
        "--limit",
        "5",
        "--approval",
        APPROVAL,
        "--feature",
        V7_FEATURE,
        "--expect-revision",
        str(_state(path)["revision"]),
        "--json",
    ]


def test_ac1_public_command_accepts_only_the_complete_bounded_argument_set(
    tmp_path, monkeypatch, run_cli
):
    from heddle.contracts import operations as ops
    from heddle.runtime.contracts import COMMAND_SURFACE

    host, path = current_host(tmp_path, monkeypatch)
    command = next(row for row in COMMAND_SURFACE if row.name == "review allowance")
    assert command.mutating and command.dry_run
    assert {flag.name for flag in command.flags} == {
        "--role",
        "--limit",
        "--approval",
        "--feature",
        "--expect-revision",
        "--dry-run",
        "--json",
    }
    assert ops.operation_type_name(ops.ReviewAllowance) == "review allowance"
    with pytest.raises(TypeError):
        ops.ReviewAllowance("spec-review", 5, APPROVAL)

    before = snapshot(host)
    for feature in ("", "different-feature"):
        invalid_target = invoke(
            "ReviewAllowance",
            role="spec-review",
            limit=5,
            approval=APPROVAL,
            feature=feature,
            expect_revision=_state(path)["revision"],
        )
        assert not invalid_target.ok
        assert snapshot(host) == before
    code, out, _err = run_cli(
        [
            "review",
            "allowance",
            "--limit",
            "5",
            "--approval",
            APPROVAL,
            "--feature",
            V7_FEATURE,
            "--expect-revision",
            str(_state(path)["revision"]),
            "--json",
        ]
    )
    envelope = json.loads(out)
    assert code == 2 and not envelope["ok"]
    assert "--role" in (envelope["error"]["message"] + envelope["error"]["hint"])
    assert snapshot(host) == before

    code, out, _err = run_cli(_cli_args(path))
    succeeded = json.loads(out)
    assert code == 0 and succeeded["ok"], succeeded
    assert succeeded["data"]["quality_round_limit"] == {
        "before": 2,
        "after": 5,
        "added": 3,
    }


def test_ac2_valid_increase_appends_one_complete_policy_revision(tmp_path, monkeypatch):
    _host, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", limit=2)},
    )
    before = _state(path)
    old_policy = deepcopy(before["feature_policy"])
    preview = _allowance(path, dry_run=True)
    assert preview.ok, preview.to_envelope()
    result = _allowance(path)
    assert result.ok, result.to_envelope()
    after = _state(path)

    assert after["revision"] == before["revision"] + 1
    assert after["feature_policy"]["revision"] == old_policy["revision"] + 1
    assert after["feature_policy"]["approval"] == APPROVAL
    assert _gate(after["feature_policy"], "spec-review")["limit"] == 5
    expected = deepcopy(old_policy)
    expected.update(revision=2, approval=APPROVAL)
    _gate(expected, "spec-review")["limit"] = 5
    assert after["feature_policy"] == expected
    assert after["policy_history"][-2:] == [old_policy, expected]
    policy_keys = {"revision", "updated", "feature_policy", "policy_history"}
    for key in set(before) - policy_keys:
        assert after[key] == before[key], f"unexpected allowance side effect in {key}"

    data = result.data
    assert data["role"] == "spec-review"
    assert data["quality_round_limit"] == {"before": 2, "after": 5, "added": 3}
    assert data["policy_revision"] == {"before": 1, "after": 2}
    assert data["wrote"] is True and data["readback"] is False
    transaction_fields = {"revision", "wrote", "dry_run", "readback"}
    assert {k: v for k, v in preview.data.items() if k not in transaction_fields} == {
        k: v for k, v in data.items() if k not in transaction_fields
    }


def test_ac3_dry_run_exact_readback_and_stale_revision_are_safe(tmp_path, monkeypatch):
    host, path = current_host(tmp_path, monkeypatch)
    before = snapshot(host)
    preview = _allowance(path, dry_run=True)
    assert preview.ok and preview.data["wrote"] is True, preview.to_envelope()
    assert preview.data["dry_run"] is True and preview.data["readback"] is False
    assert snapshot(host) == before

    old_revision = _state(path)["revision"]
    written = _allowance(path)
    assert written.ok and written.data["wrote"] is True
    after_write = snapshot(host)
    current_revision = _state(path)["revision"]
    replay = invoke(
        "ReviewAllowance",
        role="spec-review",
        limit=5,
        approval=APPROVAL,
        feature=V7_FEATURE,
        expect_revision=current_revision,
    )
    assert replay.ok, replay.to_envelope()
    assert replay.data["wrote"] is False and replay.data["readback"] is True
    transaction_fields = {"revision", "wrote", "dry_run", "readback"}
    assert {k: v for k, v in replay.data.items() if k not in transaction_fields} == {
        k: v for k, v in written.data.items() if k not in transaction_fields
    }
    assert snapshot(host) == after_write

    stale = invoke(
        "ReviewAllowance",
        role="spec-review",
        limit=6,
        approval="Owner authorizes six rounds",
        feature=V7_FEATURE,
        expect_revision=old_revision,
    )
    assert not stale.ok and stale.error.code == "conflict"
    assert snapshot(host) == after_write


def test_ac3_publish_reobservation_race_is_conflict_and_write_free(
    tmp_path, monkeypatch
):
    from heddle.contracts.result import Conflict
    from heddle.runtime import feature_policy

    host, path = current_host(tmp_path, monkeypatch)
    before = snapshot(host)

    def collide(*_args, **_kwargs):
        raise Conflict("state revision changed during allowance publication")

    monkeypatch.setattr(feature_policy, "commit_state", collide)
    result = _allowance(path)
    assert not result.ok and result.error.code == "conflict"
    assert snapshot(host) == before


@pytest.mark.parametrize(
    ("selection", "role", "limit", "approval", "message"),
    [
        (entry("spec-review", limit=3), "spec-review", 2, APPROVAL, "increase"),
        (
            entry("spec-review", limit=3),
            "spec-review",
            3,
            "different owner record",
            "approval",
        ),
        (
            entry("behavior-review", mode="off", limit=None, minimum_rounds=0),
            "behavior-review",
            3,
            APPROVAL,
            "upper-limit",
        ),
        (
            entry("spec-review", mode="convergence", limit=None),
            "spec-review",
            3,
            APPROVAL,
            "upper-limit",
        ),
    ],
)
def test_ac4_narrow_surface_refuses_broader_policy_changes_atomically(
    tmp_path, monkeypatch, selection, role, limit, approval, message
):
    host, path = current_host(
        tmp_path, monkeypatch, overrides={selection.role: selection}
    )
    before = snapshot(host)
    result = _allowance(path, role=role, limit=limit, approval=approval)
    assert not result.ok
    error = (result.error.message + result.error.hint).lower()
    assert message in error and "feature policy" in error
    assert snapshot(host) == before


def test_ac4_unknown_role_refuses_without_mutation(tmp_path, monkeypatch):
    host, path = current_host(tmp_path, monkeypatch)
    before = snapshot(host)
    result = _allowance(path, role="unknown-review-role")
    error = (result.error.message + result.error.hint).lower()
    assert not result.ok and "role" in error and "feature policy" in error
    assert snapshot(host) == before


@pytest.mark.parametrize("state_case", ["missing-policy", "corrupt-policy", "complete"])
def test_ac4_unavailable_policy_or_completed_feature_refuses_atomically(
    tmp_path, monkeypatch, state_case
):
    host, path = current_host(tmp_path, monkeypatch)
    value = _state(path)
    if state_case == "missing-policy":
        value.pop("feature_policy")
    elif state_case == "corrupt-policy":
        _gate(value["feature_policy"], "spec-review")["mode"] = "invalid"
    else:
        value["completion"] = {
            "accepted_at": "2026-09-13T00:00:00Z",
            "authorization_index": 0,
            "verification_indexes": [],
            "review_run_ids": [],
            "accepted_smoke_decision_id": None,
            "spec_stamp": {
                "before_sha256": "0" * 64,
                "after_sha256": "1" * 64,
                "executable": False,
            },
            "close_suite": None,
        }
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    before = snapshot(host)
    result = _allowance(path)
    assert not result.ok
    assert snapshot(host) == before


@pytest.mark.parametrize("bad_limit", ["not-an-integer", "0", "-2"])
def test_ac1_invalid_cli_limit_is_usage_and_write_free(
    tmp_path, monkeypatch, run_cli, bad_limit
):
    host, path = current_host(tmp_path, monkeypatch)
    before = snapshot(host)
    code, out, _err = run_cli(
        [
            "review",
            "allowance",
            "--role",
            "spec-review",
            "--limit",
            bad_limit,
            "--approval",
            APPROVAL,
            "--feature",
            V7_FEATURE,
            "--expect-revision",
            str(_state(path)["revision"]),
            "--json",
        ]
    )
    envelope = json.loads(out)
    assert code == 2 and not envelope["ok"]
    error = envelope["error"]["message"] + envelope["error"]["hint"]
    assert "limit" in error.lower() and "positive integer" in error.lower()
    assert snapshot(host) == before


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing-approval", "approval"),
        ("duplicate-role", "role"),
        ("unknown-flag", "unknown"),
        ("blank-approval", "approval"),
        ("approval-is-dry-run", "approval"),
        ("bad-revision", "revision"),
    ],
)
def test_ac1_other_malformed_cli_inputs_are_usage_and_write_free(
    tmp_path, monkeypatch, run_cli, case, expected
):
    host, path = current_host(tmp_path, monkeypatch)
    argv = _cli_args(path)
    if case == "missing-approval":
        index = argv.index("--approval")
        del argv[index : index + 2]
    elif case == "duplicate-role":
        argv[2:2] = ["--role", "spec-review"]
    elif case == "unknown-flag":
        argv[2:2] = ["--unknown-allowance", "value"]
    elif case == "blank-approval":
        argv[argv.index("--approval") + 1] = "   "
    elif case == "approval-is-dry-run":
        argv[argv.index("--approval") + 1] = "--dry-run"
    else:
        argv[argv.index("--expect-revision") + 1] = "not-an-integer"
    before = snapshot(host)
    code, out, _err = run_cli(argv)
    envelope = json.loads(out)
    assert code == 2 and not envelope["ok"]
    error = envelope["error"]["message"] + envelope["error"]["hint"]
    assert expected in error.lower()
    assert snapshot(host) == before
