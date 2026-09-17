---
name: registry-over-literal-versioning
area: architecture
status: active
created: 2026-06-22
superseded_by: null
---

# Registry over literal versioning

**Principle:** [Extend through data and one resolver, not parallel paths](../workflow/engineering-principles.md#architecture).

**Intent:** make "can this runtime read this wire-format version?" a
*registry-membership* decision instead of a literal equality check, so the
known / behind / ahead cases split cleanly and remediation routes by direction
(ahead → upgrade the runtime; behind → migrate) instead of collapsing into one
opaque "wrong version" error.

**When to use:** any in-band-versioned format (`schema: foo/vN` on a state file,
a doc-schema id, a lockfile corpus version) that more than one runtime
generation will read, where an older *and* a newer producer can both appear.
Not worth it for a single-writer single-reader format that never spans versions.

**Recipe:**

1. **One registry is the single authority.** Hold the known versions as an
   ordered constant (`STATE_SCHEMA_VERSIONS = ("foo/v0", "foo/v1")`) plus a
   `CURRENT` alias. Every version decision consults the registry; keep the old
   single-version constant only as a back-compat alias *equal to* `CURRENT`,
   never as the thing membership is tested against.
2. **Replace `value != CURRENT` with `value in REGISTRY`.** A known version
   loads — *including a known-but-behind one, which must stay readably loadable*,
   not rejected. Only a version **not in the registry** is invalid.
3. **Split the invalid case by direction, not by a flag.** A not-in-registry
   value that parses as a *future* `foo/vN` (ahead — written by a newer runtime)
   routes to **upgrade the runtime** (you cannot downgrade it); any other
   (foreign/garbled) value routes to a repair/`doctor` path. A *behind* version
   surfaces an on-demand "run migrate" advisory — never forced.
4. **Register a version only at the milestone that can actually read it.** The
   entry *order* across milestones is itself a contract: registering `v1` before
   the code that reads it ships makes "v1 loads after step N" vacuous and leaves
   ahead-fixtures ambiguous. Stage the registry additions with the readers.
5. **Lock it with a structural test.** Assert the literal-equality check is gone
   and that decisions key off the registry (not a bare constant) — prove
   *behavior* (a behind version loads, an ahead version is rejected with the
   upgrade hint), not just token presence.

**Anti-patterns / caveats:** the classic bug is hinting `migrate` for an *ahead*
schema — migrate only advances behind→current, so an ahead file needs a runtime
upgrade; conflating the two mis-routes every newer-producer file. A second trap
is treating "behind" as invalid: known-but-old must keep loading or you churn the
whole existing corpus on every bump. Don't let `CURRENT` and the registry drift —
`CURRENT` must be a member.

**Concrete future-feature scenario:** knowledge-plane document schemas and
`.heddle.lock` each carry a version;
both reuse this registry + directional-routing recipe verbatim rather than
re-deriving a per-format "is this readable" check, and both get the
ahead→upgrade / behind→migrate split for free.
