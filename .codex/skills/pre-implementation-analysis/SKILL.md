---
name: pre-implementation-analysis
description: |
  Produce a structured pre-implementation analysis (dependencies, contracts,
  edge cases, risks, open questions) for a change whose blast radius is
  larger than one module. Analysis document only, no code.
---

# Pre-Implementation Analysis

Establish complete understanding of the current system and requirements before any implementation begins. This analysis is foundational — its quality determines everything that follows.

## Scope

<produce>
Structured analysis of the codebase, its dependencies, integration contracts, risks, and code-derived open questions.
</produce>

<note>
Patterns worth preserving, technical debt encountered, conventions that should guide implementation.
</note>

<ignore>
Implementation code, task breakdowns, time estimates, code quality evaluation, architecture redesign proposals, specification-level question discovery across requirements docs and PRDs.
</ignore>

Surface ambiguity rather than making silent assumptions. When encountering an assumption, classify it: **verified** (confirmed by code or tests), **plausible** (consistent with code but untested), or **speculative** (no supporting evidence). Only verified assumptions should inform the analysis without caveat.

## Quality Bar

A good pre-implementation analysis enables a developer unfamiliar with this codebase to understand the full picture — what exists, why it exists, what's fragile, and what's uncertain — without reading the code themselves.

What distinguishes excellent analysis from adequate analysis:

- **Excellent:** Surfaces the _implicit_ — contracts that consumers depend on but aren't formally documented, behavior that exists by accident rather than design, invariants maintained by convention rather than enforcement, and cross-cutting concerns that don't live in any single module. These are the most common source of unexpected breakage in changes that appear safe.
- **Adequate:** Catalogs the _explicit_ — lists files, describes functions, notes what's documented.

### Examples

**Codebase Context — Excellent entry:**

> **Existing Pattern: Event ordering guarantee.** The `OrderProcessor` (order_processor.py:L45-89) emits events through `EventBus.publish()` which consumers assume arrive in causal order. However, this guarantee is accidental — it holds only because `EventBus` currently uses a single-threaded synchronous dispatcher (event_bus.py:L12). Nothing in the interface contract promises ordering, and three downstream consumers (`InventorySync`, `BillingTrigger`, `AuditLogger`) would break silently if dispatch became async. This is an implicit contract that the change must preserve or explicitly migrate.

**Risk Assessment — Excellent entry:**

| Risk                                                                                                                                                                                                                                                                                            | Likelihood | Impact                                                               | Mitigation                                                                                                         |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Changing the `UserSession` schema breaks the mobile app's cached session deserialization. Mobile app pins to schema version via `X-Schema-Version` header (mobile_api.py:L203) but the header is optional — older app versions don't send it and will receive the new schema without migration. | Medium     | High — silent data corruption for ~12% of users on app versions <3.2 | Add schema version detection with fallback. Check analytics for active app version distribution before proceeding. |

**Integration Point — Excellent entry:**

> **Payment Gateway → Order Service contract.** The payment gateway calls `POST /orders/{id}/confirm` with a webhook payload. The contract is documented in `api_spec.yaml` but the actual handler (order_confirm_handler.py:L30-67) accepts two additional undocumented fields (`metadata` and `retry_count`) that the Stripe integration silently depends on. The handler also has a 5-second timeout that isn't in the spec. Any change to this endpoint must preserve these undocumented behaviors or coordinate with the payments team.

## Analysis Framework

The six phases below are lenses on one change and feed a single analysis document; they do not gate each other.

### Phase 1: Codebase Immersion

Coverage is defined by the Completion Bar below. Focus on: behavior that exists by accident rather than design, implicit contracts between modules that aren't captured in interfaces or tests, patterns that are followed by convention without enforcement, and the gap between what documentation says and what the code actually does. Review related tests not just for coverage but for what invariants they reveal about expected behavior.

### Phase 2: Requirements Analysis

Understand what must be built — functional requirements, non-functional requirements (performance, scalability, security, observability), acceptance criteria, and constraints.

Focus on: implicit requirements that everyone assumes but hasn't stated, contradictions between stated requirements and existing system behavior, and requirements that are technically stated but practically unmeasurable ("must be fast," "should scale").

### Phase 3: Edge Cases and Failure Modes

Anticipate problems — boundary conditions, error handling, recovery behavior, concurrency considerations.

Focus on: failure modes where the system enters an undefined state rather than a clean error, cascading failures across integration boundaries, and race conditions in state transitions that are tested in isolation but not under concurrent load.

### Phase 4: Integration Points

Map all system boundaries — API contracts (inputs, outputs, errors), data flows, external dependencies, backward compatibility requirements.

Focus on: promises this code makes to consumers that aren't formally documented, promises this code depends on from others that aren't formally guaranteed, and version skew scenarios where different components are at different deployment versions simultaneously.

### Phase 5: Risk Assessment

Identify what could go wrong — breaking changes, affected dependencies, migration implications, rollback feasibility, blast radius.

Focus on: risks that are invisible at the code level but manifest at deployment (configuration dependencies, environment assumptions, feature flag interactions), and risks where the failure mode is silent data corruption rather than a visible error.

### Phase 6: Code-Derived Open Questions

Surface all uncertainty discovered during codebase examination — unclear intent, ambiguous behavior, unvalidated assumptions, decisions that must be made before implementation.

This phase captures questions that emerged from reading the code, not specification-level question discovery (see Scope).

## Completion Bar

The analysis is complete when:

1. Every file in the change scope appears in the "Files Examined" list — a missing file means incomplete immersion.
2. Every integration point documents both contract directions (what this code promises, what it depends on) — one-sided contract analysis misses the most dangerous breakage.
3. Every open question carries a resolution approach — a question without a resolution path is an observation, not an actionable finding.
4. Both the change itself and its blast radius are covered — not just "what we're changing" but "what could break because of what we're changing."

## Parallel Sub-Agent Analysis (Complex Scope)

When the change touches three or more distinct modules, dependency chains are deep, or the codebase is unfamiliar and broad exploration is needed, fan out sub-agents for parallel immersion (an agent team only if the explorers must challenge each other directly).

### Decomposition

Pre-implementation analysis has a natural two-stage structure:

**Stage 1 — Parallel Codebase Immersion (Phase 1):** Spawn Explore agents to examine different areas simultaneously. Natural splits:

- Upstream dependencies — data sources, configuration, initialization paths
- Downstream dependencies — consumers, API callers, downstream effects
- Patterns and precedent — similar implementations, related tests, conventions (lead with `heddle search "<intent>" --titles-only` to surface prior Feature Spec and pattern sections by content match before scanning directories; on command failure, fall back to `docs/features/_descriptions.yaml` and spec filenames; canonical contract: `docs/workflow/document-structure.md` § Knowledge Index)
- Adjacent systems (if needed) — integration points, external service contracts

Each agent reports: files examined, key findings, patterns observed, questions encountered.

**Stage 2 — Synthesis and Phases 2–6:** After Stage 1, synthesize immersion findings, then work through the remaining phases with the full context assembled. These later phases benefit from a single analyst holding the complete picture.

### Briefing and Synthesis

Brief and synthesize per `docs/workflow/subagent-exploration-patterns.md` (§ Model tier, § Spawn Prompt Design Principles, § Synthesizing Fan-Out Findings). Stage 1 contract traces and consumer surveys are exploration work and run at the exploration tier; the compatibility judgment, the conflict between recorded intent and current behavior, and the Stage 2 synthesis stay with the caller. Task-specific: synthesize the Stage 1 briefs into a unified codebase map, then use it to work Phases 2–6 with depth a single pass couldn't achieve.

## Output Format

Keep analysis entries specific and evidence-based. Reference file paths and line numbers. Avoid generic observations that could apply to any codebase. Sections with nothing to report say "None found" in one line; length follows the change footprint.

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

[Landmines in affected areas — with evidence]

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

## Constraints

- Produce analysis, not implementation code
- Do not gloss over uncertainty — surface it explicitly with classification
- Base analysis on verifiable evidence; flag speculation as speculation
- If something remains unclear after examination, state "Unable to determine: [reason]" rather than papering over it
