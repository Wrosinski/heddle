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
the product purpose and authorized scope, conceptual integrity and agreement
with the approved concept, consequential design commitments, canonical AC
quality, internal consistency, ambiguity, and necessity. Owners, dependency
direction, consumer promises, failure/recovery classes, and expensive-to-reverse
choices must be clear where they matter.

Private helper names, routine algorithms, and file layouts may remain delegated.
Missing EARS copies, implementation architecture, enforcement restatements,
hour estimates, or a particular document length are not defects. A missing
consequential commitment or observable acceptance clause still is a defect.
Use the delivered Decision routing policy for ownership; this briefing retains
the post-spec-review scope checkpoint and its native recording duties.

## Checkpoint 1

When the spec's Approved MVP cites a specification-checkpoint decision ID, read
that ruling, and any live ruling cited beside it, in the workspace `state.yaml`
before the first gate run; the gate cannot see them. Confirm that the class-8
`Specification checkpoint` question is resolved and that the spec and plan
match what the owner approved. Apply any change a ruling made, such as a
narrowed part or a different live posture, then run `heddle validate`. A
citation that names no resolved checkpoint decision counts as absent. The
confirmed rulings stand for scope per part, concept deltas, witness shape,
stages and grants; do not re-ask what the owner already ruled.

Checkpoint 1 then confirms only what spec review changed: a changed part, a new
concept delta, or a changed AC set or witness shape that no gate REPORT decision
already owns. Apply those changes first, then record them together as one
class-2 `question` naming the original ruling, which lists each changed part and
each new concept delta for confirmation item by item, not blanket. Record
nothing when nothing material changed. It is approval only, as at specify: give
it a single option approving the revised documents, and say in its body that any
other outcome means leaving it pending and revising in an interactive
spec-review session, because resolving it lets the driver advance to plan
review. Confirm too that the spec's Approved concept still matches its approved
source, the bound research reference or the workspace brief, and restore any
drift.

### Work specified before the specification checkpoint

Without such a citation, the full Checkpoint 1 applies. Its scope batch carries
each labeled concept delta in the spec's Approved concept, confirmed item by
item as for changed parts; the lead reads the plan's Technical Architecture
against the approved approach and flow and records any departure as a delta
before the batch. Work drafted before concept notes existed needs no
retroactive concept.

The lead presents the plan's Integrated Witness Proposal with the scope batch,
including when spec review is Off, and checks the plan's concrete proposal and
any changed spec commitments while keeping the gate review at spec altitude. The
scope ruling explicitly confirms the e2e shape, execution stages and bounded
reruns for every posture; no separate e2e decision is needed. Recommend e2e
during implementation and milestones. Every declared lane has two runs: a
required one at implement exit, before any review, and a post-review rerun that
happens by default. The owner may opt out of a lane's rerun here; record that
choice in the ruling so a later lead can apply it as a standing grant through a
witness waiver. Reuse an existing scope decision; if none exists, including when
the reviewer is Off, record the lead-owned scope question with class 8. Apply
only the authorized owner's ruling or an identified standing grant.

For this earlier work, record one class-5 `question`, titled
`Live witness: <slug>`, when proposing live or declining it for a real-provider
feature. Bundle the posture and reason, all user-required prerequisites,
allowed effects and cleanup/recovery, time/turn/retry and
per-attempt/aggregate cost caps, live stages and bounded reruns, or the reason
and concrete fallback. Recommend live at implement exit with the default
post-review rerun, and size the caps for both runs. Scope confirmation and
credentials alone are not execution grants. Record the actual ruling, actor and
authority natively and reference exact decision IDs from the plan. Preserve
already applicable grants.

## Resolve and exit

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
