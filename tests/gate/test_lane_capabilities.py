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

When the baselines were recorded, `_stable_text` stripped `<checkout>/` from
every runtime section, embedded bodies included, so each recorded AC-22
baseline is independent of the checkout path: each was observed equal under two
differently named temporary roots. The fix replaced it with normalization of
Heddle-generated path labels only.

AC-23 survivor pins: each Codex preparation decision (navigation, local
instructions for the four gates, milestone insertion and construction) and
Claude runs reproduce their recorded preparation byte for byte through the
refactor and the fix. Each pin holds the effective digest, review basis, input
hash and the exact replay text: the SHA-256 of the provider stdin, the exact
system text, and the ordered runtime sections by first line and SHA-256.

AC-22 discriminators, red until the fix:

- Codex: the milestone diff and test-file bodies arrive byte-exact, labels stay
  checkout-relative, and the effective digest and input hash differ from the
  recorded baseline while the review basis equals it;
- Claude: the same, for the body Claude embeds. Claude embeds no diff and no
  test-file body, so its affected input is a review-test-scaffolding run whose
  verification-command facts carry the checkout path;
- recovery after landing, for each lane (`AFFECTED`): the affected member's
  output, published under its recorded pre-change identity, is stale and its
  member makes exactly one provider call, while the other member's output is
  reused;
- the recovery rule, one signal at a time: a publication whose input hash
  alone, or effective prompt digest alone, differs reruns once beside a reused
  member.

AC-22 survivors, which held before and after the fix: the affected input's
review basis under any checkout path; the Claude milestone review, which embeds
no diff and keeps its identities; a review recorded before the identity change,
which status and orient read as current (freshness observed at the public
boundary, see that test); and the recovery control below. The Antigravity half
belongs to m4.

Fenced and CRLF sections (AC-22, both embedding lanes, shared helpers
`write_fenced_crlf_sections` and `assert_sections_arrive_whole`): real plan and
spec files whose milestone section and selected AC block hold a fenced example
and a CRLF span arrive whole as their source bytes. These fixtures put the
checkout path in the plan and spec, so they record no identity.

Recovery rule (owner ruling, 2026-11-24): an unrecorded publication whose
input hash or effective prompt digest is its only difference from what its own
launch (feature, gate, scope, assignment, round and reviewer slot) stands for
is stale. It is not reused, its member makes exactly one provider call, and no
member of the batch is refused. A launch that recorded a usable outcome (a
canonical review or a usable capture) stands for that outcome alone, with the
binding read from the outcome's own artifact, so the stale publication a rerun
leaves behind stays stale for every later launch, whatever has changed since
in its old scope's sources or in the current output contract. Which launches
recorded an outcome is read from the ledger alone, and only the judged
launch's artifacts are opened: a missing one refuses, while another slot's
missing artifact leaves the recovery as it was. Only a launch
with no recorded usable outcome stands for the current preparation, as for a
run in flight across the change. Any other binding mismatch still refuses
before a provider call (survivor control), including a changed identity beside
another difference (output contract, prompt version, execution), a publication
whose launch recorded only failures and a duplicate of a recorded outcome. The
rule is one predicate, `_stale_publication` (heddle/runtime/gate_run.py),
shared by `_unrecorded_completion`, which sets stale publications aside before
the binding check (`validate_review_replay`, heddle/gate/entry.py), and by
standalone `run-gate`'s unrecorded-capture path, which prepares a capture's
launch under its own invocation only when no recorded outcome decides it.
Canonical outputs and usable captures are both covered.

Label normalization also covers the preflight anomaly that names the plan, and
a checkout root that the runtime manifest's JSON escapes.

Test seams: `heddle.gate.diff.resolve_diff` (`_inject_checkout_bodies`), the
gate-engine provider doubles at `heddle.gate.entry.run_gate_for_runtime`
(`_recover`, `_review_engine`, `_counting_capture_engine`), and
`heddle.gate.entry.prepare_gate_run` (`_recorded_before_the_change`), which
stands in a pre-change identity for a review recorded before landing.
`_capture_outcome` retains a capture through the runner's own capture builder
and publisher, with no provider process.
"""

from __future__ import annotations

import hashlib
import json
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
    typed_failure,
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
    return prepare_reviewer(host, role, SLOT_REVIEWERS[role][slot])


def prepare_reviewer(host: Path, role: str, reviewer: dict[str, str]):
    """Prepare `reviewer`'s slot through the runtime's gate context and preparation.

    The plan and spec are read from the host's files, as a real run reads them.
    """
    from heddle.gate import entry
    from heddle.gate.registry import GATES
    from heddle.kernel.model import resolve_snapshot
    from heddle.kernel.project_config import load_project_config

    config = load_project_config(host)
    snapshot = resolve_snapshot(config, V7_FEATURE)
    gate = GATES[role]
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


def _capture_outcome(gate_type, context, feature: str):
    """Retain a usable capture for the prepared run and publish it.

    The capture is the one the runner retains for a complete response it could
    not accept (`heddle.gate.retained.capture_review`), published by the
    runner's own create-only publisher; only the provider process is absent.
    """
    import tempfile
    from types import SimpleNamespace

    from heddle.contracts.review_assignments import ArtifactRef
    from heddle.gate.cli import GateArgs
    from heddle.gate.io import gate_artifact_location, publish_review_artifact
    from heddle.gate.retained import capture_review, capture_view, serialize_capture
    from heddle.gate.types import GateUsableReviewCapture
    from heddle.io.process import Termination

    prepared = context.prepared_run
    assert prepared is not None
    with tempfile.TemporaryDirectory() as temp:
        response = Path(temp) / "response.json"
        response.write_text('{"review": "complete, not in the native shape"}\n')
        completed = SimpleNamespace(
            monitor=SimpleNamespace(termination=Termination.COMPLETED),
            cli_exit=0,
            failure_reason=None,
            raw_out_path=response,
            msg_out_path=response,
            response_channel=None,
        )
        capture = capture_review(
            prepared, completed, "Fixture response needs lead interpretation."
        )
    raw = serialize_capture(capture)
    workspace = context.workspace_dir or context.plan_path.parent
    args = GateArgs(
        gate_name=gate_type.name,
        cli=prepared.invocation.exec_config.cli,
        feature=feature,
    )
    directory, name = gate_artifact_location(context, args)
    directory.mkdir(parents=True, exist_ok=True)
    output, _view, _warnings = publish_review_artifact(
        directory / f"{name}.md",
        raw,
        capture_view(capture),
        workspace=workspace,
        kind="capture",
    )
    return GateUsableReviewCapture(
        capture=capture,
        artifact=ArtifactRef(
            output.relative_to(workspace).as_posix(),
            hashlib.sha256(raw).hexdigest(),
            "capture",
        ),
        structure_warnings=capture.warnings,
    )


def _publish(
    host: Path,
    role: str,
    slot: str,
    identity: Identity | None,
    *,
    outcome=canonical_outcome,
    **binding: object,
) -> None:
    """Publish a worker's output under a recorded identity.

    The output is canonical, or a usable capture with
    `outcome=_capture_outcome`. Assignment, round, slot, prompt version,
    execution and output contract are those of the current preparation unless
    `binding` overrides one; the three identities are the given literals, as an
    in-flight run at landing would carry them, or the current preparation's
    when `identity` is None.
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
    outcome(context.gate_type, context, V7_FEATURE)


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
    """Survivor pin (AC-23): the prepared identities and replay text equal the
    preparation recorded before the capability-table refactor and the
    checkout-path fix, byte for byte."""
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
    equals the recorded baseline wherever the checkout lives, before and after
    the fix, because embedded bodies are not part of the basis."""
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
    """Discriminator (AC-22, Codex): the embedded milestone diff and test-file
    body arrive byte-exact with the checkout path, no Heddle-generated label
    carries it, and the effective digest and input hash differ from the recorded
    baseline while the review basis equals it. Before the fix `_stable_text`
    rewrote the bodies, so the identities equalled the baseline."""
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
    """Discriminator (AC-22, Claude half): the Claude affected input is a
    review-test-scaffolding run whose embedded verification-command facts carry
    the checkout path. The facts arrive byte-exact, no Heddle-generated label
    carries the path, and the effective digest and input hash differ from the
    recorded baseline while the review basis equals it. Before the fix
    `_stable_text` rewrote the commands, so the identities equalled the baseline.

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
    orient, before and after the fix.

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
    """Discriminator (AC-22 recovery witness): outputs published under the
    recorded pre-change identities (an in-flight run at landing) are
    recovered. The affected member's identity has changed, so recovery treats
    its publication as stale: its output is not reused and it makes exactly
    one provider call. The other member is reused with none. Before the fix the
    identity did not change, so both were reused and the double saw no call.

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
    """Discriminator (AC-22 recovery rule): either identity signal alone
    makes a publication stale. One member is published under its recorded
    AC-23 identity with exactly one of the input hash and the effective prompt
    digest replaced, the other under its recorded AC-23 identity unchanged. The
    changed member reruns once and the other is reused; a rule that needs both
    signals to differ refuses the batch and fails here, as recovery that
    refused any binding difference did before the fix.
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


def test_ac22_recovery_reuses_a_current_publication_beside_a_stale_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule, a stale rerun interrupted after it published: the member
    has a stale publication and a current one. The stale one is set aside
    before the one-output-per-member check, so the current one is reused with
    no provider call and nothing is refused. Without that, two unrecorded
    outputs would refuse the batch for reconciliation.
    """
    host = _host(tmp_path, monkeypatch, MILESTONE)
    pinned = PINS["codex-milestone-review"].identity
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        replace(pinned, input=_sha256("published before landing: input")),
    )
    _publish(host, MILESTONE, PRIMARY, pinned)
    _publish(host, MILESTONE, SECONDARY, PINS["claude-milestone-review"].identity)
    result, calls = _recover(monkeypatch)
    assert result.ok, result.to_envelope()
    assert calls == [], "FAIL AC-22: a current publication was not reused"
    members = {member_key(row): row for row in result.data["members"]}
    assert [row["reuse"] for row in members.values()] == ["recovered"] * 2
    assert [row["publication"] for row in members.values()] == ["recorded"] * 2


def _counting_review_engine(
    monkeypatch: pytest.MonkeyPatch, manager, slot: str, finding_id: str
):
    """Fork-safe provider double that counts calls across worker processes;
    `slot` raises `finding_id` in round 1, as `_review_engine` does."""
    from tests.structured_review_helpers import finding

    calls = manager.list()

    def run(gate_type, context, *, feature, **_kwargs):
        prepared = context.prepared_run
        assert prepared is not None
        calls.append((gate_type.name, prepared.reviewer_slot))
        raised = prepared.reviewer_slot == slot and prepared.round_number == 1
        findings = (finding(finding_id, classification="implement"),) if raised else ()
        return canonical_outcome(gate_type, context, feature, findings=findings)

    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
    return calls


def _recover_and_open_the_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict[str, str]]:
    """The Codex AC-22 recovery, then the assignment's verification round.

    Recovery reruns the stale Codex member, whose round-one report raises a
    finding, and reuses the Claude member; the stale Codex publication stays
    behind, unrecorded. The lead retains the finding and opens round two, which
    the Codex slot alone serves. Returns the host, its state path and the
    round-one run of each slot.
    """
    case = AFFECTED["codex-milestone-diff"]
    host = _host(tmp_path, monkeypatch, case.role)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    _affected_input(host, monkeypatch, case)
    _publish(host, case.role, case.slot, case.baseline)
    _publish(host, case.role, SECONDARY, case.reusable)
    with multiprocessing.Manager() as manager:
        calls = _counting_review_engine(
            monkeypatch, manager, case.slot, case.finding_id
        )
        recovered = _ok(execute_batch())
        assert list(calls) == [(case.role, case.slot)]
    runs = {row["reviewer_slot"]: row["run_id"] for row in recovered.data["members"]}
    _ok(
        dispose(
            state_path,
            [
                disposition(runs[case.slot], case.finding_id, status="retained"),
                *(
                    disposition(run_id, "@coverage", status="settled")
                    for run_id in runs.values()
                ),
            ],
        )
    )
    _ok(
        _open_verification_round(
            state_path, case.role, _gate_scope(state_path, case.role)
        )
    )
    return host, state_path, runs


def _run_counted(monkeypatch: pytest.MonkeyPatch, command):
    """Run one standalone `command`, counting provider calls."""
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        result = command()
        observed = list(calls)
    return result, observed


def _run_the_next_round(monkeypatch: pytest.MonkeyPatch):
    """Run round two's Codex slot, counting provider calls."""
    return _run_counted(monkeypatch, lambda: run_slot(MILESTONE, PRIMARY))


def _counting_capture_engine(monkeypatch: pytest.MonkeyPatch, manager, slot: str):
    """Fork-safe provider double that counts calls; `slot` returns a usable
    capture and every other slot a canonical review without findings."""
    calls = manager.list()

    def run(gate_type, context, *, feature, **_kwargs):
        prepared = context.prepared_run
        assert prepared is not None
        calls.append((gate_type.name, prepared.reviewer_slot))
        if prepared.reviewer_slot == slot:
            return _capture_outcome(gate_type, context, feature)
        return canonical_outcome(gate_type, context, feature)

    monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", run)
    return calls


def _attempt_outcomes(state_path: Path) -> list[tuple[int, str, str]]:
    attempts = yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"]
    return [
        (row["round_number"], row["reviewer_slot"], row["outcome"]["kind"])
        for row in attempts
    ]


def _manifest(prepared) -> str:
    return next(
        section
        for section in prepared.runtime_sections
        if section.startswith(RUNTIME_MANIFEST)
    )


def test_ac22_recovery_lets_the_next_round_run_past_the_stale_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule, judged against a publication's own launch: the stale
    publication a rerun leaves behind is bound to a launch that has since
    recorded a usable outcome with another identity, so it stays stale. The
    assignment's next round neither reuses nor refuses it and makes its one
    call. Judged against the launch being prepared instead, that publication
    would refuse every later launch of the gate on its CLI as another binding.
    """
    host, _state_path, _runs = _recover_and_open_the_next_round(tmp_path, monkeypatch)
    reviews = host / "plans" / V7_FEATURE / "reviews"
    left = sorted(reviews.glob(f"{MILESTONE}-m1.codex.*.review.json"))
    assert len(left) == 2, "fixture lost the stale publication beside the rerun"
    result, calls = _run_the_next_round(monkeypatch)
    assert result.ok, (
        "FAIL AC-22: the stale publication left by the rerun refused the next "
        f"round: {result.to_envelope().get('error')}"
    )
    assert calls == [(MILESTONE, PRIMARY)], calls
    assert all(path.exists() for path in left), "a publication was removed"


def test_ac22_recovery_still_refuses_a_publication_whose_launch_only_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule control: only a recorded usable outcome (a canonical review
    or a usable capture) makes another launch's publication stale. Here the
    Claude secondary's round-one attempt fails with an engine failure, then a
    Codex publication bound to that secondary launch appears. Its launch has
    recorded no output, so the publication may still be recoverable there: the
    Codex primary's launch refuses it as another binding, before any call,
    instead of skipping it.
    """
    host = _host(tmp_path, monkeypatch, MILESTONE)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    monkeypatch.setattr(
        "heddle.gate.entry.run_gate_for_runtime",
        lambda gate_type, context, **_kwargs: typed_failure(
            context, (gate_type.name, context.prepared_run.reviewer_slot)
        ),
    )
    run_slot(MILESTONE, SECONDARY)
    attempts = yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"]
    assert [(row["reviewer_slot"], row["outcome"]["kind"]) for row in attempts] == [
        (SECONDARY, "engine-failure")
    ], attempts
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        PINS["codex-milestone-review"].identity,
        reviewer_slot=SECONDARY,
    )
    before = state_path.read_bytes()
    with multiprocessing.Manager() as manager:
        calls = install_counting_engine(monkeypatch, manager)
        result = run_slot(MILESTONE, PRIMARY)
        observed = list(calls)
    assert not result.ok, (
        "FAIL AC-22: a publication whose launch recorded only an engine failure "
        "was skipped instead of refusing"
    )
    assert result.error.code == "workspace-invalid", result.to_envelope()
    assert "binding" in result.error.message, result.error.message
    assert observed == [], "FAIL AC-22: the refused launch called a provider"
    assert state_path.read_bytes() == before


def test_ac22_recovery_still_refuses_a_duplicate_of_a_recorded_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule control: a publication carrying its recorded launch's own
    identity is a duplicate of that recorded outcome, not a stale one, so the
    next round refuses it as another binding before any call. The duplicate is
    bound to round one's Codex launch with the recorded rerun's identities and
    different content; the stale publication beside it is still set aside.
    """
    host, state_path, runs = _recover_and_open_the_next_round(tmp_path, monkeypatch)
    recorded = next(
        row for row in gate_runs(state_path) if row["run_id"] == runs[PRIMARY]
    )
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        Identity(
            recorded["effective_prompt_sha256"],
            recorded["review_basis_hash"],
            recorded["input_hash"],
        ),
        round_number=recorded["round_number"],
    )
    before = state_path.read_bytes()
    result, calls = _run_the_next_round(monkeypatch)
    assert not result.ok, (
        "FAIL AC-22: a duplicate of a recorded outcome was skipped instead of refusing"
    )
    assert result.error.code == "workspace-invalid", result.to_envelope()
    assert "binding" in result.error.message, result.error.message
    assert calls == [], "FAIL AC-22: the refused launch called a provider"
    assert state_path.read_bytes() == before


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
    batch before any provider call, before and after the fix. The publication
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


# ---------------------------------------------------------------------------
# AC-22 recovery rule: identity-only staleness, captures and standalone runs
# ---------------------------------------------------------------------------

_CHANGED_INPUT = _sha256("published before landing: input")


@pytest.mark.parametrize("mismatch", ["output-contract", "prompt-version", "execution"])
def test_ac22_recovery_refuses_a_changed_identity_beside_another_binding_mismatch(
    mismatch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule control: a publication is stale only when its input hash
    or effective prompt digest is its only difference from its launch's
    preparation. One whose input hash changed and whose output contract,
    prompt version or execution also differs refuses the batch before any
    provider call, with the ledger untouched. Read as stale, its member would
    rerun and the other difference would never be seen.
    """
    from heddle.gate.results import review_output_contract

    host = _host(tmp_path, monkeypatch, MILESTONE)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    _context, prepared = _prepare(host, MILESTONE, PRIMARY)
    execution = prepared.invocation.exec_config
    other_model = SLOT_REVIEWERS["spec-review"][PRIMARY]["model"]
    assert other_model != execution.model
    binding = {
        "output-contract": {
            "output_contract": replace(
                review_output_contract(MILESTONE),
                schema_sha256=_sha256("another output contract"),
            )
        },
        "prompt-version": {"prompt_version": "fixture-prompt/2"},
        "execution": {
            "invocation": replace(
                prepared.invocation,
                exec_config=replace(execution, model=other_model),
            )
        },
    }[mismatch]
    pinned = PINS["codex-milestone-review"].identity
    _publish(host, MILESTONE, PRIMARY, replace(pinned, input=_CHANGED_INPUT), **binding)
    before = state_path.read_bytes()
    result, calls = _recover(monkeypatch)
    assert not result.ok, (
        f"FAIL AC-22: a changed identity masked the {mismatch} mismatch; the "
        "publication was treated as stale instead of refusing the batch"
    )
    assert result.error.code == "workspace-invalid", result.to_envelope()
    assert "binding" in result.error.message, result.error.message
    assert calls == [], "FAIL AC-22: the refused batch called a provider"
    assert not gate_runs(state_path) and state_path.read_bytes() == before


def test_ac22_recovery_refuses_a_leftover_differing_from_its_outcome_beyond_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule control, judged against a publication's own launch: a
    leftover is superseded only when its input identity is its only difference
    from a recorded outcome of its launch. One bound to round one's Codex
    launch with another identity and another prompt version refuses the next
    round before any call; the stale leftover beside it is still set aside.
    """
    host, state_path, runs = _recover_and_open_the_next_round(tmp_path, monkeypatch)
    recorded = next(
        row for row in gate_runs(state_path) if row["run_id"] == runs[PRIMARY]
    )
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        Identity(
            _sha256("another leftover: effective"),
            recorded["review_basis_hash"],
            _sha256("another leftover: input"),
        ),
        round_number=recorded["round_number"],
        prompt_version="fixture-prompt/2",
    )
    before = state_path.read_bytes()
    result, calls = _run_the_next_round(monkeypatch)
    assert not result.ok, (
        "FAIL AC-22: a leftover differing from its recorded outcome in more than "
        "its identity was skipped as stale instead of refusing"
    )
    assert result.error.code == "workspace-invalid", result.to_envelope()
    assert "binding" in result.error.message, result.error.message
    assert calls == [], "FAIL AC-22: the refused launch called a provider"
    assert state_path.read_bytes() == before


def test_ac22_recovery_reruns_a_member_whose_capture_carries_a_changed_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule, capture-shaped: a usable capture published under a
    changed input hash for the launch being prepared is stale like a canonical
    output. Its member makes exactly one provider call and the other member's
    canonical output is reused; read as another binding, the capture would
    refuse the batch.
    """
    host = _host(tmp_path, monkeypatch, MILESTONE)
    pinned = PINS["codex-milestone-review"].identity
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        replace(pinned, input=_CHANGED_INPUT),
        outcome=_capture_outcome,
    )
    _publish(host, MILESTONE, SECONDARY, PINS["claude-milestone-review"].identity)
    reviews = host / "plans" / V7_FEATURE / "reviews"
    captures = sorted(reviews.glob("*.capture.json"))
    assert len(captures) == 1, "fixture lost its capture"
    result, calls = _recover(monkeypatch)
    _assert_only_rerun(result, calls, MILESTONE, PRIMARY, SECONDARY)
    assert all(path.exists() for path in captures), "a publication was removed"


def test_ac22_recovery_lets_the_next_round_run_past_a_capture_recorded_launch_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule, judged against a publication's own launch: a usable
    capture recorded for a slot-round supersedes that launch's stale leftover
    as a canonical review does. Round one's Codex slot leaves a stale canonical
    output behind, runs once and records a usable capture, which the lead
    interprets; the next round neither reuses nor refuses the leftover and
    makes its one call.
    """
    from heddle.contracts import operations as ops
    from heddle.gate.retained import decode_capture
    from heddle.runtime.application import execute
    from tests.runtime.test_review_retention import interpretation
    from tests.structured_review_helpers import (
        complete_fixture_coverage,
        complete_fixture_rerun,
        complete_fixture_verdict,
        finding,
    )
    from tests.tiering_review_helpers import review_content

    finding_id = AFFECTED["codex-milestone-diff"].finding_id
    host = _host(tmp_path, monkeypatch, MILESTONE)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    pinned = PINS["codex-milestone-review"].identity
    _publish(host, MILESTONE, PRIMARY, replace(pinned, input=_CHANGED_INPUT))
    left = sorted((host / "plans" / V7_FEATURE / "reviews").glob("*.review.json"))
    assert len(left) == 1, "fixture lost its leftover"
    with multiprocessing.Manager() as manager:
        calls = _counting_capture_engine(monkeypatch, manager, PRIMARY)
        retained = _ok(run_slot(MILESTONE, PRIMARY))
        claude = _ok(run_slot(MILESTONE, SECONDARY))
        assert list(calls) == [(MILESTONE, PRIMARY), (MILESTONE, SECONDARY)]
    assert retained.data["status"] == "retained", retained.data
    assert _attempt_outcomes(state_path) == [
        (1, PRIMARY, "usable-capture"),
        (1, SECONDARY, "canonical-review"),
    ]
    capture = decode_capture((host / retained.data["artifact"]).read_bytes())
    content = review_content(
        MILESTONE, findings=[finding(finding_id, classification="implement")]
    )
    validation = capture.validation
    complete_fixture_coverage(content, validation.ac_ids, validation.active_rules)
    complete_fixture_rerun(
        content, validation.prior_reviews, validation.review_decisions
    )
    complete_fixture_verdict(content, bool(validation.prior_reviews))
    _ok(execute(ops.InterpretReview(payload=interpretation(retained.data, content))))
    codex_run, claude_run = retained.data["run_id"], claude.data["run_id"]
    _ok(
        dispose(
            state_path,
            [
                disposition(codex_run, finding_id, status="retained"),
                disposition(codex_run, "@coverage", status="settled"),
                disposition(claude_run, "@coverage", status="settled"),
            ],
        )
    )
    _ok(
        _open_verification_round(
            state_path, MILESTONE, _gate_scope(state_path, MILESTONE)
        )
    )
    result, calls = _run_the_next_round(monkeypatch)
    assert result.ok, (
        "FAIL AC-22: the stale leftover of a launch that recorded a usable "
        f"capture refused the next round: {result.to_envelope().get('error')}"
    )
    assert calls == [(MILESTONE, PRIMARY)], calls
    assert all(path.exists() for path in left), "a publication was removed"


def test_ac22_standalone_recovery_reruns_once_past_a_stale_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule, standalone: `run-gate` meets an unrecorded capture before
    preparing its own member and records it when it is current. A capture whose
    input hash changed for its own launch is stale under the same rule as at
    recovery, so it is not recorded there: the slot makes exactly one provider
    call and records that review, and the capture stays on disk unrecorded.
    """
    host = _host(tmp_path, monkeypatch, MILESTONE)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    pinned = PINS["codex-milestone-review"].identity
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        replace(pinned, input=_CHANGED_INPUT),
        outcome=_capture_outcome,
    )
    captures = sorted((host / "plans" / V7_FEATURE / "reviews").glob("*.capture.json"))
    assert len(captures) == 1, "fixture lost its capture"
    result, calls = _run_counted(monkeypatch, lambda: run_slot(MILESTONE, PRIMARY))
    assert result.ok, result.to_envelope()
    assert calls == [(MILESTONE, PRIMARY)], (
        "FAIL AC-22: the stale capture was recorded instead of rerunning; "
        f"calls={calls}"
    )
    assert _attempt_outcomes(state_path) == [(1, PRIMARY, "canonical-review")]
    assert all(path.exists() for path in captures), "a publication was removed"


@pytest.mark.parametrize("command", ["slot-command", "bare-command"])
def test_ac22_standalone_recovery_records_a_current_capture_without_a_call(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule, standalone control: a current unrecorded capture is still
    recorded with no provider call. It is judged under its own invocation, not
    the command's flags: the bare `run-gate` resolves the Codex primary, yet
    the Claude secondary's capture is recovered, as before the rule existed.
    """
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute

    host = _host(tmp_path, monkeypatch, MILESTONE)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    _publish(host, MILESTONE, SECONDARY, None, outcome=_capture_outcome)
    commands = {
        "slot-command": lambda: run_slot(MILESTONE, SECONDARY),
        "bare-command": lambda: execute(ops.RunGate(MILESTONE, feature=V7_FEATURE)),
    }
    result, calls = _run_counted(monkeypatch, commands[command])
    assert result.ok, result.to_envelope()
    assert calls == [], (
        f"FAIL AC-22: a current capture was not recovered; calls={calls}"
    )
    assert result.data["status"] == "retained" and result.data["cached"], result.data
    assert result.data["invocation"]["reviewer_slot"] == SECONDARY
    assert _attempt_outcomes(state_path) == [(1, SECONDARY, "usable-capture")]


def test_ac22_failed_preflight_manifest_is_the_same_under_any_checkout_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Label normalization covers preflight: a milestone review whose plan has
    no section for its milestone fails preflight, and the anomaly in its
    runtime manifest names the plan by its checkout-relative label. The
    manifest is byte-identical under two checkout roots and names neither.
    """
    manifests = {}
    for checkout in ("checkout", "a-much-longer-checkout-name"):
        host = _host(tmp_path, monkeypatch, MILESTONE, checkout=checkout)
        plan = host / "plans" / V7_FEATURE / "plan.md"
        text = plan.read_text(encoding="utf-8")
        assert "### Milestone m1" in text, "fixture plan lost its milestone section"
        plan.write_text(text.replace("### Milestone m1", "### Stage m1"), "utf-8")
        _context, prepared = _prepare(host, MILESTONE, PRIMARY)
        assert prepared.preflight.status == "failed", prepared.preflight
        manifest = _manifest(prepared)
        assert host.as_posix() not in manifest, (
            "FAIL AC-22: the failed-preflight manifest carries the checkout path"
        )
        manifests[checkout] = manifest
    first, second = manifests.values()
    assert f"Missing Milestone m1 section in plans/{V7_FEATURE}/plan.md." in first
    assert first == second, (
        "FAIL AC-22: the failed-preflight manifest differs by checkout path"
    )


def test_ac22_non_ascii_checkout_root_keeps_labels_relative_and_identities_equal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Label normalization covers a checkout root the runtime manifest's JSON
    escapes. Under two non-ASCII roots the prepared input carries
    checkout-relative labels and neither form of its root, and the prepared
    identities equal each other and the recorded pin. Normalizing the
    serialized text instead would miss the escaped root.
    """
    identities = []
    for checkout in ("checkout-é", "チェックアウト"):
        host = _host(tmp_path, monkeypatch, MILESTONE, checkout=checkout)
        root = host.as_posix()
        escaped = json.dumps(root)[1:-1]
        assert escaped != root, "fixture root is not escaped by JSON"
        _context, prepared = _prepare(host, MILESTONE, PRIMARY)
        text = (prepared.transport.system or "") + prepared.transport.stdin
        assert root not in text and escaped not in text, (
            "FAIL AC-22: the prepared input carries the non-ASCII checkout path"
        )
        body = _manifest(prepared).split("=====\n", 1)[1].rsplit("\n=====", 1)[0]
        labels = json.loads(body)
        assert (labels["plan"], labels["spec"]) == (
            f"plans/{V7_FEATURE}/plan.md",
            f"docs/features/runtime/{V7_FEATURE}.md",
        ), labels
        identities.append(_observe(prepared).identity)
    assert identities[0] == identities[1] == PINS["codex-milestone-review"].identity, (
        "FAIL AC-22: identities differ under non-ASCII checkout roots"
    )


def test_ac22_standalone_later_milestone_runs_past_a_superseded_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule, judged against a publication's own launch, before any
    preparation: a capture whose launch recorded a usable outcome is judged
    against that outcome alone. On a two-milestone host, m1's Codex capture is
    stale, recovery reruns its slot and records the replacement, and m2 becomes
    current. After an edit to a file only m1 owns, a standalone run of m2's
    Codex slot passes the superseded capture and makes its one call. Prepared
    again under m1's scope instead, the capture would read as that launch's
    current output and refuse on m1's changed source.
    """
    from heddle.contracts import operations as ops
    from heddle.runtime.application import execute
    from tests.concurrent_review_helpers import dual
    from tests.readiness_helpers import verify
    from tests.tiering_completion_helpers import final_host

    host = final_host(
        tmp_path,
        monkeypatch,
        stage="implement",
        verify_now=False,
        overrides={MILESTONE: dual(MILESTONE, window=1)},
    )
    verify(host.state, "m1")
    _context, prepared = _prepare(host.root, MILESTONE, PRIMARY)
    assert prepared.scope == "m1"
    _publish(
        host.root,
        MILESTONE,
        PRIMARY,
        replace(_observe(prepared).identity, input=_CHANGED_INPUT),
        outcome=_capture_outcome,
    )
    captures = sorted(host.state.parent.glob("reviews/*.capture.json"))
    assert len(captures) == 1, "fixture lost its capture"
    recovered, calls = _recover(monkeypatch)
    assert recovered.ok, recovered.to_envelope()
    assert sorted(calls) == [(MILESTONE, PRIMARY), (MILESTONE, SECONDARY)]
    assert _attempt_outcomes(host.state) == [
        (1, PRIMARY, "canonical-review"),
        (1, SECONDARY, "canonical-review"),
    ]
    _ok(
        dispose(
            host.state,
            [
                disposition(row["run_id"], "@coverage", status="settled")
                for row in gate_runs(host.state)
            ],
        )
    )
    _ok(execute(ops.MilestoneAdvance(feature=V7_FEATURE)))
    _ok(execute(ops.MilestoneAdvance(feature=V7_FEATURE)))
    milestones = yaml.safe_load(host.state.read_text())["milestones"]
    assert [(row["id"], row["status"]) for row in milestones] == [
        ("m1", "done"),
        ("m2", "current"),
    ]
    source = host.root / "src/example.py"
    source.write_text(source.read_text() + "\n# An edit after m1's review.\n")
    verify(host.state, "m2")
    result, calls = _run_counted(monkeypatch, lambda: run_slot(MILESTONE, PRIMARY))
    assert result.ok, (
        "FAIL AC-22: a capture superseded by its launch's recorded outcome "
        f"refused the later milestone: {result.to_envelope().get('error')}"
    )
    assert calls == [(MILESTONE, PRIMARY)], calls
    gates = yaml.safe_load(host.state.read_text())["gates"]
    assert [(gate["scope"], len(gate["runs"])) for gate in gates] == [
        ("m1", 2),
        ("m2", 1),
    ], gates
    assert all(path.exists() for path in captures), "a publication was removed"


def test_ac22_next_round_runs_past_the_leftover_after_an_output_schema_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule, against the recorded outcome's own binding: the leftover
    and its launch's recorded replacement were both bound to the output
    contract of their time, read from the replacement's own artifact. When the
    current schema digest changes afterwards, the leftover still differs from
    its replacement only in identity, so the next round passes it and makes
    its one call under the new contract. Taking the recorded outcome's
    contract from the current preparation would refuse it as another binding.
    """
    from heddle.gate.registry import GATES
    from heddle.gate.results import canonical_json

    host, state_path, _runs = _recover_and_open_the_next_round(tmp_path, monkeypatch)
    reviews = host / "plans" / V7_FEATURE / "reviews"
    left = {
        path: path.read_bytes()
        for path in reviews.glob(f"{MILESTONE}-m1.codex.*.review.json")
    }
    assert len(left) == 2, "fixture lost the leftover or its replacement"
    gate = GATES[MILESTONE]
    schema = json.loads(gate.output_contract.schema_json)
    schema["description"] = "A same-version annotation of the output contract."
    raw = canonical_json(schema)
    contract = replace(
        gate.output_contract,
        schema_json=raw.decode("utf-8"),
        schema_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert contract.schema_sha256 != gate.output_contract.schema_sha256
    monkeypatch.setitem(GATES, MILESTONE, replace(gate, output_contract=contract))
    result, calls = _run_the_next_round(monkeypatch)
    assert result.ok, (
        "FAIL AC-22: a schema change made the superseded leftover refuse the "
        f"next round: {result.to_envelope().get('error')}"
    )
    assert calls == [(MILESTONE, PRIMARY)], calls
    latest = json.loads(
        (state_path.parent / gate_runs(state_path)[-1]["artifact"]).read_text()
    )
    assert latest["invocation"]["output_contract_sha256"] == contract.schema_sha256
    assert all(path.read_bytes() == raw for path, raw in left.items())


def test_ac22_standalone_recovery_refuses_a_capture_duplicating_a_recorded_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule control, standalone: a capture bound to a launch whose
    recorded outcome carries the same identity is a duplicate, not superseded,
    so standalone `run-gate` hands it to the binding check, as the batch does.
    Bound to round one's Codex launch while round two is current, it is not
    that launch's exact replay and refuses there, before any call and with the
    ledger untouched. Without the check it would reach the recorder, whose
    round guard refuses it only after the capture's source is revalidated.
    """
    host, state_path, runs = _recover_and_open_the_next_round(tmp_path, monkeypatch)
    recorded = next(
        row for row in gate_runs(state_path) if row["run_id"] == runs[PRIMARY]
    )
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        Identity(
            recorded["effective_prompt_sha256"],
            recorded["review_basis_hash"],
            recorded["input_hash"],
        ),
        outcome=_capture_outcome,
        round_number=recorded["round_number"],
    )
    before = state_path.read_bytes()
    result, calls = _run_the_next_round(monkeypatch)
    assert not result.ok, (
        "FAIL AC-22: a capture duplicating a recorded outcome was recorded "
        "instead of refusing"
    )
    assert result.error.code == "workspace-invalid", result.to_envelope()
    assert "binding" in result.error.message, result.error.message
    assert calls == [], "FAIL AC-22: the refused launch called a provider"
    assert state_path.read_bytes() == before


def _record_the_primary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A dual-slot host whose Codex primary is recorded in round one, which the
    Claude secondary still has to serve. Returns the host, its state path and
    the primary's recorded canonical artifact."""
    host = _host(tmp_path, monkeypatch, MILESTONE)
    state_path = host / "plans" / V7_FEATURE / "state.yaml"
    first, calls = _run_counted(monkeypatch, lambda: run_slot(MILESTONE, PRIMARY))
    _ok(first)
    assert calls == [(MILESTONE, PRIMARY)]
    assert _attempt_outcomes(state_path) == [(1, PRIMARY, "canonical-review")]
    (run,) = gate_runs(state_path)
    return host, state_path, state_path.parent / run["artifact"]


def test_ac22_standalone_recovery_records_a_capture_beside_a_missing_sibling_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule control, unaffected output stays reusable: only the
    recorded outcome of a publication's own launch is read. With the Codex
    primary's recorded canonical file removed, a current capture of the Claude
    secondary, whose launch recorded nothing, is still recovered by a
    standalone run without a call: the sibling's artifact cannot be its
    comparison target. Reading every recorded artifact before looking up the
    launch would refuse it on the sibling's read.
    """
    host, state_path, sibling = _record_the_primary(tmp_path, monkeypatch)
    _publish(host, MILESTONE, SECONDARY, None, outcome=_capture_outcome)
    sibling.unlink()
    result, calls = _run_counted(monkeypatch, lambda: run_slot(MILESTONE, SECONDARY))
    assert result.ok, (
        "FAIL AC-22: a missing sibling artifact blocked recovery of a current "
        f"capture: {result.to_envelope().get('error')}"
    )
    assert calls == [], "FAIL AC-22: the current capture was not recovered"
    assert _attempt_outcomes(state_path) == [
        (1, PRIMARY, "canonical-review"),
        (1, SECONDARY, "usable-capture"),
    ]


def test_ac22_standalone_recovery_refuses_when_its_launch_s_recorded_artifact_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rule control, failing loud: a capture whose own launch recorded
    an outcome is judged against that outcome's artifact. This capture differs
    from the recorded Codex primary only in identity, so it would be passed as
    superseded; with the recorded canonical file removed, the standalone run
    refuses instead, naming the recorded review it could not validate, before
    any call and with the ledger untouched.
    """
    host, state_path, recorded = _record_the_primary(tmp_path, monkeypatch)
    _context, prepared = _prepare(host, MILESTONE, PRIMARY)
    assert prepared.round_number == 1
    _publish(
        host,
        MILESTONE,
        PRIMARY,
        replace(_observe(prepared).identity, input=_CHANGED_INPUT),
        outcome=_capture_outcome,
    )
    recorded.unlink()
    before = state_path.read_bytes()
    result, calls = _run_counted(monkeypatch, lambda: run_slot(MILESTONE, PRIMARY))
    assert not result.ok, (
        "FAIL AC-22: a capture was judged without its launch's recorded outcome"
    )
    assert result.error.code == "workspace-invalid", result.to_envelope()
    assert "recorded review" in result.error.message, result.error.message
    assert "could not be validated" in result.error.message, result.error.message
    assert calls == [], "FAIL AC-22: the refused launch called a provider"
    assert state_path.read_bytes() == before


# ---------------------------------------------------------------------------
# AC-22: fenced and CRLF plan and spec sections arrive whole as source bytes
# ---------------------------------------------------------------------------

# A fenced example holding a `## ` heading and milestone and AC lookalikes,
# including lookalikes of the selected headings.
FENCED_EXAMPLE = (
    "```markdown\n"
    "## Example heading inside a fence\n"
    "### Milestone m9: Fenced lookalike\n"
    "### Milestone m1: Fenced lookalike of the selected heading\n"
    "### AC-9: Fenced lookalike\n"
    "### AC-1: Fenced lookalike of the selected heading\n"
    "```\n"
)
# A four-backtick fence around a three-backtick block: the inner run is
# shorter, so it neither closes the outer fence nor exposes its headings.
NESTED_FENCE = (
    "````markdown\n"
    "```python\n"
    "## Heading inside a nested block\n"
    "```\n"
    "### Milestone m9: Lookalike after the nested block\n"
    "````\n"
)
# Indented code holding a literal fence marker: four spaces of indent make it
# code, not a fence opening, so the heading after it still bounds the section.
INDENTED_BACKTICK_MARKER = "    ```\n    literal backtick marker in indented code"
INDENTED_TILDE_MARKER = "    ~~~\n    literal tilde marker in indented code"


@dataclass(frozen=True)
class FencedSections:
    """What a capture must embed whole, as the source bytes of the host files."""

    milestone: str
    acceptance: str
    spans: tuple[str, ...]


def write_fenced_crlf_sections(host: Path) -> FencedSections:
    """Rewrite the host plan and spec so m1 and AC-1 hold fences and CRLF spans.

    The m1 plan section and the AC-1 block each hold ``FENCED_EXAMPLE`` with
    text after it, and a CRLF span naming the checkout path; the m1 section
    also holds ``NESTED_FENCE`` before its span. A fenced lookalike of each
    selected heading sits before the real one, inside an earlier fence. The
    m1 section ends in ``INDENTED_BACKTICK_MARKER`` right before the m2
    heading, and ``INDENTED_TILDE_MARKER`` sits right before the AC-1 heading.
    Milestone and AC ids are the fixture's own.
    """
    root = host.as_posix()
    plan_span = f"Plan CRLF span: {root}/plan/input.json\r\nsecond plan line\r\n"
    spec_span = f"Spec CRLF span: {root}/spec/input.json\r\nsecond spec line\r\n"
    milestone = (
        "### Milestone m1: Declared value\n\nSatisfies: AC-1\n\n"
        "Implement the declared value.\n\n"
        f"{FENCED_EXAMPLE}\nPlan text after the fence.\n\n{NESTED_FENCE}\n"
        f"Plan text after the nested fence.\n{plan_span}End of the m1 section.\n\n"
        f"{INDENTED_BACKTICK_MARKER}"
    )
    block = (
        "### AC-1: Declared value\n\nPriority: MUST\n"
        "Action: Read VALUE.\nExpected: The value is 7.\n\n"
        f"{FENCED_EXAMPLE}\nSpec text after the fence.\n{spec_span}"
        "End of the AC-1 block."
    )
    plan = host / "plans" / V7_FEATURE / "plan.md"
    plan.write_bytes(
        (
            "# Plan\n\n~~~text\n### Milestone m1: Lookalike before the real one\n"
            f"~~~\n\n{milestone}\n\n### Milestone m2: Verify the value\n\n"
            "Satisfies: AC-1\n\nVerify the declared value.\n"
        ).encode()
    )
    spec = host / "docs/features/runtime" / f"{V7_FEATURE}.md"
    spec.write_bytes(
        (
            "# Operational example\n\n## Acceptance Criteria\n\n"
            "### AC-2: Another value\n\nExpected: another.\n\n"
            "```text\n### AC-1: Lookalike before the real one\n```\n\n"
            f"Text after the AC-2 fence.\n\n{INDENTED_TILDE_MARKER}\n\n{block}\n\n"
            "## Decision Log\n\nNo decisions yet.\n"
        ).encode()
    )
    return FencedSections(
        milestone=milestone,
        # The section heading, then the selected block (the extractor's shape).
        acceptance=f"## Acceptance Criteria\n{block}",
        spans=(plan_span, spec_span),
    )


def assert_sections_arrive_whole(
    prepared, host: Path, sections: FencedSections, label: str
) -> None:
    """The capture embeds each whole section byte for byte; labels stay relative.

    Each section must sit exactly between two of Heddle's delimiter lines, so
    a section cut short or started early at a lookalike fails. Booleans keep
    pytest from printing the whole prompt on failure.
    """
    stdin = prepared.transport.stdin
    root = host.as_posix()
    outside_diff = stdin.replace(prepared.diff_text, "")
    for name, text in (
        ("plan milestone section", sections.milestone),
        ("spec AC-1 block", sections.acceptance),
    ):
        whole = f"=====\n{text}\n=====" in outside_diff
        assert whole, (
            f"FAIL {label}: the {name} is not embedded whole as its source bytes; "
            f"text after the fence kept: {'text after the fence' in outside_diff}, "
            f"CRLF kept: {chr(13) in outside_diff}"
        )
    kept = [span in outside_diff for span in sections.spans]
    assert all(kept), f"FAIL {label}: CRLF spans kept byte-exact: {kept}"
    labels = outside_diff
    for text in (sections.milestone, sections.acceptance):
        labels = labels.replace(text, "")
    assert root not in labels, (
        f"FAIL {label}: a Heddle-generated label carries the checkout path"
    )
    assert f"plans/{V7_FEATURE}/plan.md" in labels, (
        f"FAIL {label}: the generated labels do not name the plan checkout-relative"
    )


def test_ac22_codex_fenced_crlf_plan_and_spec_sections_arrive_whole_and_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discriminator (AC-22, Codex): real plan and spec files whose m1 section
    and AC-1 block hold a fenced example (a `## ` heading and milestone and AC
    lookalikes, text after it) and a CRLF span naming the checkout path; the
    m1 section also holds a four-backtick fence around a three-backtick block
    with a `## ` line inside. Indented code holding a literal fence marker
    sits right before the m2 heading and right before the AC-1 heading. The
    Codex milestone review, reading them through the runtime context, embeds
    each whole section as its source bytes, and Heddle's labels stay
    checkout-relative. Red while a fenced line bounds a section, a shorter
    inner run closes a fence, an indented marker opens one or a span's CRLF
    is rewritten.
    """
    host = _host(tmp_path, monkeypatch, MILESTONE)
    sections = write_fenced_crlf_sections(host)
    _context, prepared = _prepare(host, MILESTONE, PRIMARY)
    assert prepared.invocation.exec_config.cli == "codex"
    assert_sections_arrive_whole(prepared, host, sections, "AC-22 codex")
