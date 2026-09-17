"""
adoption current boundary P1-4 (owner ruling G, 2026-07-24) — `heddle doctor` reports
install mode.

The adopter's question is "am I running the installed package or my working
copy?", and before this the answer was unavailable from any Heddle surface.
Ruling G bounds the answer to the mode plus the resolved package location: no
ref, no dirty state, no bundle version, and no `heddle --version` while the
version is the constant "0.0.1" for every build ever made.

Detection is PEP 610 `direct_url.json`, which pip writes only for installs from
a local path or a VCS and stamps `dir_info.editable: true` for editable ones.
The metadata is monkeypatched here rather than exercised through real installs:
the suite always runs from one editable install, so a test that read the true
metadata could only ever assert one of the three modes. The built-wheel path is
covered where it belongs — `test_packaged_prompt_floor.py` installs a real wheel
into a clean venv.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from heddle.runtime.identity import (
    _DISTRIBUTION_NAME,
    _PACKAGE_ROOT,
    _install_mode,
    runtime_identity_diagnostic,
)


class _FakeDistribution:
    """Only the one method `_install_mode` calls. `read_text` returning None is
    importlib.metadata's own "that file is not in this distribution" answer,
    which is what an ordinary wheel install looks like."""

    def __init__(self, payload: str | None, *, raises: Exception | None = None) -> None:
        self._payload = payload
        self._raises = raises

    def read_text(self, filename: str) -> str | None:
        assert filename == "direct_url.json", (
            f"install-mode detection must read PEP 610 metadata, not {filename!r}"
        )
        if self._raises is not None:
            raise self._raises
        return self._payload


def _distribution(monkeypatch, dist: object | Exception) -> None:
    def _fake(name: str) -> object:
        assert name == _DISTRIBUTION_NAME, (
            f"the mode question is about {_DISTRIBUTION_NAME!r}, not {name!r}"
        )
        if isinstance(dist, Exception):
            raise dist
        return dist

    monkeypatch.setattr(metadata, "distribution", _fake)


class TestModeDetection:
    def test_editable_direct_url_reports_editable(self, monkeypatch) -> None:
        payload = json.dumps(
            {
                "url": "file:///home/dev/sample-checkout",
                "dir_info": {"editable": True},
            }
        )
        _distribution(monkeypatch, _FakeDistribution(payload))
        assert _install_mode() == "editable"

    def test_absent_direct_url_reports_installed(self, monkeypatch) -> None:
        """A wheel from an index carries no `direct_url.json` at all."""
        _distribution(monkeypatch, _FakeDistribution(None))
        assert _install_mode() == "installed"

    def test_non_editable_direct_url_still_reports_installed(self, monkeypatch) -> None:
        """`pip install ./heddle` or a git ref writes `direct_url.json` without
        the editable flag. It is a real install, and ruling G's two-mode
        vocabulary deliberately does not split it out by source."""
        payload = json.dumps(
            {"url": "file:///tmp/heddle", "dir_info": {"editable": False}}
        )
        _distribution(monkeypatch, _FakeDistribution(payload))
        assert _install_mode() == "installed"

    def test_missing_distribution_reports_unknown(self, monkeypatch) -> None:
        """A source tree merely on `sys.path` is neither mode; report `unknown`
        rather than guess (owner ruling, 2026-07-24)."""
        _distribution(monkeypatch, metadata.PackageNotFoundError("heddle"))
        assert _install_mode() == "unknown"

    @pytest.mark.parametrize(
        "payload",
        [
            "{not json",
            json.dumps(["a", "list", "is", "not", "a", "mapping"]),
            json.dumps({"url": "file:///x", "dir_info": "not-a-mapping"}),
            json.dumps({"url": "file:///x"}),
        ],
    )
    def test_unreadable_or_unexpected_payloads_never_raise(
        self, monkeypatch, payload: str
    ) -> None:
        """Malformed metadata must degrade, not take `doctor` down — `doctor`'s
        whole job is to run when the installation is broken."""
        _distribution(monkeypatch, _FakeDistribution(payload))
        assert _install_mode() in {"installed", "unknown"}

    @pytest.mark.parametrize(
        "error",
        [
            OSError("permission denied"),
            UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte"),
        ],
        ids=["unreadable", "byte-corrupt"],
    )
    def test_unreadable_metadata_file_reports_unknown(
        self, monkeypatch, error: Exception
    ) -> None:
        """
        Both arms are required by the runtime decode guard (A6 / robustness
                review): a `read_text` on a control-plane file must catch
                `UnicodeDecodeError` alongside `OSError`, and `doctor`'s whole job is to
                keep running when the installation is broken.
        """
        _distribution(monkeypatch, _FakeDistribution(None, raises=error))
        assert _install_mode() == "unknown"


class TestDiagnostic:
    @pytest.mark.parametrize(
        ("mode", "expected_phrase"),
        [
            ("editable", "editable checkout"),
            ("installed", "installed package"),
            ("unknown", "install mode unknown"),
        ],
    )
    def test_every_mode_names_itself_and_the_package_location(
        self, monkeypatch, mode: str, expected_phrase: str
    ) -> None:
        monkeypatch.setattr("heddle.runtime.identity._install_mode", lambda: mode)
        diagnostic = runtime_identity_diagnostic()
        assert diagnostic.code == "install-mode"
        assert diagnostic.source == mode, (
            "the mode must be machine-readable in `source`, not only prose"
        )
        assert expected_phrase in diagnostic.message
        assert str(_PACKAGE_ROOT) in diagnostic.message, (
            "ruling G requires the resolved package location in every mode — "
            f"got {diagnostic.message!r}"
        )
        assert f"interpreter={sys.executable}" in diagnostic.message

    def test_no_mode_is_a_fault(self, monkeypatch) -> None:
        """An editable checkout is the sanctioned development mode and
        `unknown` means unreadable metadata, not breakage — so install mode
        must never move doctor's exit code off the severity it would otherwise
        report."""
        for mode in ("editable", "installed", "unknown"):
            monkeypatch.setattr(
                "heddle.runtime.identity._install_mode", lambda mode=mode: mode
            )
            assert runtime_identity_diagnostic().severity.value == "info"

    def test_package_root_is_the_heddle_package_itself(self) -> None:
        """Not the repo root, and not `runtime/` — the directory whose contents
        are the code actually executing."""
        assert _PACKAGE_ROOT.name == "heddle"
        assert (_PACKAGE_ROOT / "runtime" / "doctor.py").is_file()


class TestDoctorSurface:
    def test_doctor_emits_the_install_category(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ) -> None:
        host = tmp_path / "host"
        (host / "plans" / "sample").mkdir(parents=True)
        (host / ".heddle.yaml").write_text("layout: {}\n", encoding="utf-8")
        (host / "plans" / "sample" / "state.yaml").write_text(
            "not: a valid state\n", encoding="utf-8"
        )
        monkeypatch.chdir(host)

        code, out, _err = run_cli(["doctor", "--json"])
        envelope = envelope_tools.parse(out)
        rows = [d for d in envelope["diagnostics"] if d["code"] == "install-mode"]
        assert len(rows) == 1, f"exactly one install-mode row expected, got {rows!r}"
        assert rows[0]["source"] in {"editable", "installed", "unknown"}
        assert f"interpreter={sys.executable}" in rows[0]["message"]
        assert code in (0, 3, 4), f"FAIL: doctor exit {code} outside {{0,3,4}}"

    def test_install_mode_survives_the_no_project_failure(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ) -> None:
        """No-Heddle-project-here is the error an adopter hits *before* `init`,
        and it is exactly when knowing which copy of the code is running
        matters. `doctor` returns early from that branch, so the diagnostic has
        to be gathered before the config load to reach it."""
        outside = tmp_path / "not-a-project"
        outside.mkdir()
        monkeypatch.chdir(outside)

        code, out, _err = run_cli(["doctor", "--json"])
        envelope = envelope_tools.parse(out)
        assert code == 3, f"missing project root stays fatal, got {code}"
        codes = [str(d["code"]) for d in envelope["diagnostics"]]
        assert codes == ["install-mode"], (
            "the pre-config diagnostic must be carried into the "
            f"missing-project-root envelope — got {codes!r}"
        )


class TestOrientSurface:
    def test_normal_and_pending_intake_results_carry_one_identity(
        self, tmp_path, monkeypatch
    ) -> None:
        from heddle.contracts import operations as ops
        from heddle.runtime.application import execute
        from tests.tiering_helpers import blank_host, prepare_input
        from tests.tiering_review_helpers import V7_FEATURE, current_host

        current_host(tmp_path, monkeypatch)
        normal = execute(ops.Orient(feature=V7_FEATURE))
        assert normal.ok, normal.to_envelope()
        assert [row.code for row in normal.diagnostics].count("install-mode") == 1

        pending_root = tmp_path / "pending"
        pending_root.mkdir()
        host = blank_host(pending_root, monkeypatch)
        (host / "brief.md").write_text("# Research\nOne behavior.\n")
        prepared = execute(
            ops.FeaturePrepare("pending-demo", "runtime", prepare_input())
        )
        assert prepared.ok, prepared.to_envelope()
        pending = execute(ops.Orient(feature="pending-demo"))
        assert pending.ok, pending.to_envelope()
        assert [row.code for row in pending.diagnostics].count("install-mode") == 1

    @pytest.mark.parametrize(
        "arguments",
        (
            ("orient", "--feature", "sample-feature", "--json"),
            ("run-gate", "spec-review", "--feature", "sample-feature", "--json"),
        ),
    )
    def test_incompatible_state_keeps_one_identity_and_the_original_remedy(
        self, tmp_path, monkeypatch, run_cli, envelope_tools, arguments
    ) -> None:
        from tests.runtime.validation_helpers import copy_host, read_yaml, write_yaml

        source = Path("tests/fixtures/workspaces/tiny").resolve()
        host = copy_host(tmp_path, source)
        state_path = host / "plans/sample-feature/state.yaml"
        state = read_yaml(state_path)
        state["schema"] = "heddle.state/v99"
        write_yaml(state_path, state)
        before = state_path.read_bytes()
        monkeypatch.chdir(host)

        code, out, _err = run_cli(list(arguments))
        envelope = envelope_tools.parse(out)
        assert code == 3
        assert envelope["error"]["code"] == "workspace-invalid"
        assert "fresh workspace" in envelope["error"]["hint"]
        identity = [
            row for row in envelope["diagnostics"] if row["code"] == "install-mode"
        ]
        assert len(identity) == 1
        assert "package=" in identity[0]["message"]
        assert f"interpreter={sys.executable}" in identity[0]["message"]
        assert state_path.read_bytes() == before


class TestDistributionNameIsPinned:
    """The hole every other test in this file leaves open. Mode detection looks
    the distribution up by name, and each test above monkeypatches that lookup —
    so renaming the project in `pyproject.toml` would make every real install
    report `unknown` forever while this file stayed green. These two close it
    from both ends: statically against the declared name, and once against the
    live metadata."""

    def test_lookup_name_matches_the_declared_project_name(self) -> None:
        pyproject = _PACKAGE_ROOT.parent / "pyproject.toml"
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "name"
        ]

        # PEP 503 normalization: the installed distribution name is normalized
        # from the declared one, and `metadata.distribution` matches either.
        def normalize(name: str) -> str:
            return re.sub(r"[-_.]+", "-", name).lower()

        assert normalize(_DISTRIBUTION_NAME) == normalize(declared), (
            f"doctor looks install metadata up as {_DISTRIBUTION_NAME!r} but "
            f"pyproject.toml declares {declared!r} — mode would report "
            "`unknown` for every install"
        )

    def test_this_environments_real_install_is_detected(self) -> None:
        """The one test here that reads live metadata. It cannot assert *which*
        mode without pinning how the suite was installed, so what it pins is the
        implication: metadata present means a real mode, metadata absent means
        `unknown`. Both branches assert, so no environment opts itself out.

        It deliberately does not carry the rename guard — the lookup it probes
        with is the same one under test, so a rename would make both sides move
        together and agree. That is why the static test above exists.
        """
        try:
            metadata.distribution(_DISTRIBUTION_NAME)
            installed = True
        except metadata.PackageNotFoundError:
            installed = False

        expected = {"editable", "installed"} if installed else {"unknown"}
        assert _install_mode() in expected, (
            f"distribution {'present' if installed else 'absent'} must resolve "
            f"to one of {sorted(expected)} — an installed distribution reporting "
            "`unknown` means PEP 610 detection broke, not that metadata is absent"
        )


def test_no_version_surface_was_added() -> None:
    """Ruling G excludes `heddle --version`: the version is the constant
    "0.0.1" for every build, so it would report nothing useful until real
    versioning exists. Pinned so the exclusion is a decision, not an
    oversight — delete this with the ruling, not around it."""
    from heddle.runtime.contracts import COMMAND_SURFACE

    # COMMAND_SURFACE is a tuple of CommandContract, so a bare `in` check
    # against it is vacuously true for every string — compare the names.
    names = {contract.name for contract in COMMAND_SURFACE}
    assert "doctor" in names, (
        f"self-check: the surface must be keyed as expected, got {sorted(names)}"
    )
    assert "version" not in names, (
        "a version command appeared without revisiting ruling G"
    )
    root = Path(_PACKAGE_ROOT)
    cli_text = (root / "cli.py").read_text(encoding="utf-8")
    assert "--version" not in cli_text, (
        "a --version flag appeared without revisiting ruling G"
    )
