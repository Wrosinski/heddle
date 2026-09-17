---
name: typed-error-channel-rail
area: architecture
status: active
created: 2026-06-12
superseded_by: null
---

# Typed error channel + structural rail

**Intent:** every failure on a package's read/IO paths surfaces as the
domain's typed error (mapped to pinned codes and exits), never a bare
`OSError`/`UnicodeDecodeError` reaching a generic catch-all — and the
guard class is structurally unregressable as the package grows.

**When to use:** any package whose IO failures must map to pinned error
codes / exit codes (control-plane readers, state writers, validators).
Not for code where a generic wrapper IS the contract (e.g. a
last-resort dispatcher handler — that wrapper is the floor the typed
channel keeps failures away from, not a substitute for it).

**Recipe:**

1. Pick one domain error type carrying `code`/`message`/`hint` (for example,
   `KernelError`); callers/handlers own the code→exit mapping, so the
   same error can map differently per surface.
2. Guard every content read with `except (OSError, UnicodeDecodeError)`
   → raise the typed error naming the file, or fall through (e.g.
   `return None`) where the boundary's semantics are "behave as
   absent". `UnicodeDecodeError` is a `ValueError`, NOT an `OSError` —
   catching `OSError` alone is the classic miss.
3. When a review fix establishes the guard at one site, **grep the call
   shape (`read_text`) and sweep every sibling site in the same
   batch** — point fixes leave the class open (in one case, 3 of 4 sites were
   fixed at review; the fourth surfaced as the next phase's only Important
   finding).
4. Encode the class as an AST rail: every read call must sit in a `try`
   whose handlers name both exception types (for example,
   `TestKernelDecodeGuard` / `TestRuntimeDecodeGuard`). Keep the rail
   even where it is vacuous today (a package with no reads yet) — it
   bites the moment growth adds the first read.
5. Add behavioral companions per boundary: a non-UTF-8 file case (runs
   everywhere) plus an unreadable-permissions case (root-guarded, or
   deterministic via fault injection).
6. **Project the type through the public handler.** A typed reason that is
   correct inside the reader but collapses at dispatch into the wrong exit,
   diagnostic, or action is not a complete channel. Exercise the public
   handler in-process when deterministic fault injection cannot cross an
   installed subprocess, and assert the complete envelope.
7. **Prove remediation reachability.** Trace each proposed next command
   through its own prerequisites before publishing it. If the failure occurs
   before the diagnostic command can bootstrap, that command is a self-loop,
   not a next action. When recovery modes are asymmetric (for example editable
   checkout, pinned Git, or reviewed wheel reinstall), name the choices in the
   hint and emit no executable action rather than silently selecting one.
8. **Bound structured reads before parsing.** Charge actual bytes against a
   documented ceiling before decoding or parsing untrusted workspace input.
   Route the parser's documented resource failures—including `RecursionError`
   for pathologically deep JSON/YAML—through the same typed channel. Exercise
   both oversize and deeply nested payloads at the public handler; do not use a
   broad `except Exception` to approximate this rail.

**Anti-patterns / caveats:** catching `OSError` alone; fixing the
flagged site without sweeping siblings; treating the dispatcher's
`except Exception` as "handled" (it destroys the error contract with a
generic message and wrong exit); forgetting that fall-through
(`return None`) is also a valid typed channel — choose raise vs
fall-through per boundary, don't default to raise. The rail also covers
**write** sites, not only reads: a state-write seam's `os.replace` can
fail on `ENOSPC`/`EROFS`/`EACCES` and must map to the typed
`workspace-invalid` error. And a **retryable** typed
error (e.g. a CAS conflict / exit 5) is not a free pass to loop forever —
bound the retries and, on exhaustion, halt FATAL-with-notify rather than
spinning; unbounded retry of a retryable code is the
write-side analogue of swallowing an error. The same rule applies to
remediation: a type-correct error with an unreachable or self-looping
`next_action` is still a broken public contract. Do not add a command merely
to make the action list non-empty. Parser failures deserve the same precision:
catching syntax errors while deep input escapes as `RecursionError`, or parsing
an unbounded file before checking its size, leaves the public channel
incomplete.

**Concrete future-feature scenario:** Heddle's state writer/reader —
its new read+write sites inherit `TestRuntimeDecodeGuard` unchanged and
extend the same `KernelError` mapping; `validate`'s knowledge-plane
reader grows beside the hot path under the same rail. A future pre-config
bootstrapper also drives an injected packaged-resource failure through its
public handler and publishes only a remediation whose prerequisites exist at
that boundary.
