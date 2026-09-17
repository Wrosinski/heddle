---
name: conjunctive-safety-decision-matrix
area: testing
status: active
created: 2026-09-05
superseded_by: null
---

# Conjunctive safety decision matrix

**Intent:** prove that every prerequisite in a destructive or
authority-granting decision can independently stop the operation.

**When to use:** an operation is permitted only when several conditions are
simultaneously true—for example commit containment, empty tracked/staged/
untracked/ignored inventories, current authorization, and a stable target.
Skip it for a single-condition predicate whose two polarities are already
exercised directly.

**Recipe:**

1. Enumerate the primitive prerequisites before writing fixtures. Do not start
   from the implementation's aggregate `safe` boolean.
2. Build one baseline row in which every prerequisite is true and assert the
   exact permitted action.
3. Build one row per prerequisite with only that term false. Keep all other
   terms true and assert the exact blocker and absence of mutation.
4. Add pairwise or ordering rows only for real interactions, such as a stale
   integration reference changing while local inventory is clean. Avoid a full
   combinatorial matrix without a risk hypothesis.
5. Re-resolve mutable inputs immediately before the destructive boundary and
   include a row where a formerly true term changes after preflight.
6. Assert the public outcome: exit/code, diagnostic, next action, and filesystem
   or state non-mutation. Counts or helper-return booleans alone do not prove
   that the protected operation stayed unreachable.

**Anti-patterns / caveats:** one all-false fixture cannot show which predicates
matter; one happy path plus a generic dirty case leaves conjunction terms
untested; deriving expected values with the production helper makes the test
tautological; and branch containment, authorization, or `.gitignore` membership
must never stand in for the other terms. The matrix is deliberately linear in
the number of prerequisites unless evidence justifies interaction rows.

**Concrete future-feature scenario:** worktree cleanup requires a current local
integration ref, contained commits, and empty tracked, staged, untracked, and
unknown-ignored inventories. Six one-false rows prove that each condition alone
blocks ordinary removal, while one all-true row proves the safe path and one
stale-ref row proves boundary-time revalidation.
