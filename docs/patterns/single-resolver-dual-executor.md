---
name: single-resolver-dual-executor
area: architecture
status: active
created: 2026-06-17
superseded_by: null
---

# Single resolver, dual executor

**Principle:** [Extend through data and one resolver, not parallel paths](../workflow/engineering-principles.md#architecture).

**Intent:** add a new execution mode as a second *executor* of one shared
decision resolver — never a parallel state machine — so the modes provably
cannot drift and the "intelligence" lives in the single seam both modes consume.

**When to use:** a feature that introduces an autonomous / scheduled / detached
way to run a workflow that already has a deterministic next-step resolver and a
human who executes its steps interactively. The new mode must produce the same
artifacts and pass the same gates as the existing one. Not for genuinely
different workflows (a new resolver is honest there), and not when the existing
"resolver" is actually scattered across the UI/handlers (extract it first).

**Recipe:**

1. **Find the one resolver.** Locate the seam that, from current state, returns
   the single legal next move (Heddle: `derive_next_actions(snapshot)` → exactly
   one `(command, reason)`, empty only at terminal). If there isn't one, that is
   the real work — the dual-executor property is only as strong as the
   single-resolver property.
2. **Frame the new mode as "execute `next_actions` in a loop."** The interactive
   mode = a human executes the resolver's next move; the new mode = a thin
   executor loops `resolve → execute the one front move → re-resolve` until
   terminal or blocked. No second brain, no second state file.
3. **Add only data + a seam, never logic the resolver lacks.** What the new mode
   needs reduces to (a) mode-as-data on the state, (b) any mode-conditional
   *derivations* folded into the *same* resolver (so both modes read one truth),
   and (c) the executor itself as a leaf that consumes only public seams and is
   imported by nothing (enforce with an AST boundary test).
4. **Re-derive "done" from artifacts, never from a self-report.** The executor
   validates each step's real output (files parse, IDs unique, verdict converged,
   state advanced legally) before continuing — it never trusts a model/agent
   claim of completion.
5. **One uniform advance rule.** A transition happens iff nothing blocks; the
   modes differ *only* in whether one condition (e.g. human authorization) is
   among the things that can block — expressed as a mode-conditional blocking
   condition in the shared resolver, not as a fork in the executor.

**Anti-patterns / caveats:** the failure mode is a *second* state machine /
orchestrator "brain" that re-implements sequencing — it drifts from the
interactive path the first time the workflow changes. Mode-conditional behavior
belongs in the shared resolver's derivations, not in `if mode == auto` branches
sprinkled through the executor. The executor must stay stateless (resume =
re-invocation) so it can't accumulate a private view that contradicts the
resolved snapshot. Keep it a leaf: if the resolver or its packages ever import
the executor, the dependency has inverted and the "one brain" claim is gone.

**Concrete future-feature scenario:** a future Heddle scheduled/cron or
detached-cloud flow mode reuses `derive_next_actions` + the same write seams as a
*third* executor (alongside HITL and `heddle drive`) rather than a new
orchestrator; the only additions are the mode value and any mode-conditional
blocking derivations.
