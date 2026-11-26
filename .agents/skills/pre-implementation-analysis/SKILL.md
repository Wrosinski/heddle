---
name: pre-implementation-analysis
description: |
  Analyse the current system before a change whose blast radius is larger
  than one module: dependencies, contracts, edge cases, risks, and open
  questions. Produces an analysis document only, no code. Use before planning
  or building such a change.
---

# Pre-Implementation Analysis

Describe the system a change will touch, before anyone builds the change. A
good analysis lets a developer who does not know this codebase see what exists,
why it exists, what is fragile, and what is uncertain, without reading the
code.

## Approach

- **Produce an analysis document.** It covers the code, its dependencies, its
  integration contracts, the risks, and the open questions the code raises.
  Note patterns worth keeping, technical debt you meet, and conventions the
  implementation should follow. Leave out implementation code, task breakdowns,
  time estimates, code-quality grading, redesign proposals, and questions about
  the requirements documents themselves.
- **Look for what nobody wrote down.** Listing files and documented behavior is
  the minimum. The value is in contracts that consumers rely on without a
  written promise, behavior that exists by accident, invariants kept only by
  convention, and concerns that cut across modules. These cause most of the
  breakage in changes that look safe.
- **Give evidence for every entry.** Cite file paths and line numbers. Leave
  out observations that would fit any codebase.
- **Show uncertainty.** Classify each assumption as **verified** (confirmed by
  code or tests), **plausible** (fits the code, untested), or **speculative**
  (no supporting evidence). Only verified assumptions go in without a caveat.
  When something stays unclear after you looked, write "Unable to determine:
  [reason]".

An entry at the expected level of detail:

> **Existing pattern: event ordering.** The `OrderProcessor`
> (order_processor.py:L45-89) emits events through `EventBus.publish()`, and
> consumers assume they arrive in causal order. The order holds only because
> `EventBus` uses a single-threaded synchronous dispatcher (event_bus.py:L12).
> Nothing in the interface promises it, and three consumers (`InventorySync`,
> `BillingTrigger`, `AuditLogger`) would break silently if dispatch became
> async. The change must keep this implicit contract or migrate it openly.

## Steps

1. Find the change scope: the files and modules the change reaches.
2. Look for precedent. Start with `heddle search "<intent>" --titles-only`,
   which finds earlier Feature Spec and pattern sections by content. If the
   command fails, use `docs/features/_descriptions.yaml` and the spec
   filenames. `docs/workflow/document-structure.md` § Knowledge Index defines
   this index.
3. Examine the change through the six lenses below.
   The six lenses look at one change and feed one analysis document; they do
   not gate each other.
4. Write the document in the format under Result, and check it against the
   completion list there.

### The six lenses

- **Codebase.** Cover upstream dependencies (data sources, configuration,
  start-up paths), downstream dependencies (consumers, API callers, later
  effects), patterns and precedent (similar implementations, related tests,
  conventions), and adjacent systems (integration points, external service
  contracts). Look for behavior that exists by accident, contracts between
  modules that no interface or test captures, conventions that nothing
  enforces, and gaps between the documentation and the code. Read related tests
  for the invariants they reveal as well as for coverage.
- **Requirements.** Cover functional requirements, non-functional requirements
  (performance, scalability, security, observability), acceptance criteria, and
  constraints. Look for requirements everyone assumes and nobody stated,
  requirements that contradict current behavior, and requirements nobody can
  measure, such as "must be fast".
- **Edge cases and failure modes.** Cover boundary conditions, error handling,
  recovery, and concurrency. Look for failures that leave an undefined state
  instead of a clean error, failures that spread across integration
  boundaries, and races in state changes that are tested alone and never under
  concurrent load.
- **Integration points.** Cover API contracts (inputs, outputs, errors), data
  flows, external dependencies, and backward compatibility. Look for promises
  this code makes that are not documented, promises it relies on that are not
  guaranteed, and components that run different versions at the same time.
- **Risks.** Cover breaking changes, affected dependencies, migration,
  rollback, and blast radius. Look for risks that appear only at deployment
  (configuration, environment assumptions, feature-flag interactions) and for
  failures that corrupt data silently instead of raising an error.
- **Open questions from the code.** Cover unclear intent, ambiguous behavior,
  unvalidated assumptions, and decisions needed before implementation. Include
  only questions that came from reading the code.

## Delegation

Delegate an independent area, such as a contract trace or a consumer survey,
when a bounded report saves context or time while you continue useful work.
The number of modules a change touches or an unfamiliar codebase alone does
not require parallel agents. Follow
`docs/workflow/subagent-exploration-patterns.md` for how to brief an agent,
which model tier to use, what the tools allow, and how to combine the reports.
Contract traces and consumer surveys are exploration work. You keep the
compatibility judgment, any conflict between recorded intent and current
behavior, and the synthesis.

## Result

Sections with nothing to report say "None found" in one line; length follows
the change footprint.

```markdown
## 1. Executive Summary

[3–5 sentences: core situation, key findings, highest-priority risks or open questions]

## 2. Codebase Context

### Files Examined

[List with brief purpose of each]

### Existing Patterns and Conventions

[Precedents that should guide implementation — with file references]

### Implicit Contracts Discovered

[Behaviors consumers depend on that aren't formally documented]

### Technical Debt and Known Issues

[Problems in the affected areas — with evidence]

## 3. Requirements Analysis

### Functional Requirements

[What the system must do]

### Non-Functional Requirements

[Performance, security, scalability, observability — with measurable thresholds where available]

### Implicit Requirements Surfaced

[What was assumed but unstated]

### Explicit Exclusions

[What is out of scope]

## 4. Edge Cases and Failure Modes

| Scenario | Expected Behavior | Current Handling | Gap |
| -------- | ----------------- | ---------------- | --- |

## 5. Integration Points

[For each boundary: what this code promises, what it depends on, what's documented vs. actual]

## 6. Risk Assessment

| Risk | Likelihood | Impact | Evidence | Mitigation |
| ---- | ---------- | ------ | -------- | ---------- |

## 7. Open Questions

[Prioritized. Each with: what's uncertain, why it matters, suggested resolution approach]

## 8. Assumptions

[Each classified as: verified / plausible / speculative — with evidence basis]
```

The analysis is complete when:

1. Every file in the change scope appears in "Files Examined".
2. Every integration point documents both contract directions: what this code
   promises and what it depends on.
3. Every open question carries a resolution approach.
4. Both the change itself and its blast radius are covered: what changes, and
   what could break because it changes.
