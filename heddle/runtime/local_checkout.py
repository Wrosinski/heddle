"""Local-checkout mode: Heddle kept out of a shared repository.

An owner who runs Heddle in a repository whose other contributors do not use
it hides every Heddle file through Git's ignore rules. An ignored
``.heddle.yaml`` is that signal; a tracked one is an ordinary shared adoption.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from heddle.contracts.result import Diagnostic, HeddleResult, Severity
from heddle.io.git import is_ignored
from heddle.kernel.project_config import (
    HEDDLE_CONFIG_FILENAME,
    KernelError,
    ProjectConfig,
    find_project_root,
    load_project_config,
)
from heddle.kernel.resources import resolve_resource

LOCAL_CHECKOUT_ASSET = "local-checkout.md"


def is_local_checkout(root: Path) -> bool:
    """True when Git ignores the ``.heddle.yaml`` at ``root``."""
    return is_ignored(root, HEDDLE_CONFIG_FILENAME)


def local_checkout_guidance(config: ProjectConfig) -> str | None:
    """The local-checkout rules for a session, or ``None`` for a shared
    adoption. Raises ``KernelError`` when the selected resource is unusable."""
    if not is_local_checkout(config.root):
        return None
    path, _source = resolve_resource(LOCAL_CHECKOUT_ASSET, config)
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"{LOCAL_CHECKOUT_ASSET} is unreadable at {path}: {error}",
            hint=f"repair the selected {LOCAL_CHECKOUT_ASSET} as readable UTF-8",
        ) from error
    if not text.strip():
        raise KernelError(
            code="workspace-invalid",
            message=f"{LOCAL_CHECKOUT_ASSET} is blank at {path}",
            hint="restore the local-checkout rules in the selected resource",
        )
    return text


def attach_local_checkout(result: HeddleResult) -> HeddleResult:
    """Prepend a note naming the local-checkout rules when the working
    directory's project is local. A project that cannot be read gets no note;
    its own failure is what the session must act on first."""
    try:
        root = find_project_root(Path.cwd())
        if root is None or not is_local_checkout(root):
            return result
        path, _source = resolve_resource(
            LOCAL_CHECKOUT_ASSET, load_project_config(root)
        )
    except (KernelError, OSError):
        return result
    note = Diagnostic(
        Severity.INFO,
        "local-checkout",
        f"Git ignores {HEDDLE_CONFIG_FILENAME}, so Heddle stays local to this "
        f"checkout: read and follow {path} before changing any file",
    )
    return replace(result, diagnostics=(note, *result.diagnostics))
