# Complete

You are at the **complete** stage — Phase 9, the human-owned close. Every
required gate has converged and the exit bar held; entering this stage ran no
Phase 9 checks and stamped nothing. What remains is the knowledge handoff and
the durable close, driven with the user: until explicit completion
is accepted, the kernel derives `awaiting-human-completion` with
`heddle kickoff` as its remedy, and drive never spawns a session here. This
briefing is the full close protocol.

Work areas 1–9 below autonomously, then stop at area 10. Mutable Feature Spec,
plan, documentation, and close-artifact work is expected before the checkpoint;
do not persist proposed memory and pattern candidates to their durable stores
and delete nothing before the final checkpoint.

## 1. Orientation across permanent and ephemeral evidence

Inspect the configured close suite and nested commands before completion.
The additional close suite takes `.heddle.yaml`'s `autopilot.test_command` from
host tooling and remains separate from feature proof. Heddle does not create a
clean environment before running it; execution uses the current host checkout.
Broad suites and local full-workflow e2e/live execution need an explicit request
covering that scope; a completion grant or stored command does not independently
supply it. Existing applicable execution grants persist. Reuse current native
evidence, rerun affected checks when needed, and keep required unrun proof pending.
Optional unrequested broad regression is not a new close gate. Do not narrow a
required close command or treat excluded/skipped witnesses as passing proof.
Confirm the host's fast feedback and fast public-boundary acceptance routes meet
their chosen budgets. Any required over-budget or flaky witness must remain
explicit with measured cost, reason, owner, review point and substitute evidence;
do not reclassify required proof as optional to close the feature.

Orient before changing anything. The permanent record is the Feature Spec
(Decision Log, Surprises & Discoveries, Design Commitments, Outcomes; historical
specs may call their commitments Architecture). The
workspace holds ephemeral `plans/<slug>/plan.md` prose alongside durable
`state.yaml` session/decision/gate facts and canonical artifacts in
`plans/<slug>/reviews/`. Read the records before extracting knowledge. Cleanup removes
plan prose and attributable temporary output, while retaining the accepted
ledger, canonical reviews and referenced logs. A verified raw archive preserves
the entire workspace. Once accepted, historical verification logs and raw archives
are optional on subsequent hosts; their absence does not block new feature work.
Keep the structured ledger, canonical reviews and verification manifests.

At close, feature-caused or unattributed smoke red blocks. Only proven
outside-owned failure enters the exception path: keep its command and log,
prove that it predates the feature and falls beyond the full ownership union,
mark it unresolved, and state that it stays red.

For degraded close, explicit current user acceptance must cite the attribution
and exact red evidence. Require green acceptance plus green affected
owned-consumer tests, preserve the choice in the Feature Spec and decision
journal, and never describe smoke as green. Reaching this path through absolute
intermediate bars requires explicit user-owned decisions for the intervening
phase exits; the agent cannot infer that authority from close acceptance.

Record that authority with a class-2 `heddle decisions add --input-json` batch
item carrying `smoke_disposition`: `stage`, `attribution`, `baseline_evidence`,
sorted `outside_paths`, and non-empty milestone `owned_scopes`. The runtime
binds the latest failed smoke, green acceptance, named green owned-consumer
facts, and full ownership. Resolve it only after the user's explicit acceptance
with `heddle decisions resolve <id> --kind accept-degraded-smoke
--resolution '<accepted attribution and evidence>' --routes-to <spec-path>`.
Attribution must explain the pinned baseline evidence;
path location alone is not causal proof. Each decision authorizes only its
named current stage. A rerun or changed command, source, supporting evidence,
or ownership invalidates the old acceptance. Status still reports smoke as
failed, with the accepted decision shown separately. Preserve that decision
in the journal under `## <decision-id>` with fields `choice:
accept-degraded-smoke`, `attribution:` (the exact disposition attribution), and
`smoke_fact_sha256:` (the bound smoke digest from the decision). Close audits
these fields. Record the policy and attribution in the Feature Spec before
verification; keep the exact fact digest in the journal, outside the owned source
manifest, to avoid invalidating its own evidence. The typed completion fact
references this decision and the authorizing verifications; the retained ledger
and raw archive preserve their complete bindings after cleanup.

Interpret the final evidence on five independent axes: **content identity**
shows that the declared bytes still match a stable run; **command adequacy**
asks whether the right suite was configured; **semantic quality** asks whether
its assertions prove the intended behavior; **operational reliability** covers
repeatability, timing, dependencies, and flakiness; and **tool behavior** covers
Heddle's own execution, capture, and reporting. A fresh fact proves the first
axis and the recorded process result only.

## 2. Propagation sweep

Search the workspace records for unprocessed `[PROPAGATE]` tags — milestone
discoveries and decisions routed to the Feature Spec but not yet applied.
Apply each to its target section (Surprises & Discoveries, Decision Log, or
Design Commitments, or the corresponding historical section) and mark it
`[PROPAGATED]`. Then mine the session records
(`state.yaml` `sessions:` entries, the decision journal) for permanent
knowledge that was never tagged: library quirks, debugging insights,
workarounds a future agent needs. Permanent knowledge belongs in the spec;
operational detail (task states, timestamps, environment setup) does not.
Use independent read-only tag-scanner and knowledge-miner
subagents when the records are broad enough that their condensed reports
reduce the lead's work. Complete a compact propagation and knowledge sweep
directly. Both checks are required regardless of who gathers the evidence.

## 3. Memory candidates

Surface memory candidates from the feature's decisions, discoveries, and
session records: durable cross-feature rules that are not derivable from the
repo itself. Classify each (`user` / `feedback` / `project` / `reference`),
deduplicate against existing memory before proposing — a candidate matching
an existing entry surfaces as an update suggestion, never an auto-merge —
and recommend persist / drop / defer with a one-line reason.

Disclose memory capability explicitly: this repository provides no memory
surface of its own. Where the hosting agent has one (auto-memory, a
`MEMORY.md` index), route approved candidates there; where none exists,
report the degraded capability and put each approved candidate to an
explicit user choice — retry on another surface, drop it, or defer it
visibly into the Feature Spec. Cross-agent sync is best-effort and may be
reported unavailable. Do not build a memory system to compensate, and do not
claim cross-feature persistence that does not exist.

## 4. Reusable patterns

Extract reusable pattern candidates (`docs/patterns/`): recipes another
feature could follow, each with a concrete future-feature scenario. Match
the structure of the existing pattern library, and deduplicate against it —
propose updating an existing pattern rather than adding a near-duplicate. Every pattern write requires its own explicit approval;
pattern candidates are pass-2 items at the final checkpoint (area 10).

## 5. Deferred enforcement

Disposition every "enforce later" deterministic item as implement, retain as documented
guidance, or defer with a named reason. Documentation-only capture may proceed
inside Phase 9. An executable linter, structural-test, or CI change alters the
reviewed repository surface: after implementing it, run its targeted tests,
rerun any review gate whose evidence it invalidates, then rerun the
required final verification scopes against the current declared relevant
content and commands. If that proof cannot be completed under the
close posture, defer the executable change rather than shipping an unreviewed
mutation.

## 6. Gate effectiveness

Measure this feature's gates before writing anything else about them. Gather
the numbers from the gate summaries under `plans/<slug>/reviews/`,
`heddle status --json`, and the host's current accepted gate-trajectory
aggregate. If that aggregate is unavailable, record the trend comparison as
unavailable instead of inventing it. This feature's export lands at acceptance,
so compare against the aggregate as it stood before the feature.

Write one entry in the gate-effectiveness log (`plans/gate-effectiveness.md`,
which persists across features) with the stable ID `GE-<date>-<slug>`:

- runs per gate — count, iterations against the aggregate mean,
  CLI/model/effort, findings by severity and classification;
- classification — true positives, false positives, and every false negative
  (an issue a gate should have caught) with finding IDs, naming the gate that
  missed it and why; do not assign a miss to a gate whose declared job did
  not include the check;
- finding quality on fixed axes — precision, actionability, severity
  calibration, duplication, evidence quality — citing finding IDs;
- trend against the previous three to five features;
- improvement proposals, each naming its target class (flow, prompt,
  workflow, enforcement), the cited gap, its route, and the smallest change.

Record each proposal with `heddle decisions add`; they are pass-2 items at
the final checkpoint (area 10) and nothing is applied here. An accepted
prompt proposal starts a prompt-refinement record citing the entry ID as its
observed failure; a flow proposal becomes a proposal or decision; a workflow
proposal becomes an owner-approved document edit. Write each decision outcome
back into the entry.

Prompt distillation review: when this feature's work added instruction
text to any gate prompt in the packaged corpus, assess each addition
against the
measured-gap rule (`prompt-authoring-standards.md`
§ Change protocol). An addition with no measured gap behind it — no
recorded false negative, repeated incident class, or harness-parsed
contract — is distilled into a principle, a shared partial, or a
pattern-doc pointer rather than kept verbatim.

## 7. Outcomes, retrospective, and close artifacts

Write the Feature Spec's Outcomes & Retrospective section substantively:
what was achieved, what was harder than expected, remaining gaps, lessons
for future features.
Re-evaluate any deferred scope — recommend greenlight / drop / hold per
deferred follow-up; the call is the user's at the final checkpoint.

Write the two close artifacts the runtime audits at final acceptance:

- the friction retrospective at `plans/<slug>.friction-retrospective.md`,
  covering all four categories (Tooling fights; Prompt gaps; Validator
  false-positives; Escalations that should have been policy-resolvable).
  Build Tooling fights from this feature's rows in `plans/friction-log.md`,
  not from memory, and give each row still marked `candidate` a greenlight /
  drop / hold disposition at the final checkpoint alongside deferred scope;
- the decision journal at `plans/<slug>.decision-journal.md`, one section
  per policy-resolved decision id and per user-accepted degraded smoke
  decision, with the matching fields specified above.

The close write refuses while either audit is missing or incomplete.

## 8. Self-containment verification

The final handoff must stand on its own for someone who did not watch the run. Lead with the outcome, then explain what changed and what remains. Expand workflow shorthand on first use. Use short, literal sentences with a paragraph break between ideas. Prefer direct wording to metaphor or flourish, and lists or tables for parallel findings or options. State decisions with a recommendation and its trade-off. Report only recorded evidence. Do not infer test type, suite completeness, failure details, or actions from a count or an outcome. State unavailable detail as unknown.

Before anything is cleaned up, verify the Feature Spec passes the
fresh-agent test: an agent that has never seen this workspace or any
conversation should understand what the feature does, why it is designed
this way, what was tried and rejected, what quirks were discovered, and how
to verify it works. Read the spec top to bottom checking for gaps
(referenced-but-unexplained decisions), stale content (original design vs.
what was actually built), and missing verification. Fill gaps now, while the
workspace records still exist.

Before cleanup, audit every Feature Spec `Verified-by:` route against the final
test names and commands. Repair references made stale by test consolidation or
renaming, and confirm the required final verification facts still match
current declared relevant content, commands and source inputs. Declared live
proof is additional to acceptance and needs that same binding. Execution
deferral remains pending; only an explicit contract ruling with reconciled
commands, coverage and evidence changes an obligation. Follow the proposal's
native ruling IDs and existing grants. For older active work, reconcile existing
design and approvals without fabricating a checkpoint or restarting prior stages.
Committing unchanged content does not itself invalidate proof.

Audit any declared alignment Assessment under the plan's `### Verification Commands`. It
names criteria, assessor, the final native run and relevant source identity,
exact retained artifacts, conclusion and limitations. A relevant fix or new
output requires a refreshed assessment by the execution-owning lead before
acceptance. Revalidate affected native closure evidence after an Assessment edit;
it is authored plan content, not an exemption from source freshness or required
originating inspection. Misalignment is a blocking defect; quality beyond the contract is
observational, and explicit contract quality thresholds still bind. Reference
the Assessment in spec Outcomes and session summaries. Preserve artifacts in
durable local evidence, preferably inside the workspace for archival, or record
their external retention location. This judgment is not a verification fact.

## 9. Repository docs

Update the affected repository docs (`CLAUDE.md` / `AGENTS.md`, workflow
references) when the feature changed conventions, commands, or structure
other agents need to know — repository docs are permanent surfaces and must
reflect the shipped behavior before the workspace evidence disappears.

## 10. Final checkpoint, acceptance, local retention, cleanup

Everything above is mutable work. Finish it before accepting completion: the
accepted ledger is immutable and retries repair effects without qualifying again.
Feature admission records a source baseline from the initial source commit.
A record commit for Heddle workspace files is optional; the runtime never stages
or commits them, and completion does not move the baseline.

1. **Final checkpoint (user-owned; required for every formal feature).** Present the
   complete close package: handoff results, any declared alignment assessment
   and separate quality observations, memory and pattern candidates with
   recommendations, and the disclosed cleanup set. Apply existing explicit
   authorization to its approved scope; ask only for an outstanding owner choice.
   Proposed candidates do not enter durable stores before their required approval.
   - **Pass 1** — cleanup authorization batch-approves the disclosed `project`
     and `reference` memory candidates, where the host's memory rules permit it.
   - **Pass 2** — each `feedback` and `user` memory candidate, each extracted
     reusable pattern, and each gate improvement proposal from area 6 needs
     individually explicit approval.
   Drop unconfirmed terminal candidates visibly in the close summary.
2. **Explicit acceptance.** Preview with
   `heddle feature complete --feature <slug> --dry-run`. The preview checks
   readiness, source coverage, spec identity and the read-only audits. When
   independently inspectable close documents, configuration or spec frontmatter
   need repair, human and JSON output return one ordered report with every known
   cause, repository-relative path and legal next action. Repair all reported
   inputs and preview again; an unreadable input is not also diagnosed for its
   content, and unsafe spec paths remain integrity failures. A repair report runs
   no suite and writes no state, stamp, archive or log. A successful JSON
   preview reports `close_obligation` and `close_suite_command`; dry-run does not
   run the configured suite. Then invoke
   `heddle feature complete --feature <slug>`; `--expect-revision <n>` optionally
   requires the pending ledger revision. The command takes no narrative payload.
   `session log` remains ordinary narrative and cannot accept completion.
   Initial acceptance requires complete stage, finished milestones/tasks,
   current required evidence, exact source coverage and both close audits.
   Every configured close suite runs. An absent suite is permitted only when
   the last append-ordered grant through complete is explicitly user-sourced.
   After the suite the runtime checks current evidence and audits again, then
   compares the latest locked ledger, including same-revision review/decision
   appends. It writes one typed completion fact before any file effects.
3. **Stamp and archive.** The runtime stamps only the accepted spec preimage;
   exact postimage is an idempotent success. Other bytes or executable-mode
   changes produce a visible conflict. It publishes the whole workspace,
   including ignored output, to
   `docs/gate-trajectories/.raw/<slug>/completion.tar.gz`, create-only, with
   verified member readback. The archive provides local retention for accepted
   `state.yaml`, `plan.md`, `brief.md`, indexed review artifacts and other
   workspace evidence. A Git commit is optional and never qualifies deletion;
   `.gitignore` normally keeps `docs/gate-trajectories/` out of the repository,
   so losing the working copy loses the archive. The derived gate trajectory is exported separately
   on a best-effort basis; report any failure. It cannot substitute for the raw
   archive. Regenerate a repository aggregate if the repository maintains one.
4. **Local retention qualification.** Under the recording lock, the runtime
   rechecks the accepted local ledger, requires authored `plan.md` and `brief.md`,
   and validates every indexed archive member by path, bytes, type and mode.
   It does not consult Git HEAD. Users may commit workflow records according to
   repository policy, but record commits are optional for acceptance, archival
   and cleanup.
5. **Bounded cleanup and recovery.** The runtime compares eligible files to
   their indexed and archived bytes, type and mode before deletion. It can delete
   only recorded disposable generated views and provider scratch files. Retain
   `state.yaml`, `plan.md`, `brief.md`, every completed canonical review at its original
   `GateRun.artifact` path, every referenced verification log and the close-suite
   log. Preserve and report unknown, unmatched or new files. An eligible changed
   file stays in place with an exact-path conflict. An accepted retry treats an
   already removed disposable as cleaned only when the verified archive still
   contains its exact recorded identity. Never remove
   the whole `plans/<slug>/` directory. The Feature Spec, decision journal,
   friction retrospective, gate-effectiveness log, trajectory, raw archive and
   approved durable knowledge also survive.

Accepted completion stands if stamping, archival or cleanup is interrupted.
Use the returned manual recovery instruction for the exact conflict, then retry
`feature complete`. Retries keep the same acceptance timestamp, fact and revision;
they run no close suite, audits or current-source qualification. Fully finished
effects return exit 0. Pending/conflicting effects return accepted data with
advisory diagnostics, repair actions and exit 4, never an apparent rejection of
an already accepted feature.

Status and orient with explicit `--feature` read accepted history and show
pending repairs; portfolio reads label effects unassessed. Active selection and
uniqueness inference exclude retained completed workspaces. All state mutations
refuse accepted history. Driver execution remains a human handoff before
acceptance and reports pending effects after it, without probing providers or
spawning a completion session. Historical receipts remain historical artifacts;
current completion neither publishes nor reads them.
