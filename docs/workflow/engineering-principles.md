---
status: ratified
ratified: 2026-07-27
amended: 2026-09-16
---

# Engineering Principles

_Status: ratified. This public copy contains the current principle set._

The owner's preference profile: what wins when two defensible goods conflict. An agent resolving an open question honors these as its owner's judgment. This document is the **primary source for engineering preference** in this repository: whenever multiple defensible options exist — a design choice in Phase 1–4, a REPORT finding or reviewer contradiction at a gate, an implementation decision or Discovery in Phase 6, a pattern proposal in Phase 9 — consult it before escalating. `AGENTS.md` retains only mechanical bindings (tool choices, commands, repo facts) and defers to this document on preference.

The content is deliberately subjective — anyone adopting this document edits their copy to match their own preferences. It covers engineering preference only: not product, UX, or domain analysis decisions (scope and build order are covered in `docs/workflow/brainstorming-guidelines.md`), and not code style or quality thresholds (those are `heddle/resources/prompt-authoring-standards.md#code-quality-checklist`).

Inviolables are hard — an option that violates one is out, whatever its other merits. The ranked principles are weighed judgment: the ordering gives relative weight when two conflict, not a lookup table. Architecture entries say how structure is chosen when a change adds or reshapes components; a ranked principle overrides them. Defaults apply unless a principle overrides. When the principles are silent or tie, prefer the option that builds less now.

How resolutions are made, recorded, and escalated: cite the single decisive principle and record the resolution where decisions live. Stretching a principle or ignoring a higher-ranked conflict is not a resolution. Genuine conflicts, irreversible or precedent-setting choices, and amendments to ratified contracts escalate, whatever the principles otherwise favor.

## Inviolables

- **Never misrepresent.** Surfaces, claims, and verification reflect what is actually true: done means evidence, checks are never weakened to pass, placeholders are tracked and never presented as product.
- **Fail loud.** No silent clobber, skip, truncation, fallback, or unbounded retry; every degradation is visible in an artifact or an exit code.
- **Point-in-time integrity.** No computation reads data that was unknowable at its as-of time; temporal leakage is silent corruption of every downstream result, not a recoverable error. Where structural enforcement is impossible, the exception is explicit, author-trusted, and audited.
- **The governing record leads.** When work contradicts its spec or a ratified decision, stop and surface the conflict — never silently adapt either side. The same route serves a requirement whose cost is out of proportion to its purpose: challenge it with its owner as a Discovery or gate finding before building, rather than silently complying or silently dropping it.
- **Stay inside owned scope.** Record and route out-of-scope discoveries; never bundle drive-by fixes, however small.
- **Destruction requires proven success.** Delete or overwrite only on recorded evidence; prefer recoverable forms — move before delete. Another stream's unfamiliar recent changes are work in progress, not debris: patch forward, and never roll them back unasked.
- **Secrets never touch code, logs, or artifacts.** Reference them by name from the environment or a secret store.

## Principles, ranked

1. **Correct beats simple; simple beats general; general beats clever.** Optimize for the reader's reasoning cost — correctness is rank zero.
2. **Core first; scope is earned.** At every level — brief, spec, plan, code — build the smallest end-to-end slice that delivers the purpose and reach real use with it soonest: a slice in real use teaches more than a slice under review, and observed use, not anticipated need, decides what deepens next. Additions beyond the slice carry the burden of proof and default to named, deferred follow-ups; no generality until real callers exist; name future seams without building them. A documented manual step is a valid implementation until recurrence proves the shape worth automating. Decide up front what the work is worth in time; when it outgrows that budget, the slice shrinks — the floor does not, time extends only by owner decision, and cuts become named follow-ups.
3. **Simplicity first; the contract sets the robustness floor.** The governing contract (the AC's named inputs, edges, and failure modes; the spec; the caller's promise) defines the robustness that is required — meeting it is part of correct, never optional polish. Beyond it, simplicity wins: no speculative guards, no defenses for failure modes nothing names; out-of-scope concerns route as Discoveries. Between two contract-meeting options, take the simpler, even when the other is more robust.
4. **Reversibility beats optimality.** Take the option cheapest to undo; when intent is ambiguous, take the smallest defensible interpretation and flag it for review.
5. **Data first; code follows.** The data model is the most expensive thing to change, and a right model makes the code obvious.
6. **Structural beats policed.** Make bad states impossible rather than guarding against them; enforce at the contract that defines the state, not in a downstream review; when a root cause dies, delete its guard. Structure earns its place by deleting guards, not by adding layers.
7. **Everything once, in one place.** One source of truth per fact, one spelling per concept, one handler per failure class; derived copies are generated or locked, and conventions are extended rather than forked. Facts unify immediately; similar code unifies when the shared shape is proven — duplication is cheaper than the wrong abstraction.
8. **Pure core, effects at the edges.** Deterministic logic over data; I/O, time, randomness, and subprocesses in a thin shell around it. Effects inside the core are what make tests non-hermetic and failures non-reproducible.
9. **Proven tools beat bespoke machinery.** Research before building: a well-maintained, dedicated solution beats building from scratch — if it is production-grade and removes more complexity than it adds.
10. **Design for the docs-blind consumer.** Every surface usable by a consumer that has read nothing but the surface itself: discoverable contracts, errors that name what happened, the relevant non-sensitive input or field, and the next legal move.

## Architecture

How structure is chosen when a change adds or reshapes components. Unordered; a ranked principle overrides. Each entry names the failure it prevents, because each was paid for once in this repository before the rule existed.

- **One owner per authority.** Every fact, decision, and state transition has one component that decides it and one that publishes it; every other component consumes it through that owner's interface. A copy that needs a drift test to stay aligned is a second owner, not a cache. A temporary second owner is a named migration step with its removal recorded, not a steady state. Prevents constants duplicated across a boundary and a ledger and a document each claiming the same progress.
- **Dependencies point inward, not back out.** Layers depend toward the stable core — vocabulary, then pure derivation, then effects, then supervision. A lower layer does not reach up through a callback or injected closure to do the upper layer's work, and a boundary is not dodged by assembling the forbidden name at runtime. When a rule needs a bypass, the layering is wrong, not the rule. Direction is enforced by a structural test, not by review.
- **Small surface, private everything else.** A unit's public contract is describable in a few sentences: the types, commands, or files it promises and nothing more. Expose the minimum; a forwarding facade that adds no decision is a surface without a reason, and an exported helper is a contract someone will depend on. Design for the docs-blind consumer says what the surface must explain; this entry says how little of it there should be.
- **Extend through data and one resolver, not parallel paths.** A new variant — a gate, mode, lane, provider, or wire version — registers as data against the existing resolver rather than adding a branch cascade or a second state machine that can drift from the first. Name the seam before the second variant exists; build it when that variant arrives (Core first owns the timing).
- **One responsibility per unit; assembly in one place.** A long procedure is a pipeline of pure, named steps with one orchestrator and one failure mapper. A function that resolves, budgets, executes, records, and projects has five reasons to change and no test that isolates one of them. Pure core, effects at the edges says where the effects go; this entry says how the steps are joined.

## Defaults

Unordered; a ranked principle overrides.

- Validate configuration and contracts at startup, not at first use, so a misconfiguration fails before any work depends on it.
- Least privilege everywhere; external input is untrusted until parsed — a boundary that assumes good input has no way to fail loud.
- Exceptions propagate. A handler recovers or adds boundary context and re-raises; it never suppresses an unexpected failure (the Fail loud Inviolable applied to handlers). Destructive or corrective remedies discriminate the failure subtype before acting, because a signal that pattern-matches a known failure may have a different cause.
- Backwards compatibility is opt-in: prefer the clean contract, announce the break, and migrate callers in the same change; a speculative compatibility shim is generality without a caller.
- Tests are hermetic and deterministic by default — external dependencies are controlled at their boundary and fixtures are deterministic; live proof is explicit, marked, and opt-in. A test that depends on the world proves the world, not the code.
- When outcomes are asymmetric, require the caller to choose explicitly; a silent default may only pick the safe side.

## Editing

A preference profile, not engineering truth — disagreeing with this copy is not an error; edit it. Keep entries short, general, and non-overlapping; a principle earns its place only if it could realistically lose. Each entry carries its condition and its why, is stated once in normal register with never/always reserved for Inviolables, and where two entries could conflict the ranking names the winner; the evidence for why these authoring rules matter to the models consuming this document is `heddle/resources/model-prompting-principles.md` ("Destination, constraints, and the why", "One rule, one home, one winner", "Conditions, not corrections"). Cite entries by name, not number; the ordering evolves and numbers shift. Architecture entries follow the same authoring rules and are cited by name. Agents amend this document only at the owner's direction; record that authorization through the `Principles-Amendment:` trailer enforced by `heddle/resources/hooks/check-principles-amendment.py`, and while the frontmatter is not `status: ratified`, agents and gates must not resolve decisions against it — every open question escalates instead.

**Former entries** (names used in records written before 2026-09-05): "Ship to learn", "Name the appetite before starting", and "A documented manual step is a valid implementation" → Core first; scope is earned. "Treat unfamiliar recent changes as parallel work in progress" → Destruction requires proven success. "Requirements may be challenged, not only met" → The governing record leads. "Check, don't assume" → removed as native behavior of current models. "Log decisions and state transitions, not noise" and "Meet performance budgets by construction" → `heddle/resources/prompt-authoring-standards.md#code-quality-checklist`. "Keep behavior changes and mechanical changes in separate commits" → `AGENTS.md`.
