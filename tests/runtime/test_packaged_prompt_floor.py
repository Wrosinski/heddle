"""
The packaged gate-prompt floor ships inside the distribution and renders.

adoption current boundary (A1) moved the prompt corpus to `heddle/resources/prompts/`
because
the previous floor was descriptor-relative (`Path(__file__).parents[2] /
prompts/<gate>.md`) — the checkout one level above the package directory, a
path that exists in a source tree and never inside a wheel. Every gate raised
`FileNotFoundError` from an installed artifact, and nothing was red: the whole
suite runs from a checkout, where the broken floor and the working corpus were
the same file.

That is the failure mode these two tests exist for. The first is structural
and instant; the second is the empirical proof, kept as a test rather than a
one-off script so the next resource move cannot silently un-prove it.

This is the second built-wheel harness in the suite; the first is
`test_ac10_built_wheel_nested_cwd_without_active_feature` in
`tests/runtime/test_search_acceptance.py`. The two are deliberately
independent copies: per the repo's own extraction rule (extract at the third
occurrence, not the second), a third one should lift the shared build and
install steps into a helper.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

import heddle
from heddle.gate.prompt import (
    _PARTIAL_TOKEN,
    PACKAGED_PROMPTS_DIR,
    PARTIALS_DIRNAME,
    packaged_prompt_path,
)
from heddle.gate.registry import GATES
from heddle.kernel.model import ENGINEERING_PRINCIPLES_REL
from tests.operational_model_helpers import document
from tests.runtime.adoption_helpers import adopt_fixture_host
from tests.runtime.test_document_altitude import (
    PRODUCT_ASSESSMENT_GUIDANCE,
    REVIEW_RECORD_GUIDANCE,
)
from tests.runtime.wheel_harness import (
    build_installed_wheel,
)
from tests.runtime.wheel_harness import (
    run as _run,
)
from tests.tiering_helpers import snapshot, wire_policy

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "tests/fixtures/workspaces/golden"

# Run inside the installed venv: resolve every gate's packaged template, splice
# its partials, and report what came back. `expand_partials` raises on a
# missing partial, so a corpus that ships templates without their `_partials/`
# tree fails here as a non-zero exit rather than a quiet half-render.
_PROBE = """
import hashlib
import json
from pathlib import Path

import heddle
from heddle.gate.prompt import (
    PACKAGED_STANDARDS_DOC,
    expand_partials,
    packaged_prompt_path,
    partials_dir_for,
)
from heddle.gate.registry import GATES

package_root = Path(heddle.__file__).resolve().parent
report = {
    "package_root": str(package_root),
    "standards_present": PACKAGED_STANDARDS_DOC.is_file(),
    "standards_sha256": (
        hashlib.sha256(PACKAGED_STANDARDS_DOC.read_bytes()).hexdigest()
        if PACKAGED_STANDARDS_DOC.is_file()
        else None
    ),
    "rendered": {},
    "missing": [],
    "outside_package": [],
    "unexpanded": [],
}
for name, gate in sorted(GATES.items()):
    path = packaged_prompt_path(gate.prompt_template)
    if not path.is_file():
        report["missing"].append(name)
        continue
    if not path.is_relative_to(package_root):
        report["outside_package"].append(name)
    rendered = expand_partials(path.read_text(encoding="utf-8"), partials_dir_for(path))
    report["rendered"][name] = len(rendered)
    if "[partial-" in rendered:
        report["unexpanded"].append(name)
print(json.dumps(report))
"""


def _prepare_show_prompt_host(tmp_path: Path) -> Path:
    host = tmp_path / "show-prompt-host"
    shutil.copytree(GOLDEN, host)
    config_path = host / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gates"]["enabled"] = sorted(GATES)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    adopt_fixture_host(host)

    state_path = host / "plans/nl-screening/state.yaml"
    state = document(
        schema="heddle.state/v9",
        feature="nl-screening",
        spec="docs/features/analysis/nl-screening.md",
        stage="implement",
        feature_policy=wire_policy(),
    )
    state.pop("tier", None)
    for milestone in state["milestones"]:
        milestone.pop("estimated_hours")
    state["milestones"][0]["status"] = "done"
    state["milestones"][1]["status"] = "current"
    state["milestones"][1]["owns"] = [
        "src/example/screening/validate",
        "src/tests/screening/validate",
    ]
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")

    source = host / "src/example/screening/validate/fixture.py"
    test = host / "src/tests/screening/validate/test_fixture.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    test.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("VALUE = 'baseline'\n", encoding="utf-8")
    test.write_text("def test_fixture():\n    assert True\n", encoding="utf-8")

    for argv in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Heddle Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
    ):
        result = _run(argv, cwd=host)
        assert result.returncode == 0, (
            f"installed show-prompt host setup failed: {argv}\n{result.stderr}"
        )
    source.write_text("VALUE = 'changed'\n", encoding="utf-8")

    return host


def test_packaged_floor_resolves_inside_the_installed_package() -> None:
    """The floor is package-relative, so anything `packages = ["heddle"]`
    ships is reachable from an install.

    The instant half of the rail: it needs no build, so it fails in the same
    edit that moves a prompt out of the package rather than only in the lane
    that builds a wheel.
    """
    package_root = Path(heddle.__file__).resolve().parent
    assert PACKAGED_PROMPTS_DIR.is_relative_to(package_root), (
        f"the packaged prompt floor escaped the distribution: "
        f"{PACKAGED_PROMPTS_DIR} is not under {package_root} — an installed "
        "artifact cannot reach it (M8A P0-1)"
    )

    partials_dir = PACKAGED_PROMPTS_DIR / PARTIALS_DIRNAME
    unreachable: list[str] = []
    for name, gate in sorted(GATES.items()):
        template = packaged_prompt_path(gate.prompt_template)
        if not template.is_relative_to(package_root) or not template.is_file():
            unreachable.append(f"{name}: {gate.prompt_template}")
            continue
        # A template without its partials is a template that renders with its
        # rules silently missing, so the tree only counts as shipped as a unit.
        text = template.read_text(encoding="utf-8")
        unreachable.extend(
            f"{name}: [partial-{partial}]"
            for partial in sorted(set(_PARTIAL_TOKEN.findall(text)))
            if not (partials_dir / f"{partial}.md").is_file()
        )
    assert not unreachable, (
        f"packaged corpus incomplete inside the distribution: {unreachable}"
    )

    for asset in ("conventions.yaml",):
        assert (PACKAGED_PROMPTS_DIR / asset).is_file(), (
            f"the packaged corpus lost {asset!r} — the convention manifest ships "
            "with the prompts it describes"
        )


def test_packaged_standards_doc_ships_with_the_distribution() -> None:
    """The shared prompt-authoring contract ships inside the package (P0-5).

    `compute_prompt_version` hashes the standards document into every gate's
    version. Before P0-5 it read `docs/workflow/prompt-authoring-standards.md`
    from the repo root — a path no host and no wheel ever has — and silently
    hashed without it when absent, so identical prompt bytes landed in
    different version buckets in-repo versus installed. The packaged read
    makes absence mean a broken installation, and `compute_prompt_version`
    declines to stamp (returns None) rather than hashing a silently different
    input set (`test_prompt_partials.py` pins that half).
    """
    from heddle.gate.prompt import PACKAGED_STANDARDS_DOC

    package_root = Path(heddle.__file__).resolve().parent
    assert PACKAGED_STANDARDS_DOC.is_file(), (
        "the distribution no longer ships prompt-authoring-standards.md — the "
        "contract every gate prompt is written against (M8A P0-5)"
    )
    assert PACKAGED_STANDARDS_DOC.is_relative_to(package_root), (
        f"the packaged standards doc escaped the distribution: "
        f"{PACKAGED_STANDARDS_DOC} is not under {package_root} — an installed "
        "artifact cannot reach it"
    )


# The repo-root paths a shipped asset may cite even though a fresh host lacks
# them, each with the reason the citation is still true wherever the asset runs:
# - the principles seed: the path `heddle init` will project it to (adoption operational
# core,
# scope §5; rulings I-2 and K-2). Today the kernel's `principles-not-ratified`
# blocker already names exactly this path, so the citation is the remedy
# instruction, not a dangling read. Derived from the kernel's single owner so
# a operational core relocation moves the allowlist with it.
# - the feature-descriptions record: host-owned, written by the workflow itself
# at completion; the one read (specify.briefing) carries an inline fallback for
# when it does not exist yet.
_HOST_SEED_CITATIONS = frozenset(
    {
        ENGINEERING_PRINCIPLES_REL.as_posix(),
        "docs/features/_descriptions.yaml",
    }
)

# Any docs/ or scripts/ file citation: the two repo-root trees that exist only
# in the heddle checkout (docs/workflow, docs/design, docs/patterns, the
# completion-stage
# scripts have all shipped dead citations at least once). plans/ paths are
# host-owned workspace writes, never checkout reads, so they are not swept.
_HOST_DOC_CITATION = re.compile(
    r"(?:docs|scripts)/[A-Za-z0-9_./-]*\.(?:md|py|sh|ya?ml|json)"
)


def test_shipped_assets_require_no_document_the_host_lacks() -> None:
    """
    P0-5's invariant as a rail: a packaged asset citing a repo-root `docs/`
        or `scripts/` path demands a file that exists only in the heddle checkout —
        dead on every host and inside every wheel. current boundary hit this class twice
        in
        docs/workflow alone, both found by hand, and the P0-5 review round found
        six more across docs/patterns, docs/design, and scripts/. This sweep covers
        every file in the resource tree, whatever its suffix.

    """
    resources_root = Path(heddle.__file__).resolve().parent / "resources"
    offenders: list[str] = []
    for path in sorted(resources_root.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for cited in _HOST_DOC_CITATION.findall(text):
            if cited not in _HOST_SEED_CITATIONS:
                offenders.append(f"{path.relative_to(resources_root)}: {cited}")
    assert not offenders, (
        "packaged assets cite checkout-only files a host will not have — "
        f"reword the citation or package the document (M8A P0-5): {offenders}"
    )


@pytest.mark.toolchain
def test_built_wheel_renders_every_gate_prompt_outside_the_checkout(
    tmp_path: Path,
) -> None:
    """Build the wheel, install it into a clean venv, and render from a cwd
    outside the checkout — the condition under which the pre-A1 floor failed.

    Structural reachability is not the same claim as "the build ships it":
    this is the half that would catch a packaging exclusion.
    """
    installed = build_installed_wheel(tmp_path)
    venv_python = installed.python
    target_site = installed.site_packages

    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    clean_env = installed.env
    # `tmp_path` is outside the checkout, so nothing here can fall back to the
    # repo tree the way an editable install silently would.
    result = _run(
        [str(venv_python), str(probe)],
        cwd=tmp_path,
        env=clean_env,
    )
    assert result.returncode == 0, (
        f"installed prompt render failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    report = json.loads(result.stdout)
    package_root = Path(report["package_root"])
    assert package_root.is_relative_to(target_site), (
        f"the probe imported heddle from {package_root}, not the installed "
        f"package under {target_site} — the run proves nothing about a wheel"
    )
    assert not report["missing"], (
        f"gate prompts absent from the installed artifact: {report['missing']} "
        "(the pre-A1 failure: every gate raised FileNotFoundError here)"
    )
    assert report["standards_present"], (
        "prompt-authoring-standards.md absent from the installed artifact — "
        "compute_prompt_version would decline to stamp on every install "
        "(M8A P0-5)"
    )
    from heddle.gate.prompt import PACKAGED_STANDARDS_DOC

    checkout_sha256 = hashlib.sha256(PACKAGED_STANDARDS_DOC.read_bytes()).hexdigest()
    assert report["standards_sha256"] == checkout_sha256, (
        "installed prompt-authoring-standards.md differs from the checkout "
        f"source: installed={report['standards_sha256']} "
        f"checkout={checkout_sha256}"
    )
    assert not report["outside_package"], (
        f"gate prompts resolved outside the installed package: "
        f"{report['outside_package']}"
    )
    assert not report["unexpanded"], (
        f"partials did not expand from the installed corpus: {report['unexpanded']}"
    )
    assert set(report["rendered"]) == set(GATES), (
        "the installed registry and corpus disagree with the checkout: "
        f"{sorted(set(GATES) ^ set(report['rendered']))}"
    )
    assert all(size > 0 for size in report["rendered"].values())

    host = _prepare_show_prompt_host(tmp_path)
    public_rendered: set[str] = set()
    for gate_name, gate in sorted(GATES.items()):
        result = installed.run(
            "show-prompt",
            gate_name,
            "--feature",
            "nl-screening",
            "--cli",
            gate.default_cli,
            "--json",
            cwd=host,
        )
        assert result.returncode == 0, (
            f"installed public show-prompt failed for {gate_name}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True
        prompt = envelope["data"]["prompt"]
        assert isinstance(prompt, str) and prompt.strip(), (
            f"installed public show-prompt returned no instructions for {gate_name}"
        )
        assert "[partial-" not in prompt, (
            f"installed public show-prompt left a partial token in {gate_name}"
        )
        assert envelope["data"]["decision_policy_source"] == "packaged"
        policy_path = Path(envelope["data"]["decision_policy_path"])
        assert policy_path.is_relative_to(target_site)
        expected_policy = (
            REPO_ROOT / "heddle/resources/decision-routing.md"
        ).read_text()
        assert prompt.count(expected_policy) == 1
        public_rendered.add(envelope["data"]["gate"])
    assert public_rendered == set(GATES)

    kickoff = installed.run("kickoff", "--feature", "nl-screening", "--json", cwd=host)
    assert kickoff.returncode == 0, kickoff.stdout + kickoff.stderr
    data = json.loads(kickoff.stdout)["data"]
    assert data["decision_policy_source"] == "packaged"
    assert data["briefing"].count(expected_policy) == 1


@pytest.mark.toolchain
@pytest.mark.xfail(
    strict=True,
    reason="completion-feedback-contracts-v1 packaged review guidance is absent",
)
def test_built_wheel_delivers_review_assessment_location_guidance(
    tmp_path: Path,
) -> None:
    """AC-7/AC-8 red: installed public Kickoff ships both review contracts."""
    installed = build_installed_wheel(tmp_path)
    host = _prepare_show_prompt_host(tmp_path)
    state_path = host / "plans/nl-screening/state.yaml"

    for stage in ("spec-review", "plan-review"):
        state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        state["stage"] = stage
        state["authorized_through"] = stage
        state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
        before = snapshot(host)

        orient = installed.run(
            "orient", "--feature", "nl-screening", "--json", cwd=host
        )
        kickoff = installed.run(
            "kickoff", "--feature", "nl-screening", "--json", cwd=host
        )
        assert orient.returncode == 0, orient.stdout + orient.stderr
        assert kickoff.returncode == 0, kickoff.stdout + kickoff.stderr
        orient_data = json.loads(orient.stdout)["data"]
        text = json.loads(kickoff.stdout)["data"]["briefing"]
        assert orient_data["workspace"] == "plans/nl-screening/"
        assert REVIEW_RECORD_GUIDANCE in text
        assert PRODUCT_ASSESSMENT_GUIDANCE in text
        assert "data.workspace" in text
        assert "reviews/" in text
        assert "native dispositions" in text.lower()
        assert "plans/<slug>/reviews/" not in text
        assert "every assessment is a workflow review record" not in text.lower()
        assert "assessment is required" not in text.lower()
        assert snapshot(host) == before
