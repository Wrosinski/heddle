"""The state-file transaction owner: locked reread, validation and publication."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

import yaml

from heddle.contracts.result import Conflict
from heddle.kernel.project_config import KernelError
from heddle.kernel.review_assignments import validate_retained_transition
from heddle.kernel.state import (
    decode_state_yaml,
    parse_state_document,
    validate_state_transition,
)
from heddle.runtime.clock import utc_now_minutes
from heddle.runtime.locking import state_recording_lock

type StateTransform = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class StateCommit:
    revision: int
    wrote: bool


def check_expect_revision(
    expect_revision: int | None, revision: int
) -> Conflict | None:
    """Check a caller's optional revision against the captured input snapshot."""
    if expect_revision is not None and (
        type(expect_revision) is not int or expect_revision != revision
    ):
        return Conflict(f"expected revision {expect_revision}, found {revision}")
    return None


def read_state_document(state_path: Path) -> dict[str, Any]:
    """Read a validated current document; callers needing a transaction lock it."""
    document, _raw = read_state_source(state_path)
    return document


def read_state_source(state_path: Path) -> tuple[dict[str, Any], bytes]:
    """Return one validated document and the exact bytes decoded to produce it."""
    try:
        raw = state_path.read_bytes()
        document = decode_state_yaml(raw.decode("utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise _invalid(f"state file is unreadable: {error}") from error
    parse_state_document(document, source=state_path)
    return cast(dict[str, Any], document), raw


def commit_state(
    state_path: Path,
    *,
    expect_revision: int,
    transform: StateTransform,
) -> StateCommit:
    """Commit a snapshot-based mutation, or refuse its stale revision."""
    return _commit_state(
        state_path, expect_revision=expect_revision, transform=transform
    )


def _commit_state(
    state_path: Path,
    *,
    expect_revision: int,
    transform: StateTransform,
    before_publish: Callable[[], None] | None = None,
) -> StateCommit:
    # The private hook is exclusively for policy journal-before-state ordering.
    if type(expect_revision) is not int or expect_revision < 0:
        raise Conflict("expected revision must be a nonnegative integer")
    with state_recording_lock(state_path):
        document, _source_raw = read_state_source(state_path)
        _require_mutable(document)
        revision = document["revision"]
        if revision != expect_revision:
            raise Conflict(
                f"{state_path}: expected revision {expect_revision}, found {revision!r}"
            )
        updated = transform(deepcopy(document))
        if updated == document:
            validate_state_transition(document, updated)
            validate_retained_transition(document, updated)
            parse_state_document(updated, source=state_path)
            if before_publish is not None:
                before_publish()
            return StateCommit(revision, False)
        updated["revision"] = revision + 1
        updated["updated"] = utc_now_minutes()
        validate_state_transition(document, updated)
        validate_retained_transition(document, updated)
        parse_state_document(updated, source=state_path)
        text = _serialize(state_path, updated)
        if before_publish is not None:
            before_publish()
        _publish(state_path, text, create=False)
        return StateCommit(revision + 1, True)


def append_state(state_path: Path, *, transform: StateTransform) -> StateCommit:
    """Merge an accepted event into locked-latest state with one revision bump."""
    with state_recording_lock(state_path):
        document = read_state_document(state_path)
        _require_mutable(document)
        updated = transform(deepcopy(document))
        if updated == document:
            validate_state_transition(document, updated)
            validate_retained_transition(document, updated)
            parse_state_document(updated, source=state_path)
            return StateCommit(document["revision"], False)
        updated["revision"] = document["revision"] + 1
        updated["updated"] = utc_now_minutes()
        validate_state_transition(document, updated)
        validate_retained_transition(document, updated)
        parse_state_document(updated, source=state_path)
        _publish(state_path, _serialize(state_path, updated), create=False)
        return StateCommit(updated["revision"], True)


def create_state(state_path: Path, document: dict[str, Any]) -> StateCommit:
    """Create-only publication; an existing or concurrently created file wins."""
    with state_recording_lock(state_path):
        if state_path.exists():
            raise _exists(state_path)
        text = _serialize(state_path, document)
        parse_state_document(document, source=state_path)
        _publish(state_path, text, create=True)
        return StateCommit(document["revision"], True)


def _require_mutable(document: dict[str, Any]) -> None:
    parse_state_document(document, source=Path("state.yaml"))
    if document["completion"] is not None:
        raise accepted_state_error(document["feature"])


def accepted_state_error(feature: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"feature {feature} is accepted; its ledger is immutable",
        hint=f"retry pending effects with heddle feature complete --feature {feature}",
    )


def _serialize(state_path: Path, document: dict[str, Any]) -> str:
    parse_state_document(document, source=state_path)
    try:
        return yaml.safe_dump(
            document,
            sort_keys=False,
            allow_unicode=True,
            width=4096,
            default_flow_style=False,
        )
    except yaml.YAMLError as error:
        raise _invalid(f"state file could not be serialized: {error}") from error


def _publish(state_path: Path, text: str, *, create: bool) -> None:
    try:
        _write_text(state_path, text, create=create)
    except FileExistsError as error:
        if create:
            raise _exists(state_path) from error
        raise _invalid(f"state file is unwritable: {error}") from error
    except OSError as error:
        raise _invalid(f"state file is unwritable: {error}") from error


def replace_file_text(path: Path, text: str, *, create: bool = False) -> None:
    """Publish text atomically; create-only callers refuse existing paths.

    The document's semantic owner validates its contents and maps I/O errors.
    Operational state still goes through create_state/commit_state.
    """
    _write_text(path, text, create=create)


def _write_text(path: Path, text: str, *, create: bool) -> None:
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        if create:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _exists(state_path: Path) -> KernelError:
    return KernelError(
        code="workspace-exists",
        message=f"state file already exists: {state_path}",
        hint="use the existing feature or choose a fresh workspace",
    )


def _invalid(message: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=message,
        hint="check the workspace state and filesystem, then retry the operation",
    )
