"""Live admission for the Antigravity lane's live nodes (AC-25 harness, fast).

Drives the real admission path, ``admit_live_attempt`` in
``tests/antigravity_live_helpers.py``, from simulated invocations. Each
invocation is its own Python process, because an admission is held by the
pytest process that took it. A simulated node calls the helper and, only when
admitted, makes its "provider call" through a doubled ``agy`` that records
every process start; ``agy --version`` calls are recorded apart, since the
version check runs before admission.

Seam named by this scaffold (the plan names only the function and its file):

- ``admit_live_attempt(kind, node, *, admissions=None) -> Path`` runs the
  version check, reads ``HEDDLE_W4_LIVE_ROOT``, takes or reuses the
  invocation's admission and returns the node's own new subdirectory under
  the root. ``admissions`` overrides the slot directory, whose children are
  ``<kind>/<n>`` slot files.
- It raises ``LiveAdmissionRefused`` on a refusal and calls ``pytest.skip``
  when the ``agy`` version differs. An unset root refuses or skips.
- ``attempt_deadline() -> float`` is the invocation-wide live deadline on the
  ``time.monotonic()`` clock, fixed when the invocation is admitted and shared
  by every later node: admission plus 3,600 s for ``stage`` attempts, which
  ``HEDDLE_W4_LIVE_ATTEMPT_SECONDS`` may raise only up to 5,400 s (a recorded
  overrun; a larger value refuses admission), and admission plus 1,200 s for
  ``m5`` attempts. It raises when the invocation is not admitted.

Red until the helper exists.

The last section drives, offline, the live canary module's two consumers of
that deadline, with doubles in place of the provider: the guard at a runner
launch's physical launch boundary (``guard_launches``, around
``run_monitored``) and the bounded writes of evidence kept after judging
(``retain_files``, inside the launch's retention window).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = "tests.antigravity_live_helpers"
ROOT_VARIABLE = "HEDDLE_W4_LIVE_ROOT"
OVERRIDE_VARIABLE = "HEDDLE_W4_LIVE_ATTEMPT_SECONDS"
ATTEMPT_SECONDS = {"stage": 3600, "m5": 1200}
OVERRUN_CEILING = 5400
TESTED_VERSION = "1.5.18"
LIMITS = {"stage": 4, "m5": 3}

AGY = r"""
import json, os, sys
from pathlib import Path
home = Path(sys.argv[0]).resolve().parent.parent
name = "versions.jsonl" if sys.argv[1:] == ["--version"] else "launches.jsonl"
with open(home / name, "a") as handle:
    handle.write(json.dumps({"pid": os.getpid(), "argv": sys.argv[1:]}) + "\n")
if name == "versions.jsonl":
    print((home / "version.txt").read_text().strip())
"""

INVOCATION = r"""
import json, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
spec = json.loads(sys.argv[2])
fault = spec.get("fault")
if fault == "after-root":
    real_mkdir = os.mkdir

    def mkdir(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        if os.path.abspath(path) == os.path.abspath(os.environ["HEDDLE_W4_LIVE_ROOT"]):
            os._exit(9)

    os.mkdir = mkdir
elif fault == "after-slot":
    real_open = os.open
    slots = os.path.abspath(spec["admissions"])

    def open_(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        exclusive = flags & os.O_CREAT and flags & os.O_EXCL
        if exclusive and os.path.abspath(path).startswith(slots + os.sep):
            os._exit(9)
        return descriptor

    os.open = open_
if spec.get("go"):
    deadline = time.monotonic() + 20
    while not os.path.exists(spec["go"]) and time.monotonic() < deadline:
        time.sleep(0.002)

import pytest
import tests.antigravity_live_helpers as helpers
from tests.antigravity_live_helpers import LiveAdmissionRefused, admit_live_attempt

attempt_deadline = getattr(helpers, "attempt_deadline", None)


def deadline_now():
    if attempt_deadline is None:
        return {"deadline": None, "deadline_missing": True}
    try:
        return {"deadline": attempt_deadline()}
    except Exception as error:  # the refusal's type is reported, not judged here
        return {"deadline": None, "deadline_error": type(error).__name__}


results = []
before = deadline_now() if spec.get("deadline_first") else None
for kind, node in spec["nodes"]:
    if results and spec.get("pause"):
        time.sleep(spec["pause"])
    started = time.monotonic()
    try:
        path = admit_live_attempt(kind, node, admissions=Path(spec["admissions"]))
    except LiveAdmissionRefused as error:
        results.append({"node": node, "outcome": "refused", "detail": str(error)})
        continue
    except pytest.skip.Exception as error:
        results.append({"node": node, "outcome": "skipped", "detail": str(error)})
        continue
    finished = time.monotonic()
    subprocess.run(["agy", "--print", node], check=True)
    row = {"node": node, "outcome": "admitted", "path": str(path)}
    row.update(started=started, finished=finished, **deadline_now())
    results.append(row)
print(json.dumps({"pid": os.getpid(), "results": results, "before": before}))
"""


def require_helper() -> None:
    assert importlib.util.find_spec(HELPER) is not None, (
        f"FAIL AC-25: {HELPER} (admit_live_attempt) is not implemented"
    )


class Lane:
    """The doubled ``agy``, the slot directory and simulated invocations."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = tmp_path / "agy"
        (self.home / "bin").mkdir(parents=True)
        agy = self.home / "bin" / "agy"
        agy.write_text(f"#!{sys.executable}\n{AGY}", encoding="utf-8")
        agy.chmod(0o755)
        self.version(TESTED_VERSION)
        self.admissions = tmp_path / "admissions"
        self.tmp_path = tmp_path
        self.path = f"{self.home / 'bin'}{os.pathsep}{os.environ['PATH']}"
        monkeypatch.delenv(ROOT_VARIABLE, raising=False)

    def version(self, value: str) -> None:
        (self.home / "version.txt").write_text(value, encoding="utf-8")

    def fresh_root(self, name: str = "live") -> Path:
        root = self.tmp_path / "evidence" / name
        root.parent.mkdir(exist_ok=True)
        assert not root.exists()
        return root

    def seed(self, kind: str, count: int) -> None:
        directory = self.admissions / kind
        directory.mkdir(parents=True, exist_ok=True)
        for number in range(1, count + 1):
            (directory / str(number)).write_text(
                json.dumps({"seeded": True}), encoding="utf-8"
            )

    def slots(self, kind: str) -> list[str]:
        directory = self.admissions / kind
        if not directory.is_dir():
            return []
        return sorted(path.name for path in directory.iterdir())

    def launches(self) -> list[dict]:
        path = self.home / "launches.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def start(
        self,
        nodes: list[tuple[str, str]],
        root: Path | None,
        *,
        fault: str | None = None,
        go: Path | None = None,
        env: dict[str, str] | None = None,
        **options: object,
    ) -> subprocess.Popen:
        environment = {**os.environ, "PATH": self.path, "PYTHONDONTWRITEBYTECODE": "1"}
        environment.pop(ROOT_VARIABLE, None)
        environment.pop(OVERRIDE_VARIABLE, None)
        environment.update(env or {})
        if root is not None:
            environment[ROOT_VARIABLE] = str(root)
        spec = {
            "nodes": nodes,
            "admissions": str(self.admissions),
            "fault": fault,
            "go": str(go) if go else None,
            **options,
        }
        return subprocess.Popen(
            [sys.executable, "-c", INVOCATION, str(REPO_ROOT), json.dumps(spec)],
            cwd=self.tmp_path,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def invoke(self, nodes, root, *, fault=None, **options) -> dict:
        process = self.start(nodes, root, fault=fault, **options)
        out, err = process.communicate(timeout=60)
        if fault is not None:
            assert process.returncode == 9, f"fixture: the fault did not fire: {err}"
            return {"results": []}
        assert process.returncode == 0, f"fixture: invocation crashed: {err[-2000:]}"
        return json.loads(out.splitlines()[-1])


def outcomes(run: dict) -> list[str]:
    return [row["outcome"] for row in run["results"]]


@pytest.fixture
def lane(tmp_path, monkeypatch) -> Lane:
    return Lane(tmp_path, monkeypatch)


def test_ac25_live_admission_each_kind_counts_only_its_own_slots(lane):
    """With ``stage`` and ``m5`` slots seeded, each kind's limit counts only its
    own slots: a full ``m5`` refuses while ``stage`` admits its last slot."""
    require_helper()
    lane.seed("stage", LIMITS["stage"] - 1)
    lane.seed("m5", LIMITS["m5"])
    refused = lane.invoke([("m5", "runner-canary")], lane.fresh_root("m5-full"))
    assert outcomes(refused) == ["refused"], refused
    assert lane.slots("m5") == ["1", "2", "3"]
    admitted = lane.invoke([("stage", "canary")], lane.fresh_root("stage-last"))
    assert outcomes(admitted) == ["admitted"], admitted
    assert lane.slots("stage") == ["1", "2", "3", "4"]
    slot = (lane.admissions / "stage" / "4").read_text()
    assert str(lane.tmp_path / "evidence" / "stage-last") in slot
    assert str(admitted["pid"]) in slot, "the slot does not record the process"
    again = lane.invoke([("stage", "canary")], lane.fresh_root("stage-over"))
    assert outcomes(again) == ["refused"]
    assert [row["argv"] for row in lane.launches()] == [["--print", "canary"]]


def test_ac25_live_admission_two_processes_race_for_the_last_slot(lane):
    """Two invocations racing for a kind's last slot: exactly one is admitted
    and the other refuses before any provider call."""
    require_helper()
    lane.seed("m5", LIMITS["m5"] - 1)
    go = lane.tmp_path / "go"
    racers = [
        lane.start([("m5", "runner-canary")], lane.fresh_root(name), go=go)
        for name in ("racer-a", "racer-b")
    ]
    time.sleep(0.2)
    go.touch()
    runs = []
    for process in racers:
        out, err = process.communicate(timeout=60)
        assert process.returncode == 0, err[-2000:]
        runs.append(json.loads(out.splitlines()[-1]))
    assert sorted(outcome for run in runs for outcome in outcomes(run)) == [
        "admitted",
        "refused",
    ], runs
    assert lane.slots("m5") == ["1", "2", "3"]
    assert len(lane.launches()) == 1


def test_ac25_live_admission_a_shared_fresh_root_admits_one_invocation(lane):
    """Two invocations given the same fresh root: one is admitted; the other
    refuses with no slot claimed and no process started."""
    require_helper()
    root = lane.fresh_root()
    first = lane.invoke([("stage", "canary")], root)
    second = lane.invoke([("stage", "canary")], root)
    assert outcomes(first) == ["admitted"] and outcomes(second) == ["refused"]
    assert lane.slots("stage") == ["1"], "the refused invocation claimed a slot"
    assert len(lane.launches()) == 1


def test_ac25_live_admission_fault_after_the_root_counts_no_slot(lane):
    """A fault after step 1 leaves the root with no slot counted, and that
    root refuses the next invocation."""
    require_helper()
    root = lane.fresh_root()
    lane.invoke([("m5", "runner-canary")], root, fault="after-root")
    assert root.is_dir() and lane.slots("m5") == []
    later = lane.invoke([("m5", "runner-canary")], root)
    assert outcomes(later) == ["refused"]
    assert lane.slots("m5") == [] and lane.launches() == []


@pytest.mark.parametrize("record", [False, True], ids=["no-record", "with-record"])
def test_ac25_live_admission_fault_after_the_slot_keeps_it_counted(lane, record):
    """A fault after step 2 leaves the slot counted. A later invocation that
    finds that root, with or without its record, refuses and starts nothing;
    a fresh root takes the next slot."""
    require_helper()
    root = lane.fresh_root()
    lane.invoke([("m5", "runner-canary")], root, fault="after-slot")
    assert lane.slots("m5") == ["1"]
    root.mkdir(exist_ok=True)
    if record:
        (root / "admission.json").write_text(json.dumps({"slot": 1}))
    later = lane.invoke([("m5", "runner-canary")], root)
    assert outcomes(later) == ["refused"] and lane.launches() == []
    assert lane.slots("m5") == ["1"]
    fresh = lane.invoke([("m5", "runner-canary")], lane.fresh_root("next"))
    assert outcomes(fresh) == ["admitted"]
    assert lane.slots("m5") == ["1", "2"]


def test_ac25_live_admission_a_later_node_reuses_the_invocation_admission(lane):
    """A later node of the same invocation proceeds on its admission with no
    new slot, in its own subdirectory under the root."""
    require_helper()
    root = lane.fresh_root()
    run = lane.invoke(
        [
            ("stage", "identity-antigravity"),
            ("stage", "canary"),
            ("stage", "realistic"),
        ],
        root,
    )
    assert outcomes(run) == ["admitted", "admitted", "admitted"], run
    assert lane.slots("stage") == ["1"]
    assert [Path(row["path"]) for row in run["results"]] == [
        root / "identity-antigravity",
        root / "canary",
        root / "realistic",
    ]
    assert len(lane.launches()) == 3


def test_ac25_live_admission_a_node_of_the_other_kind_refuses(lane):
    """An invocation admitted as one kind runs a node of the other kind: that
    node refuses with no new slot claimed and no process started."""
    require_helper()
    run = lane.invoke([("m5", "runner-canary"), ("stage", "canary")], lane.fresh_root())
    assert outcomes(run) == ["admitted", "refused"], run
    assert lane.slots("m5") == ["1"] and lane.slots("stage") == []
    assert [row["argv"] for row in lane.launches()] == [["--print", "runner-canary"]]


@pytest.mark.parametrize("cause", ["version-skip", "root-unset"])
def test_ac25_live_admission_a_skip_or_unset_root_claims_nothing(lane, cause):
    """A node that ends in the version skip, or finds the root variable unset,
    claims no slot and starts nothing (the version check may run)."""
    require_helper()
    root = lane.fresh_root()
    if cause == "version-skip":
        lane.version("1.6.0")
        run = lane.invoke([("stage", "canary")], root)
        assert outcomes(run) == ["skipped"], run
    else:
        run = lane.invoke([("stage", "canary")], None)
        assert outcomes(run)[0] in {"refused", "skipped"}, run
    assert lane.slots("stage") == [] and lane.launches() == []
    assert not root.exists(), "a node that claims nothing created the root"


# --------------------------------------------------------------------------- #
# The invocation-wide attempt deadline
# --------------------------------------------------------------------------- #
def assert_deadline(row: dict, seconds: int) -> float:
    """The node's deadline is ``seconds`` after its invocation's admission."""
    assert not row.get("deadline_missing"), (
        "FAIL AC-25: attempt_deadline is not implemented in the live helpers"
    )
    assert row.get("deadline") is not None, (
        f"FAIL AC-25: attempt_deadline raised {row.get('deadline_error')} for an "
        "admitted invocation"
    )
    deadline = row["deadline"]
    assert row["started"] + seconds <= deadline <= row["finished"] + seconds, (
        f"FAIL AC-25: the deadline is not admission + {seconds} s"
    )
    return deadline


def test_ac25_attempt_deadline_is_fixed_at_admission_and_shared(lane):
    """A stage invocation's deadline is fixed when it is admitted, 3,600 s on
    the monotonic clock, and a later node of the same invocation reads the same
    deadline, not one measured from its own start."""
    require_helper()
    run = lane.invoke(
        [("stage", "identity-antigravity"), ("stage", "canary")],
        lane.fresh_root(),
        pause=0.3,
    )
    assert outcomes(run) == ["admitted", "admitted"], run
    first, later = run["results"]
    deadline = assert_deadline(first, ATTEMPT_SECONDS["stage"])
    assert later["started"] - first["finished"] >= 0.3
    assert later.get("deadline") == deadline, (
        "FAIL AC-25: a later node's deadline differs from the invocation's"
    )


def test_ac25_attempt_deadline_gives_the_m5_kind_1200_seconds(lane):
    """An ``m5`` invocation's deadline is admission plus 1,200 s."""
    require_helper()
    run = lane.invoke([("m5", "runner-canary")], lane.fresh_root())
    assert outcomes(run) == ["admitted"], run
    assert_deadline(run["results"][0], ATTEMPT_SECONDS["m5"])


def test_ac25_attempt_deadline_override_is_ignored_for_the_m5_kind(lane):
    """The override raises only a stage attempt's deadline: with
    ``HEDDLE_W4_LIVE_ATTEMPT_SECONDS`` at 5,400 s, an ``m5`` invocation is still
    admitted, not refused, and its deadline is exactly admission plus 1,200 s."""
    require_helper()
    run = lane.invoke(
        [("m5", "runner-canary")],
        lane.fresh_root(),
        env={OVERRIDE_VARIABLE: str(OVERRUN_CEILING)},
    )
    assert outcomes(run) == ["admitted"], (
        f"FAIL AC-25: the override refused an m5 admission: {run}"
    )
    assert_deadline(run["results"][0], ATTEMPT_SECONDS["m5"])


@pytest.mark.parametrize(
    ("seconds", "admitted"),
    [(OVERRUN_CEILING, True), (OVERRUN_CEILING + 1, False)],
    ids=["at-the-ceiling", "above-the-ceiling"],
)
def test_ac25_attempt_deadline_override_is_capped_at_5400_seconds(
    lane, seconds, admitted
):
    """``HEDDLE_W4_LIVE_ATTEMPT_SECONDS`` raises a stage invocation's deadline
    up to 5,400 s; a larger value refuses admission, claiming no slot and
    starting nothing."""
    require_helper()
    run = lane.invoke(
        [("stage", "canary")],
        lane.fresh_root(),
        env={OVERRIDE_VARIABLE: str(seconds)},
    )
    if admitted:
        assert outcomes(run) == ["admitted"], run
        assert_deadline(run["results"][0], seconds)
        return
    assert outcomes(run) == ["refused"], (
        f"FAIL AC-25: an override of {seconds} s above the ceiling was not refused"
    )
    assert lane.slots("stage") == [] and lane.launches() == []


def test_ac25_attempt_deadline_raises_before_admission(lane):
    """Before its invocation is admitted, ``attempt_deadline`` raises; the call
    admits nothing, and the node admitted afterwards gets its own deadline."""
    require_helper()
    run = lane.invoke([("stage", "canary")], lane.fresh_root(), deadline_first=True)
    before = run["before"]
    assert not before.get("deadline_missing"), (
        "FAIL AC-25: attempt_deadline is not implemented in the live helpers"
    )
    assert before.get("deadline") is None and before.get("deadline_error"), (
        f"FAIL AC-25: attempt_deadline returned {before.get('deadline')} before "
        "admission"
    )
    assert outcomes(run) == ["admitted"], run
    assert_deadline(run["results"][0], ATTEMPT_SECONDS["stage"])


# --------------------------------------------------------------------------- #
# The live canary's launch boundary and bounded evidence retention
# --------------------------------------------------------------------------- #
CANARY = "tests.live.test_antigravity_canary_e2e"
THIS_MODULE = "tests.runtime.test_antigravity_live_admission"
# The doubled launcher: it records its own start, then exits.
LAUNCHER = r"""
import json, os, sys
with open(sys.argv[1], "a") as handle:
    handle.write(json.dumps({"pid": os.getpid()}) + "\n")
"""
LAUNCH_TIMEOUT = 5.0
WINDOW_SECONDS = 0.6


def canary_module():
    return importlib.import_module(CANARY)


def recording_writer(request: dict) -> dict:
    """A bounded writer's double that only records that it started."""
    Path(request["evidence"], "writer-started").write_text("started")
    return {"written": []}


def hung_writer(request: dict) -> dict:
    """A bounded writer's double that outlives any window, then writes late."""
    evidence = Path(request["evidence"])
    (evidence / "writer-started").write_text("started")
    time.sleep(30)
    (evidence / "written-late").write_text("late")
    return {"written": []}


def refusals(record: Path) -> list[str]:
    path = record / "refused.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line)["reason"] for line in path.read_text().splitlines()]


def test_ac25_runner_launch_is_refused_at_the_physical_boundary(tmp_path, monkeypatch):
    """The runner launch's guard reads the clock where the provider process is
    spawned (``run_monitored``, the binding the antigravity runner calls), so
    the runner's own work before it, such as the version probe, counts. A
    launch whose bound fit when the gate run was entered, but no longer fits
    after that work, is refused there, recorded, and the doubled launcher
    never starts. With time to spare, one launch starts and a second is
    refused."""
    canary = canary_module()
    from heddle.gate.runners import antigravity as runner
    from heddle.io import process

    launcher = tmp_path / "launcher.py"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    starts = tmp_path / "starts.jsonl"
    command = [sys.executable, str(launcher), str(starts)]
    config = process.MonitorConfig(
        hard_timeout_s=LAUNCH_TIMEOUT,
        inactivity_timeout_s=LAUNCH_TIMEOUT,
        poll_s=0.05,
        term_grace_s=1.0,
    )

    late = tmp_path / "late"
    late.mkdir()
    with monkeypatch.context() as patch:
        bound = LAUNCH_TIMEOUT + canary.RUNNER_CLEANUP_SECONDS
        stop_at = time.monotonic() + bound + 0.5
        canary.guard_launches(patch, late, stop_at)
        assert runner.run_monitored is process.run_monitored, (
            "FAIL AC-25: the launch guard does not cover the runner's own binding"
        )
        assert canary.launch_refusal(0, LAUNCH_TIMEOUT, stop_at) is None, (
            "fixture: the launch does not fit when the gate run is entered"
        )
        time.sleep(0.7)  # the runner's pre-launch work
        with pytest.raises(RuntimeError, match="live attempt guard"):
            runner.run_monitored(command, tmp_path / "late.ndjson", config)
    reasons = refusals(late)
    assert len(reasons) == 1 and "no longer fits" in reasons[0], reasons
    assert not starts.exists(), "FAIL AC-25: a refused launch started the provider"

    spare = tmp_path / "spare"
    spare.mkdir()
    with monkeypatch.context() as patch:
        canary.guard_launches(patch, spare, time.monotonic() + 3600)
        result = runner.run_monitored(command, tmp_path / "spare.ndjson", config)
        assert result.exit_code == 0
        with pytest.raises(RuntimeError, match="second provider launch"):
            runner.run_monitored(command, tmp_path / "again.ndjson", config)
    assert len(starts.read_text().splitlines()) == 1
    assert refusals(spare) == ["a second provider launch in one runner launch"]


@pytest.mark.parametrize("window", ["exhausted", "passes-during-the-write"])
def test_ac25_evidence_retention_writes_nothing_past_its_window(
    tmp_path, monkeypatch, window
):
    """Evidence the canary keeps after judging (the directed-call files and the
    stream copy) is written inside what remains of the launch's retention
    window. With the window already exhausted, ``save_directed`` starts no
    writer and writes no file; when the window passes during a write, the
    writer's process is killed at it and nothing is written after. Either way
    an explicit evidence-failure record names the error, which propagates."""
    canary = canary_module()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    writer = "recording_writer" if window == "exhausted" else "hung_writer"
    monkeypatch.setattr(canary, "WRITE_ENTRY", f"{THIS_MODULE}:{writer}")
    remaining = -1.0 if window == "exhausted" else WINDOW_SECONDS
    kept = {
        "label": "the runner canary",
        "work": str(tmp_path / "work"),
        "events": [],
        "retention_until": time.monotonic() + remaining,
    }
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        if window == "exhausted":
            canary.save_directed(evidence, kept, [])
        else:
            source = tmp_path / "stream.ndjson"
            source.write_text('{"event": "init"}\n', encoding="utf-8")
            canary.retain_files(
                evidence, kept, {}, ((str(source), "stream.redacted.ndjson"),)
            )
    elapsed = time.monotonic() - started
    failure = json.loads((evidence / "evidence-failure.json").read_text())
    if window == "exhausted":
        assert sorted(path.name for path in evidence.iterdir()) == [
            "evidence-failure.json"
        ], "FAIL AC-25: a write started after the retention window was exhausted"
        assert "exhausted" in failure["evidence_error"], failure
        return
    assert (evidence / "writer-started").exists(), "fixture: the writer never ran"
    assert elapsed < WINDOW_SECONDS + 5, f"the write ran {elapsed:.1f} s"
    time.sleep(0.2)
    assert not (evidence / "written-late").exists(), (
        "FAIL AC-25: evidence was written after the retention window"
    )
    assert "bound" in failure["evidence_error"], failure


def kept_double(request: dict) -> dict:
    """A retention child's double: it keeps nothing and returns at once."""
    return {"envelope": {"ok": True}, "rows": [], "events": [], "refused": []}


def test_ac25_evidence_retention_inside_its_window_writes_every_file(
    tmp_path, monkeypatch
):
    """``retain_launch`` opens the launch's retention window and returns where
    it ends; evidence kept after judging is then written inside it by the
    canary's real writer: each JSON file, and the stream copy byte for byte."""
    canary = canary_module()
    deadline = time.monotonic() + 3600
    monkeypatch.setattr(canary, "attempt_deadline", lambda: deadline)
    monkeypatch.setattr(canary, "RETENTION_ENTRY", f"{THIS_MODULE}:kept_double")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source = tmp_path / "stream.ndjson"
    source.write_bytes(
        ('{"event": "init", "text": "a' + chr(0x2028) + 'b"}\n').encode()
    )
    launch = {
        "label": "the runner canary",
        "host": str(tmp_path),
        "state": str(tmp_path / "state.yaml"),
        "work": str(tmp_path / "work"),
        "gate": "spec-review",
        "killed": None,
        "before": set(),
        "processes": {},
    }
    opened = time.monotonic()
    kept = canary.retain_launch(evidence, launch)
    window = canary.RETENTION_SECONDS
    assert opened + window <= kept["retention_until"] <= time.monotonic() + window, (
        "FAIL AC-25: retain_launch does not return its retention window's end"
    )
    rows = [{"call": "read_file", "outcome": "inspectable"}]
    canary.retain_files(
        evidence,
        kept,
        {"directed-calls.json": rows},
        ((str(source), "stream.redacted.ndjson"),),
    )
    assert json.loads((evidence / "directed-calls.json").read_text()) == rows
    assert (evidence / "stream.redacted.ndjson").read_bytes() == source.read_bytes()
    assert not (evidence / "evidence-failure.json").exists()
