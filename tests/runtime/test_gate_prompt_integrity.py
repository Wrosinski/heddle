"""
Integrated scaffold-stage acceptance scaffold for gate prompt integrity.

The acceptance command drives public CLI/runtime boundaries.  Focused unit
files own exhaustive encoding and malformed-input matrices; this file proves
those contracts remain connected through real workspace state and recording.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from heddle.gate import prompt as gate_prompt
from heddle.gate.prompt import PACKAGED_PROMPTS_DIR
from heddle.gate.registry import GATES
from tests.operational_model_helpers import (
    authored_evidence,
)
from tests.runtime.adoption_helpers import adopt_fixture_host
from tests.runtime.validation_helpers import ensure_files
from tests.structured_review_helpers import finding, scripted_review
from tests.tiering_helpers import ASTRA, OPUS
from tests.tiering_helpers import entry as tier_entry
from tests.tiering_review_helpers import (
    V7_FEATURE,
    current_host,
    provider_transport,
    review_content,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests/fixtures/workspaces/golden"


def _host(tmp_path: Path) -> Path:
    host = tmp_path / "host"
    shutil.copytree(GOLDEN, host)
    adopt_fixture_host(host)
    spec = host / "docs/features/analysis/nl-screening.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("# NL screening\n\nVersion one.\n", encoding="utf-8")
    return host


def _enable_auto(host: Path) -> None:
    config = host / ".heddle.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + '\nautopilot:\n  test_command: "true"\n',
        encoding="utf-8",
    )
    principles = host / "docs/workflow/engineering-principles.md"
    principles.parent.mkdir(parents=True, exist_ok=True)
    principles.write_text(
        "---\nstatus: ratified\n---\n\n# Principles\n", encoding="utf-8"
    )


def _state(host: Path) -> dict:
    return yaml.safe_load(
        (host / "plans/nl-screening/state.yaml").read_text(encoding="utf-8")
    )


def _gate_runs(state: dict, gate: str) -> list[dict]:
    return [
        run for fact in state["gates"] if fact["gate"] == gate for run in fact["runs"]
    ]


def _seed_peer_review_actionability(host: Path, *, owner_status: str | None) -> Path:
    """Create a fully verified peer-review state with one REPORT synthesis."""
    state_path = host / "plans/nl-screening/state.yaml"
    document = _state(host)
    document["stage"] = "peer-review"
    document["authorized_through"] = "complete"
    document["flow"] = "auto"
    for milestone in document["milestones"]:
        milestone["status"] = "done"
        for task in milestone.get("tasks", []):
            task["status"] = "done"
    ensure_files(host, [p for m in document["milestones"] for p in m["owns"]])
    document["verifications"] = [
        {
            "scope": milestone["id"],
            "command": milestone["verification"]["command"],
            "evidence": authored_evidence(host, milestone["owns"]),
            "exit_code": 0,
            "at": f"2026-07-23T19:0{index}Z",
            "log": f"verification/{milestone['id']}.log",
        }
        for index, milestone in enumerate(document["milestones"], start=1)
    ]
    all_paths = sorted({p for m in document["milestones"] for p in m["owns"]})
    for scope, command_key in (
        ("acceptance", "acceptance_test"),
        ("smoke", "smoke_test"),
    ):
        document["verifications"].append(
            {
                "scope": scope,
                "command": document["commands"][command_key],
                "exit_code": 0,
                "at": "2026-07-23T19:10Z",
                "log": f"verification/{scope}.log",
                "evidence": authored_evidence(
                    host, all_paths, kind="feature-owned-union"
                ),
            }
        )
    document["gates"] = []
    from tests.readiness_helpers import authored_current_event
    from tests.structured_review_helpers import complete_fixture_synthesis, content

    # These are explicitly authored canonical fixtures, with current sources and
    # exact producer references. The test varies only the finding owner below.
    state_path.write_text(yaml.safe_dump(document, sort_keys=False))
    sources = []
    for gate in (
        "review-correctness",
        "review-architecture",
        "review-robustness",
        "review-tests",
    ):
        event = authored_current_event(state_path, gate, "feature")
        document["gates"].append({"gate": gate, "scope": "feature", "runs": [event]})
        state_path.write_text(yaml.safe_dump(document, sort_keys=False))
        body = json.loads((state_path.parent / event["artifact"]).read_text())[
            "content"
        ]
        sources.append((event, body))
    payload = content(
        "review-synthesis",
        findings=[finding("SY-I1", title="Operator choice", classification="report")],
    )
    complete_fixture_synthesis(payload, sources)
    refs = {}
    for row in payload["synthesis"]["assessment_coverage"]:
        refs.setdefault(row["source"]["run_id"], row["source"])
    payload["synthesis"]["conflicts"] = [
        {
            "id": "CON-1",
            "kind": "interpretation",
            "owner_finding_id": "SY-I1",
            "perspectives": [
                {
                    "finding_ref": None,
                    "assessment_ref": ref,
                    "position": f"Fixture interpretation {index + 1}",
                }
                for index, ref in enumerate(list(refs.values())[:2])
            ],
            "explanation": "The authored interpretations require a durable owner.",
        }
    ]
    event = authored_current_event(
        state_path, "review-synthesis", "feature", payload=payload
    )
    document["gates"].append(
        {"gate": "review-synthesis", "scope": "feature", "runs": [event]}
    )
    document["decisions"] = [
        item
        for item in document["decisions"]
        if not str(item.get("source", "")).startswith("review-synthesis#")
    ]
    if owner_status is not None:
        owner = {
            "id": "synthesis-owner",
            "kind": "finding",
            "class": 4,
            "origin_run_id": document["gates"][-1]["runs"][0]["run_id"],
            "origin_finding_id": "SY-I1",
            "rationale": None,
            "resolution": None,
            "resolution_source": None,
            "resolved_at": None,
            "source": "review-synthesis#1",
            "title": "Operator choice",
            "status": owner_status,
            "routes_to": [],
            "created_at": "2026-07-23T20:10Z",
        }
        if owner_status == "resolved":
            owner.update(
                {
                    "resolution": {"kind": "disposition"},
                    "rationale": "Selected contract",
                    "resolution_source": "user",
                    "routes_to": ["docs/features/analysis/nl-screening.md"],
                    "resolved_at": "2026-07-23T20:11Z",
                }
            )
        document["decisions"].append(owner)
    document["sessions"].append(
        {
            "started_at": "2026-07-23T19:50Z",
            "ended_at": "2026-07-23T19:51Z",
            "completed": ["peer review prepared"],
            "started": [],
            "key_context": "actionability fixture",
            "next_steps": "synthesize reviews",
            "blockers": [],
            "stage": "peer-review",
        }
    )
    state_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return state_path


def _gate_projection(envelope: dict) -> dict:
    """Complete machine payload normalized only for fresh/cache provenance."""
    data = copy.deepcopy(envelope["data"])
    assert set(data) >= {
        "feature",
        "gate",
        "scope",
        "status",
        "cached",
        "attempt",
        "max_attempts",
        "rerun_allowed",
        "cap_decision_recorded",
        "findings",
        "artifact",
    }
    data.pop("cached")
    return data


def _spec_synthesis_artifact() -> dict:
    return scripted_review()


def _test_scaffolding_synthesis_artifact() -> dict:
    return scripted_review()


@pytest.mark.acceptance
def test_ac1_to_ac5_public_show_run_doctor_cache_and_recording_flow(
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """AC-1..AC-5: current public surfaces share prepared prompt truth."""
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        overrides={
            "spec-review": tier_entry("spec-review", primary=OPUS, secondary=ASTRA)
        },
    )
    calls = provider_transport(monkeypatch, review_content("spec-review"))
    resolve = getattr(gate_prompt, "resolve_gate_prompt", None)
    assert callable(resolve), "FAIL AC-1: production prompt resolver is missing"
    resolved_prompts = []

    def observe_resolution(*args, **kwargs):
        value = resolve(*args, **kwargs)
        resolved_prompts.append(value)
        return value

    monkeypatch.setattr(gate_prompt, "resolve_gate_prompt", observe_resolution)

    show_code, show_out, _ = run_cli(
        ["show-prompt", "spec-review", "--feature", V7_FEATURE, "--json"]
    )
    assert show_code == 0
    shown = envelope_tools.parse(show_out)["data"]
    assert "operational-example.md" in shown["prompt"]
    assert "[partial-" not in shown["prompt"]
    assert shown["prompt_source"] in {"override", "generated", "packaged"}

    code, out, _ = run_cli(
        ["run-gate", "--json", "--feature", V7_FEATURE, "spec-review"]
    )
    assert code == 0, out
    assert len(calls) == 1
    runs = _gate_runs(yaml.safe_load(state_path.read_text()), "spec-review")
    assert len(runs) == 1
    assert len(runs[0]["input_hash"]) == 64
    assert len(runs[0]["review_basis_hash"]) == 64

    cached_code, cached_out, _ = run_cli(
        ["run-gate", "--json", "--feature", V7_FEATURE, "spec-review"]
    )
    assert cached_code == 0, cached_out
    assert envelope_tools.parse(cached_out)["data"]["cached"] is True
    assert len(calls) == 1, "FAIL AC-5: a converged current-identity run must be reused"

    codex_code, codex_out, _ = run_cli(
        [
            "run-gate",
            "--json",
            "--feature",
            V7_FEATURE,
            "--cli",
            "codex",
            "spec-review",
        ]
    )
    assert codex_code == 0, codex_out
    assert len(calls) == 2
    runs = _gate_runs(yaml.safe_load(state_path.read_text()), "spec-review")
    assert {run["reviewer_slot"] for run in runs} == {"primary", "secondary"}

    doctor_code, doctor_out, _ = run_cli(["doctor", "--json", "--feature", V7_FEATURE])
    assert doctor_code in (0, 4), doctor_out
    diagnostics = envelope_tools.parse(doctor_out)["diagnostics"]
    source_names = {
        item.get("source")
        for item in diagnostics
        if str(item.get("code", "")).startswith("resource-")
    }
    assert shown["prompt_source"] in source_names
    spec_resolutions = [
        resolved for resolved in resolved_prompts if resolved.gate == "spec-review"
    ]
    assert len(spec_resolutions) >= 4
    assert {resolved.template.source for resolved in spec_resolutions} == {
        shown["prompt_source"]
    }
    effective_instructions = {
        resolved.effective_instructions for resolved in spec_resolutions
    }
    assert shown["prompt"] in effective_instructions
    assert len(effective_instructions) == 2, (
        "Claude and Codex share resolved source bytes but retain their "
        "deliberately different output-capture constraints"
    )


@pytest.mark.acceptance
@pytest.mark.parametrize("resource_tier", ("packaged", "generated", "override"))
def test_ac1_public_surfaces_agree_for_each_resource_tier(
    resource_tier: str,
    run_cli,
    envelope_tools,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """
    AC-1 red discriminator: show/run/doctor agree within every resource precedence tier.
    """

    host, _state_path = current_host(tmp_path, monkeypatch)
    for tier in ("generated", "overrides"):
        root = host / ".heddle" / tier
        (root / "spec-review.prompt.md").unlink(missing_ok=True)
        shutil.rmtree(root / "_partials", ignore_errors=True)
    if resource_tier != "packaged":
        source = PACKAGED_PROMPTS_DIR
        tier_root = (
            host
            / ".heddle"
            / ("generated" if resource_tier == "generated" else "overrides")
        )
        tier_root.mkdir(parents=True, exist_ok=True)
        (tier_root / "spec-review.prompt.md").write_bytes(
            (source / "spec-review.md").read_bytes()
        )
        shutil.copytree(
            source / "_partials",
            tier_root / "_partials",
            dirs_exist_ok=True,
        )
    unrelated_cwd = host / "unrelated/nested"
    unrelated_cwd.mkdir(parents=True)
    monkeypatch.chdir(unrelated_cwd)
    calls = provider_transport(monkeypatch, review_content("spec-review"))
    resolve = getattr(gate_prompt, "resolve_gate_prompt", None)
    assert callable(resolve), "FAIL AC-1: production prompt resolver is missing"
    active_surface = "show"
    observed: dict[str, list] = {"show": [], "run": [], "doctor": []}

    def observe_resolution(*args, **kwargs):
        value = resolve(*args, **kwargs)
        observed[active_surface].append(value)
        return value

    monkeypatch.setattr(gate_prompt, "resolve_gate_prompt", observe_resolution)

    show_code, show_out, _ = run_cli(
        ["show-prompt", "spec-review", "--feature", V7_FEATURE, "--json"]
    )
    assert show_code == 0, show_out
    shown = envelope_tools.parse(show_out)["data"]
    assert shown["prompt_source"] == resource_tier

    active_surface = "run"
    run_code, run_out, _ = run_cli(
        ["run-gate", "--json", "--feature", V7_FEATURE, "spec-review"]
    )
    assert run_code == 0, run_out
    assert len(calls) == 1

    active_surface = "doctor"
    doctor_code, doctor_out, _ = run_cli(["doctor", "--json", "--feature", V7_FEATURE])
    assert doctor_code in (0, 4), doctor_out
    doctor = envelope_tools.parse(doctor_out)
    prompt_diagnostics = [
        item
        for item in doctor["diagnostics"]
        if item.get("code") == "resource-spec-review-prompt"
    ]
    assert len(prompt_diagnostics) == 1
    assert resource_tier in prompt_diagnostics[0]["message"]
    for surface in ("show", "run", "doctor"):
        matching = [
            resolved for resolved in observed[surface] if resolved.gate == "spec-review"
        ]
        assert matching, f"FAIL AC-1: {surface} bypassed the shared resolver"
        assert {resolved.template.source for resolved in matching} == {resource_tier}
        assert {resolved.effective_instructions for resolved in matching} == {
            shown["prompt"]
        }


@pytest.mark.acceptance
def test_ac2_featureless_preview_is_explicitly_non_runnable(
    run_cli, envelope_tools, tmp_path: Path, monkeypatch
) -> None:
    """AC-2 red discriminator: only fact-free preview has no run identity."""
    from heddle.gate.types import GateType

    host = _host(tmp_path)
    prompt = host / "prompts/featureless-fixture.md"
    prompt.parent.mkdir()
    prompt.write_text("# Featureless fixture\n", encoding="utf-8")
    gate = GateType(
        name="featureless-fixture",
        requires_plan=False,
        requires_milestone=False,
        requires_diff=False,
        supported_clis=("claude",),
        tool_permissions=(),
        output_contract=None,
        prompt_template="prompts/featureless-fixture.md",
    )
    monkeypatch.setitem(GATES, gate.name, gate)
    monkeypatch.chdir(host)

    code, out, _ = run_cli(["show-prompt", gate.name, "--json"])
    data = envelope_tools.parse(out)["data"]
    assert code == 0
    assert data["run_identity"] is None
    assert data["identity_status"] == "no run identity was prepared"
    assert data["prompt_identity"]["review_basis_hash"] is None
    assert data["prompt_identity"]["input_hash"] is None

    bound_code, bound_out, _ = run_cli(["show-prompt", "spec-review", "--json"])
    assert bound_code == 3
    assert envelope_tools.parse(bound_out)["error"]["code"] == "workspace-invalid"


def _mutate_native_review(value, mutation):
    value = copy.deepcopy(value)
    payload = value.get("content", value)
    item = payload["findings"][0]
    if mutation in {"missing-severity", "missing-confidence", "missing-classification"}:
        del item[mutation.removeprefix("missing-")]
    elif mutation == "extended-classification":
        item["classification"] = "REPORT (operator)"
    elif mutation == "missing-recommended":
        del item["decision"]["recommended"]
    elif mutation in {"implement-recommended", "ignore-recommended"}:
        item["classification"] = mutation.removesuffix("-recommended")
    elif mutation == "missing-routing":
        del payload["synthesis"]["conflicts"][0]["owner_finding_id"]
    elif mutation == "unknown-routing":
        payload["synthesis"]["conflicts"][0]["owner_finding_id"] = "SY-MISSING"
    elif mutation == "missing-verdict":
        del payload["verdict"]
    elif mutation in {"malformed-verdict", "extended-status"}:
        payload["verdict"]["status"] = "PASSING"
    elif mutation == "extended-rerun":
        payload["verdict"]["rerun_recommended"] = "No thanks"
    raw = json.dumps(value)
    if mutation.startswith("duplicate-"):
        key = {
            "recommended": "recommended",
            "routing": "owner_finding_id",
            "rerun": "rerun_recommended",
        }.get(mutation[10:], mutation[10:])
        pattern = rf'"{key}": ("[^"\n]*"|false|true)'
        raw, count = re.subn(
            pattern, lambda m: m.group(0) + ", " + m.group(0), raw, count=1
        )
        assert count == 1
    return raw.encode()
