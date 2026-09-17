# Gate Effectiveness Log

## Tracking Protocol

One entry per feature, written at Phase 9 in the shape defined by
`docs/workflow/workflow.md` § Gate Quality Monitoring: stable ID
`GE-<date>-<slug>`, runs per gate, true/false positive and false negative
classification with finding IDs, finding quality on fixed axes, trend against
the previous three to five features, and improvement proposals with their
owner decision written back. Findings are cited as `GE-<date>-<slug>/<id>`;
that citation is the observed failure a prompt-refinement record or decision
names.

When a gate catches a genuine issue → record as TRUE POSITIVE.
When a gate flags a non-issue → record as FALSE POSITIVE.
When a bug/issue is found post-gate that the gate should have caught
→ record as FALSE NEGATIVE (miss).

## Log

<!-- Append entries here as features complete Phase 9. -->
<!-- Format:

### GE-YYYY-MM-DD-[slug] — [Feature Name]

| Gate | Runs | Iterations (aggregate mean) | Critical / Important / Minor | TP | FP | FN |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| [gate] | [n] | [n] ([mean]) | [c] / [i] / [m] | [n] | [n] | [n] |

- Classification: [finding IDs per TP / FP / FN, with the gate that missed each FN and why]
- Quality: [precision, actionability, severity calibration, duplication, evidence — with finding IDs]
- Trend: [against the previous 3–5 features: convergence, finding mix, contradictions, near-cap, prompt-version boundaries]

| Proposal | Target | Evidence | Route | Decision |
| --- | --- | --- | --- | --- |
| [one line] | prompt / flow / workflow / enforcement | GE-…/[id] | refinement record / proposal / doc edit / enforcement | accepted / declined / deferred |

- Post-release: [issue; which gate should have caught it; why it was missed; the proposal it produces] (appended later, if any)

-->
