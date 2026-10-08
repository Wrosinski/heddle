Evidence has a `kind`, exact source `references`, and an `explanation` with the
checked trace or quoted clause and its consequence. Preserve Trace versus
Speculation: `trace` is a checked path, `test` a checked assertion, `execution`
an observed run, and `speculation` an unverified causal hypothesis. `absence`
means the source was checked and the evidence is absent; `unavailable` means
it could not be checked. Name the limit in `limitations`. Unavailable,
speculative or absent evidence cannot establish AC `pass`, full test coverage,
an addressed prior defect, or an observed regression. A coverage claim carried
over from an earlier round cites a `trace` or `test`; `absence` never means
"unchanged since the last round". Retain affected AC IDs, source quotations,
dimension/category and severity rationale in the finding's problem, impact and
evidence, without inventing additional machine fields.
