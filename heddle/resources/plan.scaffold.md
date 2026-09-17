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

### Verification Commands

_(Reference the native command facts and owned source declarations. Explain
expected evidence and failure diagnostics without maintaining copied commands.
For each route, record its class, lifecycle/setup and total expected cost. If
required proof exceeds its budget, record a measured, scoped exception with a
cause, owner and review point. Separate planned, authorized, executed, and
current proof.)_

### Live E2E Test Prerequisites

_(State applicability and any required data, credentials, cost, or effects.
Separate auto-resolvable setup from user-required choices and execution grants.
Retain unresolved or unrun required proof as pending.)_

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
