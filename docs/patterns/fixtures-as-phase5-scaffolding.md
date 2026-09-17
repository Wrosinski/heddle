---
name: fixtures-as-phase5-scaffolding
area: testing
status: active
created: 2026-06-11
superseded_by: null
---

# Fixtures as Phase 5 scaffolding

**Intent:** author validation corpora at test-scaffolding time, against
the ratified vocabularies, so implementation *verifies* fixtures instead
of authoring them under deadline pressure.

**When to use:** any feature whose tests need a non-trivial fixture
corpus (synthetic workspaces, golden files, migration inputs) and whose
target schemas/vocabularies are already pinned by design docs. Not for
fixtures that depend on implementation output formats still in flux.

**Recipe:**

1. At Phase 5 (test scaffolding), author the full corpus plus a README
   **coverage map** recording which fixture exercises which edge case —
   fixture assets count as scaffolding and get gate-reviewed there.
   Home invalid/negative corpora **outside** the conformance-walked
   tree (for example, `tests/fixtures/invalid-states/` beside the walked
   `workspaces/`), with their own README map — the conformance walk
   over valid assets must stay clean by construction.
2. Write the fixture tests in the same phase; they go green
   automatically the moment the vocabulary/contract module lands.
3. The fixture-owning milestone then becomes **verification-weighted**:
   confirm the corpus against the implemented vocabulary, complete any
   stubbed assertion, verify the README map row-by-row on disk.
4. If the sweep produces zero changes, close with a **no-change audit
   record** (concrete file/assertion evidence in the plan) instead of an
   empty diff.
5. Discrepancies resolve toward the ratified docs: fix the fixture; if
   the *vocabulary* is wrong, stop — that is a structural (Level 3)
   contradiction.

**Anti-patterns / caveats:** gratuitous deviation from the design docs'
worked examples creates rework for the later milestones that parse the
same files. Sanitize real-host material (use the `example` domain) and
enforce it with a structural test.

**Concrete future-feature scenario:** a future `migrate --from-legacy`
needs a legacy-workspace corpus; a validator needs invalid-workspace
fixtures — both can be authored at their Phase 5 against pinned schemas.
