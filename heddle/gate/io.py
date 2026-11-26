from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from heddle.gate.cli import GateArgs
from heddle.gate.types import GateContext
from heddle.io.process import (
    LAUNCH_TOKEN_ENV,
    process_alive,
    process_start_time,
    reap_token,
)
from heddle.kernel.locking import (
    LOCK_IGNORE_BYTES,
    LOCKS_DIRNAME,
    exclusive_descriptor_lock,
)
from heddle.kernel.project_config import KernelError


@dataclass(frozen=True)
class GatePaths:
    output: Path
    log: Path
    summary: Path
    prompt: Path
    raw_out: Path
    last_message: Path
    json_message: Path
    filtered_jsonl: Path
    command_output: Path
    temp_dir: Path
    previous_output: Path | None = None
    previous_summary: Path | None = None
    published_views: tuple[Path, ...] = ()
    # Warnings from the recovery pass this path set's construction ran, for
    # the run to log once its own log starts.
    recovery_warnings: tuple[str, ...] = ()


class SummaryEmitter:
    def __init__(self) -> None:
        self._fd: int | None
        try:
            self._fd = os.dup(sys.stdout.fileno())
        except (AttributeError, OSError, io.UnsupportedOperation):
            self._fd = None

    def emit(self, message: str) -> None:
        if self._fd is None:
            return
        if not self._should_write():
            return
        try:
            os.write(self._fd, (message + "\n").encode())
        except OSError:
            return

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        except OSError:
            return

    def _should_write(self) -> bool:
        if self._fd is None:
            return False
        try:
            return os.tcgetpgrp(self._fd) == os.getpgrp()
        except OSError:
            return True


IMPL_REVIEW_GATES = {"peer-review-sequential"}
STALE_TEMP_DIR_MAX_AGE_SECONDS = 48 * 60 * 60
_REVIEWS_TEMP_IGNORE_ENTRIES = ("*.tmp.*/", ".*.tmp")


def build_output_name(ctx: GateContext, args: GateArgs) -> str:
    return f"{ctx.feature}.{_output_basename(ctx, args)}"


def _output_basename(
    ctx: GateContext,
    args: GateArgs,
    *,
    include_cli: bool = True,
) -> str:
    """Feature-prefix-free artifact basename: ``<gate>[-m<N>][.<cli>]``.
    Native artifacts live inside the feature's own ``reviews/`` directory,
    so the feature prefix would be redundant there — matching the shape the
    fixture corpus pins."""
    if ctx.gate_type.name in IMPL_REVIEW_GATES:
        name = "impl-review"
    else:
        name = ctx.gate_type.name
    if ctx.gate_type.milestone_output_naming and ctx.milestone:
        # Kernel milestone ids already carry the "m"; legacy
        # frontmatter carried the bare number ("2"). Normalize so neither
        # source yields "-mm2".
        token = str(ctx.milestone)
        if not token.startswith("m"):
            token = f"m{token}"
        name = f"{name}-{token}"
    if include_cli and args.cli != "claude":
        name = f"{name}.{args.cli}"
    return name


def build_gate_lock_path(ctx: GateContext, args: GateArgs) -> Path:
    """Locks are shared across features, so they live at the plans *root* rather
    than inside any one workspace, and the feature-prefixed name is what keeps
    them unique.

    The root is the host's configured ``layout.plans``, derived from the
    workspace directory: ``FeatureSnapshot.workspace`` is
    ``"<layout.plans>/<slug>/"``, so its parent is that configured root. Until
    This once joined the literal ``"plans"`` under a comment claiming it held
    "regardless of layout" — on a host that configures any other plans root, the
    locks landed in a directory the projected ``.gitignore`` entry and the
    packaged ``check-no-committed-gate-locks`` hook (which reads
    ``{{layout_plans}}``) both looked for elsewhere.

    Legacy-format features carry no workspace directory and only ever existed
    under Heddle's own default root, so they keep the literal ``plans/``; an
    adopting host has no legacy-format features to resolve."""
    del args
    plans_root = (
        ctx.workspace_dir.parent
        if ctx.workspace_dir is not None
        else ctx.repo_root / "plans"
    )
    # Exclusion is deliberately broader than artifact naming.  One feature and
    # gate type share a lock across scope, milestone, reviewer, model, CLI and
    # slot changes so mutable identity cannot create same-role overlap.
    lock_name = f"{ctx.feature}.{ctx.gate_type.name}"
    return plans_root / LOCKS_DIRNAME / f"{lock_name}.lock"


def reviews_dir(ctx: GateContext) -> Path:
    """The directory a gate run's durable artifacts land in — the single owner
    of the workspace ``reviews/`` layout branch, shared by artifact writers
    and readers through the canonical workspace root. Native workspaces:
    ``<workspace>/reviews/``; legacy: the shared ``plans/`` root."""
    if ctx.workspace_dir is not None:
        return ctx.workspace_dir / "reviews"
    return ctx.repo_root / "plans"


def gate_artifact_location(ctx: GateContext, args: GateArgs) -> tuple[Path, str]:
    """Where a gate run's durable artifacts (.md/.log/.gate-summary.json) live.

    Native workspaces: ``plans/<slug>/reviews/`` with the
    feature-prefix-free basename, so the recorded run-fact ``artifact`` is
    workspace-relative (kernel §6.2) and resolvable by both consumers
    (validate's gate-artifact check and the run cache). Legacy: flat at the
    plans root with the feature-prefixed name, where workspace == plans root
    keeps the same contract true. The directory is single-sourced via
    ``reviews_dir``; only the basename convention differs by layout."""
    if ctx.workspace_dir is not None:
        return reviews_dir(ctx), _output_basename(ctx, args)
    return reviews_dir(ctx), build_output_name(ctx, args)


@contextmanager
def gate_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_lock_ignore(path.parent / ".gitignore")
    with path.open("a+", encoding="utf-8") as handle:
        with exclusive_descriptor_lock(
            handle.fileno(),
            on_wait=lambda: _emit_gate_lock_wait(path),
        ):
            yield


def _emit_gate_lock_wait(path: Path) -> None:
    sys.stderr.write(f"heddle run-gate: waiting for gate lock {path.stem}…\n")


def _ensure_lock_ignore(ignore: Path) -> None:
    """Publish the lock-directory ignore atomically and reject drift."""
    try:
        existing = ignore.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"cannot read gate-lock ignore file {ignore}: {error}",
            hint=f"restore a readable {ignore} containing exactly '*\\n'",
        ) from error
    if existing is not None:
        if existing != LOCK_IGNORE_BYTES:
            raise KernelError(
                code="workspace-invalid",
                message=f"gate-lock ignore file {ignore} must contain exactly '*\\n'",
                hint=f"replace {ignore} with a single '*\\n' ignore rule",
            )
        return

    descriptor: int | None = None
    temp_path: Path | None = None
    try:
        descriptor, raw_temp_path = tempfile.mkstemp(
            prefix=".gitignore.", suffix=".tmp", dir=ignore.parent
        )
        temp_path = Path(raw_temp_path)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(LOCK_IGNORE_BYTES)
        try:
            os.link(temp_path, ignore)
        except FileExistsError:
            try:
                raced = ignore.read_bytes()
            except OSError as error:
                raise KernelError(
                    code="workspace-invalid",
                    message=f"cannot read gate-lock ignore file {ignore}: {error}",
                    hint=f"restore a readable {ignore} containing exactly '*\\n'",
                ) from error
            if raced != LOCK_IGNORE_BYTES:
                raise KernelError(
                    code="workspace-invalid",
                    message=(
                        f"gate-lock ignore file {ignore} must contain exactly '*\\n'"
                    ),
                    hint=f"replace {ignore} with a single '*\\n' ignore rule",
                ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as error:
                raise KernelError(
                    code="workspace-invalid",
                    message=(
                        "cannot clean gate-lock ignore staging file "
                        f"{temp_path}: {error}"
                    ),
                    hint="restore write access to the gate-lock directory",
                ) from error


def build_gate_paths(
    ctx: GateContext, args: GateArgs, *, truncate_log: bool = True
) -> GatePaths:
    artifact_dir, output_name = gate_artifact_location(ctx, args)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for entry in _REVIEWS_TEMP_IGNORE_ENTRIES:
        _ensure_ignore_entry(artifact_dir / ".gitignore", entry)
    # The recovery pass runs first and alone decides which launch directories
    # the age sweep must keep, whatever their age.
    recovery = recover_launches(artifact_dir, grace_s=args.term_grace_seconds)
    cleanup_stale_temp_dirs(
        artifact_dir,
        retained=frozenset(
            PurePosixPath(path).parent.name for path in ctx.indexed_temporary_paths
        )
        | recovery.keep,
    )

    output = artifact_dir / f"{output_name}.md"
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f"{output_name}.tmp.", dir=artifact_dir)
    ).resolve()
    log = output.with_suffix(".log")
    summary = output.with_suffix(".gate-summary.json")
    previous_output = _backup_path(output, ".prev.md")
    previous_summary = _backup_path(summary, ".prev.gate-summary.json")
    prompt = temp_dir / "prompt.md"
    raw_out = temp_dir / "raw.out"
    last_message = temp_dir / "last-message.txt"
    json_message = temp_dir / "json-message.txt"
    filtered_jsonl = temp_dir / "events.jsonl"
    command_output = temp_dir / "command-output.txt"

    if truncate_log:
        log.write_text("")

    return GatePaths(
        output=output,
        log=log,
        summary=summary,
        prompt=prompt,
        raw_out=raw_out,
        last_message=last_message,
        json_message=json_message,
        filtered_jsonl=filtered_jsonl,
        command_output=command_output,
        temp_dir=temp_dir,
        previous_output=previous_output,
        previous_summary=previous_summary,
        recovery_warnings=recovery.warnings,
    )


def append_log(path: Path, message: str) -> None:
    """Append one gate-log entry, redacted before it is written."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(redact_text(message).rstrip() + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    """Publish UTF-8 text through a same-directory staged replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(text)
        os.replace(staged, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        staged.unlink(missing_ok=True)


def _ensure_ignore_entry(path: Path, entry: str) -> None:
    """Append one ignore rule without replacing host-owned rules."""
    with path.open("a+", encoding="utf-8") as handle:
        # Reuse the same advisory-lock primitive at file scope without creating
        # another metadata file: every Heddle writer follows this protocol.
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = handle.read()
            if entry in existing.splitlines():
                return
            handle.seek(0, os.SEEK_END)
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(entry + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cleanup_temp_dir(paths: GatePaths) -> None:
    shutil.rmtree(paths.temp_dir, ignore_errors=True)


def cleanup_previous_artifacts(paths: GatePaths) -> None:
    for candidate in (paths.previous_output, paths.previous_summary):
        if candidate is None:
            continue
        candidate.unlink(missing_ok=True)


def rotate_existing_artifacts(
    paths: GatePaths,
    *,
    include_output: bool,
    include_summary: bool,
) -> None:
    if include_output and paths.previous_output is not None:
        _move_to_backup(paths.output, paths.previous_output)
    if include_summary and paths.previous_summary is not None:
        _move_to_backup(paths.summary, paths.previous_summary)


def publish_review_result(
    paths: GatePaths,
    canonical: bytes,
    view: str,
    *,
    workspace: Path,
    kind: Literal["review", "capture"] = "review",
) -> tuple[GatePaths, tuple[str, ...]]:
    """Publish immutable review/capture JSON; views never own acceptance."""
    output, published_view, warnings = publish_review_artifact(
        paths.output, canonical, view, workspace=workspace, kind=kind
    )
    views = (
        (*paths.published_views, published_view)
        if published_view is not None
        else paths.published_views
    )
    return (
        replace(
            paths,
            output=output,
            previous_output=None,
            published_views=views,
        ),
        warnings,
    )


def publish_review_artifact(
    output: Path,
    canonical: bytes,
    view: str,
    *,
    workspace: Path,
    kind: Literal["review", "capture"] = "review",
) -> tuple[Path, Path | None, tuple[str, ...]]:
    """One create-only publisher for runner and native interpretation artifacts."""
    parent = output.parent
    try:
        relative = parent.absolute().relative_to(workspace.absolute())
    except ValueError as error:
        raise OSError(
            "canonical review destination is outside its workspace"
        ) from error
    if ".." in relative.parts:
        raise OSError("canonical review destination is not a contained path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(workspace, flags)
    try:
        for part in relative.parts:
            nested = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = nested
        digest = hashlib.sha256(canonical).hexdigest()
        stem = output.name.removesuffix(".md")
        name = f"{stem}.{digest}.{kind}.json"
        view_name = f"{stem}.{digest}.md"
        staged = []
        try:
            for destination, payload in (
                (name, canonical),
                (view_name, view.encode("utf-8")),
            ):
                temporary = f".{destination}.{secrets.token_hex(8)}.tmp"
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                staged.append(temporary)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            try:
                os.link(
                    staged[0],
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
                )
                with os.fdopen(descriptor, "rb") as existing:
                    if (
                        not stat.S_ISREG(os.fstat(existing.fileno()).st_mode)
                        or existing.read(len(canonical) + 1) != canonical
                    ):
                        raise OSError(
                            "existing canonical review differs "
                            "from its content identity"
                        ) from None
            warnings: tuple[str, ...] = ()
            os.fsync(directory)
            try:
                os.replace(
                    staged[1], view_name, src_dir_fd=directory, dst_dir_fd=directory
                )
                published_view: Path | None = parent / view_name
            except OSError as error:
                published_view = None
                warnings = (
                    f"canonical review is published; generated view {view_name} "
                    f"needs regeneration: {error}",
                )
            return parent / name, published_view, warnings
        finally:
            for temporary in staged:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
    finally:
        os.close(directory)


def publish_content_addressed_log(
    log: Path, content: bytes, *, workspace: Path
) -> Path:
    """Publish one immutable log beside its runtime-owned rolling alias."""
    if not log.name.endswith(".log"):
        raise OSError("content-addressed log source must end in .log")
    parent = log.parent
    try:
        relative = parent.absolute().relative_to(workspace.absolute())
    except ValueError as error:
        raise OSError("gate log destination is outside its workspace") from error
    if ".." in relative.parts:
        raise OSError("gate log destination is not a contained path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(workspace, flags)
    try:
        for part in relative.parts:
            nested = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = nested
        digest = hashlib.sha256(content).hexdigest()
        stem = log.name.removesuffix(".log")
        name = f"{stem}.{digest}.log"
        temporary = f".{name}.{secrets.token_hex(8)}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
                )
                with os.fdopen(descriptor, "rb") as existing:
                    descriptor = -1
                    if (
                        not stat.S_ISREG(os.fstat(existing.fileno()).st_mode)
                        or existing.read(len(content) + 1) != content
                    ):
                        raise OSError(
                            "existing gate log differs from its content identity"
                        ) from None
            os.fsync(directory)
            return parent / name
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory)


def cleanup_stale_temp_dirs(
    plans_dir: Path,
    *,
    now: float | None = None,
    retained: frozenset[str] = frozenset(),
) -> int:
    cutoff = (time.time() if now is None else now) - STALE_TEMP_DIR_MAX_AGE_SECONDS
    removed = 0
    for candidate in plans_dir.glob("*.tmp.*"):
        if not candidate.is_dir() or candidate.name in retained:
            continue
        try:
            if candidate.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        removed += 1
    return removed


def _backup_path(path: Path, suffix: str) -> Path:
    name = path.name
    if name.endswith(".gate-summary.json"):
        stem = name.removesuffix(".gate-summary.json")
        return path.with_name(f"{stem}{suffix}")
    if name.endswith(".md"):
        stem = name.removesuffix(".md")
        return path.with_name(f"{stem}{suffix}")
    return path.with_name(f"{name}{suffix}")


def _move_to_backup(source: Path, backup: Path) -> None:
    if not source.exists():
        return
    backup.unlink(missing_ok=True)
    source.replace(backup)


def _copy_to_sibling_temp(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    staged = Path(raw_path)
    try:
        shutil.copyfile(source, staged)
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return staged


# --------------------------------------------------------------------------- #
# Redaction: credential-like values never reach a file Heddle keeps.
# --------------------------------------------------------------------------- #
REDACTED = "[redacted]"
# Every credential body is drawn from this alphabet, which holds no quote and
# no backslash, so a replacement never spans either and JSON text stays JSON.
_CREDENTIAL_ALPHABET = (
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)
# A match never starts inside a JSON ``\uXXXX`` escape. Of the prefixes only
# ``A`` is a hex digit, so only the ``A`` shapes carry the guard, after their
# first letter, which keeps every alternative literal-led for a fast scan.
_A_NOT_IN_ESCAPE = (
    r"(?<!\\uA)(?<!\\u[0-9A-Fa-f]A)(?<!\\u[0-9A-Fa-f]{2}A)(?<!\\u[0-9A-Fa-f]{3}A)"
)
_CREDENTIAL_PATTERN = (
    r"gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    rf"|A{_A_NOT_IN_ESCAPE}(?:KIA[0-9A-Z]{{16}}|SIA[0-9A-Z]{{16}}"
    r"|Iza[0-9A-Za-z_-]{35})"
)
_CREDENTIAL_TEXT = re.compile(_CREDENTIAL_PATTERN)
_CREDENTIAL_BYTES = re.compile(_CREDENTIAL_PATTERN.encode("ascii"))
_REDACT_BLOCK_BYTES = 1 << 20
# A run of credential-alphabet bytes longer than this is carried no further
# between blocks: no credential shape is anywhere near this long.
_REDACT_CARRY_BYTES = 4096


def redact_text(text: str) -> str:
    """``text`` with every credential-like value replaced."""
    return _CREDENTIAL_TEXT.sub(REDACTED, text)


def redact_bytes(data: bytes) -> bytes:
    """``data`` with every credential-like value replaced, byte for byte
    elsewhere."""
    return _CREDENTIAL_BYTES.sub(REDACTED.encode("ascii"), data)


def _safe_cut(data: bytes) -> int:
    """Where ``data`` can be split so that no credential, and no escape a
    credential could follow, straddles the cut."""
    run = len(data) - len(data.rstrip(_CREDENTIAL_ALPHABET))
    if run == len(data) or run > _REDACT_CARRY_BYTES:
        return len(data)
    index = len(data) - run - 1
    # Keep a backslash with what follows it: ``\u00A`` + ``KIA...`` must be
    # judged whole.
    return index if data[index] == 0x5C else index + 1


def redact_file(path: Path) -> bool:
    """Replace ``path`` with its redacted copy, streaming in bounded blocks.

    Returns whether anything was replaced; an unchanged file is left as is.
    """
    descriptor, raw_staged = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".redacting", dir=path.parent
    )
    staged = Path(raw_staged)
    changed = False
    try:
        with path.open("rb") as source, os.fdopen(descriptor, "wb") as sink:
            descriptor = -1
            carry = b""
            while block := source.read(_REDACT_BLOCK_BYTES):
                data = carry + block
                cut = _safe_cut(data)
                redacted = redact_bytes(data[:cut])
                changed = changed or redacted != data[:cut]
                sink.write(redacted)
                carry = data[cut:]
            redacted = redact_bytes(carry)
            changed = changed or redacted != carry
            sink.write(redacted)
        if changed:
            os.replace(staged, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        staged.unlink(missing_ok=True)
    return changed


# --------------------------------------------------------------------------- #
# Launch records and the recovery pass
# --------------------------------------------------------------------------- #
LAUNCH_RECORD_NAME = "launch-record.json"


@dataclass(frozen=True)
class LaunchRecord:
    """One physical launch's token, its launcher and its raw stream, written
    into the run temp directory before the launch starts."""

    token: str
    launcher_pid: int
    launcher_start: int | None
    raw_stream: str
    variable: str = LAUNCH_TOKEN_ENV
    reaped: bool = False
    redacted: bool = False


@dataclass(frozen=True)
class LaunchRecovery:
    """What one recovery pass leaves: the launch directories the age sweep
    must keep, and the cleanup warnings it met."""

    keep: frozenset[str]
    warnings: tuple[str, ...]


def write_launch_record(
    temp_dir: Path, *, token: str, raw_stream: Path
) -> LaunchRecord:
    """Record ``token`` for a launch this process is about to start."""
    pid = os.getpid()
    record = LaunchRecord(
        token=token,
        launcher_pid=pid,
        launcher_start=process_start_time(pid),
        raw_stream=raw_stream.name,
    )
    _store_launch_record(temp_dir, record)
    return record


def _store_launch_record(temp_dir: Path, record: LaunchRecord) -> None:
    atomic_write_text(
        temp_dir / LAUNCH_RECORD_NAME,
        json.dumps(
            {
                "variable": record.variable,
                "token": record.token,
                "launcher": {
                    "pid": record.launcher_pid,
                    "start": record.launcher_start,
                },
                "raw_stream": record.raw_stream,
                "reaped": record.reaped,
                "redacted": record.redacted,
            },
            sort_keys=True,
        ),
    )


def _load_launch_record(path: Path) -> LaunchRecord:
    value = json.loads(path.read_text(encoding="utf-8"))
    launcher = value["launcher"]
    raw_stream = value["raw_stream"]
    if (
        not isinstance(value["token"], str)
        or not isinstance(value["variable"], str)
        or type(launcher["pid"]) is not int
        or not (launcher["start"] is None or type(launcher["start"]) is int)
        or not isinstance(raw_stream, str)
        or Path(raw_stream).name != raw_stream
        or not isinstance(value["reaped"], bool)
        or not isinstance(value["redacted"], bool)
    ):
        raise ValueError("unexpected launch record shape")
    return LaunchRecord(
        token=value["token"],
        launcher_pid=launcher["pid"],
        launcher_start=launcher["start"],
        raw_stream=raw_stream,
        variable=value["variable"],
        reaped=value["reaped"],
        redacted=value["redacted"],
    )


def finish_launch(
    temp_dir: Path, record: LaunchRecord, *, grace_s: float
) -> tuple[str, ...]:
    """Reap a finished launch's token, then replace its raw stream with the
    redacted copy. The record goes once both are done; otherwise it stays,
    updated, so a later pass retries and the sweep keeps the directory.
    Cleanup is warning-only: the warnings are returned, never raised."""
    warnings: list[str] = []
    reaped = record.reaped
    if not reaped:
        result = reap_token(record.token, variable=record.variable, grace_s=grace_s)
        warnings.extend(result.warnings)
        reaped = result.verified
    redacted = record.redacted
    if not redacted:
        stream = temp_dir / record.raw_stream
        try:
            if stream.exists():
                redact_file(stream)
            redacted = True
        except OSError as error:
            warnings.append(f"could not redact {stream.name}: {error}")
    path = temp_dir / LAUNCH_RECORD_NAME
    try:
        if reaped and redacted:
            path.unlink(missing_ok=True)
        else:
            _store_launch_record(
                temp_dir, replace(record, reaped=reaped, redacted=redacted)
            )
    except OSError as error:
        warnings.append(f"could not update {path.name}: {error}")
    return tuple(warnings)


def recover_launches(directory: Path, *, grace_s: float) -> LaunchRecovery:
    """Finish every recorded launch in ``directory`` whose launcher is proven
    gone, whatever its directory's age; keep every launch directory whose
    launcher is live or whose cleanup is not yet verified.

    Liveness alone decides, from the recorded process ID and start time: a
    live launch, its token and its stream are never touched.
    """
    keep: set[str] = set()
    warnings: list[str] = []
    for candidate in sorted(directory.glob("*.tmp.*")):
        record_path = candidate / LAUNCH_RECORD_NAME
        if not candidate.is_dir() or not record_path.is_file():
            continue
        try:
            record = _load_launch_record(record_path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            keep.add(candidate.name)
            warnings.append(f"{candidate.name}: unreadable launch record: {error}")
            continue
        if process_alive(record.launcher_pid, record.launcher_start):
            keep.add(candidate.name)
            continue
        warnings.extend(
            f"{candidate.name}: {warning}"
            for warning in finish_launch(candidate, record, grace_s=grace_s)
        )
        if record_path.exists():
            keep.add(candidate.name)
    return LaunchRecovery(keep=frozenset(keep), warnings=tuple(warnings))
