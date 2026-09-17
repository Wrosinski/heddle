---
name: root-cause-analysis
description: |
  Root-cause a bug whose symptom may mislead: intermittent, flaky, or
  already "fixed" once and back. Produces the causal chain with evidence
  and a solution proposal, not the implementation.
---

# Root Cause Analysis

Explain the reported failure through a supported causal mechanism and propose
the change that would remove it. Distinguish a verified mechanism from proof
that it caused this particular incident. If the incident cannot be established,
report the remaining uncertainty and the evidence needed to resolve it.

## Decision ownership

Use `heddle/resources/decision-routing.md` for ownership and question quality;
feature kickoff delivers the resolved host policy in full. Preserve this skill's
analysis/authoring boundary and applicable standing authorization. A review or
diagnosis request alone grants no implementation authority.

## Scope and Authority

Investigate the reported symptom, relevant failure paths, and plausible causes.
Use the governing spec and applicable decisions for expected behavior; use source,
tests, logs, and reproducible observations for actual behavior. Read-only inspection
and relevant hermetic diagnostic checks are in scope. A diagnosis request alone
does not authorize implementation, permanent tests, live/paid reproduction,
production mutation, or unrelated cleanup. Honor additional authorization already
supplied for the task.

State expected versus actual behavior, affected inputs/environment, timing, scope,
and reproducibility from available evidence. Ask for missing information only when
it changes the investigation; finish independent checks while a question is open.
Do not invent a reproduction or treat an old successful run as current evidence.

## Causal Evidence

Follow the failing execution and state transitions far enough to identify the
condition that produces the symptom. Compare working and failing cases when useful.
Use causal tracing, fault trees, or difference analysis as appropriate; there is
no required number of whys, branches, or hypotheses.

Evaluate plausible alternatives against both supporting and contrary evidence.
Record an untested alternative as untested, not disproven. An absence of contrary
evidence is not itself confirmation. Correlated timing identifies a lead; the
mechanism and observations establish causation.

A supported root cause explains the observed manifestation conditions and identifies
the actionable defect, violated contract, or external condition. Explain how the
proposal removes the mechanism or prevents its recurrence under those conditions.
Do not force an unchangeable upstream outage into a fictional locally fixable cause.
Distinguish the cause from a mitigation, detection improvement, or recovery measure.
Reverting a fix can restore a symptom for both a causal fix and a workaround, so
reversion alone does not distinguish them.

For example, queue delay is a symptom when a trace shows another worker holding a
shared connection throughout a large transaction. Reducing transaction scope is a
causal proposal only if the transaction's atomicity is unnecessary under the actual
contract. Priority scheduling may mitigate delay without removing connection
starvation. Both proposals need their own evidence and trade-offs.

Reuse applicable investigation evidence. Recheck it when a relevant source,
environment, input, or contradictory observation changes. Distinguish inspection,
reproduction, and inference; a passing suite does not disprove an untested incident.

## Named traps

- **Premature termination:** stopping at a plausible label without explaining
  the mechanism or the observed conditions. Trace the decision-relevant links;
  mark an inaccessible link uncertain instead of extending the chain by guesswork.
- **Single-thread pursuit:** accepting an early hypothesis without testing
  plausible alternatives. State what observations distinguish them and which
  checks were possible. Missing contrary evidence is neither confirmation nor
  proof that contrary evidence must exist.
- **Symptom-fix conflation:** presenting mitigation, detection, or recovery as
  removal of the cause. Explain which mechanism the proposal changes and under
  what conditions recurrence is prevented. An external cause can be established
  even when only a local mitigation is available.

## Delegation

Delegate distinct hypotheses or independent path traces when that adds evidence
or saves time while the caller continues useful work. Intermittency or a prior
failed fix alone does not require parallel agents. Follow
`docs/workflow/subagent-exploration-patterns.md` for bounded briefs, model tier,
current tool capabilities, and evidence synthesis. A path trace is exploration
work; an investigation whose product is weighing contradictory evidence or
adjudicating a prior failed fix is the named case for the top tier. Give each
investigator the same observed problem and require supporting evidence, contrary
evidence, and unresolved limits.

## Deliverable and Completion

Use the requested report format; otherwise provide:

- **Finding:** confirmed cause or leading explanation, confidence, and incident-specific
  uncertainty, followed by expected/actual behavior and conditions.
- **Evidence:** causal chain with source locations and observations, hypotheses
  tested, contrary evidence addressed, and relevant checks with their actual results.
- **Proposal:** recommended change and its causal connection, alternatives when
  materially different, affected contracts, regression risks, and required authority.
- **Verification plan:** the smallest meaningful check that distinguishes the
  diagnosis from its alternatives and proves the proposed behavior if implemented.

The investigation is complete when the evidence supports the cause and proposal,
or when available in-scope checks are exhausted and the remaining evidence gap is
explicit. An unsupported causal link stays uncertain. End with the diagnosis and
next decision; do not implement the fix unless the user authorizes it.
