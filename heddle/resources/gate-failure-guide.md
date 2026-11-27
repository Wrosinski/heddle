# Gate failure guide

Use the reason printed by `heddle run-gate` to find the matching entry below.
Inspect the named gate summary and log before retrying. A retry must preserve
the round's reviewer (provider, model, reasoning effort and sandbox) and the
review budget; the runtime selects that reviewer, including its model fallback.

Current writable work uses `heddle.state/v9`,
`heddle.review-assignments/v3`, and content-addressed
`heddle.source-evidence/v1` sidecars. When a qualified command reports missing,
malformed, tampered, or contradictory verification evidence, preserve both the
state fact and named sidecar and repair the specific artifact or source condition.
Rerun the recorded verification scope when fresh proof is required; never restamp
old bytes or edit the reference by hand. A v8 workspace is unsupported by the
current runtime and must remain with its compatible pinned supervisor.

## Exit status of a recorded round

`heddle run-gate` and `heddle run-gates` return `ok: true` once a round is
recorded. Exit 0 then means the verdict converged; native closure may still ask
for the round's coverage disposition. Exit 4 means the recorded round left
work: conditions or findings to disposition, owner decisions it routed, or a
retained response to interpret. Read `data`, and for `run-gates` every member
row (the batch exits with its most severe member); nothing failed and no retry
is needed. A review stop (round cap, no progress or no decrease) recorded
through `heddle review round-open` also returns `ok: true` with exit 4 and
names its stop decision. An `ok: false` result, usually exit 3, means the command failed
or was refused: follow its `error` and the matching entry below.

## Model fallback

Claude Code's own refusal fallback is switched off for every gate and drive
session, so a refusal fails the call instead of finishing on another model.
Heddle's single model fallback replaces it: when a slot's reviewer on
`claude-fable-5-2` fails for a provider reason, the same command reruns that
slot once on `claude-opus-5-5` at `xhigh`. Provider reasons are
`model-refusal`, `model-substituted`, `unsupported-model`,
`claude-overloaded`, `exit`, `budget-exhausted`, `max-turns`, `timeout`,
`inactivity-timeout`, `empty-output`, `truncated-output` and
`invalid-output-structure`. A truncated review is first retried once on the
same model inside the call. Preflight, reviewed-source, capture-storage and
`execution-failure` results keep the frozen reviewer, because a missing
binary or a local error never reached a model. The failed run stays recorded
as Fable and the rerun as Opus, in the same assignment, round and slot. The
result names the replaced run under `fallback_from` (a `run-gates` member
under `fallback`) with a `model-fallback` diagnostic. The command fails only
when the rerun fails too, or when the fallback cannot start, and its message
then names both.

For the rest of that round the slot runs on the fallback, and readiness's
suggested command names it. An explicit `--model claude-fable-5-2` is refused
until the next round, which starts on Fable again. A fallback run speaks for
its slot: a later Fable round can inspect the findings of an Opus fallback
run, and the reverse. Under `heddle drive` a failed Fable phase session is
recorded with both models named, and the drive's next phase session runs on
the fallback; a new `heddle drive` starts on Fable.

## Completed responses awaiting interpretation

For current assigned reviews, `ok: true`, `status: retained`, advisory exit 4
means the provider completed but its content could not be accepted natively.
This is not a clean review or a failed provider call. Findings are unavailable,
not zero. Do not rerun a provider to repair formatting.

Read the named immutable `.capture.json` and its convenient sibling Markdown
view. The capture contains exact raw stream and selected-response bytes (base64,
byte counts and SHA-256), original invocation/prompt/schema, and frozen validation
inputs. The view is non-authoritative and labels replacement of undecodable bytes.
An absent selected channel remains absent; the raw stream is still preserved.

The lead can analyze the complete response and apply supported changes, then
submit `heddle review interpret --feature <slug> --input-json <path|->`.
The input has exactly these fields:

- `schema`: `heddle.review-interpretation-input/v1`
- `run_id` and `capture_sha256`: copied from the retained response envelope
- `author` and `reason`: attribution and an explanation of the interpretation
- `content`: the complete native review object described by the capture's
  `schema_json`, preserving findings, limitations and uncertainty

Use `--dry-run` to validate without writing, and optionally `--expect-revision`
to refuse a stale state snapshot. Do not supply new invocation fields or stamp
old output as reviewing subsequently changed source. Accepted interpretation
earns the original independent slot's credit; REPORT decisions, dispositions,
required evidence and final closure still use their normal owners. Repeating the
same interpretation is idempotent; changing an accepted interpretation is refused.

The capture and its warning remain after acceptance and archival. A repeated
`run-gate` while interpretation is pending returns the capture without another
provider call. A published capture whose state recording was interrupted is
recovered on reinvocation if its original slot can still be recorded; conflicting
or invalid recovery refuses before provider execution. A failure to publish the
capture itself is a storage failure, not a successful retention claim.

Actual process failures, timeouts, missing/conflicting terminal events, stream
corruption and unavailable durable storage still use the failure entries below.
Historical reviews retain their original strict contract; they are not relabeled.

## `capture-storage-failure`

**Status:** emitted

**Evidence:** The provider completed with CLI exit zero, but bounded capture,
serialization or publication failed. The summary retains the real CLI completion;
the log records `CaptureStorageError` and staging/raw artifacts are preserved.
No response retention or accepted-review credit is claimed.

**Remedy:** Preserve the raw response and inspect the precise storage/size error.
Repair storage or reconcile an over-limit response with the owner. Do not retry
the provider merely to normalize presentation. A fully published, applicable
capture can recover its interrupted state recording without a provider call;
raw logs alone are not a durable capture or accepted review.

## `reviewed-source-changed`

**Status:** emitted by native recording admission

**Evidence:** The reviewed source basis changed during provider execution. The
original artifact and invocation remain attached to an error event, not a review
assignment source. A rejected capture stays ineligible for orphan recovery even
if source is restored or that capture is renamed.

**Remedy:** Preserve the rejected artifact and inspect the source change. Review
the intended current source under the confirmed policy; do not interpret the
rejected response as an accepted review. An unindexed capture whose current
source basis differs also requires reconciliation before any provider execution.

## `reviewed-source-unavailable`

**Status:** emitted by native recording admission

**Evidence:** Source could not be revalidated after provider completion. The
recorded error and diagnostic preserve rejection without assigning review credit.

**Remedy:** Restore readable source and inspect the failure before reviewing the
current source. Restoring a file does not retroactively accept a rejected capture.
This does not change explicit interpretation of a response successfully retained
and indexed before a later source edit.

## `missing-plan-path`

**Status:** emitted

**Evidence:** The gate summary preflight block reports that a required Implementation Plan could not be resolved.

**Remedy:** Create or restore the feature plan, validate the feature workspace, and retry the same gate invocation.

## `missing-milestone-section`

**Status:** emitted

**Evidence:** The preflight anomalies name the current milestone whose plan section could not be found.

**Remedy:** Add or restore that milestone section so it matches state, validate, and retry.

## `missing-satisfies-ac-ids`

**Status:** emitted

**Evidence:** The preflight anomalies report that the current milestone has no acceptance-criterion ownership.

**Remedy:** Record the milestone's real `satisfies` criteria through the Heddle authoring command, validate, and retry.

## `empty-authoritative-diff`

**Status:** emitted

**Evidence:** The summary diff block identifies the authoritative source and reports zero reviewed bytes.

**Remedy:** Confirm the intended implementation changes are present in the authoritative diff before rerunning review.

## `changed-files-empty`

**Status:** emitted

**Evidence:** The summary reports a non-empty diff whose changed-file projection is empty.

**Remedy:** Inspect the diff source for unsupported or malformed file records, repair it, and retry.

## `missing-owned-paths`

**Status:** emitted

**Evidence:** The preflight anomalies report that no milestone-owned paths were available for overlap checks.

**Remedy:** Add the exact implementation and test paths to milestone ownership, validate, and retry.

## `zero-diff-owned-path-overlap`

**Status:** emitted

**Evidence:** The summary lists changed files and milestone-owned paths but no overlap files.

**Remedy:** Select the correct milestone or align its ownership with the intended change before review.

## `untracked-owned-files-hidden-from-diff`

**Status:** emitted

**Evidence:** The preflight anomalies list untracked owned files omitted from the authoritative diff.

**Remedy:** Add the intended files to Git so the review diff includes their content, then retry.

## `off-scope-changed-files`

**Status:** emitted

**Evidence:** The preflight anomalies list changed files outside milestone ownership and approved lifecycle records.

**Remedy:** Move unrelated changes to a separate workstream or extend ownership only when the work genuinely belongs to this milestone.

## `timeout`

**Status:** emitted

**Evidence:** The summary termination and runtime blocks show the hard timeout and elapsed execution context.

**Remedy:** Inspect the log for the last useful activity, correct the underlying stall or reduce the bounded workload, and retry unchanged policy.

## `inactivity-timeout`

**Status:** emitted

**Evidence:** The summary records inactivity termination, observed idle seconds, and the last activity marker.

**Remedy:** Inspect the log near the activity marker, resolve the stalled provider or tool interaction, and retry.

## `claude-overloaded`

**Status:** emitted

**Evidence:** The Claude event stream or log contains an overload response and the summary preserves this reason.

**Remedy:** Wait for provider capacity and retry the same invocation without changing its configured model.

## `codex-at-capacity`

**Status:** emitted

**Evidence:** A Codex `error` or `turn.failed` event, or the raw process output, reports that the selected model is at capacity; the summary diagnosis category is `capacity`, and the raw output and event stream keep the refusal.

**Remedy:** Wait for provider capacity and retry the same authorized model. The refusal is not reviewer tool churn, so do not trim the prompt, and do not switch to another model as the message suggests without an explicit policy change.

## `budget-exhausted`

**Status:** emitted

**Evidence:** The Claude result or error event reports exhaustion of the configured execution budget.

**Remedy:** Reduce unnecessary work in the reviewed input or resolve the recorded cap decision; keep budget changes explicit and user-authorized.

A reached review-role cap uses `heddle review allowance` only after the owner
supplies an absolute quality round total and approval. That command changes no
provider retry budget and does not resolve the assignment stop; follow the
separate native stop-resolution and round-open route afterward.

## `max-turns`

**Status:** emitted

**Evidence:** The Claude result reports that the configured turn ceiling was reached before completion.

**Remedy:** Inspect the log for repeated work, narrow the review input if appropriate, and retry only within the authorized turn policy.

## `execution-failure`

**Status:** emitted

**Evidence:** The summary and log preserve an engine or provider execution exception that prevented a valid completion. On the Antigravity lane, a provider stream that could not be redacted is never kept or named: the summary's `artifacts.raw_out` is null, the log's `ExecutionFailure` line says so, and the stream waits in the run's temporary directory until the next gate run's recovery redacts it.

**Remedy:** Correct the reported local or provider failure, verify prerequisites, and retry the same gate configuration.

## `unsupported-model`

**Status:** emitted

**Evidence:** The provider stream reports model or account rejection and the summary records the requested execution tuple.

**Remedy:** Verify account access and the authorized model identifier; do not substitute a different model beyond the runtime's model fallback without an explicit policy change.

## `model-refusal`

**Status:** emitted

**Evidence:** The Claude stream reports a refusal: a `model_refusal_no_fallback` system event or a main-thread assistant message with `stop_reason: refusal`. The log's `ModelRefusal` line names the model and, when the provider gave one, the refusal category.

**Remedy:** A refusal is never a clean review: its findings are unavailable, not zero. On Fable the runtime has already rerun the slot on its model fallback (see Model fallback). When the fallback refused too, or the reviewer has none, read the refusal category in the log and the reviewed inputs it touched, and take the review gap to the owner; do not reword the prompt or the inputs to get past a refusal.

## `model-substituted`

**Status:** emitted

**Evidence:** The Claude stream reports that another model answered (`model_refusal_fallback` or `model_fallback`), although every Heddle launch switches Claude Code's fallback off. The log's `ModelSubstituted` line names the original and fallback models.

**Remedy:** The output is not credited, because the recorded reviewer did not produce it. Check that the installed Claude Code honors `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK` and that no wrapper strips the variable, then rerun the gate.

## `transport-error`

**Status:** emitted

**Evidence:** The provider event stream or process log records a transport failure before a usable final result.

**Remedy:** Restore connectivity or provider authentication and retry the identical prepared invocation.

## `missing-final-message`

**Status:** emitted

**Evidence:** Codex completed without the final-message event required to construct the review artifact.

**Remedy:** Inspect the captured event stream for protocol drift, update the compatible parser if necessary, and retry.

## `missing-message-event`

**Status:** emitted

**Evidence:** Codex completed without any supported message event in the captured event stream.

**Remedy:** Inspect event types in the log, correct parser compatibility or provider behavior, and retry.

## `missing-tool-events`

**Status:** reserved

**Evidence:** No current gate runner emits this reason; it is retained for compatibility with older summaries and tooling.

**Remedy:** If an existing summary contains it, inspect that summary's event counts and log using the runtime version that produced them.

## `missing-turn-completed`

**Status:** reserved

**Evidence:** No current gate runner emits this reason; it is retained for compatibility with older summaries and tooling.

**Remedy:** If an existing summary contains it, inspect the captured terminal events and log before deciding whether a retry is valid.

## `inactivity-no-final-message`

**Status:** emitted

**Evidence:** Codex became inactive after stream activity but before emitting a usable final message.

**Remedy:** Inspect the last event and activity marker, resolve the provider stall, and retry the same invocation.

## `output-last-message-empty`

**Status:** emitted

**Evidence:** Codex named a last-message output but its captured content was empty.

**Remedy:** Inspect the event stream and output paths for protocol or filesystem failure, repair the cause, and retry.

## `exit`

**Status:** emitted

**Evidence:** The provider process returned a nonzero exit that did not match a more specific classified reason.

**Remedy:** Read the gate log and summary exit fields, correct the reported command failure, and retry unchanged policy.

## `empty-output`

**Status:** emitted

**Evidence:** The provider completed but the filtered review artifact contained no usable output.

**Remedy:** Inspect the raw and filtered output evidence, correct transport or parsing issues, and retry.

## `invalid-output-structure`

**Status:** emitted

**Evidence:** The summary output-contract block lists the structural error found in the produced review.

**Remedy:** Check whether this is an actual failure or a current retained response. For a retained response, use lead interpretation above without a provider retry. For a strict historical or corrupt transport result, preserve the evidence and repair the specific contract/transport failure before retrying.

## `truncated-output`

**Status:** emitted

**Evidence:** The summary and archived attempt artifacts show output that ended before the required review structure completed.

**Remedy:** A completed current response with truncated content uses retained interpretation above. If provider completion was not established, inspect the archived attempt and log, repair the interruption, and retry only within the existing authority and limit.

## `antigravity-quota`

**Status:** emitted

**Evidence:** The Antigravity stream carries an `AGY_ERROR:` line that reports quota or resource exhaustion, such as status `RESOURCE_EXHAUSTED`. The gate log's `AgyOutcome:` line quotes it. The run is a hard failure: nothing is retained for interpretation, retried, or rerun on another model.

**Remedy:** Wait for the account's Antigravity quota to reset, or raise it, then rerun the same gate. Do not move the slot to another lane or model without an explicit policy change.

## `antigravity-api-error`

**Status:** emitted

**Evidence:** The Antigravity stream carries an `AGY_ERROR:` line that is not a quota error, for example an unavailable service. The gate log's `AgyOutcome:` line quotes it, with its `retryable` flag when agy gave one. No retry or fallback follows.

**Remedy:** Read the quoted error. When agy marks it retryable, rerun the same gate once the service recovers; otherwise correct the reported account, model or request problem first.

## `antigravity-context-compacted`

**Status:** emitted

**Evidence:** The Antigravity stream shows a `checkpoint` step: agy compacted the conversation during the review, so the reviewer may no longer hold the input it read. Compaction outranks a terminal failure or an unread chunk in the same run.

**Remedy:** The review is not credited. Rerun the gate. If compaction recurs, reduce the review input, for example the milestone's diff, so the review fits without compaction.

## `antigravity-unapproved-tool`

**Status:** emitted

**Evidence:** The Antigravity stream shows a tool call outside the lane's allowlist, or a `subagent` step. The gate log names the tool. The allowlist admits only tools whose every target is named in a parameter the audit inspects, so a tool added by a later agy release fails until a Heddle release admits it.

**Remedy:** The review is not credited. Rerun the gate. If the reviewer keeps choosing the tool, check whether an agy upgrade introduced it, and keep that agy version off the lane until Heddle supports it; do not edit the prompt or the stream to hide the call.

## `antigravity-incomplete-coverage`

**Status:** emitted

**Evidence:** The review input was delivered as chunk files, and at least one chunk has no credited whole read. A read is credited only when the chunk was read whole with `view_file` (recorded as `read_file`), nothing wrote to it or named it in executed text before that read, and every transport file still matched its frozen digest when the run ended. The gate summary records the credited reads and the uncovered byte ranges.

**Remedy:** The review is not credited, because the reviewer did not read all of its input. Rerun the gate. If chunks keep going unread, reduce the review input so it needs fewer chunks.

## `out-of-scope-access`

**Status:** emitted

**Evidence:** The reviewer reached outside its review, and the gate log names the access. On every lane this covers naming or reading other review artifacts under the feature's `reviews/` directory. On the Antigravity lane it also covers agy's data directory, recursive searches rooted at `/`, `~`, `$HOME` or `/home`, running `heddle` by name, path or module, running a process-inspection command, and any denied action in the result's `denied_actions` (the lane's only deny rule is on agy's data directory). No retry or fallback follows.

**Remedy:** The review is not credited. Read the named access. When host instructions such as `AGENTS.md` tell agents to run `heddle` or to read earlier reviews, scope those instructions away from review sessions, then rerun the gate.
