# Implementation Plan: NL Screening

Judgment prose for the nl-screening workspace; operational facts live in
`state.yaml` (WM-7). The `### Milestone <id>:` headings are the id-join
(§4.1) — `validate` cross-checks that every state milestone has a prose
section and vice versa.

## Quick Orientation

Tier 3, mid-implementation: the query parser (m1) is done and verified; the
criteria validator (m2) is current with tasks expanded.

### Milestone m1: Query parser

Scope: `src/example/screening/parser` turns natural-language phrases into
criteria AST nodes.

Work: phrase tokenizer, grammar table, AST builder, diagnostics for
unparseable fragments.

Notes: the AST shape is shared with `query_search` — see the brief.

Decisions (this milestone):

- Reuse the `query_search` AST node types rather than minting a parallel
  hierarchy; divergence would force a mapping layer in m2.

Discoveries (this milestone):

- The example phrases in the spec exercise only two of the three grammar
  arms; a third-arm phrase was added to the parser test corpus.

### Milestone m2: Criteria validation

Scope: `src/example/screening/validate` checks ASTs against the screening
vocabulary.

Work: field-name validation, range checks, unknown-field diagnostics, unit
tests for valid/edge/malformed criteria.

Notes: validator errors carry the failing field name verbatim (AC-3).

Decisions (this milestone):
[Recorded during implementation]

Discoveries (this milestone):
[Recorded during implementation]
