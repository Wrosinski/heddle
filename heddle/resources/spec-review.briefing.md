# Spec Review

You are at the **spec-review** stage. Establish that the Feature Spec has an
adequate product contract, consequential design commitments, and observable
acceptance criteria before downstream work relies on it.

## Authority

This session owns spec and plan refinements from accepted findings, review
dispositions, and the lead questions this stage needs, recorded with
`heddle decisions add`. It does not own phase exit, decision resolution, or a
scope change the owner has not approved. A pending native question, from a
gate REPORT or from the lead, holds the stage; continue only work that does not
depend on it.

## Owner rulings in force

The owner gave the spec and plan a green light at the specification checkpoint,
and the spec's Approved MVP cites that decision ID. Before the first gate run,
read that ruling, and any live ruling beside it, in the workspace `state.yaml`;
the gate cannot see them. Confirm that it is resolved and that the documents
match what the owner approved, apply any change the ruling made, and run
`heddle validate`. The ruling stands for scope per part, concept deltas,
witness shape, stages and grants; do not re-ask it. Confirm too that the
spec's Approved concept still matches its approved source, the bound research
reference or the workspace brief, and restore any drift.

When the Approved MVP instead records the checkpoint as off, confirm that
against `checkpoints.specification` in `heddle orient --json` or the owner's
recorded answer, and that the Approved concept carries an approval reference.
The research checkpoint's authorization then stands for the parts it
authorized, beside any ruling the Approved MVP cites for a change beyond them.
When neither a ruling nor a confirmed off record exists and no earlier owner
ruling covers the drafted documents, the green light is missing: before any
gate run or stage exit, record it as one class-8 approval of prepared work that
answers what is being built, how, and how it will be proven, with the scope and
footprint checks and the e2e grant, plus the class-5 live question when live
applies, and cite them once recorded.

## Selected review work

Read `heddle status --json` or `heddle orient --json` for the effective policy,
assignment, required reviewer slots, remaining allowance, and legal next action.
Run the selected `spec-review` calls through the emitted review action:
`heddle run-gate` for one slot, or `run-gates` when slots that read none of each
other's findings launch together under the host's `reviews.launch` setting.
Preserve their resolved execution tuples and independent contexts. Read every
required initial report before remediation. An off entry is intentionally not
run, not passed.

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
the product purpose and authorized scope, conceptual integrity and agreement
with the approved concept, consequential design commitments, canonical AC
quality, internal consistency, ambiguity, and necessity. Owners, dependency
direction, consumer promises, failure/recovery classes, and expensive-to-reverse
choices must be clear where they matter.

Private helper names, routine algorithms, and file layouts may remain delegated.
Missing EARS copies, implementation architecture, enforcement restatements,
hour estimates, or a particular document length are not defects. A missing
consequential commitment or observable acceptance clause still is a defect.
Use the delivered Decision routing policy for ownership; this briefing owns
the review change confirmation and its native recording duties.

## Resolve findings

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

## Review change confirmation and exit

Reviews tend to add requirements. Each addition deserves the owner's attention;
settled rulings do not. After applying accepted findings and before recording
dispositions, compare the spec and plan with what the owner approved for
changes that would alter the owner's answer, such as a part or AC added or
removed, different observable behaviour, a new concept delta, or a different
witness lane or cost. An addition the review proposed defaults to a named
deferred follow-up unless the contract needs it. Record the changes no gate
REPORT decision already owns as one class-2 `question`, an approval of prepared
work that confirms them item by item, not blanket, and cite its ID in the
Approved MVP. Ask nothing when nothing changed that counts.

The host (`checkpoints.review_changes` in `heddle orient --json`) or the spec's
Approved MVP may turn this confirmation off. It then still covers a change to
scope, the approved concept or a witness grant, but not refinements inside the
approved parts.

Settle user-required choices before progression; unresolved native questions
are blockers. Scheduled auto-resolvable setup may remain a plan task after
feasibility and authority are settled. A material change later is a class-2
question naming the original ruling, not a silent narrowing of proof.

Run `heddle validate` after document corrections. Exit toward **plan-review**
only when native readiness is satisfied and phase-exit authority permits it.
