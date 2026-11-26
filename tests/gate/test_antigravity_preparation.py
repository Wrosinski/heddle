"""Antigravity lane preparation: the frozen route, the capture and the lane block.

antigravity-reviewer-lane-v1, preparation milestone. Covers AC-6 and AC-7's
preparation part (frozen route, capture bytes, chunk plan), AC-8 (refusal
before any launch, standalone), AC-10 (embedded milestone context and the
lane block), AC-22's Antigravity half (embedded bodies keep the checkout
path; generated labels stay checkout-relative) and AC-4's preview.

Synthetic gate contexts drive the real ``prepare_gate_run`` with a diff file
the test writes, so every embedded byte is known. Host cases use the
operational fixture host and the in-process application boundary. Provider
work is never reached: the engine seam is replaced by a recorder that fails
the test if called. No case reads agy's data directory or runs agy.

Every case is a red discriminator for its named AC unless its docstring says
survivor. Seams named here, since the plan names none:
``PreparedGateRun.antigravity_route`` for the frozen route, with the route
shape described in ``frozen_route``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from heddle.gate.preparation import prepare_gate_run
from heddle.gate.prompt import output_constraint, resolve_gate_prompt
from heddle.gate.registry import GATES
from heddle.gate.types import (
    DiffResult,
    GateContext,
    GateExecutionConfig,
    ResolvedGateInvocation,
)
from heddle.kernel.project_config import KernelError

INLINE_LIMIT = 160_000
CEILING = 250_000
REFUSAL = "antigravity-input-too-large"
# agy's measured per-call read limit and the plan's six-digit prefix margin.
READ_LINES = 800
READ_BYTES = 46_080
WORST_PREFIX = len(b"999999: ")
# agy wraps inline content in a 15-byte header and a trailer of about 400
# bytes, and the model receives at most 192,000 bytes of that message.
WRAPPED_OVERHEAD = len(b"<USER_REQUEST>\n") + 400
WRAPPED_CAP = 192_000
MULTIBYTE = "aé€🚀"
ESCAPE_HEAVY = '"\\\t\n\r\f\v<>&\x85\u2028\u2029'
ANTIGRAVITY = {
    "cli": "antigravity",
    "model": "gemini-4-neon",
    "reasoning_effort": "high",
}
LANES = {
    "antigravity": ("gemini-4-neon", "high"),
    "codex": ("gpt-6-sol", "high"),
}

PLAN_PATH = "plans/fixture/plan.md"
SPEC_PATH = "docs/spec.md"
MILESTONE_SECTION = "### Milestone m1: Fixture\n\nPLAN_MILESTONE_SENTINEL\n"
PLAN = "---\nfeature: fixture\n---\n# Plan\n\n" + MILESTONE_SECTION
SPEC = (
    "# Spec\n\n## Acceptance Criteria\n\n### AC-1: Value\n\n"
    "Expected: SPEC_AC_SENTINEL.\n"
)
DIFF_HEAD = (
    "diff --git a/src/core.py b/src/core.py\n"
    "--- a/src/core.py\n"
    "+++ b/src/core.py\n"
    "@@ -1 +1 @@\n"
    "+"
)


# --------------------------------------------------------------------------- #
# Synthetic checkout and preparation
# --------------------------------------------------------------------------- #
@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    for relative, text in {
        PLAN_PATH: PLAN,
        SPEC_PATH: SPEC,
        "src/core.py": "VALUE = 1\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def lane_config(cli: str) -> GateExecutionConfig:
    from heddle.contracts.gate_execution import VALID_SANDBOXES_BY_CLI

    model, effort = LANES[cli]
    sandbox = sorted(VALID_SANDBOXES_BY_CLI.get(cli, {"danger-full-access"}))[0]
    return GateExecutionConfig(cli, model, effort, sandbox)


def write_diff(checkout: Path, body: str) -> Path:
    path = checkout.parent / "prepared.diff"
    path.write_bytes((DIFF_HEAD + body).encode("utf-8"))
    return path


def prepare(
    checkout: Path,
    diff_path: Path,
    *,
    gate: str = "milestone-review",
    cli: str = "antigravity",
):
    gate_type = GATES[gate]
    context = GateContext(
        gate_type=gate_type,
        feature="fixture",
        repo_root=checkout,
        plan_path=checkout / PLAN_PATH,
        spec_path=checkout / SPEC_PATH,
        milestone="m1" if gate_type.requires_milestone else None,
        milestone_title="Fixture",
        milestone_section=MILESTONE_SECTION,
        satisfies_ac_ids=["AC-1"],
        spec_ac_ids=["AC-1"],
        owned_paths=["src/core.py"],
        feature_owned_paths=("src/core.py",),
        plan_content=PLAN,
        spec_content=SPEC,
    )
    return prepare_gate_run(
        context,
        gate_type=gate_type,
        invocation=ResolvedGateInvocation(lane_config(cli), frozenset()),
        prompt=resolve_gate_prompt(context, gate_type, cli=cli),
        diff=DiffResult("fixture", "fixture", diff_path, ("src/core.py",)),
    )


def prepare_body(checkout: Path, body: str, **kwargs):
    return prepare(checkout, write_diff(checkout, body), **kwargs)


def capture_bytes(prepared) -> bytes:
    """The frozen capture: the whole stdin, since the lane has no system prompt."""
    return prepared.transport.stdin.encode("utf-8")


def frozen_route(prepared):
    """The route frozen in the prepared run (seam named by this scaffold).

    ``PreparedGateRun.antigravity_route`` has ``kind`` ("inline", "chunked"),
    ``size`` (the capture's UTF-8 bytes) and ``chunks``: byte ranges into the
    capture with ``start``, ``end`` and the ``sha256`` of the slice.

    The attribute's name and the route's field names and kind values are
    delegated: this helper is their only reader, and the cases see the route
    only through the view it returns. The error code, the 160,000-byte inline
    limit and the 250,000-byte ceiling are the spec's and stay fixed.
    """
    from types import SimpleNamespace

    route = getattr(prepared, "antigravity_route", None)
    assert route is not None, (
        "FAIL AC-6/AC-7: the antigravity run was prepared without a frozen "
        "route (PreparedGateRun.antigravity_route)"
    )
    return SimpleNamespace(
        kind=route.kind,
        size=route.size,
        chunks=tuple(
            SimpleNamespace(start=chunk.start, end=chunk.end, sha256=chunk.sha256)
            for chunk in route.chunks
        ),
    )


def sized(total: int, unit: str) -> str:
    """Exactly ``total`` UTF-8 bytes of repeated ``unit``, padded with ASCII."""
    text = unit * (total // len(unit.encode("utf-8")))
    text += "x" * (total - len(text.encode("utf-8")))
    assert len(text.encode("utf-8")) == total, "fixture precondition: exact size"
    return text


def body_for_capture(checkout: Path, total: int, unit: str) -> str:
    """A diff body that makes the antigravity capture exactly ``total`` bytes.

    Two probes measure the capture around the embedded body. A capture that
    does not grow byte for byte with the body does not embed the milestone
    diff, which is AC-10's milestone context, so this fails red there.
    """
    small = len(capture_bytes(prepare_body(checkout, "x")))
    large = len(capture_bytes(prepare_body(checkout, "x" * 1_001)))
    assert large - small == 1_000, (
        "FAIL AC-6/AC-10: the antigravity capture does not embed the milestone "
        f"diff body byte for byte (a 1,000-byte longer body grew it by "
        f"{large - small})"
    )
    return sized(total - (small - 1), unit)


def assert_embedded(raw: bytes, body: str) -> None:
    assert raw.count(body.encode("utf-8")) == 1, (
        "FAIL AC-7/AC-10: the antigravity capture does not embed the milestone "
        "diff body exactly once, byte for byte"
    )


def assert_whole_readable_chunks(raw: bytes, route) -> None:
    """AC-7: the frozen chunks partition the capture and fit one read each."""
    assert route.kind == "chunked", f"FAIL AC-7: expected chunked, got {route.kind}"
    assert route.size == len(raw), (
        f"FAIL AC-7: the route measured {route.size} bytes of a {len(raw)}-byte capture"
    )
    assert route.chunks, "FAIL AC-7: a chunked route plans no chunks"
    cursor = 0
    for index, chunk in enumerate(route.chunks):
        assert chunk.start == cursor < chunk.end <= len(raw), (
            f"FAIL AC-7: chunk {index} [{chunk.start}, {chunk.end}) does not "
            f"continue the partition at byte {cursor}"
        )
        piece = raw[chunk.start : chunk.end]
        try:
            piece.decode("utf-8")
        except UnicodeDecodeError as error:
            pytest.fail(f"FAIL AC-7: chunk {index} splits a UTF-8 character: {error}")
        assert chunk.sha256 == hashlib.sha256(piece).hexdigest(), (
            f"FAIL AC-7: chunk {index}'s digest does not bind its byte range"
        )
        lines = piece.count(b"\n") + (0 if piece.endswith(b"\n") else 1)
        assert lines <= READ_LINES, f"FAIL AC-7: chunk {index} has {lines} lines"
        assert len(piece) + lines * WORST_PREFIX <= READ_BYTES, (
            f"FAIL AC-7: chunk {index} does not fit one read_file call"
        )
        cursor = chunk.end
    assert b"".join(raw[chunk.start : chunk.end] for chunk in route.chunks) == raw, (
        "FAIL AC-7: the chunks do not reassemble the frozen capture"
    )


def route_plan(route) -> tuple:
    return (
        route.kind,
        route.size,
        tuple((chunk.start, chunk.end, chunk.sha256) for chunk in route.chunks),
    )


def number(value: int) -> str:
    """A pattern for ``value`` written with or without thousands separators."""
    return r"\b" + f"{value:,}".replace(",", ",?") + r"\b"


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def lane_sections(prepared, other) -> str:
    """Runtime sections the antigravity run carries and the Codex run does not."""
    shared = set(other.runtime_sections)
    return "\n\n".join(
        section for section in prepared.runtime_sections if section not in shared
    )


# --------------------------------------------------------------------------- #
# AC-6 and AC-7: the frozen route over the real assembled capture
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "unit", [MULTIBYTE, ESCAPE_HEAVY], ids=["1-to-4-byte", "escape-heavy"]
)
def test_ac6_capture_at_the_inline_limit_is_frozen_inline_and_byte_exact(
    checkout, unit
):
    """AC-6: a 160,000-byte capture freezes inline and holds the body verbatim.

    The wrapped size of agy's message (header, content, trailer) stays under
    its 192,000-byte cap; the NDJSON line does not count against it.
    """
    body = body_for_capture(checkout, INLINE_LIMIT, unit)
    prepared = prepare_body(checkout, body)
    raw = capture_bytes(prepared)
    assert len(raw) == INLINE_LIMIT, "fixture precondition: calibrated capture"
    assert prepared.transport.system is None, "FAIL AC-6: the capture is split"
    route = frozen_route(prepared)
    assert route.kind == "inline", (
        f"FAIL AC-6: a {INLINE_LIMIT}-byte capture froze as {route.kind}"
    )
    assert route.size == len(raw), f"FAIL AC-6: route measured {route.size}"
    assert raw.count(body.encode("utf-8")) == 1, (
        "FAIL AC-6: the frozen capture does not hold the embedded body byte-exact"
    )
    assert WRAPPED_OVERHEAD + route.size <= WRAPPED_CAP, (
        "FAIL AC-6: the inline message would exceed agy's wrapped-message cap"
    )


def test_ac6_ac7_capture_one_byte_over_the_limit_freezes_whole_readable_chunks(
    checkout,
):
    """AC-6/AC-7: 160,001 bytes freezes a chunk plan that rebuilds the capture."""
    body = body_for_capture(checkout, INLINE_LIMIT + 1, MULTIBYTE)
    prepared = prepare_body(checkout, body)
    raw = capture_bytes(prepared)
    assert len(raw) == INLINE_LIMIT + 1, "fixture precondition: calibrated capture"
    assert_whole_readable_chunks(raw, frozen_route(prepared))


def test_ac7_capture_at_the_ceiling_freezes_whole_readable_chunks(checkout):
    """AC-7: exactly 250,000 bytes of multi-byte text is still delivered."""
    body = body_for_capture(checkout, CEILING, MULTIBYTE)
    prepared = prepare_body(checkout, body)
    raw = capture_bytes(prepared)
    assert len(raw) == CEILING, "fixture precondition: calibrated capture"
    assert_whole_readable_chunks(raw, frozen_route(prepared))


@pytest.mark.parametrize(
    "marker",
    [
        "\u200b",
        "\ue000",
        "\ufdd0",
        "\x00",
        "<USER_REQUEST>",
        "</context_summary>",
    ],
    ids=[
        "zero-width-space",
        "private-use",
        "noncharacter",
        "nul",
        "open-tag",
        "lower-case-close-tag",
    ],
)
def test_ac7_small_unsafe_capture_freezes_whole_readable_chunks(checkout, marker):
    """AC-7: a stripped code point or wrapper tag in a small capture is chunked."""
    body = f"text = 'before {marker} after'\n"
    prepared = prepare_body(checkout, body)
    raw = capture_bytes(prepared)
    assert_embedded(raw, body)
    assert len(raw) < INLINE_LIMIT, "fixture precondition: small capture"
    assert_whole_readable_chunks(raw, frozen_route(prepared))


@pytest.mark.parametrize(
    "near_miss", ["USER_REQUEST", "<USER_REQ"], ids=["bare-name", "truncated-tag"]
)
def test_ac7_near_miss_controls_keep_the_capture_inline(checkout, near_miss):
    """AC-7 controls: neither the lane's own text nor a near miss forces chunks."""
    body = f"text = '{near_miss}'\n"
    prepared = prepare_body(checkout, body)
    assert_embedded(capture_bytes(prepared), body)
    route = frozen_route(prepared)
    assert route.kind == "inline", f"FAIL AC-7: {near_miss!r} froze as {route.kind}"


def test_ac6_ac7_route_and_identity_are_frozen_with_the_capture(checkout):
    """AC-6/AC-7: preparing the same inputs twice freezes the same plan."""
    body = ("x" * 120 + "</USER_REQUEST>\n") * 600
    first = prepare_body(checkout, body)
    second = prepare_body(checkout, body)
    assert route_plan(frozen_route(first)) == route_plan(frozen_route(second)), (
        "FAIL AC-7: the same capture froze two different chunk plans"
    )
    assert (first.input_hash, first.effective_prompt_sha256) == (
        second.input_hash,
        second.effective_prompt_sha256,
    )
    assert frozen_route(first).kind == "chunked"


# --------------------------------------------------------------------------- #
# AC-8: the ceiling refusal, in preparation and through standalone run-gate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("total", [CEILING + 1, 600_000], ids=["one-over", "far"])
def test_ac8_capture_above_the_ceiling_is_refused_in_preparation(
    checkout, tmp_path, total
):
    """AC-8: a typed refusal names the size, the ceiling and the policy remedy.

    Preparation writes nothing: the refusal leaves every file as it was.
    """
    from heddle.contracts.result import ERROR_CODES

    assert REFUSAL in ERROR_CODES, (
        f"FAIL AC-8: {REFUSAL} is not in the public error-code vocabulary "
        "(heddle/contracts/result.py ERROR_CODES)"
    )
    diff_path = write_diff(checkout, body_for_capture(checkout, total, MULTIBYTE))
    before = tree(tmp_path)
    with pytest.raises(KernelError) as refused:
        prepare(checkout, diff_path)
    error = refused.value
    assert_ceiling_refusal(error.code, error.message, error.hint, total, "preparation")
    assert tree(tmp_path) == before, "FAIL AC-8: a refused preparation wrote files"


def assert_ceiling_refusal(
    code: str, message: str, hint: str, total: int, route: str
) -> None:
    """AC-8: the typed refusal names the exact size, the ceiling and the remedy.

    Shared by every route that refuses: preparation, standalone run-gate, the
    run-gates batch and the drive.
    """
    assert code == REFUSAL, f"FAIL AC-8: {route} refused as {code}: {message}"
    assert re.search(number(total), message), (
        f"FAIL AC-8: {route}'s refusal does not name the {total:,}-byte size: {message}"
    )
    assert re.search(number(CEILING), message), (
        f"FAIL AC-8: {route}'s refusal does not name the ceiling: {message}"
    )
    remedy = hint.lower()
    assert "polic" in remedy and "amend" in remedy and "lane" in remedy, (
        f"FAIL AC-8: {route}'s remedy is not a policy amendment to another lane: {hint}"
    )


def require_lane_value() -> None:
    from heddle.contracts.gate_execution import VALID_GATE_CLIS

    assert "antigravity" in VALID_GATE_CLIS, (
        "FAIL AC-4/AC-8: the antigravity lane value is not accepted yet, so no "
        "policy slot can name it"
    )


def antigravity_host(tmp_path, monkeypatch, reviewer=None, *, role="spec-review"):
    """An operational host at ``role``'s stage whose ``role`` slot is the lane."""
    from heddle.contracts.review_assignments import ROLE_STAGES
    from tests.tiering_helpers import entry
    from tests.tiering_review_helpers import current_host

    require_lane_value()
    return current_host(
        tmp_path,
        monkeypatch,
        stage=ROLE_STAGES[role],
        overrides={role: entry(role, primary=reviewer or ANTIGRAVITY)},
    )


# A filler line of 1-, 2-, 3- and 4-byte characters: its 49 UTF-8 bytes are 31
# characters, so a route that counts characters misses the exact size.
FILLER_LINE = "Read every byte: " + MULTIBYTE * 3 + ".\n"
PROBE_FILLER = 100_000


def sized_prompt(filler: int) -> str:
    """A host prompt projection whose filler is exactly ``filler`` UTF-8 bytes.

    The filler sits between fixed text, so the prompt grows byte for byte
    with it wherever the capture embeds the prompt.
    """
    lines, rest = divmod(filler, len(FILLER_LINE.encode("utf-8")))
    return (
        "# Review\n\n"
        + FILLER_LINE * lines
        + "x" * rest
        + "\nEnd of the review instructions.\n"
    )


def fit_capture(
    host: Path, gate: str, total: int, run_route, *, cli: str = "antigravity"
) -> None:
    """Write the host prompt so the route freezes a ``total``-byte capture.

    ``run_route`` runs the public route under test: standalone run-gate, the
    run-gates batch or the drive. Two probe runs of that same route measure
    the lane's capture around the prompt: a wrapper at the preparation seam
    records each capture's UTF-8 bytes and stops the run there, before any
    launch. The prompt is then sized so the route's next run freezes exactly
    ``total`` bytes, whatever the lane adds around the prompt.
    """
    from heddle.gate import entry

    path = host / "prompts" / f"{gate}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = entry.prepare_gate_run
    sizes: list[int] = []

    def probing(*args, **kwargs):
        prepared = original(*args, **kwargs)
        if prepared.invocation.exec_config.cli != cli:
            return prepared
        sizes.append(len(capture_bytes(prepared)))
        raise KernelError("internal", "capture-size probe", "the test stops here")

    measured: list[set[int]] = []
    with pytest.MonkeyPatch.context() as probe:
        probe.setattr(entry, "prepare_gate_run", probing)
        for filler in (PROBE_FILLER, PROBE_FILLER + 1_000):
            sizes.clear()
            path.write_text(sized_prompt(filler), encoding="utf-8")
            run_route()
            measured.append(set(sizes))
    assert all(len(run) == 1 for run in measured), (
        f"fixture: the {gate} route did not prepare one {cli} capture per probe "
        f"run: {measured}"
    )
    (small,), (large,) = measured
    assert large - small == 1_000, (
        f"FAIL AC-8: the {gate} {cli} capture does not carry the host prompt "
        f"byte for byte (a 1,000-byte longer prompt grew it by {large - small})"
    )
    path.write_text(sized_prompt(total - (small - PROBE_FILLER)), encoding="utf-8")


def record_launches(monkeypatch, tmp_path: Path):
    """Replace the engine seam; every call is a provider launch the test forbids."""
    log = tmp_path / "launches.log"

    def launch(gate_type, context, *, feature, **_kwargs):
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"{gate_type.name}\n")
        raise AssertionError("FAIL AC-8: a provider launch was attempted")

    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", launch)
    return lambda: log.read_text(encoding="utf-8").split() if log.exists() else []


@pytest.mark.parametrize("total", [CEILING + 1, 600_000], ids=["one-over", "far"])
def test_ac8_standalone_run_gate_refuses_before_any_launch(
    tmp_path, monkeypatch, total
):
    """AC-8: standalone run-gate refuses a frozen capture above the ceiling.

    The host prompt sizes the slot's capture to exactly 250,001 bytes, or far
    above. The refusal names that size, the ceiling and the policy remedy. No
    provider of any lane launches: the engine seam records every call, and the
    doubled agy and claude on PATH record every process. State is unchanged.
    """
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute
    from tests.gate.test_antigravity_runner import install_agy
    from tests.tiering_review_helpers import V7_FEATURE

    host, state_path = antigravity_host(tmp_path, monkeypatch)
    agy = install_agy(tmp_path, monkeypatch)
    launches = record_launches(monkeypatch, tmp_path)

    def run():
        return execute(ops.RunGate("spec-review", feature=V7_FEATURE, **ANTIGRAVITY))

    fit_capture(host, "spec-review", total, run)
    before = state_path.read_bytes()
    result = run()
    assert not result.ok, "FAIL AC-8: an oversized antigravity capture ran"
    error = result.error
    assert_ceiling_refusal(
        error.code, error.message, error.hint, total, "standalone run-gate"
    )
    assert launches() == [], f"FAIL AC-8: engine launches {launches()}"
    assert agy.launches() == [] and agy.claude_calls() == [], (
        "FAIL AC-8: a provider process launched"
    )
    assert state_path.read_bytes() == before, "FAIL AC-8: the refusal wrote state"


# --------------------------------------------------------------------------- #
# AC-22, Antigravity half: embedded bodies keep the checkout path
# --------------------------------------------------------------------------- #
def test_ac22_embedded_bodies_keep_the_checkout_path_and_labels_stay_relative(
    checkout,
):
    """AC-22: the diff body is byte-exact; Heddle's own path labels are relative.

    The diff carries a source hunk and a test-file hunk whose bodies contain
    the absolute checkout path. Only those bodies may carry it.
    """
    root = checkout.as_posix()
    body = (
        f"SOURCE = '{root}/src/core.py'\n"
        "diff --git a/tests/test_core.py b/tests/test_core.py\n"
        "--- a/tests/test_core.py\n"
        "+++ b/tests/test_core.py\n"
        "@@ -0,0 +1 @@\n"
        f"+FIXTURE = '{root}/tests/fixtures/input.json'\n"
    )
    prepared = prepare_body(checkout, body)
    raw = capture_bytes(prepared)
    assert raw.count(body.encode("utf-8")) == 1, (
        "FAIL AC-22: the embedded diff body is not byte-exact in the capture"
    )
    labels = raw.replace(body.encode("utf-8"), b"")
    assert f"{root}/".encode() not in labels, (
        "FAIL AC-22: a generated label outside the embedded bodies carries the "
        "absolute checkout path"
    )
    assert PLAN_PATH.encode() in labels, (
        "FAIL AC-22: the generated labels do not name the plan checkout-relative"
    )
    assert frozen_route(prepared).kind == "inline"


def test_ac10_ac22_fenced_crlf_plan_and_spec_sections_arrive_whole_and_byte_exact(
    tmp_path, monkeypatch
):
    """AC-10/AC-22: real plan and spec files with fences and CRLF spans.

    The host's m1 plan section and AC-1 block each hold a fenced example (a
    `## ` heading and milestone and AC lookalikes, text after it) and a CRLF
    span naming the checkout path, with a fenced lookalike of each selected
    heading before the real one. The m1 section also holds a four-backtick
    fence around a three-backtick block with a `## ` line inside, which a
    fence rule toggling on every fence-like line would read as a boundary.
    Both also hold an HTML comment around the same fenced example, with text
    after it; CommonMark reads the comment as one HTML block, so a fence-only
    rule would read its `## ` line as a boundary. Indented code holding a
    literal fence marker sits right before the m2 heading and right before
    the AC-1 heading; it opens no fence, so m1 ends at m2 and the AC-1 block
    is found. The Antigravity milestone review, reading them through the
    runtime context, embeds each whole section as its source bytes in its own
    milestone context, and Heddle's labels stay checkout-relative.
    """
    from tests.gate.test_lane_capabilities import (
        assert_sections_arrive_whole,
        prepare_reviewer,
        write_fenced_crlf_sections,
    )

    host, _state_path = antigravity_host(tmp_path, monkeypatch, role="milestone-review")
    sections = write_fenced_crlf_sections(host)
    _context, prepared = prepare_reviewer(host, "milestone-review", ANTIGRAVITY)
    assert prepared.invocation.exec_config.cli == "antigravity"
    assert_sections_arrive_whole(prepared, host, sections, "AC-10/AC-22 antigravity")
    assert frozen_route(prepared).kind == "inline"


# --------------------------------------------------------------------------- #
# AC-10: the milestone context and the lane block
# --------------------------------------------------------------------------- #
def test_ac10_milestone_capture_embeds_the_diff_plan_and_spec_sections(checkout):
    """AC-10: the lane's own milestone context carries all three inputs."""
    body = "DIFF_BODY_SENTINEL = 1\n"
    lane = prepare_body(checkout, body)
    codex = prepare_body(checkout, body, cli="codex")
    sections = lane_sections(lane, codex)
    for sentinel in (
        "DIFF_BODY_SENTINEL",
        "PLAN_MILESTONE_SENTINEL",
        "SPEC_AC_SENTINEL",
    ):
        assert sentinel in sections, (
            f"FAIL AC-10: the Antigravity milestone context lacks {sentinel}"
        )
    assert "codex" not in sections.lower(), (
        "FAIL AC-10: the Antigravity capture carries Codex-labelled context"
    )


@pytest.mark.parametrize("gate", ["milestone-review", "peer-review-sequential"])
def test_ac10_lane_block_names_forbidden_tools_and_paths_and_bans_heddle(
    checkout, gate
):
    """AC-10: the runtime lane block in milestone and final peer review captures.

    It names forbidden tools and paths, says not to run heddle whatever host
    rules say, and says to read captured input with read_file only. The
    ``reviews/`` rule draws the run temp directory boundary: this run's chunk
    index and chunks may be read, and every other ``reviews/`` path, sibling
    and earlier review output included, stays forbidden.
    """
    lane = prepare_body(checkout, "VALUE = 2\n", gate=gate)
    codex = prepare_body(checkout, "VALUE = 2\n", gate=gate, cli="codex")
    block = lane_sections(lane, codex)
    assert block, f"FAIL AC-10: the {gate} capture has no Antigravity lane block"
    lowered = block.lower()
    for path in ("reviews/", ".gemini"):
        assert path in block, f"FAIL AC-10: the lane block does not name {path}"
    for tool in ("search_web", "read_url_content", "run_workflow", "ask_question"):
        assert tool in block, f"FAIL AC-10: the lane block does not forbid {tool}"
    for kind in ("subagent", "browser"):
        assert kind in lowered, f"FAIL AC-10: the lane block does not forbid {kind}"
    lines = block.splitlines()
    assert any(
        "heddle" in line.lower()
        and re.search(r"\b(?:not|never|no)\b|n't", line, re.IGNORECASE)
        and re.search(r"AGENTS|CLAUDE|host|repositor|rule|instruction", line, re.I)
        for line in lines
    ), "FAIL AC-10: no line bans heddle whatever the host rules say"
    read_rule = [
        line for line in lines if "read_file" in line and "only" in line.lower()
    ]
    assert read_rule, (
        "FAIL AC-10: no line says to read captured input with read_file only"
    )
    assert any(
        "index" in line.lower() and "chunk" in line.lower() for line in read_rule
    ), "FAIL AC-10: the read_file rule does not name the chunk index and the chunks"
    reviews_rule = " ".join(line for line in lines if "reviews/" in line).lower()
    assert re.search(r"\b(?:this|the current) run\b", reviews_rule) and all(
        word in reviews_rule for word in ("index", "chunk")
    ), "FAIL AC-10: the reviews/ rule does not allow this run's chunk index and chunks"
    assert re.search(
        r"\b(?:every|any|all) other\b[^.;]*\b(?:forbidden|never|not)\b", reviews_rule
    ) and all(word in reviews_rule for word in ("sibling", "earlier")), (
        "FAIL AC-10: the reviews/ rule does not keep every other reviews/ path, "
        "sibling and earlier review output included, forbidden"
    )


def test_ac4_ac10_antigravity_output_constraint_heads_the_capture(checkout):
    """AC-4/AC-10: the lane has its own output wording, first in the capture."""
    constraint = output_constraint("antigravity")
    assert constraint not in {
        output_constraint("claude"),
        output_constraint("codex"),
    }, "FAIL AC-10: output_constraint('antigravity') reuses another lane's wording"
    assert "JSON" in constraint
    prepared = prepare_body(checkout, "VALUE = 3\n")
    assert prepared.transport.system is None
    assert prepared.transport.stdin.startswith(constraint)
    assert prepared.transport.stdin.count(constraint) == 1


# --------------------------------------------------------------------------- #
# AC-4: the preview resolves through the policy slot
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("model", "effort"),
    [("gemini-4-neon", "high"), ("gemini-4-hydrogen", "medium")],
    ids=["neon-high", "hydrogen-medium"],
)
def test_ac4_show_prompt_previews_through_the_antigravity_slot(
    tmp_path, monkeypatch, capsys, model, effort
):
    """AC-4: ``show-prompt --cli antigravity`` prepares the slot's reviewer.

    The non-default variant shows the slot, not the per-CLI default table,
    supplies model and effort. The preview writes nothing.
    """
    from heddle.cli import main
    from tests.tiering_review_helpers import V7_FEATURE

    reviewer = {"cli": "antigravity", "model": model, "reasoning_effort": effort}
    _host, state_path = antigravity_host(tmp_path, monkeypatch, reviewer)
    before = state_path.read_bytes()
    code = main(
        [
            "show-prompt",
            "spec-review",
            "--feature",
            V7_FEATURE,
            "--cli",
            "antigravity",
            "--json",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0 and envelope["ok"], f"FAIL AC-4: preview refused: {envelope}"
    data = envelope["data"]
    execution = {key: data["execution"][key] for key in reviewer}
    assert (data["cli"], execution) == ("antigravity", reviewer), (
        f"FAIL AC-4: the preview did not resolve the slot: {data['execution']}"
    )
    assert data["identity_status"] == "prepared" and data["run_identity"]
    assert data["prompt"].startswith(output_constraint("antigravity"))
    assert output_constraint("antigravity") != output_constraint("claude"), (
        "FAIL AC-4: the preview shows another lane's output wording"
    )
    assert state_path.read_bytes() == before, "FAIL AC-4: the preview wrote state"
