# Heddle architecture

Heddle is a local protocol runtime for agent-driven feature work. The runtime
answers what is true and which action is legal next; agents and users supply
engineering judgment. This document owns the current component, interface and
integration boundaries. [Workflow model](workflow-model.md) owns phase policy
and operational facts; [engineering principles](../workflow/engineering-principles.md)
owns engineering preferences. Maintained source, schemas, and tests named below
are the authority for current behavior.

## Components and authority

| Owner | Responsibility |
| --- | --- |
| [contracts](../../heddle/contracts/) | Dependency-neutral vocabulary: immutable operations, typed actions, result envelope, gate catalog, review-policy identities, decision variants and schema keys. |
| [kernel](../../heddle/kernel/) | Decode current config/state and compute deterministic judgments from supplied facts and observations. No runtime/driver/gate import, state publication or subprocess. Knowledge reads are explicit off-hot-path services; `knowledge_search.py` owns the one CommonMark dependency exception. |
| [io](../../heddle/io/) | Bounded Git and process operations and direct source observation. No imports from application policy or engines. |
| [runtime](../../heddle/runtime/) | Compose context, resource resolution, readiness and operations; own state transactions, host adoption, projections and completion effects. |
| [gate](../../heddle/gate/) | Prepare and execute review transport; validate native content, bind canonical results, render views and decide exact reuse. Runtime owns application orchestration and event publication. |
| [driver](../../heddle/driver/) | Execute typed application actions and supervised sessions; halt/notify using the common result contract. No independent state writer or command-string execution of `next_actions`. |
| [resources](../../heddle/resources/) | Installed prompt and briefing floor, schemas, shared instruction fragments and scaffold templates. |

The kernel import boundary and driver leaf apply
**Dependencies point inward, not back out** in the
[Architecture principles](../workflow/engineering-principles.md#architecture).

The CLI dispatches to the typed [application service](../../heddle/runtime/application.py).
Direct application calls and CLI calls share the same handler, validation and
result semantics. A presentation command is rendered from a typed action; it is
never parsed back into executable policy. Small standalone gate surfaces remain
where registered; this is not a promise that every command works without a project.

`runtime/state_store.py` is the sole physical operational-state publisher,
applying **One owner per authority**. Pure
kernel transforms describe changes. Ordinary mutations take a short workspace
lock, validate the latest state, check revision, transform and validate again,
then atomically publish. Gate-event append mode uses the same lock and can keep
the revision unchanged. Long provider/suite execution happens outside that lock.
Completion additionally compares readiness-relevant facts under lock; a revision
alone cannot detect every event append. All ordinary writers reject accepted
history before applying even a no-op transform. Git commits remain operator-owned.

`runtime/feature_context.py` applies **One owner per authority** to shared
feature/snapshot resolution and write
admission, including caller revision checks. Its resolved snapshot context derives
the configured state path; completion, status, kickoff and close audits share
that value. The shared write argument parser decodes the optional nonnegative
caller expectation for public single-state mutations, and typed command rendering
preserves any supplied expectation exactly once. `runtime/diagnostics.py` owns
shared write/refusal envelopes, while `runtime/readiness.py` renders boundary and
verification readiness failures.

Knowledge prose is human/agent-owned. The runtime's narrow knowledge writes are
scaffolding, explicitly managed projections, and the accepted-identity lifecycle
stamp. A filename suffix does not define the control/knowledge boundary: actual
imports, read traces, pure transforms and write/process sinks do.

## Public interface and diagnostics

The [command manifest](../../heddle/runtime/contracts.py),
[operations](../../heddle/contracts/operations.py), and
[schemas](../../heddle/contracts/schemas.py) own machine spellings and payloads.
Operation types own command spellings; the command manifest declares each
ordinary CLI adapter through an internal lazy binding. Dispatch derives its
ordinary routes from those declarations while preserving the special raw-argv
gate/drive entries. Internal bindings are excluded from public serialization.
`heddle help --json` publishes one entry per leaf command, including arguments,
flags, input/output schemas, mutation/dry-run support and actual exit codes.
Bare nouns are not extra command entries. An input schema is null when the
command accepts no authored payload. A command is advertised as implemented only
when it honors that contract; exact landed commands do not promote an entire
roadmap shelf.

The common envelope has `schema_version`, `ok`, `diagnostics`, `next_actions`
and exactly one of `data` or `error`. `ok` and process exit status are independent:
a recorded gate or accepted completion can succeed while an advisory exit reports
remaining work. Error codes are stable; diagnostic codes are a separate extensible
namespace. Legal next actions are typed command, session or manual instructions;
an empty action list is preferable to fabricating an executable remedy.
`decision-already-resolved` remains a fatal refusal and invalid routes are usage
errors; neither becomes a hot-path blocker. See [workflow interface semantics](workflow-model.md#123-interface-constants-pinned-here-for-increment-1).

The warm target is 300 ms with the existing scaled CI ceiling of 2 s for its
measured command classes; [machine constants](../../heddle/runtime/contracts.py)
and [performance tests](../../tests/runtime/test_performance.py) own the exact
scope. Feature readiness reads now observe declared content and canonical
artifacts. Portfolio reads stay snapshot-only; no constant-time claim applies to
arbitrary declared source trees. Recorded immutable results support free exact
replay only when the single reuse owner accepts their integrity and identity.

## Project configuration and resources

`.heddle.yaml` is the project policy/configuration source. `pyproject.toml` is
package/build configuration, not a second Heddle policy store. The
[config parser](../../heddle/kernel/project_config.py) owns accepted keys and
validation. `layout.*` controls the declared layout consumers; a declared key
must not be mistaken for a separately shipped capability. In particular,
`layout.prompts` is not the prompt-resolution root.

[Resource resolution](../../heddle/kernel/resources.py) applies
**Extend through data and one resolver, not parallel paths**: explicit host
override, generated resource, then installed packaged fallback. The runtime
reports the selected source; generated/override trees are distinct from legacy
host `prompts/` conventions. Briefings have one canonical stage owner. Session
entry is `heddle orient`, follow its ordered actions, then `heddle kickoff` only
when routed. Kickoff is a read, not an enacted workflow fact.

The [gate catalog](../../heddle/contracts/gates.py) defines registered identity
and relationships; the [execution registry](../../heddle/gate/registry.py) owns
transport/prompt defaults, another application of **Extend through data and one
resolver, not parallel paths**. The confirmed native feature policy selects
per-role requirements and reviewer tuples independently of scope, complexity
and testability. `gates.enabled` constrains the available selection; it does not
grant invocation permission. Unknown host gate names refuse. A host-authored
gate loader and general workflow/topology DSL are not shipped extension points.
The bounded W1-D policy has retired admission; historical policy IDs remain
interpretable without authorizing new experimental events.

## Discovery and retained history

Resolution order is explicit `--feature`, then the worktree-local pointer, then
a unique active workspace. An explicit read does not re-pin selection. The
pointer lives at `git rev-parse --git-path heddle/active-feature`; the pointer
writer is separate session state, not a committed feature fact or revision CAS.
A stale or completed pointer is advisory and falls through. Ambiguity names the
candidates and legal selection; no active candidate returns the documented
inception remedy. Invalid candidate ledgers are surfaced, never silently dropped.

Active discovery and `feature switch` exclude typed accepted workspaces. Explicit
historical reads and `status --all` retain them; the portfolio ignores active
selection and labels completion effects unassessed. Historical accepted state
never asserts that current source is fresh. Explicit status/orient show observed
pending repairs. Doctor and validate check retained canonical artifacts without
requiring a removed plan or reassessing later source. After accepted completion,
historical verification logs and raw archives are optional on a fresh host;
missing logs are informational. Active work and acceptance retain their existing
evidence requirements.

## Review, verification and completion

Current state is `heddle.state/v10`, with `heddle.review-assignments/v3` as the
single review-attempt and boundary-acceptance authority and
`heddle.source-evidence/v1` as the immutable verification-manifest format. The
v10 ledger records the source baseline captured from the initial source commit
at admission; a later record commit for workflow files is optional. The
current runtime reads active v9 ledgers through their state-add compatibility
baseline and rejects older or future schemas without mutation; unfinished older
work remains recoverable with the compatible runtime that created it rather than
through in-place conversion. Native review
judgment is `heddle.review-content/v3`, bound by the
runtime into immutable `heddle.review-result/v3` evidence with assignment, round
and reviewer-slot identity. Recommendations are prose, not option-name selectors.
Completed imperfect responses retain immutable captures and warnings; the lead's
explicitly attributed native interpretation can credit the original independent
slot without a formatting-only provider retry. Historical v1/v2 content and v2
results retain their version-bound interpretation. Completed event identity and typed finding and
decision references own authority. Markdown views, provider output, legacy
receipts and trajectory summaries are not alternate authoritative results.
Current assignments retain original reviewer findings; the lead records explicit
dispositions and coverage using their exact source references. Native closure
qualifies those sources, the selected policy and current review basis. Successful
spec, plan, scaffold, and milestone transitions seal duty-bearing assignments in
append-only boundary receipts. Later work reads those assignments as accepted at
that boundary while current milestones and final reviews continue to use live
source and verification evidence. Completion retains and validates every
receipt-referenced canonical artifact and source-evidence manifest. Synthesis and
replaced gate IDs are historical-only, not current scheduling routes. See
[workflow gate semantics](workflow-model.md#62-run-facts-and-convergence).

`heddle review allowance` is a constrained facade over the same complete
confirmed-policy writer. It raises one role-wide absolute quality round limit
with CAS and dry-run projection, without adding assignment state or invoking a
provider. Assignment stops remain separate durable authority and must still be
resolved before ordinary round opening.

`runtime/gate_run.py` keeps locked target refresh, preparation, exact reuse,
admission, execution and recording in order. Its deterministic review policy and
completion decision supply both recording flags and response semantics. Result
rendering receives steering actions observed after recording. Exact reuse avoids
both provider execution and a new event, including at a spent review budget;
provider execution holds the gate lock while leaving the state lock available.

Readiness separates execution, disposition and applicability at existing boundaries.
Verification separates content identity, command adequacy, semantic quality,
operational reliability and tool behavior. Source identity is the declared raw
working content, including established byte/mode/symlink/missing-path semantics;
Git may discover tracked leaves and provide diagnostics, but HEAD is not freshness
authority. Observations are before/after point samples, not an atomic filesystem
snapshot or protection against change-and-restore between samples.

`heddle feature complete` accepts one immutable typed completion fact after current
qualification and the configured close suite, before stamping, archival or cleanup.
The spec stamp accepts only its recorded raw preimage or exact postimage and
executable mode. Create-only `completion.tar.gz` preserves the entire workspace,
including ignored output, with verified member integrity. Cleanup requires exact
local validation of the accepted ledger and exact archive readback, then compares
each candidate immediately before deletion. This local retention does not require
Git to contain the ledger or archive; a record commit remains optional. It retains
state, canonical reviews and referenced logs; unknown files are preserved.
Accepted pending effects report exit 4 and legal recovery commands. Retries keep
the fact, time and revision and run no qualification or suite. The
[human close briefing](../../heddle/resources/complete.briefing.md) owns the
operator-facing details.

## Validation and evidence boundaries

The D35 reliance test sets severity: a broken fact downstream computation relies
on is fatal; an incomplete quality relationship is advisory. There is no date,
feature-count or legacy grandfathering. A dangling `satisfies` reference is fatal;
an unassigned spec AC is advisory. Validation checks cross-plane references and
its bounded content guardrails, not full Markdown document-schema conformance.

D36 keeps `workspace-invalid` for a single-file fatal and `ledger-drift` for a
cross-plane fatal; the latter is not a hot-path blocking condition. Repo-global
validation catches each workspace failure, records diagnostics and continues
(CP1-B). If both fatal kinds occur, `ledger-drift` takes top-level precedence while
all per-workspace findings survive. Doctor checks environment/resources and
retained evidence; readiness qualifies a specific transition. None replaces the
others. [Validation](../../heddle/runtime/validate.py) owns this split; select its
current runtime consumers from the testing map.

`Verified-by` names an executable test binding, not a recorded passing run.
Repository hooks inspect applicable native workspace metadata and deliberately
changed bindings; a broken test name cannot be satisfied by an incidental AC
comment. Runtime verification records command, source identity, outcome and log.
The old M5 migrate/backfill and CP1-D/ADD-1 recovery machinery is superseded by
current-only cutover, independently of surviving CP1-A validation obligations.

## Host integration, installation and limits

The init contract defines the four-file footprint: `.heddle.yaml`, root
`AGENTS.md`, an unratified engineering-principles
seed, and `.heddle.lock`. Init preserves existing host-owned bytes and writes the
lock last; the per-target preview/apply contract reports create/accept/integrate/
refuse/skip. Its filesystem race failures remain exit 3, distinct from state
revision conflicts. Transient directories self-ignore without editing host
`.gitignore`. The host authors and ratifies its principles; init never does so.

The committed v1 lock binds path/class/hash for the adoption footprint; it is not
build provenance. Doctor reports install mode, loaded package location and
interpreter; orient carries the same observation, and incompatible-state errors
receive it through the shared diagnostic mapper. These facts are not proof of a
clean wheel, exact Git ref or provider confinement. Doctor reports runner
presence informationally and does not probe safely recoverable writes. Installed
acceptance runs outside the source checkout with controlled provider processes;
that proves the exercised tooling spine and portability, not comparative model
quality. [README installation modes](../../README.md#install-heddle-for-adoption)
retain editable development, exact pushed-SHA checkpoints and built-wheel
acceptance; start a fresh process after switching packages and install host
application dependencies separately.

`heddle sync` remains explicit-only and has two targets: the plan-status managed
region and root AGENTS session-entry region. Exact markers, byte preservation,
idempotency and per-file publication are enforced by the projection runtime and
its focused tests.
It is not part of the driver loop. Accepted historical plans are not regenerated.
Optional root mirror detection does not imply a shipped mirror writer. Repository
`.claude/skills` to `.codex/skills` generation is separately scoped development
tooling, not host skill distribution or D46 implementation.

`heddle search` owns bounded repository knowledge search using an in-memory FTS5
index of configured specs and an optional patterns shelf. It uses literal AND
intent, deterministic ties, no active feature requirement, and no persistent
cache. Research uses search then the documented index fallback on a failure;
absent optional content is distinct from unreadable present content.

Current-only cutover builds no converters, backfills, old result/receipt readers
or dual-format delegates. Finish active older features with a compatible pinned
supervisor; preserve their evidence outside active discovery before fresh native
work. The D44 legacy/native live comparison remains deferred, not achieved by
installed deterministic tests. D46 host mirror writing, broad M8B adapters/hooks/
profiles, MCP, host gate loading, document schema/fix machinery and new default
review topology/budgets remain deferred. M8C operational migration is superseded
by the clean-slate ruling. Historical driver-commit/canary rollout work is not
silently promoted by maintenance work. The current contracts above state the
retained limitations.
