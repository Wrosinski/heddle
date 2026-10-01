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

_(After research, answer three questions at the altitude of concepts and
high-level contracts: no file layouts, private helpers or signatures unless a
consumer depends on them. The prompts under each question say what a good
answer usually covers; they are not fields to fill, and a point that changes no
decision gets a line or nothing. When the admission-bound research reference
already holds an approved concept, cite it here instead. Otherwise the owner
approves this one at the research checkpoint; record the approval (owner and
date, or decision ID) at the end.)_

### What are we building?

_(The outcome and what it is not, by reference to the problem and outcome
above. The failure behaviour the first slice must handle: loud reversible
failures may defer, product edge cases are the owner's, and irreversible or
trust-destroying failures belong in the slice. Named follow-ups with the seams
that keep them additive, and seams deliberately not built. Owner questions the
spec must not start without.)_

### How are we building it?

_(The spec retains this answer verbatim, so keep its five labeled parts.)_

- **Approach:** the mechanism, the main alternative rejected with its reason,
  and why this is the simplest approach that works.
- **Flow:** trigger; inputs (source, shape, producer, trust); the steps where
  decisions happen; outputs (shape, consumer, persistence); side effects; one
  worked example with realistic data.
- **Contracts and interfaces:** each boundary crossed, marked new, changed or
  relied on unchanged; whom a changed contract breaks and what migrates.
- **State and ownership:** records created or changed, the single writer of
  each, their lifecycle, and what happens to records that already exist.
- **Footprint:** the codebase areas the change will touch, and nearby areas it
  must not.

### How will we know it works?

_(The end-to-end scenario that would show it works, the e2e/live posture, and
what is real versus doubled. This seeds the acceptance criteria without listing
them. Whether anything is judged rather than checked: a judgment-based aim is
an Assessment Target and never gates completion. The assumptions the concept
rests on, how we would notice one failing, and whether a spike or prototype
milestone should come first.)_

Approved by: _(owner and date, or decision ID)_

## External Services (optional)

_(Real providers/services the feature calls, credential sources by reference,
representative data/freshness, cost sensitivity, effects and recovery needs.)_
