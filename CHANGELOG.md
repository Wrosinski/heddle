# Changelog

All notable changes to Heddle are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor
version can change command and payload contracts; `heddle help --json` is the
authority for the installed version.

## [Unreleased]

### Changed

- The recommended review policy under the Full base selects `gpt-6-astra` at
  `xhigh` as the `review-test-scaffolding` secondary for every round, and no
  longer lists it as a suggestion. Under the Light base it stays a suggestion.
  Confirmed policies keep the reviewers they recorded.
- `heddle verify` reports each run as `run` beside the recorded fact, in its
  result, error details and `--stale` rows: `termination` (`completed`,
  `timeout`, `inactivity` or `launch-failed`), `elapsed_s` and the effective
  hard and inactivity `limits` with their source (`default` or `env`) and
  override variable. A run Heddle stopped at a limit names the limit and its
  override instead of asking to repair the check, and reports `counts: null`,
  because the log's last pytest summary may belong to an earlier chained step;
  a command that exits 124 itself stays `completed`. `--dry-run` shows the
  limits and `heddle verify --help` states the defaults. The recorded fact is
  unchanged. A run whose log or after-capture cannot be published still
  reports its `run`, and human output prints each `--stale` row's run.
- A close-suite failure from `heddle feature complete` names how its run ended:
  a Heddle limit with its override, a shell that could not start, or the
  command's own exit code.
- `heddle status` and `heddle orient` verification rows of a witness lane
  (`acceptance` or `live`) with a routed rerun carry `rerun_route`: `waivable`
  after review, `accept-prior` at implement exit when only formatting changed,
  or `rerun-only`, with its `reason`, including a waiver that stopped binding.
  The orient line prints it. Fresh and waived rows omit it. Next-action
  reasons, the boundary hint, `verify --stale` skip reasons and the
  `witness-waiver` refusal read the same route, so an offer and a refusal no
  longer disagree. The field is additive under `heddle.status/v1` and
  `heddle.orient/v0`; the release process now states that rule.
- `heddle run-gates` refusals name their cause in `details.cause`:
  `launch-sequential`, `implementation-pending`, `other-work-first`,
  `single-slot` or `no-launchable-slot`, in that order of precedence, with a
  message to match. Every launch and refusal lists the slots of routed reviews
  it left out under `excluded_slots`, each with its reason:
  `scope-unresolved`, `assignment-sealed`, `changed-source-pending`,
  `slot-recorded` or `admission-refused`, the last with the refusal's code,
  message and hint.
- `heddle run-gate milestone-review` for the current milestone refuses with
  `task-incomplete` while that milestone has no tasks or an unfinished one,
  as readiness already withheld it; its details name the pending work.
  Reviews of done milestones are still admitted.
- A new disposition or scope-change reference that names a test node of an
  existing file (`path::node`) or a commit (`commit <hex>`), or is absolute or
  escapes the repository, is refused as `malformed-field` with a remedy to
  cite the file and name the node or commit in the reason. Other missing
  references stay `reference-unavailable`, and their remedy mentions node ids
  and commits when the reference has that shape.
- `heddle task edit <ref> <text>` names the `--text` form it needs.
- `heddle run-gate` and `heddle run-gates` print progress to stderr once a
  minute while reviewers run, naming the gate, slot, CLI and elapsed time;
  `run-gates` gives every slot's state (`running`, `done after …` or
  `failed after …`) on one line. Progress used to arrive as unlabelled lines
  only after each provider had exited. `run-gates` members report
  `started_at`, `finished_at` and `duration_s`, and its human output names
  each member instead of printing `None`.
- A recorded review whose verdict has not converged exits 4 with `ok: true`
  on spec, plan and scaffold review too, where it exited 3, so exit 3 always
  comes with `ok: false`. The driver carries only an `ok: true` exit 4 into
  the next session.
- `heddle run-gates` prints launch advice once, before any slot starts, and
  returns it in the envelope diagnostics whether the batch succeeds or fails.
  The advice's diagnostic code `optional-lane-unavailable` is renamed
  `review-input-git-status`.
- After a review round, readiness lists one decision action for every
  pending decision the round recorded, not only the first.
- `heddle decisions resolve --kind continue-review` and `continue-stage` take
  `--routes-to` as optional; the route defaults to the feature spec. A
  round-limit stop lists both continuation steps up front: the resolution,
  then `heddle review allowance` for the reached cap.
- `heddle decisions add` returns the decisions it allocated in
  `data.decisions` (`id`, `title`, `status`), with the would-be ids on a dry
  run.
- A failing configured close suite at `heddle feature complete` reports
  `verification-failed` with the command, its exit code and the log path,
  instead of `internal`.
- A disposition with verification evidence but no `verification_scope` is
  refused with a remedy: record the run with `heddle verify` and name its
  scope, or use `inspection` for edits not yet run under `heddle verify`. A
  review whose prior-finding accounting cites an unknown or misses an
  expected finding names the unmatched and missing pairs and the closest
  accepted run id.
- Stage briefings carry new guidance; no gate prompt changes:
  - specify and plan review say an alignment check is recorded as one
    Assessment entry in the plan's verification note;
  - scaffold and implement run the host's formatters over every file a
    disposition will cite before recording it, and the final boundaries
    settle ownership of new paths before final verification and
    dispositions;
  - specify reads allocated decision ids from the `decisions add` result;
  - implement corrects an open task's text with `heddle task edit` when a
    ruling changes its work;
  - the stage briefings and the gate failure guide say that exit 4 with
    `ok: true` after a review means the round was recorded with work
    remaining, and that exit 3 means the command failed.
- The recommended review policy runs `milestone-review` on
  `claude-opus-5-5` at `xhigh` reasoning effort instead of `high`, and the
  Claude lane default for that gate matches. Confirmed policies keep the
  reviewer they recorded.
- Scaffold review admission and the repository AC-coverage hook resolve every
  `Verified-by:` line of an acceptance criterion, not only the first. A broken
  target on a later line refuses before the reviewer is called, and a test file
  named only on a later line becomes a reviewed input, so a scaffold review of
  such a feature needs a refresh. An empty `Verified-by:` line no longer hides
  targets on the lines after it, a repeated target binds once, and a
  `Verified-by:` example inside fenced code never binds. In the hook, an AC's
  block ends at the next AC or section heading, so Decision Log lines no
  longer bind to the last AC. Changing any `Verified-by:` line of a
  historical spec's AC now re-checks every line of that AC; previously only
  its first line was checked, even when a later line was the one edited.
- Direct concept-review guidance now checks the read-only Codex sandbox before
  a provider call, preserves launch failures and supplied-text coverage limits,
  and requires authorization for wider access. Full-access change checks compare
  contents rather than treating unchanged Git status as proof. The host guide
  explains AppArmor diagnosis and qualification of a scoped Bubblewrap repair.
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
- A disposition row that cannot close a REPORT finding is refused with
  `decision-routed` and a remedy naming the owner decision; such a finding
  closes only through `settled` with that decision's `decision_id`. Rows
  already recorded that way are explained in readiness instead of staying
  open without a reason.
- At a round-limit, no-progress or no-decrease stop, readiness routes to
  `heddle review disposition` or `heddle review reaffirm` instead of the stop
  when every open row closes by re-recording evidence alone. That covers a row
  whose assignment basis, citations or verification changed, judged at the
  current basis and as re-cited, and, at a round limit, the latest round's
  undisposed `@coverage`. A row whose evidence fails on its own, an inspection
  duty, review evidence whose review source changed, and any retained,
  undisposed or awaiting-decision row keep the stop, and a pending owner
  decision still comes first. The routed action says why no round or owner
  decision is needed. Rows `heddle review reaffirm` writes carry
  `rebound_by: reaffirm`; `state.yaml` accepts only `review-equivalence` and
  `reaffirm` as a `rebound_by` writer.
- A `heddle review round-open` that records a no-progress, no-decrease or
  round-limit stop returns `ok: true` with exit 4, and its `data` carries
  `wrote`, `revision`, `decision_id` and `stop_reason`; the next action
  resolves that decision. A repeat call while the stop is pending, a reached
  cap and a sealed acceptance are still refused. `heddle drive` records the
  stop, then pauses on its pending decision and notifies the owner, exiting 1
  where it exited 3.
- `heddle decisions add`, `resolve` and `record-policy`, `heddle feature
  policy`, `heddle review allowance` and `heddle review disposition`,
  `round-open` and `reaffirm` report `wrote` on a real write; `would_write`
  appears only in dry runs, and a real write no longer reports
  `dry_run: false`.
- `heddle milestone add` accepts complexity `low` or `high` only and refuses
  any other value as a usage error naming both. The plan scaffold no longer
  says the command appends a plan section; the lead writes it.
- Stage briefings and gate prompts carry new guidance:
  - spec, plan and scaffold review name the validating-round route;
  - specify asks for sources on current-behaviour statements, ownership traced
    from each milestone's effects, a commit-guard check of planned test edits,
    "no data" as a rule outcome of its own, the host's specs `_index` table
    when it has one, a second-model milestone-review secondary for milestones
    that rebind an inherited boundary, and a fallback when the concept
    review's read-only Codex sandbox cannot start;
  - scaffold asks for history fixtures recorded by the pre-change code;
  - implement re-presents milestone review settings when a contract amendment
    widens scope;
  - the shared decision policy asks owner questions on data, time or state
    rules to show a case on each side of the boundary;
  - scaffold review reports an acceptance criterion proven only by an
    acceptance or live test as `partial`, and a coverage claim carried over
    from an earlier round cites a trace or test.

  Every gate's prompt version changes. Review evidence recorded under the
  earlier prompts in features already in flight must be refreshed or
  re-dispositioned.
- A driven specify session may edit the specs directory's `_index` table
  beside `_descriptions.yaml`.
- Formatter reflows no longer reopen reviews. Beside each path's raw identity,
  Heddle records a review-equivalence identity under one of two rules:
  `python-tree/1` for `.py` files (the syntax tree, comments and cleaned
  docstrings, under the interpreter's major and minor version) and
  `markdown-text/1` for `.md` files (table padding, delimiter dashes, internal
  spacing, non-break trailing whitespace and line endings ignored outside code,
  HTML blocks, front matter and link targets, under the CommonMark parser's
  version). Disposition bases, `review` evidence, recorded review runs and
  cited references stay current across review-equivalent changes, and a gate
  run whose sources change only that way during the provider call is recorded
  with its launch-time basis. Verification stays raw: a formatting change still
  makes a fact `content-stale`, and its explanation names the change
  formatting-only. Content delivered to reviewers and commands is unchanged.
- Source evidence is written as `heddle.source-evidence/v2`, adding the
  equivalence identity per observation and named components per manifest;
  v1 artifacts read and re-encode byte-exactly, and upgrading mid-feature
  stales nothing. Dispositions name the manifest behind their basis, so a
  missing or altered manifest is an integrity error.
- Phase exit and milestone advance seal dispositions that are current only by
  equivalence by appending a re-binding row (`rebound_by`) for each one before
  the receipt seals the current raw basis; a dry run lists them as
  `rebinding_rows`.
- Stale explanations carry typed `changes` beside `changed_dependencies`, each
  naming a path or component and its kind (`content`, `formatting-only`,
  `added`, `removed`, `kind`, `mode` or `membership`), through dispositions
  backed by verification or by a review too. A record made before this
  release says its change detail is unavailable.
- The scaffolding review judges the declared tests and bindings plus every test
  under the tests root that differs in content from the feature baseline,
  including untracked and deleted ones, so a commit followed by its revert, an
  amend or a rebase no longer changes it. Assignments opened before this
  release keep their earlier selection through sealing. When the baseline is
  unavailable, readiness reports a `baseline-unavailable` blocking condition
  naming the reason and `heddle feature baseline set`; orient, plan-status and
  the driver stop there, and scaffolding and milestone gates refuse before any
  provider call.
- The rendered plan-status `next:` and `conditions:` now equal orient's first
  action and blocking conditions, and a `heddle sync` rewrite of that region
  no longer produces uncommitted-plan advice before a review.
- `heddle review reaffirm` takes `--role` and `--scope` as optional together.
  Without them it covers every unsealed assignment at the current boundary and
  skips sealed ones. It writes the stale rows whose citations and evidence
  still qualify in one transaction and lists the rest with their changed
  paths, and an assignment missing a slot with its reason. It exits 0 when
  nothing remains, 4 when rows remain beside those written, and 1, writing
  nothing, when no row is writable. Readiness routes it as `review-reaffirm`
  authoring work listing the rows it would write and refuse; the driver opens
  a session for that work and never runs the command itself.
- Stage briefings carry the new behaviour; no gate prompt changes:
  - spec review, plan review, scaffold, implement, peer review and robustness
    explain partial reaffirm and which reformatting keeps review evidence
    current;
  - implement and peer review name `heddle verify --stale` and explain that
    verification stays raw;
  - implement, the plan scaffold and the witness guidance let the owner accept
    an earlier witness at implement exit only for formatting-only changes, and
    peer review and robustness keep a waiver across such changes;
  - scaffold explains the content-based reviewed tests and the
    `baseline-unavailable` stop.
- Project commands stop looking for `.heddle.yaml` at the first Git boundary,
  a `.git` directory or file. A linked worktree or submodule inside another
  checkout no longer resolves to that checkout's project when its own Heddle
  files are untracked; it fails with `workspace-invalid`, names its Git root
  and points to copying the files or `heddle init --dry-run`. A
  `.heddle.yaml` above the Git root is no longer found.

### Added

- `heddle init --no-session-entry` adopts a host without the `AGENTS.md`
  session entry. The lock records only `.heddle.yaml` and the principles file,
  and `heddle sync` and `heddle validate` then leave `AGENTS.md` alone. A
  lock that records the session entry refuses the flag. Older Heddle builds
  reject a lock without the `AGENTS.md` entry.
- `heddle task edit <task-ref> --text <text>` corrects an open task's text on
  the current milestone, keeping its id and status; a done task is refused.
- `heddle session log --help` publishes its payload schema,
  `heddle.session-input/v1`, with a note to quote `started_at` and
  `ended_at` in YAML.
- `heddle feature start --through <stage> --statement <text>` records the
  owner's stated authority ceiling and where it was given. Without
  `--through` the ceiling stays at specify. While the specification
  checkpoint is on, specify exit still waits until a resolved
  `Specification checkpoint` decision exists, or until an owner
  `phase-exit` when the owner skipped that checkpoint for the feature. The
  review change confirmation stays the lead's question, as under any raised
  ceiling.
- `heddle review round-open --help` publishes its payload schema,
  `heddle.review-round-input/v1`, and `heddle milestone add --help` publishes
  `heddle.milestone-input/v1`. Disposition help now explains each status and
  evidence kind and which references can be cited.
- `heddle verify` reports pytest outcome counts as `counts` in its result and
  human output. The counts are not recorded in state.
- `heddle validate` adds three advisories: `verified-by-unread` for
  Verified-by text the parser does not read, `scaffold-test-path-missing` for
  test paths the scaffolding review will refuse to start on, and
  `verified-by-unselected` for Verified-by targets no verification command
  selects. `heddle commands set` and `heddle milestone edit` report
  `verified-by-unselected` after a write, so the scaffold session sees it
  where it sets the commands.
- A Codex model-capacity refusal is classified as `codex-at-capacity`, with
  diagnosis `capacity`, instead of a generic exit or a tool loop. The gate
  failure guide says to retry the same model once capacity returns.
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
  passing fact and the current source manifest, so a later formatting-only
  change keeps it while any other relevant edit or a new run makes it inert.
  Failed, missing, unstable and command-stale evidence is never waivable, and
  the waiver never qualifies review-disposition evidence. At implement exit
  the same decision lets the owner accept an earlier passing witness only when
  every change since it is formatting-only; readiness offers it beside the
  rerun in that case alone. Completion records waived lanes in
  `completion.waived_witness_decision_ids`, and the close audit expects a
  matching decision-journal entry.
- `heddle verify --stale` reruns, in readiness order, every actionable stale
  scope readiness leads with: a required scope that is `content-stale` or
  `source-set-stale`, whose rerun is not waived or accepted as degraded smoke,
  and which is not `live`. Each scope is admitted, reassessed and recorded as
  its own fact: `data.verifications` has one row per scope run, `skipped`
  lists every other scope with its reason, and `unrun` the scopes a stop left.
  With nothing to run it returns `ok: true`, exit 0. The batch continues past
  a recorded failing or unstable run (`ok: false`, exit 3
  `verification-failed`, every row in the error details) and stops at anything
  else, such as a conflict (exit 5), lost authority or an integrity failure
  (exit 3), keeping the rows recorded before the stop. `--stale` excludes
  `--scope`, and `--dry-run` lists every command it would run. Orient shows
  one `verify --stale` action in place of two or more leading verify actions,
  and the driver counts each recorded row toward its scope's attempt cap.
- `heddle feature baseline set --commit <rev> --reason <text>` re-points a
  feature baseline that no longer resolves or is no longer an ancestor of
  `HEAD`. It refuses while the recorded baseline is available, requires an
  ancestor of `HEAD`, supports `--expect-revision` and `--dry-run`, appends the
  previous value, reason and time to `source_baseline_history`, and is on no
  agent session allowlist.

### Fixed

- An outside-feature attribution no longer blocks coverage while Git reports
  no change to its path since the feature baseline, for example after a
  merge reverts it. Such a path needs no coverage and could not be
  attributed again, so validate, status, stage transitions and completion
  stayed blocked with no way out. If the path
  changes later, the attribution applies again, bound to the bytes and
  evidence it recorded.

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
