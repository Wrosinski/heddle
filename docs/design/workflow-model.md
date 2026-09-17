# Heddle workflow model

This document describes current v10 features: the facts Heddle records, the
requirements it derives, and the boundaries it checks. The
[workflow guide](../workflow/workflow.md) explains the development process;
[architecture](architecture.md) names the implementation owners. Use
`heddle help --json` for current command arguments and payload schemas.

## Facts and derived state

Native `state.yaml` records enacted facts: the confirmed feature policy, stage,
authorizations, milestones, tasks, commands, review assignments and results,
dispositions, verification attempts, decisions, sessions, and completion.
Agents author the spec and plan; runtime commands own operational state.

The kernel derives readiness, unmet obligations, evidence applicability and
legal next actions from those facts and current observations. A prose summary,
rendered plan-status block or passing reviewer statement is not a substitute
for the underlying recorded evidence.

The [document structure](../workflow/document-structure.md) separates the spec's
product contract and Design Commitments from the plan's approach and Technical
Architecture. Native identifiers join milestones to acceptance criteria and
prose; they do not make the plan a second progress ledger.

## Admission and review policy

Research precedes the user's Direct/Heddle choice. Direct work creates no formal
feature workspace. For Heddle work:

1. `heddle feature prepare` records researched intake and recommends a policy.
2. The owner confirms the complete policy through `heddle feature policy`.
3. `heddle feature start` admits the feature and creates its working records.

Preparation is not approval. Intake assesses scope (small/medium/large),
complexity (low/high), and testability (full/partial/none) independently, with
reasons. These axes inform the recommendation; the confirmed policy governs
execution. Host exclusions constrain the selection without silently replacing
reviewers.

Each review role has a feature or milestone scope, a primary reviewer and an
optional independent secondary reviewer, plus one of these modes:

| Mode | Meaning |
| --- | --- |
| `off` | Intentionally not run; no invocation is authorized by that assignment. |
| `upper-limit` | Required minimum rounds and an explicit maximum. |
| `convergence` | Required minimum rounds without a numerical quality-round ceiling; closure and stop rules still apply. |

Reviewer selections include CLI, model and reasoning effort. The policy exposes
minimum and maximum call budgets; an unbounded assignment is reported as such.
Amendments retain policy history and spent work. All-Off review policy does not
waive final verification. Robustness review requires an explicit integration gap
and supporting references; it is not inferred from size alone.

The [policy resolver](../../heddle/kernel/feature_policy.py) and
[role catalog](../../heddle/contracts/gates.py) own these rules.

## Stages and authorization

The native stage order is forward-only. Research and route selection happen
before admission; the workflow guide's phase numbers are explanatory labels.

| Stage | Work and boundary |
| --- | --- |
| `specify` | Author the spec, plan, integrated witness proposal and milestone skeleton; draft checkpoint questions without recording future blockers. |
| `spec-review` | Review the product contract; the lead confirms scope, witness shape, prerequisites and explicit lane grants at Checkpoint 1, even with the reviewer Off. |
| `plan-review` | Review approach, architecture and delivery strategy. |
| `scaffold` | Realize the confirmed witness, bind acceptance criteria and exact commands within the recorded grants; route material changes to the original owner. |
| `implement` | Complete tasks, verify and review each milestone, then advance it. |
| `peer-review` | Review the integrated change; perform final proof when robustness is Off. |
| `robustness` | Perform selected robustness work and final post-hardening proof; Off adds no substitute inline review. |
| `complete` | Prepare the human handoff and explicitly accept completion. |

`heddle phase-exit` advances a stage only when its applicable boundary qualifies.
`heddle milestone advance` owns milestone advancement. A disabled review role
is not a passing review and does not remove other applicable obligations.

`stage` records progress; `authorized_through` records how far work is authorized.
The `hitl` flow uses recorded user grants at stage boundaries. The `auto` flow
allows policy-sourced advancement when the boundary is satisfied, under ratified
engineering principles. Neither flow turns configured commands or credentials
into permission for broad tests, providers, publication or other external effects.
The host's execution rules and existing grants continue to apply.

The driver executes typed actions from the same readiness machinery as the CLI.
It pauses for owner decisions and blockers, and bounds repeated failures or
non-progress. It does not run a headless completion session. Entering `complete`
is not acceptance: `awaiting-human-completion` identifies the human handoff
until `heddle feature complete` accepts the feature.

## Session continuity

Run `heddle orient` at session entry and follow its ordered `next_actions`.
Fresh-stage routing can request `heddle kickoff`, which renders the applicable
briefing and current authoring guidance without recording progress. Mid-stage
routing uses recorded tasks, decisions and session context.

Feature selection is explicit `--feature`, then a worktree-local active pointer,
then a unique active workspace. Ambiguity names candidates; no active feature
routes to intake. Accepted workspaces are excluded from active discovery but
remain available through explicit historical reads and `status --all`.

`heddle session log` records handoffs. `heddle search` discovers related specs
and patterns without requiring an active feature or persistent search index.

## Milestones and verification

Milestones record dependency order, owned paths, linked acceptance criteria,
Low/High complexity, tasks and a verification command with its expected result.
Ownership describes the source to qualify; prospective paths can be declared
before creation, but must be reconciled before the applicable boundary.

Use `heddle verify --scope <scope>` to execute and record native proof:

| Scope | Command and source |
| --- | --- |
| A milestone ID, such as `m1` | Its verification command and declared owned paths. |
| `acceptance` | The configured acceptance command and feature ownership union. |
| `smoke` | The configured system-health command and feature ownership union. |
| `live` | The configured live command and feature ownership union, when required and separately authorized. |

The progressive `test_command` is feedback, not a native `feature` verification
scope. Required final proof remains applicable even when no model review runs.
The [testing strategy](../workflow/testing-strategy.md) governs exact selection,
cost and execution authority; an inherited command is not an execution grant.

The integrated witness design and any declared alignment assessment are authored
plan judgments, not new state fields or verification scopes. Current milestone,
acceptance and smoke evidence remain required under existing disposition rules;
declared live proof is additional. The owning final-boundary lead assesses final
retained output after the last relevant fix and applicable passing run, with
native run/source and artifact references. Misalignment blocks the finishing
claim; quality beyond the contract is observational. Session/spec summaries
reference the plan's assessment instead of creating another evidence owner.
Assessment edits are authored plan changes, so source-bound review evidence can
become stale; existing qualification and originating-inspection duties remain.

These documentary duties do not change native enforcement: declared live is
required at robustness and complete; robustness is traversed even with R Off.
The peer-review lead then owes live before handoff even though the peer boundary
does not enforce it.
Checkpoint witness questions use existing decisions, and unresolved questions
block progression. Recorded grants remain separate from design and prerequisites.

Verification records the exact command, exit code, log, declared source set and
before/after source-evidence manifests. Freshness compares the current command,
source definition and relevant content with the stable recorded run. It
separately reports missing, failed, unstable, command-stale, source-set-stale and
content-stale evidence. Git HEAD is diagnostic, not freshness authority.
Admission captures a source baseline from the initial source commit so changed
source can be reconciled with declared ownership.

Observations include applicable file bytes, kinds, executable modes, symlinks
and missing paths. They are point samples, not an atomic filesystem snapshot or
protection against changes restored between observations.

Keep five evidence questions separate:

- **Content identity:** do the declared inputs still match the stable run?
- **Command adequacy:** was the right verification command selected?
- **Semantic quality:** do its assertions prove the intended behavior?
- **Operational reliability:** is the check repeatable under its dependencies?
- **Tool behavior:** did execution, capture and reporting work correctly?

Freshness proves content identity and the recorded process result, not all five
questions. A proven pre-existing, outside-owned smoke failure requires the
explicit native degraded-smoke decision and supporting green proof. Its status
remains failed, with acceptance recorded separately; the exception is not a pass.

## Review results and closure

The runtime binds each canonical review result to its assignment, round,
reviewer slot, inputs and execution identity. Original findings remain intact.
The lead records dispositions with exact source references and supporting
inspection, contract, verification, review or decision evidence. A clean later
report alone does not settle an earlier finding or coverage obligation.

Readiness checks execution, disposition and applicability separately. Relevant
changes to an assignment's subject or cited evidence can invalidate its current
closure. Repairing evidence does not automatically require another provider call;
follow the returned action. Completed imperfect responses retain their original
captures, and a capture-bound lead interpretation can preserve independent-slot
credit without a formatting-only retry.

Round opening enforces the confirmed allowance and stop decisions. Continuing a
stopped review requires its explicit decision; a reached upper limit additionally
requires an approved policy amendment. `heddle review allowance` raises a role's
absolute quality-round limit without calling a provider or resolving its stop.
No amendment resets spent calls or erases earlier obligations.

Exact replay is distinct from closure. The reuse owner validates original
identity and artifact integrity before returning retained output without a new
provider call or event. Reusing a result does not make unresolved findings pass.

The one declared concurrent group is eligible initial `behavior-review` and
`complexity-review`. `heddle run-gates` admits the pair from one snapshot,
runs isolated workers and serializes result publication. Other states follow
single-role actions; callers cannot supply arbitrary role groups or worker counts.

Successful duty-bearing spec, plan, scaffold and milestone transitions seal their
accepted assignments in append-only boundary receipts. Those receipts preserve
accepted predecessor work at its recorded identity when later work changes shared
paths. Current milestone proof, final review, acceptance, smoke and applicable
live proof still require current evidence.

## Decisions

Native decisions retain the question, owner, alternatives, resolution and routes
to governing documents. IMPLEMENT findings describe actionable in-scope work;
REPORT findings retain owner choices; IGNORE findings remain awareness.
Recommendations do not grant authority or rewrite a settled contract.

`heddle decisions resolve` records user-sourced resolutions. A lead applying a
specific standing user grant must name that grant, its scope and the actor in
the rationale. The source field describes the resolution path, not proof that a
human typed the command. Ordinary reversible choices derived from ratified
principles use the constrained `heddle decisions record-policy` path. Generic
autonomy does not authorize scope expansion or irreversible choices.

The [shared decision policy](../../heddle/resources/decision-routing.md) owns
classification and question quality. A `none` policy basis requires `[REVIEW]`;
new explicit `conflict` entries refuse with an owner route even when flagged.
Existing complete conflict facts remain readable and support exact retries.
Basis and rationale remain authored judgment, not a grant of authority.

Policy recording validates the whole batch before publishing its journal and
then state facts. Status/orient diagnose journal-only entries: nonconflict
entries support an identical batch retry; journal-only conflict recovery requires
an owner ruling because comma-joined alternatives cannot prove structured
identity. Changed-content collisions refuse. Final close audits require every
accepted policy fact to have a matching complete journal entry.

## Completion and recovery

`heddle feature complete` accepts one typed fact after current qualification and
the configured close suite, before stamping, archival or cleanup. The additional
close command runs in the existing checkout and retains its separate execution
authority; it does not replace native feature proof or create a clean environment.

Acceptance is immutable. The spec lifecycle stamp checks the recorded preimage
or exact postimage. Create-only `completion.tar.gz` provides local retention of
the workspace, including ignored output, with a verified member manifest and
accepted ledger. A record commit is optional; this repository's
[local-records policy](../workflow/local-records.md) keeps execution records out
of public Git history.

Only after local ledger validation and verified archival may cleanup remove
eligible generated views and known scratch files. `plan.md`, `brief.md`, native
state, canonical reviews and referenced logs remain. Unknown or changed files
are preserved and reported; the entire workspace is never recursively removed.

Acceptance and its remaining file effects are separate facts. Interrupted
stamp, archive or cleanup effects return accepted data and recovery actions;
retry retains the original acceptance identity and runs no new qualification or
suite. Pending effects use exit 4. Dry-run writes nothing and launches no suite
or provider. Historical acceptance does not claim current source is fresh.

## State publication and compatibility

`runtime/state_store.py` is the sole physical operational-state writer. Ordinary
mutations lock the workspace, validate the latest state, check any expected
revision, apply a pure transform, validate again and atomically publish. No-op
mutations retain the revision; revision conflicts refuse before writing.
Gate-event appends use the same lock and can retain the revision, so completion
also rechecks readiness-relevant records under lock. Long provider and test runs
happen outside the state lock. Ordinary writes refuse accepted history.

Current schemas are `heddle.state/v10`, `heddle.review-assignments/v3`,
`heddle.review-content/v3`, `heddle.review-result/v3`, and
`heddle.source-evidence/v1`. Active v9 ledgers remain readable and writable
through their compatibility baseline. The current runtime rejects v8, earlier,
and future state schemas without mutation. Preserve incompatible work and use
the runtime that created it; no current feature-migration command converts it.
Retained older review formats have their version-specific interpretation and do
not authorize current admission or scheduling.

## Interface semantics

CLI and application calls share typed operations and result semantics. The
versioned envelope contains `ok`, diagnostics, `next_actions` and exactly one of
`data` or `error`. Process status and `ok` are distinct: a recorded result may
still report unmet obligations. Typed command, session, authoring, decision and
manual actions carry their own payloads; displayed command strings are not the
execution authority.

| Exit code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Internal error. |
| `2` | Usage error. |
| `3` | Fatal contract failure or blocked operation. |
| `4` | Advisory findings or accepted completion with pending effects. |
| `5` | Revision conflict. |

Errors carry a stable code, message, hint and structured details. Diagnostics
have fatal, advisory or info severity. `doctor` checks environment/resources,
`validate` checks state and document relationships, and readiness qualifies a
specific transition. None substitutes for the others. Follow the observed
remedy rather than reconstructing a command from a remembered workflow.
