"""Antigravity runner acceptance: the doubled ``agy`` behind the real runner.

AC-2, AC-4, AC-6, AC-7, AC-9, AC-11, AC-12, AC-13, AC-18 and AC-22 through
the public typed boundary (``RunGate`` and ``RunGates``). An executable
``agy`` double is found on ``PATH``, as the Claude and Codex doubles are found;
preparation, the runner, recording and the ledger stay real.

Per physical launch the double records its argv, working directory, process
and parent IDs, the gate marker, SHA-256 digests of the environment values
its launcher added (never a value), stdin, the decoded user-message content,
the files the message points at and the schema it was given. It refuses a
wrapped message above 192,000 bytes and emits the synthetic NDJSON stream its
scenario names, in the shapes ``agy`` 1.5.16 emits. No local run evidence.

Red until the runner exists: every case first asserts the runner module.
Cases that need the stream audit also assert the audit module; they are the
real-stream runner-level cases that replace stub-fact cases, and turn green
one milestone after the runner.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from tests.tiering_helpers import FABLE, entry
from tests.tiering_review_helpers import V7_FEATURE, current_host, review_content

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = "heddle.gate.runners.antigravity"
AUDIT = "heddle.gate.runners.antigravity_audit"
NEON = {"cli": "antigravity", "model": "gemini-4-neon", "reasoning_effort": "high"}
TESTED_VERSION = "1.5.18"
OTHER_VERSION = "1.6.2"
INLINE_LIMIT = 160_000
CEILING = 250_000
WRAPPED_CAP = 192_000
WRAPPED_OVERHEAD = len(b"<USER_REQUEST>\n") + 400
READ_LINES = 800
READ_BYTES = 46_080
WORST_PREFIX = len(b"999999: ")
MARKER = "HEDDLE_AGENT_SESSION"
DATA_DIR = Path.home() / ".gemini" / "antigravity-cli"
CONVERSATION = "c0ffee00-0000-4000-8000-000000000001"
ROLES = ("spec-review", "milestone-review", "behavior-review", "complexity-review")
NEW_REASONS = (
    "antigravity-quota",
    "antigravity-api-error",
    "antigravity-context-compacted",
    "antigravity-unapproved-tool",
    "antigravity-incomplete-coverage",
    "out-of-scope-access",
)
MULTIBYTE = "aé€🚀"
ESCAPE_HEAVY = '"\\\t\n\f\v<>&\x85  '
WRAPPER_TAG = "</USER_REQUEST>"
SPEC = "spec-review"


# --------------------------------------------------------------------------- #
# Seams
# --------------------------------------------------------------------------- #
def require_runner(ac: str) -> None:
    assert importlib.util.find_spec(RUNNER) is not None, (
        f"FAIL {ac}: {RUNNER} is not implemented"
    )


def require_audit(ac: str) -> None:
    require_runner(ac)
    assert importlib.util.find_spec(AUDIT) is not None, (
        f"FAIL {ac}: {AUDIT} is not implemented, so no real stream is audited"
    )


def credential(tag: str = "a") -> str:
    """A GitHub-token-shaped synthetic value; source never holds a literal one."""
    return (
        "gh"
        + "p_"
        + hashlib.sha256(f"heddle-synthetic-{tag}".encode()).hexdigest()[:36]
    )


# --------------------------------------------------------------------------- #
# The doubled provider executables
# --------------------------------------------------------------------------- #
AGY_SOURCE = r'''
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[0]).resolve().parent.parent
REPO = __REPO__
CONVERSATION = __CONVERSATION__
CHILD = """
import os, signal, time
kind = __KIND__
if kind != "plain" and os.fork():
    os._exit(0)
if kind == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
path = __PIDFILE__
with open(path + ".tmp", "w") as handle:
    handle.write(str(os.getpid()))
os.replace(path + ".tmp", path)
time.sleep(30)
"""


def load(name, default):
    path = ROOT / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append(name, row):
    with open(ROOT / name, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def start_time(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        return int(text.rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def emit(value):
    line = value if isinstance(value, str) else json.dumps(value)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


SCENARIO = load("scenario.json", {})
argv = sys.argv[1:]
if argv == ["--version"]:
    version = SCENARIO.get("version", "1.5.18")
    append("versions.jsonl", {"pid": os.getpid(), "version": version})
    if version is None:
        sys.stderr.write("agy: version information is unavailable\n")
        sys.exit(2)
    print(version)
    sys.exit(0)


def flag(name):
    if name not in argv:
        return None
    index = argv.index(name)
    return argv[index + 1] if index + 1 < len(argv) else None


MODEL = flag("--model")
CWD = os.getcwd()
GEMINI = os.path.join(os.path.expanduser("~"), ".gemini")
raw = sys.stdin.buffer.read()
events, stdin_errors = [], []
for line in raw.splitlines():
    if line.strip():
        try:
            events.append(json.loads(line))
        except ValueError as error:
            stdin_errors.append(str(error))
texts = [
    event["message"]["content"]
    for event in events
    if isinstance(event, dict)
    and event.get("event") == "user"
    and isinstance(event.get("message"), dict)
    and isinstance(event["message"].get("content"), str)
]
wrapped = [15 + len(text.encode("utf-8")) + 400 for text in texts]
PATHLIKE = re.compile(r"[\w.@+~/-]*/[\w.@+~/-]+")
observed = {}
# Each absolute path token opened exactly as written (no normalization), as a
# reviewer's read_file call would, mapped to the real file it reached then.
opened = {}


def open_as_written(token):
    if not os.path.isabs(token) or token in opened:
        return
    if os.path.abspath(token).startswith(GEMINI):
        return
    try:
        real = os.path.realpath(token)
        if real.startswith(GEMINI) or not os.path.isfile(token):
            return
        with open(token, "rb") as handle:
            handle.read(1)
    except OSError:
        return
    opened[token] = real


def visit(text, base, depth):
    for token in PATHLIKE.findall(text):
        open_as_written(token.rstrip(".,:;"))
        token = os.path.expanduser(token.rstrip(".,:;"))
        options = [token] if os.path.isabs(token) else [
            os.path.join(CWD, token),
            os.path.join(base, token),
        ]
        for option in options:
            absolute = os.path.abspath(option)
            # Never touch agy's own data directory.
            if absolute.startswith(GEMINI):
                continue
            absolute = os.path.realpath(absolute)
            if absolute in observed or not os.path.isfile(absolute):
                continue
            if os.path.getsize(absolute) > 4_000_000:
                continue
            with open(absolute, "rb") as handle:
                data = handle.read()
            observed[absolute] = data
            if depth < 2:
                text = data.decode("utf-8", "replace")
                visit(text, os.path.dirname(absolute), depth + 1)


for text in texts:
    visit(text, CWD, 1)
digests = {path: hashlib.sha256(data).hexdigest() for path, data in observed.items()}
index_path, chunks = None, []
for path, data in observed.items():
    text = data.decode("utf-8", "replace")
    found, taken = [], {}
    for other, digest in sorted(digests.items()):
        if other == path or digest not in text:
            continue
        # Byte-identical files share a digest: each takes its own occurrence,
        # so identical chunks keep their places in the index's order.
        at = text.find(digest, taken.get(digest, -1) + 1)
        taken[digest] = at = at if at >= 0 else text.find(digest)
        found.append((at, other))
    found.sort()
    if len(found) > len(chunks):
        index_path, chunks = path, [other for _, other in found]
schema_path = flag("--json-schema")
schema_text = None
if schema_path and os.path.isfile(schema_path):
    with open(schema_path, encoding="utf-8") as handle:
        schema_text = handle.read()
try:
    ROLE = json.loads(schema_text)["properties"]["role"]["enum"][0]
except (TypeError, ValueError, KeyError, IndexError):
    ROLE = None
baseline = set(load("baseline-env.json", []))
append(
    "launches.jsonl",
    {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "start": start_time(os.getpid()),
        "argv": argv,
        "cwd": CWD,
        "marker": os.environ.get("HEDDLE_AGENT_SESSION"),
        "env": {
            name: hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()
            for name, value in os.environ.items()
            if name not in baseline
        },
        "role": ROLE,
        "stdin": base64.b64encode(raw).decode("ascii"),
        "stdin_errors": stdin_errors,
        "events": [event.get("event") for event in events if isinstance(event, dict)],
        "contents": texts,
        "wrapped": wrapped,
        "refused": any(size > 192_000 for size in wrapped),
        "files": {
            path: base64.b64encode(data).decode("ascii")
            for path, data in observed.items()
        },
        "index": index_path,
        "chunks": chunks,
        "opened": opened,
        "schema": schema_text,
    },
)
if any(size > 192_000 for size in wrapped):
    emit("error: the user message exceeds 192000 bytes")
    sys.exit(1)

reviews = SCENARIO.get("reviews", {})
plan = SCENARIO.get("roles", {}).get(ROLE) or SCENARIO.get("ops", [])
exit_code = 0
step_index = 0


def expand(value):
    if value == "$MODEL":
        return MODEL
    if value == "$CWD":
        return CWD
    if value == "$REVIEW":
        return reviews.get(ROLE)
    if isinstance(value, dict):
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    return value


def step(update):
    global step_index
    body = {"conversation_id": CONVERSATION, "step_index": step_index, "state": "DONE"}
    body.update(update)
    step_index += 1
    emit({"event": "step_update", "step_update": body})


def tool(name, parameters, output=None, **extra):
    info = {"name": name, "parameters": parameters}
    if output is not None:
        info["output"] = output
    step({"step_type": "tool", "tool_name": name, "tool_info": info, **extra})


def spawn(kind):
    pidfile = str(ROOT / f"child-{os.getpid()}-{kind}-{time.monotonic_ns()}.pid")
    code = (
        CHILD.replace("__KIND__", repr(kind)).replace("__PIDFILE__", repr(pidfile))
    )
    subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=kind != "plain",
        close_fds=True,
    )
    deadline = time.monotonic() + 10
    while not os.path.exists(pidfile):
        if time.monotonic() > deadline:
            raise SystemExit("a tokened child did not start")
        time.sleep(0.005)
    with open(pidfile) as handle:
        pid = int(handle.read())
    append(
        "children.jsonl",
        {
            "launch": os.getpid(),
            "role": ROLE,
            "kind": kind,
            "pid": pid,
            "start": start_time(pid),
        },
    )


def attempt(spec):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = REPO
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(
        [sys.executable, "-m", "heddle", *spec["argv"]],
        cwd=spec["copy"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        code = json.loads(done.stdout)["error"]["code"]
    except (ValueError, KeyError, TypeError):
        code = None
    append(
        "attempts.jsonl",
        {
            "launch": os.getpid(),
            "argv": spec["argv"],
            "exit": done.returncode,
            "code": code,
            "marker": os.environ.get("HEDDLE_AGENT_SESSION"),
        },
    )
    output = (
        "Created At: 2026-01-01T00:00:00Z\nCompleted At: 2026-01-01T00:00:01Z\n"
        f"Command exited with code {done.returncode}.\n\nCommand output:\n"
        + (done.stdout + done.stderr).replace("\n", "\r\n")
    )
    tool(
        "run_command",
        {"CommandLine": "heddle " + " ".join(spec["argv"]), "Cwd": spec["copy"]},
        output,
    )


for op in plan:
    name = next(iter(op)) if isinstance(op, dict) and len(op) == 1 else None
    if name is None or not name.startswith("$"):
        emit(expand(op))
        continue
    value = op[name]
    if name == "$step":
        step(expand(value))
    elif name == "$tool":
        tool(
            value["name"],
            expand(value["parameters"]),
            expand(value.get("output")),
            **expand(value.get("extra", {})),
        )
    elif name == "$read_chunks":
        targets = {"all": chunks, "all-but-last": chunks[:-1]}.get(value, [])
        for path in targets:
            data = observed[path]
            lines = data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
            tool("read_file", {"Path": path}, f"{lines} lines, {len(data)} bytes")
    elif name == "$raw":
        emit(value)
    elif name == "$oversize":
        filler = "x" * (1 << 20)
        for _ in range(value // (1 << 20) + 1):
            emit(filler)
    elif name == "$children":
        for kind in value:
            spawn(kind)
    elif name == "$hold":
        append("holding.jsonl", {"launch": os.getpid(), "role": ROLE})
        deadline = time.monotonic() + 60
        while not (ROOT / value).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    elif name == "$heddle":
        attempt(value)
    elif name == "$alter_chunk":
        # Change one ASCII letter's case in a delivered chunk after it was read.
        target = chunks[value]
        os.chmod(target, os.stat(target).st_mode | 0o200)
        with open(target, "rb") as handle:
            data = bytearray(handle.read())
        position = next(i for i, byte in enumerate(data) if 97 <= byte <= 122)
        data[position] -= 32
        with open(target, "wb") as handle:
            handle.write(bytes(data))
        append("altered.jsonl", {"launch": os.getpid(), "chunk": value, "path": target})
    elif name == "$write":
        target = Path(CWD, value["path"])
        text = target.read_text(encoding="utf-8")
        assert text.count(value["old"]) == 1, "double: the write target is ambiguous"
        target.write_text(text.replace(value["old"], value["new"]), encoding="utf-8")
        append("writes.jsonl", {"launch": os.getpid(), "path": str(target)})
        parameters = {
            "TargetFile": str(target),
            "TargetContent": value["old"],
            "ReplacementContent": value["new"],
            "AllowMultiple": False,
        }
        tool("replace_file_content", parameters, "Edited 1 occurrence.")
    elif name == "$exit":
        exit_code = value
    elif name == "$result":
        body = {
            "conversation_id": CONVERSATION,
            "status": "SUCCESS",
            "response": "",
            "duration_seconds": 1.0,
            "num_turns": 1,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 200,
                "thinking_tokens": 100,
                "cache_read_tokens": 0,
                "total_tokens": 1300,
            },
        }
        body.update(expand(value))
        emit({"event": "result", "result": body})
sys.exit(exit_code)
'''

CLAUDE_SOURCE = r"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(sys.argv[0]).resolve().parent.parent
argv = sys.argv[1:]
sys.stdin.read()
try:
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    role = schema["properties"]["role"]["enum"][0]
except (ValueError, KeyError, IndexError, TypeError):
    role = "spec-review"
with open(ROOT / "claude.jsonl", "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"pid": os.getpid(), "role": role}) + "\n")
review = json.loads((ROOT / "reviews.json").read_text(encoding="utf-8"))[role]
print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "structured_output": review,
}))
"""


@dataclass
class Agy:
    """The doubled ``agy`` (and a Claude sibling) with its observation logs."""

    root: Path
    reviews: dict

    def scenario(
        self,
        ops: list | None = None,
        *,
        roles: dict[str, list] | None = None,
        version: str | None = TESTED_VERSION,
        reviews: dict | None = None,
    ) -> None:
        value = {
            "ops": clean_stream() if ops is None else ops,
            "roles": roles or {},
            "version": version,
            "reviews": reviews or self.reviews,
        }
        (self.root / "scenario.json").write_text(json.dumps(value), encoding="utf-8")

    def rows(self, name: str) -> list[dict]:
        path = self.root / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def launches(self) -> list[dict]:
        return self.rows("launches.jsonl")

    def claude_calls(self) -> list[dict]:
        return self.rows("claude.jsonl")

    def children(self) -> list[dict]:
        return self.rows("children.jsonl")

    def release(self, name: str = "release") -> None:
        (self.root / name).touch()

    def prepared(self) -> list[dict]:
        return self.rows("prepared.jsonl")

    def wait_for(self, name: str, count: int = 1, timeout: float = 20.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while len(self.rows(name)) < count:
            assert time.monotonic() < deadline, f"fixture: {name} never reached {count}"
            time.sleep(0.01)
        return self.rows(name)


def _executable(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(0o755)


def fast_gate_args(monkeypatch, *, timeout: float = 20, inactivity: float = 20) -> None:
    """Short monitor polling for every gate the run builds (fork workers inherit)."""
    from heddle.gate import cli

    original = cli.GateArgs

    def fast(*args, **kwargs):
        value = original(*args, **kwargs)
        value.poll_seconds = 0.02
        value.term_grace_seconds = 0.2
        value.timeout_seconds = timeout
        value.inactivity_timeout_seconds = inactivity
        return value

    monkeypatch.setattr(cli, "GateArgs", fast)


def frozen_route(prepared) -> dict | None:
    """The frozen route preparation sets, as plain data (fork-safe).

    The only reader of the route seam: ``PreparedGateRun.antigravity_route``
    with ``.kind``, ``.size``, ``.ceiling`` and ``.chunks`` (each ``.start``,
    ``.end``, ``.sha256``), so a rename is one edit here.
    """
    route = getattr(prepared, "antigravity_route", None)
    if route is None:
        return None
    return {
        "kind": route.kind,
        "size": route.size,
        "ceiling": route.ceiling,
        "chunks": [[chunk.start, chunk.end, chunk.sha256] for chunk in route.chunks],
    }


def observe_prepared(monkeypatch, root: Path) -> None:
    """Record each prepared run's frozen capture at the engine seam, fork-safe."""
    from heddle.gate import entry as gate_entry

    original = gate_entry.run_gate_for_runtime

    def observed(gate_type, context, **kwargs):
        prepared = context.prepared_run
        contract = prepared.output_contract
        row = {
            "gate": gate_type.name,
            "cli": prepared.invocation.exec_config.cli,
            "slot": prepared.reviewer_slot,
            "assignment": prepared.assignment_id,
            "execution": {
                "cli": prepared.invocation.exec_config.cli,
                "model": prepared.invocation.exec_config.model,
                "reasoning_effort": prepared.invocation.exec_config.reasoning_effort,
            },
            "input_hash": prepared.input_hash,
            "review_basis_hash": prepared.review_basis_hash,
            "capture": prepared.transport.stdin,
            "system": prepared.transport.system,
            "route": frozen_route(prepared),
            "schema": contract.schema_json if contract is not None else None,
        }
        with (root / "prepared.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        return original(gate_type, context, **kwargs)

    monkeypatch.setattr(gate_entry, "run_gate_for_runtime", observed)


REAPER = ("heddle.io.process", "reap_token")


def observe_reaping(monkeypatch, root: Path) -> None:
    """Record the SHA-256 of every token handed to the reaper, never the token.

    The reaper is the plan's ``reap_token(token)`` in ``heddle.io.process``
    (named in ``REAPER``, the only reference). Modules that imported it by
    name are patched too. Absent today, so nothing is recorded yet.
    """
    import importlib

    module_name, attribute = REAPER
    module = importlib.import_module(module_name)
    original = getattr(module, attribute, None)
    if original is None:
        return

    def observed(token, *args, **kwargs):
        value = token.decode() if isinstance(token, bytes) else str(token)
        digest = hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()
        with (root / "reaps.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "sha256": digest}) + "\n")
        return original(token, *args, **kwargs)

    for name, loaded in list(sys.modules.items()):
        if name.startswith("heddle") and getattr(loaded, attribute, None) is original:
            monkeypatch.setattr(loaded, attribute, observed)


def install_agy(tmp_path: Path, monkeypatch, **gate_args) -> Agy:
    """Put the doubled ``agy`` and ``claude`` first on PATH; record the baseline env."""
    root = tmp_path / "agy-double"
    (root / "bin").mkdir(parents=True)
    source = AGY_SOURCE.replace("__REPO__", repr(str(REPO_ROOT))).replace(
        "__CONVERSATION__", repr(CONVERSATION)
    )
    _executable(root / "bin" / "agy", source)
    _executable(root / "bin" / "claude", CLAUDE_SOURCE)
    reviews = {role: review_content(role) for role in ROLES}
    (root / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    monkeypatch.setenv("PATH", f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(MARKER, raising=False)
    (root / "baseline-env.json").write_text(json.dumps(sorted(os.environ)))
    fast_gate_args(monkeypatch, **gate_args)
    observe_prepared(monkeypatch, root)
    observe_reaping(monkeypatch, root)
    agy = Agy(root, reviews)
    agy.scenario()
    return agy


# --------------------------------------------------------------------------- #
# Synthetic streams
# --------------------------------------------------------------------------- #
TOOLS = [
    "read_file",
    "grep_search",
    "find_by_name",
    "list_dir",
    "run_command",
    "send_command_input",
    "manage_task",
]
ABSENT = object()
READ_ALL = {"$read_chunks": "all"}
READ_ALL_BUT_LAST = {"$read_chunks": "all-but-last"}


def init_event(model: object = "$MODEL") -> dict:
    body = {
        "model": model,
        "cwd": "$CWD",
        "tools": TOOLS,
        "permission_mode": "always-proceed",
    }
    if model is ABSENT:
        body.pop("model")
    return {"event": "init", "conversation_id": CONVERSATION, "init": body}


def result_event(**fields) -> dict:
    body: dict = {"structured_output": "$REVIEW", "response": "Review complete."}
    body.update(fields)
    return {
        "$result": {key: value for key, value in body.items() if value is not ABSENT}
    }


def tool_step(name: str, parameters: dict, output: str | None = None, **extra) -> dict:
    spec: dict = {"name": name, "parameters": parameters}
    if output is not None:
        spec["output"] = output
    if extra:
        spec["extra"] = extra
    return {"$tool": spec}


def clean_stream(*middle, result: dict | None = None, head=None, reads=READ_ALL):
    return [
        *(head if head is not None else [init_event()]),
        {"$step": {"step_type": "user_input"}},
        reads,
        *middle,
        {"$step": {"step_type": "finish"}},
        result if result is not None else result_event(),
    ]


def shell_output(text: str, code: int = 0) -> str:
    return (
        "Created At: 2026-01-01T00:00:00Z\nCompleted At: 2026-01-01T00:00:01Z\n"
        f"Command exited with code {code}.\n\nCommand output:\n"
        + text.replace("\n", "\r\n")
    )


# --------------------------------------------------------------------------- #
# Hosts and runs
# --------------------------------------------------------------------------- #
def spec_host(tmp_path, monkeypatch, *, secondary=None, prompt: str | None = None):
    """A spec-review host whose primary slot is the Antigravity reviewer."""
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="spec-review",
        overrides={"spec-review": entry(SPEC, primary=NEON, secondary=secondary)},
    )
    if prompt is not None:
        write_prompt(host, prompt)
    return host, state_path


def write_prompt(host: Path, text: str, gate: str = SPEC) -> None:
    path = host / "prompts" / f"{gate}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def chunked_prompt(size: int = 2_000) -> str:
    """A small host prompt carrying a wrapper tag, so delivery must be chunked."""
    line = "Review every declared value against the specification.\n"
    return f"# Review\n\nQuote {WRAPPER_TAG} literally if it appears.\n\n" + line * max(
        1, size // len(line)
    )


def run_gate(gate: str = SPEC, reviewer: dict | None = None):
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    return execute(ops.RunGate(gate, feature=V7_FEATURE, **(reviewer or NEON)))


def run_batch():
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    return execute(ops.RunGates(feature=V7_FEATURE))


def gate_rows(state_path: Path) -> list[dict]:
    return [
        {**run, "gate": gate["gate"]}
        for gate in yaml.safe_load(state_path.read_text())["gates"]
        for run in gate["runs"]
    ]


def lane_rows(state_path: Path, cli: str = "antigravity") -> list[dict]:
    return [row for row in gate_rows(state_path) if row.get("cli") == cli]


def summary_for(state_path: Path, gate: str = SPEC) -> dict:
    found = []
    for path in sorted((state_path.parent / "reviews").glob("*.gate-summary.json")):
        # Skip rotated summaries and the published review view's summary.
        if ".prev." in path.name or path.name.endswith(".review.gate-summary.json"):
            continue
        value = json.loads(path.read_text())
        if value.get("gate") == gate and value.get("cli") == "antigravity":
            found.append(value)
    assert len(found) == 1, f"fixture: expected one antigravity {gate} summary"
    return found[0]


def gate_log(state_path: Path, gate: str = SPEC) -> str:
    return Path(summary_for(state_path, gate)["io"]["log"]).read_text()


def captures(state_path: Path) -> list[Path]:
    return sorted((state_path.parent / "reviews").glob("*.capture.json"))


def flag_value(argv: list[str], name: str) -> str | None:
    if name not in argv:
        return None
    index = argv.index(name)
    return argv[index + 1] if index + 1 < len(argv) else None


def temp_dir_of(launch: dict) -> Path:
    schema = flag_value(launch["argv"], "--json-schema")
    assert schema, "FAIL AC-9: the launch names no --json-schema file"
    return Path(schema).parent


def files_of(launch: dict) -> dict[str, bytes]:
    return {path: base64.b64decode(data) for path, data in launch["files"].items()}


def _prepared_row(agy: Agy, gate: str) -> dict:
    rows = [
        row
        for row in agy.prepared()
        if row["gate"] == gate and row["cli"] == "antigravity"
    ]
    assert rows, "fixture: no prepared antigravity run reached the engine seam"
    return rows[-1]


def capture_of(agy: Agy, gate: str = SPEC) -> bytes:
    """The frozen capture: ``transport.stdin``, with no system part."""
    row = _prepared_row(agy, gate)
    assert row["system"] is None, "FAIL AC-6: the frozen transport has a system part"
    return row["capture"].encode("utf-8")


def route_of(agy: Agy, kind: str, ac: str, gate: str = SPEC) -> dict:
    """The frozen route, checked against the frozen capture's UTF-8 bytes."""
    row = _prepared_row(agy, gate)
    route = row["route"]
    assert route is not None, f"FAIL {ac}: preparation froze no antigravity route"
    capture = row["capture"].encode("utf-8")
    assert route["kind"] == kind, f"FAIL {ac}: route {route['kind']}, expected {kind}"
    assert route["size"] == len(capture) and route["ceiling"] == CEILING, (
        f"FAIL {ac}: the route's size or ceiling differs from the frozen capture"
    )
    if kind != "chunked":
        assert list(route["chunks"]) == [], f"FAIL {ac}: an inline route has chunks"
        return route
    ranges = [(start, end) for start, end, _digest in route["chunks"]]
    assert ranges and ranges[0][0] == 0 and ranges[-1][1] == len(capture), (
        f"FAIL {ac}: the route's chunks do not span the capture"
    )
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:], strict=False)), (
        f"FAIL {ac}: the route's chunks are not contiguous"
    )
    for start, end, digest in route["chunks"]:
        assert hashlib.sha256(capture[start:end]).hexdigest() == digest, (
            f"FAIL {ac}: chunk [{start}, {end}) digest differs from its slice"
        )
    return route


# --------------------------------------------------------------------------- #
# Processes
# --------------------------------------------------------------------------- #
def _start_time(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = text.rsplit(")", 1)[1].split()
    return None if fields[0] == "Z" else int(fields[19])


def alive(pid: int, start: int | None) -> bool:
    """The process with this ID and start time exists and is not a zombie."""
    current = _start_time(pid)
    return current is not None and (start is None or current == start)


class Processes:
    """An unrelated, untokened process, and teardown of anything left running."""

    def __init__(self, agy: Agy) -> None:
        self.agy = agy
        self.unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.unrelated_start = None
        deadline = time.monotonic() + 5
        while self.unrelated_start is None and time.monotonic() < deadline:
            self.unrelated_start = _start_time(self.unrelated.pid)

    def unrelated_alive(self) -> bool:
        return self.unrelated.poll() is None and alive(
            self.unrelated.pid, self.unrelated_start
        )

    def cleanup(self, kill: Callable[[int, int], None] = os.kill) -> None:
        for child in self.agy.children():
            if alive(child["pid"], child["start"]):
                try:
                    kill(child["pid"], signal.SIGKILL)
                except OSError:
                    pass
        self.unrelated.kill()
        self.unrelated.wait()


@pytest.fixture
def agy(tmp_path, monkeypatch) -> Agy:
    return install_agy(tmp_path, monkeypatch)


@pytest.fixture
def processes(agy):
    tracker = Processes(agy)
    yield tracker
    tracker.cleanup()


# --------------------------------------------------------------------------- #
# Assertions shared by the outcome rows
# --------------------------------------------------------------------------- #
def assert_hard_failure(state_path: Path, agy: Agy, reason: str, ac: str) -> None:
    """One launch, the reason recorded, no capture, no retry, no other lane."""
    rows = lane_rows(state_path)
    assert rows, f"FAIL {ac}: no antigravity run was recorded"
    assert rows[-1].get("failure_reason") == reason, (
        f"FAIL {ac}: expected {reason}, recorded "
        f"{rows[-1].get('failure_reason')} ({rows[-1]['verdict']['status']})"
    )
    assert rows[-1]["verdict"]["status"] == "error"
    assert len(agy.launches()) == 1, (
        f"FAIL {ac}: a hard failure launched agy {len(agy.launches())} times "
        "(a retry or a fallback)"
    )
    assert captures(state_path) == [], (
        f"FAIL {ac}: a hard failure left an interpretable capture"
    )
    attempts = yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"]
    assert "usable-capture" not in [row["outcome"]["kind"] for row in attempts], (
        f"FAIL {ac}: a hard failure was offered for interpretation"
    )


def assert_recorded(state_path: Path, ac: str) -> dict:
    rows = lane_rows(state_path)
    assert rows, f"FAIL {ac}: the clean antigravity launch did not record"
    row = rows[-1]
    assert row["verdict"]["status"] != "error", (
        f"FAIL {ac}: the clean launch failed as {row.get('failure_reason')}"
    )
    execution = row.get("execution_config") or {}
    recorded = {key: execution.get(key) for key in NEON}
    assert row["cli"] == NEON["cli"] and recorded == NEON, (
        f"FAIL AC-4: the ledger row records {row['cli']} {recorded}"
    )
    return row


def assert_reads_back_as_antigravity(
    state_path: Path, gate: str = SPEC, cli: str = "antigravity"
) -> None:
    """AC-4: the recorded run reads back through the normal decoders, so the
    frozen reviewer slot and the recorded result both carry the lane."""
    from heddle.contracts import operations as ops
    from heddle.gate.results import decode_review_result
    from heddle.kernel.review_assignments import parse_assignments
    from heddle.runtime.application import execute

    state = yaml.safe_load(state_path.read_text())
    try:
        assignments = parse_assignments(state["review_assignments"])
    except Exception as error:  # the decoder's own refusal is the finding
        raise AssertionError(
            "FAIL AC-4: the frozen-slot decoder (parse_assignments in "
            f"heddle.kernel.review_assignments) refused the antigravity slot: {error}"
        ) from error
    clis = {
        slot.reviewer.cli
        for assignment in assignments.assignments
        if assignment.role == gate
        for round_ in assignment.rounds
        for slot in round_.slots
    }
    assert cli in clis, (
        f"FAIL AC-4: the frozen-slot decoder read the {gate} slots as {clis}"
    )
    row = lane_rows(state_path, cli)[-1]
    raw = (state_path.parent / row["artifact"]).read_bytes()
    try:
        result = decode_review_result(raw)
    except Exception as error:  # the decoder's own refusal is the finding
        raise AssertionError(
            "FAIL AC-4: the result decoder (decode_review_result in "
            f"heddle.gate.results) refused execution.cli antigravity: {error}"
        ) from error
    assert result.invocation.execution.cli == cli, (
        "FAIL AC-4: the result decoder read execution.cli as "
        f"{result.invocation.execution.cli}"
    )
    status = execute(ops.Status(feature=V7_FEATURE))
    assert status.ok, (
        "FAIL AC-4: status could not read the recorded antigravity run back "
        f"(frozen-slot and result decoders): {status.to_envelope()}"
    )


def batch_member(result, slot: str) -> dict:
    members = (
        result.data["members"] if result.ok else result.error.details.get("members")
    )
    assert members, f"fixture: the batch ran no members: {result.to_envelope()}"
    found = [row for row in members if row["reviewer_slot"] == slot]
    assert len(found) == 1
    return found[0]


def resolve_refs(schema: dict) -> dict:
    """The canonical schema with every local ``$ref`` inlined and ``$defs`` dropped."""
    definitions = schema.get("$defs", {})

    def walk(value, depth=0):
        assert depth < 64, "fixture: the canonical schema refers to itself"
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                target = walk(
                    definitions[reference.removeprefix("#/$defs/")], depth + 1
                )
                siblings = {
                    k: walk(v, depth + 1) for k, v in value.items() if k != "$ref"
                }
                return {**target, **siblings}
            return {k: walk(v, depth + 1) for k, v in value.items() if k != "$defs"}
        if isinstance(value, list):
            return [walk(item, depth + 1) for item in value]
        return value

    return walk(schema)


def chunk_files(launch: dict) -> list[bytes]:
    files = files_of(launch)
    return [files[path] for path in launch["chunks"]]


HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
INDEX_ENTRY_KEYS = frozenset({"path", "start", "end", "sha256"})


def index_records(index: bytes, ac: str = "AC-7") -> list[dict]:
    """The delivered index read strictly under the plan's fixed format: one
    JSON object ``{"chunks": [...]}`` whose entries are exactly
    ``{"path", "start", "end", "sha256"}`` (an absolute path string, integer
    byte offsets, a lowercase hex SHA-256). Returns the entries in index order;
    any other shape fails. The one index reader for the runner and installed
    witnesses."""
    try:
        value = json.loads(index.decode("utf-8"))
    except ValueError as error:
        raise AssertionError(
            f"FAIL {ac}: the index is not one JSON object: {error}"
        ) from None
    assert isinstance(value, dict) and set(value) == {"chunks"}, (
        f"FAIL {ac}: the index is not one {{'chunks': [...]}} object: "
        f"keys {sorted(value) if isinstance(value, dict) else type(value).__name__}"
    )
    entries = value["chunks"]
    assert isinstance(entries, list), f"FAIL {ac}: the index's chunks is not a list"
    for number, record in enumerate(entries):
        assert isinstance(record, dict) and set(record) == INDEX_ENTRY_KEYS, (
            f"FAIL {ac}: index entry {number} has keys "
            f"{sorted(record) if isinstance(record, dict) else record!r}, not exactly "
            f"{sorted(INDEX_ENTRY_KEYS)}"
        )
        assert isinstance(record["path"], str) and os.path.isabs(record["path"]), (
            f"FAIL {ac}: index entry {number} path {record['path']!r} is not absolute"
        )
        for key in ("start", "end"):
            assert type(record[key]) is int, (
                f"FAIL {ac}: index entry {number} {key} {record[key]!r} "
                "is not an integer"
            )
        assert isinstance(record["sha256"], str) and HEX_DIGEST.fullmatch(
            record["sha256"]
        ), f"FAIL {ac}: index entry {number} sha256 {record['sha256']!r} is not hex"
    return entries


def assert_index_lists_chunks(
    index: bytes,
    opened: dict[str, tuple[str, bytes]],
    capture: bytes,
    slices: list[tuple[int, int]],
    ac: str = "AC-7",
    *,
    directory: Path,
) -> None:
    """AC-7 under the plan's fixed index format. ``slices`` are the chunks'
    half-open capture ranges in capture order, computed independently of the
    index. ``opened`` maps each absolute path the reviewer opened exactly as
    written (no normalization) to ``(real path reached at open, bytes read)``.

    The index lists exactly ``len(slices)`` chunks, and entry ``n``'s path, as
    written: is absolute; opened as written and yielded exactly slice ``n``'s
    bytes; resolved inside the run temp ``directory``. Any spelling that opens
    as written is accepted.
    The entry states exactly that slice's ``start`` and exclusive ``end`` and
    carries that slice's SHA-256. No two entries reach the same file."""
    entries = index_records(index, ac)
    assert len(entries) == len(slices), (
        f"FAIL {ac}: the index lists {len(entries)} chunks; the capture has "
        f"{len(slices)}"
    )
    inside = os.path.realpath(directory)
    reached = []
    for number, (record, (first, last)) in enumerate(zip(entries, slices, strict=True)):
        piece = capture[first:last]
        path = record["path"]
        assert path in opened, (
            f"FAIL {ac}: index entry {number} path {path!r} does not open as written"
        )
        real, data = opened[path]
        assert Path(real).is_relative_to(inside), (
            f"FAIL {ac}: index entry {number} path {path!r} resolves to {real}, "
            f"outside the run temp directory {directory}"
        )
        assert data == piece, (
            f"FAIL {ac}: index entry {number} path {path!r} does not yield chunk "
            f"{number} (capture[{first}:{last}])"
        )
        assert (record["start"], record["end"]) == (first, last), (
            f"FAIL {ac}: index entry {number} states [{record['start']}, "
            f"{record['end']}); chunk {number} is the half-open [{first}, {last})"
        )
        digest = hashlib.sha256(piece).hexdigest()
        assert record["sha256"] == digest, (
            f"FAIL {ac}: index entry {number} carries sha256 {record['sha256']}, not "
            f"chunk {number}'s {digest}"
        )
        reached.append(real)
    assert len(set(reached)) == len(reached), (
        f"FAIL {ac}: index entries reach the same file twice: {reached}"
    )


def opened_as_written(launch: dict) -> dict[str, tuple[str, bytes]]:
    """The double's exact-path opens, each with the real file it reached and
    that file's bytes as observed at launch."""
    files = files_of(launch)
    return {
        path: (real, files[real])
        for path, real in launch["opened"].items()
        if real in files
    }


def assert_index_binds_each_chunk(
    launch: dict, capture: bytes, ac: str, route: dict
) -> None:
    """AC-7: the delivered index lists the frozen route's chunks in capture
    order, each by an absolute path that opens exactly as written to
    its delivered file in the run temp directory, with its exact half-open
    range and digest."""
    assert_index_lists_chunks(
        files_of(launch)[launch["index"]],
        opened_as_written(launch),
        capture,
        [(first, last) for first, last, _digest in route["chunks"]],
        ac,
        directory=temp_dir_of(launch),
    )


def assert_chunked_delivery(launch: dict, capture: bytes, ac: str, route: dict) -> None:
    """The index lists the frozen route's chunks under the plan's fixed format,
    and the written chunks, in index order, are the frozen route's slices of
    the frozen capture."""
    assert launch["index"] is not None, (
        f"FAIL {ac}: the launch message points at no index binding chunk digests"
    )
    assert_index_binds_each_chunk(launch, capture, ac, route)
    chunks = chunk_files(launch)
    assert chunks, f"FAIL {ac}: no chunk file was observed"
    assert b"".join(chunks) == capture, (
        f"FAIL {ac}: the written chunks do not reassemble the frozen capture"
    )
    assert [capture[start:end] for start, end, _ in route["chunks"]] == chunks, (
        f"FAIL {ac}: the written chunks are not the frozen route's slices"
    )
    for number, piece in enumerate(chunks):
        piece.decode("utf-8")
        lines = piece.count(b"\n") + (0 if piece.endswith(b"\n") else 1)
        assert lines <= READ_LINES, f"FAIL {ac}: chunk {number} has {lines} lines"
        assert len(piece) + lines * WORST_PREFIX <= READ_BYTES, (
            f"FAIL {ac}: chunk {number} does not fit one read_file call"
        )
    (message,) = launch["contents"]
    assert len(message.encode("utf-8")) < 8_192 and MULTIBYTE * 4 not in message, (
        f"FAIL {ac}: the NDJSON message carries capture text, not the launch pointer"
    )


# --------------------------------------------------------------------------- #
# AC-9, AC-4, AC-11 success, AC-2 clean launch, AC-6 inline: one clean launch
# --------------------------------------------------------------------------- #
def test_ac9_ac4_ac11_clean_launch_is_exact_and_records_in_process(
    tmp_path, monkeypatch, agy
):
    """AC-9 launch observation; AC-4 recorded identity, read back through the
    frozen-slot and result decoders; AC-11 success row; AC-2's clean
    Antigravity launch records in process; AC-6 small inline."""
    require_runner("AC-9")
    host, state_path = spec_host(tmp_path, monkeypatch)
    result = run_gate()
    assert result.ok, f"FAIL AC-11: the clean launch failed: {result.to_envelope()}"
    assert_recorded(state_path, "AC-11")
    assert_reads_back_as_antigravity(state_path)
    (launch,) = agy.launches()
    argv = launch["argv"]
    assert Path(launch["cwd"]).resolve() == host.resolve(), (
        f"FAIL AC-9: agy ran in {launch['cwd']}, not the checkout root"
    )
    assert flag_value(argv, "--input-format") == "stream-json"
    assert flag_value(argv, "--output-format") == "stream-json"
    assert flag_value(argv, "--model") == NEON["model"], f"FAIL AC-9: argv {argv}"
    assert flag_value(argv, "--effort") == NEON["reasoning_effort"], (
        f"FAIL AC-9: model and effort are not separate values: {argv}"
    )
    assert "--dangerously-skip-permissions" in argv, "FAIL AC-9: not full permissions"
    assert "--disable-slash-commands" in argv, "FAIL AC-9: slash commands enabled"
    assert not any(arg.startswith("--log-file") for arg in argv), (
        "FAIL AC-9: agy was given --log-file"
    )
    settings = json.loads(flag_value(argv, "--settings") or "{}")
    rules = settings.get("permissions", {}).get("deny", [])
    assert len(rules) == 1, f"FAIL AC-9: expected one deny rule, got {rules}"
    denied = re.fullmatch(r"read_file\((.+)\)", rules[0])
    assert denied and os.path.normpath(denied.group(1)) == str(DATA_DIR), (
        f"FAIL AC-9: the deny rule is not read_file on agy's data dir: {rules}"
    )
    schema = launch["schema"]
    assert schema is not None, "FAIL AC-9: the --json-schema file was not readable"
    assert "$ref" not in schema and "$defs" not in schema, "FAIL AC-9: $ref in schema"
    canonical = json.loads(agy.prepared()[-1]["schema"])
    assert json.loads(schema) == resolve_refs(canonical), (
        "FAIL AC-9: the delivered schema is not the canonical contract inlined"
    )
    assert launch["marker"] == "gate", f"FAIL AC-9: marker {launch['marker']!r}"
    tokens = {name for name in launch["env"] if name != MARKER}
    assert tokens, "FAIL AC-9: the launch environment carries no launch token"
    assert not launch["refused"] and launch["stdin_errors"] == []
    assert launch["events"] == ["user"], f"FAIL AC-6: stdin events {launch['events']}"
    assert launch["contents"] == [capture_of(agy).decode("utf-8")], (
        "FAIL AC-6: the decoded NDJSON content is not the frozen capture"
    )
    route_of(agy, "inline", "AC-6")


def test_ac4_run_gate_naming_only_the_cli_launches_and_records_neon_high(
    tmp_path, monkeypatch, agy
):
    """AC-4 no-model case at the public boundary: the per-CLI default table
    gives the lane ``gemini-4-neon`` at ``high``, and a ``run-gate`` naming only
    ``--cli antigravity`` (no model, no effort) launches the doubled ``agy``
    with those values and persists them in the run's execution record."""
    require_runner("AC-4")
    from heddle.gate.summary import resolve_exec_config

    default = resolve_exec_config("antigravity", gate=SPEC)
    assert (default["model"], default["reasoning_effort"]) == (
        NEON["model"],
        NEON["reasoning_effort"],
    ), f"FAIL AC-4: the per-CLI default table gives the lane {default}"
    _host, state_path = spec_host(tmp_path, monkeypatch)
    result = run_gate(SPEC, {"cli": "antigravity"})
    assert result.ok, (
        f"FAIL AC-4: --cli antigravity alone failed: {result.to_envelope()}"
    )
    (launch,) = agy.launches()
    argv = launch["argv"]
    assert (flag_value(argv, "--model"), flag_value(argv, "--effort")) == (
        NEON["model"],
        NEON["reasoning_effort"],
    ), f"FAIL AC-4: the no-model run launched agy with {argv}"
    assert _prepared_row(agy, SPEC)["execution"] == NEON, (
        "FAIL AC-4: the prepared execution is not gemini-4-neon at high"
    )
    assert_recorded(state_path, "AC-4")
    assert_reads_back_as_antigravity(state_path)


def test_ac9_each_physical_launch_carries_a_fresh_token(tmp_path, monkeypatch, agy):
    """AC-9: two physical launches of one slot each carry a fresh launch token.
    Each launch's token is bound to that launch alone: it is the value the
    runner handed its reaper while that launch's run completed (observed before
    the next launch starts; digests only), present in that launch's
    environment, never in its argv; the two tokens differ."""
    require_runner("AC-9")
    _host, state_path = spec_host(tmp_path, monkeypatch)

    def token_of(launch: dict, reaped: set[str], which: str) -> str:
        assert reaped, f"FAIL AC-9: the {which} run handed no token to its reaper"
        carried = {
            digest for name, digest in launch["env"].items() if name != MARKER
        } & reaped
        assert len(carried) == 1, (
            f"FAIL AC-9: expected exactly one environment value of the {which} "
            f"launch to be a token its own run gave the reaper; found {len(carried)}"
        )
        argv_digests = {
            hashlib.sha256(arg.encode()).hexdigest() for arg in launch["argv"]
        }
        assert not argv_digests & carried, (
            f"FAIL AC-9: the {which} launch token is in argv"
        )
        return carried.pop()

    agy.scenario(clean_stream() + [{"$exit": 1}])
    first = run_gate()
    assert not first.ok
    (one,) = agy.launches()
    seen = len(agy.rows("reaps.jsonl"))
    token_one = token_of(
        one, {row["sha256"] for row in agy.rows("reaps.jsonl")}, "first"
    )
    agy.scenario()
    second = run_gate()
    assert second.ok, second.to_envelope()
    launched_first, two = agy.launches()
    assert launched_first == one, "fixture: the first launch record changed"
    token_two = token_of(
        two, {row["sha256"] for row in agy.rows("reaps.jsonl")[seen:]}, "second"
    )
    assert token_one != token_two, "FAIL AC-9: both launches carried the same token"


ALTERED = ("chunk", "index", "launch")
# Where a transport file is altered: right after the runner writes the
# transport, or right after it records the launch token, the last bookkeeping
# step before the launch. ``alter_during_monitor_setup`` alters one inside the
# shared monitor.
WRITE_SEAM = "write_transport"
RECORD_SEAM = "write_launch_record"


def alter_after_write(
    monkeypatch, state_path: Path, target: str, record: Path, seam: str = WRITE_SEAM
) -> None:
    """Alter one transport file between the runner's write and its re-verification.

    Seam: ``heddle.gate.runners.antigravity.write_transport`` writes the launch
    message, chunks and index into the run temp directory and returns before
    re-verification; ``write_launch_record`` (the ``seam`` argument) records
    the launch token after that write and before the launch. The change keeps
    the file's length.
    """
    runner = importlib.import_module(RUNNER)
    assert hasattr(runner, seam), (
        f"FAIL AC-9: {RUNNER} exposes no write-to-verify seam {seam}"
    )
    original = getattr(runner, seam)
    reviews = state_path.parent / "reviews"

    def altered(*args, **kwargs):
        written = original(*args, **kwargs)
        directories = sorted(reviews.glob("*.antigravity.tmp.*"), key=os.path.getmtime)
        assert directories, "fixture: no antigravity run temp directory"
        _alter_transport(directories[-1], target, record)
        return written

    monkeypatch.setattr(runner, seam, altered)


def alter_during_monitor_setup(monkeypatch, target: str, record: Path) -> None:
    """Alter one transport file while the shared monitor sets up the launch.

    Seam: ``heddle.io.process.file_marker``, whose first call in
    ``run_monitored`` reads the raw stream's marker before the monitor opens
    the launch message and the stream and spawns the process. The first call
    for an Antigravity run temp directory alters that directory's file,
    keeping its length; a Claude sibling's calls pass through.
    """
    process = importlib.import_module("heddle.io.process")
    original = process.file_marker
    altered: set[Path] = set()

    def marker(path):
        directory = Path(path).parent
        if ".antigravity.tmp." in directory.name and directory not in altered:
            altered.add(directory)
            _alter_transport(directory, target, record)
        return original(path)

    monkeypatch.setattr(process, "file_marker", marker)


def _alter_transport(directory: Path, target: str, record: Path) -> None:
    path = transport_file(directory, target)
    data = bytearray(path.read_bytes())
    position = _mutable_position(data, target)
    data[position] = _mutated(data[position], target)
    path.write_bytes(bytes(data))
    record.write_text(str(path))


def transport_file(directory: Path, target: str) -> Path:
    files = [path for path in directory.rglob("*") if path.is_file()]
    digests = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    index, chunks = None, []
    for path in files:
        text = path.read_bytes().decode("utf-8", "replace")
        found = [
            other
            for other, digest in digests.items()
            if other != path and digest in text
        ]
        if len(found) > len(chunks):
            index, chunks = path, found
    if target == "chunk":
        assert chunks, "fixture: the run temp directory holds no chunk files"
        # A capture copy can share a chunk's digest; a chunk is a file the
        # index names.
        listed = index.read_bytes() if index is not None else b""
        named_chunks = sorted(path for path in chunks if path.name.encode() in listed)
        assert named_chunks, "fixture: the index names no chunk file"
        return named_chunks[0]
    assert index is not None, "fixture: the run temp directory holds no index"
    if target == "index":
        return index
    pointers = [
        path
        for path in files
        if path != index
        and path not in chunks
        and index.name.encode() in path.read_bytes()
    ]
    named = [path for path in files if "launch" in path.name]
    assert pointers or named, "fixture: no launch message names the index"
    return (pointers or named)[0]


def _mutable_position(data: bytearray, target: str) -> int:
    if target == "index":
        match = re.search(rb"[0-9a-f]{64}", data)
        assert match, "fixture: the index carries no SHA-256 digest"
        return match.start() + 63
    for position, byte in enumerate(data):
        if 0x61 <= byte <= 0x7A:
            return position
    raise AssertionError("fixture: no ASCII letter to alter")


def _mutated(byte: int, target: str) -> int:
    if target == "index":
        return ord("0") if byte != ord("0") else ord("1")
    return byte - 0x20


@pytest.mark.parametrize("target", ALTERED)
def test_ac9_altered_transport_file_fails_standalone_before_any_launch(
    tmp_path, monkeypatch, agy, target
):
    """AC-9 standalone: an altered chunk, index or launch message fails the run as
    ``execution-failure``, names the changed file, and agy never launches."""
    require_runner("AC-9")
    _host, state_path = spec_host(tmp_path, monkeypatch, prompt=chunked_prompt())
    record = tmp_path / "altered.txt"
    alter_after_write(monkeypatch, state_path, target, record)
    result = run_gate()
    assert not result.ok
    assert record.exists(), "fixture: the transport write seam was not reached"
    assert agy.launches() == [], f"FAIL AC-9: agy launched after the {target} changed"
    rows = lane_rows(state_path)
    assert rows and rows[-1].get("failure_reason") == "execution-failure", (
        f"FAIL AC-9: the altered run recorded {rows[-1:]}"
    )
    changed = Path(record.read_text()).name
    assert changed in gate_log(state_path) or changed in json.dumps(
        result.to_envelope()
    ), f"FAIL AC-9: no warning names the changed file {changed}"


@pytest.mark.parametrize("target", ALTERED)
def test_ac9_transport_altered_after_the_launch_record_fails_before_any_launch(
    tmp_path, monkeypatch, agy, target
):
    """AC-9 standalone: a chunk, index or launch message altered after the
    launch token is recorded, the last step before the launch, still fails
    the run as ``execution-failure`` naming the changed file, agy never
    launches, and no launch record is left behind."""
    require_runner("AC-9")
    _host, state_path = spec_host(tmp_path, monkeypatch, prompt=chunked_prompt())
    record = tmp_path / "altered.txt"
    alter_after_write(monkeypatch, state_path, target, record, seam=RECORD_SEAM)
    result = run_gate()
    assert not result.ok
    assert record.exists(), "fixture: the launch-record seam was not reached"
    assert agy.launches() == [], f"FAIL AC-9: agy launched after the {target} changed"
    rows = lane_rows(state_path)
    assert rows and rows[-1].get("failure_reason") == "execution-failure", (
        f"FAIL AC-9: the altered run recorded {rows[-1:]}"
    )
    changed = Path(record.read_text())
    assert changed.name in gate_log(state_path), (
        f"FAIL AC-9: no warning names the changed file {changed.name}"
    )
    from heddle.gate.io import LAUNCH_RECORD_NAME

    assert not (changed.parent / LAUNCH_RECORD_NAME).exists(), (
        "FAIL AC-9: a launch that never started left its token recorded"
    )


def test_ac9_transport_altered_during_monitor_setup_fails_before_any_launch(
    tmp_path, monkeypatch, agy
):
    """AC-9 standalone: the chunk index altered while the shared monitor sets
    up the launch, after its last bookkeeping step, still fails the run as
    ``execution-failure`` naming the changed file, and agy never launches:
    the re-verification runs immediately before the spawn."""
    require_runner("AC-9")
    _host, state_path = spec_host(tmp_path, monkeypatch, prompt=chunked_prompt())
    record = tmp_path / "altered.txt"
    alter_during_monitor_setup(monkeypatch, "index", record)
    result = run_gate()
    assert not result.ok
    assert record.exists(), "fixture: the monitor setup seam was not reached"
    assert agy.launches() == [], "FAIL AC-9: agy launched after the index changed"
    rows = lane_rows(state_path)
    assert rows and rows[-1].get("failure_reason") == "execution-failure", (
        f"FAIL AC-9: the altered run recorded {rows[-1:]}"
    )
    assert Path(record.read_text()).name in gate_log(state_path), (
        "FAIL AC-9: no warning names the changed file"
    )


# --------------------------------------------------------------------------- #
# AC-6, AC-7, AC-22: delivery of the frozen capture
# --------------------------------------------------------------------------- #
def _probe_capture_size(monkeypatch) -> Callable[[], int]:
    """Prepare a standalone run and stop before any launch, reporting its size."""
    from heddle.gate import entry as gate_entry
    from heddle.kernel.project_config import KernelError

    original = gate_entry.prepare_gate_run
    sizes: list[int] = []

    def probe(*args, **kwargs):
        prepared = original(*args, **kwargs)
        sizes.append(len(prepared.transport.stdin.encode("utf-8")))
        raise KernelError(
            code="workspace-invalid", message="fixture probe", hint="fixture probe"
        )

    def measure() -> int:
        with monkeypatch.context() as patch:
            patch.setattr(gate_entry, "prepare_gate_run", probe)
            run_gate()
        assert sizes, "fixture: the probe never reached preparation"
        return sizes[-1]

    return measure


def sized_prompt(host: Path, monkeypatch, total: int, unit: str) -> None:
    """Write a host prompt that makes the frozen capture exactly ``total`` bytes.

    The prompt is a heading, ``count`` repetitions of ``unit`` and ASCII
    padding. Probes measure the base size and one unit's growth, then the
    padding absorbs any remainder; a last probe confirms the exact size.
    """
    measure = _probe_capture_size(monkeypatch)
    head = "# Review\n\n"

    def body(count: int, pad: int) -> str:
        # Padding leads, so it never meets the units' stripped line ends.
        return head + "x" * pad + unit * count + "\n"

    write_prompt(host, body(0, 0))
    base = measure()
    write_prompt(host, body(100, 0))
    growth = (measure() - base) / 100
    count = max(0, int((total - base - 256) // growth))
    pad = 0
    for _ in range(6):
        write_prompt(host, body(count, pad))
        size = measure()
        if size == total:
            return
        if size > total:
            # Prompt whitespace is normalized, so growth is not exactly linear.
            count = max(0, count - int((size - total) // growth) - 2)
            pad = 0
        else:
            pad += total - size
    raise AssertionError(f"fixture: could not size the capture to {total} bytes")


@pytest.mark.parametrize(
    "unit", [MULTIBYTE, ESCAPE_HEAVY], ids=["1-to-4-byte", "escape-heavy"]
)
def test_ac6_capture_at_the_inline_limit_is_delivered_byte_exact(
    tmp_path, monkeypatch, agy, unit
):
    """AC-6: at exactly 160,000 bytes the decoded NDJSON content is the capture."""
    require_runner("AC-6")
    host, state_path = spec_host(tmp_path, monkeypatch)
    sized_prompt(host, monkeypatch, INLINE_LIMIT, unit)
    result = run_gate()
    assert result.ok, result.to_envelope()
    (launch,) = agy.launches()
    capture = capture_of(agy)
    assert len(capture) == INLINE_LIMIT
    route_of(agy, "inline", "AC-6")
    assert not launch["refused"], "FAIL AC-6: the wrapped message exceeded the cap"
    assert launch["contents"] == [capture.decode("utf-8")], (
        "FAIL AC-6: the decoded NDJSON content is not the capture byte for byte"
    )
    assert launch["wrapped"][0] == INLINE_LIMIT + WRAPPED_OVERHEAD <= WRAPPED_CAP


CHUNKED_CASES = {
    "160001-bytes": (INLINE_LIMIT + 1, MULTIBYTE, ""),
    "250000-bytes": (CEILING, MULTIBYTE, ""),
    "format-code-point": (None, MULTIBYTE, "​"),
    "wrapper-tag": (None, MULTIBYTE, "<context_summary>"),
}


@pytest.mark.parametrize("case", list(CHUNKED_CASES))
def test_ac6_ac7_chunked_capture_is_delivered_as_whole_readable_files(
    tmp_path, monkeypatch, agy, case
):
    """AC-6 at 160,001 bytes and AC-7: the index, in the plan's fixed
    ``{"chunks": [...]}`` format, lists the frozen route's chunks in capture
    order with each one's absolute path, half-open range and digest; the
    written chunks equal the frozen capture; each fits one read; the message
    is the pointer only."""
    require_runner("AC-7")
    total, unit, marker = CHUNKED_CASES[case]
    host, state_path = spec_host(tmp_path, monkeypatch)
    if total is None:
        write_prompt(host, f"# Review\n\nNote {marker} here.\n" + unit * 200 + "\n")
    else:
        sized_prompt(host, monkeypatch, total, unit)
    result = run_gate()
    assert result.ok, result.to_envelope()
    (launch,) = agy.launches()
    capture = capture_of(agy)
    if total is not None:
        assert len(capture) == total
    route = route_of(agy, "chunked", "AC-7")
    assert_chunked_delivery(launch, capture, "AC-7", route)


def test_ac7_near_miss_controls_are_delivered_inline(tmp_path, monkeypatch, agy):
    """AC-7 controls: ``USER_REQUEST`` without ``<`` and ``<USER_REQ`` stay inline."""
    require_runner("AC-7")
    _host, state_path = spec_host(
        tmp_path, monkeypatch, prompt="# Review\n\nUSER_REQUEST and <USER_REQ stay.\n"
    )
    result = run_gate()
    assert result.ok, result.to_envelope()
    (launch,) = agy.launches()
    assert launch["contents"] == [capture_of(agy).decode("utf-8")], (
        "FAIL AC-7: a near-miss control was not delivered inline"
    )
    route_of(agy, "inline", "AC-7")


def test_ac22_milestone_body_with_the_checkout_path_is_delivered_byte_exact(
    tmp_path, monkeypatch, agy
):
    """AC-22 (Antigravity delivery): the embedded diff body that names the
    absolute checkout path reaches agy byte for byte."""
    require_runner("AC-22")
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={"milestone-review": entry("milestone-review", primary=NEON)},
    )
    line = f"SOURCE = '{host.resolve()}/src/example.py'  # é漢"
    (host / "src/example.py").write_text(f"VALUE = 7\n{line}\n", encoding="utf-8")
    result = run_gate("milestone-review")
    assert result.ok, result.to_envelope()
    (launch,) = agy.launches()
    capture = capture_of(agy, "milestone-review")
    assert f"+{line}\n".encode() in capture, "fixture: the diff body is not embedded"
    kind = "inline" if launch["index"] is None else "chunked"
    route_of(agy, kind, "AC-22", "milestone-review")
    delivered = (
        launch["contents"][0].encode("utf-8")
        if launch["index"] is None
        else b"".join(chunk_files(launch))
    )
    assert delivered == capture, (
        "FAIL AC-22: the delivered capture differs from the frozen one"
    )


# --------------------------------------------------------------------------- #
# AC-11: the outcome table through the real runner
# --------------------------------------------------------------------------- #
def quota_line(status: str, retryable: bool) -> dict:
    payload = {
        "short_error": "request failed",
        "status": status,
        "retryable": retryable,
    }
    return {"$raw": "AGY_ERROR: " + json.dumps(payload)}


INVALID_MODEL = (
    'error: invalid model selection (--model "gemini-4-neon" --effort "high"): '
    "the model is not available"
)
# Rows 3, 5, 7 and the unread-free parts of the table: the runner alone.
RUNNER_ROWS = {
    "print-timeout": (
        clean_stream(result=result_event(finished_reason="print_timeout")),
        "timeout",
    ),
    "interrupted": (
        clean_stream(
            {"$raw": "error: interrupted"},
            result=result_event(
                status="ERROR",
                structured_output=ABSENT,
                finished_reason="interrupted",
                error="interrupted",
            ),
        ),
        "execution-failure",
    ),
    "no-result": (clean_stream()[:-1], "transport-error"),
    "two-results": (clean_stream() + [result_event()], "transport-error"),
    "model-mismatch": (
        clean_stream(head=[init_event("gemini-3.8-flash")]),
        "model-substituted",
    ),
    "no-init": (clean_stream(head=[]), "transport-error"),
    "init-without-model": (clean_stream(head=[init_event(ABSENT)]), "transport-error"),
    "two-inits-one-other": (
        clean_stream(head=[init_event(), init_event("gemini-4-flash")]),
        "model-substituted",
    ),
    "missing-structured-output": (
        clean_stream(result=result_event(structured_output=ABSENT)),
        "empty-output",
    ),
    "quota": (
        [init_event(), quota_line("RESOURCE_EXHAUSTED", False), {"$exit": 3}],
        "antigravity-quota",
    ),
    "retryable-api-error": (
        [init_event(), quota_line("UNAVAILABLE", True), {"$exit": 3}],
        "antigravity-api-error",
    ),
    "invalid-model-selection": (
        [{"$raw": INVALID_MODEL}, {"$exit": 1}],
        "unsupported-model",
    ),
    "oversized-stream": (
        clean_stream({"$oversize": 64 * 1024 * 1024 + 1}),
        "transport-error",
    ),
    "unparseable-line": (
        clean_stream({"$raw": '{"event": "step_update", "step_update": {'}),
        "transport-error",
    ),
    "nonzero-exit": (clean_stream() + [{"$exit": 1}], "exit"),
}
# Malformed stream records beside an otherwise valid run, and an init event
# whose model is blank: a JSON-shaped line must be one event object, and only
# a named model is an identity.
STREAM_SHAPE_ROWS = {
    "array-line": (clean_stream({"$raw": '["step_update", {}]'}), "transport-error"),
    "unterminated-string-line": (
        clean_stream({"$raw": '"step_update'}),
        "transport-error",
    ),
    "bare-json-value": (clean_stream({"$raw": "null"}), "transport-error"),
    "blank-init-model": (
        clean_stream(head=[init_event("")]),
        "transport-error",
    ),
    "whitespace-init-model": (
        clean_stream(head=[init_event("   ")]),
        "transport-error",
    ),
}
OUTCOME_ROWS = {**RUNNER_ROWS, **STREAM_SHAPE_ROWS}


def test_ac11_plain_text_notices_stay_diagnostics(tmp_path, monkeypatch, agy):
    """AC-11 control: plain-text lines that are not JSON, such as an update
    notice and the measured ``error: interrupted`` text, are diagnostics
    beside an otherwise valid stream, which records."""
    require_runner("AC-11")
    _host, state_path = spec_host(tmp_path, monkeypatch)
    agy.scenario(
        clean_stream(
            {"$raw": "A newer agy release is available; run agy update to install."},
            {"$raw": "error: interrupted"},
            {"$raw": "  checking for updates (12.5 s)"},
        )
    )
    result = run_gate()
    assert result.ok, f"FAIL AC-11: a plain-text notice failed the run: {result}"
    assert_recorded(state_path, "AC-11")


def _spec_batch_host(tmp_path, monkeypatch, prompt=None):
    """Antigravity primary beside a ready Claude secondary on one spec review."""
    return spec_host(tmp_path, monkeypatch, secondary=FABLE, prompt=prompt)


@pytest.mark.parametrize("row", list(OUTCOME_ROWS))
def test_ac11_runner_outcome_row_standalone(tmp_path, monkeypatch, agy, row):
    """AC-11 rows 3, 5 and 7 standalone; each is a hard failure with one launch."""
    require_runner("AC-11")
    _host, state_path = spec_host(tmp_path, monkeypatch)
    ops, reason = OUTCOME_ROWS[row]
    agy.scenario(ops)
    result = run_gate()
    assert not result.ok, f"FAIL AC-11: {row} recorded as a success"
    assert_hard_failure(state_path, agy, reason, "AC-11")
    if row == "oversized-stream":
        assert summary_for(state_path)["reason"] != "truncated-output"


PEER = ("behavior-review", "complexity-review")


def peer_host(
    tmp_path, monkeypatch, *, launch: str | None = "concurrent", chunked=False
):
    """Behavior and complexity reviews, each on the Antigravity lane; with
    ``launch`` set, the two are members of one concurrent ``run-gates`` batch."""
    from tests.concurrent_review_helpers import off, set_launch

    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="peer-review",
        overrides={
            "peer-review-sequential": off("peer-review-sequential"),
            **{gate: entry(gate, primary=NEON) for gate in PEER},
        },
    )
    if launch is not None:
        set_launch(host, launch)
    if chunked:
        for gate in PEER:
            write_prompt(host, chunked_prompt(80_000), gate)
    return host, state_path


def paired(rows: dict) -> list:
    """Two rows per batch, one per peer gate; an odd last row meets a clean run,
    so every row still runs as a member beside a sibling."""
    names = list(rows)
    pairs = [names[index : index + 2] for index in range(0, len(names), 2)]
    return [
        pytest.param(pair[0], pair[1] if len(pair) == 2 else None, id="+".join(pair))
        for pair in pairs
    ]


def gate_member(result, gate: str) -> dict:
    members = (
        result.data["members"] if result.ok else result.error.details.get("members")
    )
    assert members, f"fixture: the batch ran no members: {result.to_envelope()}"
    (member,) = [row for row in members if row["gate"] == gate]
    return member


def assert_member_outcomes(result, state_path, agy, cases: dict, ac: str) -> None:
    """Each member fails with its row's reason, or records when it ran clean."""
    for gate, (row, reason) in cases.items():
        member = gate_member(result, gate)
        launches = [launch for launch in agy.launches() if launch["role"] == gate]
        assert len(launches) == 1, f"FAIL {ac}: {gate} launched {len(launches)} times"
        rows = [run for run in lane_rows(state_path) if run["gate"] == gate]
        assert rows, f"FAIL {ac}: {gate} recorded nothing"
        if reason is None:
            assert (member["execution"], member["publication"]) == (
                "completed",
                "recorded",
            ), f"FAIL {ac}: the clean sibling did not record: {member}"
            continue
        assert (member["execution"], member["publication"]) == (
            "failed",
            "recorded-error",
        ), f"FAIL {ac}: {row} as a member: {member}"
        assert member["fallback"] is None, f"FAIL {ac}: {row} fell back"
        assert rows[-1].get("failure_reason") == reason, (
            f"FAIL {ac}: {row} as a member recorded {rows[-1].get('failure_reason')}, "
            f"expected {reason}"
        )
        assert rows[-1]["verdict"]["status"] == "error"
    assert captures(state_path) == [], f"FAIL {ac}: a member left a capture"
    attempts = yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"]
    assert "usable-capture" not in [row["outcome"]["kind"] for row in attempts], (
        f"FAIL {ac}: a member's hard failure was offered for interpretation"
    )
    assert agy.claude_calls() == [], f"FAIL {ac}: a member fell back to another lane"


@pytest.mark.parametrize(
    ("first", "second"), paired(RUNNER_ROWS) + paired(STREAM_SHAPE_ROWS)
)
def test_ac11_runner_outcome_rows_as_batch_members(
    tmp_path, monkeypatch, agy, first, second
):
    """AC-11 rows 3, 5 and 7 as ``run-gates`` members: two rows per batch, one
    per peer gate, each failing alone with no retry or fallback."""
    require_runner("AC-11")
    _host, state_path = peer_host(tmp_path, monkeypatch)
    cases = {
        gate: (row, OUTCOME_ROWS[row][1] if row else None)
        for gate, row in zip(PEER, (first, second), strict=True)
    }
    agy.scenario(
        roles={
            gate: OUTCOME_ROWS[row][0] if row else clean_stream()
            for gate, (row, _reason) in cases.items()
        }
    )
    result = run_batch()
    assert not result.ok
    assert_member_outcomes(result, state_path, agy, cases, "AC-11")


def unassigned(monkeypatch) -> None:
    """Run the prepared invocation without its review assignment."""
    from dataclasses import replace

    from heddle.gate import entry as gate_entry

    original = gate_entry.run_gate_for_runtime

    def without_assignment(gate_type, context, **kwargs):
        context.prepared_run = replace(context.prepared_run, assignment_id=None)
        return original(gate_type, context, **kwargs)

    monkeypatch.setattr(gate_entry, "run_gate_for_runtime", without_assignment)


def noncanonical(agy: Agy) -> dict:
    return {
        role: {**review, "extra": "kept verbatim"}
        for role, review in agy.reviews.items()
    }


def test_ac11_canonical_rejection_of_an_assigned_run_is_retained(
    tmp_path, monkeypatch, agy
):
    """AC-11 row 8, assigned: the existing retention keeps the run for review."""
    require_runner("AC-11")
    _host, state_path = spec_host(tmp_path, monkeypatch)
    agy.scenario(reviews=noncanonical(agy))
    result = run_gate()
    assert result.ok and result.exit_code == 4, (
        f"FAIL AC-11: the assigned noncanonical run was not retained: "
        f"{result.to_envelope()}"
    )
    assert result.data["accepted"] is False
    assert len(captures(state_path)) == 1
    assert len(agy.launches()) == 1


def test_ac11_canonical_rejection_of_an_unassigned_run_is_invalid_output(
    tmp_path, monkeypatch, agy
):
    """AC-11 row 8, unassigned: ``invalid-output-structure`` and no capture."""
    require_runner("AC-11")
    _host, state_path = spec_host(tmp_path, monkeypatch)
    unassigned(monkeypatch)
    agy.scenario(reviews=noncanonical(agy))
    result = run_gate()
    assert not result.ok
    assert summary_for(state_path)["reason"] == "invalid-output-structure", (
        "FAIL AC-11: the unassigned noncanonical run did not fail as "
        "invalid-output-structure"
    )
    assert captures(state_path) == []
    assert len(agy.launches()) == 1


def test_ac11_canonical_rejection_as_batch_member_is_retained(
    tmp_path, monkeypatch, agy
):
    """AC-11 row 8 as a ``run-gates`` member: retained beside a recorded sibling."""
    require_runner("AC-11")
    _host, state_path = _spec_batch_host(tmp_path, monkeypatch)
    agy.scenario(reviews={**agy.reviews, SPEC: noncanonical(agy)[SPEC]})
    result = run_batch()
    member = batch_member(result, "primary")
    assert (member["execution"], member["publication"]) == ("retained", "retained")
    sibling = batch_member(result, "secondary")
    assert sibling["publication"] == "recorded"
    assert len(captures(state_path)) == 1


def test_ac11_new_reasons_never_fall_back_or_retry():
    """AC-11: the lane's reasons exist and stay out of the fallback set."""
    from heddle.contracts.gate_execution import FALLBACK_FAILURE_REASONS
    from heddle.gate.types import FailureReason

    values = {reason.value for reason in FailureReason}
    missing = [reason for reason in NEW_REASONS if reason not in values]
    assert not missing, f"FAIL AC-11: FailureReason lacks {missing}"
    assert not set(NEW_REASONS) & set(FALLBACK_FAILURE_REASONS), (
        "FAIL AC-11: an Antigravity hard failure falls back"
    )


# Rows 1, 2, 4 and 6 and their competing pairs: real streams through the audit.
def _data_dir_read() -> dict:
    """A ``read_file`` of agy's data directory that the deny rule refused."""
    return {
        "$step": {
            "step_type": "tool",
            "state": "ERROR",
            "tool_name": "read_file",
            "tool_info": {
                "name": "read_file",
                "parameters": {"Path": str(DATA_DIR / "conversations")},
                "error": {"type": "TOOL_ERROR", "message": "denied by settings"},
            },
        }
    }


AUDIT_ROWS = {
    "denied-actions": (
        clean_stream(
            result=result_event(
                denied_actions=[{"action": "command", "display_name": "Bash"}]
            )
        ),
        "out-of-scope-access",
        False,
    ),
    "unapproved-tool": (
        clean_stream(
            tool_step("search_web", {"query": "review guidance"}, "3 results")
        ),
        "antigravity-unapproved-tool",
        False,
    ),
    "subagent-step": (
        clean_stream(
            {
                "$step": {
                    "step_type": "subagent",
                    "tool_name": "invoke_subagent",
                    "subagent_info": {"task": "read the chunks"},
                }
            }
        ),
        "antigravity-unapproved-tool",
        False,
    ),
    "checkpoint": (
        clean_stream(
            {"$step": {"step_type": "system_message"}},
            {"$step": {"step_type": "checkpoint"}},
        ),
        "antigravity-context-compacted",
        False,
    ),
    "unread-chunk": (
        clean_stream(reads=READ_ALL_BUT_LAST),
        "antigravity-incomplete-coverage",
        True,
    ),
    "checkpoint-with-interrupted": (
        clean_stream(
            {"$step": {"step_type": "checkpoint"}},
            result=result_event(
                status="ERROR",
                structured_output=ABSENT,
                finished_reason="interrupted",
                error="interrupted",
            ),
        ),
        "antigravity-context-compacted",
        False,
    ),
    "checkpoint-with-unread-chunks": (
        clean_stream({"$step": {"step_type": "checkpoint"}}, reads=READ_ALL_BUT_LAST),
        "antigravity-context-compacted",
        True,
    ),
    "denied-data-dir-read-with-valid-object": (
        clean_stream(
            _data_dir_read(),
            result=result_event(
                denied_actions=[{"action": "read_file", "display_name": "Read File"}]
            ),
        ),
        "out-of-scope-access",
        False,
    ),
    "unapproved-tool-with-out-of-scope-path": (
        clean_stream(
            tool_step("view_code_outline", {"Path": str(DATA_DIR / "brain")}, "1 item")
        ),
        "out-of-scope-access",
        False,
    ),
}


@pytest.mark.parametrize("row", list(AUDIT_ROWS))
def test_ac11_audit_outcome_row_standalone(tmp_path, monkeypatch, agy, row):
    """AC-11 rows 1, 2, 4, 6 and the competing pairs, standalone (real stream
    through the audit): compaction, compaction, out-of-scope, out-of-scope."""
    require_audit("AC-11")
    ops, reason, chunked = AUDIT_ROWS[row]
    _host, state_path = spec_host(
        tmp_path, monkeypatch, prompt=chunked_prompt(80_000) if chunked else None
    )
    agy.scenario(ops)
    result = run_gate()
    assert not result.ok, f"FAIL AC-11: {row} recorded as a success"
    assert_hard_failure(state_path, agy, reason, "AC-11")


@pytest.mark.parametrize(("first", "second"), paired(AUDIT_ROWS))
def test_ac11_audit_outcome_rows_as_batch_members(
    tmp_path, monkeypatch, agy, first, second
):
    """AC-11 rows 1, 2, 4, 6 and the competing pairs as ``run-gates`` members
    (real streams through the audit), two rows per batch over chunked input."""
    require_audit("AC-11")
    _host, state_path = peer_host(tmp_path, monkeypatch, chunked=True)
    cases = {
        gate: (row, AUDIT_ROWS[row][1] if row else None)
        for gate, row in zip(PEER, (first, second), strict=True)
    }
    agy.scenario(
        roles={
            gate: AUDIT_ROWS[row][0] if row else clean_stream()
            for gate, (row, _reason) in cases.items()
        }
    )
    result = run_batch()
    assert not result.ok
    assert_member_outcomes(result, state_path, agy, cases, "AC-11")


# --------------------------------------------------------------------------- #
# AC-2: the reviewer child's marker and its refused mutation
# --------------------------------------------------------------------------- #
MUTATION = ["commands", "set", "smoke_test", "--command", "true", "--json"]


def heddle_attempt(host: Path, tmp_path: Path) -> dict:
    import shutil

    copy = tmp_path / "host-copy"
    shutil.copytree(host, copy, symlinks=True)
    return {"$heddle": {"copy": str(copy), "argv": MUTATION}}


def test_ac2_reviewer_child_is_marked_and_its_mutation_exits_3(
    tmp_path, monkeypatch, agy
):
    """AC-2 (runner half): the agy child carries the marker; its mutating
    ``heddle`` attempt on a disposable host copy exits 3 with
    ``gate-session-refused``, and the kept stream shows the attempt."""
    require_runner("AC-2")
    host, state_path = spec_host(tmp_path, monkeypatch)
    agy.scenario(clean_stream(heddle_attempt(host, tmp_path)))
    run_gate()
    (launch,) = agy.launches()
    assert launch["marker"] == "gate", "FAIL AC-2: the agy child is not marked"
    (attempt,) = agy.rows("attempts.jsonl")
    assert (attempt["exit"], attempt["code"]) == (3, "gate-session-refused"), (
        f"FAIL AC-2: the marked mutation returned {attempt}"
    )
    stream = Path(summary_for(state_path)["artifacts"]["raw_out"]).read_text()
    assert "heddle commands set" in stream and "exited with code 3" in stream, (
        "FAIL AC-2: the kept stream does not show the refused attempt"
    )


@pytest.mark.parametrize("mode", ["standalone", "batch-member"])
def test_ac2_reviewer_heddle_attempt_fails_as_out_of_scope_access(
    tmp_path, monkeypatch, agy, mode
):
    """AC-2 (real stream through the audit): the attempt fails the run as
    ``out-of-scope-access`` with no credit; the child's exit 3 is in the stream."""
    require_audit("AC-2")
    host, state_path = (
        spec_host(tmp_path, monkeypatch)
        if mode == "standalone"
        else _spec_batch_host(tmp_path, monkeypatch)
    )
    agy.scenario(clean_stream(heddle_attempt(host, tmp_path)))
    result = run_gate() if mode == "standalone" else run_batch()
    assert not result.ok
    assert [row["exit"] for row in agy.rows("attempts.jsonl")] == [3]
    assert_hard_failure(state_path, agy, "out-of-scope-access", "AC-2")


# --------------------------------------------------------------------------- #
# AC-14: a reviewed-source write is left to the recording admission
# --------------------------------------------------------------------------- #
MILESTONE = "milestone-review"
SOURCE = "src/example.py"
SOURCE_TEXT = "VALUE = 7\nNAME = 'example'\n"
SOURCE_WRITES = {
    # (old, new, expected failure reason or None when the run records)
    "substantive": ("NAME = 'example'", "NAME = 'changed'", "reviewed-source-changed"),
    "review-equivalent": ("NAME = 'example'", 'NAME = "example"', None),
}


def milestone_host(tmp_path, monkeypatch):
    """A milestone review whose reviewed sources include a changed Python file."""
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        overrides={MILESTONE: entry(MILESTONE, primary=NEON)},
    )
    (host / SOURCE).write_text(SOURCE_TEXT, encoding="utf-8")
    return host, state_path


def latest_attempt(state_path: Path) -> dict:
    ledger = yaml.safe_load(state_path.read_text())["review_assignments"]
    return ledger["attempts"][-1]


@pytest.mark.parametrize("write", list(SOURCE_WRITES))
def test_ac14_reviewed_source_write_goes_to_the_recording_admission(
    tmp_path, monkeypatch, agy, write
):
    """AC-14 (assigned runs; real stream through the audit and the existing
    recording admission, unchanged): an allowed write tool edits a reviewed
    Python source while the reviewer runs. The substantive edit fails as
    ``reviewed-source-changed`` with no credit; the review-equivalent edit (a
    quote-style change) records as usual. The second is the control: it fails
    if the audit refuses every reviewed-source write before the admission rules.
    """
    require_audit("AC-14")
    host, state_path = milestone_host(tmp_path, monkeypatch)
    old, new, reason = SOURCE_WRITES[write]
    agy.scenario(clean_stream({"$write": {"path": SOURCE, "old": old, "new": new}}))
    result = run_gate(MILESTONE)
    assert _prepared_row(agy, MILESTONE)["assignment"], "fixture: run not assigned"
    assert agy.rows("writes.jsonl"), "fixture: the double's write never ran"
    assert (host / SOURCE).read_text() == SOURCE_TEXT.replace(old, new)
    assert len(agy.launches()) == 1, "FAIL AC-14: the run launched more than once"
    rows = [row for row in lane_rows(state_path) if row["gate"] == MILESTONE]
    assert rows, "FAIL AC-14: the assigned run recorded nothing"
    recorded = rows[-1].get("failure_reason")
    if reason is None:
        assert result.ok and recorded is None, (
            f"FAIL AC-14: the review-equivalent write did not record as usual "
            f"({recorded}); the audit may refuse reviewed-source writes before "
            "the recording admission can rule"
        )
        assert_recorded(state_path, "AC-14")
        assert latest_attempt(state_path)["source"] is not None, (
            "FAIL AC-14: the review-equivalent run earned no credit"
        )
        return
    assert not result.ok and recorded == reason, (
        f"FAIL AC-14: the substantive write recorded {recorded}, expected {reason} "
        "from the recording admission"
    )
    assert rows[-1]["verdict"]["status"] == "error"
    assert latest_attempt(state_path)["source"] is None, (
        "FAIL AC-14: the substantive write's run was credited"
    )


# --------------------------------------------------------------------------- #
# AC-17: a delivered chunk changed after its whole read (runner-level, m6)
# --------------------------------------------------------------------------- #
ALTERED_CHUNK = 1


def test_ac17_chunk_changed_after_its_whole_read_leaves_the_capture_uncovered(
    tmp_path, monkeypatch, agy
):
    """AC-17 through the real runner and audit: every chunk is read whole, then
    one delivered chunk file is really altered before the run ends, so a
    transport file no longer matches its frozen digest at completion. Under the
    global credit rule (a read counts only when every transport file still
    matches), no read is credited: the run fails as
    ``antigravity-incomplete-coverage`` and the persisted audit, read from its
    structured fields, has the transport not intact, the whole capture
    uncovered and nothing credited. A diagnostic may name the altered file;
    that is not required."""
    require_audit("AC-17")
    _host, state_path = spec_host(tmp_path, monkeypatch, prompt=chunked_prompt(80_000))
    agy.scenario(clean_stream({"$alter_chunk": ALTERED_CHUNK}))
    result = run_gate()
    capture = capture_of(agy)
    route = route_of(agy, "chunked", "AC-17")
    assert len(route["chunks"]) > ALTERED_CHUNK, "fixture: too few chunks"
    assert agy.rows("altered.jsonl"), "fixture: the double never altered the chunk"
    (launch,) = agy.launches()
    assert len(launch["chunks"]) == len(route["chunks"]), (
        "fixture: the double did not observe and read every chunk"
    )
    assert not result.ok, "FAIL AC-17: a run whose transport changed was credited"
    assert_hard_failure(state_path, agy, "antigravity-incomplete-coverage", "AC-17")
    audit = summary_for(state_path).get("audit")
    assert isinstance(audit, dict), "FAIL AC-17: the summary carries no audit record"
    assert audit.get("transport_intact") is False, (
        f"FAIL AC-17: the audit records transport_intact "
        f"{audit.get('transport_intact')!r} after a chunk file changed"
    )
    assert audit.get("uncovered") == [[0, len(capture)]], (
        f"FAIL AC-17: uncovered is {audit.get('uncovered')}, expected the whole "
        f"capture [[0, {len(capture)}]] once a transport file changed"
    )
    assert audit.get("credited") == [], (
        f"FAIL AC-17: reads were credited after a transport file changed: "
        f"{audit.get('credited')}"
    )


# --------------------------------------------------------------------------- #
# AC-21 (b): a shown `reviews/` entry fails an assigned run (runner-level, m6)
# --------------------------------------------------------------------------- #
def test_ac21_shown_reviews_entry_fails_an_assigned_run_without_credit(
    tmp_path, monkeypatch, agy
):
    """AC-21 (b) through assigned recording: an otherwise clean stream with a
    valid review object, whose shell search output shows a sibling artifact
    under the feature's ``reviews/``, fails as ``out-of-scope-access`` with no
    interpretable capture and no slot credit."""
    require_audit("AC-21")
    host, state_path = spec_host(tmp_path, monkeypatch)
    reviews = state_path.parent / "reviews"
    reviews.mkdir(exist_ok=True)
    sibling = reviews / "peer-review.md"
    sibling.write_text("# Sibling review\n\nTODO tighten\n", encoding="utf-8")
    shown = sibling.relative_to(host).as_posix()
    search = tool_step(
        "run_command",
        {"CommandLine": "grep -rl TODO plans | sort", "Cwd": str(host)},
        shell_output(f"{shown}\n"),
    )
    agy.scenario(clean_stream(search))
    result = run_gate()
    assert _prepared_row(agy, SPEC)["assignment"], "fixture: the run is not assigned"
    assert not result.ok, "FAIL AC-21: a shown sibling artifact was credited"
    assert_hard_failure(state_path, agy, "out-of-scope-access", "AC-21")
    assert latest_attempt(state_path)["source"] is None, (
        "FAIL AC-21: the slot was credited despite the shown reviews/ entry"
    )


# --------------------------------------------------------------------------- #
# AC-12: the runner reaps its launch token
# --------------------------------------------------------------------------- #
def test_ac12_normal_exit_reaps_every_tokened_child(
    tmp_path, monkeypatch, agy, processes
):
    """AC-12 normal exit: plain and daemonised tokened children are gone when the
    run returns; the unrelated process survives; the review records."""
    require_runner("AC-12")
    _host, state_path = spec_host(tmp_path, monkeypatch)
    agy.scenario(clean_stream({"$children": ["plain", "daemon"]}))
    result = run_gate()
    assert result.ok, result.to_envelope()
    children = agy.children()
    assert len(children) == 2, "fixture: the double did not start its children"
    left = [row for row in children if alive(row["pid"], row["start"])]
    assert left == [], f"FAIL AC-12: tokened children survived the run: {left}"
    assert processes.unrelated_alive(), "FAIL AC-12: the unrelated process was killed"
    assert_recorded(state_path, "AC-12")


def test_ac12_timeout_reaps_every_tokened_child(tmp_path, monkeypatch, processes):
    """AC-12 timeout (and AC-11's monitor ``timeout``): the hung launch's
    daemonised child is reaped; the unrelated process survives."""
    import threading

    require_runner("AC-12")
    agy = processes.agy
    seen: list[dict] = []
    try:
        fast_gate_args(monkeypatch, timeout=2.0, inactivity=20)
        _host, state_path = spec_host(tmp_path, monkeypatch)
        agy.scenario([init_event(), {"$children": ["daemon"]}, {"$hold": "never"}])

        def watch() -> None:
            # Before the timeout fires: the tokened child is recorded and alive.
            (child,) = agy.wait_for("holding.jsonl") and agy.children()
            if alive(child["pid"], child["start"]):
                seen.append(child)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        result = run_gate()
        watcher.join(timeout=5)
        assert seen, "fixture: no live tokened child existed before the timeout"
        assert not result.ok
        assert_hard_failure(state_path, agy, "timeout", "AC-11")
        left = [row for row in agy.children() if alive(row["pid"], row["start"])]
        assert left == [], f"FAIL AC-12: the timed-out launch left {left}"
        assert processes.unrelated_alive()
    finally:
        agy.release("never")
        processes.cleanup()


SILENT = "behavior-review"


@pytest.mark.parametrize("mode", ["standalone", "batch-member"])
def test_ac11_ac12_inactivity_timeout_fails_hard_and_reaps_the_child(
    tmp_path, monkeypatch, processes, mode
):
    """AC-11 monitor ``inactivity-timeout`` through the real runner, in process,
    with a 2 s inactivity bound: the doubled ``agy`` writes ``init``, starts a
    daemonised tokened child and goes silent while staying alive. The run is a
    hard failure (one launch, no capture, no retry or fallback), standalone and
    as a ``run-gates`` member beside a clean sibling, and the child is reaped
    (AC-12)."""
    import threading

    require_runner("AC-11")
    agy = processes.agy
    seen: list[dict] = []
    try:
        fast_gate_args(monkeypatch, timeout=30, inactivity=2.0)
        silent = [init_event(), {"$children": ["daemon"]}, {"$hold": "never"}]
        if mode == "standalone":
            _host, state_path = spec_host(tmp_path, monkeypatch)
            agy.scenario(silent)
        else:
            _host, state_path = peer_host(tmp_path, monkeypatch)
            agy.scenario(roles={SILENT: silent, PEER[1]: clean_stream()})

        def watch() -> None:
            # Before the bound fires: the tokened child is recorded and alive.
            (child,) = agy.wait_for("holding.jsonl") and agy.children()
            if alive(child["pid"], child["start"]):
                seen.append(child)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        result = run_gate() if mode == "standalone" else run_batch()
        watcher.join(timeout=5)
        assert seen, "fixture: no live tokened child existed before the bound fired"
        assert not result.ok, "FAIL AC-11: a silent launch was not a failure"
        if mode == "standalone":
            assert_hard_failure(state_path, agy, "inactivity-timeout", "AC-11")
        else:
            cases = {SILENT: ("silent", "inactivity-timeout"), PEER[1]: (None, None)}
            assert_member_outcomes(result, state_path, agy, cases, "AC-11")
        left = [row for row in agy.children() if alive(row["pid"], row["start"])]
        assert left == [], f"FAIL AC-12: the silent launch left {left}"
        assert processes.unrelated_alive()
    finally:
        agy.release("never")
        processes.cleanup()


# --------------------------------------------------------------------------- #
# AC-13: the agy version state
# --------------------------------------------------------------------------- #
VERSION_STATES = {
    "matching": TESTED_VERSION,
    "differing": OTHER_VERSION,
    "unavailable": None,
}
# The gate log's ``<Name>Warning:`` convention (as ``DiffWarning:``): the
# version diagnostic is keyed on its own prefix and on nothing else.
VERSION_WARNING = re.compile(r"(?:^|\s)AgyVersionWarning:")
UNKNOWN_VERSION = re.compile(r"\b(unknown|unavailable)\b", re.I)
# Failed launches on each host before its clean run: agy exits nonzero, and
# reading the stream fails once agy has run.
FAILED_LAUNCHES = {"exit": "nonzero-exit", "execution-failure": "unreadable-stream"}


def unreadable_stream(monkeypatch) -> None:
    """Reading a finished launch's stream raises ``OSError``."""
    runner = importlib.import_module(RUNNER)

    def unreadable(path):
        raise OSError(5, "Input/output error", str(path))

    monkeypatch.setattr(runner, "read_stream", unreadable)


def test_ac13_version_state_is_recorded_without_changing_the_outcome(
    tmp_path, monkeypatch, agy
):
    """AC-13: the same input on three identical hosts, with ``agy --version``
    matching, differing and unavailable. The summary's ``agy_version`` records
    ``state``, ``tested`` and ``reported`` (null when unavailable); the gate log
    writes one ``AgyVersionWarning:`` line naming the tested and reported
    versions when they differ, or the tested version and an unknown reported
    version when the probe fails, and no such line on a match. The outcome, the
    input's identity and the reviewer selection are equal across all three.
    Two failed launches on each host before its clean run, a nonzero exit and
    a stream that cannot be read once agy has run, record the same
    ``agy_version`` with the same failure reason, identity and selection across
    all three."""
    require_runner("AC-13")
    observed = {}
    failed: dict[str, dict[str, str]] = {reason: {} for reason in FAILED_LAUNCHES}
    for state, reported in VERSION_STATES.items():
        root = tmp_path / state
        root.mkdir()
        _host, state_path = spec_host(root, monkeypatch)
        for reason, launch in FAILED_LAUNCHES.items():
            launches = len(agy.launches())
            with monkeypatch.context() as patch:
                if launch == "unreadable-stream":
                    unreadable_stream(patch)
                    agy.scenario(version=reported)
                else:
                    agy.scenario(clean_stream() + [{"$exit": 1}], version=reported)
                failure = run_gate()
            assert not failure.ok and len(agy.launches()) == launches + 1, (
                f"fixture: the {launch} run did not launch once and fail ({state})"
            )
            row = lane_rows(state_path)[-1]
            version = summary_for(state_path).get("agy_version") or {}
            assert {key: version.get(key) for key in ("state", "reported")} == {
                "state": state,
                "reported": reported,
            }, (
                f"FAIL AC-13: the {launch} failure records agy_version {version} "
                f"for the {state} state"
            )
            failed[reason][state] = json.dumps(
                [
                    row.get("failure_reason"),
                    row.get("input_hash"),
                    row.get("review_basis_hash"),
                    row["reviewer_slot"],
                    row["execution_config"],
                ],
                sort_keys=True,
            )
            assert row.get("failure_reason") == reason, (
                f"FAIL AC-13: the {launch} run failed as {row.get('failure_reason')}"
            )
        agy.scenario(version=reported)
        before = len(agy.rows("versions.jsonl"))
        result = run_gate()
        assert len(agy.rows("versions.jsonl")) > before, (
            f"FAIL AC-13: agy --version was never run ({state})"
        )
        row = assert_recorded(state_path, "AC-13")
        prepared = _prepared_row(agy, SPEC)
        observed[state] = {
            "outcome": (result.ok, row["verdict"]["status"], row.get("failure_reason")),
            "identity": (prepared["input_hash"], prepared["review_basis_hash"]),
            "recorded_identity": (row.get("input_hash"), row.get("review_basis_hash")),
            "selection": (
                prepared["slot"],
                prepared["execution"],
                row["reviewer_slot"],
                row["execution_config"],
            ),
        }
        version = summary_for(state_path).get("agy_version")
        assert isinstance(version, dict) and {"state", "tested", "reported"} <= set(
            version
        ), f"FAIL AC-13: the summary's agy_version is {version!r} ({state})"
        assert {key: version[key] for key in ("state", "tested", "reported")} == {
            "state": state,
            "tested": TESTED_VERSION,
            "reported": reported,
        }, (
            f"FAIL AC-13: the summary records agy_version {version} "
            f"for the {state} state"
        )
        log = gate_log(state_path)
        warnings = [line for line in log.splitlines() if VERSION_WARNING.search(line)]
        if state == "matching":
            assert TESTED_VERSION in log, (
                "FAIL AC-13: the gate log does not record the matching version"
            )
            assert warnings == [], f"FAIL AC-13: a matching version warned: {warnings}"
        else:
            assert len(warnings) == 1, (
                f"FAIL AC-13: expected one AgyVersionWarning: line for the {state} "
                f"state, got {warnings}"
            )
            (warning,) = warnings
            assert TESTED_VERSION in warning, (
                f"FAIL AC-13: the {state} warning does not name the tested "
                f"version: {warning}"
            )
            if state == "differing":
                assert OTHER_VERSION in warning, (
                    f"FAIL AC-13: the differing warning does not name the reported "
                    f"version {OTHER_VERSION}: {warning}"
                )
            else:
                assert UNKNOWN_VERSION.search(warning), (
                    f"FAIL AC-13: the unavailable warning does not call the reported "
                    f"version unknown: {warning}"
                )
    for reason, rows in failed.items():
        assert len(set(rows.values())) == 1, (
            f"FAIL AC-13: the {reason} failure differs across version states: {rows}"
        )
    assert observed["matching"]["outcome"][0], "FAIL AC-13: the clean run failed"
    for key in ("outcome", "identity", "recorded_identity", "selection"):
        values = {state: observed[state][key] for state in VERSION_STATES}
        assert (
            len({json.dumps(value, sort_keys=True) for value in values.values()}) == 1
        ), f"FAIL AC-13: the {key} differs across version states: {values}"


# --------------------------------------------------------------------------- #
# AC-18: the redaction handoff
# --------------------------------------------------------------------------- #
def echo(review: dict, token: str) -> dict:
    copied = json.loads(json.dumps(review))
    copied["summary"] = f"{copied.get('summary', '')} Found {token} in the log."
    return copied


def files_holding(root: Path, needle: bytes) -> list[str]:
    return [
        str(path.relative_to(root))
        for path in sorted(root.rglob("*"))
        if path.is_file() and needle in path.read_bytes()
    ]


@pytest.mark.parametrize("kept", ["recorded", "retained"])
def test_ac18_echoed_credential_reaches_no_kept_file(tmp_path, monkeypatch, agy, kept):
    """AC-18 handoff: a credential-like value in tool parameters, shell output and
    the response reaches no kept file: the review artifact or retained capture,
    the summary, the log and the temp files."""
    require_runner("AC-18")
    host, state_path = spec_host(tmp_path, monkeypatch)
    token = credential(kept)
    review = echo(agy.reviews[SPEC], token)
    if kept == "retained":
        review["extra"] = f"kept {token}"
    agy.scenario(
        clean_stream(
            tool_step(
                "run_command",
                {"CommandLine": f"grep -rn {token} src"},
                shell_output(f"src/example.py:1:{token}\n"),
            ),
            result=result_event(response=f"Echoed {token}."),
        ),
        reviews={**agy.reviews, SPEC: review},
    )
    result = run_gate()
    assert result.ok, result.to_envelope()
    assert result.exit_code == (4 if kept == "retained" else 0)
    reviews = state_path.parent / "reviews"
    assert list(reviews.glob("*.antigravity.tmp.*")), "fixture: no temp directory"
    assert (captures(state_path) != []) == (kept == "retained")
    leaked = files_holding(host, token.encode())
    assert leaked == [], f"FAIL AC-18: the credential reached kept files {leaked}"


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            text for item in value.items() for part in item for text in _strings(part)
        ]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _decoded_views(data: bytes) -> list[bytes]:
    """A kept file's bytes, and every base64 string value inside it when it is
    JSON, JSON lines or YAML, decoded."""
    views = [data]
    documents: list[object] = []
    try:
        documents.append(json.loads(data))
    except ValueError:
        try:
            documents.extend(json.loads(line) for line in data.splitlines() if line)
        except ValueError:
            try:
                documents.append(yaml.safe_load(data))
            except yaml.YAMLError:
                pass
    for text in (text for document in documents for text in _strings(document)):
        if len(text) >= 8 and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", text):
            try:
                views.append(base64.b64decode(text, validate=True))
            except ValueError:
                pass
    return views


def decoded_holding(
    root: Path, needle: bytes, exempt: Collection[Path] = ()
) -> list[str]:
    """Kept files under ``root`` holding ``needle`` as written or inside a
    decoded base64 value."""
    return [
        str(path.relative_to(root))
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path not in exempt
        and any(needle in view for view in _decoded_views(path.read_bytes()))
    ]


def run_gate_in_child(gate: str = SPEC) -> None:
    """Run the gate in a forked process that then exits, as the process of a
    ``heddle run-gate`` command does."""
    import multiprocessing

    child = multiprocessing.get_context("fork").Process(target=run_gate, args=(gate,))
    child.start()
    child.join(timeout=60)
    assert child.exitcode == 0, "fixture: the forked gate run did not finish"


@pytest.mark.parametrize("route", ["recorded", "retained", "unreadable-stream"])
def test_ac18_unredactable_stream_fails_the_run_and_reaches_no_kept_artifact(
    tmp_path, monkeypatch, agy, route
):
    """AC-18: when the raw stream cannot be replaced by its redacted copy, the
    run fails as ``execution-failure``, nothing is recorded or retained, the
    failure summary names no raw stream, the gate log says why, and no kept
    artifact holds the credential, decoded or not. Only the run temp
    directory's raw stream does, as written: no failure message replaces it.
    Once that run's process has exited, the next run's recovery pass redacts
    it. ``route`` is how the run would otherwise have ended: recorded,
    retained, or failed reading its stream after the launch.

    Injection seam: ``os.replace`` refuses the redacted copy (a staged file
    whose name ends ``.redacting``) with ``PermissionError``, in the forked
    process that runs the first gate; for ``unreadable-stream``, reading the
    finished launch's stream also raises ``OSError`` there.
    """
    require_runner("AC-18")
    host, state_path = spec_host(tmp_path, monkeypatch)
    token = credential(f"unredactable-{route}")
    review = echo(agy.reviews[SPEC], token)
    if route == "retained":
        review["extra"] = f"kept {token}"
    agy.scenario(
        clean_stream(
            tool_step(
                "run_command",
                {"CommandLine": f"grep -rn {token} src"},
                shell_output(f"src/example.py:1:{token}\n"),
            ),
            result=result_event(response=f"Echoed {token}."),
        ),
        reviews={**agy.reviews, SPEC: review},
    )
    real_replace = os.replace

    def refused(source, destination, *args, **kwargs):
        if str(source).endswith(".redacting"):
            raise PermissionError(13, "Permission denied", str(destination))
        return real_replace(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", refused)
        if route == "unreadable-stream":
            unreadable_stream(patch)
        run_gate_in_child()

    rows = lane_rows(state_path)
    assert rows and rows[-1].get("failure_reason") == "execution-failure", (
        f"FAIL AC-18: the unredacted run recorded {rows[-1:]}"
    )
    assert captures(state_path) == [], "FAIL AC-18: the unredacted run was retained"
    summary = summary_for(state_path)
    assert summary["artifacts"]["raw_out"] is None, (
        f"FAIL AC-18: the failure summary names the unredacted stream "
        f"{summary['artifacts']['raw_out']}"
    )
    assert re.search(r"could not be redacted", gate_log(state_path)), (
        "FAIL AC-18: the gate log does not say the stream could not be redacted"
    )
    (launch,) = agy.launches()
    raw = [
        path
        for path in temp_dir_of(launch).iterdir()
        if path.is_file() and token.encode() in path.read_bytes()
    ]
    assert len(raw) == 1, (
        f"FAIL AC-18: the run temp directory does not keep the raw stream as "
        f"written for recovery: {raw}"
    )
    leaked = decoded_holding(host, token.encode(), exempt=raw)
    assert leaked == [], f"FAIL AC-18: the credential reached kept files {leaked}"

    agy.scenario()
    assert run_gate().ok
    leaked = decoded_holding(host, token.encode())
    assert leaked == [], f"FAIL AC-18: recovery left the credential in {leaked}"


# The redactor streams in 1 MiB blocks; each case puts a cut inside a
# credential that ends a run of more than 5,000 credential-alphabet bytes.
REDACT_BLOCK = 1 << 20


def _cut_secret(shape: str) -> str:
    body = hashlib.sha256(shape.encode()).hexdigest() * 4
    return {
        "gh": credential("block-cut"),
        "gh-long": "gh" + "p_" + body[:50],
        "aiza": "AI" + "za" + body[:35],
        "sk-long": "s" + "k-" + body * 24,
        "sk-across-two-cuts": "s" + "k-" + "Q" * (REDACT_BLOCK + REDACT_BLOCK // 2),
    }[shape]


CUT_CASES = {
    "gh-cut-after-its-prefix": ("gh", 4),
    "gh-cut-mid-body": ("gh", 20),
    "gh-cut-before-its-last-byte": ("gh", 39),
    "gh-long-cut-past-its-deciding-bytes": ("gh-long", 45),
    "aiza-cut-mid-body": ("aiza", 19),
    "sk-long-cut-near-its-start": ("sk-long", 10),
    "sk-across-two-cuts": ("sk-across-two-cuts", 10),
}


@pytest.mark.parametrize("case", list(CUT_CASES))
def test_ac18_streamed_redaction_does_not_depend_on_where_blocks_are_cut(
    tmp_path, case
):
    """AC-18: a credential at the end of a 5,000-byte run of credential-alphabet
    bytes, with a 1 MiB block cut inside it, is redacted by the streamed file
    redactor exactly as the whole-buffer redactor redacts the same bytes."""
    from heddle.gate.io import redact_bytes, redact_file

    shape, into = CUT_CASES[case]
    secret = _cut_secret(shape).encode()
    head = b'{"event": "tool", "output": "' + b"x" * 5_000
    start = REDACT_BLOCK - into
    padding = start - len(head) - len(b'{"pad": ""}\n')
    data = (
        b'{"pad": "'
        + b" " * padding
        + b'"}\n'
        + head
        + secret
        + b'"}\n{"event": "finish"}\n'
    )
    assert data[start : start + len(secret)] == secret, "fixture: misplaced secret"
    path = tmp_path / "raw.out"
    path.write_bytes(data)

    assert redact_file(path) is True
    redacted = path.read_bytes()
    assert redacted == redact_bytes(data), (
        f"FAIL AC-18: the streamed redaction of {case} differs from the "
        "whole-buffer redaction"
    )
    assert secret[:12] not in redacted and secret[-12:] not in redacted, (
        f"FAIL AC-18: part of the {case} credential survived redaction"
    )
