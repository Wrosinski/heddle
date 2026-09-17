# Spec Review

You are at the **spec-review** stage. Establish that the Feature Spec has an
adequate product contract, consequential design commitments, and observable
acceptance criteria before downstream work relies on it.

## Selected review work

Read `heddle status --json` or `heddle orient --json` for the effective policy,
assignment, required reviewer slots, remaining allowance, and legal next action.
Run the selected `spec-review` calls through `heddle run-gate`; preserve their
resolved execution tuples and independent contexts. Read every required initial
report before remediation. An off entry is intentionally not run, not passed.

The lead's native evidence-bound dispositions establish closure. There is no
synthesis gate or separate prose verdict owner. A clean report cannot erase an
earlier finding, coverage gap, pending decision, or originating inspection duty.

## What the review pressures

The spec reviewer reads the Feature Spec, not the Implementation Plan. Check
the product purpose and authorized scope, conceptual integrity, consequential
design commitments, canonical AC quality, internal consistency, ambiguity, and
necessity. Owners, dependency direction, consumer promises, failure/recovery
classes, and expensive-to-reverse choices must be clear where they matter.

Private helper names, routine algorithms, and file layouts may remain delegated.
Missing EARS copies, implementation architecture, enforcement restatements,
hour estimates, or a particular document length are not defects. A missing
consequential commitment or observable acceptance clause still is a defect.
The ratified engineering principles can resolve engineering choices, not product
intent or scope authorization.

## Resolve and exit

Apply supported IMPLEMENT refinements to the spec. `heddle run-gate` records
eligible REPORT decisions; reuse those exact owners and resolve them only from
the authorized owner's ruling. Use `heddle decisions add` for new lead questions.
Record dispositions for exact original run/finding references
through `heddle review disposition`, retaining qualified evidence and any
independent inspection requirement. Do not silently treat absence in a later
report as settlement.

Use the runtime's next action for a necessary targeted round or cap/stop
decision; a limit is not a fixed number of required passes and is not closure.
Run `heddle validate` after document corrections. Exit toward **plan-review**
only when native readiness is satisfied and phase-exit authority permits it.
Checkpoint 1 confirms changed Implementation Parts per part, not blanket.
