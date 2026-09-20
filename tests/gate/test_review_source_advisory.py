"""AC-3: exact-path source advice is visible before dispatch and never authority."""

import json

import pytest

from heddle.contracts.review_assignments import ROLE_STAGES
from tests.content_identity_helpers import git
from tests.proof_continuity_helpers import SPEC, diagnostics, launch, read, write
from tests.tiering_helpers import entry
from tests.tiering_review_helpers import (
    current_host,
    provider_transport,
    review_content,
)


@pytest.mark.parametrize(
    "role,kind",
    [
        ("spec-review", "unstaged"),
        ("plan-review", "staged"),
        ("review-test-scaffolding", "untracked"),
        ("milestone-review", "unstaged"),
    ],
)
def test_selected_dirty_inputs_warn_before_provider_and_in_result_without_flag(
    tmp_path, monkeypatch, capsys, role, kind
):
    root, path = current_host(
        tmp_path, monkeypatch, stage=ROLE_STAGES[role], overrides={role: entry(role)}
    )
    selected = (
        SPEC
        if role == "spec-review"
        else (
            "plans/operational-example/plan.md"
            if role == "plan-review"
            else "tests/new [check].py"
            if kind == "untracked"
            else "src/example.py"
        )
    )
    target = root / selected
    target.write_text(
        (target.read_text() if target.exists() else "") + "\n# Dirty review input.\n"
    )
    if kind == "untracked":
        value = read(path)
        value["milestones"][0]["owns"].append(selected)
        write(path, value)
    if kind == "staged":
        git(root, "add", "--", selected)
    before_dispatch = []

    def transport(_cli, _prompt):
        before_dispatch.append(capsys.readouterr().err)
        return review_content(role)

    calls = provider_transport(monkeypatch, transport)
    code, result = launch(capsys, role=role)
    assert code == 0 and result["data"]["accepted"] and len(calls) == 1, result
    advice = diagnostics(result).lower()
    assert selected.lower() in advice and "format" in advice and "commit" in advice, (
        advice
    )
    assert any(selected in output for output in before_dispatch), before_dispatch
    attempts = read(path)["review_assignments"]["attempts"]
    assert set(attempts[-1]["invocation"]) == {
        "cli",
        "model",
        "reasoning_effort",
        "sandbox",
        "input_hash",
        "review_basis_hash",
        "prompt_version",
        "effective_prompt_sha256",
    }


def test_provided_diff_does_not_bypass_source_observation(tmp_path, monkeypatch):
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.diff import resolve_diff
    from heddle.gate.preparation import prepare_gate_run
    from heddle.gate.prompt import resolve_gate_prompt
    from heddle.gate.types import GateInvocationOverrides
    from tests.content_identity_helpers import capture_context

    root, _path = current_host(tmp_path, monkeypatch, stage="implement")
    context = capture_context(root, role="milestone-review", lane="codex")
    gate = context.gate_type
    supplied = tmp_path / "provided.diff"
    supplied.write_bytes(git(root, "diff", "HEAD", "--", "src/example.py"))
    diff = resolve_diff(diff_path=supplied, repo_root=root)
    assert diff.source == "provided-file"
    invocation = resolve_gate_execution(gate, GateInvocationOverrides(cli="codex"))
    prepared = prepare_gate_run(
        context,
        gate_type=gate,
        invocation=invocation,
        prompt=resolve_gate_prompt(context, gate, cli="codex"),
        diff=diff,
    )
    assert prepared.preflight.fatal_reason is None
    advice = " ".join(prepared.diagnostics).lower()
    assert "src/example.py" in advice and "format" in advice and "commit" in advice, (
        advice
    )


def test_ignored_document_input_stays_hash_bound_without_commit_required_warning(
    tmp_path, monkeypatch, capsys
):
    root, path = current_host(tmp_path, monkeypatch)
    git(root, "rm", "--cached", "--", SPEC)
    (root / ".gitignore").write_text(SPEC + "\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "Ignore local spec records")
    (root / SPEC).write_text((root / SPEC).read_text() + "\nIgnored but reviewed.\n")
    (root / "unrelated.py").write_text("UNRELATED = 1\n")
    calls = provider_transport(monkeypatch, review_content())
    code, first = launch(capsys)
    assert code == 0 and first["data"]["accepted"], first
    advice = diagnostics(first).lower()
    assert "commit" not in advice and "unrelated.py" not in advice
    basis = read(path)["review_assignments"]["attempts"][-1]["invocation"][
        "review_basis_hash"
    ]
    (root / SPEC).write_text((root / SPEC).read_text() + "\nA second material edit.\n")
    _code, changed = launch(capsys)
    assert not changed["ok"] and "input changed" in json.dumps(changed), changed
    assert (
        read(path)["review_assignments"]["attempts"][-1]["invocation"][
            "review_basis_hash"
        ]
        == basis
    )
    assert len(calls) == 1


def test_git_observation_failure_is_reported_unknown_without_preflight_refusal(
    tmp_path, monkeypatch, capsys
):
    from heddle.io import git as git_io

    _root, _path = current_host(tmp_path, monkeypatch)
    real_git = git_io.run_git

    def unavailable_status(args, *rest, **kwargs):
        if "status" in args:
            return "", "synthetic Git status unavailable"
        return real_git(args, *rest, **kwargs)

    monkeypatch.setattr(git_io, "run_git", unavailable_status)
    calls = provider_transport(monkeypatch, review_content())
    code, result = launch(capsys)
    assert code == 0 and result["data"]["accepted"] and len(calls) == 1, result
    advice = diagnostics(result).lower()
    assert "git" in advice and any(
        word in advice for word in ("unknown", "unavailable")
    ), advice


@pytest.mark.parametrize(
    "change", ["staged-delete", "unstaged-delete", "staged-rename", "unstaged-rename"]
)
def test_dirty_deleted_and_renamed_owned_inputs_keep_exact_endpoints(
    tmp_path, monkeypatch, capsys, change
):
    root, path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review")},
    )
    old, new = "src/old name.py", "src/new name.py"
    (root / old).write_text("OLD = 1\n")
    git(root, "add", "--", old)
    git(root, "commit", "-qm", "synthetic rename/delete source")
    value = read(path)
    value["milestones"][0]["owns"] = ["src", "tests/check.py"]
    write(path, value)
    if "rename" in change:
        (root / old).rename(root / new)
    else:
        (root / old).unlink()
    if change.startswith("staged"):
        git(root, "add", "-A", "--", "src")
    before_dispatch = []

    def transport(_cli, _prompt):
        before_dispatch.append(capsys.readouterr().err)
        return review_content("milestone-review")

    calls = provider_transport(monkeypatch, transport)
    code, result = launch(capsys, role="milestone-review")
    assert code == 0 and result["data"]["accepted"] and len(calls) == 1, result
    advice = diagnostics(result)
    for endpoint in (old, new) if "rename" in change else (old,):
        assert endpoint in advice and any(endpoint in out for out in before_dispatch)
    assert "format" in advice.lower() and "commit" in advice.lower()
    attempt = read(path)["review_assignments"]["attempts"][-1]
    assert set(attempt["invocation"]) == {
        "cli",
        "model",
        "reasoning_effort",
        "sandbox",
        "input_hash",
        "review_basis_hash",
        "prompt_version",
        "effective_prompt_sha256",
    }
