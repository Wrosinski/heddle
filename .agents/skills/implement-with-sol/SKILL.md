---
name: implement-with-sol
description: |
  Explicitly opt into Astra-led implementation with bounded Sol workers during
  an authorized Heddle implement milestone. Use when the user selects this
  approach; ordinary implementation remains direct work by the lead.
---

# Implement with Sol

Deliver the authorized milestone as a coherent, correct, and maintainable
whole. Astra leads and delegates implementation to Sol where a bounded contract
and adequate evidence justify the handoff. Direct implementation remains useful
when the work needs shared, changing context or the handoff adds little value.

## Selection and authority

Invoke `$implement-with-sol` in an Astra session. This skill does not change the
lead's model or reasoning effort. The selection applies to the current milestone
unless the user specifies a different scope within the implement stage.

Run `heddle orient` and follow its next actions. Read
`heddle/resources/implement.briefing.md` and the governing records. The user's
request and governing Heddle and repository authority take precedence over this
skill and its assignments. This skill changes execution style, not scope, stage
authority, test authority, or review policy.

If Heddle-owned guidance is absent from the host checkout, use
`heddle doctor --json` to locate the installation. Read an editable checkout's
resources where available; otherwise use native help and routed briefings,
identifying any remaining guidance gap.

## Assigning work

Choose between direct work and delegation by uncertainty, consequence,
coupling, and verifiability. Retain consequential shared reasoning and
integration with the lead. Give Sol a cohesive outcome with settled inputs,
clear ownership, and observable completion criteria.

Build the assignment with
`heddle/resources/work-package-brief.scaffold.md`. Use the existing
specification, milestone scope, scaffolded tests, consumer contracts, and
applicable verification authority. Supply the context and operative
instructions the worker needs without prescribing reversible implementation
choices. A defect in those inputs is a discovery to resolve through the
governing workflow, not permission to weaken the contract.

Use `sol-implementer`, defined in `.codex/agents/sol-implementer.toml`. Its
reasoning effort is pinned to `xhigh`. Check the active harness's model,
context, and permission controls. Surface an unavailable agent, model, or effort
level rather than silently substituting one or claiming the requested
configuration ran. The custom agent's instructions are behavioral constraints;
workers inherit the active harness permissions.

## Execution and handoff

The lead may supervise multiple Sol workers in parallel when their work
packages are independent, shared contracts are settled, write ownership does
not overlap, and the integration order is clear. One owner may write a given
path at a time. The lead may implement a separate package concurrently under
the same rule. If an overlap, dependency, or shared-contract change emerges,
pause the affected package and re-brief it from current source before writing
continues.

The lead tracks package ownership, dependencies, and source identities while
retaining commits, Heddle mutations, governing-document changes, integration,
and formal review within its existing authority. Integrate in dependency order
and rerun affected evidence on the integrated source; a worker's passing result
can become stale after another package lands.

Inspect the returned changes and evidence against the governing requirements
and actual consumers. Resolve consequential gaps before dependent work. Reuse
worker context when it remains useful; change the assignment or implement
directly when that better addresses the source of uncertainty. Follow native
verification, review, remediation, and completion requirements. Do not add an
extra verifier without a distinct evidence need; supplementary assessment does
not replace formal review.

When the user selects standard implementation, finish or stop the active
worker's handoff before the lead resumes writing. Preserve its changes, inspect
the resulting source, and record remaining work in the existing handoff.
Changing execution style neither discards evidence nor establishes that it
still applies.
