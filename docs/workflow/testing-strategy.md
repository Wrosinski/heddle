# Testing strategy

Status: active repository testing policy. The formal runner, execution bands,
authority checks, and root configuration apply now. This document separates fast
feedback, hermetic toolchain checks, installed end-to-end workflows, and live
provider work. Current capabilities and pending qualification are stated here;
private delivery records are not required to apply the policy.

## Test-class time boundaries

These are starting design ceilings for a documented development reference
environment. They are chosen for useful iteration speed, not extrapolated from
an unrelated host's timings. The owner approved this planning direction on 2026-09-12 and extended E2E/live
ceilings to 60 minutes. These are design requirements, not measured performance
claims or newly implemented automatic timeouts.
They classify execution responsibilities, not Python classes or new pytest bands.

| Test class | Per-case lifecycle ceiling | Complete focused selection ceiling | Intended cadence |
| --- | ---: | ---: | --- |
| Pure core behavior | 100 ms | 5 s | Relevant edit/fix |
| Component or boundary integration | 1 s | 10 s | Relevant edit/fix |
| Fast feature acceptance | 1 s | 15 s | Feature behavior change; completion of the affected slice |
| Necessary slower hermetic/toolchain proof | 10 s | 60 s | When its distinct contract changes; outside the default fast selection |
| Local feature E2E | 60 min per journey | 60 min per selected feature lane | Completed implementation/final proof with explicit grant |
| Live provider/service proof | 60 min per case | 60 min per selected feature lane | Completed implementation/final proof with explicit grant and prerequisites |

The combined ordinary test loop should finish within **30 seconds**, and the
local test/lint/hook feedback path should target **60 seconds**. Individual
selections are not an instruction to run every row after every edit. Broad suites
have a separately declared budget and require their own execution grant.

E2E/live ceilings apply to both one journey and the complete selected lane, not
60 minutes multiplied by its cases. Declare a shorter deadline where practical.
A time allowance never grants execution. A timeout is failed/incomplete proof.

Include all lifecycle and selected-command costs, including children and shared
preparation. Use one worker, disclose cold/warm conditions and environment, and
reuse observations from authorized work. An unavoidable overrun needs an exact
target, required property, observed cost, finite alternative ceiling, owner and
review point in the existing plan/map. Existing overruns are migration backlog;
new fast acceptance must meet its budget or carry that explicit exception.
Qualification begins with review and bounded timing evidence; automated deadline
enforcement is deferred and must not use stopwatch assertions in correctness tests.

## Design for fast feedback first

The ordinary edit-test loop should finish in seconds. Choose a feedback budget
and the important defect signals before designing tests. A suite that is cheap
to iterate with is a design requirement, not only a later optimization project.
Measure the complete selected invocation, including startup, collection, setup,
child commands and teardown; a cheap test body does not establish a fast loop.

Prioritize core behavior and uncertain, complex or risky parts. Use representative
success, boundary and consequential failure cases. Add test depth where it resolves
uncertainty or protects a significant failure consequence; do not pursue exhaustive
coverage, a test per helper, or Cartesian combinations merely because they can be
generated cheaply. Each extra parameter dimension needs a distinct defect rationale.
No fixed case count or coverage percentage establishes adequacy.

Tests must be self-contained across fresh workspaces. Author synthetic evidence
in test fixtures or create it in disposable hosts. Do not read this repository's
prior feature workspaces, provider responses, run logs, or completion archives
as regression inputs. Installation and host adoption start with the adopter's
own state.

Ordinary feature acceptance tests must also be designed for fast feedback. Exercise
user-observable behavior through the real internal boundary with deterministic
fixtures and controlled external dependencies. Keep actual installation, full
lifecycle and provider/service proof in separately selectable E2E/toolchain/live
lanes. Their small set of integration witnesses complements fast acceptance;
it does not repeat the local behavior matrix.

An AC can require both fast acceptance and E2E/live evidence. Existing required
journeys remain required even when their execution is scheduled separately.
The native acceptance verification scope and historical `acceptance` marker are
not automatically identical to the new fast acceptance test class. Reconcile
commands, required scopes and `Verified-by:` before changing any proof route.

The [time-boundary contract](#test-class-time-boundaries) defines per-case and
complete-selection ceilings, reference conditions and an exception procedure.
Those design requirements are not current performance claims, pytest options or
enforced timeouts. Diagnose and redesign expensive fast
tests before considering a documented separate-lane exception; relabeling alone
does not improve their cost or discharge their proof obligation.

## Start here before selecting tests

1. Identify the changed behavior, acceptance criteria (ACs), public contracts,
   and directly affected consumers. Consult their `Verified-by:` references and
   the [selection map](../testing/test-selection-map.md).
2. Start with shallow inspection of the actual assertions, directly used helpers,
   ancestor/autouse fixtures and configuration. Check for wheel builds, provider
   calls, real workflows, sleeps, and nested verification. Deepen when uncertainty
   changes selection, confidence or execution safety; ordinary edits do not need
   a transitive dependency graph.
3. Choose the smallest existing files or nodes that prove the affected behavior.
   Include relevant slow cases when their distinct failure mode matters.
   Select a known slow-only witness through its appropriate focused lane directly;
   an empty fast run is not a prerequisite.
4. Check execution authority separately from relevance. Full workflows and live
   dependencies require explicit authorization even when the tests are local.
5. Run the selected checks; after a fix, rerun failed and affected checks. Record
   what passed, what did not run, and what remains required.

A selection map is a starting point, not an exhaustive dependency oracle.
Missing or stale mappings require inspection. Do not infer that no tests apply,
or automatically substitute a repository-wide run.

## Current capabilities and transition

The original audited baseline was Heddle
`3c87e3c5611fe1128d13b0d5ba957ed382ebba18`. On 2026-09-12, Wave 2 M3
activated `tests/pytest_policy.py` through root `tests/conftest.py`, registered
the band markers and strict pytest configuration, migrated the recorded
classifications and retired the environment-only live guards. Plain pytest now
selects the fast band. E2E and live nodes refuse before fixtures unless their
exact direct permissions are present.

For a directly relevant, inspected hermetic target, use the formal entry:

```bash
.venv/bin/python -m tests.proof_runner <exact-file.py-or-node-id>
```

Replace the placeholder with existing exact files or nodes. The launcher requires
every requested witness to complete and pass. It refuses inherited permission
flags and configuration overrides that could reshape the witness set. An ad hoc
invocation does not replace required native `heddle verify` facts.

| Capability | Status at publication | Delivery owner |
| --- | --- | --- |
| Focused selection and explicit broad/e2e/live execution policy | Approved guidance | This document |
| Initial contract-to-test map | Available; bounded and manually maintained | Selection map / Wave 1 |
| Packaged prompt and briefing propagation | Implemented with budget-first selection, fast acceptance and scoped-exception duties; local rendering and contract checks do not establish provider behavior quality | Strategy and selection map |
| Execution bands, independent permission checks, strict proof mode | Active at the repository root; final review, native acceptance and exact installed AC-9 proof complete (`4376ed10`) | Wave 2 |
| Named shared-helper environment, executable and cleanup hardening | Deferred during simplification; named paths only, no suite-wide guarantee | Wave 3 |
| Feedback budgets and risk-based fast acceptance | Packaged W1-R guidance and numerical ceilings apply; existing-test migrations pending | Strategy / migration outline |
| Existing feedback-loop qualification | Deferred during simplification; reuse recipes, correction only if needed and selected | Wave 4 |
| Packaged strategy/map starter and maintenance examples | Deferred during simplification; manual adoption and core qualification, no init/lock automation | Wave 5 |
| Qualified worker configuration | Optional, on hold after the measurement checkpoint | E1 (former worker Wave 5) |

Update this table with actual commits and evidence as mechanisms ship. Never
advertise an unimplemented flag as a runnable instruction. The repository's
configured health command is an exact strict-proof selection. Its presence does
not grant broader, E2E or live execution.

## Execution authority and cadence

Narrow relevant hermetic tests, configured lint, and applicable pre-commit
checks are normal authorized implementation work. A related slow test may run
when it is necessary to verify the changed contract. Explain the selection
briefly; no fixed number of tests establishes adequacy.

Full-repository and broad cross-feature execution requires an explicit user
request covering that scope. Milestone exit, phase exit, review, pre-merge,
completion, a populated plan command, and a general instruction to finish do
not independently supply that request. Existing explicit authorization remains
valid within its stated scope; do not repeatedly ask for it.

Both local full-workflow e2e and real external tests require explicit execution
authority and exact file/node targets. Normal scheduling is after implementation
is complete, at final verification or post-hardening. Earlier execution is valid
only when specifically requested, for example a bounded diagnostic. Scaffolding
may design and safely collect these tests without running them. Prerequisites,
credentials, a marker, or an environment variable are not human authorization.

For live execution, establish the provider/service, targets, expected cost,
timeout, allowed effects, credential source, and evidence destination. Preserve
the applicable workflow approval and retry limits. A failed run does not imply
unbounded paid retries. Local e2e still needs its execution grant, but does not
need invented credential or provider requirements.

Inspect nested subprocesses and verification helpers. A narrow wrapper around
a broad suite has the broad suite's cost and execution scope. Collection is not
execution evidence; imports and collection hooks must not launch external work.

| Work stage | Test activity |
| --- | --- |
| Research and specification | Inspect existing contracts/tests and identify proof obligations; no broad baseline by default |
| Plan review and scaffolding | Declare exact selections, meaningful red/green expectations, costs, fixtures, and separately authorized e2e/live proof |
| Task implementation | Run affected behavior and direct-consumer checks; include related slow cases for a concrete reason |
| Milestone boundary | Execute the applicable recorded milestone proof within its authority; preserve required unrun obligations |
| Review and remediation | Reuse applicable evidence; rerun affected checks after fixes; reviewers retain their role limits |
| Final verification / robustness | Complete required acceptance and authorized e2e/live proof against current relevant content |
| Completion | Account for all required evidence and explicit dispositions; do not add an automatic broad run |

If a required scope cannot yet run, keep its proof claim pending and continue
independent safe work. Optional broad regression that was not requested is a
reported limitation, not a new completion gate. An actual required AC or gate
cannot be waived by selecting fewer tests or changing this guide.

## Structure and classification

Keep the architecture-based ownership layout:

| Area | Primary responsibility |
| --- | --- |
| `tests/kernel/` | Deterministic policy, state interpretation, contract decisions, and core invariants |
| `tests/runtime/` | Application operations, CLI behavior, persistence, verification, installation and lifecycle boundaries |
| `tests/gate/` | Prompt construction, review parsing, runner contracts, monitoring and provider adapters |
| `tests/driver/` | Supervised execution, authorization, readiness, session behavior and provider-session doubles |
| `tests/guardrails/` | Repository and packaged-hook contracts, instruction integrity and development tooling |
| `tests/live/` | Existing externally executed and local workflow tests; directory membership is not the permission authority |
| `tests/fixtures/` and support modules | Representative input corpora and reusable test setup with explicit mutation ownership |

Within an area, separate files by observable contract and infrastructure when
that makes selection and setup cheaper. Do not impose empty unit/integration/
acceptance/e2e directories on every feature or relocate the whole suite.

Keep these independent:

- **Purpose:** behavior, integration, acceptance, installation/toolchain, or a
  complete user journey. `acceptance` means an AC witness; ordinary acceptance
  tests additionally have the fast-feedback design requirement above.
- **Effects:** isolated local resources versus real providers/services.
- **Cost:** collection/import, setup, test call, teardown, and total invocation.

The registered marker vocabulary adds `e2e`, `slow`, and `toolchain` to the existing
`live` and `acceptance` concepts. Milestone markers may remain for historical
discovery, but must not decide execution permission or be copied into every new
feature. Retire obsolete markers only with their reference migration.

- `e2e`: a complete user workflow through the real application boundary,
  including local installed-CLI journeys with controlled providers.
- `live`: real external dependencies, including paid providers. A live adapter
  probe need not be e2e; a live workflow has both markers.
- `toolchain`: build/install/hook infrastructure execution. A simple test of a
  command builder is not toolchain merely because its output describes a build.
- `slow`: a measured cost classification with a reason and review point; never
  a way to hide a newly introduced regression.
- `acceptance`: orthogonal AC coverage. It grants no execution permission.

A single local subprocess checking parsing is not automatically e2e. A complete
feature lifecycle through the CLI is. Classify by what executes, including
helpers, rather than filename, mock count, directory, or acceptance label.

## Runner contract

These Wave 2 semantics are active through the root pytest integration and the
formal proof entry.

| Band | Eligibility predicate | Permission |
| --- | --- | --- |
| `fast` (default) | not slow, toolchain, e2e, or live | Focused ordinary work |
| `routine` | not toolchain, e2e, or live; includes slow | Focused relevant work |
| `toolchain` | toolchain; excludes both e2e and live | Explicit infrastructure selection within task authority |
| `e2e` | e2e and not live; includes any slow/toolchain members | `--allow-e2e` plus human authority |
| `live` | live, with any other classification | `--allow-live`; also `--allow-e2e` for e2e members |

The implementation owns these predicates once. `-m` and `-k` refine a band; they
cannot broaden it or grant permission. Plain pytest excludes e2e/live. An exact
band-ineligible node produces a nonzero zero-selection result. E2e/live execution
also rejects missing direct permission or non-exact targets before fixtures. An
ordinary mixed-file selection may exclude ineligible cases, but must report that
fact; zero selected tests is never a pass.

Invocation forms:

```bash
.venv/bin/python -m pytest --test-band=fast <exact-file.py-or-node-id>
.venv/bin/python -m pytest --test-band=routine <exact-file.py-or-node-id>
.venv/bin/python -m pytest --test-band=toolchain <exact-file.py-or-node-id>
.venv/bin/python -m pytest --test-band=e2e --allow-e2e <exact-file.py-or-node-id>
.venv/bin/python -m pytest --test-band=live --allow-live --allow-e2e <exact-file.py-or-node-id>
```

Permission flags must come from the current caller's invocation, not inherited
environment or pytest configuration. E2e/live execution requires explicit Python
files or nodes, not a root or directory selection. Intentionally disabling the
project plugin is unsupported; these controls protect cooperative repository
execution, not arbitrary hostile Python code.

Formal pytest proof uses `python -m tests.proof_runner` with exact targets. The
launcher enables strict mode and requires positive completed proof as well as a
zero pytest exit. The direct-pytest `--strict-proof` flag alone is not the formal
entry. Omitted requested witnesses, skips, xfails, collection errors and incomplete
execution cannot satisfy proof. Ordinary exploratory runs may report legitimate
skips without claiming the skipped property was proved.

## Formal verification and evidence

Heddle verification scopes and pytest bands are different concepts. Scopes name
workflow obligations (`m<N>`, feature, smoke, acceptance, live); bands select
eligible tests. A local e2e acceptance witness can therefore require the e2e
band while remaining part of an acceptance obligation. Do not rename runtime
scopes merely to match pytest terminology.

Configure exact commands through `heddle commands set` and milestone operations;
never edit `state.yaml` directly. Preview an unfamiliar recorded command with
`heddle verify --scope <scope> --dry-run` where available and inspect any nested
launches. Required execution and provenance are recorded by `heddle verify`.
An ad hoc focused pytest pass does not replace a broader required fact.

Before changing band defaults, map every required witness to a command that
still executes it. Exact target lists and strict proof checks prevent default
exclusions from silently converting incomplete acceptance into a green exit.
The reviewer must still establish selection adequacy: a runner cannot infer a
missing AC from an incorrectly authored target list.

Record in the existing plan/session verification note:

| Field | Contents |
| --- | --- |
| Obligation | Changed behavior, AC, and relevant consumer |
| Command | Exact selection, band, permissions, and nested scope |
| Cost | Expected setup/time/resources, or explicitly unknown |
| Outcome | Selected/executed/passed/failed/skipped counts and actual exit |
| Applicability | Relevant source and environment, linked native fact where required |
| Unrun | Required pending proof separately from optional unrequested regression |

Reuse evidence only while Heddle's relevant-content and command identity rules
hold. A new Git commit alone does not invalidate unchanged relevant content;
an informal assertion of equivalence does not override stale native provenance.
Retain failed/interrupted evidence as such. Do not create another receipt
authority or persistent result cache for this program.

## Designing useful tests

1. Name the observable defect each case should detect. Assert independent
   expected behavior, not the same value or algorithm used in setup.
2. Select representative core values, boundaries and consequential failures in
   the pure layer. Spend additional depth on uncertainty and risk rather than
   exhaustive enumeration. Prove wiring, persistence, translation, installation
   and complete journeys at their real boundaries with a small purposeful set.
3. Map every AC to evidence without duplicating every parameter combination at
   every layer. Keep interacting combinations when a named contract needs them;
   require a distinct defect rationale for additional matrix dimensions.
4. Use structural assertions for structural contracts. Preserve real CLI/hook
   entry points where an AC promises their behavior. Mock external boundaries;
   avoid mocking away the internal composition being verified.
5. Keep optional imports and expensive fixtures local to their consumers.
   Shared immutable preparation is useful; mutable state needs isolated lifetime
   and explicit restoration. Avoid suite-wide expensive autouse fixtures.
6. Use support modules for shared helpers. Do not import private helpers from
   other test modules or depend on another test running first.
7. Use events/barriers and event-anchored clocks for controlled timing. Keep
   real scheduling, locking, cancellation, process-group cleanup, and recovery
   tests when these effects are the property being proved.
8. Tests must clean up their processes, file descriptors, temporary workspaces,
   and environment changes on success and failure. Do not share real user homes,
   provider authentication, or mutable repository fixtures.
9. Keep ordinary behavior tests from launching broad pytest or all-file tooling.
   Required repository proof remains explicit and source-bound; deleting its
   wrapper requires migration of the actual coverage obligation.
10. Separate product-performance contracts from suite-performance measurement.
    Existing contractual timing ceilings remain until their owning contract is
    changed. New fragile host-speed assertions are not a suite management tool.

When moving, splitting, reducing, or renaming tests, update `Verified-by:`, exact
plan commands, mappings, required node identities, and structural guards in the
same coherent migration. Preserve historical verification artifacts. Keep
mechanical moves separate from behavior changes in Git commits.

## Long-term maintenance

The original activation census proves lossless classification at its recorded
revision; it is not a permanent suite-size contract. Preserve historical inventory
and results while evolving current collection checks to require complete band
coverage and correct classification of the current suite. Strict proof still
requires every requested case to execute and pass. Simplification's approved
legacy deletions need contract dispositions and current-witness replacements
where the invariant survives; no test is retained merely to match an old count.
The explicit reviewed preview/write command maintains the current inventory
without dropping exact added and removed node accounting. Historical evidence
may be retained outside the public package without becoming a current suite-size
contract.

Regenerate the reviewed inventory after an intentional test-corpus change with:

```bash
.venv/bin/python -m tests.guardrails.test_pytest_inventory --write
```

The author changing a contract also updates its selection-map row and test
classification. Reviewers check direct consumers, hidden setup, independent
assertions, and missing proof. Wave handoffs name the contributor responsible
for any outstanding regression or quarantine; do not invent an unassigned
standing team or automatic background job.

Measure representative selected invocations with collection/setup/call/teardown
breakdown, worker count, environment, and fixture/build counts. Compare the same
selection and relevant inputs before and after an optimization. Report per-node
slowest cases and end-to-end latency; a fast test body can hide costly setup.
Choose design budgets from the required feedback loop, then use representative
measurements to qualify compliance, exceptions and improvements. Do not normalize
an already slow baseline into the desired budget. Automated hard enforcement
needs an explicit contract and stable measurement environment; until then,
overruns require design attention and a visible disposition. Changing the
selection is not evidence that unchanged work became faster.

For a flaky test, retain the original failure and record its owner, affected
contract, reproduction command, issue/reason, and review date. Quarantine needs
an explicit disposition and substitute proof for any required obligation; it
does not turn a required test green. Do not automatically retry until a pass
conceals the instability. Skips and xfails follow the same ownership discipline.

Share immutable wheel artifacts where measurement supports it; isolate mutable
installations and workspaces. Parallelize only after isolation is proved. Test
bounded worker counts rather than assuming `-n auto` is optimal; xdist session
fixtures execute once per worker, and process-global state still needs care.
Keep ordering-sensitive or resource-conflicting cases serial with a named reason.

Review mapping misses, quarantine age, duplicate proof launches, setup cost, and
slowest cases during changes to the affected area and at explicitly scheduled
maintenance work. No automatic nightly broad suite, live-provider schedule,
affected-test selector, cross-run evidence cache, or new reporting service is
introduced. Any future automation needs measured benefit and defined authority.

## Principles and provenance

This strategy applies the ratified
[engineering principles](engineering-principles.md): Pure core, effects at the
edges; Everything once, in one place; Structural beats policed; Never
misrepresent; and Core first, scope is earned. It does not amend that document.

Prior test-policy guidance contributed practical mappings, heuristic retagging,
verifier consolidation, and timing guidance. The owner's later fast-feedback
direction strengthens the selective test-design and acceptance requirements.
Preserve Heddle's packaged resources, flat prompt partials, typed scopes, and
runtime-owned evidence.

Implementation references: [pytest marker/options support](https://docs.pytest.org/en/stable/example/markers.html),
[pytest invocation and durations](https://docs.pytest.org/en/stable/how-to/usage.html),
[pytest-socket](https://github.com/miketheman/pytest-socket), and
[xdist fixture/process behavior](https://pytest-xdist.readthedocs.io/en/stable/how-to.html).
Recheck tool compatibility against installed versions when the owning wave starts.
