"""W4 AC-14: installed host and real close; only provider process is scripted."""

from __future__ import annotations

import json
import os

import pytest

from tests.content_identity_helpers import git, require_current_state
from tests.operational_model_helpers import FEATURE, SPEC, read, write
from tests.runtime.verification_provenance_helpers import convert_current_fixture_to_v8
from tests.runtime.wheel_harness import (
    build_installed_wheel,
    parse_envelope,
    write_claude_shim,
)
from tests.tiering_completion_helpers import final_host
from tests.tiering_helpers import entry
from tests.tiering_review_helpers import disposition, review_content


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac14_installed_review_drift_restore_completion_and_terminal_retry(tmp_path):
    require_current_state()
    installed = build_installed_wheel(tmp_path / "installed")
    with pytest.MonkeyPatch.context() as patch:
        fixture = final_host(
            tmp_path,
            patch,
            stage="implement",
            verify_now=False,
            overrides={
                "milestone-review": entry(
                    "milestone-review",
                    primary={
                        "cli": "claude",
                        "model": "fixture-model",
                        "reasoning_effort": "high",
                    },
                )
            },
        )
    host, path = fixture.root, fixture.state
    convert_current_fixture_to_v8(host, path)
    assert read(path)["schema"] == "heddle.state/v9"
    spec = host / SPEC
    # Preserve the fixture's explicit configuration ownership before any evidence.
    initial = read(path)
    initial["milestones"][0]["owns"].append(".heddle.yaml")
    write(path, initial)
    provider_log = tmp_path / "provider-calls.jsonl"
    native = review_content("milestone-review")
    terminal = (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": native,
            }
        )
        + "\n"
    )
    binary = write_claude_shim(tmp_path / "bin", terminal, provider_log)
    env = {
        **installed.env,
        "PATH": str(binary.parent) + os.pathsep + installed.env["PATH"],
    }

    def execute(*arguments, expected=0):
        result = installed.run(
            *arguments, "--feature", FEATURE, "--json", cwd=host, env=env
        )
        envelope = parse_envelope(result)
        assert result.returncode == expected, envelope
        assert envelope["ok"] is (expected in {0, 4}), envelope
        return envelope

    def verify(*scopes):
        for scope in scopes:
            execute("verify", "--scope", scope)
            fact = read(path)["verifications"][-1]
            assert fact["exit_code"] == 0
            assert fact["evidence"]["schema"] == "heddle.verification-evidence/v1"
            assert fact["evidence"]["before"] == fact["evidence"]["after"]
            reference = fact["evidence"]["before"]
            artifact = path.parent / reference["artifact"]
            manifest = read(artifact)
            assert manifest["schema"] == "heddle.source-evidence/v1"
            assert manifest["source_sha256"] == reference["source_sha256"]

    review = (
        "run-gate",
        "milestone-review",
        "--cli",
        "claude",
        "--model",
        "fixture-model",
        "--reasoning-effort",
        "high",
    )

    def reviewed():
        result = execute(*review)
        payload = tmp_path / "coverage.json"
        payload.write_text(
            json.dumps(
                {
                    "schema": "heddle.review-disposition-input/v1",
                    "dispositions": [
                        disposition(
                            result["data"]["run_id"],
                            "@coverage",
                            status="settled",
                            references=[
                                "src/example.py",
                                "src/other.py",
                                "tests/check.py",
                            ],
                            reason=(
                                "Inspect both declared values and the real check "
                                "after the clean review"
                            ),
                        )
                    ],
                }
            )
        )
        closed = execute(
            "review",
            "disposition",
            "--input-json",
            str(payload),
            "--expect-revision",
            str(read(path)["revision"]),
        )
        assert closed["data"]["closure"]["closed"]
        return result

    verify("m1")
    first = reviewed()
    source = host / "src/example.py"
    original = source.read_bytes()
    source.write_bytes(original + b"\n# changed declared content\n")
    verify("m1")
    status = execute("status")
    assert status["data"]["review_status"][0]["applicability"] == "unavailable"
    assert status["data"]["review_closure"]["open_refs"]
    before = path.read_bytes()
    refused = execute("milestone", "advance", expected=3)
    assert refused["next_actions"] and path.read_bytes() == before
    source.write_bytes(original)
    replay = execute(*review)
    assert (
        replay["data"]["cached"] and replay["data"]["run_id"] == first["data"]["run_id"]
    )
    verify("m1")
    execute("milestone", "advance")
    execute("milestone", "advance")
    reviewed()
    verify("m2")
    execute("milestone", "advance")
    verify("m1", "m2", "acceptance", "smoke")
    for expected_stage in ("peer-review", "robustness", "complete"):
        execute("phase-exit")
        assert read(path)["stage"] == expected_stage
    (host / f"plans/{FEATURE}.decision-journal.md").write_text(
        "# Decision Journal\n\nThe clean fixture reviews created no owner decisions.\n"
    )
    (host / f"plans/{FEATURE}.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        + "\n\n".join(
            "## " + heading + "\n\nNone in this installed acceptance fixture."
            for heading in (
                "Tooling fights",
                "Prompt gaps",
                "Validator false-positives",
                "Escalations that should have been policy-resolvable",
            )
        )
        + "\n"
    )
    close = execute("feature", "complete")
    assert close["data"]["wrote"] is True and close["data"]["accepted"]
    archive = host / f"docs/gate-trajectories/.raw/{FEATURE}/completion.tar.gz"
    assert archive.is_file()
    accepted = path.read_bytes(), spec.read_bytes(), archive.read_bytes()
    git(host, "add", "-f", "plans", "docs")
    git(host, "commit", "-qm", "retain accepted installed evidence")
    retry = execute("feature", "complete", expected=0)
    assert retry["data"]["wrote"] is False
    terminal_status = execute("status")
    assert terminal_status["data"]["accepted"]
    assert terminal_status["next_actions"] == []
    assert (path.read_bytes(), spec.read_bytes(), archive.read_bytes()) == accepted
    assert len(provider_log.read_text().splitlines()) == 2
    assert not installed.forbidden_log.exists() and not installed.network_log.exists()
