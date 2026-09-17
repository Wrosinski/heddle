---
name: fatal-advisory-by-reliance-test
area: architecture
status: active
created: 2026-06-22
superseded_by: null
---

# Fatal vs advisory by the reliance test

**Intent:** classify every finding a validator emits as **fatal** vs **advisory**
by *one discriminating question* — "does a command or derivation rely on the
violated fact?" — instead of a hand-curated severity list that gets re-litigated
each time a new check lands.

**When to use:** any multi-check validator / linter / drift detector that emits
findings of mixed importance and must return a single worst-severity exit code.
The danger it addresses is real: as checks accrue, "is this one an error or a
warning?" becomes a per-check argument, and severity drifts by author mood. Skip
it for a single-purpose check with only one obvious severity.

**Recipe:**

1. **Adopt one rule, stated as a question.** *Fatal iff a command or kernel
   derivation relies on the violated fact; advisory iff the ledger/document is
   resolvable but incomplete or lower-quality.* Write it into the spec as an
   invariant, not a table.
2. **Derive the per-check buckets as the rule's *application*, not a list.** Show
   the mapping table (`dangling reference → fatal`, `unassigned AC → advisory`,
   …) explicitly *labelled as* "the reliance test applied," so a reader checks
   each row against the question rather than memorizing it. The asymmetry falls
   out naturally: a `satisfies → phantom AC` is fatal (the join relies on it); an
   `AC → no satisfies` is advisory (nothing breaks).
3. **Forbid the tempting non-rules.** Severity must not depend on document age,
   finding count, or "blocks a transition" (off-the-hot-path findings would all
   become advisory). Drop any inherited grandfathering when porting a check.
4. **Map worst-severity to the exit code in one place.** Accumulate all findings,
   then `any fatal → exit-fatal; else any advisory → exit-advisory; else 0`. Each
   finding carries its own free-form `code`; only the single top-level error code
   is vocabulary-constrained.
5. **Test the invariance.** A fixture proves the same finding keeps its severity
   regardless of age/count, and that a fatal+advisory mix exits fatal with both
   present in the findings list.

**Anti-patterns / caveats:** the failure mode is sliding back into a curated
list — every new check spawns a severity debate the rule was meant to end. If two
engineers disagree on a bucket, the fix is to sharpen "what relies on this fact,"
not to add a special case. Watch for checks inherited from a tool with a *binary*
exit (pass/fail): re-bucket each finding by the reliance test, don't inherit the
source's ERROR/WARN split.

**Concrete future-feature scenario:** a knowledge-plane document-schema
conformance and any future `validate --fix` / corpus lint reuse the reliance test
to assign severities consistently as dozens of new checks land — each new check
answers one question instead of opening a new severity negotiation.
