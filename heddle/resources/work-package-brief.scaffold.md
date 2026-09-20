# Work-package brief

Give an implementation worker enough context to exercise judgment without
reconstructing the lead's conversation. Keep governing requirements in their
canonical records and reference the relevant sections. Supply operative
instructions directly when the worker cannot reliably retrieve them.

Use the sections below without padding them or prescribing reversible internal
choices.

```markdown
## Purpose and outcome

<Why this work matters, who consumes it, and the finished outcome expected
within the active stage and milestone.>

## Governing context

<Relevant specification, plan, decisions, stage guidance, worktree, and current
source identity. Name the versions or working state on which the assignment
depends.>

## Scope and relationships

<Owned changes and their exclusive writer, parallel packages, boundaries,
affected consumers, shared contracts, dependencies, and integration order.
State which packages may proceed concurrently and which must wait. Include only
task-specific constraints.>

## Acceptance and evidence

<Observable criteria tied to the governing requirements, including
consequential failure behavior. Name required checks, their authorized scope and
expected outcomes, relevant retained evidence, and any mutable test resources
that prevent checks from running concurrently.>

## Return

State the outcome and provide a criterion-by-criterion evidence table:

| Criterion | Met / not met / not verified | Evidence and limits |
| --------- | ---------------------------- | ------------------- |

Identify changed artifacts and source examined. Include material assumptions,
departures, unresolved questions, and follow-ups where present. Distinguish
observed results from inference and identify evidence that requires a rerun
after integration.
```
