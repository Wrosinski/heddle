"""Live admission and the attempt deadline for the Antigravity lane's live nodes.

Every live node of the lane (the identity case's antigravity parameter, the
canary items, the realistic reviews and the runner canary) calls
``admit_live_attempt`` immediately before its first provider call. An attempt
is one invocation: one pytest process that runs its live nodes serially. The
first node takes the invocation's admission and every later node of the same
process reuses it, so a run past the granted attempts refuses before it costs
anything.

Kinds are counted separately, each against its own limit: ``stage`` attempts
(the implement-exit, post-review and close stages, 5) and ``m5`` attempts (the
milestone's runner canary, 3). An invocation admits one kind; a node of the
other kind in the same invocation refuses.

Each node, in order:

- passes the ``agy`` version check; a missing ``agy``, or one that does not
  report the tested version, skips the node, claiming nothing;
- finds ``HEDDLE_W4_LIVE_ROOT`` set to an absolute path, or refuses.

Only then does the invocation's first node take admission:

1. **Root.** It creates the evidence root exclusively. A root that exists
   refuses with no slot claimed: another invocation, or an earlier one, owns
   it. A root's record therefore never grants anything.
2. **Slot.** It claims the lowest free slot file, ``<admissions>/<kind>/<n>``
   for ``n`` from 1 to the kind's limit, with ``O_CREAT | O_EXCL``, so two
   invocations racing for the last slot cannot both win. The file records a
   random invocation ID, the kind, the root, the process ID and the time.
   With every slot taken the node refuses. A slot is never released, whether
   the attempt passes, fails or is interrupted; an empty slot file left by an
   interruption still counts.
3. **Record.** It writes the same facts into ``admission.json`` under the
   root, as evidence only.

Each node then creates its own subdirectory under the root, refusing one that
exists, and receives that directory.

Admission fixes the invocation's one deadline on the ``time.monotonic()``
clock, read by every later node through ``attempt_deadline``: admission plus
3,600 s for a ``stage`` attempt and plus 1,200 s for an ``m5`` attempt. A
recorded time overrun may raise a stage attempt's allowance through
``HEDDLE_W4_LIVE_ATTEMPT_SECONDS``, a whole number of seconds from 3,600 to
5,400; any other value refuses before the root is created. The override never
applies to the ``m5`` kind. There is no token cap: usage is evidence only.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# The slot directory: one child directory per kind, one slot file per claim.
ADMISSIONS = (
    REPO_ROOT / "plans" / "antigravity-reviewer-lane-v1" / "evidence" / "admissions"
)
ROOT_VARIABLE = "HEDDLE_W4_LIVE_ROOT"
OVERRIDE_VARIABLE = "HEDDLE_W4_LIVE_ATTEMPT_SECONDS"
TESTED_VERSION = "1.5.19"
LIMITS = {"stage": 5, "m5": 3}
ATTEMPT_SECONDS = {"stage": 3600, "m5": 1200}
# The override may raise only a stage attempt's allowance, and only this far.
OVERRIDABLE = frozenset({"stage"})
OVERRUN_CEILING = 5400
RECORD = "admission.json"
VERSION_SECONDS = 30


class LiveAdmissionRefused(Exception):
    """The node may not make provider calls, and it has started none."""


@dataclass(frozen=True)
class _Admission:
    """What every later node of the invocation proceeds on."""

    kind: str
    root: Path
    deadline: float


# The admission this pytest process took, if any.
_held: _Admission | None = None


def admit_live_attempt(kind: str, node: str, *, admissions: Path | None = None) -> Path:
    """Admit ``node`` as a live attempt of ``kind`` and return its own new
    directory under the invocation's evidence root.

    Call it immediately before the node's first provider call. It skips (via
    ``pytest.skip``) when ``agy`` is missing or not the tested version, and
    raises ``LiveAdmissionRefused`` on every refusal. ``admissions`` replaces
    the slot directory.
    """
    if kind not in LIMITS:
        raise ValueError(
            f"unknown live attempt kind {kind!r}; use one of {sorted(LIMITS)}"
        )
    if node in {"", ".", ".."} or Path(node).name != node:
        raise ValueError(f"live node name {node!r} must be one path component")
    _check_version()
    root = _live_root()
    admission = _held
    if admission is None:
        admission = _take(kind, root, admissions or ADMISSIONS)
    elif admission.kind != kind:
        raise LiveAdmissionRefused(
            "live admission refused: this invocation was admitted as "
            f"{admission.kind!r}, so the {kind!r} node {node!r} needs an invocation "
            "of its own; no slot was claimed"
        )
    directory = admission.root / node
    try:
        os.mkdir(directory)
    except FileExistsError:
        raise LiveAdmissionRefused(
            f"live admission refused: the node directory {directory} already exists "
            "under this invocation's root"
        ) from None
    return directory


def attempt_deadline() -> float:
    """The invocation's deadline on the ``time.monotonic()`` clock, fixed at its
    admission. Raises ``RuntimeError`` when this process holds no admission."""
    admission = _held
    if admission is None:
        raise RuntimeError(
            "attempt_deadline() needs this process's live admission: no live node "
            "has been admitted yet; call admit_live_attempt first"
        )
    return admission.deadline


def _attempt_seconds(kind: str, environ: Mapping[str, str]) -> int:
    """The attempt's allowance in seconds: the kind's own, or for a stage
    attempt the override when set. An override outside 3,600-5,400 refuses."""
    floor = ATTEMPT_SECONDS[kind]
    raw = environ.get(OVERRIDE_VARIABLE)
    if kind not in OVERRIDABLE or raw is None:
        return floor
    text = raw.strip()
    if not (text.isascii() and text.isdigit()) or not (
        floor <= int(text) <= OVERRUN_CEILING
    ):
        raise LiveAdmissionRefused(
            f"live admission refused: {OVERRIDE_VARIABLE}={raw!r} must be a whole "
            f"number of seconds from {floor} to {OVERRUN_CEILING} (a recorded time "
            "overrun); nothing was claimed"
        )
    return int(text)


def _version_matches(output: str) -> bool:
    """Whether ``agy --version`` output names the tested version as a word."""
    return TESTED_VERSION in output.split()


def _check_version() -> None:
    executable = shutil.which("agy")
    if executable is None:
        _skip_host("agy is not on PATH")
    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=VERSION_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _skip_host(f"agy --version failed ({error})")
    reported = completed.stdout.strip()
    if completed.returncode != 0 or not _version_matches(reported):
        _skip_host(
            f"agy --version reported {reported[:200]!r} with exit status "
            f"{completed.returncode}"
        )


def _skip_host(reason: str) -> NoReturn:
    """Skip the node on its host prerequisite; the node claims nothing."""
    why = f"{reason}; this node claims nothing and its proof stays pending"
    pytest.skip(f"live lane requires explicit opt-in and agy {TESTED_VERSION}: {why}")


def _live_root() -> Path:
    raw = os.environ.get(ROOT_VARIABLE)
    if not raw:
        raise LiveAdmissionRefused(
            f"live admission refused: {ROOT_VARIABLE} is not set; set it to a new "
            "absolute path under the feature's ignored records for this attempt. "
            "No slot was claimed"
        )
    root = Path(raw)
    if not root.is_absolute():
        raise LiveAdmissionRefused(
            f"live admission refused: {ROOT_VARIABLE}={raw!r} is not an absolute "
            "path. No slot was claimed"
        )
    return root


def _take(kind: str, root: Path, admissions: Path) -> _Admission:
    """Root, slot, record: the invocation's admission, held from the slot on."""
    global _held
    seconds = _attempt_seconds(kind, os.environ)
    root.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(root)
    except FileExistsError:
        raise LiveAdmissionRefused(
            f"live admission refused: the evidence root {root} already exists; "
            f"another invocation, or an earlier one, owns it. Set {ROOT_VARIABLE} to "
            "a new path. No slot was claimed"
        ) from None
    directory = admissions / kind
    directory.mkdir(parents=True, exist_ok=True)
    claimed = _claim(directory, LIMITS[kind])
    if claimed is None:
        raise LiveAdmissionRefused(
            f"live admission refused: all {LIMITS[kind]} {kind!r} attempts are "
            f"claimed under {directory}; the root {root} is spent and no provider "
            "call ran"
        )
    slot, descriptor = claimed
    _held = _Admission(kind, root, time.monotonic() + seconds)
    facts = {
        "invocation": secrets.token_hex(16),
        "kind": kind,
        "slot": slot,
        "root": str(root),
        "pid": os.getpid(),
        "admitted_at": datetime.now(UTC).isoformat(),
        "attempt_seconds": seconds,
    }
    record = json.dumps(facts, ensure_ascii=False, indent=2) + "\n"
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(record)
    with (root / RECORD).open("x", encoding="utf-8") as stream:
        stream.write(record)
    return _held


def _claim(directory: Path, limit: int) -> tuple[int, int] | None:
    """The lowest free slot, created exclusively, with its open descriptor;
    ``None`` when every slot up to ``limit`` exists."""
    for number in range(1, limit + 1):
        try:
            descriptor = os.open(
                directory / str(number), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
            )
        except FileExistsError:
            continue
        return number, descriptor
    return None
