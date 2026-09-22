# Prompt authoring standards for CLI gates

Single source of truth for CLI gate prompt conventions: severity taxonomy, AC verification procedure, finding structure, enforcement extraction format, and finding emission rules. Cosmetic, prose, formatting, and naming findings are Minor + IGNORE unless they change implementability or behavior. Each gate prompt in the packaged corpus references this document instead of restating.

## Why this document exists

The model-behavior rationale is in
[Model prompting principles](model-prompting-principles.md); that reference's
header owns the covered-model list. These standards apply the principles to
gate contracts, especially "Destination, constraints, and the why" and
"One rule, one home, one winner". Fixed output fields and protocol dependencies
remain explicit; judgment work needs its outcome and evidence, not a scripted
internal reasoning process.

## Effective prompt contract

Review the instructions the model actually receives: the resolved template,
expanded partials, supplied evidence and runtime manifest, native output schema,
and applicable session instructions. A reference alone does not establish that
its target was loaded. Keep one maintained source for each rule; expand its
operative content when the consumer cannot reliably access that source.

The stage or role owns the outcome, permitted inputs/actions, review dimensions,
and completion bar. The runtime owns resolved inputs, authority, model settings,
and the native output contract. Preserve that separation when simplifying text.
The supplied native JSON schema governs machine representation; Markdown tables
and finding examples in this reference describe information and human views,
not permission to replace the schema or emit an operational verdict a role lacks.

In the current native contract, `decision.recommended` describes the preferred
resolution in ordinary prose; it is not an option-name selector. Preserve the
distinct options and rationale without requiring the recommendation to copy an
option name.

Use the [argumentation standard](model-prompting-principles.md#argumentation-standard)
for conclusions and recommendations. State the responsibility and decision
criteria directly. Preserve every distinct review dimension and required field;
remove repeated role framing, fixed investigation quotas, and instructions to
narrate private reasoning. A reproducible evidence method or dependency order
can remain explicit without scripting judgment.

## Evidence depth and reuse

Coverage follows the contract. Depth follows consequence, uncertainty, and
contract reach. A small diff can affect an important boundary; a large diff does
not by itself justify speculative probes. Preserve required gate and completion
checks. Additional investigation needs a concrete uncertainty or changed risk.

Distinguish inspected source, inspected tests, observed execution, and inference.
Reuse supplied evidence only for the source, contract, environment, and question
it supports. A prior PASS or plan summary is not proof of applicability. Use the
runtime's accepted evidence and freshness result where it owns that decision;
prose cannot refresh a stale fact. Report unavailable evidence and remaining
limits rather than inferring success or repeating checks to signal diligence.

## Severity taxonomy

Applies to all current review roles. The registered role and supplied native
schema own their names and dimensions; historical role names do not schedule
current work.

| Severity      | Definition                                                                                                                       | Examples                                                                                                                                                                                                              |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Critical**  | Blocks merge. User-facing failure risk, structural integrity threatened, or coverage-faking tests.                               | Any MUST AC fails; data loss or corruption risk; security vulnerability; module boundary violation that breaks isolation; tautological test that asserts on mock returns; undeclared cross-module dependency.         |
| **Important** | Must address before next release. Code quality, maintainability, or test gap that doesn't block correctness today but will bite. | Duplication at 3+ sites; unvalidated labeled assumption; missing error path on external dependency; test covers happy path but not AC-specified edge case; naming inconsistent with module patterns; SHOULD AC fails. |
| **Minor**     | Polish / fix-when-adjacent. No behavioral impact.                                                                                | Verbose conditional; low-probability failure mode with clear recovery; single-use magic number; prose quality in spec.                                                                                                |

Reviewers report supported findings at all three severities, each tagged with
confidence (see [Finding structure](#finding-structure)). Preserve original
findings for evidence-bound lead dispositions; do not silently filter another
reviewer's result or manufacture a synthesis authority.

**Minor precedence.** Every Minor finding remains visible as audit information
and creates no independent correction, decision, verification or rerun duty.
Follow the role's Minor/IGNORE classification rule before the general
principle-settled → IMPLEMENT rule. This does not waive required independent
passes, coverage or final proof. Critical/Important findings keep their native
classification and disposition duties.

Severity calibration across dimensions:

- **Correctness**: MUST AC fail → Critical. SHOULD AC fail → Important. Edge case unhandled when not AC-specified → Minor.
- **Architecture**: Boundary violation / undeclared dependency → Critical. Pattern inconsistency / abstraction gap → Important. Single-use abstraction / naming nit → Minor.
- **Robustness**: Data loss / crash / security breach → Critical. Unhandled recoverable error / missing validation → Important. Low-probability failure with clear recovery → Minor.
- **Test quality**: Tautological test / untested MUST AC → Critical. AC edge case untested / test reliability risk → Important. Low-probability edge case untested → Minor.
- **Code quality**: O(n²) in hot path / misleading name / duplication 3+ sites → Critical. Excessive nesting / suboptimal abstraction → Important. Single-use magic number / verbose conditional → Minor.
- **Spec/plan**: Missing AC for MUST behavior / ambiguous AC that forces guessing → Critical. Missing rationale / implicit dependency → Important. Prose-quality issue → Minor.
- **Cosmetic/prose/formatting/naming**: Anchor to Minor severity and IGNORE classification unless the issue changes implementability or behavior.

## Rerun recommendation criterion

Where the supplied document-review schema includes a rerun recommendation,
follow its version-bound field rule. For a current native assignment it is true
for a Critical finding, an Important IMPLEMENT finding, or a Critical document
assessment; otherwise false. Retained material work can keep that advisory true
without granting another invocation. Historical document pipelines retain
their historical interpretation. Required passes, stop decisions and any maximum
come from native confirmed policy; no recommendation grants or waives a call.

## Impact Assessment levels

Used by implementation sessions for Discoveries and `robustness-analysis` for
failure-mode impact. Impact levels describe the blast radius of a discovery,
change or failure, not its severity. A Level 3 discovery with Low likelihood
may still be Important severity; a Level 1 failure with High likelihood may
still be Critical severity. Use both dimensions independently and record both.

- **Level 1 — Localized**: Contained within a single file or function. No external observers, no other modules affected. Fix or mitigation does not require coordinating with other code or stakeholders.
- **Level 2 — Multi-component**: Crosses a module boundary or affects more than one file, but stays within the feature's architecture. Fix requires updating both sides of an interface, multiple implementation files, or coordinating with an adjacent module's maintainer.
- **Level 3 — AC-invalidating / contract-breaking**: Would cause an AC to fail, break a Conceptual Design invariant, or violate a contract another feature depends on. Surface immediately; fix requires spec-level reconciliation or stakeholder input.

When used for finding classification, Level 3 impacts typically map to Critical severity, Level 2 to Important, Level 1 to Minor — but severity and impact are independent axes. A Level 1 failure that happens on every invocation of a hot path is Critical severity; a Level 3 failure that requires three simultaneous rare conditions is Minor severity.

## AC specificity taxonomy

Used by spec-review's acceptance-criteria-quality dimension and implementation
triage. Classify the observable product contract, not the amount of test or code
detail. The criterion and its explicit commitment references must define the
robustness floor ("Simplicity first; the contract sets the robustness floor").

- **Concrete** — identifies the observable action, relevant inputs or conditions,
  expected outcome and consequential failure classes. Explicit references may
  carry shared commitments without copying them into every AC. Example: "On
  a rejected or interrupted publication, the reader still sees the prior accepted
  document and the caller receives a failure; retry follows Design Commitments."
  This need not enumerate test permutations, prescribe internal helpers or name
  implementation exceptions unless those are consumer-facing promises.
- **Partial** — gives a checkable success outcome but leaves a consequential
  failure, ownership or consumer boundary unresolved. Name that exact gap rather
  than demanding more detail in general. Omitted reversible internals alone do
  not make a contract Partial.
- **Vague** — gives no checkable product outcome or discernible failure boundary,
  such as "handles uploads gracefully" without observable acceptance or refusal.

Downstream consequences:

- **`spec-review`** requires all ACs Concrete for PASS at this product-contract
  altitude. Partial ACs are Important for the consequential missing commitment;
  Vague ACs are Critical. Do not demand duplicate EARS requirements, a test-case
  inventory or delegated implementation choices as the remedy.
- **`implementation-agent`** triages AC specificity in Explore step 5 before writing code. Concrete ACs → proceed. Partial ACs → record a Level 2 Discovery naming the specific gap and proceed only for the named inputs/outputs. Vague ACs → stop and surface as Level 3 Re-Plan per the Impact Assessment Protocol.

The taxonomy lives here rather than in either prompt to keep the definitions synchronized across the spec-review gate and the implementation-agent triage step.

## AC verification procedure

Used by current roles with AC status or coverage responsibilities, including
milestone, integrated peer, Behavior and test-scaffolding review. Architecture
roles limit their claim to what their permitted inputs establish.

For each AC ID in spec order, connect its preconditions, action, and expected
result to the implementing paths. Quote the relevant AC text and cite
`file:function:line` with the observable behavior established there. Trace the
named inputs and contract-implied boundaries; choose additional probes for a
concrete uncertainty or failure risk, not a fixed number of examples.

- **PASS:** The implementing paths and applicable evidence establish the
  required behavior on the named inputs, edges, and failure modes.
- **PARTIAL:** The core path is established but a boundary or required evidence
  remains incomplete; name the gap and apply the execution-only rule below.
- **FAIL:** Required implementing behavior is absent or a traced path
  contradicts the AC's expected result.

These meanings apply within each gate's supplied schema. Where a role supports
`untestable`, follow its missing-access rule; do not invent that status for a
role whose schema lacks it. Preserve the runtime's original-source coverage
and disposition authority; no merged table replaces it.

A citation alone is insufficient: explain what it establishes and the remaining
limit so another reviewer can check the conclusion without repeating the whole
investigation. Distinguish source traces, inspected test assertions, and commands
actually executed.

Severity mapping:

- MUST AC FAIL → Critical.
- MUST AC PARTIAL → Critical if any AC-specified input produces wrong output; otherwise Important.
- SHOULD AC FAIL → Important.
- SHOULD AC PARTIAL → Minor or Important, depending on the gap.
- PASS with additional edge-case gaps outside the required behavior → Important or Minor finding, not a status change.

### Execution-only ACs: a recorded result is not PASS evidence

Some ACs are only observable by running something, not by reading code or a diff: "command-pass" ACs (modified files pass flake8/mypy/pytest), "hermeticity" ACs (default paths do not call a live service), and "schema/payload-completeness" ACs (an emitted record carries the full required key set). For these:

- A plan-recorded verification line, a self-declared fake/config flag, or "code exists at the right location" is NOT acceptable PASS evidence — each attests the artifact, not the property.
- A gate that does not (or may not) execute marks such an AC PARTIAL, not PASS, and names the deferred runtime check in Notes (e.g., "command-pass requires executing flake8/mypy; defer to whole-feature verification"). This sense of PARTIAL means "property not yet proven at this gate," distinct from the boundary-gap sense ("an AC-specified input is unhandled").
- A gate marks such an AC PASS only when the property is asserted by evidence the gate can check: a `Verified-by` test that runs the command, monkeypatches the live entry point to raise, or asserts the required key set (a failable property assertion), OR a committed machine-checkable artifact (e.g., a gate-summary JSON with a zero return code).
- This overrides the MUST-AC severity mapping above: a MUST AC backed only by execution-only evidence the gate cannot check is PARTIAL, never PASS, and the PARTIAL is itself the finding (Important — the property is unverified at this gate).

Test-design counterpart: an execution-only AC whose `Verified-by` test only
asserts a recorded result or a self-declared flag, rather than executing the
command, forcing the live path to fail or asserting the full key set, is a
false-green test gap. Flag it (Important) and require the test to assert the
property.

For Phase 5 expected-failure scaffolding, a strict `xfail` counts as red
evidence only when its test body contains an executable non-constant assertion
and fails for the expected discriminator. The packaged hook owns that narrow
mechanical assertion check; the test-scaffolding reviewer owns the semantic
judgment that the assertion represents the AC. A retained marker that becomes
green must surface as XPASS and be removed.

## Finding structure

Every review finding carries the following information, represented by its
supplied native JSON schema. This Markdown example explains a human view; it
is not an alternate wire format:

```
### [ID]: [One-line title]

- **Severity**: Critical / Important / Minor
- **Confidence**: High / Medium / Low — your certainty the finding is real, independent of its severity. Trace evidence is typically High; Speculation is typically Medium or Low. Confidence calibrates downstream ranking; report low-confidence findings, do not drop them. (Distinct from the robustness gates' Likelihood field, which is the probability a failure occurs — not your certainty the finding is real.)
- **Location**: `file:line` (or `[spec-section]:[line-hint]` for doc reviews)
- **Failure mode**: [What goes wrong, in one sentence. State the observable consequence.]
- **Evidence**: [Quote the code, spec clause, or AC definition verbatim. If referencing code behavior, quote the specific lines that produce the behavior — not a paraphrase.]
- **Suggested fix**: [State the required outcome and an evidence-supported mechanism that preserves the applicable commitments. If the mechanism is not established, name the required properties and unresolved conditions; do not present an uncertain remedy as implementation-ready.]
- **Enforceable**: Yes (linter rule / CI check / structural test) / No
- **Classification**: IMPLEMENT
```

The IMPLEMENT / REPORT / IGNORE classification is the canonical taxonomy for every review finding, across every gate. The workflow's decision-classification protocol routes each classification downstream (IMPLEMENT = autonomous fix; REPORT = batched to a user checkpoint; IGNORE = informational only).

Classification is one native enum value, not combined alternatives or prose.
Use the schema's exact spelling and keep rationale in its designated fields.
Current canonical JSON owns the finding; Markdown renderings and historical
text extractors are not alternate current authority.

Apply the delivered Decision routing policy, whose authoring home is
`decision-routing.md`, to determine ownership. This section owns the reviewer
adapter. Except for the Minor override below, a supported in-scope correction
settled by a decisive principle is `IMPLEMENT`; name that principle and evidence
from `docs/workflow/engineering-principles.md`. An unresolved owner choice is
`REPORT`; if ownership remains uncertain after the shared procedure, default to
`REPORT`. `IGNORE` retains awareness without creating implementation work.
Every REPORT preserves the complete useful-question content in its native
`decision`: context, options with trade-offs, recommendation and rationale,
deferral impact, and route. Reviewers never settle the decision themselves.

The corresponding human-view fields illustrate the conditional meaning; the
schema owns their native representation:

```markdown
- **Recommended**: <the recommended resolution in one imperative line>
- **Route**: <the single repo-relative file where the recommended ruling would land>
- **Deciding principle**: <principle name> — <one clause on why it settles this>
```

REPORT carries the required native decision object; other classifications do
not. A principle-settled IMPLEMENT finding names the decisive principle and why
it resolves the engineering choice. Where supported, a route names the one
repository-relative file where a ruling lands; omit it when no single file
applies. Apply Minor precedence before this general routing rule. Keep the
original owner identity when a REPORT is pending or settled; do not duplicate it.

### Finding IDs

IDs use the gate prefix + severity initial + counter:

| Gate                              | Prefix |
| --------------------------------- | ------ |
| Milestone review                  | `MR`   |
| Peer review (sequential)          | `PR`   |
| Behavior review                   | `BR`   |
| Complexity review                 | `CX`   |
| Spec review                       | `SP`   |
| Plan review                       | `PL`   |
| Robustness analysis               | `RA`   |
| Test scaffolding review           | `TS`   |

Example: `MR-C1` is a milestone-review Critical finding. Historical reports
retain their original prefixes and IDs; never rewrite them to current role
names. Runtime-owned decision IDs and original run/finding references remain
distinct from new finding IDs. Use the supplied schema for observations,
contradictions and prior dispositions.

### Trace vs speculation

Every finding states evidence category:

- **Trace**: The reviewer read the code and confirmed the behavior by following execution. Evidence quotes the specific lines.
- **Speculation**: The reviewer reasoned about the code without fully tracing it.
  Evidence explains the inference and its limits so the lead can route necessary
  verification. Do not present it as an observed trace.

## Enforcement extraction format

Roles with enforcement suggestions emit the schema's native records, preserving
the following information where applicable. This table describes the human view:

| Pattern        | Rule Type                                                  | Enforces                                        | Draft Message                                     | Priority                          |
| -------------- | ---------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------- | --------------------------------- |
| [Pattern name] | linter rule / CI check / structural test / pre-commit hook | [What violation the rule catches, one sentence] | [Agent-facing error message — see template below] | Blocking / Recommended / Optional |

Agent-facing error messages follow this template so the message lands actionably in a future agent's context:

```
[Rule name]: [what was violated, one sentence]. Fix: [concrete action]. Why: [one-sentence rationale — typically a prior incident or a load-bearing invariant].
```

Example:

```
module boundary: file X imports from Y directly. Fix: import from the public interface at Z. Why: direct imports defeat the boundary that keeps the two subsystems independently testable.
```

## Review finding emission rules

All review gates follow these rules unless the gate explicitly overrides:

1. **Report findings without silently filtering originals.** Report in-scope
   issues at every severity, with confidence and the supporting trace or labeled
   inference. The lead records their dispositions under native authority.
   Low severity or uncertainty alone does not justify omission; speculation
   does not become observed fact. Follow the supplied round purpose.
2. **Quote, don't paraphrase**. When citing the spec, AC, or code, copy the text verbatim. Paraphrase is acceptable only for cross-reference (e.g., "see AC-3 for the full definition").
3. **Trace, don't speculate**. For each finding, label the evidence category (Trace / Speculation) per the definitions in [Finding structure](#finding-structure) above.
4. **Ground every finding in a file:line or spec section**. Findings without a location are classified as IGNORE (awareness-only).
5. **Finding ≠ improvement**. A finding names something that is wrong per a Severity definition. Improvements (e.g., "this could be clearer") are classified as IGNORE unless they meet a Severity threshold.
6. **Scope discipline**. When a role explicitly excludes an issue, retain useful
   awareness with a pointer to the appropriate owner under that role's schema.
   Do not implement it, reopen scope or schedule another role yourself.

These rules govern discovery within the supplied assignment. Verification reruns
follow [Disposition rerun](#disposition-rerun); an explicitly independent pass
retains its supplied discovery boundary. Do not infer round purpose from a
historical run count or another reviewer's output.

## Disposition rerun

Exception: when a native `resume-incomplete-review` decision explicitly supplies
an initial code-quality completion context, finish that missing initial assessment
over the full supplied scope. The retained six-unavailable, zero-finding responses
remain history and spent allowance; they are not substantive first passes. Follow
the supplied initial-completion context, without fabricated regressions. This
exception supplies neither a new budget nor authority for partial-review reopening.

A supplied verification rerun is a **disposition pass, not fresh discovery**.
Use the runtime's original-source context, targeted concerns, adjudicated
decisions and pending decisions. A historical Disposition Rerun Ledger serves
its recorded version; neither it nor a new instruction changes native policy.
Within the supplied verification scope:

1. **Verify dispositions.** For each prior IMPLEMENT finding, confirm the current reviewed content addresses it; report any that is not addressed, citing the prior finding ID.
2. **Catch regressions.** Report defects the intervening fixes introduced, inside the content changed since the prior review (the manifest diff).
3. **Honor settled ground.** A resolved decision is adjudicated: do not re-raise it, reword it, or derive variants of it. When a resolution documents an accepted risk as a labeled assumption or operational restriction, the absence of enforcement for it is **not a finding** — the governing record leads.
4. **Never duplicate pending decisions.** They are already awaiting a ruling.

Use the native prior_dispositions and regressions fields with exact original
run/finding references. An addressed or settled claim needs affirmative evidence.
Retained findings keep their origin; a new regression requires observed
before/after change evidence, not merely a currently visible defect. Other
current gaps remain explicit in coverage or limitations without a fabricated
regression or a claim they are resolved. Verification scope does not suppress
an evidenced new violation.

The confirmed policy owns required passes, independent contexts, stop decisions
and any maximum. No universal four/five-run limit, automatic reset, or synthesis
allowance is supplied by this document. Independent initial contexts remain
independent; the lead reads their required results before remediation. A clean
later report does not close an older concern by omission.

## AC verification output table

Roles that verify ACs emit their supplied native coverage/status records for
every assigned AC. The following columns explain the common human view; they
do not replace role-specific native fields or permit unsupported statuses.

| AC ID | Status                | Coverage                 | Evidence                         | Notes                                          |
| ----- | --------------------- | ------------------------ | -------------------------------- | ---------------------------------------------- |
| AC-1  | PASS / PARTIAL / FAIL | Full / Partial / Missing | `file:line` + one-sentence trace | [Any caveat, edge case, or test-coverage note] |

- **Status** describes the behavior established within the role's input boundary.
- **Coverage** describes the test/proof support that role can establish.
- **Evidence** is trace-grade: `file:line` + what the code does.
- **Notes** captures edge cases, related findings, or cross-gate references.

Preserve every required original coverage record. Conflicting assessments stay
explicit for evidence-bound lead resolution; no role's clean result silently
overrides another source. A passing implementation claim with missing required
proof remains an open coverage duty, not an accepted `PASS (untested)` shortcut.
The runtime qualifies closure from original sources and recorded dispositions;
a human-view table or manually consolidated finding list cannot authorize it.

## Code-quality checklist

These defaults complement the existing code-quality dimensions and repository
conventions; they do not change finding severity or authorized scope.

- Log decisions and state transitions at boundaries, not noise inside helpers,
  so a failure can be explained without obscuring the useful signal.
- Meet performance budgets by construction, then assert them as tripwires;
  avoid speculative micro-optimization without a budget or observed need.

## Persona calibration vs procedural instruction

Name the responsibility and decision standard. A code-quality dimension proposes
behavior-preserving improvements; an architecture dimension assesses owners
and dependencies within its input boundary. Keep persona framing only when it adds a distinct,
useful criterion. It carries no authority and needs no fixed behavior count.

Preserve steps required by dependencies, evidence methods, and authorization.
Remove steps that merely simulate expertise, require self-assessed diligence,
or repeat the rubric. Examples clarify difficult judgments or required output;
their number follows that need.

## Relationship to other documents

- **`AGENTS.md`**: repository entry and mechanical bindings.
  Read the applicable instructions for interactive work. Stage briefings own
  delivered workflow instructions; referenced host preferences are supplied by
  their resolved paths. Shared gate text must not assume checkout-only files
  are present on an installed host.

- **The workflow process documentation** (phases, gates, orchestration): this document does not duplicate workflow content; it specifies the content of prompts that the workflow invokes.
- **The document-structure templates** (Feature Specs and Implementation Plans): this document applies only to gate prompts.

## Model-generation audit trigger

A material model-generation change is adoption of a different model generation
or family on an authoring, review, or workflow-session surface, or a provider
change with documented instruction-following, refusal, tool-use, or output
behavior relevant to that surface. An alias or patch rename without a behavior
claim is recorded but does not by itself require the full audit.

When that material change occurs, audit all of these model-facing assumptions
before adapting instructions:

- prompt wording;
- analysis skills;
- model and reasoning-effort defaults;
- gate topology;
- iteration and stop caps; and
- orchestration assumptions.

Apply "Lean, loaded on demand, pruned per generation" from the
[model guidance reference](model-prompting-principles.md): include both skill
mirrors and inspect conflicting permission language, unnecessary pauses,
verification breadth, and model-specific corrections. Name an actual runtime
checkpoint when it requires a stop; a vague caution is not a new checkpoint.

The audit produces separately owned proposals; it does not authorize a live
rewrite. Stage every candidate inertly, pin its baseline and candidate
identities, name its owner, target, consumers, and compatibility cases, and
require controlled same-case and same-configuration evidence before promotion.

A generation audit must not silently change gates, defaults, stops, caps,
skills, prompts, runtime behavior, or runtime ownership. Route each proposed
change through its own applicable workflow and keep existing behavior in force
until that proposal is accepted, verified, and deliberately promoted.

## Change protocol

Changes to this document change the contract for every gate prompt. Before editing:

1. Identify every gate prompt that references the section being changed.
2. Verify the proposed change does not break those prompts (e.g., severity definition changes must stay consistent with how each gate applies the taxonomy).
3. If the change is load-bearing, open a prompt-optimization cycle: update this document, then update referenced prompts in the same commit.

**Measured-gap rule for prompt additions and removals.** An instruction needs
a named purpose: a task or authorization contract, a consumed output field, or
a demonstrated failure. Prefer a decision rule and examples that distinguish
the difficult cases. Remove redundant lore and obsolete model corrections;
retain domain invariants and necessary evidence methods even when they are
long. Move detail to a reference only when the consumer reliably loads it.

Validate delivered prompts, native output contracts, routing, skill mirrors,
and affected links. Compatibility checks establish compatibility, not model
quality. For behavior changes, retain the controlled comparisons and explicit
promotion required by the audit trigger. Evaluate finding recall, unsupported
claims, decision routing, convergence, and observed cost/latency separately.
Word counts describe instruction size; they do not prove effectiveness.

**Editing this file moves every gate's `prompt_version`.** The version hash
includes this document alongside each template and its partials, including for
a citation-only edit. Effective instructions and semantic input identity are
separate: a version rotation alone is not evidence of changed reviewer behavior
or permission to rerun a gate. Record the changed sections, actual consumers,
delivered instruction differences and attributable comparisons. Batch cosmetic
edits with the authorized prompt change or record their measurement-only
boundary below; never infer quality from an aggregate bucket split.

Record cosmetic edits as measurement-only prompt-version rotations. A rotation
without delivered instruction differences does not establish a contract or
quality change.
