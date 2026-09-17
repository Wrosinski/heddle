"""Integrated acceptance coverage for repository validation."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from heddle.runtime.validate import LEDGER_DRIFT
from tests.runtime.validation_helpers import (
    clean_current_host,
    copy_host,
    read_yaml,
    write_yaml,
)
from tests.runtime.validation_helpers import (
    clone_workspace as _clone_workspace,
)
from tests.runtime.validation_helpers import (
    seed_guardrails as _seed_guardrails,
)
from tests.runtime.validation_helpers import (
    seed_single_pattern_violation as _seed_single_pattern_violation,
)
from tests.runtime.validation_helpers import (
    state_path as _state_path,
)
from tests.runtime.validation_helpers import (
    workspace_path as _workspace,
)
from tests.runtime.validation_helpers import (
    write_pattern as _write_pattern,
)

pytestmark = pytest.mark.acceptance

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"
RUNTIME_DIR = REPO_ROOT / "heddle" / "runtime"
KERNEL_DIR = REPO_ROOT / "heddle" / "kernel"


def _clean_host(tmp_path: Path, name: str) -> Path:
    return clean_current_host(tmp_path, TINY, name)


def _run_validate(run_cli, envelope_tools, host: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(host)
    code, out, _err = run_cli(["validate", "--json"])
    envelope = envelope_tools.parse(out)
    envelope_tools.assert_shape(envelope)
    return code, envelope


def _diagnostics(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    return list(envelope["diagnostics"])


def _codes(envelope: dict[str, Any]) -> set[str]:
    return {diagnostic["code"] for diagnostic in _diagnostics(envelope)}


def _messages(envelope: dict[str, Any], severity: str | None = None) -> str:
    return "\n".join(
        diagnostic["message"]
        for diagnostic in _diagnostics(envelope)
        if severity is None or diagnostic["severity"] == severity
    )


def _kernel_control_plane(host: Path, slug: str = "sample-feature") -> dict[str, Any]:
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    snapshot = resolve_snapshot(load_project_config(host), slug)
    return {
        "stage": snapshot.stage,
        "authorized_through": snapshot.authorized_through,
        "milestone_coverage": {
            milestone.id: list(milestone.satisfies)
            for milestone in snapshot.state.milestones
        },
    }


def _validate_control_plane(
    envelope: dict[str, Any], slug: str = "sample-feature"
) -> dict[str, Any]:
    workspaces = envelope["data"].get("workspaces")
    assert isinstance(workspaces, list), (
        "FAIL AC-8: validate data must include per-workspace control_plane facts"
    )
    matches = [
        workspace
        for workspace in workspaces
        if workspace.get("feature") == slug
        or str(workspace.get("workspace", "")).rstrip("/").endswith(f"/{slug}")
    ]
    assert len(matches) == 1, (
        f"FAIL AC-8: expected one validate facts row for {slug!r}, got {workspaces!r}"
    )
    control_plane = matches[0].get("control_plane")
    assert isinstance(control_plane, dict), (
        "FAIL AC-8: validate workspace rows must include control_plane"
    )
    return {
        "stage": control_plane.get("stage"),
        "authorized_through": control_plane.get("authorized_through"),
        "milestone_coverage": control_plane.get("milestone_coverage"),
    }


def _append_ac(spec_path: Path, ac_id: str) -> None:
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8")
        + f"\n### {ac_id}: deliberately unassigned\n\n"
        + "Priority: MUST\nAction: validate.\nExpected: advisory.\n",
        encoding="utf-8",
    )


@pytest.mark.acceptance
class TestValidationAcceptance:
    """AC-level integrated checks over the real migrate/validate surfaces."""

    def test_ac01_ac02_schema_registry_and_version_loading(self, tmp_path):
        from heddle.contracts import schemas
        from heddle.kernel.project_config import KernelError
        from heddle.kernel.state import read_state_file

        assert schemas.STATE_SCHEMA_VERSIONS == (
            "heddle.state/v9",
            "heddle.state/v10",
        )
        assert (
            schemas.CURRENT_STATE_SCHEMA
            == schemas.STATE_SCHEMA_ID
            == "heddle.state/v10"
        )
        host = _clean_host(tmp_path, "schema")
        path = _state_path(host)
        assert read_state_file(path).schema == "heddle.state/v9"
        for schema in (
            "heddle.state/v0",
            "heddle.state/v1",
            "heddle.state/v2",
            "heddle.state/v3",
            "heddle.state/v99",
        ):
            value = read_yaml(path)
            value["schema"] = schema
            incompatible = tmp_path / "incompatible.yaml"
            write_yaml(incompatible, value)
            before = incompatible.read_bytes()
            with pytest.raises(KernelError) as error:
                read_state_file(incompatible)
            assert (
                schema in error.value.message and "fresh workspace" in error.value.hint
            )
            assert incompatible.read_bytes() == before

    def test_ac03_ac04_ac05_retired_migration_preserves_all_authority(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        host = copy_host(tmp_path, TINY, "retired-migration")
        monkeypatch.chdir(host)
        path = _state_path(host)
        value = read_yaml(path)
        value["schema"] = "heddle.state/v1"
        write_yaml(path, value)
        before = {
            str(p.relative_to(host)): p.read_bytes()
            for p in host.rglob("*")
            if p.is_file()
        }
        for args in (
            ["migrate"],
            ["migrate", "--dry-run"],
            ["migrate", "--from-legacy"],
        ):
            code, out, _ = run_cli([*args, "--json"])
            assert code == 2 and envelope_tools.parse(out)["error"]["code"] == "usage"
        code, out, _ = run_cli(["status", "--json"])
        refusal = envelope_tools.parse(out)["error"]
        assert code == 3 and "no active feature" in refusal["message"]
        assert "feature prepare" in refusal["hint"]
        code, out, _ = run_cli(["status", "--feature", "sample-feature", "--json"])
        explicit = envelope_tools.parse(out)["error"]
        assert code == 3 and "unsupported" in explicit["message"]
        assert {
            str(p.relative_to(host)): p.read_bytes()
            for p in host.rglob("*")
            if p.is_file()
        } == before

    def test_ac06_clean_advisory_and_fatal_validate_exits(
        self,
        run_cli,
        envelope_tools,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clean = _clean_host(tmp_path, "clean")
        code, envelope = _run_validate(run_cli, envelope_tools, clean, monkeypatch)
        assert code == 0 and envelope["ok"] is True and envelope["diagnostics"] == []

        advisory = _clean_host(tmp_path, "advisory")
        _append_ac(
            advisory / "docs" / "features" / "example" / "sample-feature.md",
            "AC-3",
        )
        code, envelope = _run_validate(run_cli, envelope_tools, advisory, monkeypatch)
        assert code == 4 and envelope["ok"] is True and "error" not in envelope
        assert "ac-unassigned" in _codes(envelope)

        fatal = _clean_host(tmp_path, "fatal")
        state = read_yaml(_state_path(fatal))
        state["spec"] = "docs/features/example/missing-spec.md"
        write_yaml(_state_path(fatal), state)
        code, envelope = _run_validate(run_cli, envelope_tools, fatal, monkeypatch)
        assert code == 3 and envelope["error"]["code"] == LEDGER_DRIFT

    def test_ac07_ac09_reference_resolution_and_accumulation(
        self,
        run_cli,
        envelope_tools,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from heddle.contracts.result import ERROR_CODES
        from heddle.contracts.schemas import BLOCKING_CONDITIONS

        assert LEDGER_DRIFT in ERROR_CODES
        assert LEDGER_DRIFT not in BLOCKING_CONDITIONS

        host = _clean_host(tmp_path, "references")
        _clone_workspace(host, "dangling-spec-lab", schema="heddle.state/v9")
        dangling_spec_state_path = _state_path(host, "dangling-spec-lab")
        dangling_spec_state = read_yaml(dangling_spec_state_path)
        dangling_spec_state["spec"] = "docs/features/example/missing-spec.md"
        write_yaml(dangling_spec_state_path, dangling_spec_state)
        _append_ac(
            host / "docs" / "features" / "example" / "sample-feature.md",
            "AC-3",
        )
        state_path = _state_path(host)
        state = read_yaml(state_path)
        state["milestones"][0]["satisfies"].append("AC-99")
        state["milestones"][0]["status"] = "done"
        for task in state["milestones"][0]["tasks"]:
            task["status"] = "done"
        state["milestones"][0]["owns"].append("src/example_app/missing_owned.py")
        write_yaml(state_path, state)
        plan_path = _workspace(host) / "plan.md"
        plan_text = plan_path.read_text(encoding="utf-8")
        plan_text = plan_text.replace("### Milestone m2:", "### Milestone mx:")
        plan_text += "\n### Milestone m3: orphan\n\nScope: future prose.\n"
        plan_path.write_text(plan_text, encoding="utf-8")

        code, envelope = _run_validate(run_cli, envelope_tools, host, monkeypatch)
        assert code == 3 and envelope["error"]["code"] == LEDGER_DRIFT
        assert len(_diagnostics(envelope)) >= 5, (
            "FAIL AC-9: validate must accumulate all seeded findings"
        )
        fatal_messages = _messages(envelope, "fatal")
        advisory_messages = _messages(envelope, "advisory")
        for token in (
            "AC-99",
            "missing-spec.md",
            "missing_owned.py",
            "m2",
        ):
            assert token in fatal_messages, (
                f"FAIL AC-7: missing fatal reference {token!r}"
            )
        assert "plan-section-missing" in _codes(envelope)
        assert "plan-section-orphan" in _codes(envelope)
        assert "mx" in advisory_messages, (
            "FAIL AC-7: renamed plan prose with no state milestone must be advisory"
        )
        assert "m3" in advisory_messages, (
            "FAIL AC-7/AC-9: every orphan plan section must be enumerated"
        )
        assert "ac-unassigned" in _codes(envelope)

    def test_ac08_no_parallel_parser_structural_tripwire(
        self,
        run_cli,
        envelope_tools,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        validate_source = (RUNTIME_DIR / "validate.py").read_text(encoding="utf-8")
        assert not (RUNTIME_DIR / "migrate.py").exists()
        assert "resolve_snapshot" in validate_source
        for module_name, source in {
            "validate.py": validate_source,
        }.items():
            tree = ast.parse(source)
            forbidden_yaml_loads = {"safe_load", "load", "full_load", "unsafe_load"}
            direct_yaml_loads = [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and (
                    (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr in forbidden_yaml_loads
                    )
                    or (
                        isinstance(node.func, ast.Name)
                        and node.func.id in forbidden_yaml_loads
                    )
                )
            ]
            assert not direct_yaml_loads, (
                f"FAIL AC-8: {module_name} reparses state YAML at {direct_yaml_loads}"
            )
        knowledge_source = (KERNEL_DIR / "knowledge.py").read_text(encoding="utf-8")
        assert "state.yaml" not in knowledge_source
        assert "WORKSPACE_STATE" not in knowledge_source

        host = _clean_host(tmp_path, "acceptance-snapshot")
        expected = _kernel_control_plane(host)
        code, envelope = _run_validate(run_cli, envelope_tools, host, monkeypatch)
        assert code == 0 and envelope["ok"] is True, (
            "FAIL AC-8: clean validate fixture must pass before comparing kernel facts"
        )
        assert _validate_control_plane(envelope) == expected, (
            "FAIL AC-8: validate facts must equal resolve_snapshot facts"
        )

    def test_ac10_ac11_ac12_guardrails_run_inside_validate(
        self,
        run_cli,
        envelope_tools,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        host = _clean_host(tmp_path, "guardrails")
        _seed_guardrails(host)

        code, envelope = _run_validate(run_cli, envelope_tools, host, monkeypatch)
        assert code == 3 and envelope["error"]["code"] == LEDGER_DRIFT
        codes = _codes(envelope)
        for code_name in (
            "spec-drift",
            "spec-baseline-missing",
            "pattern-integrity",
            "orientation-drift",
        ):
            assert code_name in codes, f"FAIL guardrail diagnostic {code_name} missing"
        fatal_messages = _messages(envelope, "fatal")
        advisory_messages = _messages(envelope, "advisory")
        for token in (
            "drifted",
            "grandfathered",
            "missing-file",
            "no-such-slug",
            "duplicate-slug",
            "missing-feature",
            "missing.md",
            "missing-anchor",
        ):
            assert token in fatal_messages, (
                f"FAIL AC-10/11/12: expected fatal guardrail token {token!r}"
            )
        for token in (
            "missing-baseline",
            "missing-scenario",
            "no-description",
            "unlinked.md",
        ):
            assert token in advisory_messages, (
                f"FAIL AC-10/11/12: expected advisory guardrail token {token!r}"
            )
        assert "existing-anchor" not in fatal_messages, (
            "FAIL AC-12: valid orientation anchors must not be fatal"
        )
        assert "covered.md" not in advisory_messages, (
            "FAIL AC-12: linked covered docs must not be reported as unindexed"
        )
        assert "matching" not in _messages(envelope)
        assert "active" not in _messages(envelope)

        clean_patterns = _clean_host(tmp_path, "acceptance-clean-patterns")
        patterns = clean_patterns / "docs" / "patterns"
        patterns.mkdir(parents=True, exist_ok=True)
        (patterns / "_descriptions.yaml").write_text(
            "clean-target: Clean target pattern\nclean-ref: Clean reference pattern\n",
            encoding="utf-8",
        )
        scenario = "**Concrete future-feature scenario:** a future feature uses it."
        _write_pattern(
            patterns / "clean-target.md",
            name="clean-target",
            body=scenario,
        )
        _write_pattern(
            patterns / "clean-ref.md",
            name="clean-ref",
            body=f"{scenario}\n\nSee [[pattern:clean-target]].",
        )
        code, clean_envelope = _run_validate(
            run_cli, envelope_tools, clean_patterns, monkeypatch
        )
        assert code == 0 and "pattern-integrity" not in _codes(clean_envelope), (
            "FAIL AC-11: clean present patterns library must not emit pattern-integrity"
        )

        clean_orientation = _clean_host(tmp_path, "acceptance-clean-orientation")
        design = clean_orientation / "docs" / "design"
        design.mkdir(parents=True, exist_ok=True)
        (design / "covered.md").write_text(
            "# Covered Doc\n\n## Existing Anchor\n", encoding="utf-8"
        )
        orientation = clean_orientation / "docs" / "workflow" / "orientation.md"
        orientation.parent.mkdir(parents=True, exist_ok=True)
        orientation.write_text(
            "---\n"
            "covers:\n"
            "  - docs/design/*.md\n"
            "---\n\n"
            "# Orientation\n\n"
            "- [covered](../design/covered.md#existing-anchor)\n",
            encoding="utf-8",
        )
        code, clean_envelope = _run_validate(
            run_cli, envelope_tools, clean_orientation, monkeypatch
        )
        assert code == 0 and "orientation-drift" not in _codes(clean_envelope), (
            "FAIL AC-12: clean present orientation docs must not emit orientation-drift"
        )

        no_covers = _clean_host(tmp_path, "acceptance-orientation-no-covers")
        orientation = no_covers / "docs" / "workflow" / "orientation.md"
        orientation.parent.mkdir(parents=True, exist_ok=True)
        orientation.write_text(
            "---\n"
            "title: Orientation without covers\n"
            "---\n\n"
            "# Orientation\n\n"
            "- [missing](../design/missing.md)\n",
            encoding="utf-8",
        )
        code, no_covers_envelope = _run_validate(
            run_cli, envelope_tools, no_covers, monkeypatch
        )
        assert code == 3 and "orientation-drift" in _codes(no_covers_envelope), (
            "FAIL AC-12: no-covers orientation docs must still run broken-link "
            "detection"
        )
        assert "missing.md" in _messages(no_covers_envelope, "fatal"), (
            "FAIL AC-12: no-covers orientation docs must report broken links"
        )
        assert "unlinked" not in _messages(no_covers_envelope, "advisory"), (
            "FAIL AC-12: no-covers orientation docs must not emit unindexed "
            "target advisories"
        )

    def test_ac13_repo_global_sweep_and_manifest_accuracy(
        self,
        run_cli,
        envelope_tools,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        validate_host = _clean_host(tmp_path, "validate-global")
        _clone_workspace(validate_host, "future-lab", schema="heddle.state/v99")
        _clone_workspace(validate_host, "drift-lab", schema="heddle.state/v6")
        _seed_single_pattern_violation(validate_host)
        drift_state_path = _state_path(validate_host, "drift-lab")
        drift_state = read_yaml(drift_state_path)
        drift_state["milestones"][0]["owns"].append("src/drift_lab/missing.py")
        write_yaml(drift_state_path, drift_state)

        code, envelope = _run_validate(
            run_cli, envelope_tools, validate_host, monkeypatch
        )
        assert code == 3 and envelope["error"]["code"] == LEDGER_DRIFT
        assert "future-lab" in _messages(envelope)
        assert "drift-lab" in _messages(envelope)
        assert any(
            d["severity"] == "fatal"
            and "workspace-invalid" in d["code"]
            and "future-lab" in d["message"]
            for d in _diagnostics(envelope)
        ), (
            "FAIL AC-13: repo-global validate must enumerate ahead workspaces "
            "as fatal workspace-invalid diagnostics"
        )
        pattern_findings = [
            d for d in _diagnostics(envelope) if d["code"] == "pattern-integrity"
        ]
        assert (
            len(pattern_findings) == 1
            and "no-such-slug" in (pattern_findings[0]["message"])
        ), (
            "FAIL AC-13: repo-global validate must run guardrails once for "
            f"the whole repo, got {pattern_findings!r}"
        )

        before = {str(p): p.read_bytes() for p in validate_host.rglob("state.yaml")}
        code, out, _err = run_cli(["migrate", "--json"])
        assert code == 2 and envelope_tools.parse(out)["error"]["code"] == "usage"
        assert {
            str(p): p.read_bytes() for p in validate_host.rglob("state.yaml")
        } == before

        code, out, _err = run_cli(["help", "--json"])
        manifest = envelope_tools.parse(out)["data"]
        by_name = {entry["name"]: entry for entry in manifest["commands"]}
        assert "migrate" not in by_name
        assert by_name["validate"]["mutating"] is False
        assert by_name["validate"]["dry_run"] is False
        assert by_name["validate"]["exit_codes"] == [0, 1, 2, 3, 4]
        assert by_name["validate"]["output_schema"] == "heddle.validate/v0"
