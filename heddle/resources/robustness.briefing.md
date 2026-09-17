# Robustness

You are at the **robustness** stage. Stress the implemented feature against its
failure modes: edge cases, error paths, resource limits, and the invariants the
spec named. Follow the selected robustness work and resolve supported findings;
harden within the authorized contract.

## Selected Overlay R work

Use native status/orientation for the confirmed `robustness-analysis`
assignment, reviewer slots, allowance, and next action. Run only selected calls
through `heddle run-gate`, with independent initial contexts and all required
initial reports read before remediation. The runtime records canonical results
and decisions; the lead records evidence-bound dispositions, not a synthesis.

When Overlay R is off, there is no substitute inline review obligation. Follow
the native route: peer review owns the final proof boundary. Off is intentionally
not run, never a pass, and it cannot erase earlier findings or required evidence.

## What to stress

Error handling inside AC scope was built and tested during implementation;
this stage focuses on integration-level concerns (the Phase 8 contract):

- External dependencies unavailable or slow (APIs, databases, filesystem)
- Malformed, missing, or adversarial input
- Concurrent access and race conditions
- Resource exhaustion (memory, disk, connections, rate limits)
- Error messages: actionable for debugging, with diagnostic context
- Failure recoverability — no broken intermediate states left behind
- Security concerns (injection, authentication bypass, data exposure)

Rate severity and likelihood for each concern found. Harden inside the AC
defense boundary; record out-of-scope concerns as decisions or discoveries
(`decisions add` — a human-owned ledger write) rather than silently
expanding scope.

Add an adversarial case only when it protects a distinct credible risk. Inspect
shared fixtures, first-order consumers and total setup/launch cost before growing
a matrix or command. Preserve the host's fast feedback and fast public-boundary
acceptance budgets. A required slow or flaky witness remains pending with its
reason, owner, review point and any substitute evidence; record a measured,
scoped budget exception instead of dropping proof.

Apply accepted hardening in code and tests, run the affected verification after
each change batch, and commit the final robustness fixes. The gate is a
read-only evaluator; this phase session owns remediation.

Use the same reconciliation routes as peer review. Pure implementation defects
change code/tests. Documentary drift under an unchanged approved contract may
update the Feature Spec and plan followed by `heddle validate`. If hardening
would revise a criterion, assumption, or settled decision, leave the finding in
the REPORT route until it is resolved; then update the Decision Log, documents,
implementation, and verification as one coherent change.

Calibrate adversarial concerns to the declared trust boundary: heddle runs
as a personal local tool over files its own operator controls, so
hostile-filesystem and multi-tenant attack classes count only when the
spec declares such an adversary. When you surface an allegation of
tampering or misuse at Critical/Important, identify who the attacker
would be and confirm the spec admits that attacker; where it does not,
downgrade rather than escalate, and suggest the spec name the threat if
the exposure seems real.

## Conceptual validation

Validate behavior against the spec's *intent*, not just its letter: describe
scenarios as user goals ("a user who wants X does Y and sees Z") and verify
them against the running system. A technically-satisfied spec with the wrong
experience is a finding, not a pass.

Use five separate axes in the final assessment: **content identity** proves the
current declared bytes match a stable run; **command adequacy** assesses whether
the configured suite is the right one; **semantic quality** assesses the
strength of its assertions; **operational reliability** covers timing,
dependencies, and flakiness; and **tool behavior** covers Heddle's execution and
reporting. Fresh provenance does not collapse these judgments into one green
claim. The same freshness rule applies to post-hardening evidence.

## Convergence budget

The confirmed policy owns the allowance; native closure is checked before
exhaustion or no-progress stops. A **disposition rerun** targets prior concerns
and supported changes under `prompt-authoring-standards.md#disposition-rerun`.
Settled decisions and documented accepted-risk assumptions are not fresh findings
without contradictory evidence. Preserve every original run/finding reference,
coverage duty, and required originating inspection. Use `heddle review disposition`
for evidence-bound closure and native next actions for another round or an
explicit stop/budget decision. Do not invent fixed passes, a lower cap, a
synthesis, or a reset after a policy/model amendment.

## Smoke ownership during hardening

Apply three smoke classifications during hardening: feature-caused,
unattributed, and proven outside-owned. The first two block. For the third,
retain its command and failure output, show that the cause predates this work
and is beyond every owned path, and record it as unresolved; the result stays
red.

Before degraded closure, obtain explicit current user acceptance that names
the attribution and exact red evidence. Separately prove green acceptance and
green affected owned-consumer tests. Route the exception to the completion
record without calling smoke green.

## Final post-hardening proof

Inspect exact targets and nested launches before the commands below. Broad
suites and both local full-workflow e2e and real external execution require
explicit scope authority; completion-stage work and confirmed prerequisites do
not independently grant it. Existing applicable grants persist. Reuse valid
evidence and rerun affected checks after fixes, while retaining all required
native proof. Report required pending checks separately from optional unrun
regression; a narrower pass or skipped witness cannot satisfy the obligation.

When Overlay R is selected, after its assignments are closed and every accepted
fix is committed, prove the current relevant content under the applicable
execution grants:

```bash
heddle verify --scope acceptance
heddle verify --scope smoke
```

Read the Integrated Witness Proposal and follow its exact native ruling IDs.
Use the confirmed grants for both lanes within their approved stages, effects
and bounds. When live is declared, also run `heddle verify --scope live` against
the same relevant content; live does not replace acceptance. A declined lane
retains its owner-confirmed reason and concrete fallback. Deferring execution
leaves required proof pending; changing the obligation requires an explicit
contract ruling and reconciled commands and coverage. Route missing or materially
changed choices to the original owner. For older active work, reconcile existing
design/grants without inventing past approval or restarting completed stages.
Prerequisites and review authority do not grant external execution.

After the last relevant fix and final applicable passing run, the lead performs
any declared alignment assessment against the retained artifacts and spec/plan
criteria. Misalignment and unmet contract quality thresholds block finishing;
observations about quality beyond the contract do not. Keep one Assessment in
the plan under `### Verification Commands`, with criteria, assessor, native run/source
reference, exact artifacts, conclusion, limitations and separate quality
observations. Session/spec summaries reference it. Refresh after relevant changes
or replacement output; an old assessment cannot qualify a new run. Judgment is
not a verification fact. The robustness-analysis gate retains its existing
charter; there is no separate assessment-only review. Native freshness still
applies to authored plan edits and required originating-reviewer inspection.
Finish the Assessment before a needed qualifying rereview. If it is recorded
later, revalidate closure and refresh any invalidated disposition or review proof.

If any required result fails, repair the regression, rerun the affected tests
and any review dimension invalidated by the fix, then rerun the applicable
verification scopes. Final facts bind current declared relevant content,
commands and source inputs; committing unchanged content does not make them
stale.

Exit toward **complete** only when selected robustness assignments are closed,
originating findings and coverage are qualified, the tree is clean, and the applicable
post-hardening verification is green under existing smoke-disposition rules,
declared live evidence is current and any declared alignment assessment is
recorded. An authorized change to the obligation must already be reconciled;
mere execution deferral does not qualify exit (the `phase-exit` boundary is
driver- or user-owned).

After hardening, replace any earlier degraded-smoke ruling with a
current-stage, user-resolved `smoke_disposition`. The command schema is available
via `heddle decisions add --help`.
