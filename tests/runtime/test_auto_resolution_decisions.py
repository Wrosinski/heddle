"""Decision recording and resolution for automatic implementation findings."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from tests.operational_model_helpers import native_run_fields, new_run_id

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EDGE = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "edge"


def _resolve(
    run_cli, *, resolution: str, route: str, dry_run: bool = False
) -> tuple[int, dict]:
    argv = [
        "decisions",
        "resolve",
        "D1",
        "--feature",
        "blocked-pending-decision",
        "--kind",
        "disposition",
        "--resolution",
        resolution,
        "--routes-to",
        route,
        "--json",
    ]
    if dry_run:
        argv.append("--dry-run")
    code, out, _err = run_cli(argv)
    import json

    return code, json.loads(out)


def test_ac11_resolution_retry_is_idempotent_and_amendment_fails_closed(
    run_cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "host"
    shutil.copytree(EDGE, host)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "blocked-pending-decision" / "state.yaml"

    times = iter(
        [
            "2026-07-10T10:00Z",
            "2026-07-10T10:01Z",
            "2026-07-10T10:02Z",
            "2026-07-10T10:03Z",
        ]
    )
    from heddle.runtime import decisions as decisions_mod

    monkeypatch.setattr(decisions_mod, "utc_now_minutes", lambda: next(times))

    code, first = _resolve(
        run_cli, resolution="Use event invalidation", route="  docs/cache.md  "
    )
    assert code == 0 and first["ok"] is True
    after_first = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    first_revision = after_first["revision"]
    first_decision = next(d for d in after_first["decisions"] if d["id"] == "D1")
    assert first_decision["routes_to"] == ["docs/cache.md"], (
        "FAIL AC-11: route normalization strips only boundary whitespace "
        f"before persistence, got {first_decision['routes_to']!r}"
    )

    code, retry = _resolve(
        run_cli, resolution="Use event invalidation", route="docs/cache.md"
    )
    assert code == 0 and retry["ok"] is True
    assert retry["data"]["mutation_summary"]["would_write"] is False
    after_retry = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    retry_decision = next(d for d in after_retry["decisions"] if d["id"] == "D1")
    assert after_retry["revision"] == first_revision
    assert retry_decision == first_decision, (
        "FAIL AC-11: an identical retry must not rewrite resolved_at or any "
        "decision field"
    )

    before_amendment = state_path.read_bytes()
    code, amendment = _resolve(
        run_cli, resolution="Use TTL only", route="docs/cache.md"
    )
    assert code == 3 and amendment["error"]["code"] == "decision-already-resolved"
    assert state_path.read_bytes() == before_amendment, (
        "FAIL AC-11: changing a resolved fact requires a future explicit "
        "amend/supersede operation; resolve must fail closed"
    )

    before_route_amendment = state_path.read_bytes()
    code, route_amendment = _resolve(
        run_cli,
        resolution="Use event invalidation",
        route="docs/alternate-cache.md",
    )
    assert code == 3 and route_amendment["error"]["code"] == (
        "decision-already-resolved"
    )
    assert state_path.read_bytes() == before_route_amendment, (
        "FAIL AC-11: same-resolution/different-route retry must fail closed "
        "without mutating the first-recorded truthful route"
    )


@pytest.mark.parametrize(
    "requested_route",
    ("docs/cache.md", "  docs/cache.md  "),
)
def test_ac11_current_normalized_route_retry_is_a_byte_stable_noop(
    requested_route,
    run_cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "host"
    shutil.copytree(EDGE, host)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "blocked-pending-decision" / "state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    decision = next(item for item in state["decisions"] if item["id"] == "D1")
    decision.update(
        {
            "status": "resolved",
            "resolution": {"kind": "disposition"},
            "rationale": "Use event invalidation",
            "resolution_source": "user",
            "routes_to": ["docs/cache.md"],
            "resolved_at": "2026-07-10T09:59Z",
        }
    )
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    before = state_path.read_bytes()

    code, retry = _resolve(
        run_cli,
        resolution="Use event invalidation",
        route=requested_route,
    )
    assert code == 0 and retry["data"]["mutation_summary"]["would_write"] is False
    assert state_path.read_bytes() == before, (
        "FAIL AC-11: an equivalent normalized current route retry must not "
        "canonicalize or otherwise rewrite immutable resolution history"
    )


@pytest.mark.parametrize(
    "route",
    [
        "",
        "   ",
        "/absolute/path.md",
        "../escape.md",
        "docs/../escape.md",
        "docs/./cache.md",
    ],
)
def test_ac11_invalid_truthful_routes_are_rejected(
    route: str, run_cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "host"
    shutil.copytree(EDGE, host)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "blocked-pending-decision" / "state.yaml"
    before = state_path.read_bytes()

    code, envelope = _resolve(run_cli, resolution="Use event invalidation", route=route)
    assert code == 2 and envelope["error"]["code"] == "invalid-route", (
        "FAIL AC-11: routes must be normalized repo-relative POSIX paths with "
        f"no dot segments, got {code} {envelope!r} for {route!r}"
    )
    assert state_path.read_bytes() == before


def test_ac11_dry_run_has_resolution_validation_parity(
    run_cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "host"
    shutil.copytree(EDGE, host)
    monkeypatch.chdir(host)
    state_path = host / "plans" / "blocked-pending-decision" / "state.yaml"

    before = state_path.read_bytes()
    code, invalid = _resolve(
        run_cli,
        resolution="Use event invalidation",
        route="docs/../escape.md",
        dry_run=True,
    )
    assert code == 2 and invalid["error"]["code"] == "invalid-route"
    assert state_path.read_bytes() == before

    code, _first = _resolve(
        run_cli, resolution="Use event invalidation", route="docs/cache.md"
    )
    assert code == 0
    resolved = state_path.read_bytes()

    code, retry = _resolve(
        run_cli,
        resolution="Use event invalidation",
        route=" docs/cache.md ",
        dry_run=True,
    )
    assert code == 0
    assert retry["data"]["mutation_summary"]["would_write"] is False
    assert state_path.read_bytes() == resolved

    code, amendment = _resolve(
        run_cli,
        resolution="Use TTL only",
        route="docs/cache.md",
        dry_run=True,
    )
    assert code == 3
    assert amendment["error"]["code"] == "decision-already-resolved"
    assert state_path.read_bytes() == resolved


# decision deferral-E/configuration: explicit verdict-cap resolutions and immutable
# supersession.
EXTEND = "Extend the budget by 5 more reviews"
ADJUDICATE = "Adjudicate the remaining findings without rerunning"
AUTHORITY_RUN_ID = "00000000-0000-0000-0000-00000000a110"


def _run(cli: str, status: str) -> dict:
    return {
        **native_run_fields(cli),
        "run_id": new_run_id(),
        "report_findings": [],
        "at": "2026-07-11T00:00Z",
        "cli": cli,
        "artifact": "reviews/spec-review.review.json",
        "input_hash": f"hash-{cli}-{status}",
        "verdict": {"status": status, "rerun_recommended": True},
        "findings": {
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
    }


def _verdict_host(tmp_path: Path, monkeypatch, *, decision_id: str) -> Path:
    """Five Claude/two Codex completions; one error; a distinct deciding run."""
    host = tmp_path / "host"
    shutil.copytree(EDGE, host)
    monkeypatch.chdir(host)
    path = host / "plans/blocked-pending-decision/state.yaml"
    state = yaml.safe_load(path.read_text())
    state["stage"] = "spec-review"
    state["authorized_through"] = "complete"
    runs = [
        _run("claude", "pass_with_conditions"),
        _run("claude", "fail"),
        _run("claude", "pass_with_conditions"),
        _run("codex", "fail"),
        _run("claude", "error"),
        _run("claude", "pass_with_conditions"),
        _run("codex", "pass_with_conditions"),
        _run("claude", "fail"),
    ]
    authority = _run("claude", "pass_with_conditions")
    authority["run_id"] = AUTHORITY_RUN_ID
    state["gates"] = [
        {"gate": "spec-review", "scope": "feature", "runs": runs},
        {"gate": "spec-review-synthesis", "scope": "feature", "runs": [authority]},
    ]
    state["decisions"].append(
        {
            "id": decision_id,
            "kind": "verdict-cap",
            "class": 3,
            "source": "driver",
            "title": "The Claude lane spent its five reviews",
            "question": "Extend the budget or adjudicate the deciding artifact?",
            "options": [EXTEND, ADJUDICATE],
            "status": "pending",
            "routes_to": [],
            "created_at": "2026-07-11T00:00Z",
            "rationale": None,
            "resolution": None,
            "resolution_source": None,
            "resolved_at": None,
            "origin_run_id": runs[-1]["run_id"],
            "target_stage": "spec-review",
        }
    )
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    return path


def _resolve_id(run_cli, decision_id, *, kind, resolution, run_id=None):
    import json

    argv = [
        "decisions",
        "resolve",
        decision_id,
        "--feature",
        "blocked-pending-decision",
        "--kind",
        kind,
        "--resolution",
        resolution,
        "--routes-to",
        "docs/cache.md",
        "--json",
    ]
    if run_id is not None:
        argv += ["--run-id", run_id]
    code, out, _err = run_cli(argv)
    return code, json.loads(out)


def _settled_caps(path):
    state = yaml.safe_load(path.read_text())
    assert "continuation_overrides" not in state
    return [
        d
        for d in state["decisions"]
        if d["status"] == "resolved" and d["kind"] in {"stage-cap", "verdict-cap"}
    ]
