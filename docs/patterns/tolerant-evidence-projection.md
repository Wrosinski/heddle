---
name: tolerant-evidence-projection
area: workflow
status: active
created: 2026-07-14
superseded_by: null
---

# Tolerant evidence projection

**Intent:** preserve useful external evidence when its presentation drifts from
the preferred template, while keeping machine decisions deterministic and
making every recovery visible to the supervising session.

**When to use:** a gate, analyzer, importer, or other external producer emits a
human-readable artifact that also feeds a machine summary. Use this when the
semantic fields can be recovered unambiguously even if headings, line breaks,
or field placement differ slightly. Do not use it to guess missing values,
choose between contradictory values, or accept output whose meaning is
ambiguous.

**Recipe:**

1. **Keep the original artifact authoritative as evidence.** Store the
   producer's exact bytes. Never rewrite the evidence into the preferred shape
   merely to make extraction pass.
2. **Separate evidence from projection.** Parse the original artifact into a
   typed, normalized machine projection used for counts, routing, and status.
   Presentation normalization belongs only in that projection.
3. **Use a bounded recovery ladder.** Try the canonical grammar first, then
   named deterministic recoveries for known harmless drift (for example,
   labeled fields sharing a line). Each recovery must yield one unambiguous
   value for every required semantic field.
4. **Warn on every non-canonical recovery.** Emit a durable warning in the
   stored summary and the runtime response. The warning identifies the drift
   and asks the main/supervising session to review coherence and, when useful,
   adjust future structure.
5. **Preserve warning parity on cache hits.** A cached result must reproduce the
   same recovery warnings as the fresh extraction; caching cannot make a
   previously visible concern disappear.
6. **Fail only on unsafe meaning.** Missing required evidence, contradictory
   values, duplicate candidates with no deterministic winner, or malformed
   data that changes semantics remain typed failures. A cosmetic deviation by
   itself is never a rejection reason.
7. **Test all three classes.** Lock canonical output, at least one recoverable
   presentation variant, and ambiguity/contradiction negatives. Assert original
   artifact preservation, normalized projection equivalence, warning content,
   and fresh/cache parity.

**Anti-patterns / caveats:** do not make the parser silently permissive: a
recovered artifact without a warning hides producer drift. Do not feed an
artifact through an LLM "cleanup" step before parsing; that destroys evidence
identity and makes recovery nondeterministic. Do not overwrite the original
with the normalized projection. Most importantly, tolerance is not
best-effort guessing: when two interpretations remain plausible, fail and ask
for review.

**Concrete future-feature scenario:** a future security-review gate emits
`Severity: Important Likelihood: Low Confidence: High` on one Markdown line
instead of three canonical lines. The extractor recovers the three uniquely
labeled values, records a structure-drift warning, and produces the same typed
finding projection as canonical output. If it emits two different `Severity`
values, extraction fails as contradictory.

The governing rule is to warn and accept slight unambiguous structural
differences, then let the main session review coherence, while retaining strict
rejection for unsafe ambiguity. The gate extraction and summary pipeline has
fresh and cache warning regressions for this behavior.
