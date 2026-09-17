---
name: author-fact-and-artifact-from-one-production-path
area: architecture
status: active
created: 2026-06-17
superseded_by: null
---

# Author the fact and its audit artifact from one production path

**Principle:** [One owner per authority](../workflow/engineering-principles.md#architecture).

**Intent:** when a machine-readable state fact and a human-readable artifact
(journal, retrospective, report) must agree, write *both* from the same
production path so a completeness audit has a real fact-backed oracle — instead of
a "present + well-formed" check that a test double can satisfy vacuously.

**When to use:** any feature with an audit/journal artifact whose completeness
claim is "every X has an entry" (every autonomous resolution is journaled, every
processed item is reported). The risk this addresses: the only ground truth for
"every X" is the artifact itself (circular), so the check degrades to "the file
exists and parses." Not needed when the artifact is purely informational with no
completeness claim.

**Recipe:**

1. **One production payload drives both writes.** The production code that records
   the fact (`resolution_source: policy`, "item processed") also renders the
   artifact entry from the *same* payload, in the *same* loop — not a separate
   side effect, and never only in a test fixture/double.
2. **Audit the artifact against the recorded facts.** The completeness oracle
   reads the fact ledger and asserts a matching, well-formed artifact entry per
   fact — a real oracle, because the facts are an independent source of truth from
   the prose.
3. **Make the production path the authoritative writer.** At the close/audit
   boundary, have production *reconcile* the artifact — re-assert an entry for
   every recorded fact the audit reports missing/malformed — so a later session's
   overwrite cannot wedge the fact-backed check.
4. **Verify a reachable producer exists.** The path that authors the artifact
   must actually run in a real (non-double) execution. A producer mapped only to a
   terminal/no-session boundary authors nothing in production (see caveats).
5. **Keep the render/parse lossless.** The audit compares a rendered line to a
   canonically re-rendered fact; normalize both sides, never re-parse the rendered
   form (a delimiter inside a field otherwise wedges the comparison forever).

**Anti-patterns / caveats:** the headline trap is **a green-but-vacuous oracle**
— the acceptance test compares against a test double that writes the artifact,
while *no production path authors it at all*. One failure mode is a close-artifact
producer reachable only through a session double: the acceptance test is green
while a real run authors nothing. A second trap is downgrading the oracle to "present +
well-formed" because wiring the fact side is more work — that cannot detect an
un-journaled resolution, the exact thing the audit exists to catch. A third is a
lossy round-trip silently failing the match (see step 5).

**Concrete future-feature scenario:** Heddle `validate`/`validate --fix`
emitting a repair report whose "every fixed item is reported" claim is checked
against the recorded repair facts; or any future gate that records findings as
facts *and* renders a human report — both write from one path so the report is
audit-backed.
