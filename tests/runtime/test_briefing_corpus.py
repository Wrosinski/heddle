"""
commands gate — briefing canonicalization + kickoff render proof (AC-6, AC-7).

Behavior contract: commands-native-switchover
Milestone: gate (Briefing canonicalization, R-P1-3 hybrid fold); this file is
the AC-7 kickoff render proof, pinned home per the plan, joined to gate's
`owns` at implement time (PL-C2 owns lifecycle).

At scaffold time (preimplementation state), every packaged briefing still carried the
"Minimal gate briefing … canonical … lands at authoring" stamp and none carried the
REQ-8 fold items, so AC-6/AC-7 failed until gate landed. The AC-7 tests invoke
`heddle kickoff` in-process against fixture workspaces copied to tmp_path —
never ambient cwd (REQ-31/review) — at all 8 stages x tiers 2 and 3; this is
the render-level proof for the four briefings commands's own run cannot
session-prove (Boundary conditions). The kickoff idempotence/pure-read/resource
precedence
contract stays pinned by tests/runtime/test_kickoff.py (unmodified).

Fold-item discriminators are per-item token co-occurrences from the legacy
sources (REQ-8), not single generic words: the implement session folds the
content and these pins hold; if a fold rewords a token away entirely, adjust
the pin with justification in the milestone prose — never by weakening it to
a tautology. (TQ-core/review-tests#1 strengthened the weak single-word anchors
to co-occurrences so a substance-thinned rewrite that keeps one keyword no
longer false-greens.)
"""

from __future__ import annotations

import functools
import re
import shutil
from pathlib import Path

import pytest

from heddle.contracts.schemas import STAGES
from heddle.gate.prompt import PACKAGED_PROMPTS_DIR
from tests.tiering_helpers import ROLES, entry
from tests.tiering_review_helpers import V7_FEATURE, current_host

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESOURCES = REPO_ROOT / "heddle" / "resources"
PROMPTS = PACKAGED_PROMPTS_DIR
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"
FIXTURE_SLUG = "nl-screening"  # golden tier-3 workspace; stage/tier patched

BRIEFING_FILES = tuple(f"{stage}.briefing.md" for stage in STAGES)
AUTHORITATIVE_FILES = BRIEFING_FILES

STAMP_MARKERS = ("Minimal M3 briefing", "lands at M6")

# REQ-8 fold items -> (target briefing, per-item required-token co-occurrences).
# Each item is a tuple of tokens that must ALL be present in the briefing — a
# co-occurrence unique to the folded discipline rather than a single generic
# word that could false-green on a substance-thinned rewrite (TQ-core /
# review-tests#1). Already-specific phrases stay 1-tuples; the weak single
# words ("triage"/"immersion"/"propagat") gain a co-occurring token that pins
# the folded substance, not just the keyword.
FOLD_ITEMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "implement.briefing.md": (
        ("code quality rules",),  # F-IM2 (the <code_quality_rules> restatement)
        ("specificity", "triage"),  # F-IM7 (AC-specificity triage)
        ("impact assessment",),  # F-IM8 (Impact Assessment / Re-Plan protocol)
        ("codebase", "immersion"),  # X-1 (codebase immersion; folded once)
    ),
    "scaffold.briefing.md": (
        ("red phase validation",),  # F-SC8 (red-phase validation ...)
        ("passing test", "triage"),  # F-SC8 (... + passing-test triage)
    ),
    # write replaces merged synthesis with exact original-source accounting.
    "peer-review.briefing.md": (("every original finding", "evidence bound"),),
    "complete.briefing.md": (
        ("self contain", "spec"),  # F-CO9 (spec self-containment verification)
        ("propagat", "sweep"),  # F-CO2 (propagation sweep / [PROPAGATE] tags)
        ("memory candidate",),  # X-2 (memory-candidates reminder)
    ),
}

# AC-6: zero references to deletion targets, template variables, paste
# gates, phase_authorized_through hand-edits, or the events ledger.
FORBIDDEN_IN_BRIEFINGS = (
    "run-gate.sh",
    "run_gate.py",
    "run-kickoff.sh",
    "python -m heddle.gate",
    "gpt-5.4",
    "phase12-kickoff",
    "phase5-kickoff",
    "review-kickoff",
    "phase7-kickoff",
    "phase9-kickoff",
    "implementation-agent.md",
    "templates/feature-spec.md",
    "templates/implementation-plan.md",
    "{{",
    "phase_authorized_through",
    "events ledger",
    "paste",
)

# REQ-10: the three no-source briefings receive modest enrichment sourced
# from workflow.md's phase descriptions — non-stub. The current minimal-gate
# stubs are ~420-460 bytes; canonical stage content is materially larger.
NO_SOURCE_BRIEFINGS = (
    "spec-review.briefing.md",
    "plan-review.briefing.md",
    "robustness.briefing.md",
)
NON_STUB_MIN_BYTES = 1500

# REQ-10 non-duplication (TQ-reader / review-tests#2): each no-source briefing's
# stage gate prompt — the enrichment must come from workflow.md's phase
# descriptions, not be padded with copied gate-prompt prose. Incidental
# artifact-path overlaps run to ~6 shared words, so a 12-word verbatim span is
# the signal of copied prose.
GATE_PROMPT_FOR_NO_SOURCE = {
    "spec-review.briefing.md": "spec-review.md",
    "plan-review.briefing.md": "plan-review.md",
    "robustness.briefing.md": "robustness-analysis.md",
}
DUP_SHINGLE_WORDS = 12


# The briefing files do not change during a test run, so caching is safe.
@functools.cache
def _text(name: str) -> str:
    return (RESOURCES / name).read_text(encoding="utf-8")


# Whitespace folds with -/_ so anchors survive markdown reflow (a wrapped
# anchor phrase must not fail the pin spuriously — code-quality#7).
def _normalized(name: str) -> str:
    return re.sub(r"[\s_\-]+", " ", _text(name).casefold())


def _word_shingles(text: str, n: int) -> set[str]:
    """
    The set of contiguous ``n``-word spans in ``text`` (casefolded, split on
        non-word chars) — used to detect large verbatim overlap between a briefing
        and a gate prompt (TQ-reader / review-tests#2).
    """
    words = re.findall(r"\w+", text.casefold())
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


class TestAC6BriefingsCanonical:
    """AC-6: the briefings are canonical and carry the REQ-8 fold."""

    def test_stamp_absent_from_all_authoritative_files(self) -> None:
        stamped = [
            f"{name}: {marker!r}"
            for name in AUTHORITATIVE_FILES
            for marker in STAMP_MARKERS
            if marker in _text(name)
        ]
        assert not stamped, (
            f"FAIL AC-6: the canonical-at-M6 stamp survives: {stamped} "
            "(REQ-7/REQ-12 — m6b IS the M6 that discharges it)"
        )

    def test_nine_fold_items_present_in_target_briefings(self) -> None:
        # TQ-core (review-tests#1): each fold item is a co-occurrence of tokens
        # that must ALL appear — a substance-thinned rewrite that keeps one
        # generic keyword but drops the discipline no longer false-greens.
        missing = [
            f"{name}: {item!r} (missing {tok!r})"
            for name, items in FOLD_ITEMS.items()
            for item in items
            for tok in item
            if tok not in _normalized(name)
        ]
        assert not missing, (
            f"FAIL AC-6: REQ-8 fold items missing from target briefings: "
            f"{missing} (7 HIGH/CRITICAL + X-1 + X-2 fold inline; source "
            "material is read from the legacy files, in-tree until m4)"
        )

    def test_x1_codebase_immersion_folds_exactly_once(self) -> None:
        hosts = [
            name for name in AUTHORITATIVE_FILES if "immersion" in _normalized(name)
        ]
        assert hosts == ["implement.briefing.md"], (
            f"FAIL AC-6: X-1 (codebase immersion) must fold exactly once, "
            f"into implement.briefing.md — found in {hosts} (REQ-8)"
        )

    def test_no_legacy_or_deleted_references(self) -> None:
        hits = [
            f"{name}: {token!r}"
            for name in AUTHORITATIVE_FILES
            for token in FORBIDDEN_IN_BRIEFINGS
            if token in _text(name).casefold()
        ]
        assert not hits, (
            f"FAIL AC-6: briefings reference legacy mechanics or deletion "
            f"targets: {hits} (REQ-11/INV-5 — every command reference native; "
            "DIES-WITH-SHIM content never folds)"
        )

    def test_current_role_navigation_names_native_commands(self) -> None:
        stages = (
            "spec-review",
            "plan-review",
            "scaffold",
            "implement",
            "peer-review",
            "peer-review",
            "peer-review",
            "robustness",
        )
        missing = [
            f"{stage}: {role}"
            for stage, role in zip(stages, ROLES, strict=True)
            if role not in _text(f"{stage}.briefing.md")
            or "heddle run gate" not in _normalized(f"{stage}.briefing.md")
        ]
        assert not missing, f"Missing current role/native invocation: {missing}"

    def test_req10_no_source_briefings_are_non_stub(self) -> None:
        thin = [
            f"{name} ({size} bytes)"
            for name in NO_SOURCE_BRIEFINGS
            if (size := len((RESOURCES / name).read_bytes())) < NON_STUB_MIN_BYTES
        ]
        assert not thin, (
            f"FAIL AC-6: no-source briefings still stub-sized: {thin} — "
            "REQ-10 enriches spec-review/plan-review/robustness from "
            "workflow.md's phase descriptions (not duplicated gate-prompt "
            "content)"
        )

    def test_req10_no_source_briefings_do_not_duplicate_gate_prompts(
        self,
    ) -> None:
        # TQ-reader (review-tests#2): byte-size alone can't tell enrichment sourced
        # from workflow.md from padding with copied gate-prompt prose — the
        # anti-pattern REQ-10 forbids. Assert no large verbatim span (>=
        # DUP_SHINGLE_WORDS words) is shared with the stage gate prompt;
        # incidental artifact-path overlaps run to ~6 words, well under it.
        offenders = []
        for briefing, prompt_name in GATE_PROMPT_FOR_NO_SOURCE.items():
            briefing_spans = _word_shingles(_text(briefing), DUP_SHINGLE_WORDS)
            prompt_spans = _word_shingles(
                (PROMPTS / prompt_name).read_text(encoding="utf-8"),
                DUP_SHINGLE_WORDS,
            )
            shared = sorted(briefing_spans & prompt_spans)
            if shared:
                offenders.append(f"{briefing} <-> {prompt_name}: {shared[:2]}")
        assert not offenders, (
            f"FAIL AC-6/REQ-10: a no-source briefing duplicates gate-prompt "
            f"content ({DUP_SHINGLE_WORDS}-word verbatim span): {offenders} — "
            "enrichment must be sourced from workflow.md's phase descriptions, "
            "not copied from the gate prompt"
        )


def _legacy_stage_host(tmp_path: Path, stage: str, tier: int) -> Path:
    """
    Fixture host with the golden tier-3 workspace patched to (stage, tier).

        REQ-31/review: kickoff is invoked only against this tmp_path copy, never
        the ambient repo.

    """
    host = tmp_path / f"host-{stage}-t{tier}"
    shutil.copytree(GOLDEN, host)
    state_path = host / "plans" / FIXTURE_SLUG / "state.yaml"
    text = state_path.read_text(encoding="utf-8")
    for needle, replacement in (
        ("stage: implement\n", f"stage: {stage}\n"),
        ("authorized_through: implement\n", f"authorized_through: {stage}\n"),
        ("tier: 3\n", f"tier: {tier}\n"),
    ):
        assert text.count(needle) == 1, (
            f"FIXTURE ROT: expected exactly one {needle!r} in the golden "
            f"{FIXTURE_SLUG} state.yaml — repin the patcher"
        )
        text = text.replace(needle, replacement)
    state_path.write_text(text, encoding="utf-8")
    return host


def _stage_host(tmp_path, stage, selection, monkeypatch):
    """Author a fresh current host; never convert an operational v6 ledger."""
    fixture_root = tmp_path / f"{stage}-{selection}"
    fixture_root.mkdir()
    overrides = (
        {role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES}
        if selection == "off"
        else {}
    )
    return current_host(fixture_root, monkeypatch, stage=stage, overrides=overrides)


class TestAC7KickoffRendersClean:
    """AC-7: heddle kickoff renders every refreshed briefing clean, per
    stage, with selected and all-off current policies, from fixture workspaces."""

    @pytest.mark.parametrize("selection", ["selected", "off"])
    @pytest.mark.parametrize("stage", STAGES)
    def test_kickoff_renders_refreshed_briefing(
        self,
        stage: str,
        selection: str,
        run_cli,
        envelope_tools,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _host, path = _stage_host(tmp_path, stage, selection, monkeypatch)
        before = path.read_bytes()
        ctx = f"AC-7({stage}, {selection})"

        code, out, _err = run_cli(["kickoff", "--json", "--feature", V7_FEATURE])
        assert code == 0, (
            f"FAIL {ctx}: kickoff exited {code} — the render must succeed per stage"
        )
        envelope = envelope_tools.parse(out)
        assert envelope["ok"] is True, f"FAIL {ctx}: kickoff envelope not ok"
        data = envelope["data"]
        assert data["stage"] == stage, (
            f"FAIL {ctx}: envelope reports stage {data['stage']!r}"
        )
        assert data["source"] == "packaged", (
            f"FAIL {ctx}: briefing must use the packaged resource on "
            f"a clean host, got {data['source']!r}"
        )
        briefing = data["briefing"]
        assert briefing.strip(), f"FAIL {ctx}: empty briefing"
        for marker in STAMP_MARKERS:
            assert marker not in briefing, (
                f"FAIL {ctx}: served briefing still carries the "
                f"canonical-at-M6 stamp ({marker!r}) — kickoff must serve "
                "the refreshed canonical content (REQ-7)"
            )
        assert "{{" not in briefing, (
            f"FAIL {ctx}: unresolved template-variable residue in the served briefing"
        )
        assert briefing == _text("decision-routing.md") + "\n\n---\n\n" + _text(
            f"{stage}.briefing.md"
        )
        # Delivered stage duties survive selected and all-Off review policy.
        # These are instruction-delivery checks, not model-judgment evidence.
        folded = " ".join(briefing.split())
        if stage == "specify":
            assert "do not record that future native question at specify" in folded
            assert "unresolved decision blocks progression" in folded
        elif stage == "spec-review":
            assert "including when spec review is Off" in folded
            assert "no separate e2e decision is needed" in folded
            assert "scope question with class 8" in folded
            assert "class-5 `question`" in folded
            assert "per-attempt/aggregate cost caps" in folded
        elif stage in {"peer-review", "robustness"}:
            assert "Assessment" in briefing and "passing run" in briefing
            assert "replacement output" in briefing
            assert "quality beyond the contract" in folded
            assert "native freshness" in folded.lower()
            assert "qualifying" in folded and "rereview" in folded
            assert "live does not replace acceptance" in folded
        elif stage == "complete":
            assert "final native run and relevant source identity" in folded
            assert "Execution deferral remains pending" in folded
        assert path.read_bytes() == before


# --------------------------------------------------------------------------- #
# completion (kickoff-guidance-integration) — canonical content contracts (write)
# --------------------------------------------------------------------------- #
# Red discriminators for AC-3/AC-5/AC-6 (content halves) plus the AC-11/12/13
# [content-test] legs, with survivor rails for AC-1/review and the stop-pattern
# scan. Anchor style follows FOLD_ITEMS: multi-token co-occurrences over
# _normalized text, never single generic words. COMPLETION_FOLD_ITEMS seeds the
# per-destination coverage from the Feature Spec's Required-treatment column;
# milestone write's state mutation delta worklist EXTENDS this dict to the full
# enumerated
# coverage manifest (recorded in the Implementation Plan at the write boundary)
# — every manifest item lands in its named destination, with the pairwise
# shingle rail below keeping folded content from landing twice.
# Behavior contract: kickoff-guidance-integration

COMPLETION_FOLD_ITEMS: dict[str, tuple[tuple[str, ...], ...]] = {
    # Full enumerated state mutation manifest (write delta worklist, recorded in the
    # Implementation Plan § state mutation coverage manifest). Item ids state
    # mutation-<dest><n>
    # match the manifest rows; robustness (no fold items — robustness only)
    # and complete (enumerated by COMPLETION_COMPLETE_TEN_AREAS plus the two-pass/
    # cleanup/ordering/close-fact anchor sets below) have no rows here.
    "specify.briefing.md": (
        ("feature spec", "implementation plan"),
        ("research checkpoint", "checkpoint 1"),
        ("related work", "repository evidence"),
        ("implementation parts", "authorization"),
        ("enforcement rules referenced at their source", "design commitments"),
        ("one canonical acceptance criteria list", "verified by"),
    ),
    "scaffold.briefing.md": (
        ("integrated acceptance test",),
        ("live e2e lane", "prerequisite"),
        ("smoke test",),
        ("ac coverage matrix",),
        ("commands set",),
        ("red discriminator", "survivor pin"),
        ("completion evidence",),
    ),
    "implement.briefing.md": (
        ("session orientation",),
        ("quick orientation", "relevant spec pointers"),
        ("verify", "scope", "smoke"),
        ("commit the remediation",),
        ("propagate", "record reading pointers"),
        ("final feature verification",),
    ),
    "peer-review.briefing.md": (
        ("assess a finding",),  # state mutation-P1 phase7: read code before judging
        ("reviewer sequencing",),  # state mutation-P2 phase7: reviewer sequencing
        ("no synthesis authority", "evidence bound"),
        ("disposition rerun",),  # state mutation-P4: disposition protocol
        ("exit readiness",),  # state mutation-P5 phase7: post-fix acceptance
    ),
}


class TestStageSemanticContracts:
    """Cross-stage outcome, ownership, and final-proof obligations."""

    def test_specify_produces_both_documents_and_separates_checkpoints(self) -> None:
        text = _normalized("specify.briefing.md")
        assert "feature spec" in text and "implementation plan" in text
        assert "research checkpoint" in text and "checkpoint 1" in text
        assert "heddle milestone add" in text and "heddle validate" in text
        assert "verbatim as provenance" in text and "scope delta" in text
        assert "part changed shape" in text and "requiring authorization" in text

    def test_scaffold_owns_the_complete_verification_architecture(self) -> None:
        text = _normalized("scaffold.briefing.md")
        required = (
            "milestone tests",
            "integrated acceptance test",
            "live e2e lane",
            "smoke test",
            "ac coverage matrix",
            "red discriminator",
            "survivor pin",
            "heddle commands set",
            "always green progressive regression",
            "future red discriminators",
            "design failure output for the next model",
            "grep friendly",
            "actionable first diagnostic",
            "contract evidence",
            "fixture preconditions",
            "inspection, collection, or execution",
        )
        assert not [item for item in required if item not in text]

    def test_implement_restores_orientation_continuity_and_final_proof(self) -> None:
        text = _normalized("implement.briefing.md")
        required = (
            "session orientation",
            "quick orientation",
            "relevant spec pointers",
            "latest session",
            "recent git history",
            "commit the remediation",
            "final feature verification",
            "heddle verify scope acceptance",
            "heddle verify scope smoke",
            "live e2e prerequisites",
            "live lane readiness or explicit deferral",
        )
        assert not [item for item in required if item not in text]
        assert "quote the spec s contracts in your reasoning" not in text

    def test_final_acceptance_follows_all_review_fix_surfaces(self) -> None:
        peer = _normalized("peer-review.briefing.md")
        robust = _normalized("robustness.briefing.md")
        complete = _normalized("complete.briefing.md")
        assert (
            "when overlay r is off, this is the owning final verification boundary"
            in peer
        )
        assert (
            "when overlay r is on, robustness owns final post hardening proof" in peer
        )
        assert "documentary drift under an unchanged approved contract" in peer
        assert "heddle validate" in peer
        assert not [
            token
            for token in (
                "a change to an ac",
                "labeled assumption",
                "prior user decision",
                "contract changing fix until resolved",
            )
            if token not in peer
        ]
        assert "heddle verify scope acceptance" in robust
        assert "heddle verify scope smoke" in robust
        assert "heddle verify scope live" in robust
        assert "declared live evidence is current" in robust
        assert "mere execution deferral does not qualify exit" in robust
        assert (
            "authorized change to the obligation must already be reconciled" in robust
        )
        assert "documentary drift under an unchanged approved contract" in robust
        assert all(
            token in robust for token in ("criterion", "assumption", "settled decision")
        )
        assert "report route until it is resolved" in robust
        assert "unreviewed mutation" in complete
        assert "verified by" in complete
        assert "declared live proof is additional to acceptance" in complete

    def test_complete_checkpoint_allows_mutable_close_work_but_not_persistence(
        self,
    ) -> None:
        text = _normalized("complete.briefing.md")
        assert not [
            token
            for token in (
                "mutable feature spec",
                "plan",
                "documentation",
                "close artifact work",
            )
            if token not in text
        ]
        assert "do not persist proposed memory and pattern candidates" in text
        assert "nothing persists" not in text


# AC-3: the ten approved functional areas of the complete briefing.
COMPLETION_COMPLETE_TEN_AREAS: dict[str, tuple[str, ...]] = {
    "area-1-orientation": ("orientation", "ephemeral"),
    "area-2-propagation": ("propagat", "sweep"),
    "area-3-memory": ("memory candidate", "dedup"),
    "area-4-patterns": ("reusable pattern", "explicit approval"),
    "area-5-deferred-enforcement": ("enforce later", "deterministic"),
    "area-6-gate-effectiveness": ("gate effectiveness", "false negative"),
    "area-7-outcomes": ("outcomes", "retrospective", "deferred scope"),
    "area-8-self-containment": ("fresh agent", "self contain"),
    "area-9-repo-docs": ("repository docs",),
    "area-10-final-close": ("final checkpoint", "archive", "cleanup"),
}

# AC-11 [content-test]: the two-pass approval grammar as defined in the spec's
# Terms — pass 1 batch (project/reference), pass 2 individually explicit
# (feedback/user + patterns), proposed durable memory/pattern writes wait for
# the final checkpoint, and unconfirmed terminal candidates are dropped visibly.
COMPLETION_TWO_PASS_ANCHORS: tuple[tuple[str, ...], ...] = (
    ("pass 1", "project", "reference"),
    ("pass 2", "individually", "feedback"),
    ("final checkpoint",),
    ("memory", "pattern", "durable stores"),
    ("drop", "visibl"),
)

# AC-12 amended by W5: bounded eligible cleanup and retained evidence.
# Runtime controls prove the actual barrier; these anchors protect disclosure.
COMPLETION_CLEANUP_ANCHORS: tuple[tuple[str, ...], ...] = (
    ("bounded cleanup", "plan.md", "brief.md"),
    ("retain", "state.yaml", "canonical review", "verification log"),
    ("preserve", "unknown"),
    ("decision journal",),
    ("friction retrospective",),
    ("gate effectiveness",),
    ("trajectory",),
    ("feature spec",),
)

# AC-13 [content-test]: cleanup ordering protects evidence.
COMPLETION_ORDERING_ANCHORS: tuple[tuple[str, ...], ...] = (
    ("completion.tar.gz", "verified member readback"),
    ("local retention", "accepted", "state.yaml", "archive"),
    ("git commit", "optional"),
    ("plan.md", "brief.md", "retain"),
    ("accepted retry", "already removed"),
    ("manual recovery",),
)

# AC-3/REQ-16 amended by W5: explicit typed acceptance and memory degradation.
COMPLETION_CLOSE_FACT_ANCHORS: tuple[tuple[str, ...], ...] = (
    ("heddle feature complete", "typed completion fact"),
    ("session log", "ordinary narrative", "cannot accept completion"),
    ("retries", "no close suite"),
    ("session log",),
    ("memory", "degrad"),
)

# AC-6: the six case-insensitive stop-pattern anchors forbidden from every
# through-robustness briefing (the complete briefing, which OWNS the HITL
# protocol, is exempt by design).
COMPLETION_STOP_PATTERNS: tuple[str, ...] = (
    "wait for the user",
    "stop and ask the user",
    "pause for user",
    "await user approval",
    "mandatory user checkpoint",
    "do not proceed without user",
)

COMPLETION_THROUGH_ROBUSTNESS: tuple[str, ...] = tuple(
    f"{stage}.briefing.md" for stage in STAGES if stage != "complete"
)

# The six COMPLETION-refreshed destinations (spec Content translation table).
COMPLETION_REFRESHED: tuple[str, ...] = (
    "specify.briefing.md",
    "scaffold.briefing.md",
    "implement.briefing.md",
    "peer-review.briefing.md",
    "robustness.briefing.md",
    "complete.briefing.md",
)


def _completion_missing(name: str, anchors) -> list[str]:
    text = _normalized(name)
    return [
        f"{name}: {item!r} (missing {[tok for tok in item if tok not in text]})"
        for item in anchors
        if not all(token in text for token in item)
    ]


class TestCompletionCoverageManifest:
    """
    AC-5: every manifest item lands in its named destination (seeded from
        the Required-treatment column; write extends COMPLETION_FOLD_ITEMS from the
        state mutation
        delta worklist), and folded content lands exactly once — no 12-word
        verbatim span shared between any two refreshed briefings.
    """

    def test_completion_fold_items_present_in_named_destinations(self) -> None:
        missing = [
            entry
            for name, anchors in COMPLETION_FOLD_ITEMS.items()
            for entry in _completion_missing(name, anchors)
        ]
        assert not missing, (
            f"FAIL AC-5: manifest items missing from their named "
            f"destinations: {missing}"
        )

    # Two pre-completion shared passages are pinned as baseline: the commands gate-
    # artifact location sentence (implement <-> peer-review) and the decision deferral-D
    # disposition-rerun protocol sentence (peer-review <-> robustness).
    # They predate this feature; the rail forbids NEW duplication only.
    _PREEXISTING_SHARED = (
        "Artifacts land in `plans/[feature]/reviews/`; gate facts in the "
        "workspace `state.yaml`.",
        "A rerun is a **disposition rerun**, not fresh discovery: the "
        "runtime archives the displaced artifact and injects a Disposition "
        "Rerun Ledger into the prompt; the protocol is "
        "`prompt-authoring-standards.md#disposition-rerun`. "
        "Settled decisions and documented accepted-risk assumptions are not "
        "findings on a rerun. An actionable fourth review records a class-3 "
        "cap decision; a fifth run is rejected.",
        "it shares the decision deferral-D ceiling: at most four completed reviews per "
        "scope (initial + up to three reruns).",
    )

    def test_completion_no_verbatim_duplication_between_refreshed_briefings(
        self,
    ) -> None:
        """Survivor rail (green over the pinned baseline BY DESIGN):
        exactly-once placement — shared workflow procedures are CITED, not
        copied, so no folded item may land verbatim in two destinations."""
        baseline: set[str] = set()
        for passage in self._PREEXISTING_SHARED:
            baseline |= _word_shingles(passage, DUP_SHINGLE_WORDS)
        offenders = []
        for index, first in enumerate(COMPLETION_REFRESHED):
            for second in COMPLETION_REFRESHED[index + 1 :]:
                shared = sorted(
                    _word_shingles(_text(first), DUP_SHINGLE_WORDS)
                    & _word_shingles(_text(second), DUP_SHINGLE_WORDS) - baseline
                )
                if shared:
                    offenders.append(f"{first} <-> {second}: {shared[:1]}")
        assert not offenders, (
            f"FAIL AC-5: folded content landed twice ({DUP_SHINGLE_WORDS}-"
            f"word verbatim span shared between destinations): {offenders}"
        )


class TestCompletionCompleteBriefingProtocol:
    """AC-3 + the AC-11/AC-12/AC-13 [content-test] legs: the complete
    briefing owns the full native HITL completion protocol."""

    def test_completion_all_ten_functional_areas_present(self) -> None:
        text = _normalized("complete.briefing.md")
        missing = [
            f"{area}: needs {[tok for tok in anchors if tok not in text]}"
            for area, anchors in COMPLETION_COMPLETE_TEN_AREAS.items()
            if not all(token in text for token in anchors)
        ]
        assert not missing, (
            f"FAIL AC-3/REQ-15: complete.briefing.md is missing approved "
            f"functional areas: {missing}"
        )

    def test_completion_two_pass_approval_grammar_disclosed(self) -> None:
        missing = _completion_missing(
            "complete.briefing.md", COMPLETION_TWO_PASS_ANCHORS
        )
        assert not missing, (
            f"FAIL AC-11: the two-pass approval grammar is not fully "
            f"disclosed: {missing}"
        )

    def test_completion_cleanup_disposition_names_survivors_and_scope(self) -> None:
        missing = _completion_missing(
            "complete.briefing.md", COMPLETION_CLEANUP_ANCHORS
        )
        assert not missing, (
            f"FAIL AC-12: the disclosed cleanup set is incomplete: {missing}"
        )

    def test_completion_cleanup_ordering_protects_evidence(self) -> None:
        missing = _completion_missing(
            "complete.briefing.md", COMPLETION_ORDERING_ANCHORS
        )
        assert not missing, (
            f"FAIL AC-13: archive/local-retention/delete ordering and recovery "
            f"are not fully documented: {missing}"
        )

    def test_completion_qualifying_fact_and_memory_degradation_disclosed(self) -> None:
        missing = _completion_missing(
            "complete.briefing.md", COMPLETION_CLOSE_FACT_ANCHORS
        )
        assert not missing, (
            f"FAIL AC-3/REQ-16: the qualifying fact contract or the memory-"
            f"capability degradation disclosure is missing: {missing}"
        )

    @pytest.mark.parametrize("selection", ["selected", "off"])
    def test_completion_survivor_complete_briefing_renders_for_current_policies(
        self,
        selection: str,
        run_cli,
        envelope_tools,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Survivor pin: policy selection cannot thin completion guidance."""
        _stage_host(tmp_path, "complete", selection, monkeypatch)
        code, out, _err = run_cli(["kickoff", "--json", "--feature", V7_FEATURE])
        assert code == 0, f"FAIL AC-3({selection}): kickoff exited {code}"
        envelope = envelope_tools.parse(out)
        assert envelope["data"]["briefing"] == _text(
            "decision-routing.md"
        ) + "\n\n---\n\n" + _text("complete.briefing.md"), (
            f"FAIL AC-3({selection}): kickoff must deliver the canonical "
            "complete briefing byte-for-byte"
        )


class TestCompletionPermissionContainment:
    """AC-6 (content half): briefing-command profile containment and the
    forbidden stop-pattern scans over through-robustness briefings."""

    @classmethod
    def _leading_executables(
        cls, profiles: dict[str, tuple[str, ...]]
    ) -> frozenset[str]:
        """The AC-6 closed recognizer's leading-token allowlist, DERIVED
        from the union of Bash prefixes across every constructible stage
        profile (review-synthesis#2) so it tracks the profiles automatically:
        `heddle` plus any executable a profile ever names. No other leading
        token is extracted."""
        executables = {"heddle"}
        for prefixes in profiles.values():
            for prefix in prefixes:
                executables.add(prefix.split()[0])
        return frozenset(executables)

    @classmethod
    def _extract_commands(
        cls, name: str, profiles: dict[str, tuple[str, ...]]
    ) -> list[str]:
        """The AC-6 closed recognizer: fenced-block lines plus backticked
        inline commands whose first token is in the profile-derived
        executable allowlist (`_leading_executables`)."""
        import re as _re

        executables = cls._leading_executables(profiles)

        def _recognized(candidate: str) -> bool:
            tokens = candidate.split()
            return bool(tokens) and tokens[0] in executables

        commands: list[str] = []
        in_fence = False
        for line in _text(name).splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                if _recognized(stripped):
                    commands.append(stripped)
                continue
            for span in _re.findall(r"`([^`]+)`", line):
                token = span.strip()
                if _recognized(token):
                    commands.append(token)
        return commands

    @staticmethod
    def _allowed_prefixes(host: Path) -> tuple[str, ...]:
        import re as _re

        from heddle.driver.sessions import allowed_tools_for_stage
        from heddle.kernel.model import resolve_snapshot
        from heddle.kernel.project_config import load_project_config

        config = load_project_config(host)
        snapshot = resolve_snapshot(config, V7_FEATURE)
        prefixes = []
        for tool in allowed_tools_for_stage(config, snapshot):
            match = _re.fullmatch(r"Bash\(([^:]+):\*\)", tool)
            if match is not None:
                prefixes.append(match.group(1))
        return tuple(prefixes)

    def test_completion_extracted_commands_resolve_within_stage_profiles(
        self, tmp_path, monkeypatch
    ) -> None:
        profiles = {
            stage: self._allowed_prefixes(
                _stage_host(tmp_path, stage, "selected", monkeypatch)[0]
            )
            for stage in STAGES
            if stage != "complete"
        }
        offenders = []
        for stage, prefixes in profiles.items():
            for command in self._extract_commands(f"{stage}.briefing.md", profiles):
                if not any(
                    command == prefix or command.startswith(prefix + " ")
                    for prefix in prefixes
                ):
                    offenders.append(f"{stage}.briefing.md: `{command}`")
        assert not offenders, (
            "FAIL AC-6: briefing command invocations must resolve within the "
            "stage's existing allowed-tools profile (added content requires "
            f"no newly granted operation): {offenders}"
        )

    @pytest.mark.parametrize(
        "stage", [stage for stage in STAGES if stage != "complete"]
    )
    def test_current_disposition_preapproval_is_scoped_to_review_stages(
        self, tmp_path, monkeypatch, stage
    ):
        host, _path = _stage_host(tmp_path, stage, "selected", monkeypatch)
        prefixes = self._allowed_prefixes(host)
        assert ("heddle review disposition" in prefixes) == (stage != "specify")
        assert "heddle review round open" not in prefixes
        assert "heddle decisions resolve" not in prefixes

    def test_completion_no_stop_patterns_through_robustness(self) -> None:
        # Green at scaffold time (the current corpus is clean) — the rail
        # that keeps write's checkpoint-ownership enrichment flow-conditional
        # (the spec pins that the anchors permit such wording).
        offenders = [
            f"{name}: {pattern!r}"
            for name in COMPLETION_THROUGH_ROBUSTNESS
            for pattern in COMPLETION_STOP_PATTERNS
            if pattern in _normalized(name)
        ]
        assert not offenders, (
            f"FAIL AC-6: forbidden stop patterns in through-robustness "
            f"briefings: {offenders}"
        )

    def test_completion_robustness_briefing_owns_no_close_artifacts(self) -> None:
        # robustness only (spec Content translation): green at scaffold time
        # and must stay green through write's robustness refresh.
        text = _normalized("robustness.briefing.md")
        offenders = [
            phrase
            for phrase in ("friction retrospective", "decision journal")
            if phrase in text
        ]
        assert not offenders, (
            f"FAIL AC-6: robustness.briefing.md must not own completion stage close "
            f"artifacts: {offenders}"
        )
