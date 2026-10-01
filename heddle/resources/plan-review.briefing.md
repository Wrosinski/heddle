# Plan Review

You are at the **plan-review** stage. Establish that the Implementation Plan
provides a feasible approach and technical architecture for the spec's contract
before scaffolding depends on its interfaces and milestone boundaries.

## Authority

This session owns plan refinements from accepted findings, native milestone
facts through `heddle milestone edit`, review dispositions, and the lead
questions this stage needs, recorded with `heddle decisions add`. It does not
own phase exit, decision resolution, or a scope change the owner has not
approved. A pending native question, from a gate REPORT or from the lead, holds
the stage; continue only work that does not depend on it.

## Selected review work

Use native status/orientation for the confirmed `plan-review` assignment,
reviewer slots, remaining allowance, and next action. Run only selected calls
through `heddle run-gate`, preserving execution tuples and independent initial
contexts. Read all required initial reports before changing the plan. Off means
intentionally not run, not a reviewer pass.

Closure belongs to native evidence-bound lead dispositions, not a synthesis or
the newest report. Preserve original findings, coverage gaps, exact REPORT
owners, and required originating inspections across rounds and policy changes.

An optional lead-authored assessment that supports native review closure is a workflow review record.
First run `heddle orient --feature <slug> --json` and use
its `data.workspace`; the legal directory is `<data.workspace>/reviews/`, even
when the host uses a nondefault workspace layout. Choose the record's final bytes
and location before binding it as evidence. Native dispositions remain
authoritative for review closure; Kickoff neither creates nor moves the record,
and an assessment is not required.
A product assessment remains an owned product artifact outside the protected workflow workspace.

## What the review pressures

For every assignment, check that the plan starts from the host's chosen time
budget and selects core behavior, uncertain or risky interactions, consequential
failures, fast feedback and fast public-boundary acceptance at the cheapest
adequate layers. Check exact selections, direct-consumer coverage, shared-fixture
fan-out, lifecycle and total nested-launch cost, and separately scheduled
packaging/broad/e2e/live proof. If essential evidence will miss that budget,
require its measured overrun, cause and named owner/reassessment point. Those
runs require explicit scope authority even at a phase boundary; a stored command
or confirmed prerequisite does not supply it. Keep required unrun proof pending
rather than narrowing the obligation. Use the host's runner conventions.

Check the confirmed Integrated Witness Proposal: complete flow through the real
application boundary, external-only doubles, meaningful pass conditions, AC
coverage with concrete fallback witnesses, justified live posture, prerequisites,
safe effects, credible caps with headroom, and explicit grants for both proposed
lanes. Follow the exact native decision IDs in workspace state, or the cited
research reference for an e2e grant made at the research checkpoint; a
lead-authored checkpoint question is not automatically supplied as prior review
ground. Unresolved user-required choices are REPORT questions, not scheduled
setup or reviewer choices. The final proof includes acceptance even when live is
declared.
Each declared lane is scheduled at implement exit, before any review, with a
post-review rerun the owner may waive.
Any alignment check names observable contract criteria and retained artifacts;
quality judgments belong to the spec's Assessment Targets and stay observational.
For existing active work, reconcile existing design and approvals rather than
requiring a retrospective checkpoint.

Read the plan and native milestone facts with the spec's commitments and ACs.
Check approach soundness, architecture fit, AC coverage, dependency sequencing,
verification feasibility, necessary environment context, risk/recovery, and
scope discipline. Trace each milestone to its ACs or a concrete runnability/
testability need. The plan must explain how its actual owners, interfaces, and
publication/proof boundaries satisfy the spec.

Each milestone needs bounded writes, applicable dependencies, Low/High
complexity, and an executable verification plan with an expected result.
Preserve explicit pending status where scaffolding has not yet created the
tests. Check prerequisite and execution-authority gaps without pretending a
plan review proves tests ran. Prefer a prototype for consequential uncertainty.

Do not demand duplicate ACs, Design Context, an enforcement register, hours,
task-count or line-count bands, or a blanket Idempotence section. Recovery
details are required where the proposed work can destroy or overwrite data.
Refer to the spec's contract and existing rule owners instead of requiring copies.

## Resolve and exit

Apply supported IMPLEMENT refinements to plan prose and use `heddle milestone
edit` for native facts. `heddle run-gate` records eligible REPORT decisions; reuse
those exact owners and the authorized ruling. Use `heddle decisions add` for new
lead questions. Use `heddle
review disposition` for evidence-bound original findings and coverage. A ruling
that accepts a departure from the spec's approved concept follows the
workflow's Impact Assessment and Re-Plan protocols, which amend the spec's
concept delta under that authority.

The review change confirmation also closes this stage. After applying accepted
findings and before recording dispositions, compare the plan with what the owner
approved, read in the workspace `state.yaml` from the rulings the spec cites,
for changes that would alter the owner's answer, such as structure no AC needs,
a new public surface or code area, or a different witness lane or cost. An
addition the review proposed defaults to a named deferred follow-up unless the
contract needs it. Record the changes no gate REPORT decision already owns as
one class-2 `question`, an approval of prepared work, and cite its ID in the
plan, or ask nothing when nothing changed that counts. When the host
(`checkpoints.review_changes` in `heddle orient --json`) or the spec's Approved
MVP turns the confirmation off, it still covers a change to scope, the approved
concept or a witness grant.

For source-bound evidence, finish edits, sync, format, inspect, then record
dispositions. Formatting is an authored byte change when an explicit raw-file
reference observes it; future formatting is never invisible to evidence.

Run `heddle validate` after changes. Follow native next actions for targeted
review, stops, and explicit budget decisions; do not invent another fixed pass
count or restart usage after an amendment. Exit toward **scaffold** only when
native readiness and the caller's phase-exit authority permit it.
