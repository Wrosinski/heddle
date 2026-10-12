---
name: dual-identity-equivalence-evidence
area: architecture
status: active
created: 2026-10-12
superseded_by: null
---

# Dual identity for equivalence-tolerant evidence

**Intent:** let a judgment recorded against some content survive cosmetic
rewrites of that content without weakening any proof that depends on the exact
bytes.

**When to use:** a record (a review disposition, an approval, a waiver) was
made by reading content that formatters, hooks or round trips routinely
rewrite without changing its meaning, and re-earning the record after such a
rewrite is costly. It does not apply to evidence whose consumer reads raw
bytes: a test run, a format check, a hash-pinned artifact. That evidence must
stay exact.

**Recipe:**

1. Keep raw identity authoritative. Every record still binds the exact bytes,
   and every existing comparison, digest recipe and receipt keeps its meaning.
2. Record a second, optional identity beside each observation: a named,
   versioned rule id plus a canonical digest. The rule id carries every
   version that changes the canonical form (an interpreter or parser
   version), so a record made under another version compares raw.
3. Define each rule conservatively. Normalise only what is known to be
   cosmetic, and keep exact any construct the rule cannot classify safely.
   Where the content has a grammar, take block boundaries from the reference
   parser rather than restating the grammar by hand.
4. Two observations are equivalent when their raw identities match, or when
   both carry identities under the same rule id with equal digests. An
   observation without an identity, an unknown rule or a different kind or
   mode compares raw. Fail closed in every ambiguous case.
5. Compute identity lazily. Stamp it when a record is written, and on reads
   only for paths whose raw identity differs from the record, through one
   helper that every consumer calls. Reads pay nothing while content matches.
6. Return typed changes, not a boolean: each difference names its path and a
   kind (content, formatting-only, membership and so on). A membership or
   declaration change is reported on its own, even beside a formatting-only
   change to the same path, and is never equivalent.
7. Choose per consumer. Records whose meaning depends on understanding the
   content accept equivalence; records whose meaning depends on executing the
   content stay raw and, when stale, explain that the change was
   formatting-only so a rerun is known to be mechanical.
8. Upgrade without staling. Older records without the identity decode
   byte-exactly and compare raw. A record that names a manifest requires it,
   and a missing or altered one is an integrity error, never a raw fallback.

**Anti-patterns / caveats:** replacing raw identity with the canonical form,
which leaks canonical bytes into delivered content and ties every record to a
rule version; letting equivalence decide whether a check would pass; a
consumer that compares without stamping the current side, which fails closed
but silently defeats the feature; a reflow masking a membership change; a
rule that normalises inside protected constructs (code spans, link titles,
table cells, definitions) because a line-level heuristic missed the
container; computing identity on every read, which puts parsers on hot paths;
and changing a shipped rule's canonical form without bumping its id.

**Concrete future-feature scenario:** a host wants owner approvals of
`.heddle.yaml` or `pyproject.toml` to survive key reordering and comment
reflow by an editor. A `yaml-mapping/1` rule records a canonical digest of the
parsed document beside the raw bytes. Approvals compare by equivalence and
report `formatting-only` changes, while the commands that read the files
keep raw freshness and rerun after any byte change.
