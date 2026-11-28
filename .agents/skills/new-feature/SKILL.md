---
name: new-feature
description: |
  Start a new Heddle feature: research it, agree the route and scope with the
  user, then enter the workflow that writes the Feature Spec and
  Implementation Plan. Takes an optional feature brief path. Use when starting
  a new feature from scratch within the current session.
---

# New Feature

Start new feature work through Heddle's own commands. This skill gets you into
the workflow. The `heddle` commands and the stage briefings they deliver hold
the rules. The skill takes an optional path to a feature brief.

## Approach

- **One feature, one workspace.** Resume work that already exists instead of
  starting a second workspace for it. For analysis without starting a feature,
  use the `pre-implementation-analysis` skill.
- **The user chooses the route.** Heddle recommends a route and a review
  matrix; a recommendation is not approval. Direct work creates no feature
  workspace, spec, plan, or review schedule and follows the user's scoped
  request. On the Heddle route this skill writes research and specifications,
  and no production code.
- **Heddle's commands write the records.** Do not hand-edit `state.yaml` or
  intake records, because the commands run their checks when they write them.
  When a command refuses, do what its message says.
- **Settled choices stay settled.** Keep research and scope choices that are
  already recorded. Reopen one only when new evidence bears on it.
- **Stop only at Heddle's checkpoints:** the research checkpoint, the
  specification checkpoint at the end of specify, and the review change
  confirmation after each document review. This skill adds no approval step
  of its own. For who decides an open choice and how to ask, follow
  `heddle/resources/decision-routing.md`; kickoff delivers the host's full
  policy.
- **Keep one spec and one plan per feature,** and let Heddle track progress.

## Steps

1. If the user gave a brief, read it. Keep the original research reference
   unchanged, because formal admission records its identity. After admission,
   add to the generated `plans/<slug>/brief.md` without rewriting the approved
   provenance.
2. Run `heddle orient --json`. If it finds existing work, follow its routing
   and resume that feature.
3. Research the intended outcome and the code it will touch, following
   `docs/workflow/brainstorming-guidelines.md`. Use
   `heddle search "<intent>" --titles-only` to find related specs and
   precedents before you scan directories. Keep a research reference.
   Delegate exploration only when the tools allow it, it is permitted, and it
   helps alongside your own work; combine what comes back into one result.
4. Propose the route, with scope, complexity, and testability, and give your
   reasons.
5. Describe the concept in the brief's `## Concept` section and offer the
   opt-in concept review.
6. At the research checkpoint, record in that section, before step 7 fixes the
   research reference:
   - the owner's approval of the concept;
   - whether the owner skips the specification checkpoint or the review change
     confirmation for this feature (both are on unless `checkpoints` in
     `.heddle.yaml` turns one off); and
   - the e2e execution grant, when the specification checkpoint will not run.
7. Record the intake with
   `heddle feature prepare <slug> --area <area> --from-file <research-input>`.
   The command's help defines the input. Preparing creates no formal feature,
   and the matrix it recommends is not approval.
8. Confirm the route with the user. For the Heddle route, show the complete
   resolved matrix and record the user's actual confirmation with
   `heddle feature policy <slug> --from-file <confirmed-policy> --expect-revision <N>`.
   Leave the host's exclusions as they are.
9. After the policy is confirmed, run `heddle feature start <slug>`. Follow the
   `next_actions` it returns, including a feature switch when it emits one, and
   take no other transition.
10. Run `heddle orient --feature <slug> --json`, then
    `heddle kickoff --feature <slug>` only when orient routes there.
11. From here kickoff and the specify briefing own the stage. Run
    `heddle validate` at the briefing's checkpoints. Advance only through
    `heddle phase-exit` or the driver, when the runtime and your execution
    permission allow it.

## Where the rules live

- `docs/workflow/brainstorming-guidelines.md`: research, splitting the work
  into parts, and scope approval for each part.
- `docs/workflow/workflow.md`: confirmation, execution permission, and the
  feature lifecycle.
- `docs/workflow/document-structure.md`: the spec, the plan, and the knowledge
  index.
