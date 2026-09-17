"""Heddle runtime contracts.

Re-exports the public contract names; ``heddle.contracts.schemas`` is the
vocabulary module (plain constants) and is consumed as a module.
"""

from heddle.contracts.result import (
    ENVELOPE_SCHEMA_VERSION,
    ERROR_CODES,
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.runtime.contracts import (
    COMMAND_SURFACE,
    MANIFEST_SCHEMA_VERSION,
    PERF_CI_CEILING_S,
    PERF_WARM_TARGET_MS,
    ArgSpec,
    CommandContract,
    FlagSpec,
    build_manifest,
)

__all__ = [
    "COMMAND_SURFACE",
    "ENVELOPE_SCHEMA_VERSION",
    "ERROR_CODES",
    "MANIFEST_SCHEMA_VERSION",
    "PERF_CI_CEILING_S",
    "PERF_WARM_TARGET_MS",
    "ArgSpec",
    "CommandContract",
    "Diagnostic",
    "ExitCode",
    "FlagSpec",
    "HeddleError",
    "HeddleResult",
    "NextAction",
    "Severity",
    "build_manifest",
]
