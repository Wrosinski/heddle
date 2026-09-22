# How Heddle differs from Spec Kit

Heddle and [GitHub Spec Kit](https://github.com/github/spec-kit) both want an
agent to research, specify, plan and verify before it calls a change done. They
differ in what happens when the agent does not. Spec Kit asks; Heddle checks.
This page compares the two on the axes reviewers already use for spec-driven
tools, then says where each is the better choice.

Claims about Spec Kit come from its public documentation as of September 2026:
the [README](https://github.com/github/spec-kit), the
[quickstart](https://github.github.io/spec-kit/quickstart.html) and the
[methodology](https://github.com/github/spec-kit/blob/main/spec-driven.md).
Claims about Heddle come from its command contracts and the [tour](tour.md),
which you can rerun in ten minutes. Corrections are welcome as issues.

## The short version

Spec Kit is a set of templates, scripts and slash commands. `specify init`
copies them into a repository, and `/speckit-specify`, `/speckit-plan`,
`/speckit-tasks` and `/speckit-implement` prompt the agent through the
methodology. Its gates are text. The plan template's "Phase -1 gates" are
described as "compile-time checks for architectural principles", and the agent
is told it "cannot proceed without either passing the gates or documenting
justified exceptions". Whether that happens depends on the agent reading the
template and doing what it says.

Heddle is a runtime. The stage order, the confirmed review policy, the
milestones, every verification result and every review finding are recorded
facts, and each transition is derived from those facts. When the evidence is
missing, failed or stale, the transition is refused with an error code, a hint
and the exact command that repairs it:

```console
$ heddle phase-exit
heddle: error[verification-missing]: verification scope 'm1' is not fresh: content-stale
  hint: rerun every listed verification action and retry the transition
  next: heddle verify --scope m1 --expect-revision 11 --feature add-rate-limit - refresh m1 verification (content-stale)
```

Two consequences follow. The agent cannot talk its way past a boundary, because
the boundary does not read prose. And a second session, a second agent or a
second provider can pick the work up from the recorded facts rather than from a
chat transcript.

## Side by side

| Axis | Spec Kit | Heddle |
| --- | --- | --- |
| Enforcement | Templates and prompts. Helper scripts check that the previous artifact exists; content and order are the agent's responsibility. | State machine. `heddle phase-exit` advances exactly one legal stage and refuses on missing tasks, missing or stale proof, unresolved decisions or open review obligations. |
| Verification | `/speckit-converge` asks the agent to report convergence. The bug-fixing extension asks for a `verified`, `partial` or `failed` verdict. Nothing records that a test actually ran. | `heddle verify` runs the stored command and records the exit code, the log and a content manifest of the owned sources. Status reports missing, failed, command-stale, source-set-stale or content-stale evidence, and stale evidence blocks the boundary. |
| Review | `/speckit-clarify`, `/speckit-checklist` and `/speckit-analyze` are self-review steps run by the same agent. They are advisory. | Eight review roles, each with a confirmed provider CLI, model, reasoning effort, round mode, round limit and optional independent second reviewer. Findings stay attached to the review that raised them; the lead records an evidence-bound disposition for each one. A clean later report does not close an earlier finding. |
| Cross-provider review | The agent that wrote the code reviews it. | Codex can review Claude Code's work and the reverse, under one policy, with retained captures per reviewer slot. |
| Session continuity | Artifacts on disk. The next session re-reads them and decides what to do. | `heddle orient` derives the next legal action from recorded state; `heddle kickoff` renders the briefing for that stage; tasks, decisions and session notes are recorded natively. |
| Agent support | Around forty named integrations plus a generic option. | Any agent that reads `AGENTS.md` can drive the CLI. Review gates run only through Codex and Claude Code. |
| Lock-in | None. MIT, plain Markdown, bring your own agent. | None. MIT, local YAML and Markdown, bring your own provider CLIs. |
| Pricing | Free. You pay your agent's provider. | Free. You pay for the review calls your policy schedules. The confirmed policy reports its minimum and maximum call budget, all-off is a legal policy, and final proof is required regardless. |
| Footprint | Python 3.11 or newer and uv. `specify init` writes `.specify/` and per-agent command files. | Python 3.13 or newer and uv. `heddle init` writes `.heddle.yaml`, a managed region in `AGENTS.md`, a principles seed and a lock. Workflow records live under `plans/` and `.heddle/`. |
| Maturity | Maintained by GitHub, large community, extensions for bug fixing and idea assessment. | Version 0.0.1, one maintainer, used to build itself. Contracts can still change. No support commitment. |

## What Heddle enforces

### Stage order and authorization

A feature moves through specify, spec review, plan review, scaffold, implement,
peer review, robustness and complete. `heddle phase-exit` moves one stage; a
`--through` ceiling records how far a human has authorized unattended travel.
Entering `complete` accepts nothing by itself. Acceptance is a separate,
human-owned step that requires current evidence and every configured close
suite.

### Proof bound to the code it tested

Each milestone declares the source paths it owns and one verification command
with its expected result. `heddle verify --scope m1` runs that command and
records the exit code, the log and a manifest of the owned files' content
identity. Change an owned file and the recorded run becomes `content-stale`;
change the command and it becomes `command-stale`; change the owned set and it
becomes `source-set-stale`. Git `HEAD` is diagnostic only. The same rule covers
the feature-wide `acceptance`, `smoke` and `live` scopes at their boundaries.

### Review findings that stay open

A review result is bound to its assignment, round, reviewer slot, inputs and
execution identity. The original findings are never rewritten. The lead
resolves each one with a disposition that cites inspection, a contract, a
verification run, a later review or an explicit owner decision. Editing the
reviewed subject invalidates the disposition. Continuing a review that stopped
for no progress needs an explicit owner decision, and a reached round cap needs
a policy amendment. None of this is prose the agent can paraphrase; it is state
the runtime reads.

### Explicit review policy, explicit cost

`heddle feature prepare` records the researched intake and returns a
recommended policy. The owner confirms the complete matrix through
`heddle feature policy`: every role, its scope, mode, limits, reviewer tuple and
optional secondary. Only then does `heddle feature start` admit the feature. The
confirmed policy is the only source of provider calls; nothing infers a call
from a stage transition or a populated command.

## What Spec Kit does better

- **Reach.** Some forty agent integrations against Heddle's two review runners.
  If your team is on Cursor, Gemini CLI or Copilot, Spec Kit meets you there.
- **Ceremony.** Five slash commands cover the short path. Heddle wants YAML
  payloads, revision guards and a confirmed eight-role policy before the first
  stage opens. That is the price of the record; it is still a price.
- **Breadth.** Spec Kit ships bug-fixing and idea-assessment extensions. Heddle
  covers one feature lifecycle.
- **Maintenance.** GitHub maintains Spec Kit with a large contributor base.
  Heddle is one person's runtime at version 0.0.1.
- **Python floor.** Spec Kit runs on Python 3.11; Heddle requires 3.13.

## Choosing

Pick Spec Kit when you want a lighter process, your agent is not Codex or Claude
Code, or you trust the agent to follow a good template and mainly need one.

Pick Heddle when the cost of an unverified "done" is high, when work spans
sessions or agents and you need the handoff to survive, when you want a second
provider to check the first, or when you want review spend to be a decision you
made rather than something that happened.

Heddle does not care which prompts produced the spec and the plan. A team that
likes Spec Kit's templates can keep them for authoring and let Heddle own the
record; the two do not compete for the same files.

## Other tools on the same axes

A June 2026 roundup of spec-driven tools builds its table around exactly these
axes, and notes that "almost every row says none" under enforcement and
verification. On its reading, OpenSpec's `validate` checks structure and its
verify step does not block; BMAD gates process handoffs and adds an adversarial
reviewer but has no built-in verification; Kiro runs tests through agent hooks
but ties you to its IDE and metered credits. Heddle's answer on both columns is
the runtime described above.

Sources: [codemyspec, "Best spec-driven development tools (2026)"](https://codemyspec.com/blog/best-spec-driven-development-tools);
[Spec Kit README](https://github.com/github/spec-kit);
[Spec Kit quickstart](https://github.github.io/spec-kit/quickstart.html);
[Spec Kit integrations](https://github.github.io/spec-kit/reference/integrations.html);
[Spec Kit methodology](https://github.com/github/spec-kit/blob/main/spec-driven.md).
