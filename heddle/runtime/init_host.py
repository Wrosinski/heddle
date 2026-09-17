"""Host-adoption planning and the ``heddle init`` runtime surface.

The planner runs before project configuration exists.  It discovers the
nearest Git root without spawning Git and renders one immutable plan: the
three lock-recorded targets, the declared AGENTS.md mirror (`sync.mirror`,
default CLAUDE.md, never lock-recorded), and the lock.  Dry-run serializes
that plan without touching the host filesystem; apply installs the same
desired bytes atomically and commits the lock last.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.kernel.knowledge import read_yaml_mapping_with_duplicates
from heddle.kernel.managed_regions import (
    begin_marker,
    end_marker,
    marker_fault_kind,
    replace_managed_region,
)
from heddle.kernel.project_config import (
    DEFAULT_SYNC_MIRROR,
    HEDDLE_CONFIG_FILENAME,
    KernelError,
    load_project_config,
)
from heddle.runtime.output import emit_envelope
from heddle.runtime.sync import (
    SESSION_ENTRY_ID,
    install_projection,
    render_session_entry,
)

InitClass = Literal["scaffold-once", "managed-region", "runtime-owned", "mirror"]
InitAction = Literal["create", "accept", "integrate", "refuse", "skip"]
InitOutcome = Literal["done", "failed", "not-reached"]

GIT_ROOT_MISSING = "missing-git-root"
CONFIG_UNPARSABLE = "config-unparsable"
INIT_LOCK_MISSING = "missing-init-lock"
INIT_RESOURCE_UNREADABLE = "init-resource-unreadable"

_RESOURCE_ROOT = Path(__file__).resolve().parent.parent / "resources"
_LOCK_SCHEMA = "heddle.lock/v1"
_LOCK_PATH = PurePosixPath(".heddle.lock")
_TARGET_SPECS: tuple[tuple[PurePosixPath, InitClass], ...] = (
    (PurePosixPath(".heddle.yaml"), "scaffold-once"),
    (PurePosixPath("AGENTS.md"), "managed-region"),
    (
        PurePosixPath("docs/workflow/engineering-principles.md"),
        "scaffold-once",
    ),
    (_LOCK_PATH, "runtime-owned"),
)


@dataclass(frozen=True)
class InitTarget:
    path: PurePosixPath
    class_: InitClass
    action: InitAction
    desired_bytes: bytes | None


@dataclass(frozen=True)
class InitPlan:
    root: Path
    targets: tuple[InitTarget, ...]

    @property
    def refused(self) -> bool:
        return any(target.action == "refuse" for target in self.targets)


@dataclass(frozen=True)
class InitResultRow:
    path: PurePosixPath
    class_: InitClass
    action: InitAction
    outcome: InitOutcome | None = None

    def to_payload(self) -> dict[str, str]:
        payload = {
            "path": self.path.as_posix(),
            "class": self.class_,
            "action": self.action,
        }
        if self.outcome is not None:
            payload["outcome"] = self.outcome
        return payload


@dataclass(frozen=True)
class InitLockEntry:
    """One validated target recorded by the runtime-owned adoption lock."""

    path: PurePosixPath
    class_: InitClass
    sha256: str


@dataclass(frozen=True)
class _LockState:
    raw: bytes
    entries: dict[str, dict[str, str]]


def read_init_lock(root: Path) -> tuple[InitLockEntry, ...]:
    """Read the complete validated lock without interpreting recorded hashes."""
    lock_path = root / _LOCK_PATH
    state = _read_lock(lock_path)
    if state is None:
        raise KernelError(
            code="workspace-invalid",
            message=f"{lock_path} is missing",
            hint="run `heddle init` to complete or recover host adoption",
            reason=INIT_LOCK_MISSING,
        )
    return tuple(
        InitLockEntry(
            path=PurePosixPath(entry["path"]),
            class_=cast(InitClass, entry["class"]),
            sha256=entry["sha256"],
        )
        for entry in state.entries.values()
    )


def plan_init(cwd: Path, *, adopt_existing: bool = False) -> InitPlan:
    """Build the immutable host-adoption plan rooted at the nearest Git host."""
    return _plan_init_root(_find_git_root(cwd), adopt_existing=adopt_existing)


def _plan_init_root(root: Path, *, adopt_existing: bool = False) -> InitPlan:
    """Build a plan after the pre-config Git-root boundary has resolved."""

    # Syntax- or structure-invalid YAML is a bootstrap failure rather than an
    # occupied-row preview. A valid foreign document is still host-owned and
    # is handled by the ordinary scaffold-once refusal below.
    config_path = root / HEDDLE_CONFIG_FILENAME
    config_mode = _target_mode(config_path)
    mirror_rel: str | None = DEFAULT_SYNC_MIRROR
    if config_mode is not None and stat.S_ISREG(config_mode):
        try:
            mirror_rel = load_project_config(root).sync_mirror
        except KernelError as error:
            raise KernelError(
                code=error.code,
                message=error.message,
                hint=error.hint,
                details=error.details,
                candidates=error.candidates,
                reason=CONFIG_UNPARSABLE,
            ) from error

    resources = {
        ".heddle.yaml": _read_resource("heddle-yaml.scaffold.yaml"),
        "docs/workflow/engineering-principles.md": _read_resource(
            "engineering-principles.seed.md"
        ),
    }
    managed_body = render_session_entry()
    managed_block = (
        f"{begin_marker(SESSION_ENTRY_ID)}\n"
        f"{managed_body}"
        f"{end_marker(SESSION_ENTRY_ID)}\n"
    ).encode()
    resources["AGENTS.md"] = managed_block

    lock = _read_lock(root / _LOCK_PATH)
    recorded = lock.entries if lock is not None else {}
    planned: list[InitTarget] = []
    for path, class_ in _TARGET_SPECS[:-1]:
        target_path = root / path
        if path.as_posix() in recorded:
            planned.append(_plan_recorded_target(target_path, path, class_))
        elif class_ == "managed-region":
            planned.append(
                _plan_unrecorded_agents(
                    target_path,
                    managed_block,
                    managed_body,
                )
            )
        else:
            planned.append(
                _plan_scaffold_target(
                    target_path,
                    path,
                    class_,
                    resources[path.as_posix()],
                    adopt_existing=adopt_existing,
                )
            )

    lock_bytes: bytes | None = _render_lock(planned, lock)
    if lock is None:
        lock_action: InitAction = "create"
    elif lock.raw == lock_bytes:
        lock_action = "accept"
    else:
        # A valid lock with all recorded targets carries its recording-time
        # entries forward byte-for-byte, so this branch is defensive.
        lock_action = "refuse"
        lock_bytes = None
    if mirror_rel is not None:
        agents_index = next(
            index
            for index, target in enumerate(planned)
            if target.class_ == "managed-region"
        )
        mirror = _plan_mirror(
            root,
            PurePosixPath(mirror_rel),
            planned[agents_index],
            adopted=lock is not None,
        )
        if mirror is not None:
            planned.insert(agents_index + 1, mirror)
    planned.append(
        InitTarget(
            path=_LOCK_PATH,
            class_="runtime-owned",
            action=lock_action,
            desired_bytes=lock_bytes,
        )
    )
    return InitPlan(root=root, targets=tuple(planned))


def run_init(args: list[str], json_mode: bool) -> int:
    """Serve the live planner, write-free preview, and atomic apply command."""
    operation, parse_failure = _parse_args(args)
    if parse_failure is not None:
        return emit_envelope(parse_failure, json_mode, _render_human)
    from heddle.runtime.application import execute

    assert operation is not None
    return emit_envelope(execute(operation), json_mode, _render_human)


def initialize(operation: ops.Init) -> HeddleResult:
    dry_run = operation.dry_run
    root: Path | None = None
    try:
        root = _find_git_root(Path.cwd())
        plan = _plan_init_root(root, adopt_existing=operation.adopt_existing)
    except KernelError as error:
        return _init_failure(error, root)

    rows = tuple(
        InitResultRow(target.path, target.class_, target.action)
        for target in plan.targets
    )
    if dry_run:
        return HeddleResult.success(
            {
                "dry_run": True,
                "targets": [row.to_payload() for row in rows],
            }
        )

    refused = tuple(target for target in plan.targets if target.action == "refuse")
    if refused:
        return _refused_failure(plan, refused)

    try:
        applied = apply_init(plan)
    except KernelError as error:
        return _init_failure(error, plan.root)
    result = HeddleResult.success(
        {"targets": [row.to_payload() for row in applied]},
        next_actions=(
            NextAction(
                action=ops.ManualAction(
                    "${EDITOR:-vi} docs/workflow/engineering-principles.md"
                ),
                reason=(
                    "review the host principles; preserve existing ratification "
                    "and ratify a seed only with owner approval"
                ),
            ),
            NextAction(
                action=ops.CommandAction(ops.Doctor()),
                reason="verify the initialized host after ratifying its principles",
            ),
            NextAction(
                action=ops.ManualAction("heddle feature start <slug> --area <area>"),
                reason="start the first feature after doctor reports a healthy host",
            ),
        ),
    )
    return result


def apply_init(plan: InitPlan) -> tuple[InitResultRow, ...]:
    """Install one immutable plan in footprint order, committing its lock last."""
    if plan.refused:
        raise KernelError(
            code="workspace-invalid",
            message="an init plan containing refused targets cannot be applied",
            hint="run `heddle init --dry-run` and repair every refused target",
        )

    _check_accepted_scaffolds(plan)
    applied: list[InitResultRow] = []
    for target in plan.targets:
        if target.path == _LOCK_PATH:
            _check_accepted_scaffolds(plan)
        if target.action not in {"accept", "skip"}:
            desired = target.desired_bytes
            if desired is None:
                raise KernelError(
                    code="workspace-invalid",
                    message=f"init target has no planned bytes: {target.path}",
                    hint="run `heddle init --dry-run` to recompute the plan",
                )
            try:
                text = desired.decode("utf-8")
            except UnicodeDecodeError as error:
                raise KernelError(
                    code="workspace-invalid",
                    message=f"init target is not valid UTF-8: {target.path}",
                    hint="reinstall Heddle from a complete wheel and retry",
                ) from error
            destination = plan.root / target.path
            _prepare_parent(destination, plan.root)
            try:
                install_projection(
                    destination,
                    text,
                    create=target.action == "create" and target.path != _LOCK_PATH,
                )
            except KernelError as error:
                raise _init_install_error(error) from error
        applied.append(
            InitResultRow(
                target.path,
                target.class_,
                target.action,
                outcome="done",
            )
        )
    return tuple(applied)


def _check_accepted_scaffolds(plan: InitPlan) -> None:
    for target in plan.targets:
        if target.class_ != "scaffold-once" or target.action != "accept":
            continue
        if _target_bytes(plan.root / target.path) != target.desired_bytes:
            raise KernelError(
                code="workspace-invalid",
                message=f"accepted host target changed during init: {target.path}",
                hint="review the changed file and rerun the init preview",
            )


def _prepare_parent(destination: Path, root: Path) -> None:
    """Create projection parents without accepting a non-directory ancestor."""
    relative_parent = destination.parent.relative_to(root)
    current = root
    for part in relative_parent.parts:
        current /= part
        mode = _target_mode(current)
        if mode is None:
            try:
                current.mkdir()
            except FileExistsError:
                mode = _target_mode(current)
                if mode is not None and stat.S_ISDIR(mode):
                    continue
                raise KernelError(
                    code="workspace-invalid",
                    message=f"init parent appeared during install: {current}",
                    hint="run `heddle init --dry-run` to inspect the changed host",
                ) from None
            except OSError as error:
                raise KernelError(
                    code="workspace-invalid",
                    message=f"cannot create init parent {current}: {error}",
                    hint=(
                        "make the host root writable and rerun `heddle init --dry-run`"
                    ),
                ) from error
            continue
        if not stat.S_ISDIR(mode):
            raise KernelError(
                code="workspace-invalid",
                message=f"init parent is not a directory: {current}",
                hint=f"move {current} aside and rerun `heddle init --dry-run`",
            )


def _init_install_error(error: KernelError) -> KernelError:
    """Translate a shared projection failure to init's remediation route."""
    return KernelError(
        code=error.code,
        message=error.message,
        hint=error.hint.replace("heddle sync", "heddle init"),
        details=error.details,
        candidates=error.candidates,
        reason=error.reason,
    )


def _find_git_root(start: Path) -> Path:
    try:
        resolved = start.resolve()
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"cannot resolve the working directory: {error}",
            hint="move to a readable Git working tree and retry",
        ) from error
    for candidate in (resolved, *resolved.parents):
        git_entry = candidate / ".git"
        try:
            mode = git_entry.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise KernelError(
                code="workspace-invalid",
                message=f"cannot inspect Git entry {git_entry}: {error}",
                hint=f"make {git_entry} readable and retry",
            ) from error
        if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
            return candidate
    raise KernelError(
        code="workspace-invalid",
        message="no Git root exists from the working directory upward",
        hint="run `git init` at the intended project root, then retry",
        reason=GIT_ROOT_MISSING,
    )


def _read_resource(name: str) -> bytes:
    path = _RESOURCE_ROOT / name
    try:
        return path.read_bytes()
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"packaged init resource {name!r} is unreadable: {error}",
            hint=(
                "reinstall Heddle from the same selected installation mode "
                "(editable checkout, pinned Git commit, or reviewed wheel), "
                "then retry"
            ),
            reason=INIT_RESOURCE_UNREADABLE,
        ) from error


def _target_bytes(path: Path) -> bytes | None:
    mode = _target_mode(path)
    if mode is None or not stat.S_ISREG(mode):
        return None
    try:
        return path.read_bytes()
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"cannot read init target {path}: {error}",
            hint=f"make {path} readable and retry",
        ) from error


def _target_mode(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"cannot inspect init target {path}: {error}",
            hint=f"make {path} accessible and retry",
        ) from error


def _plan_scaffold_target(
    target_path: Path,
    relative: PurePosixPath,
    class_: InitClass,
    desired: bytes,
    *,
    adopt_existing: bool = False,
) -> InitTarget:
    current = _target_bytes(target_path)
    if current is None:
        action: InitAction = "create" if _target_mode(target_path) is None else "refuse"
        return InitTarget(
            relative,
            class_,
            action,
            desired if action != "refuse" else None,
        )
    accepted = current == desired or adopt_existing
    return InitTarget(
        relative,
        class_,
        "accept" if accepted else "refuse",
        current if accepted else None,
    )


def _plan_recorded_target(
    target_path: Path,
    relative: PurePosixPath,
    class_: InitClass,
) -> InitTarget:
    current = _target_bytes(target_path)
    if current is None:
        return InitTarget(relative, class_, "refuse", None)
    if class_ == "managed-region" and not _has_valid_managed_pair(current):
        return InitTarget(relative, class_, "refuse", None)
    return InitTarget(relative, class_, "skip", current)


def _plan_unrecorded_agents(
    path: Path,
    managed_block: bytes,
    managed_body: str,
) -> InitTarget:
    current = _target_bytes(path)
    relative = PurePosixPath("AGENTS.md")
    if current is None:
        action: InitAction = "create" if _target_mode(path) is None else "refuse"
        return InitTarget(
            relative,
            "managed-region",
            action,
            managed_block if action == "create" else None,
        )
    try:
        text = current.decode("utf-8")
    except UnicodeDecodeError:
        return InitTarget(relative, "managed-region", "refuse", None)
    fault = marker_fault_kind(text, SESSION_ENTRY_ID)
    if fault == "absent":
        separator = b"" if not current or current.endswith(b"\n") else b"\n"
        desired = current + separator + managed_block
    elif fault is None:
        desired = replace_managed_region(
            text,
            SESSION_ENTRY_ID,
            managed_body,
        ).encode()
    else:
        return InitTarget(relative, "managed-region", "refuse", None)
    return InitTarget(relative, "managed-region", "integrate", desired)


def _plan_mirror(
    root: Path,
    relative: PurePosixPath,
    agents: InitTarget,
    *,
    adopted: bool,
) -> InitTarget | None:
    """Plan the declared full-file mirror of the planned AGENTS.md bytes.

    The mirror is never lock-recorded. Absent → create. A symlink resolving
    to AGENTS.md, or a regular file already equal to the planned AGENTS.md
    bytes → accept. On first adoption (no lock) any other regular file is
    host-authored and refused; on an adopted host the mirror is
    runtime-owned and brought back byte-identical (integrate). Any other
    shape is refused. Returns None when AGENTS.md itself was refused, since
    its bytes are unknown."""
    source = agents.desired_bytes
    if source is None:
        return None
    path = root / relative
    mode = _target_mode(path)
    if mode is None:
        return InitTarget(relative, "mirror", "create", source)
    if stat.S_ISLNK(mode):
        try:
            same = os.path.realpath(path) == os.path.realpath(root / agents.path)
        except OSError:
            same = False
        return InitTarget(relative, "mirror", "accept" if same else "refuse", None)
    if not stat.S_ISREG(mode):
        return InitTarget(relative, "mirror", "refuse", None)
    current = _target_bytes(path)
    if current == source:
        return InitTarget(relative, "mirror", "accept", source)
    if adopted:
        return InitTarget(relative, "mirror", "integrate", source)
    return InitTarget(relative, "mirror", "refuse", None)


def _has_valid_managed_pair(raw: bytes) -> bool:
    return _managed_pair_fault(raw) is None


def _managed_pair_fault(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "invalid-utf8"
    return marker_fault_kind(text, SESSION_ENTRY_ID)


def _read_lock(path: Path) -> _LockState | None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"cannot inspect {path}: {error}",
            hint=f"make {path} accessible and retry",
        ) from error
    if not stat.S_ISREG(mode):
        raise KernelError(
            code="workspace-invalid",
            message=f"{path} is not a regular file",
            hint=f"move {path} aside and rerun `heddle init --dry-run`",
        )
    try:
        raw = path.read_bytes()
        document, duplicates = read_yaml_mapping_with_duplicates(path)
    except (OSError, UnicodeDecodeError, KernelError) as error:
        raise KernelError(
            code="workspace-invalid",
            message=f"{path} is not a readable {_LOCK_SCHEMA} document: {error}",
            hint=f"repair or remove {path}, then rerun `heddle init --dry-run`",
        ) from error
    if duplicates:
        raise _invalid_lock(path)
    entries = _validate_lock_document(document, path)
    return _LockState(raw=raw, entries=entries)


def _validate_lock_document(
    document: Any,
    path: Path,
) -> dict[str, dict[str, str]]:
    expected = {relative.as_posix(): class_ for relative, class_ in _TARGET_SPECS[:-1]}
    if not isinstance(document, dict) or document.get("schema") != _LOCK_SCHEMA:
        raise _invalid_lock(path)
    rows = document.get("targets")
    if not isinstance(rows, list):
        raise _invalid_lock(path)
    entries: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "class", "sha256"}:
            raise _invalid_lock(path)
        relative = row["path"]
        class_ = row["class"]
        digest = row["sha256"]
        if (
            not isinstance(relative, str)
            or relative not in expected
            or class_ != expected[relative]
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or relative in entries
        ):
            raise _invalid_lock(path)
        entries[relative] = {
            "path": relative,
            "class": class_,
            "sha256": digest,
        }
    if list(entries) != sorted(expected) or set(entries) != set(expected):
        raise _invalid_lock(path)
    return entries


def _invalid_lock(path: Path) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"{path} does not match the {_LOCK_SCHEMA} contract",
        hint=f"repair or remove {path}, then rerun `heddle init --dry-run`",
    )


def _render_lock(
    targets: list[InitTarget],
    existing: _LockState | None,
) -> bytes:
    targets = [target for target in targets if target.class_ != "mirror"]
    if existing is not None and all(target.action == "skip" for target in targets):
        return existing.raw
    entries: list[dict[str, str]] = []
    for target in sorted(targets, key=lambda row: row.path.as_posix()):
        path = target.path.as_posix()
        if target.action == "skip" and existing is not None:
            entries.append(dict(existing.entries[path]))
            continue
        desired = target.desired_bytes or b""
        entries.append(
            {
                "path": path,
                "class": target.class_,
                "sha256": hashlib.sha256(desired).hexdigest(),
            }
        )
    lines = [f"schema: {_LOCK_SCHEMA}", "targets:"]
    for entry in entries:
        lines.extend(
            (
                f"- path: {entry['path']}",
                f"  class: {entry['class']}",
                f"  sha256: {entry['sha256']}",
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _parse_args(args: list[str]) -> tuple[ops.Init | None, HeddleResult | None]:
    flags = {"--dry-run", "--adopt-existing"}
    if len(args) == len(set(args)) and all(token in flags for token in args):
        return ops.Init(
            dry_run="--dry-run" in args, adopt_existing="--adopt-existing" in args
        ), None
    unexpected = next(
        (token for token in args if token not in flags or args.count(token) > 1),
        "",
    )
    return (
        None,
        HeddleResult.failure(
            HeddleError(
                code="usage",
                message=f"unrecognized init argument {unexpected!r}",
                hint="usage: heddle init [--adopt-existing] [--dry-run] [--json]",
            ),
            exit_code=ExitCode.USAGE,
        ),
    )


def _init_failure(error: KernelError, root: Path | None) -> HeddleResult:
    actions: tuple[NextAction, ...]
    diagnostics: tuple[Diagnostic, ...]
    if error.reason == GIT_ROOT_MISSING:
        actions = (
            NextAction(
                action=ops.ManualAction("git init"),
                reason="initialize the intended host as a Git repository",
            ),
        )
        diagnostics = ()
    elif root is not None and error.reason == CONFIG_UNPARSABLE:
        config_path = shlex.quote(str(root / HEDDLE_CONFIG_FILENAME))
        actions = (
            NextAction(
                action=ops.ManualAction(f"${{EDITOR:-vi}} {config_path}"),
                reason=f"fix the malformed {HEDDLE_CONFIG_FILENAME} configuration",
            ),
        )
        diagnostics = (
            Diagnostic(
                severity=Severity.FATAL,
                code="config-unparsable",
                message=error.message,
                source=HEDDLE_CONFIG_FILENAME,
            ),
        )
    elif error.reason == INIT_RESOURCE_UNREADABLE:
        # The install source is an explicit adopter choice (editable checkout,
        # pinned Git commit, or reviewed wheel). Do not guess one, and do not
        # route to doctor: before init creates the project config, doctor
        # returns at the missing-root boundary without probing resources.
        actions = ()
        diagnostics = ()
    else:
        actions = (
            NextAction(
                action=ops.CommandAction(ops.Init(dry_run=True)),
                reason="repair the reported host state and recompute the plan",
            ),
        )
        diagnostics = ()
    return HeddleResult.failure(
        HeddleError(
            code=error.code,
            message=error.message,
            hint=error.hint,
            details=error.details,
        ),
        exit_code=ExitCode.FATAL,
        diagnostics=diagnostics,
        next_actions=actions,
    )


def _refused_failure(
    plan: InitPlan,
    refused: tuple[InitTarget, ...],
) -> HeddleResult:
    named = ", ".join(target.path.as_posix() for target in refused)
    diagnostics: list[Diagnostic] = []
    for target in refused:
        code = "init-refused-target"
        message = f"{target.path.as_posix()} cannot be adopted safely"
        if target.class_ == "mirror":
            code = "mirror-drift"
            message = (
                f"{target.path.as_posix()} is not byte-identical to AGENTS.md "
                "(declared sync.mirror): copy AGENTS.md over it, replace it "
                "with a symlink to AGENTS.md, or set `sync: {mirror: null}`"
            )
        elif target.path == PurePosixPath("AGENTS.md"):
            current = _target_bytes(plan.root / target.path)
            fault = _managed_pair_fault(current) if current is not None else None
            if fault not in {None, "invalid-utf8"}:
                code = "managed-block-integrity"
                message = (
                    f"{target.path.as_posix()} has a {fault} "
                    f"{SESSION_ENTRY_ID} marker fault"
                )
        diagnostics.append(
            Diagnostic(
                severity=Severity.FATAL,
                code=code,
                message=message,
                source=target.path.as_posix(),
            )
        )
    return HeddleResult.failure(
        HeddleError(
            code="workspace-invalid",
            message=f"init refused occupied or structurally invalid targets: {named}",
            hint=(
                "review every refused row before applying; to preserve existing "
                "host-owned config and principles, preview "
                "`heddle init --adopt-existing --dry-run`; structural faults "
                "and mirror conflicts still require repair"
            ),
        ),
        exit_code=ExitCode.FATAL,
        diagnostics=tuple(diagnostics),
        next_actions=(
            NextAction(
                action=ops.CommandAction(ops.Init(dry_run=True)),
                reason="review or remove every refused target before applying",
            ),
        ),
    )


def _render_human(result: HeddleResult) -> None:
    if result.ok:
        data = result.data or {}
        label = "init dry-run" if data.get("dry_run") else "init"
        print(f"{label}:")
        for row in data.get("targets", ()):
            print(f"  {row['action']}: {row['path']} ({row['class']})")
        return
    error = result.error
    assert error is not None
    print(f"heddle: error[{error.code}]: {error.message}")
    print(f"  hint: {error.hint}")
    for action in result.next_actions:
        print(f"  next: {action.command} — {action.reason}")
