# Scaffold

You are at the **scaffold** stage. Build the verification system that makes the
implementation target observable before production code changes: milestone
tests, integrated acceptance coverage, a live lane where applicable, and a
fast health check. Tests come before implementation so every later milestone
has a concrete feedback loop — a failing test is a compass, not retroactive
confirmation. Preserve useful pre-existing behavior with survivor pins; new or
changed behavior must have a discriminator that is red for the intended
reason.

## Authority and sources

This session may edit tests, the Feature Spec's `Verified-by:` routes, the
feature plan, milestone verification payloads, and runtime command facts via
`heddle commands set`. It may not hand-edit `state.yaml`, implement production
behavior, advance the phase, or silently authorize cost, credentials, or new
scope. Record user-owned choices with `heddle decisions add`.

## Orientation

Ground the scaffold in the repository before writing tests:

1. Read `heddle status --json`, the Feature Spec's canonical AC set and relevant
   Design Commitments, and the plan's Technical Architecture, verification/
   environment context, and milestones.
2. Confirm every AC ID appears in some milestone's `satisfies` entry. Repair
   missing assignments with `heddle milestone edit` — reading the AC and each
   milestone's scope for best fit — before writing that AC's tests; an
   unassigned AC would never be implemented or verified.
3. Read the interfaces the feature touches as they exist in code — real
   types, signatures, and module boundaries, with dependencies traced in both
   directions — not as the spec paraphrases them, so tests pin the actual
   contract. Delegate independent surveys when authorized tools support them
   and useful local work can continue; each brief identifies its sources and
   remaining gaps. A wide surface alone does not require delegation.
4. Survey the existing test infrastructure (conftest fixtures, markers,
   helpers) and the test approaches of related features in the same area:
   structure conventions, shared fixtures, mock strategy for common external
   services, acceptance and live test designs. Reuse the repository's testing
   idioms instead of creating a parallel harness.

## Verification design

Use existing host conventions, its chosen time budget and a selection map where
available. At the cheapest adequate layer select core behavior, uncertain,
complex or risky interactions, and consequential failures. Preserve a fast
implementation route and a fast public-boundary acceptance route. Group by
contract ownership and needed infrastructure; inspect shared fixtures and their
first-order consumers, then keep cheap behavior, related slow cases, toolchain
and local e2e/live workflows independently selectable. Map every AC without
repeating each parameter combination at every layer. Inspect fixture setup and
nested launches before estimating lifecycle and total command cost. Required
proof beyond budget needs a measured, scoped exception with a cause, owner and
review point; it does not disappear.

All execution below follows explicit scope authority. Broad suites and both
local full-workflow e2e and external tests require a request covering the exact
scope; a plan command, phase grant or confirmed prerequisite is not permission.
Design and safely collect e2e/live tests here; normally execute them only after
implementation or at final verification, unless earlier execution is specifically
requested. Existing applicable grants persist. Required unrun proof stays pending.

Create and record all applicable lanes:

1. **Milestone tests.** For each milestone's `satisfies` ACs, write tests
   that verify the specified observable behavior — not internal
   implementation details, which break on refactor without proving anything.
   Keep tests in their owning area with independently selectable infrastructure
   groups, reference ACs by ID, and populate each AC's `Verified-by:` route in
   the Feature Spec as you pin it. Write executable tests against consumer promises, the plan's
   reviewed interfaces, and actual code contracts. Delegated helper names or
   algorithms are not a missing product requirement. Where a concrete test
   still depends on a later interface, retain an explicit activation seam and
   owner; the implement session completes it before claiming that AC verified.
2. **Integrated acceptance test.** Define one command that answers "does the
   composed feature work end-to-end": it crosses the feature's internal
   module boundaries, covers every AC, mocks only true external systems, and
   asserts AC-granular observable outcomes. When its success criteria are not
   already approved, record them as a user-owned decision whose payload
   names: the end-to-end flow exercised, which assertions cover which AC IDs,
   the conditions under which the test counts as passing, and what is mocked
   versus real.
3. **Live E2E lane.** Where the feature integrates with external services or
   real data, isolate a no-mock test behind a dedicated marker, never in the
   default suite or CI. It repeats the acceptance flow against real
   dependencies with representative data and must be idempotent. Enumerate
   every prerequisite and classify each as auto-resolvable (this session or
   the existing dev setup provides it) or user-required: credentials, network
   reach, data availability and freshness windows, estimated cost and quota,
   side effects, environment variables. Route user-required prerequisites
   through `heddle decisions add`; a prerequisite the user defers needs a
   recorded fallback (cached session, smaller data subset, named ACs excluded
   from live coverage) noted in the plan.
4. **Smoke test.** Provide a fast session-start check proving the repository
   is healthy enough for new work. It exercises pre-existing behavior only —
   run it now and keep it green. Classify any red result as feature-caused,
   unattributed, or proven outside-owned. Feature-caused and unattributed red
   block new work. A proven outside-owned baseline red may allow continued
   in-scope work only when the exact command and output or log are pinned,
   evidence proves the failure predates the feature and lies outside its full
   ownership union, and the result stays red and unresolved in the session
   record. Degraded close additionally requires green acceptance, green
   affected owned-consumer tests, and explicit current user acceptance naming
   the exact red evidence and attribution; preserve that decision in the
   Feature Spec and decision journal. This exception never makes smoke green.
5. **AC coverage matrix.** Add a table to the plan showing, for every AC,
   which lanes prove it:

   | AC ID | Milestone test route            | Acceptance | Live               | Notes |
   | ----- | ------------------------------ | ---------- | ------------------ | ----- |
   | AC-1  | `tests/.../test_x.py::test_ac1` | covered    | deferred: fallback |       |

   Mark each lane cell covered, deferred (name the recorded fallback), or not
   applicable (state why). The milestone column must agree with the spec's
   `Verified-by:` routes. A blank cell requires a reason.

Create supporting infrastructure as the lanes need it: fixtures, mock
services, seed data, conftest entries.

## Command facts

Record concise contract evidence in the existing `### Verification Commands`,
`### Live E2E Test Prerequisites`, `## Technical Architecture`, or `## AC Coverage
Matrix` sections so the scaffold reviewer can assess tests without reading
production source. For each affected consumer promise, name the actual public
help, manifest, type, or interface inspected; the fixture preconditions needed
to reach it; and the expected red assertion with its observed failure cause.
Cite an existing retained log when one carries the observation. Label evidence
as inspection, collection, or execution: collection is not proof that fixture
setup reached the assertion, and a broken pre-existing fixture is not the
intended red discriminator.

When installed lifecycle behavior changes, include the minimum close skeleton
needed by the fixture: state birth, required milestone and final proof,
frontmatter, close result, and retained evidence. When cancellation behavior
changes, make the double preserve the real worker, monitor, provider-session,
and parent-publication topology. These conditional duties do not authorize an
installed, full-workflow, or provider run; keep required unrun proof pending.

For each command, record its AC/contract selection, direct consumers, related
slow cases, expected setup/execution cost and execution stage. Distinguish
required pending proof from optional broad regression. A focused passing command
must not replace a required witness excluded by the host's runner defaults.

Use the plan's `### Verification Commands` and `### Live E2E Test Prerequisites`
sections for references, expected summaries, failure hints, and non-obvious
setup. Native command facts are authoritative; do not maintain a second command
register in prose. Store commands without editing YAML directly:

```bash
heddle commands set smoke_test --command "<exact smoke command>"
heddle commands set test_command --command "<exact progressive-regression command>"
heddle commands set acceptance_test --command "<exact acceptance command>"
heddle commands set live_e2e_test --command "<exact live command>"
heddle commands set lint_command --command "<exact lint command>"
```

Omit only a genuinely inapplicable command and explain the omission in the
plan. Update milestone verification through `heddle milestone edit`.

Keep the command roles distinct:

- `smoke_test` is an always-green, fast check of pre-existing repository
  health.
- `test_command` is an always-green progressive regression selection covering
  survivor pins plus current and completed milestone behavior. Future-red
  discriminators belong to their milestone verification, acceptance, or live
  scopes until their activation seam is reached.
- Milestone verification drives that milestone's discriminators red-to-green;
  acceptance proves the composed feature; live repeats the same core flow
  against approved real dependencies.

Progressive `test_command` output and session prose are feedback; only
`heddle verify --scope <scope>` records native proof.

Host tooling owns `.heddle.yaml`'s `autopilot.test_command`, which configures
the additional close suite run by `heddle feature complete`. It does not replace
feature proof, and `feature complete` does not create a clean environment; it
runs the command in the current host checkout.

Design failure output for the next model: keep stdout concise, make failures
grep-friendly with `FAIL` or `ERROR` beside the cause, summarize counts, give
an actionable first diagnostic, and provide a fast selection for large suites.

## Red phase validation and survivor pins

Classify each scaffolded test:

- **Red discriminator** — covers behavior introduced or changed by this
  feature. Before implementation it must FAIL or ERROR because the named
  behavior is absent, not because imports, fixtures, or infrastructure are
  accidentally broken.
- **Survivor pin** — proves behavior that already works and must remain stable.
  It must pass before implementation and be visibly labelled.

A test that passes regardless of feature state gives the implement session a
false progress signal, so apply passing test triage when a supposed
discriminator passes — trace what it actually depends on:

- It targets feature behavior that does not exist yet: rewrite the assertion
  to depend on the missing behavior.
- It asserts on mock wiring, a hardcoded value, or a constant: the test is
  tautological — rewrite it against behavior that requires implementation.
- It proves pre-existing behavior the planned change puts at risk:
  reclassify it as a labelled survivor pin. If the behavior is not at risk,
  it does not belong in this feature's scaffold.

Skips are limited to a documented later-milestone activation seam and must
have an owner for removal. An `xfail` marker is not an exemption: it counts
as red evidence only when strict and failing for the intended
missing-behavior reason. The packaged hook makes the mechanical check that
the test contains an executable non-constant assertion. The scaffolding
reviewer makes the semantic check that the assertion represents the AC and
fails for its expected discriminator. Once the behavior is green, strict
pytest semantics expose the retained marker as XPASS; remove it so the test
becomes an ordinary pass.

Run the relevant authorized smoke, progressive, milestone, and acceptance
selections after scaffold adjustments. Inspect nested launches first; a planned
command or available prerequisite grants no broad, full-workflow, or real-provider
execution authority. Keep required unrun proof pending. Record exact commands, date, red count, survivor
count, skip count, and the failure reasons of red discriminators. Check that
fixture setup reaches the intended assertion and that deliberately breaking the
target contract makes the relevant test fail.

## Gate and completion evidence

Follow the confirmed `review-test-scaffolding` assignment and emitted native
next actions. Run selected calls through `heddle run-gate`; preserve independent
initial contexts and read all required initial reports before remediation.
Off means intentionally not run, not passed. Native evidence-bound lead
dispositions own closure; there is no synthesis gate. Preserve exact original
finding/decision references, coverage gaps, and originating inspection duties.
Use the remaining round allowance and explicit stop/budget decisions, not a
hardcoded tier sequence or a new lower cap.
At a reached confirmed cap, `heddle review allowance` can raise an owner-approved
absolute quality round limit. It invokes no provider and does not resolve a stop
or open a round; follow the separate native actions in their emitted order.

The stage is ready for **implement** when the AC coverage matrix is complete,
approved/deferred acceptance and live decisions are recorded, smoke is green,
every changed-behavior AC has a correctly failing discriminator, survivor pins
are green, command facts are executable, and required review assignments are
closed. A coverage design or authorized test is not evidence that it ran.
Phase exit remains driver- or user-owned.
