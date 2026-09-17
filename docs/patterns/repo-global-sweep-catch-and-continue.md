---
name: repo-global-sweep-catch-and-continue
area: architecture
status: active
created: 2026-06-22
superseded_by: null
---

# Repo-global sweep: catch-and-continue

**Intent:** make a repo-global command that visits every workspace **isolate
per-item faults** — catch the bad one, record a fatal diagnostic that names it,
and keep sweeping the rest — instead of letting one unparsable/ahead/escaping
item abort the whole run and discard every result already accumulated.

**When to use:** any command that fans out over a corpus (validate, migrate,
`status --all`, a projection refresh) where items are independent and one item's
fault should not blind the operator to the other N. Especially when each item's
mutation is independently atomic, so partial progress is safe and re-running
converges. Not for an all-or-nothing transaction across items.

**Recipe:**

1. **Wrap each item in its own try/except `… continue`.** The unit of isolation
   is the item (workspace / doc), not the run. A raise inside one item becomes a
   fatal *finding* about that item; the loop proceeds.
2. **Catch the structural exceptions, not just the domain error.** Path and
   encoding operations throw `ValueError` (`Path.relative_to`), `OSError`
   (`stat`/symlink loops/null bytes), `UnicodeDecodeError` — the catch set must
   include these alongside the domain error, or the "never abort" claim is a
   layer too narrow (see the sibling caveat in
   [[pattern:typed-error-channel-rail]]).
3. **Isolate at *every* layer that can raise.** If the command has nested sweeps
   (per-workspace, then per-guardrail, then per-doc), each level needs its own
   catch-and-continue. Fixing the outer level and leaving an inner one unguarded
   still loses the inner sweep's accumulated findings — sweep all sibling layers
   symmetrically when you establish the guard.
4. **Report the per-item split through diagnostics, not `data`.** A failure
   envelope is `data`-xor-`error` (you cannot carry a top-level `error` *and* a
   `data` summary), so the migrated/skipped/checked split lives in
   `diagnostics[]`: `info` for the successes (`workspace-migrated`), `fatal` for
   the skips (`workspace-invalid`). The top-level error code is the umbrella;
   keep `data` absent on any fatal run.
5. **Top-level code by class precedence.** Set the single top-level error code to
   the single-item structural code *only when that is the sole fatal class*; if
   any cross-cutting fatal also fired, the umbrella code wins and the single-item
   faults are enumerated underneath.

**Anti-patterns / caveats:** the bug this kills is a bare unguarded call (an
escaping `covers:` glob, a malformed doc) propagating to the dispatcher's generic
handler and returning a bare `internal`/exit-1 that **discards every
already-accumulated diagnostic** — the operator sees one stack trace instead of
the nine real findings. The other trap is trying to stuff the per-item report
into `data` on a failing run and silently violating data-xor-error. Don't make
catch-and-continue swallow a *retryable* fault (a CAS conflict is exit-now-and-
retry, not skip-and-continue).

**Concrete future-feature scenario:** `status --all`, a corpus-wide migrate, or
projection refreshes fan out over every workspace and reuse one sweep-and-isolate
harness — a single bad workspace is reported and skipped, the rest complete, and
the run stays idempotent on re-invocation.
