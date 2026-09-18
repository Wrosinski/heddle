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

An optional lead-authored assessment that supports native review closure is a workflow review record.
First run `heddle orient --feature <slug> --json` and use
its `data.workspace`; the legal directory is `<data.workspace>/reviews/`, even
when the host uses a nondefault workspace layout. Choose the record's final bytes
and location before binding it as evidence. Native dispositions remain
authoritative for review closure; Kickoff neither creates nor moves the record,
and an assessment is not required.
A product assessment remains an owned product artifact outside the protected workflow workspace.

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
Use the delivered Decision routing policy for ownership; this briefing retains
the post-spec-review scope checkpoint and its native recording duties.

## Resolve and exit

At Checkpoint 1, the lead presents the plan's Integrated Witness Proposal with
the scope batch, including when spec review is Off. Keep the gate review at spec
altitude; the lead checks the plan's concrete proposal and any changed spec
commitments. The scope ruling explicitly confirms the e2e shape, execution
stages and bounded reruns for every posture; no separate e2e decision is needed.
Recommend e2e during implementation, milestone and final verification.
Reuse an existing scope decision; if none exists, including when the reviewer
is Off, record the lead-owned scope question with class 8. Apply only the
authorized owner's ruling or an identified standing grant.

Record one class-5 `question`, titled `Live witness: <slug>`, when proposing
live or declining it for a real-provider feature. Bundle the posture and reason,
all user-required prerequisites, allowed effects and cleanup/recovery,
time/turn/retry and per-attempt/aggregate cost caps, live stages and bounded
reruns, or the reason and concrete fallback. Recommend live at the owning final
boundary. Scope confirmation and credentials alone are not execution grants.
Record the actual ruling, actor and authority natively and reference exact
decision IDs from the plan. Preserve already applicable grants.

Settle user-required choices before progression; unresolved native questions
are blockers. Scheduled auto-resolvable setup may remain a plan task after
feasibility and authority are settled. A material change later is a class-2
question naming the original ruling, not a silent narrowing of proof.
For existing active work lacking this section, reconcile existing design and
approvals; ask only about missing or materially changed choices, without
fabricating a past checkpoint or restarting completed stages.

Apply supported IMPLEMENT refinements to the spec. `heddle run-gate` records
eligible REPORT decisions; reuse those exact owners and resolve them only from
the authorized owner's ruling. Use `heddle decisions add` for new lead questions.
Record dispositions for exact original run/finding references
through `heddle review disposition`, retaining qualified evidence and any
independent inspection requirement. Do not silently treat absence in a later
report as settlement.

For source-bound evidence, finish edits, sync, format, inspect, then record
dispositions. Formatting is an authored byte change when an explicit raw-file
reference observes it; future formatting is never invisible to evidence.

Use the runtime's next action for a necessary targeted round or cap/stop
decision; a limit is not a fixed number of required passes and is not closure.
Run `heddle validate` after document corrections. Exit toward **plan-review**
only when native readiness is satisfied and phase-exit authority permits it.
Checkpoint 1 confirms changed Implementation Parts per part, not blanket.
