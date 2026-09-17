# Briefing prompting principles

Heddle-specific application of the seven
[Model prompting principles](../../heddle/resources/model-prompting-principles.md).
That document owns the vendor sources, covered-model list, resolution rule,
and per-model notes. This companion owns their application to packaged stage
briefings in `heddle/resources/`.

Kickoff composes the shared `decision-routing.md` policy with a separately
validated stage body. That composed result is delivered byte-identically as the
phase-session work-prompt head, so the session must
receive the complete operative contract without depending on this design note.

## Principles

1. **The briefing is the operative contract.** Apply "One rule, one home,
   one winner": load-bearing protocol —
   ordering, caps, command vocabulary, escalation routes, artifact shapes —
   lives in the briefing itself. Cited documents (`docs/workflow/*.md`) carry
   rationale, extended patterns, and templates; never split a rule the
   session must obey across a citation boundary and hope the session reads
   the target.
2. **No contradictions, no dead references.** State each operative rule once
   in the briefing section where it binds. For workflow mechanics,
   a briefing never promises behavior the runtime forbids (denied tools,
   boundary-owned writes) and never asks the session to police a mechanism
   the runtime already enforces.
3. **Prescribe contracts, not reasoning.** Apply "Destination, constraints,
   and the why". Fixed process — gate order and
   caps, red-phase rules, decision routing — is stated explicitly and in
   order. Judgment work — how to decompose, what to read, when to look
   deeper — gets goal, boundaries, success criteria, and a motivating clause.
   For example, tests precede implementation so each milestone has a feedback
   loop. Reserve prohibitions for invariants such as runtime-owned state writes.
4. **One autonomy policy, in one place.** Apply "Define done and the
   boundaries, once". The shared decision policy owns classification and question quality; each
   briefing's Authority section owns that stage's actions and boundary-owned writes. Stop conditions are
   enumerated there, not sprinkled through the process text — scattered
   approval language causes unnecessary stops. Within recorded authority the
   session acts and completes independent work while a question is open.
   If a file requires a stop, identify its instruction and the actual
   checkpoint. Do not infer a new permission requirement from a caution.
5. **Show artifact shapes.** Apply "Show the bar". Where an artifact's form
   matters — a decision
   payload, a coverage matrix, a session handoff — name the exact fields or
   show a minimal example. For summaries, preserve outcome, evidence, material
   caveat, and next action; let length follow the content.
6. **Evidence over claims.** Progress statements bind to recorded facts
   (verification runs, gate facts, counts, dates, exact commands).
   Tie workflow records to their real events, such as milestone completion.
   Size tests to the change and repository requirements; repeat or broaden
   them when new evidence warrants it. Any model-specific progress or
   verification cadence belongs in harness configuration.
7. **Simplify on evidence, not on faith.** Apply "Lean, loaded on demand,
   pruned per generation" through the
   [prompt-refinement protocol](../workflow/prompt-refinement.md). Audit
   contradictions, duplicated rules, and old model-specific corrections;
   use plain language and conditions rather than blanket behavioral nudges.
   The briefing corpus tests (`tests/runtime/test_briefing_corpus.py`) check
   content anchors, forbidden tokens, stop patterns, command containment,
   and cross-briefing duplication.

## Applied to the stage briefings

Scaffold and implement briefings cover repository investigation, test design,
prerequisite classification, per-task source inspection, commit readiness,
impact assessment and handoff. Each instruction belongs at the stage where it
applies, names its purpose and required evidence, and leaves reversible
engineering choices to the implementing agent.
