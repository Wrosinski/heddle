"""Local-checkout mode: Heddle kept out of a shared repository.

An owner who runs Heddle in a repository whose other contributors do not use
it hides every Heddle file through Git's ignore rules. An ignored
``.heddle.yaml`` is that signal; a tracked one is an ordinary shared adoption.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from heddle.contracts.operations import CommandAction, Init, ManualAction
from heddle.contracts.result import Diagnostic, HeddleResult, NextAction, Severity
from heddle.io.git import GitError, is_ignored, is_linked_worktree
from heddle.kernel.project_config import (
    HEDDLE_CONFIG_FILENAME,
    KernelError,
    ProjectConfig,
    find_project_root,
    is_git_boundary,
    load_project_config,
)
from heddle.kernel.resources import read_resource_text, resolve_resource

LOCAL_CHECKOUT_ASSET = "local-checkout.md"


def is_local_checkout(root: Path) -> bool:
    """True when Git ignores the ``.heddle.yaml`` at ``root``; False outside
    any Git checkout. Raises ``KernelError`` when Git cannot answer."""
    if not any(is_git_boundary(path) for path in (root, *root.parents)):
        return False
    try:
        return is_ignored(root, HEDDLE_CONFIG_FILENAME)
    except GitError as error:
        raise _git_failure(root, error) from error


def local_checkout_guidance(config: ProjectConfig) -> str | None:
    """The local-checkout rules for a session, or ``None`` for a shared
    adoption. Raises ``KernelError`` when the mode or the rules are unknown."""
    if not is_local_checkout(config.root):
        return None
    _path, _source, text = read_resource_text(
        LOCAL_CHECKOUT_ASSET, config, contents="the local-checkout rules"
    )
    return text


def attach_local_checkout(result: HeddleResult) -> HeddleResult:
    """Prepend a note naming the local-checkout rules when the working
    directory's project is local, or an advisory when that is unknown. A
    project that cannot be read gets no note; its own failure is what the
    session must act on first."""
    try:
        root = find_project_root(Path.cwd())
        config = None if root is None else load_project_config(root)
    except (KernelError, OSError):
        return result
    if config is None:
        return result
    try:
        if not is_local_checkout(config.root):
            return result
        path, _source = resolve_resource(LOCAL_CHECKOUT_ASSET, config)
    except KernelError as error:
        note = Diagnostic(
            Severity.ADVISORY, "local-checkout", f"{error.message}; {error.hint}"
        )
    else:
        note = Diagnostic(
            Severity.INFO,
            "local-checkout",
            f"Git ignores {HEDDLE_CONFIG_FILENAME}, so Heddle stays local to "
            f"this checkout: read and follow {path} before changing any file",
        )
    return replace(result, diagnostics=(note, *result.diagnostics))


def missing_project_first_move(git_root: Path) -> NextAction | None:
    """The first move for a checkout without ``.heddle.yaml`` whose clone
    keeps Heddle local, or ``None`` for a shared clone: a linked worktree
    copies the clone's adoption, any other checkout adopts without the shared
    session entry."""
    try:
        if not is_local_checkout(git_root):
            return None
        linked = is_linked_worktree(git_root)
    except GitError as error:
        failure = _git_failure(git_root, error)
        return NextAction(ManualAction(failure.hint), reason=failure.message)
    except KernelError as error:
        return NextAction(ManualAction(error.hint), reason=error.message)
    if linked:
        return NextAction(
            action=ManualAction(
                "Copy this clone's hidden Heddle files into this checkout as "
                "regular files from the checkout that has them (git worktree "
                "list names the checkouts): .heddle.yaml, .heddle.lock, every "
                "target the lock lists, and any .heddle/overrides/ or "
                ".heddle/generated/; then run heddle doctor here"
            ),
            reason=(
                "Git ignores .heddle.yaml here, so this clone keeps Heddle "
                "local and its worktrees copy one adoption"
            ),
        )
    return NextAction(
        action=CommandAction(Init(dry_run=True, session_entry=False)),
        reason=(
            "Git ignores .heddle.yaml here, so adopt this clone without the "
            "shared AGENTS.md session entry"
        ),
    )


def _git_failure(root: Path, error: GitError) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=(
            "cannot tell whether this checkout keeps Heddle local, from "
            f"{root}: {error}"
        ),
        hint="repair Git access to this checkout and retry",
    )
