# Agent-Driven Development: Complete Workflow Reference

Heddle records workflow authority, evidence and the next legal action. The user
and implementing agent supply product and engineering judgment. This reference
explains the lifecycle; it is not another state machine or review schedule.

## How to Use This Document

Enter a session with `heddle orient`, follow its ordered `next_actions`, and run
`heddle kickoff` only when routed. The delivered stage briefing owns detailed
work instructions. Native operations own state transitions, policy, commands,
decisions, tasks and sessions; never hand-edit those facts.

Use [document structure](document-structure.md) for the two-document boundary,
[brainstorming guidelines](brainstorming-guidelines.md) for research and scope,
[engineering principles](engineering-principles.md) for preferences, and the
[testing strategy](testing-strategy.md) for proof selection and execution
authority. Load the sources needed for the current decision. Missing access is
unavailable evidence, not evidence of absence.

Test execution follows the [testing strategy](testing-strategy.md). Use focused
affected selections and direct consumers. Broad suites and both local
full-workflow e2e and live tests need explicit execution authority for that scope;
phase exits, review, pre-merge and populated commands do not independently grant
it. Existing applicable grants persist. Keep required unrun proof pending and
report optional unrequested regression separately. Inspect nested launches, and
do not substitute a focused pass for a required native verification scope.

## Core Principles

- **Fix the demonstrated cause.** Distinguish model instructions, missing
  context, tool delivery and runtime defects. Behavior-changing instruction
  edits follow the [prompt-refinement protocol](prompt-refinement.md).
  Unexpected Heddle behavior follows the [Tooling Discovery Protocol](#tooling-discovery-protocol).
- **Make governing knowledge discoverable.** Keep each contract in one home
  with source-backed reading pointers. Self-containment means sources are
  accessible, not that the plan copies the spec or chat becomes authority.
- **State outcomes; delegate reversible internals.** ACs own observable
  promises. The spec resolves consequential commitments; the plan explains
  approach and architecture. Scaffolding supplies discriminating test cases.
- **Deliver useful, verifiable increments.** Milestones earn their boundaries
  through capability, dependencies and proof, not hours, line counts or a
  fixed number of tasks. Record task progress through native operations.
- **Enforce at the defining owner.** Prefer an existing type, linter or
  structural check for a real invariant or recurring failure. Delete redundant
  guards when the structural cause disappears; do not add checks merely
  because wording can be matched.
- **Keep decisions and evidence honest.** Reconcile substantive discoveries,
  preserve failed and withheld evidence, and distinguish source freshness from
  assertion quality or execution authority. A clean review is not a test run.
- **Propagate durable knowledge.** Move lasting lessons into their permanent
  document home; keep progress and execution detail in retained native history.

Measure gate effectiveness at every feature close; improvement proposals remain
owner-decided under [Gate Quality Monitoring](#gate-quality-monitoring).

## Workflow Selection

Research precedes formal admission. Assess **scope** (Small/Medium/Large),
**complexity** (Low/High), and **testability** (Full/Partial/None) independently.
Explain what is known, consequential uncertainty and proof limitations. These
axes inform a recommendation; none grants permission or implies another axis.

Use `heddle feature prepare` with its declared research-input contract. Present
the recommendation and confirm the user's **Direct** or **Heddle** choice.
Direct work creates no formal workspace, spec, plan or review schedule. For
Heddle, present the complete resolved per-role matrix and record the user's
actual confirmation through `heddle feature policy`; then use `heddle feature
start` and follow its emitted actions. Host exclusions remain constraints, not
silent substitutions. Native help owns argument and payload spellings.

The confirmed policy records `off`, `upper-limit` or `convergence` per role,
reviewer tuples, required contexts and any maximum. Change it only through its
explicit amendment operation. Runtime-owned closure accounts for required passes,
current evidence, dispositions and decisions. No document introduces a hidden
round cap, waives a selected role or schedules a retired synthesis gate.

The former tier presets and automatic tier overrides are retired. Historical
policies remain interpretable under their recorded version, not templates for
new admission. Every formal feature retains mandatory final proof even when
all model-review roles are Off.

## Architecture & Orchestration

The implementing agent coordinates work through native actions. Formal reviewers
are read-only judgment producers; the runtime records their canonical results.
The lead dispositions original findings and records owner decisions without
rewriting a reviewer result into a new combined authority.

Use [exploration patterns](subagent-exploration-patterns.md) only when independent
read-only work is available, authorized and useful beside local work. Explorer
reports are evidence, not formal reviews, decisions or state-writing authority.
Heddle exposes concurrent formal review only through a declared typed group.
At the initial Full peer-review boundary, readiness replaces the independent
`behavior-review` and `complexity-review` actions with one `heddle run-gates`
action. The command accepts only `--feature` and runs at most those two primary
assignments. It acquires their gate-type locks in declared order, prepares both
from one state snapshot, admits both calls before launch, and runs isolated
workers. Workers write gate artifacts; as each next declared-order result becomes
available, the parent alone records it while any unfinished sibling continues.
Publication remains behavior-then-complexity and one state write at a time.

If either member is disabled, already completed outside the batch, awaiting a
decision or disposition, using a later round or slot, or otherwise dependent,
readiness keeps the current `heddle run-gate` action. Do not construct a role
list or worker count: no such public inputs exist. On a provider failure,
interruption, or CAS conflict, inspect every returned member summary. Completed
artifacts remain available for restart, and `heddle run-gates` resolves exact or
recoverable output before admitting another provider call. Commands carrying
`--expect-revision` remain sequential and must reread the revision before each
write. This reference grants neither delegation nor external execution.

### Decision Classification Protocol

Apply the ratified engineering principles before escalating a defensible
engineering choice. Record the decisive principle and evidence at the decision's
owner. Genuine product choices, irreversible or precedent-setting changes,
ratified-contract amendments and unresolved principle conflicts remain owner
decisions. Do not infer approval from silence or a recommendation.

Review findings preserve their native IMPLEMENT, REPORT and IGNORE meanings.
Act on in-scope IMPLEMENT work, route REPORT to its owner, and retain IGNORE as
awareness. Account for exact original run/finding identities, contradictions
and coverage through native disposition operations. An aggregate summary or
reviewer's no-issue statement cannot settle missing coverage or a pending choice.

When a review stops for no progress, no decrease or its round cap, the native
round-open operation records an owner question. An owner can authorize one next
round with `heddle decisions resolve <id> --kind continue-review --resolution
<rationale> --routes-to <record>`. The resolution binds the exact assignment and
stopped round; advancing the round consumes it, and replay grants no extra pass.
A reached cap still requires an explicit feature-policy amendment. A generic
`disposition` ruling does not authorize another stagnant round. Later stops get
new questions, while all previous rounds, decisions and obligations remain.
Use `heddle review allowance` to preview or publish an owner-approved absolute
quality round ceiling for an existing upper-limit role. The command changes no
provider retry budget, invokes no provider, and leaves the stop in force; after
the increase, resolve that exact stop with `continue-review`, then use
`heddle review round-open` through ordinary native routing.

Disposition freshness follows the assignment's review subject. Spec review
tracks the spec; plan review tracks the plan and governing spec; implementation
reviews track feature inputs and the assigned milestone's owned paths (the union
of owned paths for feature-wide assignments). Each disposition also tracks its
explicitly cited evidence. A relevant edit invalidates that disposition; an
uncited plan edit does not reopen spec review, and an unrelated milestone edit
does not reopen another milestone's review. Reviewer inspection and verification
evidence retain their own provenance checks. Older dispositions whose broad
source basis differs need a new supported disposition; historical evidence is
never silently stamped fresh or converted into a mandatory provider rerun.

### Batching Protocol

Batch independent questions with their context, evidence, recommendation and
trade-off. Keep the actual owner's answer in native decisions and the decision
journal. The spec's Decision Log holds lasting alternatives-and-rationale, not
checkpoint transcripts or a second operational ledger. Existing authorization
continues within its scope; ask again only for a genuinely outstanding choice.

## Workflow Overview

Research and route confirmation precede formal specification. The native stages
then cover specify, spec review, plan review, scaffold, implementation, peer
review, robustness and completion. A stage may have no selected model gate;
its required work and proof still apply. The runtime derives order and legal
advancement from the recorded policy and current evidence.

| Review role | Responsibility |
| --- | --- |
| `spec-review` | Product contract, scope, concepts and consequential commitments |
| `plan-review` | Approach, methodology, technical architecture and delivery proof |
| `review-test-scaffolding` | Executable test design, AC linkage and proof adequacy |
| `milestone-review` | Milestone implementation correctness and code quality |
| `peer-review-sequential` | Integrated implementation review under its named dimensions |
| `complexity-review` | Necessity, ownership, dependencies and simplification |
| `behavior-review` | Observable behavioral consistency and consumer contracts |
| `robustness-analysis` | Conceptual boundary risks, failure classes and integration scenarios |

Use the selected role's delivered input boundary. Do not substitute a retired
gate, treat one role as another, or infer a provider tuple from an old example.

## Phase 1: Research & Problem Understanding

Clarify the user outcome, smallest useful slice and negative scope. Inspect
actual callers, integrations and repository precedent. Begin related-spec
discovery with `heddle search "<intent>" --titles-only`; a successful zero-hit
result stands, while command failure permits the documented knowledge-index
fallback. Distinguish observed behavior, inference and unknowns.

Resolve consequential commitments and label assumptions. Delegate reversible
internals rather than inventorying every future file or test. Follow the
brainstorming scope-authorization contract and native intake described above.
The research checkpoint is not the later post-spec-review scope checkpoint.

## Phase 2: Specification (Feature Spec + Implementation Plan)

The spec owns the product goal, approved scope, concepts, the canonical AC list
and Design Commitments. The plan owns approach, technical architecture,
methodology, sequencing and proof strategy. Use their canonical scaffolds and
[document contracts](document-structure.md); do not create another template here.

Keep machine-read AC/milestone identifiers and reader anchors stable. Preserve
Priority and Verified-by linkage without turning each AC into an exhaustive
test inventory. Signatures are spec material when they promise behavior to a
consumer; chosen implementation interfaces otherwise belong in the plan and
actual source. Standing enforcement and native commands are referenced, not
copied. Record delegated internals explicitly where that affects review.

## Phase 3: Automated Spec Review & Refinement (CLI Gate)

Follow the selected native spec-review assignment. It evaluates the observable
contract, scope and commitments; omission of reversible internals is not itself
a defect. Required scope assessment and every required dimension remain covered.

Remediate findings within their authority, preserve original results and record
explicit dispositions. Reruns follow the native required-pass/stop policy and
prior-result boundary. No EARS mirror, copied-contract sweep, code-layout quota
or separate synthesis is required for new documents.

## Checkpoint 1: User Decision Batch

Present unresolved owner questions and the scope proposal with approved core,
proposed additions and named deferred parts. **Scope confirmation is explicit
even when the review finds scope adequate.** Do not silently authorize a part
or replace the original pending question on a rerun. Record the actual ruling
and reconcile affected spec/plan content and review evidence.

## Phase 4: Plan Review (CLI Gate)

The plan explains why its approach meets the spec, what owns each change and
how dependencies and interfaces fit actual consumers. Review commitments made
and internals delegated; challenge unnecessary abstraction, duplicated authority
and outward dependencies using the named Architecture principles.

Verification and milestone scope must cover the canonical ACs and declared
failure classes. Unknowns may justify a prototype with explicit promotion or
discard evidence. Lines, hours, task counts and a copied Design Context do not
establish plan quality. Run the selected assignment and record closure natively
before scaffolding when the runtime requires it.

## Phase 5: Test Scaffolding

Translate ACs into executable, non-tautological assertions at the actual
production boundary, using the reviewed plan's architecture and real interfaces.
Inspect existing tests, fixtures and infrastructure before choosing commands.
Classify effects and execution authority separately from purpose and cost under
the testing strategy. Scaffolding can safely collect ungranted e2e/live witnesses;
collection is not execution proof.

Record exact commands natively, prerequisites and AC coverage in their plan
reader sections, and actual test links under Verified-by. Follow the selected
scaffolding review assignment. Missing proof remains pending; do not weaken a
contract or launch a nested broad workflow to make scaffold look green.

### Smoke Failure Ownership

Feature-caused or unattributed red blocks the applicable boundary. An exception
requires proven pre-existing, outside-owned failure, retained exact evidence and
the explicit current owner decision supported by native degraded-smoke policy.
A close-stage acceptance does not silently authorize an earlier phase exit.
Preserve the failure as red, with its disposition separate from the result.

## Phase 6: Milestoned Implementation with Continuous Verification

Read the current milestone, governing ACs/Design Commitments, plan architecture
and actual interfaces. Expand tasks at the current frontier through native
operations. Implement the owned slice, run affected authorized checks, repair
change-caused failures, and record evidence and task completion truthfully.

Use the milestone's selected review assignment and original-finding disposition
flow. Final review or all-Off policy does not replace milestone proof. Advance
only when the native boundary is satisfied. Commit scoped behavior changes
separately from mechanical cleanup using the repository's commit procedure.

Milestone ownership may name another feature's Heddle records when a migration
explicitly changes or deletes those exact paths. It may not name the active
feature's workspace, close records, or the shared gate-effectiveness log, and a
broad `plans` declaration is invalid because it overlaps those controls. After a
milestone is done, ownership can only expand. Replacing its nonblank verification
command additionally requires that strict expansion and must preserve the
recorded expected result; filling a previously blank command remains the narrow
one-way repair.

### Document Reconciliation

The spec defines intended behavior, code shows current behavior, the plan
explains approach, and state records progress and authority. Resolve a real
contradiction at its defining owner. Do not rewrite a consumer promise to match
a bug or update the reviewed plan merely because the working frontier moved.

### Impact Assessment During Implementation

Assess a discovery before proceeding:

- **Level 1:** local implementation changes, with ACs and commitments intact;
  record the approach and evidence in milestone notes.
- **Level 2:** downstream approach or sequencing changes with the governing
  contract intact; update affected native milestones and plan judgment, propagate
  durable lessons at the boundary, and refresh invalidated proof/review.
- **Level 3:** an AC cannot hold, a consequential assumption is false, or an
  owner decision's trade-off no longer holds; stop work that relies on it,
  record the invalidation and resolve the governing choice before continuing.

### Re-Plan Protocol (Level 3)

Record what changed, its evidence, affected ACs, assumptions and decisions, and
the available alternatives. Consult engineering principles; escalate choices
that still belong to the owner. Amend the governing spec only under that
authority, align plan architecture and remaining native milestones, then
refresh affected qualification. No copied Design Context needs synchronization.
Independent work may continue only where the unresolved contract cannot affect it.

### Tooling Discovery Protocol

Heddle is used to build Heddle, so every feature session is also a live test of the runtime. When a `heddle` command, gate, briefing, or projection behaves against its documented contract — or against what you expected — treat it as a tooling discovery with a fixed response. The response is deliberately short so that it costs less than the workaround it replaces, and it runs in the moment, not from memory at feature close.

1. **Stop.** Do not route around the behavior silently and do not patch Heddle inside the feature (Stay inside owned scope; Fail loud). A workaround adopted without a record is the silent adaptation the Inviolables forbid.
2. **Flag.** Append one row to `plans/friction-log.md` now, while the evidence is on screen: date, feature, observed versus expected behavior with the command or artifact as evidence, and the proposed resolution from step 4. If the behavior blocks the current step, also record it with `heddle decisions add` so it reaches the owner at the next checkpoint instead of at feature close.
3. **Trace.** Classify the cause per Core Principle 1 — missing context, conflicting instruction, model selection, tool delivery, or runtime defect — and name the governing record that defines the expected behavior: `docs/design/workflow-model.md`, `docs/design/architecture.md`, the owning Feature Spec, or the command's `--help` contract. The docs lead: when the record says Heddle is right, the discovery is an expectation or documentation gap, and the row says so. Use the `root-cause-analysis` skill read-only when the symptom may mislead; a diagnosis does not authorize a fix.
4. **Propose.** Record the smallest change that removes the mechanism, the decisive engineering principle it satisfies, and where it lands (module, contract, or document). Prefer a resolution that leaves Heddle simpler than it was found — a deleted guard, a unified owner, a corrected contract — over a new layer. Size it: a trivial correction with clear correct behavior becomes a maintenance edit in its own commit under owner approval; anything larger becomes a named follow-up feature (`heddle feature start`) or, when it reshapes a ratified contract, a proposal under `docs/proposals/`. A workaround inside the feature's own code is never the resolution.
5. **Resume.** Continue the feature. The row's `Retired by` names the feature, decision, or commit that retires the friction, or `candidate` while nothing does; a `candidate` that recurs is checkpoint material.

At Phase 9 the feature's rows feed the friction retrospective's Tooling fights category, and each row still marked `candidate` receives a disposition at the final checkpoint. Appending rows by hand is the intended shape until recurrence proves a `heddle` command worth building.

### Continuous Enforcement Extraction

Consider enforcement for demonstrated recurring failures or a stable contract
that belongs at an existing structural boundary. Record the invariant, owner,
evidence and enforce-now/later disposition. Do not invent a check to satisfy a
quota. An executable change carries its own affected tests and review refresh.

### Prototyping Milestones

Isolate the uncertain mechanism, evaluate the declared promotion/discard
criteria, and retain both successes and failures. Update the chosen approach
and remaining milestones from that evidence. A discarded prototype does not
prove the alternative implementation.

### Handling Multi-Session Implementation

Native task/milestone facts identify the resume point. Use `heddle session log`
for concerns, evidence references and next-frontier reading pointers. Resume
through orient and the emitted actions. The managed plan-status region is a
projection; it does not create another progress owner.

## Phase 7: Automated Peer Review (CLI Gate)

Execute the projected `heddle run-gates` action when the declared initial pair
is eligible; otherwise execute the projected single-gate action. Then follow
native routing for dispositions and any verification round.
Judge implementation against the spec's commitments, plan architecture and
actual consumer behavior within each role's allowed inputs. Preserve original
findings from every required context; one passing reviewer does not erase
another's findings or unavailable coverage.

Apply fixes, record exact dispositions and decisions, and refresh affected
checks and review evidence. Peer review owns final proof when Overlay R is Off;
when R is selected, robustness owns final post-hardening proof. Follow native
applicability and do not infer an extra broad test grant from this stage.

## Checkpoint 2: User Decision Batch

Resolve outstanding owned review questions with their original identity,
evidence and alternatives. Existing settled decisions stand unless material new
evidence justifies reopening them through the proper owner. Native closure,
not a manually merged review or prose recommendation, decides readiness.

## Phase 8: Robustness & Conceptual Validation

When Overlay R is selected, use its robustness assignment and delivered
conceptual validation work. When it is Off, follow the native route without a
substitute inline review obligation; final proof remains at peer review.
Probe contract-relevant failure classes, integration boundaries and
recoverability; do not add speculative defenses beyond the contract by default.
Retain limitations and route out-of-scope discoveries rather than hiding them.

After the last relevant fix, complete the required current final scopes,
including applicable separately authorized installed/e2e/live witnesses. Every
formal feature owes final proof even when all review roles are Off. A required
unrun witness remains pending and blocks the claim it proves; optional ungranted
broad regression is a reported limitation, not an invented completion gate.

## Phase 9: Completion & Knowledge Capture

Entering `complete` accepts nothing. This is a human-owned close stage; the
native complete briefing owns its detailed checkpoint, acceptance and recovery
protocol. Work within the existing grant and stop for outstanding owner choices.

Sweep propagation tags, mine durable lessons, disposition deferred enforcement,
classify gate effectiveness and write concise spec outcomes. Prepare the native
decision journal and friction retrospective. Audit Verified-by routes against
the final tests and required current evidence. Keep detailed logs and qualification
records at their retained source rather than dumping them into the spec.

Present memory and pattern candidates with the close package. Proposed durable
memory/pattern writes require their stated approval and the host's own rules;
they are not implied by ordinary documentation work. The complete briefing
preserves the two-pass checkpoint. Drop unconfirmed terminal candidates visibly.

Preview `heddle feature complete --feature <slug> --dry-run`. Actual acceptance
requires the applicable explicit grant, current required evidence, audited close
artifacts and every configured close suite within its execution authority.
The runtime records one typed acceptance before stamp/archive/cleanup effects.

Write the feature's `GE-<date>-<slug>` entry using the measurement shape below.
When instruction text was added to the packaged corpus, assess it against the
measured-gap rule in `heddle/resources/prompt-authoring-standards.md`; any proposed
prompt distillation follows the refinement protocol before live bytes change.

Feature admission stores the source baseline from the initial source commit.
A record commit for workflow files is optional. At close, `completion.tar.gz`
provides local retention for the accepted ledger, authored records and indexed
artifacts; verified readback, rather than Git HEAD, qualifies bounded cleanup.
Preserve `plan.md`, `brief.md`, canonical reviews at their original paths and
referenced verification/close logs; unknown or changed files remain reported.
Never remove the whole workspace. Accepted pending effects can return exit 4;
use the exact recovery action and retry without re-running acceptance.
[Document structure §6](document-structure.md#6-completion-and-handoff) and the
complete briefing retain the full handoff boundary.

## Deterministic Enforcement

The [enforcement baseline](enforcement-baseline.md) names active checks and their
owners. Use it by reference. Prefer existing language/tool support; additional
guards need an actual invariant or recurring failure and maintenance value.
Tests should prove observable behavior or a structural boundary, not merely
assert new wording. The testing strategy governs execution and evidence claims.

## Gate Quality Monitoring

Automated gates are only valuable if they're catching real issues and not producing excessive noise. Without monitoring, gate quality degrades silently. Monitoring has two instruments that meet at Phase 9: the runtime's trajectory export (numbers, automatic) and the gate-effectiveness log (judgment, written by the closing session).

### Instruments

- **Trajectory export.** `heddle feature complete` exports `docs/gate-trajectories/<slug>.json` and regenerates `_aggregate.md`. Both are local records for the host repository; regression tests create their own isolated records. Current v2 exports include confirmed policy history, assignments, rounds, calls and original dispositions, including zero-run Off policies. Historical iteration/cap and synthesis metrics remain separate; they are not current closure authority. CLI/model/effort, prompt identity and finding classifications stay attributable to their original runs. `scripts/gate-trajectory-report.py --since <date>` renders the same aggregate for a window.
- **Gate-effectiveness log.** `plans/gate-effectiveness.md` persists across features and holds one entry per feature, written at Phase 9 in the shape below.

The export runs at acceptance, after the log entry is written, so the entry cites the feature's gate summaries under `plans/<slug>/reviews/` and `heddle status --json`, and its trend compares against the aggregate as it stood before this feature; the aggregate catches up at acceptance. Gathering the numbers by hand is the intended shape until recurrence proves a pre-acceptance summary command worth building.

### Per-Feature Measurement (Phase 9)

Every feature close writes one entry with the stable ID `GE-<YYYY-MM-DD>-<slug>`; findings inside it are cited as `GE-<date>-<slug>/<finding-id>`. The ID is what a prompt-refinement record or a decision cites as its observed failure, so an entry that cannot be cited is incomplete.

1. **Runs.** Per assignment: rounds, completed calls, selected reviewers, closure/disposition outcomes, CLI/model/effort and findings by severity (critical / important / minor) and classification (implement / report / ignore). Compare only compatible policy/schema cohorts; label legacy iteration means as historical and Off assignments as intentionally not run.
2. **Classification.** True positives, false positives, and false negatives with counts and finding IDs. A false negative is an issue found later — by a later gate, by implementation, by human review, or post-release — that a gate's declared job included; do not assign a miss to a gate whose charter did not cover the check.
3. **Finding quality.** Assess on fixed axes so entries compare across features: precision (share of findings that were real), actionability (could the finding be applied without re-investigation), severity calibration (inflated or deflated severities), duplication across reviewers or reruns, and evidence quality (the finding cites a trace, not a speculation). Name the finding IDs behind each judgment.
4. **Trend.** Compare the same measures with the previous three to five compatible features from the aggregate or the `--since` report. Identify changes in rounds/calls, findings, limits and prompt versions; preserve legacy contradiction/convergence trends separately. If there is no comparable cohort, say so.
5. **Improvement proposals.** Each proposal names its target class — flow (confirmed policy matrix, assignment topology, budgets), prompt (a gate template or partial), workflow (a `docs/workflow/` document), or enforcement (linter, structural test, CI) — the measured gap behind it cited by entry ID, its route below, and the smallest change that closes the gap. A proposal without a cited gap is dropped, not deferred.

### Improvement Proposals Are Owner-Decided

Nothing in Phase 9 changes a gate, prompt, flow, or workflow document. Each proposal is recorded with `heddle decisions add` (Category B, with a Routes-to annotation) and presented as a pass-2 item at the final checkpoint, individually explicit like a pattern candidate. The outcome — accepted, declined, deferred — is written back into the entry's proposals table so the next feature's trend can see it.

Accepted proposals route by target class:

- **Prompt** → a [prompt-refinement](prompt-refinement.md) record whose `observed_failure_ids` cites the entry; the protocol's comparison and promotion bar applies unchanged.
- **Flow** → a proposal under `docs/proposals/` or a D-numbered decision, since policy matrices and assignment topology are ratified contracts.
- **Workflow** → an owner-approved edit to the named document.
- **Enforcement** → the [enforcement extraction](#continuous-enforcement-extraction) disposition path.

### Entry Shape

```markdown
### GE-YYYY-MM-DD-[slug] — [Feature Name]

| Assignment | Calls | Rounds (compatible mean) | Critical / Important / Minor | TP | FP | FN |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| milestone-review | 3 | 3 (2.5) | 0 / 2 / 3 | 5 | 0 | 1 |

- Classification: [finding IDs per TP / FP / FN, with the gate that missed each FN and why]
- Quality: [precision, actionability, severity calibration, duplication, evidence — with finding IDs]
- Trend: [against the previous 3–5 compatible features: rounds/calls, finding mix, limits, prompt-version boundaries; historical metrics separately]

| Proposal | Target | Evidence | Route | Decision |
| --- | --- | --- | --- | --- |
| [one line] | prompt / flow / workflow / enforcement | GE-…/[id] | refinement record / proposal / doc edit / enforcement | accepted / declined / deferred |
```

### Post-Release Findings

If a bug is discovered after a feature ships, append a `Post-release` line to that feature's entry: the issue, which gate should have caught it, why it was missed, and the proposal it produces, routed as above.

### Cadence

The trend read at every Phase 9 replaces the unowned periodic review. A deeper cross-feature review — gates with sustained high false-positive rates, systematic blind spots, prompt drift — is optional and, when run, is recorded as its own `GE-<date>-review` entry with proposals routed the same way.

Retain true/false-positive dispositions and record demonstrated misses in
`plans/gate-effectiveness.md`. Gate quality changes follow the refinement
protocol with attributable inputs, cases and promotion decisions. A newer model
is an audit trigger, not authority to retune prompts or change execution policy.
Post-release measurement can inform future work without silently becoming a
current release dependency.

## CLI Orchestration Quick Reference

`heddle help --json` owns current command/payload contracts. Orient owns routing;
kickoff supplies the stage briefing; status shows recorded facts and blockers.
Use native policy, milestone, task, command, decision, review, verification and
completion operations for their owned facts. A refusal's exact diagnostic and
legal remedy takes precedence over remembered legacy command examples.
