# Heddle Workflow Model

## Current policy and retained model

The tiering/gate redesign supersedes the v6 tier matrix, fixed gate/synthesis
topology, review-count limits and state-shape examples below for current v10 features.
Those passages remain a version-qualified historical model, not current
authoring or execution instructions. See [current architecture](architecture.md)
for current owners and integration.

Current native state records independent scope/complexity/testability axes,
the confirmed assignment policy, original reviewer results, evidence-bound lead
dispositions and policy history. Native readiness derives required work and
closure. An off assignment grants no invocation and waives no final proof.
Research precedes the user's Direct/Heddle choice; only confirmed Heddle policy
admits a formal workspace. Current features use `heddle.state/v10`,
`heddle.review-assignments/v3` and immutable `heddle.source-evidence/v1`
manifests. Active v9 ledgers remain readable and writable through their
compatibility baseline. The current runtime rejects v8, earlier, and future state
schemas without mutation; unfinished incompatible work remains recoverable with
its compatible runtime.

Admission records a source baseline from the initial source commit. A record
commit for workflow files is optional. Completion uses local retention through
`completion.tar.gz`, with exact archive readback and recovery from interrupted
effects; it does not require Git to contain the ledger or archive.

Review ledger v3 records one immutable acceptance receipt with each successful
duty-bearing spec, plan, scaffold, or milestone transition. A valid receipt
closes that completed predecessor at its recorded source identity even when
later work changes shared paths. Public readiness labels this evidence
`accepted` and reports its boundary and receipt revision. Current milestone
verification, final review assignments, acceptance, smoke, and live proof remain
live requirements; a historical receipt cannot satisfy them. Completion archives
the receipt-bearing state and every canonical artifact or source manifest the
receipt references.

The [document structure](../workflow/document-structure.md) gives product goals,
canonical ACs and Design Commitments to the spec, approach and Technical
Architecture to the plan, and operational facts to native state. A copied Design
Context is not a current requirement. The retained completion sections below
still explain acceptance before effects; entering `complete` accepts nothing.
The native complete briefing and typed acceptance remain the current boundary.

## Historical v6 model and amendments

**Status:** ratified 2026-06-10 (D23). WM-1..8 and the §15 command-surface
deltas are adopted; §15.3's `start-feature` spelling is amended by D24 to
`feature <start|switch>` (the [active-feature contract](architecture.md#discovery-and-retained-history)); §7/§12 implement-stage deferral amended by
D40 (2026-07-08); §2.1/§9/§12 complete-stage semantics amended by D42
(2026-07-14 — all-tier HITL Phase 9, fact-backed terminality); §4/§5 and
completion retention narrowly amended by the verification-provenance D37/D42
rider; typed operational records, application actions, restricted policy
recording, explicit completion, and retained history are incorporated into the
current model above.

This document is the formal domain model for Heddle's kernel: the entities,
the state machine, the authorization rules, the `state.yaml` v6 schema, and
the derived-state catalog. It formalizes the operational skeleton that
`docs/workflow/workflow.md` describes in prose — it does not replace that
document's process guidance, and it deliberately models *less* than the prose
describes (see §12). [Current architecture](architecture.md) defines the
integration owners.

Historical source inputs were the workflow, document templates, prompt index
and ported gate engine. Current machine names and relationships belong to
`heddle/contracts/gates.py`; execution defaults to `heddle/gate/registry.py`;
native findings to the structured review contract. The historical source list
does not retain the retired Markdown extractor as an authority.

---

## 1. The design rule: store facts, derive judgments

The single rule that keeps this model small:

> **`state.yaml` stores only facts that were *enacted* — things someone did.
> Everything that can be computed from those facts is derived by the kernel
> and never stored.**

Stored (facts): tier, stage, authorization grants, the milestone/task
skeleton, gate-run records, verification records, decision records, session
entries, verification commands, and explicit completion acceptance.

Derived (never stored): required gates, gate convergence, AC coverage,
blocking conditions, `next_actions`, eligibility to exit a stage, overall
feature status, staleness. If a derived value were stored it could disagree
with its inputs — the entire class of drift bugs Heddle exists to eliminate.

Corollary: there is no `status: in-progress` field. "Status" is a view.

## 2. The state machine

### 2.1 Stages

One linear sequence of eight named stages. Names, not numbers — numbers
invite off-by-one ambiguity across tiers and read worse in `next_actions`.
The canonical workflow.md phase numbers are kept as documentation aliases.

| # | Stage | workflow.md phases | What happens |
|---|---|---|---|
| 1 | `specify` | 1–2 | Research; Feature Spec + Implementation Plan authored. Workspace is created here by `feature start`. |
| 2 | `spec-review` | 3 | Spec-review gate(s) + Checkpoint 1 decision batch. |
| 3 | `plan-review` | 4 | Plan-review gate (T3) / inline checklist (T2). |
| 4 | `scaffold` | 5 | Test scaffolding; verification commands finalized. |
| 5 | `implement` | 6 | The milestone loop (§4). Per-milestone gates run here. |
| 6 | `peer-review` | 7 | Peer review (sequential or parallel+synthesis) + Checkpoint 2. |
| 7 | `robustness` | 8 | Robustness analysis (T3 gate) / lightweight check (T2). |
| 8 | `complete` | 9 | Phase 9 handoff — an all-tier HITL work stage (D42): knowledge capture, then `feature complete` acceptance. **Terminal once the typed fact is accepted.** The ledger and canonical evidence remain; temporary work is archived and cleaned. |

Transitions are strictly forward and only via `phase-exit`. A stage that the
feature's tier marks inactive (§3) is **auto-satisfied**: `phase-exit` passes
through it without work, recording the pass-through. There is no backward
transition — a Level 3 re-plan revises the milestone skeleton and
documents but does not move the stage backward (matching today's practice,
where re-planning happens *inside* phase 6).

**Options considered (WM-1):** (a) nine numeric phases — faithful but forces
tier-conditional numbering and models pre-workspace research as state that has
nowhere to live; (b) coarse collapse (draft/build/review/done) — simple but
loses the gate routing and phase-exit boundaries the existing tooling is built
around; (c) **eight named stages, 1:1 with the phase-exit boundaries that
actually exist, phases 1–2 merged because their kickoff, artifacts, and exit
are already merged in practice. Recommended: (c).**

### 2.2 Authorization

Authorization is an **explicit, recorded act** — it answers "how far has the
user allowed work to proceed," which is different from "how far has work
proceeded." This encodes the existing Phase Transition Gate ("the agent emits
a Phase Exit Summary and stops at the phase boundary, proceeding only on
explicit user authorization").

- `stage` — where the feature is.
- `authorized_through` — the furthest stage whose *entry* the user has
  granted.
- `flow` — `hitl | auto` (D32). HITL is today's behavior (a human authorizes
  each boundary); `auto` drives the authorized machine stages via `heddle drive` and pauses
  for the all-tier human completion handoff. Default
  from `.heddle.yaml`, else `hitl`. Read at boundary evaluation, so a switch
  takes effect at the next boundary (PR-16); an in-flight switch uses `heddle
  flow set <hitl|auto>`, recorded as a flow-change fact; `feature start --flow`
  selects the initial flow.
- **Invariant: `stage` never exceeds `authorized_through`.** Every mutating
  command checks it; violations are fatal with a `not-authorized` diagnostic
  and a `next_actions` pointing at `phase-exit`.

`heddle phase-exit` is the only stage mutation. It:

1. computes **eligibility** (§7: required gates converged, no pending
   decisions for the exiting stage, milestone/verification facts complete when
   exiting `implement`);
2. scaffolds the phase-exit summary (judgment content stays with the agent);
3. records the grant (`--through <stage>`, default: the next stage) **with its
   `source` (`user | policy` — D32)** and advances `stage`.

**Flow mode and policy-sourced grants (D32).** Authorization is a recorded fact
carrying a **`source`**. One uniform rule governs both flows: *a transition
happens when the boundary has zero blocking conditions; the flows differ only in
whether human authorization is among the conditions that can block.*

- In `hitl` flow each stage boundary raises **`awaiting-human-authorization`**
  (§12.1) until a `source: user` grant is recorded — the Phase Transition Gate,
  behavior unchanged.
- In `auto` flow `awaiting-human-authorization` never arises; `phase-exit`
  records a `source: policy` grant **exactly when the boundary has zero blocking
  conditions** — evaluated across **both** the kernel's state-derived §12.1 codes
  **and** `phase-exit`'s own implement-exit-completeness gate (`_implement_exit_block`,
  the M4 D1 / A7 refinement). The runtime still cannot verify a human typed an
  authorization; it records the claim, the `source`, and makes both auditable.

Tier 1 features are granted `authorized_through: complete` at
`feature start` — full autonomy, zero ceremony, consistent with the ≤3
ceremony-interaction bar (`feature start` + two `phase-exit`s; `verify`
replaces a test run the agent performs anyway, so it adds no ceremony).

**Options considered (WM-2):** (a) user-only grants, one stage at a time —
faithful but forbids the legitimate "you're authorized through peer-review"
batch grant and makes Tier 1 noisy; (b) fully derived auto-advance
(eligible ⇒ advance) — elegant but deletes the Phase Transition Gate, which
exists *because* eligibility is checkable and judgment is not; (c)
**eligibility computed by the kernel, the grant recorded as an act, batch
grants via `--through`, Tier 1 pre-granted. Recommended: (c)** — it keeps one
command, makes autonomy a per-feature dial, and stores only the act. *(c) is
extended at D32 — the grant records a `source`, and the human gate becomes the
flow-conditional `awaiting-human-authorization` blocking condition rather than
command behavior.*

The runtime cannot verify a human typed the authorization; it records the
claim and makes it auditable (git history of the grant), which is the same
trust model the prose gate has today — now with an audit trail.

## 3. Tiers

`tier` (1 | 2 | 3) is stored once, set at `feature start`, mutable only by
explicit `--retier` (re-assessment is legitimate; silent drift is not). The
tier matrix is **kernel data, not host config** — hosts choose a tier per
feature, not a custom matrix (Principle 13's boundary: gates are data, the
workflow is not).

| Stage | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| `specify` | active (abbreviated spec) | active | active |
| `spec-review` | skipped | skipped | active — `spec-review` (+`-synthesis`) |
| `plan-review` | skipped | active — inline checklist, no gate | active — `plan-review` (+`-synthesis`) |
| `scaffold` | skipped | active — gate optional | active — `review-test-scaffolding` required (D40-E; `-synthesis` participates via convergence authority, never in `required_gates`) |
| `implement` | active — gates optional | active — `code-quality` + `self-review` per milestone | same as Tier 2 |
| `peer-review` | skipped | active — `peer-review-sequential` | active — 4 parallel reviewers + `review-synthesis` |
| `robustness` | skipped | active — inline, no gate | active — `robustness-analysis` |
| `complete` | active | active | active |

Tier 2 enters robustness for inline hardening and final verification, without a
robustness-analysis gate. Packaged routing and the kernel matrix own the current
requirements; the historical pre-canonicalization prompt-index mismatch is closed.

## 4. Milestones and tasks

### 4.1 The stored/prose split

Applying §1's rule to the plan document:

| Content | Home | Why |
|---|---|---|
| id, title, ordering, `depends_on` | `state.yaml` | sequencing is computed |
| `satisfies` (AC IDs), `owns` (paths) | `state.yaml` | AC coverage and ownership checks are computed |
| complexity, estimated hours | `state.yaml` | the overshoot signal compares against them |
| verification command + expected | `state.yaml` | the runtime executes them (§5) |
| milestone status, task list + statuses | `state.yaml` | the inner loop mutates them |
| Scope, Work, Notes, Decisions, Discoveries prose | `plan.md` | judgment — the runtime never touches it |

`plan.md` keeps a `### Milestone <id>: <title>` section per milestone for the
prose; the id is the join key. `validate` cross-checks that every state
milestone has a prose section and vice versa.

### 4.2 Statuses and invariants

- Milestone: `todo | current | done`. Exactly one `current` while `stage:
  implement` (zero immediately before the first and after the last).
- Task: `todo | current | done`, within the current milestone; at most one
  `current` (the `← current` marker, now a field instead of an arrow in
  prose).
- Tasks are authored at milestone start (task expansion) via `heddle task
  add`; 4–8 per milestone is guidance, not a kernel rule.
- A milestone may become `done` only with a passing verification fact (§5)
  newer than its last task completion. Gate requirements on `done` are
  enforced at `phase-exit`, not per milestone — the §6.2 convergence rule at
  the boundary (the legacy `check-gate-convergence.py` backstop dissolved
  into it; retired at D40-E).
- A milestone may become `current` only when all `depends_on` are `done`.
- The skeleton may be empty (a Tier 1 abbreviated plan): with no milestones,
  the one-`current` invariant is vacuous and exiting `implement` requires a
  feature-scope verification fact alone (§5). Configure that shape with
  `heddle commands set test_command --command <shell-line>` and a non-empty
  JSON path array through
  `heddle feature inputs set --from-file <path-or->`; never hand-edit
  `feature_inputs`. A milestone-bearing Tier 1 instead derives feature scope
  from the union of milestone `owns` and rejects the inert input-setting path.
- Level 3 re-plans edit the skeleton (`heddle milestone add|edit`) — recorded
  facts, judgment prose routed to the documents by the agent.

## 5. Verification facts

The loop's "VERIFY — do not proceed until verification passes" becomes a
recorded fact instead of an honor-system log file:

`heddle verify [--scope feature|m2|smoke|acceptance|live]` runs the relevant
stored command, captures output to `verification/` in the workspace, and
appends a fact containing its scope, command, result, log, and before/after
source manifest. `milestone advance`, selected phase exits, and completion
require the latest fact for each required scope to match the current command,
source set, and content identity. The D37 rider deliberately adds this drift
check while leaving exact-commit authority and semantic commit judgment out of
scope.

A fresh fact proves only **content identity** for the declared inputs and the
recorded process result. Treat **command adequacy**, **semantic quality** of the
checks, **operational reliability** (including timing and flakiness), and
**tool behavior** as separate review axes; none follows from a matching digest.
At initial completion, current evidence is reconciled with feature-baseline
changes and referenced by the typed acceptance fact. Verification logs and
canonical reviews stay directly readable after cleanup. The whole workspace is
also archived create-only with verified member integrity. The accepted local
ledger, required authored records, indexed artifacts and archive readback
establish retention before eligible deletion. Git remains descriptive and never
establishes retention or source freshness. Historical receipts have no current
authority.

(The runtime already executes host commands for gates; `verify` reuses that
bounded-subprocess machinery. This is the one command-surface addition this
model requires — WM-3.)

## 6. Gates

### 6.1 Manifest

The gate manifest (Principle 13) is not new design — it is the existing
`GateType`, lifted to data: `name`, `requires_plan`, `requires_milestone`,
`requires_diff`, `supported_clis`, `tool_permissions`, `output_contract`,
`prompt_template`, `synthesis_inputs`, `milestone_output_naming`,
`context_builders`, `preflight_applicable`, `source_selector`. The source
selector is `none`, `milestone-owns` or `feature-owned-union`; the immutable
`GateInputContract` derives it from the registry. The shared contract catalog
owns registered names/relationships; `.heddle.yaml` `gates.enabled` selects
requirements within that catalog and the tier matrix requires them. Unknown
host names refuse; host-authored gate loading is a deferred extension.

### 6.2 Run facts and convergence

`run-gate` appends a run fact:

```yaml
gate: <gate name>
scope: feature | <milestone id>
runs:
  - run_id: <opaque unique event id>
    report_findings: [{finding_id: <canonical id>, title: <text>, severity: critical | important | minor}]
    at: <timestamp>
    cli: claude | codex
    artifact: reviews/<artifact>.review.json
    artifact_sha256: <canonical result digest>
    input_hash: <captured input identity>
    review_basis_hash: <history-independent captured source digest>
    execution_config: {cli, model, reasoning_effort, sandbox}
    overridden_axes: [<explicit public override axes>]
    prompt_version: <captured authored instruction identity>
    effective_prompt_sha256: <exact provider system/stdin transport digest>
    verdict: <reported verdict or explicit engine error>
    findings: {by_severity: {critical, important, minor},
               by_classification: {implement, report, ignore, unknown},
               total, contradictions}
```

Convergence is **derived**, using the engine's existing rule verbatim:

- *Verdict-producing gates* (`spec-review`, `plan-review`,
  `review-test-scaffolding`, and their `-synthesis` variants): converged ⇔
  latest run has `status ≠ FAIL` **and** `rerun_recommended ≠ YES` — where
  for the three base gates the run evaluated is the consuming `-synthesis`
  fact's latest non-error run, and it is REQUIRED (D40-E as amended by the
  documented policy below). The bounded cleanup schedule can instead
  name one actual base-gate authority; its recorded policy ID preserves that
  interpretation after experiment admission closes.
- *All other gates* (`code-quality`, `self-review`, the peer-review family,
  `robustness-analysis`): converged ⇔ a contract-valid run exists for the
  required scope (per milestone where `milestone_output_naming`).

**Phase 6 Minor/cadence specialization (D40-B).** For per-milestone
`code-quality` and `self-review`, every Minor finding is retained but accepted
as `IGNORE`; it causes no remediation, decision, verification, or rerun. The
command-level actionability expression counts only Critical/Important
`IMPLEMENT` or unknown findings plus contradictions. Each `(gate, milestone)`
permits at most four completed non-error reviews (initial + up to three reruns)
in either flow. An actionable fourth review records an immediate class-3
decision; the runtime rejects attempt five. Engine errors remain on the separate
`gate-cap-reached` five-error path. The persisted run-fact shape and the
existence convergence rule above are unchanged.

**Synthesis-stage actionability and the generalized ceiling (D40-D).** The
four parallel peer reviewers (`review-correctness`, `review-architecture`,
`review-robustness`, `review-tests`) defer actionability only when their consuming
synthesis is required for the current stage/tier (feature D40-G). In that cell: a clean completion exits OK regardless of findings, records the run
fact and findings aggregates, and records no decisions — the actionability
expression and decision recording bind once, at `review-synthesis`, over the
merged set. The deferred set is derived from the registry (gate is a
`synthesis_inputs` member of another gate ∧ not verdict-producing).
`review-synthesis` runs once on the claude lane and consumes both suites:
four unconditional claude inputs plus four present-only codex inputs (absent
→ literal `(not run)`). The D40-B four-completed-reviews ceiling extends to
every non-verdict gate at any scope — `(gate, scope)`, not just Phase 6
milestones; the Minor-acceptance rule itself stays Phase-6-only. A rerun of
a capped gate retains every immutable canonical result and supplies its captured
content in the Disposition Rerun Ledger with adjudicated and pending decisions.
The rerun verifies dispositions against those explicit event identities. The convergence rules above are unchanged.

**Verdict-gate convergence protections (D40-E).** The Phase 3/4/5 verdict
gates receive the same discipline, adapted to their Status/Rerun loop:

- *Synthesis authority (required — documented policy).* A base
  verdict gate converges ONLY through its consuming `-synthesis` gate fact
  (same scope): the synthesis's latest non-error run decides convergence,
  and while no such run exists the gate is not converged — a clean
  single-lane base pass never converges the stage. (The original D40-E
  form fell back to the base fact's latest run when no synthesis fact
  existed; the ruling retires that fallback — at Tier 3 the dual-suite
  pipeline claude suite → codex suite → synthesis is the convergence path,
  and the kernel's next-action projection walks it round by round.) The
  static base→synthesis map is catalog-owned (`VERDICT_SYNTHESIS_AUTHORITY`
  in `heddle/contracts/gates.py`) and consumed by the kernel;
  the `-synthesis` gates themselves never enter `required_gates`.
- *Per-lane budget.* Each `(verdict gate, scope, CLI lane)` allows five
  completed non-error reviews. The runtime rejects a further lane run
  FATAL/3 only while the pipeline is unconverged; a converged gate always
  accepts a re-review of changed input. An unconverged completion at or past
  the budget records one `kind: verdict-cap`, class-3 decision with exact
  origin and target references. Its typed `extend-verdict` resolution carries
  per-lane baselines; decision IDs and rationale text confer no authority. Deliberately
  runtime-owned like the D40-D ceiling: **not** a §12.1 blocker, and
  distinct from the stage-level review cap, which remains an independent
  quality valve.
- *Disposition reruns.* Verdict-gate reruns retain each same-lane canonical
  result and carry the Disposition Rerun Ledger; the adjudication scope is
  the verdict pipeline (the gate's own decisions plus its consuming
  synthesis's, and the synthesis's inputs').
- *Tally alignment.* The review-cap tally counts a completed run as
  non-converged iff `status == "fail"` or `rerun_recommended` is not `False`
  — the exact complement of the convergence rule, so converged
  `pass_with_conditions` rounds no longer inflate the stage cap.

Since D38/H18, a failed engine run records an **error-marked** run fact
(`verdict: {status: error}` — runtime-authored, never as-reported). Error
runs are invisible to convergence: both rules above read only the non-error
runs ("latest run" = latest non-error run). They remain visible as failed
evidence and contribute to the separate `gate-cap-reached` tally (§12.1).

Every completed non-error event requires the canonical `.review.json` artifact
path and digest, complete stored execution configuration and override provenance,
review basis, and authored/effective prompt identities. Error events may omit
bindings that preparation or publication did not produce. Those fields are
validated at the current state boundary; required result consumers additionally
validate the contained canonical bytes and their exact accepted-event bindings.
Provider content never supplies an event ID. Markdown views and diagnostic
summaries derive from the validated result and confer no operational authority.

Every accepted run retains its canonical REPORT metadata. Only the effective
convergence authority creates finding decisions, each bound by `origin_run_id`
and `origin_finding_id`. Deferred producers retain evidence without creating a
second decision owner. Runtime recording validates the complete outcome, appends
the event and all decisions atomically, and returns the accepted run ID. Exact
replay returns that event without a new decision, revision or review charge.
`source`, timestamps and artifact names remain descriptive evidence, not joins.

### 6.3 Engine context seam

Runtime prepares the gate from a validated kernel snapshot and the captured
prose id-join. The engine returns an immutable `GateOutcome` and never reads or
writes the operational ledger. Runtime alone accepts and records the complete
outcome after rereading current state. Prompt context retains the milestone's
prose, while the following fields have one authoritative source:

| Engine input | Historical source | Current source |
|---|---|---|
| review milestone id | plan frontmatter `current_milestone` | current milestone; at all-done implement exit, first non-ready scope for the requested gate in state order, reselected under its lock |
| milestone title | plan body heading | `milestones[].title` |
| milestone prose section | plan body extraction | `plan.md` `### Milestone <id>:` via the id-join (§4.1) |
| `satisfies` AC IDs | prose `Satisfies:` line | `milestones[].satisfies` |
| milestone-owned paths | prose task paths | `milestones[].owns` |
| verification commands | plan frontmatter | `commands.*` |
| stale-milestone preflight | diff-overlap heuristic | retired — `milestone advance` owns the mutation (Simplification Dividend) |

### 6.4 Current boundary readiness

Feature-scoped `status` and `orient`, driver routing, milestone advance, phase
exit and final completion consume one runtime assessment of their boundary.
The kernel judges supplied observations; the runtime reads the selected canonical
results and observes their current governing sources through the gate entry seam.
Each command has its own observation frame. Writers observe again before accepting
their transition, so a preceding green read does not authorize a later write.

The `review_status` rows expose independent dimensions:

| Dimension | Meaning |
|---|---|
| `execution` | Selected completed event, missing event, or invalid supplied evidence |
| `disposition` | Existing finding policy reports settled, pending, or incoherent ownership |
| `applicability` | The recorded review basis matches current declared content, is stale, or is unavailable |

Valid canonical content remains required even when its basis is stale. Missing,
changed or invalid canonical bytes return a typed refusal with a restoration
remedy; Markdown views cannot substitute for the accepted JSON. Trailing errors
retain the latest completed selection. A synthesis must also account for the
currently selected producer set, even when a newer producer has the same source
basis. This relationship does not change that synthesis's source applicability.
Optional producer lanes join that set only while current, matching synthesis
admission. Stale optional evidence creates no new review or budget obligation;
current optional arrivals still require synthesis to consume them.

Unfinished tasks retain their work action. At an evidence boundary, verification
repair precedes review repair; required stale producers precede their synthesis.
Existing finding deferral, convergence, authorization and review budgets survive.
An exhausted non-verdict review offers restoration of its governing basis or a
fresh workspace, never a fifth call. Once every milestone is done, implement exit
assesses each required milestone scope in state order. Later phases assess their
own reviews, and disabled settled reviews acquire no new freshness obligation.
Retained finding obligations still apply after narrowing.

`status --all` and workspace discovery remain snapshot-only and omit current
review and verification rows. Terminal reads remain historical. Final completion
repeats current qualification after its close suite and records acceptance before
recoverable file effects. Accepted retries do not reassess source or reviews.
Source observations are point samples, not atomic snapshots.

## 7. Decisions and checkpoints

A decision has an explicit kind and an optional typed resolution:

```yaml
id: <opaque decision id>
kind: finding | question | verdict-cap | stage-cap | session-cap | smoke-disposition | policy
source: <descriptive raiser>
title: <text>
status: pending | resolved
resolution: null | {kind: <resolution kind>, <kind-specific fields>}
rationale: null | <authored explanation>
routes_to: [<repo-relative record path>]
resolution_source: null | user | policy
created_at: <timestamp>
resolved_at: null | <timestamp>
```

Finding decisions bind exact `origin_run_id` and `origin_finding_id` values.
Cap decisions also carry their explicit target; eligibility is derived from the
current facts. The kernel validates kind, origin, target and resolution together.
A rationale, decision ID or `source` spelling never selects an effect.

- `run-gate` creates finding decisions only for the effective authority's REPORT
  findings. `decisions add --input-json <path|->` accepts the closed
  `heddle.decision-batch/v1` input for eligible questions and escalation proposals.
- `decisions resolve <id> --kind <resolution> --resolution <rationale>
  --routes-to <path>` records the authorized user's ruling. Adjudication additionally
  requires `--run-id` for the exact current completed authority. The closed variants
  are `disposition`, `extend-verdict`, `adjudicate-verdict`, `adjudicate-review`,
  `continue-stage`, `continue-session` and `accept-degraded-smoke`; each variant owns
  only its declared effect. Superseding a permitted cap resolution appends a new
  decision linked by `supersedes`; the original ruling stays immutable.
- Checkpoints remain views. Any pending decision blocks phase exit. During
  implementation, finding decisions defer until every milestone is done;
  session and driver escalation decisions remain immediately actionable.
- Continuation and degraded-evidence authority lives in those typed resolutions.
  There is no parallel continuation-override store and no operational prose parser.

**Escalation resolution remains user-sourced (D32).** Auto sessions are denied
`decisions resolve`, authorization and equivalent privileged mutations by their
permission profile. Re-invoking `drive` after a user ruling derives the next work
from the updated typed facts.

**Restricted standing-policy recording (Wave 2).** An eligible session calls
`decisions record-policy --from-file <path|->` with `heddle.policy-batch/v1` JSON.
Each resolution supplies `id`, `title`, `question`, `options`, `choice`, `rationale`,
`basis`, `reversibility`, `confidence`, `scope` and `routes_to`, plus optional
`review_flag`. Reversibility must be `reversible`, confidence is `high|medium|low`,
scope is `feature` or the current milestone, and choice names an offered option.
A none/conflict basis requires `[REVIEW]`; basis and rationale remain authored
judgment. Unknown fields and authority-bearing proposals refuse.

The command creates only its own `kind: policy` decision and matching journal
entry. It cannot resolve an existing decision, change authorization or grant a
continuation. Under the state lock, the service rereads and checks the revision,
validates the whole batch and all collisions, then publishes the journal before
the facts. A journal failure leaves state untouched; a later state failure leaves
an unbacked journal section. Status/orient diagnose that orphan and name the exact
batch retry. An identical retry completes the fact once; a changed-content
collision refuses. The final close audit requires every accepted policy fact to
have a matching complete journal entry. Session stdout is never policy authority.

## 8. Sessions

`heddle session log` appends `{started_at, ended_at, completed, started,
key_context, next_steps, blockers}` as ordinary narrative, with optional stage
and progress fields. `orient` returns the latest entry. Text in these fields has
no completion authority. Sessions remain in the retained ledger. After
acceptance all state writes, including session appends and no-op mutations, refuse.

## 9. Explicit completion and the Spec lifecycle stamp

`heddle feature complete [--feature <slug>] [--expect-revision <n>] [--dry-run]`
is the human-owned acceptance operation. Stage entry remains a Phase 9 handoff
in every tier and flow. Completion requires finished milestones/tasks, the last
complete grant, applicable current readiness and exact ownership reconciliation,
and both close audits. Every configured suite runs outside the short ledger lock;
only a user-sourced effective complete grant permits no suite configuration.
Before readiness or a suite, completion reports every independently inspectable
retrospective, decision-journal, close-configuration and spec-frontmatter repair
in that fixed order. The ordinary error carries the ordered rows in
`error.details.repairs` and one matching next action per row; human and JSON output
describe the same causes, repository-relative paths and remedies. One unreadable
input suppresses its dependent content checks but not independent repairs. Unsafe,
missing, symlinked or nonregular spec paths remain integrity failures rather than
repair advice. A repair report runs no suite and writes no acceptance or effects.
The runtime then repeats audits and source/readiness checks, compares the latest
locked revision and readiness-relevant records, and accepts one typed fact.
Intervening narrative is preserved; same-revision gate/decision changes cannot be
lost. A concurrent accepted winner is returned as the same historical acceptance.

`feature start` writes `lifecycle: active`. After acceptance, the runtime changes
only lifecycle to `complete`, accepting only recorded raw pre/post identities and
executable mode. It preserves other bytes, LF/CRLF and mode. Missing, changed or
symlinked specs are visible conflicts. The ledger records digests, never whole
spec images or a content projection. Spec preparation failure prevents acceptance;
a subsequent stamp failure leaves acceptance intact and repairable.

Create-only `docs/gate-trajectories/.raw/<feature>/completion.tar.gz` retains the
whole workspace, including ignored output. Its internal member manifest checks
path, type, digest, mode and accepted ledger bytes; it is integrity metadata.
Only after local ledger validation and verified archival may cleanup remove
indexed disposable generated views and known provider scratch. `plan.md`,
`brief.md`, canonical review paths, referenced verification logs and the
close-suite log remain. Unknown files are preserved and reported. Changed eligible
files are conflicts; a retry handles only remaining eligible files whose exact
archived identities still exist. No recursive workspace removal or runtime Git
commit occurs.

Before acceptance, errors use the ordinary data-free envelope. After acceptance,
`accepted`, `accepted_at`, `revision`, `wrote` and stamp/archive/cleanup effect
states report the historical fact and remaining work. Pending effects use exit 4
and exact repair/retry actions; all effects finished use exit 0. Retry ignores a
stale caller revision and current source because acceptance is already immutable.
Dry-run is read-only and launches neither suites nor providers.

This replaces D42's narrative marker and the D37/D42 receipt-first close ordering;
those mechanisms survive only in historical documents and evidence.

## 10. Historical `state.yaml` v6 model

### 10.1 Retained historical schema

This historical section describes `heddle.state/v6`. Required keys and closed
variant shapes are defined in `heddle/contracts/schemas.py` and parsed by
`heddle/kernel/state.py`. This outline shows the field relationships; angle-bracket
values are explanatory placeholders, not a loadable fixture.

```yaml
schema: heddle.state/v6
feature: <slug>
spec: <repo-relative path>
tier: 1 | 2 | 3
revision: <nonnegative integer>
created: <timestamp>
updated: <timestamp>
stage: <stage slug>
authorized_through: <stage slug>
flow: hitl | auto
authorizations: [{through: <stage>, source: user | policy, at: <timestamp>}]
flow_changes: [<flow-switch facts>]
commands:
  smoke_test: <command>
  dev_setup: <command>
  test_command: <command>
  lint_command: <command>
  acceptance_test: <command>
  live_e2e_test: <command>
feature_inputs: [<paths for a Tier-1 empty skeleton>]
completion: null  # one CompletionFact after explicit acceptance; see below
milestones:
  - id: m1
    title: <text>
    satisfies: [AC-1]
    depends_on: []
    owns: [<paths>]
    complexity: low | medium | high
    estimated_hours: [<lo>, <hi>]
    verification: {command: <command>, expected: <text>}
    status: todo | current | done
    tasks: [{id: t1, text: <text>, status: todo | current | done}]
gates: [<gate/scope groups with event identity, REPORT metadata and native result bindings; §6.2>]
verifications:
  - {scope, command, exit_code, at, log, provenance}
decisions: [<typed decisions and resolutions; §7>]
sessions:
  - {started_at, ended_at, completed: [<text>], started: [<text>],
     key_context, next_steps, blockers: [<text>],
     stage, revision_before, revision_after, advanced}
```

Verification provenance is required and non-null with schema
`heddle.verification-source/v2`. It carries normalized nonempty
`declaration_paths`, selected current `paths` (possibly empty), source kind and
before/after content identities. Old/null provenance refuses at the current
reader boundary. Runtime-owned review bindings use `heddle.review-result/v2`;
model judgment content remains `heddle.review-content/v1`. Session list fields are lists at the
boundary; scalar/list coercion is removed. Every run, including an engine error,
has an event ID and REPORT metadata. Typed decisions own cap baselines,
adjudication and degraded-smoke acceptance; `continuation_overrides` is rejected.

When accepted, `completion` contains exactly `accepted_at`,
`authorization_index`, `verification_indexes`, `review_run_ids`,
`accepted_smoke_decision_id`, `spec_stamp`, and `close_suite`. References are
zero-based indexes or existing unique event/decision IDs in this same ledger.
`spec_stamp` carries raw before/after SHA-256 and an executable boolean;
`close_suite` is null under the user-grant exception or `{command, log, exit_code}`
with a real zero exit. Required scopes reference the latest stable matching
verification facts in boundary order; accepted degraded smoke retains its exact
user decision separately and never becomes a passing verification.

### 10.2 Fresh-state cutover

No v5 or earlier ledger converter or backfill runs in the candidate. Preserve incompatible
workspaces and their evidence, complete an active feature with its compatible
pinned supervisor, and create a fresh current workspace between closed features.
The D32 additions describe their historical landing; they do not authorize an
older ledger shape at this boundary. Executable current examples live in
`tests/fixtures/workspaces/` and the operational-model acceptance tests.

## 11. Concurrency: the CAS token

`revision` is a monotonically increasing CAS token. `runtime/state_store.py`
owns physical state publication. A snapshot mutation takes the short OS state
lock, rereads and validates the current document, checks the expected revision,
applies a pure transform, validates the result and atomically publishes it.
Mismatch is exit 5 (`conflict`) before any mutation. The no-op path keeps the
revision. Each public single-state mutation with a typed `expect_revision`
field accepts optional `--expect-revision <n>` and preserves it when rendering
the typed operation as a command. Malformed or negative input is a usage error;
omission retains the command's existing behavior. Create-only feature preparation,
worktree pointer changes, generated-file sync, gate execution and driver
orchestration do not acquire a feature-state expectation through this rule.
Gate event recording uses its append mode under the same state lock and retains
the revision; the service preserves intervening events when applying a snapshot
mutation. Completion additionally compares readiness-relevant records under that
lock because an event append can keep the revision unchanged. Every ordinary
writer refuses an accepted ledger before applying its transform. The driver and
gate engine are not additional state writers.

**Options considered (WM-6):** content hash (no counter, but formatting-
sensitive and unreadable in diffs); file mtime (unreliable across git
operations); **integer revision — trivially mergeable to reason about (a git
conflict on `revision` *is* a true concurrent edit), human-legible in diffs.
Recommended.**

## 12. Derived-state catalog

### 12.1 Blocking conditions

| Code | Meaning | Typical `next_actions` |
|---|---|---|
| `not-authorized` | requested work implies a stage beyond `authorized_through` | `phase-exit` |
| `gate-not-converged` | a required gate for the exiting stage lacks a converged run | `run-gate <gate>` |
| `pending-decisions` | unresolved decision(s) block the boundary; during `implement`, gate-recorded (class-4) decisions defer to the stage boundary — D40 | `decisions` |
| `verification-missing` | milestone/acceptance verification fact absent or stale | `verify --scope …` |
| `milestone-out-of-sequence` | `depends_on` not satisfied / two `current` | `milestone edit` |
| `task-incomplete` | advancing a milestone with open tasks | `task done …` |
| `workspace-invalid` | invalid current state or incompatible ledger schema | `doctor`; preserve evidence and use a compatible supervisor or a fresh workspace |
| `awaiting-human-authorization` | HITL flow: a stage boundary awaits a `source: user` grant (never arises in auto) — D32 | `phase-exit` |
| `awaiting-human-completion` | the snapshot is at `complete` with no typed completion acceptance; derives after the ordinary rows, at every tier and in both flows — D42 | `kickoff` |
| `principles-not-ratified` | auto flow standing precondition: `engineering-principles.md` is not `status: ratified` — D32 | (ratify the principles doc) |
| `review-cap-reached` | auto flow: a `spec-review`/`plan-review` scope has ≥ 5 non-converged recorded runs (PR-2) — D32 | `decisions` |
| `gate-cap-reached` | auto flow: the required gates for the current stage have ≥ 5 error-marked recorded runs (engine attempts that did not complete) — D38/H18 | `decisions` |
| `no-progress` | auto flow, driver-raised (M4A class 6): the last two recorded session facts for the stage advanced nothing | `decisions` |
| `attempt-cap` | auto flow, driver-raised (M4A class 7): the recorded session count for the stage reaches the cap | `decisions` |

**Flow-conditional derivations (D32; extended by D38/H18).** Three §12.1
derivations depend on `flow`: `awaiting-human-authorization` (raised only in
`hitl`), `review-cap-reached`, and `gate-cap-reached` (blockers in `auto`; in
`hitl` an iteration cap remains an *allowed exit*).
`principles-not-ratified` is a standing `auto`-only precondition. All four are
derived from recorded facts (the grant ledger, the principles-file status, and
the counts of non-converged / error-marked runs for the gate scope).
**Complete-stage derivation (D42, amended by W5).** A pending `complete` stage
retains ordinary correctness rows, followed by `awaiting-human-completion`.
The five auto-execution-only conditions (`principles-not-ratified`,
`review-cap-reached`, `gate-cap-reached`, `no-progress`, `attempt-cap`) never
derive there. Typed acceptance makes the ledger terminal: no current-source
qualification or workflow action is derived afterward. Runtime read surfaces
may add pending effect repair actions. Explicit historical reads and the portfolio
retain completed workspaces, while active pointers, uniqueness inference and
`feature switch` exclude them. Invalid ledgers are surfaced, never silently dropped.

`review-cap-reached` generalizes to `gate-cap-reached` for every required gate
at the current stage (H18): failed engine runs record error-marked run facts
(`verdict: {status: error}`) that do not authorize convergence, cache reuse,
artifact/entry progress or completed-review credit. The error cap counts
them since the matching typed resolution baseline. Eligible user-authored
`continue-stage` and `continue-session` resolutions record those baselines (§7).
Error events remain visible in status and diagnostics; they cannot authorize
convergence or completed-review credit.

Cross-plane conditions (`ac-unassigned`: a Spec AC in no `satisfies`;
`plan-section-missing`: milestone with no prose section) are **`validate`
findings, not hot-path blockers** — they require parsing the knowledge plane.

### 12.2 `next_actions` derivation

One rule: *the first unmet requirement of the current stage's exit
conditions, in matrix order; if all are met, `phase-exit`.* Within
`implement`: the current task → `task done` → `verify` → gates → milestone
advance → next milestone or `phase-exit`. This is a lookup, not a planner —
the agent exercises judgment about *how*; the runtime names *what's legal
next*.

Session entry routes by the same derivation (D26): a freshly-entered stage —
no facts yet reference the current stage — routes to `kickoff`; a mid-stage
resume routes to the current task and the latest session entry's
`next_steps`. `kickoff` itself is a pure read: a rendered briefing is not a
workflow fact and is never stored. An accepted feature bypasses fresh-stage
kickoff routing; `kickoff` gives only historical completion/recovery guidance.

**HITL work projection (M8A steering amendment, 2026-07-22).** In HITL flow
the `awaiting-human-authorization` condition holds for the whole stage
(grant == stage), but a grant *through the current stage authorizes the
stage's work* — the human pause is a boundary event. While stage work
remains (fresh-entry kickoff, implement task steps, or an unconverged
required gate's dual-suite walk), `next_actions` projects that work; the
authorization row is projected only at exit-readiness. Blocker
*classification* is unchanged: the blocking payload still lists the
condition, and the driver's supervised pause (`first_actionable_blocker`,
M4A AC-24) still fires — with the work projection as its displayed command.
The same projection is attached to every completed `run-gate` success
envelope, so a finished dual-suite leg names the round's next step itself.

### 12.3 Interface constants (pinned here for increment 1)

- **Exit codes:** `0` success · `1` internal error · `2` usage error ·
  `3` fatal (contract violation, blocked operation) · `4` advisory findings
  (verdict gates: `pass_with_conditions`; equation-bearing non-verdict gates:
  actionable
  findings remain — `implement`/`unknown`/contradictions > 0 — D40; the four
  deferred peer reviewers exit 0 on any clean completion — D40-D, §6.2) ·
  `5` CAS conflict (retry).
- **Diagnostic severity:** `fatal | advisory | info`.
- **Error object (D25, amended 2026-09-13):**
  `error: {code, message, hint, details}` — `code` stable and
  machine-matchable, drawn from §12.1 plus
  `usage | internal | conflict | feature-ambiguous` (later additive members:
  `not-implemented` D27, `ledger-drift` D36, and the D40-C resolution codes
  `invalid-route | decision-already-resolved`, plus `verification-failed |
  disposition-batch-invalid`);
  `hint` names the remediation and `details` is always a JSON object, empty
  when the operation has no structured error context.
- **`next_actions` entry:** `{action, command, reason}` in `heddle.envelope/v2`.
  Command actions contain `{kind: command, operation: {name, arguments}}`;
  session actions contain `{kind: session, feature, work}`; manual actions contain
  `{kind: manual, instruction}`. `command` is generated presentation. The driver
  executes the typed operation or session work; it never splits that string or
  executes a manual remedy. `work` is `stage-work` or `task-expansion`.
- `runtime/application.execute` accepts frozen operation inputs and returns the
  original `HeddleResult`. CLI adapters own parsing and rendering. `ok` and exit
  code are independent: a completed unconverged gate can have `ok: true` with
  exit 3/4. A verification command succeeds only for zero-exit fresh proof;
  recorded unusable proof returns `verification-failed` while retaining exact
  attempt identity in `error.details`. The driver preserves those distinctions
  and diagnostics without envelope reconstruction.
- Review disposition input is atomic. For a structurally valid batch with
  ordinary row failures, `disposition-batch-invalid` returns every rejected row
  once in input order, including its zero-based index, first failing predicate,
  observed context and remedy. Dry-run and apply use the same qualifier; neither
  publishes any row when one or more rows fail. Canonical evidence corruption,
  state integrity failures and CAS conflicts retain their own error classes.
- Changed payloads advertise `heddle.status/v1`, `heddle.run-gate/v1` and the
  `heddle.decisions-{list,add,resolve,record-policy}/v1` family. Unchanged payloads
  retain their own versions; `heddle.manifest/v0` describes the same manifest
  structure. Private Python handler names are never public command metadata.

## 13. Historical migration boundary

The earlier `migrate --from-legacy` command moved plan-frontmatter fields into
an operational ledger. That converter and its command are retired in Wave 2.
Current callers use the typed operations over fresh v5 state (§10.2); historical
plans and evidence remain knowledge-plane records, not an alternate state reader.

## 14. Deliberately not modeled

Per the no-over-engineering bar, these stay prose/judgment, with the state
file recording only their *consequences*:

- **Impact levels (1/2/3)** — classification is judgment; consequences are
  milestone edits, decisions, and document updates, all already modeled.
- **`[PROPAGATE]` tags and propagation sweeps** — knowledge-plane mechanics;
  `validate` may later grep for leftover tags as an advisory check.
- **Design Context freshness** — derivable from git timestamps of spec vs
  plan when needed; no stored freshness state.
- **Enforcement extraction** — a workflow practice, not workflow state.
- **Backward stage transitions, feature dependencies, sub-features, parallel
  milestones** — add when a real feature demands them, not before.

## 15. Command-surface decision provenance (ratified 2026-06-10)

These amendments retain their decision history. Current flags, payloads and
operation spellings are published by `heddle help --json`, including the later
`heddle feature complete` addition in §9.

1. **`heddle verify`** — new (§5); turns verification into a recorded fact.
2. **Noun-verb consolidation:** `heddle milestone <add|edit|advance>`,
   `heddle task <add|done|current>`, `heddle session log`,
   `heddle decisions <list|add|resolve|record-policy>` — replacing the flat
   `advance-milestone` / `task` / `log-session` spellings (amends D11's
   naming, not its substance). One consistent scheme reads better in
   `next_actions` and `help --json`.
3. **`feature start --tier N [--retier]`** — tier set at creation, explicit
   re-assessment only. *(Amended at ratification by D24: `start-feature` is
   respelled `feature start`, joined by `feature switch` for active-feature
   selection.)*
4. `status --json` payload: `phase`/numeric is superseded by
   `stage`/`authorized_through` slugs.
5. **`heddle kickoff`** — added post-ratification by D26: renders the current
   stage's briefing for the active feature; a pure read, no stored facts
   (§12.2).

## 16. Decision summary

| ID | Decision | Recommendation |
|---|---|---|
| WM-1 | Stage model | 8 named stages, 1:1 with real phase-exit boundaries, phases 1–2 merged |
| WM-2 | Authorization | computed eligibility + recorded grant **with `source` (`user\|policy`)**; **flow-conditional human gate (`awaiting-human-authorization`, HITL only) — D32**; `--through` batch grants; Tier 1 pre-granted |
| WM-3 | Verification | `heddle verify` records facts; advancement requires fresh pass |
| WM-4 | Decision batches | recorded by `run-gate` at completion; checkpoints are derived views; implement-stage gate decisions defer to one end-of-implement batch with `--resolution` prefills (D40) |
| WM-5 | Spec lifecycle | one runtime-written `lifecycle` stamp in Spec frontmatter |
| WM-6 | CAS token | integer `revision` field |
| WM-7 | Stored/prose split | facts in `state.yaml`, judgment prose in `plan.md`, id-joined |
| WM-8 | Tier matrix | kernel data with fixed semantics; hosts pick a tier, not a matrix |
| WM-9 | Active feature | explicit `--feature` > active worktree-local pointer > active uniqueness; ambiguity names candidates; explicit historical reads remain available ([D24 current contract](architecture.md#discovery-and-retained-history)) |
| WM-10 | Session entry | `orient` routes — fresh stage → `kickoff`, mid-stage resume → current task + `next_steps`; `kickoff` is a derived read, never a stored fact (D26) |
