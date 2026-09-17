"""Tiered resource resolution.

The precedence by which a prompt or briefing asset is located: explicit
host override (``.heddle/overrides/``) > generated host projection
(``.heddle/generated/``) > packaged default (shipped inside the installed
``heddle`` package, never the host tree). The resolver returns the resolved
source tag so ``doctor`` (AC-15) can name it.

Shared home: the runtime↔gate import boundary forbids runtime modules from
importing the gate package, so the one resolver that both the gate
gate prompt preparation and the runtime ``kickoff``/``doctor`` handlers consume lives
under ``heddle.kernel`` — the only package both may import.
"""

from __future__ import annotations

from pathlib import Path

from heddle.kernel.project_config import KernelError, ProjectConfig

# Host-tier asset directories, relative to the project root.
_OVERRIDE_PARTS = (".heddle", "overrides")
_GENERATED_PARTS = (".heddle", "generated")

# The packaged-default tier ships inside the installed ``heddle`` package
# (``<package>/resources/<asset>``) — a "packaged" resolution never points
# into the host tree.
_PACKAGED_ROOT = Path(__file__).resolve().parent.parent / "resources"


def resolve_host_override(asset: str, config: ProjectConfig) -> tuple[Path, str] | None:
    """Resolve ``asset`` against the host tiers only — override
    (``.heddle/overrides/``) > generated (``.heddle/generated/``) — returning
    ``(path, source)`` or ``None`` when neither host tier carries it. The gate
    Prompt resolution consumes this so a host projection takes precedence
    over the engine's own packaged template."""
    override = config.root.joinpath(*_OVERRIDE_PARTS, asset)
    if override.is_file():
        return override, "override"
    generated = config.root.joinpath(*_GENERATED_PARTS, asset)
    if generated.is_file():
        return generated, "generated"
    return None


def resolve_resource(asset: str, config: ProjectConfig) -> tuple[Path, str]:
    """Resolve ``asset`` by the precedence, returning ``(path, source)``
    where ``source`` ∈ ``{"override", "generated", "packaged"}``.

    Raise ``KernelError`` naming the searched precedence when the asset
    resolves in no tier — ``doctor`` surfaces it as a fatal resource
    diagnostic (AC-14)."""
    host = resolve_host_override(asset, config)
    if host is not None:
        return host
    packaged = _PACKAGED_ROOT / asset
    if packaged.is_file():
        return packaged, "packaged"
    raise KernelError(
        code="workspace-invalid",
        message=(
            f"resource {asset!r} resolves in no tier — searched, in "
            "precedence order: override (.heddle/overrides/), generated "
            "(.heddle/generated/), packaged (the installed heddle package)"
        ),
        hint=(
            f"add {asset!r} under .heddle/overrides/ or .heddle/generated/, "
            "or ship it as a packaged default"
        ),
    )
