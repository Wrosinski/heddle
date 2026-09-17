---
name: contract-value-citation-lock
area: contracts
status: active
created: 2026-06-11
superseded_by: null
---

# Contract value citation + lock

**Intent:** encode ratified/external values as typed constants that
cannot drift silently from their source or from their own spec.

**When to use:** any feature whose job is transcribing pinned values
(design-doc decisions, wire schemas, vocabularies, exit codes) into
code rather than inventing them. Not for values the feature itself is
free to choose.

**Recipe:**

1. Encode each value as a typed constant (frozen dataclass, `str`/`Int`
   Enum, plain tuple) with a **citation comment** beside it naming the
   governing contract or doc section, such as the workflow model
   [interface semantics](../design/workflow-model.md#interface-semantics).
2. Add a **value-lock test** per constant that asserts against a
   *transcribed literal* — never re-import the constant under test, or
   the test proves nothing (edit-detection: a transcription typo fails
   loudly even when no other test exercises it). Lock **negative
   membership** too where the contract pins what something is NOT
   (for example, `milestone-review` is not a verdict-producing gate) — exact-set locks beat suffix/pattern
   inference, which silently admits future misnamed members.
3. Where the contract mirrors a live structure, add a **tripwire test**
   asserting ordered equality against the live side (e.g.
   `tuple(f.name for f in dataclasses.fields(LiveType))`) so either
   side drifting fails the build.
4. Exact/ordered wire contracts compare with **direct equality** —
   never `sorted()`, sets, or substring matching (a sorted comparison
   accepts wire output the contract pins as ordered).
5. Let review check citation fidelity against the source docs; the
   tests check shape and value, review checks provenance.

**Anti-patterns / caveats:** filling unpinned values with invented ids
("contract fiction") — advertise `null`/empty and grow additively at
the milestone that pins them. A contradiction between a ratified source
and the code is a stop-and-amend event, never a silent local fix. A
**"sensible default" constant that duplicates a value already ratified
and owned by another module** is a citation-lock violation even when it
looks like a free choice — bind to the source, don't re-declare it. For
example, `verify` once invented a 2 s timeout literal instead of inheriting
the host-policy `run-gate` default `2700/900`; the mismatch survived until
review because the value-lock layer was missing for a cross-module default.

**Concrete future-feature scenario:** Heddle `validate` pins payload
schema ids, while the kernel encodes resolver vocabularies — both
transcribe ratified values and need the same three lock layers.
