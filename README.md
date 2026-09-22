<picture>
  <source media="(prefers-color-scheme: dark)"
          srcset="assets/brand/logos/heddle-horizontal-inverse.svg">
  <source media="(prefers-color-scheme: light)"
          srcset="assets/brand/logos/heddle-horizontal-graphite.svg">
  <img alt="Heddle" src="assets/brand/logos/heddle-horizontal-graphite.svg"
       width="360">
</picture>

Heddle is an open-source local workflow runtime for LLM-assisted software
engineering. It helps agents carry a change from researched scope to verified
implementation, with explicit decisions, accountable reviews, and a reliable
handoff between sessions.

Most spec-driven tools hand the agent a template and ask it to follow the
process. Heddle records what actually happened and refuses to advance when the
evidence is missing, failed, or stale. If you know Spec Kit, start with
[how Heddle differs from it](docs/comparison.md).

## Try it in two minutes

You need [uv](https://docs.astral.sh/uv/); it fetches Python 3.13 on demand.
You do not need Codex or Claude Code to look around. Those runners are called
only when a confirmed review policy schedules a model review.

Run the v0.1.0 release without installing it:

```bash
uvx --from git+https://github.com/Wrosinski/heddle@v0.1.0 heddle help
```

Or put it on your `PATH`:

```bash
uv tool install --python 3.13 "heddle @ git+https://github.com/Wrosinski/heddle.git@v0.1.0"
heddle help
```

Drop `@v0.1.0` to follow the default branch instead.

The PyPI package named `heddle` is an unrelated project; do not install it by
bare name.

Then take the [tour](docs/tour.md). It creates a throwaway repository, adopts
it, admits one feature with every model review switched off, records real test
evidence, and ends at the boundary Heddle refuses to cross. The transcript in
the next section is taken from it.

## What it looks like

A feature moves through eight stages: specify, spec review, plan review,
scaffold, implement, peer review, robustness, and complete. Your agent does the
work in each stage. Heddle records the facts, tells the agent what is legal
next, and refuses the transitions the facts do not support.

Adoption writes four files and nothing else:

```console
$ heddle init
init:
  create: .heddle.yaml (scaffold-once)
  create: AGENTS.md (managed-region)
  create: docs/workflow/engineering-principles.md (scaffold-once)
  create: .heddle.lock (runtime-owned)
```

Admission takes a researched intake and a review policy you confirm in full.
From then on every session starts the same way:

```console
$ heddle orient
add-rate-limit — stage specify (authorized through specify) — fresh entry
  task: (none)
  guidance (read-only): stage-work; briefing: heddle kickoff --feature add-rate-limit
  blocking:  awaiting-human-authorization
  next: heddle kickoff --feature add-rate-limit — render the specify briefing — fresh stage, no facts yet
```

Proof is recorded against the files it covered. Change one of them and the
proof stops counting (trimmed):

```console
$ heddle verify --scope m1
verify [add-rate-limit] revision 10

$ heddle status
  ...
  verification m1: fresh

$ printf '\n\ndef limit(client):\n    return True\n' >> src/app.py

$ heddle status
  ...
  blocking:  verification-missing, awaiting-human-authorization
  verification m1: content-stale; rerun: heddle verify --scope m1 --expect-revision 11 --feature add-rate-limit

$ heddle phase-exit
heddle: error[verification-missing]: verification scope 'm1' is not fresh: content-stale
  hint: rerun every listed verification action and retry the transition
  next: heddle verify --scope m1 --expect-revision 11 --feature add-rate-limit - refresh m1 verification (content-stale)
```

Git was not consulted. The runtime compared the owned files with the manifest
from the recorded run and refused the boundary.

### The process, distilled

1. **Research and route.** The agent researches the change. You choose Direct
   work or a Heddle feature. Prepare records the intake and recommends a
   review policy; you confirm the whole matrix; start admits the feature.
2. **Author.** At specify, the agent writes the feature spec and the
   implementation plan from packaged scaffolds. `heddle kickoff` renders the
   briefing for whichever stage is current.
3. **Review.** Each role in the policy runs through `heddle run-gate` with
   Codex or Claude Code as a read-only reviewer. Findings stay attached to the
   review that raised them; the lead records an evidence-bound disposition for
   each; closure is derived, not declared.
4. **Build and prove.** Milestones declare owned paths and a verification
   command. `heddle verify` runs it and records exit code, log, and content
   identity. `heddle phase-exit` moves one stage only when the recorded
   evidence supports it.
5. **Close.** Completion is human-owned. Acceptance needs current proof, the
   configured close suite, and an explicit grant. Records are retained locally.
6. **Automate when you choose.** `heddle drive` executes the next legal
   actions until it reaches a decision, a blocker, or the human handoff.

### What to expect

- Inputs are small YAML or JSON payloads; `heddle help` prints every schema
  with an example.
- Every mutating command returns a revision, and the next command can guard on
  it, so two sessions cannot silently overwrite each other.
- Records live under the plans and intake directories in your repository.
  Whether you commit them is the host's decision.
- Model reviews cost exactly the provider calls the confirmed policy schedules.
  All-off is a legal policy; final proof is required regardless.
- Expect more ceremony than a slash command. Small changes can take the Direct
  route without a workspace.
- This is version 0.1.0. Contracts can change between releases.

## Why Heddle?

Heddle gives agent-assisted development a repeatable path from an idea to an
accepted change. Agents investigate, design, and implement; Heddle tracks the
work, identifies what must happen next, and checks the requirements for moving
forward.

- **Verification tied to the code tested.** Recorded checks include the
  command, result, log, and identities of the declared source inputs.
  Heddle checks whether that evidence still applies before allowing progress,
  making missing, failed, or stale verification visible.
- **Review findings followed through to resolution.** Original findings remain
  attached to their reviews. The lead records how each obligation is addressed,
  supported by inspection, tests, later review, or an explicit decision.
  A clean later report alone does not erase an earlier unresolved finding.
- **Review effort matched to the change.** Heddle recommends a review policy
  based on scope, complexity, and testability. You confirm the review roles,
  models, reasoning effort, and round limits, with optional independent
  reviewers across Codex and Claude Code, so one provider can check the other.
- **A connected development workflow.** Carry researched scope through
  specification, planning, test scaffolding, implementation, review, and
  explicit completion. Milestones connect implementation work to acceptance
  criteria, so progress stays tied to the intended outcome.
- **Continuity across sessions.** Resume with recorded tasks, decisions,
  verification results, and session handoffs. `heddle orient` identifies the
  next action, while stage briefings give agents the instructions and context
  for the work ahead.
- **Your engineering principles guide the work.** Host-authored principles
  inform design choices, implementation, and review. The packaged guidance
  emphasizes repository research, justified scope, simple designs, and focused
  testing, giving agents a consistent basis for everyday trade-offs.
- **Autonomy with explicit boundaries.** Work interactively or use supervised
  automation for authorized stages. The driver pauses for unresolved decisions,
  blocked progress, and the human completion handoff. Review limits and
  non-progress checks help keep repeated attempts under control.

Heddle runs locally and keeps workflow records in your workspace. It integrates
with installed Codex and Claude Code runners; model execution uses the selected
provider.

## Quick start

### Install Heddle for adoption

The two-minute install above pins the release tag. For a repository you
mean to keep, choose one installation mode; see the
[host-integration guide](docs/workflow/host-integration.md) for setup details.
Requires **Python 3.13+**, [uv](https://docs.astral.sh/uv/), and installed,
authenticated Codex or Claude Code runners for the review roles you select.

#### Editable checkout — develop Heddle

Use a local clone when you want your Heddle source edits to take effect in new
CLI processes. Replace `<checkout>` with that clone's absolute path:

```bash
uv tool install --force --editable --python 3.13 <checkout>
```

#### Pinned Git commit — use a fixed version

This non-editable installation uses a fixed snapshot, independent of later
changes to a local clone. Replace the placeholder with a reviewed full commit SHA:

```bash
uv tool install --force --refresh --python 3.13 "heddle @ git+https://github.com/Wrosinski/heddle.git@<full-commit-sha>"
```

#### Reviewed wheel — use a built package

Also non-editable; install the exact wheel you reviewed:

```bash
uv tool install --force --python 3.13 /absolute/path/to/dist/heddle-<version>-py3-none-any.whl
```

Editable source changes need a fresh CLI process; metadata or dependency changes
also need reinstallation. For non-editable installations, rerun the chosen
install command to select a new commit or wheel.

### Adopt a host

From the repository you want Heddle to manage, preview and apply its setup:

```bash
heddle init --dry-run
heddle init
```

Init preserves existing `AGENTS.md` content while integrating its session-entry
instructions. The adopter must author and ratify
`docs/workflow/engineering-principles.md`: set `status: ratified` after reviewing
and adopting those principles. Interactive work proceeds without it; the
automated flow stops on a `principles-not-ratified` blocker until it is set.
Then run doctor to check the installation:

```bash
heddle doctor
heddle orient
```

If the host already has Heddle configuration and principles but no adoption lock,
use `heddle init --adopt-existing --dry-run` before applying
`heddle init --adopt-existing`. See the
[host-integration guide](docs/workflow/host-integration.md) for refusal handling
and configuration details.

### Start your first feature

Ask your agent to research the change, then choose Direct work or a formal
Heddle workflow. For Heddle, the agent prepares the intake and presents the full
review policy for your confirmation before starting:

```bash
heddle feature prepare <slug> --area <area> --from-file <intake.yaml>
heddle feature policy <slug> --from-file <approved-policy.yaml> --expect-revision <revision>
heddle feature start <slug> --area <area> --expect-revision <revision>
heddle orient --feature <slug>
```

The files contain researched intake and the complete owner-approved policy;
`heddle help` describes their schemas, and the [tour](docs/tour.md) shows
worked examples of both. Use the current revision returned by each mutating
command for the next command's guard, then follow `next_actions`.

## Daily use

Start each session with `heddle orient` and follow its `next_actions`.
Run `heddle kickoff` when routed, `heddle status` for progress and blockers,
and `heddle help` for commands. `.heddle.yaml` holds host configuration;
native commands maintain workflow state. Repository instructions and your
authorization continue to govern test runs and external actions.

## Updating

Stop long-lived processes, especially `heddle drive`, and checkpoint your work
before changing installations. Start a fresh CLI process and run `heddle doctor`
after updating. Follow the [update procedure](docs/workflow/host-integration.md#update-heddle-safely)
for verification, runtime compatibility, and explicit projection previews.
Installation changes never silently rewrite host documents.

## Documentation and contributing

- [Tour](docs/tour.md): one feature from adoption to a refused boundary, in ten minutes.
- [How Heddle differs from Spec Kit](docs/comparison.md): enforcement, verification, review, lock-in, and cost, side by side.
- [Workflow guide](docs/workflow/workflow.md): the development lifecycle.
- [Workflow model](docs/design/workflow-model.md): state, review, and evidence rules.
- [Architecture](docs/design/architecture.md): package structure and integration boundaries.
- [Testing strategy](docs/workflow/testing-strategy.md): test selection and execution authority.
- [Release process](docs/workflow/release-process.md): how a version is qualified, tagged and published.
- [Changelog](CHANGELOG.md): what each release changed.
- [Contributing](CONTRIBUTING.md): development setup and checks.
- [Security](SECURITY.md): vulnerability reporting.

Heddle is copyright Wojtek Rosinski and distributed under the [MIT License](LICENSE),
with no support commitment, guarantees, or warranty. Review commands before
allowing them to change a repository or call an external service.
