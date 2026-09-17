# Prompt refinement

This protocol turns an accepted instruction intent or a measured failure into
an attributable prompt or analysis-skill change. It is deliberately stricter
than ordinary prose editing: model-facing text is executable behavior, shared
wording has multiple consumers, and a plausible rewrite is not evidence that
the system improved.

The protocol owns refinement records, comparison discipline, and promotion
evidence. It does not authorize a gate, skill, default, topology, cap, stop, or
runtime change. Those remain with the Feature Spec and the owner of the
affected surface.

## When to use

Use this protocol before a behavior-changing edit to a gate prompt, shared
prompt partial, analysis skill, or other instruction that can affect a model's
output. Begin from at least one of:

- an accepted intent or owner decision that names the behavior to preserve;
- an observed failure tied to a fixed case, expected signal, and current
  instruction bytes; or
- a material model-generation change routed here by the
  [packaged model-generation audit trigger](../../heddle/resources/prompt-authoring-standards.md#model-generation-audit-trigger).

A material model-generation change is adoption of a different model generation
or family on an authoring, review, or workflow-session surface, or a provider
change with documented instruction-following, refusal, tool-use, or output
behavior relevant to that surface. An alias or patch rename without a behavior
claim is recorded but does not by itself require the full audit.

Do not use speculative prompt edits to repair a runtime defect, a delivery
failure, an unsupported capability, or evidence that only one model/config
fails while another follows the same instruction. First classify the cause. If
there is no accepted intent and no observed failure, record `NO CHANGE` rather
than manufacturing a mandate.

## Record contract

Create the refinement record before editing a live model-facing surface. The
record is the unit of review and attribution. Its machine-readable form must
remain complete enough for another author to reconstruct what was compared
without relying on chat history.

The mandatory groups are:

| Group | Required evidence |
| --- | --- |
| Identity and authority | Schema, record ID, date, status, at least one accepted-intent or observed-failure ID, promotion authority, and allowed target. |
| Classification and placement | Content role, change intent, local/shared placement mode, exact target and anchors, every consumer, affected families, and a fixed compatibility case for each materially affected family. |
| Baseline and candidate | Repository and runtime revisions, prompt version where applicable, effective-prompt SHA-256, candidate path, and candidate UTF-8 SHA-256. |
| Evaluation | Fixed cases and expected signals, frozen rubric and grader identities/versions, gate, CLI, model, reasoning effort, sandbox, runtime revision, iteration cap, and all relevant configuration. |
| Arm results | The resolved execution tuple; effective-prompt, review-basis, and input identities; expected-signal hits, false positives, output-contract result, iterations, cost, and latency for both arms. |
| Disposition | Attribution, promotion result, named authority, named target, and reason. |

At least one of `source.accepted_intent_ids` or
`source.observed_failure_ids` must be non-empty. An accepted intent authorizes
the goal, not the implementation placement; the target still needs compatible
evidence. An observed failure must point to durable evidence rather than a
summary recalled from conversation.

The identity fields answer different questions:

- `prompt_version` identifies authoring inputs: the raw gate template, each
  referenced partial, and the packaged prompt-authoring standard.
- `effective_prompt_sha256` identifies instructions visible to the reviewer.
- `review_basis_hash` adds the selected execution configuration and
  authoritative reviewed inputs.
- `input_hash` additionally captures iteration and rerun context.

Do not substitute one identity for another. In particular, an edit to the
packaged authoring standard rotates `prompt_version` without changing effective
reviewer instructions or semantic cache identity.

An inert planning record may use the literal `NOT RUN` for its execution,
identity, and arm-result fields only when promotion is `WITHHELD` and the
reason names the missing evidence. A live behavior change cannot use that
exception.

### Pre-edit validity check

Before changing live bytes, validate the complete record, recompute the
candidate SHA-256, re-read every exact anchor, and freeze the cases, rubric,
grader, and execution tuple. A missing mandatory key, stale anchor, unresolved
default, or unpinned comparison axis makes the record ineligible to authorize
an edit. Set promotion to `WITHHELD`, name the gap, and leave the candidate
outside live resolver roots.

## Classify and place the candidate

Classify content before choosing a destination. The classification is about
what the instruction does, not where its source project happened to store it.

- **Role values:** `procedure`, `doctrine`, `reusable-library`
- **Intent values:** `addition`, `removal`, `consolidation`, `relocation`, `wording-correction`

`procedure` is an executable method or decision rule. `doctrine` is
orientation that constrains judgment but should not masquerade as evidence.
`reusable-library` is a menu, base rate, archetype, worked example, or other
reference material that a judge may consult but that should not be baked into
producer instructions as an answer template. This is the accepted
process/doctrine/library classification.

A removal has equal standing with addition. Every pass must look for redundant,
obsolete, conflicting, or low-value text and record why it was removed or
retained. Prefer consolidating or tightening an existing sentence to adding a
new paragraph when both express the same behavior.

Local placement is the default. For a local candidate, record the exact target
and exact anchors, verify each anchor against current bytes, and name the
consumer and fixed case that exercises it. Do not use line numbers alone: they
move without identifying the sentence being changed.

Shared placement is eligible only when the same rule binds every consumer.
Record every consumer, group them into each materially affected family, and
provide at least one fixed compatibility case for each family. If the census
or a fixed compatibility case is missing, keep the candidate local or retain it
inert. A shared destination is not justified merely because wording currently
appears more than once.

Re-verify all anchors immediately before promotion. If an anchor drifted, stop
and revise the record; do not find a nearby sentence and silently treat it as
the approved baseline.

## Run a controlled comparison

Run fresh, isolated arms from the same clean baseline. The baseline arm uses
the current live bytes and the candidate arm applies only the staged candidate.
Only the candidate bytes differ.

For both arms use the same cases, same rubric, same grader, same CLI, same
model, same reasoning effort, same sandbox, same runtime revision, same
iteration cap, and same configuration, including context builders, tool
permissions, prompt caps, rerun state, and input fixtures. Record every value;
"default" is not an identity unless the resolved default is also recorded.

A resolved empty list inside configuration is an explicit setting, such as no
context builders, rather than missing evidence. Record it identically in the
frozen tuple and both arms; missing values and `NOT RUN` remain unresolved.
The evaluation block freezes the planned tuple; each arm records the tuple it
actually resolved so equality can be checked rather than assumed.
Both actual tuples must equal the frozen plan. Matching arms are still invalid
when they drift together from that plan.

For each case, predeclare the expected signal and the unacceptable false
positive. Grade the output contract and evidence quality, not just whether the
desired phrase appeared. Raw finding counts are insufficient: a larger count
may be duplication or false positives, while a smaller count may be a missed
critical issue.

Any mismatched fixed axis makes the comparison invalid for prompt attribution.
Do not normalize the mismatch after seeing results or combine arms produced
from different repository states. Re-run both arms fresh after correcting the
record.

For a shared candidate, the comparison set must include the fixed case for
every affected family. Passing the originating case alone does not establish
compatibility for the other consumers.

## Attribute the result

- **Attribution values:** `prompt`, `model-selection`, `delivery-transport`, `runtime`, `invalid-comparison`, `not-run`

Use `prompt` only when the complete execution tuple is equal and the candidate
bytes explain the observed difference. Use `model-selection` when the same
instruction/configuration is followed under one model choice but not another.
Use `delivery-transport` when the intended bytes were not delivered or tool
transport changed them. Use `runtime` for orchestration, input, caching, or
execution behavior outside the candidate. Use `invalid-comparison` for any
uncontrolled axis, and `not-run` when no behavioral arms executed.

Pre-commit the expected attribution per candidate where possible. A candidate
that teaches an already-present procedure may reveal a model-selection issue;
repeatedly escalating the prompt in that case consumes attention without
repairing the cause.

Trajectory aggregates are a post-promotion watch surface, not causal A/B
evidence. They do not by themselves retain the fixed case, full execution
tuple, rubric, grader, or semantic identities required by this record.

## Promotion decision

Promotion requires sufficient evidence, a named authority, and a named target.
Fail closed when any of the three is absent. Prior authorization of the program
intent does not turn incomplete comparison evidence into permission to edit a
live surface.
Promotion also requires complete executed arm results, `prompt` attribution,
and equality between the frozen tuple and both resolved arm tuples. A `NOT RUN`
result, `not-run` attribution, or synchronized drift is ineligible.

Valid dispositions are:

- **PROMOTE** — the controlled comparison supports the candidate, compatibility
  cases pass, and the named authority permits the named target.
- **NO CHANGE** — the baseline is already adequate or the candidate provides no
  material gain.
- **WITHHELD** — evidence is missing, an axis differs, authority is absent, or
  the target/consumer census is incomplete; retain inert until resolved.
- **REVISE** — change the candidate or placement and run both arms again.
- **REMOVE** — evidence supports deleting or shortening live text; removal uses
  the same comparison and authority bar as addition.

It is also valid to retain inert a useful-looking candidate when it has not
earned promotion. Never claim improvement from an unexecuted or invalid
comparison. Record the reason and the evidence needed for a future pass.

After promotion, run the target's lockstep tests, refresh any exact-byte or
anchor pins, record the `prompt_version` boundary, and watch subsequent cases
for regression. Promotion of one candidate does not authorize adjacent edits.

## Model-generation audit

When model generation changes materially, start with the
[packaged model-generation audit trigger](../../heddle/resources/prompt-authoring-standards.md#model-generation-audit-trigger).
It identifies affected instruction and orchestration assumptions and emits
separately owned candidates. Bring each candidate through this protocol; do not
apply the audit output directly.

The packaged trigger is self-contained because installed hosts do not carry
this checkout document. This companion may link to the package, but the package
must not depend on this companion.

## Record template

Copy this record beside the durable evidence for one refinement pass. Replace
every angle-bracket placeholder. Empty optional result values are represented
explicitly; do not delete mandatory keys.

SHA-256 identities are 64 lowercase hexadecimal characters; `prompt_version`
is the resolved 16-character value or `not-applicable`. Iteration values and
caps are positive integers. An executed arm records `PASS` or `FAIL`, result
lists, and non-negative cost/latency values with units. These shapes make an
unresolved template visibly ineligible rather than merely present.

```yaml
schema: heddle.prompt-refinement/v1
record_id: "<stable-record-id>"
status: planning
date: "YYYY-MM-DD"
source:
  accepted_intent_ids: ["<decision-or-intent-id>"]
  observed_failure_ids: []
classification:
  role: procedure
  intent: consolidation
placement:
  default: local
  mode: local
  target: "<repo-relative-live-target>"
  anchors:
    - path: "<repo-relative-current-file>"
      contains: "<verbatim-current-text>"
  consumers: ["<repo-relative-consumer>"]
  affected_families:
    - id: "<family-id>"
      case_id: "<fixed-case-id>"
      consumers: ["<repo-relative-consumer>"]
baseline:
  repo_revision: "<git-revision>"
  runtime_revision: "<runtime-revision>"
  prompt_version: "<resolved-version-or-not-applicable>"
  effective_prompt_sha256: "<sha256>"
candidate:
  path: "<repo-relative-inert-candidate>"
  sha256: "<utf8-sha256>"
evaluation:
  evidence_sufficient: false
  cases: ["<fixed-case-id>"]
  expected_signals: ["<predeclared-signal-and-false-positive-boundary>"]
  rubric:
    id: "<rubric-id>"
    version: "<rubric-version-or-sha256>"
  grader:
    id: "<grader-id>"
    version: "<grader-version>"
  execution:
    gate: "<gate>"
    cli: "<cli>"
    model: "<model>"
    reasoning_effort: "<effort>"
    sandbox: "<sandbox>"
    runtime_revision: "<runtime-revision>"
    iteration_cap: 0  # replace with a positive integer
    configuration: {"<resolved-key>": "<resolved-value>"}
arms:
  baseline:
    execution:
      cases: ["<fixed-case-id>"]
      rubric: {id: "<rubric-id>", version: "<rubric-version-or-sha256>"}
      grader: {id: "<grader-id>", version: "<grader-version>"}
      gate: "<gate>"
      cli: "<cli>"
      model: "<model>"
      reasoning_effort: "<effort>"
      sandbox: "<sandbox>"
      runtime_revision: "<runtime-revision>"
      iteration_cap: 0  # replace with a positive integer
      configuration: {"<resolved-key>": "<resolved-value>"}
    effective_prompt_sha256: "<sha256>"
    review_basis_hash: "<sha256>"
    input_hash: "<sha256>"
    expected_signal_hits: ["<signal-id>"]
    false_positives: []
    output_contract: "<PASS-or-FAIL>"
    iterations: 0  # replace with a positive integer
    cost: {value: 0.0, unit: "<unit>"}
    latency: {value: 0.0, unit: "<unit>"}
  candidate:
    execution:
      cases: ["<fixed-case-id>"]
      rubric: {id: "<rubric-id>", version: "<rubric-version-or-sha256>"}
      grader: {id: "<grader-id>", version: "<grader-version>"}
      gate: "<gate>"
      cli: "<cli>"
      model: "<model>"
      reasoning_effort: "<effort>"
      sandbox: "<sandbox>"
      runtime_revision: "<runtime-revision>"
      iteration_cap: 0  # replace with a positive integer
      configuration: {"<resolved-key>": "<resolved-value>"}
    effective_prompt_sha256: "<sha256>"
    review_basis_hash: "<sha256>"
    input_hash: "<sha256>"
    expected_signal_hits: ["<signal-id>"]
    false_positives: []
    output_contract: "<PASS-or-FAIL>"
    iterations: 0  # replace with a positive integer
    cost: {value: 0.0, unit: "<unit>"}
    latency: {value: 0.0, unit: "<unit>"}
disposition:
  attribution: not-run
  promotion: WITHHELD
  authority: "<named-authority>"
  target: "<allowed-target>"
  reason: "<evidence-based-disposition>"
```
