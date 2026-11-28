---
name: root-cause-analysis
description: |
  Find the real cause of a bug whose symptom may mislead: intermittent, flaky,
  or already "fixed" once and back. Produces the chain from cause to symptom
  with evidence, and a proposed fix. Does not implement the fix. Use when a
  failure needs a diagnosis before anyone changes code.
---

# Root Cause Analysis

Explain why the reported failure happens, with evidence, and propose the change
that removes the cause. The reader uses the result to decide what to do next
and has not seen your investigation.

## Approach

- **Diagnose; leave the fix to a later decision.** Reading code and running
  relevant hermetic checks need no permission. A request for a diagnosis does
  not allow implementation, permanent tests, live or paid reproduction, changes
  to production, or unrelated cleanup. Permission the user already gave for the
  task still applies. For who decides an open choice and how to ask, follow
  `heddle/resources/decision-routing.md`.
- **What can cause the failure is not yet what did.** Say whether you showed
  that a mechanism produces the symptom, or that it produced this incident.
- **Evidence decides.** Expected behavior comes from the spec and recorded
  decisions; actual behavior comes from source, tests, logs, and what you
  reproduce. Label each claim as inspected, reproduced, or inferred. An old
  passing run is not current evidence, and a passing suite does not disprove
  an incident it never exercised. Do not invent a reproduction.
- **An untested alternative is untested, not disproven.** Finding no contrary
  evidence does not confirm a cause. Events that happen together give a lead;
  the mechanism and what you observed establish the cause.
- **A cause differs from a mitigation, a detection, or a recovery.** Only a
  fix for the cause changes the mechanism. Reverting a change brings the
  symptom back for a real fix and for a workaround, so reversion alone does
  not distinguish them. An external cause can be established even when only a
  local mitigation is available; do not invent a local cause for it.
- **Depth follows the evidence.** There is no required number of whys,
  branches, or hypotheses. Mark a link you cannot reach as uncertain instead
  of guessing.

Example: a slow queue is a symptom when a trace shows another worker holding a
shared connection through a large transaction. Making the transaction smaller
fixes the cause only if the contract does not need it to be atomic. Priority
scheduling shortens the delay and leaves the connection starved. Each proposal
needs its own evidence and trade-offs.

## Steps

1. State the problem from the evidence: expected and actual behavior, affected
   inputs and environment, timing, scope, and how reliably it reproduces. Ask
   for missing information only when the answer changes the investigation, and
   finish the checks that do not depend on it meanwhile.
2. Follow the failing run and its state changes to the condition that produces
   the symptom. Compare a working case with a failing one when that helps.
3. List the other plausible causes and the observation that would tell them
   apart. Run the checks that are possible and record each result.
4. Name the cause: the defect, broken contract, or external condition that
   explains when the symptom appears and when it does not.
5. Propose the change and explain how it removes the mechanism or stops it
   recurring. Say when a proposal only mitigates.

Reuse earlier investigation evidence where it still applies. Check it again
when the source, environment, input, or a contradicting observation changed.

## Delegation

Delegate separate hypotheses or independent path traces when that adds
evidence or saves time while you continue useful work. Intermittency or a
prior failed fix alone does not require parallel agents. Follow
`docs/workflow/subagent-exploration-patterns.md` for briefs, model tier, tool
limits, and combining reports. Tracing a path is exploration work. Weighing
conflicting evidence, or judging why an earlier fix failed, is work for the
top model tier. Give each investigator the same observed problem and ask for
supporting evidence, contrary evidence, and what remains unresolved.

## Result

Use the report format the user asked for. Otherwise give:

- **Finding:** the confirmed cause or the leading explanation, your confidence,
  what is still uncertain about this incident and what would settle it, then
  the expected and actual behavior and the conditions.
- **Evidence:** the chain from cause to symptom with source locations and
  observations, the hypotheses tested, the contrary evidence and how it was
  answered, and each relevant check with its actual result.
- **Proposal:** the recommended change and how it removes the cause,
  alternatives that differ in substance, the contracts it affects, regression
  risks, and the permission it needs.
- **Verification plan:** the smallest meaningful check that distinguishes the
  diagnosis from the other explanations and proves the proposed behavior once
  it is built.

The work is done when the evidence supports the cause and the proposal, or
when the available in-scope checks are exhausted and the remaining gap is
stated. An unsupported causal link stays uncertain. End with the diagnosis and
the next decision.
