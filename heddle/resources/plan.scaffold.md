# Implementation Plan: {{slug}}

Judgment prose for the {{slug}} workspace; operational facts live in
`state.yaml`. Milestone prose sections join this document as level-3
`Milestone <id>:` headings when `milestone add` records the first
skeleton entry (validate enforces the id-join).

## Quick Orientation

<!-- heddle:begin plan-status -->
Status block not yet projected — run `heddle sync --feature {{slug}}` to
populate this managed region. Operational facts live in `state.yaml`,
which is authoritative.
<!-- heddle:end plan-status -->

_(State the approach, sequencing rationale, important uncertainties, and
reading pointers to the spec's commitments and ACs. Started {{date}}.)_

Record changing progress with native task/session commands and read it through
`heddle status`. Use `heddle sync` for the managed region above. Authored prose
changes affect review freshness; update it for genuine design corrections,
with fresh evidence at the applicable boundary, rather than routine progress.

## Codebase Context

_(Identify existing owners, callers, affected modules, reusable patterns, and
integration boundaries. Reference applicable enforcement rules by name at their
owner; do not copy a second rules register.)_

## Technical Architecture

_(Explain module responsibilities, interfaces, data flow, and the concrete
integration approach. Honor the spec's commitments and leave delegated
internals open where several implementations meet the same contract.)_

## Verification and Environment

_(Record non-obvious setup and recovery notes where a step can destroy or
overwrite data. Start from the host's chosen test budget. Select core behavior,
uncertain or risky interactions, consequential failures, fast feedback and a
fast public-boundary acceptance route at the cheapest adequate layers. Keep
packaging, local e2e and live proof separate. For a shared fixture, inspect its
first-order consumers and select representative cross-consumer coverage instead
of defaulting to the full suite. A plan is not execution authority.)_

### Integrated Witness Proposal

_(After drafting spec and plan, propose the complete user-visible flow through
the real application boundary. The e2e lane doubles only external systems;
propose live too whenever real providers/services, representative input,
obtainable credentials, bounded cost and safe effects make it feasible. Name
ACs per lane and concrete fallback witnesses for exclusions, deterministic pass
conditions, real/doubled systems, alignment criteria and retained artifacts.
List prerequisites as auto-resolvable setup or user-required decisions, effects
and idempotency or reversible-effect cleanup/recovery. Propose execution stages
and bounds for both lanes; give live time/turn/retry and per-attempt/aggregate
cost caps expected to accommodate healthy runs with headroom.)_

_(Draft at specify; the lead records the witness batch and obtains its ruling at
Checkpoint 1 after spec review, including when the reviewer is Off. The scope
ruling includes explicit e2e execution authority for every posture; live or
declining live for a real-provider feature has a bundled class-5 question.
Reference exact native decision IDs and grants here. Unresolved owner choices
block progression; scheduled auto-resolvable setup may remain a task. Scaffold
binds exact commands within the grant and routes material changes as class-2
questions naming the original ruling. The proposal itself grants no execution.)_

_(Finishing needs current milestone, acceptance and smoke proof under existing
smoke-disposition rules, plus declared live proof and alignment assessment.
Live does not replace acceptance; an unrun required lane stays pending.
For existing active work, reconcile design and prior grants without fabricating
a retrospective checkpoint or restarting completed stages.)_

### Verification Commands

_(Reference the native command facts and owned source declarations. Explain
expected evidence and failure diagnostics without maintaining copied commands.
For each route, record its class, lifecycle/setup and total expected cost. If
required proof exceeds its budget, record a measured, scoped exception with a
cause, owner and review point. Separate planned, authorized, executed, and
current proof.)_

_(When an alignment check was declared, this verification note owns one
Assessment entry: criteria, assessor, native run/source reference, exact retained
artifacts, conclusion, limitations and separate quality observations. The final
peer-review/robustness lead records it after the last relevant fix and applicable
passing run, refreshing after changed output or relevant changes. Misalignment
blocks; quality beyond the contract does not. Session/spec summaries reference
this entry. Judgment never substitutes for a verification fact. This is authored
plan content: record it before a required qualifying final rereview, or refresh
affected closure evidence afterward. Native freshness and originating-inspection
requirements still apply.)_

### Live E2E Test Prerequisites

_(Reference applicability, prerequisite design and native rulings from the
Integrated Witness Proposal. Record setup status and material changes here
without duplicating design or grants. Separate scheduled auto-resolvable work
from unresolved owner choices, which block progression. Execution deferral leaves
required proof pending; changing the obligation needs an explicit contract ruling.)_

### AC Coverage Matrix

_(During scaffolding, map canonical AC IDs to milestone, acceptance, and
applicable live witnesses. Reference the spec's `Verified-by:` routes; do not
copy the acceptance clauses. Distinguish coverage design from executed proof.)_

## Implementation Strategy

_(Explain dependency-ordered, independently verifiable milestones. Use prototypes
to resolve consequential uncertainty. Record `satisfies`, `owns`, dependencies,
Low/High complexity, and verification through native milestone commands; hour
estimates and fixed task counts are not required. Each appended milestone's
Scope, Work, Decisions, and Discoveries explain approach without copying ACs.)_
