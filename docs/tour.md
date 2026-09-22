# Tour: one feature, from adoption to a refused boundary

This tour takes about ten minutes, runs entirely in a throwaway directory and
never calls a model. It adopts a small repository, admits one feature with every
model review switched off, walks the feature to implementation, records real
test evidence, changes the code that evidence covered, and shows Heddle refusing
the next stage until the proof is refreshed.

Every output block below was captured from the commands shown. Two repeated
lines are trimmed after their first appearance: the `install-mode` note and the
source-coverage advisory, both explained where they first occur.

## Before you start

Install [uv](https://docs.astral.sh/uv/). It fetches Python 3.13 on demand.
Put `heddle` on your `PATH` for the session in either of these ways:

```bash
uv tool install --python 3.13 "heddle @ git+https://github.com/Wrosinski/heddle.git@v0.1.0"
```

```bash
alias heddle='uvx --from git+https://github.com/Wrosinski/heddle@v0.1.0 heddle'
```

The verification step below runs tests through `uvx pytest`, so there is
nothing else to install. Do not install the PyPI package named `heddle`; it is
an unrelated project.

## 1. A repository to adopt

Create a repository with one module, one test and one research note. The note
stands in for the research an agent would do before proposing a feature.

```bash
mkdir -p heddle-tour/src heddle-tour/tests heddle-tour/docs/research
cd heddle-tour
git init -q -b main
printf 'def hello():\n    return "hi"\n' > src/app.py
printf 'from src.app import hello\n\n\ndef test_hello():\n    assert hello() == "hi"\n' > tests/test_app.py
printf '[pytest]\npythonpath = .\n' > pytest.ini
printf '# Rate limit research\n\nRequests enter through src/app.py; tests/test_app.py covers the entry point.\n' > docs/research/rate-limit.md
git add -A && git commit -qm "initial"
```

## 2. Adopt it

Preview first. Init never overwrites host content; it reports what it would
create, integrate, refuse or skip.

```console
$ heddle init --dry-run
init dry-run:
  create: .heddle.yaml (scaffold-once)
  create: AGENTS.md (managed-region)
  create: docs/workflow/engineering-principles.md (scaffold-once)
  create: .heddle.lock (runtime-owned)

$ heddle init
init:
  create: .heddle.yaml (scaffold-once)
  create: AGENTS.md (managed-region)
  create: docs/workflow/engineering-principles.md (scaffold-once)
  create: .heddle.lock (runtime-owned)
```

Four files. `.heddle.yaml` is the host configuration and is yours after
creation. `AGENTS.md` gets one managed region telling agents to start every
session with `heddle orient`. The engineering-principles file is a seed for the
host team to author and ratify. Interactive work does not need it; the
automated flow stops on a `principles-not-ratified` blocker until the file's
frontmatter says `status: ratified`. The lock records adoption-time facts.

Run the diagnostics whenever you like. They are read-only.

```console
$ heddle doctor
info: install-mode: running from an editable checkout; package=...; interpreter=...
info: config-ok: .heddle.yaml is valid
info: lock-ok: .heddle.lock is valid and all 3 recorded paths are present
info: parse-zero-features: no active feature workspaces under plans/
```

Your first line will name a tool installation rather than an editable checkout.
Doctor also lists every packaged briefing and prompt it resolved; those lines
are omitted here.

## 3. Admit a feature

Admission has three steps and each one is explicit. First the researched
intake, which names the research source and rates the change on three axes:

```bash
cat > intake.yaml <<'YAML'
schema: heddle.intake-input/v1
route: heddle
route_reason: The change touches the request path and its tests, so it needs a spec, a plan and recorded proof.
research:
  reference: docs/research/rate-limit.md
  summary: Establishes where requests enter, which callers are affected and what the current tests cover.
axes:
  scope: small
  complexity: low
  testability: full
  scope_rationale: One module and one public entry point change.
  complexity_rationale: Existing owners and failure modes are known.
  testability_rationale: The entry point has a hermetic test.
YAML
heddle feature prepare add-rate-limit --area api --from-file intake.yaml
```

Prepare returns a recommended review policy as JSON: eight roles, each with a
provider CLI, model, reasoning effort, round mode and limit. For this small,
low-complexity, fully testable change the recommendation is the `light` base:
five roles on with a two-round upper limit, three roles off, reviewers split
between Codex and Claude Code. Preparation is not approval.

Second, the owner confirms the complete policy. For the tour every role is
`off`, which is a legal policy. Quote the word `off` in YAML, or it parses as a
boolean and the command refuses it.

```bash
cat > policy.yaml <<'YAML'
schema: heddle.feature-policy/v1
revision: 1
axes:
  scope: small
  complexity: low
  testability: full
  scope_rationale: One module and one public entry point change.
  complexity_rationale: Existing owners and failure modes are known.
  testability_rationale: The entry point has a hermetic test.
approval: Owner approved this complete policy with every model review off for the tour.
entries:
  - {role: spec-review, scope: feature, mode: "off", limit: null, minimum_rounds: 0, primary: {cli: codex, model: gpt-6-astra, reasoning_effort: high}, secondary: null, trigger: null}
  - {role: plan-review, scope: feature, mode: "off", limit: null, minimum_rounds: 0, primary: {cli: codex, model: gpt-5.6-sol, reasoning_effort: high}, secondary: null, trigger: null}
  - {role: review-test-scaffolding, scope: feature, mode: "off", limit: null, minimum_rounds: 0, primary: {cli: codex, model: gpt-5.6-sol, reasoning_effort: xhigh}, secondary: null, trigger: null}
  - {role: milestone-review, scope: milestone, mode: "off", limit: null, minimum_rounds: 0, primary: {cli: codex, model: gpt-5.6-sol, reasoning_effort: high}, secondary: null, trigger: null}
  - {role: peer-review-sequential, scope: feature, mode: "off", limit: null, minimum_rounds: 0, primary: {cli: claude, model: claude-opus-5, reasoning_effort: xhigh}, secondary: null, trigger: null}
  - {role: behavior-review, scope: feature, mode: "off", limit: null, minimum_rounds: 0, primary: {cli: claude, model: claude-opus-5, reasoning_effort: xhigh}, secondary: null, trigger: null}
  - {role: complexity-review, scope: feature, mode: "off", limit: null, minimum_rounds: 0, primary: {cli: codex, model: gpt-5.6-sol, reasoning_effort: high}, secondary: null, trigger: null}
  - {role: robustness-analysis, scope: feature, mode: "off", limit: null, minimum_rounds: 0, primary: {cli: codex, model: gpt-5.6-sol, reasoning_effort: xhigh}, secondary: null, trigger: null}
YAML
heddle feature policy add-rate-limit --from-file policy.yaml --expect-revision 1
```

The response echoes the effective policy and ends with its call budget, which
is zero on every line for an all-off policy, and the stage that owns final
verification. It also reports the new owner revision, `2`. Every mutating
command returns a revision; the next command guards on it, so two sessions
cannot silently write over each other.

Third, admission:

```console
$ heddle feature start add-rate-limit --area api --expect-revision 2
feature start [add-rate-limit]: admitted with confirmed feature policy
  advisory: Source coverage is unresolved: correct verification ownership for unresolved paths: .heddle.lock, .heddle.yaml, AGENTS.md, docs/workflow/engineering-principles.md, intake.yaml, policy.yaml. Declare product paths in milestone owns or attribute genuinely outside-feature work; coverage remains mandatory at completion.
  next: heddle feature switch add-rate-limit — make the new feature active

$ heddle feature switch add-rate-limit
active feature: add-rate-limit (pointer .git/heddle/active-feature, previous (none))
```

The advisory is Heddle noticing files that changed since the feature's source
baseline and are not yet owned by any milestone. It does not block now; it
will block completion. Real features resolve it by declaring owned paths on
milestones or attributing outside-feature work explicitly. The tour leaves it
in place, and the line is trimmed from the outputs below.

## 4. Look around

`heddle orient` is the session entry point. It reports the stage, what is
blocking, and the one action to take next.

```console
$ heddle orient
add-rate-limit — stage specify (authorized through specify) — fresh entry
  task: (none)
  guidance (read-only): stage-work; briefing: heddle kickoff --feature add-rate-limit
  close obligation (absent): (none); source: .heddle.yaml:autopilot.test_command; runs at: feature complete
  blocking:  awaiting-human-authorization
  next: heddle kickoff --feature add-rate-limit — render the specify briefing — fresh stage, no facts yet
```

`heddle status` shows the same facts with milestone, review and verification
rows once they exist. `heddle kickoff` renders the briefing an agent would work
from at this stage: the shared decision-routing policy followed by the stage
instructions.

```console
$ heddle kickoff | head -5
# Decision routing

Use this procedure to determine who owns an unresolved choice. It supplies no
additional scope, tool permission, phase authority, or execution grant. Your
role's instructions and native actions determine what you may record or do.
```

## 5. Walk the stages

Stages advance one at a time through `heddle phase-exit`. The `--through` flag
records how far a human has authorized travel; the command still moves one
legal step per call.

```console
$ heddle phase-exit --through implement
phase-exit [add-rate-limit]: stage specify -> spec-review; authorization specify -> implement; revision 2; wrote true
  next: heddle kickoff --feature add-rate-limit - render the spec-review briefing — fresh stage, no facts yet

$ heddle phase-exit --through implement
phase-exit [add-rate-limit]: stage spec-review -> plan-review; authorization implement -> implement; revision 3; wrote true
  next: heddle kickoff --feature add-rate-limit - render the plan-review briefing — fresh stage, no facts yet

$ heddle phase-exit --through implement
phase-exit [add-rate-limit]: stage plan-review -> scaffold; authorization implement -> implement; revision 4; wrote true
  next: heddle kickoff --feature add-rate-limit - render the scaffold briefing — fresh stage, no facts yet

$ heddle phase-exit --through implement
phase-exit [add-rate-limit]: stage scaffold -> implement; authorization implement -> implement; revision 5; wrote true
  next: heddle kickoff --feature add-rate-limit - render the implement briefing — fresh stage, no facts yet
```

Nothing blocked, and that is honest: with every review off and a human granting
each exit, these stages carry no recorded obligations yet. In real work the
agent authors the spec and plan at specify, the confirmed reviewers run at spec
review and plan review, their findings must be dispositioned with evidence, and
scaffold binds the tests that later proof will run.

## 6. Record proof, then break it

A milestone declares the paths it owns and the command that proves it. Declare
one, promote it, give it a task, and finish the task.

```bash
cat > m1.yaml <<'YAML'
title: Add a per-client rate limit to the request path
complexity: low
estimated_hours: [2, 4]
verification:
  command: uvx pytest -q tests/test_app.py
  expected: all tests pass
owns:
  - src/app.py
  - tests/test_app.py
  - pytest.ini
YAML
```

```console
$ heddle milestone add --from-file m1.yaml
write [add-rate-limit] revision 6
  next: heddle validate - author the matching '### Milestone m1:' section in the implementation plan — validate enforces the cross-plane id-join

$ heddle milestone advance
milestone advance [add-rate-limit]: promoted milestone m1 to current; revision 7; wrote true
  next: heddle kickoff --feature add-rate-limit - expand tasks for m1 (§4.2 task expansion)

$ heddle task add "Implement limit() in src/app.py"
task add [add-rate-limit] revision 8

$ heddle task done t1
task done [add-rate-limit] revision 9
```

Now run the stored verification command through Heddle. It records the exit
code, the log and a manifest of the owned files' content.

```console
$ heddle verify --scope m1
verify [add-rate-limit] revision 10

$ heddle status
add-rate-limit — stage implement (authorized through implement)
  milestone: m1
  guidance (read-only): stage-work; briefing: heddle kickoff --feature add-rate-limit
  close obligation (absent): (none); source: .heddle.yaml:autopilot.test_command; runs at: feature complete
  blocking:  awaiting-human-authorization
  next: heddle milestone advance --feature add-rate-limit — advance the converged milestone
  review milestone-review/m1/None: missing, settled, current
  verification m1: fresh

$ heddle milestone advance
milestone advance [add-rate-limit]: completed current milestone m1; revision 11; wrote true
  next: heddle phase-exit --expect-revision 11 --feature add-rate-limit - resolve blocking condition: awaiting-human-authorization
```

The milestone is complete and its proof is fresh. Now change an owned file, as
an agent would when it keeps editing after the tests ran:

```console
$ printf '\n\ndef limit(client):\n    return True\n' >> src/app.py

$ heddle status
add-rate-limit — stage implement (authorized through implement)
  milestone: (none)
  guidance (read-only): stage-work; briefing: heddle kickoff --feature add-rate-limit
  close obligation (absent): (none); source: .heddle.yaml:autopilot.test_command; runs at: feature complete
  blocking:  verification-missing, awaiting-human-authorization
  next: heddle verify --scope m1 --expect-revision 11 --feature add-rate-limit — refresh m1 verification (content-stale)
  verification m1: content-stale; rerun: heddle verify --scope m1 --expect-revision 11 --feature add-rate-limit

$ heddle phase-exit
heddle: error[verification-missing]: verification scope 'm1' is not fresh: content-stale
  hint: rerun every listed verification action and retry the transition
  next: heddle verify --scope m1 --expect-revision 11 --feature add-rate-limit - refresh m1 verification (content-stale)
```

Nothing was edited in Heddle's records. The runtime compared the owned files
with the manifest from the recorded run, found `src/app.py` changed, and
refused the stage boundary. Git was not consulted; an uncommitted edit stales
proof just as a committed one does. Refresh the proof and the boundary opens:

```console
$ heddle verify --scope m1
verify [add-rate-limit] revision 12

$ heddle phase-exit --dry-run
phase-exit preview [add-rate-limit]: stage implement -> peer-review; authorization implement -> peer-review; revision 12 (projected 13); would write true
```

The same rule applies to the feature-wide `acceptance`, `smoke` and `live`
scopes at the boundaries that require them, and to review dispositions whose
cited evidence changes.

## 7. What was written

Heddle wrote its records under the plans and intake directories and an active
feature pointer under `.git/heddle/`:

```text
plans/add-rate-limit/
  brief.md                  authored: the feature brief scaffold
  plan.md                   authored: the implementation plan scaffold
  state.yaml                runtime-owned: every recorded fact
  verification/
    m1-<timestamp>-r10-<hash>.log
    m1-<timestamp>-r12-<hash>.log
    source-evidence.<hash>.yaml
.heddle/intake/add-rate-limit.yaml
```

Never edit the state file by hand; every fact in it has a command. The brief
and plan are yours to author. Whether workflow records are committed is the
host's decision; Heddle's own repository keeps them local, as described in the
[local records policy](workflow/local-records.md).

## 8. Clean up

```bash
cd .. && rm -rf heddle-tour
uv tool uninstall heddle   # only if you installed it
```

## Where to go next

- With a real agent, the managed region in `AGENTS.md` sends it to
  `heddle orient`; `heddle kickoff` gives it the stage briefing; it authors the
  spec and plan and records tasks, decisions and sessions natively. The
  [workflow reference](workflow/workflow.md) explains each stage.
- With reviews on, `heddle run-gate` and `heddle run-gates` invoke the confirmed
  Codex or Claude Code reviewers, retain their findings and route the lead
  through evidence-bound dispositions. The
  [workflow model](design/workflow-model.md) states the closure rules.
- `heddle drive` executes the next legal actions until it reaches a decision, a
  blocker or the human completion handoff.
- Adopting a real repository, including existing `AGENTS.md` content and
  principles, follows the [host-integration guide](workflow/host-integration.md).
- If you are weighing Heddle against Spec Kit or a similar toolkit, read
  [how Heddle differs](comparison.md).
