# CLAUDE.md — Workflow Section

<!-- Copy the section below into your project's AGENTS.md file, outside the -->
<!-- heddle-managed markers. Heddle keeps CLAUDE.md as a byte-identical mirror -->
<!-- of AGENTS.md (sync.mirror, default on), so edit AGENTS.md, not the mirror. -->
<!-- Adjust paths and commands to match your project. -->

## Development Workflow

This repository uses Heddle. Enter a feature session with `heddle orient`,
follow its `next_actions`, and run `heddle kickoff` only when orientation
routes there. The returned briefing carries the current stage's contracts,
artifacts, and authority. Keep this entry rule in one standing instruction
source, including any section installed by `heddle init`.

### For New Features

After research, an explicit Heddle route and confirmed review policy, formal
admission creates the two documents:

- **Feature Spec** at `docs/features/[area]/[feature-name].md` — the
  permanent design record. Captures what was built, why, the contracts,
  and lessons learned. When a change touches a feature's contract, read
  its spec first. Scale other context gathering to the change's blast radius.
- **Implementation Plan** at `plans/[feature-name]/plan.md` — approach,
  methodology and technical architecture. Machine facts live beside it in
  runtime-owned `state.yaml`; use Heddle commands to update those facts.

For new inception, research and use `heddle feature prepare`, confirm Direct or
Heddle, and record the full Heddle matrix through `heddle feature policy` before
`heddle feature start`. Direct work creates no formal documents or review
schedule. Follow native help for the input contracts and returned actions.
For ongoing work, orientation resolves the workspace and its
specification; resume it instead of creating another feature. Required
research and scope decisions follow the briefing's checkpoint route, including
already-recorded authorization.

### CLI Gates

Run automated review through `heddle run-gate <gate>` when the runtime routes
it. Heddle owns gate order, reviewer isolation, evidence, and transitions;
exploration subagents do not replace formal gates.

### Prompt Templates

The installed Heddle package supplies gate prompts and stage briefings.
Use `heddle show-prompt <gate>` to inspect the effective gate prompt for this
host, including any configured override.
