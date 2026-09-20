"""AC-4/5: exact bookkeeping exemptions never weaken declared byte identity."""

from dataclasses import replace

import pytest

from heddle.kernel.project_config import load_project_config
from heddle.kernel.state import read_state_file
from heddle.runtime.verification import (
    assess_current_verification,
    workflow_control_paths,
)
from tests.proof_continuity_helpers import (
    FEATURE,
    SPEC,
    diagnostics,
    execute,
    ops,
    source_host,
    verify,
)
from tests.tiering_helpers import snapshot


@pytest.mark.parametrize("owned", [False, True])
def test_record_edit_freshness_follows_explicit_ownership(tmp_path, monkeypatch, owned):
    """Survivor: neither ignore rules nor control exemptions rewrite ownership."""
    owns = ["src/example.py", "tests/check.py", *([SPEC] if owned else [])]
    root, path = source_host(tmp_path, monkeypatch, owns=owns)
    verify(path, "m1")
    (root / SPEC).write_text((root / SPEC).read_text() + "\nSession bookkeeping.\n")
    state = read_state_file(path)
    assert assess_current_verification(root, state, "m1").status == (
        "content-stale" if owned else "fresh"
    )


def test_explicit_record_dependency_is_explained_without_rewriting_it(
    tmp_path, monkeypatch
):
    for index, spelling in enumerate((SPEC, f"{SPEC}/")):
        case = tmp_path / str(index)
        case.mkdir()
        root, _path = source_host(case, monkeypatch, owns=["src/example.py", spelling])
        before = snapshot(root)
        result = execute(ops.Validate(feature=FEATURE))
        assert result.ok, result.to_envelope()
        advice = diagnostics(result)
        assert SPEC in advice and "depend" in advice.lower(), advice
        assert (
            "Bookkeeping edits to unowned workflow records do not invalidate" in advice
        )
        assert "material contract inputs intentionally owned" in advice
        assert "editing their bytes makes proof stale" in advice
        assert "Explicit citations retain separate evidence identity" in advice
        assert "even when the record is unowned" in advice
        assert snapshot(root) == before


@pytest.mark.parametrize(
    "plans,specs", [("plans", "docs/features"), ("work/plans", "product/specs")]
)
def test_controls_use_exact_configured_paths_not_parent_directory_exemptions(
    tmp_path, monkeypatch, plans, specs
):
    root, path = source_host(tmp_path, monkeypatch)
    config = load_project_config(root)
    config = replace(config, layout=replace(config.layout, plans=plans, specs=specs))
    controls = workflow_control_paths(config, read_state_file(path))
    expected = {
        f"{plans}/friction-log.md",
        f"{plans}/gate-effectiveness.md",
        f"{plans}/.briefs/{FEATURE}.md",
        f"{specs}/_index.md",
        f"{specs}/_descriptions.yaml",
    }
    assert expected <= set(controls.exact)
    for neighbor in (
        f"{plans}/unrelated.md",
        f"{plans}/.briefs/another.md",
        f"{specs}/another-feature.md",
        "docs/analysis/another.md",
    ):
        assert neighbor not in controls.exact
        assert not any(
            neighbor == p or neighbor.startswith(p + "/") for p in controls.roots
        )


def test_control_and_neighbor_attribution_use_the_same_exact_exemption(
    tmp_path, monkeypatch
):
    root, path = source_host(tmp_path, monkeypatch)
    control = "plans/friction-log.md"
    neighbor = "plans/product-analysis.md"
    for relative in (control, neighbor):
        (root / relative).write_text("Other work, not this feature.\n")

    def attribute(relative):
        return execute(
            ops.AttributeSources(
                {
                    "schema": "heddle.source-attribution-input/v1",
                    "attributions": [
                        {
                            "paths": [relative],
                            "references": ["tests/check.py"],
                            "reason": "Independent product analysis.",
                        }
                    ],
                },
                feature=FEATURE,
            )
        )

    before = path.read_bytes()
    refused = attribute(control)
    assert not refused.ok, "A coverage-exempt control must not need attribution."
    assert path.read_bytes() == before
    accepted = attribute(neighbor)
    assert accepted.ok, accepted.to_envelope()


@pytest.mark.parametrize("fault", [None, "malformed", "binding-mismatch"])
def test_only_valid_available_admission_research_gets_an_exact_exemption(
    tmp_path, monkeypatch, fault
):
    from heddle.kernel.project_config import KernelError
    from tests.tiering_helpers import FEATURE as ADMITTED_FEATURE
    from tests.tiering_helpers import blank_host, confirmed, invoke

    root = blank_host(tmp_path, monkeypatch)
    confirmed(root)
    admitted = invoke("FeatureStart", slug=ADMITTED_FEATURE)
    assert admitted.ok, admitted.to_envelope()
    state = read_state_file(root / f"plans/{ADMITTED_FEATURE}/state.yaml")
    intake = root / f".heddle/intake/{ADMITTED_FEATURE}.yaml"
    if fault == "malformed":
        intake.write_text("invalid: [\n")
    elif fault == "binding-mismatch":
        intake.write_text(intake.read_text().replace("brief.md", "another.md"))
    before = snapshot(root)
    if fault:
        with pytest.raises(KernelError):
            workflow_control_paths(load_project_config(root), state)
    else:
        controls = workflow_control_paths(load_project_config(root), state)
        assert "brief.md" in controls.exact
        assert "another.md" not in controls.exact and "." not in controls.roots
    assert snapshot(root) == before
