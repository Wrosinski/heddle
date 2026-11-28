---
status: ratified
ratified: 2026-07-27
amended: 2026-11-24
---

# Engineering Principles

_Status: ratified. This public copy contains the current principle set._

This document says how we build software in this repository. It starts with one philosophy, then lists the principles and hard rules that follow from it.

It is the owner's preference profile and the **primary source for engineering preference** here. When an engineering question has more than one reasonable answer, an agent uses this document to decide and treats it as the owner's judgment. Consult it before escalating. `AGENTS.md` holds only mechanical bindings (tool choices, commands, repo facts) and defers to this document on preference.

The content is subjective on purpose. Anyone who adopts this document should edit their copy to match their own preferences. It covers engineering preference only. Product, UX, and domain decisions are out of scope; scope and build order are covered in `docs/workflow/brainstorming-guidelines.md`. Code style and quality thresholds are in `heddle/resources/prompt-authoring-standards.md#code-quality-checklist`.

[Decision routing](../../heddle/resources/decision-routing.md) owns the procedure
for applying these preferences, asking the owner questions, and checking
execution authority. This document owns the preferences themselves. When a
principle settles an engineering choice, cite that one principle by name, with
the evidence.

## Philosophy

We build the simplest solution that is complete and correct.

- *Correct* means it does what its contract says. The contract is the acceptance criteria, the spec, or the promise made to the caller, including the failures it names. There is evidence that it works.
- *Complete* means the purpose works from start to finish in real use. Nothing on the way is a stub or an unstated step. We judge this against the purpose of the piece being built, not against every case we can imagine.
- *Simple* means a reader has little to keep in mind to check it: few concepts, few states, few paths, few dependencies. It does not mean the fewest lines, the most familiar style, or the least effort to write.

Correct and complete are requirements. Simplicity is what we optimize once they are met. Generality and cleverness are costs. We pay them only when a real need calls for them.

Three beliefs are behind this.

**Human attention is the scarce resource.** Agents write most of the code here. Humans check it at the level of concepts: the data model, the boundaries, the contracts, and the invariants. They do not read every line. Writing code is cheap, so the real cost of a design is how much a person must keep in mind to trust it. Every concept we add uses some of that attention for as long as it exists. A design that is easy to check is also easy to debug and extend.

**Small things move fast.** We learn fastest by putting a small working thing into real use and letting what we see decide the next step. Code we did not write needs no review, no tests, and no migration. A choice that is cheap to undo does not have to be right the first time.

**Robustness comes from the design.** A system is robust when it has few states, one owner for each fact, clear boundaries, and a loud failure for anything it does not handle. Guards, fallbacks, and retries for cases nobody asked for make a system bigger, not more trustworthy. So a first version does not handle rare cases. It rejects them with a clear error, and we add handling for the cases that real use brings up.

The Inviolables are hard limits. An option that breaks one is out, whatever else is good about it. The principles are not ranked. Each one says which side it takes in the trade-off it covers. If two principles pull in different directions, go back to the philosophy: pick the option that leaves a person less to check and still meets the contract. When the principles are silent or tie, prefer the option that builds less now.

## Principles

The principles are not ranked, and the order within a group means nothing.

### Scope and change

- **Core first; scope is earned.** Build the smallest slice that delivers the purpose from start to finish, and get it into real use as soon as possible. This applies at every level: brief, spec, plan, and code. What we see in real use decides what to build next, not what we guess we might need. Anything beyond the slice has to be justified; by default it becomes a named follow-up. Do not make something general until a second real caller needs it. A documented manual step is a valid implementation until it recurs often enough to be worth automating. Decide up front how much time the work is worth. If the work grows past that, make the slice smaller and turn what you cut into named follow-ups. Do not lower what the contract requires, and do not take more time unless the owner decides to.
- **Delete before adding.** Before you build, look for a way not to. Can the requirement be narrowed? Does an existing path already do the job? Is there a guard whose cause is gone? Is there a well-maintained tool that removes more complexity than it adds? When new code replaces old code, remove the old code and move its callers in the same change. Do not keep a second path, code "just in case", or a compatibility shim unless a named consumer needs it. Version control is how we undo. Agents add code more easily than they remove it, and everything left behind is something a person must keep checking. Remove only inside the scope of the change, and only with evidence that nothing still depends on what goes. If you find something to remove outside that scope, record it and route it.
- **Reversibility beats optimality.** Prefer the option that is cheapest to undo. When the intent is unclear, take the smallest reasonable reading and flag it for review. Put design effort where undoing is expensive: data models, stored formats, and public contracts deserve care. Internals are cheap to rewrite, so do not spend long debating them.

### Robustness

- **Simplicity first; the contract sets the robustness floor.** The contract says how robust the work must be. That contract is the acceptance criteria with their named inputs, edge cases, and failure modes; the spec; and the promise to the caller. Meeting it is part of being correct, not optional polish. Beyond the contract, simplicity wins. If the contract does not name an input or a state, reject it with a clear error instead of handling it. Accept less rather than handle more. Do not add guards, fallbacks, or retries for failures that nothing names. When real use brings up a new case, handle it then. Concerns outside the scope are routed as Discoveries. When two options both meet the contract, take the simpler one, even if the other is more robust.
- **Structural beats policed.** Design the system so that a bad state cannot happen, instead of adding checks to catch it. Fix a defect where it starts, not where it shows up. Enforce a rule in the contract that defines the state, not in a later review. When a root cause is gone, delete the guard that covered for it. New structure is worth adding when it lets us delete guards, not when it only adds layers.
- **Strict at boundaries, plain inside.** Be careful where trust and ownership change hands. At a boundary, decide on purpose who owns what, which way dependencies point, and what is exposed. Validate configuration and contracts at startup, and parse outside input where it enters, so a bad value fails before any work depends on it. Inside a boundary, trust what you were given and keep the code plain. Do not validate again, and do not add internal layers, patterns, or extension points until a second real case needs them. Boundaries are what a person checks, and they are expensive to move. Internals are cheap to rewrite.

### Architecture

These entries say how to draw a boundary when a change adds or reshapes components. Each one names the problem it prevents.

- **Data first; code follows.** Settle the data shapes, their owners, and their invariants before writing the logic. The data model is the most expensive thing to change, and when the model is right the code is obvious.
- **One owner per authority.** Every fact, decision, and state transition has one component that decides it and one that publishes it. Everything else gets it through that owner's interface. This means one source of truth per fact, one spelling per concept, and one handler per kind of failure. Extend an existing convention instead of starting a second one. Copies are generated or locked. A copy that needs a test to keep it in sync is a second owner, not a cache. A temporary second owner is allowed only as a named migration step with its removal recorded. Unify facts right away. Unify similar code only once the shared shape is proven, because duplication is cheaper than the wrong abstraction. This prevents constants duplicated across a boundary, and a ledger and a document that each claim the same progress.
- **Dependencies point inward, not back out.** Layers depend toward the stable core: vocabulary first, then pure derivation, then effects, then supervision. Each layer works at one level of abstraction and uses the layer below only through its public surface. A lower layer does not reach up to do the upper layer's work, for example through a callback or an injected closure. Do not get around a boundary by building the forbidden name at runtime. If a rule needs a bypass, the layering is wrong, not the rule. A structural test enforces the direction; review does not.
- **Pure core, effects at the edges.** Keep the logic deterministic and working on plain data. Put I/O, time, randomness, and subprocesses in a thin shell around it. Effects inside the core make tests depend on the outside world and make failures hard to reproduce. For the same reason, tests are hermetic and deterministic by default, and live proof is explicit, marked, and opt-in.
- **One responsibility per unit; assembly in one place.** Write a long procedure as a pipeline of named steps. The steps pass data through explicit inputs and outputs. One orchestrator joins them, and one place maps failures. A function that resolves, budgets, executes, records, and projects has five reasons to change, and no test can isolate any one of them.
- **Small, self-explaining surface.** A unit's public contract should fit in a few sentences: the types, commands, or files it promises, and nothing more. Everything else is private, and the unit has only the access its job needs. A facade that only forwards calls and makes no decision has no reason to exist. An exported helper becomes a contract that someone will depend on. A consumer who has read nothing but the surface should be able to use it. That means contracts are easy to find, and errors say what happened, which non-sensitive input or field was involved, and what the caller can do next. When the possible outcomes are not equally safe, make the caller choose. A silent default may only pick the safe side.
- **Extend through data and one resolver, not parallel paths.** A new variant, such as a gate, mode, lane, provider, or wire version, is registered as data with the existing resolver. It does not get its own chain of branches or a second state machine that can drift from the first. Name the seam before the second variant exists. Build it when that variant arrives.

## Inviolables

These are hard rules. They hold no matter what a principle or a deadline suggests.

### In the software

- **Fail loud.** No silent clobber, skip, truncation, fallback, or unbounded retry. Every degradation shows up in an artifact or an exit code. Exceptions propagate: a handler either recovers, or adds context at the boundary and re-raises. It never hides an unexpected failure.
- **Secrets never touch code, logs, or artifacts.** Refer to them by name, from the environment or a secret store.

### In the work

- **Never misrepresent.** Surfaces, claims, and verification show what is actually true. Done means there is evidence. Checks are never weakened to make them pass. Placeholders are tracked and never presented as product.
- **The governing record leads.** When the work contradicts its spec or a ratified decision, stop and raise the conflict. Never quietly change either side. Do the same when a requirement costs far more than its purpose is worth: raise it with its owner as a Discovery or a gate finding before building. Do not quietly comply, and do not quietly drop it.
- **Stay inside owned scope.** Record and route what you find outside your scope. Never bundle a drive-by fix, however small.
- **Destruction requires proven success.** Delete or overwrite only when recorded evidence shows it is safe. Prefer forms you can recover from: move before you delete. Before a corrective fix acts, confirm the exact failure, because something that looks like a known failure may have a different cause. Unfamiliar recent changes from another stream are work in progress, not debris. Patch forward, and never roll them back unless asked.

## Editing

This is a preference profile, not engineering truth. Disagreeing with this copy is not an error; edit it.

When you write or change an entry:

- Keep it short, general, and separate from the other entries. Use plain words and short sentences.
- A principle earns its place only if it could realistically lose.
- Give the condition and the reason, and state the entry once.
- Use normal wording. Keep "never" and "always" for the Inviolables.
- Where two entries could conflict, one of them says which wins.
- Cite entries by name.

The evidence for why these authoring rules matter to the models that read this document is in `heddle/resources/model-prompting-principles.md` ("Destination, constraints, and the why", "One rule, one home, one winner", "Conditions, not corrections").

Agents amend this document only at the owner's direction. Record that authorization with the `Principles-Amendment:` trailer, which `heddle/resources/hooks/check-principles-amendment.py` enforces. While the frontmatter is not `status: ratified`, agents and gates must not resolve otherwise-open engineering choices against it. Apply the shared decision policy; existing explicit decisions and grants remain applicable.

**Former entries.** Records written before 2026-09-05 may use these names: "Ship to learn", "Name the appetite before starting", and "A documented manual step is a valid implementation" → Core first; scope is earned. "Treat unfamiliar recent changes as parallel work in progress" → Destruction requires proven success. "Requirements may be challenged, not only met" → The governing record leads. "Check, don't assume" → removed as native behavior of current models. "Log decisions and state transitions, not noise" and "Meet performance budgets by construction" → `heddle/resources/prompt-authoring-standards.md#code-quality-checklist`. "Keep behavior changes and mechanical changes in separate commits" → `AGENTS.md`.

Records written before 2026-11-24 may use a principle's rank, the Defaults section, or these names. The ranking and the Defaults section were removed on that date. "Correct beats simple; simple beats general; general beats clever" → Philosophy. "Everything once, in one place" → One owner per authority. "Design for the docs-blind consumer" and "Small surface, private everything else" → Small, self-explaining surface. "Proven tools beat bespoke machinery" and "Backwards compatibility is opt-in" → Delete before adding. "Exceptions propagate" → Fail loud. "Point-in-time integrity" → removed; it belonged to another repository. The other Defaults moved as follows: startup validation and untrusted input → Strict at boundaries, plain inside; least privilege and asymmetric outcomes → Small, self-explaining surface; hermetic tests → Pure core, effects at the edges; checking the exact failure before a corrective fix → Destruction requires proven success.
