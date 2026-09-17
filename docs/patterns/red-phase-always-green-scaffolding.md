---
name: red-phase-always-green-scaffolding
area: testing
status: active
created: 2026-06-11
superseded_by: null
---

# Red-phase tests and focused green feedback

**Intent:** make future behavior testable before implementation while retaining
fast, honest feedback for the work already delivered.

**When to use:** a multi-milestone feature has tests for later behavior alongside
current milestone and pre-existing behavior checks.

**Recipe:**

1. Give changed behavior an executable assertion that fails for the intended
   missing behavior. Preserve pre-existing behavior with passing survivor cases.
2. Keep future-red witnesses independently selectable. Use exact files or nodes
   for the current milestone and its consumers; retain future witnesses in their
   declared milestone and acceptance obligations.
3. Scope the progressive feedback command to the delivered behavior. Do not
   describe a green subset as complete feature verification or narrow a required
   acceptance scope merely to obtain a pass.
4. If a test cannot yet bind to its production boundary, name the activation seam,
   owner and pending obligation. Skips and xfails require the host's reviewed
   exception; they cannot satisfy strict proof. Collection is not execution.
5. Activate every due witness and remove temporary import/skip guards before
   claiming its milestone or feature complete. Then run the exact required
   selection through the host's proof entry.

**Anti-patterns / caveats:** automatic `importorskip` hiding missing production
modules; broad suite exclusion used as acceptance; permanent skip probes that
mask regressions; or treating milestone markers as execution authority. Exact
selection still needs inspection of fixtures and nested subprocesses.

**Current Heddle application:** the
[testing strategy](../workflow/testing-strategy.md#runner-contract) requires
positive completed proof for every requested witness. Progressive `test_command`
feedback is separate from native milestone and final verification facts.

**Concrete future-feature scenario:** scaffold parser and integration behavior
for two milestones, run the parser and its affected consumers first, then activate
the integration witnesses before integrated acceptance. Both obligations remain
visible throughout the work.
