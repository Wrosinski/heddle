---
name: cas-guarded-state-write-seam
area: architecture
status: active
created: 2026-06-16
superseded_by: null
---

# CAS-guarded state write seam

**Principle:** [One owner per authority](../workflow/engineering-principles.md#architecture).

**Intent:** make a runtime the single writer for an operational state
file through one compare-and-swap-guarded atomic seam, so every mutation
is conflict-safe, dry-run-able, idempotent, and impossible to do as a
silent hand-edit — generalizing an append-only recording seam into the
full write path. When CAS mutations and revision-neutral appenders share
the file, serialize every read-modify-write with one stable container lock.

**When to use:** a feature that turns a previously hand-edited (or
append-only) control-plane file into a set of runtime-owned mutations,
where two writers (two agents, a feature switch, a stray git op) could
race. Not for content the runtime is free to overwrite wholesale, and not
for knowledge-plane prose (keep that on a separate, explicitly-counted
seam — see caveats).

**Recipe:**

1. **One seam, pure transforms.** Put the mutation logic in pure,
   no-I/O transforms that take the *parsed document* and return the
   mutated document (`render(document) -> document`); a single runtime
   seam (`commit_state_write`) is the only place that serializes and
   writes. Round-trip the **parsed document** (e.g. `yaml.safe_dump(...,
   sort_keys=False, allow_unicode=True, width=4096)`), not a typed
   dataclass — no serializer of the frozen model is needed, and key order
   / multiline prose survive.
2. **Serialize mixed writers on a stable descriptor.** When any writer can
   append without changing the revision, CAS alone cannot detect that append.
   Acquire one workspace-scoped lock before each complete read-transform-
   replace transaction, including both CAS mutations and revision-neutral
   recording. Lock the stable workspace-directory descriptor: locking the
   target file does not survive `os.replace`, while creating a lock file inside
   the workspace can violate exact artifact or cleanup contracts. If an outer
   operation also holds a narrower execution lock, fix and test one order
   (for example, gate lock then state lock) so no reverse acquisition can form
   a deadlock cycle.
3. **CAS or conflict inside the lock.** The seam re-reads the on-disk revision
   token while holding the shared lock; commit only if it still equals the
   value read at the start of the command, then increment it and restamp the
   timestamp. A mismatch is a typed conflict mapped to a dedicated exit code
   (retryable), never a silent overwrite. The lock prevents undetectable
   revision-neutral loss; CAS still rejects a command prepared from stale
   state.
4. **No-op short-circuit in the seam.** When `render` returns a document
   equal to the input, return a "did-not-write" result with the revision
   *unchanged* — so state-convergent commands (re-running a satisfied
   mutation) are successful no-ops that don't bump the revision. Keep this
   in the seam, not in each handler.
5. **Atomic write.** Temp file + `os.replace` + `finally` unlink — no
   partial writes; a crashed write leaves the prior bytes intact.
6. **Dry-run writes nothing.** A `--dry-run` flag computes the would-be
   mutation and revision and reports them, leaving the file and workspace
   byte-for-byte unchanged.
7. **Distinguish convergent from append acts.** State-machine mutations
   are convergent (no-op on repeat); appenders (record a fact / log /
   entry) append a *distinct* record per invocation — "idempotent" for
   them means atomic and serialized, **not** append-dedup. Say which is which
   in the spec and exercise a CAS writer racing an append writer.
8. **Lock the seam set structurally.** A write-sink ban test asserts the
   allowlist of write seams is *exactly* the intended set (each with one
   sanctioned write call) so package growth cannot smuggle in a new sink.
9. **Wrap filesystem errors into the typed channel, symmetrically.** The
   write itself (temp + `os.replace`) can fail on `ENOSPC`/`EROFS`/`EACCES`;
   catch it and raise the domain's typed `workspace-invalid` error so callers
   map it to a structured halt, never a raw `OSError` traceback. Do this on
   **every** write seam, including the CAS-exempt knowledge-plane stamp — an
   asymmetry (one seam guarded, its sibling not) is a latent crash; sweep both
   when you add the guard to one.

**Anti-patterns / caveats:** under a strictly single-operator contract, a CAS
re-read without a lock can be an explicit simplification. Do not retain that
assumption once a revision-neutral recorder, autonomous driver, or second
operator can write concurrently: two invocations can both read R and both
replace while CAS sees no conflict. Do not lock the replaceable state-file
descriptor, and do not add undeclared workspace metadata merely to host a
lock; use the stable containing directory when the platform supplies
descriptor locks.
A second, knowledge-plane write (e.g. a frontmatter stamp) must be a
*separate, explicitly-counted* seam — CAS-exempt and edit-only-the-
frontmatter (split at the fences, mutate the mapping, re-concatenate the
byte-for-byte-unchanged body) — not folded into the control-plane seam,
or "one writer for operational state" stops being true. Beware an
eligibility/precondition that a *consuming* derivation does not actually
surface (as in an implement-exit completeness check added *above* the ratified
derivation; a candidate pattern, not yet extracted —
and see [[pattern:verified-by-completeness-rule]]). The FS-error-wrapping symmetry
(recipe 8) matters on every *consumer* of this seam: `commit_state_write` once
crashed with a raw `OSError` while its CAS-exempt sibling `stamp_lifecycle` was
already guarded — keep the two seams'
fault handling symmetric (see [[pattern:typed-error-channel-rail]]).

**Concrete future-feature scenario:** Heddle `validate --fix` and
`heddle drive`, and a paid-review recorder all mutate `state.yaml`. Repair and
driver actions retain revision CAS, while the recorder appends without a
revision bump. All three acquire the same workspace-directory descriptor lock
around their read-transform-replace transaction, preventing a completed review
fact from disappearing between the CAS read and atomic publication.
