"""m3: Claude and Codex identity pins and the checkout-path fix (AC-22, AC-23).

The literals below were recorded on 2026-11-24 from the unmodified preparation
code at base commit da1cffe, before the lane capability table or the
checkout-path fix existed. They are records, not oracles: no test recomputes
them, and a change to them needs the same justification as an identity change.

Fixtures are synthetic disposable hosts (`tests.concurrent_review_helpers`
`same_gate_host`: real Git, real snapshot, real gate context and preparation;
each role has a Codex primary and a Claude secondary). Three choices make the
recorded identities independent of the machine and of later wording edits:

- Git author and committer dates are fixed, because the runtime manifest names
  the feature-baseline commit, whose hash depends on them.
- The gate prompt template and the decision policy are host overrides, so the
  pins move with Heddle-generated text (output constraint, Codex instructions,
  runtime manifest, milestone context, navigation policy), never with packaged
  prompt wording.
- No checkout path appears in any owned source, spec or plan. AC-22's bodies
  carry it only where the review basis does not hash it: inside the prepared
  diff, injected by `_inject_checkout_bodies` at the diff-resolution test seam
  (`heddle.gate.diff.resolve_diff`), and in the verification-command facts
  (`_set_checkout_commands`). Owned, spec and plan content is hashed raw into
  the review basis, so a path there would make the basis, and the input hash,
  depend on the disposable host's location.

Today `_stable_text` strips `<checkout>/` from every runtime section, so the
recorded AC-22 baselines are independent of the checkout path: each was
observed equal under two differently named temporary roots.

AC-23 survivor pins (pass now, must pass byte for byte after the refactor and
the fix): each Codex preparation decision (navigation, local instructions for
the four gates, milestone insertion and construction) and Claude runs. Each pin
holds the effective digest, review basis, input hash and the exact replay text:
the SHA-256 of the provider stdin, the exact system text, and the ordered
runtime sections by first line and SHA-256.

AC-22 red discriminators:

- Codex: the milestone diff and test-file bodies arrive byte-exact, labels stay
  checkout-relative, and the effective digest and input hash differ from the
  recorded baseline while the review basis equals it. Fails now because
  `_stable_text` rewrites the embedded bodies;
- Claude: the same, for the body Claude embeds. Claude embeds no diff and no
  test-file body, so its affected input is a review-test-scaffolding run whose
  verification-command facts carry the checkout path;
- recovery after landing, for each lane (`AFFECTED`): the affected member's
  output, published under its recorded pre-change identity, is stale and its
  member makes exactly one provider call, while the other member's output is
  reused. Fails now because the identity has not changed, so both are reused;
- the recovery rule, one signal at a time: a publication whose input hash
  alone, or effective prompt digest alone, differs reruns once beside a reused
  member. Fails now because recovery refuses any binding difference.

AC-22 survivors (pass now and after the fix): the affected input's review basis
under any checkout path; the Claude milestone review, which embeds no diff and
keeps its identities; a review recorded before the identity change, which
status and orient read as current (freshness observed at the public boundary,
see that test); and the recovery control below. The Antigravity half belongs
to m4.

Recovery rule (owner ruling, 2026-11-24): an unrecorded publication with the
same feature, gate, scope, assignment, round and reviewer slot as the prepared
run, but a different input hash or effective prompt digest, is stale. It is
not reused, its member makes exactly one provider call, and no member of the
batch is refused. Any other binding mismatch still refuses before a provider
call (survivor control). Today `_unrecorded_completion`
(heddle/runtime/gate_run.py) refuses every mismatch through
`validate_review_replay` (heddle/gate/entry.py); m3 owns both.

Test seams: `heddle.gate.diff.resolve_diff` (`_inject_checkout_bodies`), the
gate-engine provider doubles at `heddle.gate.entry.run_gate_for_runtime`
(`_recover`, `_review_engine`), and `heddle.gate.entry.prepare_gate_run`
(`_recorded_before_the_change`), which stands in a pre-change identity for a
review recorded before landing.
"""

from __future__ import annotations

import hashlib
import multiprocessing
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pytest
import yaml

from tests.concurrent_review_helpers import (
    PRIMARY,
    SECONDARY,
    SLOT_REVIEWERS,
    canonical_outcome,
    edit_state,
    execute_batch,
    gate_runs,
    install_counting_engine,
    member_key,
    revision,
    run_slot,
    same_gate_host,
)
from tests.tiering_review_helpers import V7_FEATURE, dispose, disposition

FIXED_GIT_DATE = "2026-11-24T00:00:00+0000"
MILESTONE = "milestone-review"
LARGE_SOURCE_LINES = 60_000


@dataclass(frozen=True)
class Identity:
    """The three recorded identities of one prepared input."""

    effective: str
    basis: str
    input: str


@dataclass(frozen=True)
class Pin:
    """One recorded preparation: identities plus the exact replay text."""

    identity: Identity
    navigation: bool
    stdin_sha256: str
    system: str | None
    sections: tuple[tuple[str, str], ...]


# The four Codex-only preparation decisions the capability table replaces.
NAVIGATION = "navigation"
LOCAL = "local-instructions"
MILESTONE_EMBED = "milestone-insertion-and-construction"


@dataclass(frozen=True)
class Fixture:
    role: str
    slot: str
    decisions: frozenset[str]
    large: bool = False


FIXTURES = {
    # Codex local instructions for each of the four gates.
    "codex-spec-review": Fixture("spec-review", PRIMARY, frozenset({LOCAL})),
    "codex-plan-review": Fixture("plan-review", PRIMARY, frozenset({LOCAL})),
    "codex-review-test-scaffolding": Fixture(
        "review-test-scaffolding", PRIMARY, frozenset({LOCAL})
    ),
    # Milestone context: built (construction) and appended (insertion).
    "codex-milestone-review": Fixture(
        MILESTONE, PRIMARY, frozenset({LOCAL, MILESTONE_EMBED})
    ),
    # Navigation: the assembled input exceeds CODEX_INLINE_LIMIT.
    "codex-navigation": Fixture(
        MILESTONE, PRIMARY, frozenset({LOCAL, MILESTONE_EMBED, NAVIGATION}), large=True
    ),
    # Claude runs: none of the four decisions applies.
    "claude-milestone-review": Fixture(MILESTONE, SECONDARY, frozenset()),
    "claude-review-test-scaffolding": Fixture(
        "review-test-scaffolding", SECONDARY, frozenset()
    ),
}

_CLAUDE_SYSTEM = (
    "Produce the structured review JSON object using the supplied JSON Schema."
)

# First lines of the runtime sections a prepared run can carry.
LEDGER = "Native document-review assignment context:"
SCAFFOLD_CONTEXT = "Resolved authoritative inputs for review-test-scaffolding:"
LOCAL_INSTRUCTIONS = "Codex local-workspace instruction:"
RUNTIME_MANIFEST = "Gate Runtime Manifest (operator-generated; authoritative)"
MILESTONE_CONTEXT = "AUTHORITATIVE INPUT RESOLUTION OVERRIDE FOR CODEX:"

# Recorded 2026-11-24 at da1cffe; see the module docstring.
PINS: dict[str, Pin] = {
    "codex-spec-review": Pin(
        Identity(
            "fdd51a5b3c892f152c66706eaf8346cd8183503be47a88e1618f0e9f619ea25d",
            "7286f632b01a74b41dd90ed326f9c9400ed45efcb026bf5e4d62fde1dddf32ef",
            "9fd51fbbc0c5f5d474033bd7db55dbf49c651fb58ac691128c8e5f9a49698fd0",
        ),
        navigation=False,
        stdin_sha256="9a8b593c568e86f791e2e0e6a2e2b45c9061ce23d00887cd9f626c9a84c5508b",
        system=None,
        sections=(
            (
                LEDGER,
                "40bc20dd3101ce4abf81fbebc4959d23d33582b865c660497cce4c7908a7fddb",
            ),
            (
                LOCAL_INSTRUCTIONS,
                "be5bdf07d2d1cd8f10825c7ad80e6770c1acea54f1ed4957ceeb4a45419f9925",
            ),
            (
                RUNTIME_MANIFEST,
                "c6c128d2c7dbb65ae76ed85ac421ba0cee20599b3841b6909bd718d85c4a5af5",
            ),
        ),
    ),
    "codex-plan-review": Pin(
        Identity(
            "55bd323e8ae22516434ef6637c5e5819d508d2d5e2fffe4ff7ee6342c330eef9",
            "150e79a4a577177cc7006af259d3aa10c6699348e1ed5dbddccfe58f23471e67",
            "3ddcb6a6a66dfc18a07caf52584ce0bc09137903aec4bca7d84eef3820ecb129",
        ),
        navigation=False,
        stdin_sha256="535f4e7945b579ad3c3f4013dc1a67d7e3fdd9e3c6a504bd5fc338862dd8520c",
        system=None,
        sections=(
            (
                LEDGER,
                "6579aeaa212169bd48453826ba764596aeaeeaf632782ede7fa7481c2b03b03d",
            ),
            (
                LOCAL_INSTRUCTIONS,
                "5ca16e1254719f620ca5b8af0313739facc1b9d990f5232b09864b04f78838ec",
            ),
            (
                RUNTIME_MANIFEST,
                "b86fdf958f7758922e5ed612cbbcae4e3b050e5fb2627b8f4895f756ffc9ed78",
            ),
        ),
    ),
    "codex-review-test-scaffolding": Pin(
        Identity(
            "11b482d1b338a49a5625b855ecfcc4a6cbe5a29dbbea019955575bfdaa677209",
            "a973e80a1574ca4fb8397a820d9b66bd23feb627e429cbbfaa6855df15a01c3a",
            "fb4afcbc1379c2217d198493baa78bdc9d07ab52cdf6a0a1a0c6cc08a98d124d",
        ),
        navigation=False,
        stdin_sha256="010faad33298c5cce5324a062119a5c61dff9d4c410f3b3e1c2f0000d0aa8f71",
        system=None,
        sections=(
            (
                LEDGER,
                "492d93441047aad9a0253e88d47157f1fd6368e581db3ea0678ccab5ed65c16a",
            ),
            (
                SCAFFOLD_CONTEXT,
                "2c08e8e9db980bc33b540df5fd1e6da1449fff19e406a12a0f3f037138140697",
            ),
            (
                LOCAL_INSTRUCTIONS,
                "7dc10a110dd2ce814630496b8fe1d6940b688e7b3bab43cc5a3a5f765ca50cba",
            ),
            (
                RUNTIME_MANIFEST,
                "b8e805ec22ef17fe2591108304c39c585d3f0f15fa7d90c95a966aee03a466f6",
            ),
        ),
    ),
    "codex-milestone-review": Pin(
        Identity(
            "020515c70461ebf39cd5c58775df4ae8c0b63008d3b2ffb07a5050749099bfda",
            "54fe1c697447172d60a7ae7ea2915e62518774eb3ae6b24657667feb144e2bf7",
            "26dbbe884a28181bd8f629683c155a533553993a3c6a5d6111fc09d830946088",
        ),
        navigation=False,
        stdin_sha256="2ed090f3d26ec2ff79587f7cb5a1879c4b3cc224a55ab3cb1ecc1c31d6b5e2d1",
        system=None,
        sections=(
            (
                LEDGER,
                "071df4c93a50090c8fdac9695dc789d0125470d8c9f1113415a5ec5fd7064920",
            ),
            (
                LOCAL_INSTRUCTIONS,
                "7b752d888aae55c24b83e99b0c97ae4a05c04330d3df9aaaa80d2dcf75224454",
            ),
            (
                RUNTIME_MANIFEST,
                "745cacc1c54308772b1a06d46ea1e497da67b569eaf9755511144e751acee15a",
            ),
            (
                MILESTONE_CONTEXT,
                "96e07a3fcca9e472b659e21ee67f91e8810e6b5650399a96cc017b26c835e4b3",
            ),
        ),
    ),
    "codex-navigation": Pin(
        Identity(
            "0092c4bf0ec8456e49bd1cab55f571f95df19ec8fd6a20bb2dd8e38efd64f7b0",
            "d0780b0cecb8df0675dfe64ddccdae863d24042245df042e5a6dd6736160793d",
            "5add66ec03c00d0f44449056a07394bbd678af1c822e0cc5bde664302474e865",
        ),
        navigation=True,
        stdin_sha256="f92cdd5dc077c5f86a817ec03aa5b2a185c9c327499f756a075837acf8ca9fbc",
        system=None,
        sections=(
            (
                LEDGER,
                "071df4c93a50090c8fdac9695dc789d0125470d8c9f1113415a5ec5fd7064920",
            ),
            (
                LOCAL_INSTRUCTIONS,
                "7b752d888aae55c24b83e99b0c97ae4a05c04330d3df9aaaa80d2dcf75224454",
            ),
            (
                RUNTIME_MANIFEST,
                "745cacc1c54308772b1a06d46ea1e497da67b569eaf9755511144e751acee15a",
            ),
            (
                MILESTONE_CONTEXT,
                "7a636b4774a2c772788cd49832032a55402d7dbbbc01d764e51301a901fc919a",
            ),
        ),
    ),
    "claude-milestone-review": Pin(
        Identity(
            "39ea86dd344e3f4f414f0b5d8e9729894d7ad3777139f89ab203ed4174187d2d",
            "3f9c74a02344f4f0120bb2c81249b2557d63f9d6e443f2df6e0e8aa8d11c5e64",
            "c3165acc50da6b59b76f068c02933a88e95900e29de48b4e3c2e1efba981ebca",
        ),
        navigation=False,
        stdin_sha256="6d12ca421bbc5d9ab0e6b38b253129e9b1f6418eb91ccee33c13ce5f4726d84f",
        system=_CLAUDE_SYSTEM,
        sections=(
            (
                LEDGER,
                "071df4c93a50090c8fdac9695dc789d0125470d8c9f1113415a5ec5fd7064920",
            ),
            (
                RUNTIME_MANIFEST,
                "745cacc1c54308772b1a06d46ea1e497da67b569eaf9755511144e751acee15a",
            ),
        ),
    ),
    "claude-review-test-scaffolding": Pin(
        Identity(
            "a3b4cabc29d165598cec714589ba5c339980f839417fc1d3b5f95a1ea436230b",
            "4a5d6983c991b680cefe5598605396071590eed6edcd0763f230e1757fb6754f",
            "3efff1618fff3e5da01ae20570918d11402d2915fc88def3e14568442118a938",
        ),
        navigation=False,
        stdin_sha256="c58659743068f8b55473d3e2e838a422f6af413ad5ea253c07d6694658127d2d",
        system=_CLAUDE_SYSTEM,
        sections=(
            (
                LEDGER,
                "492d93441047aad9a0253e88d47157f1fd6368e581db3ea0678ccab5ed65c16a",
            ),
            (
                SCAFFOLD_CONTEXT,
                "2c08e8e9db980bc33b540df5fd1e6da1449fff19e406a12a0f3f037138140697",
            ),
            (
                RUNTIME_MANIFEST,
                "b8e805ec22ef17fe2591108304c39c585d3f0f15fa7d90c95a966aee03a466f6",
            ),
        ),
    ),
}

# AC-22 baseline, recorded 2026-11-24 at da1cffe: the milestone review whose
# prepared diff carries the absolute checkout path in a source body and a
# test-file body (`_checkout_bodies`).
AC22_CODEX_BASELINE = Identity(
    "eb6eb9e80b1ac01b6b59993de16259facb4c7858d4faf0aec56f17f9ed32bc45",
    "54fe1c697447172d60a7ae7ea2915e62518774eb3ae6b24657667feb144e2bf7",
    "961058a46a30db8f5cf5e46a69253a939dbf315e9cd44e59db6750c937d28438",
)
# Claude embeds no diff, so the Claude milestone review with those bodies has
# the AC-23 Claude milestone pin as its baseline; it is the recovery witness's
# unaffected member.
AC22_CLAUDE_BASELINE_FIXTURE = "claude-milestone-review"
# AC-22 Claude baseline, recorded 2026-11-24 at da1cffe: the Claude
# review-test-scaffolding run whose embedded verification-command facts carry
# the absolute checkout path (`_checkout_commands`). Its basis equals the
# AC-23 Claude scaffolding pin's: the facts are not part of the basis.
AC22_CLAUDE_FACTS_BASELINE = Identity(
    "e1b7c68d1b9d758c7a77e5976ba54e5b43b42d7a9f467d133a8a6064ae741cb5",
    "4a5d6983c991b680cefe5598605396071590eed6edcd0763f230e1757fb6754f",
    "3791a0d55b5eccc89c0a9127853e75355f567b3e05196a238de258d0376a62ec",
)


@dataclass(frozen=True)
class Affected:
    """One lane's AC-22 affected input and the other member of its batch."""

    role: str
    slot: str
    baseline: Identity
    # The other member's publication at recovery: its recorded AC-23 identity,
    # or None for its current preparation when every member of the batch
    # embeds the affected body.
    reusable: Identity | None
    # Rounds the secondary slot serves; a Claude verification round needs two.
    window: int
    finding_id: str


AFFECTED = {
    # Codex embeds the milestone diff; the Claude milestone member embeds none.
    "codex-milestone-diff": Affected(
        MILESTONE,
        PRIMARY,
        AC22_CODEX_BASELINE,
        PINS[AC22_CLAUDE_BASELINE_FIXTURE].identity,
        1,
        "MR-I1",
    ),
    # Both scaffolding members embed the command facts, so the Codex member
    # is published under its current preparation, reusable before and after
    # the fix. No batch offers this case an AC-23 neighbour the fix leaves
    # unchanged; see the recovery witness's docstring.
    "claude-command-facts": Affected(
        "review-test-scaffolding",
        SECONDARY,
        AC22_CLAUDE_FACTS_BASELINE,
        None,
        2,
        "TS-I1",
    ),
}


def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str, **kwargs):
    """A dual-slot host for `role` whose identities are machine-independent."""
    checkout = kwargs.pop("checkout", "checkout")
    large = kwargs.pop("large", False)
    window = kwargs.pop("window", 1)
    assert not kwargs, kwargs
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, FIXED_GIT_DATE)
    base = tmp_path / checkout
    base.mkdir()
    host, _state_path = same_gate_host(base, monkeypatch, role=role, window=window)
    overrides = host / ".heddle/overrides"
    overrides.mkdir(parents=True, exist_ok=True)
    (overrides / "decision-routing.md").write_text(
        "Fixture decision policy: route every decision to its owner.\n",
        encoding="utf-8",
    )
    (overrides / f"{role}.prompt.md").write_text(
        f"Fixture {role} instructions for [feature] on [cli-tool]"
        "[review-file-suffix].\n"
        "Spec: [spec-path]. Plan: [plan-path]. Milestone: [milestone].\n",
        encoding="utf-8",
    )
    if large:
        lines = "".join(f"FILLER_{n:06d} = {n}\n" for n in range(LARGE_SOURCE_LINES))
        (host / "src/example.py").write_text("VALUE = 7\n" + lines, encoding="utf-8")
    return host


def _invocation(snapshot, config, gate, slot_reviewer):
    from heddle.gate.types import GateInvocationOverrides
    from heddle.runtime.review_assignments import resolve_invocation

    overrides = GateInvocationOverrides(
        cli=slot_reviewer["cli"],
        model=slot_reviewer["model"],
        reasoning_effort=slot_reviewer["reasoning_effort"],
    )
    # The same resolution `run-gate` and `run-gates` use for a slot.
    return resolve_invocation(snapshot, config, gate, overrides, admit=True)


def _prepare(host: Path, role: str, slot: str):
    """Prepare one slot through the runtime's gate context and preparation."""
    from heddle.gate import entry
    from heddle.gate.registry import GATES
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    config = load_project_config(host)
    snapshot = resolve_snapshot(config, V7_FEATURE)
    gate = GATES[role]
    reviewer = SLOT_REVIEWERS[role][slot]
    invocation = _invocation(snapshot, config, gate, reviewer)
    context = entry.build_gate_context(snapshot, config, gate, cli=reviewer["cli"])
    prepared = entry.prepare_gate_run(context, gate_type=gate, invocation=invocation)
    return context, prepared


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decisions(prepared) -> frozenset[str]:
    """Which Codex-only preparation decisions shaped this prepared input."""
    from heddle.gate.input_navigation import CODEX_INLINE_LIMIT, NAVIGATION_POLICY

    heads = [section.split("\n", 1)[0] for section in prepared.runtime_sections]
    stdin = prepared.transport.stdin
    taken = set()
    if stdin.startswith(NAVIGATION_POLICY + "\n\n"):
        assert len(stdin) - len(NAVIGATION_POLICY) - 2 > CODEX_INLINE_LIMIT
        taken.add(NAVIGATION)
    if LOCAL_INSTRUCTIONS in heads:
        taken.add(LOCAL)
    if MILESTONE_CONTEXT in heads:
        # Constructed by the Codex milestone builder, inserted last.
        assert heads[-1] == MILESTONE_CONTEXT
        assert prepared.diff_text in prepared.runtime_sections[-1]
        taken.add(MILESTONE_EMBED)
    return frozenset(taken)


def _observe(prepared) -> Pin:
    from heddle.gate.input_navigation import NAVIGATION_POLICY

    return Pin(
        identity=Identity(
            prepared.effective_prompt_sha256,
            prepared.review_basis_hash,
            prepared.input_hash,
        ),
        navigation=prepared.transport.stdin.startswith(NAVIGATION_POLICY + "\n\n"),
        stdin_sha256=_sha256(prepared.transport.stdin),
        system=prepared.transport.system,
        sections=tuple(
            (section.split("\n", 1)[0], _sha256(section))
            for section in prepared.runtime_sections
        ),
    )


def _checkout_bodies(root: str) -> str:
    """A source body and a test-file body that contain the checkout path."""
    return (
        "diff --git a/src/settings.py b/src/settings.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/settings.py\n"
        "@@ -0,0 +1,2 @@\n"
        f'+DATA = "{root}/data/input.json"\n'
        f'+CACHE = "{root}/.cache/fixture"\n'
        "diff --git a/tests/test_settings.py b/tests/test_settings.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tests/test_settings.py\n"
        "@@ -0,0 +1,5 @@\n"
        "+from src.settings import DATA\n"
        "+\n"
        "+\n"
        "+def test_data_is_under_the_checkout():\n"
        f'+    assert DATA == "{root}/data/input.json"\n'
    )


def _checkout_commands(root: str) -> dict[str, str]:
    """Verification commands that name the checkout's own interpreter."""
    return {
        "smoke_test": f"{root}/.venv/bin/python tests/check.py",
        "test_command": f"{root}/.venv/bin/python -m pytest tests/check.py",
    }


def _set_checkout_commands(host: Path) -> dict[str, str]:
    """Record `_checkout_commands` as the feature's verification commands.

    The state file changes only in the working tree, after the fixture commit,
    so the baseline commit and its hash stay machine-independent.
    """
    commands = _checkout_commands(host.as_posix())

    def change(value):
        value["commands"] = dict(commands)

    edit_state(host / "plans" / V7_FEATURE / "state.yaml", change)
    return commands


def _inject_checkout_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Append `_checkout_bodies` to every resolved gate diff.

    Test seam at `heddle.gate.diff.resolve_diff`, which gate preparation
    imports at call time. Only the diff body changes: source, detail and the
    changed-file list stay as resolved, so the review basis and the Claude
    input are those of the uninjected host.
    """
    from heddle.gate import diff as diff_module

    original = diff_module.resolve_diff

    def resolve(diff_path, repo_root, temp_dir=None, **kwargs):
        result = original(diff_path, repo_root, temp_dir, **kwargs)
        assert temp_dir is not None, "gate preparation resolves into a temp dir"
        injected = Path(temp_dir) / "checkout-bodies.diff"
        injected.write_bytes(
            result.diff_path.read_bytes()
            + _checkout_bodies(Path(repo_root).as_posix()).encode("utf-8")
        )
        return replace(result, diff_path=injected)

    monkeypatch.setattr(diff_module, "resolve_diff", resolve)


def _affected_input(
    host: Path, monkeypatch: pytest.MonkeyPatch, case: Affected
) -> None:
    """Give the case's affected input its checkout-path bodies."""
    if case.role == MILESTONE:
        _inject_checkout_bodies(monkeypatch)
    else:
        _set_checkout_commands(host)


def _publish(
    host: Path,
    role: str,
    slot: str,
    identity: Identity | None,
    **binding: object,
) -> None:
    """Publish a worker's canonical output under a recorded identity.

    Assignment, round, slot, prompt version, execution and output contract
    are those of the current preparation unless `binding` overrides one; the
    three identities are the given literals, as an in-flight run at landing
    would carry them, or the current preparation's when `identity` is None.
    """
    context, prepared = _prepare(host, role, slot)
    if identity is not None:
        prepared = replace(
            prepared,
            effective_prompt_sha256=identity.effective,
            review_basis_hash=identity.basis,
            input_hash=identity.input,
        )
    context.prepared_run = replace(prepared, **binding)
    canonical_outcome(context.gate_type, context, V7_FEATURE)


def _recover(monkeypatch: pytest.MonkeyPatch):
    """Run the batch, whose recovery pass meets the published outputs.

    `install_counting_engine` is the fork-safe provider double at the
    gate-engine seam; it counts calls across worker processes.
    """
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        result = execute_batch()
        observed = list(calls)
    return result, observed


def _assert_only_rerun(result, calls, role: str, stale: str, reused: str) -> None:
    """Recovery reran the stale member once, reused the other, refused none."""
    refusal = None if result.ok else f"{result.error.code}: {result.error.message}"
    assert refusal is None, (
        "FAIL AC-22: recovery refused the batch instead of rerunning the "
        f"stale member: {refusal}"
    )
    members = {member_key(row): row for row in result.data["members"]}
    assert calls == [(role, stale)], (
        "FAIL AC-22: recovery must call the provider once for the stale member "
        f"and never for the reusable one; calls={calls}, "
        f"reuse={ {key: row['reuse'] for key, row in members.items()} }"
    )
    assert members[(role, stale)]["reuse"] == "none"
    assert members[(role, reused)]["reuse"] == "recovered"
    assert [row["publication"] for row in members.values()] == ["recorded"] * 2, (
        "FAIL AC-22: a recovery member was not recorded"
    )


def _review_engine(monkeypatch: pytest.MonkeyPatch, slot: str, finding_id: str):
    """In-process provider double: `slot` raises `finding_id` in round 1 and
    every later report accounts for the round's targets."""
    from tests.structured_review_helpers import finding

    def run(gate_type, context, *, feature, **_kwargs):
        prepared = context.prepared_run
        assert prepared is not None
        raised = prepared.reviewer_slot == slot and prepared.round_number == 1
        findings = (finding(finding_id, classification="implement"),) if raised else ()
        return canonical_outcome(gate_type, context, feature, findings=findings)

    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)


def _recorded_before_the_change(
    monkeypatch: pytest.MonkeyPatch, slot: str
) -> dict[str, str]:
    """Record `slot`'s runs under a stand-in pre-change identity.

    Test seam at `heddle.gate.entry.prepare_gate_run`, which the runtime calls
    to prepare each member. The effective digest and input hash are replaced
    by stand-ins derived from them, so the recorded run carries an identity
    the current preparation does not produce, as a review recorded before
    landing would. The review basis is kept. Returns the stand-ins.
    """
    from heddle.gate import entry

    original = entry.prepare_gate_run
    stand_in: dict[str, str] = {}

    def prepare(*args, **kwargs):
        prepared = original(*args, **kwargs)
        if prepared.reviewer_slot != slot:
            return prepared
        stand_in.update(
            effective_prompt_sha256=_sha256(
                "pre-change " + prepared.effective_prompt_sha256
            ),
            input_hash=_sha256("pre-change " + prepared.input_hash),
        )
        return replace(prepared, **stand_in)

    monkeypatch.setattr(entry, "prepare_gate_run", prepare)
    return stand_in


def _ok(result):
    assert result.ok, result.to_envelope()
    return result


def _gate_scope(state_path: Path, role: str) -> str:
    gates = yaml.safe_load(state_path.read_text())["gates"]
    return next(gate["scope"] for gate in gates if gate["gate"] == role)


def _open_verification_round(state_path: Path, role: str, scope: str):
    from tests.tiering_helpers import invoke

    return invoke(
        "ReviewRoundOpen",
        feature=V7_FEATURE,
        payload={
            "schema": "heddle.review-round-input/v1",
            "role": role,
            "scope": scope,
            "purpose": "verification",
            "reason": "Inspect the remaining original concern",
        },
        expect_revision=revision(state_path),
    )


def _pin_diff(observed: Pin, pinned: Pin) -> dict[str, tuple]:
    left, right = asdict(observed), asdict(pinned)
    return {key: (left[key], right[key]) for key in left if left[key] != right[key]}


# ---------------------------------------------------------------------------
# AC-23: Claude and Codex identities are otherwise unchanged (survivor pins)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_ac23_survivor_prepared_identity_and_replay_text_equal_the_recorded_pin(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin (AC-23): passes on the unmodified code and must keep passing
    byte for byte after the capability-table refactor and the checkout-path fix.
    """
    fixture = FIXTURES[name]
    host = _host(tmp_path, monkeypatch, fixture.role, large=fixture.large)
    _context, prepared = _prepare(host, fixture.role, fixture.slot)
    assert host.as_posix() not in prepared.transport.stdin, (
        f"FAIL AC-23 fixture {name}: the checkout path reached the prompt"
    )
    assert _decisions(prepared) == fixture.decisions, (
        f"FAIL AC-23 {name}: Codex preparation decisions taken "
        f"{sorted(_decisions(prepared))}, expected {sorted(fixture.decisions)}"
    )
    differences = _pin_diff(_observe(prepared), PINS[name])
    assert not differences, f"FAIL AC-23 {name}: (observed, pinned) {differences}"


# ---------------------------------------------------------------------------
# AC-22: embedded source bodies keep checkout paths (Claude and Codex half)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("checkout", ["checkout", "a-much-longer-checkout-name"])
def test_ac22_survivor_recorded_basis_is_the_basis_under_any_checkout_path(
    checkout: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin (AC-22 baseline): the affected Codex input's review basis
    equals the recorded baseline wherever the checkout lives, now and after the
    fix, because embedded bodies are not part of the basis."""
    host = _host(tmp_path, monkeypatch, MILESTONE, checkout=checkout)
    _inject_checkout_bodies(monkeypatch)
    _context, prepared = _prepare(host, MILESTONE, PRIMARY)
    assert host.as_posix() in prepared.diff_text, "fixture lost its checkout bodies"
    assert prepared.review_basis_hash == AC22_CODEX_BASELINE.basis, (
        "FAIL AC-22: the affected input's review basis differs from the "
        "recorded baseline"
    )


def test_ac22_codex_embedded_bodies_arrive_byte_exact_with_new_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red discriminator (AC-22, Codex): today `_stable_text` strips the
    checkout path from the embedded diff, so the body is rewritten and the
    identities still equal the recorded baseline."""
    host = _host(tmp_path, monkeypatch, MILESTONE)
    _inject_checkout_bodies(monkeypatch)
    root = host.as_posix()
    _context, prepared = _prepare(host, MILESTONE, PRIMARY)
    stdin = prepared.transport.stdin
    body = _checkout_bodies(root)
    assert body in prepared.diff_text, "fixture lost its checkout bodies"
    assert prepared.review_basis_hash == AC22_CODEX_BASELINE.basis

    # Booleans keep pytest from printing the whole prompt on failure.
    source_line = f'+DATA = "{root}/data/input.json"\n'
    test_line = f'+    assert DATA == "{root}/data/input.json"\n'
    exact = prepared.diff_text in stdin
    assert exact, (
        "FAIL AC-22: the embedded Codex diff does not arrive byte-exact; "
        f"source body kept: {source_line in stdin}, "
        f"test-file body kept: {test_line in stdin}"
    )
    labels = stdin.count(root) - prepared.diff_text.count(root)
    assert labels == 0, (
        f"FAIL AC-22: {labels} Heddle-generated path label(s) carry the checkout path"
    )
    assert prepared.effective_prompt_sha256 != AC22_CODEX_BASELINE.effective, (
        "FAIL AC-22: the effective digest still equals the recorded baseline"
    )
    assert prepared.input_hash != AC22_CODEX_BASELINE.input, (
        "FAIL AC-22: the input hash still equals the recorded baseline"
    )


def test_ac22_claude_command_facts_arrive_byte_exact_with_new_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red discriminator (AC-22, Claude half): the Claude affected input is a
    review-test-scaffolding run whose embedded verification-command facts carry
    the checkout path. Today `_stable_text` strips it from the facts, so the
    commands are rewritten and the identities equal the recorded baseline.

    The owner's ruling names an embedded plan section or the
    verification-command facts; this uses the facts. A plan-section body
    cannot have a recorded baseline: plan content is hashed raw into the review
    basis, so its basis would depend on the disposable host's location.
    """
    role = "review-test-scaffolding"
    host = _host(tmp_path, monkeypatch, role)
    root = host.as_posix()
    commands = _set_checkout_commands(host)
    _context, prepared = _prepare(host, role, SECONDARY)
    stdin = prepared.transport.stdin
    assert prepared.transport.system == _CLAUDE_SYSTEM
    assert "===== BEGIN VERIFICATION COMMAND FACTS =====" in stdin
    assert prepared.review_basis_hash == AC22_CLAUDE_FACTS_BASELINE.basis

    kept = {key: value in stdin for key, value in commands.items()}
    assert all(kept.values()), (
        f"FAIL AC-22: embedded Claude command facts are not byte-exact: {kept}"
    )
    labels = stdin.count(root) - sum(value.count(root) for value in commands.values())
    assert labels == 0, (
        f"FAIL AC-22: {labels} Heddle-generated path label(s) carry the checkout path"
    )
    assert prepared.effective_prompt_sha256 != AC22_CLAUDE_FACTS_BASELINE.effective, (
        "FAIL AC-22: the Claude effective digest still equals the recorded baseline"
    )
    assert prepared.input_hash != AC22_CLAUDE_FACTS_BASELINE.input, (
        "FAIL AC-22: the Claude input hash still equals the recorded baseline"
    )


def test_ac22_survivor_claude_milestone_review_embeds_no_diff_and_keeps_its_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor pin (AC-22, Claude): the Claude milestone review embeds neither
    the diff nor a test-file body, so checkout-path bodies in the diff never
    reach it and its identities equal the recorded baseline (the AC-23 Claude
    milestone pin) before and after the fix. It is the Codex recovery
    witness's unaffected member."""
    host = _host(tmp_path, monkeypatch, MILESTONE)
    _inject_checkout_bodies(monkeypatch)
    _context, prepared = _prepare(host, MILESTONE, SECONDARY)
    assert host.as_posix() in prepared.diff_text, "fixture lost its checkout bodies"
    assert "data/input.json" not in prepared.transport.stdin
    assert host.as_posix() not in prepared.transport.stdin
    assert _observe(prepared) == PINS[AC22_CLAUDE_BASELINE_FIXTURE], (
        "FAIL AC-22: the Claude identities differ from the recorded baseline"
    )


@pytest.mark.parametrize("lane", sorted(AFFECTED))
def test_ac22_survivor_review_recorded_before_the_change_reads_current_in_status(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor (AC-22 freshness, public boundary): a review of the affected
    input recorded before the identity change reads as current in status and
    orient, today and after the fix.

    Freshness is observed where Heddle judges a recorded review: a disposition
    whose evidence is a later-round review of the affected input. Round one
    raises a finding on the affected slot; round two's report on that slot
    addresses it and is recorded under a stand-in pre-change identity
    (`_recorded_before_the_change`) with the basis it has now, which must equal
    the recorded baseline basis. Status must then judge that review current:
    the assignment closes, its evidence reads current with no explanation, and
    no review launch is offered. A freshness judgement that followed the
    effective digest or input hash, or a fix that moved the basis, fails here.
    The Claude case gives the secondary a two-round window so the Claude slot
    serves the verification round.
    """
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    case = AFFECTED[lane]
    host = _host(tmp_path, monkeypatch, case.role, window=case.window)
    _affected_input(host, monkeypatch, case)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    _review_engine(monkeypatch, case.slot, case.finding_id)

    first = {}
    for slot in (PRIMARY, SECONDARY):
        _ok(run_slot(case.role, slot))
        first[slot] = gate_runs(state_path)[-1]["run_id"]
    origin = first[case.slot]
    _ok(
        dispose(
            state_path,
            [
                disposition(origin, case.finding_id, status="retained"),
                *(
                    disposition(run_id, "@coverage", status="settled")
                    for run_id in first.values()
                ),
            ],
        )
    )
    scope = _gate_scope(state_path, case.role)
    _ok(_open_verification_round(state_path, case.role, scope))
    second = {}
    with monkeypatch.context() as patch:
        stand_in = _recorded_before_the_change(patch, case.slot)
        for slot in (PRIMARY, SECONDARY)[: case.window]:
            _ok(run_slot(case.role, slot))
            second[slot] = gate_runs(state_path)[-1]["run_id"]
    review = second[case.slot]
    cited = dispose(
        state_path,
        [
            disposition(
                origin,
                case.finding_id,
                evidence_kind="review",
                review_run_id=review,
            ),
            *(
                disposition(run_id, "@coverage", status="settled")
                for run_id in second.values()
            ),
        ],
    )
    rejected = [
        (row["finding_id"], row["predicate"], row.get("status"))
        for row in (cited.to_envelope().get("error") or {})
        .get("details", {})
        .get("rows", [])
    ]
    assert cited.ok, (
        "FAIL AC-22: the review recorded before the identity change was "
        f"refused as disposition evidence: {rejected}"
    )

    status = _ok(execute(ops.Status(feature=V7_FEATURE)))
    orient = _ok(execute(ops.Orient(feature=V7_FEATURE)))
    recorded = next(
        run
        for gate in status.data["gates"]
        for run in gate["runs"]
        if run["run_id"] == review
    )
    assert stand_in and {key: recorded[key] for key in stand_in} == stand_in, (
        "fixture did not record the review under its stand-in identity"
    )
    assert recorded["review_basis_hash"] == case.baseline.basis, (
        "FAIL AC-22: the recorded review's basis differs from the recorded "
        "pre-change baseline basis"
    )
    rows = [
        next(
            row
            for row in result.data["review_closure"]["assignments"]
            if row["role"] == case.role and row["scope"] == scope
        )
        for result in (status, orient)
    ]
    assert rows[0] == rows[1], "FAIL AC-22: status and orient disagree"
    row = rows[0]
    assert row["evidence_state"] == "current" and not row["evidence_explanations"], (
        "FAIL AC-22: status reads the review recorded before the identity "
        f"change as {row['evidence_state']}: {row['evidence_explanations']}"
    )
    assert row["closed"] and row["open_refs"] == [], (
        f"FAIL AC-22: the review-backed disposition reopened: {row['open_refs']}"
    )
    launches = [
        action["command"]
        for action in status.to_envelope()["next_actions"]
        if "run-gate" in action["command"] or "round-open" in action["command"]
    ]
    assert launches == [], f"FAIL AC-22: a current review offers a launch: {launches}"


@pytest.mark.parametrize("lane", sorted(AFFECTED))
def test_ac22_recovery_reruns_only_the_affected_member_published_before_landing(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red discriminator (AC-22 recovery witness): outputs published under the
    recorded pre-change identities (an in-flight run at landing) are
    recovered. The affected member's identity must have changed, so recovery
    treats its publication as stale: its output is not reused and it makes
    exactly one provider call. The other member is reused with none. Today
    both are reused and the double sees no call.

    Codex case: the affected Codex milestone member beside the unaffected
    Claude milestone member (the AC-23 Claude milestone fixture). Claude case:
    the affected Claude scaffolding member beside the Codex scaffolding
    member, which embeds the same command facts and so is published under its
    current preparation.

    No approved layout gives the affected Claude member a neighbour whose
    recorded AC-23 identity the fix leaves unchanged: the scaffold stage runs
    only the scaffolding role, its other slot uses the other CLI, and the
    Codex scaffolding input carries every section the Claude one does,
    including the command facts. Reuse of a genuinely unchanged pre-change
    AC-23 publication beside a stale Claude member is witnessed by the
    `claude-*` cases of
    `test_ac22_recovery_reruns_a_member_whose_input_or_effective_digest_alone_differs`,
    where the Codex milestone member keeps its AC-23 pin.

    Test seams: `_inject_checkout_bodies` patches
    `heddle.gate.diff.resolve_diff` so the prepared diff carries the checkout
    path while owned content does not (the recorded identities stay
    machine-independent); `_recover` uses the fork-safe counting provider
    double at the gate-engine seam.
    """
    case = AFFECTED[lane]
    other = SECONDARY if case.slot == PRIMARY else PRIMARY
    host = _host(tmp_path, monkeypatch, case.role)
    _affected_input(host, monkeypatch, case)
    _publish(host, case.role, case.slot, case.baseline)
    _publish(host, case.role, other, case.reusable)
    result, calls = _recover(monkeypatch)
    _assert_only_rerun(result, calls, case.role, case.slot, other)


@pytest.mark.parametrize(
    ("slot", "signal"),
    [
        (PRIMARY, "input"),
        (PRIMARY, "effective"),
        (SECONDARY, "input"),
        (SECONDARY, "effective"),
    ],
    ids=[
        "codex-input-hash-only",
        "codex-effective-digest-only",
        "claude-input-hash-only",
        "claude-effective-digest-only",
    ],
)
def test_ac22_recovery_reruns_a_member_whose_input_or_effective_digest_alone_differs(
    slot: str, signal: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red discriminator (AC-22 recovery rule): either identity signal alone
    makes a publication stale. One member is published under its recorded
    AC-23 identity with exactly one of the input hash and the effective prompt
    digest replaced, the other under its recorded AC-23 identity unchanged. The
    changed member reruns once and the other is reused; a rule that needs both
    signals to differ refuses the batch and fails here. Today recovery refuses
    any binding difference, so the batch is refused.
    """
    host = _host(tmp_path, monkeypatch, MILESTONE)
    other = SECONDARY if slot == PRIMARY else PRIMARY
    pinned = {
        PRIMARY: PINS["codex-milestone-review"].identity,
        SECONDARY: PINS["claude-milestone-review"].identity,
    }
    changed = replace(
        pinned[slot], **{signal: _sha256(f"published before landing: {signal}")}
    )
    _publish(host, MILESTONE, slot, changed)
    _publish(host, MILESTONE, other, pinned[other])
    result, calls = _recover(monkeypatch)
    _assert_only_rerun(result, calls, MILESTONE, slot, other)


@pytest.mark.parametrize(
    "binding",
    [
        {"reviewer_slot": SECONDARY},
        {"assignment_id": f"{V7_FEATURE}:{MILESTONE}:m2:p1"},
    ],
    ids=["other-slot", "other-assignment"],
)
def test_ac22_survivor_recovery_still_refuses_any_other_binding_mismatch(
    binding: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Survivor control (AC-22 recovery rule): only an input-hash or
    effective-digest difference makes an unrecorded publication stale. A
    publication bound to another reviewer slot or assignment still refuses the
    batch before any provider call, today and after the fix. The publication
    carries the recorded AC-23 identities of the uninjected input, which equal
    its preparation before and after the fix, so only the binding differs.
    """
    host = _host(tmp_path, monkeypatch, MILESTONE)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        PINS["codex-milestone-review"].identity,
        **binding,
    )
    before = state_path.read_bytes()
    result, calls = _recover(monkeypatch)
    assert not result.ok, (
        f"FAIL AC-22: a publication with a different {sorted(binding)} binding "
        "was reused or treated as stale instead of refusing the batch"
    )
    assert result.error.code == "workspace-invalid", result.to_envelope()
    # The refusal names the binding, not an unrelated preparation failure.
    assert "binding" in result.error.message, result.error.message
    assert calls == [], "FAIL AC-22: the refused batch called a provider"
    assert not gate_runs(state_path) and state_path.read_bytes() == before
