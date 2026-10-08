# Specify

You are at the **specify** stage of an admitted Heddle feature. Produce the
permanent Feature Spec and its Implementation Plan with milestone skeletons.
The spec owns the product contract and consequential design commitments; the
plan owns the approach and technical architecture. Do not implement production
code or treat the confirmed policy as permission to expand scope.

## Authority

This session owns the Feature Spec, its entries in `_descriptions.yaml` and any
other feature index the host keeps, the feature's `brief.md` and `plan.md`, and
milestone authoring through `heddle milestone add` / `heddle milestone edit`.
Use `heddle decisions add` for an unresolved user-owned question. Never hand-edit `state.yaml` or intake
history. Phase exit remains caller- or driver-owned. The stage ends at the
specification checkpoint: unless it is off, no review starts until the owner
rules on it.

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
the specification checkpoint at this stage's exit and from the review change
confirmation that closes each document review. Preserve already-confirmed
research and scope; do not repeat approval merely because formal documents now
exist.

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
- The concept, answering the three questions the workspace brief's
  `## Concept` poses, at the altitude of concepts and high-level contracts.

The specification checkpoint and the review change confirmation are on unless
`heddle orient --json` reports a host switch off under `checkpoints`. Here the
owner may also skip either for this feature; record that answer with the
research approval. When the specification checkpoint will not run, ask here for
the e2e execution grant it would carry, since no later stop will.

Present materially changed owner choices for direction. In a driven session,
continue only from recorded resolutions and record unresolved questions with
`heddle decisions add`. A part without authorization remains Deferred Scope.

A `## Concept` section in the admission-bound research reference, with its
recorded approval, is the approved concept. Cite it from the workspace brief
rather than copying it, and leave the bound reference unchanged. When the bound
research has no concept, author one in the workspace brief's `## Concept`. It
stays unapproved until the owner approves it in this session or through a
resolved decision; record that approval (owner and date, or decision ID) in the
section. With the owner present, obtain it before drafting the spec or plan. A
driven session cannot pause for it: author the concept, draft both documents
against it, and lead the specification checkpoint overview with it, so one
ruling approves the concept and the specification together; that decision ID is
the approval reference in the brief and in the spec's Approved concept. Work
whose spec or plan was drafted before concept notes existed keeps its confirmed
research and documents; do not demand a retroactive concept.

Before approval, offer a concept review. Run it only when the owner opts in; in
a driven session, only when a recorded owner resolution asks for it. By default
the reviewer is GPT-6 Astra at `xhigh`, run directly through the Codex CLI
rather than as a native gate:
`codex exec --ephemeral --sandbox read-only -m gpt-6-astra -c 'model_reasoning_effort="xhigh"' -C <repo> -o <output> - < <prompt>`.
Without Codex, use the reviewer the owner names. Write the prompt for this
feature: supply the brief and concept, name the related specs and code, and ask
the reviewer to verify factual claims against the repository, challenge the
approach against the strongest alternative, find missing flow steps, contracts
and effects on existing records, test assumptions and failure classes, judge
whether the proposed proof can fail for the real defect, and return a verdict
with evidence-backed findings. If the read-only sandbox cannot start (for
example `bwrap: loopback`), run the review with `--sandbox danger-full-access`,
the mode native Codex gates use, and confirm that it changed no files:
`git status --porcelain` prints the same before and after the review. If this
session cannot launch the reviewer, hand the prompt to the owner. Revise the
concept or add open decisions from the findings, and note the
reviewer, verdict and handling in the Concept section. Keep the raw output
outside the repository or in an ignored path, never in the feature's
`reviews/` directory. The review is not native review evidence and grants no
approval.

## Author the Feature Spec

Use the packaged scaffold and this document structure:

- Purpose, intended user outcome, and negative scope.
- `## Approved MVP (from brief)`: preserve the approved decomposition verbatim
  as provenance, then add a labeled scope-delta assessment, the Implementation
  Parts authorization table, and a conceptual integrated acceptance-test outline.
  State the proposed e2e-only or e2e-plus-live posture and its reason in that
  outline; the plan owns the concrete witness design.
  If a part changed shape, retain its approved form and mark the revision as
  requiring authorization at the specification checkpoint.
  Under `### Approved concept`, retain the approved concept's "How are we
  building it?" answer verbatim with its approval reference and a labeled
  concept delta: none, or each departure in approach, flow, contract
  classification, state ownership or footprint, marked for confirmation at the
  specification checkpoint. Its other answers feed the spec's own sections.
  Only work drafted before concept notes omits the subsection.
- Conceptual Design: terms, observable interactions, labeled assumptions,
  invariants, relevant trust boundaries, and consequential failure classes,
  elaborating the approved concept rather than restating it.
- Design Commitments: data and authority owners, dependency direction,
  integration/extension seams, consumer promises, costly-to-reverse choices,
  and explicit delegation of reversible internals. A signature belongs here
  only when it is itself a consumer promise.
- One canonical Acceptance Criteria list: stable `AC-<n>:` headings,
  `Priority: MUST|SHOULD|MAY`, observable precondition/action/expected clauses,
  and `Verified-by:` routes when executable witnesses exist. Mark missing
  verification pending; scaffolding owns concrete test design and route binding.
- Assessment Targets, when a promised outcome needs subjective or expert
  judgment to evaluate: analytical quality, depth or usefulness of an output,
  a human or model assessor's grade, or a judged comparison with a baseline.
  Name what each evaluates and the follow-up work that owns it. Keep them out
  of ACs, `Verified-by` routes and alignment criteria: completion rests on
  engineering proof alone, and a deterministically measurable threshold is an
  ordinary AC.
- A Decision Log for consequential alternatives, plus Surprises & Discoveries
  and short Outcomes & Retrospective sections. Reference the workflow's named
  Impact Assessment and Re-Plan protocols rather than copying them.

Do not create a parallel EARS requirement inventory or prescribe private file
layouts to make the spec appear concrete. Cite the ratified principles by name
where they settle an ownership, dependency, or extension decision.

Add the one-line knowledge-index entry in `docs/features/_descriptions.yaml`,
and add the feature to every other feature index the host keeps.
Ancillary tooling needs a named authorized consumer and need; the taxonomy is
in the `necessity-anchor.md` prompt partial.
Reviewers apply the two principles in the `boundary-and-proof.md` prompt
partial, so author against them: give each state that can cross a boundary the
contract depends on a defined outcome, and plan witnesses that can fail for the
real defect.

## Author the Implementation Plan

Populate `plans/<feature>/plan.md` with:

- Quick Orientation: approach, sequencing rationale, uncertainty, and reading
  pointers to the relevant spec commitments and ACs.
- Codebase Context: existing owners, consumers, integration seams, applicable
  patterns, and enforcement rules referenced at their source.
- Technical Architecture: component responsibilities, interfaces, data flow,
  and the integration approach, without turning delegated internals into new
  product commitments. Follow the approved concept's approach, flow and
  footprint; record a departure as a concept delta in the spec.
- Implementation Strategy: dependency-ordered, independently verifiable
  milestones with Scope, Work, Decisions, and Discoveries.
- Verification and Environment: references to native commands, non-obvious
  setup and recovery notes, and `### Integrated Witness Proposal`. After both
  documents are drafted, propose the complete flow through the real application
  boundary, an e2e lane doubling only external systems, and live whenever the
  feature calls a real provider/service and a run is feasible. Name ACs per lane
  and concrete fallback witnesses for exclusions, deterministic pass conditions,
  real/doubled systems, alignment criteria (observable contract conditions,
  not quality judgments) and retained artifacts. Include
  live prerequisites (targets, credential references, data/freshness, network,
  quota/cost, effects and evidence destination), classified auto-resolvable or
  user-required; effects must be idempotent or reversible with cleanup/recovery.
  Propose stages and bounded reruns for both lanes: each declared lane runs at
  implement exit before review and again by default after review changes. Base
  live time/turn/retry and per-attempt/aggregate cost caps on expected healthy
  cost for both runs, with headroom within host limits. Finishing needs current milestone, acceptance and smoke
  proof under existing disposition rules, plus declared live and alignment
  evidence. Live does not replace acceptance, and assessment targets never gate
  finishing. Planned or unapproved execution remains explicitly pending.
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

Choose ownership as a proof dependency declaration. Product source and tests
belong in `owns`; bookkeeping records already accounted for by coverage need
not be added merely to satisfy coverage. An intentionally owned material
contract input remains byte-strict: changing it makes proof stale. Material
requirement changes still follow Impact Assessment and Re-Plan. Explicit
citations have separate evidence identity even when their files are unowned;
coverage exemptions and ignore rules never filter those checks.

When a milestone reuses or rebinds an inherited boundary, such as a shared
read, a matching rule or a validator, name the boundary in its Work and propose
at the specification checkpoint a feature policy amendment adding a
milestone-review secondary: a second model on the other CLI, which can probe
the real component with reduced limits, non-canonical input order and unusable
inputs. The policy's one milestone-review entry covers every milestone, so that
secondary joins each milestone's review within its `secondary_rounds` window.

## Validate and hand off

Run `heddle validate`. Resolve document/schema failures before reporting readiness:
both documents agree, milestone `satisfies` covers the canonical ACs, and the
authorized/deferred boundary and remaining decisions are explicit. Readiness
uses the confirmed matrix; a disabled gate is intentionally not run, not passed.

## Specification checkpoint

Before any review starts, the owner gets an overview of what was specified and
gives an explicit green light. It is how the owner judges whether the spec and
plan still fit what they meant, stay inside the authorized scope, and touch only
what they should. Present it once `heddle validate` passes, as a short argument
rather than a form: re-answer the approved concept's three questions for what
was actually specified, say "as approved" where it held, lead with the
differences and risks, and add what the concept could not know yet. An
unapproved concept from a driven session is the first exception. A small
feature needs a few sentences.

1. **What are we building?** The core slice as specified, parts changed or
   added since the research checkpoint, and what is deferred.
2. **How are we building it?** The concept's five parts, each as approved or
   with its delta, and where the plan connects to existing code.
3. **How will we know it works?** What must pass for done, the e2e and live
   scenario in plain words with its cost, and anything judged rather than
   checked; Assessment targets never gate completion.

Then check, with evidence:

- **Scope:** whatever does not trace to the authorized parts and the approved
  concept, including growth inside a part. Such an addition defaults to
  deferral.
- **Footprint and complexity:** owned paths by area against the approved
  footprint, contracts changed, new public surface, and structure no AC needs.
  Close with your judgment of whether this is the simplest design that meets
  the ACs.

Record one class-8 `question` titled `Specification checkpoint: <slug>`, with
the overview as its body. It carries scope per part, concept deltas, and the
e2e shape, execution stages, bounded reruns and execution grant. Recommend e2e
iteration during implementation and milestones, both lanes at implement exit,
and a post-review rerun the owner may decline per lane; record that choice so a
later lead can apply it as a standing grant through a witness waiver. When live
is proposed, or declined for a real-provider feature, also record one class-5
`question` titled `Live witness: <slug>`. Bundle the posture and reason, all
user-required prerequisites, allowed effects and cleanup or recovery,
time/turn/retry and per-attempt/aggregate cost caps sized for both runs, and the
live stages and bounded reruns, or the reason and a concrete fallback. Draft
both documents with the recommended posture. Read the allocated decision IDs
back from the workspace `state.yaml` and cite both in the spec's Approved MVP
and the plan's Integrated Witness Proposal. Scope confirmation and credentials
alone are not execution grants.

Both are approvals of prepared work under the Decision routing policy:
resolving them lets the driver advance straight to spec review, so any change,
including narrowing scope or a different live posture, cap or prerequisite, is
made while they stay pending. With the owner present, present the overview and
record the actual ruling, actor and authority. A driven session records the
questions and ends. Do not claim the approval before it is recorded. Follow
native next actions and the caller's phase-exit authority.

When the host or the owner turned this checkpoint off, record in the spec's
Approved MVP that it is off and who turned it off, in place of a ruling ID, and
likewise a review change confirmation the owner skipped. Cite the research
checkpoint's e2e grant in the plan by the research reference that records it,
with its owner, date and bounds. An unapproved concept keeps this checkpoint on,
since it is the owner's only chance to approve that concept. Still hold the
drafting to the two checks: a changed or added part or a concept delta,
including a wider footprint, goes to the owner as one class-8 approval of
prepared work cited in the Approved MVP, and a proposed or declined live lane
still gets its class-5 question.
