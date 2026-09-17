---
name: new-feature
description: |
  Kick off a new feature through Phase 1 (Research & Brainstorming) and
  Phase 2 (Specification). Takes an optional feature brief path. Use when
  starting a new feature from scratch within the current session.
---

# New Feature Inception

Enter Heddle's native intake and, when selected, its feature workflow. This
skill is a launcher, not a second policy resolver or copy of the workflow.

## When to Use

- Starting genuinely new feature work from a verbal request or written brief
- Discovering related specifications and deciding the smallest useful scope
- Producing the Feature Spec and Implementation Plan for a confirmed Heddle route

For an in-progress feature, resume the existing workspace instead of starting
another one. For analysis without feature inception, use
`/pre-implementation-analysis`.

## Invocation

`/new-feature [brief-path]`

Read a supplied brief before entering the workflow. Keep the original research
reference intact; formal admission binds its identity. After admission, enrich
the generated `plans/<slug>/brief.md` without rewriting the approved provenance.

## Native Entry Sequence

1. Run `heddle orient --json`. If it resolves existing work, follow its emitted
   routing and resume that feature; do not create a duplicate workspace.
2. For new work, research the intended outcome and actual integration surface
   under `docs/workflow/brainstorming-guidelines.md`. Retain a research reference
   and propose the route plus scope, complexity, and testability with reasons.
3. Use `heddle feature prepare <slug> --area <area> --from-file <research-input>`
   to record the intake. Native help defines its input contract. Preparation
   creates no formal feature, and its recommended matrix is not approval.
4. Confirm the user's route. Direct work creates no feature workspace, spec,
   plan, or review schedule. For the Heddle route, present the complete resolved
   matrix and record the user's actual confirmation with `heddle feature policy
   <slug> --from-file <confirmed-policy> --expect-revision <N>`. Do not infer
   approval from the recommendation or rewrite host exclusions.
5. Only after formal policy confirmation, run `heddle feature start <slug>`.
   Follow returned `next_actions`, including a feature switch when emitted;
   do not infer an alternate transition or introduce a tier argument.
6. Run `heddle orient --feature <slug> --json`, then `heddle kickoff --feature
   <slug>` only when orientation routes there.

## Research and Specification Contract

For an admitted feature, kickoff and the packaged specify briefing own the
current stage. The research checkpoint is distinct from Checkpoint 1 after
spec review. Honor already-recorded research and scope choices; reopen them
only for material new evidence. This skill adds no separate approval step.

Use these sources for their respective contracts:

- `docs/workflow/brainstorming-guidelines.md`: research, decomposition,
  consequential commitments, and per-part scope authorization;
- `docs/workflow/workflow.md`: confirmation, execution authority, and lifecycle;
- `docs/workflow/document-structure.md`: spec commitments and canonical ACs,
  plan approach and technical architecture, and the knowledge index; and
- `heddle search "<intent>" --titles-only`: related specs and precedents
  before broad directory scanning.

Use independent exploration only when available, authorized, and useful beside
local work. Synthesize its evidence into one research result.

Run `heddle validate` at the briefing's checkpoints. Advance only through
`heddle phase-exit` or the driver when the runtime and execution authority
permit it. Required decisions and authorization stops remain mandatory.

## Constraints

- Never hand-edit `state.yaml` or intake records; native commands own them.
- For a Heddle route, this skill authors research and specifications, not
  production implementation. Direct work follows the user's scoped request.
- Keep one canonical document pair and one native progress owner.
- On a native refusal, follow its exact diagnostic and legal remedy; do not
  substitute a remembered legacy procedure or silently fall back to a provider.
