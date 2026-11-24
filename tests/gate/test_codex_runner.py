"""Tests for Codex gate runner integration."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from heddle.gate.cli import GateArgs, resolve_gate_execution
from heddle.gate.io import GatePaths
from heddle.gate.registry import GATES
from heddle.gate.runners.codex import execute_codex
from heddle.gate.types import (
    ExecutionResult,
    GateInvocationOverrides,
    PromptTransport,
)
from tests.driver.helpers import write_executable_script
from tests.operational_model_helpers import FEATURE
from tests.proof_continuity_helpers import command
from tests.tiering_helpers import SOL, entry
from tests.tiering_review_helpers import current_host, review_content

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


def _gate_paths(tmp_path: Path):
    from heddle.gate.io import GatePaths

    temp_dir = tmp_path / "tmp"
    return GatePaths(
        output=tmp_path / "out.md",
        log=tmp_path / "out.log",
        summary=tmp_path / "out.gate-summary.json",
        prompt=temp_dir / "prompt.md",
        raw_out=temp_dir / "raw.out",
        last_message=temp_dir / "last-message.txt",
        json_message=temp_dir / "json-message.txt",
        filtered_jsonl=temp_dir / "events.jsonl",
        command_output=temp_dir / "command-output.txt",
        temp_dir=temp_dir,
    )


@REQUIRES_IMPL
def test_gate_agent_env_marks_managed_reviewer_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heddle.gate.runners.environment import gate_agent_env

    monkeypatch.setenv("EXISTING_ENV_VALUE", "kept")
    monkeypatch.delenv("PRIVATE_APP_SKIP_AGENT_STOP_LINT", raising=False)

    env = gate_agent_env()

    assert env["HEDDLE_AGENT_SESSION"] == "gate"
    assert "PRIVATE_APP_SKIP_AGENT_STOP_LINT" not in env
    assert env["EXISTING_ENV_VALUE"] == "kept"
    assert os.environ["EXISTING_ENV_VALUE"] == "kept"


@REQUIRES_IMPL
@pytest.mark.parametrize(
    ("model", "effort", "expected_model", "expected_effort"),
    (("gpt-test", "low", "gpt-test", "low"),),
)
def test_codex_command_pins_effective_model_and_effort(
    tmp_path: Path,
    model: str | None,
    effort: str | None,
    expected_model: str,
    expected_effort: str,
) -> None:
    from heddle.gate.cli import GateArgs, resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.runners.codex import _codex_command
    from heddle.gate.types import GateInvocationOverrides

    args = GateArgs(
        gate_name="spec-review",
        cli="codex",
        cli_bin="codex-test",
        workdir=tmp_path,
    )
    invocation = resolve_gate_execution(
        GATES["spec-review"],
        GateInvocationOverrides(
            cli="codex",
            model=model,
            reasoning_effort=effort,
        ),
    )
    prepared = SimpleNamespace(
        invocation=invocation,
        output_contract=GATES["spec-review"].output_contract,
    )
    command = _codex_command(args, _gate_paths(tmp_path), prepared)

    assert command[0:2] == ["codex-test", "exec"]
    assert command[command.index("-m") + 1] == expected_model
    assert (
        command[command.index("-c") + 1]
        == f'model_reasoning_effort="{expected_effort}"'
    )


@pytest.mark.parametrize("length", [1_048_575, 1_048_576, 1_048_577])
def test_codex_large_input_preserves_exact_capture_at_character_boundary(
    tmp_path: Path, length: int
) -> None:
    import hashlib
    import json

    from heddle.gate.runners.codex import _codex_stdin
    from heddle.gate.types import PromptTransport

    paths = _gate_paths(tmp_path / "host with spaces")
    paths.temp_dir.mkdir(parents=True)
    text = "é" * (length - 4) + "TAIL"
    raw = text.encode("utf-8")
    paths.prompt.write_bytes(raw)
    prepared = SimpleNamespace(
        transport=PromptTransport(system=None, stdin=text), diff_text=""
    )
    source = _codex_stdin(paths, prepared)
    assert paths.prompt.read_bytes() == raw
    assert prepared.transport.stdin == text
    if length <= 1_048_576:
        assert source == paths.prompt
        assert not (paths.temp_dir / "launch.md").exists()
        assert not (paths.temp_dir / "input-index.json").exists()
        return
    launch = source.read_text()
    assert len(launch) < 4096
    manifest = json.loads(launch.splitlines()[1])
    assert Path(manifest["input_file"]).read_bytes() == raw
    assert manifest["bytes"] == len(raw)
    assert manifest["sha256"] == hashlib.sha256(raw).hexdigest()
    index_bytes = Path(manifest["index_file"]).read_bytes()
    assert len(index_bytes) == manifest["index_bytes"]
    assert hashlib.sha256(index_bytes).hexdigest() == manifest["index_sha256"]
    assert "completely in bounded chunks" in launch
    assert "truncated tool response is not complete coverage" in launch
    assert "captured-file/v2" in paths.log.read_text()


@pytest.mark.parametrize(
    "mutation", ["missing", "changed", "existing-launch", "existing-index"]
)
def test_codex_large_input_refuses_bad_capture_or_launch_collision(
    tmp_path: Path, mutation: str
) -> None:
    from heddle.gate.runners.codex import _codex_stdin
    from heddle.gate.types import PromptTransport

    paths = _gate_paths(tmp_path)
    paths.temp_dir.mkdir()
    text = "x" * 1_048_577
    paths.prompt.write_text(text)
    prepared = SimpleNamespace(
        transport=PromptTransport(system=None, stdin=text), diff_text=""
    )
    launch = paths.temp_dir / "launch.md"
    index = paths.temp_dir / "input-index.json"
    if mutation == "missing":
        paths.prompt.unlink()
    elif mutation == "changed":
        paths.prompt.write_text("different capture")
    elif mutation == "existing-launch":
        launch.write_text("another writer")
    else:
        index.write_text("another index writer")
    expected = {
        "missing": FileNotFoundError,
        "changed": ValueError,
        "existing-launch": FileExistsError,
        "existing-index": FileExistsError,
    }[mutation]
    with pytest.raises(expected):
        _codex_stdin(paths, prepared)
    if mutation == "existing-launch":
        assert launch.read_text() == "another writer"
    if mutation == "existing-index":
        assert index.read_text() == "another index writer" and not launch.exists()


@pytest.mark.parametrize("tamper", ["none", "capture", "index", "missing-index"])
def test_codex_large_input_real_process_reads_capture_and_tamper_refuses(
    tmp_path: Path, tamper: str
) -> None:
    import json
    import sys

    from heddle.gate.cli import GateArgs, resolve_gate_execution
    from heddle.gate.registry import GATES
    from heddle.gate.runners.codex import execute_codex
    from heddle.gate.types import GateInvocationOverrides, PromptTransport

    paths = _gate_paths(tmp_path / "host with spaces")
    paths.temp_dir.mkdir(parents=True)
    # One oversized line exposes tools that accidentally rely on line counts.
    text = "instructions\n" + "x" * 1_048_577 + "\nTAIL\n"
    paths.prompt.write_text(text)
    prepared = SimpleNamespace(
        output_contract=GATES["spec-review"].output_contract,
        invocation=resolve_gate_execution(
            GATES["spec-review"], GateInvocationOverrides(cli="codex")
        ),
        transport=PromptTransport(system=None, stdin=text),
        diff_text="",
    )
    shim = tmp_path / "local-provider"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import hashlib,json,sys\nfrom pathlib import Path\n"
        "launch=sys.stdin.read()\nassert len(launch)<4096\n"
        "record=json.loads(launch.splitlines()[1])\n"
        "source=Path(record['input_file'])\n"
        "raw=source.read_bytes()\nassert len(raw)==record['bytes']\n"
        "assert hashlib.sha256(raw).hexdigest()==record['sha256']\n"
        "assert raw.startswith(b'instructions\\n') and raw.endswith(b'\\nTAIL\\n')\n"
        "index_path=Path(record['index_file'])\n"
        "encoded=index_path.read_bytes()\n"
        "assert len(encoded)==record['index_bytes']\n"
        "assert hashlib.sha256(encoded).hexdigest()==record['index_sha256']\n"
        "index=json.loads(encoded)\n"
        "assert index['bytes']==len(raw) and index['sha256']==record['sha256']\n"
        "assert sorted(index['read_order'])==list(range(len(index['ranges'])))\n"
        "parts={}\n"
        "for i in index['read_order']:\n"
        " row=index['ranges'][i]\n"
        " parts[i]=raw[row['start']:row['end']]\n"
        " assert parts[i].decode().encode()==parts[i]\n"
        "assert b''.join(parts[i] for i in sorted(parts))==raw\n"
        "Path(sys.argv[sys.argv.index('-o')+1]).write_text('{}')\n"
        + {
            "none": "",
            "capture": "source.write_text('tampered')\n",
            "index": "index_path.write_text('tampered')\n",
            "missing-index": "index_path.unlink()\n",
        }[tamper]
        + "print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':'{}'}}))\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    shim.chmod(0o755)
    args = GateArgs(
        gate_name="spec-review",
        cli="codex",
        cli_bin=str(shim),
        workdir=tmp_path,
        timeout_seconds=5,
        inactivity_timeout_seconds=3,
        poll_seconds=0.02,
        term_grace_seconds=0.1,
    )
    if tamper != "none":
        expected = FileNotFoundError if tamper == "missing-index" else ValueError
        message = "input-index" if tamper == "missing-index" else "captured Codex"
        with pytest.raises(expected, match=message):
            execute_codex(args, paths, prepared)
    else:
        result = execute_codex(args, paths, prepared)
        assert result.cli_exit == 0 and result.failure_reason is None
        assert paths.prompt.read_text() == text
        assert json.loads(paths.last_message.read_text()) == {}


# ---------------------------------------------------------------------------
# AC-21 (m6): the lane-neutral `reviews/` rule on the Codex lane, through the
# real runner with a doubled provider, as an assigned run. The synthetic
# streams follow the CLI's current `--json` shapes: `command_execution` items
# carrying a shell-wrapped `command` and `aggregated_output`, plus the
# documented `file_change` item, which returns no result.
#
# Activation seam, owned by the m6 implementer:
# - the runner takes the feature's `reviews/` directory as
#   `paths.output.parent` and its own run temp directory as `paths.temp_dir`;
# - each `command_execution` item starts in the `-C` directory the runner
#   passed, which defaults to the feature directory in the native layout;
# - warnings go to the gate log. The unrecognized-shape warning names the lane
#   and the call kind and contains "unrecognized". The ambiguous-context
#   warning names the lane and contains "ambiguous".
# ---------------------------------------------------------------------------

OUT_OF_SCOPE = "out-of-scope-access"
CodexStep = tuple[str, ...]


@dataclass(frozen=True)
class ReviewLayout:
    """A native host: the feature's `reviews/` holds sibling output and this run."""

    root: Path
    feature: Path
    reviews: Path
    temp: Path
    sibling: Path


def _review_layout(tmp_path: Path) -> ReviewLayout:
    root = tmp_path / "host"
    feature = root / "plans" / "sample-feature"
    reviews = feature / "reviews"
    temp = reviews / "spec-review.tmp.k3x9q2"
    temp.mkdir(parents=True)
    (temp / "prompt.md").write_text("review", encoding="utf-8")
    (feature / "notes").mkdir()
    (root / "docs" / "reviews").mkdir(parents=True)
    sibling = reviews / "peer-review.md"
    sibling.write_text("# Sibling review\n\nTODO tighten\n", encoding="utf-8")
    (feature / "plan.md").write_text(
        "# Plan\n\nSee reviews/peer-review.md.\n", encoding="utf-8"
    )
    (feature / "state.yaml").write_text(
        "runs:\n- artifact: reviews/peer-review.md\n", encoding="utf-8"
    )
    return ReviewLayout(root, feature, reviews, temp, sibling)


def _layout_paths(layout: ReviewLayout) -> GatePaths:
    temp = layout.temp
    return GatePaths(
        output=layout.reviews / "spec-review.md",
        log=layout.reviews / "spec-review.log",
        summary=layout.reviews / "spec-review.gate-summary.json",
        prompt=temp / "prompt.md",
        raw_out=temp / "raw.out",
        last_message=temp / "last-message.txt",
        json_message=temp / "json-message.txt",
        filtered_jsonl=temp / "events.jsonl",
        command_output=temp / "command-output.txt",
        temp_dir=temp,
    )


def _run(script: str, output: str = "", shell: str = "/bin/bash") -> CodexStep:
    return ("command", script, output, shell, "aggregated_output")


def _unrecognized_run(script: str, output: str) -> CodexStep:
    """A command item whose output moved to a field the parser does not know."""
    return ("command", script, output, "/bin/bash", "output")


def _file_change(path: Path) -> CodexStep:
    return ("file_change", str(path))


def _codex_events(steps: list[CodexStep], review_text: str) -> list[dict]:
    events: list[dict] = [
        {"type": "thread.started", "thread_id": "thread-0001"},
        {"type": "turn.started"},
    ]
    for index, step in enumerate(steps, start=1):
        item_id = f"item_{index}"
        if step[0] == "file_change":
            events.append(
                {
                    "type": "item.completed",
                    "item": {
                        "id": item_id,
                        "type": "file_change",
                        "changes": [{"path": step[1], "kind": "add"}],
                        "status": "completed",
                    },
                }
            )
            continue
        _, script, output, shell, field = step
        wrapped = f"{shell} -lc {shlex.quote(script)}"
        base = {"id": item_id, "type": "command_execution", "command": wrapped}
        events.append(
            {
                "type": "item.started",
                "item": {**base, field: "", "exit_code": None, "status": "in_progress"},
            }
        )
        events.append(
            {
                "type": "item.completed",
                "item": {**base, field: output, "exit_code": 0, "status": "completed"},
            }
        )
    events.append(
        {
            "type": "item.completed",
            "item": {"id": "item_final", "type": "agent_message", "text": review_text},
        }
    )
    events.append(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
        }
    )
    return events


def _run_codex_stream(
    tmp_path: Path,
    layout: ReviewLayout,
    steps: list[CodexStep],
    *,
    workdir: Path | None = None,
) -> tuple[ExecutionResult, str]:
    """Run one synthetic stream through the real Codex runner, assigned.

    With no ``workdir`` the runner passes its default `-C`, the feature
    directory in the native layout.
    """
    review_text = json.dumps(review_content())
    recorded = tmp_path / "codex-stream.jsonl"
    recorded.write_text(
        "".join(
            json.dumps(event) + "\n" for event in _codex_events(steps, review_text)
        ),
        encoding="utf-8",
    )
    binary = write_executable_script(
        tmp_path / "codex-double",
        f"#!{sys.executable}\n"
        "import sys\n"
        "sys.stdin.read()\n"
        "target = sys.argv[sys.argv.index('-o') + 1]\n"
        "with open(target, 'w', encoding='utf-8') as handle:\n"
        f"    handle.write({review_text!r})\n"
        f"with open({str(recorded)!r}, encoding='utf-8') as source:\n"
        "    sys.stdout.write(source.read())\n",
    )
    gate_type = GATES["spec-review"]
    prepared = SimpleNamespace(
        output_contract=gate_type.output_contract,
        invocation=resolve_gate_execution(
            gate_type, GateInvocationOverrides(cli="codex")
        ),
        transport=PromptTransport(system=None, stdin="review"),
        diff_text="",
        assignment_id="assignment",
    )
    args = GateArgs(
        gate_name="spec-review",
        cli="codex",
        cli_bin=str(binary),
        workdir=workdir,
        timeout_seconds=10,
        inactivity_timeout_seconds=5,
        poll_seconds=0.02,
        term_grace_seconds=0.1,
    )
    paths = _layout_paths(layout)
    result = execute_codex(args, paths, prepared)
    log = paths.log.read_text(encoding="utf-8") if paths.log.exists() else ""
    return result, log


def _reason(result: ExecutionResult) -> str | None:
    return None if result.failure_reason is None else result.failure_reason.value


def _assert_out_of_scope(result: ExecutionResult, case: str) -> None:
    assert _reason(result) == OUT_OF_SCOPE, (
        f"FAIL AC-21 ({case}): the Codex runner returned {_reason(result)!r}; "
        f"`reviews/` access must fail as {OUT_OF_SCOPE!r} whatever the object says"
    )
    assert result.response_warning is None, (
        f"FAIL AC-21 ({case}): the failed run kept a capture for interpretation"
    )


def _assert_recorded(result: ExecutionResult, case: str) -> None:
    assert _reason(result) is None and result.response_warning is None, (
        f"FAIL AC-21 ({case}): an in-scope Codex run failed as "
        f"{_reason(result)!r} (warning {result.response_warning!r})"
    )


def _lane_warnings(log: str, keyword: str, kind: str | None = None) -> list[str]:
    return [
        line
        for line in log.splitlines()
        if keyword in line.lower()
        and "codex" in line.lower()
        and (kind is None or kind in line)
    ]


# Each case: whether the runner's `-C` is the checkout root (True) or its
# default, the feature directory (False), and the stream's steps.
CodexCase = tuple[bool, Callable[[ReviewLayout], list[CodexStep]]]

NAMED_REVIEWS_ACCESS: dict[str, CodexCase] = {
    "bare-cat-under-default-C": (
        False,
        lambda layout: [_run("cat reviews/peer-review.md", "# Sibling review")],
    ),
    "absolute-read": (
        False,
        lambda layout: [_run(f"cat {layout.sibling}", "# Sibling review")],
    ),
    "zsh-wrapped-read": (
        False,
        lambda layout: [
            _run("cat reviews/peer-review.md", "# Sibling", shell="/usr/bin/zsh")
        ],
    ),
    "listing": (
        False,
        lambda layout: [_run(f"ls {layout.reviews}", "peer-review.md")],
    ),
    "search-root": (
        False,
        lambda layout: [_run(f"rg -n nothing-matches {layout.reviews}")],
    ),
    "shell-write": (False, lambda layout: [_run("printf x > reviews/notes.md")]),
    "file-change-write": (
        False,
        lambda layout: [_file_change(layout.reviews / "notes.md")],
    ),
    "cd-chain-from-the-root": (
        True,
        lambda layout: [
            _run("cd plans/sample-feature && cat reviews/peer-review.md", "#")
        ],
    ),
    "parent-spelling": (
        False,
        lambda layout: [_run("cd notes && cat ../reviews/peer-review.md", "#")],
    ),
    "new-item-starts-at-C-after-unknown": (
        False,
        lambda layout: [
            _run('cd "$D"'),
            _run("cat reviews/peer-review.md", "# Sibling review"),
        ],
    ),
}


@pytest.mark.parametrize("case", sorted(NAMED_REVIEWS_ACCESS))
def test_ac21_named_reviews_access_fails_on_the_codex_lane(
    tmp_path: Path, case: str
) -> None:
    """
    Red discriminator (AC-21 (a); m6): a read, listing, search or write of a
    path named under the feature's `reviews/`, outside the run temp directory,
    fails as out-of-scope-access once the shell wrapper is removed. Each item
    starts in the `-C` directory: a bare `cat reviews/<file>` under the default
    `-C`, the feature directory in the native layout, is the common spelling.
    """
    layout = _review_layout(tmp_path)
    at_root, steps = NAMED_REVIEWS_ACCESS[case]

    result, _ = _run_codex_stream(
        tmp_path, layout, steps(layout), workdir=layout.root if at_root else None
    )

    _assert_out_of_scope(result, case)


SHOWN_REVIEWS_ENTRIES: dict[str, CodexCase] = {
    "wrapped-search": (
        False,
        lambda layout: [
            _run(
                "rg -n TODO",
                "plan.md:5:TODO later\nreviews/peer-review.md:3:TODO tighten",
            )
        ],
    ),
    "cd-led-search": (
        True,
        lambda layout: [
            _run("cd plans && rg -l TODO", "sample-feature/reviews/peer-review.md")
        ],
    ),
    "recursive-listing": (
        False,
        lambda layout: [
            _run(
                "ls -R .",
                ".:\nnotes\nplan.md\nreviews\nstate.yaml\n\n./notes:\n\n"
                "./reviews:\npeer-review.md\nspec-review.tmp.k3x9q2\n",
            )
        ],
    ),
    "find-listing": (
        False,
        lambda layout: [
            _run(
                "find . -name '*.md'",
                "./plan.md\n./reviews/spec-review.tmp.k3x9q2/prompt.md\n"
                "./reviews/peer-review.md",
            )
        ],
    ),
}


@pytest.mark.parametrize("case", sorted(SHOWN_REVIEWS_ENTRIES))
def test_ac21_shown_reviews_entries_fail_on_the_codex_lane(
    tmp_path: Path, case: str
) -> None:
    """
    Red discriminator (AC-21 (b); m6): a wrapped search or listing whose
    `aggregated_output` shows an entry under `reviews/` fails as
    out-of-scope-access. Path-led lines resolve against the item's directory,
    moved by a leading `cd`.
    """
    layout = _review_layout(tmp_path)
    at_root, steps = SHOWN_REVIEWS_ENTRIES[case]

    result, _ = _run_codex_stream(
        tmp_path, layout, steps(layout), workdir=layout.root if at_root else None
    )

    _assert_out_of_scope(result, case)


IN_SCOPE_CONTROLS: dict[str, CodexCase] = {
    "own-temp-read": (
        False,
        lambda layout: [_run(f"cat {layout.temp / 'prompt.md'}", "review")],
    ),
    "own-temp-listing": (
        False,
        lambda layout: [_run(f"ls {layout.temp}", "prompt.md\nraw.out")],
    ),
    "printed-state-file": (
        False,
        lambda layout: [
            _run("cat state.yaml", "runs:\n- artifact: reviews/peer-review.md")
        ],
    ),
    "printed-plan-lines": (
        False,
        lambda layout: [
            _run("sed -n 1,20p plan.md", "# Plan\n\nSee reviews/peer-review.md.")
        ],
    ),
    "hidden-ancestor-search": (
        False,
        lambda layout: [_run("rg -l TODO | wc -l", "2")],
    ),
    "unclassified-command-output": (
        False,
        lambda layout: [_run("python3 tools/summary.py", "reviews/peer-review.md")],
    ),
    "list-with-a-content-printing-element": (
        False,
        lambda layout: [
            _run("rg -l TODO; cat plan.md", "reviews/peer-review.md\n# Plan")
        ],
    ),
    "each-item-starts-at-a-C-outside-the-feature": (
        True,
        lambda layout: [
            _run(f"cd {layout.feature}"),
            _run("cat reviews/peer-review.md", "cat: reviews/peer-review.md: missing"),
        ],
    ),
    "unrelated-directory-in-the-same-command": (
        False,
        lambda layout: [
            _run(
                f"cd {layout.root / 'docs'} && cat reviews/peer-review.md",
                "cat: reviews/peer-review.md: missing",
            )
        ],
    ),
    "file-change-outside-reviews": (
        False,
        lambda layout: [_file_change(layout.feature / "notes" / "scratch.md")],
    ),
}


@pytest.mark.parametrize("case", sorted(IN_SCOPE_CONTROLS))
def test_ac21_in_scope_controls_record_on_the_codex_lane(
    tmp_path: Path, case: str
) -> None:
    """
    Survivor pin (AC-21 (c), (d), (e); m6): own-temp access, printed file
    content, an ancestor search whose results are hidden, unclassified output,
    a list with a content-printing element, a relative `reviews/` name in an
    item that starts outside the feature or after a `cd` into an unrelated
    directory, and a write outside `reviews/` all record today and must keep
    recording once the check lands.
    """
    layout = _review_layout(tmp_path)
    at_root, steps = IN_SCOPE_CONTROLS[case]

    result, _ = _run_codex_stream(
        tmp_path, layout, steps(layout), workdir=layout.root if at_root else None
    )

    _assert_recorded(result, case)


UNKNOWN_DIRECTORY: dict[str, CodexCase] = {
    "default-C": (
        False,
        lambda layout: [_run('cd "$D" && cat reviews/peer-review.md', "#")],
    ),
    "relative-cd-after-unknown": (
        True,
        lambda layout: [
            _run('cd "$D" && cd plans/sample-feature && cat reviews/peer-review.md')
        ],
    ),
}


@pytest.mark.parametrize("case", sorted(UNKNOWN_DIRECTORY))
def test_ac21_unparseable_cd_grounds_no_refusal_and_warns_on_codex(
    tmp_path: Path, case: str
) -> None:
    """
    Red discriminator (AC-21; m6): an unparseable `cd` leaves the item's
    directory unknown, even under the default feature-directory `-C`, and a
    relative `cd` from there keeps it unknown. A relative `reviews/` name there
    grounds no refusal; the run records and the gate log carries the
    ambiguous-context warning.
    """
    layout = _review_layout(tmp_path)
    at_root, steps = UNKNOWN_DIRECTORY[case]

    result, log = _run_codex_stream(
        tmp_path, layout, steps(layout), workdir=layout.root if at_root else None
    )

    _assert_recorded(result, case)
    assert _lane_warnings(log, "ambiguous"), (
        f"FAIL AC-21 ({case}): no ambiguous-context warning naming the codex "
        f"lane in the gate log:\n{log}"
    )


def test_ac21_absolute_reviews_path_after_unparseable_cd_fails_on_codex(
    tmp_path: Path,
) -> None:
    """
    Red discriminator (AC-21 (a); m6): an absolute `reviews/` path does not
    depend on the directory, so it fails even after an unparseable `cd`.
    """
    layout = _review_layout(tmp_path)

    result, _ = _run_codex_stream(
        tmp_path, layout, [_run(f'cd "$D" && cat {layout.sibling}', "#")]
    )

    _assert_out_of_scope(result, "absolute path after an unparseable cd")


@pytest.mark.parametrize("recognized_first", [False, True], ids=["all", "mixed"])
def test_ac21_unrecognized_result_shapes_warn_per_item_on_codex(
    tmp_path: Path, recognized_first: bool
) -> None:
    """
    Red discriminator (AC-21 (f); m6): a `command_execution` item whose output
    is not in `aggregated_output` is not inspected. The run records, and the
    gate log carries one unrecognized-shape warning per such item, naming the
    lane and the call kind. In the mixed stream, the item with a recognized
    `aggregated_output` and the `file_change` control, a kind that returns no
    result, log none.
    """
    layout = _review_layout(tmp_path)
    first = (
        _run("git status --short", " M src/app.py")
        if recognized_first
        else _unrecognized_run("git status --short", " M src/app.py")
    )
    steps = [
        first,
        _unrecognized_run("rg -n TODO src", "src/app.py:4:TODO"),
        _file_change(layout.feature / "notes" / "scratch.md"),
    ]

    result, log = _run_codex_stream(tmp_path, layout, steps)

    _assert_recorded(result, "unrecognized result shapes")
    warnings = _lane_warnings(log, "unrecognized", "command_execution")
    assert len(warnings) == (1 if recognized_first else 2), (
        "FAIL AC-21 (f): expected one unrecognized-shape warning naming the "
        f"codex lane and command_execution per unrecognized item, got {warnings}"
    )
    assert not _lane_warnings(log, "unrecognized", "file_change"), (
        "FAIL AC-21 (f): a file_change item returns no result and must not warn"
    )


# ---------------------------------------------------------------------------
# AC-2 (m1): the reviewer child carries the gate-session marker and a mutating
# `heddle` command it runs is refused, while the gate runtime still records the
# review in process.
# ---------------------------------------------------------------------------

REVIEWER_MUTATION = ("commands", "set", "smoke_test", "--command", "true")


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _marked_codex_reviewer(path: Path, *, record: Path, disposable: Path) -> Path:
    """A doubled Codex that reports its environment and tries one mutation."""
    review = json.dumps(review_content())
    return write_executable_script(
        path,
        f"#!{sys.executable}\n"
        "import json, os, shlex, subprocess, sys\n"
        "sys.stdin.read()\n"
        f"mutation = {list(REVIEWER_MUTATION)!r}\n"
        "attempt = subprocess.run(\n"
        "    [sys.executable, '-m', 'heddle', *mutation,\n"
        f"     '--feature', {FEATURE!r}, '--json'],\n"
        f"    cwd={str(disposable)!r}, capture_output=True, text=True, timeout=120,\n"
        ")\n"
        f"with open({str(record)!r}, 'w', encoding='utf-8') as handle:\n"
        "    json.dump({'marker': os.environ.get('HEDDLE_AGENT_SESSION'),\n"
        "               'exit': attempt.returncode, 'stdout': attempt.stdout,\n"
        "               'stderr': attempt.stderr}, handle)\n"
        f"review = {review!r}\n"
        "target = sys.argv[sys.argv.index('-o') + 1]\n"
        "with open(target, 'w', encoding='utf-8') as handle:\n"
        "    handle.write(review)\n"
        "wrapped = '/bin/bash -lc ' + shlex.quote('heddle ' + ' '.join(mutation))\n"
        "item = {'id': 'item_1', 'type': 'command_execution', 'command': wrapped,\n"
        "        'aggregated_output': attempt.stdout + attempt.stderr,\n"
        "        'exit_code': attempt.returncode, 'status': 'completed'}\n"
        "events = [\n"
        "    {'type': 'thread.started', 'thread_id': 'thread-0001'},\n"
        "    {'type': 'turn.started'},\n"
        "    {'type': 'item.completed', 'item': item},\n"
        "    {'type': 'item.completed', 'item': {'id': 'item_2',\n"
        "     'type': 'agent_message', 'text': review}},\n"
        "    {'type': 'turn.completed', 'usage': {}},\n"
        "]\n"
        "for event in events:\n"
        "    print(json.dumps(event))\n",
    )


def test_ac2_marked_codex_reviewer_child_mutation_is_refused_and_review_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Red discriminator (AC-2; m1): from an unmarked parent, a real Codex gate
    run launches a doubled reviewer through the real launcher. The child's
    environment carries `HEDDLE_AGENT_SESSION=gate` (already true), and its
    mutating `heddle` command on a disposable host copy exits 3 with
    `gate-session-refused`, leaving that copy byte-identical (red until the
    dispatcher guard lands). The gate runtime still records the review in
    process.
    """
    monkeypatch.delenv("HEDDLE_AGENT_SESSION", raising=False)
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=SOL)},
    )
    disposable = tmp_path / "disposable"
    shutil.copytree(host, disposable, symlinks=True)
    before = _tree_bytes(disposable)
    shims = tmp_path / "shims"
    shims.mkdir()
    record = tmp_path / "reviewer-child.json"
    _marked_codex_reviewer(shims / "codex", record=record, disposable=disposable)
    monkeypatch.setenv("PATH", f"{shims}{os.pathsep}{os.environ['PATH']}")

    code, response = command(capsys, "run-gate", "spec-review")

    child = json.loads(record.read_text(encoding="utf-8"))
    assert child["marker"] == "gate", child
    assert child["exit"] == 3, (
        "FAIL AC-2: the marked reviewer child's mutating heddle command was not "
        f"refused: exit {child['exit']}, output {child['stdout'] or child['stderr']}"
    )
    refusal = json.loads(child["stdout"])
    assert refusal["ok"] is False
    assert refusal["error"]["code"] == "gate-session-refused", refusal
    assert _tree_bytes(disposable) == before, (
        "FAIL AC-2: the refused mutation changed the disposable host copy"
    )
    assert code == 0 and response["data"]["accepted"], response
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    run = state["gates"][-1]["runs"][-1]
    assert run["execution_config"]["cli"] == "codex"
    assert (state_path.parent / run["artifact"]).is_file()


# ---------------------------------------------------------------------------
# AC-21 (m6), as an assigned run: Codex's bare sibling read under its default
# `-C` fails through `run-gate` and leaves nothing interpretable for credit.
# ---------------------------------------------------------------------------


def _replaying_codex(path: Path, steps: list[CodexStep]) -> Path:
    """A doubled Codex that writes `-o` and replays a fixed stream."""
    review_text = json.dumps(review_content())
    stream = "".join(
        json.dumps(event) + "\n" for event in _codex_events(steps, review_text)
    )
    return write_executable_script(
        path,
        f"#!{sys.executable}\n"
        "import sys\n"
        "sys.stdin.read()\n"
        "target = sys.argv[sys.argv.index('-o') + 1]\n"
        "with open(target, 'w', encoding='utf-8') as handle:\n"
        f"    handle.write({review_text!r})\n"
        f"sys.stdout.write({stream!r})\n",
    )


def _assigned_codex_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    steps: list[CodexStep],
) -> tuple[Path, int, dict[str, object]]:
    """An assigned spec-review `run-gate` on a doubled Codex under its default `-C`."""
    monkeypatch.delenv("HEDDLE_AGENT_SESSION", raising=False)
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=SOL)},
    )
    sibling = host / "plans" / FEATURE / "reviews" / "peer-review.md"
    sibling.parent.mkdir(exist_ok=True)
    sibling.write_text("# Sibling review\n\nTODO tighten\n", encoding="utf-8")
    shims = tmp_path / "shims"
    shims.mkdir()
    _replaying_codex(shims / "codex", steps)
    monkeypatch.setenv("PATH", f"{shims}{os.pathsep}{os.environ['PATH']}")
    code, response = command(capsys, "run-gate", "spec-review")
    return state_path, code, response


def _assert_refused_assigned_run(
    state_path: Path, code: int, response: dict[str, object], *, cli: str, case: str
) -> None:
    """Out-of-scope-access, nothing interpretable kept, no slot credit."""
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    run = state["gates"][-1]["runs"][-1]
    assert run["execution_config"]["cli"] == cli
    assert run.get("failure_reason") == OUT_OF_SCOPE, (
        f"FAIL AC-21 ({case}): an assigned {cli} run that reached a sibling "
        f"review recorded failure_reason {run.get('failure_reason')!r}"
    )
    assert code != 0 and not response["ok"], response
    [attempt] = state["review_assignments"]["attempts"]
    assert attempt["outcome"]["reason"] == OUT_OF_SCOPE, attempt["outcome"]
    assert attempt["interpretation"] is None and attempt["source"] is None, (
        f"FAIL AC-21 ({case}): the refused run left an interpretation or source credit"
    )
    reviews = state_path.parent / "reviews"
    assert not sorted(reviews.glob("*.capture.json")), (
        f"FAIL AC-21 ({case}): the refused run kept a retained capture"
    )


def test_ac21_bare_sibling_read_fails_an_assigned_codex_run_with_no_credit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Red discriminator (AC-21 (a); m6): an assigned `run-gate` whose doubled
    Codex runs a bare `cat reviews/peer-review.md` under the default `-C`, the
    feature directory, and returns a valid object fails as
    out-of-scope-access. The run keeps no retained capture or interpretation
    and earns the assignment no source credit.
    """
    state_path, code, response = _assigned_codex_run(
        tmp_path,
        monkeypatch,
        capsys,
        [_run("cat reviews/peer-review.md", "# Sibling review\n")],
    )

    _assert_refused_assigned_run(state_path, code, response, cli="codex", case="a")


def test_ac21_shown_sibling_entry_fails_an_assigned_codex_run_with_no_credit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Red discriminator (AC-21 (b); m6): an assigned `run-gate` whose doubled
    Codex runs a search under the default `-C`, the feature directory, whose
    `aggregated_output` lists an entry under `reviews/`, and returns a valid
    object fails as out-of-scope-access. The run keeps no retained capture or
    interpretation and earns the assignment no source credit.
    """
    state_path, code, response = _assigned_codex_run(
        tmp_path,
        monkeypatch,
        capsys,
        [_run("rg -l TODO .", "./plan.md\n./reviews/peer-review.md\n")],
    )

    _assert_refused_assigned_run(state_path, code, response, cli="codex", case="b")
