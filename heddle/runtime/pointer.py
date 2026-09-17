"""feature switch handler + the sanctioned pointer write.

The worktree-local active-feature pointer is the session-state
exemption: ``write_active_feature_pointer`` is THE one write sink in
``heddle/runtime/`` (the structural write-sink rails allowlist
exactly this seam). ``feature switch`` reads no ``state.yaml`` and takes
no CAS guard, so its exit codes are {0, 1, 2}: every config-load
``KernelError`` maps to ``usage``/exit 2 (its contract has no exit 3),
the underlying cause staying visible in the message.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
)
from heddle.kernel.model import (
    active_feature_pointer_path,
    is_terminal,
    read_active_feature_pointer,
    resolve_active_feature,
    resolve_snapshot,
)
from heddle.kernel.project_config import KernelError, load_project_config_from_cwd
from heddle.runtime.diagnostics import envelope_diagnostics
from heddle.runtime.output import emit_envelope
from heddle.runtime.state_store import accepted_state_error


def write_active_feature_pointer(root: Path, slug: str) -> Path:
    """THE one sanctioned write sink in heddle/runtime/:
    mkdir -p the pointer's parent, then write ``<slug>\\n`` ATOMICALLY —
    a sibling temp file renamed over the pointer (``os.replace``), so a
    failed or concurrent write can never truncate the previously
    recorded pointer. Trivially
    idempotent — the same slug rewrites the same one-line file."""
    pointer_path = active_feature_pointer_path(root)
    if pointer_path is None:
        raise KernelError(
            code="usage",
            message=(
                f"no .git directory or file at {root} — the pointer's only "
                "home is the worktree's private git directory"
            ),
            hint=(
                "run inside a git checkout, or select per invocation with "
                "`--feature <slug>` on status/orient"
            ),
        )
    temp_path = pointer_path.with_name(f"{pointer_path.name}.{os.getpid()}.tmp")
    try:
        pointer_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(f"{slug}\n", encoding="utf-8")
        os.replace(temp_path, pointer_path)
    except OSError as error:
        # Write I/O failures stay on
        # the typed error channel — usage, like the outside-git case (the
        # pointer's home exists but cannot be written).
        raise KernelError(
            code="usage",
            message=(
                f"cannot write the active-feature pointer at {pointer_path}: {error}"
            ),
            hint=(
                "check that the worktree's git directory is writable, or "
                "select per invocation with `--feature <slug>` on "
                "status/orient"
            ),
        ) from error
    finally:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
    return pointer_path


def run_feature_switch(args: list[str], json_mode: bool) -> int:
    slug, parse_failure = _parse_slug(args)
    if parse_failure is not None:
        return _emit(parse_failure, json_mode)

    from heddle.runtime.application import execute

    return _emit(execute(ops.FeatureSwitch(slug)), json_mode)


def feature_switch(operation: ops.FeatureSwitch) -> HeddleResult:
    slug = operation.feature
    # Config diagnostics ride every envelope built after a successful load.
    held_diagnostics: tuple[Diagnostic, ...] = ()
    try:
        config = load_project_config_from_cwd()
        held_diagnostics = envelope_diagnostics(config.diagnostics)
        resolve_active_feature(config, slug)  # slug validation
        if is_terminal(resolve_snapshot(config, slug).state):
            raise accepted_state_error(slug)
        previous = read_active_feature_pointer(config.root)
        written = write_active_feature_pointer(config.root, slug)
    except KernelError as error:
        # The {0, 1, 2} contract: every kernel failure here — malformed
        # config (workspace-invalid for status/orient), unknown slug,
        # outside-git — maps to usage/exit 2, the cause staying visible.
        return HeddleResult.failure(
            HeddleError(code="usage", message=error.message, hint=error.hint),
            exit_code=ExitCode.USAGE,
            diagnostics=held_diagnostics,
        )

    result = HeddleResult.success(
        {
            "feature": slug,
            "pointer": _pointer_display(written, config.root),
            "previous": previous,
        },
        diagnostics=held_diagnostics,
    )
    return result


def _pointer_display(pointer_path: Path, root: Path) -> str:
    """Project-root-relative in the normal `.git`-directory case;
    absolute in the linked-worktree case (spec §Payloads)."""
    try:
        return pointer_path.relative_to(root).as_posix()
    except ValueError:
        return str(pointer_path)


def _parse_slug(args: list[str]) -> tuple[str, HeddleResult | None]:
    positionals = [token for token in args if not token.startswith("-")]
    flags = [token for token in args if token.startswith("-")]
    if flags:
        return "", _usage_failure(
            f"unrecognized argument {flags[0]!r}",
            "usage: heddle feature switch <slug> [--json]",
        )
    if len(positionals) != 1:
        return "", _usage_failure(
            "feature switch takes exactly one <slug> argument",
            "usage: heddle feature switch <slug> [--json]",
        )
    return positionals[0], None


def _usage_failure(message: str, hint: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="usage", message=message, hint=hint),
        exit_code=ExitCode.USAGE,
    )


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is None:
        data = result.data or {}
        lines = [
            f"active feature: {data['feature']} "
            f"(pointer {data['pointer']}, previous {data['previous'] or '(none)'})"
        ]
    else:
        error = result.error
        lines = [
            f"heddle: error[{error.code}]: {error.message}",
            f"  hint: {error.hint}",
        ]
    lines.extend(
        # Human rendering includes retained diagnostics.
        f"note: {diagnostic.code}: {diagnostic.message}"
        for diagnostic in result.diagnostics
    )
    print("\n".join(lines))
