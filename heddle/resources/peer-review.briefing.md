# Peer Review

You are at the **peer-review** stage. Review the integrated feature and resolve
the selected assignments' findings. Read the Feature Spec's commitments and
ACs, the plan's Technical Architecture and milestone decisions/discoveries, and
the touched modules before acting: you cannot assess a finding about code you
have not read.

## Selected reviewers and sequencing

Read each typed next action as current authority. Execute a command action only
through its supplied operation. For an authoring action, the lead authors the
payload from its exact references and input schema. For a decision action, the
driver pauses until the named user decision is resolved. A repeated unchanged
action remains subject to the existing bounded non-progress stop.

Use existing applicable test evidence within its source/command/environment
limits. After fixes, rerun affected checks through the host's configured runner
and required native scopes. A review or phase exit does not authorize a broad
suite or local e2e/live execution; these need an explicit request for that scope,
with prerequisites separate. Existing applicable grants persist. Required unrun
proof remains pending; optional unrequested regression is reported separately.
This does not change any review role's permitted actions.

Assess command adequacy against the host's chosen budget: every selected test
should protect a distinct contract or consequential failure at the cheapest
adequate layer. Inspect shared-fixture fan-out and lifecycle cost. Require a fast
feedback route and fast public-boundary acceptance, with packaging/e2e/live proof
kept separate. A required slow or flaky witness stays pending with its reason,
owner, review point and any substitute evidence; a budget exception must be
measured and scoped.

Read native status/orientation for the effective matrix and legal next actions.
The available whole-feature roles are `peer-review-sequential`, `behavior-review`,
and `complexity-review`; run only the confirmed assignments through `heddle
run-gate`. Reviewer sequencing follows the runtime's independence rule: launch
the confirmed initial assignments concurrently when they are independent,
meaning distinct roles or milestone scopes whose initial reviews do not read
each other's findings, with no pending fix or decision between them. Keep one
run per assignment slot at a time and preserve independent initial contexts,
including required secondary slots. Commands that carry `--expect-revision`
run one at a time with a fresh revision. Read all required initial reports
before remediation.

There is no synthesis authority or manual merged verdict. The lead accounts
for every original finding and coverage duty through native evidence-bound
dispositions. Off means intentionally not run, not passed. Neither a later clean
report nor a policy/model amendment erases an originating obligation or resets
spent rounds.

## Acting on findings

Apply supported IMPLEMENT fixes, rerun affected tests under their execution
authority, and commit the remediation with the finding references. `heddle
run-gate` records eligible REPORT decisions; do not duplicate those owners with
a manual decision write. Pending decisions from implementation remain visible,
and new peer-review decisions follow the current boundary's blocking semantics.
Use `prompt-authoring-standards.md#finding-structure` for classification.

Route accepted findings by what they change:

- An implementation defect changes code/tests and targeted verification.
- Documentary drift under an unchanged approved contract reconciles the
  Feature Spec and plan, then runs `heddle validate`.
- A change to an AC, labeled assumption, or prior user decision remains REPORT.
  Apply no contract-changing fix until resolved; then update its Decision Log,
  affected documents, implementation, and verification together.

Record dispositions for exact original run/finding references using `heddle
review disposition`. Affirmative evidence must support a closed claim. Preserve
every coverage gap and independent originating inspection duty; an assurance
from another reviewer is not the required inspection. The native decision and
qualified disposition are authority, not an edited Markdown review.

For source-bound evidence, finish edits, sync, format, inspect, then record
dispositions. Formatting is an authored byte change when an explicit raw-file
reference observes it; future formatting is never invisible to evidence.

## Follow-up review and evidence reuse

For further inspection after lead closure, request boundary-owned
`review round-open --input-json <file>` with schema
`heddle.review-round-input/v1`, the selected whole-feature role, scope `feature`,
purpose `verification` and a concrete reason. The complete JSON example is in
`implement.briefing.md`; use `behavior-review` or the actual confirmed role here.
Then execute the selected gate. The unsealed assignment must have remaining
allowance, all prior slots filled and no unresolved stop; off roles and sealed
acceptances require their own remedies. Targets cover mandatory concerns from
all preceding slots plus open originals. Reviewer accounting for every target
never substitutes for the lead's acceptance of the new `@coverage` obligation.

The launch advisory identifies changed selected source paths. Consider formatter
and commit hooks before spending a review call. Staged, unstaged and untracked
inputs remain reviewable without acknowledgement; an unavailable Git observation
is disclosed. Ignored records are still strict inputs when selected, and later
formatting can invalidate reviewed bytes. Early coverage advice helps resolve
ownership or evidenced outside-feature attribution; final coverage is compulsory.

Invoke `heddle review reaffirm --role <role> --scope feature --feature <slug>`
only after judging that the original resolutions apply to the current source.
Preview with `--dry-run`, or bind the transaction with `--expect-revision`.
The operation selects effective addressed/settled rows, preserves evidence hashes
and inspection/decision bindings, and qualifies the whole batch before copying
changed basis/time values. It checks current rows before returning a no-op.
Superseded and unresolved rows remain untouched. Stale proof, edited citations
or a missing required originating review need new qualifying evidence; no test,
provider call, decision resolution or missing coverage acceptance is implicit.

## Verification evidence

Keep five claims separate: **content identity** (the declared bytes match the
run), **command adequacy** (the right checks were selected), **semantic quality**
(the assertions prove the promised behavior), **operational reliability**
(repeatability and bounded operation), and **tool behavior** (correct execution
and reporting). A fresh provenance fact proves the first claim and recorded
process outcome only. Changed relevant source makes affected
proof stale; follow the native `heddle verify` remedy before the next boundary.

Source inspection and inspected assertions are not observed execution. A planned
command, known prerequisite, or review grant does not authorize broad suites,
installed full workflows, or real-provider tests. Preserve required unrun proof
as pending and request its actual execution scope.

## Further rounds and exit readiness

Use native closure and the remaining confirmed allowance. A disposition rerun
targets retained concerns and supported changes under
`prompt-authoring-standards.md#disposition-rerun`; it is not a fresh unrelated
discovery pass. Preserve original decision constraints, regressions, and reviewer
identity. A limit is an upper bound unless the owner confirmed fixed passes;
spent allowance or a no-progress stop requires the native decision route, not
an invented pass, a hidden lower cap, or an automatic reset.
At a reached confirmed cap, `heddle review allowance` may record an explicit
owner-approved absolute quality round total. It changes no provider retry
budget, invokes no provider, and leaves the stop for its own resolution before
ordinary round opening.

When Overlay R is off, this is the owning final verification boundary. After
all relevant fixes, retain current `heddle verify --scope acceptance` and
`heddle verify --scope smoke`, plus any declared live obligation under its
separate authority. When Overlay R is on, robustness owns final post-hardening
proof. Disabling all peer roles never waives those final proofs or older open
obligations. A deferral remains pending unless an authorized contract decision
actually changes what is required.

Read the Integrated Witness Proposal and its exact native ruling IDs. Reuse the
confirmed grants for both lanes within their stages, effects and bounded reruns;
live does not replace acceptance. With R Off, discharge declared live via
`heddle verify --scope live` before handoff even though the native peer boundary
does not require that scope. Native enforcement applies at robustness (traversed
even with R Off) and complete. Resolve missing or materially changed
choices with the original owner; older active work reuses sufficient approvals
without fabricating a past checkpoint or restarting completed stages.

When this is the final boundary, the lead performs any declared alignment check
after the last relevant fix and applicable passing run. Read the exact retained
artifacts against the declared spec/plan criteria. Misalignment blocks finishing;
quality beyond the contract is an observation, and explicit quality thresholds
remain binding. Under the plan's `### Verification Commands`, record one
Assessment: criteria,
assessor, native run reference and relevant source identity, exact artifacts,
conclusion, limits and separate quality observations. Session and spec summaries
reference it. Refresh after a relevant fix or replacement output; judgment is
not a verification fact. Source reviews may precede this final run. Preserve
their pending-proof limits. Recording the Assessment adds no independent review
obligation, but it changes authored plan inputs: native freshness and required
originating inspection still govern closure. Write it before any required
qualifying final rereview; otherwise validate and refresh affected dispositions
and review evidence afterward. When R is On, hand these duties to robustness.

Exit readiness requires closed applicable assignments, committed IMPLEMENT
fixes, resolved blocking decisions, and current applicable evidence. Follow
native routing toward **robustness** or **complete** without claiming feature
acceptance early. Phase exit remains caller- or driver-owned.

A degraded peer-review exit requires its own `smoke_disposition` decision;
inspect `heddle decisions add --help` and obtain explicit current user resolution
bound to the exact red evidence. Never relabel red smoke as green.
