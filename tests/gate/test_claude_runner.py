"""Claude gate runner: the command-execution posture reaches the child process."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from heddle.contracts.limits import (
    DEFAULT_GATE_INACTIVITY_TIMEOUT_S,
    DEFAULT_SESSION_HARD_TIMEOUT_S,
    GATE_BASH_DEFAULT_TIMEOUT_S,
    GATE_BASH_MAX_TIMEOUT_S,
)
from heddle.gate.cli import GateArgs, resolve_gate_execution
from heddle.gate.io import GatePaths
from heddle.gate.registry import GATES
from heddle.gate.runners.claude import execute_claude
from heddle.gate.types import ExecutionResult, GateInvocationOverrides
from tests.driver.helpers import write_executable_script
from tests.operational_model_helpers import FEATURE
from tests.proof_continuity_helpers import command
from tests.tiering_helpers import OPUS, entry
from tests.tiering_review_helpers import (
    current_host,
    provider_transport,
    review_content,
    review_status,
)

OBSERVED_ENV = (
    "BASH_DEFAULT_TIMEOUT_MS",
    "BASH_MAX_TIMEOUT_MS",
    "HEDDLE_AGENT_SESSION",
    "CLAUDECODE",
    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK",
)


def _paths(tmp_path: Path) -> GatePaths:
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    prompt = temp_dir / "prompt.md"
    prompt.write_text("review", encoding="utf-8")
    return GatePaths(
        output=tmp_path / "out.md",
        log=tmp_path / "out.log",
        summary=tmp_path / "out.gate-summary.json",
        prompt=prompt,
        raw_out=temp_dir / "raw.out",
        last_message=temp_dir / "last-message.txt",
        json_message=temp_dir / "json-message.txt",
        filtered_jsonl=temp_dir / "events.jsonl",
        command_output=temp_dir / "command-output.txt",
        temp_dir=temp_dir,
    )


def _recording_claude(tmp_path: Path, record: Path) -> Path:
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": {},
    }
    return write_executable_script(
        tmp_path / "claude",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "sys.stdin.read()\n"
        f"with open({str(record)!r}, 'w', encoding='utf-8') as stream:\n"
        "    json.dump({'argv': sys.argv[1:], 'env': "
        f"{{name: os.environ.get(name) for name in {OBSERVED_ENV!r}}}}}, stream)\n"
        f"print({json.dumps(result)!r})\n",
    )


def _flag(argv: list[str], name: str) -> str:
    assert argv.count(name) == 1, name
    return argv[argv.index(name) + 1]


@pytest.mark.parametrize(
    "gate_name",
    sorted(name for name, gate in GATES.items() if "claude" in gate.supported_clis),
)
def test_every_claude_gate_runs_commands_under_auto_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate_name: str
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    record = tmp_path / "record.json"
    gate_type = GATES[gate_name]
    prepared = SimpleNamespace(
        invocation=resolve_gate_execution(
            gate_type, GateInvocationOverrides(cli="claude")
        ),
        transport=SimpleNamespace(system="constraint"),
        output_contract=SimpleNamespace(schema_json="{}"),
        assignment_id=None,
    )
    args = GateArgs(
        gate_name=gate_name,
        cli_bin=str(_recording_claude(tmp_path, record)),
        workdir=tmp_path,
        poll_seconds=0.05,
    )

    execute_claude(gate_type, args, _paths(tmp_path), "review", prepared)  # type: ignore[arg-type]

    observed = json.loads(record.read_text(encoding="utf-8"))
    argv = observed["argv"]
    assert _flag(argv, "--tools") == "Read,Grep,Glob,Bash"
    assert _flag(argv, "--allowedTools") == "Read,Grep,Glob"
    assert _flag(argv, "--permission-mode") == "auto"
    assert _flag(argv, "--max-turns") == "500"
    assert _flag(argv, "--max-budget-usd") == "50.00"
    assert observed["env"] == {
        "BASH_DEFAULT_TIMEOUT_MS": "600000",
        "BASH_MAX_TIMEOUT_MS": "900000",
        "HEDDLE_AGENT_SESSION": "gate",
        "CLAUDECODE": None,
        "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1",
    }


def test_gate_timeouts_keep_commands_inside_the_inactivity_window() -> None:
    assert GATE_BASH_DEFAULT_TIMEOUT_S <= GATE_BASH_MAX_TIMEOUT_S
    assert GATE_BASH_MAX_TIMEOUT_S < DEFAULT_GATE_INACTIVITY_TIMEOUT_S
    assert DEFAULT_GATE_INACTIVITY_TIMEOUT_S < DEFAULT_SESSION_HARD_TIMEOUT_S
    args = GateArgs(gate_name="spec-review")
    assert args.inactivity_timeout_seconds == DEFAULT_GATE_INACTIVITY_TIMEOUT_S
    assert args.timeout_seconds == DEFAULT_SESSION_HARD_TIMEOUT_S


def test_public_claude_gate_records_auto_and_reads_the_retired_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=OPUS)},
    )
    calls = provider_transport(monkeypatch, review_content())

    code, response = command(capsys, "run-gate", "spec-review")

    assert code == 0 and response["data"]["accepted"], response
    launched = list(calls[0][2])
    assert _flag(launched, "--permission-mode") == "auto"
    assert _flag(launched, "--tools") == "Read,Grep,Glob,Bash"
    state = yaml.safe_load(path.read_text(encoding="utf-8"))
    run = state["gates"][-1]["runs"][-1]
    assert run["execution_config"]["sandbox"] == "auto"
    closure = review_status(path)

    artifact = path.parent / run["artifact"]
    original = artifact.read_bytes()
    result = json.loads(original)
    assert original == _canonical(result)
    result["invocation"]["execution"]["sandbox"] = "read-only-tools"
    historical = _canonical(result)
    old_digest = hashlib.sha256(original).hexdigest()
    new_digest = hashlib.sha256(historical).hexdigest()
    artifact.unlink()
    artifact.with_name(artifact.name.replace(old_digest, new_digest)).write_bytes(
        historical
    )
    path.write_text(
        yaml.safe_dump(_retire_auto(state, old_digest, new_digest), sort_keys=False),
        encoding="utf-8",
    )

    assert review_status(path) == closure


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _retire_auto(value: object, old_digest: str, new_digest: str) -> object:
    if isinstance(value, dict):
        return {
            key: "read-only-tools"
            if key == "sandbox" and item == "auto"
            else _retire_auto(item, old_digest, new_digest)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_retire_auto(item, old_digest, new_digest) for item in value]
    if isinstance(value, str):
        return value.replace(old_digest, new_digest)
    return value


def test_a_refused_assigned_review_fails_instead_of_awaiting_interpretation(
    tmp_path: Path,
) -> None:
    from heddle.gate.types import FailureReason

    events = [
        {
            "type": "assistant",
            "parent_tool_use_id": None,
            "message": {"model": "claude-fable-5-2", "stop_reason": "refusal"},
        },
        {
            "type": "system",
            "subtype": "model_refusal_no_fallback",
            "original_model": "claude-fable-5-2",
            "api_refusal_category": "cyber",
        },
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    binary = write_executable_script(
        tmp_path / "claude",
        f"#!{sys.executable}\n"
        "import sys\n"
        "sys.stdin.read()\n"
        f"print({chr(10).join(json.dumps(event) for event in events)!r})\n",
    )
    gate_type = GATES["spec-review"]
    prepared = SimpleNamespace(
        invocation=resolve_gate_execution(
            gate_type, GateInvocationOverrides(cli="claude")
        ),
        transport=SimpleNamespace(system="constraint"),
        output_contract=SimpleNamespace(schema_json="{}"),
        assignment_id="assignment",
    )
    args = GateArgs(
        gate_name="spec-review",
        cli_bin=str(binary),
        workdir=tmp_path,
        poll_seconds=0.05,
    )
    paths = _paths(tmp_path)

    result = execute_claude(gate_type, args, paths, "review", prepared)  # type: ignore[arg-type]

    assert result.failure_reason is FailureReason.REFUSAL
    assert result.response_warning is None
    log = paths.log.read_text(encoding="utf-8")
    assert "ModelRefusal:" in log and '"api_refusal_category": "cyber"' in log


# ---------------------------------------------------------------------------
# AC-21 (m6): the lane-neutral `reviews/` rule on the Claude lane, through the
# real runner with a doubled provider, as an assigned run. The synthetic
# streams follow the CLI's current stream-json shapes: `assistant` events
# carrying `tool_use` blocks and `user` events carrying `tool_result` blocks.
#
# Activation seam, owned by the m6 implementer:
# - the runner takes the feature's `reviews/` directory as
#   `paths.output.parent` and its own run temp directory as `paths.temp_dir`;
# - Claude starts in its process directory, the launching process's working
#   directory, which `init.cwd` also reports;
# - warnings go to the gate log. The unrecognized-shape warning names the lane
#   and the call kind and contains "unrecognized". The ambiguous-context
#   warning names the lane and contains "ambiguous".
# ---------------------------------------------------------------------------

OUT_OF_SCOPE = "out-of-scope-access"
ClaudeStep = tuple[str, dict[str, object], str | None]


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


def _bash(command: str, output: str | None = "") -> ClaudeStep:
    return ("Bash", {"command": command, "description": "Inspect the change"}, output)


def _tool(name: str, output: str | None, **inputs: object) -> ClaudeStep:
    return (name, dict(inputs), output)


class ClaudeStream:
    """A synthetic Claude stream-json run that ends in a schema-valid object."""

    def __init__(
        self, layout: ReviewLayout, steps: list[ClaudeStep] | None = None
    ) -> None:
        self.layout = layout
        self.events: list[dict[str, object]] = [
            {
                "type": "system",
                "subtype": "init",
                "cwd": str(layout.root),
                "model": OPUS["model"],
                "tools": ["Read", "Grep", "Glob", "Bash"],
            }
        ]
        self._calls = 0
        for name, inputs, output in steps or ():
            self.call(name, inputs, output)

    def call(self, name: str, inputs: dict[str, object], output: str | None) -> str:
        self._calls += 1
        call_id = f"toolu_{self._calls:02d}"
        block = {"type": "tool_use", "id": call_id, "name": name, "input": inputs}
        self.events.append(
            {
                "type": "assistant",
                "parent_tool_use_id": None,
                "message": {"role": "assistant", "content": [block]},
            }
        )
        if output is not None:
            self.result_block(
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": output,
                    "is_error": False,
                }
            )
        return call_id

    def result_block(self, block: dict[str, object]) -> None:
        self.events.append(
            {
                "type": "user",
                "parent_tool_use_id": None,
                "message": {"role": "user", "content": [block]},
            }
        )

    def finish(self) -> list[dict[str, object]]:
        terminal = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": review_content(),
        }
        return [*self.events, terminal]


def _run_claude_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: ClaudeStream
) -> tuple[ExecutionResult, str]:
    """Run one synthetic stream through the real Claude runner, assigned."""
    layout = stream.layout
    recorded = tmp_path / "claude-stream.jsonl"
    recorded.write_text(
        "".join(json.dumps(event) + "\n" for event in stream.finish()),
        encoding="utf-8",
    )
    binary = write_executable_script(
        tmp_path / "claude-double",
        f"#!{sys.executable}\n"
        "import sys\n"
        "sys.stdin.read()\n"
        f"with open({str(recorded)!r}, encoding='utf-8') as source:\n"
        "    sys.stdout.write(source.read())\n",
    )
    monkeypatch.chdir(layout.root)
    gate_type = GATES["spec-review"]
    prepared = SimpleNamespace(
        invocation=resolve_gate_execution(
            gate_type, GateInvocationOverrides(cli="claude")
        ),
        transport=SimpleNamespace(system="constraint"),
        output_contract=SimpleNamespace(schema_json="{}"),
        assignment_id="assignment",
    )
    args = GateArgs(
        gate_name="spec-review",
        cli_bin=str(binary),
        workdir=layout.root,
        poll_seconds=0.02,
    )
    paths = _layout_paths(layout)
    result = execute_claude(gate_type, args, paths, "review", prepared)  # type: ignore[arg-type]
    log = paths.log.read_text(encoding="utf-8") if paths.log.exists() else ""
    return result, log


def _reason(result: ExecutionResult) -> str | None:
    return None if result.failure_reason is None else result.failure_reason.value


def _assert_out_of_scope(result: ExecutionResult, case: str) -> None:
    assert _reason(result) == OUT_OF_SCOPE, (
        f"FAIL AC-21 ({case}): the Claude runner returned {_reason(result)!r}; "
        f"`reviews/` access must fail as {OUT_OF_SCOPE!r} whatever the object says"
    )
    assert result.response_warning is None, (
        f"FAIL AC-21 ({case}): the failed run kept a capture for interpretation"
    )


def _assert_recorded(result: ExecutionResult, case: str) -> None:
    assert _reason(result) is None and result.response_warning is None, (
        f"FAIL AC-21 ({case}): an in-scope Claude run failed as "
        f"{_reason(result)!r} (warning {result.response_warning!r})"
    )


def _lane_warnings(log: str, keyword: str, kind: str | None = None) -> list[str]:
    return [
        line
        for line in log.splitlines()
        if not line.startswith("ClaudeCmd:")
        and keyword in line.lower()
        and "claude" in line.lower()
        and (kind is None or kind in line)
    ]


NAMED_REVIEWS_ACCESS: dict[str, Callable[[ReviewLayout], list[ClaudeStep]]] = {
    "read-tool": lambda layout: [
        _tool("Read", "1\t# Sibling review", file_path=str(layout.sibling))
    ],
    "grep-root": lambda layout: [
        _tool(
            "Grep",
            "No files found",
            pattern="TODO",
            path=str(layout.reviews),
            output_mode="files_with_matches",
        )
    ],
    "glob-pattern": lambda layout: [
        _tool("Glob", "No files found", pattern="plans/sample-feature/reviews/*.md")
    ],
    "cat-absolute": lambda layout: [_bash(f"cat {layout.sibling}", "# Sibling")],
    "list-relative": lambda layout: [
        _bash("ls plans/sample-feature/reviews", "peer-review.md")
    ],
    "shell-write": lambda layout: [
        _bash("printf 'x' > plans/sample-feature/reviews/notes.md")
    ],
    "cd-chain": lambda layout: [
        _bash("cd plans/sample-feature && cat reviews/peer-review.md", "# Sibling")
    ],
    "parent-spelling": lambda layout: [
        _bash("cd plans/sample-feature/notes && cat ../reviews/peer-review.md", "#")
    ],
    "dot-dot-normalized": lambda layout: [
        _bash("cat plans/sample-feature/notes/../reviews/peer-review.md", "# Sibling")
    ],
    "tilde-spelling": lambda layout: [
        _bash("cat ~/host/plans/sample-feature/reviews/peer-review.md", "# Sibling")
    ],
    "home-variable-spelling": lambda layout: [
        _bash('cat "$HOME/host/plans/sample-feature/reviews/peer-review.md"', "#")
    ],
    "cd-in-earlier-call": lambda layout: [
        _bash(f"cd {layout.feature}"),
        _bash("cat reviews/peer-review.md", "# Sibling review"),
    ],
}


@pytest.mark.parametrize("case", sorted(NAMED_REVIEWS_ACCESS))
def test_ac21_named_reviews_access_fails_on_the_claude_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """
    Red discriminator (AC-21 (a); m6): a read, listing, search or write of a
    path named under the feature's `reviews/`, outside the run temp directory,
    fails as out-of-scope-access. Relative names resolve against the `Bash`
    directory, which persists between calls; `~`, `$HOME` and `..` normalize.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    layout = _review_layout(tmp_path)
    stream = ClaudeStream(layout, NAMED_REVIEWS_ACCESS[case](layout))

    result, _ = _run_claude_stream(tmp_path, monkeypatch, stream)

    _assert_out_of_scope(result, case)


SHOWN_REVIEWS_ENTRIES: dict[str, Callable[[ReviewLayout], list[ClaudeStep]]] = {
    "grep-matching-files": lambda layout: [
        _tool(
            "Grep",
            f"Found 2 files\n{layout.feature / 'plan.md'}\n{layout.sibling}",
            pattern="TODO",
            path=str(layout.feature),
            output_mode="files_with_matches",
        )
    ],
    "glob-listing": lambda layout: [
        _tool(
            "Glob",
            f"{layout.feature / 'plan.md'}\n{layout.temp / 'prompt.md'}\n"
            f"{layout.sibling}",
            pattern="**/*.md",
            path=str(layout.feature),
        )
    ],
    "rg-path-led-lines": lambda layout: [
        _bash(
            "rg -n TODO plans",
            "plans/sample-feature/plan.md:5:TODO later\n"
            "plans/sample-feature/reviews/peer-review.md:3:TODO tighten",
        )
    ],
    "cd-led-search": lambda layout: [
        _bash("cd plans && rg -l TODO", "sample-feature/reviews/peer-review.md")
    ],
    "recursive-listing": lambda layout: [
        _bash(
            "ls -R plans/sample-feature",
            "plans/sample-feature:\nnotes\nplan.md\nreviews\nstate.yaml\n\n"
            "plans/sample-feature/notes:\n\n"
            "plans/sample-feature/reviews:\npeer-review.md\n"
            "spec-review.tmp.k3x9q2\n",
        )
    ],
    "find-listing": lambda layout: [
        _bash(
            "find plans -name '*.md'",
            "plans/sample-feature/plan.md\n"
            "plans/sample-feature/reviews/spec-review.tmp.k3x9q2/prompt.md\n"
            "plans/sample-feature/reviews/peer-review.md",
        )
    ],
    "pipeline-first-stage": lambda layout: [
        _bash(
            "rg -l TODO plans | sort",
            "plans/sample-feature/plan.md\nplans/sample-feature/reviews/peer-review.md",
        )
    ],
}


@pytest.mark.parametrize("case", sorted(SHOWN_REVIEWS_ENTRIES))
def test_ac21_shown_reviews_entries_fail_on_the_claude_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """
    Red discriminator (AC-21 (b); m6): a search or listing from an ancestor
    whose shown result holds an entry under `reviews/` fails as
    out-of-scope-access. Structured `Grep` and `Glob` results and the output of
    shell searches and listings count; path-led lines resolve against the
    call's directory.
    """
    layout = _review_layout(tmp_path)
    stream = ClaudeStream(layout, SHOWN_REVIEWS_ENTRIES[case](layout))

    result, _ = _run_claude_stream(tmp_path, monkeypatch, stream)

    _assert_out_of_scope(result, case)


IN_SCOPE_CONTROLS: dict[str, Callable[[ReviewLayout], list[ClaudeStep]]] = {
    "own-temp-read": lambda layout: [
        _tool("Read", "1\treview", file_path=str(layout.temp / "prompt.md"))
    ],
    "own-temp-search": lambda layout: [
        _tool(
            "Grep",
            f"Found 1 file\n{layout.temp / 'prompt.md'}",
            pattern="review",
            path=str(layout.temp),
            output_mode="files_with_matches",
        )
    ],
    "own-temp-listing": lambda layout: [
        _bash(f"ls {layout.temp}", "prompt.md\nraw.out")
    ],
    "document-mentions-a-reviews-path": lambda layout: [
        _tool(
            "Read",
            "1\t# Plan\n2\t\n3\tSee reviews/peer-review.md.",
            file_path=str(layout.feature / "plan.md"),
        )
    ],
    "printed-state-file": lambda layout: [
        _bash(
            "cat plans/sample-feature/state.yaml",
            "runs:\n- artifact: reviews/peer-review.md",
        )
    ],
    "hidden-ancestor-search": lambda layout: [_bash("rg -l TODO plans | wc -l", "2")],
    "unclassified-command-output": lambda layout: [
        _bash(
            "python3 scripts/summarize.py",
            "plans/sample-feature/reviews/peer-review.md",
        )
    ],
    "list-with-a-content-printing-element": lambda layout: [
        _bash(
            "rg -l TODO plans; cat plans/sample-feature/plan.md",
            "plans/sample-feature/reviews/peer-review.md\n# Plan",
        )
    ],
    "unrelated-directory-in-earlier-call": lambda layout: [
        _bash(f"cd {layout.root / 'docs'}"),
        _bash("cat reviews/peer-review.md", "cat: reviews/peer-review.md: missing"),
    ],
}


@pytest.mark.parametrize("case", sorted(IN_SCOPE_CONTROLS))
def test_ac21_in_scope_controls_record_on_the_claude_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """
    Survivor pin (AC-21 (c), (d), (e); m6): own-temp access, a document that
    mentions a `reviews/` path, printed file content, an ancestor search whose
    results are hidden, unclassified output, a list with a content-printing
    element and a `reviews/` name relative to an unrelated directory all record
    today and must keep recording once the check lands.
    """
    layout = _review_layout(tmp_path)
    stream = ClaudeStream(layout, IN_SCOPE_CONTROLS[case](layout))

    result, _ = _run_claude_stream(tmp_path, monkeypatch, stream)

    _assert_recorded(result, case)


UNKNOWN_DIRECTORY: dict[str, Callable[[ReviewLayout], list[ClaudeStep]]] = {
    "same-command": lambda layout: [
        _bash('cd "$D" && cat reviews/peer-review.md', "# Sibling review")
    ],
    "relative-cd-after-unknown": lambda layout: [
        _bash('cd "$D"'),
        _bash("cd plans/sample-feature"),
        _bash("cat reviews/peer-review.md", "# Sibling review"),
    ],
}


@pytest.mark.parametrize("case", sorted(UNKNOWN_DIRECTORY))
def test_ac21_unparseable_cd_grounds_no_refusal_and_warns_on_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """
    Red discriminator (AC-21; m6): an unparseable `cd` leaves the directory
    unknown, and a relative `cd` from there keeps it unknown across later
    `Bash` calls. A relative `reviews/` name there grounds no refusal; the run
    records and the gate log carries the ambiguous-context warning.
    """
    layout = _review_layout(tmp_path)
    stream = ClaudeStream(layout, UNKNOWN_DIRECTORY[case](layout))

    result, log = _run_claude_stream(tmp_path, monkeypatch, stream)

    _assert_recorded(result, case)
    assert _lane_warnings(log, "ambiguous"), (
        f"FAIL AC-21 ({case}): no ambiguous-context warning naming the claude "
        f"lane in the gate log:\n{log}"
    )


def test_ac21_absolute_reviews_path_after_unparseable_cd_fails_on_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Red discriminator (AC-21 (a); m6): an absolute `reviews/` path does not
    depend on the directory, so it fails even after an unparseable `cd`.
    """
    layout = _review_layout(tmp_path)
    stream = ClaudeStream(layout, [_bash(f'cd "$D" && cat {layout.sibling}', "#")])

    result, _ = _run_claude_stream(tmp_path, monkeypatch, stream)

    _assert_out_of_scope(result, "absolute path after an unparseable cd")


def test_ac21_absolute_cd_reestablishes_the_directory_on_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Red discriminator (AC-21 (a); m6): after an unparseable `cd`, a later
    `Bash` call's absolute `cd` re-establishes the directory, so its relative
    `reviews/` read fails.
    """
    layout = _review_layout(tmp_path)
    stream = ClaudeStream(
        layout,
        [
            _bash('cd "$(mktemp -d)"'),
            _bash(f"cd {layout.feature} && cat reviews/peer-review.md", "# Sibling"),
        ],
    )

    result, _ = _run_claude_stream(tmp_path, monkeypatch, stream)

    _assert_out_of_scope(result, "absolute cd after an unparseable cd")


@pytest.mark.parametrize("recognized_grep", [False, True], ids=["all", "mixed"])
def test_ac21_unrecognized_result_shapes_warn_per_call_on_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recognized_grep: bool
) -> None:
    """
    Red discriminator (AC-21 (f); m6): a recognized call whose result is not
    in a recognized `tool_result` shape is not inspected. The run records, and
    the gate log carries one unrecognized-shape warning per such call, naming
    the lane and the call kind. In the mixed stream, `Grep` keeps the
    recognized shape and logs none.
    """
    layout = _review_layout(tmp_path)
    stream = ClaudeStream(layout)
    grep_output = f"Found 1 file\n{layout.feature / 'plan.md'}"
    grep_id = stream.call(
        "Grep",
        {"pattern": "TODO", "path": str(layout.feature)},
        grep_output if recognized_grep else None,
    )
    if not recognized_grep:
        stream.result_block(
            {"type": "tool_output", "tool_use_id": grep_id, "output": grep_output}
        )
    bash_id = stream.call(
        "Bash", {"command": "git status --short", "description": "Status"}, None
    )
    stream.result_block(
        {
            "type": "tool_result",
            "tool_use_id": bash_id,
            "content": {"stdout": " M src/app.py", "stderr": ""},
        }
    )

    result, log = _run_claude_stream(tmp_path, monkeypatch, stream)

    _assert_recorded(result, "unrecognized result shapes")
    bash_warnings = _lane_warnings(log, "unrecognized", "Bash")
    grep_warnings = _lane_warnings(log, "unrecognized", "Grep")
    assert len(bash_warnings) == 1, (
        "FAIL AC-21 (f): expected one unrecognized-shape warning naming the "
        f"claude lane and Bash, got {bash_warnings}"
    )
    assert len(grep_warnings) == (0 if recognized_grep else 1), (
        "FAIL AC-21 (f): Grep warnings must follow its result shape, got "
        f"{grep_warnings}"
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


def _marked_claude_reviewer(path: Path, *, record: Path, disposable: Path) -> Path:
    """A doubled Claude that reports its environment and tries one mutation."""
    review = json.dumps(review_content())
    return write_executable_script(
        path,
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys\n"
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
        "use = {'type': 'tool_use', 'id': 'toolu_01', 'name': 'Bash',\n"
        "       'input': {'command': 'heddle ' + ' '.join(mutation)}}\n"
        "shown = {'type': 'tool_result', 'tool_use_id': 'toolu_01',\n"
        "         'content': attempt.stdout + attempt.stderr,\n"
        "         'is_error': attempt.returncode != 0}\n"
        "events = [\n"
        "    {'type': 'assistant', 'parent_tool_use_id': None,\n"
        "     'message': {'role': 'assistant', 'content': [use]}},\n"
        "    {'type': 'user', 'parent_tool_use_id': None,\n"
        "     'message': {'role': 'user', 'content': [shown]}},\n"
        "    {'type': 'result', 'subtype': 'success', 'is_error': False,\n"
        f"     'structured_output': json.loads({review!r})}},\n"
        "]\n"
        "for event in events:\n"
        "    print(json.dumps(event))\n",
    )


def test_ac2_marked_claude_reviewer_child_mutation_is_refused_and_review_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Red discriminator (AC-2; m1): from an unmarked parent, a real Claude gate
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
        overrides={"spec-review": entry("spec-review", primary=OPUS)},
    )
    disposable = tmp_path / "disposable"
    shutil.copytree(host, disposable, symlinks=True)
    before = _tree_bytes(disposable)
    shims = tmp_path / "shims"
    shims.mkdir()
    record = tmp_path / "reviewer-child.json"
    _marked_claude_reviewer(shims / "claude", record=record, disposable=disposable)
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
    assert run["execution_config"]["cli"] == "claude"
    assert (state_path.parent / run["artifact"]).is_file()


# ---------------------------------------------------------------------------
# AC-21 (m6), as an assigned run: a sibling read fails through `run-gate` and
# leaves nothing interpretable for slot credit.
# ---------------------------------------------------------------------------


def _replaying_claude(path: Path, events: list[dict[str, object]]) -> Path:
    """A doubled Claude that reads its prompt and replays a fixed stream."""
    stream = "".join(json.dumps(event) + "\n" for event in events)
    return write_executable_script(
        path,
        f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n"
        f"sys.stdout.write({stream!r})\n",
    )


def test_ac21_sibling_read_fails_an_assigned_claude_run_with_no_credit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Red discriminator (AC-21 (a); m6): an assigned `run-gate` whose doubled
    Claude reads a sibling review under the feature's `reviews/` and returns a
    valid object fails as out-of-scope-access. The run is not accepted, keeps
    no artifact and earns the assignment no source credit.
    """
    monkeypatch.delenv("HEDDLE_AGENT_SESSION", raising=False)
    host, state_path = current_host(
        tmp_path,
        monkeypatch,
        overrides={"spec-review": entry("spec-review", primary=OPUS)},
    )
    feature = host / "plans" / FEATURE
    sibling = feature / "reviews" / "peer-review.md"
    sibling.parent.mkdir(exist_ok=True)
    sibling.write_text("# Sibling review\n", encoding="utf-8")
    layout = ReviewLayout(host, feature, sibling.parent, sibling.parent, sibling)
    events = ClaudeStream(
        layout, [_tool("Read", "1\t# Sibling review", file_path=str(sibling))]
    ).finish()
    shims = tmp_path / "shims"
    shims.mkdir()
    _replaying_claude(shims / "claude", events)
    monkeypatch.setenv("PATH", f"{shims}{os.pathsep}{os.environ['PATH']}")

    code, response = command(capsys, "run-gate", "spec-review")

    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    run = state["gates"][-1]["runs"][-1]
    assert run["execution_config"]["cli"] == "claude"
    assert run.get("failure_reason") == OUT_OF_SCOPE, (
        "FAIL AC-21 (a): an assigned Claude run that read a sibling review "
        f"recorded failure_reason {run.get('failure_reason')!r}"
    )
    assert code != 0 and not response["ok"], response
    [attempt] = state["review_assignments"]["attempts"]
    assert attempt["outcome"]["reason"] == OUT_OF_SCOPE, attempt["outcome"]
    assert attempt["interpretation"] is None and attempt["source"] is None, (
        "FAIL AC-21 (a): the refused run left an interpretation or source credit"
    )
    reviews = state_path.parent / "reviews"
    assert not sorted(reviews.glob("*.capture.json")), (
        "FAIL AC-21 (a): the refused run kept a retained capture"
    )
