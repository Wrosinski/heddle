# Inception Brief: {{slug}}

## Problem

_(What hurts today and for whom.)_

## Desired outcome

_(What exists when this feature ships and what is deliberately outside it.
Name any judgment-based aim, such as the analytical quality of an output,
separately: it becomes an Assessment Target, not a success criterion.)_

## Initial thinking

_(Relevant code and precedents, consequential uncertainties, and the smallest
useful end-to-end approach. Record the proposed scope, complexity, and
testability axes and workflow route for explicit confirmation, not a
tier-derived approval.)_

## Concept

_(After research, describe the feature at the altitude of concepts and
high-level contracts: no file layouts, private helpers or signatures unless a
consumer depends on them. A section that changes no decision is one line or
"none". When the admission-bound research reference already holds an approved
concept, cite it here instead. Otherwise the owner approves this one at the
research checkpoint, before the spec and plan are written; record the approval
(owner and date, or decision ID) below the list.)_

1. **What we're building:** the outcome and what it is not, by reference to the
   problem and outcome above.
2. **Approach:** the mechanism in a paragraph, and the main alternative rejected
   with its reason.
3. **Flow:** trigger; inputs (source, shape, producer, trust); the steps where
   decisions happen; outputs (shape, consumer, persistence); side effects; one
   worked example with realistic data.
4. **Contracts and interfaces:** each boundary crossed, marked new, changed or
   relied on unchanged; whom a changed contract breaks and what migrates.
5. **State and ownership:** records created or changed, the single writer of
   each, their lifecycle, and what happens to records that already exist.
6. **Assumptions:** each with what breaks if it is false and how we would
   notice.
7. **Failure behaviour:** loud reversible failures that may defer, product edge
   cases for the owner, and irreversible or trust-destroying failures the first
   slice must handle.
8. **Proof sketch:** the end-to-end scenario that would show it works, the
   e2e/live posture, and what is real versus doubled. This seeds the acceptance
   criteria without listing them; a judgment-based aim is an Assessment Target.
9. **Unknowns and risks:** what could invalidate the concept, and whether a
   spike or prototype milestone comes first.
10. **Future considerations:** named follow-ups with the seams that keep them
    additive, and seams deliberately not built.
11. **Open decisions:** owner questions the spec must not start without.

## External Services (optional)

_(Real providers/services the feature calls, credential sources by reference,
representative data/freshness, cost sensitivity, effects and recovery needs.)_
