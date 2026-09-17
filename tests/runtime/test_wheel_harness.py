"""Focused tests for the installed-wheel audit harness."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.runtime.wheel_harness import _install_forbidden_source_audit_hook


def _probe(
    site_packages: Path, env: dict[str, str], source: str
) -> subprocess.CompletedProcess[str]:
    clean = os.environ.copy()
    clean.update(env)
    clean["PYTHONPATH"] = str(site_packages)
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=site_packages.parent,
        env=clean,
        text=True,
        capture_output=True,
        check=False,
    )


def test_contained_audit_log_fails_before_hook_registration(tmp_path: Path) -> None:
    site_packages = tmp_path / "site"
    forbidden = tmp_path / "checkout"
    site_packages.mkdir()
    forbidden.mkdir()
    _install_forbidden_source_audit_hook(site_packages)

    log = forbidden / "audit.log"
    result = _probe(
        site_packages,
        {
            "HEDDLE_FORBIDDEN_SOURCE_ROOT": str(forbidden),
            "HEDDLE_FORBIDDEN_SOURCE_LOG": str(log),
        },
        "import heddle_portability_audit",
    )

    assert result.returncode != 0
    assert "source diagnostic log inside the forbidden source root" in result.stderr
    assert "RecursionError" not in result.stderr
    assert not log.exists()


def test_forbidden_read_writes_one_non_reentrant_diagnostic(tmp_path: Path) -> None:
    site_packages = tmp_path / "site"
    forbidden = tmp_path / "checkout"
    site_packages.mkdir()
    forbidden.mkdir()
    source = forbidden / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    log = tmp_path / "audit.log"
    _install_forbidden_source_audit_hook(site_packages)

    result = _probe(
        site_packages,
        {
            "HEDDLE_FORBIDDEN_SOURCE_ROOT": str(forbidden),
            "HEDDLE_FORBIDDEN_SOURCE_LOG": str(log),
        },
        f"import heddle_portability_audit; open({str(source)!r}).read()",
    )

    assert result.returncode != 0
    assert "forbidden checkout-root read" in result.stderr
    assert "RecursionError" not in result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [str(source.resolve())]


def test_network_event_writes_one_non_reentrant_diagnostic(tmp_path: Path) -> None:
    site_packages = tmp_path / "site"
    forbidden = tmp_path / "checkout"
    site_packages.mkdir()
    forbidden.mkdir()
    log = tmp_path / "network.log"
    _install_forbidden_source_audit_hook(site_packages)

    result = _probe(
        site_packages,
        {
            "HEDDLE_FORBIDDEN_SOURCE_ROOT": str(forbidden),
            "HEDDLE_NETWORK_AUDIT_LOG": str(log),
        },
        "import sys, heddle_portability_audit; "
        "sys.audit('socket.connect', object(), ('example.invalid', 443))",
    )

    assert result.returncode != 0
    assert "network access forbidden" in result.stderr
    assert "RecursionError" not in result.stderr
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
