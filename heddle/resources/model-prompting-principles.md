# Model prompting principles

Canonical model-guidance reference for authors of Heddle prompts, briefings,
skills, and agent instructions. Models covered:
Claude Fable 5.1, Claude Fable 5, Claude Opus 5, GPT-6 Astra, and GPT-5.6.
The notes describe guidance inputs, not Heddle's configured model selection.

This reference ships beside [prompt-authoring-standards.md](prompt-authoring-standards.md).
The standards own gate output contracts; stage briefings own the instructions
delivered to workflow sessions. This document supplies authoring rationale,
not another runtime policy or a required host-side document.

**Resolution rule.** When model defaults differ, write the shared condition
under which a behavior helps. Keep the model-specific adjustment in harness
configuration or deliberately injected instructions. A vendor recommendation
is evidence for an audit, not authority to change Heddle's gates or defaults.

## Argumentation standard

State the conclusion and the evidence that supports it. Explain the connection
where it affects the decision. Distinguish observation, inference, and
uncertainty. Consider alternatives when they could change the conclusion, and
explain the decisive trade-off. Choose investigative methods and depth according
to the contract, consequence, and unresolved uncertainty. Preserve required
outputs and authorization boundaries.

## Principles

Cite these entries by name; ordering and numbering may change.

1. **Destination, constraints, and the why.** Specify the outcome, scope,
   success criteria, and rationale. Leave judgment about how to reach them
   to the model. Preserve reproducible evidence methods, distinct coverage
   obligations, and ordered steps where dependencies, checkpoints, or
   authorization require them. Detail earns its place by changing a decision
   or preserving a contract; it is not a proxy for thoroughness. Apply the
   argumentation standard above without prescribing internal reasoning.
2. **Conditions, not corrections.** A rule correcting one model's default
   can push another model in the wrong direction. State when to act: use
   lists for parallel or sequential content, ask when different readings
   would materially change the work, and delegate when authorized independent
   work benefits from it. Avoid blanket nudges to do more or less of a behavior.
3. **Define done and the boundaries, once.** Name the complete requested
   deliverable, actions already authorized, decisions that need the user,
   and the verification warranted by the change. Continue independent,
   authorized work while a question is open. Ground progress in session
   evidence and identify failed or skipped checks. Broaden verification when
   a change, failure, or unresolved concern justifies it; scratch checks need
   not become permanent tests.
4. **One rule, one home, one winner.** Keep each rule in its canonical
   authoring source, in normal language, with precedence explicit where rules
   can conflict. Cross-reference rationale; include the operative contract in
   the delivered prompt when its consumer cannot access that source. Preserve
   user instructions over skill guidelines and existing authorization over
   inferred approval requirements. When a file causes a pause, identify the
   exact file and instruction and explain its applicability. A runtime-owned
   transition still goes through its command; prose cannot grant a write the
   runtime forbids.
5. **Lean, loaded on demand, pruned per generation.** Optimize useful
   context, not minimum length. Load relevant references and skills when
   needed; preserve contracts and decisive evidence even when they need detail.
   A generation change triggers an audit of redundant, conflicting, or dated
   instructions, especially corrections to the previous model's defaults.
   Stable obligations do not expire with a model generation. Prefer a relevant
   example, rubric, or test to its prose restatement. Compare representative
   outputs; token reduction alone does not establish better judgment.
6. **Show the bar: examples and named anti-patterns.** A worked example with
   a reason for its shape can convey more than a catalog of prohibitions.
   Describe an unwanted pattern by its cause so the instruction generalizes:
   for example, replace decorative metaphor with the concrete claim it hides.
   Prescribe output fields when a reader or tool consumes them; extra
   templates otherwise invite padding. The number of examples follows the
   ambiguity they resolve, not a universal quota.
7. **Behavior knobs are configuration.** Model, effort, verbosity, tool
   availability, delegation limits, and history handling belong to the harness.
   Model-specific reminders belong in explicitly owned injected blocks,
   rather than shared gate text. Compare effort on fixed cases instead of
   assuming a level name means the same thing across generations. Append
   turn-specific reminders without rewriting earlier conversation turns.

## Model-specific notes

These are dated vendor observations and candidate tuning responses, not shared
workflow invariants or claims of a measured Heddle effect. Verify current
capabilities before applying model-specific advice. Existing prompts remain the
baseline until a change has evidence under the applicable refinement protocol;
task authority and completion requirements still belong in the task contract.

### GPT-6 Astra

The official guide identifies increased sensitivity to skill instructions,
clarification pauses, detailed formatting, less delegation than some workflows
expect, and excessive test breadth for small tasks. Audit competing approval
rules, define completion and verification scope, and state conditional writing
and delegation preferences. Keep these adjustments compatible with the user's
authorization and Heddle's runtime boundaries.

Existing GPT-5.6 prompts carry over. Model adoption is a separate configuration
decision: the guide requires Responses for tool calling, removes several
sampling parameters, and documents effort and cache changes. Check that guide
and runner compatibility when evaluating adoption; a documentation refresh
does not establish transport support.

### Claude Fable 5.1

The Fable 5 guidance remains the baseline. The 5.1 additions concern
non-blocking collaboration, progress visibility, append-only history,
turn-scoped batching reminders, and effort calibration. Audit blanket
anti-formatting rules, unnecessary whole-file rewrites, scope expansion, and
repeated requests for already-granted permission. Harness-provided reminders
should not be copied into repository instructions.

### Claude Fable 5, Claude Opus 5, and GPT-5.6

The source guidance differs on verification and delegation: Fable describes
fresh-context verification for long runs; Opus warns about redundant
self-checking and excessive delegation. Preserve conditional shared guidance
and evaluate any extra verifier through the harness. Heddle's formal reviews
remain native runtime gates; independent runs may be launched concurrently
under the workflow reference's rule.

GPT-5.6's persistence and concision differ from Astra's reported defaults.
Retain the scope boundary and calibrate effort and output length through
configuration; do not carry over a behavioral correction without checking its
effect on the selected model.

## Application in Heddle

- [Prompt authoring standards](prompt-authoring-standards.md) own gate
  mechanics and the model-generation audit trigger. An editorial standards
  change rotates prompt-version attribution; it does not prove a behavioral
  improvement or select a new model.
- Delivered stage briefings apply these principles within Heddle's authority
  and artifact rules.
- `docs/workflow/engineering-principles.md` owns this repo's engineering
  preferences. Host teams author their own profile from the unratified seed.
- Behavior-changing prompt work requires controlled comparisons and promotion
  evidence. Follow the packaged prompt-authoring standard's model-generation
  audit trigger; include both skill mirrors and examine pause language as well
  as output quality.
- `heddle/gate/registry.py` owns resolved gate model and effort policy;
  shared guidance does not override it. The host's delegation policy owns the
  exploration model tier and its dated provider bindings; briefings and skills
  consume that policy rather than restating model names. Provider-specific
  transport and delegation changes require their own implementation and evidence.

## Sources

The primary guides below are evidence sources, not additional standing
instructions. Verify their current guidance before changing model-specific
instructions or configuration.

- [OpenAI: GPT-6 Astra model guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra)
- [OpenAI: GPT-6 Astra model card](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [OpenAI: GPT-5.6 prompting guidance](https://developers.openai.com/api/docs/guides/prompt-guidance-gpt-5p6)
- [Anthropic: prompting Claude Fable 5.1](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1)
- [Anthropic: Fable 5.1 migration guide](https://platform.claude.com/docs/en/models/fable-5-1/migration-guide)
- [Anthropic: prompting Claude Fable 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5)
- [Anthropic: prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)
- [Anthropic: cross-model prompting practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
