"""Portfolio status aggregation and fault isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.runtime.write_path_helpers import (
    copy_host as _copy_host,
)
from tests.runtime.write_path_helpers import (
    read_yaml as _read_yaml,
)
from tests.runtime.write_path_helpers import (
    write_yaml as _write_yaml,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"
TINY = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "tiny"


def _golden_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "host"
) -> Path:
    host = _copy_host(tmp_path, GOLDEN, name=name)
    monkeypatch.chdir(host)
    return host


def _run(run_cli, envelope_tools, argv: list[str]) -> tuple[int, dict]:
    code, out, _err = run_cli(argv)
    envelope = envelope_tools.parse(out)
    envelope_tools.assert_shape(envelope)
    return code, envelope


class TestAC13PortfolioOnGolden:
    def _extended_golden(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> Path:
        """AC-13 precondition: the golden pair (nl-screening tier 3,
        tier1-quickfix tier 1) extended in-test with a `feature start`ed
        tier-2 workspace — the shipped golden alone has no tier 2."""
        host = _golden_host(tmp_path, monkeypatch)
        code, out, _err = run_cli(
            ["feature", "start", "demo", "--area", "analysis", "--tier", "2", "--json"]
        )
        envelope = envelope_tools.parse(out)
        assert code == 0, (
            f"FAIL AC-13 precondition: extending golden with a tier-2 "
            f"feature start must succeed, got {code} {envelope.get('error')!r}"
        )
        return host

    def test_no_active_feature_selection_runs(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        # The golden root holds two workspaces and no pointer: plain `status`
        # is feature-ambiguous, yet --all must answer identically well —
        # the observable proof the selection chain is not run (REQ-21).
        _golden_host(tmp_path, monkeypatch)

        code, envelope = _run(run_cli, envelope_tools, ["status", "--json"])
        assert code == 2 and envelope["error"]["code"] == "feature-ambiguous", (
            "test setup: the bare-status probe must be ambiguous on golden"
        )

        code, envelope = _run(run_cli, envelope_tools, ["status", "--all", "--json"])
        assert code == 0, (
            f"FAIL AC-13/REQ-21: --all must ignore selection and answer on an "
            f"ambiguous, pointerless root, got {code} {envelope.get('error')!r}"
        )
        assert len(envelope["data"]["features"]) == 2


class TestAC14FaultAccumulationAndPropagation:
    def test_kernel_error_becomes_an_error_row_and_the_sweep_continues(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        host = _golden_host(tmp_path, monkeypatch)
        state_path = host / "plans" / "nl-screening" / "state.yaml"
        state = _read_yaml(state_path)
        del state["stage"]  # a missing required key is a KernelError corruption
        _write_yaml(state_path, state)

        code, envelope = _run(run_cli, envelope_tools, ["status", "--all", "--json"])
        assert code == 0, (
            f"FAIL AC-14/REQ-20: typed per-workspace faults ride the payload "
            f"— the sweep exits 0, got {code} {envelope.get('error')!r}"
        )
        features = envelope["data"]["features"]
        slugs = [row["feature"] for row in features]
        assert slugs == ["nl-screening", "tier1-quickfix"], (
            f"FAIL AC-14: the corrupt row stays in slug order and the sweep "
            f"continues, got {slugs!r}"
        )
        corrupt = features[0]
        assert set(corrupt.keys()) == {"feature", "error"}, (
            f"FAIL AC-14: the error row is {{feature, error}}, got "
            f"{sorted(corrupt.keys())}"
        )
        assert corrupt["error"]["code"] == "workspace-invalid", (
            "FAIL AC-14/REQ-20: the error row carries the KernelError's OWN code"
        )
        healthy = features[1]
        assert healthy.get("stage"), (
            "FAIL AC-14: the healthy row must be the full status payload"
        )
        advisories = [
            diagnostic
            for diagnostic in envelope["diagnostics"]
            if diagnostic.get("severity") == "advisory"
            and "nl-screening" in str(diagnostic.get("message", ""))
        ]
        assert len(advisories) == 1, (
            f"FAIL AC-14/REQ-20: exactly one advisory diagnostic accompanies "
            f"the error row, got {envelope['diagnostics']!r}"
        )

    def test_empty_portfolio_is_a_true_answer(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        import shutil

        host = _copy_host(tmp_path, TINY)
        shutil.rmtree(host / "plans" / "sample-feature")
        monkeypatch.chdir(host)

        code, envelope = _run(run_cli, envelope_tools, ["status", "--all", "--json"])
        assert code == 0, (
            f"FAIL AC-14/REQ-21: an empty portfolio is a true answer "
            f"(exit 0), got {code} {envelope.get('error')!r}"
        )
        assert envelope["data"]["features"] == [], (
            "FAIL AC-14: an empty scan returns features: []"
        )

    def test_a_raw_exception_propagates_out_of_the_command_unmasked(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        _golden_host(tmp_path, monkeypatch)

        def boom(*_args: Any, **_kwargs: Any):
            raise RuntimeError("injected non-KernelError fault for AC-14")

        from heddle.kernel.project_config import load_project_config_from_cwd
        from heddle.runtime import status

        monkeypatch.setattr(status, "resolve_snapshot", boom)
        with pytest.raises(RuntimeError, match="injected non-KernelError"):
            status._portfolio_payload(load_project_config_from_cwd())
        # The row collector catches only KernelError. The application boundary
        # maps unexpected failures once to its typed internal result.
        code, envelope = _run(run_cli, envelope_tools, ["status", "--all", "--json"])
        assert code == 1 and not envelope["ok"]
        assert envelope["error"]["code"] == "internal"

    def test_rows_are_sorted_even_if_the_enumeration_is_not(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        # review (peer review): Assumption A9's defensive layer — REQ-19's
        # slug-ascending ordering holds even if list_feature_workspaces
        # ever stops sorting.
        _golden_host(tmp_path, monkeypatch)

        def unsorted_scan(*_args: Any, **_kwargs: Any) -> tuple[str, ...]:
            return ("tier1-quickfix", "nl-screening")

        monkeypatch.setattr(
            "heddle.runtime.status.list_feature_workspaces", unsorted_scan
        )

        code, envelope = _run(run_cli, envelope_tools, ["status", "--all", "--json"])
        assert code == 0
        slugs = [row["feature"] for row in envelope["data"]["features"]]
        assert slugs == ["nl-screening", "tier1-quickfix"], (
            f"FAIL XS-M1/A9: rows must come back slug-ascending under an "
            f"unsorted enumeration, got {slugs!r}"
        )

    def test_scan_level_kernel_error_maps_to_the_handler_exit(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        # review (peer review): a scan-level typed fault — the enumerator
        # itself failing — stays on the handler-owned code→exit mapping
        # (exit 3, workspace-invalid envelope), never escaping to the
        # dispatcher's generic `internal` net. Raw exception propagation
        # (R-P3-3) is the separate test above.
        from heddle.kernel.project_config import KernelError

        _golden_host(tmp_path, monkeypatch)

        def broken_scan(*_args: Any, **_kwargs: Any):
            raise KernelError(
                code="workspace-invalid",
                message="injected enumeration fault for XS-I1",
                hint="repair the plans directory",
            )

        monkeypatch.setattr(
            "heddle.runtime.status.list_feature_workspaces", broken_scan
        )

        code, envelope = _run(run_cli, envelope_tools, ["status", "--all", "--json"])
        assert code == 3 and envelope["error"]["code"] == "workspace-invalid", (
            f"FAIL XS-I1: a scan-level KernelError exits 3 with its own "
            f"code, got {code} {envelope.get('error')!r}"
        )

    def test_all_combined_with_feature_is_usage(
        self, run_cli, envelope_tools, tmp_path: Path, monkeypatch
    ) -> None:
        _golden_host(tmp_path, monkeypatch)
        code, envelope = _run(
            run_cli,
            envelope_tools,
            ["status", "--all", "--feature", "nl-screening", "--json"],
        )
        assert code == 2, (
            f"FAIL AC-14/REQ-21: --all with --feature is a loud usage refusal "
            f"(exit 2), never a silent precedence choice, got {code} "
            f"{envelope.get('error')!r}"
        )
