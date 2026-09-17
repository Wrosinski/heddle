---
name: verified-by-completeness-rule
area: testing
status: active
created: 2026-06-14
superseded_by: null
---

# Verified-by completeness — assert every Expected clause, not a proxy

**Intent:** each acceptance criterion's spec-linked `Verified-by` test
asserts *every* observable property its Expected clause names — not a
weaker proxy (non-empty data, exit-code-only, "any-of" substring) — so a
green test actually means the AC holds. Prevents the common failure where
a test passes while the behavior it claims to pin is untested.

**When to use:** any feature whose ACs enumerate several observable
properties, name distinct precondition fixtures, or pin exact values
(resolved tiers, exit codes, key sets, ordered output). Adopt it as an
Active Enforcement Rule from milestone one, not retrofitted at review.

**Recipe:**

1. For each AC, list every property the Expected clause names; the linked
   test must have a failable assertion for *each* — render payload AND
   byte-identical state AND idempotent output, not just "exit 0".
2. **Exact, not any-of:** a value the spec pins (a resolved tier
   `override|generated|packaged`, an exit code) is asserted as *that*
   value with the alternatives *excluded* — never "token present in the
   blob" (`category in " ".join(diagnostics)` is satisfiable by
   one diagnostic naming all categories; assert the `code`-prefix instead).
3. **Negative/asymmetric case:** assert the thing that should be *excluded*
   is excluded (one acceptance test asserted a configured root is present but
   never that a non-configured root is absent — couldn't distinguish
   "follow config" from "accept everything").
4. **A fixture per named precondition:** if the Expected names distinct
   cases (a parse fault, a missing resource), cover each with its own
   fixture — don't let one fixture stand in for several.
5. **Composition-pinning for surface-unreachable cases:** when a branch
   cannot be driven through the CLI (for example, a packaged floor can make
   `resource-missing` unreachable), pin it by composition (the unit that
   raises + the shared mapping that turns it into the exit code) and
   *record that coverage* rather than silently dropping it or contorting
   the fixture to reach it.
6. **Execute boundary conditions, don't trace them:** for exit-code
   mappings and spec-mandated diagnostics, run the real handler and assert
   the observable result — a code trace at a milestone gate once let two
   `doctor` gaps (exit-4-not-2; missing needs-classification advisory)
   slip to peer review.
7. **For a *derived* eligibility/blocking clause, execute against a
   fixture that makes the upstream derivation actually fire the
   condition** — not one that merely traces "the handler consumes the
   derivation." When an AC's precondition is produced by another module's
   derivation, that derivation may not surface the exact condition the AC
   assumes; only a fixture that drives the condition exposes the gap. In one
   example, `phase-exit` "consumes `blocking_conditions`, re-derives nothing"
   traced clean, but the upstream model gated `gate-not-converged` behind `exit_ready` and
   surfaced no incomplete-milestone condition, so a bare `phase-exit` could
   leave `implement` with open work — missed by three milestone gates, the
   AC-10 fixtures being all-done or already-blocked).

**Anti-patterns / caveats:** exit-code-only or non-empty-data proxies;
"any-of" substring assertions on pinned values; a single fixture covering
multiple named preconditions; deleting an unreachable branch as "dead"
instead of composition-pinning it; trusting a code-trace for a
boundary-condition AC instead of executing it; and trusting
"the handler consumes derivation X" for a *derived*-eligibility
AC instead of driving a fixture that makes X fire (the upstream derivation
may not surface the condition the AC assumes).

**Concrete future-feature scenario:** Heddle's write-path commands
(`phase-exit`, inner-loop writes) have multi-property ACs (state mutation
+ envelope + exit) and boundary conditions (CAS conflict → exit 5) — each
`Verified-by` asserts every clause and executes the conflict path, caught
by this rule at scaffold time rather than peer review.
