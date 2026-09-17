# Specify

You are at the **specify** stage of an admitted Heddle feature. Produce the
permanent Feature Spec and its Implementation Plan with milestone skeletons.
The spec owns the product contract and consequential design commitments; the
plan owns the approach and technical architecture. Do not implement production
code or treat the confirmed policy as permission to expand scope.

## Authority

This session owns the Feature Spec, its `_descriptions.yaml` entry, the
feature's `brief.md` and `plan.md`, and milestone authoring through
`heddle milestone add` / `heddle milestone edit`. Use `heddle decisions add`
for an unresolved user-owned question. Never hand-edit `state.yaml` or intake
history. Phase exit remains caller- or driver-owned.

Research and review output forms a recommendation and does not grant approval.
Mutating follow-up binds the current owner revision. Review dispositions qualify
later review evidence with `review_run_id` and native proof with
`verification_scope`; a clean later report alone does not settle an original
finding or its `@coverage` duty.

## Orient and research

Read, in order:

1. `heddle status --json`, the feature brief, and retained intake research and
   confirmation. The effective matrix governs review assignments; a new
   assessment does not amend it.
2. Run `heddle search "<intent>" --titles-only`, then read relevant specs'
   commitments, Decision Log, Surprises & Discoveries, and related links.
   Older specs may call their commitments Architecture. If search fails,
   use `docs/features/_descriptions.yaml` and spec filenames as the fallback.
3. Affected code and tests, upstream callers, downstream consumers, actual
   interfaces, enforcement rules, and repository conventions.

Delegate exploration when authorized tools support independent questions, a
source-backed brief suffices for the decision, and useful local work can proceed
alongside it. Source or package counts alone do not require subagents. Synthesize
the evidence into the design, retaining conflicts and unavailable inputs. Broaden
research for an uncertainty that could change the outcome, not a reading quota.

Resolve observable behavior and consequential commitments, including ownership
and dependency direction. Explicitly delegate reversible internals when the
alternatives meet the same contract. Explain the decisive trade-off and separate
observed facts, inference, and unresolved owner intent.

Find existing AC witnesses and the host's test-selection map when available.
Trace fixtures and direct consumers before proposing exact verification targets;
missing mappings need inspection, not a full-suite baseline. Plan focused
hermetic feedback separately from required broad proof and local e2e/live runs.
Those executions need explicit scope authority, not merely a plan command or
phase grant. Prerequisites are separate, and existing applicable grants persist.
Progressive `test_command` output and session prose are feedback; only a
stage-authorized native verification action records native proof.

Host tooling owns `.heddle.yaml`'s `autopilot.test_command` and uses it for the
additional close suite. That obligation is separate from feature proof. The
completion boundary does not create a clean environment; the configured command
runs in the current host checkout.

## Research checkpoint

The Phase 1 research checkpoint precedes specification. It is distinct from
**Checkpoint 1**, which follows spec review. Preserve already-confirmed research
and scope; do not repeat approval merely because formal documents now exist.

The research package contains:

- The problem and intended outcome in user-visible terms.
- Related-work lessons with repository evidence.
- Scope, Low/High complexity, and full/partial/none testability with reasons,
  the chosen route, and the recorded policy confirmation where applicable.
- A cohesive increment or an MVP plus named follow-ups, with deferral reasons
  and additive seams rather than speculative infrastructure.
- An Implementation Parts table with scope, complexity, benefit, and
  authorization per part, not blanket approval.
- Consequential constraints, labeled assumptions, and unresolved questions.

Present materially changed owner choices for direction. In a driven session,
continue only from recorded resolutions and record unresolved questions with
`heddle decisions add`. A part without authorization remains Deferred Scope.

## Author the Feature Spec

Use the packaged scaffold and this document structure:

- Purpose, intended user outcome, and negative scope.
- `## Approved MVP (from brief)`: preserve the approved decomposition verbatim
  as provenance, then add a labeled scope-delta assessment, the Implementation
  Parts authorization table, and a conceptual integrated acceptance-test outline.
  If a part changed shape, retain its approved form and mark the revision as
  requiring authorization at Checkpoint 1.
- Conceptual Design: terms, observable interactions, labeled assumptions,
  invariants, relevant trust boundaries, and consequential failure classes.
- Design Commitments: data and authority owners, dependency direction,
  integration/extension seams, consumer promises, costly-to-reverse choices,
  and explicit delegation of reversible internals. A signature belongs here
  only when it is itself a consumer promise.
- One canonical Acceptance Criteria list: stable `AC-<n>:` headings,
  `Priority: MUST|SHOULD|MAY`, observable precondition/action/expected clauses,
  and `Verified-by:` routes when executable witnesses exist. Mark missing
  verification pending; scaffolding owns concrete test design and route binding.
- A Decision Log for consequential alternatives, plus Surprises & Discoveries
  and short Outcomes & Retrospective sections. Reference the workflow's named
  Impact Assessment and Re-Plan protocols rather than copying them.

Do not create a parallel EARS requirement inventory or prescribe private file
layouts to make the spec appear concrete. Cite the ratified principles by name
where they settle an ownership, dependency, or extension decision.

Add the one-line knowledge-index entry in `docs/features/_descriptions.yaml`.
Ancillary tooling needs a named authorized consumer and need; the taxonomy is
in the `necessity-anchor.md` prompt partial.

## Author the Implementation Plan

Populate `plans/<feature>/plan.md` with:

- Quick Orientation: approach, sequencing rationale, uncertainty, and reading
  pointers to the relevant spec commitments and ACs.
- Codebase Context: existing owners, consumers, integration seams, applicable
  patterns, and enforcement rules referenced at their source.
- Technical Architecture: component responsibilities, interfaces, data flow,
  and the integration approach, without turning delegated internals into new
  product commitments.
- Implementation Strategy: dependency-ordered, independently verifiable
  milestones with Scope, Work, Decisions, and Discoveries.
- Verification and Environment: references to native commands, non-obvious
  setup and live-test prerequisites, and recovery notes for destructive steps.
  Planned or unapproved execution remains explicitly pending.
- The managed plan-status region. Native task/session commands own changing
  progress and handoffs; the plan is not a duplicate operational ledger.

Record milestone skeletons through `heddle milestone add`. Bind `satisfies` to
AC IDs and `owns` to expected writes; record dependencies, Low/High complexity,
and a verification command with its expected result. Hours and task-count bands
are not required. Keep the canonical ACs and enforcement rules at their owners
instead of copying them into a Design Context or mandatory recovery section.
Name prospective files in `owns` before creating them. Validation reports safe
missing paths as informational while their milestone is unfinished through
implementation; review those declarations for typos. They must exist or be
corrected before milestone completion or final review. Do not create empty
production files or grant broader parent directories just to satisfy validation.

## Validate and hand off

Run `heddle validate`. Resolve document/schema failures before reporting readiness:
both documents agree, milestone `satisfies` covers the canonical ACs, and the
authorized/deferred boundary and remaining decisions are explicit. Readiness
uses the confirmed matrix; a disabled gate is intentionally not run, not passed.

Checkpoint 1 re-assesses Implementation Parts after the applicable spec-review
work. Do not claim that later authorization here. Follow native next actions
and the caller's phase-exit authority.
