# Brainstorming & Specification Guidelines

<!-- Current guidance for Heddle's native workflow surfaces. -->

Guidelines for Phase 1 (Research & Brainstorming) and Phase 2
(Specification) of the agent-driven development workflow. Referenced
by the native specify briefing and the `/new-feature` skill.

## Product scope and build order

These guidelines define Heddle's product scope and build-order posture. They
inform the scope proposal and owner decisions; they do not add a second
ratification mechanism or authorize a reviewer to settle unresolved product
intent. Engineering tradeoffs remain governed by
[Engineering Principles](engineering-principles.md).

- **Simplest working system first, end to end.** Put the smallest complete
  path into real use before deepening a component or adding another path.
  Each later increment is again the next smallest working slice, earned by
  observed use. A complete path exposes a wrong concept while change is cheap;
  a polished partial system can hide it. Name what the first slice leaves out
  in the spec. Engineering's "Core first; scope is earned" owns the time budget
  and the burden of proof on additions.
- **The reversibility test.** Give expensive-to-reverse and trust-shaping
  decisions full treatment in the first spec. Defer other cases only when
  their failures can surface, correction is cheap, and the conceptual core
  is stable. A fail-loud error, useful log, metric, or feedback channel can
  make a deferred case observable; without one, deferral creates an
  observability requirement. A discovery that invalidates the product concept
  returns to its owner instead of becoming a quiet pivot.
- **The three-way edge-case split.** Distinguish reversible, observable
  technical misbehavior from product behavior that looks like an edge case
  (empty states, cancellation, concurrent conflicts), and from irreversible
  or trust-destroying failures (data loss, privacy, false completion). The
  first class may defer; the owner decides the second; the third belongs in
  the first slice's contract.
- **User-owned decision classes.** Route unresolved product direction,
  user-visible behavior, trust, money, privacy, data-loss exposure, and scope
  changes through the existing user-decision mechanism. Prior authorization
  still applies; do not ask again for a decision already made. Outside these
  classes, use engineering's "Reversibility beats optimality" for the smallest
  defensible interpretation, recorded with its rationale and flagged for review.
- **The domain boundary.** In Heddle, the first slice must preserve runtime
  write ownership, authorization, recoverable state, and truthful evidence
  and completion. Thin means fewer supported paths, each meeting its full
  contract. Convenience features may defer; a thinner feature cannot weaken
  state transitions or verification provenance. This boundary and the
  engineering Inviolables take precedence over deferral.

Source-name map: "Simplest thing that works well" becomes "Simplest working
system first, end to end"; "Observability is the deferral license" and
"Conceptual correctness on day one; evolutionary correctness thereafter"
are preconditions of "The reversibility test".

---

## Phase 1 — Research Guidelines

These apply during problem understanding, related-spec discovery, and
risk assessment — before any design decisions are made.

Use the [testing strategy](testing-strategy.md) to identify existing contract
witnesses, exact affected selections and expensive shared dependencies. Research
does not require a broad baseline run. Plan required broad proof and local
e2e/live execution separately from the focused implementation loop.

**Discover before designing.** First run
`heddle search "<intent>" --titles-only` against the knowledge index to
surface candidate Feature Spec and pattern sections by content match. This is
the first discovery action, before scanning any directory listing (canonical
contract: `docs/workflow/document-structure.md` § Knowledge Index). Then read
the specs relevant to the intended outcome, shared contracts, and uncertainties
that could change the design. Choose by relevance rather than a fixed count. If the command
fails, scan `docs/features/_descriptions.yaml` and spec filenames as the
failure fallback. Features that seem unrelated often share infrastructure,
patterns, or hard-won lessons.

**Extract lessons from related specs.** For each related spec, read
the Decision Log (decisions that inform or constrain this feature),
Surprises & Discoveries (pitfalls and edge cases that may recur), and
Design Commitments (historical specs: Architecture) for reusable patterns and
established conventions. Check
the `related` frontmatter for further specs to examine.

**Assess independent axes after research.** Record scope (small/medium/large),
complexity (low/high), and testability (full/partial/none), each with its rationale.
Use actual consumers, uncertainty and feasible evidence, not hours or counts.
The native intake recommends a review matrix; it does not approve one. Surface
the Direct/Heddle choice and, for Heddle, the complete policy for explicit user
confirmation. Sensitive or irreversible effects inform the contract and choices;
they do not silently restore a retired tier override or grant execution authority.

**Frame from the user's perspective.** Start with: what can someone
do after this change that they couldn't do before? This anchors
everything that follows and prevents solutions that are technically
sound but don't address the actual need. Answer at two levels: the
smallest end-to-end slice worth shipping (the MVP), and the full
intended scope.

**Decompose into an MVP and follow-ups.** Apply
[Product scope and build order](#product-scope-and-build-order). Split the feature into the
smallest end-to-end increment worth shipping (the MVP — a walking
skeleton someone can start using) and the follow-up increments that
extend it. Record follow-ups by name with a one-line deferral rationale
and the additive seam that keeps each one additive; do not design them.
Judge whether decomposition is even warranted — cohesive, bounded
features ship as a single increment; large, multi-capability, or
high-uncertainty features split. This is a soft discipline: the goal is
to start using the feature sooner and defer the cost of specifying
scope we may not need, not to force every feature into increments. The
proposed decomposition is always surfaced to the user as an explicit
decision — at the Phase 1 checkpoint, and again at Checkpoint 1 after
spec review — never silently assumed.

**Present the Implementation Parts for per-part authorization.** At the
Phase 1 checkpoint, the Scope Decomposition is presented as a per-part
table — each part with a clear description, a scope-and-complexity
assessment, and the benefit / core functionality it delivers — and
**each part requires explicit user authorization before it may be
implemented**. Authorization is per part, not blanket; a part without
authorization defaults to Deferred Scope. The same table, re-assessed
against the drafted spec, is recorded in the spec's Approved MVP block
(`docs/workflow/document-structure.md` §2) and re-confirmed at
Checkpoint 1 (parts often change shape between brief and spec — which
is exactly why authorization happens at both checkpoints).

**Identify unknowns early.** Which aspects are well-understood and
which carry uncertainty? Uncertain areas become candidates for
prototyping milestones in Phase 2.

---

## Phase 2 — Specification Guidelines

These apply during Feature Spec and Implementation Plan creation —
after the research checkpoint is confirmed. That Phase 1→2 checkpoint
is the original **Phase Transition Gate**. For later transitions, follow
`heddle orient` and the current stage briefing: interactive authorization,
driver-owned transitions, and already-recorded decisions have different
routes. A phase boundary is not a new request for permission when its
authorization already exists. Unresolved user-owned scope decisions still
require resolution, and completion remains human-owned.

**Write for a completely fresh agent.** The agent reading this spec
has never seen your brainstorming session and knows nothing about the
codebase except what it can read from the repo. Everything it needs
must be in the spec or pointed to from the spec.

**Scope by exclusion, not just inclusion.** State what the feature is
NOT. Positive scoping ("the feature does X") leaves ambiguity at the
boundaries. Negative scoping ("this is NOT P&L attribution") closes
the gap.

**Label assumptions explicitly.** In the Conceptual Design, mark
every assumption that must hold for the design to be valid. These
become the triggers for impact assessment during implementation.

**Surface domain challenges before proposing architecture.** When the
domain imposes fundamental challenges, explain the challenge before
the architectural response. Agents that understand WHY an
architecture has a particular shape are less likely to optimise away
its essential properties.

**Explain the problem that shapes the solution.** Map each structural
mitigation to the specific threat it defends against. Either listed
alone is fragile.

**Make the argument clear.** Explain the connection between evidence and
the design choice. Use prose for causal connections and trade-offs, and lists
or tables for parallel obligations, alternatives, or comparisons. Preserve the
required Acceptance Criteria format. Apply the
[argumentation standard](../../heddle/resources/model-prompting-principles.md#argumentation-standard).

**Resolve consequential commitments; delegate reversible internals.** Specify
observable behavior, consumer promises, and expensive-to-reverse choices clearly.
Use engineering principles for routine technical choices; surface user-owned
intent or conflicting contracts through the existing decision route. Explicitly
delegate internal choices when the permitted alternatives meet the same contract,
so the implementer can choose with better local evidence.

Ownership and dependency direction are consequential commitments: apply "One
owner per authority" and "Dependencies point inward, not back out" by name from
the ratified principles. State extension seams where they affect consumers;
"Extend through data and one resolver, not parallel paths" guides their shape.

**Decompose goals, not just purpose.** When a feature serves multiple
objectives, decompose them into named goals within the Conceptual
Design. Purpose anchors the feature in user value. Goals tell the
agent how to weigh trade-offs.

**Reference existing enforcement rules.** Cross-check against Active
Enforcement Rules in the enforcement baseline. Don't re-specify
conventions that are already mechanically enforced — reference the
rule instead.

**Specify consumer promises, delegate internal choices.** Types and signatures
belong in the spec only when they are consumer-facing contracts. The plan owns
the technical architecture and approach; neither document needs a file-by-file
implementation recipe or a second Acceptance Criteria list.

**Immerse in the codebase before writing the spec.** Before creating
the Feature Spec, examine the actual code areas the feature will
touch. Read existing modules, trace dependencies upstream and
downstream, identify integration points with their real types and
contracts, surface implicit contracts, and note existing patterns.
Specs that describe code without the author having read that code
produce design commitments and technical architecture that don't match reality.

**Read the workflow and document structure references before writing.**
Before creating any Feature Spec or Implementation Plan, thoroughly
read both `docs/workflow/workflow.md` (Phase 2 process, principles
for writing Feature Specs, intake axes and confirmed policy) and
`docs/workflow/document-structure.md` (authoritative templates, YAML
header fields, distinct document owners and cross-document protocols).
Read the actual scaffold templates they reference. These documents define the
structure, content expectations, and quality bar. The documents you
create must align with their principles and templates — do not rely
on abbreviated summaries alone.

---

## Feature Brief Format

A lightweight markdown document that seeds Phase 1-2. Lives in
`plans/.briefs/`, committed to git. Feature name derived from the
filename (authoritative when it disagrees with the heading).

Expected headings (not schema-enforced — missing fields are handled
interactively by the agent):

```markdown
# Feature Brief: [feature-name]

## Area

[target area in docs/features/]

## Problem

[What's wrong today? What can't users do?]

## Desired Outcome

[What should users be able to do after this ships?]

## Scope Decomposition

The smallest end-to-end version worth shipping, and what is deferred.
Recorded here, refined in Phase 1, and pressure-tested at Phase 3 spec
review — not binding.

### MVP / Core Increment

[One short paragraph: the smallest end-to-end slice that delivers usable
value — the walking skeleton someone can actually start using.]

### Implementation Parts

[The per-part authorization table presented at the Phase 1 checkpoint.
Each part requires explicit user authorization before it may be
implemented; a part without authorization defaults to Deferred Scope.]

| Part   | Description  | Scope & complexity | Benefit / core functionality delivered |
| ------ | ------------ | ------------------ | -------------------------------------- |
| [name] | [what it is] | [assessment]       | [what it delivers]                     |

### Follow-up Increments

[Named, not designed. One line each: description, why it's deferred, and
the additive seam (the interface/extension point in the MVP it slots
into without a rewrite). Mark each **Important** or **Optional**.]

### Decomposition judgment

[One line: cohesive enough to ship as a single increment, or warrants an
MVP + follow-up split?]

## Initial Thinking

[Rough ideas, architecture hunches, known constraints,
open questions.]

## Scope Boundaries (optional)

[What this is NOT — permanent exclusions, distinct from the deferred
follow-ups in Scope Decomposition, which are out for now, not forever.]

## Related Features (optional)

[Features you know are related.]
```

Begin with research and a durable brief, before formal document creation.
Use `heddle feature prepare` with the research reference, assessed axes and the
user's explicit route choice. Direct creates no formal feature documents or
review authority. For Heddle, record the user's complete confirmed selection
through `heddle feature policy`, then `heddle feature start`. Consult native help
for the versioned payloads rather than hand-writing operational state.

After admission, enter through `heddle orient` and invoke `heddle kickoff` only
when its next action routes there. The delivered specify briefing owns the
session's work. Existing phase authority applies; creation does not authorize
unapproved scope, broad tests, installed journeys or provider comparisons.
