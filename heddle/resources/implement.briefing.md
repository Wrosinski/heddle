# Implement

You are at the **implement** stage. Complete one milestone at a time, drive its
scaffolded contracts green, apply actionable gate findings, preserve the living
design record, and finish with feature-level acceptance plus system health
evidence. One verified milestone at a time keeps errors from compounding
across unverified work.

Mindset: a builder exercising sustained judgment. Investigate before acting —
read code in its current state rather than trusting a description of it.
Match the module's existing patterns before abstract ideals. Read the spec's
relevant commitments and ACs, then the plan's Technical Architecture and the
actual consumer interfaces. Keep reversible internals delegated. When the plan
is wrong, surface it rather than patching around a broken assumption.

The same holds when Heddle itself is wrong: a command,
gate, briefing, or projection behaving against its contract or your
expectation is a tooling discovery. Stop, append a row to
`plans/friction-log.md` with observed versus expected behavior, classify the
cause, and propose the simplest principled fix — through `heddle decisions add`
when it blocks you. Never patch the runtime inside this feature; treat those
steps as the Tooling Discovery Protocol.

## Authority

This session owns implementation and test changes, task facts, verification
runs, accepted gate remediations, plan continuity, and spec reconciliation
required by a genuine design discovery. It does not own phase exit, milestone
advance, decision resolution, or unrecorded scope expansion. Use
`heddle decisions add` for user-owned questions; gate REPORT findings are
recorded by `heddle run-gate` and must not be duplicated.

## Session orientation and codebase immersion

All verification commands below follow the user's authorized test scope. Start
from the host's chosen time budget and inspect actual targets, shared fixtures,
first-order consumers and nested launches before execution. Choose the least
costly layer that still proves the contract, covering normal operation,
uncertain or high-risk interactions and failures with serious consequences.
Keep the edit loop quick and retain a quick acceptance check at the public
boundary; include related slow cases for a distinct contract reason. If an
evidence obligation will overrun that budget, preserve
its measured excess, narrow rationale, responsible owner and recheck point.
Broad suites and both local full-workflow e2e and real external tests require
explicit execution authority; a phase
boundary or inherited plan command does not supply it. Prerequisites are
separate, and existing applicable grants persist. Keep required unrun scopes
pending; do not replace their native proof with a narrower pass.

Before editing:

1. Run `heddle orient` and `heddle status --json`.
2. Read the plan's Quick Orientation, Technical Architecture, current and next
   milestone prose, relevant spec pointers, and the latest session fact.
3. Reconcile material changes to the spec's commitments and ACs with the plan's
   approach. Apply any `[PROPAGATE]` tags left unprocessed by earlier milestones
   before starting new work; do not create a copied Design Context inventory.
4. Read recent git history and inspect the working tree.
5. Run `heddle verify --scope smoke` under its applicable execution authority.
   Repair pre-existing breakage before adding work unless the Smoke ownership
   policy below classifies it as proven outside-owned.
6. Establish the affected modules, callers, tests, and enforcement rules from
   relevant reads and applicable earlier evidence. Ground decisions in the
   governing contracts; explain the decisive trade-off and label uncertainty.

Reuse investigation that still applies to the current source, contract,
environment, and question. Runtime-owned verification remains subject to its
freshness checks and the required commands above. Revisit a conclusion for a
changed input or concrete concern; a plan summary or prior PASS is not proof.

Orientation scales with blast radius. A local text edit needs direct reads and
focused checks. A shared interface or invariant needs its exact definition,
upstream and downstream dependencies, consumers, and tests. Make surgical
edits that preserve unrelated bytes and recorded user decisions. Every
permanent test must protect an acceptance criterion, invariant, regression, or
enforcement boundary; keep one-off probes ephemeral. Route discoveries that
change the planned approach through the re-plan path.

Read the spec sections needed for the current working frontier. Broaden the
read when an impact assessment needs labeled assumptions, a shared contract
changes, or a concrete gap remains; pointers are navigation, not copied authority.

Changing a shared symbol, signature, exception, field, or invariant requires a
package-wide blast-radius sweep and the tests for every affected consumer. Apply
the same sweep when a review remediation changes that shared contract; earlier
selection predates the fix. A patterned defect requires a class sweep, not a
one-site patch.

## Milestone loop

### Typed next actions

Treat the public action value as the routing contract. A command action contains
the complete operation to execute and is revalidated against current authority.
An authoring action carries `feature`, `work`, `references`, `input_schema`, and
`expected_revision`; the lead authors the payload from those original references
and submits it through the named schema. A decision action carries `feature`,
`decision_id`, `choices`, and `routes_to`; the driver pauses for the user-owned
choice. Preserve bounded non-progress behavior when an unchanged action repeats.
Do not reconstruct these fields from display text or route by action class names.

During long runs, give the user a one-line progress note before each milestone
step and each gate invocation. Keep these notes in the session conversation;
formal gate artifacts retain their required structure and non-narration rules.

1. Expand the milestone into cohesive, dependency-ordered tasks with
   `heddle task add`; set and maintain the current-task marker. Check the
   list against the milestone's work, scope, and `satisfies` ACs — add what
   is missing, drop what falls outside scope.
2. Implement in dependency order, writing tests alongside behavior. Independent
   work packages may proceed in supervised parallel under the Collaboration
   topology below; the lead-owned current-task marker remains the integration
   frontier, not a worker scheduler. Before a package's first edit, read the
   files it touches in their current state and the callers of every interface
   it changes, and note which ACs and invariants the change must satisfy. When a
   package creates, renames, or splits a test that satisfies an AC, update that
   AC's `Verified-by:` route in the same task. Keep writes within `owns`. If an
   entangled contract fix needs another path, record the path and reason
   immediately so the boundary owner amends `owns` before advancement.
3. Run the authorized `heddle verify --scope <milestone>` and exact relevant
   consumer tests discovered by the contract sweep. Diagnose the first causal failure; do
   not weaken a scaffolded contract to obtain green. Keep the progressive
   `test_command` green for survivor pins and current/completed behavior;
   future-red tests remain in their milestone, acceptance, or live scopes.
   Progressive command output and session prose are feedback; native proof is
   recorded only through a verification action authorized for this stage.
   Host tooling supplies `.heddle.yaml`'s `autopilot.test_command` to the
   additional close suite. Keep it distinct from feature proof. Heddle does not
   create a clean environment for that suite; execution stays in the current
   host checkout.
   If verification still fails after five fix-and-retry cycles, record the
   failing check, what was tried, and why it is not converging via
   `heddle decisions add` instead of attempting a sixth.
4. Commit the verified milestone work using the repository commit procedure.
   Commit-readiness: milestone verification and lint green, no debug
   artifacts or commented-out code, `Verified-by:` routes current,
   `[PROPAGATE]` tags applied or explicitly deferred, message naming the
   milestone.
5. Follow the confirmed `milestone-review` assignment through `heddle run-gate`.
   Off means intentionally not run, not passed. Read all required initial
   reports before remediation; Minor findings are audit-only. Apply supported
   Critical/Important IMPLEMENT fixes, rerun affected milestone/consumer proof,
   and commit the remediation. If a fix changes a shared contract, refresh the
   consumer census before choosing that proof and record inspected consumers,
   exact witnesses, and required pending witnesses. A neighboring failure's
   location alone does not establish that it is unrelated. Record exact original findings and coverage
   through `heddle review disposition`; preserve independent inspection duties
   and REPORT owners. Native closure and next actions determine whether another
   targeted round or explicit stop/budget decision is needed. Do not invent a
   synthesis, fixed pass count, or reset allowance after a policy/model change.
   If the confirmed cap is reached, `heddle review allowance` records only an
   owner-approved absolute quality round increase. It invokes no provider and
   leaves the assignment stop for its separate native resolution.
6. Record progress and continuity with `heddle session log` — completed,
   started, key context, next steps, blockers. Use native task facts and
   `heddle status` for operational position. The boundary owner can refresh
   the existing managed plan-status region when a document view is useful.
   Keep routine progress out of authored Quick Orientation and milestone prose
   after review: those bytes are reviewed inputs. For the
   next frontier, read the relevant spec sections and record reading pointers
   in the session handoff. Reconcile genuine design changes and propagate
   durable Decisions/Discoveries before review where possible; later governing
   edits require fresh evidence at the existing boundary. The next session
   resumes from native facts. Milestone advance remains boundary-owned.

Before starting the final milestone, read the Integrated Witness Proposal and
its exact native decision references, then inspect the live E2E prerequisites and
their recorded approvals. Checkpoint 1 settles user-required choices and explicit
grants for both lanes; scheduled auto-resolvable setup may still need completing.
Reopen a materially changed choice through a class-2 question naming its original
ruling. Record a genuinely missing live choice with class 5; obtain missing e2e
authority in the scope ruling, using class 8 for a new lead-owned scope question.
A required lane deferred from execution stays pending. For older active work,
reconcile existing design and approvals without
inventing a retrospective checkpoint or restarting completed stages.

Use the checkpoint's explicit grant for e2e iteration at its approved
implementation/milestone stages and for live at its approved final boundary.
Without such earlier authority, local e2e and live workflows remain scheduled
after implementation or at final verification. Design or
collection does not establish execution, and a skipped case does not prove its AC.

Verification and a clean scoped commit remain required even when the confirmed
matrix disables milestone review. Review policy does not grant test, provider,
or external-effect authority.

## Reading verification evidence

Keep five claims separate whenever you report or act on verification:

- **Content identity** says the declared source bytes stayed stable during the
  run and still match now.
- **Command adequacy** asks whether the configured command covers the intended
  scope.
- **Semantic quality** asks whether the tests make meaningful assertions about
  the acceptance criteria.
- **Operational reliability** covers repeatability, timing, dependencies, and
  flaky behavior.
- **Tool behavior** covers whether Heddle executed, captured, and reported the
  attempt correctly.

A fresh provenance row establishes the first claim and a process result; it
does not establish the other four. Changed relevant source makes affected proof stale: rerun
stale evidence through `heddle verify`, and evaluate test meaning and runtime
quality separately.

`heddle run-gate` records eligible REPORT findings. Do not duplicate those
facts. While an implement milestone is open, they accumulate in the
`pending-decisions` batch surfaced at the implement boundary.

## Follow-up review and evidence reuse

Finish formatting and applicable commit hooks before a paid review when
appropriate. Launch emits an advisory for exact selected nonignored inputs with
staged, unstaged or untracked changes; Git observation failure is unknown, not
clean. Advice adds no acknowledgement flag or execution authority. Ignored
records still participate whenever the review or citation contract selects them.

When another inspection is warranted, the boundary owner can use
the Heddle `review round-open --input-json <file> --feature <slug>` operation
with this payload. Round opening remains boundary-owned:

```json
{
  "schema": "heddle.review-round-input/v1",
  "role": "milestone-review",
  "scope": "m1",
  "purpose": "verification",
  "reason": "Verify the original required concerns against current inputs."
}
```

Use the actual role and scope, then launch that confirmed `heddle run-gate`.
Verification can follow lead closure while the assignment remains unsealed
and allowance is available. Required targets include the preceding completed
round's concerns across slots and still-open originals, including `@coverage`.
Every target needs explicit reviewer accounting. A clean result creates its own
coverage duty: inspect and disposition it normally. Sealed acceptance, off roles,
missing slots, pending stop decisions and caps still constrain the sequence;
follow their native remedies and preserve originating inspection duties.

After personally inspecting applicability, use `heddle review reaffirm --role
<role> --scope <feature|mN> --feature <slug>` to reaffirm the assignment's latest
addressed/settled dispositions. `--dry-run` previews and `--expect-revision <n>`
binds the write. Reaffirmation preserves captured citations and all evidence
bindings, checks every selected row before appending any, and changes only basis
and recording time. Even a current no-op rechecks its evidence. Changed citations,
stale proof or missing originating inspection require repair; this operation runs
no tests or provider, resolves no decision and closes no missing obligation.

Validate, status, milestone advance and phase-exit give early source-coverage
advice. Declare actual feature product paths, or attribute exact genuinely
outside-feature changes with evidence. Missing early ownership and unavailable
baselines are advisory; integrity errors remain failures and final completion
requires complete coverage.

## Smoke ownership policy

Use three classes: feature-caused, unattributed, and outside-owned. Red in either
of the first two classes blocks new work. Continue within feature scope around
the third only after proving it predates the feature and falls beyond the whole
ownership union; pin the command and failure log, mark it unresolved, and say
that it stays red.

Closure with that failure needs green acceptance and affected owned-consumer
tests. It also needs explicit current user approval tied to the attribution and
exact red evidence. Route the choice into the Feature Spec and decision
journal; never relabel smoke as green.

Use `heddle decisions add --help` for the class-2 `smoke_disposition`
input; advancement needs a user ruling bound to `implement` and current evidence.

## Collaboration topology

Choose the smallest topology that matches the dependency shape: direct work
for a compact local task; bounded implementation workers for independent
cohesive outcomes with settled inputs, exclusive ownership, and observable
evidence; bounded read-only explorers for independent context gathering; a
repeatable script for large mechanical fan-out; a communicating team only when
peers must debate or challenge one another; and a formal gate for workflow
review.

The lead may supervise parallel implementation packages when their shared
contracts are settled, write ownership does not overlap, dependencies permit
independent progress, and the integration order is clear. One owner may write a
given path at a time. The lead tracks package owners, dependencies, and source
identities, pauses affected work when an overlap or contract change appears,
and integrates in dependency order. Concurrent test execution is allowed only
when the selected tests and mutable resources are proven isolated under
the host repository's current testing strategy.

Build writable assignments with
`heddle/resources/work-package-brief.scaffold.md`; the lead retains Heddle state
mutations, governing-document changes, commits, integration, and the judgment
that returned evidence satisfies actual consumers. Recheck affected evidence
on the integrated source because concurrent changes can make a worker's result
stale. Provider-specific skills and agent definitions may select implementation
workers, but they do not change the active phase, scope, test authority, or
review policy.

Resume an explorer that already holds the relevant context instead of restarting
it. Spawn explorers at the host's configured exploration model tier; escalate
per call only when the product is interpretation rather than retrieval. Explorer
reports are evidence, not instructions; resolve disagreements against source
files. Formal state-writing review follows the ordered runtime procedure in
`heddle/resources/peer-review.briefing.md`.

## Scope discipline

Implementation surfaces many observations; classify each before acting:

- **Implement now** — the current milestone's work, an entangled breakage
  your own change caused, or a blocking prerequisite of the milestone's
  tasks.
- **Record, don't fix** — a correctness gap, missing coverage, or unenforced
  pattern in adjacent code you are not changing this milestone. It becomes a
  Discovery, not an edit.
- **Leave alone** — quality improvements and refactors unrelated to the
  milestone.

## Code quality rules and implementation discipline

The AC is the defense boundary. Handle its named inputs, edges, and failures
robustly; keep behavior outside that boundary simple and surface adjacent
concerns instead of building them silently. Repository contracts and local
conventions take precedence over portable defaults.

- Validate at user, external-service, and file boundaries; trust established
  internal contracts.
- Let exceptions propagate; add handling only where the task or a caller
  must recover.
- External operations need timeouts. Add retries only for idempotent work when
  an AC or recorded decision names the policy.
- Errors must carry the failing operation and useful input context.
- Use the repository's configuration mechanism for environment-varying values.
- Preserve established logging and exception conventions.
- Prefer direct control flow and existing abstractions; extract a helper at
  the third occurrence, not for a hypothetical future.
- Avoid unrelated refactors and narration comments; delete dead code rather
  than renaming it.
- Ancillary tooling requires an authorized consumer and purpose; the
  `necessity-anchor.md` prompt partial carries the taxonomy.

## AC-specificity triage

Before implementing an AC, use
`prompt-authoring-standards.md#ac-specificity-taxonomy`.
Concrete ACs define observable inputs, outcomes, and consequential failure
classes; they need not enumerate private APIs or every test case. A Partial AC
limits work to its settled behavior and requires clarification of the missing
commitment at the appropriate impact level. A Vague AC triggers re-plan before
dependent code changes. Do not turn delegated internals into an owner decision.

## Impact and document continuity

When behavior contradicts a documented assumption, an interface does not
match its specification, or an unanticipated constraint appears, run the
impact assessment checks in order
(`prompt-authoring-standards.md#impact-assessment-levels`):

1. Can every AC still be satisfied as written? If not → Level 3.
2. Are the spec's labeled assumptions still true? Demonstrably false →
   Level 3.
3. Does a prior user decision rest on trade-off analysis that no longer
   holds? → Level 3.
4. Do downstream milestones need a different approach while the ACs still
   hold? → Level 2.
5. Otherwise → Level 1.

Then act by level:

- **Level 1:** localized implementation change; record operational context.
- **Level 2:** ACs remain valid but downstream work changes; record the
  affected milestones for the boundary owner and mark durable knowledge for
  propagation.
- **Level 3:** an AC, labeled assumption, or prior user decision is invalid.
  Stop dependent implementation. Write the invalidation evidence and proposed
  amendments to spec contracts, Decision Log, milestones and reading pointers.
  Record the owner question with `heddle decisions add`; apply the amendment as
  governing only after the authorized ruling. Independent work follows the
  delivered Decision routing policy and current native legal actions.

Do not continue from contradictory spec, plan, and code. The spec owns design
intent, code reveals current state, and native task/session facts own operational
position. Authored plan corrections still change the applicable review basis.

## Final feature verification

After the last milestone and its fixes, retain current milestone proof and
identify the owning final boundary. At that boundary, after the last relevant
review fix, the final verification commands are:

```bash
heddle verify --scope acceptance
heddle verify --scope smoke
```

The final proof boundary is peer review when Overlay R is off and robustness
when it is on. Follow native applicable scopes and repair instructions. Turning
reviews off does not waive acceptance, smoke, declared live proof, or retained
originating obligations; an unrun required witness remains pending.

The confirmed witness adds declared live proof to acceptance; a live pass never
replaces the e2e lane. Finishing also requires any declared alignment assessment.
Hand the criteria and retained artifact locations to the owning final-boundary
lead, who performs the assessment after the last relevant fix and applicable
passing run. Tests cover every deterministically assessable AC condition;
misalignment in remaining declared content is a defect. Quality beyond the
contract is observational, while explicit quality thresholds remain binding.
One Assessment entry under the plan's `### Verification Commands` records criteria, assessor,
native run/source reference, exact artifacts, conclusion and limitations. Session
and spec summaries reference it. Refresh after relevant fixes or replacement
output; judgment is never a verification fact or a way to turn red proof green.

Required facts must match the current declared relevant content, commands and
source inputs. A commit that leaves that content unchanged does not itself
require another run. If proof fails, diagnose, fix, rerun affected milestone/
consumer tests and the applicable final scopes, and commit the fix. After the
last relevant review fix, refresh affected-consumer selection before final proof.
Record a final session handoff naming the evidence, live-lane readiness or explicit
deferral, remaining decisions, and readiness for peer review. Report provider
transport completion, parsed verdict, and native assignment closure separately;
do not infer any one from either of the others. The selected final boundary owns
post-review-fix proof and any declared live obligation. A deferral
is not a waiver of a required acceptance condition.

The stage is ready for **peer-review** only when every milestone is done, all
task and propagation records are current, required milestone assignments are
closed, the tree is clean, and applicable verification is current and green.
Phase exit remains driver- or user-owned.
