"""heddle.kernel — the read-only typed state resolver.

The one-resolver seam: all derivation lives here; runtime handlers
render one snapshot. Read-only by invariant — the kernel's write-sink ban
ships with an empty allowlist. Import discipline (Feature Spec
§Architecture): stdlib + ``yaml`` +
``heddle.kernel`` + the stdlib-only ``heddle.contracts`` layer; never
``heddle.runtime`` or the gate engine (gate code imports the kernel, not
the reverse).
"""

from heddle.kernel.model import (
    ActiveFeatureResolution,
    FeatureSnapshot,
    GateArtifact,
    active_feature_pointer_path,
    derive_next_actions,
    list_feature_workspaces,
    read_active_feature_pointer,
    resolve_active_feature,
    resolve_snapshot,
)
from heddle.kernel.project_config import (
    CONFIG_UNKNOWN_KEY,
    DEFAULT_LAYOUT,
    HEDDLE_CONFIG_FILENAME,
    ConfigDiagnostic,
    KernelError,
    LayoutConfig,
    ProjectConfig,
    find_project_root,
    load_project_config,
)
from heddle.kernel.state import (
    DecisionFact,
    GateFact,
    GateRun,
    Milestone,
    SessionFact,
    StateFile,
    Task,
    VerificationFact,
    read_state_file,
)

__all__ = [
    "CONFIG_UNKNOWN_KEY",
    "DEFAULT_LAYOUT",
    "HEDDLE_CONFIG_FILENAME",
    "ActiveFeatureResolution",
    "ConfigDiagnostic",
    "DecisionFact",
    "FeatureSnapshot",
    "GateArtifact",
    "GateFact",
    "GateRun",
    "KernelError",
    "LayoutConfig",
    "Milestone",
    "ProjectConfig",
    "SessionFact",
    "StateFile",
    "Task",
    "VerificationFact",
    "active_feature_pointer_path",
    "derive_next_actions",
    "find_project_root",
    "list_feature_workspaces",
    "load_project_config",
    "read_active_feature_pointer",
    "read_state_file",
    "resolve_active_feature",
    "resolve_snapshot",
]
