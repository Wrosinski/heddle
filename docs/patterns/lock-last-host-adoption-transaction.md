---
name: lock-last-host-adoption-transaction
area: architecture
status: active
created: 2026-07-29
superseded_by: null
---

# Lock-last host-adoption transaction

**Intent:** adopt several files in a repository the application does not own
without destructive overwrite or partial state masquerading as success. A
complete immutable plan governs both preview and apply; each file publishes
atomically; the lock is written last as the transaction's commit point.

**When to use:** a bootstrap, migration, or adoption command projects multiple
files into a user-owned repository and must preserve compatible existing
content, converge after interruption, and distinguish initialized from
partially applied. Not for runtime-owned state that needs compare-and-swap
updates; use [[pattern:cas-guarded-state-write-seam]] there.

**Recipe:**

1. **Preflight every target before writing.** Resolve the repository root,
   render all desired content, inspect target type/content/markers and any
   existing lock, then produce one ordered immutable plan. Any refusal blocks
   all writes.
2. **Use an explicit action vocabulary.** Distinguish `create` (absent),
   `accept` (identical but not recorded), `integrate` (a bounded owned region),
   `refuse` (foreign or malformed), and `skip` (recorded host-owned content).
   Each term has one write/no-write meaning.
3. **Render once.** Store desired bytes in the plan so preview and apply cannot
   observe different templates, environment, or time.
4. **Publish each non-lock row atomically.** Use exclusive creation for absent
   files and destination-sibling atomic replacement only for a validated owned
   region. Preserve every byte outside that region; dependency failures remain
   typed and actionable.
5. **Write the lock last.** Only after every non-lock row succeeds, derive and
   atomically publish the lock. Lock presence is the initialization commit
   point; no other file may claim completion.
6. **Make every interruption prefix convergent.** A stop before the lock leaves
   complete files but no commit point. On rerun, absent rows become `accept`,
   already-integrated managed regions integrate byte-identically, and the
   command finishes by writing the lock.
7. **Keep host ownership after adoption.** A lock entry records membership,
   class, and recording-time bytes; it does not grant the engine ownership of
   later host edits. Carry recorded `skip` entries forward verbatim rather than
   re-hashing user-owned content into a new authority claim.
8. **Diagnose partial states distinctly.** Missing lock, malformed lock,
   missing recorded target, and inaccessible recorded target are different
   states with different remediation. Content drift at an intentionally
   host-owned recorded path is not automatically an error.

**Anti-patterns / caveats:** writing as planning proceeds; re-rendering during
apply; writing the lock before the last projected file; treating a recorded
hash as perpetual content ownership; hand-merging arbitrary host prose; or
inventing a second recovery protocol instead of rerunning the same plan. This
is process-level atomicity under a single-operator model, not a multi-writer
transaction or power-loss durability guarantee.

**Concrete future-feature scenario:** a future `heddle migrate` command adopts
legacy config, a managed instruction block, and a migration manifest. It
preflights all three, publishes compatible targets atomically, writes the
migration lock last, and safely resumes after any injected cut point without
overwriting host edits.
