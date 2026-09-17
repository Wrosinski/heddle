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

## Why Heddle?

Heddle gives agent-assisted development a repeatable path from an idea to an
accepted change. Agents investigate, design, and implement; Heddle tracks the
work, identifies what must happen next, and checks the requirements for moving
forward.

- **A connected development workflow.** Carry researched scope through
  specification, planning, test scaffolding, implementation, review, and
  explicit completion. Milestones connect implementation work to acceptance
  criteria, so progress stays tied to the intended outcome.
- **Continuity across sessions.** Resume with recorded tasks, decisions,
  verification results, and session handoffs. `heddle orient` identifies the
  next action, while stage briefings give agents the instructions and context
  for the work ahead.
- **Review effort matched to the change.** Heddle recommends a review policy
  based on scope, complexity, and testability. You confirm the review roles,
  models, reasoning effort, and round limits, with optional independent
  reviewers across Codex and Claude Code.
- **Verification tied to the code tested.** Recorded checks include the
  command, result, log, and identities of the declared source inputs.
  Heddle checks whether that evidence still applies before allowing progress,
  making missing, failed, or stale verification visible.
- **Review findings followed through to resolution.** Original findings remain
  attached to their reviews. The lead records how each obligation is addressed,
  supported by inspection, tests, later review, or an explicit decision.
  A clean later report alone does not erase an earlier unresolved finding.
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

Requires **Python 3.13+**, [uv](https://docs.astral.sh/uv/), and installed,
authenticated Codex or Claude Code runners for the selected roles. Choose one
installation mode; see the [host-integration guide](docs/workflow/host-integration.md)
for setup details.

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
and adopting those principles, then run doctor to check the installation:

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
`heddle help` describes their schemas. Use the current revision returned by each
mutating command for the next command's guard, then follow `next_actions`.

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

- [Workflow guide](docs/workflow/workflow.md): the development lifecycle.
- [Workflow model](docs/design/workflow-model.md): state, review, and evidence rules.
- [Architecture](docs/design/architecture.md): package structure and integration boundaries.
- [Testing strategy](docs/workflow/testing-strategy.md): test selection and execution authority.
- [Contributing](CONTRIBUTING.md): development setup and checks.
- [Security](SECURITY.md): vulnerability reporting.

Heddle is copyright Wojtek Rosinski and distributed under the [MIT License](LICENSE),
with no support commitment, guarantees, or warranty. Review commands before
allowing them to change a repository or call an external service.
