"""
Literal tiering fixtures; missing APIs fail inside tests, never collection.

These expectations are authored from the approved contract, not production
recommendations. Disposable hosts use the existing adoption and Git fixture.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import asdict
from pathlib import Path

from tests.operational_model_helpers import fresh_host

FEATURE = "tiering-example"
STAMP = "2026-09-09T00:00Z"
ROLES = (
    "spec-review",
    "plan-review",
    "review-test-scaffolding",
    "milestone-review",
    "peer-review-sequential",
    "behavior-review",
    "complexity-review",
    "robustness-analysis",
)
RETIRED = (
    "self-review",
    "code-quality",
    "review-correctness",
    "review-robustness",
    "review-architecture",
    "review-tests",
    "review-synthesis",
    "spec-review-synthesis",
    "plan-review-synthesis",
    "review-test-scaffolding-synthesis",
)
ASTRA = {"cli": "codex", "model": "gpt-6-astra", "reasoning_effort": "high"}
FABLE = {"cli": "claude", "model": "claude-fable-5-1", "reasoning_effort": "high"}
SOL = {"cli": "codex", "model": "gpt-5.6-sol", "reasoning_effort": "high"}
SOL_XHIGH = {**SOL, "reasoning_effort": "xhigh"}
OPUS = {"cli": "claude", "model": "claude-opus-5-5", "reasoning_effort": "xhigh"}


def api(name="heddle.contracts.feature_policy"):
    assert importlib.util.find_spec(name) is not None, (
        f"FAIL TIERING: required feature contract {name} is not implemented"
    )
    return importlib.import_module(name)


def axes(scope="small", complexity="low", testability="full"):
    return api().FeatureAxes(
        scope=scope,
        complexity=complexity,
        testability=testability,
        scope_rationale="Localized responsibility unless this case states otherwise",
        complexity_rationale="Case declares the consequential uncertainty",
        testability_rationale="Case declares the feasible executable evidence",
    )


def entry(
    role,
    *,
    mode="upper-limit",
    limit=2,
    minimum_rounds=1,
    primary=None,
    secondary=None,
    trigger=None,
):
    c = api()
    return c.GatePolicy(
        role=role,
        scope="milestone" if role == "milestone-review" else "feature",
        mode=mode,
        limit=limit,
        minimum_rounds=minimum_rounds,
        primary=c.Reviewer(**(primary or ASTRA)),
        secondary=c.Reviewer(**secondary) if secondary else None,
        trigger=trigger,
    )


def policy(*, overrides=None, assessment=None, revision=1):
    """A literal Light matrix; callers explicitly edit choices, not defaults."""
    values = {
        "spec-review": entry("spec-review"),
        "plan-review": entry("plan-review", primary=SOL),
        "review-test-scaffolding": entry("review-test-scaffolding", primary=SOL_XHIGH),
        "milestone-review": entry("milestone-review", primary=SOL),
        "peer-review-sequential": entry("peer-review-sequential", primary=OPUS),
        "behavior-review": entry(
            "behavior-review", mode="off", limit=None, minimum_rounds=0, primary=OPUS
        ),
        "complexity-review": entry(
            "complexity-review",
            mode="off",
            limit=None,
            minimum_rounds=0,
            primary=SOL,
        ),
        "robustness-analysis": entry(
            "robustness-analysis",
            mode="off",
            limit=None,
            minimum_rounds=0,
            primary=SOL_XHIGH,
        ),
    }
    values.update(overrides or {})
    return api().ConfirmedPolicy(
        revision=revision,
        axes=assessment or axes(),
        approval="User explicitly confirmed this complete fixture matrix",
        entries=tuple(values[role] for role in ROLES),
    )


def wire_policy(**kwargs):
    return {"schema": "heddle.feature-policy/v1", **asdict(policy(**kwargs))}


def blank_host(tmp_path, monkeypatch):
    # Existing support creates benign local check.py and isolated Git/adoption.
    host, _ = fresh_host(tmp_path, seed=False)
    monkeypatch.chdir(host)
    return host


def snapshot(root: Path):
    """Bytes and symlink identities, excluding only Git's diagnostic metadata."""
    return {
        p.relative_to(root).as_posix(): ("link", str(p.readlink()))
        if p.is_symlink()
        else ("file", p.read_bytes(), bool(p.stat().st_mode & 0o111))
        for p in root.rglob("*")
        if ".git" not in p.relative_to(root).parts and (p.is_file() or p.is_symlink())
    }


def prepare_input(*, route="heddle", assessment=None):
    return {
        "schema": "heddle.intake-input/v1",
        "route": route,
        "route_reason": "User explicitly selected " + route,
        "research": {"reference": "brief.md", "summary": "One declared behavior"},
        "axes": asdict(assessment or axes()),
    }


def new_operation(name, *args, **kwargs):
    from heddle.contracts import operations

    assert hasattr(operations, name), f"FAIL TIERING: typed {name} is missing"
    return getattr(operations, name)(*args, **kwargs)


def invoke(name, *args, **kwargs):
    from heddle.runtime.application import execute

    return execute(new_operation(name, *args, **kwargs))


def prepared(host, *, route="heddle"):
    (host / "brief.md").write_text("# Research\nOne declared behavior.\n")
    result = invoke(
        "FeaturePrepare",
        slug=FEATURE,
        area="runtime",
        payload=prepare_input(route=route),
    )
    assert result.ok, result.to_envelope()
    return result


def confirmed(host, **policy_kwargs):
    import yaml

    prepared(host)
    path = host / ".heddle" / "intake" / f"{FEATURE}.yaml"
    revision = yaml.safe_load(path.read_text())["revision"]
    result = invoke(
        "FeaturePolicy",
        slug=FEATURE,
        payload=wire_policy(**policy_kwargs),
        expect_revision=revision,
    )
    assert result.ok, result.to_envelope()
    return result
