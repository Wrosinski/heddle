---
name: architecture-proposal-review
description: Review an architecture proposal against repository evidence, integration surfaces, alternatives, and engineering principles before implementation. Use when a user asks to critique a proposed design or architecture. The review is conversational and read-only unless the user requests an artifact.
---

# Architecture Proposal Review

Challenge a proposed architecture before implementation. Determine whether the
proposal is factually grounded, serves its real consumers, uses the right
system boundary, and offers a sound delivery shape.

## Decision ownership

Use `heddle/resources/decision-routing.md` for ownership and question quality;
feature kickoff delivers the resolved host policy in full. Preserve this skill's
analysis/authoring boundary and applicable standing authorization. A review or
diagnosis request alone grants no implementation authority.

## Inputs and boundary

Use the proposal documents named by the user. If no identifiable proposal is
available, ask for it; otherwise derive the change scope from the proposal and
repository. Read the affected code, tests, governing records, and current
integration points rather than accepting the proposal's account of them.

The review is conversational and read-only by default. Write or modify an
artifact only when the user explicitly requests one. Use the destination they
provide, or ask for one. Reviewing a proposal never grants authority to
implement it or to perform external actions.

## Review method

### Establish reality

- Treat every material factual claim as untrusted until checked. Record it as
  **verified**, **contradicted**, or **unresolved**, with repository evidence
  such as `file:line`, commands, or observed interfaces.
- Trace upstream dependencies, downstream consumers, and the blast radius
  across production code, tests, fixtures, configuration, stored data, and
  in-flight migration or compatibility work where relevant.
- Extract the concrete requirements and invariants of actual consumers,
  especially the first or blocked consumer. Do not substitute the proposal's
  summary for their governing contracts.

### Check the system boundary

Sweep the integration surface for adjacent or overlapping mechanisms, including
parallel implementations and read/write twins. Give every relevant mechanism
one disposition:

- **absorb** — the proposal should incorporate it;
- **exclude** — it is intentionally outside the boundary, with the reason
  stated; or
- **missed** — the proposal failed to account for it.

Recommend unification only when real callers demonstrate a shared shape.
Coincidental similarity is not enough.

Assess the proposal against `docs/workflow/engineering-principles.md`. Cite
decisive principles by name and link the observed facts to the judgment; do not
copy a second standing-principles register into the review.

Apply "One owner per authority" to competing policy, state or write owners;
"Dependencies point inward, not back out" to actual boundary crossings; and
"Extend through data and one resolver, not parallel paths" to proposed extension
seams. Cite the governing definition and observed consumers, not the label alone.

### Compare credible choices

Construct materially different alternatives instead of polishing variants of
one idea. Include the smallest intervention that meets the robustness floor and,
where useful, a staged path to the same end state. For each option, compare:

- mechanism and ownership boundary;
- fit to verified consumer requirements;
- benefit, effort, and blast radius;
- important principle conflicts and failure risks; and
- reversibility of both the design and its rollout.

Place the options on a benefit-versus-effort frontier. Say plainly when the
proposal is dominated by a lower-effort or higher-benefit option.

### Separate architecture from delivery

Give an **architecture verdict** and a **delivery verdict** independently. A
sound target architecture can still have an unsafe migration, sequencing, or
cutover plan; a convenient delivery plan does not rescue a weak architecture.

Surface owner decisions separately. For each, explain the options, practical
implications, and recommendation. Do not silently overrule ratified product or
scope choices. Put defects that exist regardless of the proposal in a separate
discoveries section so they are not smuggled into the proposal's scope.

## Result

Lead with the two verdicts: **adopt**, **adopt with changes**, or **reject**,
followed by a compact rationale. Then present only the sections supported by
evidence:

1. claim-verification ledger;
2. consumer, dependency, and blast-radius map;
3. adjacent-mechanism dispositions;
4. engineering-principle fit;
5. alternatives and frontier judgment;
6. recommended delivery shape;
7. owner decisions; and
8. standalone discoveries.

Mark inference and uncertainty explicitly. Prefer exact evidence and concise
decision-ready prose over ceremony or a fixed report length.
