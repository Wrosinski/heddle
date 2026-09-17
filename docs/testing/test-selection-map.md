# Contract-to-test selection map

Status: bounded, manually maintained map of current representative witnesses.
This map is deliberately not exhaustive.

Use the [testing strategy](../workflow/testing-strategy.md) before execution.
Select the row matching the changed contract, then read test definitions,
fixtures, and direct consumers. A row is a discovery route, not a command to
run every listed file. Historical activation records are not executable selections or standing test
authorization. Inspect the current files and helpers before choosing nodes.

| Changed contract | Start with | Inspect direct consumers / cost boundary |
| --- | --- | --- |
| Project configuration and layout | [kernel config](../../tests/kernel/test_project_config.py), [kernel boundaries](../../tests/kernel/test_boundaries.py) | Runtime config validation and installed discovery; acceptance labels do not establish relevance |
| Runtime-to-gate import boundaries | [canonical runtime boundary](../../tests/runtime/test_boundaries.py), [normal-selection binding](../../tests/runtime/test_schemas.py) | Select the canonical boundary node whenever a change alters imports from `heddle.runtime` to gate, IO, driver, or third-party modules. The repository's normal and autopilot verification commands include this node; update the shared edge policy in `policy_helpers.py` only with the owning contract change. |
| State/model decisions and completion | [current policy/state](../../tests/kernel/test_tiering_policy.py), [completion boundary](../../tests/runtime/test_tiering_completion.py), [completion repairs](../../tests/runtime/test_completion_repairs.py) | Runtime completion and driver completion; the [installed repair journey](../../tests/runtime/test_completion_repairs_installed.py) and other installed journeys are opt-in e2e |
| Review policy and registry | [gate policy](../../tests/gate/test_gate_execution_transport.py), [current role codecs](../../tests/gate/test_tiering_roles.py) | Runtime policy resolution and controlled adapters; pure policy checks launch no provider |
| Confirmed feature policy and qualified review closure | [pure policy and state](../../tests/kernel/test_tiering_policy.py), [qualified closure](../../tests/kernel/test_review_closure.py) | Hermetic contracts/kernel tests: closed wire vocabulary, override preservation, round/call arithmetic, originating obligations and evidence-based stops. Inspect prior-format state/model and review-accounting consumers. Runtime admission and installed journeys are separate downstream proof. |
| Confirmed intake, current role execution and native dispositions | [intake/admission](../../tests/runtime/test_tiering_intake.py), [versioned role codecs](../../tests/gate/test_tiering_roles.py), [native review integration](../../tests/runtime/test_tiering_dispositions.py) | In-process temporary hosts with real recording, policy-selected adapters and provider transport doubles. Covers create-only recovery, owner/CAS checks, exact original refs, rounds, capabilities and read-only driver projections. Include the exact Low/High milestone node in `tests/runtime/test_document_altitude.py` and relevant historical milestone nodes. Structural imports/write seams are in `tests/runtime/test_boundaries.py` and `test_policy_locks.py`. Installed journeys and real-provider prompt qualification remain separately authorized proof. |
| CLI routing and envelopes | [dispatch](../../tests/runtime/test_dispatch.py), [contracts](../../tests/runtime/test_contracts.py), [schemas](../../tests/runtime/test_schemas.py) | Direct application operations, driver callers, cold subprocess imports |
| Readiness assessment and typed action routing | [typed action scenarios](../../tests/runtime/test_action_contracts.py), [evidence explanations](../../tests/runtime/test_evidence_explanations.py), [action-routing acceptance](../../tests/runtime/test_action_routing_acceptance.py) | Select the exact assessment, wire, driver, freshness, or explanation node first. Controlled provider doubles remain hermetic; the installed lifecycle and the declared one-node controlled-provider E2E witness retain their separate execution authority. |
| Truthful phase and milestone transitions | [focused acceptance](../../tests/runtime/test_truthful_transitions.py), [installed journeys](../../tests/runtime/test_truthful_transitions_installed.py) | Select focused cases for Off-robustness qualification, human exit guidance with the driver still awaiting authorization, retained duties, preview/apply/no-op attribution, unrelated command preview compatibility, post-write readback races and complete-stage driver handoff. Stale-proof refusal covers both pending and already-granted boundary authorization. Add affected readiness, revision, receipt, help and import-boundary survivors. The two installed candidate-wheel journeys are explicit E2E and retain separate exact execution authority. |
| Native verification-command removal | [command removal](../../tests/runtime/test_commands_unset.py), [command setting](../../tests/runtime/test_commands_set.py), the `commands unset` cases in `test_contracts.py` | Real current-state CLI/typed writes, dry-run, exact selected-key delta, absent/blank behavior, stale/concurrent CAS, historical/accepted refusal, retained logs and mandatory acceptance/smoke after removal. Terminal fixtures run only their tiny local assertion commands, not a full workflow/provider. Older-format fixtures are read/refusal witnesses, not permission to execute archived workflows. |
| Content identity and readiness | [kernel identity](../../tests/kernel/test_content_identity.py), [current completion](../../tests/runtime/test_tiering_completion.py), [review freshness](../../tests/runtime/test_review_freshness.py) | [runtime identity](../../tests/runtime/test_content_identity.py) mixes local assertions with installed execution; inspect exact nodes |
| Verification command execution and evidence | [shell semantics](../../tests/runtime/test_verify_shell_semantics.py), [kernel evidence](../../tests/kernel/test_verification_provenance.py), [runtime evidence](../../tests/runtime/test_verification_provenance.py) | Acceptance, readiness and completion consumers; inspect source-evidence manifest publication and binding, nested subprocess scope, and skipped proof |
| Prompt partials and effective prompt identity | [partials](../../tests/gate/test_prompt_partials.py), [prompt corpus](../../tests/gate/test_prompt_corpus.py), [generation trigger](../../tests/gate/test_prompt_generation_trigger.py) | [runtime integrity](../../tests/runtime/test_gate_prompt_integrity.py), installed packaged floor; preserve flat expansion and resource identity |
| Structured review parsing/results | [extraction](../../tests/gate/test_extraction.py), [current role codecs](../../tests/gate/test_tiering_roles.py), [native review integration](../../tests/runtime/test_tiering_dispositions.py), [coverage disposition admission](../../tests/runtime/test_review_coverage.py) | Runtime and driver structured-review consumers; controlled provider transports are hermetic. Coverage checks preserve native assignment origins, frozen captures, required independent inspection and source freshness without a provider retry. |
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

Update paths and classifications with their owning contract. Do not copy old
feature grants or rollout claims into this map. Supported state versions are
owned by `heddle/contracts/schemas.py`; compatibility and refusal witnesses start
with `tests/runtime/test_source_evidence.py` and
`tests/kernel/test_tiering_policy.py`. There is no active v6 migration command.

Review closure, dispositions and current-source applicability start with
`tests/kernel/test_review_closure.py`, `tests/runtime/test_tiering_dispositions.py`
and `tests/runtime/test_review_freshness.py`. Boundary receipt tests in
`tests/runtime/test_sealed_review_boundaries.py` distinguish accepted predecessors
from live final evidence. Inspect exact nodes before executing these files.

The reviewed collection inventory is
`tests/fixtures/pytest-inventory-current.txt.gz.b64`. Update it only after an
intentional test-corpus change through the preview/write procedure in the
[testing strategy](../workflow/testing-strategy.md#long-term-maintenance).
Historical fixtures retain their original meaning; they are not templates for
current state or claims that an old workflow can execute.

## Runner and proof selections

These focused selections exercise execution denial and strict proof through
controlled child pytest processes. They do not launch real providers or a full
feature lifecycle. Read shared helpers before using them for a changed contract.

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

## Selection examples

For documentation and prompt edits, select the actual reader and rendering
checks. `test_document_altitude.py` in the runtime and gate areas covers document
ownership, captured inputs and kickoff delivery. Prompt/briefing corpus tests
cover supported commands and the current role catalog. These checks establish
local contracts, not model quality.

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

### Representative selections

These are inspected selection routes, not claims that all example tests ran.
Resolve parameterized IDs with focused collection before executing them.

| Example change | Initial witness | Expansion and cost decision |
| --- | --- | --- |
| New public CLI feature | Start with the cheapest owner-level contract test plus the relevant public dispatch case in `tests/runtime/test_dispatch.py` | Within the host budget select core behavior, one uncertain adapter interaction, a consequential invalid-input/refusal case and fast public-boundary acceptance. Keep an installed lifecycle witness separate and pending until its exact e2e run is authorized; a passing component route does not satisfy it. |
| Pure configuration default | `tests/kernel/test_project_config.py::TestAC2DefaultsDiagnosticsDiscovery::test_empty_config_applies_full_defaults` | Read config producers and runtime discovery callers; add affected diagnostics/default consumers, not the whole runtime area |
| Shared state vocabulary | `tests/runtime/test_schemas.py::TestAC14StateVocabularyValueLock::test_state_required_keys` | Pair with affected typed-reader cases in `tests/kernel/test_tiering_policy.py` and actual runtime/driver consumers; schema shape alone does not prove interpretation |
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
execution authority. When measuring costs, retain the source and environment alongside the result
rather than treating one duration as a permanent promise. Quarantine and skip ownership live with their
owning tests/issues, with the map pointing to a substitute witness when required.

Automated selection, import graphs as completeness proof, and automatic broad
fallback are not supplied by this map. This map supports agent judgment.
