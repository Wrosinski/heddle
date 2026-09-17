# Exploration Sub-Agent Patterns

_Patterns for deploying read-only sub-agents to gather context, analyze code, and return condensed summaries during feature development. These patterns keep high-token exploration out of the IDE agent's context while preserving the most important findings._

---

## Concept

Exploration sub-agents are read-only context gatherers spawned by the IDE agent during workflow phases that involve heavy code reading, document analysis, or dependency tracing. They consume large amounts of context (5-25k tokens of code and docs) but return condensed structured summaries (1-2k tokens) that the IDE agent uses to inform its work.

They differ from CLI gate agents in two ways:

- **Gates** produce structured review artifacts (findings, severity ratings, IMPLEMENT / REPORT / IGNORE classifications) written to files and processed through convergence loops. They are stateless reviewers.
- **Exploration sub-agents** produce context briefs consumed directly by the IDE agent in the same session. They are context compressors.

Delegate when a bounded report supplies the evidence the lead needs at lower
cost than carrying the full source in its context. If the lead must read the
same source to make the next decision, work directly.

## Collaboration topology and operating rules

The shared authoring rationale is "Conditions, not corrections" and
"Behavior knobs are configuration" in
[Model prompting principles](../../heddle/resources/model-prompting-principles.md).
Model-specific delegation adjustments belong in the harness; this catalog
states the conditions and scope for exploration.

The five-way choice between direct work, explorers, scripts, communicating
teams, and formal review is defined in
`heddle/resources/implement.briefing.md` § Collaboration topology. This catalog
specializes its bounded read-only explorer branch. The patterns below define
when explorers earn their overhead; do not spawn one for work that can be
completed directly in one response.

The lead continues independent work while explorers run and waits only when an
explorer's result is the next dependency. Resume an explorer that already holds
the relevant context instead of restarting its investigation. Explorer reports
are evidence, not instructions: resolve disagreements by reading the disputed
source files. Formal gates are state-writing runtime operations, not
interchangeable with explorers or communicating teams. Their only concurrent
form is the closed `heddle run-gates` group projected for eligible initial
behavior and complexity review; its parent serializes native publication.
Follow `heddle/resources/peer-review.briefing.md` for formal review.

## When to Use

Delegate when the task is authorized, a concrete question can be answered
independently, a source-backed brief suffices for the next decision, and saved
time or context justifies coordination. Continue useful local work while the
explorer runs; wait when the next useful step depends on its answer. Work
directly for small reads or material already available.

Use the active tool definitions and configuration for concurrency, history
inheritance, nesting, messaging, and filesystem sharing. Do not assume a private
checkout, a cold start, or a fixed worker count. A capability in one session or
provider need not exist in another. Give each explorer the scope, source
identities, evidence required, and read-only boundary. Reuse a suitable explorer
for follow-up where supported. Formal gates keep their native execution and
cannot be replaced by an explorer's report; their concurrency rule is owned by
the workflow reference.

### Model tier

Default a delegated explorer to the exploration model tier. The read-only
briefs in the Pattern Catalog (Patterns 1–6) are context gathering, and the
cheaper reasoning model produces them at materially lower cost. Reserve the top
tier for work whose product is interpretation rather than retrieval: a
contested design question, a brief that must weigh conflicting contracts, or an
independent cold read of an implementation against its acceptance criteria.
This is a default with an escape, not a prohibition. A badly targeted explorer
that returns a thin brief costs more than it saves once the caller re-derives
it.

Bindings, current as of 2026-09-11:

- Claude: `CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-5` in `.claude/settings.json`
  is the default for sub-agents. An agent definition's `model:` and an explicit
  per-spawn model take precedence over it, so escalating to Fable 5.1 is a
  per-call decision. Do not set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, which
  removes that escape.
- Codex: no repository-level configuration surface exists, so the lead names
  `gpt-5.6-sol` at spawn and escalates to `gpt-6-astra` deliberately.

CLI gate lanes are out of scope. Gate model and effort come from the confirmed feature policy, subject to
host constraints; the exploration lane and the gate lane differ on
purpose. The model names above are dated bindings, re-derived under the
model-generation audit trigger in
`heddle/resources/prompt-authoring-standards.md`.

## Applicability

Scope and uncertainty help identify useful exploration; neither determines a
worker count or an automatic trigger. The phase's required research still happens when the main
agent performs it directly. The patterns below are possible decompositions;
choose the concerns that resolve the actual uncertainty.

## Pattern Catalog

### Pattern 1: Related Spec Lesson Extraction

**Phase:** 1-2 (Research & Specification)
**Prompt:** the canonical `specify.briefing.md` (served by `heddle kickoff`
at `specify`), § Orient and research

**Use when:** Related specs contain independently useful lessons and the
delegation conditions above hold.

**Current flow:** The IDE agent reads related specs, scanning Decision Log,
Surprises, Design Commitments (historical specs: Architecture), and `related`
frontmatter.

**Sub-agent flow:** After the IDE agent identifies relevant specs (primary: `heddle search "<intent>" --titles-only`; failure fallback: scan `docs/features/_descriptions.yaml` and spec filenames; canonical contract: `docs/workflow/document-structure.md` § Knowledge Index), it fans out one sub-agent per spec in parallel.

**Spawn template:**

```
Read the feature spec at {SPEC_PATH} in full.

We are building a new feature: {FEATURE_NAME}.
Problem: {PROBLEM_STATEMENT}
Desired outcome: {DESIRED_OUTCOME}

Extract from this spec only what is relevant to our feature:

1. **Decisions** — Decision Log entries that inform or constrain
   our feature. Include: decision, rationale, date.
2. **Surprises** — Surprises & Discoveries entries with pitfalls,
   edge cases, or non-obvious behaviors that may recur.
3. **Patterns** — Architecture patterns, established conventions,
   and existing modules we could extend or reuse.
4. **Related specs** — from the `related` frontmatter, which
   further specs should we examine and why.

Return a structured summary. Budget: under 1000 tokens.
Omit sections with no relevant content.
```

**Output contract:** Structured markdown, ≤1000 tokens per spec. Four sections; any may be omitted if empty.

**Consumption:** The IDE agent receives source-backed briefs sized for the next decision. It synthesizes them into the Research Summary's Related Work table and Design Considerations.

---

### Pattern 2: Codebase Immersion

**Phase:** 1-2 (Research & Specification) and Phase 5 (Test Scaffolding)
**Prompts:** the canonical `specify.briefing.md` § Orient and research and
`scaffold.briefing.md` § Orientation (served by `heddle kickoff` at `specify`
and `scaffold`)

**Use when:** Independent interface, dependency, or pattern questions can be
answered alongside useful local work. Package count alone is not a trigger.

**Current flow:** The IDE agent reads all affected modules, traces dependencies both directions, identifies integration points with real types and signatures, surfaces implicit contracts, and notes patterns.

**Sub-agent flow:** Fan out by concern (not by code area — concerns produce more coherent output):

| Sub-agent          | Focus                                                         | Budget       |
| ------------------ | ------------------------------------------------------------- | ------------ |
| Integration mapper | Interfaces, integration points, implicit contracts, data flow | ≤2000 tokens |
| Dependency tracer  | Upstream and downstream dependencies, breakage risks          | ≤1500 tokens |
| Pattern cataloger  | Naming, error handling, logging, testing conventions          | ≤1500 tokens |

**Spawn template (integration mapper):**

```
Read the source modules at:
{MODULE_PATHS}

This feature will: {BRIEF_DESCRIPTION}

Trace the integration surface:
1. **Public interfaces** — functions, methods, classes exposed by
   each module. Include signatures with types.
2. **Integration points** — where the new feature will connect to
   existing code. Note actual types and contracts.
3. **Implicit contracts** — behaviors consumers depend on that
   aren't formally documented (e.g., ordering guarantees, None
   handling, side effects).
4. **Data flow** — how data moves through these modules, including
   transformations and validation boundaries.

Return an integration brief. Budget: under 2000 tokens.
```

**Spawn template (dependency tracer):**

```
Starting from these modules:
{MODULE_PATHS}

Trace dependencies in both directions:
1. **Upstream** — what these modules import and depend on. For each
   dependency: what it provides, how tightly coupled.
2. **Downstream** — what imports or uses these modules. For each
   consumer: how it uses the interface, what assumptions it makes.
3. **Breakage risks** — changes to these modules that would break
   consumers. Be specific: "if X changes, Y breaks because Z."

Return a dependency map. Budget: under 1500 tokens.
```

**Spawn template (pattern cataloger):**

```
Read the source code in:
{MODULE_PATHS}

Catalog the conventions and patterns in use:
1. **Naming** — variable, function, class, file naming conventions
2. **Error handling** — how errors are raised, propagated, logged
3. **Logging** — logger initialization, log levels, structured fields
4. **Testing** — test file locations, fixture patterns, mock strategies
5. **Data validation** — where and how inputs are validated

Return a pattern guide. Budget: under 1500 tokens.
Focus on patterns the implementing agent must follow.
```

**Output contract:** Three briefs totaling ≤5000 tokens (vs. 15-30k consumed).

**Consumption:** The IDE agent uses the briefs to ground the Feature Spec's Design Commitments or the plan's Technical Architecture, or to inform test scaffolding design (Phase 5). Read actual contracts before deciding; a brief does not become their owner. In Phase 5, the pattern cataloger is typically replaced by Pattern 3's test-specific survey.

---

### Pattern 3: Test Infrastructure Survey

**Phase:** 5 (Test Scaffolding)
**Prompt:** the canonical `scaffold.briefing.md` (served by `heddle kickoff`
at `scaffold`), § Orientation and § Verification design

**Trigger:** A verification design needs both an infrastructure survey
and related-test analysis, and those independent reads are broad enough to
benefit from separate reports. The survey is required; delegation depends on
the work. Read a compact, familiar test setup directly.

**Current flow:** The IDE agent reads test directories, conftest files, fixtures, then reads related feature tests to extract mock strategies, assertion patterns, and test structure conventions.

**Sub-agent flow:** Two parallel sub-agents:

| Sub-agent               | Focus                                                                   | Budget       |
| ----------------------- | ----------------------------------------------------------------------- | ------------ |
| Infrastructure surveyor | Conftest files, shared fixtures, test utilities, markers                | ≤2000 tokens |
| Related test analyst    | Test files from related features — mock strategies, patterns, structure | ≤2000 tokens |

**Spawn template (infrastructure surveyor):**

```
Survey the test infrastructure relevant to the {AREA} area:

Read these conftest files and test directories:
{CONFTEST_PATHS}
{TEST_DIR_PATHS}

Catalog:
1. **Shared fixtures** — name, what it provides, location, scope
   (session/module/function)
2. **Mock strategies** — for each external service (databases, external APIs,
   LLMs, etc.): how it's mocked, which fixture provides it
3. **Test markers** — custom pytest markers and their purpose
4. **Test utilities** — shared helpers, assertion utilities, data
   builders
5. **Conftest hierarchy** — which conftest provides what, any
   area-specific overrides

Return a test infrastructure inventory. Budget: under 2000 tokens.
```

**Spawn template (related test analyst):**

```
Read the test files from these related features:
{RELATED_TEST_PATHS}

Extract reusable patterns for a new feature in the {AREA} area:
1. **Test structure** — file organization, class vs function tests,
   parametrization patterns
2. **Fixture reuse** — which shared fixtures these tests use and how
3. **Mock strategies** — how external services are mocked, any
   patterns for deterministic test data
4. **Acceptance test patterns** — how integration and acceptance
   tests are structured in this area
5. **Live E2E patterns** — how live tests handle prerequisites,
   idempotency, cost management

Return a test pattern guide. Budget: under 2000 tokens.
```

**Output contract:** Two briefs totaling ≤4000 tokens.

**Consumption:** The IDE agent uses the inventory and pattern guide to create test scaffolding that follows existing conventions rather than reinventing infrastructure.

---

### Pattern 4: Per-Task Exploration

**Phase:** 6 (Milestoned Implementation)
**Prompt:** the canonical `implement.briefing.md` (served by `heddle kickoff`
at `implement`), § Milestone loop — the per-task explore step

**Use when:** A changed interface, unfamiliar area, or unresolved dependency
can be investigated independently while the lead continues useful work.
Choose a bounded question; neither module count nor novelty alone requires an
explorer. For already-established context, work directly.

**Current flow:** Before each task, the IDE agent reads relevant code, traces interfaces, and checks patterns.

**Sub-agent flow:** Single explorer sub-agent per qualifying task.

**Spawn template:**

```
I am implementing this task for {FEATURE_NAME}, milestone {N}:
> {TASK_DESCRIPTION}

Read these files and their immediate callers/consumers:
{FILE_PATHS}

Return an exploration brief. Budget: under 1500 tokens.

1. **Current state** — what each file does now (1-2 sentences each)
2. **Interfaces** — functions and classes I'll interact with, their
   signatures and behavioral contracts
3. **Callers** — who calls these interfaces and what they depend on
4. **Patterns** — naming, error handling, and logging conventions
   in these files
5. **Risks** — what could break if I modify these files, and which
   specific callers to update
```

**Output contract:** Single brief, ≤1500 tokens.

**Consumption:** The IDE agent reads the brief and proceeds to Implement. Use the brief where its evidence suffices; read disputed or missing contracts directly before deciding.

---

### Pattern 5: Working-Frontier Reading Pointers

**Phase:** 6 (Milestoned Implementation)
**Prompt:** the canonical `implement.briefing.md` (served by `heddle kickoff`
at `implement`), § Milestone loop — relevant source reads

**Use when:** A changed working frontier or authorized impact makes the relevant
contract locations unclear, and an independent source map saves useful work.
Document size alone is not a delegation trigger. The governing spec, reviewed
plan and actual interfaces remain authoritative when the lead works directly.

**Direct flow:** Read the canonical ACs and Design Commitments, the plan's
Technical Architecture and current milestone, and relevant actual interfaces.
Record pointers needed for continuation in the native session handoff.

**Sub-agent flow:** A read-only explorer identifies those sources and reports
unresolved conflicts without copying the governing contracts into another home.

**Spawn template:**

```
Read the Feature Spec at {SPEC_PATH}, the Implementation Plan at {PLAN_PATH},
and the actual interfaces relevant to milestones {MILESTONE_IDS} and ACs
{AC_IDS_LIST}. Return source-backed reading pointers with paths, sections and
observed source identities; explain why each changes the next decision.
Identify unresolved contract conflicts or missing evidence. Do not edit files,
restate AC definitions, settle owner decisions, or treat a summary as authority.
```

**Output contract:** A concise source map scoped to the named working frontier,
with explicit uncertainty and conflicts; no derived Design Context block.

**Consumption:** Check the source identities and milestone scope, read disputed
or consequential contracts directly, and record useful pointers in the native
handoff. A frontier shift does not require rewriting the reviewed plan. Genuine
contract corrections use the existing impact and evidence-refresh boundary.

---

### Pattern 6: Propagation Sweep & Knowledge Mining

**Phase:** 9 (Completion & Knowledge Capture)
**Prompt:** the canonical `complete.briefing.md` (served by `heddle kickoff`
at `complete`), areas 1–2

**Trigger:** A completion has enough propagation records and session
history for independent tag and knowledge reports to reduce the lead's work.
Complete a small propagation sweep directly. The completion obligations apply
regardless of how the evidence is gathered.

**Current flow:** The IDE agent reads the full Implementation Plan (15-20k tokens), finds all `[PROPAGATE]` tags, and reads Session Log entries for permanent knowledge.

**Sub-agent flow:** Two parallel sub-agents:

| Sub-agent       | Focus                                                                      | Budget       |
| --------------- | -------------------------------------------------------------------------- | ------------ |
| Tag scanner     | Find all `[PROPAGATE]` and `[PROPAGATED]` tags, return structured manifest | ≤2000 tokens |
| Knowledge miner | Read Session Log, milestone notes, surface permanent knowledge not tagged  | ≤2000 tokens |

**Spawn template (tag scanner):**

```
Read the Implementation Plan at {PLAN_PATH} in full.

Find every [PROPAGATE] and [PROPAGATED] tag across all milestones.

For each tag, record:
1. **Verbatim text** — the full tagged entry
2. **Target section** — what follows the arrow (e.g., Decision Log,
   Surprises & Discoveries, Design Commitments or the historical equivalent)
3. **Status** — "unprocessed" or "propagated on YYYY-MM-DD"
4. **Source** — milestone number and section (Decisions or
   Discoveries)

Return a propagation manifest grouped by target section.
List unprocessed tags first within each group.
```

**Spawn template (knowledge miner):**

```
Read the Implementation Plan at {PLAN_PATH} in full.

Mine for permanent knowledge that was NOT tagged with [PROPAGATE]
but should survive in the Feature Spec. Check:

1. **Session Log entries** — patterns, recurring issues, approach
   changes that reveal design insights
2. **Milestone Notes** — implementation decisions with rationale
   not captured in Decisions sections
3. **Task-level context** — comments in task descriptions revealing
   non-obvious constraints or behaviors
4. **Enforcement extraction** — "enforce later" items that need
   finalization

For each finding, state: the knowledge, which Feature Spec section
it belongs in, and why it is worth preserving.

Budget: under 2000 tokens. Only surface genuinely permanent
knowledge — skip task progress, session state, and environment
details.
```

**Output contract:** Two reports totaling ≤4000 tokens.

**Consumption:** The IDE agent uses the propagation manifest as a checklist (process each unprocessed tag) and the knowledge mining report to add entries the implementation phase missed. Read session and decision facts from `state.yaml` alongside plan prose. Finish this sweep before `heddle feature complete`; accepted history is immutable. Admission freezes the source baseline at the initial source commit. A record commit for workflow files is optional. The runtime provides local retention by archiving the full workspace to `completion.tar.gz`, verifies the ledger and indexed members, and then performs only bounded cleanup. Keep the archive for recovery from interrupted effects. These reports do not authorize deletion or replace the human-owned close.

---

## Spawn Prompt Design Principles

These principles apply across all patterns. Templates show useful report
shapes, not additional workflow gates. Their suggested lengths guide
compression; preserve decisive evidence even when it needs more space.

1. **Self-contained prompts.** Inspect the active tool contract for inherited context; do not assume what a subagent received. Include file paths, feature context, and output format in the prompt itself.

2. **Explicit token budgets.** Use a length target when it helps the caller; preserve material contracts and evidence even when they need more space.

3. **Structured output.** Specify fields the caller actually consumes. Otherwise let the explorer choose a clear result without padding empty sections.

4. **Read-only scope.** Exploration sub-agents never write files. They read code and documents and return analysis.

5. **Parallel when independent.** When a pattern's trigger justifies multiple
   explorers, their independent reads may run concurrently. Formal review uses
   `heddle run-gates` only when readiness projects that closed action;
   revision-pinned state writes stay sequential.

6. **Scope by file paths.** Always specify exact file paths or directories. "Read the codebase" is too broad; "Read `heddle/kernel/model.py` and `heddle/runtime/write_path.py`" is scoped.

7. **Sibling awareness.** When fanning out several sub-agents over one task, tell each what the others are covering and exactly what to report back. This prevents overlapping exploration and keeps the findings composable.

## Synthesizing Fan-Out Findings

After a fan-out returns, synthesize the briefs before acting on them:

- **Contradictions** — where sub-agents disagree, resolve by reading the disputed code directly rather than averaging conflicting claims.
- **Convergence** — agreement is useful corroboration only to the extent the evidence is independent; repeated claims alone do not establish the conclusion.
- **Composition** — assemble the per-agent briefs into the unified picture the next step needs (a codebase map, a hypothesis verdict table). The value of the fan-out is realized here, not in the raw briefs.

## Anti-Patterns

| Anti-Pattern                              | Why It Fails                                          | Instead                                                        |
| ----------------------------------------- | ----------------------------------------------------- | -------------------------------------------------------------- |
| Sub-agent for single-file reads           | Spawn overhead exceeds context savings                | Read the file directly                                         |
| Sub-agent for creative work               | Summaries lose the nuance needed for design decisions | IDE agent reads and reasons directly                           |
| Chaining sub-agents (output of A feeds B) | Sequential dependency eliminates parallelism benefit  | Run both reads in the IDE agent, or combine into one sub-agent |
| Vague scope ("explore the codebase")      | Sub-agent wastes context discovering what to read     | Specify exact file paths and directories                       |
| Re-exploring areas already in context     | Duplicates work the IDE agent already did             | Skip the sub-agent for familiar code                           |
| Top-tier model for a retrieval brief      | Pays interpretation-grade cost for context gathering  | Spawn at the exploration tier (§ Model tier); escalate per call |
| Delegating without independent useful work | Coordination adds cost without resolving uncertainty | Work directly unless a bounded source-backed report helps |
