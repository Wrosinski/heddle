# Contract-to-test selection map

Status: bounded, manually maintained map of current representative witnesses.
This map is deliberately not exhaustive.

Use the [testing strategy](../workflow/testing-strategy.md) before execution.
Select the row matching the changed contract, then read test definitions,
fixtures, and direct consumers. A row is a discovery route, not a command to
run every listed file. These are the Wave 1 current-only routes; later activation
records retain historical paths as evidence rather than executable targets.

| Changed contract | Start with | Inspect direct consumers / cost boundary |
| --- | --- | --- |
| Project configuration and layout | [kernel config](../../tests/kernel/test_project_config.py), [kernel boundaries](../../tests/kernel/test_boundaries.py) | Runtime config validation and installed discovery; acceptance labels do not establish relevance |
| Runtime-to-gate import boundaries | [canonical runtime boundary](../../tests/runtime/test_boundaries.py), [normal-selection binding](../../tests/runtime/test_schemas.py) | Select the canonical boundary node whenever a change alters imports from `heddle.runtime` to gate, IO, driver, or third-party modules. The repository's normal and autopilot verification commands include this node; update the shared edge policy in `policy_helpers.py` only with the owning contract change. |
| State/model decisions and completion | [current v8 policy/state](../../tests/kernel/test_tiering_policy.py), [completion boundary](../../tests/runtime/test_tiering_completion.py), [completion repairs](../../tests/runtime/test_completion_repairs.py) | Runtime completion and driver completion; the [installed repair journey](../../tests/runtime/test_completion_repairs_installed.py) and other installed journeys are opt-in e2e |
| Review policy and registry | [gate policy](../../tests/gate/test_gate_execution_transport.py), [current role codecs](../../tests/gate/test_tiering_roles.py) | Runtime policy resolution and controlled adapters; pure policy checks launch no provider |
| Confirmed feature policy and qualified review closure | [pure policy and v8 state](../../tests/kernel/test_tiering_policy.py), [qualified closure](../../tests/kernel/test_review_closure.py) | Hermetic contracts/kernel tests: closed wire vocabulary, override preservation, round/call arithmetic, originating obligations and evidence-based stops. Inspect prior-format state/model and review-accounting consumers. Runtime admission and installed journeys are separate downstream proof. |
| Confirmed intake, current role execution and native dispositions | [intake/admission](../../tests/runtime/test_tiering_intake.py), [versioned role codecs](../../tests/gate/test_tiering_roles.py), [native review integration](../../tests/runtime/test_tiering_dispositions.py) | In-process temporary hosts with real recording, policy-selected adapters and provider transport doubles. Covers create-only recovery, owner/CAS checks, exact original refs, rounds, capabilities and read-only driver projections. Include the exact Low/High milestone node in `tests/runtime/test_document_altitude.py` and relevant historical milestone nodes. Structural imports/write seams are in `tests/runtime/test_boundaries.py` and `test_policy_locks.py`. Installed journeys and real-provider prompt qualification remain separately authorized proof. |
| CLI routing and envelopes | [dispatch](../../tests/runtime/test_dispatch.py), [contracts](../../tests/runtime/test_contracts.py), [schemas](../../tests/runtime/test_schemas.py) | Direct application operations, driver callers, cold subprocess imports |
| Readiness assessment and typed action routing | [typed action scenarios](../../tests/runtime/test_action_contracts.py), [evidence explanations](../../tests/runtime/test_evidence_explanations.py), [action-routing acceptance](../../tests/runtime/test_action_routing_acceptance.py) | Select the exact assessment, wire, driver, freshness, or explanation node first. Controlled provider doubles remain hermetic; the installed lifecycle and the declared one-node controlled-provider E2E witness retain their separate execution authority. |
| Truthful phase and milestone transitions | [focused acceptance](../../tests/runtime/test_truthful_transitions.py), [installed journeys](../../tests/runtime/test_truthful_transitions_installed.py) | Select focused cases for Off-robustness qualification, human exit guidance with the driver still awaiting authorization, retained duties, preview/apply/no-op attribution, unrelated command preview compatibility, post-write readback races and complete-stage driver handoff. Stale-proof refusal covers both pending and already-granted boundary authorization. Add affected readiness, revision, receipt, help and import-boundary survivors. The two installed candidate-wheel journeys are explicit E2E and retain separate exact execution authority. |
| Native verification-command removal | [command removal](../../tests/runtime/test_commands_unset.py), [command setting](../../tests/runtime/test_commands_set.py), the `commands unset` cases in `test_contracts.py` | Real current-state CLI/typed writes, dry-run, exact selected-key delta, absent/blank behavior, stale/concurrent CAS, historical/accepted refusal, retained logs and mandatory acceptance/smoke after removal. Terminal fixtures run only their tiny local assertion commands, not a full workflow/provider. D18 separately authorizes archived-v6 installed component qualification and supervisor adoption; never use the moving candidate as operational supervisor. |
| Content identity and readiness | [kernel identity](../../tests/kernel/test_content_identity.py), [current completion](../../tests/runtime/test_tiering_completion.py), [review freshness](../../tests/runtime/test_review_freshness.py) | [runtime identity](../../tests/runtime/test_content_identity.py) mixes local assertions with installed execution; inspect exact nodes |
| Verification command execution and evidence | [shell semantics](../../tests/runtime/test_verify_shell_semantics.py), [kernel evidence](../../tests/kernel/test_verification_provenance.py), [runtime evidence](../../tests/runtime/test_verification_provenance.py) | Acceptance, readiness and completion consumers; inspect source-evidence manifest publication and binding, nested subprocess scope, and skipped proof |
| Prompt partials and effective prompt identity | [partials](../../tests/gate/test_prompt_partials.py), [prompt corpus](../../tests/gate/test_prompt_corpus.py), [generation trigger](../../tests/gate/test_prompt_generation_trigger.py) | [runtime integrity](../../tests/runtime/test_gate_prompt_integrity.py), installed packaged floor; preserve flat expansion and resource identity |
| Structured review parsing/results | [extraction](../../tests/gate/test_extraction.py), [current role codecs](../../tests/gate/test_tiering_roles.py), [native review integration](../../tests/runtime/test_tiering_dispositions.py) | Runtime and driver structured-review consumers; controlled provider transports are hermetic |
| Free-form decision recommendations | [recommendation contracts](../../tests/gate/test_review_recommendations.py), [current role codecs](../../tests/gate/test_tiering_roles.py), [runtime recommendation recording](../../tests/runtime/test_review_recommendations.py) | Current v3 content accepts recommendation prose. Controlled adapters exercise publication, decision ownership and exact replay without a real provider or full workflow. |
| Completed imperfect review retention and lead interpretation | [retained-response integration](../../tests/runtime/test_review_retention.py) | All eight current roles; both real adapters with controlled transport. Exact bytes, warning/restart/recovery, native CLI/CAS/dry-run, original slot credit, immutable provenance, REPORT/dual-slot preservation, source drift, reporting and actual final-boundary archive/cleanup. The driver case stops at a fake lead-session transport. Historical codecs remain covered by `test_review_recommendations.py`; native ownership/reporting consumers are `test_tiering_dispositions.py` and `test_tiering_reporting.py`. No full workflow, live provider, wheel install or final-proof qualification. |
| Process monitoring and cancellation | [monitor](../../tests/gate/test_monitor.py), [Codex runner](../../tests/gate/test_codex_runner.py) | Real process termination, locks and cleanup; controlled child processes are not automatically e2e or live |
| Scaffold contract evidence and post-remediation consumer selection | [scaffold prompt delivery](../../tests/gate/test_document_altitude.py), [kickoff briefing delivery](../../tests/runtime/test_document_altitude.py) | Inspect captured plan sections, imported test helpers and the changed contract's first-order consumers. Installed close and cancellation topology are conditional on the affected behavior; their E2E/live execution remains separately authorized. |
| Codex oversized captured-input delivery | [Codex runner](../../tests/gate/test_codex_runner.py), [pure input navigation](../../tests/gate/test_input_navigation.py) | Exact character boundary, UTF-8 partition, reading order, capture/index bindings and tamper refusal. The local process shim launches no real provider or full workflow. |
| Driver authorization and session decisions | [authorization seam](../../tests/driver/test_authorization_seam.py), [completion](../../tests/driver/test_completion.py) | Fake-session boundaries and runtime state mutations; [host permissions](../../tests/driver/test_supported_host_permissions.py) includes a live case |
| Repository / packaged hooks | [repository hooks](../../tests/guardrails/test_repository_hooks.py), [packaged hooks](../../tests/guardrails/test_packaged_hooks.py) | Some tests invoke actual pre-commit/toolchain; inspect helper fan-out and configuration before running |
| Wheel packaging and installed-host behavior | [wheel harness](../../tests/runtime/wheel_harness.py), [portability](../../tests/runtime/test_portability_lane.py), [packaged floor](../../tests/runtime/test_packaged_prompt_floor.py) | Helper is not a test target. Separate toolchain packaging witnesses from full e2e journeys; builds and mutable installs cost setup time |
| Installed feature lifecycle | [completion installed](../../tests/runtime/test_completion_installed.py), [identity/readiness installed](../../tests/runtime/test_identity_readiness_installed.py) | Explicit local e2e execution; controlled providers do not remove the e2e requirement |
| Product performance | [performance](../../tests/runtime/test_performance.py) | Separate existing contractual ceilings, structural hot-path assertions and expensive scale measurements |

For local-development-history changes, start with admission, baseline diff,
source inventory or completion owners according to the affected contract. The
installed completion witness begins from an initial source commit, checks the
stored source baseline, and keeps every workflow record commit optional. Its
`completion.tar.gz` archive is the local retention and interruption recovery
boundary. Execute that installed witness only with exact E2E authority.

## Maintaining the map

These routes describe current behavior and do not require retaining superseded
modules or fixtures. When tests move, update affected commands, markers, and
executable AC references while keeping process, installation, attribution, and
recovery obligations in current witnesses.

Current suite membership may change through explained additions and approved
retirement; current band coverage and complete requested proof remain required.
The preview/write command compares collection with
`tests/fixtures/pytest-inventory-current.txt.gz.b64` and updates its pinned digest.
It does not require historical run records. The native
recipe’s former inline provenance-path assertion now resolves the referenced
source-evidence manifest. Remeasure changed recipes only; do not infer product
latency improvements from test-loop timing or treat old wheel proof as current.

Preserve both assignment-local milestone-convergence nodes in
`tests/runtime/test_tiering_completion.py`: distinct evidence bases cannot borrow
closure across assignments. These are bounded native transitions with controlled
transport. The combined packaged-floor toolchain witness now constructs a native
v8 assignment host and renders all eight current roles from an installed wheel.
Wave 4 still owns final program-wide installed integration.

## Runner and proof feedback recipes

Updated 2026-09-12 after Wave 2 M3 root activation. These exact selections exercise
the active formal runner entry. V means verified by source inspection and the
listed run; I means an inferred downstream relationship; U means unresolved
qualification. Confidence describes the named relationship, not exhaustive
dependency coverage. Apply the [strategy budgets](../workflow/testing-strategy.md#test-class-time-boundaries).

| Recipe | Distinct risk and class | Direct helpers, consumers and effects | Confidence / observed cost |
| --- | --- | --- | --- |
| Selection and denial | Inherited opt-in or exact forbidden request executes fixtures; component, 1 s/case and 10 s/selection | `pytest_policy` selection/authority; `pytest_policy_helpers.Project` launches controlled child pytest with file sentinels. No actual full workflow/provider | V: 7 cases; 0.78 s wall, maximum rounded lifecycle 0.10 s |
| Proof integrity | Duplicate targets inflate proof, alternate configuration removes fixture witnesses, or nonpassing phases become success; component, 1 s/case and 10 s/selection | Real `proof_runner` → `pytest_policy`, controlled helper children and sentinel/report assertions; native verification is the downstream consumer | V: 19 cases; 2.55 s wall, maximum rounded lifecycle 0.19 s |
| Native fast acceptance | A zero process exit without complete proof is accepted, or a runner edit fails to invalidate native evidence; fast acceptance, 1 s/case and 15 s/selection | Actual `write_path.verify` and freshness assessment; `current_host`/tiering helpers create disposable state, `write_yaml` authors test inputs, copied runner launches tiny local checks. No feature completion, wheel build or provider | V: 2 cases; 1.12 s wall, maximum rounded lifecycle 0.43 s |

Run the relevant recipe from the repository root:

```bash
# Selection and denial
.venv/bin/python -m tests.proof_runner \
  tests/guardrails/test_pytest_selection.py::test_ac1_default_and_old_environment_cannot_execute_forbidden_items \
  tests/guardrails/test_pytest_selection.py::test_ac2_denial_precedes_every_fixture

# Proof integrity
.venv/bin/python -m tests.proof_runner \
  tests/guardrails/test_pytest_proof.py::test_ac4_complete_parameter_set_and_overlap_count_once \
  tests/guardrails/test_pytest_proof.py::test_ac4_configuration_override_cannot_remove_fixture_witnesses \
  tests/guardrails/test_pytest_proof.py::test_ac5_incomplete_or_nonpassing_outcomes_cannot_prove

# Native fast acceptance
.venv/bin/python -m tests.proof_runner \
  tests/guardrails/test_pytest_proof.py::test_ac6_native_fact_records_incomplete_proof_and_runner_staleness
```

Measure these recipes locally when timing matters. Native verification remains
the proof owner; recipes do not replace the complete affected proof selection or
separately authorized installed journeys. Add the exact existing witness when
changing a related failure mechanism.

Active v6 conversion and immutable history are covered by
`tests/runtime/test_tiering_migration.py`: temporary in-process hosts exercise
source/CAS-bound conversion, exact snapshot recovery, explicit Medium choices,
read-side source attribution, accepted-v6 refusal and native CLI preview. No
provider or installed workflow is launched. Its retained-round and retired-duty
cases now cover exact synthesis producer/decision attribution, immutable bootstrap
round-zero adoption and spent calls, retained dispositions without manufactured
freshness, clean completed history versus real coverage gaps, and forged usage,
tuple/order/owner refusal.
The same file exercises later-boundary retired inspection with actual controlled
native transport, v6 driver refusal before any probe/session/notify, compatible
read-only surfaces and historical host exclusions. Final boundary coverage uses
`tests/runtime/test_tiering_completion.py`: in-process cases with explicit
tiny local check commands, including overlay ownership, amendment/off closure,
stale proof, all qualified dual sources in acceptance, prior-milestone duties,
human-owned Drive repair and immutable accepted-v7 effect retries/readers. These
are boundary tests, not installed or full-workflow journeys.
`test_tiering_reporting.py` covers zero-run off policy, original migration
round/call IDs, current policy history, dispositions and version-separated
aggregation. Retired synthesis counters keep their original per-CLI meaning in
both unmigrated v6 and source-bound migrated v7 history; exports do not alter
the workspace or canonical artifacts. Direct consumers are `tests/runtime/test_trajectory_archival.py`
(authored old v2 canonical results with the explicit historical contract) and
`tests/gate/test_trajectory_report.py` (a bounded report-script subprocess over
temporary JSON). Neither launches a full workflow or provider. The four
installed journeys retain their explicit final-tree execution grant.

Current v8 schema consumers additionally select the exact vocabulary/version
nodes in `test_acceptance.py` and `test_validation_acceptance.py`, the read-only
`test_validation_schemas.py`, and the three `test_ac3_*`/`test_ac4_*` in-process families
in `test_content_identity.py`. The latter file's installed node is excluded.
Historical corpus assertions still require v6 without restamping. Current
status/orient/portfolio use milestone-scoped assignment reporting, not the old
feature-only pipeline; retain the old pipeline and bounded-authority survivors.
The identity/readiness installed and live callers of the shared current-schema
helper are source-adapted to confirmed v8 policy and native coverage dispositions.
They are collected only, not covered by the four tiering installed-case grant;
execution proof for these additional exact journeys remains pending.

The v8 native disposition selection also counts real source captures, assignment bases,
canonical validations and closure derivations. Bases are cached by assignment identity, so roles with feature scope retain independent document dependencies. It proves that status/orient share
one projection, batches qualify each original assignment once, post-write
projection observes new dispositions, and later commands see source changes.
These are structural assertions, not host-speed benchmarks or a persistent cache.
The same selection follows emitted readiness operations for required later passes
and ownerless stops, rejects opening a closed or over-budget assignment, and
preserves exact replay plus an admitted dual round's missing slot at its cap.
It also exercises single-use `continue-review` resolutions, generic-disposition
non-authority, pending-stop preservation across amendments, reached-cap refusal,
and new decisions for later stops. The driver tests in `test_review_retention.py`
stop at a controlled lead transport for both interpretation and disposition.
`tests/runtime/test_review_freshness.py` tests role-specific document/source
changes and cited versus uncited evidence through native dispositions and status.
A citation edit reopens only its original finding; relevant edits can be settled
with new evidence without an automatic provider call. These cases use controlled
provider transport and do not launch full-workflow or live tests.

These are in-process assignment transitions with controlled transport, not a
full feature lifecycle or a real-provider test.

Typed-action checks keep assessment, wire, and consumer claims distinct. Start
with `test_action_contracts.py` for complete `CommandAction`,
`AuthoringAction`, and `DecisionAction` delivery and revision revalidation. Add
`test_evidence_explanations.py` for evidence applicability and diagnostic
payloads, and the exact acceptance node for cross-surface behavior. Run the
installed or E2E selection only when its separately recorded authority applies;
inspect its nested transport before execution.

Exact existing milestone refresh waiver is covered by
`tests/runtime/test_milestone_review_refresh_waiver.py`: seven fixed completed
origins, stale-only exemption with unchanged rows/counters, independent evidence
and selected-input guards, latest-run isolation, canonical hash validation,
source-bound migration without v7 authority, and honest terminal attribution.
No provider, subprocess or workflow launches. Compatible installed qualification
separately exercises the actual decision writer and historical all-done selector
through phase exit with explicitly controlled freshness observations.

The exact D32 robustness lead acceptance is covered by
`tests/runtime/test_robustness_lead_approval.py`: fixed error/run, owner and
immutable eight-disposition triage; exact raw archive and source/test
evidence; closed receipt with stable current native proof; graph, replay,
tamper, scope, version and source-drift refusals; truthful status/help and
independent final proof. These are hermetic in-process components. The tests author their own evidence in disposable hosts.

The exact task-specific lead peer approval is covered by
`tests/runtime/test_peer_lead_approval.py`: complete 23-ID accounting, fixed
original artifacts and owner fact, stable zero-exit native proof bindings,
current-source qualification, artifact-tamper refusal, unchanged failed/missing
review rows and counts, later-attempt isolation, migration-history-only retention
and current-runtime v6 write refusal. These are authored hermetic components,
not a provider or full-workflow journey. The separately qualified compatible-v6
delta exercises the actual locked decision writer, replay, status/orient and an
isolated phase boundary before any real supervisor adoption.

## Selection examples

Current document and prompt acceptance follows focused compatibility checks and
organic feature feedback. The two
`test_document_altitude.py` files check current source capture, native dimensions,
document ownership and kickoff delivery; they do not require model comparison
arms. Prompt/briefing corpus tests cover all eight current roles, current policy
profiles, historical read-only migration guidance and command preapproval
containment. `test_prompt_remedies.py` and the all-current-role retention node
protect the preserved recommendation and lead-interpretation contracts.

`python -m tests.tiering_qualification_probe` exercises synthetic evidence-record
counterexamples. Current document reader and rendering checks live in the two
`test_document_altitude.py` files. These checks require no prior comparison runs.

The gate file's `test_ac12_scaffold_review_reads_plan_test_boundary_and_binds_fresh_changes`
uses a temporary v8 host and native source capture/preparation, without running a
gate or verification command. It checks Technical Architecture, the canonical AC
slice, command/prerequisite/test-path blocks, independently retained decision
ground, and actual changed rendered content plus fresh identity. The two
`TestPromptContextBuilders::test_review_test_scaffolding_*` cases in
`tests/gate/test_parity_regressions.py` preserve old plan-section and diff-driven
test-file discovery without requiring the new architecture heading in history.

- A change to flat prompt expansion starts with the relevant cases in
  `tests/gate/test_prompt_partials.py`; inspect effective identity consumers
  before adding their nodes. It does not imply a live model comparison.
- A shell-chain execution fix starts with the chain, failure and publication
  cases in `tests/runtime/test_verify_shell_semantics.py`; widen to native
  provenance/recording only when that contract can change.
- A shared wheel-harness change needs representative installation/isolation
  cases and any affected e2e consumers. Request e2e execution for those exact
  cases; an ordinary fast selection cannot qualify the harness by excluding them.
- A registry or schema change needs representative producer and consumer
  witnesses. Python imports alone do not reveal all dynamic/configuration users.
- An AGENTS/README-only navigation edit needs link/content checks for the changed
  guidance. It does not require a runtime suite or a provider gate merely because
  runtime filenames are mentioned.
- A host-integration guide or linked README entry change starts with
  `tests/runtime/test_host_integration_guidance.py`. Add
  `tests/runtime/test_portability_lane.py::test_ac14_red_readme_install_and_update_contract`
  when installation commands or the public update contract change.

Use `rg` to find actual test definitions and focused `--collect-only` to resolve
parameterized IDs when needed. Do not guess node IDs or use root `-k` collection
as the default discovery mechanism. Collection must remain side-effect free.

### Representative selection exercises (Wave 1)

These are inspected selection routes, not claims that all example tests ran.
Resolve parameterized IDs with focused collection before executing them.

| Example change | Initial witness | Expansion and cost decision |
| --- | --- | --- |
| New public CLI feature | Start with the cheapest owner-level contract test plus the relevant public dispatch case in `tests/runtime/test_dispatch.py` | Within the host budget select core behavior, one uncertain adapter interaction, a consequential invalid-input/refusal case and fast public-boundary acceptance. Keep an installed lifecycle witness separate and pending until its exact e2e run is authorized; a passing component route does not satisfy it. |
| Pure configuration default | `tests/kernel/test_project_config.py::TestAC2DefaultsDiagnosticsDiscovery::test_empty_config_applies_full_defaults` | Read config producers and runtime discovery callers; add affected diagnostics/default consumers, not the whole runtime area |
| Shared state vocabulary | `tests/runtime/test_schemas.py::TestAC14StateVocabularyValueLock::test_state_required_keys` | Pair with affected typed-reader cases in `tests/kernel/test_state.py::TestAC4StateReaderTypesCorpus` and actual runtime/driver consumers; schema shape alone does not prove interpretation |
| Prompt leaf insertion | `tests/gate/test_prompt_partials.py::TestExpandPartials`, `tests/gate/test_prompt_partials.py::TestComputePromptVersion` | Add rendered corpus and briefing checks for propagation; a model-quality claim needs separate evidence, not these local contracts |
| Shared installed fixture | `tests/runtime/wheel_harness.py::build_installed_wheel` is the changed helper, not a runnable node | Inspect its first-order callers with `rg build_installed_wheel tests`; select one representative build/install consumer for the changed contract and a distinct consequential failure consumer. Account for wheel build, venv creation and offline-install lifecycle cost. Keep each affected full lifecycle journey separately selectable and pending until its exact e2e execution is authorized; fixture sharing alone does not justify the whole suite. |
| Installed resource packaging | `tests/runtime/test_packaged_prompt_floor.py::test_built_wheel_renders_every_gate_prompt_outside_the_checkout` | Toolchain witness with a real build/install; add `tests/runtime/test_portability_lane.py::test_ac10_red_installed_ratified_journey_honors_nondefault_plans_layout` only if the lifecycle contract is affected and exact e2e execution is authorized |

## Maintenance contract

The contributor changing a source contract, fixture, test path, or classification
updates the affected row and owning `Verified-by:` references. A reviewer checks
the row against actual fixture and consumer dependencies. Record any unresolved
coverage gap in the active plan; do not label the map complete while it persists.

Add rows for new shared contracts, not every local helper. A useful row identifies
the contract, initial witnesses, direct consumers, cost/effects, and any special
execution authority. As Wave 4 measures costs, attach evidence references rather
than timeless duration promises. Quarantine and skip ownership live with their
owning tests/issues, with the map pointing to a substitute witness when required.

Automated selection, import graphs as completeness proof, and automatic broad
fallback are outside this program. This map supports agent judgment.
