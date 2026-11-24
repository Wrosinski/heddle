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

Red until the helper exists.
"""

from __future__ import annotations

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
TESTED_VERSION = "1.5.16"
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
from tests.antigravity_live_helpers import LiveAdmissionRefused, admit_live_attempt

results = []
for kind, node in spec["nodes"]:
    try:
        path = admit_live_attempt(kind, node, admissions=Path(spec["admissions"]))
    except LiveAdmissionRefused as error:
        results.append({"node": node, "outcome": "refused", "detail": str(error)})
        continue
    except pytest.skip.Exception as error:
        results.append({"node": node, "outcome": "skipped", "detail": str(error)})
        continue
    subprocess.run(["agy", "--print", node], check=True)
    results.append({"node": node, "outcome": "admitted", "path": str(path)})
print(json.dumps({"pid": os.getpid(), "results": results}))
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
    ) -> subprocess.Popen:
        environment = {**os.environ, "PATH": self.path, "PYTHONDONTWRITEBYTECODE": "1"}
        environment.pop(ROOT_VARIABLE, None)
        if root is not None:
            environment[ROOT_VARIABLE] = str(root)
        spec = {
            "nodes": nodes,
            "admissions": str(self.admissions),
            "fault": fault,
            "go": str(go) if go else None,
        }
        return subprocess.Popen(
            [sys.executable, "-c", INVOCATION, str(REPO_ROOT), json.dumps(spec)],
            cwd=self.tmp_path,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def invoke(self, nodes, root, *, fault=None) -> dict:
        process = self.start(nodes, root, fault=fault)
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
