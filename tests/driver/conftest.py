"""
Shared fixtures for the ``heddle drive`` executor and autopilot policy tests.

With ``--import-mode=importlib`` and no ``__init__.py`` in test packages, test
modules cannot import a sibling ``conftest`` by name — shared helpers arrive
through fixtures (the core/gate/write idiom). The cross-package helper module
``tests.runtime.write_path_helpers`` IS importable by full path and is reused here.

autopilot fixture workspaces live under ``tests/driver/fixtures/workspaces/`` —
deliberately OUTSIDE the global corpus sweep in
``tests/runtime/test_fixtures.py`` (``tests/fixtures/workspaces/**``). They
carry the net-new ``flow`` top-level key (and integration/completion carry net-new
decision/session fields), which the current ``state.yaml`` v0 vocabulary
rejects — exactly the red-phase signal — and must not contaminate the shared
corpus conformance contract that is green today.
"""

from __future__ import annotations

import json
import os
import textwrap
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.driver.constants import AUTO_TIER2_FIXTURE, AUTO_TIER2_SLUG
from tests.driver.helpers import write_executable_script
from tests.runtime.write_path_helpers import copy_host as _copy_host
from tests.runtime.write_path_helpers import read_yaml as _read_yaml
from tests.runtime.write_path_helpers import write_yaml as _write_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DRIVER_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "workspaces"

# The five wire keys of the result envelope.
ENVELOPE_KEYS_SUCCESS = {"ok", "schema_version", "data", "diagnostics", "next_actions"}
ENVELOPE_KEYS_FAILURE = {"ok", "schema_version", "error", "diagnostics", "next_actions"}


def _parse_single_envelope(stdout: str) -> dict:
    """
    Parse stdout as exactly one JSON envelope.
    """
    stripped = stdout.strip()
    envelope, end = json.JSONDecoder().raw_decode(stripped)
    assert isinstance(envelope, dict), "FAIL: stdout is not a single JSON object"
    assert end == len(stripped), (
        "FAIL: stdout carries content besides the JSON envelope"
    )
    return envelope


def _assert_envelope_shape(envelope: dict) -> None:
    keys = set(envelope.keys())
    assert keys in (ENVELOPE_KEYS_SUCCESS, ENVELOPE_KEYS_FAILURE), (
        f"FAIL: envelope keys {sorted(keys)} are not exactly the five wire keys"
    )
    if "data" in envelope:
        assert envelope["ok"] is True, "FAIL: data present but ok is not true"
    else:
        assert envelope["ok"] is False, "FAIL: error present but ok is not false"
        assert set(envelope["error"].keys()) == {
            "code",
            "message",
            "hint",
            "details",
        }, "FAIL: error object must serialize exactly {code, message, hint, details}"
    assert isinstance(envelope["diagnostics"], list)
    assert isinstance(envelope["next_actions"], list)


@pytest.fixture
def run_cli(capsys):
    """
    Invoke the installed console-script entry in-process.

        Returns ``(exit_code, stdout, stderr)`` — the core/gate/write in-process idiom.
        The live E2E test exercises the real subprocess path separately.

    """

    def _run(argv: list[str]) -> tuple[int, str, str]:
        from heddle.cli import main

        code = main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def envelope_tools():
    """Envelope parsing/shape helpers (mirrors tests/runtime/conftest.py)."""
    return SimpleNamespace(
        parse=_parse_single_envelope,
        assert_shape=_assert_envelope_shape,
        success_keys=ENVELOPE_KEYS_SUCCESS,
        failure_keys=ENVELOPE_KEYS_FAILURE,
    )


@pytest.fixture
def run_json(run_cli, envelope_tools):
    def _run(argv: list[str]) -> tuple[int, dict, str, str]:
        code, out, err = run_cli(argv)
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        return code, envelope, out, err

    return _run


@pytest.fixture
def driver_corpus(tmp_path):
    """autopilot fixture-workspace paths + copy/read/write helpers.

    ``copy(name)`` copies ``tests/driver/fixtures/workspaces/<name>`` into a
    fresh ``tmp_path`` subdir (never mutate a committed fixture in place) and
    returns the copied host root. ``state(host, slug)`` resolves the
    ``plans/<slug>/state.yaml`` path inside a copied host.
    """

    copies: dict[str, int] = {}

    def _copy(name: str) -> Path:
        copies[name] = copies.get(name, 0) + 1
        target_name = name if copies[name] == 1 else f"{name}-{copies[name]}"
        return _copy_host(tmp_path, DRIVER_FIXTURES / name, name=target_name)

    def _state(host: Path, slug: str) -> Path:
        return host / "plans" / slug / "state.yaml"

    def _arm(host: Path, slug: str, **overrides) -> Path:
        state_path = _state(host, slug)
        state = _read_yaml(state_path)
        state.update(overrides)
        _write_yaml(state_path, state)
        return state_path

    def _mutate(host: Path, slug: str, mutator: Callable[[dict], None]) -> Path:
        state_path = _state(host, slug)
        state = _read_yaml(state_path)
        mutator(state)
        _write_yaml(state_path, state)
        return state_path

    return SimpleNamespace(
        root=DRIVER_FIXTURES,
        copy=_copy,
        state=_state,
        arm=_arm,
        mutate=_mutate,
        read_yaml=_read_yaml,
        write_yaml=_write_yaml,
    )


@pytest.fixture
def prepared_auto_tier2(driver_corpus, monkeypatch):
    def _make(
        *,
        chdir: bool = True,
        mutate: Callable[[dict], None] | None = None,
        **overrides,
    ) -> tuple[Path, Path]:
        host = driver_corpus.copy(AUTO_TIER2_FIXTURE)
        state_path = driver_corpus.state(host, AUTO_TIER2_SLUG)
        if overrides:
            state_path = driver_corpus.arm(host, AUTO_TIER2_SLUG, **overrides)
        if mutate is not None:
            state_path = driver_corpus.mutate(host, AUTO_TIER2_SLUG, mutate)
        if chdir:
            monkeypatch.chdir(host)
        return host, state_path

    return _make


@pytest.fixture
def auto_tier2_workspace(prepared_auto_tier2):
    def _make(
        *,
        chdir: bool = True,
        mutate: Callable[[dict], None] | None = None,
        **overrides,
    ) -> Path:
        host, _state_path = prepared_auto_tier2(chdir=chdir, mutate=mutate, **overrides)
        return host

    return _make


@pytest.fixture
def arranged_auto_tier2(prepared_auto_tier2):
    def _arrange(
        *,
        mutate: Callable[[dict], None] | None = None,
        **overrides,
    ) -> tuple[Path, Path]:
        return prepared_auto_tier2(chdir=True, mutate=mutate, **overrides)

    return _arrange


def _write_fake_cli(bin_dir: Path, name: str, log: Path) -> Path:
    script = bin_dir / name
    return write_executable_script(
        script,
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tests.driver.fake_claude_runtime import main

            raise SystemExit(main({name!r}, {str(log)!r}))
            """
        ),
    )


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A scripted fake ``claude`` on PATH — the session-double (Architecture →
    *The session-spawn facility*).

    It captures every invocation's argv + stdin to ``argv_log`` and emits a
    canned terminal ``stream-json`` success envelope, so loop-level ACs
    (AC-5/AC-9/AC-14) and the spawn-profile AC (AC-6) run deterministically
    with zero billed model calls. ``calls()`` returns the parsed invocation
    records; ``last_argv()`` the most recent argv list.
    """

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    argv_log = tmp_path / "cli-invocations.jsonl"
    _write_fake_cli(bin_dir, "claude", argv_log)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def _calls() -> list[dict]:
        if not argv_log.exists():
            return []
        return [json.loads(line) for line in argv_log.read_text().splitlines() if line]

    def _last_argv() -> list[str]:
        records = _calls()
        assert records, "FAIL: the fake claude was never invoked"
        return records[-1]["argv"]

    def _enable_autopilot_completion() -> None:
        monkeypatch.setenv("HEDDLE_FAKE_CLAUDE_AUTOPILOT_COMPLETE", "1")

    return SimpleNamespace(
        bin_dir=bin_dir,
        argv_log=argv_log,
        calls=_calls,
        last_argv=_last_argv,
        enable_autopilot_completion=_enable_autopilot_completion,
    )


@pytest.fixture
def notify_capture(tmp_path):
    """A capture script for the host-configured notify command (AC-15).

    Returns a namespace whose ``command`` is a shell-invocable string that
    appends its stdin (the result envelope) to ``payload``. ``received()``
    returns the captured envelopes.
    """

    bin_dir = tmp_path / "notifybin"
    bin_dir.mkdir()
    payload = tmp_path / "notify-payloads.jsonl"
    script = bin_dir / "notify-capture"
    write_executable_script(
        script,
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys
            from pathlib import Path
            data = "" if sys.stdin.isatty() else sys.stdin.read()
            with Path({str(payload)!r}).open("a", encoding="utf-8") as fh:
                fh.write(data.replace(chr(10), " ") + chr(10))
            """
        ),
    )

    def _received() -> list[str]:
        if not payload.exists():
            return []
        return [line for line in payload.read_text().splitlines() if line]

    return SimpleNamespace(command=str(script), payload=payload, received=_received)
