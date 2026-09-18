# Document Structure for Agent-Driven Development

The Feature Spec owns the product contract and durable design commitments.
The Implementation Plan owns approach, technical architecture and delivery
judgment. Native state owns operational facts. Keep each fact in its defining
home and link to it from the other documents.

## How to Use This Document

Use §2 for specification, §3 for planning, §4–5 for recording and reconciliation,
and §6 for handoff. The [workflow](workflow.md) owns process, the
[engineering principles](engineering-principles.md) own preferences, and the
[testing strategy](testing-strategy.md) owns test selection and execution authority.
This document does not create another permission or review schedule.

## Knowledge Index

Begin precedent discovery with `heddle search "<intent>" --titles-only`.
Search needs no active feature and writes no persistent index. Read relevant
results and actual consumers before choosing a design. `--json` returns the
versioned `heddle.search/v0` payload. A successful zero-hit result is authoritative;
only command failure warrants fallback to `docs/features/_descriptions.yaml`
and filenames. Missing access is not evidence of absence.

## 1. The Two-Document Model

| Information | Defining home |
| --- | --- |
| Product goal, observable contract, consequential commitments | Feature Spec |
| Chosen mechanism, dependencies, milestones and proof approach | Implementation Plan |
| Scope/complexity/testability axes, confirmed review policy, stage and authorization | Native state |
| Tasks, sessions, commands, accepted review/disposition and verification facts | Native state |
| Checkpoint rulings and review bookkeeping | Native decisions and decision journal |
| Standing engineering and enforcement policy | Governing repository documents |

The spec is the permanent knowledge entry point. The plan is retained with the
full workspace before eligible cleanup. Reading pointers connect the documents;
do not require a copied Design Context, parallel AC list or enforcement register.

### Document Size Targets

Detail earns its place by changing a decision or making a contract checkable.
Use headings, focused reading pointers and appropriate decomposition. There is
no line, hour, task-count or module-count threshold that determines correctness
or feature scope. Split independently useful responsibilities when the contract
and integration boundaries justify it, not to meet a numeric proxy.

## 2. Feature Specification

Create the formal spec only after research, explicit Direct/Heddle choice and
confirmed Heddle policy. Native admission renders the
[spec scaffold](../../heddle/resources/feature-spec.scaffold.md) at
`docs/features/<area>/<slug>.md`. Preserve its identity frontmatter. The runtime
owns `lifecycle: active` and the eventual complete stamp; do not hand-flip it.

The authoring shape is:

- **Purpose:** who can do what afterwards, why it matters and observable success.
- **Approved MVP (from brief):** approved core behavior, proposed additions,
  named deferred scope, and the Implementation Parts authorization table. A part
  without authorization is deferred. Checkpoint scope confirmation remains an
  explicit owner choice, including when the review finds the scope adequate.
  Its conceptual end-to-end outline states the e2e/live lane posture and reason;
  concrete witness design belongs in the plan.
- **Conceptual Design:** terms, interactions, competing goals where consequential,
  labeled assumptions, invariants, domain challenges and relevant failure classes.
  Cover what changes a decision. Do not substitute a component tour for behavior.
- **Acceptance Criteria:** the one canonical list of observable contracts.
- **Design Commitments:** data ownership, dependency direction, integration and
  extension seams, consumer promises and expensive-to-reverse choices. State
  which internals are delegated. Types/signatures belong here only when they are
  promises to consumers, not as a prescribed file-by-file implementation.
- **Decision Log:** durable choices, alternatives and rationale. Keep checkpoint
  transcripts and gate iteration bookkeeping in their existing native/journal homes.
- **Outcomes & Retrospective:** a short account of delivered behavior, meaningful
  limitations, durable lessons and links to retained evidence. Do not dump logs.

Optional sections are useful when they have a distinct responsibility. Record
an invariant once and reference it. Do not mandate a separate EARS list, a
cross-cutting copy of the same contract, or a pasted Impact Assessment Protocol.

### Acceptance Criteria

Keep `## Acceptance Criteria`, `### AC-<n>:`, `Priority:` and `Verified-by:`.
IDs remain stable through reordering; retire removed IDs rather than reuse them.
Sub-IDs may preserve identity when splitting a criterion. `Priority:` is MUST,
SHOULD or MAY; `Verified-by:` links to actual test targets when scaffolding lands.

A concrete AC identifies observable conditions, outcomes and consequential
failure classes, with explicit references for shared commitments. It need not
enumerate all test cases or prescribe reversible internals. The
[AC specificity taxonomy](../../heddle/resources/prompt-authoring-standards.md#ac-specificity-taxonomy)
owns Concrete/Partial/Vague and their consequences. Scaffolding owns the
discriminating examples, boundary cases and executable assertions.

## 3. Implementation Plan

Native admission creates `plans/<slug>/plan.md` using the
[plan scaffold](../../heddle/resources/plan.scaffold.md). The plan has no
operational frontmatter and is not another state ledger.

- **Quick Orientation:** governing spec and repository reading pointers.
- **Technical Architecture:** the chosen approach, rationale and consequential
  alternatives; existing owners, changed boundaries and extension seams;
  integration/dependency direction; compatibility or migration strategy where
  applicable. Explain why it fits the spec and actual consumers.
- **Environment and verification:** prerequisites, limitations and evidence
  expectations. Exact commands live in native `commands` and milestone
  `verification`, not a second copied command inventory.
- **Integrated Witness Proposal:** complete flow, lanes, ACs per lane and
  concrete fallback witnesses, deterministic conditions, real/doubled systems,
  alignment criteria/artifacts, prerequisites, execution stages, caps and
  finishing criterion. Draft at specify; confirm shape and grants at Checkpoint 1;
  realize at scaffold. Reference exact native rulings rather than copying them.
- **Implementation Strategy:** independently verifiable milestones with scope,
  expected capability, approach, owned write surface, dependencies and acceptance
  evidence. Expand tasks at the current milestone through native operations.

Keep `### Milestone <id>:` joined to native milestone IDs and preserve the
`heddle:begin plan-status` / `heddle:end plan-status` managed region. Preserve the
`### Integrated Witness Proposal`, `### Verification Commands`,
`### Live E2E Test Prerequisites` and
`### AC Coverage Matrix` reader anchors where scaffold context uses them; their
content points to native commands and records proof coverage/prerequisites,
not duplicate AC definitions. A prerequisite is not execution approval.

Verification follows the [testing strategy](testing-strategy.md): record exact
contract and direct-consumer selections, expected cost including nested launches,
and required pending proof separately from optional regression. Broad suites and
both local full-workflow e2e and live tests require explicit scope authority,
including at phase exit or pre-merge. Prerequisites do not grant execution.

The plan's verification note under `### Verification Commands` owns a declared
alignment Assessment: criteria,
assessor, native run reference and relevant source identity, exact retained
artifacts, conclusion, limitations and separate quality observations. The owning
final-boundary lead records it after the applicable pass and last relevant fix;
replacement output or relevant changes require refresh. Session handoffs and
spec Outcomes reference this entry. Judgment is not a native verification result.
The entry is authored content: its edits remain subject to source-bound review
freshness. Record it before any required qualifying final rereview, or refresh
affected closure evidence afterward.

### Complexity Levels for Milestones

Current milestones use **Low** for an established approach with understood
dependencies and **High** for consequential uncertainty, new integration or
expensive-to-reverse choices. Choose a prototype when it resolves that uncertainty.
Hours are optional metadata, not admission or sizing criteria. Do not copy
unsupported complexity values from older records into current milestones.

### Compression Protocol

Keep work-focused prose and source pointers. Native sessions hold continuity;
never truncate their history. A frontier shift is a reading/handoff task, not
reason to rewrite reviewed prose. Genuine plan changes invalidate affected
review evidence and require qualification at the applicable boundary.

### Idempotence and Recovery

There is no blanket section requirement. Describe retry, interruption, rollback
and destructive-effect safeguards at the boundary that needs them. The spec owns
observable recovery promises; the plan explains the mechanism and proof strategy.

## 4. Boundary Rules: What Goes Where

Put product changes and durable commitments in the spec; implementation approach
in the plan; progress and command facts through the CLI. Use `heddle status` for
the actual stage, task and blockers. Standing policy stays at its maintained
source. A review report does not become another approval or schedule owner.

For a surprise, use the workflow's impact assessment: local approach corrections
can continue within scope; propagated implementation changes update affected
milestones; invalidated ACs, assumptions or owner decisions require reconciliation
and the relevant owner decision before conflicting work continues.

## 5. Cross-Document Protocols

### 5.1 The `[PROPAGATE]` Mechanism

Tag durable lessons or commitments in milestone notes with their spec destination,
for example `[PROPAGATE → Decision Log]` or `[PROPAGATE → Design Commitments]`.
At milestone boundaries, apply warranted changes and mark them propagated.
Do not propagate task completion, copied reviews or environment trivia.

### 5.2 Reading Pointers and Current Context

Read the spec's relevant commitments, the plan's technical architecture and the
current milestone directly. Store next-frontier pointers in the native session
handoff. Do not maintain a derived Design Context copy or refresh timestamp.
Historical plans with such a block still defer to their governing spec.

### 5.3 Document Reconciliation

The spec defines intended behavior; code shows implemented behavior; the plan
explains the chosen route; native state shows operational progress and authority.
Resolve actual contradictions at the right owner. Do not silently rewrite a
product commitment to match code or turn delegated internals into spec defects.
Record the decision and refresh invalidated proof. Stop only the work that cannot
safely continue under the unresolved contract, escalating owner choices as needed.

### 5.4 Decision Actor and Authority

[Decision routing](../../heddle/resources/decision-routing.md) owns classification
and question quality. This section owns recording attribution, not another
procedure for deciding when to ask. Canonical review `decision` fields retain the
complete REPORT question; a compact native decision listing does not replace it.

Use the existing decision operation and rationale to preserve both who supplied
the reasoning and what authorized it. `resolution_source: user` identifies a
successful `heddle decisions resolve` path; by itself it does not say whether the
user chose the option directly or a lead applied a standing user grant.

- For an explicit user choice, say that the user selected the choice and record
  the supplied rationale or context without inventing additional reasoning.
- For a lead resolution under a standing user grant, name the lead as actor and
  identify the current grant, the scope it covers, the selected choice, and why
  that choice falls inside the grant. Keep this in the first-write rationale and
  point `routes_to` at the real repository document where the ruling applies.
  A Checkpoint 1 witness grant is one example: name the approved lane, stages,
  effects and bounds, and verify that the scaffold-bound exact command fits it.
- For an ordinary reversible choice derived from ratified engineering policy,
  use `heddle decisions record-policy --from-file <path>`. Its policy fact and
  matching journal entry own the attribution; do not route it through user
  resolution merely because an earlier example did.

If the claimed standing grant cannot be identified, or does not cover the exact
choice, record the authority gap and escalate instead of describing the result
as user-selected. Irreversible choices, amendments to ratified contracts,
cross-feature precedent, scope expansion, and acceptance of exact degraded-smoke
evidence retain their explicit owner rules. Never rewrite accepted decision facts
to improve attribution; retries reuse the original payload.

## 6. Completion and Handoff

Follow the native complete-stage briefing and applicable user authorization.
Entering `complete` does not accept the feature. Sweep propagation tags, retain
durable lessons, disposition enforcement candidates and prepare the spec outcomes,
decision journal and friction retrospective. Keep detailed evidence at its source.

Preview `heddle feature complete --feature <slug> --dry-run`, then execute under
the existing explicit completion grant. Required final proof remains mandatory
even with every reviewer off. The runtime rechecks current source/command identity,
audits and the configured close suite before one typed acceptance.

Write the feature's `GE-<date>-<slug>` gate-effectiveness entry before acceptance,
including attributable findings, quality judgments, trend and owner-decided
proposals. See [Gate Quality Monitoring](workflow.md#gate-quality-monitoring).

Acceptance precedes the runtime-owned spec stamp and full-workspace archive at
`docs/gate-trajectories/.raw/<slug>/completion.tar.gz`, which is local untracked
evidence. Local retention validates the accepted ledger and exact archive readback
before bounded cleanup. Leave both in place and retry after repairing any reported
effect; a record commit for workflow files is optional. Admission records the
source baseline from the initial source commit, so cleanup and interrupted-effect
recovery do not depend on a later Git commit. Keep completed canonical reviews at
their original paths and referenced verification/close-suite logs. Unknown or
changed files are reported, not silently removed. Never delete the whole
workspace. Exit 4 can mean accepted with pending effects; retry does not reopen
acceptance.

Completion and accepted Status, Orient and Kickoff share a `retained_evidence`
projection. Use it to distinguish validated local retention from verified
archive membership: `pending` has no member claim, `archive-bound` names verified
members, and `conflict` removes unchecked claims. Rows report exact path, roles,
kind, SHA-256 and mode. Cleanup candidates and unknown preserved files keep their
separate effect classifications. Human diagnostics render the same facts and
repair actions; terminal reads never run qualification, a suite or a provider.

## 7. Scaling to Feature Complexity

Assess scope, complexity and testability independently after research. These axes
inform a recommendation, not permission. The user chooses Direct or Heddle and
confirms the complete review policy; native state owns that choice and amendments.
No document shape silently schedules a reviewer, grants a test run or waives proof.

## 8. Agent Interaction Patterns

Implementers read the current frontier and its governing contracts. Reviewers
follow their role's delivered reading boundary: spec review judges commitments;
plan review judges architecture and delivery; scaffolding judges tests/proof;
integrated reviewers inspect implementation against the spec. Missing context
stays unavailable, not absent or passed. Future maintainers start from the spec
and its relevant code/evidence pointers, not the entire execution transcript.

## 9. File Organization

Specs live under `docs/features/<area>/`. Active and retained feature workspaces
live under `plans/<slug>/` with native `state.yaml`, plan/brief prose, `reviews/`
and `verification/`. Root-level close journals, retrospectives and
`plans/gate-effectiveness.md` survive cleanup. Split milestone prose into workspace
siblings only when useful; the native ID join and root navigation remain clear.

For configurable layouts, obtain the real workspace from
`heddle orient --feature <slug> --json`. An optional lead-authored assessment used
to support review closure is a workflow review record only when placed under
`<data.workspace>/reviews/` and bound after its final bytes and location are
known. Native dispositions still decide closure. Product assessments remain
owned product artifacts outside the protected workspace; Kickoff does not create,
move or require either assessment.

## 10. Reading older records

Apply this shape to new authoring. Do not mass-rewrite existing specs or restamp
historical state, reviews or evidence. Preserve original section names and their
meaning when reading old documents. The current runtime supports active v9 and
v10 state; incompatible work needs the runtime that created it. There is no
current `heddle feature migrate` command. See the
[compatibility contract](../design/workflow-model.md#state-publication-and-compatibility).
Document editing is not state conversion.
