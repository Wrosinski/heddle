---
name: architecture-proposal-review
description: Review an architecture proposal against repository evidence, integration surfaces, alternatives, and engineering principles before implementation. Use when a user asks to critique a proposed design or architecture. The review is conversational and read-only unless the user requests an artifact.
---

# Architecture Proposal Review

Test a proposed architecture before anyone builds it. Decide whether its facts
hold, whether it serves the code that will use it, whether it draws the system
boundary in the right place, and whether its delivery plan is safe.

## Approach

- **Check the proposal against the repository.** Read the affected code, tests,
  records, and integration points yourself. The proposal's account of them is a
  claim to check.
- **Read-only by default.** Reply in the conversation. Write or change a file
  only when the user explicitly requests one, at the destination they give; ask
  for a destination if they gave none. A review request does not allow
  implementing the proposal or acting outside the repository. For who decides
  an open choice and how to ask, follow `heddle/resources/decision-routing.md`.
- **Judge by the engineering principles.** Use
  `docs/workflow/engineering-principles.md`. Cite the principle that decides a
  point by name, with its definition and the consumers you observed. Do not
  restate the principles in the review. For example, "One owner per authority"
  covers competing owners of policy, state, or writes; "Dependencies point
  inward, not back out" covers boundary crossings; and "Extend through data and
  one resolver, not parallel paths" covers proposed extension points.
- **Give two verdicts.** The architecture verdict and the delivery verdict are
  independent. A sound target can have an unsafe migration, order of work, or
  cutover. An easy delivery plan does not repair a weak architecture.

## Steps

1. **Find the proposal.** Use the documents the user named. If you cannot tell
   which proposal is meant, ask. Work out the change scope from the proposal
   and the repository.
2. **Check the facts.** Mark each factual claim that matters as **verified**,
   **contradicted**, or **unresolved**, with repository evidence such as
   `file:line`, a command, or an observed interface.
3. **Map what the change touches.** Trace upstream dependencies, downstream
   consumers, and the blast radius across production code, tests, fixtures,
   configuration, stored data, and migration or compatibility work in
   progress. Take requirements and invariants from the real consumers and
   their contracts, above all the first consumer or the one that is blocked.
4. **Check the boundary.** Look across the integration surface for adjacent or
   overlapping mechanisms, including parallel implementations and read/write
   pairs. Give each one a disposition:
   - **absorb**: the proposal should include it;
   - **exclude**: it is outside the boundary on purpose, with the reason; or
   - **missed**: the proposal did not account for it.

   Recommend unifying only when real callers show the same shape. Looking alike
   is not enough.
5. **Compare real alternatives.** Build materially different alternatives
   instead of variations on one idea. Include the smallest change that meets
   the robustness the contract requires and, where useful, a staged path to the
   same end state. For each option compare:
   - mechanism and ownership boundary;
   - fit to the verified consumer requirements;
   - benefit, effort, and blast radius;
   - important principle conflicts and failure risks; and
   - how easily the design and its rollout can be undone.

   Say plainly when the proposal is dominated: another option gives more
   benefit for the same effort, or the same benefit for less.
6. **Separate what belongs to others.** Do not quietly overrule product or
   scope choices the owner has already made. List owner decisions with their
   options, practical implications, and your recommendation. Put defects that
   exist with or without the proposal in a discoveries section, so they do not
   widen the proposal's scope.

## Result

Lead with the architecture verdict and the delivery verdict. Each is **adopt**,
**adopt with changes**, or **reject**, with a short reason. Then give only the
sections the evidence supports:

1. claim-verification ledger;
2. consumer, dependency, and blast-radius map;
3. adjacent-mechanism dispositions;
4. engineering-principle fit;
5. alternatives and how they compare on benefit and effort;
6. recommended delivery shape;
7. owner decisions; and
8. standalone discoveries.

Mark inference and uncertainty. Write for a reader who has not read the code
you read. Length follows the evidence; there is no fixed report length.
