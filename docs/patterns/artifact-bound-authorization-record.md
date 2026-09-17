---
name: artifact-bound-authorization-record
area: contracts
status: active
created: 2026-09-03
superseded_by: null
---

# Artifact-bound authorization record

**Intent:** ensure that a record authorizing a live mutation identifies valid
evidence and the exact contained artifact bytes that were reviewed, rather than
trusting plausible paths, hashes, placeholders, or a self-declared success flag.

**When to use:** a promotion, migration, replacement, or cleanup record whose
approval depends on repository files and structured evidence. Not for a
read-only report that cannot authorize a mutation, or for an immutable artifact
already verified inside a trusted content-addressed store.

**Recipe:**

1. **Define the complete typed contract independently.** Enumerate every
   mandatory leaf, its type and value domain, allowed statuses and
   dispositions, and the exact authority and target fields. Reject blanks,
   unresolved placeholders, and execution sentinels such as `NOT RUN` on every
   live disposition path.
2. **Exercise a real positive record.** Materialize actual candidate and anchor
   files, valid identities and results, sufficient evidence, and named
   authority. A fixture containing synthetic path and hash strings does not
   prove artifact binding.
3. **Prove lexical and resolved containment.** Require a normalized
   repository-relative path, resolve it strictly against the workspace root,
   reject missing paths, directories, resolution errors, and any resolved path
   outside that root. A relative spelling is not containment: a symlink can
   escape it.
4. **Validate the bytes actually read.** Catch filesystem errors, read bytes,
   require the declared encoding, and hash those same bytes. Re-read every
   exact anchor from a contained file and reject absent or stale anchor text.
   A matching digest alone does not prove readable review content.
5. **Join evidence, intent, and authority.** Promotion requires complete
   evidence plus the named authority and allowed target. Give every live
   disposition—addition, replacement, removal, or equivalent—its own valid
   positive fixture and apply the same negative evidence matrix to each.
6. **Mutate every failure class independently.** Remove each leaf; supply blank,
   malformed, wrong-domain, or sentinel values; drift hashes and anchors; use
   absent, unreadable, non-UTF-8, directory, absolute, parent-traversal, and
   symlink-escaping paths; mismatch intent and disposition. Every case must
   fail closed before live bytes change.
7. **Close mutable-target races at the write boundary.** Evidence validation is
   not a concurrency primitive. If another writer can change the artifact,
   revalidate its identity inside the mutation's lock, compare-and-swap, or
   transaction before committing the write.

**Anti-patterns / caveats:** checking only key presence; filling every field
with one generic “resolved” token; trusting a boolean such as
`evidence_sufficient`; hashing bytes without proving they decode; rejecting
`..` while ignoring symlinks; reading the expected hash from the record under
test; testing `PROMOTE` while leaving `REMOVE` unreachable; or validating once
and mutating a concurrently writable target later.

**Concrete future-feature scenario:** a repository cleanup manifest may delete
or replace a generated file only after its record proves that the path still
resolves inside the workspace, its readable bytes and anchors match what was
reviewed, the disposition matches the declared intent, and the deletion
authority is current.
