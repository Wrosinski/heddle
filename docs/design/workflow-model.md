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
for the underlying recorded evidence. Evidence identity for the feature plan
therefore excludes a well-formed plan-status region: gate inputs, review
assignment bases, cited references and attribution references hash the plan's
authored bytes, so `heddle sync` never makes feature-plan evidence stale. Every
other cited file, and a plan with malformed markers, is hashed as it stands.

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

Each review role has a feature or milestone scope, a primary reviewer, an
optional independent secondary reviewer, and one of these modes. The secondary
serves the rounds its `secondary_rounds` window names, counted from the
review's first round: a positive integer *n* for rounds 1 to *n* (at most an
upper-limit role's round limit), or `all` for every round, which follows later
allowance raises. Absent means 1 and is how window 1 is stored; a value other
than 1 needs a secondary. Each round freezes its reviewers when it opens, so an
amendment applies from the next round, and an amendment that adds, replaces or
widens a secondary is refused when that secondary could serve no round the role
can still open. Older Heddle builds refuse a policy that sets a window other
than 1. The modes are:

| Mode | Meaning |
| --- | --- |
| `off` | Intentionally not run; no invocation is authorized by that assignment. |
| `upper-limit` | Required minimum rounds and an explicit maximum. |
| `convergence` | Required minimum rounds without a numerical quality-round ceiling; closure and stop rules still apply. |

Reviewer selections include CLI, model and reasoning effort. The policy exposes
minimum and maximum call budgets: each active role adds its primary rounds plus
the rounds within them that its secondary serves; an unbounded assignment is
reported as such.
Amendments retain policy history and spent work. All-Off review policy does not
waive final verification. Robustness review requires an explicit integration gap
and supporting references; it is not inferred from size alone.

The recommended reviewer defaults are:

| Review role | Primary model / effort | Secondary model / effort | `secondary_rounds` |
| --- | --- | --- | --- |
| `spec-review` | `gpt-6-astra` / `xhigh` | `claude-fable-5-1` / `xhigh` | `all` |
| `plan-review` | `gpt-6-astra` / `xhigh` | `claude-fable-5-1` / `xhigh` | `all` |
| `review-test-scaffolding` | `claude-opus-5-5` / `xhigh` | None (suggested: `gpt-6-astra` / `xhigh`) | `all` when the suggestion is adopted |
| `milestone-review` | `claude-opus-5-5` / `xhigh` | None | — |
| `peer-review-sequential` | `claude-opus-5-5` / `xhigh` | None | — |
| `behavior-review` | `claude-opus-5-5` / `xhigh` | `gpt-6-astra` / `xhigh` | 1 |
| `complexity-review` | `claude-opus-5-5` / `xhigh` | None | — |
| `robustness-analysis` | `gpt-6-astra` / `xhigh` | `claude-fable-5-1` / `xhigh` | 1 |

The recommendation's suggestions list the scaffolding secondary. A suggestion
selects nothing and schedules no call until the owner copies its `secondary`
and `secondary_rounds` into that role's entry at confirmation.

GPT models use Codex; Claude models use Claude Code. These reviewer choices
apply across scope, complexity and testability assessments. Those axes still
select the Light or Full role schedule and round limits. An Off role may retain
both reviewer selections, but schedules no calls and adds nothing to the call
budget. Robustness remains Off until explicitly selected with an integration gap.
Existing confirmed policies retain their recorded reviewer choices.

Every reviewer may run commands to gather evidence and must not modify files,
records or repository state. Codex reviewers run with `danger-full-access`.
Claude Code reviewers get Read, Grep, Glob and Bash under
`--permission-mode auto`: the read tools are pre-approved and Bash commands pass
the auto-mode classifier. They inherit the operator's Claude settings. A Claude
reviewer's commands time out after 600 s by default and 900 s at most; each
attempt is capped at 500 turns and 50 USD. Gate runs of either CLI end after
1200 s without stream activity and 2700 s overall.

The [policy resolver](../../heddle/kernel/feature_policy.py) and
[role catalog](../../heddle/contracts/gates.py) own these rules.

## Stages and authorization

The native stage order is forward-only. Research and route selection happen
before admission; the workflow guide's phase numbers are explanatory labels.

| Stage | Work and boundary |
| --- | --- |
| `specify` | Author the spec, plan, integrated witness proposal and milestone skeleton; at the specification checkpoint, present the overview and record the owner's scope, witness shape, prerequisite and lane-grant rulings before any review. |
| `spec-review` | Review the product contract; at exit the lead confirms spec-review changes to the owner's specification-checkpoint ruling. |
| `plan-review` | Review approach, architecture and delivery strategy; at exit the lead confirms plan-review changes to the owner's specification-checkpoint ruling. |
| `scaffold` | Realize the confirmed witness, bind acceptance criteria and exact commands within the recorded grants; route material changes to the original owner. |
| `implement` | Complete tasks, verify and review each milestone, then advance it; exit only after every declared witness lane passes. |
| `peer-review` | Review the integrated change; perform final proof, including the default witness rerun, when robustness is Off. |
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
declared live proof is additional. The implement lead assesses the retained
output of the passing pre-review run, and the owning final-boundary lead
refreshes that assessment after the last relevant fix and applicable passing
run, with native run/source and artifact references. Misalignment blocks the finishing
claim; quality observations and the spec's judgment-based Assessment Targets
never gate it. Session/spec summaries
reference the plan's assessment instead of creating another evidence owner.
Assessment edits are authored plan changes, so source-bound review evidence can
become stale; existing qualification and originating-inspection duties remain.

Native enforcement places the witness at two points. Once every milestone is
done, implement exit requires current passing `acceptance` and, when
`live_e2e_test` is declared, `live`, so review starts only after the witness
has passed. At the final boundary the same scopes are required again: acceptance
at peer review when it owns final proof, declared live at robustness and
complete. Robustness is traversed even with R Off, so the peer-review lead owes
live before handoff even though the peer boundary does not enforce it.

A stale lane at those later boundaries reruns by default. While review work at
that boundary is open, readiness orders that rerun after the review actions;
the lane still blocks exit. A user-resolved
class-5 `witness-waiver` decision with resolution `accept-prior-witness` can
instead waive one lane's rerun. It binds the identity of the latest scoped fact,
which must be stable, passing and on the current command, plus the ownership
union and current source digest. While those bindings hold, the boundary and
`complete` accept the content-stale or source-set-stale fact; any later
relevant edit or new run makes the waiver inert. The waiver never applies at
implement exit, never qualifies review-disposition evidence, and is named by
the completion fact. Like accepted degraded smoke, it needs a matching decision
journal entry at close.
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

Ownership is an explicit proof dependency, separate from source coverage.
Unowned bookkeeping edits do not invalidate verification. Intentionally owned
material contract documents remain byte-strict, and explicit citations retain
separate evidence identity. Material requirement changes still follow the
Impact Assessment and Re-Plan protocols. The exact coverage controls include
feature records, generated evidence roots, configured shared logs/spec indexes,
the feature brief and available admission-bound research; whole plans/specs
roots and neighboring product documents are not exempt.

Validate, status, milestone advance and phase-exit share early coverage advice,
including unavailable baselines and empty early ownership. Observation does not
write or block ordinary transitions; integrity errors remain failures. Completion
requires full ownership/control/qualified-attribution reconciliation. Repair
ownership through `milestone edit` with replacement `owns` or `owns_append`, never
both. Append normalizes and unions on todo/current/done milestones; replay is a
no-op and growth requires refreshed affected proof.

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
A waived witness rerun likewise keeps its stale status, with the waiver shown
separately.

## Review results and closure

The runtime binds each canonical review result to its assignment, round,
reviewer slot, inputs and execution identity. Original findings remain intact.
The lead records dispositions with exact source references and supporting
inspection, contract, verification, review or decision evidence. A clean later
report alone does not settle an earlier finding or coverage obligation.

Readiness checks execution, disposition and applicability separately. Relevant
changes to an assignment's subject or cited evidence can invalidate its current
closure. Repairing evidence does not automatically require another provider call;
follow the returned action. When every open concern is an addressed or settled
disposition whose cited evidence changed, that action is a fresh disposition, not
a mandatory stop, even at the round limit. The same holds at the round limit when
every open concern is the latest round's own `@coverage` duty and none has a
disposition yet. Completed imperfect responses retain
their original captures, and a capture-bound lead interpretation can preserve
independent-slot credit without a formatting-only retry.

Round opening enforces the confirmed allowance and stop decisions. Continuing a
stopped review requires its explicit decision; a reached upper limit additionally
requires an approved policy amendment. `heddle review allowance` raises a role's
absolute quality-round limit without calling a provider or resolving its stop.
No amendment resets spent calls or erases earlier obligations.

An unsealed lead-closed assignment can open a `verification` round within its
confirmed allowance using `heddle review round-open --input-json <file>`. The
`heddle.review-round-input/v1` payload names role, scope, purpose and reason.
Targets derive from required concerns across the preceding round's accepted
slots plus still-open originals; the open-concern progress record is unchanged.
Direct and retained-result validation require every frozen target explicitly,
including synthetic `@coverage`. Old captures without that operand retain their
original validation meaning. Recording a clean follow-up still creates its own
coverage duty, requiring ordinary lead disposition.

Launch advice names selected nonignored staged, unstaged and untracked inputs
for formatting/commit hooks before paid review, without a new flag or refusal
solely for dirtiness. Failed Git observation is unknown. Actual review inputs
remain hash-bound, including ignored records selected by the review contract.

`heddle review reaffirm --role <role> --scope <feature|mN>` expresses the lead's
current applicability judgment for latest affirmative dispositions. It preserves
captured references, reasons and evidence/inspection bindings and qualifies the
whole selection before appending basis/time changes. Current rows are checked
before no-op; superseded or unresolved rows are not resurrected. Changed citations,
stale supporting proof and sealed assignments refuse. Dry-run is read-only and
expected-revision enforces CAS. No missing duty, test, review or decision is
completed implicitly.

Exact replay is distinct from closure. The reuse owner validates original
identity and artifact integrity before returning retained output without a new
provider call or event. Reusing a result does not make unresolved findings pass.

A lead may author an optional workflow review record to support a native
disposition. Its legal location is `reviews/` under the exact workspace returned
by `heddle orient --feature <slug> --json`; callers must not reconstruct a
`plans/<slug>/` path when a host can configure another layout. Finalize its bytes
and location before evidence binding. Native dispositions remain the closure
authority. Product assessments remain owned product artifacts outside the
protected workflow workspace, and Kickoff guidance creates or moves neither kind.

Review slots launch together by one derived rule at every stage: readiness
joins the routed slots whose reviews read none of each other's findings into
one `heddle run-gates` action. It re-derives them under their gate locks, runs
isolated workers and records results one at a time in catalog role and slot
order. `reviews.launch: sequential` keeps one slot per action; callers cannot
supply role groups or worker counts.

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

Completion preview reports the prospective `close_obligation` and
`close_suite_command` with no `close_suite` execution fact. Accepted completion,
retry and terminal read projections report the recorded fact and command,
without substituting later configuration or running the suite again.

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
Retained evidence and authored records match their recorded and archived
identity on bytes, kind and the executable bit, the only mode bit Git keeps; a
cleanup candidate must also keep its exact recorded mode.

Acceptance and its remaining file effects are separate facts. Interrupted
stamp, archive or cleanup effects return accepted data and recovery actions;
retry retains the original acceptance identity and runs no new qualification or
suite. Pending effects use exit 4. Dry-run writes nothing and launches no suite
or provider. Historical acceptance does not claim current source is fresh.

Completion and terminal Status, Orient and Kickoff project one deterministic
retained-evidence report from validated local identities. Rows are path-sorted
and carry exact unique roles and local presence; present rows add kind, digest
and mode. Verified member names appear only while archive readback matches. A
cleanup-candidate conflict can preserve an `archive-bound` report; retained-file
damage, archive damage or cleanup-time archive revalidation failure yields
`conflict` without unchecked member claims. Unknown preserved files and
disposable candidates remain in the existing effect report. The compatible
historical read-only branch omits the current report.

After acceptance, raw captures, review, verification and close-suite logs and
the completion archive are checkout-local. A verified archive accounts for such
files absent from the workspace. Without an archive, the absence of one of them,
or of a disposable review input (a derived view, or temporary output with no
recorded mode), makes the report and the archive and cleanup effects
`not-local`, an informational exit-0 state that publishes nothing; the archive
effect lists absent disposable inputs in `absent_disposable`. With every input
present and unchanged, a missing archive stays `pending`. The call that records acceptance treats any absence as a conflict. A
present capture or review log that differs from its recorded digest, a present
file that differs from its verified archive member, a missing ledger, canonical
or evidence record or verification manifest, and an archive that lacks an absent
file remain conflicts. Verification and close-suite logs carry no digest, so
without an archive a rewritten one goes undetected.

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
