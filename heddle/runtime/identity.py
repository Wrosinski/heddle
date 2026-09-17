"""Runtime package identity observation shared by diagnostic surfaces."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from importlib import metadata
from pathlib import Path

from heddle.contracts.result import Diagnostic, HeddleResult, Severity

# The installed ``heddle`` package directory — the one location that answers
# "which copy of the code is running", identically for a wheel in site-packages
# and for an editable checkout.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# The *distribution* name to look install metadata up under, which is not
# guaranteed to equal the import package name. Named rather than inlined so
# `tests/runtime/test_install_mode_reporting.py` can pin it against
# `pyproject.toml`: a rename there would otherwise make every install report
# `unknown` forever, silently, with the mode tests still green.
_DISTRIBUTION_NAME = "heddle"


def _install_mode() -> str:
    """``editable`` / ``installed`` / ``unknown`` — read from PEP 610 metadata.

    ``direct_url.json`` exists only for an install from a local path or a VCS,
    and carries ``dir_info.editable: true`` when that install is editable; an
    ordinary wheel or index install has no such file. A source tree merely on
    ``sys.path`` has no distribution metadata at all, and that reports
    ``unknown`` rather than a guess — the honest
    answer, since a checkout with no install is neither mode.
    """
    try:
        raw = metadata.distribution(_DISTRIBUTION_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return "unknown"
    except (OSError, UnicodeDecodeError):
        # Unreadable or byte-corrupt metadata is indistinguishable from absent
        # metadata for this question, and must not take diagnostics down — the
        # runtime decode guard requires both.
        return "unknown"
    if raw is None:
        return "installed"
    try:
        direct_url = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "unknown"
    dir_info = direct_url.get("dir_info") if isinstance(direct_url, dict) else None
    if isinstance(dir_info, dict) and dir_info.get("editable") is True:
        return "editable"
    # A non-editable direct URL (`pip install ./heddle`, or a git ref) is still
    # a real install, and the mode question does not distinguish its source.
    return "installed"


def runtime_identity_diagnostic() -> Diagnostic:
    """Name mode, loaded package location and interpreter in one INFO row."""
    mode = _install_mode()
    if mode == "editable":
        message = f"running from an editable checkout; package={_PACKAGE_ROOT}"
    elif mode == "installed":
        message = f"running from an installed package; package={_PACKAGE_ROOT}"
    else:
        message = (
            "install mode unknown (no readable distribution metadata for "
            f"{_DISTRIBUTION_NAME!r}); package={_PACKAGE_ROOT}"
        )
    message += f"; interpreter={sys.executable}"
    return Diagnostic(Severity.INFO, "install-mode", message, source=mode)


def attach_runtime_identity(result: HeddleResult) -> HeddleResult:
    """Prepend one identity row while preserving every result contract field."""
    if any(row.code == "install-mode" for row in result.diagnostics):
        return result
    return replace(
        result,
        diagnostics=(runtime_identity_diagnostic(), *result.diagnostics),
    )
