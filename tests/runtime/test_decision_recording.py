"""Gate finding projection and decision-recording behavior."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from tests.structured_review_helpers import finding, scripted_review

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"


def _state_path(host: Path) -> Path:
    return host / "plans" / "nl-screening" / "state.yaml"


def _read_state(host: Path) -> dict:
    import yaml

    return yaml.safe_load(_state_path(host).read_text(encoding="utf-8"))


def _new_reviewed_input(host: Path) -> None:
    """
    Force the next run-gate call to own a new reviewed-input event.

        The digest covers the current milestone's ``owns`` paths, so the edit must
        land under golden reader's ``src/example/screening/validate`` directory.

    """
    reviewed = host / "src" / "example" / "screening" / "validate" / "rule.py"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("RULE = 'new reviewed input'\n", encoding="utf-8")


def _run_gate_landed() -> bool:
    from heddle.runtime.contracts import COMMAND_SURFACE

    run_gate = next((c for c in COMMAND_SURFACE if c.name == "run-gate"), None)
    return run_gate is not None and any(f.name == "--json" for f in run_gate.flags)


# Feature landed: a regression that removed the integration surface must FAIL here, not
# silently skip the module (review integration; plan write T5 — retire red-phase
# skip sentinels to hard asserts at completion).
assert _run_gate_landed(), (
    "run-gate must return its JSON envelope — a regression here must "
    "fail loudly, not skip this module"
)


# A review carrying one of each class: REPORT (→ decision), IMPLEMENT
# (→ in-phase, no decision), IGNORE (no decision), and an unclassified
# finding (→ unknown, no decision).
MIXED_REVIEW = scripted_review(
    [
        finding(
            "SR-C1",
            classification="report",
            severity="critical",
            title="Cache invalidation policy is a product decision",
            recommendation="Apply the fixture recommendation.",
            route=None,
        ),
        finding(
            "SR-I1",
            classification="implement",
            severity="important",
            title="Extract the duplicated guard into a helper",
            recommendation="Apply the fixture repair.",
            route=None,
        ),
        finding(
            "SR-M1",
            classification="ignore",
            severity="minor",
            title="Prefer f-string here",
            recommendation="Apply the fixture repair.",
            route=None,
        ),
        finding(
            "SR-M2",
            classification="unknown",
            severity="minor",
            title="Observed an undocumented assumption",
            recommendation="Apply the fixture repair.",
            route=None,
        ),
    ],
    summary=(
        "Cache invalidation policy is a product decision; Extract the "
        "duplicated guard into a helper; Prefer f-string here; Observed"
        " an undocumented assumption"
    ),
)


# A review carrying TWO REPORT findings, to exercise the `<gate>#<seq>`
# per-finding numbering (the single-REPORT fixtures never do).
TWO_REPORT_REVIEW = scripted_review(
    [
        finding(
            "SR-C1",
            classification="report",
            severity="critical",
            title="Cache TTL vs event invalidation is a product call",
            recommendation="Apply the fixture recommendation.",
            route=None,
        ),
        finding(
            "SR-C2",
            classification="report",
            severity="critical",
            title="Retention window crosses a compliance boundary",
            recommendation="Apply the fixture recommendation.",
            route=None,
        ),
    ],
    summary=(
        "Cache TTL vs event invalidation is a product call; Retention "
        "window crosses a compliance boundary"
    ),
)


# Round-2 review: a DIFFERENT REPORT finding than TWO_REPORT_REVIEW's —
# per-run seq numbering restarts at #1, so its decision id collides with
# the prior run's still-pending batch.
SECOND_ROUND_REVIEW = scripted_review(
    [
        finding(
            "SR-C1",
            classification="report",
            severity="critical",
            title="Migration cutover date needs an operator ruling",
            recommendation="Apply the fixture recommendation.",
            route=None,
        )
    ],
    summary="Migration cutover date needs an operator ruling",
)


# ===========================================================================
# Automatic implementation-resolution behavior: finding routes, decisions,
# evidence, and retry semantics share the gate recording path.
#
# preimplementation state: core is the CURRENT milestone, so these tests are UNGUARDED
# and
# fail until core lands (pattern red-phase-always-green-scaffolding, step 1).
# Tests labeled "survivor pin" in their docstring are green from scaffold
# time BY DESIGN — they pin behavior that must survive the configuration redefinition
# (the commands AC-11 importability-half precedent); every AC also carries a red
# discriminator in this section.
#
# Exits are asserted at the CLI boundary (run_cli + fake_gate_runner) so the
# pins stay `_exit_for`-signature-agnostic. AC-2(g) is the one exception:
# malformed `by_classification` is unreachable through `_findings_to_d33`, so
# its fail-closed leg calls `_exit_for` directly.
# ===========================================================================


# A non-verdict gate artifact: NO **Status:**/**Rerun Recommended:** lines
# (non-verdict gates emit findings only), two REPORT findings carrying
# `- **Recommended**:` lines, one IGNORE finding. Zero IMPLEMENT, zero
# unknown, zero CON- headers — the AC-1 clean shape.
REPORT_IGNORE_REVIEW = scripted_review(
    [
        finding(
            "XX-C1",
            classification="report",
            severity="critical",
            title="Cache TTL vs event invalidation is a product call",
            recommendation="Adopt event invalidation with a 60s TTL fallback",
            route=None,
        ),
        finding(
            "XX-I1",
            classification="report",
            severity="important",
            title="Retention window crosses a compliance boundary",
            recommendation="Keep the 30-day window; document the carve-out",
            route=None,
        ),
        finding(
            "XX-M1",
            classification="ignore",
            severity="minor",
            title="Prefer f-string here",
            recommendation="Apply the fixture repair.",
            route=None,
        ),
    ],
    summary=(
        "Cache TTL vs event invalidation is a product call; Retention "
        "window crosses a compliance boundary; Prefer f-string here"
    ),
)

# The all-nine-gate AC-1 discriminator is deliberately pure REPORT. Keeping it
# separate from REPORT_IGNORE_REVIEW prevents an implementation from
# accidentally making IGNORE presence a prerequisite for discounting REPORTs.
REPORT_ONLY_REVIEW = scripted_review(
    [
        finding(
            "XX-C1",
            classification="report",
            severity="critical",
            title="Cache TTL vs event invalidation is a product call",
            recommendation="Adopt event invalidation with a 60s TTL fallback",
            route=None,
        ),
        finding(
            "XX-I1",
            classification="report",
            severity="important",
            title="Retention window crosses a compliance boundary",
            recommendation="Keep the 30-day window; document the carve-out",
            route=None,
        ),
    ],
    summary=(
        "Cache TTL vs event invalidation is a product call; Retention "
        "window crosses a compliance boundary"
    ),
)

ROUTED_REPORT_REVIEW = scripted_review(
    [
        finding(
            "XX-C1",
            classification="report",
            severity="critical",
            title="Cache policy has one landing file",
            recommendation="Adopt event invalidation",
            route="docs/cache policy.md",
        ),
        finding(
            "XX-I1",
            classification="report",
            severity="important",
            title="Dot segments are not truthful",
            recommendation="Keep the retention policy",
            route="docs/../retention.md",
        ),
        finding(
            "XX-I2",
            classification="report",
            severity="important",
            title="Multiple route fields are ambiguous",
            recommendation="Choose one migration owner",
            route=None,
        ),
        finding(
            "XX-I3",
            classification="report",
            severity="important",
            title="A route may be omitted",
            recommendation="Escalate the cross-cutting policy",
            route=None,
        ),
    ],
    summary=(
        "Cache policy has one landing file; Dot segments are not "
        "truthful; Multiple route fields are ambiguous; A route may be "
        "omitted"
    ),
)

IGNORE_ONLY_REVIEW = scripted_review(
    [
        finding(
            "XX-M1",
            classification="ignore",
            severity="minor",
            title="Observed a stylistic wart",
            recommendation="Apply the fixture repair.",
            route=None,
        )
    ],
    summary="Observed a stylistic wart",
)

IMPLEMENT_ONLY_REVIEW = scripted_review(
    [
        finding(
            "XX-I1",
            classification="implement",
            severity="important",
            title="Extract the duplicated guard",
            recommendation="Apply the fixture repair.",
            route=None,
        )
    ],
    summary="Extract the duplicated guard",
)


UNKNOWN_ONLY_REVIEW = scripted_review(
    [
        finding(
            "XX-I1",
            classification="unknown",
            severity="important",
            title="Undocumented assumption spotted",
            recommendation="Apply the fixture repair.",
            route=None,
        )
    ],
    summary="Undocumented assumption spotted",
)

MINOR_IMPLEMENT_REVIEW = scripted_review(
    [
        finding(
            "XX-M1",
            classification="implement",
            severity="minor",
            title="Rename the local for consistency",
            recommendation="Apply the fixture repair.",
            route=None,
        )
    ],
    summary="Rename the local for consistency",
)

MINOR_REPORT_REVIEW = scripted_review(
    [
        finding(
            "XX-M1",
            classification="report",
            severity="minor",
            title="Naming preference could use an owner call",
            recommendation="Keep the current name",
            route=None,
        )
    ],
    summary="Naming preference could use an owner call",
)

DUPLICATE_CLASSIFICATION_REVIEW = scripted_review(
    [
        finding(
            "XX-I1",
            classification="unknown",
            severity="important",
            title="Classification metadata is ambiguous",
            recommendation="Do not turn malformed metadata into a decision",
            route=None,
        )
    ],
    summary="Classification metadata is ambiguous",
)

MINOR_DUPLICATE_CLASSIFICATION_REVIEW = scripted_review(
    [
        finding(
            "XX-M1",
            classification="unknown",
            severity="minor",
            title="Minor classification metadata is ambiguous",
            recommendation="Apply the fixture repair.",
            route=None,
        )
    ],
    summary="Minor classification metadata is ambiguous",
)

IMPORTANT_REPORT_MINOR_IMPLEMENT_REVIEW = scripted_review(
    [
        finding(
            "XX-I1",
            classification="report",
            severity="important",
            title="Retention policy needs an owner call",
            recommendation="Keep the documented retention period",
            route=None,
        ),
        finding(
            "XX-M1",
            classification="implement",
            severity="minor",
            title="Rename the local for consistency",
            recommendation="Apply the fixture repair.",
            route=None,
        ),
    ],
    summary="Retention policy needs an owner call; Rename the local for consistency",
)

# A contradiction plus its required normal REPORT owner. CON-* is merge
# metadata; the severity-coded finding is the durable actionability path.
CONTRADICTION_ONLY_REVIEW = scripted_review(
    [
        finding(
            "SY-I1",
            classification="report",
            severity="important",
            title="Choose the cache contract",
            recommendation="Select one cache owner",
            route=None,
        )
    ],
    summary="Choose the cache contract",
    conflicts=1,
)


def _run_gate_for_resolution(
    run_cli,
    fake_gate_runner,
    tmp_path,
    monkeypatch,
    gate,
    review,
    *,
    fresh=False,
    stage=None,
    **fake_kwargs,
):
    """One non-verdict gate run through the real run-gate handler with the
    engine execution faked: returns (exit_code, state_dict, host)."""
    host = tmp_path / "host"
    if fresh and host.exists():
        shutil.rmtree(host)
    if not host.exists():
        shutil.copytree(GOLDEN, host)
    if stage is not None:
        state_path = _state_path(host)
        state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        state["stage"] = stage
        state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
        if stage == "peer-review":
            config_path = host / ".heddle.yaml"
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config["gates"]["enabled"] = sorted(
                set(config["gates"]["enabled"])
                | {
                    "review-correctness",
                    "review-architecture",
                    "review-robustness",
                    "review-tests",
                    "review-synthesis",
                }
            )
            config_path.write_text(
                yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
            )
    if gate == "review-synthesis":
        reviews = host / "plans" / "nl-screening" / "reviews"
        reviews.mkdir(parents=True, exist_ok=True)
        for reviewer in (
            "review-correctness",
            "review-architecture",
            "review-robustness",
            "review-tests",
        ):
            (reviews / f"{reviewer}.md").write_text(f"# {reviewer}\n", encoding="utf-8")
    monkeypatch.chdir(host)
    if gate == "review-synthesis":
        fake_gate_runner(
            review=scripted_review(summary="The scripted review found no issues."),
        )
        for reviewer in (
            "review-correctness",
            "review-architecture",
            "review-robustness",
            "review-tests",
        ):
            producer_code, _out, _err = run_cli(
                ["run-gate", "--feature", "nl-screening", reviewer]
            )
            assert producer_code == 0, f"FIXTURE: {reviewer} producer must run"
    fake_gate_runner(review=review, **fake_kwargs)
    code, _out, _err = run_cli(["run-gate", "--feature", "nl-screening", gate])
    return code, _read_state(host), host


def _gate_decisions(state, gate):
    run_ids = {
        run["run_id"]
        for fact in state["gates"]
        if fact["gate"] == gate
        for run in fact["runs"]
    }
    return [
        d
        for d in state["decisions"]
        if d["kind"] == "finding" and d["origin_run_id"] in run_ids
    ]


def _resolve_first_finding(run_cli, host, gate="self-review"):
    decision = _gate_decisions(_read_state(host), gate)[0]
    code, output, _ = run_cli(
        [
            "decisions",
            "resolve",
            decision["id"],
            "--feature",
            "nl-screening",
            "--kind",
            "disposition",
            "--resolution",
            "Select the explicit contract",
            "--routes-to",
            "docs/features/analysis/nl-screening.md",
            "--json",
        ]
    )
    assert code == 0, output


def _normalize_golden_for_strict_validate(host: Path) -> None:
    """Repair unrelated golden-corpus diagnostics for the AC-5 assertion.

    The v0 schema, unassigned AC-4, absent owned directories, and plan-less
    second workspace all predate decision deferral. None touches ``decisions[]``, so a
    clean
    result still proves strict validation accepts the recommendation field.
    """
    import yaml

    state_path = _state_path(host)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert state["schema"] == "heddle.state/v6"
    for milestone in state["milestones"]:
        if milestone["id"] == "m2":
            milestone["satisfies"].append("AC-4")
        for owned_path in milestone["owns"]:
            (host / owned_path).mkdir(parents=True, exist_ok=True)
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    shutil.rmtree(host / "plans" / "tier1-quickfix")


# The configuration scope, pinned by the registry equation (spec Terms of art).
def _non_verdict_gates():
    from heddle.gate.registry import (
        FINDING_PRODUCING_GATES,
        VERDICT_PRODUCING_GATES,
    )

    return sorted(FINDING_PRODUCING_GATES - VERDICT_PRODUCING_GATES)


NINE_NON_VERDICT_GATES = sorted(
    [
        "code-quality",
        "self-review",
        "robustness-analysis",
        "review-correctness",
        "review-architecture",
        "review-robustness",
        "review-tests",
        "review-synthesis",
        "peer-review-sequential",
    ]
)


# ---------------------------------------------------------------------------
# decision deferral-D: the nine-gate set splits into two exit regimes. The four parallel
# peer reviewers DEFER actionability to review-synthesis (clean completion →
# OK/0, no decisions); the five remaining gates BEAR the configuration equation.
# ---------------------------------------------------------------------------


def _deferred_gates():
    from heddle.gate.registry import GATES, defers_actionability

    return sorted(name for name in GATES if defers_actionability(name))


DEFERRED_GATES = sorted(
    [
        "review-correctness",
        "review-architecture",
        "review-robustness",
        "review-tests",
    ]
)

EQUATION_GATES = sorted(set(NINE_NON_VERDICT_GATES) - set(DEFERRED_GATES))

# The decision deferral-D partition input: one of each actionable class so a deferred
# gate's OK/0 is discriminating (every class the equation counts is present).
ACTIONABLE_MIX_REVIEW = scripted_review(
    [
        finding(
            "XX-C1",
            classification="implement",
            severity="critical",
            title="Unhandled error path in the write sink",
            recommendation="Apply the fixture repair.",
            route=None,
        ),
        finding(
            "XX-I1",
            classification="ignore",
            severity="important",
            title="Undocumented assumption spotted",
            recommendation="Apply the fixture repair.",
            route=None,
        ),
        finding(
            "XX-I2",
            classification="report",
            severity="important",
            title="Retention window needs an owner call",
            recommendation="Keep the 30-day window",
            route=None,
        ),
    ],
    summary=(
        "Unhandled error path in the write sink; Undocumented "
        "assumption spotted; Retention window needs an owner call"
    ),
    conflicts=1,
)


def test_ac2_missing_verdict_gate_status_fails_closed(
    run_cli, fake_gate_runner, tmp_path, monkeypatch
) -> None:
    """AC-2(f/g): missing verdict or findings shape fails closed."""
    code, _state, _host = _run_gate_for_resolution(
        run_cli,
        fake_gate_runner,
        tmp_path,
        monkeypatch,
        "spec-review",
        REPORT_IGNORE_REVIEW,
    )
    assert code == 3, (
        "FAIL AC-2(f): gate_exit == 0 with a missing verdict-gate status "
        f"must fail closed FATAL/3, got {code}"
    )

    from heddle.contracts.result import ExitCode
    from heddle.runtime.gate_run import _exit_for

    for malformed in ({}, {"by_classification": None}):
        assert (
            _exit_for(
                verdict_gate=False,
                accept_minor=False,
                status=None,
                findings=malformed,
                severity_classification=None,
                gate_exit=0,
            )
            == ExitCode.ADVISORY
        ), (
            "FAIL AC-2(g): a malformed non-verdict findings projection must "
            f"fail closed ADVISORY/4, got {malformed!r}"
        )

    malformed_counts = (
        (
            {
                "by_classification": {"implement": "three", "unknown": 0},
                "contradictions": 0,
            },
            None,
            False,
        ),
        (
            {
                "by_classification": {"implement": 0, "unknown": -1},
                "contradictions": 0,
            },
            None,
            False,
        ),
        (
            {
                "by_classification": {"implement": 0, "unknown": 0},
                "contradictions": [],
            },
            None,
            False,
        ),
        (
            {
                "by_classification": {"implement": 0, "unknown": 0},
                "contradictions": "one",
            },
            {
                "critical/implement": 0,
                "important/implement": 0,
                "critical/unknown": 0,
                "important/unknown": 0,
            },
            True,
        ),
    )
    for malformed, severity_classification, accept_minor in malformed_counts:
        assert (
            _exit_for(
                verdict_gate=False,
                accept_minor=accept_minor,
                status=None,
                findings=malformed,
                severity_classification=severity_classification,
                gate_exit=0,
            )
            == ExitCode.ADVISORY
        ), (
            "FAIL AC-2(g): malformed or negative inner counts must fail "
            f"closed ADVISORY/4 without raising, got {malformed!r}"
        )


def test_ac3_malformed_recording_yaml_becomes_a_typed_kernel_error(
    tmp_path,
) -> None:
    from heddle.kernel.project_config import KernelError
    from heddle.runtime.state_store import read_state_document

    state_path = tmp_path / "state.yaml"
    state_path.write_text(": malformed\n", encoding="utf-8")
    with pytest.raises(KernelError) as caught:
        read_state_document(state_path)
    assert caught.value.code == "workspace-invalid"
    assert "state file is unreadable" in caught.value.message
    assert "line 1, column 1" in caught.value.message


def test_ac3_recording_rejects_report_projection_mismatch_before_mutation(
    tmp_path, monkeypatch
) -> None:
    """Failure construction prevents the former mismatched REPORT fixture."""
    from heddle.gate.cli import resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.types import GateEngineFailure, GateInvocationOverrides
    from heddle.runtime import gate_run

    recorded_fact = None

    def unexpected_record(*_args, **_kwargs):
        nonlocal recorded_fact
        recorded_fact = _kwargs["run_fact"]

    monkeypatch.setattr(gate_run.recording, "record_gate_run", unexpected_record)
    outcome = GateEngineFailure(
        gate_exit=1,
        reason="invalid projection fixture",
        rerun_recommended=None,
        structure_warnings=(),
    )
    gate_run._record(
        tmp_path,
        "milestone-review",
        "m1",
        "claude",
        "sha256:mismatch",
        outcome,
        invocation=resolve_gate_execution(
            GATES["milestone-review"], GateInvocationOverrides(cli="claude")
        ),
        errored=True,
    )
    assert recorded_fact is not None
    assert recorded_fact["verdict"] == {"status": "error"}
    assert recorded_fact["findings"] == {
        "by_severity": {"critical": 0, "important": 0, "minor": 0},
        "by_classification": {
            "implement": 0,
            "report": 0,
            "ignore": 0,
            "unknown": 0,
        },
        "total": 0,
        "contradictions": 0,
    }
    assert recorded_fact["report_findings"] == []


@pytest.mark.parametrize(
    "raw_route",
    (None, 7, "", "   ", "/absolute.md", "docs/../escape.md", "docs/./same.md"),
)
def test_gate_report_route_projection_rejects_invalid_shapes(raw_route) -> None:
    from heddle.gate.types import GateReportFinding
    from heddle.runtime.gate_run import _recorded_routes_to

    assert (
        _recorded_routes_to(
            GateReportFinding("R-1", "Route", "important", route=raw_route)
        )
        == []
    )


# Round-2 review for event-level identity: one repeated prose title and one new
# title. A genuinely new run event records BOTH; titles are not identity keys.
# Their per-run ids collide with run 1 and must be reallocated with each
# recommendation attached.
IAR_SECOND_ROUND_REVIEW = scripted_review(
    [
        finding(
            "XX-C1",
            classification="report",
            severity="critical",
            title="Cache TTL vs event invalidation is a product call",
            recommendation="Keep event invalidation after the migration",
            route=None,
        ),
        finding(
            "XX-I1",
            classification="report",
            severity="important",
            title="Migration cutover date needs an operator ruling",
            recommendation="Cut over on the first Monday after m3 lands",
            route=None,
        ),
    ],
    summary=(
        "Cache TTL vs event invalidation is a product call; Migration "
        "cutover date needs an operator ruling"
    ),
)


def test_decisions_list_notes_milestones_landed_since_raised(
    run_cli, tmp_path, monkeypatch
) -> None:
    """
    Post-completion friction retrospective (review): `decisions list` derives the
        staleness note read-time — the golden core landed (passing scoped
        verification at 16:30Z), so a pending decision raised at 16:00Z names it;
        one raised after every landing derives empty; resolved lifecycle boundary stays
        empty.
    """
    host = tmp_path / "host"
    shutil.copytree(GOLDEN, host)
    monkeypatch.chdir(host)
    state_path = _state_path(host)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["decisions"].extend(
        [
            {
                "id": "code-quality#1",
                "source": "code-quality#1",
                "title": "Extract the seam-probe helper",
                "status": "pending",
                "kind": "question",
                "class": 1,
                "rationale": None,
                "resolution_source": None,
                "resolution": None,
                "routes_to": [],
                "created_at": "2026-06-08T16:00Z",
                "resolved_at": None,
            },
            {
                "id": "code-quality#2",
                "source": "code-quality#2",
                "title": "Raised after every landing",
                "status": "pending",
                "kind": "question",
                "class": 1,
                "rationale": None,
                "resolution_source": None,
                "resolution": None,
                "routes_to": [],
                "created_at": "2026-06-10T00:00Z",
                "resolved_at": None,
            },
        ]
    )
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")

    import json

    code, out, _err = run_cli(
        ["decisions", "list", "--feature", "nl-screening", "--json"]
    )
    assert code == 0
    listed = {item["id"]: item for item in json.loads(out)["data"]["decisions"]}
    assert listed["code-quality#1"]["milestones_landed_since"] == ["m1"], (
        "FAIL: a pending decision raised before m1's landing must name it"
    )
    assert listed["code-quality#2"]["milestones_landed_since"] == [], (
        "FAIL: a decision raised after every landing must derive empty"
    )
    assert listed["D1"]["milestones_landed_since"] == [], (
        "FAIL: resolved decisions never carry a staleness note"
    )

    code, human, _err = run_cli(["decisions", "list", "--feature", "nl-screening"])
    assert code == 0
    assert "landed since raised: m1" in human, (
        "FAIL: the human render must surface the note"
    )
    assert human.count("landed since raised") == 1, (
        "FAIL: only the stale batch gets the line — empty notes render nothing"
    )
