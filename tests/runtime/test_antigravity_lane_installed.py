"""Installed Antigravity lane: needs an explicit e2e grant.

Collection is safe. Execution builds one offline wheel and runs the real
installed ``heddle`` against disposable Git hosts. Only ``agy``, ``claude`` and
``codex`` are doubled, by ``write_agy_shim`` and ``write_lane_shims`` in
``tests/runtime/wheel_harness.py``. The doubles record what they received and
emit synthetic streams; the test, not a double, judges every observation
against values it computes itself:

- argv is the specified launch command line for the slot, flag for flag;
- the working directory is the checkout root;
- every physical launch carries a launch token, and no token digest repeats
  across the launches of scenarios 1, 5 and 8 (AC-9's fresh token);
- the delivered content, inline or reassembled from the chunks in index
  order, hashes to the capture digest the run recorded;
- the delivered schema has no ``$ref`` and equals the canonical schema once
  the test inlines its references;
- AC-6's escape-heavy and 1- to 4-byte fixtures of exactly 160,000 bytes
  arrive whole, with the wrapped size under the 192,000-byte cap.

Scenarios through the real boundary:

1. healthy inline review (AC-4, AC-6, AC-9, AC-13);
2. healthy chunked review against a test-computed interval map (AC-7,
   AC-17);
3. ceiling refusal, standalone and as a ``run-gates`` member, with zero
   launches of any lane (AC-8);
4. one run per hard-failure reason of the outcome table's rows 1-7,
   standalone and in a batch, each output naming the exact reason and that
   reason's own remedy (AC-11, AC-14, AC-15, AC-16, AC-19). The one
   exception is ``inactivity-timeout``: only Heddle's monitor ends a run for
   inactivity, its bound is 1,200 s and the installed CLI has no override;
5. recovery before recording after the coordinator is killed (AC-12);
6. a marked ``heddle`` mutation from the reviewer child, on all three lanes
   (AC-1, AC-2);
7. a sibling ``reviews/`` read, on all three lanes (AC-21);
8. coordinator reaping of a killed member's tokens (AC-12).

Every recorded run's persisted audit outcome is clean, and a chunked run's
credited whole reads are exactly the test's own interval map. Before an owner
acts on a process, the test proves that the process exists and carries its
launch's token; every process the test starts is stopped in failure-safe
teardown.

Activation seam named here: the gate summary persists the run's audit outcome
as ``summary["audit"]``, with ``credited`` (one row per credited whole read:
the chunk file's absolute ``path`` and its half-open capture byte range
``start`` and ``end``), ``uncovered``, ``violations`` and
``transport_intact`` (``persisted_audit`` is the one reader to re-point).

Index format: the plan's fixed ``{"chunks": [{"path", "start", "end",
"sha256"}]}``, read and checked through the runner tests' one strict reader
(``index_records``, by way of ``assert_index_lists_chunks`` in
``tests/gate/test_antigravity_runner.py``), so both witnesses share it: entry
``n`` names the file holding chunk ``n``, states its half-open capture byte
range and carries its SHA-256.

Every node is red until the lane exists in the installed wheel. Pass
condition: each scenario's exit code, reason, ledger state, files and
observations match exactly. No network, provider or credential is used, and
the real ``agy`` is never on the hosts' ``PATH``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from tests.gate.test_antigravity_runner import (
    CEILING,
    ESCAPE_HEAVY,
    INLINE_LIMIT,
    MULTIBYTE,
    READ_ALL_BUT_LAST,
    READ_BYTES,
    READ_LINES,
    RUNNER_ROWS,
    TESTED_VERSION,
    WORST_PREFIX,
    WRAPPER_TAG,
    assert_index_lists_chunks,
    clean_stream,
    init_event,
    resolve_refs,
    result_event,
    summary_for,
    temp_dir_of,
    tool_step,
)
from tests.runtime.wheel_harness import (
    AGY_WRAPPED_CAP,
    AGY_WRAPPED_HEADER,
    AGY_WRAPPED_TRAILER,
    build_installed_wheel,
    write_agy_shim,
    write_lane_shims,
)
from tests.tiering_helpers import ASTRA, FABLE, entry, prepare_input, wire_policy
from tests.tiering_installed_helpers import (
    FEATURE,
    all_off,
    author_value,
    journey,
)
from tests.tiering_review_helpers import review_content

pytestmark = [pytest.mark.acceptance, pytest.mark.e2e, pytest.mark.toolchain]

SPEC_REVIEW = "spec-review"
NEON = {"cli": "antigravity", "model": "gemini-4-neon", "reasoning_effort": "high"}
LANES = {"antigravity": NEON, "claude": FABLE, "codex": ASTRA}
MARKER = "HEDDLE_AGENT_SESSION"
FAILED = tuple(range(1, 128))
OUT_OF_SCOPE = "out-of-scope-access"
# The outcome table's hard failures (rows 1-7), each with the runner row
# whose stream yields it. ``None`` marks a stream built here. The monitor's
# ``inactivity-timeout`` is the one reason left out (see the module doc).
HARD_FAILURES = {
    OUT_OF_SCOPE: None,
    "antigravity-unapproved-tool": None,
    "model-substituted": "model-mismatch",
    "antigravity-context-compacted": None,
    "timeout": "print-timeout",
    "antigravity-quota": "quota",
    "antigravity-api-error": "retryable-api-error",
    "unsupported-model": "invalid-model-selection",
    "exit": "nonzero-exit",
    "transport-error": "unparseable-line",
    "execution-failure": "interrupted",
    "antigravity-incomplete-coverage": None,
    "empty-output": "missing-structured-output",
}
AUDIT_KEYS = frozenset({"credited", "uncovered", "violations", "transport_intact"})
# Consumer fields of a failed ``run-gates`` member: where it names its reason,
# and where it carries its remedy.
REASON_FIELDS = ("failure_reason", "reason", "error", "message", "summary")
REMEDY_FIELDS = ("remedy", "hint", "next_actions")
GUIDE_PROBE = """
import json
from importlib.resources import files

path = files("heddle").joinpath("resources/gate-failure-guide.md")
entries, name = {}, None
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith("## `") and line.endswith("`"):
        name = line[4:-1]
        entries[name] = ""
    elif name and line.startswith("**Remedy:**"):
        entries[name] = line[len("**Remedy:**"):].strip()
print(json.dumps(entries))
"""
INSTALLED_GUIDES: dict[str, dict[str, str]] = {}
HOST_CONFIG = (
    "autopilot:\n  test_command: python3 tests/check.py\n"
    "agents:\n  claude: true\n  codex: true\n  antigravity: true\n"
    "checkpoints:\n  specification: false\n  review_changes: false\n"
)
# Token digests seen in scenarios 1, 5 and 8, so no launch reuses a token.
TOKEN_DIGESTS: dict[str, str] = {}


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("antigravity-lane"))


# --------------------------------------------------------------------------- #
# Disposable hosts with the doubled providers
# --------------------------------------------------------------------------- #
@dataclass
class LaneHost:
    """One installed host at spec-review, with doubled agy, claude and codex."""

    case: object
    home: Path
    bin: Path

    @property
    def root(self) -> Path:
        return self.case.root

    @property
    def state(self) -> Path:
        return self.case.state

    @property
    def reviews(self) -> Path:
        return self.state.parent / "reviews"

    @property
    def data_dir(self) -> Path:
        return Path(self.case.env["HOME"]) / ".gemini" / "antigravity-cli"

    def scenario(self, ops=None, *, roles=None, version=TESTED_VERSION) -> None:
        value = {
            "ops": clean_stream() if ops is None else ops,
            "roles": roles or {},
            "version": version,
            "reviews": {SPEC_REVIEW: review_content(SPEC_REVIEW)},
        }
        (self.home / "scenario.json").write_text(json.dumps(value), encoding="utf-8")

    def lane(self, cli: str, *, steps=(), hold: str | None = None) -> None:
        value = {
            "review": review_content(SPEC_REVIEW),
            "steps": list(steps),
            "hold": hold,
        }
        (self.home / f"{cli}.json").write_text(json.dumps(value), encoding="utf-8")

    def rows(self, name: str) -> list[dict]:
        path = self.home / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def observations(self) -> list[dict]:
        return self.rows("observations.jsonl")

    def launches(self) -> int:
        """Physical ``agy`` launches, from the double's invocation log."""
        return len(self.rows("invocations.log"))

    def calls(self, cli: str) -> list[dict]:
        return self.rows(f"{cli}.jsonl")

    def physical_launches(self) -> int:
        return self.launches() + len(self.calls("claude")) + len(self.calls("codex"))

    def release(self, name: str) -> None:
        (self.home / name).touch()

    def wait_for(self, name: str, count: int = 1, timeout: float = 120.0) -> list:
        deadline = time.monotonic() + timeout
        while len(self.rows(name)) < count:
            assert time.monotonic() < deadline, f"fixture: {name} never reached {count}"
            time.sleep(0.05)
        return self.rows(name)

    def run(self, *args, expected=(0,)) -> dict:
        return self.case.run(*args, expected=expected)

    def spawn(self, *args) -> subprocess.Popen:
        """Start an installed command in its own process group."""
        argv = [str(self.case.installed.command), *args, "--feature", FEATURE]
        return subprocess.Popen(
            [*argv, "--json"],
            cwd=self.root,
            env=self.case.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    def ledger(self) -> list[dict]:
        state = yaml.safe_load(self.state.read_text())
        return [
            {**run, "gate": gate["gate"]}
            for gate in state["gates"]
            if gate["gate"] == SPEC_REVIEW
            for run in gate["runs"]
        ]

    def captures(self) -> list[Path]:
        return sorted(self.reviews.glob("*.capture.json"))

    def cleanup(self) -> None:
        for name in ("release", "release-first", "release-second"):
            self.release(name)
        for child in self.rows("children.jsonl"):
            if alive(child["pid"], child["start"]):
                try:
                    os.kill(child["pid"], signal.SIGKILL)
                except OSError:
                    pass


def lane_host(installed, tmp_path, *, primary, secondary=None) -> LaneHost:
    """A real installed feature at spec-review whose slots name these reviewers."""
    case = journey(installed, tmp_path)
    (case.root / ".heddle.yaml").write_text(HOST_CONFIG)
    chosen = all_off()
    chosen[SPEC_REVIEW] = entry(
        SPEC_REVIEW, limit=3, primary=primary, secondary=secondary
    )
    prepared = case.payload("prepare.json", prepare_input())
    case.run(
        "feature",
        "prepare",
        FEATURE,
        "--area",
        "runtime",
        "--from-file",
        prepared,
        feature=False,
    )
    policy = case.payload("policy.json", wire_policy(overrides=chosen))
    case.run("feature", "policy", FEATURE, "--from-file", policy, feature=False)
    case.run("feature", "start", FEATURE, feature=False)
    author_value(case)
    case.run("phase-exit", "--through", "complete")
    assert yaml.safe_load(case.state.read_text())["stage"] == SPEC_REVIEW
    home, bin_dir = tmp_path / "doubles", tmp_path / "doubles-bin"
    write_agy_shim(bin_dir, home, baseline=sorted(case.env))
    write_lane_shims(bin_dir, home)
    case.env["PATH"] = f"{bin_dir}{os.pathsep}{case.env['PATH']}"
    case.env.pop(MARKER, None)
    for name in ("agy", "claude", "codex"):
        assert shutil.which(name, path=case.env["PATH"]) == str(bin_dir / name), (
            f"fixture: the doubled {name} is not first on the host PATH"
        )
    host = LaneHost(case, home, bin_dir)
    host.scenario()
    for cli in ("claude", "codex"):
        host.lane(cli)
    return host


@pytest.fixture
def hosts():
    made: list[LaneHost] = []
    yield made
    for host in made:
        host.cleanup()


def write_prompt(root: Path, text: str) -> None:
    """The host's generated-tier spec-review template."""
    path = root / "prompts" / f"{SPEC_REVIEW}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def filler_prompt(size: int, unit: str = MULTIBYTE, *, tag: bool = False) -> str:
    head = "# Review\n\nReview every declared value against the specification.\n\n"
    if tag:
        head += f"Quote {WRAPPER_TAG} literally if it appears.\n\n"
    body = unit * (size // len(unit.encode("utf-8")) + 1)
    return head + body + "\n\nEND OF FIXTURE\n"


def size_capture(host: LaneHost, monkeypatch, total: int, unit: str) -> None:
    """Write a host prompt that makes the frozen capture exactly ``total`` bytes.

    Sizing only: an in-process preparation of this checkout's ``heddle``,
    which the wheel was built from, measures the capture on a same-length
    twin of the host and stops before any launch. The installed run's own
    delivery is then judged against ``total``.
    """
    from heddle.contracts import operations as ops
    from heddle.gate import entry as gate_entry
    from heddle.kernel.project_config import KernelError
    from heddle.runtime.application import execute

    twin = host.root.with_name("installed-twin")
    assert len(twin.name) == len(host.root.name), "fixture: the twin path differs"
    shutil.copytree(host.root, twin, symlinks=True)
    original = gate_entry.prepare_gate_run
    sizes: list[int] = []

    def probe(*args, **kwargs):
        prepared = original(*args, **kwargs)
        sizes.append(len(prepared.transport.stdin.encode("utf-8")))
        raise KernelError(
            code="workspace-invalid", message="fixture probe", hint="fixture probe"
        )

    def measure(text: str) -> int:
        write_prompt(twin, text)
        with monkeypatch.context() as patch:
            patch.chdir(twin)
            patch.delenv(MARKER, raising=False)
            patch.setattr(gate_entry, "prepare_gate_run", probe)
            execute(ops.RunGate(SPEC_REVIEW, feature=FEATURE, **NEON))
        assert sizes, "fixture: the sizing probe never reached preparation"
        return sizes.pop()

    head = "# Review\n\n"

    def body(count: int, pad: int) -> str:
        # Padding leads, so it never meets the units' line ends.
        return head + "x" * pad + unit * count + "\nEND OF FIXTURE\n"

    try:
        base = measure(body(0, 0))
        growth = (measure(body(100, 0)) - base) / 100
        count = max(0, int((total - base - 256) // growth))
        pad = 0
        for _ in range(8):
            text = body(count, pad)
            size = measure(text)
            if size == total:
                write_prompt(host.root, text)
                return
            if size > total:
                count = max(0, count - int((size - total) // growth) - 2)
                pad = 0
            else:
                pad += total - size
        raise AssertionError(f"fixture: could not size the capture to {total} bytes")
    finally:
        shutil.rmtree(twin, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Judging the observations
# --------------------------------------------------------------------------- #
BARE_FLAGS = ("--dangerously-skip-permissions", "--disable-slash-commands")
VALUE_FLAGS = (
    "--input-format",
    "--output-format",
    "--model",
    "--effort",
    "--settings",
    "--json-schema",
)


def parsed_argv(argv: list[str]) -> dict[str, str | None]:
    """Each flag once, with its value; anything else fails the launch."""
    parsed: dict[str, str | None] = {}
    position = 0
    while position < len(argv):
        name = argv[position]
        assert name not in parsed, f"FAIL AC-9: {name} appears twice in {argv}"
        if name in BARE_FLAGS:
            parsed[name] = None
            position += 1
        elif name in VALUE_FLAGS and position + 1 < len(argv):
            parsed[name] = argv[position + 1]
            position += 2
        else:
            raise AssertionError(f"FAIL AC-9: unexpected argument {name!r} in {argv}")
    return parsed


def assert_exact_launch(host: LaneHost, observation: dict) -> None:
    """AC-9: the launch command line for the Neon slot, flag for flag."""
    argv = observation["argv"]
    assert not any(arg.startswith("--log-file") for arg in argv), (
        "FAIL AC-9: agy was given --log-file"
    )
    parsed = parsed_argv(argv)
    schema_path = parsed.get("--json-schema")
    expected = {
        "--input-format": "stream-json",
        "--output-format": "stream-json",
        "--model": NEON["model"],
        "--effort": NEON["reasoning_effort"],
        "--dangerously-skip-permissions": None,
        "--disable-slash-commands": None,
        "--settings": parsed.get("--settings"),
        "--json-schema": schema_path,
    }
    assert parsed == expected, f"FAIL AC-9: launch flags {parsed}, expected {expected}"
    settings = json.loads(parsed["--settings"] or "{}")
    rules = settings.get("permissions", {}).get("deny")
    assert isinstance(rules, list) and len(rules) == 1, f"FAIL AC-9: deny {rules}"
    denied = re.fullmatch(r"read_file\((.+)\)", rules[0])
    assert denied and os.path.normpath(denied.group(1)) == str(host.data_dir), (
        f"FAIL AC-9: the deny rule is not read_file on agy's data dir: {rules}"
    )
    assert schema_path and Path(schema_path).is_absolute(), (
        f"FAIL AC-9: --json-schema names {schema_path!r}"
    )
    assert not Path(schema_path).resolve().is_relative_to(host.root.resolve()), (
        "FAIL AC-9: the schema copy was written into the checkout, not the run temp"
    )
    assert Path(observation["cwd"]).resolve() == host.root.resolve(), (
        f"FAIL AC-9: agy ran in {observation['cwd']}, not the checkout root"
    )
    assert observation["marker"] == "gate", (
        f"FAIL AC-9: marker {observation['marker']!r}"
    )


def assert_canonical_schema(observation: dict) -> None:
    """AC-9: the delivered schema is ``$ref``-free and is the canonical
    contract with its references inlined by the test."""
    from heddle.gate.results import review_output_contract

    text = observation["schema"]
    assert text is not None, "FAIL AC-9: the --json-schema file was not readable"
    assert "$ref" not in text and "$defs" not in text, "FAIL AC-9: $ref in schema"
    canonical = json.loads(review_output_contract(SPEC_REVIEW).schema_json)
    assert json.loads(text) == resolve_refs(canonical), (
        "FAIL AC-9: the delivered schema is not the canonical contract inlined"
    )


def remember_tokens(scenario: str, observations: list[dict]) -> None:
    """AC-9: every launch carries a token, and no token digest repeats across
    the launches of scenarios 1, 5 and 8."""
    for number, observation in enumerate(observations, start=1):
        digests = observation["launch_digests"]
        assert digests, f"FAIL AC-9: {scenario} launch {number} carries no token"
        label = f"{scenario} launch {number}"
        for digest in digests.values():
            assert digest not in TOKEN_DIGESTS, (
                f"FAIL AC-9: {label} reuses the token of {TOKEN_DIGESTS[digest]}"
            )
            TOKEN_DIGESTS[digest] = label
        argv_digests = {
            hashlib.sha256(arg.encode()).hexdigest() for arg in observation["argv"]
        }
        assert not argv_digests & set(digests.values()), (
            f"FAIL AC-9: {label} shows its token in argv"
        )


def files_of(observation: dict) -> dict[str, bytes]:
    return {path: base64.b64decode(data) for path, data in observation["files"].items()}


def delivered(observation: dict) -> bytes:
    """The content the double received: inline, or the chunks in index order."""
    if observation["chunks"]:
        files = files_of(observation)
        return b"".join(files[path] for path in observation["chunks"])
    (content,) = observation["contents"]
    return content.encode("utf-8")


def capture_digest(capture: bytes) -> str:
    """The run's recorded capture digest, recomputed: SHA-256 of the canonical
    JSON of the transport, whose system part is empty on this lane."""
    canonical = json.dumps(
        {"stdin": capture.decode("utf-8"), "system": None},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def assert_delivered_capture(row: dict, observation: dict, ac: str) -> bytes:
    capture = delivered(observation)
    recorded = row.get("effective_prompt_sha256")
    assert recorded, f"FAIL {ac}: the run recorded no capture digest: {row}"
    assert capture_digest(capture) == recorded, (
        f"FAIL {ac}: the delivered content does not hash to the recorded capture"
    )
    return capture


def assert_recorded_neon(host: LaneHost, row: dict) -> None:
    """AC-4: the ledger row and the retained result carry the Neon selection."""
    assert row["verdict"]["status"] != "error", (
        f"FAIL AC-4: the run failed as {row.get('failure_reason')}"
    )
    execution = row.get("execution_config") or {}
    assert (
        row.get("cli") == "antigravity"
        and {key: execution.get(key) for key in NEON} == NEON
    ), f"FAIL AC-4: the ledger row records {row.get('cli')} {execution}"
    result = json.loads((host.state.parent / row["artifact"]).read_text())
    recorded = result["invocation"]["execution"]
    assert {key: recorded.get(key) for key in NEON} == NEON, (
        f"FAIL AC-4: the retained result records {recorded}"
    )


def assert_version_recorded(host: LaneHost) -> None:
    """AC-13: the gate log and summary record the matching ``agy`` version."""
    summary = summary_for(host.state)
    log = Path(summary["io"]["log"]).read_text()
    for text, where in ((log, "gate log"), (json.dumps(summary), "summary")):
        assert TESTED_VERSION in text, f"FAIL AC-13: the {where} lacks the version"


def lane_captures(host: LaneHost, cli: str = "antigravity") -> list[Path]:
    """Retained captures whose invocation ran on ``cli``."""
    return [
        path
        for path in host.captures()
        if json.loads(path.read_text())["invocation"]["execution"]["cli"] == cli
    ]


def names(text: str, reason: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(reason)}(?![\w-])", text) is not None


def member(envelope: dict, slot: str) -> dict:
    members = (envelope.get("data") or {}).get("members") or (
        (envelope.get("error") or {}).get("details") or {}
    ).get("members")
    assert members, f"fixture: the batch reported no members: {envelope}"
    (found,) = [row for row in members if row.get("reviewer_slot") == slot]
    return found


def alive(pid: int, start: int | None) -> bool:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    fields = text.rsplit(")", 1)[1].split()
    return fields[0] != "Z" and (start is None or int(fields[19]) == start)


def wait_gone(pid: int, start: int | None, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while alive(pid, start):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def tree(root: Path) -> dict[str, str]:
    """Bytes of every file outside ``.git``, for no-write checks."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def environ_digests(pid: int, names) -> dict[str, str] | None:
    """SHA-256 digests of ``names`` in a live process's environment."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    values = dict(item.split(b"=", 1) for item in raw.split(b"\0") if b"=" in item)
    return {
        name: hashlib.sha256(values[name.encode()]).hexdigest()
        for name in names
        if name.encode() in values
    }


def assert_tokened_child(child: dict, observation: dict, what: str) -> None:
    """Before the owner acts: the child runs and carries its launch's token."""
    assert alive(child["pid"], child["start"]), (
        f"fixture: {what} was not running before the owner acted"
    )
    tokens = observation["launch_digests"]
    assert tokens, f"FAIL AC-12: the launch behind {what} carries no token"
    assert environ_digests(child["pid"], tokens) == tokens, (
        f"FAIL AC-12: {what} does not carry its launch's token"
    )


def persisted_audit(host: LaneHost, ac: str) -> dict:
    """Seam: the audit outcome the gate summary persists (see the module doc)."""
    audit = summary_for(host.state).get("audit")
    assert isinstance(audit, dict) and AUDIT_KEYS <= set(audit), (
        f"FAIL {ac}: the gate summary persists no audit outcome"
    )
    return audit


def assert_credited(host: LaneHost, intervals, ac: str) -> None:
    """A recorded run's audit is clean, and its credited whole reads are
    exactly the test's own interval map (empty for inline delivery)."""
    audit = persisted_audit(host, ac)
    assert audit["violations"] == [], f"FAIL {ac}: violations {audit['violations']}"
    assert audit["transport_intact"] is True, f"FAIL {ac}: transport not intact"
    assert audit["uncovered"] == [], f"FAIL {ac}: uncovered {audit['uncovered']}"
    credited = sorted(
        {
            (os.path.realpath(row["path"]), int(row["start"]), int(row["end"]))
            for row in audit["credited"]
        }
    )
    expected = sorted(
        (os.path.realpath(path), start, end) for path, start, end in intervals
    )
    assert credited == expected, (
        f"FAIL {ac}: the audit credits {credited}, not the frozen chunks {expected}"
    )


def installed_guide(host: LaneHost) -> dict[str, str]:
    """The installed wheel's failure guide: each reason's remedy text."""
    installed = host.case.installed
    key = str(installed.root)
    if key not in INSTALLED_GUIDES:
        probe = installed.python_probe(GUIDE_PROBE, cwd=host.root)
        assert probe.returncode == 0, f"fixture: the guide probe failed: {probe.stderr}"
        INSTALLED_GUIDES[key] = json.loads(probe.stdout)
    return INSTALLED_GUIDES[key]


def field_text(row: dict, fields) -> str:
    return " ".join(
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for value in (row.get(field) for field in fields)
        if value
    )


def names_entry(text: str, reason: str) -> bool:
    """Whether ``text`` points at ``reason``'s own failure-guide entry."""
    pattern = rf"`{re.escape(reason)}`[^.;\n]*\bentry\b[^.;\n]*failure guide"
    return re.search(pattern, text, re.I) is not None


def assert_matching_remedy(text: str, reason: str, guide: dict[str, str]) -> None:
    """AC-19: the consumer's remedy is this reason's own, not a generic one."""
    remedy = guide.get(reason)
    assert remedy, f"FAIL AC-19: the installed failure guide has no remedy for {reason}"
    assert names_entry(text, reason) or remedy in text, (
        f"FAIL AC-19: the remedy neither names the {reason} failure-guide entry nor "
        f"carries its remedy: {text!r}"
    )
    others = [other for other in guide if other != reason and names_entry(text, other)]
    assert others == [], f"FAIL AC-19: the remedy points at other entries: {others}"


# --------------------------------------------------------------------------- #
# Scenario 1: a healthy inline review
# --------------------------------------------------------------------------- #
INLINE_FIXTURES = {
    "packaged-prompt": None,
    "1-to-4-byte-160000": MULTIBYTE,
    "escape-heavy-160000": ESCAPE_HEAVY,
}


@pytest.mark.parametrize("fixture", list(INLINE_FIXTURES))
def test_healthy_inline_review_records_neon_through_the_installed_cli(
    installed, tmp_path, monkeypatch, hosts, fixture
):
    """Scenario 1 (AC-4, AC-6, AC-9, AC-13): ``run-gate`` through the slot
    records ``antigravity`` / ``gemini-4-neon`` / ``high`` in the ledger row
    and the retained result. The one launch is exact, carries a fresh token,
    receives the capture inline and the canonical schema inlined, and the
    version is recorded. The 160,000-byte fixtures arrive whole, under the
    wrapped cap."""
    host = lane_host(installed, tmp_path, primary=NEON)
    hosts.append(host)
    unit = INLINE_FIXTURES[fixture]
    if unit is not None:
        size_capture(host, monkeypatch, INLINE_LIMIT, unit)

    envelope = host.run("run-gate", SPEC_REVIEW)

    assert envelope["ok"], f"FAIL AC-4: the installed run did not record: {envelope}"
    (row,) = host.ledger()
    assert_recorded_neon(host, row)
    assert host.launches() == 1 and host.physical_launches() == 1, (
        f"FAIL AC-9: expected one physical launch, saw {host.physical_launches()}"
    )
    (observation,) = host.observations()
    assert_exact_launch(host, observation)
    assert_canonical_schema(observation)
    remember_tokens(f"scenario 1 ({fixture})", [observation])
    assert observation["events"] == ["user"] and not observation["chunks"], (
        f"FAIL AC-6: the capture was not delivered inline: {observation['events']}"
    )
    assert not observation["refused"] and observation["stdin_errors"] == []
    capture = assert_delivered_capture(row, observation, "AC-6")
    if unit is not None:
        assert len(capture) == INLINE_LIMIT, (
            f"FAIL AC-6: delivered {len(capture)} bytes of a 160,000-byte capture"
        )
        (wrapped,) = observation["wrapped"]
        assert wrapped == AGY_WRAPPED_HEADER + INLINE_LIMIT + AGY_WRAPPED_TRAILER
        assert wrapped <= AGY_WRAPPED_CAP
    assert_version_recorded(host)
    assert_credited(host, [], "AC-4")


# --------------------------------------------------------------------------- #
# Scenario 2: a healthy chunked review
# --------------------------------------------------------------------------- #
def interval_map(observation: dict) -> list[tuple[str, int, int]]:
    """The test's own map: each chunk path with its byte range in the capture."""
    files = files_of(observation)
    rows, start = [], 0
    for path in observation["chunks"]:
        end = start + len(files[path])
        rows.append((path, start, end))
        start = end
    return rows


def test_healthy_chunked_review_reads_every_chunk_whole(installed, tmp_path, hosts):
    """Scenario 2 (AC-7, AC-17): a capture above 160,000 bytes with a wrapper
    tag is delivered as chunks behind a pointer message. The chunks
    reassemble the recorded capture; the test's interval map tiles it; the
    index binds each chunk to its digest and range; each chunk fits one
    ``read_file`` call. The run records, and its persisted audit credits
    exactly the interval map's paths and byte ranges, with nothing uncovered,
    no violation and the transport intact."""
    host = lane_host(installed, tmp_path, primary=NEON)
    hosts.append(host)
    write_prompt(host.root, filler_prompt(175_000, tag=True))

    envelope = host.run("run-gate", SPEC_REVIEW)

    assert envelope["ok"], f"FAIL AC-17: the chunked review did not record: {envelope}"
    (row,) = host.ledger()
    assert_recorded_neon(host, row)
    (observation,) = host.observations()
    assert_exact_launch(host, observation)
    assert observation["chunks"] and observation["index"], (
        "FAIL AC-7: the launch message points at no index and chunks"
    )
    capture = assert_delivered_capture(row, observation, "AC-7")
    assert INLINE_LIMIT < len(capture) <= CEILING, (
        f"fixture: the chunked capture is {len(capture)} bytes"
    )
    (message,) = observation["contents"]
    assert len(message.encode("utf-8")) < 8_192 and MULTIBYTE * 4 not in message, (
        "FAIL AC-7: the NDJSON message carries capture text, not the launch pointer"
    )
    intervals = interval_map(observation)
    assert intervals[0][1] == 0 and intervals[-1][2] == len(capture), (
        f"FAIL AC-7: the chunks do not span the capture: {intervals}"
    )
    files = files_of(observation)
    # The index, read strictly, lists exactly the interval map's slices in
    # capture order, each naming its file inside the run temp directory with
    # its own range and digest. The installed run persists no frozen route,
    # so the slices come from the delivered chunks; their order is not taken
    # on trust from the index: joined in that order they must hash to the
    # capture digest the ledger recorded (``assert_delivered_capture``).
    assert_index_lists_chunks(
        files[observation["index"]],
        {os.path.realpath(path): data for path, data in files.items()},
        capture,
        [(start, end) for _, start, end in intervals],
        "AC-7",
        directory=temp_dir_of(observation),
    )
    for path, start, end in intervals:
        piece = capture[start:end]
        assert files[path] == piece, (
            f"FAIL AC-7: chunk {path} is not the capture's slice [{start}, {end})"
        )
        lines = piece.count(b"\n") + (0 if piece.endswith(b"\n") else 1)
        assert lines <= READ_LINES and len(piece) + lines * WORST_PREFIX <= READ_BYTES
    reads = {(row["launch"], row["path"]) for row in host.rows("reads.jsonl")}
    assert {(observation["pid"], path) for path, _, _ in intervals} <= reads, (
        "fixture: the double did not read every chunk whole"
    )
    assert_credited(host, intervals, "AC-17")


# --------------------------------------------------------------------------- #
# Scenario 3: the ceiling refusal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("route", ["run-gate", "run-gates"])
def test_capture_above_the_ceiling_is_refused_before_any_launch(
    installed, tmp_path, hosts, route
):
    """Scenario 3 (AC-8): above 250,000 bytes the refusal has code
    ``antigravity-input-too-large``, names the size and the ceiling, and its
    remedy amends the policy. In the batch it names the member and the slots
    left out. No lane launches; no run is recorded."""
    batch = route == "run-gates"
    host = lane_host(
        installed, tmp_path, primary=NEON, secondary=FABLE if batch else None
    )
    hosts.append(host)
    write_prompt(host.root, filler_prompt(300_000))
    before = host.state.read_bytes()

    if batch:
        envelope = host.run("run-gates", expected=FAILED)
    else:
        envelope = host.run("run-gate", SPEC_REVIEW, expected=FAILED)

    error = envelope["error"]
    assert error["code"] == "antigravity-input-too-large", (
        f"FAIL AC-8: refusal code {error['code']!r}: {error}"
    )
    numbers = {
        int(value.replace(",", ""))
        for value in re.findall(r"\d[\d,]*", error["message"])
    }
    assert CEILING in numbers and any(value > CEILING for value in numbers), (
        f"FAIL AC-8: the refusal does not name the size and ceiling: {error['message']}"
    )
    assert "polic" in (error.get("hint") or "").lower(), (
        f"FAIL AC-8: the remedy does not amend the policy: {error.get('hint')}"
    )
    if batch:
        text = json.dumps(error)
        assert "primary" in text and "secondary" in text, (
            f"FAIL AC-8: the batch refusal does not name the member and slots: {error}"
        )
    assert host.physical_launches() == 0, (
        f"FAIL AC-8: {host.physical_launches()} provider launches after the refusal"
    )
    assert host.ledger() == [] and host.state.read_bytes() == before, (
        "FAIL AC-8: the refusal recorded a run or changed state"
    )


# --------------------------------------------------------------------------- #
# Scenario 4: one run per hard-failure reason
# --------------------------------------------------------------------------- #
def failure_stream(reason: str) -> tuple[list, bool]:
    """The synthetic stream that yields ``reason``; whether it needs chunks."""
    built = {
        "antigravity-context-compacted": clean_stream(
            {"$step": {"step_type": "checkpoint"}}
        ),
        "antigravity-unapproved-tool": clean_stream(
            tool_step("search_web", {"query": "review guidance"}, "3 results")
        ),
        "antigravity-incomplete-coverage": clean_stream(reads=READ_ALL_BUT_LAST),
        OUT_OF_SCOPE: clean_stream(
            result=result_event(
                denied_actions=[{"action": "read_file", "display_name": "Read File"}]
            )
        ),
    }
    row = HARD_FAILURES[reason]
    if row is None:
        return built[reason], reason == "antigravity-incomplete-coverage"
    stream, expected = RUNNER_ROWS[row]
    assert expected == reason, f"fixture: runner row {row} yields {expected}"
    return stream, False


@pytest.mark.parametrize("route", ["run-gate", "run-gates"])
@pytest.mark.parametrize("reason", list(HARD_FAILURES))
def test_each_hard_failure_reaches_standalone_and_batch_output(
    installed, tmp_path, hosts, reason, route
):
    """Scenario 4 (AC-11, AC-14, AC-15, AC-16, AC-19): each hard-failure
    reason is recorded on the run and reaches its consumer: the standalone
    error's message names the exact reason and its hint carries that
    reason's own remedy; in a batch, the failed member's reason and remedy
    fields do. A remedy is the reason's
    own when it names that reason's entry in the failure guide or carries the
    installed guide's remedy text, and names no other entry. The run launches
    once and leaves no retained capture."""
    batch = route == "run-gates"
    host = lane_host(
        installed, tmp_path, primary=NEON, secondary=FABLE if batch else None
    )
    hosts.append(host)
    stream, chunked = failure_stream(reason)
    if chunked:
        write_prompt(host.root, filler_prompt(80_000, tag=True))
    host.scenario(stream)
    guide = installed_guide(host)

    if batch:
        envelope = host.run("run-gates", expected=FAILED)
        failed = member(envelope, "primary")
        reason_text = field_text(failed, REASON_FIELDS)
        remedy_text = field_text(failed, REMEDY_FIELDS)
    else:
        envelope = host.run("run-gate", SPEC_REVIEW, expected=FAILED)
        reason_text = envelope["error"].get("message") or ""
        remedy_text = envelope["error"].get("hint") or ""

    assert names(reason_text, reason), (
        f"FAIL AC-19: the output does not name {reason}: {reason_text}"
    )
    assert_matching_remedy(remedy_text, reason, guide)
    rows = [row for row in host.ledger() if row.get("cli") == "antigravity"]
    assert rows and rows[-1].get("failure_reason") == reason, (
        f"FAIL AC-11: recorded {rows[-1].get('failure_reason') if rows else None}"
    )
    assert host.launches() == 1, (
        f"FAIL AC-11: a hard failure launched agy {host.launches()} times"
    )
    assert lane_captures(host) == [], "FAIL AC-11: a hard failure left a capture"


# --------------------------------------------------------------------------- #
# Scenario 5: recovery before recording
# --------------------------------------------------------------------------- #
def test_killed_coordinator_recovers_published_capture_and_relaunches_the_first(
    installed, tmp_path, hosts
):
    """Scenario 5 (AC-12): the first member's ``agy`` holds; the second
    publishes its capture with no ledger row. Killing the coordinator's
    process group and restarting records the second from its capture with no
    provider call, relaunches the first, and records both in slot order:
    three physical launches in all. The held ``agy`` and its tokened child are
    shown running, with the launch's token, before the kill, and are gone
    after the restart."""
    host = lane_host(installed, tmp_path, primary=NEON, secondary=FABLE)
    hosts.append(host)
    host.scenario([init_event(), {"$children": ["daemon"]}, {"$hold": "release"}])

    coordinator = None
    try:
        coordinator = host.spawn("run-gates")
        host.wait_for("holding.jsonl")
        host.wait_for("children.jsonl")
        host.wait_for("claude.jsonl")
        (killed,) = host.observations()
        (child,) = host.rows("children.jsonl")
        assert alive(killed["pid"], killed["start"]), (
            "fixture: the held agy was not running before the kill"
        )
        assert_tokened_child(child, killed, "the held run's child")
        deadline = time.monotonic() + 120
        while not host.captures():
            assert time.monotonic() < deadline, "fixture: no capture was published"
            time.sleep(0.05)
        assert host.ledger() == [], (
            "FAIL AC-12: the second member recorded before the first, out of order"
        )
    finally:
        if coordinator is not None:
            if coordinator.poll() is None:
                os.killpg(coordinator.pid, signal.SIGKILL)
            coordinator.wait(timeout=60)

    host.scenario()
    envelope = host.run("run-gates")

    assert envelope["ok"], f"FAIL AC-12: the restart did not record: {envelope}"
    assert len(host.calls("claude")) == 1, (
        "FAIL AC-12: the published capture was not recorded without a provider call"
    )
    assert host.launches() == 2 and host.physical_launches() == 3, (
        f"FAIL AC-12: {host.physical_launches()} physical launches, expected three"
    )
    slots = [row.get("reviewer_slot") for row in host.ledger()]
    assert slots == ["primary", "secondary"], (
        f"FAIL AC-12: members recorded as {slots}, not in slot order"
    )
    for row in host.ledger():
        assert row["verdict"]["status"] != "error", row
    remember_tokens("scenario 5", host.observations())
    for pid, start, what in (
        (killed["pid"], killed["start"], "the killed agy"),
        (child["pid"], child["start"], "the killed run's tokened child"),
    ):
        assert not alive(pid, start), f"FAIL AC-12: {what} was not reaped"


# --------------------------------------------------------------------------- #
# Scenario 6: a marked heddle mutation from the reviewer child
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", list(LANES))
def test_marked_mutation_from_the_reviewer_child_is_refused(
    installed, tmp_path, hosts, lane
):
    """Scenario 6 (AC-1, AC-2): the reviewer child carries the marker, and its
    mutating ``heddle`` call on a disposable copy of the host exits 3 with
    ``gate-session-refused`` and changes nothing. Claude and Codex still
    record; the Antigravity run fails as ``out-of-scope-access`` with no
    credit."""
    host = lane_host(installed, tmp_path, primary=LANES[lane])
    hosts.append(host)
    copy = tmp_path / "mutation-copy"
    shutil.copytree(host.root, copy, symlinks=True)
    before = tree(copy)
    mutation = {
        "argv": [
            "commands",
            "set",
            "smoke_test",
            "--command",
            "echo reviewer-mutation",
            "--feature",
            FEATURE,
            "--json",
        ],
        "cwd": str(copy),
    }
    if lane == "antigravity":
        host.scenario(clean_stream({"$heddle": mutation}))
    else:
        host.lane(lane, steps=[{"heddle": mutation}])

    envelope = host.run(
        "run-gate", SPEC_REVIEW, expected=FAILED if lane == "antigravity" else (0,)
    )

    (attempt,) = host.rows("attempts.jsonl")
    assert attempt["cli"] == lane and attempt["marker"] == "gate", (
        f"FAIL AC-2: the reviewer child's environment lacks the marker: {attempt}"
    )
    assert (attempt["exit"], attempt["code"]) == (3, "gate-session-refused"), (
        f"FAIL AC-1: the marked mutation exited {attempt['exit']} ({attempt['code']})"
    )
    assert tree(copy) == before, "FAIL AC-1: the refused mutation changed the copy"
    (row,) = host.ledger()
    if lane == "antigravity":
        assert row.get("failure_reason") == OUT_OF_SCOPE, (
            f"FAIL AC-2: the attempt run recorded {row.get('failure_reason')}"
        )
        assert not envelope["ok"]
    else:
        assert envelope["ok"] and row["verdict"]["status"] != "error", (
            f"FAIL AC-2: the {lane} reviewer's result was not recorded: {envelope}"
        )


# --------------------------------------------------------------------------- #
# Scenario 7: a sibling reviews/ read
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", list(LANES))
def test_sibling_review_read_fails_on_every_lane(installed, tmp_path, hosts, lane):
    """Scenario 7 (AC-21): a reviewer that reads a sibling review under the
    feature's ``reviews/`` and returns a valid object fails as
    ``out-of-scope-access``, keeps no capture and earns no credit."""
    host = lane_host(installed, tmp_path, primary=LANES[lane])
    hosts.append(host)
    host.reviews.mkdir(exist_ok=True)
    sibling = host.reviews / "peer-review.md"
    sibling.write_text("# Sibling review\n", encoding="utf-8")
    if lane == "antigravity":
        host.scenario(clean_stream({"$read": str(sibling)}))
    else:
        host.lane(lane, steps=[{"read": str(sibling)}])

    envelope = host.run("run-gate", SPEC_REVIEW, expected=FAILED)

    assert not envelope["ok"]
    (row,) = host.ledger()
    assert row.get("failure_reason") == OUT_OF_SCOPE, (
        f"FAIL AC-21: the {lane} run recorded {row.get('failure_reason')}"
    )
    assert host.captures() == [], "FAIL AC-21: the refused run kept a capture"
    state = yaml.safe_load(host.state.read_text())
    (attempt,) = state["review_assignments"]["attempts"]
    assert attempt["interpretation"] is None and attempt["source"] is None, (
        "FAIL AC-21: the refused run left an interpretation or source credit"
    )


# --------------------------------------------------------------------------- #
# Scenario 8: coordinator reaping
# --------------------------------------------------------------------------- #
def test_coordinator_reaps_a_killed_members_tokens(installed, tmp_path, hosts):
    """Scenario 8 (AC-12): the first member's ``agy`` spawns a daemonised
    tokened child and holds; the child is shown running with the launch's
    token, and an unrelated process running, before the owner acts. Killing
    that member's worker, found through the double's recorded parent process
    ID, makes the running coordinator reap the child before any later gate
    run. The second member then records in slot order, and the unrelated
    process survives. Every process the test starts is stopped on any exit."""
    host = lane_host(installed, tmp_path, primary=NEON, secondary=FABLE)
    hosts.append(host)
    host.scenario([init_event(), {"$children": ["daemon"]}, {"$hold": "release"}])
    host.lane("claude", hold="release-second")
    unrelated = coordinator = None
    survived = None
    try:
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        coordinator = host.spawn("run-gates")
        host.wait_for("children.jsonl")
        host.wait_for("holding.jsonl", 2)
        (observation,) = host.observations()
        (child,) = host.rows("children.jsonl")
        assert_tokened_child(child, observation, "the first member's child")
        assert unrelated.poll() is None, "fixture: the unrelated process is not running"
        worker = observation["ppid"]
        assert worker != coordinator.pid, "fixture: agy's parent is the coordinator"
        os.kill(worker, signal.SIGKILL)
        assert wait_gone(child["pid"], child["start"]), (
            "FAIL AC-12: the coordinator did not reap the killed member's child"
        )
        assert coordinator.poll() is None, (
            "fixture: the coordinator ended before the child was observed gone"
        )
        host.release("release-second")
        stdout, _stderr = coordinator.communicate(timeout=300)
        survived = unrelated.poll() is None
    finally:
        if coordinator is not None and coordinator.poll() is None:
            os.killpg(coordinator.pid, signal.SIGKILL)
            coordinator.wait(timeout=60)
        if unrelated is not None:
            unrelated.kill()
            unrelated.wait()
    assert survived, "FAIL AC-12: the unrelated process was killed"
    assert stdout.strip(), "fixture: the coordinator printed no envelope"
    slots = [
        (row.get("reviewer_slot"), row["verdict"]["status"]) for row in host.ledger()
    ]
    order = [slot for slot, _ in slots]
    # The killed member may record an error row; if it does, it comes first.
    assert order in (["secondary"], ["primary", "secondary"]), (
        f"FAIL AC-12: members recorded as {slots}, not in slot order"
    )
    assert slots[-1][1] != "error", "FAIL AC-12: the second member did not record"
    assert not alive(observation["pid"], observation["start"]), (
        "FAIL AC-12: the killed member's agy was not reaped"
    )
    remember_tokens("scenario 8", host.observations())
