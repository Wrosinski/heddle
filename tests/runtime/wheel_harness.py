"""
Offline installed-wheel harness shared by adoption portability tests.

This is the third built-wheel occurrence in the runtime suite.  It extracts
the build/install mechanics already exercised independently by
``test_search_acceptance.py`` and ``test_packaged_prompt_floor.py`` without
weakening either parent's product assertions.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import textwrap
import venv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_DISTRIBUTIONS = (
    "PyYAML",
    "loguru",
    "markdown-it-py",
    "mdurl",
    "jsonschema",
    "attrs",
    "jsonschema-specifications",
    "referencing",
    "rpds-py",
    "typing-extensions",
)


@dataclass(frozen=True)
class InstalledWheel:
    root: Path
    environment: Path
    python: Path
    command: Path
    site_packages: Path
    env: dict[str, str]
    forbidden_log: Path
    network_log: Path

    def run(
        self,
        *args: str,
        cwd: Path,
        timeout: int = 120,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return run(
            [str(self.command), *args],
            cwd=cwd,
            env=self.env if env is None else env,
            timeout=timeout,
        )

    def python_probe(
        self,
        source: str,
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        return run(
            [str(self.python), "-c", source],
            cwd=cwd,
            env=self.env,
        )


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def build_installed_wheel(root: Path) -> InstalledWheel:
    """Build once and install offline into an otherwise clean virtualenv."""
    dist_dir = root / "dist"
    build = run(
        [
            "poetry",
            "build",
            "--format",
            "wheel",
            "--output",
            str(dist_dir),
            "--no-interaction",
        ],
        cwd=REPO_ROOT,
    )
    assert build.returncode == 0, (
        "FAIL installed-wheel fixture: wheel build failed\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )
    wheels = list(dist_dir.glob("heddle-*.whl"))
    assert len(wheels) == 1, (
        f"FAIL installed-wheel fixture: expected one wheel, found {len(wheels)}"
    )

    environment = root / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / "bin" / "python"
    command = environment / "bin" / "heddle"
    site_query = run(
        [
            str(python),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        cwd=root,
    )
    assert site_query.returncode == 0, (
        "FAIL installed-wheel fixture: cannot resolve isolated site-packages\n"
        f"{site_query.stderr}"
    )
    site_packages = Path(site_query.stdout.strip())
    for distribution in RUNTIME_DISTRIBUTIONS:
        copy_distribution(distribution, site_packages)

    install = run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            str(wheels[0]),
        ],
        cwd=root,
    )
    assert install.returncode == 0, (
        "FAIL installed-wheel fixture: offline wheel install failed\n"
        f"stdout:\n{install.stdout}\nstderr:\n{install.stderr}"
    )

    forbidden_log = root / "forbidden-checkout-reads.log"
    network_log = root / "forbidden-network-events.log"
    _install_forbidden_source_audit_hook(site_packages)
    clean_env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        clean_env.pop(key, None)
    clean_env.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PATH": f"{environment / 'bin'}:/usr/bin:/bin",
            "HEDDLE_FORBIDDEN_SOURCE_ROOT": str(REPO_ROOT.resolve()),
            "HEDDLE_FORBIDDEN_SOURCE_LOG": str(forbidden_log),
            "HEDDLE_NETWORK_AUDIT_LOG": str(network_log),
        }
    )
    return InstalledWheel(
        root=root,
        environment=environment,
        python=python,
        command=command,
        site_packages=site_packages,
        env=clean_env,
        forbidden_log=forbidden_log,
        network_log=network_log,
    )


def copy_distribution(distribution_name: str, target_site: Path) -> None:
    distribution = importlib.metadata.distribution(distribution_name)
    source_root = Path(str(distribution.locate_file("")))
    target_root = target_site.resolve()
    copied = 0
    for relative in distribution.files or ():
        source = source_root / relative
        if not source.is_file():
            continue
        target = (target_site / relative).resolve()
        if not target.is_relative_to(target_root):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    assert copied, (
        f"FAIL installed-wheel fixture: no files copied for {distribution_name}"
    )


def make_git_host(root: Path, name: str) -> tuple[Path, Path]:
    host = root / name
    nested = host / "nested" / "deeper"
    nested.mkdir(parents=True)
    initialized = run(["git", "init", "-q", str(host)], cwd=root)
    assert initialized.returncode == 0, (
        f"FAIL installed-wheel fixture: git init failed: {initialized.stderr}"
    )
    return host, nested


def parse_envelope(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stripped = result.stdout.strip()
    assert stripped, (
        "FAIL installed-wheel envelope: command emitted no JSON\n"
        f"exit={result.returncode}\nstderr:\n{result.stderr}"
    )
    envelope, end = json.JSONDecoder().raw_decode(stripped)
    assert isinstance(envelope, dict) and end == len(stripped), (
        "FAIL installed-wheel envelope: stdout must contain exactly one JSON object\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return envelope


def admit_installed_feature(
    installed: InstalledWheel,
    *,
    host: Path,
    cwd: Path,
    payload_dir: Path,
    feature: str,
    area: str,
    prepare_payload: dict[str, Any],
    policy_payload: dict[str, Any],
    flow: str | None = None,
) -> dict[str, Any]:
    """Prepare, confirm, and start a feature through the installed command."""
    brief = host / "brief.md"
    if not brief.exists():
        brief.write_text("# Research\n\nDeliver one declared behavior.\n")
    payload_dir.mkdir(parents=True, exist_ok=True)
    prepare = payload_dir / f"{feature}-prepare.json"
    policy = payload_dir / f"{feature}-policy.json"
    prepare.write_text(json.dumps(prepare_payload))
    policy.write_text(json.dumps(policy_payload))
    commands = (
        (
            "feature",
            "prepare",
            feature,
            "--area",
            area,
            "--from-file",
            str(prepare),
            "--json",
        ),
        (
            "feature",
            "policy",
            feature,
            "--from-file",
            str(policy),
            "--json",
        ),
    )
    for command in commands:
        result = installed.run(*command, cwd=cwd)
        envelope = parse_envelope(result)
        assert result.returncode == 0 and envelope["ok"], (command, envelope)
    start = ["feature", "start", feature]
    if flow is not None:
        start.extend(("--flow", flow))
    start.append("--json")
    result = installed.run(*start, cwd=cwd)
    envelope = parse_envelope(result)
    assert result.returncode == 0 and envelope["ok"], (start, envelope)
    return envelope


def snapshot_tree(root: Path) -> tuple[tuple[Any, ...], ...]:
    """Recursive no-write oracle including bytes, links, modes, and mtimes."""
    entries: list[tuple[Any, ...]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            entries.append((relative, "symlink", os.readlink(path), info.st_mode))
        elif stat.S_ISREG(info.st_mode):
            entries.append(
                (
                    relative,
                    "file",
                    path.read_bytes(),
                    info.st_mode,
                    info.st_ino,
                    info.st_mtime_ns,
                )
            )
        elif stat.S_ISDIR(info.st_mode):
            entries.append((relative, "dir", info.st_mode, info.st_mtime_ns))
        else:
            entries.append((relative, "other", info.st_mode))
    return tuple(entries)


def file_hashes(host: Path, relative_paths: tuple[str, ...]) -> dict[str, str]:
    import hashlib

    return {
        relative: hashlib.sha256((host / relative).read_bytes()).hexdigest()
        for relative in relative_paths
    }


def write_executable(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def write_claude_shim(bin_dir: Path, event: str, log_path: Path) -> Path:
    """Embed the shared literal; the installed host never imports tests/."""
    return write_executable(
        bin_dir / "claude",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from pathlib import Path

            stdin = "" if sys.stdin.isatty() else sys.stdin.read()
            environment = {{
                "CLAUDECODE": os.environ.get("CLAUDECODE"),
                "HEDDLE_AGENT_SESSION": os.environ.get("HEDDLE_AGENT_SESSION"),
                "CLAUDE_CODE_EFFORT_LEVEL": os.environ.get(
                    "CLAUDE_CODE_EFFORT_LEVEL"
                ),
                "BASH_DEFAULT_TIMEOUT_MS": os.environ.get("BASH_DEFAULT_TIMEOUT_MS"),
                "BASH_MAX_TIMEOUT_MS": os.environ.get("BASH_MAX_TIMEOUT_MS"),
                "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": os.environ.get(
                    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"
                ),
            }}
            with Path({str(log_path)!r}).open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {{
                            "argv": sys.argv[1:],
                            "environment": environment,
                            "stdin": stdin,
                        }}
                    )
                )
                stream.write("\\n")
            sys.stdout.write({event!r})
            """
        ),
    )


def write_scripted_claude_shim(
    bin_dir: Path, events: Sequence[str], log_path: Path
) -> Path:
    """A claude double that answers its n-th call with ``events[n]``.

    Calls are numbered by the records already in ``log_path``, and each call
    logs like ``write_claude_shim``. A call beyond the script still logs, then
    exits 3 with no output, so an unexpected launch fails its gate instead of
    receiving a canned review.
    """
    return write_executable(
        bin_dir / "claude",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from pathlib import Path

            EVENTS = {list(events)!r}
            LOG = Path({str(log_path)!r})
            stdin = "" if sys.stdin.isatty() else sys.stdin.read()
            environment = {{
                key: os.environ.get(key)
                for key in (
                    "CLAUDECODE",
                    "HEDDLE_AGENT_SESSION",
                    "CLAUDE_CODE_EFFORT_LEVEL",
                    "BASH_DEFAULT_TIMEOUT_MS",
                    "BASH_MAX_TIMEOUT_MS",
                    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK",
                )
            }}
            index = (
                len(LOG.read_text(encoding="utf-8").splitlines())
                if LOG.exists()
                else 0
            )
            with LOG.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {{
                            "argv": sys.argv[1:],
                            "environment": environment,
                            "stdin": stdin,
                        }}
                    )
                )
                stream.write("\\n")
            if index >= len(EVENTS):
                sys.stderr.write(f"unscripted provider call {{index + 1}}\\n")
                sys.exit(3)
            sys.stdout.write(EVENTS[index])
            """
        ),
    )


def _install_forbidden_source_audit_hook(site_packages: Path) -> None:
    audit_module = textwrap.dedent(
        """\
            import os
            import sys
            import threading
            from pathlib import Path

            _forbidden = os.environ.get("HEDDLE_FORBIDDEN_SOURCE_ROOT")
            _log = os.environ.get("HEDDLE_FORBIDDEN_SOURCE_LOG")
            _network_log = os.environ.get("HEDDLE_NETWORK_AUDIT_LOG")
            _network_events = {
                "socket.connect",
                "socket.connect_ex",
                "socket.getaddrinfo",
                "socket.gethostbyaddr",
                "socket.gethostbyname",
                "socket.gethostbyname_ex",
            }
            _audit_state = threading.local()

            def _resolved(value):
                if not value:
                    return None
                try:
                    return Path(value).resolve()
                except (OSError, RuntimeError, TypeError, ValueError) as error:
                    raise RuntimeError(
                        "invalid installed-audit path configuration: " + repr(value)
                    ) from error

            _forbidden_path = _resolved(_forbidden)
            _log_path = _resolved(_log)
            _network_log_path = _resolved(_network_log)
            if _forbidden_path is not None:
                for _label, _candidate in (
                    ("audit module", Path(__file__).resolve()),
                    ("source diagnostic log", _log_path),
                    ("network diagnostic log", _network_log_path),
                ):
                    if _candidate is not None and _candidate.is_relative_to(
                        _forbidden_path
                    ):
                        raise RuntimeError(
                            "installed audit configuration places "
                            + _label
                            + " inside the forbidden source root: "
                            + str(_candidate)
                        )
            if (
                _log_path is not None
                and _network_log_path is not None
                and _log_path == _network_log_path
            ):
                raise RuntimeError(
                    "installed audit source and network diagnostic logs must differ"
                )

            def _write_diagnostic(path, text):
                if path is None or getattr(_audit_state, "writing", False):
                    return
                _audit_state.writing = True
                try:
                    with open(path, "a", encoding="utf-8") as stream:
                        stream.write(text + "\\n")
                finally:
                    _audit_state.writing = False

            def _audit(event, args):
                if getattr(_audit_state, "writing", False):
                    return
                if event in _network_events:
                    _write_diagnostic(
                        _network_log_path, event + ": " + repr(args[1:])
                    )
                    raise RuntimeError(
                        "network access forbidden in the installed portability lane: "
                        + event
                    )
                if event != "open" or not args or _forbidden_path is None:
                    return
                raw = args[0]
                if not isinstance(raw, (str, bytes, os.PathLike)):
                    return
                try:
                    resolved = Path(raw).resolve()
                except (OSError, RuntimeError, TypeError, ValueError):
                    return
                if not resolved.is_relative_to(_forbidden_path):
                    return
                _write_diagnostic(_log_path, str(resolved))
                raise RuntimeError(
                    "forbidden checkout-root read from installed Heddle: "
                    + str(resolved)
                )

            sys.addaudithook(_audit)
            """
    )
    (site_packages / "heddle_portability_audit.py").write_text(
        audit_module,
        encoding="utf-8",
    )
    # Some distributions ship a stdlib-level sitecustomize.py, which wins
    # module resolution over a venv copy. The .pth import is processed while
    # the isolated site-packages directory is added and therefore guarantees
    # the hook is live; the sitecustomize shim preserves the conventional path
    # on platforms where it is not shadowed. Import caching prevents duplicate
    # hook registration when both paths execute.
    (site_packages / "heddle-portability-audit.pth").write_text(
        "import heddle_portability_audit\n",
        encoding="utf-8",
    )
    (site_packages / "sitecustomize.py").write_text(
        "import heddle_portability_audit\n",
        encoding="utf-8",
    )


def write_review_shims(
    bin_dir: Path,
    responses: Path,
    log_path: Path,
    *,
    control: Path | None = None,
) -> Path:
    """Doubled claude and codex CLIs that return ``responses/<cli>.json``.

    Each call logs argv, the controlled environment and stdin like
    ``write_claude_shim``; the test writes the canonical review content each
    CLI returns before running the gate.

    With ``control``, each call first reads ``{"label": ..., "mode": ...}``
    from that file. ``rendezvous`` writes ``started-<label>-<cli>`` beside it
    and waits up to 60 s for the other CLI's start marker (missing: exit 3);
    codex then also waits for ``finished-<label>-claude``, so the secondary
    always finishes first. ``solo`` neither waits nor requires a sibling.
    """
    for cli in ("claude", "codex"):
        write_executable(
            bin_dir / cli,
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import fcntl
                import json
                import os
                import sys
                from pathlib import Path

                CLI = {cli!r}
                stdin = "" if sys.stdin.isatty() else sys.stdin.read()
                environment = {{
                    key: os.environ.get(key)
                    for key in (
                        "CLAUDECODE",
                        "HEDDLE_AGENT_SESSION",
                        "CLAUDE_CODE_EFFORT_LEVEL",
                        "BASH_DEFAULT_TIMEOUT_MS",
                        "BASH_MAX_TIMEOUT_MS",
                    )
                }}
                record = json.dumps(
                    {{
                        "cli": CLI,
                        "argv": sys.argv[1:],
                        "environment": environment,
                        "stdin": stdin,
                    }}
                )
                # Concurrent slots share the log: append each record under a lock.
                with Path({str(log_path)!r}).open("a", encoding="utf-8") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    stream.write(record + "\\n")
                    stream.flush()
                CONTROL = {str(control) if control else None!r}
                label = mode = None
                if CONTROL is not None:
                    import time

                    setting = json.loads(Path(CONTROL).read_text())
                    label, mode = setting["label"], setting["mode"]
                    markers = Path(CONTROL).parent

                    def wait_for(name):
                        deadline = time.monotonic() + 60
                        while not (markers / name).exists():
                            if time.monotonic() > deadline:
                                sys.stderr.write("rendezvous timed out: " + name)
                                sys.exit(3)
                            time.sleep(0.05)

                    if mode == "rendezvous":
                        other = "codex" if CLI == "claude" else "claude"
                        (markers / f"started-{{label}}-{{CLI}}").write_text("")
                        wait_for(f"started-{{label}}-{{other}}")
                        if CLI == "codex":
                            wait_for(f"finished-{{label}}-claude")
                content = Path({str(responses)!r}, CLI + ".json").read_text()
                if CLI == "claude":
                    event = {{
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "structured_output": json.loads(content),
                    }}
                    sys.stdout.write(json.dumps(event) + "\\n")
                else:
                    argv = sys.argv[1:]
                    Path(argv[argv.index("-o") + 1]).write_text(content)
                    events = (
                        {{
                            "type": "item.completed",
                            "item": {{"type": "agent_message", "text": content}},
                        }},
                        {{"type": "turn.completed", "usage": {{}}}},
                    )
                    for item in events:
                        sys.stdout.write(json.dumps(item) + "\\n")
                if mode == "rendezvous":
                    sys.stdout.flush()
                    (markers / f"finished-{{label}}-{{CLI}}").write_text("")
                """
            ),
        )
    return bin_dir


# --------------------------------------------------------------------------- #
# The doubled ``agy`` for the installed Antigravity lane
# --------------------------------------------------------------------------- #
AGY_WRAPPED_CAP = 192_000
AGY_WRAPPED_HEADER = len(b"<USER_REQUEST>\n")
AGY_WRAPPED_TRAILER = 400

_AGY_SHIM_SOURCE = r'''
import base64
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(__HOME__)
BASELINE = set(__BASELINE__)
CAP = __CAP__
HEADER = __HEADER__
TRAILER = __TRAILER__
MARKER = "HEDDLE_AGENT_SESSION"
CONVERSATION = "c0ffee00-0000-4000-8000-00000000e2e0"
CHILD = """
import os, time
kind = __KIND__
if kind == "daemon":
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
path = __PIDFILE__
with open(path + ".tmp", "w") as handle:
    handle.write(str(os.getpid()))
os.replace(path + ".tmp", path)
time.sleep(600)
"""


def load(name, default):
    path = HOME / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append(name, row):
    with open(HOME / name, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(row) + "\n")
        handle.flush()


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
    version = SCENARIO.get("version", "1.5.16")
    append("versions.jsonl", {"pid": os.getpid(), "version": version})
    if version is None:
        sys.stderr.write("agy: version information is unavailable\n")
        sys.exit(2)
    print(version)
    sys.exit(0)

# One line per physical launch, before anything else can fail.
append("invocations.log", {"pid": os.getpid(), "at": time.time()})


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
wrapped = [HEADER + len(text.encode("utf-8")) + TRAILER for text in texts]
PATHLIKE = re.compile(r"[\w.@+~/-]*/[\w.@+~/-]+")
observed = {}


def visit(text, base, depth):
    for token in PATHLIKE.findall(text):
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
    found = sorted(
        (text.find(digest), other)
        for other, digest in digests.items()
        if other != path and digest in text
    )
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
# The launch token is a variable Heddle adds for this launch. Only digests of
# the HEDDLE_ variables Heddle added are kept; the marker is kept by value.
launch_digests = {
    name: hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()
    for name, value in os.environ.items()
    if name.startswith("HEDDLE_") and name != MARKER and name not in BASELINE
}
refused = any(size > CAP for size in wrapped)
append(
    "observations.jsonl",
    {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "start": start_time(os.getpid()),
        "argv": argv,
        "cwd": CWD,
        "marker": os.environ.get(MARKER),
        "launch_digests": launch_digests,
        "role": ROLE,
        "stdin": base64.b64encode(raw).decode("ascii"),
        "stdin_errors": stdin_errors,
        "events": [event.get("event") for event in events if isinstance(event, dict)],
        "contents": texts,
        "wrapped": wrapped,
        "refused": refused,
        "files": {
            path: base64.b64encode(data).decode("ascii")
            for path, data in observed.items()
        },
        "index": index_path,
        "chunks": chunks,
        "schema": schema_text,
    },
)
if refused:
    # Real agy truncates silently and reports success; fail loudly instead.
    emit(f"error: the wrapped user message exceeds {CAP} bytes")
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
    pidfile = str(HOME / f"child-{os.getpid()}-{kind}-{time.monotonic_ns()}.pid")
    code = CHILD.replace("__KIND__", repr(kind)).replace("__PIDFILE__", repr(pidfile))
    subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.monotonic() + 20
    while not os.path.exists(pidfile):
        if time.monotonic() > deadline:
            raise SystemExit("a tokened child did not start")
        time.sleep(0.01)
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
    done = subprocess.run(
        ["heddle", *spec["argv"]],
        cwd=spec["cwd"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    try:
        code = json.loads(done.stdout)["error"]["code"]
    except (ValueError, KeyError, TypeError):
        code = None
    append(
        "attempts.jsonl",
        {
            "cli": "antigravity",
            "launch": os.getpid(),
            "argv": spec["argv"],
            "exit": done.returncode,
            "code": code,
            "marker": os.environ.get(MARKER),
        },
    )
    output = (
        "Created At: 2026-01-01T00:00:00Z\nCompleted At: 2026-01-01T00:00:01Z\n"
        f"Command exited with code {done.returncode}.\n\nCommand output:\n"
        + (done.stdout + done.stderr).replace("\n", "\r\n")
    )
    tool(
        "run_command",
        {"CommandLine": "heddle " + " ".join(spec["argv"]), "Cwd": spec["cwd"]},
        output,
    )


def read(path):
    data = Path(path).read_bytes() if os.path.isfile(path) else b""
    lines = data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
    append("reads.jsonl", {"launch": os.getpid(), "path": path, "bytes": len(data)})
    tool("read_file", {"Path": path}, f"{lines} lines, {len(data)} bytes")


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
        for path in {"all": chunks, "all-but-last": chunks[:-1]}.get(value, []):
            read(path)
    elif name == "$read":
        read(value)
    elif name == "$raw":
        emit(value)
    elif name == "$children":
        for kind in value:
            spawn(kind)
    elif name == "$hold":
        append("holding.jsonl", {"launch": os.getpid(), "role": ROLE})
        deadline = time.monotonic() + 600
        while not (HOME / value).exists() and time.monotonic() < deadline:
            time.sleep(0.02)
    elif name == "$heddle":
        attempt(value)
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


def write_agy_shim(bin_dir: Path, home: Path, *, baseline: Sequence[str] = ()) -> Path:
    """A doubled ``agy`` for the installed lane; never the real executable.

    The source is an embedded literal, so the installed host never imports
    ``tests/``. ``home`` holds the control and observation files:

    - ``scenario.json`` (the test writes it, and may rewrite it between runs):
      ``{"version": ..., "ops": [...], "roles": {role: [...]}, "reviews":
      {role: content}}``. Ops are synthetic stream events and ``$``-directives
      (``$step``, ``$tool``, ``$read_chunks``, ``$read``, ``$raw``,
      ``$children``, ``$hold``, ``$heddle``, ``$exit``, ``$result``), the same
      vocabulary as the in-process double, so its stream builders apply.
    - ``invocations.log``: one line per physical launch, written first.
    - ``observations.jsonl``: per launch, before any reply: argv, working
      directory, the gate marker, SHA-256 digests of the ``HEDDLE_*``
      variables Heddle added (the launch token among them; never a value),
      its own and its parent's process ID, stdin, the decoded message
      contents and wrapped sizes, and the files the message points at.
    - ``children.jsonl``, ``holding.jsonl``, ``reads.jsonl``,
      ``attempts.jsonl`` and ``versions.jsonl``.

    A message whose wrapped size (15-byte header, decoded content, 400-byte
    trailer allowance) exceeds 192,000 bytes is refused: real ``agy``
    truncates it silently and reports success, so the double fails loudly.
    ``baseline`` names the environment variables present before Heddle runs;
    they are never recorded.

    Activation seam: the double finds the launch token as a variable whose
    name starts with ``HEDDLE_``, other than the ``HEDDLE_AGENT_SESSION``
    marker, that the launch adds to its baseline environment. The plan
    requires a ``HEDDLE_``-prefixed token without fixing its name; this prefix
    is the one the runner must meet for the token to be observed, as
    ``HEDDLE_GATE_RUN`` does today.
    """
    home.mkdir(parents=True, exist_ok=True)
    source = (
        _AGY_SHIM_SOURCE.replace("__HOME__", repr(str(home)))
        .replace("__BASELINE__", repr(sorted(baseline)))
        .replace("__CAP__", repr(AGY_WRAPPED_CAP))
        .replace("__HEADER__", repr(AGY_WRAPPED_HEADER))
        .replace("__TRAILER__", repr(AGY_WRAPPED_TRAILER))
    )
    return write_executable(bin_dir / "agy", "#!/usr/bin/env python3\n" + source)


_LANE_SHIM_SOURCE = r"""
import fcntl
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

CLI = __CLI__
HOME = Path(__HOME__)
MARKER = "HEDDLE_AGENT_SESSION"


def append(name, row):
    with open(HOME / name, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(row) + "\n")
        handle.flush()


def emit(value):
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


argv = sys.argv[1:]
sys.stdin.read()
script = json.loads((HOME / f"{CLI}.json").read_text(encoding="utf-8"))
append(
    f"{CLI}.jsonl",
    {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "argv": argv,
        "cwd": os.getcwd(),
        "marker": os.environ.get(MARKER),
    },
)
if script.get("hold"):
    append("holding.jsonl", {"launch": os.getpid(), "cli": CLI})
    deadline = time.monotonic() + 600
    while not (HOME / script["hold"]).exists() and time.monotonic() < deadline:
        time.sleep(0.02)

calls = []
for step in script.get("steps", []):
    if "read" in step:
        path = step["read"]
        calls.append(("read", path, Path(path).read_text(encoding="utf-8"), 0))
    elif "heddle" in step:
        spec = step["heddle"]
        done = subprocess.run(
            ["heddle", *spec["argv"]],
            cwd=spec["cwd"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        try:
            code = json.loads(done.stdout)["error"]["code"]
        except (ValueError, KeyError, TypeError):
            code = None
        append(
            "attempts.jsonl",
            {
                "cli": CLI,
                "launch": os.getpid(),
                "argv": spec["argv"],
                "exit": done.returncode,
                "code": code,
                "marker": os.environ.get(MARKER),
            },
        )
        command = "heddle " + " ".join(shlex.quote(item) for item in spec["argv"])
        calls.append(("heddle", command, done.stdout + done.stderr, done.returncode))

review = json.dumps(script["review"])
if CLI == "claude":
    model = argv[argv.index("--model") + 1] if "--model" in argv else None
    emit(
        {
            "type": "system",
            "subtype": "init",
            "cwd": os.getcwd(),
            "model": model,
            "tools": ["Read", "Grep", "Glob", "Bash"],
        }
    )
    for number, (kind, target, output, _code) in enumerate(calls, start=1):
        call_id = f"toolu_{number:02d}"
        if kind == "read":
            name, inputs = "Read", {"file_path": target}
        else:
            name, inputs = "Bash", {"command": target}
        use = {"type": "tool_use", "id": call_id, "name": name, "input": inputs}
        emit(
            {
                "type": "assistant",
                "parent_tool_use_id": None,
                "message": {"role": "assistant", "content": [use]},
            }
        )
        answer = {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": output,
            "is_error": False,
        }
        emit(
            {
                "type": "user",
                "parent_tool_use_id": None,
                "message": {"role": "user", "content": [answer]},
            }
        )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": script["review"],
        }
    )
else:
    Path(argv[argv.index("-o") + 1]).write_text(review, encoding="utf-8")
    emit({"type": "thread.started", "thread_id": "thread-0001"})
    emit({"type": "turn.started"})
    for number, (kind, target, output, code) in enumerate(calls, start=1):
        line = f"cat {shlex.quote(target)}" if kind == "read" else target
        base = {
            "id": f"item_{number}",
            "type": "command_execution",
            "command": f"/bin/bash -lc {shlex.quote(line)}",
        }
        started = {"aggregated_output": "", "exit_code": None, "status": "in_progress"}
        emit({"type": "item.started", "item": {**base, **started}})
        finished = {"aggregated_output": output, "exit_code": code}
        finished["status"] = "completed"
        emit({"type": "item.completed", "item": {**base, **finished}})
    final = {"id": "item_final", "type": "agent_message", "text": review}
    emit({"type": "item.completed", "item": final})
    usage = {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}
    emit({"type": "turn.completed", "usage": usage})
"""


def write_lane_shims(bin_dir: Path, home: Path) -> Path:
    """Doubled ``claude`` and ``codex`` for the installed Antigravity scenarios.

    Each call reads ``home/<cli>.json``: ``{"review": content, "steps": [...],
    "hold": name | None}``. A step is ``{"read": path}`` (a Claude ``Read`` or
    a Codex ``cat``) or ``{"heddle": {"argv": [...], "cwd": path}}``, which
    runs the installed ``heddle`` from the reviewer child, so it inherits the
    child's environment; its exit and error code go to ``attempts.jsonl``.
    With ``hold``, the call waits for ``home/<hold>`` before replying. Each
    call logs argv, working directory, the marker and its process IDs to
    ``home/<cli>.jsonl``.
    """
    home.mkdir(parents=True, exist_ok=True)
    for cli in ("claude", "codex"):
        source = _LANE_SHIM_SOURCE.replace("__CLI__", repr(cli)).replace(
            "__HOME__", repr(str(home))
        )
        write_executable(bin_dir / cli, "#!/usr/bin/env python3\n" + source)
    return bin_dir
