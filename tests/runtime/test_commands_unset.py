"""
native removal: native single-command removal, without losing history or required proof.
"""

from copy import deepcopy

import pytest
import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.operational_model_helpers import FEATURE
from tests.tiering_review_helpers import current_host


def remove(key="live_e2e_test", **kwargs):
    operation = getattr(ops, "CommandsUnset", None)
    assert operation is not None, (
        "native removal requires a native command-removal operation"
    )
    return execute(operation(key, feature=FEATURE, **kwargs))


def configured_host(tmp_path, monkeypatch):
    host, path = current_host(tmp_path, monkeypatch)
    value = yaml.safe_load(path.read_text())
    value["commands"] = {
        "live_e2e_test": "python3 tests/obsolete_comparison.py",
        "acceptance_test": "python3 tests/acceptance.py",
        "smoke_test": "python3 tests/smoke.py",
        "lint_command": "ruff check tests",
    }
    value["sessions"] = [
        {
            "started_at": "2026-09-11T00:00Z",
            "ended_at": "2026-09-11T00:01Z",
            "completed": ["Historical work"],
            "started": [],
            "key_context": "Retain this history",
            "next_steps": "Continue",
            "blockers": [],
        }
    ]
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    return host, path


def test_unset_removes_only_the_selected_key_and_replays_without_writing(
    tmp_path, monkeypatch
):
    _host, path = configured_host(tmp_path, monkeypatch)
    before = yaml.safe_load(path.read_text())
    result = remove(expect_revision=before["revision"])
    assert result.ok, result.to_envelope()
    assert result.data["command_key"] == "live_e2e_test"
    assert result.data["wrote"] is True
    after = yaml.safe_load(path.read_text())
    expected = deepcopy(before)
    del expected["commands"]["live_e2e_test"]
    expected["revision"] += 1
    expected["updated"] = after["updated"]
    assert after == expected
    published = path.read_bytes()
    replay = remove(expect_revision=after["revision"])
    assert replay.ok and replay.data["wrote"] is False, replay.to_envelope()
    assert replay.data["revision"] == after["revision"]
    assert path.read_bytes() == published


def test_unset_cli_preview_and_apply_share_the_typed_command_contract(
    tmp_path, monkeypatch, run_cli, envelope_tools
):
    _host, path = configured_host(tmp_path, monkeypatch)
    before = path.read_bytes()
    command = ["commands", "unset", "live_e2e_test", "--feature", FEATURE, "--json"]
    code, out, _err = run_cli([*command, "--dry-run"])
    preview = envelope_tools.parse(out)
    assert code == 0, preview
    assert preview["data"]["dry_run"] is True
    assert preview["data"]["mutation_summary"]["would_write"] is True
    assert path.read_bytes() == before
    code, out, _err = run_cli(command)
    applied = envelope_tools.parse(out)
    assert code == 0, applied
    assert applied["data"]["revision"] == preview["data"]["revision"]
    assert "live_e2e_test" not in yaml.safe_load(path.read_text())["commands"]
    rendered = ops.operation_command(
        ops.CommandsUnset("live_e2e_test", feature=FEATURE)
    )
    assert rendered == f"heddle commands unset live_e2e_test --feature {FEATURE}"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["unknown"],
        ["live_e2e_test", "smoke_test"],
        ["live_e2e_test", "--command", "true"],
        ["live_e2e_test", "--unknown"],
    ],
)
def test_unset_usage_refuses_before_state_resolution(
    tmp_path, monkeypatch, run_cli, envelope_tools, arguments
):
    monkeypatch.chdir(tmp_path)  # No host: invalid argv must not resolve state.
    code, out, _err = run_cli(["commands", "unset", *arguments, "--json"])
    result = envelope_tools.parse(out)
    assert code == 2 and result["error"]["code"] == "usage", result


def test_unset_typed_unknown_key_and_stale_revision_preserve_state(
    tmp_path, monkeypatch
):
    _host, path = configured_host(tmp_path, monkeypatch)
    before = path.read_bytes()
    unknown = remove("unknown")
    assert not unknown.ok and unknown.error.code == "usage"
    assert path.read_bytes() == before
    stale = remove(expect_revision=yaml.safe_load(before)["revision"] + 1)
    assert not stale.ok and stale.error.code == "conflict"
    assert path.read_bytes() == before


def test_unset_blank_declaration_is_real_removal_and_core_does_not_mutate_input():
    from heddle.kernel.write_path import unset_command

    source = {"commands": {"dev_setup": "", "smoke_test": "check smoke"}}
    before = deepcopy(source)
    result = unset_command(source, key="dev_setup")
    assert result == {"commands": {"smoke_test": "check smoke"}}
    assert source == before
    assert unset_command(result, key="dev_setup") is result


def test_unset_preserves_a_concurrent_writer_instead_of_deleting_its_command(
    tmp_path, monkeypatch
):
    from heddle.kernel.write_path import set_command
    from heddle.runtime import write_path

    _host, path = configured_host(tmp_path, monkeypatch)
    before = yaml.safe_load(path.read_text())
    commit = write_path.commit_state

    def race(state_path, *, expect_revision, transform):
        commit(
            state_path,
            expect_revision=expect_revision,
            transform=lambda value: set_command(
                value, key="live_e2e_test", shell_command="replacement check"
            ),
        )
        return commit(state_path, expect_revision=expect_revision, transform=transform)

    monkeypatch.setattr(write_path, "commit_state", race)
    result = remove()
    assert not result.ok and result.error.code == "conflict", result.to_envelope()
    after = yaml.safe_load(path.read_text())
    assert after["commands"]["live_e2e_test"] == "replacement check"
    assert after["revision"] == before["revision"] + 1


@pytest.mark.parametrize("key", ["live_e2e_test", "acceptance_test", "smoke_test"])
def test_unset_preserves_history_and_only_optional_live_leaves_required_scopes(
    tmp_path, monkeypatch, key
):
    from tests.readiness_helpers import current_readiness, verify
    from tests.tiering_completion_helpers import final_host

    host = final_host(tmp_path, monkeypatch)
    assert execute(
        ops.CommandsSet("live_e2e_test", "python3 tests/check.py", feature=FEATURE)
    ).ok
    verify(host.state, "live")  # Inspected temporary assertion, not a provider.
    before = yaml.safe_load(host.state.read_text())
    logs = {
        host.state.parent / row["log"]: (host.state.parent / row["log"]).read_bytes()
        for row in before["verifications"]
    }
    assert any(row["scope"] == "live" for row in before["verifications"])
    assert remove(key).ok
    after = yaml.safe_load(host.state.read_text())
    assert after["verifications"] == before["verifications"]
    assert all(p.read_bytes() == content for p, content in logs.items())
    readiness = current_readiness(host.root)
    statuses = {row.scope: row.status for row in readiness.verifications}
    assert {"acceptance", "smoke"} <= set(statuses)
    if key == "live_e2e_test":
        assert "live" not in statuses
    else:
        assert statuses[key.removesuffix("_test")] == "missing"
    refused = execute(
        ops.Verify(
            scope=key.removesuffix("_test") if key != "live_e2e_test" else "live",
            feature=FEATURE,
        )
    )
    assert not refused.ok
    assert (
        yaml.safe_load(host.state.read_text())["verifications"]
        == before["verifications"]
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_unset_accepted_history_refuses_even_an_absent_key(
    tmp_path, monkeypatch, dry_run
):
    from tests.tiering_completion_helpers import final_host

    host = final_host(tmp_path, monkeypatch)
    accepted = host.complete()
    assert accepted.ok and accepted.data["accepted"], accepted.to_envelope()
    before = host.state.read_bytes()
    result = remove(dry_run=dry_run)
    assert not result.ok and result.error.code == "workspace-invalid"
    assert "accepted" in result.error.message and "immutable" in result.error.message
    assert host.state.read_bytes() == before


def test_unset_is_an_explicit_discoverable_single_key_write(run_cli, envelope_tools):
    code, out, _err = run_cli(["commands", "unset", "--help", "--json"])
    result = envelope_tools.parse(out)
    assert code == 0, result
    from heddle.runtime.contracts import build_manifest

    row = next(
        item
        for item in build_manifest()["commands"]
        if item["name"] == "commands unset"
    )
    assert row["mutating"] is True and row["dry_run"] is True
    assert [(item["name"], item["required"]) for item in row["args"]] == [
        ("command-key", True)
    ]
    assert {item["name"] for item in row["flags"]} == {
        "--feature",
        "--json",
        "--dry-run",
        "--expect-revision",
    }
