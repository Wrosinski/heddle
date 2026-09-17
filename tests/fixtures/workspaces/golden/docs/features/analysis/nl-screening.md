---
type: feature-spec
area: analysis
feature_name: NL Screening
lifecycle: active
---

# NL Screening

## Purpose

Translate natural-language screening criteria into the host's structured
query model, validate them, and surface ranked results. (Golden-fixture
spec — a Tier 3 feature mid-implementation; see workflow-model §10.2.)

## Acceptance Criteria

### AC-1: natural-language input parses into a criteria AST

Priority: MUST
Action: run the parser over the documented example phrases.
Expected: each phrase yields a criteria AST node; unparseable phrases
produce a structured diagnostic naming the failing fragment.

### AC-2: parse failures never crash the pipeline

Priority: MUST
Action: feed malformed input through the parser entry point.
Expected: a diagnostic result, exit code 0 from the library call, no
exception escapes.

### AC-3: criteria validate against the screening vocabulary

Priority: MUST
Action: validate ASTs containing valid, edge-case, and unknown field names.
Expected: valid criteria pass; unknown fields are rejected with the field
name in the message.

### AC-4: ranked results carry their matched-criteria provenance

Priority: SHOULD
Action: run a screening query end-to-end and inspect the result payload.
Expected: every result row names the criteria it matched.

<!-- AC-4 is deliberately absent from every milestone's `satisfies` in
     state.yaml — the golden corpus's `ac-unassigned` validate case
     (workflow-model §12.1 cross-plane conditions). -->
