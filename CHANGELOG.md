# Changelog

All notable changes to Heddle are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor
version can change command and payload contracts; `heddle help --json` is the
authority for the installed version.

## [Unreleased]

### Added

- Release process documentation covering qualification, tagging, the GitHub
  Release, and the repository metadata on GitHub.
- `milestone edit` can re-point a done milestone's verification command from a
  test selector that no longer resolves to one that resolves in the same file,
  as after an upstream test rename. Every other command token and the expected
  text must stay unchanged; any other rewrite still needs an ownership expansion.

### Changed

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

[Unreleased]: https://github.com/Wrosinski/heddle/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Wrosinski/heddle/releases/tag/v0.1.0
