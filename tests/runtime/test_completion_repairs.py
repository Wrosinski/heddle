"""F3 acceptance: completion reports one ordered set of independent repairs."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.completion_helpers import CompletionHost
from tests.content_identity_helpers import git
from tests.operational_model_helpers import FEATURE, read, write
from tests.readiness_helpers import verify
from tests.tiering_completion_helpers import final_host
from tests.tiering_helpers import snapshot


def _nondefault_final_host(tmp_path, monkeypatch) -> CompletionHost:
    host = final_host(tmp_path, monkeypatch, verify_now=False)
    config_path = host.root / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text())
    config.setdefault("layout", {})["plans"] = "journey-plans"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    git(host.root, "add", ".heddle.yaml")
    git(host.root, "commit", "-qm", "use nondefault synthetic workspace")

    source = host.root / "plans"
    target = host.root / "journey-plans"
    target.mkdir()
    (source / FEATURE).rename(target / FEATURE)
    for suffix in ("decision-journal.md", "friction-retrospective.md"):
        (source / f"{FEATURE}.{suffix}").rename(target / f"{FEATURE}.{suffix}")
    git(host.root, "add", "-A", "-f", "plans", "journey-plans")
    git(host.root, "commit", "-qm", "move synthetic workflow workspace")
    state_path = target / FEATURE / "state.yaml"
    value = read(state_path)
    value["schema"] = "heddle.state/v10"
    value["source_baseline"] = {
        "kind": "git-commit",
        "oid": git(host.root, "rev-parse", "HEAD").decode().strip(),
    }
    write(state_path, value)
    verify(state_path, "m1", "m2", "acceptance", "smoke")
    return CompletionHost(
        host.root,
        state_path,
        state_path.parent / "verification" / "close-suite.calls",
    )


def _retro(host) -> Path:
    return host.root / "plans" / f"{FEATURE}.friction-retrospective.md"


def _journal(host) -> Path:
    return host.root / "plans" / f"{FEATURE}.decision-journal.md"


def _seed_policy_decision(host) -> None:
    value = read(host.state)
    value["decisions"].append(
        {
            "id": "f3-policy#1",
            "kind": "policy",
            "rationale": "resolved by policy during implementation",
            "resolved_at": "2026-09-14T18:00Z",
            "source": "f3-policy#1",
            "title": "a policy decision requiring a journal entry",
            "status": "resolved",
            "resolution": {
                "kind": "policy",
                "choice": "retain one completion owner",
                "basis": "engineering-principles",
                "reversibility": "reversible",
                "confidence": "high",
                "review_flag": "",
                "scope": "complete",
            },
            "resolution_source": "policy",
            "routes_to": [f"plans/{FEATURE}/plan.md"],
            "created_at": "2026-09-14T18:00Z",
        }
    )
    write(host.state, value)


def _remove_frontmatter_fence(host) -> None:
    text = host.spec.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    host.spec.write_text(text.removeprefix("---\n"), encoding="utf-8")


def _repairs(result) -> list[dict[str, str]]:
    assert not result.ok and result.data is None and result.error is not None
    value = result.error.details["repairs"]
    assert isinstance(value, list)
    return value


def _set_close_suite(host, source: str) -> None:
    config_path = host.root / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.setdefault("autopilot", {})["test_command"] = (
        f"{sys.executable} -c {shlex.quote(source)}"
    )
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    state = read(host.state)
    if ".heddle.yaml" not in state["milestones"][0]["owns"]:
        state["milestones"][0]["owns"].append(".heddle.yaml")
        write(host.state, state)
    verify(host.state, "m1", "m2", "acceptance", "smoke")


def _remove_close_suite(host) -> None:
    config_path = host.root / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["autopilot"].pop("test_command")
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    state = read(host.state)
    if ".heddle.yaml" not in state["milestones"][0]["owns"]:
        state["milestones"][0]["owns"].append(".heddle.yaml")
        write(host.state, state)
    verify(host.state, "m1", "m2", "acceptance", "smoke")


def test_f3_a1_preview_and_apply_report_every_independent_repair_without_effects(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _retro(host).write_text("# Retrospective without required sections\n")
    _seed_policy_decision(host)
    _journal(host).write_text("# Decision Journal\n")
    _remove_frontmatter_fence(host)
    before = snapshot(host.root)

    preview = host.complete(dry_run=True)
    applied = host.complete()

    expected = [
        ("retrospective", "missing-headings"),
        ("decision-journal", "audit-incomplete"),
        ("spec-stamp", "invalid-frontmatter"),
    ]
    for result in (preview, applied):
        repairs = _repairs(result)
        assert [(row["check"], row["cause"]) for row in repairs] == expected
        assert result.error.code == "internal"
        assert int(result.exit_code) == 3
        assert len(result.next_actions) == len(repairs)
    assert preview.error.details == applied.error.details
    assert snapshot(host.root) == before
    assert host.suite_calls() == []


def test_local_history_missing_archive_input_refuses_republication(
    tmp_path, monkeypatch
) -> None:
    """AC-6 red: first archive publication cannot omit indexed input."""
    from heddle.contracts.review_assignments import (
        ArtifactRef,
        AttemptInvocation,
        EngineFailure,
        ReviewAttempt,
        review_attempt_document,
    )
    from heddle.runtime import completion

    assert hasattr(completion, "_validate_archive_inputs"), (
        "FAIL local-history AC-6: completion has no archive-input preflight"
    )
    host = final_host(tmp_path, monkeypatch, verify_now=False)
    attempt = ReviewAttempt(
        attempt_id="00000000-0000-4000-8000-000000000778",
        assignment_id="local-history:milestone-review:m1:p1",
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
        outcome=EngineFailure("fixture", "missing archive input"),
        created_at="2026-09-16T00:00Z",
        artifacts=(
            ArtifactRef(
                "reviews/missing.tmp",
                "4" * 64,
                "temporary",
                mode=0o640,
            ),
        ),
    )
    value = read(host.state)
    value["review_assignments"]["attempts"] = [review_attempt_document(attempt)]
    write(host.state, value)
    verify(host.state, "m1", "m2", "acceptance", "smoke")
    before = snapshot(host.root)

    refused = host.complete()

    assert not refused.ok
    assert "missing.tmp" in (refused.error.message + refused.error.hint)
    assert snapshot(host.root) == before
    assert not host.archive.exists()


def test_local_history_ignored_journal_and_retrospective_remain_authoritative(
    tmp_path, monkeypatch
) -> None:
    """Survivor pin for AC-7: close reads exact ignored local documents."""
    host = final_host(tmp_path, monkeypatch)
    journal = _journal(host)
    retrospective = _retro(host)
    relative = [
        journal.relative_to(host.root).as_posix(),
        retrospective.relative_to(host.root).as_posix(),
    ]
    (host.root / ".gitignore").write_text("\n".join(relative) + "\n")
    for path in relative:
        assert (
            subprocess.run(
                ["git", "check-ignore", "-q", path], cwd=host.root, check=False
            ).returncode
            == 0
        )
    _seed_policy_decision(host)
    journal.write_text("# Decision Journal\n")
    retrospective.write_text("# Missing required retrospective sections\n")

    repairs = _repairs(host.complete())

    assert [(row["check"], row["cause"]) for row in repairs[:2]] == [
        ("retrospective", "missing-headings"),
        ("decision-journal", "audit-incomplete"),
    ]
    assert read(host.state)["completion"] is None
    assert not host.archive.exists()


def test_local_history_completion_uses_exact_workflow_control_exclusions(
    tmp_path, monkeypatch
) -> None:
    """AC-4 red: public completion excludes only this feature's control paths."""
    from heddle.runtime import completion

    host = final_host(tmp_path, monkeypatch)
    observed = {}
    original = completion.reconcile_current_source

    def capture(*args, **kwargs):
        observed.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(completion, "reconcile_current_source", capture)

    result = host.complete()

    assert result.ok, result.to_envelope()
    assert "plans" not in observed["runtime_owned_roots"]
    excluded_paths = set(observed["excluded_paths"])
    assert {
        f"plans/{FEATURE}/state.yaml",
        f"plans/{FEATURE}/plan.md",
        f"plans/{FEATURE}/brief.md",
        f"docs/features/runtime/{FEATURE}.md",
        f"plans/{FEATURE}.decision-journal.md",
        f"plans/{FEATURE}.friction-retrospective.md",
        "plans/gate-effectiveness.md",
    }.issubset(excluded_paths)
    assert f"plans/{FEATURE}/product.py" not in excluded_paths
    assert {
        f"plans/{FEATURE}/reviews",
        f"plans/{FEATURE}/verification",
        f"docs/gate-trajectories/.raw/{FEATURE}",
    }.issubset(set(observed["runtime_owned_roots"]))


@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "apply"])
def test_completion_root_review_record_reports_legal_location_without_ownership_bypass(
    tmp_path, monkeypatch, dry_run
) -> None:
    """AC-6 red: a misplaced review record gets a narrow legal repair."""
    host = _nondefault_final_host(tmp_path, monkeypatch)
    misplaced = host.state.parent / "lead-assessment.md"
    misplaced.write_text("# Lead assessment\n")
    before = snapshot(host.root)

    refused = host.complete(dry_run=dry_run)

    assert not refused.ok and refused.error.code == "workspace-invalid"
    message = refused.error.message + "\n" + refused.error.hint
    assert "lead-assessment.md" in message
    assert f"journey-plans/{FEATURE}/reviews/" in message
    assert "review record" in message.lower()
    assert snapshot(host.root) == before
    assert read(host.state)["completion"] is None
    assert host.suite_calls() == []


def test_completion_accepts_unbound_review_record_in_reviews_and_archives_it(
    tmp_path, monkeypatch
) -> None:
    """AC-6 survivor pin: the legal review directory stays outside ownership."""
    host = final_host(tmp_path, monkeypatch)
    record = host.state.parent / "reviews/lead-assessment.md"
    record.parent.mkdir(exist_ok=True)
    record.write_text("# Lead assessment\n")

    completed = host.complete()

    assert completed.ok, completed.to_envelope()
    with __import__("tarfile").open(host.archive, "r:gz") as archive:
        assert (
            archive.extractfile("reviews/lead-assessment.md").read()
            == record.read_bytes()
        )


def test_completion_mixed_unresolved_paths_keep_all_required_repairs(
    tmp_path, monkeypatch
) -> None:
    """AC-6 red: review guidance augments rather than replaces mixed repairs."""
    host = final_host(tmp_path, monkeypatch)
    (host.state.parent / "lead-assessment.md").write_text("# Review record\n")
    (host.state.parent / "product.py").write_text("VALUE = 9\n")
    foreign = host.root / "foreign-feature-note.md"
    foreign.write_text("foreign change\n")

    refused = host.complete(dry_run=True)

    assert not refused.ok and refused.error.code == "workspace-invalid"
    message = refused.error.message + "\n" + refused.error.hint
    assert "lead-assessment.md" in message
    assert "product.py" in message
    assert "foreign-feature-note.md" in message
    assert f"plans/{FEATURE}/reviews/" in message


def test_f3_a2_public_json_and_human_reports_expose_the_same_ordered_repairs(
    tmp_path, monkeypatch, run_cli
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _retro(host).write_text("")
    _journal(host).write_bytes(b"\xff\xfe")
    _remove_frontmatter_fence(host)

    code, stdout, stderr = run_cli(
        ["feature", "complete", "--feature", FEATURE, "--dry-run", "--json"]
    )
    assert code == 3 and stderr == ""
    envelope = json.loads(stdout)
    repairs = envelope["error"]["details"]["repairs"]
    expected = [
        ("retrospective", "empty"),
        ("decision-journal", "unreadable"),
        ("spec-stamp", "invalid-frontmatter"),
    ]
    assert [(row["check"], row["cause"]) for row in repairs] == expected

    code, stdout, stderr = run_cli(
        ["feature", "complete", "--feature", FEATURE, "--dry-run"]
    )
    assert code == 3 and stdout == ""
    assert "completion preflight found 3 repairs" in stderr
    for repair in repairs:
        label = f"[{repair['check']}/{repair['cause']}] {repair['path']}"
        assert stderr.count(label) == 1
        assert stderr.count(f"hint: {repair['hint']}") == 1


@pytest.mark.parametrize(
    ("artifact", "condition", "expected"),
    [
        ("retrospective", "missing", "missing"),
        ("retrospective", "empty", "empty"),
        ("retrospective", "unreadable", "unreadable"),
        ("decision-journal", "missing", "missing"),
        ("decision-journal", "unreadable", "unreadable"),
        ("spec-stamp", "unreadable", "unreadable"),
    ],
)
def test_f3_a3_reports_one_truthful_cause_per_dependent_input(
    tmp_path, monkeypatch, artifact, condition, expected
) -> None:
    host = final_host(tmp_path, monkeypatch)
    path = {
        "retrospective": _retro(host),
        "decision-journal": _journal(host),
        "spec-stamp": host.spec,
    }[artifact]
    if condition == "missing":
        path.unlink()
    elif condition == "empty":
        path.write_text("")
    else:
        path.write_bytes(b"\xff\xfe")

    repairs = _repairs(host.complete(dry_run=True))

    assert [(row["check"], row["cause"]) for row in repairs] == [(artifact, expected)]
    if condition == "unreadable":
        assert "missing-headings" not in {row["cause"] for row in repairs}


def test_f3_a3_blank_journal_without_required_facts_remains_valid(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _journal(host).write_text("")

    result = host.complete(dry_run=True)

    assert result.ok, result.to_envelope()


@pytest.mark.parametrize(
    "refusal", ["stage", "revision", "authorization", "unsafe-spec"]
)
def test_f3_a4_integrity_and_authority_refusals_keep_preflight_precedence(
    tmp_path, monkeypatch, refusal
) -> None:
    host = final_host(
        tmp_path, monkeypatch, stage="robustness" if refusal == "stage" else "complete"
    )
    _retro(host).write_text("")
    kwargs = {"dry_run": True}
    if refusal == "revision":
        kwargs["expect_revision"] = read(host.state)["revision"] + 1
    elif refusal == "authorization":
        value = read(host.state)
        value["authorizations"] = [
            grant for grant in value["authorizations"] if grant["through"] != "complete"
        ]
        write(host.state, value)
    elif refusal == "unsafe-spec":
        target = tmp_path / "outside-spec.md"
        target.write_text("---\nlifecycle: active\n---\n")
        host.spec.unlink()
        host.spec.symlink_to(target)

    result = host.complete(**kwargs)

    assert not result.ok and result.error is not None
    assert "repairs" not in result.error.details
    assert (
        result.error.code
        == {
            "stage": "usage",
            "revision": "conflict",
            "authorization": "not-authorized",
            "unsafe-spec": "workspace-invalid",
        }[refusal]
    )
    assert read(host.state)["completion"] is None


def test_f3_a5_missing_suite_uses_the_effective_grant_and_names_real_config_repair(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _retro(host).write_text("")
    _remove_close_suite(host)
    value = read(host.state)
    value["authorizations"].append(
        {"through": "complete", "source": "policy", "at": "2026-01-01T00:00Z"}
    )
    write(host.state, value)

    result = host.complete(dry_run=True)

    assert [(row["check"], row["cause"]) for row in _repairs(result)] == [
        ("retrospective", "empty"),
        ("close-configuration", "suite-or-user-grant-required"),
    ]
    commands = [action.command for action in result.next_actions]
    assert commands[-1] == "set autopilot.test_command in .heddle.yaml"
    assert all("phase-exit" not in command for command in commands)


def test_f3_a5_user_grant_preserves_the_missing_suite_exemption(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch)
    _remove_close_suite(host)

    result = host.complete(dry_run=True)

    assert result.ok, result.to_envelope()


def test_f3_a6_preview_is_effect_free_and_apply_runs_one_close_suite(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch, verify_now=False)
    _set_close_suite(
        host,
        "from pathlib import Path; "
        f"p = Path('plans/{FEATURE}/verification/close-suite.calls'); "
        "p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text((p.read_text() if p.exists() else '') + 'close\\n')",
    )
    before = snapshot(host.root)

    preview = host.complete(dry_run=True)
    assert preview.ok and snapshot(host.root) == before
    assert host.suite_calls() == []

    accepted = host.complete()
    assert accepted.ok and accepted.data["accepted"] is True
    assert host.suite_calls() == ["close"]
    assert read(host.state)["completion"] is not None


def test_f3_a7_apply_repeats_preflight_after_the_suite_before_accepting(
    tmp_path, monkeypatch
) -> None:
    host = final_host(tmp_path, monkeypatch, verify_now=False)
    _set_close_suite(
        host,
        "from pathlib import Path; "
        f"Path('plans/{FEATURE}.friction-retrospective.md').write_text(''); "
        f"p = Path('plans/{FEATURE}/verification/close-suite.calls'); "
        "p.parent.mkdir(parents=True, exist_ok=True); p.write_text('close\\n')",
    )

    result = host.complete()

    assert [(row["check"], row["cause"]) for row in _repairs(result)] == [
        ("retrospective", "empty")
    ]
    assert host.suite_calls() == ["close"]
    assert read(host.state)["completion"] is None
    logs = tuple((host.state.parent / "verification").glob("final-close-*.log"))
    assert len(logs) == 1


def test_ac12_valid_nonzero_close_suite_refuses_then_retry_succeeds(
    tmp_path, monkeypatch
) -> None:
    """AC-12 survivor: an executed nonzero suite cannot stamp or accept close."""
    host = final_host(tmp_path, monkeypatch, verify_now=False)
    _set_close_suite(
        host,
        "from pathlib import Path; "
        f"p = Path('plans/{FEATURE}/verification/close-suite.calls'); "
        "p.parent.mkdir(parents=True, exist_ok=True); p.write_text('close\\n'); "
        "raise SystemExit(17)",
    )

    failed = host.complete()

    assert not failed.ok and failed.error is not None
    rendered_failure = json.dumps(failed.to_envelope()).casefold()
    assert "additional close suite" in rendered_failure
    assert "clean-venv" not in rendered_failure
    assert "clean environment" not in rendered_failure
    assert read(host.state)["completion"] is None
    assert yaml.safe_load(host.spec.read_text().split("---", 2)[1])["lifecycle"] != (
        "complete"
    )
    assert host.suite_calls() == ["close"]
    logs = tuple((host.state.parent / "verification").glob("final-close-*.log"))
    assert len(logs) == 1

    _set_close_suite(
        host,
        "from pathlib import Path; "
        f"p = Path('plans/{FEATURE}/verification/close-suite.calls'); "
        "p.write_text(p.read_text() + 'retry\\n')",
    )
    recovered = host.complete()

    assert recovered.ok and recovered.data["accepted"] is True
    assert read(host.state)["completion"] is not None
    assert host.suite_calls() == ["close", "retry"]
