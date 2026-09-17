---
name: derived-state-cache-on-fact-ledger
area: architecture
status: active
created: 2026-06-14
superseded_by: null
---

# Reuse recorded results through their fact ledger

**Intent:** reuse an expensive operation's retained output without creating a
second cache store or confusing replay with a successful workflow outcome.

**When to use:** an operation already records an identity-bound result and the
contract permits replay of that observation. An LLM call need not be deterministic
for its original captured response to be replayable.

**Recipe:**

1. Bind the inputs and execution identity that determine replay eligibility:
   subject, prompt, configuration, provider, model, reasoning effort and the
   applicable assignment/round/slot identity.
2. Keep the result binding in the existing ledger and the original payload in
   its canonical artifact. One owner decides reuse for every caller.
3. Select the eligible recorded event and validate both identity and artifact
   integrity. A matching hash alone is insufficient. Do not hide a newer
   completed input behind an older matching result.
4. Return the original result and warnings on exact replay without invoking a
   provider or appending a duplicate event. Keep closure separate: replaying a
   completed review with findings does not make those findings resolved.
5. Preserve interrupted work and recover valid artifacts through the owning
   protocol before admitting another paid call. Missing or corrupt canonical
   evidence requires an explicit repair result, not a silent cache miss.
6. Publish through the normal locked state writer. Revision-neutral append does
   not remove the need to preserve concurrent events.

**Anti-patterns / caveats:** an independent cache index; hash-only trust;
pass-only reuse that needlessly repeats a completed review; treating replay as
closure; loss of warnings on replay; or promising deterministic model output.

**Current Heddle application:** `decide_review_reuse` in
`heddle/runtime/gate_run.py` checks the latest completed same-lane event and its
captured canonical result. Review assignments separately govern further rounds,
dispositions, closure and budget.

**Concrete future-feature scenario:** a build analyzer replays its immutable
report for exactly matching inputs while a separate release policy decides
whether unresolved findings block shipment.
