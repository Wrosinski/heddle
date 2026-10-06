# Changelog

All notable changes to Heddle are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor
version can change command and payload contracts; `heddle help --json` is the
authority for the installed version.

## [Unreleased]

### Changed

- Heddle describes itself as a harness for LLM coding workflows: you define
  what a feature must do and how it will be proven, and the agent builds it
  through a formalized, verifiable process. The README leads with that split
  and a "Why Heddle?" section, and its process summary adds the scaffold step
  that turns acceptance criteria into tests before implementation. The Spec Kit
  comparison adds a test-design row. The package description, architecture
  overview and canonical GitHub About text and topics follow; `llm-workflow`
  and `harness-engineering` replace the `state-machine` topic.
- The `heddle help` banner reads "heddle — LLM coding workflow harness".
- A feature's integrated witness now runs before review. Once every milestone
  is done, `heddle phase-exit` from implement refuses until `acceptance` and,
  when `live_e2e_test` is declared, `live` pass on current content. Features in
  flight meet this at their next exit from implement; one whose live grant
  names only the final boundary needs that grant reopened first.
  An implement stage with no milestones owes the same lanes.
- After review, rerunning a witness lane that review changes made stale stays
  the default at the final boundary, and readiness says the owner may waive it.
  While review work at that boundary is still open, readiness lists that rerun
  after the review actions, so automated flow reruns once the reviews settle.
- Review slots that read none of each other's findings now launch together at
  every stage, not only the initial Full peer-review pair. When readiness would
  route two or more such slots, such as a review's primary and secondary or
  distinct reviews due together, it emits one `heddle run-gates` action that
  runs them concurrently and records each result in order. A recorded `fail`
  or `pass_with_conditions` verdict completes its member, and every member
  keeps its own remedy. The new `.heddle.yaml` setting `reviews.launch`
  defaults to `concurrent`; set it to `sequential` to launch one slot at a
  time.

### Added

- A concept note precedes the Feature Spec. After research, the brief's new
  `## Concept` section answers three questions at the altitude of concepts and
  high-level contracts: what we are building, how (approach and the
  alternative rejected, flow with a worked example, contracts new, changed or
  relied on unchanged, state ownership including existing records, and the
  footprint of codebase areas it will touch), and how we will know it works.
  The owner approves it at the research checkpoint. An independent concept
  review is offered and runs only on the owner's opt-in, by default GPT-6
  Astra at `xhigh` invoked directly through the Codex CLI rather than as a
  native gate. The spec retains the approved "How are we building it?" answer
  verbatim under `### Approved concept` with a labeled concept delta; spec
  review checks the spec against it and traces each acceptance criterion to
  it, and plan review flags an approach or owned path that departs from it.
  Specs or plans drafted before this need no retroactive concept.
- A specification checkpoint closes specify. Before any review, the lead gives
  the owner a short overview of what was specified, how, and how it will be
  proven, with a scope check and a footprint-and-complexity check, and records
  a class-8 `Specification checkpoint` question (plus the class-5 live question
  when live applies) that holds the feature until the owner approves. Both are
  approval only; revisions happen while they stay pending. The scope,
  concept-delta, e2e and live rulings move here from Checkpoint 1, and the
  spec-review lead checks the cited rulings before the first gate run. A
  driven session without an approved concept drafts against it and presents
  it at this checkpoint.
- Widening a milestone's ownership outside the approved footprint during
  implementation, review fixes or completion now needs a class-2 owner
  question first.
- The review change confirmation replaces Checkpoint 1. At the exit of spec
  review and of plan review, the lead asks one approval-only question about
  changes that would alter the owner's specification-checkpoint answer, or
  nothing; an addition a review proposed defaults to a named deferred
  follow-up. A spec without a cited ruling gets the green light at the start of
  spec review instead of the former full Checkpoint 1 batch. The shared
  decision policy defines the approval of prepared work as a question type,
  which both checkpoints use, and the spec-review and plan-review briefings
  gain Authority sections. The workflow guide's Checkpoint 2 is now the
  peer-review decision batch.
- Both owner checkpoints are opt-out. Setting `checkpoints.specification` or
  `checkpoints.review_changes` to `false` in `.heddle.yaml` turns one off for
  every feature, and `status` and `orient` report both host switches for an
  admitted feature; the owner can also skip either for one feature at the
  research checkpoint, which then asks for the e2e grant when the specification
  checkpoint will not run. Scope changes, concept deltas, footprint widening and
  execution grants still go to the owner.
- A class-5 `witness-waiver` decision, resolved with `accept-prior-witness`,
  lets the owner waive one lane's post-review rerun. It binds the latest stable
  passing fact and the current source, so a later relevant edit or a new run
  makes it inert. Failed, missing, unstable and command-stale evidence is never
  waivable, and the waiver never satisfies implement exit or review-disposition
  evidence. Completion records waived lanes in
  `completion.waived_witness_decision_ids`, and the close audit expects a
  matching decision-journal entry.

## [0.2.0] - 2026-09-29

Review gates gather their own evidence, apply shared boundary-and-proof
principles and default to newer models. Completion separates engineering proof
from judgment-based assessment. Two record rules change for features in
flight: plan citations hash only the plan's authored bytes, and an accepted
feature's raw evidence is local to the checkout that accepted it. The
plan-citation entry under Changed and the plan-review basis entry under Fixed
say what to re-dispose, reaffirm or re-attribute in a feature in flight.

### Added

- Release process documentation covering qualification, tagging, the GitHub
  Release, and the repository metadata on GitHub.
- `milestone edit` can re-point a done milestone's verification command from a
  test selector that no longer resolves to one that resolves in the same file,
  as after an upstream test rename. Every other command token and the expected
  text must stay unchanged; any other rewrite still needs an ownership expansion.

### Changed

- A model mistake observed in a paid or live run is treated as a runtime
  defect with a hermetic proof. A check on model-supplied input returns a
  recoverable rejection naming the field, the value received and the required
  value or admissible set; each such failure gets a committed replay case at
  the seam where the model met it. The testing strategy, robustness and
  complete guidance state the rule, and the testing strategy lets a provider
  schema probe run as soon as a schema changes, under paid-run authority.
- Engineering proof and judgment-based assessment are separate. Completion
  rests on tests, verification facts, declared live runs and an alignment check
  whose criteria are observable contract conditions. A target that needs
  subjective or expert judgment, such as analytical quality or an assessor's
  grade, goes in a new optional `## Assessment Targets` spec section with its
  owning follow-up and never gates completion. Specify, spec review, plan
  review, test-scaffolding review, behavior review, peer review, robustness and
  complete guidance drop the rule that explicit quality thresholds bind;
  deterministically measurable thresholds remain ordinary acceptance criteria.
- The assessment-target rule is tightened after review. Spec review and plan
  review rate a criterion that needs judgment as Important: IMPLEMENT when
  moving it to Assessment Targets preserves recorded owner intent, REPORT when
  an owner ruling placed it in the contract. Complete guidance lists every open
  assessment target in spec Outcomes and brings each to the final checkpoint
  with a greenlight / hold / drop recommendation for its owning follow-up,
  whether or not the feature declared an alignment check. The testing strategy
  adds a tie-breaker (if competent inspectors could reasonably disagree on pass
  or fail, it is an assessment target, after first restating the criterion in
  observable terms where its intent allows) and states that moving a judged
  criterion out of an accepted spec needs an explicit owner ruling. The final
  checkpoint records the user's call on each deferred follow-up and open
  assessment target in spec Outcomes before acceptance. The brief
  scaffold, brainstorming guidelines and scaffold briefing name judgment-based
  aims separately and keep alignment criteria observable. "Never blocks or
  qualifies completion" becomes "never blocks completion or adds a condition to
  it", so the wording no longer overloads Heddle's qualification term.
- Claude gate lanes, and the recommended Claude reviewer for peer and behavior
  review, default to `claude-opus-5-5` instead of `claude-opus-5`, still at
  `xhigh` effort. Confirmed feature policies keep the model they recorded.
- Codex Sol gate lanes, the Codex provider fallback, and the recommended Codex
  reviewer for plan, test-scaffolding, milestone, complexity and robustness
  review default to `gpt-6-sol` instead of `gpt-5.6-sol`, at unchanged effort.
  Spec review stays on `gpt-6-astra`. Confirmed feature policies keep the model
  they recorded.
- A `run-gate` refusal for a CLI outside the current round now names the role,
  the round and its slots. When the requested CLI is the policy's secondary
  reviewer in a later round, it says the secondary joins round 1 only and later
  rounds run the primary alone. The policy contract (`feature policy --help`),
  the peer-review briefing and the workflow model state the same rule. The
  behavior is unchanged.
- Disposition reruns state the reviewer-native prior-disposition rules in the
  rerun ledger and in `prompt-authoring-standards.md`: the status each original
  classification takes, whatever status the lead recorded; the evidence kinds an
  addressed row or a regression needs; and that a retained finding is never
  also a regression. Which review content validates is unchanged.
- A stale outside-feature attribution now names only the attributed paths that
  changed, counted against every attributed path (`1 of 2 attributed paths
  changed: outside.txt`), and lists them in the error details. Its hint gives
  both remedies: add the paths to a milestone with `owns_append` when this
  feature made the change, or record a new attribution batch naming exactly
  those paths. Changed evidence names the changed evidence files and the
  attributed paths that cite them. What refuses is unchanged.
- A review whose only open concerns are addressed or settled dispositions with
  changed cited evidence now routes to a fresh disposition instead of a
  mandatory round-limit, no-progress or no-decrease stop. Editing a cited file
  no longer sends the lead to record a stop for findings that were resolved.
  Any genuinely open concern still routes to the stop, and the recorded stop
  reason is unchanged.
- Disposition and scope-change references that cite the feature plan, and
  source-attribution references that cite it, now hash only the plan's
  authored bytes: its well-formed rendered plan-status region is excluded, as
  gate inputs and review assignment bases already exclude it. `heddle sync` no
  longer reopens those dispositions or makes those attributions stale. Any
  other cited file, and a plan whose markers are malformed, is still hashed as
  it stands. This reverses the earlier rule that an explicit plan reference
  stays raw, and it is a clean break with no compatibility path: in a feature
  in flight, a disposition or attribution that cited the plan before the
  upgrade reads as changed once. Re-dispose those rows before reaffirming the
  rest of their round, because reaffirm refuses a batch with a changed
  citation, and re-attribute stale attributions. Scope-change references are
  only compared when a scope change repeats. Accepted reviews keep their
  recorded identity.
- Every review gate applies two general principles from a shared
  `boundary-and-proof` partial: account for every state that crosses a
  boundary (partial, interrupted, repeated, concurrent and late states, every
  failure a producer emits and every input form, each with a defined outcome),
  and proof must be able to fail for the real defect. They replace milestone
  review's narrower interruption-state sentence for durable lifecycles. The
  specify, scaffold and implement briefings point authors to the same
  principles. The severity calibration rates a reachable state the acceptance
  criteria do not name by its consequence, as robustness findings are rated,
  instead of anchoring it to Minor; the missing criterion bears only on the
  IMPLEMENT or REPORT classification. Every gate's `prompt_version` changes.
- The prompt-refinement protocol has an owner-directed promotion route. The
  owner may put a named behavior change live without a controlled comparison.
  Its commit carries a prompt-change record with the decision, consumers and
  affected families, the delivered difference, `Comparison: none run` and a
  watch case for each family. The change claims no improvement; watch outcomes
  are reviewed in the next gate-effectiveness entries, and a miss opens an
  ordinary refinement record.
- Once a feature is accepted, its raw captures, review, verification and
  close-suite logs, and its completion archive are local to the checkout that
  accepted it. A verified archive accounts for such files missing from the
  workspace. Without an archive, their absence, or the absence of a disposable
  review input (a derived view, or temporary output with no recorded mode),
  makes the `retained_evidence` report and the archive and cleanup effects
  `not-local`: `status`, `orient`, `kickoff` and `feature complete` exit 0,
  publish no archive, and emit the informational
  `completion-evidence-not-local` diagnostic, whose hint applies only to the
  checkout that accepted the feature. The archive effect lists absent disposable
  inputs in `absent_disposable`. With every input present and unchanged, a
  missing archive stays pending and a retry builds it. Report rows gain `local` (`present` or
  `absent`), and absent rows omit kind, SHA-256 and mode. `validate` and
  `doctor` accept an absent raw capture, doctor counts only the gate artifacts
  it checked, and the informational `historical-logs-optional` diagnostic is
  renamed `historical-evidence-optional` and also counts raw captures and review
  logs. A present capture or review log that differs from its recorded digest, a
  present file that differs from its verified archive member, a missing ledger,
  canonical review or verification manifest, and an archive that lacks an absent
  file are still conflicts. The `feature complete` call that records acceptance
  still requires every retained file and archive input.
- Every reviewer may run commands to gather evidence and must not modify
  files, records or repository state. Claude Code gates get Read, Grep, Glob and
  Bash under `--permission-mode auto`, with the read tools pre-approved, and
  inherit the operator's Claude settings. A Claude reviewer's commands time out
  after 600 s by default and 900 s at most, and each attempt is capped at 500
  turns and 50 USD. The recorded Claude sandbox is `auto`; historical
  `read-only-tools` records still read, and every Claude review basis changes.
  Gate runs of either CLI end after 1200 s without stream activity, up from
  900 s; `verify` keeps 900 s. Driver phase sessions, which run
  `heddle run-gate` through their own Bash tool, now give a Bash call 3000 s
  by default and 3600 s at most, and end after 3600 s instead of 2700 s, so a
  gate attempt that uses its whole 2700 s still completes inside them. Review
  prompts drop their read-only statements, and the shared test-execution scope
  states the command posture and asks reviewers to leave shared state as they
  found it, since another reviewer may run in the same working tree.

### Fixed

- A `settled` or `awaiting_decision` prior disposition on an IMPLEMENT finding
  or coverage target was refused with a REPORT-decision error. Prior-disposition
  refusals now name the finding, the statuses its classification allows and the
  status used; evidence and regression refusals name the finding and the
  evidence kinds accepted.
- A feature whose reviews spanned more than 48 hours could not complete: each
  gate run deleted provider scratch directories older than 48 hours, including
  ones the state still indexed, and `feature complete` then refused because
  those indexed files were missing. The stale-scratch sweep now keeps indexed
  scratch. Completion records indexed provider scratch that is already gone as
  absent in the archive manifest (`heddle.completion-archive/v2`) and reports
  each path as a `completion-absent-scratch` advisory. Every other indexed input
  must still be present, and an accepted retry still fails when a cleaned file
  is missing from the archive. Archives written in the v1 format stay readable.
- When another command wrote the workflow state while a single `run-gate`
  review ran, recording refused with a hint that read as a paid rerun.
  `run-gate` now records the completed output itself when its inputs are
  unchanged, without another provider call, and adds the advisory
  `review-recorded-after-state-change`; changed inputs still refuse. The
  revision-drift refusal names the cause `state-revision-changed` in its
  details, and its hint says a rerun of the same command records the saved
  output.
- The `owned-path-deleted` advisory told the lead to drop the deleted path from
  the milestone's `owns` list at feature close, which a done milestone's
  append-only ownership refuses. It now says no action is needed, because the
  entry records sanctioned deletion work.
- A `heddle sync` after dispositions were recorded reopened every row of an
  unaccepted plan-review or test-scaffolding round, including rows that never
  cited `plan.md`: the assignment basis hashed the plan with its rendered
  plan-status region, which gate inputs already excluded. The basis now
  excludes the same well-formed region; an authored plan change, or a change
  to a plan with malformed markers, still reopens the round. In a feature in
  flight, a basis recorded before the upgrade reads as changed once; reaffirm
  the round.
- An accepted feature whose workspace records Git had checked out reported a
  completion archive conflict (`retained evidence mode differs`) in `status`,
  `orient`, `kickoff` and `feature complete`, and the reported repair and retry
  could not clear it: Heddle writes records at mode 0600 and Git restores only
  the executable bit. Retained evidence, the authored workspace records and
  their archive members now match on bytes, kind and the executable bit, both
  at acceptance and afterwards. A changed executable bit is still a conflict,
  and a cleanup candidate still needs its exact recorded mode before deletion.
- In a checkout other than the one that accepted a feature, `heddle validate`
  failed with `workspace-invalid` when a raw capture was absent, and `status`,
  `orient` and `kickoff` reported an archive conflict whose repair and retry
  could never succeed when the archive and some of its inputs, such as
  gitignored logs or review files that cleanup removed, existed only in the
  accepting checkout. See the `not-local` entry under Changed.
- `heddle feature complete --dry-run` that lost the acceptance race to another
  process read the winner's effects as a real run and could publish the
  completion archive and run cleanup. It now stays a dry run.

## [0.1.0] - 2026-09-22

First tagged release. The core runtime was implemented and tested before the
repository was published on 2026-09-17 as version 0.0.1. Everything since has
come from using Heddle to build Heddle: each feature implemented through the
workflow produced feedback and fixes, and those are the changes between the
first commit and this tag.

### The core, as published

- **The runtime.** A local state machine for one feature at a time: admission
  through `heddle feature prepare`, `heddle feature policy` and
  `heddle feature start`; stages from specify to complete advanced one legal
  step at a time by `heddle phase-exit`; milestones, tasks, decisions and
  session notes recorded natively; `heddle orient` and `heddle status` deriving
  the next legal action from recorded facts; `heddle kickoff` rendering the
  stage briefing; `heddle drive` executing next actions until a decision, a
  blocker or the human handoff.
- **Verification bound to content.** `heddle verify` runs a stored command and
  records the exit code, the log and a content manifest of the owned sources.
  Missing, failed, command-stale, source-set-stale and content-stale evidence
  is reported separately and blocks the boundaries that require it.
- **Review gates with retained findings.** Eight review roles run through
  `heddle run-gate` and the concurrent `heddle run-gates` pair using Codex or
  Claude Code as read-only reviewers under a confirmed per-role policy. Original
  findings stay attached to their review; the lead records evidence-bound
  dispositions, reaffirmations, capture-bound interpretations and allowance
  changes through native commands.

### Added since publication

- **Existing-host adoption.** `heddle init --adopt-existing` records
  host-authored configuration and engineering principles in the adoption lock
  without overwriting them.
- **Completion feedback contracts.** `heddle feature complete` reports
  acceptance, retention, cleanup and pending-effect outcomes as typed results
  with an exact recovery action, and retains the accepted ledger and authored
  records in a local completion archive.
- **Proof continuity.** Milestone verification declarations and dependency
  reporting, review follow-ups on declared proof, and installed-witness
  guidance keep required proof pending and visible instead of silently waived.
- **Source attribution.** `heddle feature sources attribute` records
  evidence-bound attribution of exact outside-feature changes so a long-lived
  feature can reconcile source coverage without claiming ownership.
- **Integrated witness planning.** Specify proposes the complete flow with its
  e2e and live lanes, the post-spec-review checkpoint records execution grants,
  and the final boundary records an alignment assessment.
- **Shared decision routing.** One decision-routing policy is delivered through
  kickoff and every review gate, bound to prompt identities and overridable per
  host.
- **Supervised implementation adapters.** Packaged skills and agent definitions
  let the lead delegate a bounded implementation assignment to a second
  provider under its supervision.
- **Documentation.** A two-minute install path, a reproducible tour that ends
  at a refused stage boundary, and a comparison with Spec Kit.

### Changed

- **`AGENTS.md` is the only instruction file Heddle manages.** The mechanism
  that kept a byte-identical mirror for Claude Code is retired: `heddle init`
  writes four targets, `heddle sync` maintains the plan-status and
  session-entry regions only, and the `sync` section of `.heddle.yaml` is no
  longer part of the vocabulary.
- **Repository guardrails.** Pre-commit runs the packaged hook library, the
  public-repository boundary is checked at commit, push and CI, and the
  reviewed pytest inventory is part of the release surface.

### Fixed

- Pending intakes render in `heddle orient` text output.
- Terminal kickoff repair actions render.
- A bounded `heddle drive --until` stops before probing its target stage.
- Generated workflow control paths are deduplicated and feature orchestration
  control records are recognized.
- Scaffold primary test bindings are validated before review.
- Declared record paths in dependency advice are normalized.

### Known limitations

- Review gates run only through the Codex and Claude Code CLIs.
- Python 3.13 or newer is required.
- Heddle is not on PyPI. The `heddle` name there belongs to an unrelated
  project; install from Git or from the release wheel.
- There is no `heddle --version`. `heddle doctor` reports the install mode
  and package location.

[Unreleased]: https://github.com/Wrosinski/heddle/compare/v0.2.0...dev
[0.2.0]: https://github.com/Wrosinski/heddle/releases/tag/v0.2.0
[0.1.0]: https://github.com/Wrosinski/heddle/releases/tag/v0.1.0
