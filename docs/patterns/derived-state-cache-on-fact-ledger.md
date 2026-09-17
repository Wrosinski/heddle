---
name: derived-state-cache-on-fact-ledger
area: architecture
status: active
created: 2026-06-14
superseded_by: null
---

# Derived-state cache folded onto an append-only fact ledger

**Intent:** memoize an expensive operation's result without a second
store — the cache *index* is a content hash recorded on the existing
append-only fact ledger, and the cached *payload* is the artifact the
operation already wrote. Because the cache is derived from the same facts
that produced it, it cannot drift from its inputs (the bug class the whole
system exists to kill).

**When to use:** an expensive, deterministic-given-its-inputs operation
(a CLI review, a build, an LLM call) whose result is already persisted as
a fact + artifact. Not for operations with hidden nondeterminism the hash
cannot capture, and not where a true second store with its own eviction is
actually required.

**Recipe:**

1. Compute `input_hash = hash(<all inputs that change the result>)`. Fold
   in every input: the data under review, the rendered prompt/config, AND
   the execution config (`{cli, model, reasoning_effort, sandbox}` —
   the same prompt under a different model can disagree).
2. Record `input_hash` on the operation's existing run-fact (no new
   table/file). The fact ledger is the index; the already-written artifact
   is the payload.
3. A hit requires **all** of: a matching `input_hash`, a present artifact,
   and a converged/clean verdict — re-running an *unconverged* result is
   the point, so cache only the terminal-success state (for reviews, verdict
   `pass`; "cache any completed verdict" was proposed 3× and declined —
   it breaks the exit-code-refinement contract). Any input change → new
   hash → miss; a deleted artifact → miss.
4. "Resumable" reduces to **idempotent re-invocation** keyed by
   `input_hash`: an interrupted run recorded no completed fact → it
   re-executes; a completed run returns its cached verdict. No separate
   resume state machine, no CAS for these append-only facts.
5. **Proxy honestly:** if an input cannot be captured literally
   (for example, a runtime that cannot shell out for a real `git diff` —
   so the "diff" is an owned-path content digest, and the "rendered
   prompt" is its resolved context, not a literal template digest), record
   the proxy and the resulting fidelity gap (a template-only edit is not a
   guaranteed miss until the digest tightens). Don't pretend the proxy is
   the literal input.

**Anti-patterns / caveats:** a standalone cache file/table that can
diverge from the facts; hashing only some inputs (model/exec-config omitted
→ a stale review reused under a different model); caching non-terminal
results (regresses re-run semantics); silently treating a proxy input as
the literal one.

**Concrete future-feature scenario:** Heddle `heddle drive` / any
expensive-op memoization keys on the same `input_hash` run-fact ledger;
A later change can tighten the proxy components (literal rendered-template digest, deep
artifact contract-validation) without changing the no-second-store shape.
