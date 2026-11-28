---
name: implement-with-opus
description: Explicitly opt into Fable-led implementation with bounded Opus workers during an authorized Heddle implement milestone. Use only when the user selects this approach; ordinary implementation remains direct work by the lead.
disable-model-invocation: true
effort: high
---

# Implement with Opus

Deliver the milestone as one coherent, correct, and maintainable whole. Fable
leads. Opus workers implement the parts that can be handed over with a clear
contract.

## Approach

- **This skill changes how the work is done and nothing else.** Scope, stage
  rules, test permission, and review policy stay as they are. The user's
  request, Heddle, and the repository's rules come before this skill and
  before any assignment.
- **Delegate what is settled; keep what is shared.** Hand over a package when
  its inputs are settled, it has one clear owner, and its completion can be
  observed. Do the work yourself when it needs context that is still changing
  or the handoff would add little. Weigh uncertainty, the cost of a mistake,
  coupling, and how easily the result can be checked. Reasoning that several
  packages depend on, and integration, stay with the lead.
- **The lead keeps the records.** Commits, Heddle state changes, changes to
  the spec and plan, integration, and formal review stay with the lead, within
  the permission it already has.
- **A worker's result is evidence to check.** It can go stale after another
  package lands. A worker that returns before its completion criteria are met
  has reported progress; its package is still open.

## Steps

1. **Start.** The user invokes `/implement-with-opus` in a Fable session. The
   choice covers the current milestone unless the user names another scope
   within the implement stage. Run `heddle orient` and follow its next
   actions. Read `heddle/resources/implement.briefing.md`, the spec, and the
   plan. If Heddle's guidance files are missing from the host checkout, find
   the installation with `heddle doctor --json`. Read an editable checkout's
   resources where they exist; otherwise use the commands' help and the
   briefings Heddle routes to you, and say which guidance is still missing.
2. **Write each assignment** with
   `heddle/resources/work-package-brief.scaffold.md`, from the specification,
   milestone scope, scaffolded tests, consumer contracts, and the test
   permission that applies. Give the worker the context and instructions it
   needs, and leave reversible implementation choices to it. A defect in those
   inputs is a discovery to resolve through the workflow; it does not allow
   weakening the contract.
3. **Run the workers.** Use the `opus-implementer` agent defined in
   `.claude/agents/opus-implementer.md`. This skill runs the Fable lead at
   `high` effort, and the worker definition sets `xhigh` for Opus. Agent
   instructions and tool lists shape what a worker does; a worker still runs
   with the session's permissions. If the agent, model, or effort level is
   unavailable, say so; do not substitute another one silently or claim the
   requested configuration ran.
4. **Keep packages apart.** One owner writes a given path at a time. You may
   supervise multiple workers in parallel, and implement a separate package
   yourself, when the packages are independent, shared contracts are settled,
   write ownership does not overlap, and the integration order is clear. Track
   each package's owner, dependencies, and the source revision it was built
   on. If an overlap, a dependency, or a change to a shared contract appears,
   pause the affected package and brief it again from the current source
   before writing continues.
5. **Check what comes back** against the requirements and the actual
   consumers. Resolve the gaps that matter before dependent work starts.
   Reuse a worker's context when it is still useful; change the assignment or
   implement the package yourself when that deals better with the uncertainty.
6. **Integrate in dependency order** and rerun the affected checks on the
   integrated source.
7. **Finish through Heddle.** Follow its verification, review, remediation,
   and completion requirements. Add an extra verifier only when it answers a
   question the existing evidence does not; it does not replace formal review.

When the user switches back to standard implementation, let the active worker
finish or stop its handoff before the lead writes again. Keep its changes,
inspect the resulting source, and record the remaining work in the existing
handoff. Evidence gathered so far is kept, and it must be checked again before
anyone relies on it.
