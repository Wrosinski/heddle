---
type: feature-spec
area: {{area}}
feature_name: {{slug}}
lifecycle: active
---

# {{slug}}

## Purpose

_(Who this serves, what they can do afterwards, and why that outcome matters.)_

## Approved MVP (from brief)

_(Retain the Approved Scope Decomposition verbatim as provenance. Distinguish
the authorized core, named deferred work, and any scope delta. Cite the
specification-checkpoint decision ID as the owner's approval once it is
recorded, with any later ruling that amends it.)_

### Approved concept

_(Retain the approved concept's "How are we building it?" answer verbatim as
provenance, with its approval reference. Then label the concept delta: none, or
each departure in approach, flow, contract classification, state ownership or
footprint, marked for confirmation at the specification checkpoint. The
sections below elaborate the concept rather than restate it; its other answers
feed them.)_

### Implementation Parts

| Part | Scope and benefit | Complexity | Authorization |
| --- | --- | --- | --- |

_(Authorization is per part, not blanket. Mark a changed part as requiring
authorization; do not silently replace its approved shape.)_

### Conceptual end-to-end outline

_(How the authorized parts work together and what an integrated witness must
observe. State e2e-only or e2e-plus-live posture and why; concrete witness design
belongs in the plan. This is an outline, not a second acceptance-criteria inventory.)_

## Conceptual Design

_(Define the terms, observable interactions, labeled assumptions, invariants,
and failure classes that change a decision. Distinguish required edge behavior,
deliberately excluded behavior, and unresolved owner choices.)_

## Design Commitments

_(State the data model, owners, dependency direction, integration and extension
seams, consumer-facing promises, and expensive-to-reverse choices. Cite relevant
repository principles. Include a signature only when a consumer depends on it;
explicitly delegate reversible helpers, layouts, and algorithms.)_

## Acceptance Criteria

_(Author one canonical list using level-3 `AC-<n>: <title>` headings. Each
criterion carries `Priority: MUST|SHOULD|MAY`, observable precondition/action/
expected-result clauses, and `Verified-by:` routes once executable witnesses
exist. Until then, label verification pending; never invent an executed pass.
Cover consequential inputs, outputs, and failure classes without prescribing
private implementation details or a parallel EARS list. Every pass condition is
checkable deterministically or by inspection against observable terms; a target
that needs subjective or expert judgment belongs under Assessment Targets.)_

Use the workflow's named Impact Assessment and Re-Plan protocols for changes
to this contract; do not copy their procedures here.

## Assessment Targets

_(Optional. List each target whose evaluation needs subjective or expert
judgment, such as the analytical quality or usefulness of an output or an
assessor's grade, with what it evaluates and the follow-up work that owns it.
These are not acceptance criteria and never gate completion; record any
assessment made during this feature as an observation.)_

## Decision Log

_(Record consequential alternatives, the choice, and its decisive trade-off.
Checkpoint rulings and review bookkeeping belong in the native decision
journal, not a second operational ledger.)_

## Surprises & Discoveries

_(Record evidence that changes the design or follow-up scope; link its native
decision or implementation record when one exists.)_

## Outcomes & Retrospective

_(At completion, summarize delivered value, remaining gaps, and useful lessons.
List each open assessment target with its owning follow-up and the greenlight /
hold / drop call made for it at the final checkpoint. Link the retained close
evidence instead of copying execution logs here.)_
