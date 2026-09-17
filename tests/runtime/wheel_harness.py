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
